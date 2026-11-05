import Foundation
import XCTest
@testable import BookCourse

@MainActor final class CourseDeletionTests: XCTestCase {
  private func repository() throws -> LocalBookCourseRepository {
    try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory
      .appendingPathComponent("course-deletion-tests-" + UUID().uuidString))
  }
  private func marker(_ id: String, books: Set<String> = ["book"], paths: Set<String> = []) -> CourseDeletionTombstone {
    CourseDeletionTombstone(courseID: id, workspaceIDs: [id, "workspace-" + id],
      bookIDs: books, resourceIDs: ["resource-" + id], pendingFilePaths: paths)
  }
  private func snapshot(_ ids: [String], book: String = "book") -> AppSnapshot {
    var value = AppSnapshot()
    value.courses = ids.map { Course(id: $0, name: $0, resourceIDs: [book], activeResourceID: book) }
    value.selectedCourseID = ids.first; value.selectedBookID = ids.isEmpty ? nil : book
    value.locations = Dictionary(uniqueKeysWithValues: ids.map { ($0 + "::" + book, StudyLocation(page: 7)) })
    return value
  }
  private func personal(_ snapshot: AppSnapshot, paths: [String: String] = [:]) -> LocalPersonalState {
    LocalPersonalState(snapshot: snapshot, sources: [], workspaces: [], filePaths: paths,
      courseSources: [:], dailyPlans: [:], diagnostics: [:], initialized: true)
  }
  private func cloud(_ snapshot: AppSnapshot) -> CloudLocalSnapshot {
    CloudLocalSnapshot(snapshot: snapshot, sources: [], workspaces: [], filePaths: [:], pending: [:],
      creationWorkspaceID: nil, notes: [], pendingSelection: nil)
  }

  func testActiveAndLastCourseDeletionKeepSelectionCoherent() {
    let remaining = CourseDeletionPruning.snapshot(snapshot(["A", "B"]), tombstones: [marker("A")])
    XCTAssertEqual(remaining.courses.map(\.id), ["B"])
    XCTAssertEqual(remaining.selectedCourseID, "B")
    XCTAssertEqual(remaining.selectedBookID, "book")
    XCTAssertNil(remaining.locations["A::book"])
    XCTAssertEqual(remaining.locations["B::book"]?.page, 7)
    let empty = CourseDeletionPruning.snapshot(remaining, tombstones: [marker("A"), marker("B")])
    XCTAssertTrue(empty.courses.isEmpty)
    XCTAssertNil(empty.selectedCourseID); XCTAssertNil(empty.selectedBookID)
  }

  func testTombstoneSurvivesRelaunchAndRejectsQueuedStaleSnapshot() async throws {
    let repo = try repository(), original = snapshot(["A", "B"])
    try await repo.saveSnapshot(original)
    try await repo.recordCourseDeletion(marker("A"), scope: "local")
    try await repo.finishCourseDeletion(scope: "local")
    try await repo.saveSnapshot(original) // A pre-delete persistence task finishes late.
    let reopened = try LocalBookCourseRepository(directory: repo.directory)
    let saved = try await reopened.loadSnapshot()
    XCTAssertEqual(saved?.courses.map(\.id), ["B"])
    let markers = try await reopened.courseDeletionTombstones(scope: "local")
    XCTAssertEqual(markers.map(\.courseID), ["A"])
  }

  func testCloudCacheBarrierHasAccountScopeEvenForSameCourseID() async throws {
    let repo = try repository(), original = cloud(snapshot(["A", "B"]))
    try await repo.saveCloudState(original, key: "owner")
    try await repo.saveCloudState(original, key: "other-owner")
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    try await repo.saveCloudState(original, key: "owner")
    let owner = try await repo.loadCloudState("owner"), other = try await repo.loadCloudState("other-owner")
    XCTAssertEqual(owner?.snapshot.courses.map(\.id), ["B"])
    XCTAssertEqual(other?.snapshot.courses.map(\.id), ["A", "B"])
  }

  func testPersonalAuthorityBarrierRejectsLateGenerationAndKeepsOtherCourse() async throws {
    let repo = try repository(), original = personal(snapshot(["A", "B"]))
    try await repo.savePersonalState(original, scope: "owner")
    let record = LocalGenerationRecord(id: "task-A", courseID: "A", bookID: "book", chapterID: "chapter",
      operation: "lecture", body: [:], state: "submitted")
    try await repo.saveLocalGeneration(record, scope: "owner")
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    try await repo.savePersonalState(original, scope: "owner")
    do { try await repo.saveLocalGeneration(record, scope: "owner"); XCTFail("Late generation cannot resurrect deleted data") }
    catch {}
    let saved = try await repo.loadPersonalState(scope: "owner"), generations = try await repo.localGenerations(scope: "owner")
    XCTAssertEqual(saved?.snapshot.courses.map(\.id), ["B"])
    XCTAssertTrue(generations.isEmpty)
  }

  func testSharedOriginalReclaimedOnlyAfterLastCourseReference() async throws {
    let repo = try repository()
    let path = try await repo.saveOriginalAttachment(Data("shared original".utf8), extension: "pdf")
    try await repo.saveFilePaths(["book": path])
    try await repo.savePersonalState(personal(snapshot(["A", "B"]), paths: ["book": path]), scope: "owner")
    try await repo.recordCourseDeletion(marker("A", paths: [path]), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    try await repo.recordCourseDeletion(marker("B", paths: [path]), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    XCTAssertFalse(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
  }

  func testAnotherAccountRetainsSharedOriginalAndAuthority() async throws {
    let repo = try repository()
    let path = try await repo.saveOriginalAttachment(Data("cross account original".utf8), extension: "pdf")
    try await repo.savePersonalState(personal(snapshot(["A"]), paths: ["book": path]), scope: "owner")
    try await repo.savePersonalState(personal(snapshot(["A"]), paths: ["book": path]), scope: "other")
    try await repo.recordCourseDeletion(marker("A", paths: [path]), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    let other = try await repo.loadPersonalState(scope: "other")
    XCTAssertEqual(other?.snapshot.courses.map(\.id), ["A"])
  }

  func testNotesDeleteByExplicitCourseAndPreserveUnassignedSharedBook() async throws {
    let repo = try repository()
    let values = [
      StudyNote(id: "owned-A", kind: .text, title: "A", anchor: NoteAnchor(courseID: "A", bookID: "book", page: 1)),
      StudyNote(id: "owned-B", kind: .text, title: "B", anchor: NoteAnchor(courseID: "B", bookID: "book", page: 1)),
      StudyNote(id: "legacy", kind: .text, title: "旧笔记", anchor: NoteAnchor(bookID: "book", page: 1)),
    ]
    try await repo.adoptNotes(values, scope: "owner")
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    let notes = try await repo.loadStoredNotes(scope: "owner").map(\.note.id)
    XCTAssertEqual(Set(notes), ["owned-B", "legacy"])
    do { _ = try await repo.mutateNote(values[0], scope: "owner"); XCTFail("A stale editor cannot rewrite a deleted course note") }
    catch {}
  }

  func testDuplicateDeleteAndRecoveryAfterCleanupWriteFailureAreIdempotent() async throws {
    let repo = try repository(), original = personal(snapshot(["A", "B"]))
    try await repo.savePersonalState(original, scope: "owner")
    let value = marker("A")
    try await repo.recordCourseDeletion(value, scope: "owner")
    await repo.setWriteFaultForTesting(true)
    do { try await repo.finishCourseDeletion(scope: "owner"); XCTFail("Injected cleanup write must fail") } catch {}
    await repo.setWriteFaultForTesting(false)
    // Recovery uses the durable marker; it requires neither network nor another user deletion.
    try await repo.finishCourseDeletion(scope: "owner")
    try await repo.recordCourseDeletion(value, scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    let saved = try await repo.loadPersonalState(scope: "owner"), markers = try await repo.courseDeletionTombstones(scope: "owner")
    XCTAssertEqual(saved?.snapshot.courses.map(\.id), ["B"])
    XCTAssertEqual(markers.count, 1)
  }

  func testOldDeletedCourseSetMigratesWithoutReappearance() async throws {
    let repo = try repository()
    var original = personal(snapshot(["A", "B"]))
    original.deletedCourseIDs = ["A"]
    try await repo.savePersonalState(original, scope: "owner")
    let markers = try await repo.courseDeletionTombstones(scope: "owner")
    XCTAssertTrue(CourseDeletionPruning.ids(markers).contains("A"))
    try await repo.finishCourseDeletion(scope: "owner")
    let saved = try await repo.loadPersonalState(scope: "owner")
    XCTAssertEqual(saved?.snapshot.courses.map(\.id), ["B"])
  }

  func testHistoryKeepsTextAndOtherCourseLinksWhileRemovingDeletedContext() {
    let context = AssistantSessionContext(courseID: "A", bookID: "book", bookIDs: ["book"],
      courseTitle: "A", chapterTitle: "", prompts: [], discovery: false)
    var session = AssistantSession(title: "普通对话", context: context)
    session.messages = [ChatMessage(role: "user", text: "需要保留的文字")]
    session.courseLinks = ["message": ["A", "B"]]
    let result = CourseDeletionPruning.history(AssistantHistoryArchive(sessions: [session], selectedID: session.id), tombstones: [marker("A")])
    XCTAssertEqual(result.sessions.first?.messages.first?.text, "需要保留的文字")
    XCTAssertEqual(result.sessions.first?.courseLinks["message"], ["B"])
    XCTAssertNil(result.sessions.first?.context.courseID)
    XCTAssertEqual(result.selectedID, session.id)
  }

  func testInFlightUploadRetiredByCourseAndCannotRestoreAfterRelaunch() async throws {
    let repo = try repository(), store = AppStore(repository: repo)
    store.isLoading = false; store.courses = snapshot(["A", "B"]).courses
    try await store.restoreCourseDeletionState()
    store.restoreCourseUploads()
    let draftA = CourseDraft(name: "A", resourceIDs: ["book"], importCourseID: "A")
    let draftB = CourseDraft(name: "B", resourceIDs: ["book"], importCourseID: "B")
    let batchA = CourseUploadBatch(courseID: "A", courseName: "A", scope: "local", draft: draftA,
      addingMaterials: true, files: [CourseUploadFile(filename: "A.pdf", bookID: "book", status: .uploading)])
    let batchB = CourseUploadBatch(courseID: "B", courseName: "B", scope: "local", draft: draftB,
      addingMaterials: true, files: [CourseUploadFile(filename: "B.pdf", bookID: "book", status: .completed)])
    store.uploadState.batches = [batchA, batchB]
    XCTAssertTrue(store.persistCourseUploads())
    try await store.performLocalCourseDeletion(courseID: "A", scope: "local")
    XCTAssertEqual(store.uploadState.batches.map(\.courseID), ["B"])
    XCTAssertFalse(store.canApplyCourseMutation(courseID: "A", scope: "local"))
    XCTAssertTrue(store.canApplyCourseMutation(courseID: "B", scope: "local"))
    let reopened = AppStore(repository: try LocalBookCourseRepository(directory: repo.directory))
    reopened.isLoading = false; reopened.courses = store.courses
    try await reopened.restoreCourseDeletionState()
    reopened.restoreCourseUploads()
    XCTAssertEqual(reopened.uploadState.batches.map(\.courseID), ["B"])
    XCTAssertEqual(reopened.courses.map(\.id), ["B"])
  }

  func testConcurrentSameHashStagingSurvivesDeletionBeforeSnapshotsAreSaved() async throws {
    let repo = try repository()
    let external = repo.directory.appendingPathComponent("incoming", isDirectory: true)
    try FileManager.default.createDirectory(at: external, withIntermediateDirectories: true)
    let first = external.appendingPathComponent("first.txt"), second = external.appendingPathComponent("renamed.txt")
    try Data("same bytes from concurrent course imports".utf8).write(to: first)
    try Data("same bytes from concurrent course imports".utf8).write(to: second)
    let ownerA = CourseOriginalOwner(courseID: "A", scope: "owner", id: "batchA:file")
    let ownerB = CourseOriginalOwner(courseID: "B", scope: "owner", id: "batchB:file")
    async let stagedA = LocalFileImporter.stage(url: first, repository: repo, owner: ownerA)
    async let stagedB = LocalFileImporter.stage(url: second, repository: repo, owner: ownerB)
    let (a, b) = try await (stagedA, stagedB)
    XCTAssertEqual(a.relativePath, b.relativePath)
    // Neither larger course snapshot nor upload JSON has committed yet.
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(b.relativePath).path))
    try await repo.recordCourseDeletion(marker("B"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    XCTAssertFalse(FileManager.default.fileExists(atPath: repo.attachmentURL(b.relativePath).path))
    do { _ = try await LocalFileImporter.stage(url: first, repository: repo, owner: ownerA); XCTFail("Late copy must stop at durable course barrier") }
    catch {}
  }

  func testFailedOwnerTransactionDoesNotRetainAnotherCoursesOriginalForever() async throws {
    let repo = try repository(), data = Data("owner transaction rollback".utf8)
    let ownerA = CourseOriginalOwner(courseID: "A", scope: "owner", id: "A:file")
    let ownerB = CourseOriginalOwner(courseID: "B", scope: "owner", id: "B:file")
    let path = try await repo.saveOriginalAttachment(data, extension: "txt", owner: ownerA)
    await repo.setWriteFaultForTesting(true)
    do { _ = try await repo.saveOriginalAttachment(data, extension: "txt", owner: ownerB); XCTFail("Owner write must fail atomically") }
    catch {}
    await repo.setWriteFaultForTesting(false)
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    XCTAssertFalse(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path), "Failed B ownership cannot pin an orphan")
  }

  func testRunAndEphemeralReceiptCannotReappearAfterCourseCleanup() async throws {
    let repo = try repository()
    let context = AgentFrozenContext(scope: "owner", courseID: "A", bookID: "book", bookTitle: "A",
      sourceVersion: "v1", chapterID: nil, page: 1, printedPage: nil, selection: "",
      currentNoteID: nil, capturedAt: .now)
    let run = AgentRunRecord(id: "run-A", scope: "owner", status: .running, request: "课程任务",
      context: context, evidence: [], steps: [], createdAt: .now, updatedAt: .now)
    let saved = try await repo.saveRun(run)
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    do { _ = try await repo.saveRun(saved); XCTFail("A late checkpoint cannot recreate removed run") } catch {}
    // Even an unanchored payload must not recreate a receipt for the deleted run.
    do {
      _ = try await repo.commitEphemeralToolNote(scope: "owner", runID: "run-A", toolCallID: "call",
        argumentsHash: "hash", note: StudyNote(kind: .text, title: "late"))
      XCTFail("Receipt must honor durable deleted-run marker")
    } catch {}
    let reopened = try LocalBookCourseRepository(directory: repo.directory)
    let runs = try await reopened.loadRuns(scope: "owner"), receipt = try await reopened.toolReceipt(scope: "owner", runID: "run-A", toolCallID: "call")
    XCTAssertTrue(runs.isEmpty); XCTAssertNil(receipt)
  }

  func testPublicMaterialRecordRetainsFilesUntilLastCourseReleasesBook() async throws {
    let repo = try repository(), source = try ContentBundle.load([SourceContent].self, name: "sources")[0]
    let path = try await repo.saveOriginalAttachment(Data("downloaded shared material".utf8), extension: "pdf")
    let record = LocalMaterialRecord(groupID: "public", version: "1", bookID: source.id,
      files: ["pdf": path], figurePaths: [:], source: source, diagnostics: [], grading: [])
    try await repo.saveMaterialRecord(record, scope: "owner")
    try await repo.savePersonalState(personal(snapshot(["A", "B"], book: source.id), paths: [source.id: path]), scope: "owner")
    try await repo.recordCourseDeletion(marker("A", books: [source.id]), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    let retained = try await repo.loadMaterialRecords(scope: "owner")
    XCTAssertEqual(retained.map(\.bookID), [source.id])
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    try await repo.recordCourseDeletion(marker("B", books: [source.id]), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    let removed = try await repo.loadMaterialRecords(scope: "owner")
    XCTAssertTrue(removed.isEmpty)
    XCTAssertFalse(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
  }

  func testLateMediaCleanupHonorsScopeOwnersAndRejectsExternalPaths() async throws {
    let repo = try repository()
    let path = try await repo.saveOriginalAttachment(Data("held by other owner".utf8), extension: "txt",
      owner: CourseOriginalOwner(courseID: "B", scope: "other", id: "B:file"))
    try await repo.removeCourseFileIfUnreferenced(repo.attachmentURL(path))
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    let outside = repo.directory.appendingPathComponent("outside-approved-roots.txt")
    try Data("external".utf8).write(to: outside)
    try await repo.removeCourseFileIfUnreferenced(outside)
    XCTAssertTrue(FileManager.default.fileExists(atPath: outside.path))
    let orphan = repo.directory.appendingPathComponent("lesson-media/orphan.jpg")
    try FileManager.default.createDirectory(at: orphan.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data([1, 2, 3]).write(to: orphan)
    try await repo.removeCourseFileIfUnreferenced(orphan)
    XCTAssertFalse(FileManager.default.fileExists(atPath: orphan.path))
  }


  func testOtherCourseRunKeepsTextButCannotRestoreDeletedEvidence() async throws {
    let repo = try repository()
    let context = AgentFrozenContext(scope: "owner", courseID: "B", bookID: "book", bookTitle: "B",
      sourceVersion: "v1", chapterID: nil, page: 1, printedPage: nil, selection: "",
      currentNoteID: nil, capturedAt: .now)
    let evidence = AgentEvidence(id: "evidence-A", bookID: "book", courseID: "A", sourceVersion: "v1",
      chapterID: nil, page: 1, printedPage: nil, quote: "旧课程工具证据", quoteSHA256: "hash")
    let run = AgentRunRecord(id: "run-B", scope: "owner", status: .running, request: "保留B课程普通文字",
      context: context, evidence: [evidence], steps: [], createdAt: .now, updatedAt: .now)
    let stale = try await repo.saveRun(run)
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    let loaded = try await repo.loadRun("run-B", scope: "owner")
    let cleaned = try XCTUnwrap(loaded)
    XCTAssertEqual(cleaned.request, "保留B课程普通文字")
    XCTAssertTrue(cleaned.evidence.isEmpty)
    do { _ = try await repo.saveRun(stale); XCTFail("Stale checkpoint cannot restore evidence") } catch {}
    var late = cleaned; late.evidence = [evidence]
    let saved = try await repo.saveRun(late)
    XCTAssertTrue(saved.evidence.isEmpty)
  }


  func testCourseNoteTombstoneContainsNoBusinessPayloadAndCleansOwnedAudio() async throws {
    let repo = try repository()
    let audioA = try await repo.saveAttachment(Data("A voice".utf8), extension: "m4a")
    let audioB = try await repo.saveAttachment(Data("B voice".utf8), extension: "m4a")
    let evidence = AgentEvidence(id: "evidence-A", bookID: "book", courseID: "A", sourceVersion: "v1",
      chapterID: nil, page: 1, printedPage: nil, quote: "课程原文", quoteSHA256: "hash")
    let noteA = StudyNote(id: "private-A", kind: .voice, title: "私密正文A", body: "业务正文应移除",
      anchor: NoteAnchor(courseID: "A", bookID: "book", page: 1), audioPath: audioA,
      references: [NoteReference(evidence)])
    let noteB = StudyNote(id: "private-B", kind: .voice, title: "B的笔记",
      anchor: NoteAnchor(courseID: "B", bookID: "book", page: 1), audioPath: audioB)
    try await repo.adoptNotes([noteA, noteB], scope: "owner")
    let connection = try SQLiteConnection(url: repo.directory.appendingPathComponent("bookcourse.sqlite"))
    try connection.put("cloud-note", "owner:private-A", JSONEncoder().encode(noteA))
    var foreign = noteA; foreign.audioPath = nil
    try connection.put("cloud-note", "other:private-A", JSONEncoder().encode(foreign))
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    XCTAssertTrue(try connection.get("cloud-note", "owner:private-A").isEmpty)
    XCTAssertFalse(try connection.get("cloud-note", "other:private-A").isEmpty)
    let deleted = try connection.query("SELECT length(payload),revision,deleted FROM notes WHERE scope=? AND id=?",
      [.text("owner"), .text("private-A")])
    XCTAssertEqual(deleted.first?[0].int, 0)
    XCTAssertEqual(deleted.first?[2].int, 1)
    let references = try connection.query("SELECT count(*) FROM note_references WHERE scope=? AND note_id=?",
      [.text("owner"), .text("private-A")])
    XCTAssertEqual(references.first?[0].int, 0)
    XCTAssertFalse(FileManager.default.fileExists(atPath: repo.attachmentURL(audioA).path))
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(audioB).path))
    let retained = try await repo.loadStoredNote("private-B", scope: "owner")
    XCTAssertEqual(retained?.note.title, "B的笔记")
  }


  func testDeletionCommandReceiptIsMinimalScopedAndCannotClaimUncommittedSuccess() async throws {
    let repo = try repository()
    let receipt: [String: JSONValue] = ["operation_id": .string("delete-op"), "course_id": .string("A"),
      "arguments_hash": .string("frozen-hash"), "status": .string("deleted")]
    do { try await repo.saveLocalCourseDeletionResult(receipt, id: "delete-op", scope: "owner"); XCTFail("No marker means no success") }
    catch {}
    try await repo.recordCourseDeletion(marker("A"), scope: "owner")
    try await repo.finishCourseDeletion(scope: "owner")
    try await repo.saveLocalCourseDeletionResult(receipt, id: "delete-op", scope: "owner")
    try await repo.saveLocalCourseDeletionResult(receipt, id: "delete-op", scope: "owner")
    var conflict = receipt; conflict["arguments_hash"] = .string("changed")
    do { try await repo.saveLocalCourseDeletionResult(conflict, id: "delete-op", scope: "owner"); XCTFail("Same operation cannot change arguments") }
    catch {}
    var verbose = receipt; verbose["note_body"] = .string("business content must not enter receipt")
    do { try await repo.saveLocalCourseDeletionResult(verbose, id: "delete-op", scope: "owner"); XCTFail("Receipt must contain metadata only") }
    catch {}
    let reopened = try LocalBookCourseRepository(directory: repo.directory)
    let saved = try await reopened.localCourseDeletionResult("delete-op", scope: "owner")
    let other = try await reopened.localCourseDeletionResult("delete-op", scope: "other")
    XCTAssertEqual(saved, receipt); XCTAssertNil(other)
  }


  func testCourseKeyPrefixNeverDeletesAnotherCoursesProgress() {
    let saved = snapshot(["A", "A::nested"])
    let result = CourseDeletionPruning.snapshot(saved, tombstones: [marker("A")])
    XCTAssertEqual(result.locations["A::nested::book"]?.page, 7)
    XCTAssertNil(result.locations["A::book"])
    XCTAssertFalse(CourseDeletionPruning.courseKeyIsRemoved("A:child:2026-11-05", separator: ":", removed: ["A"]))
    XCTAssertTrue(CourseDeletionPruning.courseKeyIsRemoved("A:2026-11-05", separator: ":", removed: ["A"]))
  }


  func testUploadLedgerRetirementFailureIsReportedAndRetryReclaimsOriginal() async throws {
    let repo = try repository(), store = AppStore(repository: repo)
    store.isLoading = false
    store.courses = [Course(id: "A", name: "A", resourceIDs: ["book-A"]), Course(id: "B", name: "B", resourceIDs: ["book-B"])]
    try await store.ensureCourseDeletionState(scope: "local")
    store.restoreCourseUploads()
    let path = try await repo.saveOriginalAttachment(Data("deleted course original".utf8), extension: "txt",
      owner: CourseOriginalOwner(courseID: "A", scope: "local", id: "A:file"))
    store.filePaths = ["book-A": path]
    try await repo.saveSnapshot(store.snapshot()); try await repo.saveFilePaths(store.filePaths)
    let batchA = CourseUploadBatch(courseID: "A", courseName: "A", scope: "local",
      draft: CourseDraft(name: "A", resourceIDs: ["book-A"], importCourseID: "A"), addingMaterials: true,
      files: [CourseUploadFile(filename: "A.txt", relativePath: path, bookID: "book-A", status: .completed)])
    let batchB = CourseUploadBatch(courseID: "B", courseName: "B", scope: "local",
      draft: CourseDraft(name: "B", resourceIDs: ["book-B"], importCourseID: "B"), addingMaterials: true,
      files: [CourseUploadFile(filename: "B.txt", bookID: "book-B", status: .completed)])
    store.uploadState.batches = [batchA, batchB]
    XCTAssertTrue(store.persistCourseUploads())
    let ledger = try XCTUnwrap(FileManager.default.contentsOfDirectory(at: repo.directory, includingPropertiesForKeys: nil)
      .first { $0.lastPathComponent.hasPrefix("course-uploads-") })
    store.uploadState.failLedgerWritesForTesting = true
    await store.deleteLocalCourse("A")
    XCTAssertTrue(store.toast?.title.hasPrefix("删除课程未完成") == true)
    XCTAssertFalse(store.courses.contains { $0.id == "A" })
    let durable = try await repo.courseDeletionTombstones(scope: "local")
    XCTAssertTrue(durable.contains { $0.courseID == "A" })
    let failedLedger = try JSONDecoder().decode([CourseUploadBatch].self, from: Data(contentsOf: ledger))
    XCTAssertEqual(Set(failedLedger.map(\.courseID)), ["A", "B"])
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    do { try await store.performLocalCourseDeletion(courseID: "A", scope: "local"); XCTFail("Repeated cleanup must not report success while ledger is still unwritable") }
    catch {}
    store.uploadState.failLedgerWritesForTesting = false
    try await store.performLocalCourseDeletion(courseID: "A", scope: "local")
    let savedLedger = try JSONDecoder().decode([CourseUploadBatch].self, from: Data(contentsOf: ledger))
    XCTAssertEqual(savedLedger.map(\.courseID), ["B"])
    XCTAssertFalse(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    XCTAssertTrue(store.canApplyCourseMutation(courseID: "B", scope: "local"))
    store.suspendCourseUploads(); await store.flush()
  }

  func testRestoredDeletionStaysUnreadyUntilUploadLedgerCanRetire() async throws {
    let repo = try repository(), initial = AppStore(repository: repo)
    initial.isLoading = false; initial.courses = [Course(id: "A", name: "A", resourceIDs: ["book"])]
    try await initial.ensureCourseDeletionState(scope: "local"); initial.restoreCourseUploads()
    let path = try await repo.saveOriginalAttachment(Data("restored deleted original".utf8), extension: "txt",
      owner: CourseOriginalOwner(courseID: "A", scope: "local", id: "A:file"))
    initial.filePaths = ["book": path]
    try await repo.saveSnapshot(initial.snapshot()); try await repo.saveFilePaths(initial.filePaths)
    initial.uploadState.batches = [CourseUploadBatch(courseID: "A", courseName: "A", scope: "local",
      draft: CourseDraft(name: "A", resourceIDs: ["book"], importCourseID: "A"), addingMaterials: true,
      files: [CourseUploadFile(filename: "A.txt", relativePath: path, bookID: "book", status: .completed)])]
    XCTAssertTrue(initial.persistCourseUploads())
    try await repo.recordCourseDeletion(marker("A", paths: [path]), scope: "local")
    initial.suspendCourseUploads()
    let reopened = AppStore(repository: try LocalBookCourseRepository(directory: repo.directory))
    reopened.isLoading = false; reopened.courses = initial.courses; reopened.filePaths = initial.filePaths
    reopened.uploadState.failLedgerWritesForTesting = true
    do { try await reopened.restoreCourseDeletionState(); XCTFail("Ledger retirement failure must propagate during launch recovery") }
    catch {}
    XCTAssertNil(reopened.courseDeletionState.readyEpoch)
    XCTAssertFalse(reopened.canApplyCourseMutation(courseID: "A", scope: "local"))
    XCTAssertTrue(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    reopened.uploadState.failLedgerWritesForTesting = false
    try await reopened.restoreCourseDeletionState()
    XCTAssertEqual(reopened.courseDeletionState.readyEpoch, reopened.cloudState.epoch)
    XCTAssertFalse(FileManager.default.fileExists(atPath: repo.attachmentURL(path).path))
    reopened.suspendCourseUploads(); await reopened.flush()
  }

}
