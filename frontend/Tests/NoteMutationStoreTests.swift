import Foundation
import SQLite3
import XCTest

@testable import BookCourse

final class NoteMutationStoreTests: XCTestCase {
  private func folder() -> URL {
    FileManager.default.temporaryDirectory.appendingPathComponent("note-store-\(UUID())")
  }

  private func evidence(scope: String, book: String = "book-a", course: String? = "course-a") -> AgentEvidence {
    let quote = "染色体只复制一次，而细胞分裂两次。"
    return AgentEvidence(id: "ev-1", bookID: book, courseID: course, sourceVersion: "sha256:v1",
      chapterID: "c2", page: 11, printedPage: 16, quote: quote, quoteSHA256: AgentDigest.sha256(quote))
  }

  private func run(scope: String, id: String = "run-1", book: String = "book-a") -> AgentRunRecord {
    let ev = evidence(scope: scope, book: book)
    let context = AgentFrozenContext(scope: scope, courseID: "course-a", bookID: book, bookTitle: "生物",
      sourceVersion: "sha256:v1", chapterID: "c2", page: 11, printedPage: 16, selection: ev.quote,
      currentNoteID: nil, capturedAt: .now, courseResourceID: "resource-a")
    let call = AgentToolCall(id: "call-1", name: "create_note", argumentsJSON: "{}", argumentsHash: "hash-a")
    return AgentRunRecord(id: id, scope: scope, status: .running, request: "整理并解释", context: context,
      evidence: [ev], steps: [AgentStep(index: 0, id: "step-0", state: .completed, toolCalls: [call])],
      createdAt: .now, updatedAt: .now)
  }

  private func toolNote(_ run: AgentRunRecord, id: String = "assistant-note") -> StudyNote {
    StudyNote(id: id, kind: .text, title: "减数分裂", body: "整理后的解释",
      anchor: NoteAnchor(courseID: run.context.courseID, courseResourceID: run.context.courseResourceID, bookID: run.context.bookID,
        chapterID: run.context.chapterID, page: run.context.page, printedPage: run.context.printedPage,
        quote: run.context.selection),
      references: run.evidence.map(NoteReference.init),
      provenance: NoteProvenance(runID: run.id, toolCallID: "call-1", sourceLabel: "textbook"))
  }

  func testToolReplayReturnsOriginalNoteAndConflictingArgumentsAreRejected() async throws {
    let root = folder()
    let repository = try LocalBookCourseRepository(directory: root)
    let saved = try await repository.saveRun(run(scope: "owner"))
    let first = try await repository.commitToolNote(run: saved, toolCallID: "call-1", argumentsHash: "hash-a",
      note: toolNote(saved), result: "{}")
    XCTAssertFalse(first.replayed)
    XCTAssertEqual(first.run?.steps[0].outcome("call-1")?.noteID, "assistant-note")
    // A retry after a crash generates a new note ID; the receipt still wins.
    let retry = try await repository.commitToolNote(run: saved, toolCallID: "call-1", argumentsHash: "hash-a",
      note: toolNote(saved, id: "duplicate"), result: "{}")
    XCTAssertTrue(retry.replayed)
    XCTAssertEqual(retry.noteID, first.noteID)
    XCTAssertEqual(retry.revision, first.revision)
    do {
      _ = try await repository.commitToolNote(run: saved, toolCallID: "call-1", argumentsHash: "other",
        note: toolNote(saved), result: "{}")
      XCTFail("Same execution key with different arguments must conflict")
    } catch { XCTAssertEqual(error as? NoteStoreError, .receiptConflict(toolCallID: "call-1")) }

    let reopened = try LocalBookCourseRepository(directory: root)
    let notes = try await reopened.loadStoredNotes(scope: "owner")
    XCTAssertEqual(notes.map(\.note.id), ["assistant-note"])
    XCTAssertEqual(notes.first?.note.references?.first?.page, 11)
    XCTAssertEqual(notes.first?.note.references?.first?.sourceVersion, "sha256:v1")
    XCTAssertEqual(notes.first?.note.provenance?.sourceLabel, "textbook")
    let restoredRun = try await reopened.loadRun("run-1", scope: "owner")
    XCTAssertEqual(restoredRun?.committedNoteIDs, ["assistant-note"])
  }

  func testFailedTransactionLeavesNoNoteReceiptOrProgress() async throws {
    let repository = try LocalBookCourseRepository(directory: folder())
    let saved = try await repository.saveRun(run(scope: "owner"))
    var forged = toolNote(saved)
    forged.references?[0].quote = "模型编造的原文"
    do {
      _ = try await repository.commitToolNote(run: saved, toolCallID: "call-1", argumentsHash: "hash-a",
        note: forged, result: "{}")
      XCTFail("Citations must come from registered evidence")
    } catch { XCTAssertEqual(error as? NoteStoreError, .untrustedReference(evidenceID: "ev-1")) }
    await repository.setWriteFaultForTesting(true)
    do {
      _ = try await repository.commitToolNote(run: saved, toolCallID: "call-1", argumentsHash: "hash-a",
        note: toolNote(saved), result: "{}")
      XCTFail("Write fault must surface")
    } catch {}
    await repository.setWriteFaultForTesting(false)
    let notes = try await repository.loadStoredNotes(scope: "owner")
    XCTAssertTrue(notes.isEmpty)
    let receipt = try await repository.toolReceipt(scope: "owner", runID: "run-1", toolCallID: "call-1")
    XCTAssertNil(receipt)
    let stored = try await repository.loadRun("run-1", scope: "owner")
    XCTAssertTrue(stored?.committedNoteIDs.isEmpty == true)
    XCTAssertNil(stored?.steps[0].outcome("call-1"))
  }

  func testCancelledRunRefusesFurtherWritesButKeepsCommittedNotes() async throws {
    let repository = try LocalBookCourseRepository(directory: folder())
    var saved = try await repository.saveRun(run(scope: "owner"))
    let committed = try await repository.commitToolNote(run: saved, toolCallID: "call-1",
      argumentsHash: "hash-a", note: toolNote(saved), result: "{}")
    saved = try XCTUnwrap(committed.run)
    saved.cancelRequested = true
    saved.status = .cancelled
    saved.steps[0].toolCalls.append(AgentToolCall(id: "call-2", name: "create_note", argumentsJSON: "{}",
      argumentsHash: "hash-b"))
    saved = try await repository.saveRun(saved)
    do {
      _ = try await repository.commitToolNote(run: saved, toolCallID: "call-2", argumentsHash: "hash-b",
        note: toolNote(saved, id: "after-cancel"), result: "{}")
      XCTFail("Cancelled runs execute no further tools")
    } catch { XCTAssertEqual(error as? NoteStoreError, .runClosed(runID: "run-1")) }
    let notes = try await repository.loadStoredNotes(scope: "owner")
    XCTAssertEqual(notes.map(\.note.id), ["assistant-note"])
    // A stale checkpoint (older version) cannot clear the committed cancellation.
    var stale = saved
    stale.version -= 1
    do { _ = try await repository.saveRun(stale); XCTFail("Stale checkpoint must conflict") }
    catch { XCTAssertEqual(error as? NoteStoreError, .runConflict(runID: "run-1")) }
  }

  func testToolNoteProvenanceMustMatchRunAndToolCall() async throws {
    let repository = try LocalBookCourseRepository(directory: folder())
    let saved = try await repository.saveRun(run(scope: "owner"))
    var wrongRun = toolNote(saved)
    wrongRun.provenance?.runID = "run-other"
    do {
      _ = try await repository.commitToolNote(run: saved, toolCallID: "call-1", argumentsHash: "hash-a",
        note: wrongRun, result: "{}")
      XCTFail("Provenance from another run must be refused")
    } catch { XCTAssertEqual(error as? NoteStoreError, .scopeMismatch) }
    var wrongCall = toolNote(saved)
    wrongCall.provenance?.toolCallID = "call-2"
    do {
      _ = try await repository.commitToolNote(run: saved, toolCallID: "call-1", argumentsHash: "hash-a",
        note: wrongCall, result: "{}")
      XCTFail("Provenance from another tool call must be refused")
    } catch { XCTAssertEqual(error as? NoteStoreError, .scopeMismatch) }
    let notes = try await repository.loadStoredNotes(scope: "owner")
    XCTAssertTrue(notes.isEmpty)
  }

  func testRevisionConflictKeepsLatestBodyAndScopesStayIsolated() async throws {
    let repository = try LocalBookCourseRepository(directory: folder())
    let note = StudyNote(id: "shared-id", kind: .text, title: "笔记", body: "第一版")
    let first = try await repository.mutateNote(note, scope: "owner-a", condition: .absent)
    var newer = note
    newer.body = "助手之后写入的最新正文"
    _ = try await repository.mutateNote(newer, scope: "owner-a", condition: .revision(first.revision))
    var stale = note
    stale.body = "旧编辑器的草稿"
    do {
      _ = try await repository.mutateNote(stale, scope: "owner-a", condition: .revision(first.revision))
      XCTFail("Stale base revision must conflict")
    } catch { XCTAssertEqual(error as? NoteStoreError, .revisionConflict(noteID: "shared-id")) }
    let latest = try await repository.loadStoredNote("shared-id", scope: "owner-a")
    XCTAssertEqual(latest?.note.body, newer.body)
    XCTAssertEqual(latest?.revision, 2)
    var other = note
    other.body = "另一账号"
    _ = try await repository.mutateNote(other, scope: "owner-b", condition: .absent)
    let a = try await repository.loadStoredNote("shared-id", scope: "owner-a")
    let b = try await repository.loadStoredNote("shared-id", scope: "owner-b")
    XCTAssertEqual(a?.note.body, newer.body)
    XCTAssertEqual(b?.note.body, "另一账号")

    // A run frozen for another account cannot write into this scope.
    var foreign = run(scope: "owner-b", id: "run-b")
    foreign = try await repository.saveRun(foreign)
    var misdirected = foreign
    misdirected.scope = "owner-a"
    do {
      _ = try await repository.commitToolNote(run: misdirected, toolCallID: "call-1", argumentsHash: "hash-a",
        note: toolNote(foreign), result: "{}")
      XCTFail("Scope mismatch must be refused")
    } catch {}
    let leaked = try await repository.loadStoredNotes(scope: "owner-a")
    XCTAssertFalse(leaked.contains { $0.note.id == "assistant-note" })
    // A note for a different book than the frozen context is refused.
    do {
      var wrongBook = toolNote(foreign)
      wrongBook.anchor?.bookID = "book-other"
      _ = try await repository.commitToolNote(run: foreign, toolCallID: "call-1", argumentsHash: "hash-a",
        note: wrongBook, result: "{}")
      XCTFail("Book outside the frozen context must be refused")
    } catch { XCTAssertEqual(error as? NoteStoreError, .scopeMismatch) }
    // A note for a different course than the frozen context is refused.
    do {
      var wrongCourse = toolNote(foreign)
      wrongCourse.anchor?.courseID = "course-other"
      _ = try await repository.commitToolNote(run: foreign, toolCallID: "call-1", argumentsHash: "hash-a",
        note: wrongCourse, result: "{}")
      XCTFail("Course outside the frozen context must be refused")
    } catch { XCTAssertEqual(error as? NoteStoreError, .scopeMismatch) }
  }

  func testDeletedNoteIsNotResurrectedBySnapshot() async throws {
    let repository = try LocalBookCourseRepository(directory: folder())
    let note = StudyNote(id: "gone", kind: .text, title: "删除", body: "正文")
    _ = try await repository.mutateNote(note, scope: "owner")
    try await repository.deleteNote("gone", scope: "owner")
    try await repository.adoptNotes([note], scope: "owner")
    let notes = try await repository.loadStoredNotes(scope: "owner")
    XCTAssertTrue(notes.isEmpty)
  }

  /// A version-1 database (records table only) migrates its local and cloud
  /// notes once, keeps the original rows, and keeps single anchors intact.
  func testLegacyNotesMigrateIntoScopedRowsWithoutLosingOriginals() async throws {
    let root = folder()
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let url = root.appendingPathComponent("bookcourse.sqlite")
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let local = StudyNote(id: "legacy-local", kind: .text, title: "旧笔记", body: "旧正文",
      anchor: NoteAnchor(courseID: "c", bookID: "b", page: 3, quote: "旧摘录"))
    let cloud = StudyNote(id: "legacy-cloud", kind: .voice, title: "旧云端笔记", body: "账号正文")
    let shell = LocalBookCourseRepository.CloudDiskState(
      state: CloudLocalSnapshot(snapshot: AppSnapshot(), sources: [], workspaces: [], filePaths: [:],
        pending: [:], creationWorkspaceID: nil),
      noteIDs: [cloud.id], sourceIDs: [])
    do {
      var handle: OpaquePointer?
      XCTAssertEqual(sqlite3_open(url.path, &handle), SQLITE_OK)
      defer { sqlite3_close(handle) }
      XCTAssertEqual(sqlite3_exec(handle,
        "CREATE TABLE records(kind TEXT NOT NULL,id TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(kind,id)); PRAGMA user_version=1;",
        nil, nil, nil), SQLITE_OK)
      for (kind, id, data) in [("note", local.id, try encoder.encode(local)),
                               ("cloud-note", "acct:" + cloud.id, try encoder.encode(cloud)),
                               ("cloud-shell-v2", "acct", try encoder.encode(shell))] {
        var statement: OpaquePointer?
        sqlite3_prepare_v2(handle, "INSERT INTO records VALUES(?,?,?)", -1, &statement, nil)
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        sqlite3_bind_text(statement, 1, kind, -1, transient)
        sqlite3_bind_text(statement, 2, id, -1, transient)
        _ = data.withUnsafeBytes { sqlite3_bind_blob(statement, 3, $0.baseAddress, Int32(data.count), transient) }
        XCTAssertEqual(sqlite3_step(statement), SQLITE_DONE)
        sqlite3_finalize(statement)
      }
    }
    let repository = try LocalBookCourseRepository(directory: root)
    let locals = try await repository.loadNotes()
    XCTAssertEqual(locals, [local])
    let state = try await repository.loadCloudState("acct")
    XCTAssertEqual(state?.notes, [cloud])
    let db = await repository.db
    XCTAssertEqual(try db.get("note").count, 1, "Legacy rows are retained for downgrade inspection")
    XCTAssertEqual(try db.userVersion(), SQLiteConnection.schemaVersion)
    // Migrating twice never duplicates or rolls back edits.
    var edited = local
    edited.body = "迁移后修改"
    try await repository.saveNote(edited)
    let again = try LocalBookCourseRepository(directory: root)
    let afterReopen = try await again.loadNotes()
    XCTAssertEqual(afterReopen.map(\.body), ["迁移后修改"])
  }

  func testNewerSchemaIsReadOnly() async throws {
    let root = folder()
    _ = try LocalBookCourseRepository(directory: root)
    do {
      var handle: OpaquePointer?
      sqlite3_open(root.appendingPathComponent("bookcourse.sqlite").path, &handle)
      sqlite3_exec(handle, "PRAGMA user_version=99", nil, nil, nil)
      sqlite3_close(handle)
    }
    let newer = try LocalBookCourseRepository(directory: root)
    // Reads on a newer schema must be empty, not a failed migration write.
    let sources = try await newer.loadSources()
    XCTAssertTrue(sources.isEmpty)
    let notes = try await newer.loadNotes()
    XCTAssertTrue(notes.isEmpty)
    do {
      try await newer.saveNote(StudyNote(kind: .text, title: "x", body: "y"))
      XCTFail("A newer database must refuse writes")
    } catch { XCTAssertTrue(error.localizedDescription.contains("更新版本")) }
  }

  func testNewerSchemaWithoutRecordsTableDoesNotCreateOne() async throws {
    let root = folder()
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let url = root.appendingPathComponent("bookcourse.sqlite")
    do {
      var handle: OpaquePointer?
      XCTAssertEqual(sqlite3_open(url.path, &handle), SQLITE_OK)
      defer { sqlite3_close(handle) }
      XCTAssertEqual(sqlite3_exec(handle, "PRAGMA user_version=99", nil, nil, nil), SQLITE_OK)
    }
    let newer = try LocalBookCourseRepository(directory: root)
    let db = await newer.db
    XCTAssertEqual(try db.userVersion(), 99)
    let tables = try db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='records'")
    XCTAssertTrue(tables.isEmpty, "A newer database must not gain a records table")
    // Reads against the absent table are empty rather than a thrown error.
    XCTAssertTrue(try db.get("note").isEmpty)
    XCTAssertFalse(try db.contains("note", "any"))
    let notes = try await newer.loadNotes()
    XCTAssertTrue(notes.isEmpty)
    let stored = try await newer.loadStoredNote("any", scope: LocalBookCourseRepository.localNoteScope)
    XCTAssertNil(stored)
    let snapshot = try await newer.loadSnapshot()
    XCTAssertNil(snapshot)
    do {
      try await newer.saveNote(StudyNote(kind: .text, title: "x", body: "y"))
      XCTFail("A newer database must refuse writes")
    } catch { XCTAssertTrue(error.localizedDescription.contains("更新版本")) }
  }

  /// A local upload received its server book ID: only anchors move, citations
  /// keep the book and source version they were captured from.
  func testRebindNoteAnchorsMovesBookAndKeepsCitationFields() async throws {
    let repository = try LocalBookCourseRepository(directory: folder())
    let saved = try await repository.saveRun(run(scope: "owner"))
    _ = try await repository.commitToolNote(
      run: saved, toolCallID: "call-1", argumentsHash: "hash-a",
      note: toolNote(saved), result: "{}")

    let rebound = try await repository.rebindNoteAnchors(
      scope: "owner", from: "book-a", to: "book-real")
    let stored = try XCTUnwrap(rebound.first { $0.note.id == "assistant-note" })
    XCTAssertEqual(stored.note.anchor?.bookID, "book-real")
    let reference = try XCTUnwrap(stored.note.references?.first)
    XCTAssertEqual(reference.sourceVersion, "sha256:v1")
    XCTAssertEqual(reference.bookID, "book-a")
    XCTAssertEqual(reference.page, 11)
    XCTAssertEqual(reference.printedPage, 16)
    XCTAssertEqual(reference.quote, "染色体只复制一次，而细胞分裂两次。")
  }

  @MainActor func testEditorSaveAgainstStaleRevisionDoesNotOverwriteAssistantWrite() async throws {
    let repository = try LocalBookCourseRepository(directory: folder())
    let store = AppStore(repository: repository)
    let note = StudyNote(id: "edited", kind: .text, title: "笔记", body: "初稿")
    let base = try await store.saveNote(note)
    var assistant = note
    assistant.body = "其他位置写入的最新正文"
    _ = try await store.saveNote(assistant, baseRevision: base)
    var stale = note
    stale.body = "旧编辑器草稿"
    do {
      _ = try await store.saveNote(stale, baseRevision: base)
      XCTFail("Stale editor must conflict")
    } catch { XCTAssertNotNil(error as? NoteStoreError) }
    XCTAssertEqual(store.notes.first?.body, assistant.body)
    let stored = try await repository.loadNotes()
    XCTAssertEqual(stored.first?.body, assistant.body)
  }

  /// After a cache restore the revision map is empty while the editor still
  /// holds the body it opened; saving that draft must not overwrite a newer row.
  @MainActor func testEditorSaveWithoutKnownRevisionDetectsNewerStoredBody() async throws {
    let repository = try LocalBookCourseRepository(directory: folder())
    let store = AppStore(repository: repository)
    let scope = try XCTUnwrap(store.noteScope)
    let note = StudyNote(id: "cas-note", kind: .text, title: "笔记", body: "初稿")
    let base = try await store.saveNote(note)
    var newer = note
    newer.body = "其他位置写入的最新正文"
    _ = try await repository.mutateNote(newer, scope: scope, condition: .revision(base))
    // Simulate reloadNotes after the other writer: memory and the revision map
    // both hold the newer row while the editor still shows the old copy.
    let loaded = try await repository.loadStoredNote("cas-note", scope: scope)
    let storedNewer = try XCTUnwrap(loaded)
    store.notes = [storedNewer.note]
    store.noteState.revisions = [storedNewer.note.id: storedNewer.revision]

    var draft = note
    draft.body = "旧编辑器草稿"
    do {
      _ = try await store.saveNote(draft, opened: note)
      XCTFail("A save without a known base revision must detect the newer stored body")
    } catch {
      XCTAssertEqual(error as? NoteStoreError, .revisionConflict(noteID: "cas-note"))
    }
    XCTAssertEqual(store.notes.first?.body, "其他位置写入的最新正文")
    let stored = try await repository.loadStoredNote("cas-note", scope: scope)
    XCTAssertEqual(stored?.note.body, "其他位置写入的最新正文")
  }
}
