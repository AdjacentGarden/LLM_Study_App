import CryptoKit
import Foundation

/// Verified-identity startup, offline cache recovery and authenticated retry.
extension AppStore {
  func cloudCacheKey(_ account: CloudAccountState, origin: String? = nil) -> String {
    SHA256.hash(data: Data(((origin ?? cloud?.baseURL.absoluteString ?? "") + "|" + account.user_id).utf8))
      .map { String(format: "%02x", $0) }.joined()
  }

  func loadCloudCache(_ account: CloudAccountState) async throws -> CloudLocalSnapshot? {
    let current = cloudCacheKey(account)
    if let state = try await repository.loadPersonalState(scope: current) {
      cloudState.privateLocalMode = true
      cloudState.cacheKey = current
      restorePrivateState(state)
      try await restoreLocalGenerationStatuses(scope: current)
      return CloudLocalSnapshot(snapshot: state.snapshot, sources: state.sources, workspaces: state.workspaces,
        filePaths: state.filePaths, pending: [:], notes: nil)
    }
    for origin in cloud?.cacheOrigins ?? [] {
      let key = cloudCacheKey(account, origin: origin)
      if var cached = try await repository.loadCloudState(key) {
        // A legacy origin's notes are copied (never moved) into the current scope once.
        if key != current, let notes = cached.notes, !notes.isEmpty {
          try await repository.adoptNotes(notes, scope: current)
          cached.notes = try await repository.loadStoredNotes(scope: current).map(\.note)
        }
        return cached
      }
    }
    return nil
  }

  func restoreCloudCache(_ cached: CloudLocalSnapshot) {
    if cloudState.privateLocalMode { return } // loadCloudCache already restored the full local authority.
        dailyPlanState.reset()
        dailyPlanState.plans = cached.dailyPlans ?? [:]
        dailyPlanState.pending = cached.pendingPlanCompletions ?? []
        filePaths = cached.filePaths
        cloudState.pending = cached.pending
        for (id, var pending) in cloudState.pending where pending.baseMasteredIDs == nil {
          pending.baseMasteredIDs =
            cached.workspaces.flatMap(\.resources)
            .first { $0.id == id }?.progress.mastered_ids ?? []
          cloudState.pending[id] = pending
        }
        cloudState.pendingReviews = cached.pendingReviews ?? []
        cloudState.pendingSelection = cached.pendingSelection
        cloudState.creationWorkspaceID = cached.creationWorkspaceID
        notes = cached.notes ?? []
        noteState.revisions = [:]
        courses = cached.snapshot.courses
        draft = cached.snapshot.draft
        chapterDrafts = cached.snapshot.chapterDrafts ?? [:]
        importJob = cached.snapshot.importJob
        importState.courses = cached.snapshot.courseImports ?? []
        for i in importState.courses.indices { importState.courses[i].mergeDuplicateBooks() }
        for record in importState.courses {
          cloudState.localStageFingerprints[(cloudState.cacheKey ?? "") + "::" + record.id] = stageSaveFingerprint(record)
        }
        for i in importState.courses.indices where importState.courses[i].job.status == .running {
          importState.courses[i].job.status = .interrupted
        }
        preferences = cached.snapshot.preferences
        cloudState.requiresPersonalization = cached.requiresPersonalization ?? false
        cloudState.onboardingComplete =
          !cloudState.requiresPersonalization && cached.onboardingComplete == true
          && preferences?.hasCompletedOnboarding == true
        cloudState.workspaces = cached.workspaces
        sources = cached.sources
        selectedCourseID = cached.snapshot.selectedCourseID
        selectedBookID = cached.snapshot.selectedBookID
        locations = cached.snapshot.locations
        for workspace in cached.workspaces {
          for resource in workspace.resources {
            let key = "\(workspace.id)::\(resource.book_id)"
            if locations[key] == nil {
              let value = cloudState.pending[resource.id]?.value ?? resource.progress
              locations[key] = StudyLocation(chapterID: value.chapter_id, lessonID: value.lesson_id, page: value.page)
            }
          }
        }
        // Rebuild legacy book-scoped cache entries from each resource's own progress.
        restoreCloudMastery()
        mistakes = cached.snapshot.mistakes
    rebuildCloudCourses()
    if importState.courses.isEmpty, importJob != nil { _ = registerCourseImport() }
    retainPendingCourseDraft()
  }

  func scheduleCloudRetry() {
    guard cloudState.authenticated, cloudState.retryTask == nil, let cloud else { return }
    let epoch = cloudState.epoch
    cloudState.retryTask = Task { @MainActor [weak self] in
      defer { self?.cloudState.retryTask = nil }
      while !Task.isCancelled {
        do { try await Task.sleep(for: self?.cloudState.retryDelay ?? .seconds(15)) } catch { return }
        guard let self, epoch == self.cloudState.epoch else { return }
        do {
          let account: CloudAccountState = try await cloud.request("/api/auth/me")
          guard account.account != nil, account.user_id == self.cloudState.account?.user_id else {
            throw CloudError(status: 401, message: "登录已过期，请重新登录")
          }
          await cloud.rememberAccount(account)
          try await self.reloadCloud()
          self.saveCloudCacheOnly()
          self.startCloudSync()
          return
        } catch let error as CloudError where error.status == 401 {
          self.handleCloudError(error)
          return
        } catch { self.cloudState.syncError = error.localizedDescription }
      }
    }
  }

  func bootCloud() async {
    guard let cloud else { return }
    cloudState.error = nil
    cloudState.localLogin = cloud.prefersLocalLogin
    bootError = nil
    defer {
      isLoading = false
      #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("--trace-cloud-status") {
        print("CloudPath startup", bootError == nil ? "ready" : "failed", route.rawValue, cloudState.localFirstChecked, cloudState.isHydrating)
      }
      #endif
    }
    do {
      community = []
      // Present only the last verified identity for this normalized service origin.
      // Credentials and identity live together in Keychain and are cleared together.
      let remembered = await cloud.verifiedAccount()
      var preloadedKey: String?
      var offlineBoot = false
      if let remembered, remembered.account != nil,
        let local = try await loadCloudCache(remembered) {
        cloudState.account = remembered
        cloudState.cacheKey = cloudCacheKey(remembered)
        cloudState.authenticated = true
        cloudState.isHydrating = true
        restoreCloudCache(local)
        try await restoreCourseDeletionState()
        // The cached shell carries no revisions; refresh them before the first save.
        await reloadNotes(resumeAgent: false)
        preloadedKey = cloudState.cacheKey
        cloudState.isHydrating = false
        route = cloudState.onboardingComplete ? .study : .onboarding
        isLoading = false
      }
      var account: CloudAccountState
      do {
      if cloudState.localLogin {
        do {
          let _: CloudOK = try await cloud.request("/api/native/local-login", method: "POST")
        } catch let error as CloudError where error.status == 404 {
          cloudState.localLogin = false
        }
      }
      account = try await cloud.request("/api/auth/me")
      guard account.account != nil else {
        throw CloudError(status: 401, message: "登录已过期，请重新登录")
      }
      await cloud.rememberAccount(account)
      } catch let error as CloudError where error.transportFailure && cloudState.authenticated {
        guard let remembered else { throw error }
        account = remembered
        offlineBoot = true
        cloudState.syncError = "正在使用本机缓存：" + error.localizedDescription
        scheduleCloudRetry()
      }
      cloudState.account = account
      cloudState.authenticated = true
      cloudState.isHydrating = true
      defer { cloudState.isHydrating = false }
      let key = cloudCacheKey(account)
      if cloudState.cacheKey != key {
        cloudState.privateLocalMode = false; cloudState.localFirstChecked = false
        cloudState.localGenerationStatuses = [:]; cloudState.localCourseSources = [:]; cloudState.localDiagnostics = [:]; cloudState.localDeletedCourseIDs = []
        dailyPlanState.reset()
        cloudState.onboardingComplete = false
        cloudState.requiresPersonalization = false
        sources = []
        courses = []
        notes = []
        noteState.revisions = [:]
        shelf = []
        preferences = nil
        locations = [:]
        filePaths = [:]
        mastered = []
        mistakes = []
        chapterDrafts = [:]
        draft = CourseDraft()
        importJob = nil
        clearCourseImports()
        selectedCourseID = nil
        selectedBookID = nil
        selectedChapterID = nil
        selectedLessonID = nil
        cloudState.pending = [:]
        cloudState.pendingReviews = []
        cloudState.cacheWriteError = nil
        cloudState.pendingSelection = nil
        cloudState.workspaces = []
        cloudState.creationWorkspaceID = nil
      }
      cloudState.cacheKey = key
      let cached = try await loadCloudCache(account)
      if let cached, preloadedKey != key { restoreCloudCache(cached) }
      if courseDeletionState.scope != noteScope { try await restoreCourseDeletionState() }
      else { applyCourseDeletionsInMemory(Array(courseDeletionState.tombstones.values)) }
      // Refresh note revisions (and let the agent resume) once the final scope is restored.
      if preloadedKey == key || cached != nil { await reloadNotes() }
      if !offlineBoot {
      do { try await reloadCloud() } catch {
        if cached != nil, cloudState.privateLocalMode, (error as? CloudError)?.status != 401 {
          // Capabilities can disappear during a server rollout. Keep phone state usable,
          // while the central API gate continues refusing every legacy personal write.
          cloudState.syncError = "本机数据可继续使用，学习服务暂不可用：" + error.localizedDescription
          scheduleCloudRetry()
          offlineBoot = true
        } else {
          guard cached != nil, let failure = error as? CloudError, failure.transportFailure else { throw error }
          rebuildCloudCourses()
          scheduleCloudRetry()
          cloudState.syncError = "正在使用本机缓存：" + error.localizedDescription
          offlineBoot = true
        }
      }
      }
      if let id = selectedCourseID { selectCourse(id) }
      if !offlineBoot { await reloadCloudLearningRecords() }
      route = cloudState.onboardingComplete ? .study : .onboarding
      // Restore unfinished work inside the selected course on the study page.
      if importJob?.status == .running {
        importJob?.status = .interrupted
        processingMessage = "资料处理可继续，原文件已保留"
      }
    } catch {
      handleCloudError(error)
      if cloudState.authenticated { bootError = error.localizedDescription }
    }
    cloudState.isHydrating = false
    persistCloud()
    scheduleCourseStageSaves(force: true)
    startCloudSync()
  }

  func reloadCloud() async throws {
    if cloudState.legacyPrivateSyncEnabled, !cloudState.privateLocalMode { try await reloadLegacyCloud(); return }
    guard cloud != nil else { return }
    try await verifyLocalFirstCapabilities()
    guard let scope = noteScope else { throw ContentError.invalid("请先登录") }
    if courseDeletionState.scope != scope { try await restoreCourseDeletionState() }
    if try await repository.loadPersonalState(scope: scope) == nil {
      try await migrateLegacyPersonalData()
      try await persistPrivateState()
    } else {
      // New phone contents always win; old-client updates are archived only.
      do { try await migrateLegacyPersonalData() }
      catch { cloudState.migrationStatus = "旧版变化尚未校验，已保留本机数据：" + error.localizedDescription }
    }
    try await refreshPublicCourseGroups()
    await resumePublicLibraryTasks()
    shelf = sources.filter { sourceFileURL($0.id) != nil }.map {
      ShelfBook(bookId: $0.id, title: $0.summary.title, filename: $0.summary.filename ?? $0.summary.title, coverUrl: $0.summary.cover_url ?? "")
    }
    cloudState.syncError = nil
  }
  private func reloadLegacyCloud() async throws {
    guard let cloud else { return }
    let epoch = cloudState.epoch
    cloudState.materialSyncCount += 1
    defer { if epoch == cloudState.epoch { cloudState.materialSyncCount -= 1 } }
    let bootstrap: CloudBootstrap = try await cloud.request("/api/native/bootstrap?mode=metadata")
    guard epoch == cloudState.epoch else { throw CancellationError() }
    guard bootstrap.contract_version == 1 else {
      throw CloudError(status: 0, message: "客户端与后端版本不兼容")
    }
    let removed = courseDeletionState.scope == noteScope ? courseDeletionState.deletedIDs : []
    var workspaces = bootstrap.workspaces.filter { !removed.contains($0.id) }
    for wi in workspaces.indices {
      for ri in workspaces[wi].resources.indices {
        let resource = workspaces[wi].resources[ri]
        if let version = resource.content_key,
          let content = try await repository.loadCloudContent(version, account: cloudState.cacheKey ?? "") {
          workspaces[wi].resources[ri] = content.applying(to: resource)
        }
      }
    }
    cloudState.workspaces = workspaces
    preferences = bootstrap.learner_preferences
    cloudState.onboardingComplete = !cloudState.requiresPersonalization
      && bootstrap.learner_preferences?.hasCompletedOnboarding == true
    community = try BuiltinCourseCatalog.merging(bootstrap.catalog ?? [])
    cloudState.confirmedSelection = bootstrap.selection.flatMap {
      removed.contains($0.workspace_id ?? "") ? nil : $0
    }
    rebuildCloudCourses()
    // "My books" contains actual device files, never every published server book.
    shelf = sources.filter { sourceFileURL($0.id) != nil }.map {
      ShelfBook(bookId: $0.id, title: $0.summary.title,
                filename: $0.summary.filename ?? $0.summary.title, coverUrl: $0.summary.cover_url ?? "")
    }
    restoreCloudMastery()
    for workspace in workspaces {
      for resource in workspace.resources {
        let value = cloudState.pending[resource.id]?.value ?? resource.progress
        locations["\(workspace.id)::\(resource.book_id)"] = StudyLocation(
          chapterID: value.chapter_id, lessonID: value.lesson_id, page: value.page)
      }
    }
    if selectedCourseID == nil || !courses.contains(where: { $0.id == selectedCourseID }) {
      let candidate = bootstrap.selection?.workspace_id
      selectedCourseID = courses.first { $0.id == candidate }?.id ?? courses.first?.id
      if let resourceID = bootstrap.selection?.resource_id {
        selectedBookID =
          workspaces.flatMap(\.resources).first { $0.id == resourceID }?.book_id
      }
    }
    if let workspaceID = selectedCourseID {
      try await hydrateCloudContent(workspaceID: workspaceID)
      installCloudSources(workspaceID: workspaceID)
    }
    cloudState.syncError = nil
    scheduleCourseStageSaves(force: true)
  }

}
