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
      cloudState.authenticated = false
      cloudState.showDiagnosis = false
      cloudState.epoch = UUID()
      cloudState.syncTask?.cancel()
      sources = []
      courses = []
      notes = []
      chatMessages = []
      Task { await cloud?.clearCredentials() }
    }
  }

  func bootCloud() async {
    guard let cloud else { return }
    cloudState.error = nil
    cloudState.localLogin = cloud.prefersLocalLogin
    bootError = nil
    defer { isLoading = false }
    do {
      community = try BuiltinCourseCatalog.merging([])
      if cloudState.localLogin {
        do {
          let _: CloudOK = try await cloud.request("/api/native/local-login", method: "POST")
        } catch let error as CloudError where error.status == 404 {
          cloudState.localLogin = false
        }
      }
      let account: CloudAccountState = try await cloud.request("/api/auth/me")
      cloudState.account = account
      guard account.account != nil else {
        cloudState.authenticated = false
        return
      }
      cloudState.authenticated = true
      cloudState.isHydrating = true
      defer { cloudState.isHydrating = false }
      let key = SHA256.hash(data: Data((cloud.baseURL.absoluteString + "|" + account.user_id).utf8))
        .map { String(format: "%02x", $0) }.joined()
      if cloudState.cacheKey != key {
        cloudState.onboardingComplete = false
        sources = []
        courses = []
        notes = []
        shelf = []
        preferences = nil
        locations = [:]
        filePaths = [:]
        mastered = []
        mistakes = []
        chapterDrafts = [:]
        draft = CourseDraft()
        importJob = nil
        selectedCourseID = nil
        selectedBookID = nil
        selectedChapterID = nil
        selectedLessonID = nil
        cloudState.pending = [:]
        cloudState.pendingSelection = nil
        cloudState.workspaces = []
        cloudState.creationWorkspaceID = nil
      }
      cloudState.cacheKey = key
      let cached = try await repository.loadCloudState(key)
      if let cached {
        filePaths = cached.filePaths
        cloudState.pending = cached.pending
        cloudState.pendingSelection = cached.pendingSelection
        cloudState.creationWorkspaceID = cached.creationWorkspaceID
        notes = cached.notes ?? []
        draft = cached.snapshot.draft
        chapterDrafts = cached.snapshot.chapterDrafts ?? [:]
        importJob = cached.snapshot.importJob
        preferences = cached.snapshot.preferences
        cloudState.onboardingComplete =
          cached.onboardingComplete == true && preferences?.hasCompletedOnboarding == true
        cloudState.workspaces = cached.workspaces
        sources = cached.sources
        selectedCourseID = cached.snapshot.selectedCourseID
        selectedBookID = cached.snapshot.selectedBookID
        locations = cached.snapshot.locations
        mastered = cached.snapshot.mastery
        mistakes = cached.snapshot.mistakes
      }
      do { try await reloadCloud() } catch {
        guard cached != nil else { throw error }
        rebuildCloudCourses()
        cloudState.syncError = "正在使用本机缓存：" + error.localizedDescription
      }
      if let id = selectedCourseID { selectCourse(id) }
      await reloadCloudLearningRecords()
      route = cloudState.onboardingComplete ? .home : .onboarding
      if cloudState.onboardingComplete, !draft.resourceIDs.isEmpty {
        if draft.catalogBookID != nil {
          route = .courseSetup
        } else {
          route =
            cloudState.creationWorkspaceID != nil
            ? .chapterConfirm : draft.parseCompleted ? .courseSetup : .parseReady
        }
      }
      if cloudState.onboardingComplete, let job = importJob,
        job.status == .running || job.status == .interrupted || job.status == .failed
      {
        importJob?.status = .interrupted
        processingMessage = "资料处理可继续，原文件已保留"
        route = .parseReady
      }
    } catch {
      handleCloudError(error)
      if cloudState.authenticated { bootError = error.localizedDescription }
    }
    cloudState.isHydrating = false
    persistCloud()
    startCloudSync()
  }

  func reloadCloud() async throws {
    guard let cloud else { return }
    let epoch = cloudState.epoch
    let bootstrap: CloudBootstrap = try await cloud.request("/api/native/bootstrap")
    guard epoch == cloudState.epoch else { throw CancellationError() }
    guard bootstrap.contract_version == 1 else {
      throw CloudError(status: 0, message: "客户端与后端版本不兼容")
    }
    cloudState.workspaces = bootstrap.workspaces
    preferences = bootstrap.learner_preferences
    cloudState.onboardingComplete = bootstrap.learner_preferences?.hasCompletedOnboarding == true
    community = try BuiltinCourseCatalog.merging(bootstrap.catalog ?? [])
    cloudState.confirmedSelection = bootstrap.selection
    rebuildCloudCourses()
    for book in bootstrap.library ?? [] where !sources.contains(where: { $0.id == book.book_id }) {
      let resource = CloudResource(
        id: "library:\(book.book_id)", book_id: book.book_id,
        workspace_id: "", revision: 0, filename: book.title + ".pdf", status: book.status,
        structure: book.structure, courses: [], progress: CloudProgress())
      sources.append(CloudMapping.source(resource))
    }
    shelf = (bootstrap.library ?? []).map {
      ShelfBook(
        bookId: $0.book_id, title: $0.title, filename: $0.title + ".pdf",
        coverUrl: $0.cover_url ?? "")
    }
    for workspace in bootstrap.workspaces {
      for resource in workspace.resources {
        let value = cloudState.pending[resource.id]?.value ?? resource.progress
        locations["\(workspace.id)::\(resource.book_id)"] = StudyLocation(
          chapterID: value.chapter_id, lessonID: value.lesson_id, page: value.page)
        mastered.formUnion(value.mastered_ids)
      }
    }
    if selectedCourseID == nil || !courses.contains(where: { $0.id == selectedCourseID }) {
      let candidate = bootstrap.selection?.workspace_id
      selectedCourseID = courses.first { $0.id == candidate }?.id ?? courses.first?.id
      if let resourceID = bootstrap.selection?.resource_id {
        selectedBookID =
          bootstrap.workspaces.flatMap(\.resources).first { $0.id == resourceID }?.book_id
      }
    }
    if let workspaceID = selectedCourseID { installCloudSources(workspaceID: workspaceID) }
    cloudState.syncError = nil
  }

  func rebuildCloudCourses() {
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
    for workspace in cloudState.workspaces {
      for resource in workspace.resources
      where !sources.contains(where: { $0.id == resource.book_id }) {
        sources.append(CloudMapping.source(resource))
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
        sources[index] = source
      } else {
        sources.append(source)
      }
    }
  }

  func refreshCloudCatalog() async throws {
    try await reloadCloud()
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
    startCloudSync()
    while let task = cloudState.syncTask { await task.value }
    guard cloudState.authenticated else { return }
    do {
      try await reloadCloud()
      if let workspace = selectedCourseID { selectCourse(workspace) }
      await reloadCloudLearningRecords()
      persistCloud()
    } catch {
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
    cloudState.recordsTask?.cancel()
    await cloudState.recordsTask?.value
    cloudState.syncTask?.cancel()
    await cloudState.syncTask?.value
    do {
      let _: CloudOK = try await cloud.request("/api/auth/logout", method: "POST")
      await cloud.clearCredentials()
      cloudState.epoch = UUID()
      cloudState.authenticated = false
      cloudState.account = nil
      cloudState.cacheKey = nil
      cloudState.pending = [:]
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

  func importCloudPDF(_ url: URL) async {
    guard let cloud, cloudState.authenticated else { return }
    let epoch = cloudState.epoch
    importError = nil
    do {
      guard url.pathExtension.lowercased() == "pdf" else {
        throw CloudError(status: 415, message: "云端课程目前支持 PDF，请选择 PDF 文件。")
      }
      let staged = try await LocalFileImporter.stage(url: url, repository: repository)
      let uploaded = try await cloud.uploadPDF(
        repository.attachmentURL(staged.relativePath), filename: url.lastPathComponent)
      guard epoch == cloudState.epoch else { return }
      var source = staged.source
      source.summary.book_id = uploaded.book_id
      source.summary.status = uploaded.status
      source.plan.book_id = uploaded.book_id
      sources.append(source)
      filePaths[uploaded.book_id] = staged.relativePath
      draft.resourceIDs.append(uploaded.book_id)
      if draft.name.isEmpty { draft.name = staged.source.summary.title }
      selectedBookID = uploaded.book_id
      importedFileURL = repository.attachmentURL(staged.relativePath)
      persistCloud()
      await persistTask?.value
    } catch {
      importError = error.localizedDescription
      handleCloudError(error)
    }
  }

  func processCloudImport() async {
    guard let cloud, !cloudState.isWorking else { return }
    cloudState.isWorking = true
    let epoch = cloudState.epoch
    defer { cloudState.isWorking = false }
    importError = nil
    let ids = draft.resourceIDs
    importJob = ImportJob(bookIDs: ids, status: .running)
    persistCloud()
    for (index, bookID) in ids.enumerated() {
      do {
        var status: CloudBookStatus = try await cloud.request("/api/books/\(bookID)/status")
        if !["ocr_ready", "structured"].contains(status.status) {
          let health: CloudHealth = try await cloud.request("/api/health")
          guard health.ocr_worker_enabled, health.ocr_worker_alive else {
            throw CloudError(status: 503, message: "服务器的教材解析服务尚未启动，PDF 已保存，请启动服务后重试。")
          }
        }
        if status.status == "uploaded" {
          status = try await cloud.request("/api/books/\(bookID)/process", method: "POST")
        } else if status.retryable {
          status = try await cloud.request("/api/books/\(bookID)/process/retry", method: "POST")
        }
        while !["ocr_ready", "structured"].contains(status.status) {
          guard epoch == cloudState.epoch, importJob?.status == .running else { return }
          if status.retryable || status.status == "cancelled" {
            throw CloudError(
              status: 409,
              message: status.needs_human_review
                ? "教材解析需要复核，请检查原 PDF 后重试。" : "教材处理未完成，可以稍后重试。")
          }
          processingMessage = status.current_step
          processingProgress = (Double(index) + status.progress * 0.8) / Double(max(1, ids.count))
          importJob?.progress = processingProgress
          persistCloud()
          try await Task.sleep(for: .seconds(2))
          status = try await cloud.request("/api/books/\(bookID)/status")
        }
        processingMessage = "正在整理目录和教材检索索引"
        struct Prepared: Decodable, Sendable {
          var structure: CloudStructure
          var rag_ready: Bool
        }
        let prepared: Prepared = try await cloud.request(
          "/api/native/books/\(bookID)/prepare", method: "POST")
        guard epoch == cloudState.epoch, importJob?.status == .running else { return }
        let temporary = CloudResource(
          id: "staged:\(bookID)", book_id: bookID, workspace_id: "", revision: 0,
          filename: sources.first { $0.id == bookID }?.summary.filename ?? "教材.pdf",
          status: "structured",
          structure: prepared.structure, courses: [], progress: CloudProgress())
        let source = CloudMapping.source(temporary)
        if let i = sources.firstIndex(where: { $0.id == bookID }) { sources[i] = source }
        processingProgress = Double(index + 1) / Double(max(1, ids.count))
      } catch {
        guard epoch == cloudState.epoch else { return }
        importJob?.status = .failed
        importError = error.localizedDescription
        processingMessage = "整理中断，原文件已保留"
        persistCloud()
        handleCloudError(error)
        return
      }
    }
    draft.parseCompleted = true
    importJob?.status = .completed
    processingMessage = "资料整理完成"
    processingProgress = 1
    do { try await reloadCloud() } catch { cloudState.syncError = error.localizedDescription }
    persistCloud()
    if route == .processing { go(.courseSetup) }
  }

  func saveCloudCourse(name: String, resourceIDs: [String], diagnosis: [String: [String]])
    async throws
  {
    guard let cloud else { return }
    let workspaceID =
      draft.editingCourseID ?? cloudState.creationWorkspaceID
      ?? "workspace_" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    // Persist the id before the first write so a timed-out creation can be recovered idempotently.
    if draft.editingCourseID == nil { cloudState.creationWorkspaceID = workspaceID }
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
      draft = CourseDraft()
      cloudState.creationWorkspaceID = nil
      persistCloud()
    } catch {
      handleCloudError(error)
      return
    }
    cloudState.isWorking = false
    cloudState.showCompletionOnCompile = true
    await beginCloudLearning(chapterID: selectedChapterID ?? chapters.first?.id)
  }

  func beginCloudLearning(chapterID: String?) async {
    guard let cloud, let resource = cloudResource, !cloudState.isWorking else { return }
    cloudState.isWorking = true
    defer { cloudState.isWorking = false }
    cloudState.error = nil
    cloudState.requestedChapterID = chapterID ?? resource.structure?.chapters.first?.chapter_id
    cloudState.diagnosisWorkspaceID = resource.workspace_id
    cloudState.diagnosisResourceID = resource.id
    do {
      let interview: CloudInterview = try await cloud.request(
        resource.path + "/session", method: "POST")
      guard selectedCourseID == resource.workspace_id, selectedBookID == resource.book_id else {
        return
      }
      cloudState.interview = interview
      if interview.phase == "complete" {
        try await compileCloudChapter()
      } else {
        cloudState.showDiagnosis = true
      }
    } catch { handleCloudError(error) }
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
          turn: result.next_turn, profile: result.profile)
      } else if interview.phase == "adaptive_diagnosis" {
        cloudState.interview = try await cloud.request(path + "/next", method: "POST")
      } else if interview.phase != "complete" {
        cloudState.interview = try await cloud.request(
          path + "/profile", method: "POST",
          body: [
            "answer": .string(selected.joined(separator: ",")),
            "selected_option_ids": .array(selected.map(JSONValue.string)),
          ])
      }
      if cloudState.interview?.phase == "complete" { try await compileCloudChapter() }
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

  func compileCloudChapter() async throws {
    guard let cloud, let interview = cloudState.interview,
      let chapterID = cloudState.requestedChapterID
    else { return }
    let _: CloudCourse = try await cloud.request(
      "/api/interviews/\(interview.session_id)/courses/\(chapterID)", method: "POST")
    try await reloadCloud()
    cloudState.showDiagnosis = false
    if let lesson = lessons.first(where: { $0.chapter_id == chapterID && $0.status == "ready" }) {
      guard selectedCourseID == cloudState.diagnosisWorkspaceID,
        cloudResource?.id == cloudState.diagnosisResourceID
      else { return }
      if cloudState.showCompletionOnCompile {
        selectedChapterID = lesson.chapter_id
        selectedLessonID = lesson.id
        saveLocation()
        cloudState.showCompletionOnCompile = false
        go(.courseReady)
      } else {
        openLesson(lesson)
      }
    }
    persistCloud()
  }

  func removeCloudCourse(_ id: String) async {
    do {
      guard let cloud else { return }
      let _: CloudOK = try await cloud.request("/api/workspaces/\(id)", method: "DELETE")
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

  func addCloudResource(bookID: String, workspaceID: String?) async {
    do {
      guard let cloud, let wid = workspaceID ?? selectedCourseID else { return }
      let _: CloudResource = try await cloud.request(
        "/api/workspaces/\(wid)/resources", method: "POST", body: ["book_id": .string(bookID)])
      try await reloadCloud()
      persistCloud()
    } catch { handleCloudError(error) }
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
        bookIDs: [bookID], chapterID: selectedChapterID, sources: sources, history: chatMessages)
      guard epoch == cloudState.epoch, generation == selectionGeneration else { return }
      chatMessages.append(
        ChatMessage(role: "assistant", text: result.answer, citations: result.citations))
    } catch { handleCloudError(error) }
  }

  func answerAssistant(
    question: String, bookID: String, bookIDs: [String], chapterID: String?,
    sources: [SourceContent], history: [ChatMessage]
  ) async throws -> AssistantAnswer {
    guard let cloud else {
      return try await assistant.ask(
        question: question, bookID: bookID, bookIDs: bookIDs,
        chapterID: chapterID, sources: sources, history: history)
    }
    let result: CloudQA = try await cloud.request(
      "/api/books/\(bookID)/qa", method: "POST", body: ["question": .string(question)])
    let directory = sources.first { $0.id == bookID }?.chapters ?? []
    var seen = Set<String>()
    let citations = result.claims.flatMap(\.citations).map { value in
      let chapter = directory.first {
        $0.page_start <= value.page_number && $0.page_end >= value.page_number
      }
      return Citation(
        bookID: bookID, chapterID: chapter?.id ?? "", chapterTitle: chapter?.title ?? "教材原文",
        page: value.page_number,
        chunkID: CloudMapping.citationID(
          page: value.page_number,
          quote: value.quote, blockID: value.block_id), quote: value.quote, score: result.confidence
      )
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

  func persistCloud() {
    guard cloudState.authenticated, !cloudState.isHydrating, let key = cloudState.cacheKey else {
      return
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
      var value = resource.progress
      value.page = max(1, min(location.page, resource.structure?.source_page_count ?? 1))
      value.chapter_id = location.chapterID
      value.lesson_id = location.lessonID?.hasPrefix("pending:") == true ? nil : location.lessonID
      value.completed_chapters =
        (currentSource?.plan.tasks.filter { ["done", "completed"].contains($0.status) }.compactMap(
          \.chapter_id) ?? []).sorted()
      value.task_status = Dictionary(
        uniqueKeysWithValues: (currentSource?.plan.tasks ?? []).map { ($0.id, $0.status) })
      value.mastered_ids = mastered.filter { $0.hasPrefix(resource.book_id + "::") }.sorted()
      let pending = cloudState.pending[resource.id]
      if value != (pending?.value ?? resource.progress) {
        cloudState.pending[resource.id] = CloudPendingProgress(
          workspaceID: resource.workspace_id, resourceID: resource.id, value: value)
      }
    }
    let local = CloudLocalSnapshot(
      snapshot: snapshot(), sources: sources, workspaces: cloudState.workspaces,
      filePaths: filePaths, pending: cloudState.pending,
      creationWorkspaceID: cloudState.creationWorkspaceID, notes: notes,
      pendingSelection: cloudState.pendingSelection,
      onboardingComplete: cloudState.onboardingComplete)
    let previous = persistTask
    persistTask = Task { [repository] in
      await previous?.value
      do { try await repository.saveCloudState(local, key: key) } catch {
        self.cloudState.syncError = error.localizedDescription
      }
    }
    startCloudSync()
  }

  func startCloudSync() {
    guard cloudState.authenticated, !cloudState.isHydrating, cloudState.syncTask == nil,
      !cloudState.pending.isEmpty || cloudState.pendingSelection != nil,
      let cloud
    else { return }
    let epoch = cloudState.epoch
    cloudState.syncing = true
    cloudState.syncTask = Task { @MainActor in
      defer {
        cloudState.syncing = false
        cloudState.syncTask = nil
        if epoch == cloudState.epoch, !Task.isCancelled, cloudState.syncError == nil,
          !cloudState.pending.isEmpty || cloudState.pendingSelection != nil
        {
          startCloudSync()
        }
      }
      await persistTask?.value
      while let entry = cloudState.pending.values.sorted(by: { $0.resourceID < $1.resourceID })
        .first
      {
        guard epoch == cloudState.epoch, !Task.isCancelled else { return }
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
          } else {
            cloudState.pending[entry.resourceID]?.value.revision = saved.revision
          }
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
              pending.value.revision = server.progress.revision
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
          saveCloudCacheOnly()
          return
        }
      }
      while let selection = cloudState.pendingSelection {
        guard epoch == cloudState.epoch, !Task.isCancelled else { return }
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
      onboardingComplete: cloudState.onboardingComplete)
    let previous = persistTask
    persistTask = Task { [repository] in
      await previous?.value
      do { try await repository.saveCloudState(local, key: key) } catch {
        self.cloudState.syncError = error.localizedDescription
      }
    }
  }
}
