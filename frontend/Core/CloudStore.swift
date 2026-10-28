import CryptoKit
import Foundation

extension AppStore {
  var cloudResource: CloudResource? {
    cloudState.workspaces.first { $0.id == selectedCourseID }?.resources.first {
      $0.book_id == selectedBookID
    }
  }

  func handleCloudError(_ error: Error) {
    guard !(error is CancellationError) else { return }
    cloudState.error = error.localizedDescription
    showToast(error.localizedDescription)
    if let error = error as? CloudError, error.status == 401 {
      cloudState.isWorking = false
      cloudState.diagnosisRetryAvailable = false
      cloudState.diagnosisRetryEpoch = nil
      cloudState.authenticated = false
      cloudState.showDiagnosis = false
      cloudState.epoch = UUID()
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
      chatMessages = []
      Task { await cloud?.clearCredentials() }
    }
  }

  func rebuildCloudCourses() {
    let localCourses = courses.filter { $0.id.hasPrefix("local-course:") }
    courses = cloudState.workspaces.map { workspace in
      let existing = courses.first { $0.id == workspace.id }
      let candidates = [
        existing?.activeResourceID, workspace.id == selectedCourseID ? selectedBookID : nil,
      ].compactMap { $0 }
      let active =
        candidates.first { id in workspace.resources.contains { $0.book_id == id } }
        ?? workspace.resources.first?.book_id
      return Course(
        id: workspace.id, name: workspace.name, resourceIDs: workspace.resources.map(\.book_id),
        activeResourceID: active, diagnosis: workspace.preferences,
        createdAt: Date(timeIntervalSince1970: workspace.created),
        updatedAt: Date(timeIntervalSince1970: workspace.updated))
    }
    courses += localCourses.filter { local in
      !courses.contains { remote in Set(local.resourceIDs).isSubset(of: Set(remote.resourceIDs)) }
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
    try await reloadCloud()
  }

  /// Manual sign-in starts a fresh questionnaire, including accounts signed out
  /// before this client version. Only the scoped onboarding state is reset.
  func prepareCloudLogin(_ account: CloudAccountState) async throws {
    guard cloud != nil else { return }
    await persistTask?.value
    let key = cloudCacheKey(account)
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
    guidanceStyles: [String] = [], planningSupport: String? = nil
  ) async throws {
    guard let cloud else { return }
    let value: LearnerPreferences = try await cloud.request(
      "/api/native/learner-preferences", method: "PUT",
      body: [
        "displayName": .string(name), "primaryGoal": goal.map(JSONValue.string) ?? .null,
        "dailyTime": dailyTime.map(JSONValue.string) ?? .null,
        "learningBarriers": .array(learningBarriers.map(JSONValue.string)),
        "guidanceStyles": .array(guidanceStyles.map(JSONValue.string)),
        "planningSupport": planningSupport.map(JSONValue.string) ?? .null,
        "onboardingVersion": .number(Double(LearnerPreferences.currentOnboardingVersion)),
      ])
    preferences = value
    cloudState.requiresPersonalization = false
    cloudState.onboardingComplete = value.hasCompletedOnboarding
    persistCloud()
    await persistTask?.value
  }

  func importCloudCommunity(_ book: CommunityBook) async throws {
    guard let cloud else { return }
    let workspaceID =
      cloudState.creationWorkspaceID ?? "workspace_"
      + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    cloudState.creationWorkspaceID = workspaceID
    persistCloud()
    await persistTask?.value
    let _: CloudWorkspace = try await cloud.request(
      "/api/native/catalog/\(book.id)/import", method: "POST",
      body: [
        "id": .string(workspaceID), "name": .string(draft.name.isEmpty ? book.title : draft.name),
        "preferences": .object(draft.diagnosis.mapValues { .array($0.map(JSONValue.string)) }),
      ])
    try await reloadCloud()
    selectCourse(workspaceID)
    draft = CourseDraft()
    cloudState.creationWorkspaceID = nil
    persistCloud()
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
    filePaths[bookID] = try await repository.saveAttachment(bytes, extension: "pdf")
    persistCloud()
  }

  /// The original is durable and visible locally before any server request starts.
  func retainLocalImport(_ staged: StagedSource, targetCourseID: String? = nil) async throws {
    if !sources.contains(where: { $0.id == staged.source.id }) { sources.append(staged.source) }
    filePaths[staged.source.id] = staged.relativePath
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
  }

  func bindUploadedBook(localID: String, uploaded: CloudUpload) {
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
    for (key, value) in locations where key.hasSuffix("::" + localID) {
      locations[String(key.dropLast(localID.count)) + uploaded.book_id] = value
      locations.removeValue(forKey: key)
    }
    if sourceTarget?.bookID == localID { sourceTarget?.bookID = uploaded.book_id }

    if let path = filePaths[localID] { filePaths[uploaded.book_id] = path }
    draft.resourceIDs = Array(NSOrderedSet(array: draft.resourceIDs.map { $0 == localID ? uploaded.book_id : $0 })) as? [String] ?? draft.resourceIDs
    for i in courses.indices {
      courses[i].resourceIDs = courses[i].resourceIDs.map { $0 == localID ? uploaded.book_id : $0 }
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
    let routingSelection = selectionGeneration
    importError = nil
    var originalSaved = false
    do {
      guard url.pathExtension.lowercased() == "pdf" else {
        throw CloudError(status: 415, message: "云端课程目前支持 PDF，请选择 PDF 文件。")
      }
      let processingRoute = await processingPolicy.route(for: .ocr)
      try Task.checkCancellation()
      guard epoch == cloudState.epoch, routingSelection == selectionGeneration else { return }
      guard processingRoute != .cancelled else {
        importError = "已取消本次导入，原文件未上传。"
        return
      }
      let staged = try await LocalFileImporter.stage(url: url, repository: repository)
      try Task.checkCancellation()
      guard epoch == cloudState.epoch else { return }
      try await retainLocalImport(staged, targetCourseID: targetCourseID)
      originalSaved = true
      try Task.checkCancellation()
      guard epoch == cloudState.epoch else { return }
      if processingRoute == .onDevice {
        showToast("教材已保存在本机，将使用本机文字识别。")
        return
      }
      let uploaded = try await cloud.uploadPDF(
        repository.attachmentURL(staged.relativePath), filename: url.lastPathComponent,
        onProgress: onProgress)
      try Task.checkCancellation()
      guard epoch == cloudState.epoch else { return }
      bindUploadedBook(localID: staged.source.id, uploaded: uploaded)
      await persistTask?.value
      if let error = cloudState.cacheWriteError { throw ContentError.storage(error) }
    } catch is CancellationError {
      return
    } catch {
      guard epoch == cloudState.epoch, !Task.isCancelled else { return }
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
    guard let cloud else { return }
    let importWorkspaceID = draft.importCourseID.map { id in
      "workspace_" + SHA256.hash(data: Data(id.utf8)).map { String(format: "%02x", $0) }.joined().prefix(32)
    }
    let workspaceID =
      draft.editingCourseID.flatMap { $0.hasPrefix("local-course:") ? nil : $0 } ?? importWorkspaceID ?? cloudState.creationWorkspaceID
      ?? "workspace_" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    // Persist the id before the first write so a timed-out creation can be recovered idempotently.
    if draft.editingCourseID == nil || draft.editingCourseID?.hasPrefix("local-course:") == true { cloudState.creationWorkspaceID = workspaceID }
    persistCloud()
    await persistTask?.value
    let workspace: CloudWorkspace = try await cloud.request(
      "/api/workspaces", method: "POST",
      body: [
        "id": .string(workspaceID), "name": .string(name.isEmpty ? "我的课程" : name),
        "preferences": .object(diagnosis.mapValues { .array($0.map(JSONValue.string)) }),
      ])
    var retained = Set(resourceIDs)
    for bookID in resourceIDs where !workspace.resources.contains(where: { $0.book_id == bookID }) {
      let _: CloudResource = try await cloud.request(
        "/api/workspaces/\(workspaceID)/resources", method: "POST",
        body: ["book_id": .string(bookID)])
      retained.insert(bookID)
    }
    for resource in workspace.resources where !retained.contains(resource.book_id) {
      let _: CloudOK = try await cloud.request(resource.path, method: "DELETE")
    }
    selectedCourseID = workspaceID
    selectedBookID = resourceIDs.first
    try await reloadCloud()
    selectCourse(workspaceID)
  }

  func confirmCloudChapters() async {
    guard let cloud, !cloudState.isWorking else { return }
    cloudState.isWorking = true
    defer { cloudState.isWorking = false }
    do {
      let edited = chapterDrafts
      try await saveCloudCourse(
        name: draft.name, resourceIDs: draft.resourceIDs, diagnosis: draft.diagnosis)
      guard let workspace = cloudState.workspaces.first(where: { $0.id == selectedCourseID }) else {
        return
      }
      for resource in workspace.resources {
        let values = edited[resource.book_id] ?? CloudMapping.source(resource).chapters
        let body: [String: JSONValue] = [
          "revision": .number(Double(resource.revision)),
          "chapters": .array(
            values.map {
              .object([
                "chapter_id": .string($0.id), "title": .string($0.title),
                "start_page": .number(Double($0.page_start)),
                "end_page": .number(Double($0.page_end)),
              ])
            }),
        ]
        let updated: CloudResource = try await cloud.request(
          resource.path + "/chapters", method: "PUT", body: body)
        if updated.revision != resource.revision {
          cloudState.pending.removeValue(forKey: resource.id)
        }
        chapterDrafts.removeValue(forKey: resource.book_id)
      }
      try await reloadCloud()
      finishCourseImportSetup()
      draft = CourseDraft()
      cloudState.creationWorkspaceID = nil
      persistCloud()
    } catch {
      handleCloudError(error)
      return
    }
    cloudState.isWorking = false
    cloudState.showCompletionOnCompile = true
    requestCloudLearning(chapterID: selectedChapterID ?? chapters.first?.id)
  }

  var isCloudCoursePreview: Bool { cloudResource?.isPreview == true }

  func flashcardFamiliarityLabel(_ card: Flashcard) -> String {
    if isCloudCoursePreview, cloudResource?.flashcard_reviews?[card.id] == nil,
      !isMastered(card.id) { return "尚未评估" }
    return isMastered(card.id) || card.mastery >= 80
      ? "较熟悉" : card.mastery >= 50 ? "巩固中" : "待加强"
  }

  func requestCloudLearning(chapterID: String?) {
    guard let resource = cloudResource, !cloudState.isWorking else { return }
    if resource.session_id != nil {
      Task { await beginCloudLearning(chapterID: chapterID) }
      return
    }
    cloudState.requestedChapterID = chapterID ?? resource.structure?.chapters.first?.chapter_id
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
    guard let cloud, let resource = cloudResource, !cloudState.isWorking else { return }
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
      let suffix = retryRequestID.map { "?retry=true&retry_request_id=\($0)" } ?? ""
      let interview: CloudInterview = try await cloud.request(
        resource.path + (preview ? "/preview-session" : "/session") + suffix, method: "POST")
      guard epoch == cloudState.epoch, selectedCourseID == resource.workspace_id,
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

  func advanceCloudInterview(selected: [String], confidence: Double, seconds: Double) async {
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
          turn: result.next_turn, profile: result.profile, purpose: interview.purpose)
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
    guard let cloud, let interview = cloudState.interview,
      interview.canGenerateCourse,
      let chapterID = cloudState.requestedChapterID, let accountKey = cloudState.cacheKey,
      let workspaceID = cloudState.diagnosisWorkspaceID,
      let resourceID = cloudState.diagnosisResourceID else {
      throw CloudError(status: 409, message: "请先恢复登录和本章的学习准备。")
    }
    let epoch = cloudState.epoch
    cloudState.showDiagnosis = true
    let service = CourseJobService(api: cloud, repository: repository, accountKey: accountKey)
    let task = Task<CloudCourse, Error> { @MainActor in
      let update: @Sendable (CloudCourseJob) async throws -> Void = { [weak self] job in
        try await MainActor.run {
          guard let self, self.cloudState.epoch == epoch,
            self.selectedCourseID == workspaceID, self.cloudResource?.id == resourceID else {
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
        onUpdate: update)
    }
    cloudState.courseTask = task
    defer { if cloudState.epoch == epoch { cloudState.courseTask = nil } }
    let course = try await withTaskCancellationHandler {
      try await task.value
    } onCancel: { task.cancel() }
    guard cloudState.epoch == epoch, selectedCourseID == workspaceID,
      cloudResource?.id == resourceID else { throw CancellationError() }
    try await reloadCloud()
    guard cloudState.epoch == epoch, selectedCourseID == workspaceID,
      cloudResource?.id == resourceID else { throw CancellationError() }
    guard let lesson = lessons.first(where: {
      $0.id == course.course_id && $0.chapter_id == chapterID && $0.status == "ready"
    }) else {
      throw CloudError(status: 409, message: "课程已生成，学习内容暂未同步完整；请重新查询原任务。")
    }
    cloudState.showDiagnosis = false
    if cloudState.showCompletionOnCompile {
      selectedChapterID = lesson.chapter_id
      selectedLessonID = lesson.id
      saveLocation()
      cloudState.showCompletionOnCompile = false
      go(.courseReady)
    } else {
      openLesson(lesson)
    }
    persistCloud()
  }

  func pauseCloudCourseGeneration() {
    cloudState.courseTask?.cancel()
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
    do {
      guard let cloud else { return }
      if let importID, courseImport(for: id)?.needsPreparation == true {
        await pauseCourseImport(importID)
        guard epoch == cloudState.epoch,
          importState.courses.first(where: { $0.id == importID })?.job.status == .cancelled else { return }
      }
      let _: CloudOK = try await cloud.request("/api/workspaces/\(id)", method: "DELETE")
      guard epoch == cloudState.epoch else { return }
      if let importID {
        importState.tasks[importID]?.cancel()
        importState.tasks[importID] = nil
        importState.courses.removeAll { $0.id == importID }
      }
      cloudState.pending = cloudState.pending.filter { $0.value.workspaceID != id }
      if cloudState.pendingSelection?.workspace_id == id { cloudState.pendingSelection = nil }
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
    guard cloud != nil, let bookID = selectedBookID, !chatLoading,
      !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    else { return }
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
        ChatMessage(role: "assistant", text: result.answer, citations: result.citations))
    } catch { handleCloudError(error) }
  }

  func answerAssistant(
    question: String, bookID: String, bookIDs: [String], chapterID: String?,
    sources: [SourceContent], history: [ChatMessage]
  ) async throws -> AssistantAnswer {
    let epoch = cloudState.epoch
    let routingSelection = selectionGeneration
    let processingRoute = await processingPolicy.route(for: .retrieval)
    try Task.checkCancellation()
    guard epoch == cloudState.epoch, routingSelection == selectionGeneration,
      processingRoute != .cancelled else { throw CancellationError() }
    if processingRoute == .onDevice {
      return try await answerFromOnDeviceTextbook(question: question, bookID: bookID,
        bookIDs: bookIDs, sources: sources)
    }
    guard let cloud, !bookID.hasPrefix("book_local_") else {
      return try await assistant.ask(
        question: question, bookID: bookID, bookIDs: bookIDs,
        chapterID: chapterID, sources: sources, history: history)
    }
    let resource = cloudState.workspaces.first(where: { $0.id == selectedCourseID })?
      .resources.first(where: { $0.book_id == bookID })
    guard !bookID.isEmpty, resource != nil || bookIDs.allSatisfy({ $0 == bookID }) else {
      throw CloudError(status: 409, message: "请先打开当前课程资料再提问")
    }
    var turns = history
    if turns.last?.role == "user", turns.last?.text == question { turns.removeLast() }
    let body: [String: JSONValue] = [
      "question": .string(question),
      "book_ids": .array(bookIDs.map(JSONValue.string)),
      "chapter_id": chapterID.map(JSONValue.string) ?? .null,
      "history": .array(
        turns.filter { ["user", "assistant"].contains($0.role) }
          .suffix(8).map {
            .object([
              "role": .string($0.role), "text": .string(String($0.text.prefix(4000))),
            ])
          }),
    ]
    // Keep single-book questions available for library books not yet in a course.
    let path = resource.map { $0.path + "/qa" } ?? "/api/books/\(bookID)/qa"
    let result: CloudQA = try await cloud.request(path, method: "POST", body: body)
    var seen = Set<String>()
    let citations = result.claims.flatMap { claim in
      let sourceID = claim.book_id ?? result.book_id
      let directory = sources.first { $0.id == sourceID }?.chapters ?? []
      return claim.citations.map { value in
        let chapter = directory.filter {
          $0.page_start <= value.page_number && $0.page_end >= value.page_number
        }.min { ($0.page_end - $0.page_start) < ($1.page_end - $1.page_start) }
        return Citation(
          bookID: sourceID, chapterID: chapter?.id ?? "", chapterTitle: chapter?.title ?? "教材原文",
          page: value.page_number,
          chunkID: CloudMapping.citationID(
            page: value.page_number, quote: value.quote, blockID: value.block_id),
          quote: value.quote, score: result.confidence)
      }
    }.filter { seen.insert($0.id).inserted }
    return AssistantAnswer(answer: result.answer, citations: citations)
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
    guard let cloud, let resource = cloudResource else {
      throw CloudError(status: 409, message: "请先生成当前章节课程")
    }
    let epoch = cloudState.epoch
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
    if cloud != nil {
      guard let resource = cloudResource else { return }
      let path: String
      if resource.source_content != nil {
        path = resource.path + "/catalog-flashcards/\(card.id)"
      } else if let session = resource.session_id {
        path = "/api/interviews/\(session)/courses/\(card.lesson_id)/flashcards/\(card.id)"
      } else {
        showToast("请先生成当前章节课程")
        return
      }
      cloudState.pendingReviews.append(
        CloudPendingReview(
          workspaceID: resource.workspace_id, resourceID: resource.id, cardID: card.id,
          path: path, rating: mastered ? "good" : "again", seconds: min(3600, max(0.1, seconds))))
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
      requiresPersonalization: cloudState.requiresPersonalization)
    let previous = persistTask
    let epoch = cloudState.epoch
    let saved = Task<Result<Void, Error>, Never> { [repository] in
      await previous?.value
      do {
        try await repository.saveCloudState(local, key: key)
        if epoch == self.cloudState.epoch { self.cloudState.cacheWriteError = nil }
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
    return saved
  }

  func startCloudSync() {
    guard cloudState.authenticated, !cloudState.isHydrating, cloudState.syncTask == nil,
      !cloudState.pending.isEmpty || !cloudState.pendingReviews.isEmpty
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
          cloudState.pendingReviews.removeAll { $0.eventID == review.eventID }
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
    guard let key = cloudState.cacheKey else { return }
    let local = CloudLocalSnapshot(
      snapshot: snapshot(), sources: sources, workspaces: cloudState.workspaces,
      filePaths: filePaths, pending: cloudState.pending,
      creationWorkspaceID: cloudState.creationWorkspaceID, notes: notes,
      pendingSelection: cloudState.pendingSelection,
      onboardingComplete: cloudState.onboardingComplete, pendingReviews: cloudState.pendingReviews,
      requiresPersonalization: cloudState.requiresPersonalization)
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
