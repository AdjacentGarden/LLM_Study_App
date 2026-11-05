import CryptoKit
import Foundation
import Observation
import UIKit

struct CourseUploadFile: Codable, Identifiable, Sendable {
  enum Status: String, Codable, Sendable { case preparing, queued, uploading, completed, failed, cancelled }
  var id = UUID()
  var requestID = UUID()
  var filename: String
  var relativePath: String?
  var bookID: String?
  var originalBookID: String?
  var contentHash: String? = nil
  var skippedDuplicate: Bool? = nil
  var reusedFromCourseID: String? = nil
  var sentBytes: Int64 = 0
  var totalBytes: Int64 = 0
  var status: Status = .preparing
  var error: String?
  var lastProgressAt = Date.now
  var slowNoticeShown = false
  var isSkippedDuplicate: Bool { skippedDuplicate == true }
  var fraction: Double { status == .completed ? 1 : totalBytes > 0 ? min(1, Double(sentBytes) / Double(totalBytes)) : 0 }
}

struct CourseUploadBatch: Codable, Identifiable, Sendable {
  var id = UUID()
  var courseID: String
  var courseName: String
  var scope: String
  var draft: CourseDraft
  var addingMaterials: Bool
  var files: [CourseUploadFile]
  var onDeviceOnly = false
  var completionAcknowledged = false
  /// A duplicate-only import has no course of its own. Keep its redirect in the
  /// upload ledger so restoration cannot bring its temporary course back.
  var reusedCourseID: String? = nil
  var isRunning: Bool { files.contains { [.preparing, .queued, .uploading].contains($0.status) } }
  var isComplete: Bool { !files.isEmpty && files.allSatisfy { $0.status == .completed } }
  var hasFailures: Bool { files.contains { [.failed, .cancelled].contains($0.status) } }
}

struct CourseUploadPresentation: Identifiable { var id: UUID }

@MainActor @Observable final class CourseUploadState {
  var batches: [CourseUploadBatch] = []
  var presentedID: UUID?
  @ObservationIgnored var loadedScope: String?
  @ObservationIgnored var tasks: [UUID: Task<Void, Never>] = [:]
  @ObservationIgnored var taskTokens: [UUID: UUID] = [:]
  @ObservationIgnored var stagedBooksByHash: [String: String] = [:]
  @ObservationIgnored var monitorToken = UUID()
  @ObservationIgnored var monitor: Task<Void, Never>?
}

@MainActor extension AppStore {
  var courseUploadScope: String { cloud == nil ? "local" : cloudState.cacheKey ?? "signed-out" }
  func courseUpload(for courseID: String) -> CourseUploadBatch? {
    uploadState.batches.last { $0.courseID == courseID && (!$0.completionAcknowledged || !$0.isComplete) }
  }
  private var courseUploadFileURL: URL {
    let hash = SHA256.hash(data: Data(courseUploadScope.utf8)).map { String(format: "%02x", $0) }.joined()
    return repository.directory.appendingPathComponent("course-uploads-\(hash).json")
  }
  @discardableResult func persistCourseUploads() -> Bool {
    do {
      let batches = uploadState.batches.filter { !courseDeletionState.deletedIDs.contains($0.courseID) }
      let data = try JSONEncoder().encode(batches)
      try data.write(to: courseUploadFileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
      return true
    } catch {
      showToast("上传进度保存失败", kind: .error)
      return false
    }
  }

  /// Called after account hydration. Reattaches to system transfers, rather than
  /// starting a second HTTP write when a background transfer already exists.
  func restoreCourseUploads() {
    guard !isLoading, cloud == nil || cloudState.authenticated,
      courseDeletionState.scope == noteScope else { return }
    guard uploadState.loadedScope != courseUploadScope else { return }
    suspendCourseUploads()
    uploadState.loadedScope = courseUploadScope
    #if DEBUG
      if installCourseUploadFixtureIfRequested() { return }
    #endif
    uploadState.batches = (try? Data(contentsOf: courseUploadFileURL))
      .flatMap { try? JSONDecoder().decode([CourseUploadBatch].self, from: $0) } ?? []
    uploadState.batches.removeAll { courseDeletionState.deletedIDs.contains($0.courseID) }
    applyArchivedCourseUploadAliases()
    for id in uploadState.batches.map(\.id) {
      if let pending = uploadState.batches.first(where: { $0.id == id }),
        pending.reusedCourseID == nil, pending.isComplete,
        pending.files.allSatisfy(\.isSkippedDuplicate) {
        finishStagingCourseUpload(id, showNotice: false)
      }
      guard let batch = uploadState.batches.first(where: { $0.id == id }) else { continue }
      // A process may stop after the receipt is durable but before its larger
      // account snapshot finishes. Reapply that accepted identity locally.
      for file in batch.files {
        if let original = file.originalBookID, let book = file.bookID, original != book,
          sources.contains(where: { $0.id == original }) {
          bindUploadedBook(localID: original,
            uploaded: CloudUpload(book_id: book, filename: file.filename, status: "uploaded"))
        }
      }
      if batch.reusedCourseID == nil, !courses.contains(where: { $0.id == batch.courseID }) {
        courses.append(Course(id: batch.courseID, name: batch.courseName,
          resourceIDs: Array(Set(batch.files.filter { !$0.isSkippedDuplicate }.compactMap(\.bookID))),
          activeResourceID: batch.files.first { !$0.isSkippedDuplicate }?.bookID))
      }
      for file in batch.files where file.status == .preparing {
        updateCourseUpload(batch.id, fileID: file.id) {
          $0.status = .failed
          $0.error = "文件尚未保存，请重新选择此文件。"
        }
      }
      if batch.isRunning { launchCourseUpload(batch.id) }
    }
    persistCourseUploads()
    startCourseUploadMonitor()
    if cloudState.privateLocalMode { Task { await resumePublicLibraryTasks() } }
  }

  /// Archiving an empty historical duplicate must also retire its upload
  /// receipt; otherwise a restored queued receipt would recreate that course.
  @discardableResult func applyArchivedCourseUploadAliases() -> Bool {
    var changed = false
    for index in uploadState.batches.indices {
      let batch = uploadState.batches[index]
      guard batch.scope == courseUploadScope else { continue }
      var targetID = batch.courseID
      var visited: Set<String> = []
      while let next = cloudState.archivedDuplicateCourseAliases[targetID], visited.insert(targetID).inserted {
        targetID = next
      }
      guard targetID != batch.courseID, let target = courses.first(where: { $0.id == targetID }) else { continue }
      uploadState.tasks[batch.id]?.cancel()
      uploadState.tasks[batch.id] = nil
      uploadState.taskTokens[batch.id] = nil
      for file in batch.files where [.queued, .uploading].contains(file.status) {
        if let cloud, cloud.usesNativeBackgroundUploads {
          Task { await BackgroundUploadTransport.client(origin: cloud.baseURL, scope: batch.scope).cancel(file.requestID) }
        }
      }
      uploadState.batches[index].courseID = target.id
      uploadState.batches[index].courseName = target.name
      uploadState.batches[index].draft.importCourseID = target.id
      uploadState.batches[index].draft.resourceIDs = []
      uploadState.batches[index].reusedCourseID = target.id
      uploadState.batches[index].completionAcknowledged = true
      for f in uploadState.batches[index].files.indices {
        uploadState.batches[index].files[f].skippedDuplicate = true
        uploadState.batches[index].files[f].reusedFromCourseID = target.id
        uploadState.batches[index].files[f].status = .completed
        uploadState.batches[index].files[f].error = nil
        uploadState.batches[index].files[f].sentBytes = uploadState.batches[index].files[f].totalBytes
      }
      if uploadState.presentedID == batch.id { uploadState.presentedID = nil }
      changed = true
    }
    if changed { persistCourseUploads() }
    return changed
  }

  /// Retire only the deleted course's receipts and native transfers. Removing
  /// receipts is essential: launch recovery otherwise recreates missing courses.
  @discardableResult func retireCourseUploadsForDeletion(courseIDs: Set<String>, scope: String) -> Bool {
    guard scope == courseUploadScope else { return false }
    let saved = (try? Data(contentsOf: courseUploadFileURL))
      .flatMap { try? JSONDecoder().decode([CourseUploadBatch].self, from: $0) } ?? []
    let owned = (uploadState.batches + saved).filter { $0.scope == scope && courseIDs.contains($0.courseID) }
    let ids = Set(owned.map(\.id))
    for batch in owned {
      uploadState.tasks[batch.id]?.cancel(); uploadState.tasks[batch.id] = nil
      uploadState.taskTokens[batch.id] = nil
      for file in batch.files {
        if let cloud, cloud.usesNativeBackgroundUploads {
          Task { await BackgroundUploadTransport.client(origin: cloud.baseURL, scope: scope).cancel(file.requestID) }
        }
      }
    }
    if uploadState.presentedID.map(ids.contains) == true { uploadState.presentedID = nil }
    let remaining = (uploadState.loadedScope == scope ? uploadState.batches : saved)
      .filter { !($0.scope == scope && courseIDs.contains($0.courseID)) }
    if uploadState.loadedScope == scope { uploadState.batches = remaining }
    do {
      try JSONEncoder().encode(remaining).write(to: courseUploadFileURL,
        options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
      return true
    } catch {
      // The persisted course tombstone still prevents restoration of this ledger.
      cloudState.cacheWriteError = error.localizedDescription
      return false
    }
  }

  func suspendCourseUploads() {
    for task in uploadState.tasks.values { task.cancel() }
    uploadState.tasks = [:]
    uploadState.taskTokens = [:]
    uploadState.stagedBooksByHash = [:]
    uploadState.monitorToken = UUID()
    uploadState.monitor?.cancel()
    uploadState.monitor = nil
    uploadState.presentedID = nil
    uploadState.batches = []
    uploadState.loadedScope = nil
  }

  @discardableResult func beginCourseUpload(_ urls: [URL], targetCourseID: String? = nil) -> UUID? {
    guard !urls.isEmpty, cloud == nil || cloudState.authenticated else { return nil }
    if uploadState.loadedScope != courseUploadScope { restoreCourseUploads() }
    if let targetCourseID, courseDeletionState.deletedIDs.contains(targetCourseID)
      || courseDeletionState.deleting.contains(targetCourseID) { return nil }
    let course = targetCourseID.flatMap { id in courses.first { $0.id == id } }
    let courseID = course?.id ?? "local-course:upload:" + UUID().uuidString
    var savedDraft = course == nil ? draft : CourseDraft()
    savedDraft.name = course?.name ?? (savedDraft.name.isEmpty
      ? urls[0].deletingPathExtension().lastPathComponent : savedDraft.name)
    savedDraft.resourceIDs = []
    savedDraft.importCourseID = courseID
    // A batch owns its draft. Other courses and questionnaires remain independent.
    let batch = CourseUploadBatch(courseID: courseID, courseName: savedDraft.name,
      scope: courseUploadScope, draft: savedDraft, addingMaterials: course != nil,
      files: urls.map { CourseUploadFile(filename: $0.lastPathComponent) })
    if course == nil {
      courses.append(Course(id: courseID, name: batch.courseName, resourceIDs: savedDraft.resourceIDs))
    }
    uploadState.batches.append(batch)
    uploadState.presentedID = batch.id
    closeSheet()
    persistCourseUploads()
    persist()
    launchCourseUpload(batch.id, originals: Dictionary(uniqueKeysWithValues: zip(batch.files.map(\.id), urls)))
    return batch.id
  }

  private func launchCourseUpload(_ id: UUID, originals: [UUID: URL] = [:]) {
    guard uploadState.tasks[id] == nil else { return }
    let epoch = cloudState.epoch
    let token = UUID()
    uploadState.taskTokens[id] = token
    uploadState.tasks[id] = Task { @MainActor in
      await runCourseUpload(id, originals: originals, epoch: epoch)
      if epoch == cloudState.epoch, uploadState.taskTokens[id] == token {
        uploadState.tasks[id] = nil
        uploadState.taskTokens[id] = nil
      }
    }
    startCourseUploadMonitor()
  }

  private func stageCourseUpload(_ id: UUID, originals: [UUID: URL], epoch: UUID) async throws {
    guard let initial = uploadState.batches.first(where: { $0.id == id }) else { throw CancellationError() }
    let scope = initial.scope
    // Protect only the short local copy; the system URLSession owns the long upload.
    let lease = UIApplication.shared.beginBackgroundTask(withName: "Save course originals") {
      Task { @MainActor in
        guard self.courseUploadIsCurrent(id, epoch: epoch) else { return }
        self.cancelCourseUpload(id)
      }
    }
    defer { if lease != .invalid { UIApplication.shared.endBackgroundTask(lease) } }
    for file in initial.files where file.status == .preparing {
      try Task.checkCancellation()
      guard courseUploadIsCurrent(id, epoch: epoch), let original = originals[file.id] else { continue }
      let repository = repository
      let owner = CourseOriginalOwner(courseID: initial.courseID, scope: scope,
        id: initial.id.uuidString + ":" + file.id.uuidString)
      let staging = Task.detached(priority: .userInitiated) {
        try await LocalFileImporter.stage(url: original, repository: repository, owner: owner)
      }
      do {
        let staged = try await withTaskCancellationHandler { try await staging.value } onCancel: { staging.cancel() }
        try Task.checkCancellation()
        guard courseUploadIsCurrent(id, epoch: epoch) else { throw CancellationError() }
        let indexedIDs: [String]
        if let hash = staged.contentHash {
          indexedIDs = try await repository.matchingBookIDs(contentHash: hash, scope: scope, books: filePaths)
        } else { indexedIDs = [] }
        try Task.checkCancellation()
        guard courseUploadIsCurrent(id, epoch: epoch) else { throw CancellationError() }
        // Claim on the main actor before the next suspension. Two overlapping
        // imports of the same bytes then share the first file and HTTP request.
        let claimedID = staged.contentHash.flatMap { uploadState.stagedBooksByHash[$0] }
        let matchingIDs = Set(indexedIDs + [claimedID, staged.source.id].compactMap { $0 })
        let existingCourse = preferredCourseForDuplicate(bookIDs: matchingIDs,
          excluding: initial.addingMaterials ? nil : initial.courseID)
        let courseBookID = existingCourse?.resourceIDs.first { matchingIDs.contains($0) }
          ?? uploadState.batches.first { $0.courseID == existingCourse?.id }?.files.first {
            $0.bookID.map(matchingIDs.contains) == true && !$0.isSkippedDuplicate
          }?.bookID
        let bookID = courseBookID ?? claimedID ?? indexedIDs.first ?? staged.source.id
        if let hash = staged.contentHash { uploadState.stagedBooksByHash[hash] = bookID }
        let priorFile = uploadState.batches.first(where: { $0.id == id })?.files.first {
          $0.id != file.id && $0.bookID == bookID && !$0.isSkippedDuplicate
        }
        let duplicate = priorFile != nil || existingCourse != nil
        if !duplicate, !sources.contains(where: { $0.id == bookID }), bookID == staged.source.id {
          sources.append(staged.source)
        }
        let existingPath = filePaths[bookID].flatMap { path -> String? in
          (try? repository.attachmentURL(path).resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true ? path : nil
        }
        let path = existingPath ?? staged.relativePath
        filePaths[bookID] = path
        let bytes = (try? repository.attachmentURL(path)
          .resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? 0
        updateCourseUpload(id, fileID: file.id) {
          $0.relativePath = path; $0.bookID = bookID; $0.originalBookID = bookID
          $0.contentHash = staged.contentHash; $0.skippedDuplicate = duplicate
          $0.reusedFromCourseID = existingCourse?.id
          $0.totalBytes = bytes; $0.status = duplicate ? .completed : .queued
          if duplicate { $0.sentBytes = bytes }
        }
        if let hash = staged.contentHash {
          try await repository.recordContentIdentity(contentHash: hash, bookID: bookID, relativePath: path, scope: scope)
        }
        try Task.checkCancellation()
        guard courseUploadIsCurrent(id, epoch: epoch) else { throw CancellationError() }
        // The owned file and course identity are durable before any HTTP write.
        try await repository.saveFilePaths(filePaths)
        if cloud == nil { try await repository.saveSources(sources) }
        persist()
        await persistTask?.value
        try Task.checkCancellation()
        guard courseUploadIsCurrent(id, epoch: epoch) else { throw CancellationError() }
        persistCourseUploads()
      } catch is CancellationError { throw CancellationError() }
      catch {
        guard courseUploadIsCurrent(id, epoch: epoch) else { throw CancellationError() }
        updateCourseUpload(id, fileID: file.id) { $0.status = .failed; $0.error = error.localizedDescription }
        persistCourseUploads()
      }
    }
    guard courseUploadIsCurrent(id, epoch: epoch) else { throw CancellationError() }
    finishStagingCourseUpload(id)
    persistCourseUploads()
    persist()
  }

  private func courseUploadIsCurrent(_ id: UUID, epoch: UUID) -> Bool {
    epoch == cloudState.epoch && uploadState.loadedScope == courseUploadScope
      && uploadState.batches.contains { $0.id == id && $0.scope == courseUploadScope
        && !courseDeletionState.deletedIDs.contains($0.courseID)
        && !courseDeletionState.deleting.contains($0.courseID) }
  }

  /// Matching uses the already computed content identity, never the filename.
  /// Include another batch's staged files, before its course snapshot is saved.
  private func preferredCourseForDuplicate(bookIDs: Set<String>, excluding excludedID: String?) -> Course? {
    let excluded = Set([excludedID].compactMap { $0 })
    if let course = preferredDuplicateCourse(matchingBookIDs: bookIDs, excluding: excluded) { return course }
    let pendingCourseIDs = Set(uploadState.batches.filter { batch in
      batch.scope == courseUploadScope && batch.files.contains {
        $0.bookID.map(bookIDs.contains) == true && !$0.isSkippedDuplicate
      }
    }.map(\.courseID))
    return courses.filter {
      !excluded.contains($0.id) && pendingCourseIDs.contains($0.id)
    }.sorted { $0.createdAt < $1.createdAt }.first
  }

  private func finishStagingCourseUpload(_ id: UUID, showNotice: Bool = true) {
    guard let index = uploadState.batches.firstIndex(where: { $0.id == id }) else { return }
    let batch = uploadState.batches[index]
    let duplicates = batch.files.filter(\.isSkippedDuplicate)
    guard !duplicates.isEmpty else { retainCourseUploadResources(id); return }
    let isPresented = showNotice && uploadState.presentedID == id
    if duplicates.count == batch.files.count, !batch.addingMaterials,
      let preferred = preferredCourseForDuplicate(bookIDs: Set(duplicates.compactMap(\.bookID)), excluding: batch.courseID) {
      // Only this batch's untouched placeholder is removed. Existing courses,
      // learning content and personal records are never merged or deleted.
      courses.removeAll { $0.id == batch.courseID && $0.resourceIDs.isEmpty }
      uploadState.batches[index].courseID = preferred.id
      uploadState.batches[index].courseName = preferred.name
      uploadState.batches[index].draft.importCourseID = preferred.id
      uploadState.batches[index].draft.resourceIDs = []
      uploadState.batches[index].reusedCourseID = preferred.id
      uploadState.batches[index].completionAcknowledged = true
      if isPresented {
        uploadState.presentedID = nil
        openCourse(preferred)
      } else if selectedCourseID == batch.courseID {
        selectCourse(preferred.id)
        importState.viewedCourseID = courseImport(for: preferred.id)?.id
      }
      let subject = duplicates.count == 1 ? "这本教材" : "这些资料"
      if showNotice {
        showToast("\(subject)已在「\(preferred.name)」中\(isPresented ? "，已为你打开" : "，无需重复导入")", kind: .info)
      }
      return
    }
    if duplicates.count == batch.files.count {
      uploadState.batches[index].completionAcknowledged = true
      if isPresented { uploadState.presentedID = nil }
      if showNotice {
        let ownerIDs = Set(duplicates.compactMap(\.reusedFromCourseID))
        if ownerIDs.count == 1, let owner = courses.first(where: { ownerIDs.contains($0.id) }) {
          showToast("教材已在「\(owner.name)」中，无需重复导入", kind: .info)
        } else {
          showToast("所选资料已导入，无需重复添加", kind: .info)
        }
      }
    } else {
      retainCourseUploadResources(id)
      if showNotice { showToast("已跳过 \(duplicates.count) 份重复教材，继续导入新资料", kind: .info) }
    }
  }

  private func runCourseUpload(_ id: UUID, originals: [UUID: URL], epoch: UUID) async {
    do {
      try await stageCourseUpload(id, originals: originals, epoch: epoch)
      try Task.checkCancellation()
      guard courseUploadIsCurrent(id, epoch: epoch), let batch = uploadState.batches.first(where: { $0.id == id }) else { return }
      // Duplicate-only selections reuse their existing course without asking
      // for a processing route or starting another upload/parse job.
      guard batch.files.contains(where: { [.queued, .uploading].contains($0.status) }) else { return }
      let route = batch.onDeviceOnly ? ProcessingRoute.onDevice : cloud == nil ? .onDevice : await processingPolicy.route(for: .ocr)
      try Task.checkCancellation()
      guard courseUploadIsCurrent(id, epoch: epoch) else { return }
      guard route != .cancelled else { cancelCourseUpload(id); return }
      if cloud != nil, route == .onDevice, noteScope != nil { cloudState.privateLocalMode = true }
      if route == .existing, cloud != nil, !cloudState.localFirstChecked {
        try await verifyLocalFirstCapabilities()
        guard courseUploadIsCurrent(id, epoch: epoch), !Task.isCancelled else { return }
      }
      if let index = uploadState.batches.firstIndex(where: { $0.id == id }) {
        uploadState.batches[index].onDeviceOnly = route == .onDevice
      }
      persistCourseUploads()
      // Queue every file with the system while the app is active, so later files
      // don't depend on a suspended Swift task starting the next HTTP request.
      await withTaskGroup(of: Void.self) { group in
        for file in batch.files where [.queued, .uploading].contains(file.status) {
          group.addTask { await self.uploadCourseFile(batchID: id, fileID: file.id, route: route, epoch: epoch) }
        }
      }
      guard courseUploadIsCurrent(id, epoch: epoch), !Task.isCancelled else { return }
      persistCourseUploads()
      persist()
    } catch is CancellationError { return }
    catch {
      guard courseUploadIsCurrent(id, epoch: epoch) else { return }
      if let batch = uploadState.batches.first(where: { $0.id == id }) {
        for file in batch.files where [.preparing, .queued, .uploading].contains(file.status) {
          updateCourseUpload(id, fileID: file.id) { $0.status = .failed; $0.error = error.localizedDescription }
        }
      }
      persistCourseUploads()
    }
  }

  private func uploadCourseFile(batchID: UUID, fileID: UUID, route: ProcessingRoute, epoch: UUID) async {
    guard !Task.isCancelled, courseUploadIsCurrent(batchID, epoch: epoch),
      let batch = uploadState.batches.first(where: { $0.id == batchID }),
      let file = batch.files.first(where: { $0.id == fileID }), !file.isSkippedDuplicate, let path = file.relativePath,
      let localID = file.bookID else { return }
    updateCourseUpload(batchID, fileID: fileID) { $0.status = .uploading; $0.lastProgressAt = .now }
    persistCourseUploads()
    do {
      if let cloud, route == .existing, localID.hasPrefix("book_local_") {
        let uploaded = try await cloud.uploadPDF(repository.attachmentURL(path), filename: file.filename,
          background: BackgroundUploadIdentity(id: file.requestID, scope: batch.scope)) { [weak self] progress in
          guard let self, self.courseUploadIsCurrent(batchID, epoch: epoch) else { return }
          self.updateCourseUpload(batchID, fileID: fileID) {
            guard $0.status == .uploading else { return }
            if progress.sentBytes > $0.sentBytes { $0.lastProgressAt = .now; $0.slowNoticeShown = false }
            $0.sentBytes = max($0.sentBytes, progress.sentBytes)
            $0.totalBytes = max($0.totalBytes, progress.totalBytes)
          }
        }
        try Task.checkCancellation()
        guard courseUploadIsCurrent(batchID, epoch: epoch) else { return }
        bindUploadedBook(localID: localID, uploaded: uploaded)
        for hash in Array(uploadState.stagedBooksByHash.keys)
        where uploadState.stagedBooksByHash[hash] == localID {
          uploadState.stagedBooksByHash[hash] = uploaded.book_id
        }
        for b in uploadState.batches.indices {
          for f in uploadState.batches[b].files.indices where uploadState.batches[b].files[f].bookID == localID {
            uploadState.batches[b].files[f].bookID = uploaded.book_id
          }
        }
        if let hash = file.contentHash {
          try await repository.recordContentIdentity(contentHash: hash, bookID: uploaded.book_id,
            relativePath: path, scope: batch.scope)
        }
      }
      try Task.checkCancellation()
      guard courseUploadIsCurrent(batchID, epoch: epoch) else { return }
      if let cloud, !cloudState.privateLocalMode, batch.addingMaterials,
        let bookID = uploadState.batches.first(where: { $0.id == batchID })?.files.first(where: { $0.id == fileID })?.bookID,
        !batch.courseID.hasPrefix("local-course:"),
        cloudState.workspaces.first(where: { $0.id == batch.courseID })?.resources.contains(where: { $0.book_id == bookID }) != true {
        let _: CloudResource = try await cloud.request("/api/workspaces/\(batch.courseID)/resources", method: "POST",
          body: ["book_id": .string(bookID)])
      }
      try Task.checkCancellation()
      guard courseUploadIsCurrent(batchID, epoch: epoch) else { return }
      updateCourseUpload(batchID, fileID: fileID) { $0.status = .completed; $0.sentBytes = $0.totalBytes; $0.error = nil }
      retainCourseUploadResources(batchID)
      let durable = persistCourseUploads()
      persist()
      await persistTask?.value
      guard courseUploadIsCurrent(batchID, epoch: epoch), !Task.isCancelled else { return }
      let snapshotDurable = durable && cloudState.cacheWriteError == nil
      if route == .existing, cloudState.privateLocalMode, let completedBatch = uploadState.batches.first(where: { $0.id == batchID }), completedBatch.isComplete {
        do { try await queuePublicCoursePublication(title: completedBatch.courseName,
          bookIDs: completedBatch.files.filter { !$0.isSkippedDuplicate }.compactMap(\.bookID), courseID: completedBatch.courseID) }
        catch { cloudState.syncError = "公开资料整理登记可重试：" + error.localizedDescription }
      }
      if let cloud, cloud.usesNativeBackgroundUploads, snapshotDurable {
        BackgroundUploadTransport.client(origin: cloud.baseURL, scope: batch.scope).acknowledge(file.requestID)
      }
    } catch is CancellationError {
      guard courseUploadIsCurrent(batchID, epoch: epoch), !Task.isCancelled else { return }
      updateCourseUpload(batchID, fileID: fileID) {
        $0.status = .failed; $0.error = "上传已中断，请重试。原文件仍保留在本机。"
      }
      persistCourseUploads()
    }
    catch {
      guard courseUploadIsCurrent(batchID, epoch: epoch), !Task.isCancelled else { return }
      updateCourseUpload(batchID, fileID: fileID) { $0.status = .failed; $0.error = error.localizedDescription }
      persistCourseUploads()
    }
  }

  private func retainCourseUploadResources(_ id: UUID) {
    guard let index = uploadState.batches.firstIndex(where: { $0.id == id }) else { return }
    let ids = uploadState.batches[index].files.filter { !$0.isSkippedDuplicate }.compactMap(\.bookID)
    uploadState.batches[index].draft.resourceIDs = Array(NSOrderedSet(array: ids)) as? [String] ?? ids
    let batch = uploadState.batches[index]
    if let c = courses.firstIndex(where: { $0.id == batch.courseID }) {
      for bookID in batch.draft.resourceIDs where !courses[c].resourceIDs.contains(bookID) { courses[c].resourceIDs.append(bookID) }
      if courses[c].activeResourceID == nil { courses[c].activeResourceID = batch.draft.resourceIDs.first }
    }
    if let i = importState.courses.firstIndex(where: { $0.id == batch.courseID }) {
      for bookID in batch.draft.resourceIDs where !importState.courses[i].books.contains(where: { $0.id == bookID }) {
        importState.courses[i].books.append(CourseImportBook(id: bookID))
      }
      importState.courses[i].draft.resourceIDs = importState.courses[i].books.map(\.id)
      importState.courses[i].job.bookIDs = importState.courses[i].books.map(\.id)
      if importState.courses[i].books.contains(where: { $0.status != .completed }) {
        importState.courses[i].job.status = .queued
      }
    } else if !batch.draft.resourceIDs.isEmpty {
      importState.courses.append(CourseImportRecord(id: batch.courseID, draft: batch.draft,
        books: batch.draft.resourceIDs.map { CourseImportBook(id: $0) },
        job: ImportJob(bookIDs: batch.draft.resourceIDs, status: .queued), setupCompleted: batch.addingMaterials))
    }
  }

  private func updateCourseUpload(_ id: UUID, fileID: UUID, _ update: (inout CourseUploadFile) -> Void) {
    guard let b = uploadState.batches.firstIndex(where: { $0.id == id }),
      let f = uploadState.batches[b].files.firstIndex(where: { $0.id == fileID }) else { return }
    update(&uploadState.batches[b].files[f])
  }
  func backgroundCourseUpload(_ id: UUID) {
    uploadState.presentedID = nil
    closeSheet()
    replace(.study)
  }
  func showCourseUpload(_ id: UUID) { closeSheet(); uploadState.presentedID = id }
  func acknowledgeCourseUpload(_ id: UUID) {
    if let index = uploadState.batches.firstIndex(where: { $0.id == id }) {
      uploadState.batches[index].completionAcknowledged = true
      persistCourseUploads()
    }
  }
  func continueCourseUpload(_ id: UUID) {
    guard let batch = uploadState.batches.first(where: { $0.id == id }) else { return }
    if batch.reusedCourseID != nil || batch.files.allSatisfy(\.isSkippedDuplicate) {
      acknowledgeCourseUpload(id)
      uploadState.presentedID = nil
      if let course = courses.first(where: { $0.id == batch.courseID }) { openCourse(course) }
      return
    }
    let completed = batch.files.filter { $0.status == .completed && !$0.isSkippedDuplicate }.compactMap(\.bookID)
    guard !completed.isEmpty else { return }
    retainPendingCourseDraft()
    acknowledgeCourseUpload(id)
    draft = batch.draft
    draft.resourceIDs = Array(NSOrderedSet(array: completed)) as? [String] ?? completed
    if batch.onDeviceOnly { importState.processingRoutes[batch.courseID] = .onDevice }
    cloudState.creationWorkspaceID = draft.creationWorkspaceID
    selectCourse(batch.courseID)
    importedFileURL = completed.first.flatMap { sourceFileURL($0) }
    importState.viewedCourseID = batch.courseID
    uploadState.presentedID = nil
    importError = nil
    persist()
    go(.processing)
  }
  func cancelCourseUpload(_ id: UUID) {
    uploadState.tasks[id]?.cancel()
    uploadState.tasks[id] = nil
    uploadState.taskTokens[id] = nil
    guard let batch = uploadState.batches.first(where: { $0.id == id }) else { return }
    for file in batch.files where [.preparing, .queued, .uploading].contains(file.status) {
      updateCourseUpload(id, fileID: file.id) { $0.status = .cancelled; $0.error = "上传已取消，原文件仍保留在本机。" }
      if let cloud, cloud.usesNativeBackgroundUploads { Task { await BackgroundUploadTransport.client(origin: cloud.baseURL, scope: batch.scope).cancel(file.requestID) } }
    }
    persistCourseUploads()
  }
  func retryCourseUpload(_ id: UUID) {
    guard let index = uploadState.batches.firstIndex(where: { $0.id == id }), !uploadState.batches[index].isRunning else { return }
    // Terminal file states can be observed before the previous task group has
    // finished its persistence cleanup. Retire that task before starting a retry.
    uploadState.tasks[id]?.cancel()
    uploadState.tasks[id] = nil
    uploadState.taskTokens[id] = nil
    for f in uploadState.batches[index].files.indices
    where uploadState.batches[index].files[f].relativePath != nil
      && [.failed, .cancelled].contains(uploadState.batches[index].files[f].status) {
      uploadState.batches[index].files[f].status = .queued
      uploadState.batches[index].files[f].requestID = UUID()
      uploadState.batches[index].files[f].error = nil
      uploadState.batches[index].files[f].sentBytes = 0
      uploadState.batches[index].files[f].slowNoticeShown = false
    }
    persistCourseUploads()
    launchCourseUpload(id)
  }
  fileprivate func startCourseUploadMonitor() {
    guard uploadState.monitor == nil, uploadState.batches.contains(where: \.isRunning) else { return }
    let token = UUID()
    uploadState.monitorToken = token
    uploadState.monitor = Task { @MainActor in
      defer { if uploadState.monitorToken == token { uploadState.monitor = nil } }
      while !Task.isCancelled, uploadState.batches.contains(where: \.isRunning) {
        do { try await Task.sleep(for: .seconds(1)) } catch { return }
        checkCourseUploadStalls()
      }
    }
  }

  func checkCourseUploadStalls(at now: Date = .now) {
    for b in uploadState.batches.indices {
      for f in uploadState.batches[b].files.indices {
        let file = uploadState.batches[b].files[f]
        guard file.status == .uploading, !file.slowNoticeShown,
          now.timeIntervalSince(file.lastProgressAt) >= 15 else { continue }
        uploadState.batches[b].files[f].slowNoticeShown = true
        showToast(file.fraction >= 1 ? "正在等待服务器确认" : "上传较慢，请保持网络连接", kind: .info)
      }
    }
  }

}

#if DEBUG
@MainActor extension AppStore {
  /// Isolated UI fixtures exercise the real progress presentation, routing and alerts.
  /// Normal launches never create simulated transfers.
  func installCourseUploadFixtureIfRequested() -> Bool {
    let args = ProcessInfo.processInfo.arguments
    guard ContentBundle.fixtureMode, args.contains("--fixture-upload-progress") else { return false }
    let books = Array(sources.prefix(2).map(\.id))
    let files = [
      CourseUploadFile(filename: "高中数学必修第二册.pdf", bookID: books.first,
        sentBytes: 40_000_000, totalBytes: 40_000_000, status: .completed),
      CourseUploadFile(filename: "高中数学学习资料.pdf", bookID: books.last,
        sentBytes: 12_600_000, totalBytes: 40_000_000, status: .uploading,
        lastProgressAt: .now),
    ]
    let courseID = "local-course:fixture-upload"
    let savedDraft = CourseDraft(name: "数学上传课程", resourceIDs: books, importCourseID: courseID)
    let batch = CourseUploadBatch(courseID: courseID, courseName: savedDraft.name,
      scope: courseUploadScope, draft: savedDraft, addingMaterials: false, files: files)
    courses.append(Course(id: courseID, name: savedDraft.name, resourceIDs: books))
    importState.courses.append(CourseImportRecord(id: courseID, draft: savedDraft,
      books: books.map { CourseImportBook(id: $0) }, job: ImportJob(bookIDs: books, status: .queued)))
    uploadState.batches = [batch]
    uploadState.presentedID = batch.id
    if args.contains("--fixture-upload-slow") {
      uploadState.tasks[batch.id] = Task { @MainActor in
        do { try await Task.sleep(for: .seconds(5)) } catch { return }
        guard let b = uploadState.batches.firstIndex(where: { $0.id == batch.id }) else { return }
        uploadState.batches[b].files[1].lastProgressAt = .now.addingTimeInterval(-16)
        uploadState.tasks[batch.id] = nil
      }
    }
    if args.contains("--fixture-upload-finish") {
      uploadState.tasks[batch.id] = Task { @MainActor in
        do { try await Task.sleep(for: .seconds(10)) } catch { return }
        guard let b = uploadState.batches.firstIndex(where: { $0.id == batch.id }) else { return }
        uploadState.batches[b].files[1].status = .completed
        uploadState.batches[b].files[1].sentBytes = 40_000_000
        uploadState.tasks[batch.id] = nil
      }
    }
    startCourseUploadMonitor()
    return true
  }
}
#endif
