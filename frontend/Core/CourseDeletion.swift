import Foundation
import Observation

/// A local removal is an account-scoped, permanent visibility decision. It never
/// authorizes deleting a public textbook or somebody else's backend workspace.
struct CourseDeletionTombstone: Codable, Sendable, Equatable {
  var courseID: String
  var workspaceIDs: Set<String>
  var bookIDs: Set<String>
  var resourceIDs: Set<String>
  var deletedAt: Date = .now
  var pendingFilePaths: Set<String> = []
  var originalOwnerIDs: Set<String>? = nil
  var allCourseIDs: Set<String> { workspaceIDs.union([courseID]) }
}

struct CourseOriginalOwner: Sendable {
  var courseID: String
  var scope: String
  var id: String
}
struct CourseOriginalAttachment: Codable, Sendable {
  var courseID: String
  var relativePath: String
}

@MainActor @Observable final class CourseDeletionState {
  var scope: String?
  var tombstones: [String: CourseDeletionTombstone] = [:]
  var deleting: Set<String> = []
  @ObservationIgnored var readyEpoch: UUID?
  @ObservationIgnored var initializationTask: Task<Void, Error>?
  @ObservationIgnored var initializationScope: String?
  @ObservationIgnored var initializationEpoch: UUID?
  @ObservationIgnored var initializationToken = UUID()
  #if DEBUG
  @ObservationIgnored var initializationBarrierForTesting: (@MainActor (String) async throws -> Void)?
  #endif
  var deletedIDs: Set<String> { tombstones.values.reduce(into: Set<String>()) { $0.formUnion($1.allCourseIDs) } }
}

/// Shared pure pruning rules are also applied on every snapshot write. This is
/// what makes an already queued save harmless after a deletion has committed.
enum CourseDeletionPruning {
  static func ids(_ tombstones: [CourseDeletionTombstone]) -> Set<String> {
    tombstones.reduce(into: []) { $0.formUnion($1.allCourseIDs) }
  }
  static func resources(_ tombstones: [CourseDeletionTombstone]) -> Set<String> {
    tombstones.reduce(into: []) { $0.formUnion($1.resourceIDs) }
  }
  static func courseKeyIsRemoved(_ key: String, separator: String, removed: Set<String>) -> Bool {
    guard let boundary = key.range(of: separator, options: .backwards) else { return false }
    return removed.contains(String(key[..<boundary.lowerBound]))
  }
  static func exclusiveBooks(_ snapshot: AppSnapshot, tombstones: [CourseDeletionTombstone]) -> Set<String> {
    let removed = ids(tombstones)
    var retained = Set(snapshot.courses.filter { !removed.contains($0.id) }.flatMap(\.resourceIDs))
    for record in snapshot.courseImports ?? [] where !removed.contains(record.id)
      && record.draft.creationWorkspaceID.map(removed.contains) != true {
      retained.formUnion(record.draft.resourceIDs)
    }
    if snapshot.draft.importCourseID.map(removed.contains) != true,
      snapshot.draft.editingCourseID.map(removed.contains) != true,
      snapshot.draft.creationWorkspaceID.map(removed.contains) != true { retained.formUnion(snapshot.draft.resourceIDs) }
    return tombstones.reduce(into: Set<String>()) { $0.formUnion($1.bookIDs) }.subtracting(retained)
  }
  static func snapshot(_ original: AppSnapshot, tombstones: [CourseDeletionTombstone]) -> AppSnapshot {
    guard !tombstones.isEmpty else { return original }
    var value = original
    let removed = ids(tombstones), books = exclusiveBooks(original, tombstones: tombstones)
    value.courses.removeAll { removed.contains($0.id) }
    value.courseImports?.removeAll { removed.contains($0.id) || $0.draft.creationWorkspaceID.map(removed.contains) == true }
    value.locations = value.locations.filter { key, _ in !courseKeyIsRemoved(key, separator: "::", removed: removed) }
    value.mistakes.removeAll { $0.course_id.map(removed.contains) == true }
    value.chapterDrafts = value.chapterDrafts?.filter { !books.contains($0.key) }
    value.dismissedSourceIDs.formUnion(books)
    if value.draft.importCourseID.map(removed.contains) == true
      || value.draft.editingCourseID.map(removed.contains) == true
      || value.draft.creationWorkspaceID.map(removed.contains) == true { value.draft = CourseDraft() }
    if let job = value.importJob, !job.bookIDs.isEmpty, Set(job.bookIDs).isSubset(of: books) { value.importJob = nil }
    if value.selectedCourseID.map(removed.contains) == true
      || (value.selectedCourseID != nil && !value.courses.contains { $0.id == value.selectedCourseID }) {
      value.selectedCourseID = value.courses.first?.id
      value.selectedBookID = value.courses.first?.activeResourceID ?? value.courses.first?.resourceIDs.first
    }
    if value.courses.isEmpty { value.selectedCourseID = nil; value.selectedBookID = nil }
    return value
  }
  static func notes(_ values: [StudyNote], tombstones: [CourseDeletionTombstone]) -> [StudyNote] {
    let removed = ids(tombstones), links = resources(tombstones)
    return values.filter { note in
      guard let anchor = note.anchor else { return true }
      if let courseID = anchor.courseID { return !removed.contains(courseID) }
      return anchor.courseResourceID.map(links.contains) != true
    }
  }
  static func cloud(_ original: CloudLocalSnapshot, tombstones: [CourseDeletionTombstone]) -> CloudLocalSnapshot {
    guard !tombstones.isEmpty else { return original }
    var value = original
    let removed = ids(tombstones), books = exclusiveBooks(original.snapshot, tombstones: tombstones)
    let discardedCards = Set(value.sources.filter { books.contains($0.id) }.flatMap(\.flashcards).map(\.id))
    value.snapshot = snapshot(value.snapshot, tombstones: tombstones)
    value.snapshot.mastery.subtract(discardedCards)
    value.sources.removeAll { books.contains($0.id) }
    value.filePaths = value.filePaths.filter { !books.contains($0.key) }
    value.workspaces.removeAll { removed.contains($0.id) }
    value.pending = value.pending.filter { !removed.contains($0.value.workspaceID) }
    value.pendingReviews?.removeAll { removed.contains($0.workspaceID) }
    value.notes = notes(value.notes ?? [], tombstones: tombstones)
    value.dailyPlans = value.dailyPlans?.filter { key, _ in !courseKeyIsRemoved(key, separator: ":", removed: removed) }
    value.pendingPlanCompletions?.removeAll { removed.contains($0.workspaceID) }
    if value.creationWorkspaceID.map(removed.contains) == true { value.creationWorkspaceID = nil }
    if value.pendingSelection.map({ removed.contains($0.workspace_id) }) == true { value.pendingSelection = nil }
    return value
  }
  static func personal(_ original: LocalPersonalState, tombstones: [CourseDeletionTombstone]) -> LocalPersonalState {
    guard !tombstones.isEmpty else { return original }
    var value = original
    let removed = ids(tombstones), books = exclusiveBooks(original.snapshot, tombstones: tombstones)
    let discardedCards = Set(value.sources.filter { books.contains($0.id) }.flatMap(\.flashcards).map(\.id))
    value.snapshot = snapshot(value.snapshot, tombstones: tombstones)
    value.snapshot.mastery.subtract(discardedCards)
    value.sources.removeAll { books.contains($0.id) }
    value.filePaths = value.filePaths.filter { !books.contains($0.key) }
    value.workspaces.removeAll { removed.contains($0.id) }
    value.courseSources = value.courseSources.filter { !removed.contains($0.key) }
    value.diagnostics = value.diagnostics.filter { !removed.contains($0.value.courseID) }
    value.dailyPlans = value.dailyPlans.filter { key, _ in !courseKeyIsRemoved(key, separator: ":", removed: removed) }
    value.deletedCourseIDs = (value.deletedCourseIDs ?? []).union(removed)
    return value
  }
  static func runReferences(_ original: AgentRunRecord, tombstones: [CourseDeletionTombstone]) -> AgentRunRecord {
    var value = original
    let removed = ids(tombstones), links = resources(tombstones)
    value.evidence.removeAll { evidence in
      if let courseID = evidence.courseID { return removed.contains(courseID) }
      return evidence.courseResourceID.map(links.contains) == true
    }
    value.finalCourseIDs?.removeAll { removed.contains($0) }
    return value
  }
  static func history(_ original: AssistantHistoryArchive, tombstones: [CourseDeletionTombstone]) -> AssistantHistoryArchive {
    guard !tombstones.isEmpty else { return original }
    var value = original
    let removed = ids(tombstones)
    for index in value.sessions.indices {
      value.sessions[index].courseLinks = value.sessions[index].courseLinks.mapValues { $0.filter { !removed.contains($0) } }
      value.sessions[index].runs?.removeAll { $0.context.courseID.map(removed.contains) == true }
      value.sessions[index].runs = value.sessions[index].runs?.map { runReferences($0, tombstones: tombstones) }
      if value.sessions[index].context.courseID.map(removed.contains) == true {
        value.sessions[index].context.courseID = nil
        value.sessions[index].context.courseResourceID = nil
      }
    }
    if value.newContext?.courseID.map(removed.contains) == true {
      value.newContext?.courseID = nil; value.newContext?.courseResourceID = nil
    }
    return value
  }
}

extension LocalBookCourseRepository {
  func courseDeletionTombstones(scope: String) throws -> [CourseDeletionTombstone] {
    var markers = try db.get("course-deletion:" + scope).map { try decoder.decode(CourseDeletionTombstone.self, from: $0) }
    // Earlier versions stored only a set inside the account authority. Keep
    // those removals effective even before the richer ledger has been written.
    if let data = try db.get("private-local-authority", scope).first {
      let legacy = try decoder.decode(LocalPersonalState.self, from: data)
      for id in legacy.deletedCourseIDs ?? [] where !markers.contains(where: { $0.allCourseIDs.contains(id) }) {
        let course = legacy.snapshot.courses.first { $0.id == id }
        let workspace = legacy.workspaces.first { $0.id == id }
        markers.append(CourseDeletionTombstone(courseID: id, workspaceIDs: [id],
          bookIDs: Set(course?.resourceIDs ?? workspace?.resources.map(\.book_id) ?? []),
          resourceIDs: Set(workspace?.resources.map(\.id) ?? [])))
      }
    }
    return markers
  }
  func localCourseDeletionResult(_ id: String, scope: String) throws -> [String: JSONValue]? {
    try db.get("course-deletion-result:" + scope, id).first.map {
      try decoder.decode([String: JSONValue].self, from: $0)
    }
  }
  /// A minimal command receipt survives removal of its invoking agent run. It
  /// contains no note, chat, model response or generated learning content.
  func saveLocalCourseDeletionResult(_ value: [String: JSONValue], id: String, scope: String) throws {
    let allowed: Set<String> = ["operation_id", "course_id", "arguments_hash", "status"]
    guard Set(value.keys) == allowed, value["operation_id"]?.stringValue == id,
      let courseID = value["course_id"]?.stringValue, !courseID.isEmpty,
      let hash = value["arguments_hash"]?.stringValue, !hash.isEmpty,
      value["status"]?.stringValue == "deleted" else { throw ContentError.invalid("课程删除回执无效。") }
    try db.transaction {
      guard CourseDeletionPruning.ids(try courseDeletionTombstones(scope: scope)).contains(courseID) else {
        throw ContentError.invalid("课程删除尚未持久保存，不能确认成功。")
      }
      if let previous = try localCourseDeletionResult(id, scope: scope), previous != value {
        throw ContentError.invalid("同一删除操作的参数发生变化，已拒绝覆盖回执。")
      }
      try db.put("course-deletion-result:" + scope, id, encoder.encode(value))
    }
  }
  func requireLiveCourse(courseID: String, scope: String) throws {
    guard !CourseDeletionPruning.ids(try courseDeletionTombstones(scope: scope)).contains(courseID) else {
      throw ContentError.invalid("课程已从本机删除，无法继续保存该课程的数据。")
    }
  }
  func requireLiveAgentRun(runID: String, scope: String) throws {
    guard try !db.contains("course-deleted-run:" + scope, runID) else { throw NoteStoreError.runClosed(runID: runID) }
    if let run = try loadRun(runID, scope: scope), let courseID = run.context.courseID {
      try requireLiveCourse(courseID: courseID, scope: scope)
    }
  }
  func filterDeletedCourses(_ value: AppSnapshot, scope: String) throws -> AppSnapshot {
    CourseDeletionPruning.snapshot(value, tombstones: try courseDeletionTombstones(scope: scope))
  }
  func filterDeletedCourses(_ value: CloudLocalSnapshot, scope: String) throws -> CloudLocalSnapshot {
    CourseDeletionPruning.cloud(value, tombstones: try courseDeletionTombstones(scope: scope))
  }
  func filterDeletedCourses(_ value: LocalPersonalState, scope: String) throws -> LocalPersonalState {
    CourseDeletionPruning.personal(value, tombstones: try courseDeletionTombstones(scope: scope))
  }
  func filterDeletedLearningObjects(_ values: [AgentLearningObject], scope: String) throws -> [AgentLearningObject] {
    let removed = CourseDeletionPruning.ids(try courseDeletionTombstones(scope: scope))
    return values.filter { !removed.contains($0.course_id) }
  }
  /// Commit this BEFORE task cancellation or memory changes. A failed cleanup is
  /// retried on launch, while this durable barrier already blocks resurrection.
  func recordCourseDeletion(_ value: CourseDeletionTombstone, scope: String) throws {
    try db.transaction {
      var merged = value
      if let data = try db.get("course-deletion:" + scope, value.courseID).first {
        let previous = try decoder.decode(CourseDeletionTombstone.self, from: data)
        merged.workspaceIDs.formUnion(previous.workspaceIDs)
        merged.bookIDs.formUnion(previous.bookIDs)
        merged.resourceIDs.formUnion(previous.resourceIDs)
        merged.pendingFilePaths.formUnion(previous.pendingFilePaths)
        merged.originalOwnerIDs = (merged.originalOwnerIDs ?? []).union(previous.originalOwnerIDs ?? [])
        merged.deletedAt = previous.deletedAt
      }
      try db.put("course-deletion:" + scope, value.courseID, encoder.encode(merged))
    }
  }

  /// All logical records are pruned in one SQLite transaction. File reclamation
  /// follows the commit and is repeatable; unrelated account rows are read only.
  func finishCourseDeletion(scope: String) throws {
    let tombstones = try courseDeletionTombstones(scope: scope)
    guard !tombstones.isEmpty else { return }
    let removed = CourseDeletionPruning.ids(tombstones), resourceIDs = CourseDeletionPruning.resources(tombstones)
    var candidatePaths = tombstones.reduce(into: Set<String>()) { $0.formUnion($1.pendingFilePaths) }
    try db.transaction {
      var currentSnapshot = AppSnapshot()
      if let data = try db.get("private-local-authority", scope).first {
        let original = try decoder.decode(LocalPersonalState.self, from: data)
        currentSnapshot = original.snapshot
        let state = CourseDeletionPruning.personal(original, tombstones: tombstones)
        try db.put("private-local-authority", scope, encoder.encode(state))
      }
      if let data = try db.get("cloud-shell-v2", scope).first {
        var disk = try decoder.decode(CloudDiskState.self, from: data)
        if currentSnapshot.courses.isEmpty { currentSnapshot = disk.state.snapshot }
        let books = CourseDeletionPruning.exclusiveBooks(disk.state.snapshot, tombstones: tombstones)
        disk.state = CourseDeletionPruning.cloud(disk.state, tombstones: tombstones)
        disk.sourceIDs.removeAll { books.contains($0) }
        try db.put("cloud-shell-v2", scope, encoder.encode(disk))
      }
      if let data = try db.get("cloud", scope).first {
        let original = try decoder.decode(CloudLocalSnapshot.self, from: data)
        if currentSnapshot.courses.isEmpty { currentSnapshot = original.snapshot }
        try db.put("cloud", scope, encoder.encode(CourseDeletionPruning.cloud(original, tombstones: tombstones)))
      }
      if scope == Self.localNoteScope, let original = try loadSnapshot() {
        currentSnapshot = original
        try db.put("app", "state", encoder.encode(CourseDeletionPruning.snapshot(original, tombstones: tombstones)))
      }
      let books = CourseDeletionPruning.exclusiveBooks(currentSnapshot, tombstones: tombstones)
      let originalOwnerIDs = tombstones.reduce(into: Set<String>()) { $0.formUnion($1.originalOwnerIDs ?? []) }
      for row in try db.query("SELECT id,payload FROM records WHERE kind=?", [.text("course-original:" + scope)]) {
        guard let id = row[0].text, let data = row[1].blob else { continue }
        let original = try decoder.decode(CourseOriginalAttachment.self, from: data)
        if removed.contains(original.courseID) || originalOwnerIDs.contains(id) {
          candidatePaths.insert(original.relativePath)
          try db.delete("course-original:" + scope, id)
        }
      }
      var removedNoteIDs: Set<String> = []
      for note in try loadStoredNotes(scope: scope).map(\.note) {
        if CourseDeletionPruning.notes([note], tombstones: tombstones).isEmpty {
          candidatePaths.formUnion(note.attachmentPaths)
          removedNoteIDs.insert(note.id)
          try tombstone(note.id, scope: scope)
          try db.delete("cloud-note", scope + ":" + note.id)
          if scope == Self.localNoteScope { try db.delete("note", note.id) }
        }
      }
      // Note migration intentionally retained the old rows. Remove explicitly
      // owned legacy payloads too, including ones already tombstoned in v2.
      for kind in scope == Self.localNoteScope ? ["note", "cloud-note"] : ["cloud-note"] {
        for row in try db.query("SELECT id,payload FROM records WHERE kind=?", [.text(kind)]) {
          guard let id = row[0].text, let data = row[1].blob,
            let note = try? decoder.decode(StudyNote.self, from: data),
            kind == "note" || id == scope + ":" + note.id,
            CourseDeletionPruning.notes([note], tombstones: tombstones).isEmpty else { continue }
          removedNoteIDs.insert(note.id)
          candidatePaths.formUnion(note.attachmentPaths)
          try db.delete(kind, id)
        }
      }
      if let data = try db.get("cloud-shell-v2", scope).first {
        var disk = try decoder.decode(CloudDiskState.self, from: data)
        disk.noteIDs.removeAll { removedNoteIDs.contains($0) }
        try db.put("cloud-shell-v2", scope, encoder.encode(disk))
      }
      var removedContentKeys: Set<String> = []
      for resourceID in resourceIDs {
        if let bytes = try db.get("cloud-resource", scope + ":" + resourceID).first,
          let resource = try? decoder.decode(CloudResource.self, from: bytes),
          let key = resource.content_key, !key.hasPrefix("builtin:") { removedContentKeys.insert(key) }
        try db.delete("cloud-resource", scope + ":" + resourceID)
      }
      let remainingContentKeys = Set(try db.query("SELECT id,payload FROM records WHERE kind='cloud-resource'").compactMap { row -> String? in
        guard row[0].text?.hasPrefix(scope + ":") == true, let bytes = row[1].blob else { return nil }
        return try decoder.decode(CloudResource.self, from: bytes).content_key
      })
      for key in removedContentKeys.subtracting(remainingContentKeys) { try db.delete("cloud-content", scope + ":" + key) }
      for book in books {
        try db.delete("cloud-source", scope + ":" + book)
        if scope == Self.localNoteScope { try db.delete("source", book) }
        if let material = try db.get("local-material:" + scope, book).first {
          let record = try decoder.decode(LocalMaterialRecord.self, from: material)
          candidatePaths.formUnion(record.files.values); candidatePaths.formUnion(record.figurePaths.values)
          try db.delete("local-material:" + scope, book)
        }
      }
      for record in try localGenerations(scope: scope) where removed.contains(record.courseID) {
        try db.delete("private-generation:" + scope, record.id)
        try db.delete("private-operation:" + scope, record.id)
      }
      for kind in ["lesson-media:" + scope, "private-operation:" + scope] {
        for row in try db.query("SELECT id,payload FROM records WHERE kind=?", [.text(kind)]) {
          guard let id = row[0].text, let data = row[1].blob,
            let value = try? decoder.decode([String: JSONValue].self, from: data),
            value["course_id"]?.stringValue.map(removed.contains) == true else { continue }
          if kind.hasPrefix("lesson-media:"), let file = value["file"]?.stringValue { candidatePaths.insert("lesson-media/" + file) }
          try db.delete(kind, id)
        }
      }
      let objects = try filterDeletedLearningObjects(loadAgentLearningObjects(scope: scope), scope: scope)
      try db.put("agent-learning-objects", scope, encoder.encode(objects))
      if let history = try loadAssistantHistory(scope: scope) {
        try db.put("assistant-history", scope, encoder.encode(CourseDeletionPruning.history(history, tombstones: tombstones)))
      }
      // Agent contexts hold their course identity explicitly. Remove only those
      // runs and their receipts, never other tasks in the same account.
      for run in try loadRuns(scope: scope) {
        if run.context.courseID.map(removed.contains) == true {
          try db.put("course-deleted-run:" + scope, run.id, encoder.encode(run.context.courseID))
          try db.run("DELETE FROM tool_receipts WHERE scope=? AND run_id=?", [.text(scope), .text(run.id)])
          try db.run("DELETE FROM agent_runs WHERE scope=? AND id=?", [.text(scope), .text(run.id)])
        } else {
          var cleaned = CourseDeletionPruning.runReferences(run, tombstones: tombstones)
          if cleaned != run {
            cleaned.version += 1
            try db.run("UPDATE agent_runs SET version=?,payload=?,updated_at=? WHERE scope=? AND id=?",
              [.int(cleaned.version), .blob(encoder.encode(cleaned)), .real(Date.now.timeIntervalSince1970), .text(scope), .text(run.id)])
          }
        }
      }
      for var marker in tombstones {
        marker.pendingFilePaths.formUnion(candidatePaths)
        try db.put("course-deletion:" + scope, marker.courseID, encoder.encode(marker))
      }
    }
    // SQL ownership commits first; a failed/unavailable file is harmless and its
    // candidate remains durable for a later retry. Never follow arbitrary paths.
    for path in candidatePaths {
      guard let url = courseDeletionFileURL(path) else { continue }
      try removeCourseFileIfUnreferenced(url)
    }
  }
  /// Late media downloads use this same conservative, cross-account ownership
  /// check. An unverifiable reference or a path outside app storage is retained.
  func removeCourseFileIfUnreferenced(_ candidate: URL) throws {
    guard let url = courseDeletionFileURL(candidate.absoluteString) else { return }
    // Serialize the reference decision with owners written by other repository
    // instances too. No new owner can appear between the scan and reclamation.
    try db.transaction {
      guard try !courseDeletionFileIsReferenced(url.absoluteString, url: url) else { return }
      if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
      let relative = String(url.path.dropFirst(directory.resolvingSymlinksInPath().standardizedFileURL.path.count + 1))
      let unresolved = directory.appendingPathComponent(relative).standardizedFileURL
      let pathSpellings = Set([relative, url.path, url.absoluteString, unresolved.path, unresolved.absoluteString])
      // The global original lookup is only an index. Remove its stale entries
      // after the last owner releases the bytes, never while another scope uses it.
      var paths = try filePaths()
      paths = paths.filter { !pathSpellings.contains($0.value) }
      try db.put("app", "file-paths", encoder.encode(paths))
      for row in try db.query("SELECT id,payload FROM records WHERE kind='file'") {
        if let id = row[0].text, let data = row[1].blob,
          let path = try? decoder.decode(String.self, from: data), pathSpellings.contains(path) { try db.delete("file", id) }
      }
      try db.delete("original-file-fingerprint", relative)
    }
  }
  private func courseDeletionFileURL(_ path: String) -> URL? {
    let url = path.hasPrefix("file:") ? URL(string: path) : path.hasPrefix("/") ? URL(fileURLWithPath: path) : directory.appendingPathComponent(path)
    guard let url, url.isFileURL else { return nil }
    let root = directory.resolvingSymlinksInPath().standardizedFileURL.path + "/"
    let actual = url.resolvingSymlinksInPath().standardizedFileURL
    guard actual.path.hasPrefix(root), ["attachments/", "materials/", "lesson-media/"].contains(where: { actual.path.dropFirst(root.count).hasPrefix($0) }) else { return nil }
    return actual
  }
  private func courseDeletionFileIsReferenced(_ relative: String, url: URL) throws -> Bool {
    let canonicalRelative = String(url.path.dropFirst(directory.resolvingSymlinksInPath().standardizedFileURL.path.count + 1))
    let unresolved = directory.appendingPathComponent(canonicalRelative).standardizedFileURL
    let candidates = Set([relative, canonicalRelative, url.path, url.absoluteString,
      unresolved.path, unresolved.absoluteString])
    func containsPath(_ bytes: Data) -> Bool {
      func matches(_ value: Any) -> Bool {
        if let text = value as? String { return candidates.contains(text) }
        if let list = value as? [Any] { return list.contains(where: matches) }
        if let object = value as? [String: Any] { return object.values.contains(where: matches) }
        return false
      }
      if let value = try? JSONSerialization.jsonObject(with: bytes, options: [.fragmentsAllowed]) { return matches(value) }
      return String(data: bytes, encoding: .utf8).map { text in candidates.contains(where: text.contains) } ?? false
    }
    for row in try db.query("SELECT kind,id,payload FROM records") {
      guard let kind = row[0].text, let data = row[2].blob else { continue }
      // Fingerprints and deletion receipts are indexes, not remaining owners.
      if kind.hasPrefix("course-deletion:") || kind.hasPrefix("original-") || kind == "file"
        || (kind == "app" && row[1].text == "file-paths") { continue }
      if containsPath(data) { return true }
    }
    var pathBookIDs = Set(try filePaths().filter { candidates.contains($0.value) }.map(\.key))
    for row in try db.query("SELECT id,payload FROM records WHERE kind='file'") {
      if let id = row[0].text, let data = row[1].blob,
        let path = try? decoder.decode(String.self, from: data), candidates.contains(path) { pathBookIDs.insert(id) }
    }
    for row in try db.query("SELECT payload FROM notes WHERE deleted=0") {
      if let data = row[0].blob {
        if containsPath(data) { return true }
        // An unassigned legacy note still needs its textbook to resolve anchors.
        if let note = try? decoder.decode(StudyNote.self, from: data),
          note.anchor.map({ pathBookIDs.contains($0.bookID) }) == true { return true }
      }
    }
    // Upload ledgers survive account switches, so every ledger protects its own
    // originals; the deleted course's ledger has been retired before this pass.
    for url in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
      where url.lastPathComponent.hasPrefix("course-uploads-") && url.pathExtension == "json" {
      if containsPath(try Data(contentsOf: url)) { return true }
    }
    return false
  }
}

@MainActor extension AppStore {
  func canApplyCourseMutation(courseID: String, scope: String) -> Bool {
    noteScope == scope && courseDeletionState.scope == scope && courseDeletionState.readyEpoch == cloudState.epoch
      && !courseDeletionState.deleting.contains(courseID)
      && !courseDeletionState.deletedIDs.contains(courseID)
      && courses.contains { $0.id == courseID }
  }
  /// Join one durable initialization for this account and login epoch. Learning
  /// and upload entry points use the same gate; repeated checks do not decode the
  /// account authority or scan files again after a successful initialization.
  func ensureCourseDeletionState(scope: String) async throws {
    let epoch = cloudState.epoch
    guard noteScope == scope, !Task.isCancelled else { throw CancellationError() }
    if courseDeletionState.scope == scope, courseDeletionState.readyEpoch == epoch,
      cloudState.localDeletedCourseIDs.isSubset(of: courseDeletionState.deletedIDs) { return }
    let task: Task<Void, Error>
    if courseDeletionState.initializationScope == scope, courseDeletionState.initializationEpoch == epoch,
      let current = courseDeletionState.initializationTask {
      task = current
    } else {
      courseDeletionState.initializationTask?.cancel()
      let token = UUID()
      courseDeletionState.initializationToken = token
      courseDeletionState.initializationScope = scope
      courseDeletionState.initializationEpoch = epoch
      courseDeletionState.readyEpoch = nil
      task = Task { @MainActor in
        defer {
          if self.courseDeletionState.initializationToken == token {
            self.courseDeletionState.initializationTask = nil
            self.courseDeletionState.initializationScope = nil
            self.courseDeletionState.initializationEpoch = nil
          }
        }
        #if DEBUG
        try await self.courseDeletionState.initializationBarrierForTesting?(scope)
        #endif
        guard self.noteScope == scope, self.cloudState.epoch == epoch,
          self.courseDeletionState.initializationToken == token, !Task.isCancelled else { throw CancellationError() }
        var tombstones = try await self.repository.courseDeletionTombstones(scope: scope)
        guard self.noteScope == scope, self.cloudState.epoch == epoch,
          self.courseDeletionState.initializationToken == token, !Task.isCancelled else { throw CancellationError() }
        for id in self.cloudState.localDeletedCourseIDs where !tombstones.contains(where: { $0.allCourseIDs.contains(id) }) {
          let marker = CourseDeletionTombstone(courseID: id, workspaceIDs: [id], bookIDs: [], resourceIDs: [])
          try await self.repository.recordCourseDeletion(marker, scope: scope)
          guard self.noteScope == scope, self.cloudState.epoch == epoch,
            self.courseDeletionState.initializationToken == token, !Task.isCancelled else { throw CancellationError() }
          tombstones.append(marker)
        }
        self.courseDeletionState.scope = scope
        self.courseDeletionState.tombstones = Dictionary(uniqueKeysWithValues: tombstones.map { ($0.courseID, $0) })
        self.courseDeletionState.deleting = []
        for marker in tombstones { self.cancelLocalCourseGenerations(courseIDs: marker.allCourseIDs, scope: scope) }
        self.applyCourseDeletionsInMemory(tombstones)
        for marker in tombstones {
          guard self.retireCourseUploadsForDeletion(courseIDs: marker.allCourseIDs, scope: self.courseUploadScope) else {
            throw ContentError.storage("课程删除已生效，上传记录和原件清理未完成；下次启动会继续清理。")
          }
        }
        try await self.repository.finishCourseDeletion(scope: scope)
        guard self.noteScope == scope, self.cloudState.epoch == epoch,
          self.courseDeletionState.initializationToken == token, !Task.isCancelled else { throw CancellationError() }
        self.courseDeletionState.readyEpoch = epoch
      }
      courseDeletionState.initializationTask = task
    }
    try await task.value
    guard noteScope == scope, cloudState.epoch == epoch, !Task.isCancelled,
      courseDeletionState.scope == scope, courseDeletionState.readyEpoch == epoch else { throw CancellationError() }
  }
  /// Compatibility entry for account hydration and launch recovery.
  func restoreCourseDeletionState() async throws {
    guard let scope = noteScope else {
      courseDeletionState.initializationTask?.cancel(); courseDeletionState.initializationTask = nil
      courseDeletionState.initializationToken = UUID(); courseDeletionState.readyEpoch = nil
      courseDeletionState.scope = nil; courseDeletionState.tombstones = [:]
      return
    }
    try await ensureCourseDeletionState(scope: scope)
    applyCourseDeletionsInMemory(Array(courseDeletionState.tombstones.values))
  }
  func deleteLocalCourse(_ id: String) async {
    guard let scope = noteScope else { return }
    let epoch = cloudState.epoch
    do {
      try await performLocalCourseDeletion(courseID: id, scope: scope)
      if scope == noteScope, epoch == cloudState.epoch { showToast("课程已从本机删除", kind: .success) }
    } catch {
      if scope == noteScope, epoch == cloudState.epoch { showToast("删除课程未完成：" + error.localizedDescription, kind: .error) }
    }
  }
  func performLocalCourseDeletion(courseID id: String, scope: String) async throws {
    let epoch = cloudState.epoch
    guard noteScope == scope else { throw CancellationError() }
    try await ensureCourseDeletionState(scope: scope)
    guard noteScope == scope, epoch == cloudState.epoch, !Task.isCancelled else { throw CancellationError() }
    guard !courseDeletionState.deleting.contains(id) else { throw ContentError.invalid("课程正在删除，请稍候。") }
    let existing = courseDeletionState.tombstones[id]
    guard let course = courses.first(where: { $0.id == id }) else {
      if let existing {
        guard retireCourseUploadsForDeletion(courseIDs: existing.allCourseIDs, scope: courseUploadScope) else {
          throw ContentError.storage("课程删除已生效，上传记录和原件清理未完成；下次启动会继续清理。")
        }
        cancelLocalCourseGenerations(courseIDs: existing.allCourseIDs, scope: scope)
        try await repository.finishCourseDeletion(scope: scope)
        guard noteScope == scope, epoch == cloudState.epoch, !Task.isCancelled else { throw CancellationError() }
      }
      return
    }
    courseDeletionState.deleting.insert(id)
    defer { if noteScope == scope, epoch == cloudState.epoch { courseDeletionState.deleting.remove(id) } }
    let record = courseImport(for: id)
    var aliases: Set<String> = [id]
    if let record { aliases.insert(workspaceID(for: record)); aliases.insert(record.id) }
    if let wid = record?.draft.creationWorkspaceID { aliases.insert(wid) }
    // A prior duplicate import may have transferred its ledger to this course.
    // Keep its physical ownership within the same local deletion decision.
    var addedAlias = true
    while addedAlias {
      addedAlias = false
      for (alias, keeper) in cloudState.archivedDuplicateCourseAliases where aliases.contains(keeper) {
        if aliases.insert(alias).inserted { addedAlias = true }
      }
    }
    let uploads = uploadState.batches.filter { $0.scope == courseUploadScope && aliases.contains($0.courseID) }
    let books = Set(course.resourceIDs).union(record?.draft.resourceIDs ?? [])
      .union(uploads.flatMap(\.files).compactMap(\.bookID))
      .union(uploads.flatMap(\.files).compactMap(\.originalBookID))
    var resources = Set(course.resourceIDs.compactMap { courseResourceID(courseID: id, bookID: $0) })
    resources.formUnion(cloudState.workspaces.filter { aliases.contains($0.id) }.flatMap(\.resources).map(\.id))
    resources.formUnion(record?.stageSave?.workspace?.resources.map(\.id) ?? [])
    let paths = Set(books.compactMap { filePaths[$0] }).union(uploads.flatMap(\.files).compactMap(\.relativePath))
    var marker = CourseDeletionTombstone(courseID: id, workspaceIDs: aliases, bookIDs: books,
      resourceIDs: resources, pendingFilePaths: paths,
      originalOwnerIDs: Set(uploads.flatMap { batch in batch.files.map { batch.id.uuidString + ":" + $0.id.uuidString } }))
    if let existing {
      marker.workspaceIDs.formUnion(existing.workspaceIDs); marker.bookIDs.formUnion(existing.bookIDs)
      marker.resourceIDs.formUnion(existing.resourceIDs); marker.pendingFilePaths.formUnion(existing.pendingFilePaths)
      marker.originalOwnerIDs = (marker.originalOwnerIDs ?? []).union(existing.originalOwnerIDs ?? [])
      marker.deletedAt = existing.deletedAt
    }
    try await repository.recordCourseDeletion(marker, scope: scope)
    // Cancellation begins only after SQLite has committed the durable barrier.
    guard noteScope == scope, epoch == cloudState.epoch, !Task.isCancelled else { throw CancellationError() }
    courseDeletionState.tombstones[id] = marker
    let uploadsRetired = retireCourseUploadsForDeletion(courseIDs: aliases, scope: courseUploadScope)
    for alias in aliases {
      importState.tasks[alias]?.cancel(); importState.tasks[alias] = nil
      importState.processingRoutes[alias] = nil
      let key = scope + "::" + alias
      cloudState.stageSaveTasks[key]?.cancel(); cloudState.stageSaveTasks[key] = nil
      cloudState.localStageFingerprints[key] = nil
    }
    cancelLocalCourseGenerations(courseIDs: aliases, scope: scope)
    if cloudState.diagnosisWorkspaceID.map(aliases.contains) == true {
      cloudState.courseTask?.cancel(); cloudState.courseTask = nil; cloudState.courseJob = nil
      cloudState.interview = nil; cloudState.showDiagnosis = false
      cloudState.diagnosisWorkspaceID = nil; cloudState.diagnosisResourceID = nil
    }
    assistantHistory.removeCourseReferences(tombstones: Array(courseDeletionState.tombstones.values))
    applyCourseDeletionsInMemory(Array(courseDeletionState.tombstones.values))
    persist()
    guard uploadsRetired else {
      throw ContentError.storage("课程删除已生效，上传记录和原件清理未完成；下次启动会继续清理。")
    }
    try await repository.finishCourseDeletion(scope: scope)
    guard noteScope == scope, epoch == cloudState.epoch, !Task.isCancelled else { throw CancellationError() }
  }
  func applyCourseDeletionsInMemory(_ tombstones: [CourseDeletionTombstone]) {
    guard !tombstones.isEmpty else { return }
    let removed = CourseDeletionPruning.ids(tombstones)
    let wasSelected = selectedCourseID.map(removed.contains) == true
    let state = CloudLocalSnapshot(snapshot: snapshot(), sources: sources, workspaces: cloudState.workspaces,
      filePaths: filePaths, pending: cloudState.pending, creationWorkspaceID: cloudState.creationWorkspaceID,
      notes: notes, pendingSelection: cloudState.pendingSelection, pendingReviews: cloudState.pendingReviews,
      dailyPlans: dailyPlanState.plans, pendingPlanCompletions: dailyPlanState.pending)
    let filtered = CourseDeletionPruning.cloud(state, tombstones: tombstones), saved = filtered.snapshot
    courses = saved.courses; draft = saved.draft; selectedCourseID = saved.selectedCourseID; selectedBookID = saved.selectedBookID
    locations = saved.locations; mistakes = saved.mistakes; mastered = saved.mastery
    dismissedSourceIDs = saved.dismissedSourceIDs; chapterDrafts = saved.chapterDrafts ?? [:]
    importState.courses = saved.courseImports ?? []; importJob = saved.importJob
    let deletedNoteIDs = Set(notes.map(\.id)).subtracting(filtered.notes?.map(\.id) ?? [])
    sources = filtered.sources; filePaths = filtered.filePaths; notes = filtered.notes ?? []
    for id in deletedNoteIDs { noteState.revisions[id] = nil }
    if noteState.focusNoteID.map(deletedNoteIDs.contains) == true { noteState.focusNoteID = nil }
    cloudState.workspaces = filtered.workspaces; cloudState.pending = filtered.pending
    cloudState.pendingReviews = filtered.pendingReviews ?? []; cloudState.pendingSelection = filtered.pendingSelection
    cloudState.creationWorkspaceID = filtered.creationWorkspaceID
    cloudState.localDeletedCourseIDs.formUnion(removed)
    cloudState.localCourseSources = cloudState.localCourseSources.filter { !removed.contains($0.key) }
    cloudState.localGenerationStatuses = cloudState.localGenerationStatuses.filter { !removed.contains($0.value.courseID) }
    cloudState.localDiagnostics = cloudState.localDiagnostics.filter { !removed.contains($0.value.courseID) }
    assistantState.learningObjects.removeAll { removed.contains($0.course_id) }
    dailyPlanState.plans = filtered.dailyPlans ?? [:]; dailyPlanState.pending = filtered.pendingPlanCompletions ?? []
    dailyPlanState.errors = dailyPlanState.errors.filter { key, _ in !CourseDeletionPruning.courseKeyIsRemoved(key, separator: ":", removed: removed) }
    dailyPlanState.requests = dailyPlanState.requests.filter { key, _ in !CourseDeletionPruning.courseKeyIsRemoved(key, separator: ":", removed: removed) }
    dailyPlanState.confirming = dailyPlanState.confirming.filter { key in !CourseDeletionPruning.courseKeyIsRemoved(key, separator: ":", removed: removed) }
    if dailyPlanState.focus.map({ removed.contains($0.workspaceID) }) == true { dailyPlanState.focus = nil }
    if importState.viewedCourseID.map(removed.contains) == true { importState.viewedCourseID = nil; importError = nil; importedFileURL = nil }
    if importState.legacyCourseID.map(removed.contains) == true { importState.legacyCourseID = nil }
    if cloudState.confirmedSelection.map({ removed.contains($0.workspace_id) }) == true { cloudState.confirmedSelection = nil }
    if wasSelected {
      selectedChapterID = nil; selectedLessonID = nil; sourceTarget = nil; selectedQuote = ""; selectedAsset = nil
      latestDiagnosis = nil; assignmentAnswer = ""; diagnosisText = ""
      sheet = nil; path = []; replace(.study)
      if let next = selectedCourseID { installLocalCourseContent(next) }
    }
  }
}
