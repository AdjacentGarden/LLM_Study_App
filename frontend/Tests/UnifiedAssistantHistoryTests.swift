import XCTest
@testable import BookCourse

@MainActor private final class HistoryAnswerGate {
  private var continuation: CheckedContinuation<AssistantHistoryAnswer, Never>?
  var started = false
  func wait() async -> AssistantHistoryAnswer {
    await withCheckedContinuation { continuation in
      self.continuation = continuation
      started = true
    }
  }
  func finish(_ text: String) {
    continuation?.resume(returning: AssistantHistoryAnswer(text: text))
    continuation = nil
  }
}

@MainActor final class UnifiedAssistantHistoryTests: XCTestCase {
  private func repository() throws -> LocalBookCourseRepository {
    try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
  }
  private var context: AssistantSessionContext {
    AssistantSessionContext(courseID: nil, bookID: "", bookIDs: [], chapterID: nil,
      courseTitle: "首页", chapterTitle: "", prompts: [], discovery: false)
  }
  private func wait(_ message: String, until condition: () -> Bool) async throws {
    let deadline = ContinuousClock.now.advanced(by: .seconds(5))
    while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
    XCTAssertTrue(condition(), message)
  }

  func testNewConversationPreservesBothDraftsAndLetsTheOriginalReplyFinish() async throws {
    let repository = try repository()
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    let answer = HistoryAnswerGate()
    history.submit("第一段对话", context: context) { _, _, _ in await answer.wait() }
    try await wait("First reply should start") { answer.started }
    let originalID = try XCTUnwrap(history.current?.id)
    history.setDraft("第一段的草稿", context: context)
    XCTAssertTrue(history.startNew(context: context))
    XCTAssertNil(history.current)
    XCTAssertTrue(history.runningIDs.contains(originalID), "Starting another chat must preserve the original request")
    history.setDraft("第二段的草稿", context: context)
    history.select(originalID)
    XCTAssertEqual(history.draft, "第一段的草稿")
    XCTAssertTrue(history.startNew(context: context))
    XCTAssertEqual(history.draft, "第二段的草稿")
    answer.finish("第一段的回答")
    try await wait("The reply should stay with its original session") { !history.runningIDs.contains(originalID) }
    XCTAssertEqual(history.sessions.first?.messages.last?.text, "第一段的回答")
    XCTAssertNil(history.current)
    await history.flush()
    let reopened = AssistantHistoryStore(repository: repository)
    await reopened.load(scope: "owner")
    XCTAssertNil(reopened.current)
    XCTAssertEqual(reopened.draft, "第二段的草稿")
    XCTAssertEqual(reopened.sessions.first?.draft, "第一段的草稿")
  }

  func testNewConversationAtLimitPreservesSelectionAndDeletionReleasesTheEntry() async throws {
    let repository = try repository()
    let sessions = (0..<AssistantHistoryStore.maximumSessions).map { AssistantSession(title: "对话\($0)", context: context) }
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: sessions, selectedID: sessions[0].id), scope: "owner")
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    XCTAssertFalse(history.startNew(context: context))
    XCTAssertEqual(history.current?.id, sessions[0].id)
    XCTAssertTrue(history.showingSessionLimit)
    history.delete(sessions[1].id)
    XCTAssertTrue(history.startNew(context: context))
    XCTAssertNil(history.current)
    XCTAssertEqual(history.sessions.count, AssistantHistoryStore.maximumSessions - 1)
    await history.flush()
  }

  func testDeleteRunningConversationRejectsLateAnswerRunAndAcknowledgedRemoteImport() async throws {
    let repository = try repository()
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    let answer = HistoryAnswerGate()
    history.submit("删除正在回答的对话", context: context) { _, _, _ in await answer.wait() }
    try await wait("Reply should start") { answer.started }
    let removed = try XCTUnwrap(history.current)
    history.delete(removed.id)
    XCTAssertFalse(history.busy)
    XCTAssertFalse(history.runningIDs.contains(removed.id))
    answer.finish("不应复活的迟到回答")
    await Task.yield()
    history.acknowledgeDeletion(removed.id)
    history.mergeRemoteSessions([removed])
    history.recordRun(run(id: "late", sessionID: removed.id, scope: "owner", status: .completed))
    await history.flush()
    try await repository.importAssistantHistorySessions([removed], scope: "owner")
    let reopened = AssistantHistoryStore(repository: repository)
    await reopened.load(scope: "owner")
    XCTAssertTrue(reopened.sessions.isEmpty)
    XCTAssertTrue(reopened.archive.deletedSessionIDs?.isEmpty == true)
    XCTAssertTrue(reopened.archive.removedIDs.contains(removed.id))
  }

  func testStaleQueuedArchiveAndLegacyImportCannotOverwriteADeletionOrANewerSession() async throws {
    let repository = try repository()
    let removed = AssistantSession(title: "删除目标", context: context)
    let stale = AssistantHistoryArchive(sessions: [removed], selectedID: removed.id)
    try await repository.saveAssistantHistory(stale, scope: "owner")
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    history.delete(removed.id)
    history.submit("新建的对话", context: context) { _, _, _ in AssistantHistoryAnswer(text: "新回答") }
    try await wait("New reply should finish") { !history.busy }
    let newID = try XCTUnwrap(history.current?.id)
    await history.flush()
    try await repository.saveAssistantHistoryPreservingDeletions(stale, scope: "owner")
    try await repository.importAssistantHistorySessions([removed], scope: "owner")
    let stored = try await repository.loadAssistantHistory(scope: "owner")
    let saved = try XCTUnwrap(stored)
    XCTAssertEqual(saved.sessions.map(\.id), [newID])
    XCTAssertTrue(saved.removedIDs.contains(removed.id))
    let other = AssistantSession(id: removed.id, title: "其他账号的同ID对话", context: context)
    try await repository.importAssistantHistorySessions([other], scope: "other-account")
    let otherStored = try await repository.loadAssistantHistory(scope: "other-account")
    let otherArchive = try XCTUnwrap(otherStored)
    XCTAssertEqual(otherArchive.sessions.map(\.id), [removed.id])
  }

  private func run(id: String, sessionID: String, scope: String, status: AgentRunStatus) -> AgentRunRecord {
    var frozen = AgentFrozenContext(scope: scope, courseID: nil, bookID: "", bookTitle: "", sourceVersion: "",
      chapterID: nil, page: 1, printedPage: nil, selection: "", currentNoteID: nil, capturedAt: .now)
    frozen.sessionID = sessionID
    var run = AgentRunRecord(id: id, scope: scope, status: status, request: "请求", context: frozen,
      evidence: [], steps: [], createdAt: .now, updatedAt: .now)
    run.privateTransport = true
    return run
  }

  func testDeleteCancelsOnlyItsDurableRunsAndLeavesAnotherActiveConversationAndNotes() async throws {
    let repository = try repository()
    let store = AppStore(repository: repository)
    let target = AssistantSession(title: "删除目标", context: context)
    let other = AssistantSession(title: "另一个对话", context: context)
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: [target, other], selectedID: target.id), scope: "local")
    await store.assistantHistory.load(scope: "local")
    _ = try await repository.saveRun(run(id: "target-run", sessionID: target.id, scope: "local", status: .running))
    _ = try await repository.saveRun(run(id: "other-run", sessionID: other.id, scope: "local", status: .waitingForUser))
    let note = StudyNote(kind: .text, title: "保存的学习笔记")
    _ = try await repository.mutateNote(note, scope: "local")
    store.agent.resume()
    try await wait("The other conversation should be active") { store.agent.active?.id == "other-run" && !store.agent.isWorking }
    let deleted = await store.deleteAssistantSession(target.id, expectedScope: "local")
    XCTAssertTrue(deleted)
    let targetStored = try await repository.loadRun("target-run", scope: "local")
    let otherStored = try await repository.loadRun("other-run", scope: "local")
    let targetRun = try XCTUnwrap(targetStored)
    let otherRun = try XCTUnwrap(otherStored)
    XCTAssertEqual(targetRun.status, .cancelled)
    XCTAssertTrue(targetRun.cancelRequested)
    XCTAssertEqual(otherRun.status, .waitingForUser)
    XCTAssertFalse(otherRun.cancelRequested)
    XCTAssertEqual(store.agent.active?.id, "other-run")
    let notes = try await repository.loadStoredNotes(scope: "local")
    XCTAssertEqual(notes.map(\.note.id), [note.id])
    XCTAssertEqual(store.assistantHistory.sessions.map(\.id), [other.id])
    await store.flush()
  }

  func testResumeRetiresADeletedConversationBeforeConsideringItsOldRun() async throws {
    let repository = try repository()
    let store = AppStore(repository: repository)
    let removedID = "deleted-session"
    try await repository.saveAssistantHistory(AssistantHistoryArchive(removedSessionIDs: [removedID]), scope: "local")
    _ = try await repository.saveRun(run(id: "deleted-run", sessionID: removedID, scope: "local", status: .running))
    store.agent.resume()
    try await wait("Resume should finish cancelling the removed turn") { !store.agent.isWorking }
    let stored = try await repository.loadRun("deleted-run", scope: "local")
    let saved = try XCTUnwrap(stored)
    XCTAssertEqual(saved.status, .cancelled)
    XCTAssertTrue(saved.cancelRequested)
    XCTAssertNotEqual(store.agent.active?.status, .running)
  }

  func testOldAccountCompletionAndDeleteConfirmationCannotChangeTheCurrentAccount() async throws {
    let repository = try repository()
    let session = AssistantSession(id: "shared-id", title: "账号A", context: context)
    let second = AssistantSession(id: session.id, title: "账号B", context: context)
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: [session], selectedID: session.id), scope: "account-A")
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: [second], selectedID: second.id), scope: "account-B")
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "account-A")
    let answer = HistoryAnswerGate()
    history.submit("账号A的请求", context: context) { _, _, _ in await answer.wait() }
    try await wait("Account A should start its request") { answer.started }
    await history.load(scope: "account-B")
    var late = run(id: "account-A-run", sessionID: session.id, scope: "account-A", status: .completed)
    late.finalText = "不能进入账号B"
    history.recordRun(late)
    answer.finish("旧账号迟到的回答")
    await history.flush()
    XCTAssertEqual(history.current?.title, "账号B")
    XCTAssertTrue(history.current?.messages.isEmpty == true)
    let store = AppStore(repository: repository)
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: [second]), scope: "local")
    await store.assistantHistory.load(scope: "local")
    let deleted = await store.deleteAssistantSession(second.id, expectedScope: "account-A")
    XCTAssertFalse(deleted)
    XCTAssertEqual(store.assistantHistory.sessions.count, 1)
    XCTAssertTrue(store.assistantHistory.archive.removedIDs.isEmpty)
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
    run.finalCitations = [Citation(bookID: "book", chapterID: "chapter", chapterTitle: "教材原文",
      page: 4, chunkID: "evidence", quote: "可核验原文", score: 1)]
    run.finalSourceLabel = "textbook"
    run.finalCourseIDs = ["community-course"]
    session.runs = [run]
    try await repository.saveAssistantHistory(AssistantHistoryArchive(sessions: [session], selectedID: session.id), scope: "owner")
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    XCTAssertEqual(history.current?.messages.last?.text, "任务已完成")
    XCTAssertEqual(history.current?.messages.last?.sourceLabel, "textbook")
    XCTAssertEqual(history.current?.messages.last?.citations.first?.quote, "可核验原文")
    XCTAssertEqual(history.current?.courseLinks[run.id + ".answer"], ["community-course"])
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


extension UnifiedAssistantHistoryTests {
  func testCourseRemovalCompletionSurvivesCancelledWaiterAndNeverRevivesDeletedConversation() async throws {
    let repository = try repository()
    let history = AssistantHistoryStore(repository: repository)
    await history.load(scope: "owner")
    let gate = HistoryAnswerGate()
    var waiterReturned = false
    var anchored = context
    anchored.courseID = "removed-course"
    anchored.bookID = "removed-book"
    anchored.bookIDs = ["removed-book"]
    history.submit("删除当前课程", context: anchored) { _, _, _ in
      let answer = await gate.wait()
      waiterReturned = true
      return answer
    }
    try await wait("The original waiter should start") { gate.started }
    let original = try XCTUnwrap(history.current)
    let marker = CourseDeletionTombstone(courseID: "removed-course", workspaceIDs: ["removed-course"],
      bookIDs: ["removed-book"], resourceIDs: [])
    history.removeCourseReferences(tombstones: [marker])
    XCTAssertTrue(history.recordCourseRemovalCompletion(operationID: "remove-op", sessionID: original.id,
      messageID: original.context.messageID, scope: "owner"))
    XCTAssertTrue(history.recordCourseRemovalCompletion(operationID: "remove-op", sessionID: original.id,
      messageID: original.context.messageID, scope: "owner"))
    gate.finish("这个被取消的回答不应覆盖删除结果")
    try await wait("The cancelled waiter should unwind") { waiterReturned }
    await Task.yield()
    await history.flush()
    let retained = try XCTUnwrap(history.current)
    XCTAssertEqual(retained.messages.map(\.text), ["删除当前课程", "课程已从本机删除。"])
    XCTAssertNil(retained.context.courseID)
    XCTAssertNil(retained.pendingQuestion)
    XCTAssertNil(retained.error)
    XCTAssertFalse(history.runningIDs.contains(original.id))
    XCTAssertTrue(retained.runs?.isEmpty ?? true)
    XCTAssertTrue(retained.courseLinks.isEmpty)
    history.delete(original.id)
    XCTAssertFalse(history.recordCourseRemovalCompletion(operationID: "remove-op", sessionID: original.id,
      messageID: original.context.messageID, scope: "owner"))
    await history.flush()
    await history.load(scope: "other")
    XCTAssertFalse(history.recordCourseRemovalCompletion(operationID: "remove-op", sessionID: original.id,
      messageID: original.context.messageID, scope: "owner"))
    XCTAssertTrue(history.sessions.isEmpty)
  }
}
