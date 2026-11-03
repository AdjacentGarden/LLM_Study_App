import CryptoKit
import Foundation
import SQLite3

protocol BookCourseRepository: Sendable {
  func loadSources() async throws -> [SourceContent]
  func saveSources(_ sources: [SourceContent]) async throws
  func loadSnapshot() async throws -> AppSnapshot?
  func saveSnapshot(_ snapshot: AppSnapshot) async throws
}
protocol StudyNoteRepository: Sendable {
  func loadNotes() async throws -> [StudyNote]
  func saveNote(_ note: StudyNote) async throws
  func deleteNote(_ id: String) async throws
}

/// SQLite's connection is confined to LocalBookCourseRepository's actor.
final class SQLiteConnection: @unchecked Sendable {
  /// Version 2 moves notes into revisioned rows beside agent runs and tool receipts.
  static let schemaVersion = 2
  var handle: OpaquePointer?
  var failWrites = false
  /// Set when a newer app wrote this file; reads continue but every write is refused.
  private(set) var newerSchemaVersion: Int?
  #if DEBUG
  var writeCounts: [String: Int] = [:]
  var writtenBytes = 0
  #endif
  init(url: URL) throws {
    guard
      sqlite3_open_v2(
        url.path, &handle, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil)
        == SQLITE_OK
    else { throw ContentError.storage("无法打开本地数据库") }
    // A newer app owns the file's schema. Its tables (and pragmas) must not be
    // touched, so inspect the version before any setup statement runs.
    let version = try userVersion()
    if version > Self.schemaVersion {
      newerSchemaVersion = version
    } else {
      try execute(
        "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(kind,id));"
      )
    }
  }
  /// True after a failed prepare because the newer schema has no `records` table.
  private var recordsTableMissing: Bool {
    guard let handle else { return true }
    return String(cString: sqlite3_errmsg(handle)).contains("no such table")
  }
  func userVersion() throws -> Int {
    var statement: OpaquePointer?
    guard sqlite3_prepare_v2(handle, "PRAGMA user_version", -1, &statement, nil) == SQLITE_OK else { throw failure() }
    defer { sqlite3_finalize(statement) }
    guard sqlite3_step(statement) == SQLITE_ROW else { throw failure() }
    return Int(sqlite3_column_int64(statement, 0))
  }
  func checkWritable() throws {
    guard !failWrites else { throw ContentError.storage("存储暂时不可写") }
    if newerSchemaVersion != nil {
      throw ContentError.storage("本机数据来自更新版本的云径，请更新应用后再保存")
    }
  }
  deinit { sqlite3_close(handle) }
  func execute(_ sql: String) throws {
    guard sqlite3_exec(handle, sql, nil, nil, nil) == SQLITE_OK else { throw failure() }
  }
  func failure() -> ContentError {
    .storage(handle.flatMap { sqlite3_errmsg($0) }.map { String(cString: $0) } ?? "数据库不可写")
  }
  func put(_ kind: String, _ id: String, _ bytes: Data) throws {
    try checkWritable()
    if try get(kind, id).first == bytes { return }
    var statement: OpaquePointer?
    guard
      sqlite3_prepare_v2(
        handle, "INSERT OR REPLACE INTO records(kind,id,payload) VALUES(?,?,?)", -1, &statement, nil
      ) == SQLITE_OK
    else { throw failure() }
    defer { sqlite3_finalize(statement) }
    let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    sqlite3_bind_text(statement, 1, kind, -1, transient)
    sqlite3_bind_text(statement, 2, id, -1, transient)
    _ = bytes.withUnsafeBytes {
      sqlite3_bind_blob(statement, 3, $0.baseAddress, Int32(bytes.count), transient)
    }
    guard sqlite3_step(statement) == SQLITE_DONE else { throw failure() }
    #if DEBUG
    writeCounts[kind, default: 0] += 1
    writtenBytes += bytes.count
    #endif
  }
  func contains(_ kind: String, _ id: String) throws -> Bool {
    var statement: OpaquePointer?
    guard sqlite3_prepare_v2(handle, "SELECT 1 FROM records WHERE kind=? AND id=?", -1, &statement, nil) == SQLITE_OK else {
      if recordsTableMissing { return false }
      throw failure()
    }
    defer { sqlite3_finalize(statement) }
    let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    sqlite3_bind_text(statement, 1, kind, -1, transient)
    sqlite3_bind_text(statement, 2, id, -1, transient)
    let result = sqlite3_step(statement)
    guard result == SQLITE_ROW || result == SQLITE_DONE else { throw failure() }
    return result == SQLITE_ROW
  }
  func get(_ kind: String, _ id: String? = nil) throws -> [Data] {
    var statement: OpaquePointer?
    let sql =
      id == nil
      ? "SELECT payload FROM records WHERE kind=? ORDER BY id"
      : "SELECT payload FROM records WHERE kind=? AND id=?"
    guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK else {
      if recordsTableMissing { return [] }
      throw failure()
    }
    defer { sqlite3_finalize(statement) }
    let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    sqlite3_bind_text(statement, 1, kind, -1, transient)
    if let id { sqlite3_bind_text(statement, 2, id, -1, transient) }
    var result: [Data] = []
    while true {
      let code = sqlite3_step(statement)
      if code == SQLITE_DONE { break }
      guard code == SQLITE_ROW else { throw failure() }
      if let pointer = sqlite3_column_blob(statement, 0) {
        result.append(Data(bytes: pointer, count: Int(sqlite3_column_bytes(statement, 0))))
      }
    }
    return result
  }
  func delete(_ kind: String, _ id: String) throws {
    try checkWritable()
    var statement: OpaquePointer?
    guard
      sqlite3_prepare_v2(handle, "DELETE FROM records WHERE kind=? AND id=?", -1, &statement, nil)
        == SQLITE_OK
    else { throw failure() }
    defer { sqlite3_finalize(statement) }
    let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    sqlite3_bind_text(statement, 1, kind, -1, transient)
    sqlite3_bind_text(statement, 2, id, -1, transient)
    guard sqlite3_step(statement) == SQLITE_DONE else { throw failure() }
  }
}
actor LocalBookCourseRepository: BookCourseRepository, StudyNoteRepository {
  nonisolated let directory: URL
  nonisolated let includesFixtureContent: Bool
  let db: SQLiteConnection
  let encoder = JSONEncoder(), decoder = JSONDecoder()
  init(directory: URL? = nil, includeFixtures: Bool = false) throws {
    let root =
      directory
      ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
      .appendingPathComponent("BookCourse", isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    self.directory = root
    #if DEBUG
      self.includesFixtureContent = includeFixtures
    #else
      self.includesFixtureContent = false
    #endif
    self.db = try SQLiteConnection(url: root.appendingPathComponent("bookcourse.sqlite"))
    self.encoder.outputFormatting = [.sortedKeys]
    try FileManager.default.createDirectory(
      at: root.appendingPathComponent("attachments"), withIntermediateDirectories: true)
    if db.newerSchemaVersion == nil { try Self.migrateNoteStore(db, decoder: decoder, encoder: encoder) }
  }
  init(includeFixtures: Bool, directory: URL?) throws {
    try self.init(directory: directory, includeFixtures: includeFixtures)
  }
  func loadSources() throws -> [SourceContent] {
    try retireUnchangedPreviewContent()
    let seed: [SourceContent] = includesFixtureContent ? try ContentBundle.load([SourceContent].self, name: "sources") : []
    let saved = try db.get("source").map { try decoder.decode(SourceContent.self, from: $0) }
    var sources = seed
    for source in saved {
      if let i = sources.firstIndex(where: { $0.id == source.id }) {
        sources[i] = source
      } else {
        sources.append(source)
      }
    }
    return sources
  }
  func saveSources(_ sources: [SourceContent]) throws {
    try db.execute("BEGIN IMMEDIATE")
    do {
      for source in sources { try db.put("source", source.id, encoder.encode(source)) }
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }
  func loadSnapshot() throws -> AppSnapshot? {
    try db.get("app", "state").first.map { try decoder.decode(AppSnapshot.self, from: $0) }
  }
  func saveSnapshot(_ snapshot: AppSnapshot) throws {
    try db.put("app", "state", encoder.encode(snapshot))
  }

  func loadAssistantHistory(scope: String) throws -> AssistantHistoryArchive? {
    try db.get("assistant-history", scope).first.map { try decoder.decode(AssistantHistoryArchive.self, from: $0) }
  }

  func saveAssistantHistory(_ value: AssistantHistoryArchive, scope: String) throws {
    try db.put("assistant-history", scope, encoder.encode(value))
  }

  struct CloudDiskState: Codable {
    var state: CloudLocalSnapshot
    var noteIDs: [String]
    var sourceIDs: [String]
  }

  private func contentID(_ contentKey: String, account: String) -> String {
    contentKey.hasPrefix("builtin:") ? contentKey : account + ":" + contentKey
  }

  func loadCloudContent(_ contentKey: String, account: String) throws -> CloudContent? {
    try db.get("cloud-content", contentID(contentKey, account: account)).first.map {
      try decoder.decode(CloudContent.self, from: $0)
    }
  }

  func saveCloudContent(_ content: CloudContent, contentKey: String, account: String) throws {
    try db.put("cloud-content", contentID(contentKey, account: account), encoder.encode(content))
  }

  func loadCourseJobReceipt(_ key: String) throws -> CloudCourseJobReceipt? {
    try db.get("cloud-course-job", key).first.map { try decoder.decode(CloudCourseJobReceipt.self, from: $0) }
  }

  func saveCourseJobReceipt(_ value: CloudCourseJobReceipt, key: String) throws {
    try db.put("cloud-course-job", key, encoder.encode(value))
  }

  func loadBookPreparationReceipt(_ key: String) throws -> CloudBookPreparationReceipt? {
    try db.get("cloud-book-preparation", key).first.map { try decoder.decode(CloudBookPreparationReceipt.self, from: $0) }
  }

  func saveBookPreparationReceipt(_ value: CloudBookPreparationReceipt, key: String) throws {
    try db.put("cloud-book-preparation", key, encoder.encode(value))
  }

  func loadCloudState(_ key: String) throws -> CloudLocalSnapshot? {
    if let data = try db.get("cloud-shell-v2", key).first {
      let disk = try decoder.decode(CloudDiskState.self, from: data)
      var state = disk.state
      for wi in state.workspaces.indices {
        for ri in state.workspaces[wi].resources.indices {
          let id = state.workspaces[wi].resources[ri].id
          if let bytes = try db.get("cloud-resource", key + ":" + id).first {
            var resource = try decoder.decode(CloudResource.self, from: bytes)
            if let version = resource.content_key,
              let content = try loadCloudContent(version, account: key) {
              resource = content.applying(to: resource)
            }
            state.workspaces[wi].resources[ri] = resource
          }
        }
      }
      // Notes are owned by the revisioned note table, never by this snapshot.
      state.notes = try loadStoredNotes(scope: key).map(\.note)
      state.sources = state.workspaces.flatMap(\.resources).map(CloudMapping.source)
      for id in disk.sourceIDs {
        guard let bytes = try db.get("cloud-source", key + ":" + id).first else { continue }
        let local = try decoder.decode(SourceContent.self, from: bytes)
        let matching = state.sources.indices.filter { state.sources[$0].id == local.id }
        if matching.isEmpty {
          state.sources.append(local)
        } else {
          for index in matching {
            state.sources[index] = Self.mergingLocalOCR(from: local, into: state.sources[index])
          }
        }
      }
      return state
    }
    guard let data = try db.get("cloud", key).first else { return nil }
    var legacy = try decoder.decode(CloudLocalSnapshot.self, from: data)
    legacy.notes = try loadStoredNotes(scope: key).map(\.note)
    return legacy
  }

  func saveCloudState(_ value: CloudLocalSnapshot, key: String) throws {
    var state = value
    let resourceBooks = Set(value.workspaces.flatMap(\.resources).map(\.book_id))
    let extraSources = value.sources.compactMap { source -> SourceContent? in
      guard resourceBooks.contains(source.id) else { return source }
      let chunks = Self.localOCRChunks(in: source)
      guard !chunks.isEmpty else { return nil }
      // Keep only the device text overlay. Server teaching bodies continue to
      // live once in their versioned content cache, independently of this input.
      return SourceContent(summary: source.summary, chapters: [], lessons: [], chunks: chunks,
        assets: [], flashcards: [], quizzes: [], plan: StudyPlan(
          user_id: "", book_id: source.id, days: 0, daily_minutes: 0, tasks: []))
    }
    try db.execute("BEGIN IMMEDIATE")
    do {
      for wi in state.workspaces.indices {
        for ri in state.workspaces[wi].resources.indices {
          var resource = state.workspaces[wi].resources[ri]
          let contentExists = try resource.content_key.map { try db.contains("cloud-content", contentID($0, account: key)) } ?? false
          if !contentExists && (resource.structure != nil || resource.source_content != nil || !resource.courses.isEmpty) {
            let content = CloudContent(resource: resource)
            let bytes = try encoder.encode(content)
            // Responses from old servers and mutation endpoints also gain stable content IDs.
            let version = resource.content_key ?? "legacy:" + SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
            try db.put("cloud-content", contentID(version, account: key), bytes)
            resource.content_key = version
          }
          resource.structure = nil
          resource.source_content = nil
          resource.courses = []
          try db.put("cloud-resource", key + ":" + resource.id, encoder.encode(resource))
          resource.progress = CloudProgress()
          resource.flashcard_reviews = nil
          state.workspaces[wi].resources[ri] = resource
        }
      }
      // Only first-seen notes are adopted (seeded caches); a snapshot can never
      // overwrite, roll back or resurrect a committed note.
      try importSnapshotNotes(value.notes ?? [], scope: key)
      for source in extraSources { try db.put("cloud-source", key + ":" + source.id, encoder.encode(source)) }
      state.sources = []
      state.notes = nil
      let disk = CloudDiskState(state: state, noteIDs: [], sourceIDs: extraSources.map(\.id))
      try db.put("cloud-shell-v2", key, encoder.encode(disk))
      try db.delete("cloud", key)
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }

  private static func localOCRChunks(in source: SourceContent) -> [SourceChunk] {
    source.chunks.filter {
      $0.book_id == source.id
        && $0.source_metadata?["execution_device"] == .string("ios-device")
        && $0.source_metadata?["network_required"] == .bool(false)
        && ["apple-vision-ios", "pdfkit-text"].contains(
          $0.source_metadata?["parser"]?.stringValue ?? "")
    }
  }

  /// Preserve fresh server chapters, lessons, assets and citation IDs while
  /// restoring account-local OCR text. Repeated installs do not duplicate it.
  static func mergingLocalOCR(from local: SourceContent, into server: SourceContent) -> SourceContent {
    guard local.id == server.id else { return server }
    let chunks = localOCRChunks(in: local)
    guard !chunks.isEmpty else { return server }
    var source = server
    let existingLocalIDs = Set(localOCRChunks(in: server).map(\.id))
    var positions: [String: Int] = [:]
    for (index, chunk) in source.chunks.enumerated() where positions[chunk.id] == nil {
      positions[chunk.id] = index
    }
    for chunk in chunks {
      if let index = positions[chunk.id] {
        if existingLocalIDs.contains(chunk.id) { source.chunks[index] = chunk }
      } else {
        positions[chunk.id] = source.chunks.count
        source.chunks.append(chunk)
      }
    }
    source.summary.chunk_count = source.chunks.count
    source.summary.page_count = max(source.summary.page_count, local.summary.page_count)
    source.summary.updated_at = max(source.summary.updated_at, local.summary.updated_at)
    return source
  }
  /// The unsigned-in (local) note scope; signed-in accounts use their cache key.
  func loadNotes() throws -> [StudyNote] {
    try retireUnchangedPreviewContent()
    return try loadStoredNotes(scope: Self.localNoteScope).map(\.note)
  }
  func saveNote(_ note: StudyNote) throws { _ = try mutateNote(note, scope: Self.localNoteScope) }
  func deleteNote(_ id: String) throws { try deleteNote(id, scope: Self.localNoteScope) }
  func saveAttachment(_ data: Data, extension ext: String) throws -> String {
    let safeExtension = ext.filter { $0.isLetter || $0.isNumber }.prefix(12)
    let relative = "attachments/\(UUID().uuidString).\(safeExtension)"
    try data.write(to: directory.appendingPathComponent(relative), options: .atomic)
    return relative
  }
  /// Keep large extracted documents outside the conversation's JSON snapshot.
  func saveAssistantAttachmentText(_ chunks: [SourceChunk]) throws -> (path: String, characterCount: Int) {
    let relative = "attachments/\(UUID().uuidString).txt"
    let destination = directory.appendingPathComponent(relative)
    let temporary = destination.appendingPathExtension("tmp")
    guard FileManager.default.createFile(atPath: temporary.path, contents: nil) else {
      throw ContentError.storage("无法保存附件文字。")
    }
    defer { try? FileManager.default.removeItem(at: temporary) }
    let file = try FileHandle(forWritingTo: temporary)
    var closed = false
    defer { if !closed { try? file.close() } }
    var count = 0
    for (index, chunk) in chunks.enumerated() {
      try Task.checkCancellation()
      if index > 0, chunks[index - 1].page_start != chunk.page_start {
        try file.write(contentsOf: Data([10, 10])); count += 2
      }
      try file.write(contentsOf: Data(chunk.text.utf8))
      count += chunk.text.count
    }
    try file.close()
    closed = true
    try Task.checkCancellation()
    try FileManager.default.moveItem(at: temporary, to: destination)
    return (relative, count)
  }
  nonisolated func attachmentURL(_ relative: String) -> URL {
    directory.appendingPathComponent(relative)
  }
  func registerFile(bookID: String, relativePath: String) throws {
    try db.put("file", bookID, encoder.encode(relativePath))
  }
  func filePaths() throws -> [String: String] {
    guard let d = try db.get("app", "file-paths").first else { return [:] }
    return try decoder.decode([String: String].self, from: d)
  }
  func saveFilePaths(_ values: [String: String]) throws {
    try db.put("app", "file-paths", encoder.encode(values))
  }
  func creditLedger(startingBalance: Int = 0) throws -> CreditLedger {
    if let bytes = try db.get("credit", "ledger").first {
      return try decoder.decode(CreditLedger.self, from: bytes)
    }
    return CreditLedger(balance: startingBalance)
  }
  func recoverCredits(startingBalance: Int) throws -> CreditLedger {
    var ledger = try creditLedger(startingBalance: startingBalance)
    ledger.balance += ledger.reservations.values.reduce(0, +)
    ledger.reservations = [:]
    ledger.revision += 1
    try db.put("credit", "ledger", encoder.encode(ledger))
    return ledger
  }
  func reserveCredits(_ amount: Int) throws -> (String, CreditLedger) {
    var ledger = try creditLedger()
    guard amount > 0, ledger.balance >= amount else {
      throw ContentError.invalid("积分不足：本次需要 \(amount) 积分，当前剩余 \(ledger.balance) 积分。")
    }
    let id = UUID().uuidString
    ledger.balance -= amount
    ledger.reservations[id] = amount
    ledger.revision += 1
    try db.put("credit", "ledger", encoder.encode(ledger))
    return (id, ledger)
  }
  func finishCredits(_ id: String, refund: Bool) throws -> CreditLedger {
    var ledger = try creditLedger()
    guard let amount = ledger.reservations.removeValue(forKey: id) else { return ledger }
    if refund { ledger.balance += amount }
    ledger.revision += 1
    try db.put("credit", "ledger", encoder.encode(ledger))
    return ledger
  }
  #if DEBUG
    func setWriteFaultForTesting(_ fail: Bool) { db.failWrites = fail }
    func resetWriteMetricsForTesting() { db.writeCounts = [:]; db.writtenBytes = 0 }
    func writeMetricsForTesting() -> (counts: [String: Int], bytes: Int) { (db.writeCounts, db.writtenBytes) }
  #endif

  func seedDemoNotesIfNeeded() throws {
    guard db.newerSchemaVersion == nil else { return }
    guard includesFixtureContent else { return }
    guard try db.get("meta", "learning-notes-demo-v1").isEmpty else { return }
    let (note, drawing) = try DemoNoteSeed.make()
    try drawing.write(
      to: directory.appendingPathComponent("attachments/demo-uncertain.pkdrawing"), options: .atomic
    )
    try db.execute("BEGIN IMMEDIATE")
    do {
      try importSnapshotNotes([note], scope: Self.localNoteScope)
      try db.put("meta", "learning-notes-demo-v1", Data([1]))
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }

  /// Upgrade only untouched legacy previews. Archive records before retiring them;
  /// imported files and edited notes/content are never selected by this migration.
  private func retireUnchangedPreviewContent() throws {
    guard db.newerSchemaVersion == nil else { return }
    guard !includesFixtureContent, try db.get("meta", "retired-previews-v1").isEmpty,
      let baseline = try? ContentBundle.load([SourceContent].self, name: "sources")
    else { return }
    let paths = try filePaths()
    let records = try db.get("source")
    let retired = try records.filter { bytes in
      let source = try decoder.decode(SourceContent.self, from: bytes)
      return paths[source.id] == nil && baseline.contains(source)
    }
    let ids = Set(try retired.map { try decoder.decode(SourceContent.self, from: $0).id })
    struct LegacyNote: Decodable { var id: String; var title: String; var recognizedText: String }
    let noteSeeds = (try? ContentBundle.load([LegacyNote].self, name: "demo-notes")) ?? []
    try db.execute("BEGIN IMMEDIATE")
    do {
      for bytes in retired {
        let id = try decoder.decode(SourceContent.self, from: bytes).id
        try db.put("retired-preview-source-v1", id, bytes)
        try db.delete("source", id)
      }
      for note in try loadStoredNotes(scope: Self.localNoteScope).map(\.note) {
        if note.createdAt == note.updatedAt, note.organizedText == nil, note.transcript == nil,
          note.drawingPath == "attachments/demo-uncertain.pkdrawing",
          noteSeeds.contains(where: { $0.id == note.id && $0.title == note.title && $0.recognizedText == note.body })
        {
          try db.put("retired-preview-note-v1", note.id, encoder.encode(note))
          try tombstone(note.id, scope: Self.localNoteScope)
        }
      }
      if let bytes = try db.get("app", "state").first {
        var snapshot = try decoder.decode(AppSnapshot.self, from: bytes)
        try db.put("retired-preview-snapshot-v1", "state", bytes)
        for i in snapshot.courses.indices {
          snapshot.courses[i].resourceIDs.removeAll { ids.contains($0) }
          if let active = snapshot.courses[i].activeResourceID, ids.contains(active) {
            snapshot.courses[i].activeResourceID = snapshot.courses[i].resourceIDs.first
          }
        }
        snapshot.courses.removeAll { $0.resourceIDs.isEmpty && $0.id.hasPrefix("course:") }
        snapshot.draft.resourceIDs.removeAll { ids.contains($0) }
        snapshot.mistakes.removeAll { $0.mistake_id == "mistake_demo_01" && $0.error_count == nil }
        if let id = snapshot.selectedBookID, ids.contains(id) {
          snapshot.selectedBookID = nil
        }
        if !snapshot.courses.contains(where: { $0.id == snapshot.selectedCourseID }) {
          snapshot.selectedCourseID = nil
        }
        try db.put("app", "state", encoder.encode(snapshot))
      }
      try db.put("meta", "retired-previews-v1", Data([1]))
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }

}
