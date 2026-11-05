import CryptoKit
import Foundation
import SQLite3

protocol BookCourseRepository: Sendable {
  func loadSources() async throws -> [SourceContent]
  func saveSources(_ sources: [SourceContent]) async throws
  func loadSnapshot() async throws -> AppSnapshot?
  func saveSnapshot(_ snapshot: AppSnapshot) async throws
}

extension LocalBookCourseRepository {
  private struct OriginalFileStamp: Codable, Equatable, Sendable {
    var byteCount: Int
    var modifiedAt: TimeInterval
  }

  private struct OriginalContentFile: Codable {
    var relativePath: String
    var stamp: OriginalFileStamp
  }

  private struct OriginalFingerprint: Codable {
    var contentHash: String
    var stamp: OriginalFileStamp
  }

  private struct OriginalBookIdentity: Codable {
    var bookID: String
    var relativePath: String
    var stamp: OriginalFileStamp
  }

  private struct OriginalBookIdentities: Codable {
    var preferredBookID: String
    var books: [String: OriginalBookIdentity]
  }

  private nonisolated static func contentHash(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
  }

  private nonisolated static func normalizedContentHash(_ value: String) throws -> String {
    let hash = value.lowercased()
    guard hash.utf8.count == 64, hash.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
      throw ContentError.storage("原文件内容标识无效")
    }
    return hash
  }

  private func identityKey(contentHash: String, scope: String) -> String {
    Self.contentHash(Data(scope.utf8)) + ":" + contentHash
  }

  private func originalFileStamp(relativePath: String) -> OriginalFileStamp? {
    guard let values = try? attachmentURL(relativePath).resourceValues(
      forKeys: [.isRegularFileKey, .fileSizeKey, .contentModificationDateKey]),
      values.isRegularFile == true, let count = values.fileSize,
      let modified = values.contentModificationDate else { return nil }
    return OriginalFileStamp(byteCount: count, modifiedAt: modified.timeIntervalSince1970)
  }

  private func saveOriginalFingerprint(hash: String, relativePath: String, stamp: OriginalFileStamp) throws {
    try db.put("original-file-fingerprint", relativePath,
      encoder.encode(OriginalFingerprint(contentHash: hash, stamp: stamp)))
    let canonical = try db.get("original-content-file", hash).first.map {
      try decoder.decode(OriginalContentFile.self, from: $0)
    }
    if canonical == nil || canonical.map({ originalFileStamp(relativePath: $0.relativePath) != $0.stamp }) == true {
      try db.put("original-content-file", hash,
        encoder.encode(OriginalContentFile(relativePath: relativePath, stamp: stamp)))
    }
  }

  /// Returns a fingerprint only while the original's path, size and mtime match.
  /// It never opens the file's contents on the import path.
  func cachedContentHash(relativePath: String) throws -> String? {
    guard let stamp = originalFileStamp(relativePath: relativePath),
      let bytes = try db.get("original-file-fingerprint", relativePath).first else { return nil }
    let fingerprint = try decoder.decode(OriginalFingerprint.self, from: bytes)
    return fingerprint.stamp == stamp ? fingerprint.contentHash : nil
  }

  /// `books` must contain only the current account's book-to-original paths.
  /// Full digests take precedence over the legacy content-derived local ID.
  /// Historical originals are indexed separately, so lookup never scans them.
  func resolveExistingBookID(contentHash: String, scope: String, books: [String: String]) throws -> String? {
    try matchingBookIDs(contentHash: contentHash, scope: scope, books: books).first
  }

  func matchingBookIDs(contentHash: String, scope: String, books: [String: String]) throws -> [String] {
    let hash = try Self.normalizedContentHash(contentHash)
    var matches: [String] = []
    if let bytes = try db.get("original-book-identity", identityKey(contentHash: hash, scope: scope)).first {
      let identities = try decoder.decode(OriginalBookIdentities.self, from: bytes)
      for identity in identities.books.values {
        if let path = books[identity.bookID], path == identity.relativePath,
          let stamp = originalFileStamp(relativePath: path) {
          // Restaging can repair a missing original at the same path. Its new
          // fingerprint still proves this book owns the imported bytes.
          if try stamp == identity.stamp || cachedContentHash(relativePath: path) == hash {
            matches.append(identity.bookID)
          }
        }
      }
      matches.sort { lhs, rhs in
        if lhs == identities.preferredBookID { return true }
        if rhs == identities.preferredBookID { return false }
        return lhs < rhs
      }
    }
    let localID = "book_local_" + hash.prefix(24)
    if !matches.contains(localID), let path = books[localID],
      let stamp = originalFileStamp(relativePath: path) {
      // A known changed file must be reindexed; only never-indexed legacy IDs
      // can use the old 96-bit local alias before the background warmup ends.
      if let bytes = try db.get("original-file-fingerprint", path).first {
        let fingerprint = try decoder.decode(OriginalFingerprint.self, from: bytes)
        if fingerprint.stamp == stamp, fingerprint.contentHash == hash { matches.append(localID) }
      } else {
        matches.append(localID)
      }
    }
    return matches
  }

  /// Called after staging and again after a cloud upload replaces the local ID.
  /// Sharing original bytes between accounts does not share their book identity.
  func recordContentIdentity(contentHash: String, bookID: String, relativePath: String, scope: String) throws {
    try recordContentIdentity(contentHash: contentHash, bookID: bookID, relativePath: relativePath,
      scope: scope, prefer: true)
  }

  private func recordContentIdentity(contentHash: String, bookID: String, relativePath: String,
    scope: String, prefer: Bool) throws {
    let hash = try Self.normalizedContentHash(contentHash)
    guard let stamp = originalFileStamp(relativePath: relativePath) else {
      throw ContentError.storage("原文件已不可用，请重新选择文件")
    }
    let identity = OriginalBookIdentity(bookID: bookID, relativePath: relativePath, stamp: stamp)
    let key = identityKey(contentHash: hash, scope: scope)
    try db.execute("BEGIN IMMEDIATE")
    do {
      var identities = try db.get("original-book-identity", key).first.map {
        try decoder.decode(OriginalBookIdentities.self, from: $0)
      } ?? OriginalBookIdentities(preferredBookID: bookID, books: [:])
      identities.books[bookID] = identity
      if prefer && (!bookID.hasPrefix("book_local_") || identities.preferredBookID.hasPrefix("book_local_")) {
        identities.preferredBookID = bookID
      }
      try db.put("original-book-identity", key, encoder.encode(identities))
      try saveOriginalFingerprint(hash: hash, relativePath: relativePath, stamp: stamp)
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }

  /// Start after account hydration. Concurrent callers join one indexing task.
  /// Unchanged originals use persisted metadata; only new/changed legacy files
  /// require a background read, and no extraction or OCR runs here.
  func warmContentIdentityIndex(scope: String, books: [String: String]) async throws {
    if let flight = contentIdentityWarmups[scope] { return try await flight.task.value }
    let id = UUID()
    let task = Task { try await self.indexOriginalContent(scope: scope, books: books) }
    contentIdentityWarmups[scope] = (id, task)
    defer { if contentIdentityWarmups[scope]?.id == id { contentIdentityWarmups[scope] = nil } }
    try await task.value
  }

  private func indexOriginalContent(scope: String, books: [String: String]) async throws {
    // Prefer uploaded identities to retained local aliases for the same bytes.
    let ordered = books.sorted { lhs, rhs in
      if lhs.key.hasPrefix("book_local_") != rhs.key.hasPrefix("book_local_") {
        return !lhs.key.hasPrefix("book_local_")
      }
      return lhs.key < rhs.key
    }
    for (bookID, relativePath) in ordered {
      try Task.checkCancellation()
      guard let stamp = originalFileStamp(relativePath: relativePath) else { continue }
      let hash: String
      if let cached = try cachedContentHash(relativePath: relativePath) {
        hash = cached
      } else {
        let url = attachmentURL(relativePath)
        let read = Task.detached(priority: .utility) {
          try Task.checkCancellation()
          let bytes = try Data(contentsOf: url, options: .mappedIfSafe)
          let hash = Self.contentHash(bytes)
          try Task.checkCancellation()
          return hash
        }
        #if DEBUG
        contentIdentityHashReads += 1
        #endif
        do {
          hash = try await withTaskCancellationHandler { try await read.value } onCancel: { read.cancel() }
        } catch is CancellationError { throw CancellationError() }
        catch { continue }
        // A replacement or removal while hashing cannot seed a stale identity.
        guard originalFileStamp(relativePath: relativePath) == stamp else { continue }
        try saveOriginalFingerprint(hash: hash, relativePath: relativePath, stamp: stamp)
      }
      try recordContentIdentity(contentHash: hash, bookID: bookID, relativePath: relativePath,
        scope: scope, prefer: false)
      await Task.yield()
    }
  }

  #if DEBUG
  func contentIdentityHashReadsForTesting() -> Int { contentIdentityHashReads }
  #endif
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
    sqlite3_busy_timeout(handle, 5_000)
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
  private var contentIdentityWarmups: [String: (id: UUID, task: Task<Void, Error>)] = [:]
  #if DEBUG
  private var contentIdentityHashReads = 0
  #endif
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
    let removedBooks = CourseDeletionPruning.exclusiveBooks(try loadSnapshot() ?? AppSnapshot(),
      tombstones: try courseDeletionTombstones(scope: Self.localNoteScope))
    return sources.filter { !removedBooks.contains($0.id) }
  }
  func saveSources(_ sources: [SourceContent]) throws {
    let removedBooks = CourseDeletionPruning.exclusiveBooks(try loadSnapshot() ?? AppSnapshot(),
      tombstones: try courseDeletionTombstones(scope: Self.localNoteScope))
    try db.execute("BEGIN IMMEDIATE")
    do {
      for source in sources where !removedBooks.contains(source.id) { try db.put("source", source.id, encoder.encode(source)) }
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }
  func loadSnapshot() throws -> AppSnapshot? {
    try db.get("app", "state").first.map { try filterDeletedCourses(decoder.decode(AppSnapshot.self, from: $0), scope: Self.localNoteScope) }
  }
  func saveSnapshot(_ snapshot: AppSnapshot) throws {
    try db.put("app", "state", encoder.encode(filterDeletedCourses(snapshot, scope: Self.localNoteScope)))
  }

  func loadAssistantHistory(scope: String) throws -> AssistantHistoryArchive? {
    try db.get("assistant-history", scope).first.map { try decoder.decode(AssistantHistoryArchive.self, from: $0) }
  }

  func saveAssistantHistory(_ value: AssistantHistoryArchive, scope: String) throws {
    try db.put("assistant-history", scope, encoder.encode(CourseDeletionPruning.history(value, tombstones: courseDeletionTombstones(scope: scope))))
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
      return try filterDeletedCourses(state, scope: key)
    }
    guard let data = try db.get("cloud", key).first else { return nil }
    var legacy = try decoder.decode(CloudLocalSnapshot.self, from: data)
    legacy.notes = try loadStoredNotes(scope: key).map(\.note)
    return try filterDeletedCourses(legacy, scope: key)
  }

  func saveCloudState(_ original: CloudLocalSnapshot, key: String) throws {
    let value = try filterDeletedCourses(original, scope: key)
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
  /// General attachments have one owner, which may delete them when a temporary
  /// conversation ends. Course originals use saveOriginalAttachment instead.
  func saveAttachment(_ data: Data, extension ext: String) throws -> String {
    let safeExtension = ext.filter { $0.isLetter || $0.isNumber }.prefix(12)
    let relative = "attachments/\(UUID().uuidString).\(safeExtension)"
    try data.write(to: directory.appendingPathComponent(relative), options: .atomic)
    return relative
  }
  func saveOriginalAttachment(_ data: Data, extension ext: String, contentHash: String? = nil,
    owner: CourseOriginalOwner? = nil) throws -> String {
    if let owner { try requireLiveCourse(courseID: owner.courseID, scope: owner.scope) }
    // Staging supplies its digest so a new file is read and hashed only once.
    let hash = try Self.normalizedContentHash(contentHash ?? Self.contentHash(data))
    let safeExtension = String(ext.lowercased().filter { $0.isLetter || $0.isNumber }.prefix(12))
    let suffix = safeExtension.isEmpty ? "" : "." + safeExtension
    // The transaction also serializes separate repository instances using this
    // directory. The first spelling of the extension wins for identical bytes.
    try db.execute("BEGIN IMMEDIATE")
    do {
      let previous = try db.get("original-content-file", hash).first.map {
        try decoder.decode(OriginalContentFile.self, from: $0)
      }
      var relative: String
      if let previous, originalFileStamp(relativePath: previous.relativePath).map({ $0 == previous.stamp }) != false {
        relative = previous.relativePath
      } else {
        relative = "attachments/sha256-\(hash)\(suffix)"
      }
      if let previous, previous.relativePath == relative,
        let current = originalFileStamp(relativePath: relative), current != previous.stamp {
        // A changed original can now belong to another book. Preserve those
        // bytes when restoring this digest instead of overwriting its old path.
        var version = 1
        repeat {
          relative = "attachments/sha256-\(hash)-restored-\(version)\(suffix)"
          version += 1
        } while FileManager.default.fileExists(atPath: attachmentURL(relative).path)
      }
      let current = originalFileStamp(relativePath: relative)
      if previous?.stamp != current || current?.byteCount != data.count || previous == nil {
        try db.checkWritable()
        try Task.checkCancellation()
        try data.write(to: attachmentURL(relative), options: .atomic)
      }
      guard let stamp = originalFileStamp(relativePath: relative), stamp.byteCount == data.count else {
        throw ContentError.storage("原文件保存不完整，请重新导入")
      }
      let file = OriginalContentFile(relativePath: relative, stamp: stamp)
      try db.put("original-content-file", hash, encoder.encode(file))
      try saveOriginalFingerprint(hash: hash, relativePath: relative, stamp: stamp)
      if let owner {
        try db.put("course-original:" + owner.scope, owner.id,
          encoder.encode(CourseOriginalAttachment(courseID: owner.courseID, relativePath: relative)))
      }
      try db.execute("COMMIT")
      return relative
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
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
        try db.put("app", "state", encoder.encode(filterDeletedCourses(snapshot, scope: Self.localNoteScope)))
      }
      try db.put("meta", "retired-previews-v1", Data([1]))
      try db.execute("COMMIT")
    } catch {
      try? db.execute("ROLLBACK")
      throw error
    }
  }

}
