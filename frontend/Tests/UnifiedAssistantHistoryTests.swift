import XCTest
@testable import BookCourse

@MainActor final class UnifiedAssistantHistoryTests: XCTestCase {
  private func repository() throws -> LocalBookCourseRepository {
    try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
  }
  private var context: AssistantSessionContext {
    AssistantSessionContext(courseID: nil, bookID: "", bookIDs: [], chapterID: nil,
      courseTitle: "首页", chapterTitle: "", prompts: [], discovery: false)
  }
  func testSixSessionLimitCountsSelectedAndReadOnlyAndDeleteReleasesSlot() async throws {
    let repository = try repository()
    let sessions = (0..<6).map { index in
      var session = AssistantSession(title: "会话\(index)", context: context)
      if index == 0 { session.capacityState = "read_only" }
      return session
    }
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: sessions), scope: "owner")
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    history.submit("第七个", context: context) { _, _, _ in XCTFail("No model call at limit"); return AssistantHistoryAnswer(text: "") }
    XCTAssertEqual(history.sessions.count, 6)
    XCTAssertTrue(history.showingSessionLimit)
    XCTAssertNil(history.current)
    history.delete(sessions[1].id)
    history.submit("第六个", context: context) { _, _, _ in AssistantHistoryAnswer(text: "ok") }
    XCTAssertEqual(history.sessions.count, 6)
    XCTAssertNotNil(history.current)
    await history.flush()
  }
  func testCapacityLocksAtCurrentContextThresholdAndNeverReopens() async throws {
    let history = AssistantHistoryStore(repository: try repository())
    await history.load(scope: "owner")
    history.submit("问题", context: context) { _, _, _ in AssistantHistoryAnswer(text: "ok") }
    let id = try XCTUnwrap(history.current?.id)
    history.applyCapacity(sessionID: id, tokens: 899_999, runningTaskID: "task")
    XCTAssertTrue(history.acceptsNewMessages)
    history.applyCapacity(sessionID: id, tokens: 900_000, runningTaskID: "task")
    XCTAssertEqual(history.current?.capacityState, "finishing")
    XCTAssertFalse(history.acceptsNewMessages)
    history.applyCapacity(sessionID: id, tokens: 999_999, runningTaskID: "task")
    XCTAssertEqual(history.current?.capacityState, "finishing")
    history.applyCapacity(sessionID: id, tokens: 1_000_000, runningTaskID: "task")
    XCTAssertTrue(history.current?.isReadOnly == true)
    history.applyCapacity(sessionID: id, tokens: 300, runningTaskID: nil)
    XCTAssertTrue(history.current?.isReadOnly == true)
    XCTAssertEqual(history.current?.capacityTriggerTokens, 900_000)
  }
  func testOwnershipMigrationLeavesAmbiguousNotesUnassignedAndCascadeIsIsolated() async throws {
    let repository = try repository()
    let unique = StudyNote(id: "unique", kind: .text, title: "唯一", anchor: NoteAnchor(bookID: "single", page: 2))
    let ambiguous = StudyNote(id: "ambiguous", kind: .text, title: "歧义", anchor: NoteAnchor(bookID: "shared", page: 3))
    let b = StudyNote(id: "b", kind: .text, title: "B", anchor: NoteAnchor(courseID: "B", courseResourceID: "B-shared", bookID: "shared", page: 3))
    for note in [unique, ambiguous, b] { _ = try await repository.mutateNote(note, scope: "owner") }
    let relationships = [NoteCourseResource(courseID: "A", bookID: "single", resourceID: "A-single"),
      NoteCourseResource(courseID: "A", bookID: "shared", resourceID: "A-shared"),
      NoteCourseResource(courseID: "B", bookID: "shared", resourceID: "B-shared")]
    let migrated = try await repository.migrateNoteOwnership(scope: "owner", relationships: relationships)
    XCTAssertEqual(migrated.first { $0.note.id == "unique" }?.note.anchor?.courseResourceID, "A-single")
    XCTAssertNil(migrated.first { $0.note.id == "ambiguous" }?.note.anchor?.courseResourceID)
    _ = try await repository.deleteCourseNotes(scope: "owner", courseID: "A", resourceIDs: ["A-single", "A-shared"])
    let remaining = try await repository.loadStoredNotes(scope: "owner")
    XCTAssertEqual(Set(remaining.map(\.note.id)), ["ambiguous", "b"])
  }
  func testUnscopedOriginalNoteIsRejectedBeforeWriting() async throws {
    let repository = try repository()
    let store = AppStore(repository: repository)
    let note = StudyNote(id: "unscoped", kind: .text, title: "不可自动认领", anchor: NoteAnchor(bookID: "shared", page: 1))
    do {
      _ = try await store.saveNote(note)
      XCTFail("An original page always requires an explicitly selected course")
    } catch { XCTAssertEqual(error.localizedDescription, "无指定课程，无法进行笔记撰写") }
    let stored = try await repository.loadStoredNotes(scope: "local")
    XCTAssertTrue(stored.isEmpty)
  }

  func testTemporaryNoteCommitKeepsArtifactAndMinimalReceiptWithoutChatRun() async throws {
    let repository = try repository()
    let note = StudyNote(id: "artifact", kind: .text, title: "已保存产出", body: "正文",
      anchor: NoteAnchor(courseID: "A", courseResourceID: "A-resource", bookID: "book", page: 4),
      conversation: [ChatMessage(role: "user", text: "不应持久化的临时提问")])
    let committed = try await repository.commitEphemeralToolNote(scope: "owner", runID: "temporary",
      toolCallID: "save", argumentsHash: "digest", note: note)
    XCTAssertFalse(committed.replayed)
    let replay = try await repository.commitEphemeralToolNote(scope: "owner", runID: "temporary",
      toolCallID: "save", argumentsHash: "digest", note: note)
    XCTAssertTrue(replay.replayed)
    let artifacts = try await repository.loadStoredNotes(scope: "owner")
    XCTAssertEqual(artifacts.count, 1)
    XCTAssertEqual(artifacts.first?.note.anchor?.courseResourceID, "A-resource")
    XCTAssertNil(artifacts.first?.note.conversation)
    let runs = try await repository.loadRuns(scope: "owner")
    XCTAssertTrue(runs.isEmpty)
    let receipt = try await repository.toolReceipt(scope: "owner", runID: "temporary", toolCallID: "save")
    XCTAssertFalse(receipt?.result.contains("临时提问") ?? true)
    XCTAssertEqual(receipt?.noteID, "artifact")
  }

  func testDeletedHistoryOutboxSurvivesReopenAndSuppressesRemoteResurrection() async throws {
    let repository = try repository()
    let session = AssistantSession(title: "删除目标", context: context)
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: [session]), scope: "owner")
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    history.delete(session.id)
    await history.flush()
    let reopened = AssistantHistoryStore(repository: repository)
    await reopened.load(scope: "owner")
    XCTAssertEqual(reopened.archive.deletedSessionIDs, [session.id])
    reopened.mergeRemoteSessions([session])
    XCTAssertTrue(reopened.sessions.isEmpty)
    reopened.acknowledgeDeletion(session.id)
    XCTAssertTrue(reopened.archive.deletedSessionIDs?.isEmpty == true)
  }

  func testRestartRecoversFinalCheckpointAndPreservesFinishingCapacityLock() async throws {
    let repository = try repository()
    var session = AssistantSession(title: "收尾", context: context)
    let user = ChatMessage(role: "user", text: "完成当前任务")
    session.messages = [user]
    session.pendingQuestion = user.text
    session.capacityState = "finishing"
    var frozen = AgentFrozenContext(scope: "owner", courseID: nil, bookID: "", bookTitle: "",
      sourceVersion: "", chapterID: nil, page: 1, printedPage: nil, selection: "", currentNoteID: nil, capturedAt: .now)
    frozen.sessionID = session.id
    frozen.messageID = user.id
    var run = AgentRunRecord(id: "finished-run", scope: "owner", status: .completed, request: user.text,
      context: frozen, evidence: [], steps: [], finalText: "任务已完成", createdAt: .now, updatedAt: .now)
    run.contextTokens = 100
    run.capacityState = "archived"
    session.runs = [run]
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: [session], selectedID: session.id), scope: "owner")
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    XCTAssertEqual(history.current?.messages.last?.text, "任务已完成")
    XCTAssertNil(history.current?.pendingQuestion)
    XCTAssertTrue(history.current?.isReadOnly == true)
    history.recordRun(run)
    XCTAssertEqual(history.current?.messages.count, 2)
    XCTAssertFalse(history.acceptsNewMessages)
  }

  func testManualRemovalRefreshesChangedImpactAndKeepsOtherCourseOnSharedBook() async throws {
    let repository = try repository()
    let store = AppStore(repository: repository)
    store.courses = [Course(id: "A", name: "同名课程", resourceIDs: ["shared"]),
      Course(id: "B", name: "同名课程", resourceIDs: ["shared"])]
    let a = StudyNote(id: "note-a", kind: .text, title: "A批注", anchor: NoteAnchor(courseID: "A", bookID: "shared", page: 3))
    let b = StudyNote(id: "note-b", kind: .text, title: "B批注", anchor: NoteAnchor(courseID: "B", bookID: "shared", page: 3))
    _ = try await store.saveNote(a)
    _ = try await store.saveNote(b)
    let oldImpact = try await store.prepareCourseRemoval("A")
    var changed = try XCTUnwrap(store.notes.first { $0.id == "note-a" })
    changed.body = "确认前更新"
    _ = try await store.saveNote(changed)
    do {
      try await store.confirmCourseRemoval(oldImpact)
      XCTFail("A changed impact must require another user confirmation")
    } catch { XCTAssertTrue(error.localizedDescription.contains("重新确认")) }
    XCTAssertEqual(store.courses.count, 2)
    let refreshed = try await store.prepareCourseRemoval("A")
    try await store.confirmCourseRemoval(refreshed)
    await store.flush()
    XCTAssertEqual(store.courses.map(\.id), ["B"])
    XCTAssertEqual(store.notes.map(\.id), ["note-b"])
    let persisted = try await repository.loadStoredNotes(scope: "local")
    XCTAssertEqual(persisted.map(\.note.id), ["note-b"])
  }

}
