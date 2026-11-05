import CryptoKit
import Foundation

/// Reader text is a bounded hint, separate from the user's question and verified evidence.
struct CloudReadingContext: Sendable {
  let pdfPage: Int
  let pageText: String
  let noteText: String
  let noteTruncated: Bool

  init(pdfPage: Int, pageText: String, noteText: String = "") {
    self.pdfPage = max(1, pdfPage)
    self.pageText = String(String.UnicodeScalarView(pageText.unicodeScalars.prefix(1200)))
    self.noteText = String(String.UnicodeScalarView(noteText.unicodeScalars.prefix(4000)))
    self.noteTruncated = noteText.unicodeScalars.count > 4000
  }

  var json: JSONValue {
    .object(["pdf_page": .number(Double(pdfPage)), "page_text": .string(pageText),
      "note_text": .string(noteText), "note_truncated": .bool(noteTruncated)])
  }

  func localQuestion(_ question: String) -> String {
    "\(question)\n文字批注：\(noteText)\n（PDF 第 \(pdfPage) 页；当前页文字上下文：\(pageText)）"
  }
}

extension AppStore {
  var cloudResource: CloudResource? {
    if cloudState.privateLocalMode, let course = currentCourse, let source = currentSource {
      return CloudResource(id: courseResourceID(courseID: course.id, bookID: source.id) ?? course.id + ":" + source.id,
        book_id: source.id, workspace_id: course.id, revision: 0, filename: source.summary.filename ?? source.summary.title,
        status: "ready", structure: CloudStructure(title: source.summary.title, summary: "", source_page_count: source.summary.page_count,
          chapters: source.chapters.map { CloudChapter(chapter_id: $0.id, order: 0, title: $0.title, start_page: $0.page_start,
            end_page: $0.page_end, summary: "", knowledge_points: [], source_block_ids: []) }), courses: [], progress: CloudProgress(), source_content: source)
    }
    return cloudState.workspaces.first { $0.id == selectedCourseID }?.resources.first {
      $0.book_id == selectedBookID
    }
  }

  func handleCloudError(_ error: Error) {
    guard !(error is CancellationError) else { return }
    cloudState.error = error.localizedDescription
    showToast(error.localizedDescription, kind: .error)
    if let error = error as? CloudError, error.status == 401 {
      if let owner = cloudState.localLearningOwner {
        cancelLocalCourseGenerations(courseIDs: [owner.courseID], scope: owner.scope)
      }
      cloudState.isWorking = false
      cloudState.diagnosisRetryAvailable = false
      cloudState.diagnosisRetryEpoch = nil
      cloudState.authenticated = false
      cloudState.showDiagnosis = false
      cloudState.epoch = UUID()
      assistantHistory.reset()
      cloudState.materialSyncCount = 0
      processingPolicy.reset()
      cloudState.syncTask?.cancel()
      cloudState.retryTask?.cancel()
      cloudState.contentTask?.cancel()
      cloudState.courseTask?.cancel()
      cloudState.courseTask = nil
      cloudState.courseJob = nil
      cloudState.workspaces = []
      cloudState.account = nil
      cloudState.cacheKey = nil
      cloudState.privateLocalMode = false; cloudState.localFirstChecked = false
      cloudState.localGenerationStatuses = [:]; cloudState.localCourseSources = [:]; cloudState.localDiagnostics = [:]; cloudState.localDeletedCourseIDs = []
      dailyPlanState.reset()
      cloudState.pending = [:]
      cloudState.pendingReviews = []
      cloudState.pendingSelection = nil
      preferences = nil
      locations = [:]
      filePaths = [:]
      mastered = []
      selectedCourseID = nil
      selectedBookID = nil
      selectedChapterID = nil
      selectedLessonID = nil
      shelf = []
      mistakes = []
      chapterDrafts = [:]
      draft = CourseDraft()
      importJob = nil
      clearCourseImports()
      cloudState.onboardingComplete = false
      cloudState.requiresPersonalization = false
      route = .onboarding
      sources = []
      courses = []
      notes = []
      agent.reset()
      noteState.revisions = [:]
      chatMessages = []
      Task { await cloud?.clearCredentials() }
    }
  }

  func rebuildCloudCourses() {
    guard !cloudState.privateLocalMode else { return }
    if courseDeletionState.scope == noteScope {
      cloudState.workspaces.removeAll { courseDeletionState.deletedIDs.contains($0.id) }
    }
    let localCourses = courses.filter { $0.id.hasPrefix("local-course:") }
    courses = cloudState.workspaces.filter { workspace in
      !importState.courses.contains { $0.setupCompleted != true && workspaceID(for: $0) == workspace.id }
    }.map { workspace in
      let existing = courses.first { $0.id == workspace.id }
      let candidates = [
        existing?.activeResourceID, workspace.id == selectedCourseID ? selectedBookID : nil,
      ].compactMap { $0 }
      let active =
        candidates.first { id in workspace.resources.contains { $0.book_id == id } }
        ?? workspace.resources.first?.book_id
      return Course(
        id: workspace.id, name: workspace.name, resourceIDs: workspace.resources.map(\.book_id),
        courseResourceIDs: Dictionary(workspace.resources.map { ($0.book_id, $0.id) }, uniquingKeysWith: { first, _ in first }),
        activeResourceID: active, diagnosis: workspace.preferences,
        createdAt: Date(timeIntervalSince1970: workspace.created),
        updatedAt: Date(timeIntervalSince1970: workspace.updated))
    }
    courses += localCourses.filter { local in
      guard let record = importState.courses.first(where: { $0.id == local.id }) else { return true }
      return !courses.contains { remote in remote.id == workspaceID(for: record) }
    }
    for record in importState.courses where record.needsPreparation {
      if let index = courses.firstIndex(where: { $0.id == record.id }) {
        for book in record.books where !courses[index].resourceIDs.contains(book.id) {
          courses[index].resourceIDs.append(book.id)
        }
      }
    }
    for workspace in cloudState.workspaces {
      for resource in workspace.resources {
        if let index = sources.firstIndex(where: { $0.id == resource.book_id }) {
          if let cover = resource.cover_url { sources[index].summary.cover_url = cover }
        } else {
          sources.append(CloudMapping.source(resource))
        }
      }
    }
  }

  func installCloudSources(workspaceID: String) {
    if cloudState.privateLocalMode { installLocalCourseContent(workspaceID); return }
    guard let workspace = cloudState.workspaces.first(where: { $0.id == workspaceID }) else {
      return
    }
    for var resource in workspace.resources {
      if let pending = cloudState.pending[resource.id] { resource.progress = pending.value }
      let source = CloudMapping.source(resource)
      if let index = sources.firstIndex(where: { $0.id == source.id }) {
        sources[index] = LocalBookCourseRepository.mergingLocalOCR(from: sources[index], into: source)
      } else {
        sources.append(source)
      }
    }
  }

  func refreshCloudCatalog() async throws {
    try await verifyLocalFirstCapabilities()
    try await refreshPublicCourseGroups()
  }

  /// Manual sign-in starts a fresh questionnaire, including accounts signed out
  /// before this client version. Only the scoped onboarding state is reset.
  func prepareCloudLogin(_ account: CloudAccountState) async throws {
    guard cloud != nil else { return }
    await persistTask?.value
    let key = cloudCacheKey(account)
    if var state = try await repository.loadPersonalState(scope: key) {
      state.requiresPersonalization = true
      try await repository.savePersonalState(state, scope: key)
      LearnerOnboardingDraft.clear(scope: key)
      return
    }
    var cache = try await loadCloudCache(account) ?? CloudLocalSnapshot(
      snapshot: AppSnapshot(), sources: [], workspaces: [], filePaths: [:], pending: [:],
      creationWorkspaceID: nil, notes: [], pendingSelection: nil)
    cache.requiresPersonalization = true
    cache.onboardingComplete = false
    try await repository.saveCloudState(cache, key: key)
    LearnerOnboardingDraft.clear(scope: key)
  }

  func saveCloudPreferences(
    name: String, goal: String?, dailyTime: String?, learningBarriers: [String] = [],
    guidanceStyles: [String] = [], planningSupport: String? = nil, learningStage: String? = nil
  ) async throws {
    guard cloudState.privateLocalMode else { throw ContentError.invalid("服务尚未支持本机个人数据，请先重试连接") }
    preferences = LearnerPreferences(displayName: name, primaryGoal: goal, dailyTime: dailyTime,
      learningBarriers: learningBarriers, guidanceStyles: guidanceStyles, planningSupport: planningSupport,
      learningStage: learningStage)
    cloudState.requiresPersonalization = false
    cloudState.onboardingComplete = true
    try await persistPrivateState()
    return
  }
  func importCloudCommunity(_ book: CommunityBook) async throws {
    _ = try await acquirePublicCourseGroup(catalogID: book.id, name: draft.name.isEmpty ? book.title : draft.name)
    return
  }
  func retryCloudConnection() async {
    guard cloud != nil, cloudState.authenticated, !cloudState.isWorking else { return }
    let epoch = cloudState.epoch
    cloudState.isWorking = true
    defer { if epoch == cloudState.epoch { cloudState.isWorking = false } }
    startCloudSync()
    while let task = cloudState.syncTask { await task.value }
    guard epoch == cloudState.epoch, cloudState.authenticated, !Task.isCancelled else { return }
    // A successful metadata refresh must not hide a failed write or discard its queued edits.
    guard cloudState.pending.isEmpty, cloudState.pendingReviews.isEmpty,
      cloudState.pendingSelection == nil, cloudState.cacheWriteError == nil else { return }
    do {
      try await reloadCloud()
      if let workspace = selectedCourseID { selectCourse(workspace) }
      await reloadCloudLearningRecords()
      guard epoch == cloudState.epoch, !Task.isCancelled else { return }
      persistCloud()
    } catch {
      guard epoch == cloudState.epoch, !Task.isCancelled else { return }
      handleCloudError(error)
      cloudState.syncError = error.localizedDescription
    }
  }

  func logoutCloud() async {
    guard let cloud, !cloudState.isWorking else { return }
    cloudState.isWorking = true
    defer { cloudState.isWorking = false }
    await persistTask?.value
    cloudState.epoch = UUID()
    assistantHistory.reset()
    cloudState.materialSyncCount = 0
    processingPolicy.reset()
    cloudState.retryTask?.cancel()
    cloudState.contentTask?.cancel()
    cloudState.courseTask?.cancel()
    cloudState.courseTask = nil
    cloudState.courseJob = nil
    cloudState.recordsTask?.cancel()
    await cloudState.recordsTask?.value
    cloudState.syncTask?.cancel()
    await cloudState.syncTask?.value
    do {
      do { let _: CloudOK = try await cloud.request("/api/auth/logout", method: "POST") }
      catch let error as CloudError where error.transportFailure || error.status == 401 { }
      // Keep this requirement in the account's local cache, so bootstrap cannot
      // bypass the new questionnaire with the previous server-side answers.
      cloudState.requiresPersonalization = true
      cloudState.onboardingComplete = false
      LearnerOnboardingDraft.clear(scope: cloudState.cacheKey)
      saveCloudCacheOnly()
      await persistTask?.value
      await cloud.clearCredentials()
      cloudState.epoch = UUID()
      cloudState.authenticated = false
      cloudState.onboardingComplete = false
      cloudState.requiresPersonalization = false
      cloudState.account = nil
      cloudState.cacheKey = nil
      cloudState.privateLocalMode = false; cloudState.localFirstChecked = false
      cloudState.localGenerationStatuses = [:]; cloudState.localCourseSources = [:]; cloudState.localDiagnostics = [:]; cloudState.localDeletedCourseIDs = []
      dailyPlanState.reset()
      cloudState.pending = [:]
      cloudState.pendingReviews = []
      cloudState.cacheWriteError = nil
      cloudState.pendingSelection = nil
      cloudState.confirmedSelection = nil
      cloudState.creationWorkspaceID = nil
      cloudState.showDiagnosis = false
      cloudState.interview = nil
      cloudState.workspaces = []
      sources = []
      courses = []
      notes = []
      agent.reset()
      noteState.revisions = [:]
      locations = [:]
      mastered = []
      mistakes = []
      filePaths = [:]
      preferences = nil
      draft = CourseDraft()
      selectedBookID = nil
      selectedCourseID = nil
      chapterDrafts = [:]
      importJob = nil
      clearCourseImports()
      chatMessages = []
      sheet = nil
      path = []
      route = .onboarding
    } catch { handleCloudError(error) }
  }

  func ensureCloudPDF(bookID: String) async throws {
    guard let cloud else { return }
    if let relative = filePaths[bookID],
      FileManager.default.fileExists(atPath: repository.attachmentURL(relative).path)
    {
      return
    }
    let epoch = cloudState.epoch
    let bytes = try await cloud.downloadPDF(bookID: bookID)
    guard epoch == cloudState.epoch else { throw CancellationError() }
    filePaths[bookID] = try await repository.saveOriginalAttachment(bytes, extension: "pdf")
    persistCloud()
  }

  /// The original is durable and visible locally before any server request starts.
  func retainLocalImport(_ original: StagedSource, targetCourseID: String? = nil) async throws -> String {
    var staged = original
    let epoch = cloudState.epoch, scope = courseUploadScope
    if let hash = original.contentHash {
      let matches = try await repository.matchingBookIDs(contentHash: hash, scope: scope, books: filePaths)
      guard epoch == cloudState.epoch, scope == courseUploadScope else { throw CancellationError() }
      if let course = preferredDuplicateCourse(matchingBookIDs: Set(matches + [original.source.id])),
        let bookID = course.resourceIDs.first(where: { matches.contains($0) || $0 == original.source.id }) {
        if let existing = sources.first(where: { $0.id == bookID }) { staged.source = existing }
        staged.source.summary.book_id = bookID
        staged.source.plan.book_id = bookID
        let path = filePaths[bookID].flatMap { path in
          ((try? repository.attachmentURL(path).resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true) ? path : nil
        } ?? original.relativePath
        filePaths[bookID] = path
        filePaths[original.source.id] = path
        try await repository.recordContentIdentity(contentHash: hash, bookID: bookID, relativePath: path, scope: scope)
        guard epoch == cloudState.epoch, scope == courseUploadScope else { throw CancellationError() }
        if targetCourseID == nil && draft.editingCourseID == nil { openCourse(course) }
        showToast("这本教材已在「\(course.name)」中，无需重复导入", kind: .info)
        persist()
        await persistTask?.value
        return bookID
      }
      if let bookID = matches.first, let existing = sources.first(where: { $0.id == bookID }) { staged.source = existing }
    }
    // The content-derived local ID remains an alias after upload. Resolve it
    // before replacing its attachment path, including after a cache restore.
    if let path = filePaths[original.source.id],
      let existing = sources.first(where: {
        !$0.id.hasPrefix("book_local_") && filePaths[$0.id] == path
      }) {
      staged.source = existing
    }
    filePaths[original.source.id] = staged.relativePath
    if !sources.contains(where: { $0.id == staged.source.id }) { sources.append(staged.source) }
    filePaths[staged.source.id] = staged.relativePath
    if let hash = original.contentHash {
      try await repository.recordContentIdentity(contentHash: hash, bookID: staged.source.id,
        relativePath: staged.relativePath, scope: scope)
      guard epoch == cloudState.epoch, scope == courseUploadScope else { throw CancellationError() }
    }
    if !draft.resourceIDs.contains(staged.source.id) { draft.resourceIDs.append(staged.source.id) }
    let courseID = "local-course:" + staged.source.id
    let targetCourseID = targetCourseID ?? draft.importCourseID ?? draft.editingCourseID
    if let targetCourseID,
      let index = courses.firstIndex(where: { $0.id == targetCourseID }) {
      if !courses[index].resourceIDs.contains(staged.source.id) {
        courses[index].resourceIDs.append(staged.source.id)
      }
    } else if !courses.contains(where: { $0.resourceIDs.contains(staged.source.id) }) {
      courses.append(Course(id: courseID, name: staged.source.summary.title,
                            resourceIDs: [staged.source.id], activeResourceID: staged.source.id))
    }
    draft.parseCompleted = false
    if draft.name.isEmpty { draft.name = staged.source.summary.title }
    selectedBookID = staged.source.id
    selectedCourseID = courses.first { $0.resourceIDs.contains(staged.source.id) }?.id
    importedFileURL = repository.attachmentURL(staged.relativePath)
    draft.importCourseID = targetCourseID ?? selectedCourseID
    _ = registerCourseImport()
    persistCloud()
    await persistTask?.value
    if let error = cloudState.cacheWriteError { throw ContentError.storage(error) }
    return staged.source.id
  }

  func bindUploadedBook(localID: String, uploaded: CloudUpload) {
    for index in courses.indices where courses[index].resourceIDs.contains(localID) {
      let relationship = courseResourceID(courseID: courses[index].id, bookID: localID)
      var mapping = courses[index].courseResourceIDs ?? [:]
      if let relationship { mapping[uploaded.book_id] = relationship }
      mapping[localID] = nil
      courses[index].courseResourceIDs = mapping
    }
    if let i = sources.firstIndex(where: { $0.id == localID }) {
      if sources.contains(where: { $0.id == uploaded.book_id && $0.id != localID }) {
        sources.remove(at: i)
      } else {
        sources[i].summary.book_id = uploaded.book_id
        sources[i].summary.status = uploaded.status
        sources[i].plan.book_id = uploaded.book_id
        for j in sources[i].chunks.indices { sources[i].chunks[j].book_id = uploaded.book_id }
      }
    }
    for i in notes.indices where notes[i].anchor?.bookID == localID {
      notes[i].anchor?.bookID = uploaded.book_id
    }
    if let scope = noteScope {
      Task { [repository] in
        do {
          let stored = try await repository.rebindNoteAnchors(scope: scope, from: localID, to: uploaded.book_id)
          self.applyCommitted(stored.filter { $0.note.anchor?.bookID == uploaded.book_id }, scope: scope)
        } catch { self.showToast(error.localizedDescription, kind: .error) }
      }
    }
    for (key, value) in locations where key.hasSuffix("::" + localID) {
      locations[String(key.dropLast(localID.count)) + uploaded.book_id] = value
      locations.removeValue(forKey: key)
    }
    if sourceTarget?.bookID == localID { sourceTarget?.bookID = uploaded.book_id }

    if let path = filePaths[localID] {
      filePaths[uploaded.book_id] = path
      let scope = courseUploadScope, epoch = cloudState.epoch
      Task { [repository] in
        guard let hash = try? await repository.cachedContentHash(relativePath: path),
          scope == self.courseUploadScope, epoch == self.cloudState.epoch else { return }
        try? await repository.recordContentIdentity(contentHash: hash, bookID: uploaded.book_id,
          relativePath: path, scope: scope)
      }
    }
    draft.resourceIDs = Array(NSOrderedSet(array: draft.resourceIDs.map { $0 == localID ? uploaded.book_id : $0 })) as? [String] ?? draft.resourceIDs
    for i in courses.indices {
      courses[i].resourceIDs = Array(NSOrderedSet(array: courses[i].resourceIDs.map {
        $0 == localID ? uploaded.book_id : $0
      })) as? [String] ?? courses[i].resourceIDs
      if courses[i].activeResourceID == localID { courses[i].activeResourceID = uploaded.book_id }
    }
    if selectedBookID == localID { selectedBookID = uploaded.book_id }
    if let i = importJob?.bookIDs.firstIndex(of: localID) { importJob?.bookIDs[i] = uploaded.book_id }
    rebindCourseImports(localID: localID, bookID: uploaded.book_id)
    persistCloud()
  }

  func importCloudPDF(
    _ url: URL, targetCourseID: String? = nil,
    onProgress: @escaping @MainActor @Sendable (CloudUploadProgress) -> Void = { _ in }
  ) async {
    guard let cloud, cloudState.authenticated else { return }
    let epoch = cloudState.epoch
    guard let capturedScope = noteScope else { return }
    let routingSelection = selectionGeneration
    importError = nil
    var originalSaved = false
    do {
      guard url.pathExtension.lowercased() == "pdf" else {
        throw CloudError(status: 415, message: "云端课程目前支持 PDF，请选择 PDF 文件。")
      }
      if courseDeletionState.scope != capturedScope { try await restoreCourseDeletionState() }
      if let targetCourseID, !canApplyCourseMutation(courseID: targetCourseID, scope: capturedScope) { throw CancellationError() }
      let owner = targetCourseID.map { CourseOriginalOwner(courseID: $0, scope: capturedScope, id: "legacy-import:" + UUID().uuidString) }
      let staged = try await LocalFileImporter.stage(url: url, repository: repository, owner: owner)
      try Task.checkCancellation()
      guard epoch == cloudState.epoch, routingSelection == selectionGeneration, capturedScope == noteScope else { return }
      if let targetCourseID, !canApplyCourseMutation(courseID: targetCourseID, scope: capturedScope) { return }
      guard noteScope != nil else { throw ContentError.invalid("请先登录") }
      if let hash = staged.contentHash {
        let matches = try await repository.matchingBookIDs(contentHash: hash,
          scope: courseUploadScope, books: filePaths)
        guard epoch == cloudState.epoch, routingSelection == selectionGeneration, capturedScope == noteScope else { return }
      if let targetCourseID, !canApplyCourseMutation(courseID: targetCourseID, scope: capturedScope) { return }
        if preferredDuplicateCourse(matchingBookIDs: Set(matches + [staged.source.id])) != nil {
          _ = try await retainLocalImport(staged, targetCourseID: targetCourseID)
          return
        }
      }
      let processingRoute = await processingPolicy.route(for: .ocr)
      try Task.checkCancellation()
      guard epoch == cloudState.epoch, routingSelection == selectionGeneration, capturedScope == noteScope else { return }
      if let targetCourseID, !canApplyCourseMutation(courseID: targetCourseID, scope: capturedScope) { return }
      guard processingRoute != .cancelled else {
        importError = "已取消本次导入，原文件未上传。"
        return
      }
      cloudState.privateLocalMode = true
      let retainedID = try await retainLocalImport(staged, targetCourseID: targetCourseID)
      originalSaved = true
      try Task.checkCancellation()
      guard epoch == cloudState.epoch, capturedScope == noteScope else { return }
      if let targetCourseID, !canApplyCourseMutation(courseID: targetCourseID, scope: capturedScope) { return }
      // An identical original already has a server identity and preparation job.
      // Reusing it avoids creating another upload and a second progress card.
      if !retainedID.hasPrefix("book_local_") { return }
      if processingRoute == .onDevice {
        showToast("教材已保存在本机", subtitle: "将使用本机文字识别")
        return
      }
      if !cloudState.localFirstChecked { try await verifyLocalFirstCapabilities() }
      guard epoch == cloudState.epoch, capturedScope == noteScope, !Task.isCancelled else { return }
      let uploaded = try await cloud.uploadPDF(
        repository.attachmentURL(staged.relativePath), filename: url.lastPathComponent,
        onProgress: onProgress)
      try Task.checkCancellation()
      guard epoch == cloudState.epoch, capturedScope == noteScope else { return }
      if let targetCourseID, !canApplyCourseMutation(courseID: targetCourseID, scope: capturedScope) { return }
      bindUploadedBook(localID: staged.source.id, uploaded: uploaded)
      if let hash = staged.contentHash, let path = filePaths[uploaded.book_id] {
        try await repository.recordContentIdentity(contentHash: hash, bookID: uploaded.book_id,
          relativePath: path, scope: courseUploadScope)
        guard epoch == cloudState.epoch, capturedScope == noteScope else { return }
      if let targetCourseID, !canApplyCourseMutation(courseID: targetCourseID, scope: capturedScope) { return }
      }
      await persistTask?.value
      if let error = cloudState.cacheWriteError { throw ContentError.storage(error) }
    } catch is CancellationError {
      return
    } catch {
      guard epoch == cloudState.epoch, capturedScope == noteScope, !Task.isCancelled else { return }
      importError = (originalSaved ? "原文件已保存在本机：" : "") + error.localizedDescription
      handleCloudError(error)
    }
  }

  func processCloudImport(retryPreparation: Bool = false) async {
    await processImport(retryPreparation: retryPreparation)
  }

  func saveCloudCourse(name: String, resourceIDs: [String], diagnosis: [String: [String]])
    async throws
  {
    guard cloudState.privateLocalMode else { throw ContentError.invalid("本机课程尚未就绪，请重试连接") }
    if let id = draft.editingCourseID ?? draft.importCourseID, let index = courses.firstIndex(where: { $0.id == id }) {
      courses[index].name = name.isEmpty ? "我的课程" : name
      courses[index].resourceIDs = resourceIDs
      courses[index].diagnosis = diagnosis
      courses[index].updatedAt = .now
      selectedCourseID = id
    } else {
      let id = "local-course:" + UUID().uuidString
      courses.append(Course(id: id, name: name.isEmpty ? "我的课程" : name, resourceIDs: resourceIDs,
        courseResourceIDs: Dictionary(uniqueKeysWithValues: resourceIDs.map { ($0, id + ":" + $0) }), activeResourceID: resourceIDs.first, diagnosis: diagnosis))
      selectedCourseID = id
    }
    selectedBookID = resourceIDs.first
    try await persistPrivateState()
    return
  }

  func confirmCloudChapters() async {
    if cloudState.privateLocalMode {
      do {
        try await saveCloudCourse(name: draft.name, resourceIDs: draft.resourceIDs, diagnosis: draft.diagnosis)
        for bookID in draft.resourceIDs {
          if let chapters = chapterDrafts[bookID], let index = sources.firstIndex(where: { $0.id == bookID }) { sources[index].chapters = chapters }
          chapterDrafts.removeValue(forKey: bookID)
        }
        finishCourseImportSetup()
        try await persistPrivateState()
        draft = CourseDraft(); go(.study)
      } catch { handleCloudError(error) }
      return
    }
    guard cloudState.legacyPrivateSyncEnabled else { cloudState.error = "本机课程尚未就绪"; return }
    guard let cloud, !cloudState.isWorking else { return }
    cloudState.isWorking = true
    defer { cloudState.isWorking = false }
    do {
      if let id = draft.importCourseID, courseImport(for: id) != nil {
        let saved = try await flushCourseStageSave(id)
        guard let record = courseImport(for: id) else { throw CancellationError() }
        let fingerprint = stageSaveFingerprint(record)
        let epoch = cloudState.epoch
        let confirmation: CourseStageSaveResponse = try await cloud.request(
          "/api/workspaces/\(saved.id)/creation-draft/confirm", method: "POST",
          body: ["request_id": .string(fingerprint)])
        guard epoch == cloudState.epoch, confirmation.request_id == fingerprint,
          confirmation.workspace.id == saved.id else { throw CancellationError() }
        let workspace = confirmation.workspace
        if let scope = noteScope, id != workspace.id {
          let resources = Dictionary(workspace.resources.map { ($0.book_id, $0.id) }, uniquingKeysWith: { first, _ in first })
          let stored = try await repository.rebindNoteCourse(scope: scope, from: id, to: workspace.id, resources: resources)
          guard epoch == cloudState.epoch else { throw CancellationError() }
          applyCommitted(stored, scope: scope)
        }
        if let index = cloudState.workspaces.firstIndex(where: { $0.id == workspace.id }) { cloudState.workspaces[index] = workspace }
        else { cloudState.workspaces.append(workspace) }
        if let index = importState.courses.firstIndex(where: { $0.id == id }) { importState.courses[index].setupCompleted = true }
        for bookID in draft.resourceIDs { chapterDrafts.removeValue(forKey: bookID) }
        selectedCourseID = workspace.id
        selectedBookID = draft.resourceIDs.first
        rebuildCloudCourses()
        selectCourse(workspace.id)
      } else {
        try await saveCloudCourse(name: draft.name, resourceIDs: draft.resourceIDs, diagnosis: draft.diagnosis)
      }
      finishCourseImportSetup()
      cloudState.creationWorkspaceID = nil
      if let durable = persistCloud() { try await durable.value.get() }
      draft = CourseDraft()
      persistCloud()
    } catch {
      handleCloudError(error)
      return
    }
    cloudState.isWorking = false
    cloudState.showCompletionOnCompile = true
    go(.study)
    requestCloudLearning(chapterID: selectedChapterID ?? chapters.first?.id)
  }

  var isCloudCoursePreview: Bool { cloudResource?.isPreview == true }

  func flashcardFamiliarityLabel(_ card: Flashcard) -> String {
    if isCloudCoursePreview, cloudResource?.flashcard_reviews?[card.id] == nil,
      !isMastered(card.id) { return "尚未评估" }
    return isMastered(card.id) || card.mastery >= 80
      ? "较熟悉" : card.mastery >= 50 ? "巩固中" : "待加强"
  }

  /// Directory and empty-reader entries share the same real preparation path.
  func openChapterLearning(chapterID: String) {
    guard chapters.contains(where: { $0.id == chapterID }) else {
      showToast("本章目录尚未就绪，请先查看教材准备进度。", kind: .warning)
      return
    }
    if let lesson = lessons.first(where: {
      $0.chapter_id == chapterID && ["ready", "generated"].contains($0.status)
    }) {
      openLesson(lesson)
      return
    }
    selectedChapterID = chapterID
    selectedLessonID = nil
    requestCloudLearning(chapterID: chapterID)
  }

  func showExistingChapterPreparation() {
    let activeChapter = sources.flatMap(\.chapters).first { $0.id == cloudState.requestedChapterID }
    let message = activeChapter.map { "正在准备《\($0.title)》，请等待或先阅读已就绪的章节。" }
      ?? "正在处理学习任务，请等待当前任务完成；已就绪的章节仍可阅读。"
    cloudState.error = message
    if cloudState.diagnosisWorkspaceID == selectedCourseID,
      cloudState.interview != nil || cloudState.courseJob != nil {
      cloudState.showDiagnosis = true
      cloudState.choosingLearningMode = false
    }
    showToast(message, kind: .warning)
  }

  func requestCloudLearning(chapterID: String?) {
    guard let resource = cloudResource,
      let chapterID = chapterID ?? resource.structure?.chapters.first?.chapter_id else {
      showToast("本章资料尚未就绪，请先完成教材准备。", kind: .warning)
      return
    }
    // Reading an available chapter never waits for another generation.
    if let lesson = lessons.first(where: {
      $0.chapter_id == chapterID && ["ready", "generated"].contains($0.status)
    }) {
      openLesson(lesson)
      return
    }
    guard !cloudState.isWorking, cloudState.courseTask == nil else {
      showExistingChapterPreparation()
      return
    }
    if resource.session_id != nil {
      Task { await beginCloudLearning(chapterID: chapterID) }
      return
    }
    cloudState.requestedChapterID = chapterID
    cloudState.diagnosisWorkspaceID = resource.workspace_id
    cloudState.diagnosisResourceID = resource.id
    cloudState.interview = nil
    cloudState.courseJob = nil
    cloudState.error = nil
    cloudState.choosingLearningMode = true
    cloudState.showDiagnosis = true
  }

  var canRetryCloudDiagnosis: Bool {
    cloudState.diagnosisRetryAvailable && !cloudState.isWorking
      && cloudState.diagnosisRetryEpoch == cloudState.epoch
      && cloudState.interview == nil && cloudState.courseJob == nil
      && cloudResource?.session_id == nil && cloudResource?.id == cloudState.diagnosisResourceID
      && selectedCourseID == cloudState.diagnosisWorkspaceID
  }

  func restoreCloudDiagnosis() async {
    if cloudState.privateLocalMode {
      if let session = cloudState.localDiagnostics.values.first(where: { $0.courseID == selectedCourseID && $0.bookID == selectedBookID && $0.chapterID == cloudState.requestedChapterID }) {
        cloudState.interview = localInterview(session)
        if session.position >= session.items.count {
          do { try await generateLocalPersonalChapter(sessionID: session.id) } catch { handleCloudError(error) }
        }
      }
      return
    }
    guard cloudState.legacyPrivateSyncEnabled else { return }
    guard let cloud, let resource = cloudResource, !cloudState.isWorking,
      cloudState.courseJob == nil, resource.id == cloudState.diagnosisResourceID,
      resource.workspace_id == cloudState.diagnosisWorkspaceID else { return }
    let epoch = cloudState.epoch
    cloudState.isWorking = true
    defer { if epoch == cloudState.epoch { cloudState.isWorking = false } }
    do {
      // A POST/session without retry can still generate previously unstarted
      // diagnostic chapters. Recovery is strictly these two reads.
      let remote: CloudResource = try await cloud.request(resource.path)
      guard remote.id == resource.id, remote.book_id == resource.book_id,
        remote.workspace_id == resource.workspace_id else {
        throw CloudError(status: 409, message: "诊断资源归属不匹配，请重新打开当前教材。")
      }
      guard epoch == cloudState.epoch, selectedCourseID == resource.workspace_id,
        cloudResource?.id == resource.id, selectedBookID == resource.book_id else { return }
      // Keep the original uncertainty and explicit-retry eligibility while the
      // server has not attached a session. Never silently submit another job.
      guard let sessionID = remote.session_id,
        let encoded = sessionID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) else { return }
      let interview: CloudInterview = try await cloud.request("/api/interviews/\(encoded)")
      guard interview.session_id == sessionID else {
        throw CloudError(status: 409, message: "恢复的诊断会话不匹配，请重新查询当前教材。")
      }
      guard epoch == cloudState.epoch, selectedCourseID == resource.workspace_id,
        cloudResource?.id == resource.id, selectedBookID == resource.book_id else { return }
      if let wi = cloudState.workspaces.firstIndex(where: { $0.id == resource.workspace_id }),
        let ri = cloudState.workspaces[wi].resources.firstIndex(where: { $0.id == resource.id }) {
        cloudState.workspaces[wi].resources[ri].session_id = sessionID
        cloudState.workspaces[wi].resources[ri].session_purpose = interview.purpose
      }
      cloudState.interview = interview
      cloudState.error = nil
      cloudState.diagnosisRetryAvailable = false
      cloudState.diagnosisRetryEpoch = nil
      // Even phase=complete is only restored here. Creating a course remains
      // a separate, explicit learning action, never a side effect of lookup.
    } catch {
      guard epoch == cloudState.epoch, selectedCourseID == resource.workspace_id,
        cloudResource?.id == resource.id, selectedBookID == resource.book_id else { return }
      if let error = error as? CloudError, !error.transportFailure {
        cloudState.diagnosisRetryAvailable = false
        cloudState.diagnosisRetryEpoch = nil
      }
      handleCloudError(error)
    }
  }

  func beginCloudLearning(chapterID: String?, retryDiagnosis: Bool = false,
    preview: Bool = false) async {
    if cloudState.privateLocalMode {
      let scope = noteScope, courseID = selectedCourseID, epoch = cloudState.epoch
      do { try await beginLocalDiagnosis(chapterID: chapterID, preview: preview) }
      catch is CancellationError { return }
      catch {
        guard noteScope == scope, cloudState.epoch == epoch, selectedCourseID == courseID else { return }
        handleCloudError(error)
      }
      return
    }
    guard cloudState.legacyPrivateSyncEnabled else { cloudState.error = "本机学习尚未就绪，请重试连接"; return }
    guard let cloud, let resource = cloudResource else {
      showToast("请先连接学习服务并选择教材。", kind: .warning)
      return
    }
    guard !cloudState.isWorking, cloudState.courseTask == nil else {
      showExistingChapterPreparation()
      return
    }
    guard !retryDiagnosis || canRetryCloudDiagnosis else { return }
    guard !preview || (resource.session_id == nil && !retryDiagnosis) else { return }
    let epoch = cloudState.epoch
    // One explicit action owns this nonce. URLSession retransmits the same URL;
    // ordinary start/recovery never grants a retry or generates a nonce.
    let retryRequestID = retryDiagnosis ? UUID().uuidString : nil
    cloudState.isWorking = true
    defer { if epoch == cloudState.epoch { cloudState.isWorking = false } }
    cloudState.error = nil
    cloudState.courseJob = nil
    cloudState.interview = nil
    cloudState.diagnosisRetryAvailable = false
    cloudState.diagnosisRetryEpoch = nil
    cloudState.showDiagnosis = true
    cloudState.choosingLearningMode = false
    cloudState.requestedChapterID = chapterID ?? resource.structure?.chapters.first?.chapter_id
    cloudState.diagnosisWorkspaceID = resource.workspace_id
    cloudState.diagnosisResourceID = resource.id
    do {
      let interview = try await awaitCloudSession(resource: resource, preview: preview,
        retryRequestID: retryRequestID, epoch: epoch)
      guard epoch == cloudState.epoch, cloudState.showDiagnosis, selectedCourseID == resource.workspace_id,
        cloudResource?.id == resource.id, selectedBookID == resource.book_id else {
        return
      }
      cloudState.interview = interview
      if let wi = cloudState.workspaces.firstIndex(where: { $0.id == resource.workspace_id }),
        let ri = cloudState.workspaces[wi].resources.firstIndex(where: { $0.id == resource.id }) {
        cloudState.workspaces[wi].resources[ri].session_id = interview.session_id
        cloudState.workspaces[wi].resources[ri].session_purpose = interview.purpose
      }
      if interview.canGenerateCourse {
        try await compileCloudChapter()
      } else {
        cloudState.showDiagnosis = true
      }
    } catch is CancellationError { return
    } catch {
      guard epoch == cloudState.epoch, selectedCourseID == resource.workspace_id,
        cloudResource?.id == resource.id, selectedBookID == resource.book_id else { return }
      let failure = error as? CloudError
      let retryable = (failure?.status == 409 && failure?.code == "diagnostic_submission_uncertain")
        || (failure?.status == 502 && failure?.code == "diagnostic_generation_rejected")
        || (failure?.status == 503 && failure?.code == "diagnostic_generation_not_admitted")
      cloudState.diagnosisRetryAvailable = resource.session_id == nil && retryable
      cloudState.diagnosisRetryEpoch = cloudState.diagnosisRetryAvailable ? epoch : nil
      handleCloudError(error)
    }
  }

  /// Continue only after an explicit, known pending response. A lost response
  /// or unknown supplier outcome always leaves this loop for read-only recovery.
  private func awaitCloudSession(resource: CloudResource, preview: Bool,
    retryRequestID: String?, epoch: UUID) async throws -> CloudInterview {
    guard let cloud else { throw CancellationError() }
    var recoveryID = retryRequestID
    while true {
      try Task.checkCancellation()
      guard epoch == cloudState.epoch, cloudState.showDiagnosis,
        selectedCourseID == resource.workspace_id, cloudResource?.id == resource.id,
        cloudResource?.revision == resource.revision else { throw CancellationError() }
      let suffix = preview ? "" : "?resource_revision=\(resource.revision)"
        + (recoveryID.map { "&retry=true&retry_request_id=\($0)" } ?? "")
      do {
        return try await cloud.request(
          resource.path + (preview ? "/preview-session" : "/session") + suffix, method: "POST")
      } catch let error as CloudError where !error.transportFailure && error.status == 409
        && ["diagnostic_preparation_pending", "diagnostic_resource_preparation_pending"].contains(error.code ?? "") {
        recoveryID = nil
        cloudState.error = error.message
        let delay = error.retryAfter.flatMap { $0.isFinite ? min(120, max(5, $0)) : nil } ?? 5
        while true {
          let clock = ContinuousClock()
          let deadline = clock.now.advanced(by: .seconds(delay))
          repeat {
            // Closing the sheet stops observation promptly even during a long
            // backoff. It does not cancel or replay a submitted backend job.
            try await Task.sleep(for: min(.seconds(1), clock.now.duration(to: deadline)))
            try Task.checkCancellation()
            guard epoch == cloudState.epoch, cloudState.showDiagnosis,
              selectedCourseID == resource.workspace_id, cloudResource?.id == resource.id,
              cloudResource?.revision == resource.revision else { throw CancellationError() }
          } while clock.now < deadline
          if error.code == "diagnostic_resource_preparation_pending" { break }
          // The ordinary book is already queued: GET its existing task until
          // ready, rather than repeatedly submitting /session or /prepare.
          let job: CloudBookPreparationJob = try await cloud.request("/api/books/\(resource.book_id)/prepare")
          if job.status == .failed || job.status == .cancelled {
            throw CloudError(status: 502, message: job.message, code: "diagnostic_generation_rejected")
          }
          if job.status == .succeeded { break }
        }
        cloudState.error = nil
      }
    }
  }

  func advanceCloudInterview(selected: [String], confidence: Double, seconds: Double) async {
    if cloudState.privateLocalMode {
      do {
        if cloudState.interview?.canGenerateCourse == true, let id = cloudState.interview?.session_id { try await generateLocalPersonalChapter(sessionID: id) }
        else { try await answerLocalDiagnosis(selected: selected, confidence: confidence, seconds: seconds) }
      } catch { handleCloudError(error) }
      return
    }
    guard cloudState.legacyPrivateSyncEnabled else { return }
    guard let cloud, let interview = cloudState.interview, !cloudState.isWorking else { return }
    cloudState.isWorking = true
    defer { cloudState.isWorking = false }
    cloudState.error = nil
    do {
      let path = "/api/interviews/\(interview.session_id)"
      if interview.phase == "profile_confirmation" {
        cloudState.interview = try await cloud.request(
          path + "/confirm", method: "POST", body: ["confirmed": .bool(true)])
      } else if let item = interview.turn.item {
        let body: [String: JSONValue] = [
          "item_id": .string(item.item_id), "answer": .string(selected.joined(separator: ",")),
          "selected_option_ids": .array(selected.map(JSONValue.string)),
          "confidence": .number(confidence),
          "response_seconds": .number(max(0.1, seconds)), "hints_used": .number(0),
          "revisions": .number(0),
        ]
        let result: CloudDiagnosticResult = try await cloud.request(
          path + "/respond", method: "POST", body: body)
        cloudState.interview = CloudInterview(
          session_id: interview.session_id, phase: result.next_turn.phase,
          turn: result.next_turn, profile: result.profile, purpose: interview.purpose,
          design_fingerprint: result.design_fingerprint)
      } else if interview.phase == "adaptive_diagnosis" {
        cloudState.interview = try await cloud.request(path + "/next", method: "POST")
      } else if !interview.canGenerateCourse {
        cloudState.interview = try await cloud.request(
          path + "/profile", method: "POST",
          body: [
            "answer": .string(selected.joined(separator: ",")),
            "selected_option_ids": .array(selected.map(JSONValue.string)),
          ])
      }
      if cloudState.interview?.canGenerateCourse == true { try await compileCloudChapter() }
    } catch is CancellationError { return
    } catch {
      handleCloudError(error)
      // A timed-out answer may have committed. Read the authoritative pending turn before another submission.
      if let restored: CloudInterview = try? await cloud.request(
        "/api/interviews/\(interview.session_id)")
      {
        cloudState.interview = restored
      }
    }
  }

  func compileCloudChapter(retry: Bool = false) async throws {
    if cloudState.privateLocalMode {
      guard let id = cloudState.interview?.session_id else { throw ContentError.invalid("请恢复本机学习准备") }
      try await generateLocalPersonalChapter(sessionID: id)
      return
    }
    guard cloudState.legacyPrivateSyncEnabled else { throw ContentError.invalid("本机学习尚未就绪") }
    guard let cloud, let interview = cloudState.interview,
      interview.canGenerateCourse,
      let chapterID = cloudState.requestedChapterID, let accountKey = cloudState.cacheKey,
      let workspaceID = cloudState.diagnosisWorkspaceID,
      let resourceID = cloudState.diagnosisResourceID else {
      throw CloudError(status: 409, message: "请先恢复登录和本章的学习准备。")
    }
    let epoch = cloudState.epoch
    cloudState.showDiagnosis = true
    let previousPublicationID = cloudResource?.needsPreferenceUpdate(chapterID: chapterID) == true
      ? cloudResource?.courses.first(where: { $0.chapter_id == chapterID })?.course_id : nil
    let service = CourseJobService(api: cloud, repository: repository, accountKey: accountKey,
      designFingerprint: interview.design_fingerprint ?? cloudResource?.learning_design_fingerprint,
      previousPublicationID: previousPublicationID)
    let task = Task<CloudCourse, Error> { @MainActor in
      let update: @Sendable (CloudCourseJob) async throws -> Void = { [weak self] job in
        try await MainActor.run {
          guard let self, self.cloudState.epoch == epoch else {
            throw CancellationError()
          }
          self.cloudState.courseJob = job
        }
      }
      if retry {
        let job = try await service.restart(sessionID: interview.session_id, chapterID: chapterID)
        try await update(job)
      }
      return try await service.waitForCourse(sessionID: interview.session_id, chapterID: chapterID,
        onConnectionChange: { [weak self] message in
          try await MainActor.run {
            guard let self, self.cloudState.epoch == epoch else { throw CancellationError() }
            self.cloudState.error = message
          }
        },
        onUpdate: update)
    }
    cloudState.courseTask = task
    defer { if cloudState.epoch == epoch { cloudState.courseTask = nil } }
    let course = try await withTaskCancellationHandler {
      try await task.value
    } onCancel: { task.cancel() }
    guard cloudState.epoch == epoch else { throw CancellationError() }
    try await reloadCloud()
    guard cloudState.epoch == epoch else { throw CancellationError() }
    // Completion updates the course in the background without taking over another selection.
    guard selectedCourseID == workspaceID, cloudResource?.id == resourceID else {
      cloudState.showCompletionOnCompile = false
      persistCloud()
      return
    }
    guard let lesson = lessons.first(where: {
      $0.id == course.course_id && $0.chapter_id == chapterID && $0.status == "ready"
    }) else {
      throw CloudError(status: 409, message: "课程已生成，学习内容暂未同步完整；请重新查询原任务。")
    }
    cloudState.showDiagnosis = false
    if cloudState.showCompletionOnCompile || route != .lesson {
      selectedChapterID = lesson.chapter_id
      selectedLessonID = lesson.id
      saveLocation()
      cloudState.showCompletionOnCompile = false
    } else {
      openLesson(lesson)
    }
    persistCloud()
  }

  func pauseCloudCourseGeneration() {
    cloudState.showDiagnosis = false
    cloudState.choosingLearningMode = false
  }

  func restartCloudCourseGeneration() async {
    guard !cloudState.isWorking, cloudState.courseJob?.status == .failed,
      cloudState.courseJob?.retryable == true else { return }
    cloudState.isWorking = true
    cloudState.error = nil
    defer { cloudState.isWorking = false }
    do { try await compileCloudChapter(retry: true) }
    catch { handleCloudError(error) }
  }

  func removeCloudCourse(_ id: String) async {
    let epoch = cloudState.epoch
    let importID = courseImport(for: id)?.id
    let resourceIDs = Set((courses.first(where: { $0.id == id })?.resourceIDs ?? []).compactMap {
      courseResourceID(courseID: id, bookID: $0)
    })
    do {
      guard let cloud else { return }
      if let importID, courseImport(for: id)?.needsPreparation == true {
        await pauseCourseImport(importID)
        guard epoch == cloudState.epoch,
          importState.courses.first(where: { $0.id == importID })?.job.status == .cancelled else { return }
      }
      let _: CloudOK = try await cloud.request("/api/workspaces/\(id)", method: "DELETE")
      guard epoch == cloudState.epoch else { return }
      try await clearLocalCourseData(id, resourceIDs: resourceIDs)
      if let importID {
        importState.tasks[importID]?.cancel()
        importState.tasks[importID] = nil
        importState.courses.removeAll { $0.id == importID }
      }
      cloudState.pending = cloudState.pending.filter { $0.value.workspaceID != id }
      if cloudState.pendingSelection?.workspace_id == id { cloudState.pendingSelection = nil }
      cacheAcknowledgedCourseRemoval(id)
      await persistTask?.value
      try await reloadCloud()
      if let next = selectedCourseID {
        selectCourse(next)
      } else {
        selectedBookID = nil
        selectedChapterID = nil
        selectedLessonID = nil
      }
      persistCloud()
    } catch { handleCloudError(error) }
  }

  func addCloudResource(bookID: String, workspaceID: String?) async throws {
    guard let cloud, let wid = workspaceID ?? selectedCourseID else {
      throw ContentError.invalid("请先选择课程。")
    }
    let _: CloudResource = try await cloud.request(
      "/api/workspaces/\(wid)/resources", method: "POST", body: ["book_id": .string(bookID)])
    try await reloadCloud()
    persistCloud()
    await persistTask?.value
  }

  func removeCloudResource(bookID: String, workspaceID: String) async {
    do {
      guard let cloud,
        let resource = cloudState.workspaces.first(where: { $0.id == workspaceID })?.resources
          .first(where: { $0.book_id == bookID })
      else { return }
      let _: CloudOK = try await cloud.request(resource.path, method: "DELETE")
      cloudState.pending.removeValue(forKey: resource.id)
      if cloudState.pendingSelection?.resource_id == resource.id {
        cloudState.pendingSelection = nil
      }
      try await reloadCloud()
      selectCourse(workspaceID)
      persistCloud()
    } catch { handleCloudError(error) }
  }

  func askCloud(_ question: String) async {
    guard cloud != nil, !chatLoading,
      !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    else { return }
    let bookID = selectedBookID ?? ""
    let generation = selectionGeneration
    let epoch = cloudState.epoch
    chatLoading = true
    chatMessages.append(ChatMessage(role: "user", text: question))
    defer { chatLoading = false }
    do {
      let result = try await answerAssistant(
        question: question, bookID: bookID,
        bookIDs: currentCourse?.resourceIDs ?? [bookID], chapterID: selectedChapterID,
        sources: sources, history: chatMessages)
      guard epoch == cloudState.epoch, generation == selectionGeneration else { return }
      chatMessages.append(
        ChatMessage(role: "assistant", text: result.answer, citations: result.citations,
          sourceLabel: result.sourceLabel))
    } catch { handleCloudError(error) }
  }

  func answerAssistant(
    question: String, bookID: String, bookIDs: [String], chapterID: String?,
    sources: [SourceContent], history: [ChatMessage], courseID: String? = nil,
    bindToCurrentSelection: Bool = true, requiresOnline: Bool = false,
    readingContext: CloudReadingContext? = nil, requestID: String? = nil,
    requestContext: AssistantSessionContext? = nil
  ) async throws -> AssistantAnswer {
    let epoch = cloudState.epoch
    let routingSelection = selectionGeneration
    let processingRoute = await processingPolicy.route(for: .retrieval)
    try Task.checkCancellation()
    guard epoch == cloudState.epoch, (!bindToCurrentSelection || routingSelection == selectionGeneration),
      processingRoute != .cancelled else { throw CancellationError() }
    let localQuestion = readingContext?.localQuestion(question) ?? question
    if processingRoute == .onDevice {
      if requiresOnline {
        throw ContentError.invalid("附件已保存在本机。请联网后重试附件问答，本机检索目前仅支持教材原文。")
      }
      return try await answerFromOnDeviceTextbook(question: localQuestion, bookID: bookID,
        bookIDs: bookIDs, sources: sources)
    }
    guard let cloud else { throw ContentError.invalid("AI 问答需要连接学习服务；本机仍可阅读和检索教材。") }
    // All online questions share one assistant. Selection describes the screen;
    // the server validates access and the model chooses its read-only tools.
    let selectedCourseHint = bookID.isEmpty || currentCourse?.resourceIDs.contains(bookID) == true
      ? selectedCourseID : nil
    let contextCourseID = requestContext?.courseID ?? courseID ?? selectedCourseHint
    let course = courses.first { $0.id == contextCourseID }
    var seenBooks = Set<String>()
    let contextBookIDs = ([bookID] + bookIDs).filter { !$0.isEmpty && seenBooks.insert($0).inserted }
    var context: [String: JSONValue] = [
      "course_id": contextCourseID.map(JSONValue.string) ?? .null,
      "book_id": bookID.isEmpty ? .null : .string(bookID),
      "book_ids": .array(contextBookIDs.prefix(100).map(JSONValue.string)),
      "chapter_id": chapterID.map(JSONValue.string) ?? .null,
      "course_title": .string(String((requestContext?.courseTitle ?? course?.name ?? "").unicodeScalars.prefix(500))),
      "chapter_title": .string(String((requestContext?.chapterTitle ?? "").unicodeScalars.prefix(500))),
    ]
    if let readingContext { context["reading_context"] = readingContext.json }
    // Device excerpts are optional extras; a local search failure must not block the cloud answer.
    let deviceEvidence: [JSONValue]
    do {
      deviceEvidence = try await pendingLocalEvidence(question: question, bookID: bookID,
        bookIDs: contextBookIDs, sources: sources)
    } catch is CancellationError {
      throw CancellationError()
    } catch {
      deviceEvidence = []
    }
    var turns = history
    if turns.last?.role == "user", turns.last?.text == question { turns.removeLast() }
    // Preserve the full local archive while bounding only the request window.
    // Attachment excerpts are at the end, so recent evidence remains included.
    var historyBudget = 50_000
    var boundedHistory: [JSONValue] = []
    for turn in turns.reversed().filter({ ["user", "assistant"].contains($0.role) }).prefix(40) {
      guard historyBudget > 0 else { break }
      let text = String(String.UnicodeScalarView(turn.text.unicodeScalars.prefix(min(4000, historyBudget))))
      historyBudget -= text.unicodeScalars.count
      boundedHistory.append(.object(["role": .string(turn.role), "content": .string(text)]))
    }
    let currentMessageID = history.last.flatMap { $0.role == "user" && $0.text == question ? $0.id : nil }
    let id = requestID ?? currentMessageID
      ?? UUID().uuidString
    var proposed: [String: JSONValue] = [
      "request_id": .string(id),
      "question": .string(question),
      "context": .object(context),
      // Only pending, device-only course metadata is supplied by the client.
      // Server-backed courses and their progress are always loaded by tools.
      "local_courses": .array(courses.filter { $0.id.hasPrefix("local-course:") }.prefix(100).map { course in
        var metadata: [String: JSONValue] = ["id": .string(course.id),
          "name": .string(String(course.name.unicodeScalars.prefix(500))),
          "book_ids": .array(course.resourceIDs.prefix(100).map(JSONValue.string)), "status": .string("pending_local")]
        if let record = courseImport(for: course.id) { metadata["workspace_id"] = .string(workspaceID(for: record)) }
        return .object(metadata)
      }),
      "history": .array(Array(boundedHistory.reversed())),
    ]
    // Pending imports are not server resources. Only short excerpts the phone
    // itself retrieved are sent, labelled as device evidence, never as access.
    if !deviceEvidence.isEmpty { proposed["device_evidence"] = .array(deviceEvidence) }
    let body = try await assistantHistory.freezeQueryPayload(proposed, messageID: id)
    try Task.checkCancellation()
    guard epoch == cloudState.epoch, (!bindToCurrentSelection || routingSelection == selectionGeneration) else {
      throw CancellationError()
    }
    let result: CloudAssistantQuery = try await cloud.request("/api/assistant/query", method: "POST", body: body)
    try Task.checkCancellation()
    guard epoch == cloudState.epoch, (!bindToCurrentSelection || routingSelection == selectionGeneration) else {
      throw CancellationError()
    }
    var seen = Set<String>()
    let citations = result.citations.map { value in
      let sourceID = value.book_id
      let directory = sources.first { $0.id == sourceID }?.chapters ?? []
      let chapter = directory.filter {
        $0.page_start <= value.page_number && $0.page_end >= value.page_number
      }.min { ($0.page_end - $0.page_start) < ($1.page_end - $1.page_start) }
      return Citation(
        bookID: sourceID, chapterID: chapter?.id ?? "", chapterTitle: chapter?.title ?? "教材原文",
        page: value.page_number,
        chunkID: CloudMapping.citationID(
          page: value.page_number, quote: value.quote, blockID: value.block_id),
        quote: value.quote, score: 1)
    }.filter { seen.insert($0.id).inserted }
    return AssistantAnswer(answer: result.answer, citations: citations,
      sourceLabel: result.source_label, courseIDs: result.course_ids ?? [])
  }

  /// Reliable on-device hits from pending `book_local_` imports in the turn's scope.
  private func pendingLocalEvidence(question: String, bookID: String, bookIDs: [String],
    sources: [SourceContent]) async throws -> [JSONValue] {
    let pending = Set(([bookID] + bookIDs).filter { $0.hasPrefix("book_local_") })
    guard !pending.isEmpty, sources.contains(where: { pending.contains($0.id) }) else { return [] }
    let result = try await appleTextbookSearch.search(query: question, sources: sources,
      bookIDs: pending, limit: 6)
    return result.hits.filter { $0.reliable && $0.lexicalScore > 0 && pending.contains($0.chunk.book_id) }
      .prefix(6).map { hit in
      .object(["book_id": .string(hit.chunk.book_id), "page_number": .number(Double(max(1, hit.chunk.page_start))),
        "quote": .string(String(hit.chunk.text.unicodeScalars.prefix(600)))])
    }
  }

  func submitCloudAssignment(question: QuizQuestion, answer: String) async {
    do {
      try await submitCloudAssignments([
        CloudPracticeAnswer(
          question: question, answer: answer,
          eventID: UUID().uuidString, seconds: 1, confidence: 0.5)
      ])
    } catch { handleCloudError(error) }
  }

  func submitCloudAssignments(_ submissions: [CloudPracticeAnswer]) async throws {
    if cloudState.privateLocalMode {
      for submission in submissions { try await submitLocalPractice(question: submission.question, answer: submission.answer) }
      return
    }
    guard cloudState.legacyPrivateSyncEnabled else { throw ContentError.invalid("本机作答尚未就绪") }
    guard let cloud, let resource = cloudResource else {
      throw CloudError(status: 409, message: "请先生成当前章节课程")
    }
    let epoch = cloudState.epoch
    let planFocus = activeDailyStudyTask
    var feedback: [CloudPracticeFeedback.Evidence] = []
    for submission in submissions {
      let question = submission.question
      let letter = Array("ABCD").firstIndex(of: submission.answer.first ?? " ")
      let options = question.choices.isEmpty ? [] : letter.map { [String($0)] } ?? []
      var body: [String: JSONValue] = [
        "event_id": .string(submission.eventID), "answer": .string(submission.answer),
        "selected_option_ids": .array(options.map(JSONValue.string)),
        "confidence": .number(submission.confidence),
        "response_seconds": .number(min(3600, max(0.1, submission.seconds))),
        "hints_used": .number(0), "revisions": .number(0),
      ]
      let path: String
      if resource.source_content != nil {
        path = resource.path + "/catalog-practice/\(question.id)"
        body.removeValue(forKey: "selected_option_ids")
        body.removeValue(forKey: "hints_used")
        body.removeValue(forKey: "revisions")
      } else if let sid = resource.session_id {
        path = "/api/interviews/\(sid)/courses/\(question.lesson_id)/practice/\(question.id)"
      } else {
        throw CloudError(status: 409, message: "请先生成当前章节课程")
      }
      let result: CloudPracticeFeedback = try await cloud.request(path, method: "POST", body: body)
      guard epoch == cloudState.epoch else { throw CancellationError() }
      feedback.append(result.evidence)
      if result.evidence.score < 0.8 {
        let record = MistakeRecord(
          mistake_id: submission.eventID, user_id: cloudState.account?.user_id ?? "",
          book_id: question.book_id, assignment_id: question.id, question: question.prompt,
          answer: submission.answer,
          stuck_point: result.evidence.misconception_candidates.joined(separator: "；"),
          knowledge_points: [question.concept], citation_ids: [], course_id: resource.workspace_id)
        if !mistakes.contains(where: { $0.id == record.id }) { mistakes.append(record) }
      }
    }
    await finishDailyStudyTask(planFocus, kind: "practice", itemIDs: Set(submissions.map { $0.question.id }))
    guard epoch == cloudState.epoch else { throw CancellationError() }
    guard let first = submissions.first else { return }
    let correct = feedback.filter { $0.score >= 0.8 }.count
    latestDiagnosis = Diagnosis(
      assignment_id: first.question.id, submission_id: first.eventID,
      result: "已提交 \(feedback.count) 道练习，\(correct) 道达到本次掌握要求。",
      stuck_point: feedback.flatMap(\.misconception_candidates).joined(separator: "；"),
      knowledge_points: submissions.map { $0.question.concept }, review_citations: [],
      hint: feedback.flatMap(\.missing_rubric).joined(separator: "；"),
      needs_followup: feedback.contains { $0.needs_follow_up },
      mistake_recorded: correct < feedback.count)
    persistCloud()
    go(.diagnosis)
  }

  func reloadCloudLearningRecords() async {
    guard !cloudState.privateLocalMode else { return }
    guard let cloud, let resource = cloudResource else {
      return
    }
    let path: String
    if resource.source_content != nil {
      path = resource.path + "/catalog-records"
    } else if let sessionID = resource.session_id {
      path = "/api/interviews/\(sessionID)/learning-records"
    } else {
      return
    }
    let epoch = cloudState.epoch
    do {
      let records: CloudLearningRecords = try await cloud.request(
        path)
      guard epoch == cloudState.epoch, !Task.isCancelled else { return }
      let values = records.evidence.filter { $0.score < 0.8 }.map { item in
        MistakeRecord(
          mistake_id: "\(resource.id):\(item.id)", user_id: cloudState.account?.user_id ?? "",
          book_id: records.book_id, assignment_id: item.id, question: item.title,
          answer: item.answer ?? "",
          stuck_point: item.chapter, knowledge_points: [item.chapter], citation_ids: [],
          last_wrong_at: item.at, course_id: resource.workspace_id)
      }
      mistakes.removeAll { $0.book_id == resource.book_id && $0.course_id == resource.workspace_id }
      mistakes.append(contentsOf: values)
      saveCloudCacheOnly()
    } catch {
      if !Task.isCancelled { cloudState.syncError = error.localizedDescription }
    }
  }

  func reviewFlashcard(_ card: Flashcard, mastered: Bool, seconds: Double) {
    if cloudState.privateLocalMode {
      setMastery(card.id, mastered: mastered)
      if let i = sources.firstIndex(where: { $0.id == card.book_id }), let j = sources[i].flashcards.firstIndex(where: { $0.id == card.id }) {
        sources[i].flashcards[j].mastery = mastered ? min(100, sources[i].flashcards[j].mastery + 20) : max(0, sources[i].flashcards[j].mastery - 15)
      }
      persist(); return
    }
    guard cloudState.legacyPrivateSyncEnabled else { return }
    if cloud != nil {
      guard let resource = cloudResource else { return }
      let path: String
      if assistantState.learningObjects.contains(where: { $0.id == card.id && $0.course_id == resource.workspace_id && $0.kind == "flashcard" }) {
        path = "/api/assistant/learning-data/\(card.id)/review"
      } else if resource.source_content != nil {
        path = resource.path + "/catalog-flashcards/\(card.id)"
      } else if let session = resource.session_id {
        path = "/api/interviews/\(session)/courses/\(card.lesson_id)/flashcards/\(card.id)"
      } else {
        showToast("请先生成当前章节课程", kind: .warning)
        return
      }
      cloudState.pendingReviews.append(
        CloudPendingReview(
          workspaceID: resource.workspace_id, resourceID: resource.id, cardID: card.id,
          path: path, rating: mastered ? "good" : "again", seconds: min(3600, max(0.1, seconds)),
          planCompletion: activeDailyStudyTask.flatMap { focus in
            guard focus.task.kind == "recall", focus.task.card_id == card.id else { return nil }
            return CloudPlanCompletion(workspaceID: focus.workspaceID, day: focus.day, taskID: focus.task.id)
          }))
    }
    setMastery(card.id, mastered: mastered)
  }

  func restoreCloudMastery() {
    mastered = []
    for resource in cloudState.workspaces.flatMap(\.resources) {
      let value = cloudState.pending[resource.id]?.value ?? resource.progress
      applyCloudMastery(value, resource: resource)
    }
  }

  func applyCloudMastery(_ value: CloudProgress, resource: CloudResource) {
    let prefix = resource.id + "::"
    mastered = mastered.filter { !$0.hasPrefix(prefix) }
    let wirePrefix = resource.book_id + "::"
    mastered.formUnion(
      value.mastered_ids.map {
        prefix + ($0.hasPrefix(wirePrefix) ? String($0.dropFirst(wirePrefix.count)) : $0)
      })
  }

  @discardableResult
  func persistCloud() -> Task<Result<Void, Error>, Never>? {
    if cloudState.privateLocalMode {
      let epoch = cloudState.epoch, scope = noteScope
      let previous = persistTask
      let task = Task<Result<Void, Error>, Never> { @MainActor in
        await previous?.value
        guard epoch == self.cloudState.epoch, scope == self.noteScope else { return .failure(CancellationError()) }
        do {
          try await self.persistPrivateState()
          guard epoch == self.cloudState.epoch, scope == self.noteScope else { return .failure(CancellationError()) }
          self.cloudState.cacheWriteError = nil
          return .success(())
        } catch {
          if epoch == self.cloudState.epoch, scope == self.noteScope { self.cloudState.cacheWriteError = error.localizedDescription }
          return .failure(error)
        }
      }
      persistTask = Task { _ = await task.value }
      return task
    }
    guard cloudState.legacyPrivateSyncEnabled else { return nil }
    guard cloudState.authenticated, !cloudState.isHydrating, let key = cloudState.cacheKey else {
      return nil
    }
    if let resource = cloudResource {
      let selection = CloudBootstrap.Selection(
        workspace_id: resource.workspace_id, resource_id: resource.id)
      if selection != cloudState.confirmedSelection || cloudState.pendingSelection != nil {
        cloudState.pendingSelection = selection
      }
    }
    if let resource = cloudResource, resource.structure != nil {
      let location = locations[locationKey] ?? StudyLocation()
      let pending = cloudState.pending[resource.id]
      var value = pending?.value ?? resource.progress
      value.page = max(1, min(location.page, resource.structure?.source_page_count ?? 1))
      value.chapter_id = location.chapterID
      value.lesson_id = location.lessonID?.hasPrefix("pending:") == true ? nil : location.lessonID
      let completedTasks = currentSource?.plan.tasks.filter { ["done", "completed"].contains($0.status) } ?? []
      value.completed_chapters = Array(Set(value.completed_chapters + completedTasks.compactMap(\.chapter_id))).sorted()
      // An absent/unloaded daily plan cannot erase acknowledged learning or task records.
      for task in currentSource?.plan.tasks ?? [] { value.task_status[task.id] = task.status }
      let prefix = resource.id + "::"
      value.mastered_ids = mastered.filter { $0.hasPrefix(prefix) }.map {
        resource.book_id + "::" + $0.dropFirst(prefix.count)
      }.sorted()
      if value != (pending?.value ?? resource.progress) {
        cloudState.pending[resource.id] = CloudPendingProgress(
          workspaceID: resource.workspace_id, resourceID: resource.id, value: value,
          baseMasteredIDs: pending?.baseMasteredIDs ?? resource.progress.mastered_ids,
          masteryChanges: pending?.masteryChanges)
      }
    }
    let local = CloudLocalSnapshot(
      snapshot: snapshot(), sources: sources, workspaces: cloudState.workspaces,
      filePaths: filePaths, pending: cloudState.pending,
      creationWorkspaceID: cloudState.creationWorkspaceID, notes: notes,
      pendingSelection: cloudState.pendingSelection,
      onboardingComplete: cloudState.onboardingComplete, pendingReviews: cloudState.pendingReviews,
      requiresPersonalization: cloudState.requiresPersonalization,
      dailyPlans: dailyPlanState.plans, pendingPlanCompletions: dailyPlanState.pending)
    let previous = persistTask
    let epoch = cloudState.epoch
    let localVersions = Dictionary((local.snapshot.courseImports ?? []).map {
      ($0.id, stageSaveFingerprint($0))
    }, uniquingKeysWith: { _, last in last })
    let saved = Task<Result<Void, Error>, Never> { [repository] in
      await previous?.value
      do {
        try await repository.saveCloudState(local, key: key)
        if epoch == self.cloudState.epoch {
          self.cloudState.cacheWriteError = nil
          for (id, fingerprint) in localVersions { self.cloudState.localStageFingerprints[key + "::" + id] = fingerprint }
        }
        return .success(())
      } catch {
        if epoch == self.cloudState.epoch {
          self.cloudState.syncError = error.localizedDescription
          self.cloudState.cacheWriteError = error.localizedDescription
        }
        return .failure(error)
      }
    }
    persistTask = Task { _ = await saved.value }
    startCloudSync()
    scheduleCourseStageSaves()
    return saved
  }

  func startCloudSync() {
    guard cloudState.legacyPrivateSyncEnabled else { return }
    guard cloudState.authenticated, !cloudState.isHydrating, cloudState.syncTask == nil,
      !cloudState.pending.isEmpty || !cloudState.pendingReviews.isEmpty
        || !dailyPlanState.pending.isEmpty
        || cloudState.pendingSelection != nil,
      let cloud
    else { return }
    let epoch = cloudState.epoch
    cloudState.syncing = true
    cloudState.syncTask = Task { @MainActor in
      defer {
        cloudState.syncing = false
        cloudState.syncTask = nil
        if epoch == cloudState.epoch, !Task.isCancelled, cloudState.syncError == nil,
          cloudState.cacheWriteError == nil,
          !cloudState.pending.isEmpty || !cloudState.pendingReviews.isEmpty
            || !dailyPlanState.pending.isEmpty
            || cloudState.pendingSelection != nil
        {
          startCloudSync()
        }
      }
      await persistTask?.value
      guard cloudState.cacheWriteError == nil else { return }
      while let entry = cloudState.pending.values.sorted(by: { $0.resourceID < $1.resourceID })
        .first
      {
        await persistTask?.value
        guard epoch == cloudState.epoch, !Task.isCancelled, cloudState.cacheWriteError == nil else {
          return
        }
        do {
          let saved: CloudProgress = try await cloud.request(
            entry.path + "/progress", method: "PUT",
            body: entry.value.body(mutationID: entry.mutationID))
          guard epoch == cloudState.epoch else { return }
          if let wi = cloudState.workspaces.firstIndex(where: { $0.id == entry.workspaceID }),
            let ri = cloudState.workspaces[wi].resources.firstIndex(where: {
              $0.id == entry.resourceID
            })
          {
            cloudState.workspaces[wi].resources[ri].progress = saved
          }
          if cloudState.pending[entry.resourceID]?.mutationID == entry.mutationID {
            cloudState.pending.removeValue(forKey: entry.resourceID)
          } else if var pending = cloudState.pending[entry.resourceID] {
            // Only changes made after this in-flight request remain unacknowledged.
            pending.acknowledgeMastery(entry, saved: saved)
            cloudState.pending[entry.resourceID] = pending
          }
          restoreCloudMastery()
          cloudState.syncError = nil
          saveCloudCacheOnly()
        } catch let error as CloudError where error.status == 409 {
          do {
            let workspace: CloudWorkspace = try await cloud.request(
              "/api/workspaces/\(entry.workspaceID)")
            guard let server = workspace.resources.first(where: { $0.id == entry.resourceID })
            else {
              cloudState.syncError = "这份课程资料已在服务器移除，请重新打开课程。"
              return
            }
            guard epoch == cloudState.epoch else { return }
            if var pending = cloudState.pending[entry.resourceID] {
              if pending.baseMasteredIDs == nil {
                pending.baseMasteredIDs =
                  cloudState.workspaces.flatMap(\.resources)
                  .first { $0.id == entry.resourceID }?.progress.mastered_ids ?? []
              }
              pending.rebaseMastery(onto: server.progress)
              applyCloudMastery(pending.value, resource: server)
              pending.value.completed_chapters = Array(
                Set(pending.value.completed_chapters + server.progress.completed_chapters)
              ).sorted()
              for (task, status) in server.progress.task_status
              where ["done", "completed"].contains(status) {
                pending.value.task_status[task] = status
              }
              cloudState.pending[entry.resourceID] = pending
            }
            saveCloudCacheOnly()
          } catch {
            cloudState.syncError = error.localizedDescription
            return
          }
        } catch {
          cloudState.syncError = error.localizedDescription
          if let error = error as? CloudError, error.status == 401 { handleCloudError(error) }
          else if (error as? CloudError)?.transportFailure == true { scheduleCloudRetry() }
          saveCloudCacheOnly()
          return
        }
      }
      do { try await syncDailyStudyCompletions(epoch: epoch) }
      catch {
        guard epoch == cloudState.epoch else { return }
        cloudState.syncError = error.localizedDescription
        if (error as? CloudError)?.status == 401 { handleCloudError(error) }
        else if (error as? CloudError)?.transportFailure == true { scheduleCloudRetry() }
        saveCloudCacheOnly()
        return
      }
      while let review = cloudState.pendingReviews.first {
        await persistTask?.value
        guard epoch == cloudState.epoch, !Task.isCancelled, cloudState.cacheWriteError == nil else {
          return
        }
        do {
          let result: CloudReviewResult = try await cloud.request(
            review.path, method: "POST",
            body: [
              "event_id": .string(review.eventID), "rating": .string(review.rating),
              "response_seconds": .number(review.seconds),
            ])
          guard epoch == cloudState.epoch else { return }
          if let wi = cloudState.workspaces.firstIndex(where: { $0.id == review.workspaceID }),
            let ri = cloudState.workspaces[wi].resources.firstIndex(where: {
              $0.id == review.resourceID
            })
          {
            var states = cloudState.workspaces[wi].resources[ri].flashcard_reviews ?? [:]
            states[review.cardID] = result.review_state
            cloudState.workspaces[wi].resources[ri].flashcard_reviews = states
            if selectedCourseID == review.workspaceID {
              installCloudSources(workspaceID: review.workspaceID)
            }
          }
          if let completion = review.planCompletion,
            !dailyPlanState.pending.contains(where: { $0.workspaceID == completion.workspaceID && $0.taskID == completion.taskID }) {
            dailyPlanState.pending.append(completion)
          }
          cloudState.pendingReviews.removeAll { $0.eventID == review.eventID }
          if review.path.hasPrefix("/api/assistant/learning-data/") { await reloadAgentLearningObjects() }
          cloudState.syncError = nil
          saveCloudCacheOnly()
          // Persist the acknowledged event before sending another one. A lost response
          // still retries with the same ID and cannot advance FSRS twice.
          await persistTask?.value
        } catch {
          cloudState.syncError = error.localizedDescription
          if let error = error as? CloudError, error.status == 401 { handleCloudError(error) }
          else if (error as? CloudError)?.transportFailure == true { scheduleCloudRetry() }
          saveCloudCacheOnly()
          return
        }
      }
      while let selection = cloudState.pendingSelection {
        await persistTask?.value
        guard epoch == cloudState.epoch, !Task.isCancelled, cloudState.cacheWriteError == nil else {
          return
        }
        do {
          let _: CloudOK = try await cloud.request(
            "/api/native/selection", method: "PUT",
            body: [
              "workspace_id": .string(selection.workspace_id),
              "resource_id": selection.resource_id.map(JSONValue.string) ?? .null,
            ])
          guard epoch == cloudState.epoch else { return }
          cloudState.confirmedSelection = selection
          if cloudState.pendingSelection == selection { cloudState.pendingSelection = nil }
          cloudState.syncError = nil
          saveCloudCacheOnly()
        } catch {
          cloudState.syncError = error.localizedDescription
          if let error = error as? CloudError, error.status == 401 { handleCloudError(error) }
          else if (error as? CloudError)?.transportFailure == true { scheduleCloudRetry() }
          saveCloudCacheOnly()
          return
        }
      }
    }
  }

  func saveCloudCacheOnly() {
    if cloudState.privateLocalMode { _ = persistCloud(); return }
    guard let key = cloudState.cacheKey else { return }
    let local = CloudLocalSnapshot(
      snapshot: snapshot(), sources: sources, workspaces: cloudState.workspaces,
      filePaths: filePaths, pending: cloudState.pending,
      creationWorkspaceID: cloudState.creationWorkspaceID, notes: notes,
      pendingSelection: cloudState.pendingSelection,
      onboardingComplete: cloudState.onboardingComplete, pendingReviews: cloudState.pendingReviews,
      requiresPersonalization: cloudState.requiresPersonalization,
      dailyPlans: dailyPlanState.plans, pendingPlanCompletions: dailyPlanState.pending)
    let previous = persistTask
    let epoch = cloudState.epoch
    persistTask = Task { [repository] in
      await previous?.value
      do {
        try await repository.saveCloudState(local, key: key)
        if epoch == self.cloudState.epoch { self.cloudState.cacheWriteError = nil }
      } catch {
        if epoch == self.cloudState.epoch {
          self.cloudState.syncError = error.localizedDescription
          self.cloudState.cacheWriteError = error.localizedDescription
        }
      }
    }
  }
}


// Stage saves never select a course or replace the questionnaire currently on screen.
@MainActor extension AppStore {
  func stageSaveFingerprint(_ record: CourseImportRecord) -> String {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    struct Payload: Encodable { var draft: CourseDraft; var chapters: [String: [Chapter]] }
    let data = (try? encoder.encode(Payload(draft: record.draft, chapters: record.stageSave?.chapters ?? [:]))) ?? Data()
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
  }

  func scheduleCourseStageSaves(force: Bool = false) {
    guard cloudState.legacyPrivateSyncEnabled else { return }
    guard cloud != nil, cloudState.authenticated, !cloudState.isHydrating,
      let scope = cloudState.cacheKey else { return }
    for record in importState.courses where record.setupCompleted != true && !record.draft.resourceIDs.isEmpty
      && !record.draft.resourceIDs.contains(where: { $0.hasPrefix("book_local_") }) {
      let fingerprint = stageSaveFingerprint(record)
      if record.stageSave?.fingerprint == fingerprint { continue }
      if !force, record.stageSave?.error != nil, record.stageSave?.attemptedFingerprint == fingerprint { continue }
      let key = scope + "::" + record.id
      guard cloudState.stageSaveTasks[key] == nil else { continue }
      let epoch = cloudState.epoch
      cloudState.stageSaveTasks[key] = Task { @MainActor in
        defer {
          self.cloudState.stageSaveTasks[key] = nil
          if epoch == self.cloudState.epoch, scope == self.cloudState.cacheKey,
            let latest = self.courseImport(for: record.id), latest.setupCompleted != true,
            latest.stageSave?.attemptedFingerprint != self.stageSaveFingerprint(latest) {
            self.scheduleCourseStageSaves()
          }
        }
        do {
          try await Task.sleep(for: .milliseconds(350))
          while !Task.isCancelled && epoch == self.cloudState.epoch && scope == self.cloudState.cacheKey {
            guard let cloud = self.cloud,
              let index = self.importState.courses.firstIndex(where: { $0.id == record.id }),
              self.importState.courses[index].setupCompleted != true else { return }
            let snapshot = self.importState.courses[index]
            let fingerprint = self.stageSaveFingerprint(snapshot)
            if snapshot.stageSave?.fingerprint == fingerprint { return }
            if self.importState.courses[index].stageSave == nil { self.importState.courses[index].stageSave = CourseStageSave() }
            self.importState.courses[index].stageSave?.attemptedFingerprint = fingerprint
            self.importState.courses[index].stageSave?.error = nil
            guard let durable = self.persistCloud(), case .success = await durable.value else {
              throw CloudError(status: 0, message: "课程草稿尚未保存到手机，请检查存储空间后重试。")
            }
            try Task.checkCancellation()
            guard epoch == self.cloudState.epoch, scope == self.cloudState.cacheKey else { return }
            let wid = self.workspaceID(for: snapshot)
            let edits = snapshot.stageSave?.chapters ?? [:]
            let chapters = edits.mapValues { values in JSONValue.array(values.map { chapter in
              .object(["chapter_id": .string(chapter.id), "title": .string(chapter.title),
                "start_page": .number(Double(chapter.page_start)), "end_page": .number(Double(chapter.page_end))])
            }) }
            let response: CourseStageSaveResponse = try await cloud.request(
              "/api/workspaces/\(wid)/creation-draft", method: "PUT", body: [
                "id": .string(wid), "request_id": .string(fingerprint),
                "name": .string(snapshot.draft.name.isEmpty ? "我的课程" : snapshot.draft.name),
                "preferences": .object(snapshot.draft.diagnosis.mapValues { .array($0.map(JSONValue.string)) }),
                "step": .number(Double(snapshot.draft.step)),
                "book_ids": .array(snapshot.draft.resourceIDs.map(JSONValue.string)),
                "chapters": .object(chapters),
              ])
            guard response.request_id == fingerprint, response.workspace.id == wid else {
              throw CloudError(status: 409, message: "课程草稿回执不匹配，请重新同步。")
            }
            guard epoch == self.cloudState.epoch, scope == self.cloudState.cacheKey,
              let savedIndex = self.importState.courses.firstIndex(where: { $0.id == snapshot.id }) else { return }
            self.importState.courses[savedIndex].stageSave?.serverDraftFingerprint = fingerprint
            self.persistCloud()
            var workspace = response.workspace
            // Directory draft storage above is fast. Range-dependent analysis runs
            // separately, in this background task, only for genuinely changed outlines.
            for ri in workspace.resources.indices {
              let resource = workspace.resources[ri]
              guard let values = edits[resource.book_id] else { continue }
              let existing = CloudMapping.source(resource).chapters
              let same = values.count == existing.count && zip(values, existing).allSatisfy {
                $0.id == $1.id && $0.title == $1.title && $0.page_start == $1.page_start && $0.page_end == $1.page_end
              }
              if same { continue }
              guard epoch == self.cloudState.epoch, scope == self.cloudState.cacheKey else { return }
              let updated: CloudResource = try await cloud.request(resource.path + "/chapters", method: "PUT",
                body: ["revision": .number(Double(resource.revision)), "chapters": chapters[resource.book_id] ?? .array([])])
              workspace.resources[ri] = updated
            }
            guard epoch == self.cloudState.epoch, scope == self.cloudState.cacheKey,
              let current = self.importState.courses.firstIndex(where: { $0.id == snapshot.id }) else { return }
            self.importState.courses[current].stageSave?.workspace = workspace
            self.importState.courses[current].stageSave?.fingerprint = fingerprint
            self.importState.courses[current].stageSave?.error = nil
            guard let durable = self.persistCloud(), case .success = await durable.value else {
              throw CloudError(status: 0, message: "同步回执尚未保存在手机，请重试。")
            }
            if self.stageSaveFingerprint(self.importState.courses[current]) == fingerprint { return }
            try await Task.sleep(for: .milliseconds(350))
          }
        } catch {
          guard epoch == self.cloudState.epoch, scope == self.cloudState.cacheKey,
            let current = self.importState.courses.firstIndex(where: { $0.id == record.id }) else { return }
          if self.importState.courses[current].stageSave == nil { self.importState.courses[current].stageSave = CourseStageSave() }
          self.importState.courses[current].stageSave?.error = error.localizedDescription
          self.persistCloud()
          if let failure = error as? CloudError, failure.transportFailure { self.scheduleCloudRetry() }
        }
      }
    }
  }

  func flushCourseStageSave(_ id: String) async throws -> CloudWorkspace {
    guard let scope = cloudState.cacheKey else { throw CloudError(status: 401, message: "请先登录。") }
    let epoch = cloudState.epoch
    captureCourseImportDraft()
    guard let durable = persistCloud(), case .success = await durable.value else {
      throw CloudError(status: 0, message: "课程草稿尚未保存在手机。")
    }
    let key = scope + "::" + id
    if let active = cloudState.stageSaveTasks[key] { await active.value }
    guard epoch == cloudState.epoch, scope == cloudState.cacheKey else { throw CancellationError() }
    // One explicit retry is allowed here; writes use the same workspace and payload key.
    if let record = courseImport(for: id), record.stageSave?.fingerprint != stageSaveFingerprint(record) {
      scheduleCourseStageSaves(force: true)
      await cloudState.stageSaveTasks[key]?.value
    }
    guard epoch == cloudState.epoch, scope == cloudState.cacheKey else { throw CancellationError() }
    guard let record = courseImport(for: id), let receipt = record.stageSave,
      receipt.error == nil, receipt.fingerprint == stageSaveFingerprint(record), let workspace = receipt.workspace else {
      throw CloudError(status: 503, message: courseImport(for: id)?.stageSave?.error ?? "课程修改尚未同步，请联网后重试。")
    }
    return workspace
  }

  var courseDraftSyncMessage: String? {
    guard cloud != nil, let id = draft.importCourseID, let record = courseImport(for: id) else { return nil }
    if cloudState.cacheWriteError != nil { return "保存到手机失败，请检查存储空间。" }
    if cloudState.privateLocalMode { return "课程草稿保存在本机" }
    let fingerprint = stageSaveFingerprint(record)
    let key = (cloudState.cacheKey ?? "") + "::" + id
    if cloudState.localStageFingerprints[key] != fingerprint { return "正在保存到手机…" }
    if record.draft.resourceIDs.contains(where: { $0.hasPrefix("book_local_") }) {
      return "已保存到手机，资料上传后同步。"
    }
    if record.stageSave?.error != nil {
      return record.stageSave?.serverDraftFingerprint == fingerprint
        ? "草稿已同步，目录核验未完成，可重试。" : "已保存到手机，云端尚未同步；联网后可重试。"
    }
    if record.stageSave?.fingerprint == fingerprint { return "已同步" }
    if record.stageSave?.serverDraftFingerprint == fingerprint { return "草稿已同步，正在核验目录…" }
    return "已保存到手机，正在同步…"
  }
}
