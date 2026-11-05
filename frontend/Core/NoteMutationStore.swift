import Foundation
import SQLite3

/// Bound parameter or column value for the note store's typed statements.
enum SQLValue: Hashable, Sendable {
  case text(String), int(Int), real(Double), blob(Data), null
  var text: String? { if case .text(let v) = self { v } else { nil } }
  var int: Int? { if case .int(let v) = self { v } else { nil } }
  var blob: Data? { if case .blob(let v) = self { v } else { nil } }
}

extension SQLiteConnection {
  private func prepare(_ sql: String, _ values: [SQLValue]) throws -> OpaquePointer? {
    var statement: OpaquePointer?
    guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK else { throw failure() }
    let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    for (offset, value) in values.enumerated() {
      let i = Int32(offset + 1)
      switch value {
      case .text(let v): sqlite3_bind_text(statement, i, v, -1, transient)
      case .int(let v): sqlite3_bind_int64(statement, i, Int64(v))
      case .real(let v): sqlite3_bind_double(statement, i, v)
      case .blob(let v):
        _ = v.withUnsafeBytes { sqlite3_bind_blob(statement, i, $0.baseAddress, Int32(v.count), transient) }
      case .null: sqlite3_bind_null(statement, i)
      }
    }
    return statement
  }

  func run(_ sql: String, _ values: [SQLValue] = []) throws {
    try checkWritable()
    let statement = try prepare(sql, values)
    defer { sqlite3_finalize(statement) }
    guard sqlite3_step(statement) == SQLITE_DONE else { throw failure() }
    #if DEBUG
    writeCounts["notes", default: 0] += 1
    #endif
  }

  func query(_ sql: String, _ values: [SQLValue] = []) throws -> [[SQLValue]] {
    let statement = try prepare(sql, values)
    defer { sqlite3_finalize(statement) }
    var rows: [[SQLValue]] = []
    while true {
      let code = sqlite3_step(statement)
      if code == SQLITE_DONE { break }
      guard code == SQLITE_ROW else { throw failure() }
      rows.append((0..<sqlite3_column_count(statement)).map { column in
        switch sqlite3_column_type(statement, column) {
        case SQLITE_INTEGER: .int(Int(sqlite3_column_int64(statement, column)))
        case SQLITE_FLOAT: .real(sqlite3_column_double(statement, column))
        case SQLITE_TEXT: .text(String(cString: sqlite3_column_text(statement, column)))
        case SQLITE_BLOB:
          sqlite3_column_blob(statement, column).map {
            .blob(Data(bytes: $0, count: Int(sqlite3_column_bytes(statement, column))))
          } ?? .blob(Data())
        default: .null
        }
      })
    }
    return rows
  }

  /// Runs `body` in one IMMEDIATE transaction; any thrown error rolls it back.
  func transaction<T>(_ body: () throws -> T) throws -> T {
    try checkWritable()
    try execute("BEGIN IMMEDIATE")
    do {
      let value = try body()
      try execute("COMMIT")
      return value
    } catch {
      try? execute("ROLLBACK")
      throw error
    }
  }
}

enum NoteStoreError: LocalizedError, Equatable {
  case revisionConflict(noteID: String)
  case receiptConflict(toolCallID: String)
  case runConflict(runID: String)
  case runClosed(runID: String)
  case scopeMismatch
  case untrustedReference(evidenceID: String)
  var errorDescription: String? {
    switch self {
    case .revisionConflict: "笔记已在其他位置更新，当前内容未覆盖最新版本；草稿仍保留，请重新打开笔记后再修改。"
    case .receiptConflict: "同一工具调用的参数与已保存记录不一致，已拒绝执行。"
    case .runConflict: "助手任务已在其他位置更新，请重新打开任务。"
    case .runClosed: "助手任务已取消或结束，不再执行后续操作。"
    case .scopeMismatch: "任务不属于当前账号或教材范围，已拒绝保存。"
    case .untrustedReference: "引用不是本机登记的原文证据，已拒绝保存。"
    }
  }
}

/// How a write relates to the row the caller last saw.
enum NoteWriteCondition: Sendable, Equatable {
  /// Last writer wins; used by owners such as the recorder that hold no editor base.
  case any
  /// The note must never have existed in this scope (assistant creations).
  case absent
  /// Compare-and-swap against the caller's base revision.
  case revision(Int)
}

struct StoredNote: Sendable, Equatable {
  var note: StudyNote
  var revision: Int
}

/// Outcome of a tool write. `replayed` means an earlier commit is returned unchanged.
struct ToolNoteCommit: Sendable {
  var noteID: String
  var revision: Int
  var replayed: Bool
  var run: AgentRunRecord?
}

struct ToolReceipt: Sendable, Equatable {
  var runID: String
  var toolCallID: String
  var argumentsHash: String
  var noteID: String?
  var revision: Int?
  var result: String
}

/// Notes, citations, tool receipts and agent runs share bookcourse.sqlite so one
/// transaction commits a tool's note together with its receipt and run progress.
extension LocalBookCourseRepository {
  static let localNoteScope = "local"

  static func migrateNoteStore(_ db: SQLiteConnection, decoder: JSONDecoder, encoder: JSONEncoder) throws {
    try db.transaction {
      try db.execute("""
        CREATE TABLE IF NOT EXISTS notes(scope TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
          payload BLOB NOT NULL,updated_at REAL NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(scope,id));
        CREATE TABLE IF NOT EXISTS note_references(scope TEXT NOT NULL,note_id TEXT NOT NULL,evidence_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(scope,note_id,evidence_id));
        CREATE TABLE IF NOT EXISTS agent_runs(scope TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,
          payload BLOB NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(scope,id));
        CREATE TABLE IF NOT EXISTS tool_receipts(scope TEXT NOT NULL,run_id TEXT NOT NULL,tool_call_id TEXT NOT NULL,
          arguments_hash TEXT NOT NULL,note_id TEXT,revision INTEGER,result TEXT NOT NULL,created_at REAL NOT NULL,
          PRIMARY KEY(scope,run_id,tool_call_id));
        """)
      guard try db.userVersion() < SQLiteConnection.schemaVersion else { return }
      // Legacy rows stay in `records` untouched. INSERT OR IGNORE keeps a later
      // revision if an older build reset user_version and this runs again.
      func adopt(_ bytes: Data, scope: String) throws {
        guard let note = try? decoder.decode(StudyNote.self, from: bytes) else { return }
        try db.run(
          "INSERT OR IGNORE INTO notes(scope,id,revision,payload,updated_at) VALUES(?,?,1,?,?)",
          [.text(scope), .text(note.id), .blob(bytes), .real(note.updatedAt.timeIntervalSince1970)])
      }
      for bytes in try db.get("note") { try adopt(bytes, scope: localNoteScope) }
      for row in try db.query("SELECT id,payload FROM records WHERE kind='cloud-shell-v2'") {
        guard let key = row[0].text, let data = row[1].blob,
          let disk = try? decoder.decode(CloudDiskState.self, from: data) else { continue }
        for id in disk.noteIDs {
          if let bytes = try db.get("cloud-note", key + ":" + id).first { try adopt(bytes, scope: key) }
        }
      }
      for row in try db.query("SELECT id,payload FROM records WHERE kind='cloud'") {
        guard let key = row[0].text, let data = row[1].blob,
          let legacy = try? decoder.decode(CloudLocalSnapshot.self, from: data) else { continue }
        for note in legacy.notes ?? [] { try adopt(encoder.encode(note), scope: key) }
      }
      try db.execute("PRAGMA user_version=\(SQLiteConnection.schemaVersion)")
    }
  }

  // MARK: Notes

  /// True after a failed prepare because a newer schema has no note tables.
  private var newerSchemaTableMissing: Bool {
    guard db.newerSchemaVersion != nil else { return false }
    guard let handle = db.handle else { return true }
    return String(cString: sqlite3_errmsg(handle)).contains("no such table")
  }

  private func references(scope: String, noteID: String) throws -> [NoteReference] {
    let rows: [[SQLValue]]
    do {
      rows = try db.query(
        "SELECT payload FROM note_references WHERE scope=? AND note_id=? ORDER BY ordinal",
        [.text(scope), .text(noteID)])
    } catch {
      if newerSchemaTableMissing { return [] }
      throw error
    }
    return try rows.compactMap { try $0[0].blob.map { try decoder.decode(NoteReference.self, from: $0) } }
  }

  private func row(scope: String, id: String) throws -> (revision: Int, deleted: Bool)? {
    try db.query("SELECT revision,deleted FROM notes WHERE scope=? AND id=?", [.text(scope), .text(id)])
      .first.map { ($0[0].int ?? 0, $0[1].int == 1) }
  }

  private func decodeStored(_ row: [SQLValue], scope: String) throws -> StoredNote? {
    guard let bytes = row[0].blob, var note = try? decoder.decode(StudyNote.self, from: bytes) else { return nil }
    let references = try references(scope: scope, noteID: note.id)
    note.references = references.isEmpty ? nil : references
    return StoredNote(note: note, revision: row[1].int ?? 0)
  }

  func loadStoredNotes(scope: String) throws -> [StoredNote] {
    let rows: [[SQLValue]]
    do {
      rows = try db.query(
        "SELECT payload,revision FROM notes WHERE scope=? AND deleted=0", [.text(scope)])
    } catch {
      if newerSchemaTableMissing { return [] }
      throw error
    }
    return try rows
      .compactMap { try decodeStored($0, scope: scope) }
      .sorted { $0.note.updatedAt > $1.note.updatedAt }
  }

  func loadStoredNote(_ id: String, scope: String) throws -> StoredNote? {
    let rows: [[SQLValue]]
    do {
      rows = try db.query(
        "SELECT payload,revision FROM notes WHERE scope=? AND id=? AND deleted=0",
        [.text(scope), .text(id)])
    } catch {
      if newerSchemaTableMissing { return nil }
      throw error
    }
    return try rows.first.flatMap { try decodeStored($0, scope: scope) }
  }

  /// Caller must hold a transaction. `note.references == nil` keeps stored citations.
  func writeNote(_ note: StudyNote, scope: String, condition: NoteWriteCondition) throws -> StoredNote {
    if let courseID = note.anchor?.courseID { try requireLiveCourse(courseID: courseID, scope: scope) }
    let current = try row(scope: scope, id: note.id)
    switch condition {
    case .any: break
    case .absent: if current != nil { throw NoteStoreError.revisionConflict(noteID: note.id) }
    case .revision(let base):
      guard let current, !current.deleted, current.revision == base else {
        throw NoteStoreError.revisionConflict(noteID: note.id)
      }
    }
    var stored = note
    stored.references = nil
    let revision = (current?.revision ?? 0) + 1
    try db.run(
      "INSERT OR REPLACE INTO notes(scope,id,revision,payload,updated_at,deleted) VALUES(?,?,?,?,?,0)",
      [.text(scope), .text(note.id), .int(revision), .blob(encoder.encode(stored)),
       .real(note.updatedAt.timeIntervalSince1970)])
    if let references = note.references {
      try db.run("DELETE FROM note_references WHERE scope=? AND note_id=?", [.text(scope), .text(note.id)])
      for (ordinal, reference) in references.enumerated() {
        try db.run(
          "INSERT OR REPLACE INTO note_references(scope,note_id,evidence_id,ordinal,payload) VALUES(?,?,?,?,?)",
          [.text(scope), .text(note.id), .text(reference.evidenceID), .int(ordinal),
           .blob(encoder.encode(reference))])
      }
    }
    var result = note
    if result.references == nil {
      let kept = try references(scope: scope, noteID: note.id)
      result.references = kept.isEmpty ? nil : kept
    } else if result.references?.isEmpty == true {
      result.references = nil
    }
    return StoredNote(note: result, revision: revision)
  }

  func mutateNote(_ note: StudyNote, scope: String, condition: NoteWriteCondition = .any) throws -> StoredNote {
    try db.transaction { try writeNote(note, scope: scope, condition: condition) }
  }

  /// Deletion keeps a revisioned tombstone so stale snapshots cannot resurrect it.
  func deleteNote(_ id: String, scope: String) throws {
    let paths = try loadStoredNote(id, scope: scope)?.note.photos?.flatMap { [$0.originalPath, $0.preparedPath] } ?? []
    try db.transaction { try tombstone(id, scope: scope) }
    for path in paths { try? removeCourseFileIfUnreferenced(attachmentURL(path)) }
  }

  /// Caller must hold a transaction.
  func tombstone(_ id: String, scope: String) throws {
    guard let current = try row(scope: scope, id: id), !current.deleted else { return }
    try db.run(
      "UPDATE notes SET revision=?,payload=X'',deleted=1,updated_at=? WHERE scope=? AND id=?",
      [.int(current.revision + 1), .real(Date.now.timeIntervalSince1970), .text(scope), .text(id)])
    try db.run("DELETE FROM note_references WHERE scope=? AND note_id=?", [.text(scope), .text(id)])
  }

  /// A local upload received its server book ID. Only anchors move; citations
  /// keep the book and version they were captured from.
  func rebindNoteAnchors(scope: String, from localID: String, to bookID: String) throws -> [StoredNote] {
    try db.transaction {
      for stored in try loadStoredNotes(scope: scope) where stored.note.anchor?.bookID == localID {
        var note = stored.note
        note.anchor?.bookID = bookID
        note.references = nil
        _ = try writeNote(note, scope: scope, condition: .revision(stored.revision))
      }
      return try loadStoredNotes(scope: scope)
    }
  }

  /// Existing snapshot notes are imported once; a snapshot never overwrites or
  /// deletes a committed note, so stale cloud caches cannot roll content back.
  /// Caller must hold a transaction.
  func importSnapshotNotes(_ notes: [StudyNote], scope: String) throws {
    for note in notes where try row(scope: scope, id: note.id) == nil {
      _ = try writeNote(note, scope: scope, condition: .absent)
    }
  }

  func adoptNotes(_ notes: [StudyNote], scope: String) throws {
    try db.transaction { try importSnapshotNotes(notes, scope: scope) }
  }

  // MARK: Agent runs and tool receipts

  func loadRuns(scope: String) throws -> [AgentRunRecord] {
    try db.query("SELECT payload FROM agent_runs WHERE scope=? ORDER BY updated_at DESC", [.text(scope)])
      .compactMap { try $0[0].blob.map { try decoder.decode(AgentRunRecord.self, from: $0) } }
  }

  func loadRun(_ id: String, scope: String) throws -> AgentRunRecord? {
    try db.query("SELECT payload FROM agent_runs WHERE scope=? AND id=?", [.text(scope), .text(id)])
      .first?[0].blob.map { try decoder.decode(AgentRunRecord.self, from: $0) }
  }

  private func writeRun(_ run: AgentRunRecord, scope: String) throws -> AgentRunRecord {
    guard run.scope == scope, run.context.scope == scope else { throw NoteStoreError.scopeMismatch }
    try requireLiveAgentRun(runID: run.id, scope: scope)
    if let courseID = run.context.courseID { try requireLiveCourse(courseID: courseID, scope: scope) }
    let stored = try loadRun(run.id, scope: scope)
    guard (stored?.version ?? 0) == run.version else { throw NoteStoreError.runConflict(runID: run.id) }
    var next = CourseDeletionPruning.runReferences(run, tombstones: try courseDeletionTombstones(scope: scope))
    // A committed cancellation can never be cleared by a later checkpoint.
    if stored?.cancelRequested == true { next.cancelRequested = true }
    next.version = run.version + 1
    next.updatedAt = .now
    try db.run(
      "INSERT OR REPLACE INTO agent_runs(scope,id,version,payload,updated_at) VALUES(?,?,?,?,?)",
      [.text(scope), .text(run.id), .int(next.version), .blob(encoder.encode(next)),
       .real(next.updatedAt.timeIntervalSince1970)])
    return next
  }

  /// Version-checked checkpoint; the returned record carries the new version.
  func saveRun(_ run: AgentRunRecord) throws -> AgentRunRecord {
    try db.transaction { try writeRun(run, scope: run.scope) }
  }

  func toolReceipt(scope: String, runID: String, toolCallID: String) throws -> ToolReceipt? {
    try db.query(
      "SELECT arguments_hash,note_id,revision,result FROM tool_receipts WHERE scope=? AND run_id=? AND tool_call_id=?",
      [.text(scope), .text(runID), .text(toolCallID)]
    ).first.map {
      ToolReceipt(runID: runID, toolCallID: toolCallID, argumentsHash: $0[0].text ?? "",
        noteID: $0[1].text, revision: $0[2].int, result: $0[3].text ?? "")
    }
  }

  /// Creates the note, its executor-made citations, the tool receipt and the
  /// run's outcome in one transaction. The key is (scope, run, tool call): a
  /// replay returns the original note and revision; different arguments conflict.
  func commitToolNote(
    run: AgentRunRecord, toolCallID: String, argumentsHash: String, note: StudyNote, result: String,
    verifiedCourseResourceID: String? = nil
  ) throws -> ToolNoteCommit {
    let scope = run.scope
    return try db.transaction {
      try requireLiveAgentRun(runID: run.id, scope: scope)
      if let courseID = run.context.courseID { try requireLiveCourse(courseID: courseID, scope: scope) }
      if let receipt = try toolReceipt(scope: scope, runID: run.id, toolCallID: toolCallID) {
        guard receipt.argumentsHash == argumentsHash, let noteID = receipt.noteID, let revision = receipt.revision
        else { throw NoteStoreError.receiptConflict(toolCallID: toolCallID) }
        return ToolNoteCommit(noteID: noteID, revision: revision, replayed: true, run: try loadRun(run.id, scope: scope))
      }
      guard let stored = try loadRun(run.id, scope: scope) else { throw NoteStoreError.runConflict(runID: run.id) }
      guard !stored.cancelRequested, !stored.status.isTerminal else { throw NoteStoreError.runClosed(runID: run.id) }
      guard run.context.scope == scope else { throw NoteStoreError.scopeMismatch }
      if let anchor = note.anchor {
        guard anchor.courseID != nil, anchor.courseResourceID != nil, !anchor.bookID.isEmpty else {
          throw NoteStoreError.scopeMismatch
        }
        if let verifiedCourseResourceID {
          guard anchor.courseResourceID == verifiedCourseResourceID else { throw NoteStoreError.scopeMismatch }
        } else {
          guard anchor.bookID == run.context.bookID, anchor.courseID == run.context.courseID,
            anchor.courseResourceID == run.context.courseResourceID else { throw NoteStoreError.scopeMismatch }
        }
      }
      if let provenance = note.provenance {
        guard provenance.runID == run.id, provenance.toolCallID == toolCallID else {
          throw NoteStoreError.scopeMismatch
        }
      }
      for reference in note.references ?? [] {
        if let courseID = reference.courseID { try requireLiveCourse(courseID: courseID, scope: scope) }
        guard let evidence = run.evidence(reference.evidenceID), NoteReference(evidence) == reference else {
          throw NoteStoreError.untrustedReference(evidenceID: reference.evidenceID)
        }
      }
      let written = try writeNote(note, scope: scope, condition: .absent)
      try db.run(
        "INSERT INTO tool_receipts(scope,run_id,tool_call_id,arguments_hash,note_id,revision,result,created_at) VALUES(?,?,?,?,?,?,?,?)",
        [.text(scope), .text(run.id), .text(toolCallID), .text(argumentsHash), .text(note.id),
         .int(written.revision), .text(result), .real(Date.now.timeIntervalSince1970)])
      var next = run
      for i in next.steps.indices where next.steps[i].toolCalls.contains(where: { $0.id == toolCallID }) {
        next.steps[i].outcomes.removeAll { $0.toolCallID == toolCallID }
        next.steps[i].outcomes.append(AgentToolOutcome(
          toolCallID: toolCallID, status: "ok", resultJSON: result, noteID: note.id, noteRevision: written.revision))
      }
      if !next.committedNoteIDs.contains(note.id) { next.committedNoteIDs.append(note.id) }
      let saved = try writeRun(next, scope: scope)
      return ToolNoteCommit(noteID: note.id, revision: written.revision, replayed: false, run: saved)
    }
  }
}

struct NoteCourseResource: Sendable, Equatable {
  var courseID: String
  var bookID: String
  var resourceID: String
}

extension LocalBookCourseRepository {
  /// Repeatable migration: explicit ownership always wins, ambiguous anchors stay unassigned.
  func migrateNoteOwnership(scope: String, relationships: [NoteCourseResource]) throws -> [StoredNote] {
    try db.transaction {
      for stored in try loadStoredNotes(scope: scope) {
        guard let anchor = stored.note.anchor else { continue }
        var note = stored.note
        var changed = note.conversation != nil
        note.conversation = nil
        if anchor.courseResourceID == nil {
          let matches = relationships.filter {
            $0.bookID == anchor.bookID && (anchor.courseID == nil || $0.courseID == anchor.courseID)
          }
          if matches.count == 1, let match = matches.first {
            note.anchor?.courseID = match.courseID
            note.anchor?.courseResourceID = match.resourceID
            changed = true
          }
        }
        if changed { _ = try writeNote(note, scope: scope, condition: .revision(stored.revision)) }
      }
      return try loadStoredNotes(scope: scope)
    }
  }

  func deleteCourseNotes(scope: String, courseID: String, resourceIDs: Set<String>) throws -> [String] {
    try db.transaction {
      let ids = try loadStoredNotes(scope: scope).filter { stored in
        guard let anchor = stored.note.anchor else { return false }
        return anchor.courseResourceID.map(resourceIDs.contains) == true
      }.map { $0.note.id }
      for id in ids { try tombstone(id, scope: scope) }
      return ids
    }
  }
}

extension LocalBookCourseRepository {
  /// Temporary chat commits only the artifact and a minimal operation receipt; no run or chat text.
  func commitEphemeralToolNote(scope: String, runID: String, toolCallID: String,
    argumentsHash: String, note: StudyNote) throws -> ToolNoteCommit {
    try db.transaction {
      try requireLiveAgentRun(runID: runID, scope: scope)
      if let receipt = try toolReceipt(scope: scope, runID: runID, toolCallID: toolCallID) {
        guard receipt.argumentsHash == argumentsHash, let noteID = receipt.noteID, let revision = receipt.revision else {
          throw NoteStoreError.receiptConflict(toolCallID: toolCallID)
        }
        return ToolNoteCommit(noteID: noteID, revision: revision, replayed: true)
      }
      guard note.anchor == nil || (note.anchor?.courseID != nil && note.anchor?.courseResourceID != nil) else {
        throw NoteStoreError.scopeMismatch
      }
      var artifact = note
      artifact.conversation = nil
      let written = try writeNote(artifact, scope: scope, condition: .absent)
      let result = try AgentDigest.canonicalJSON(["note_id": artifact.id, "revision": written.revision])
      try db.run(
        "INSERT INTO tool_receipts(scope,run_id,tool_call_id,arguments_hash,note_id,revision,result,created_at) VALUES(?,?,?,?,?,?,?,?)",
        [.text(scope), .text(runID), .text(toolCallID), .text(argumentsHash), .text(artifact.id),
         .int(written.revision), .text(result), .real(Date.now.timeIntervalSince1970)])
      return ToolNoteCommit(noteID: artifact.id, revision: written.revision, replayed: false)
    }
  }
}

extension LocalBookCourseRepository {
  /// Explicit promotion mapping, captured from the upload's course relationship.
  func rebindNoteCourse(scope: String, from oldCourseID: String, to courseID: String,
    resources: [String: String]) throws -> [StoredNote] {
    try db.transaction {
      for stored in try loadStoredNotes(scope: scope) where stored.note.anchor?.courseID == oldCourseID {
        guard let anchor = stored.note.anchor, let resourceID = resources[anchor.bookID] else { continue }
        var note = stored.note
        note.anchor?.courseID = courseID
        note.anchor?.courseResourceID = resourceID
        _ = try writeNote(note, scope: scope, condition: .revision(stored.revision))
      }
      return try loadStoredNotes(scope: scope)
    }
  }
}
