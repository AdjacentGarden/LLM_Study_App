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
private final class SQLiteConnection: @unchecked Sendable {
  var handle: OpaquePointer?
  var failWrites = false
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
    try execute(
      "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(kind,id)); PRAGMA user_version=1;"
    )
  }
  deinit { sqlite3_close(handle) }
  func execute(_ sql: String) throws {
    guard sqlite3_exec(handle, sql, nil, nil, nil) == SQLITE_OK else { throw failure() }
  }
  func failure() -> ContentError {
    .storage(handle.flatMap { sqlite3_errmsg($0) }.map { String(cString: $0) } ?? "数据库不可写")
  }
  func put(_ kind: String, _ id: String, _ bytes: Data) throws {
    guard !failWrites else { throw ContentError.storage("存储暂时不可写") }
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
    guard sqlite3_prepare_v2(handle, "SELECT 1 FROM records WHERE kind=? AND id=?", -1, &statement, nil) == SQLITE_OK else { throw failure() }
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
    guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK else { throw failure() }
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
  private let db: SQLiteConnection
  private let encoder = JSONEncoder(), decoder = JSONDecoder()
  init(directory: URL? = nil) throws {
    let root =
      directory
      ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
      .appendingPathComponent("BookCourse", isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    self.directory = root
    self.db = try SQLiteConnection(url: root.appendingPathComponent("bookcourse.sqlite"))
    self.encoder.outputFormatting = [.sortedKeys]
    try FileManager.default.createDirectory(
      at: root.appendingPathComponent("attachments"), withIntermediateDirectories: true)
  }
  func loadSources() throws -> [SourceContent] {
    let seed: [SourceContent] = try ContentBundle.load([SourceContent].self, name: "sources")
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

  private struct CloudDiskState: Codable {
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
      state.notes = try disk.noteIDs.compactMap { id in
        try db.get("cloud-note", key + ":" + id).first.map { try decoder.decode(StudyNote.self, from: $0) }
      }
      state.sources = state.workspaces.flatMap(\.resources).map(CloudMapping.source)
      state.sources += try disk.sourceIDs.compactMap { id in
        try db.get("cloud-source", key + ":" + id).first.map { try decoder.decode(SourceContent.self, from: $0) }
      }
      return state
    }
    guard let data = try db.get("cloud", key).first else { return nil }
    return try decoder.decode(CloudLocalSnapshot.self, from: data)
  }

  func saveCloudState(_ value: CloudLocalSnapshot, key: String) throws {
    var state = value
    let resourceBooks = Set(value.workspaces.flatMap(\.resources).map(\.book_id))
    let extraSources = value.sources.filter { !resourceBooks.contains($0.id) }
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
      for note in value.notes ?? [] { try db.put("cloud-note", key + ":" + note.id, encoder.encode(note)) }
      for source in extraSources { try db.put("cloud-source", key + ":" + source.id, encoder.encode(source)) }
      state.sources = []
      state.notes = nil
      let disk = CloudDiskState(state: state, noteIDs: (value.notes ?? []).map(\.id), sourceIDs: extraSources.map(\.id))
      try db.put("cloud-shell-v2", key, encoder.encode(disk))
      try db.delete("cloud", key)
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }
  func loadNotes() throws -> [StudyNote] {
    try db.get("note").map { try decoder.decode(StudyNote.self, from: $0) }.sorted {
      $0.updatedAt > $1.updatedAt
    }
  }
  func saveNote(_ note: StudyNote) throws { try db.put("note", note.id, encoder.encode(note)) }
  func deleteNote(_ id: String) throws { try db.delete("note", id) }
  func saveAttachment(_ data: Data, extension ext: String) throws -> String {
    let safeExtension = ext.filter { $0.isLetter || $0.isNumber }.prefix(12)
    let relative = "attachments/\(UUID().uuidString).\(safeExtension)"
    try data.write(to: directory.appendingPathComponent(relative), options: .atomic)
    return relative
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
  func creditLedger(startingBalance: Int = 100) throws -> CreditLedger {
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
    guard try db.get("meta", "learning-notes-demo-v1").isEmpty else { return }
    let (note, drawing) = try DemoNoteSeed.make()
    try drawing.write(
      to: directory.appendingPathComponent("attachments/demo-uncertain.pkdrawing"), options: .atomic
    )
    try db.execute("BEGIN IMMEDIATE")
    do {
      if try db.get("note", note.id).isEmpty { try db.put("note", note.id, encoder.encode(note)) }
      try db.put("meta", "learning-notes-demo-v1", Data([1]))
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }

}
