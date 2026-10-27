import Foundation
import XCTest

@testable import BookCourse

final class CloudTests: XCTestCase {
  func testConfiguredRemoteBackendTransportAndAuthentication() async throws {
    guard let value = ProcessInfo.processInfo.environment["BOOKCOURSE_REMOTE_API_BASE_URL"] else {
      throw XCTSkip("Set BOOKCOURSE_REMOTE_API_BASE_URL to test a real HTTPS backend")
    }
    let base = try XCTUnwrap(CloudAPI.validatedURL(value))
    XCTAssertEqual(base.scheme, "https")
    let api = CloudAPI(baseURL: base)
    struct Health: Decodable {
      let ok: Bool
      let service: String
    }
    let health: Health = try await api.request("/api/health")
    XCTAssertTrue(health.ok)
    XCTAssertEqual(health.service, "adaptive-book-learning")
    let account: CloudAccountState = try await api.request("/api/auth/me")
    if account.account == nil {
      do {
        let _: [String: JSONValue] = try await api.request("/api/native/bootstrap")
        XCTFail("An unauthenticated client must not receive protected learning data")
      } catch let error as CloudError {
        XCTAssertEqual(error.status, 401)
      }
    }
  }

  func testMasteryConflictRebasePreservesRemoteAddsAndExplicitLocalRemovals() throws {
    var pending = CloudPendingProgress(
      workspaceID: "w", resourceID: "r",
      value: CloudProgress(mastered_ids: ["book::local"]), baseMasteredIDs: ["book::removed"])
    pending.rebaseMastery(
      onto: CloudProgress(revision: 4, mastered_ids: ["book::remote", "book::removed"]))
    XCTAssertEqual(Set(pending.value.mastered_ids), ["book::remote", "book::local"])
    XCTAssertEqual(pending.value.revision, 4)
    let restored = try JSONDecoder().decode(
      CloudPendingProgress.self, from: JSONEncoder().encode(pending))
    XCTAssertEqual(restored.baseMasteredIDs, ["book::remote", "book::removed"])
    pending.recordMastery("book::local", mastered: false)
    pending.rebaseMastery(
      onto: CloudProgress(revision: 5, mastered_ids: ["book::remote", "book::later"]))
    XCTAssertEqual(Set(pending.value.mastered_ids), ["book::remote", "book::later"])
  }

  func testExplicitAgainOverridesUnseenRemoteMasteryAndInFlightToggleSurvivesAck() {
    var pending = CloudPendingProgress(
      workspaceID: "w", resourceID: "r", value: CloudProgress(), baseMasteredIDs: [])
    pending.recordMastery("book::card", mastered: false)
    pending.rebaseMastery(
      onto: CloudProgress(revision: 1, mastered_ids: ["book::card", "book::other"]))
    XCTAssertEqual(pending.value.mastered_ids, ["book::other"])
    pending.recordMastery("book::card", mastered: true)
    let sent = pending
    pending.recordMastery("book::card", mastered: false)
    pending.acknowledgeMastery(
      sent, saved: CloudProgress(revision: 2, mastered_ids: ["book::card", "book::other"]))
    XCTAssertEqual(pending.value.mastered_ids, ["book::other"])
    XCTAssertEqual(pending.masteryChanges, ["book::card": false])
  }

  @MainActor func testCloudMasteryMigratesFromResourceProgressAndKeepsCoursesIsolated() throws {
    let repo = try LocalBookCourseRepository(
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(
      repository: repo, cloud: CloudAPI(baseURL: URL(string: "http://127.0.0.1:18101")!))
    let a = CloudResource(
      id: "resource-a", book_id: "book", workspace_id: "course-a", revision: 0,
      filename: "book", status: "ready", courses: [],
      progress: CloudProgress(mastered_ids: ["book::card"]))
    let b = CloudResource(
      id: "resource-b", book_id: "book", workspace_id: "course-b", revision: 0,
      filename: "book", status: "ready", courses: [], progress: CloudProgress())
    store.cloudState.workspaces = [a, b].map {
      CloudWorkspace(
        id: $0.workspace_id, name: $0.workspace_id, preferences: [:], created: 0, updated: 0,
        resources: [$0])
    }
    store.mastered = ["book::card"]  // Ambiguous old cache must not be copied into both courses.
    store.restoreCloudMastery()
    store.selectedBookID = "book"
    store.selectedCourseID = "course-a"
    XCTAssertTrue(store.isMastered("card"))
    store.selectedCourseID = "course-b"
    XCTAssertFalse(store.isMastered("card"))
    store.setMastery("other", mastered: true)
    store.selectedCourseID = "course-a"
    XCTAssertFalse(store.isMastered("other"))
    store.cloudState.workspaces[0].resources[0].progress.mastered_ids = []
    store.restoreCloudMastery()
    XCTAssertFalse(store.isMastered("card"), "Reload must also apply remote removals")
  }

  @MainActor func testCloudNoteWriteFailureThrowsAndCanBeRetried() async throws {
    let repo = try LocalBookCourseRepository(
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(
      repository: repo, cloud: CloudAPI(baseURL: URL(string: "http://127.0.0.1:18101")!))
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = "note-write-failure"
    let note = StudyNote(id: "draft", kind: .text, title: "草稿", body: "不得假报保存成功")
    await repo.setWriteFaultForTesting(true)
    do {
      try await store.saveNote(note)
      XCTFail("The editor must receive the SQLite failure")
    } catch { XCTAssertFalse(error.localizedDescription.isEmpty) }
    XCTAssertEqual(store.notes.first?.body, note.body)
    let failed = try await repo.loadCloudState("note-write-failure")
    XCTAssertNil(failed)
    await repo.setWriteFaultForTesting(false)
    try await store.saveNote(note)
    await store.flush()
    let saved = try await repo.loadCloudState("note-write-failure")
    XCTAssertEqual(saved?.notes?.first?.body, note.body)
  }

  func testCatalogOnlyUsesServerEntriesAndDeduplicatesRefresh() throws {
    XCTAssertTrue(try BuiltinCourseCatalog.merging([]).isEmpty,
                  "A missing server catalog must not resurrect local preview courses")
    var entry = CommunityBook(id: "remote-book", title: "实际公开教材", catalogTitle: "目录", owner: "作者",
      cover: "", subject: "其他", grade: "", version: "1", learners: nil, progress: nil,
      flashcardCount: 0, tags: [], description: "服务端说明", chapters: [])
    entry.description = "更新的服务端说明"
    let refreshed = try BuiltinCourseCatalog.merging([entry, entry])
    XCTAssertEqual(refreshed, [entry])
    XCTAssertNil(refreshed.first?.learners)
    XCTAssertNil(refreshed.first?.progress)
    XCTAssertTrue(try BuiltinCourseCatalog.merging([]).isEmpty)
  }

  func testPublishedLegacyCatalogLessonsCanBeCompletedWithoutPromotingPendingLessons() throws {
    var source = try XCTUnwrap(ContentBundle.load([SourceContent].self, name: "sources").first)
    let legacy = try XCTUnwrap(source.lessons.firstIndex { $0.status == "generated" })
    let pending = try XCTUnwrap(source.lessons.indices.first { $0 != legacy })
    source.lessons[pending].status = "pending"
    let resource = CloudResource(id: "catalog-resource", book_id: source.id, workspace_id: "course",
      revision: 0, filename: "教材", status: "ready", courses: [], progress: CloudProgress(),
      source_content: source)
    let mapped = CloudMapping.source(resource)
    XCTAssertEqual(mapped.lessons[legacy].status, "ready")
    XCTAssertEqual(mapped.lessons[pending].status, "pending")
    XCTAssertEqual(mapped.lessons[legacy].blocks, source.lessons[legacy].blocks)
  }

  // SQLite actors can outlive a test's async frame. Leave their databases in the
  // Simulator's temporary directory instead of unlinking live WAL connections.
  @MainActor func testLocalDefaultAccountAutomaticallyRestoresWorkspace() async throws {
    let base = URL(string: "http://127.0.0.1:18101")!
    let configuration = URLSessionConfiguration.ephemeral
    configuration.timeoutIntervalForRequest = 1
    configuration.connectionProxyDictionary = [:]
    let probe = URLSession(configuration: configuration)
    guard
      let (data, _) = try? await probe.data(
        from: base.appendingPathComponent("api/native-test/health")),
      let health = try? JSONDecoder().decode([String: Bool].self, from: data),
      health["fixture_adapters"] == true, health["local_default"] == true
    else { throw XCTSkip("Run scripts/test-cloud.sh with NATIVE_E2E_LOCAL_LOGIN=1") }
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let api = CloudAPI(baseURL: base)
    await api.clearCredentials()
    let store = AppStore(
      repository: try LocalBookCourseRepository(directory: root.appendingPathComponent("first")),
      cloud: api)
    await store.boot()
    XCTAssertTrue(store.cloudState.localLogin)
    XCTAssertTrue(store.cloudState.authenticated, store.cloudState.error ?? "")
    XCTAssertNil(store.preferences)
    XCTAssertEqual(store.route, .onboarding)
    try await store.saveCloudPreferences(name: "本地个性化学习者", goal: "exam", dailyTime: "30to60")
    let workspace: CloudWorkspace = try await api.request(
      "/api/workspaces", method: "POST",
      body: ["id": .string("workspace_auto_local"), "name": .string("本地恢复验证")])
    await store.logoutCloud()
    let restored = AppStore(
      repository: try LocalBookCourseRepository(directory: root.appendingPathComponent("fresh")),
      cloud: CloudAPI(baseURL: base))
    await restored.boot()
    XCTAssertTrue(restored.cloudState.authenticated, restored.cloudState.error ?? "")
    XCTAssertEqual(restored.route, .home)
    XCTAssertEqual(restored.preferences?.displayName, "本地个性化学习者")
    XCTAssertEqual(restored.courses.first { $0.id == workspace.id }?.name, "本地恢复验证")
    await restored.flush()
  }

  func testServiceAddressValidationRejectsEmbeddedCredentialsAndPaths() {
    XCTAssertNotNil(CloudAPI.validatedURL("http://127.0.0.1:8100"))
    XCTAssertNotNil(CloudAPI.validatedURL("https://study.example.com"))
    for value in [
      "file:///tmp/data", "https://user:secret@study.example.com",
      "https://study.example.com/other", "https://study.example.com?key=secret",
    ] {
      XCTAssertNil(CloudAPI.validatedURL(value))
    }
  }

  @MainActor func testExistingBiologyImportAndPersonalizationRestoreWithoutGeneration() async throws
  {
    let base = URL(string: "http://127.0.0.1:18101")!
    let config = URLSessionConfiguration.ephemeral
    config.timeoutIntervalForRequest = 1
    config.connectionProxyDictionary = [:]
    let probe = URLSession(configuration: config)
    guard
      let (data, _) = try? await probe.data(
        from: base.appendingPathComponent("api/native-test/health")),
      let marker = try? JSONDecoder().decode([String: Bool].self, from: data),
      marker["fixture_adapters"] == true
    else { throw XCTSkip("Use scripts/test-cloud.sh for the isolated API test") }
    let api = CloudAPI(baseURL: base)
    await api.clearCredentials()
    let challenge: CloudChallenge = try await api.request(
      "/api/auth/code", method: "POST",
      body: ["email": .string("momo@zhiwo.test"), "purpose": .string("register")])
    let _: CloudAccountState = try await api.request(
      "/api/auth/verify", method: "POST",
      body: [
        "challenge_id": .string(challenge.challenge_id),
        "code": .string(try XCTUnwrap(challenge.demo_code)),
        "registration": .object([
          "nickname": .string("Biology Learner"), "stage": .string("high"), "interests": .array([]),
          "goal": .string("exam"),
        ]),
      ])
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let store = AppStore(
      repository: try LocalBookCourseRepository(directory: root.appendingPathComponent("first")),
      cloud: api)
    await store.boot()
    XCTAssertEqual(store.route, .onboarding)
    XCTAssertNil(store.preferences)
    // A legacy account keeps its first three answers and is asked to complete the new steps.
    let _: LearnerPreferences = try await api.request(
      "/api/native/learner-preferences", method: "PUT",
      body: [
        "displayName": .string("Biology Learner"), "primaryGoal": .string("exam"),
        "dailyTime": .string("30to60"),
      ])
    try await store.reloadCloud()
    XCTAssertFalse(store.cloudState.onboardingComplete)
    XCTAssertEqual(
      LearnerOnboardingDraft.load(scope: UUID().uuidString, preferences: store.preferences).step, 3)
    try await store.saveCloudPreferences(
      name: "Biology Learner", goal: "exam", dailyTime: "30to60",
      learningBarriers: ["understanding", "memory"], guidanceStyles: ["examples", "stepByStep"],
      planningSupport: "collaborative")
    let book = try XCTUnwrap(store.community.first { $0.id == "community_genetics" })
    store.courseFeatureStartDraft(catalogID: book.id)
    store.draft.diagnosis = ["goals": ["exam"]]
    try await store.importCloudCommunity(book)
    XCTAssertEqual(store.lessons.count, 15)
    XCTAssertEqual(store.currentSource?.flashcards.count, 90)
    XCTAssertEqual(store.currentSource?.quizzes.count, 45)
    XCTAssertTrue(store.quizzes.allSatisfy { $0.answer.isEmpty })
    XCTAssertTrue(store.plan?.tasks.allSatisfy { $0.status == "pending" } == true)
    let lesson = try XCTUnwrap(store.lessons.first { $0.id == "lesson_meiosis" })
    store.openLesson(lesson)
    XCTAssertEqual(store.route, .lesson)
    store.saveLocation(page: 12)
    store.completeLesson()
    await store.flush()
    XCTAssertNil(store.cloudState.syncError)
    store.openSource(
      SourcePageTarget(bookID: "book_biology_2", title: lesson.title, page: 12, from: .lesson))
    XCTAssertEqual(
      store.route, .source,
      "Published original-page images do not require an unavailable PDF download")
    let question = try XCTUnwrap(store.quizzes.first)
    let original = try ContentBundle.load([SourceContent].self, name: "sources").first {
      $0.id == "book_biology_2"
    }
    let answerKey = try XCTUnwrap(original?.quizzes.first { $0.id == question.id }?.answer)
    let incorrect = try XCTUnwrap(question.choices.first { $0 != answerKey })
    await store.submitCloudAssignment(question: question, answer: incorrect)
    XCTAssertNotNil(store.latestDiagnosis)
    await store.flush()
    let card = try XCTUnwrap(store.flashcards.first)
    let resource = try XCTUnwrap(store.cloudResource)
    store.reviewFlashcard(card, mastered: false, seconds: 8)
    await store.flush()
    XCTAssertNil(store.cloudState.syncError)
    XCTAssertTrue(store.cloudState.pendingReviews.isEmpty)
    XCTAssertEqual(store.cloudResource?.flashcard_reviews?[card.id]?.repetitions, 1)
    XCTAssertEqual(store.cloudResource?.flashcard_reviews?[card.id]?.last_rating, "again")
    XCTAssertNotNil(store.flashcards.first { $0.id == card.id }?.due)
    // A second client changes a different card, forcing the real 409/rebase path.
    var remote = try XCTUnwrap(store.cloudResource?.progress)
    remote.mastered_ids = [resource.book_id + "::remote-card"]
    let _: CloudProgress = try await api.request(
      resource.path + "/progress", method: "PUT", body: remote.body(mutationID: UUID().uuidString))
    store.reviewFlashcard(card, mastered: true, seconds: 10)
    await store.flush()
    XCTAssertNil(store.cloudState.syncError)
    XCTAssertTrue(store.isMastered(card.id))
    XCTAssertTrue(store.isMastered("remote-card"))
    XCTAssertEqual(store.cloudResource?.flashcard_reviews?[card.id]?.repetitions, 2)
    // Explicit local removal must survive another device adding a new mastered card.
    remote = try XCTUnwrap(store.cloudResource?.progress)
    remote.mastered_ids.append(resource.book_id + "::later-card")
    let _: CloudProgress = try await api.request(
      resource.path + "/progress", method: "PUT", body: remote.body(mutationID: UUID().uuidString))
    store.setMastery(card.id, mastered: false)
    await store.flush()
    XCTAssertFalse(store.isMastered(card.id))
    XCTAssertTrue(store.isMastered("remote-card"))
    XCTAssertTrue(store.isMastered("later-card"))
    let restored = AppStore(
      repository: try LocalBookCourseRepository(directory: root.appendingPathComponent("fresh")),
      cloud: CloudAPI(baseURL: base))
    await restored.boot()
    XCTAssertEqual(restored.cloudResource?.flashcard_reviews?[card.id]?.repetitions, 2)
    XCTAssertFalse(restored.isMastered(card.id))
    XCTAssertTrue(restored.isMastered("remote-card"))
    XCTAssertEqual(restored.route, .home)
    XCTAssertEqual(restored.preferences?.primaryGoal, "exam")
    XCTAssertEqual(restored.preferences?.dailyTime, "30to60")
    XCTAssertEqual(restored.preferences?.learningBarriers, ["understanding", "memory"])
    XCTAssertEqual(restored.preferences?.guidanceStyles, ["examples", "stepByStep"])
    XCTAssertEqual(restored.preferences?.planningSupport, "collaborative")
    XCTAssertTrue(restored.preferences?.hasCompletedOnboarding == true)
    XCTAssertEqual(restored.lessons.count, 15)
    XCTAssertEqual(restored.locations[restored.locationKey]?.page, 12)
    XCTAssertTrue(
      restored.plan?.tasks.contains { $0.chapter_id == "c2s1" && $0.status == "done" } == true)
    XCTAssertFalse(restored.mistakes.isEmpty)
    await restored.flush()
    // Simulate closing the app after a durable offline review, before the HTTP send.
    restored.cloudState.isHydrating = true
    restored.reviewFlashcard(card, mastered: false, seconds: 11)
    restored.saveCloudCacheOnly()
    await restored.persistTask?.value
    let queuedID = try XCTUnwrap(restored.cloudState.pendingReviews.first?.eventID)
    let resumed = AppStore(
      repository: try LocalBookCourseRepository(directory: root.appendingPathComponent("fresh")),
      cloud: CloudAPI(baseURL: base))
    await resumed.boot()
    await resumed.flush()
    XCTAssertTrue(resumed.cloudState.pendingReviews.isEmpty)
    XCTAssertNil(resumed.cloudState.syncError)
    XCTAssertEqual(resumed.cloudResource?.flashcard_reviews?[card.id]?.repetitions, 3)
    // A lost HTTP response retries the same persisted event, without a fourth review.
    let duplicate: CloudReviewResult = try await api.request(
      resource.path + "/catalog-flashcards/\(card.id)", method: "POST",
      body: [
        "event_id": .string(queuedID), "rating": .string("again"), "response_seconds": .number(11),
      ])
    XCTAssertTrue(duplicate.duplicate)
    XCTAssertEqual(duplicate.review_state.repetitions, 3)
  }

  @MainActor func testNativeLoginUploadCourseQARestoresProgressFromServer() async throws {
    // Explicit loopback harness replaces external OCR/model adapters, never the app/API/storage.
    let base = URL(string: "http://127.0.0.1:18101")!
    let probeConfig = URLSessionConfiguration.ephemeral
    probeConfig.timeoutIntervalForRequest = 10
    probeConfig.connectionProxyDictionary = [:]
    let probe = URLSession(configuration: probeConfig)
    let healthURL = base.appendingPathComponent("api/native-test/health")
    guard let (health, _) = try? await probe.data(from: healthURL),
      let marker = try? JSONDecoder().decode([String: Bool].self, from: health),
      marker["fixture_adapters"] == true
    else {
      throw XCTSkip(
        "Start backend/tests/run_native_server.py with NATIVE_E2E_TEST_SERVER=1 for the local API integration test"
      )
    }
    let api = CloudAPI(baseURL: base)
    await api.clearCredentials()
    let challenge: CloudChallenge = try await api.request(
      "/api/auth/code", method: "POST",
      body: [
        "email": .string("reader@zhiwo.test"), "purpose": .string("register"),
      ])
    let _: CloudAccountState = try await api.request(
      "/api/auth/verify", method: "POST",
      body: [
        "challenge_id": .string(challenge.challenge_id),
        "code": .string(try XCTUnwrap(challenge.demo_code)),
        "registration": .object([
          "nickname": .string("原生流程验证"), "stage": .string("university"),
          "interests": .array([]), "goal": .string("exam"),
        ]),
      ])
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let store = AppStore(
      repository: try LocalBookCourseRepository(directory: root.appendingPathComponent("first")),
      cloud: api, processingPolicy: stableWiFiProcessingPolicyForTesting())
    await store.boot()
    XCTAssertTrue(store.cloudState.authenticated, store.cloudState.error ?? "")
    XCTAssertTrue(store.sources.isEmpty, "Online accounts must not load bundled demo textbooks")
    // The one-second probe only detects whether the optional harness is available.
    // Rendering the CJK PDF on a cold CI runner needs the normal integration budget.
    let pdfRequest = URLRequest(
      url: base.appendingPathComponent("api/native-test/pdf"), timeoutInterval: 20)
    let (pdf, _) = try await probe.data(for: pdfRequest)
    let file = root.appendingPathComponent("生物教材.pdf")
    try pdf.write(to: file)
    var progress: [CloudUploadProgress] = []
    await store.importFile(file) { progress.append($0) }
    XCTAssertNil(store.importError)
    XCTAssertEqual(progress.first?.totalBytes, Int64(pdf.count))
    XCTAssertTrue(progress.contains { $0.sentBytes > 0 }, "The real file upload must report sent bytes")
    XCTAssertEqual(store.draft.resourceIDs.count, 1)
    await store.processImport()
    XCTAssertNil(store.importError, store.importError ?? "")
    XCTAssertTrue(store.draft.parseCompleted)
    await store.confirmCloudChapters()
    XCTAssertTrue(store.cloudState.showDiagnosis, store.cloudState.error ?? "")
    XCTAssertTrue(store.cloudState.choosingLearningMode)
    await store.beginCloudLearning(chapterID: store.cloudState.requestedChapterID)
    for _ in 0..<12 {
      guard let interview = store.cloudState.interview, interview.phase != "complete" else { break }
      let selected =
        interview.turn.item != nil ? ["0"] : interview.turn.options.first.map { [$0.id] } ?? []
      await store.advanceCloudInterview(selected: selected, confidence: 0.6, seconds: 12)
      XCTAssertNil(store.cloudState.error, store.cloudState.error ?? "")
    }
    XCTAssertEqual(store.cloudState.interview?.phase, "complete")
    let lesson = try XCTUnwrap(store.activeLesson)
    XCTAssertEqual(lesson.status, "ready")
    XCTAssertFalse(lesson.blocks.isEmpty)
    XCTAssertTrue(
      store.quizzes.allSatisfy { $0.answer.isEmpty },
      "Server-owned answer keys must not be synthesized in the client")
    store.saveLocation(page: 2)
    store.completeLesson()
    await store.flush()
    await store.cloudState.syncTask?.value
    await store.flush()
    XCTAssertNil(store.cloudState.syncError, store.cloudState.syncError ?? "")
    XCTAssertTrue(store.cloudState.pending.isEmpty)
    await store.ask("减数分裂有什么特点？")
    let reply = try XCTUnwrap(store.chatMessages.last)
    XCTAssertEqual(reply.role, "assistant")
    XCTAssertFalse(reply.citations.isEmpty)
    let card = try XCTUnwrap(store.flashcards.first)
    store.reviewFlashcard(card, mastered: true, seconds: 14)
    await store.flush()
    XCTAssertNil(store.cloudState.syncError)
    XCTAssertEqual(store.cloudResource?.flashcard_reviews?[card.id]?.algorithm, "fsrs-6")
    XCTAssertEqual(store.cloudResource?.flashcard_reviews?[card.id]?.repetitions, 1)
    let bookID = try XCTUnwrap(store.selectedBookID)
    let workspaceID = try XCTUnwrap(store.selectedCourseID)
    store.selectedCourseID = nil
    let libraryAnswer = try await store.answerAssistant(
      question: "减数分裂有什么特点？", bookID: bookID, bookIDs: [bookID],
      chapterID: store.selectedChapterID, sources: store.sources, history: [])
    XCTAssertFalse(libraryAnswer.citations.isEmpty, "Single-book library QA must remain available")
    store.selectedCourseID = workspaceID
    let second: CloudWorkspace = try await api.request(
      "/api/workspaces", method: "POST",
      body: [
        "id": .string("workspace_second_selection"), "name": .string("第二门课程"),
      ])
    let _: CloudResource = try await api.request(
      "/api/workspaces/\(second.id)/resources", method: "POST", body: ["book_id": .string(bookID)])
    try await store.reloadCloud()
    store.selectCourse(second.id)
    await store.flush()
    store.selectCourse(workspaceID)
    await store.flush()
    // A new client and a new local directory prove account-cookie and server recovery.
    let restoredAPI = CloudAPI(baseURL: base)
    let restored = AppStore(
      repository: try LocalBookCourseRepository(directory: root.appendingPathComponent("second")),
      cloud: restoredAPI)
    await restored.boot()
    XCTAssertTrue(restored.cloudState.authenticated, restored.cloudState.error ?? "")
    XCTAssertEqual(restored.selectedCourseID, workspaceID)
    XCTAssertEqual(restored.selectedBookID, bookID)
    XCTAssertEqual(restored.locations[restored.locationKey]?.page, 2)
    XCTAssertTrue(restored.plan?.tasks.allSatisfy { $0.status == "done" } == true)
    XCTAssertEqual(restored.activeLesson?.id, lesson.id)
    try await restored.ensureCloudPDF(bookID: bookID)
    XCTAssertNotNil(restored.sourceFileURL(bookID))
    await restored.logoutCloud()
    XCTAssertFalse(restored.cloudState.authenticated)
    XCTAssertTrue(restored.sources.isEmpty)
    let state: CloudAccountState = try await restoredAPI.request("/api/auth/me")
    XCTAssertNil(state.account)
    await store.flush()
    await restored.flush()
  }
}

private final class OfflineCloudProtocol: URLProtocol, @unchecked Sendable {
  static let lock = NSLock()
  nonisolated(unsafe) static var handler: ((URLRequest) throws -> (Int, Data))?
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      Self.lock.lock()
      let callback = Self.handler
      Self.lock.unlock()
      let (status, bytes) = try callback!(request)
      let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type":"application/json"])!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: bytes)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() { }
  static func respond(_ callback: @escaping (URLRequest) throws -> (Int, Data)) {
    lock.lock(); handler = callback; lock.unlock()
  }
}

extension CloudTests {
  func testTailnetTransportRecoveryOnlyReplaysSafeRequestsAndPreservesImportIdentity() async throws {
    let cases: [(String, String, String, URLError.Code, Int)] = [
      (".ts.net", "GET", "/api/auth/me", .networkConnectionLost, 2),
      (".ts.net", "POST", "/api/native/catalog/community_genetics/import", .networkConnectionLost, 2),
      (".ts.net", "POST", "/api/auth/code", .networkConnectionLost, 1),
      (".ts.net", "POST", "/api/workspaces/w/resources/r/session", .networkConnectionLost, 1),
      (".ts.net", "GET", "/api/health", .serverCertificateUntrusted, 1),
      (".example", "GET", "/api/health", .networkConnectionLost, 1),
    ]
    for (suffix, method, path, failure, expectedCalls) in cases {
      var requests: [URLRequest] = []
      OfflineCloudProtocol.respond { request in
        requests.append(request)
        if requests.count == 1 { throw URLError(failure) }
        return (200, Data("{\"ok\":true}".utf8))
      }
      let api = CloudAPI(
        baseURL: URL(string: "https://transport-\(UUID().uuidString)\(suffix)")!,
        session: isolatedCloudSession())
      do {
        let result: CloudOK = try await api.request(path, method: method,
          body: method == "POST" ? ["id": .string("stable-workspace")] : nil)
        XCTAssertEqual(expectedCalls, 2, "\(method) \(path) must not be replayed")
        XCTAssertTrue(result.ok)
      } catch let error as CloudError {
        XCTAssertEqual(expectedCalls, 1, "A safe request should recover after a transient failure")
        XCTAssertTrue(error.transportFailure)
      }
      XCTAssertEqual(requests.count, expectedCalls)
      if requests.count == 2 {
        XCTAssertEqual(requests[0].url, requests[1].url)
        XCTAssertEqual(requests[0].httpBody, requests[1].httpBody)
      }
    }
    var calls = 0
    OfflineCloudProtocol.respond { _ in calls += 1; throw URLError(.timedOut) }
    let api = CloudAPI(baseURL: URL(string: "https://bounded-\(UUID().uuidString).ts.net")!, session: isolatedCloudSession())
    do {
      let _: CloudOK = try await api.request("/api/health")
      XCTFail("A persistent failure must remain visible")
    } catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    XCTAssertEqual(calls, 2, "Transport recovery is limited to one retry")
  }

  private func generatedResourceWithoutDailyPlan() -> CloudResource {
    let chapters = ["c1", "c2"].enumerated().map { index, id in
      CloudChapter(chapter_id: id, order: index, title: id, start_page: index + 1, end_page: index + 1,
        summary: "有原文依据的章节", knowledge_points: [], source_block_ids: [])
    }
    let courses = chapters.map {
      CloudCourse(course_id: "generated-" + $0.chapter_id, version: 1, chapter_id: $0.chapter_id,
        chapter_title: $0.title, opening: "测试用真实响应结构", summary: $0.summary,
        original_reading: [], knowledge_points: [], flashcards: [], worked_examples: [],
        checkpoint_questions: [], practice_items: [], estimated_minutes: 5,
        created_at: "2026-10-24T00:00:00Z", unresolved_source_warnings: [])
    }
    return CloudResource(id: "generated-resource", book_id: "uploaded-book", workspace_id: "generated-workspace",
      revision: 0, filename: "用户教材.pdf", status: "structured",
      structure: CloudStructure(title: "用户教材", summary: "", source_page_count: 2, chapters: chapters),
      courses: courses, progress: CloudProgress(revision: 4, completed_chapters: ["c1"],
        task_status: ["existing-server-task": "done"]))
  }

  @MainActor private func installGeneratedResource(_ resource: CloudResource, store: AppStore) {
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = "generated-no-plan"
    store.cloudState.workspaces = [CloudWorkspace(id: resource.workspace_id, name: "个人课程",
      preferences: [:], created: 0, updated: 0, resources: [resource])]
    store.rebuildCloudCourses()
    store.selectedCourseID = resource.workspace_id
    store.selectedBookID = resource.book_id
    store.selectedChapterID = "c2"
    store.selectedLessonID = "generated-c2"
    store.installCloudSources(workspaceID: resource.workspace_id)
    store.restoreCloudMastery()
  }

  @MainActor func testGeneratedLessonCompletionWithoutPlanSurvivesOfflineCacheReload() async throws {
    OfflineCloudProtocol.respond { _ in throw URLError(.notConnectedToInternet) }
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let api = CloudAPI(baseURL: URL(string: "https://completion-\(UUID().uuidString).example")!, session: isolatedCloudSession())
    let repo = try LocalBookCourseRepository(directory: directory)
    let store = AppStore(repository: repo, cloud: api)
    let resource = generatedResourceWithoutDailyPlan()
    installGeneratedResource(resource, store: store)
    XCTAssertTrue(store.plan?.tasks.isEmpty == true, "Reading completion must not fabricate a daily plan")
    store.completeLesson()
    await store.flush()
    XCTAssertEqual(store.completedChapterIDs, Set(["c1", "c2"]))
    XCTAssertTrue(store.mastered.isEmpty, "Reading a lesson is not evidence of knowledge mastery")
    let progress = try XCTUnwrap(store.cloudState.pending[resource.id]?.value)
    XCTAssertEqual(progress.completed_chapters, ["c1", "c2"])
    XCTAssertEqual(progress.task_status, ["existing-server-task": "done"])
    let loadedCache = try await repo.loadCloudState("generated-no-plan")
    let cached = try XCTUnwrap(loadedCache)
    let restored = AppStore(repository: try LocalBookCourseRepository(directory: directory), cloud: api)
    restored.restoreCloudCache(cached)
    restored.installCloudSources(workspaceID: resource.workspace_id)
    XCTAssertEqual(restored.completedChapterIDs, Set(["c1", "c2"]))
    XCTAssertEqual(restored.chapters.first { $0.id == "c2" }?.status, "completed")
    XCTAssertTrue(restored.plan?.tasks.isEmpty == true)
    var other = resource
    other.id = "other-resource"
    other.workspace_id = "other-workspace"
    other.progress = CloudProgress()
    restored.cloudState.workspaces.append(CloudWorkspace(id: other.workspace_id, name: "同书另一门课",
      preferences: [:], created: 0, updated: 0, resources: [other]))
    restored.selectedCourseID = other.workspace_id
    XCTAssertTrue(restored.completedChapterIDs.isEmpty, "Completion belongs to this course material, not a shared book")
    store.cloudState.retryTask?.cancel()
    restored.cloudState.retryTask?.cancel()
  }

  @MainActor func testGeneratedLessonCompletionReceivesServerAcknowledgementWithoutPlan() async throws {
    OfflineCloudProtocol.respond { request in
      if request.url?.path.hasSuffix("/progress") == true {
        let value: Any
        if let input = request.httpBody { value = try JSONSerialization.jsonObject(with: input) }
        else {
          let stream = try XCTUnwrap(request.httpBodyStream)
          stream.open()
          defer { stream.close() }
          value = try JSONSerialization.jsonObject(with: stream)
        }
        let body = try XCTUnwrap(value as? [String: Any])
        XCTAssertEqual(body["completed_chapters"] as? [String], ["c1", "c2"])
        XCTAssertEqual(body["task_status"] as? [String: String], ["existing-server-task": "done"])
        return (200, try JSONEncoder().encode(CloudProgress(revision: 5, chapter_id: "c2", lesson_id: "generated-c2",
          completed_chapters: ["c1", "c2"], task_status: ["existing-server-task": "done"])))
      }
      return (200, Data("{\"ok\":true}".utf8))
    }
    let api = CloudAPI(baseURL: URL(string: "https://ack-\(UUID().uuidString).example")!, session: isolatedCloudSession())
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(repository: repo, cloud: api)
    let resource = generatedResourceWithoutDailyPlan()
    installGeneratedResource(resource, store: store)
    store.completeLesson()
    await store.flush()
    XCTAssertTrue(store.cloudState.pending.isEmpty, store.cloudState.syncError ?? "Progress acknowledgement pending")
    XCTAssertEqual(store.cloudResource?.progress.revision, 5)
    XCTAssertEqual(store.completedChapterIDs, Set(["c1", "c2"]))
    store.saveLocation(page: 2)
    await store.flush()
    XCTAssertEqual(store.cloudResource?.progress.completed_chapters, ["c1", "c2"])
    XCTAssertTrue(store.plan?.tasks.isEmpty == true)
  }

  private func isolatedCloudSession() -> URLSession {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [OfflineCloudProtocol.self]
    return URLSession(configuration: config)
  }
  private func cachedAccount(_ id: String = "owner-a") -> CloudAccountState {
    CloudAccountState(account: .init(email: id + "@example.com", stage: "university", interests: [], goal: "interest", is_demo: false), user_id: id, email_available: true, demo_available: false)
  }
  @MainActor private func seedOfflineCache(_ repo: LocalBookCourseRepository, api: CloudAPI, account: CloudAccountState, origin: String? = nil) async throws -> CloudLocalSnapshot {
    var app = AppSnapshot()
    app.preferences = LearnerPreferences(displayName: "缓存学习者", primaryGoal: "interest", dailyTime: "30to60")
    app.selectedCourseID = "workspace"
    app.selectedBookID = "book"
    let row = CloudResource(id: "resource", book_id: "book", workspace_id: "workspace", revision: 0, filename: "教材.pdf", status: "ready", structure: CloudStructure(title: "离线课程", summary: "", source_page_count: 10, chapters: []), courses: [], progress: CloudProgress(page: 3))
    let state = CloudLocalSnapshot(snapshot: app, sources: [], workspaces: [CloudWorkspace(id: "workspace", name: "离线课程", preferences: [:], created: 0, updated: 0, resources: [row])], filePaths: [:], pending: [:], creationWorkspaceID: nil, notes: [StudyNote(id: "private-note", kind: .text, title: "私人笔记", body: "只能归属当前账号")], pendingSelection: nil, onboardingComplete: true, pendingReviews: [])
    let store = AppStore(repository: repo, cloud: api)
    try await repo.saveCloudState(state, key: store.cloudCacheKey(account, origin: origin))
    return state
  }

  @MainActor func testUploadBindingDoesNotDuplicateBookAndPreservesLocalAnchors() throws {
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(repository: repo)
    let pending = "book_local_filehash"
    let remote = CloudResource(id: "r", book_id: "server-book", workspace_id: "w", revision: 0,
      filename: "原件.pdf", status: "uploaded", courses: [], progress: CloudProgress())
    var local = CloudMapping.source(remote)
    local.summary.book_id = pending
    local.plan.book_id = pending
    store.sources = [CloudMapping.source(remote), local]
    store.courses = [Course(id: "local-course:filehash", name: "我的书", resourceIDs: [pending], activeResourceID: pending)]
    store.notes = [StudyNote(kind: .text, title: "本机笔记", body: "原稿",
      anchor: NoteAnchor(bookID: pending, page: 2))]
    store.locations["local-course:filehash::" + pending] = StudyLocation(page: 2)
    store.selectedBookID = pending
    store.sourceTarget = SourcePageTarget(bookID: pending, title: "原文", page: 2)
    store.importJob = ImportJob(bookIDs: [pending], status: .running)
    store.bindUploadedBook(localID: pending, uploaded: CloudUpload(book_id: "server-book", filename: "原件.pdf", status: "uploaded"))
    XCTAssertEqual(store.sources.map(\.id), ["server-book"])
    XCTAssertEqual(store.notes.first?.anchor?.bookID, "server-book")
    XCTAssertEqual(store.locations["local-course:filehash::server-book"]?.page, 2)
    XCTAssertEqual(store.sourceTarget?.bookID, "server-book")
    XCTAssertEqual(store.importJob?.bookIDs, ["server-book"])
  }

  @MainActor func testFailedUploadRetainsPrivateOriginalAcrossCacheReload() async throws {
    OfflineCloudProtocol.respond { _ in throw URLError(.notConnectedToInternet) }
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let repo = try LocalBookCourseRepository(directory: root)
    let api = CloudAPI(baseURL: URL(string: "https://local-original.example")!, session: isolatedCloudSession())
    let store = AppStore(repository: repo, cloud: api,
      processingPolicy: stableWiFiProcessingPolicyForTesting())
    let account = cachedAccount()
    let key = store.cloudCacheKey(account)
    store.cloudState.authenticated = true
    store.cloudState.account = account
    store.cloudState.cacheKey = key
    let original = Data("%PDF-1.4\nreal-original-bytes".utf8)
    let file = root.appendingPathComponent("用户原件.pdf")
    try original.write(to: file)
    await store.importFile(file)
    XCTAssertNotNil(store.importError)
    let id = try XCTUnwrap(store.sources.first?.id)
    let path = try XCTUnwrap(store.sourceFileURL(id))
    XCTAssertEqual(try Data(contentsOf: path), original)
    XCTAssertTrue(id.hasPrefix("book_local_"))
    XCTAssertEqual(store.courses.first?.resourceIDs, [id])
    store.rebuildCloudCourses()
    XCTAssertEqual(store.courses.first?.resourceIDs, [id], "Server refresh must preserve pending local imports")
    await store.flush()
    let cached = try await repo.loadCloudState(key)
    let restored = AppStore(repository: try LocalBookCourseRepository(directory: root), cloud: api)
    restored.restoreCloudCache(try XCTUnwrap(cached))
    XCTAssertEqual(restored.sources.map(\.id), [id])
    XCTAssertEqual(restored.courses.first?.resourceIDs, [id])
    XCTAssertEqual(try Data(contentsOf: XCTUnwrap(restored.sourceFileURL(id))), original)
  }

  @MainActor func testAddingPDFToExistingLocalCourseRetainsOriginalWithoutDuplicateCourse() async throws {
    OfflineCloudProtocol.respond { _ in throw URLError(.notConnectedToInternet) }
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let repo = try LocalBookCourseRepository(directory: root)
    let api = CloudAPI(baseURL: URL(string: "https://existing-course.example")!, session: isolatedCloudSession())
    let store = AppStore(repository: repo, cloud: api,
      processingPolicy: stableWiFiProcessingPolicyForTesting())
    store.cloudState.authenticated = true
    store.cloudState.account = cachedAccount()
    store.cloudState.cacheKey = store.cloudCacheKey(cachedAccount())
    let courseID = "local-course:existing"
    store.courses = [Course(id: courseID, name: "已有课程", resourceIDs: [])]
    store.draft.parseCompleted = true
    let original = Data("%PDF-1.4\nretained-original".utf8)
    let file = root.appendingPathComponent("新资料.pdf")
    try original.write(to: file)
    await store.importFile(file, targetCourseID: courseID)
    XCTAssertNotNil(store.importError)
    XCTAssertEqual(store.courses.map(\.id), [courseID])
    let localID = try XCTUnwrap(store.sources.first?.id)
    XCTAssertEqual(store.courses.first?.resourceIDs, [localID])
    XCTAssertFalse(store.draft.parseCompleted)
    XCTAssertEqual(try Data(contentsOf: XCTUnwrap(store.sourceFileURL(localID))), original)
    store.bindUploadedBook(localID: localID,
      uploaded: CloudUpload(book_id: "uploaded-book", filename: "新资料.pdf", status: "uploaded"))
    XCTAssertEqual(store.courses.map(\.id), [courseID])
    XCTAssertEqual(store.courses.first?.resourceIDs, ["uploaded-book"])
    await store.flush()
  }

  @MainActor func testOnlineCourseFailedAttachmentSurvivesReloadAndSuccessfulBindingRemovesPlaceholder() async throws {
    OfflineCloudProtocol.respond { _ in throw URLError(.notConnectedToInternet) }
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let repo = try LocalBookCourseRepository(directory: root)
    let api = CloudAPI(baseURL: URL(string: "https://online-attachment.example")!, session: isolatedCloudSession())
    let store = AppStore(repository: repo, cloud: api,
      processingPolicy: stableWiFiProcessingPolicyForTesting())
    let account = cachedAccount(), key = store.cloudCacheKey(cachedAccount())
    store.cloudState.authenticated = true; store.cloudState.account = account; store.cloudState.cacheKey = key
    let workspace = CloudWorkspace(id: "existing-online-course", name: "在线课程", preferences: [:],
      created: 0, updated: 0, resources: [])
    store.cloudState.workspaces = [workspace]
    store.rebuildCloudCourses()
    let file = root.appendingPathComponent("保留的原件.pdf")
    let original = Data("%PDF-1.4\nprivate-original".utf8)
    try original.write(to: file)
    await store.importFile(file, targetCourseID: workspace.id)
    let localID = try XCTUnwrap(store.sources.first?.id)
    await store.flush()
    let restored = AppStore(repository: try LocalBookCourseRepository(directory: root), cloud: api)
    let cached = try await repo.loadCloudState(key)
    restored.restoreCloudCache(try XCTUnwrap(cached))
    XCTAssertEqual(try Data(contentsOf: XCTUnwrap(restored.sourceFileURL(localID))), original)
    XCTAssertTrue(restored.courses.contains { $0.resourceIDs.contains(localID) })
    restored.bindUploadedBook(localID: localID,
      uploaded: CloudUpload(book_id: "server-attachment", filename: "原件.pdf", status: "uploaded"))
    let resource = CloudResource(id: "r", book_id: "server-attachment", workspace_id: workspace.id,
      revision: 0, filename: "原件.pdf", status: "uploaded", courses: [], progress: CloudProgress())
    restored.cloudState.workspaces[0].resources = [resource]
    restored.rebuildCloudCourses()
    XCTAssertEqual(restored.courses.map(\.id), [workspace.id])
    XCTAssertEqual(restored.courses.first?.resourceIDs, ["server-attachment"])
    await restored.flush()
  }

  @MainActor func testOfflineRestartMigratesLegacyOriginAndKeepsLocalEdits() async throws {
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let legacyURL = URL(string: "https://offline-\(UUID().uuidString).example:443/")!
    let account = cachedAccount()
    CloudAPI.seedLegacyIdentityForTesting(url: legacyURL, account: account)
    let api = CloudAPI(baseURL: legacyURL, session: isolatedCloudSession())
    _ = try await seedOfflineCache(repo, api: api, account: account, origin: legacyURL.absoluteString)
    OfflineCloudProtocol.respond { _ in throw URLError(.notConnectedToInternet) }
    let store = AppStore(repository: repo, cloud: api)
    await store.boot()
    XCTAssertTrue(store.cloudState.authenticated)
    XCTAssertFalse(store.isLoading)
    XCTAssertEqual(store.route, .home)
    XCTAssertEqual(store.courses.first?.name, "离线课程")
    XCTAssertEqual(store.notes.first?.id, "private-note")
    try await store.saveNote(StudyNote(id: "offline-new", kind: .text, title: "离线新增", body: "离线可写"))
    await store.flush()
    let saved = try await repo.loadCloudState(store.cloudCacheKey(account))
    XCTAssertEqual(saved?.notes?.count, 2)
    store.cloudState.retryTask?.cancel()
    await store.logoutCloud()
    let remembered = await api.verifiedAccount()
    XCTAssertNil(remembered)
    XCTAssertTrue(store.notes.isEmpty)
  }

  @MainActor func testExplicit401NullAccountAndBootstrap401CannotRestoreCachedIdentity() async throws {
    for mode in ["auth401", "null-account", "bootstrap401"] {
      let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
      let url = URL(string: "https://expired-\(UUID().uuidString).example")!
      let account = cachedAccount()
      CloudAPI.seedLegacyIdentityForTesting(url: url, account: account)
      let api = CloudAPI(baseURL: url, session: isolatedCloudSession())
      _ = try await seedOfflineCache(repo, api: api, account: account)
      OfflineCloudProtocol.respond { request in
        if mode == "auth401" || request.url!.path.contains("bootstrap") { return (401, Data("{\"detail\":\"expired\"}".utf8)) }
        var response = account
        if mode == "null-account" { response.account = nil }
        return (200, try JSONEncoder().encode(response))
      }
      let store = AppStore(repository: repo, cloud: api)
      await store.boot()
      XCTAssertFalse(store.cloudState.authenticated, mode)
      XCTAssertTrue(store.cloudState.workspaces.isEmpty, mode)
      XCTAssertTrue(store.sources.isEmpty, mode)
      XCTAssertTrue(store.notes.isEmpty, mode)
      XCTAssertNil(store.cloudState.cacheKey, mode)
      await store.flush()
      await api.clearCredentials()
    }
  }

  @MainActor func testOfflineAccountAndServiceSwitchNeverLoadsAnotherCache() async throws {
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let url = URL(string: "https://switch-\(UUID().uuidString).example")!
    let api = CloudAPI(baseURL: url, session: isolatedCloudSession())
    _ = try await seedOfflineCache(repo, api: api, account: cachedAccount())
    CloudAPI.seedLegacyIdentityForTesting(url: url, account: cachedAccount("owner-b"))
    OfflineCloudProtocol.respond { _ in throw URLError(.notConnectedToInternet) }
    for destination in [url, URL(string: "https://other-\(UUID().uuidString).example")!] {
      let store = AppStore(repository: repo, cloud: CloudAPI(baseURL: destination, session: isolatedCloudSession()))
      await store.boot()
      XCTAssertFalse(store.cloudState.authenticated)
      XCTAssertTrue(store.notes.isEmpty)
      XCTAssertTrue(store.courses.isEmpty)
    }
    await api.clearCredentials()
  }

  @MainActor func testSharedContentIsWrittenOnceAndProgressWritesOnlyChangedResource() async throws {
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let source = try ContentBundle.load([SourceContent].self, name: "sources")[0]
    let workspaces = (0..<20).map { index in
      CloudWorkspace(id: "w\(index)", name: "课程\(index)", preferences: [:], created: 0, updated: 0, resources: [CloudResource(id: "r\(index)", book_id: source.id, workspace_id: "w\(index)", revision: 0, filename: "教材", status: "ready", courses: [], progress: CloudProgress(), source_content: source, content_key: "builtin:test-version")])
    }
    var state = CloudLocalSnapshot(snapshot: AppSnapshot(), sources: [], workspaces: workspaces, filePaths: [:], pending: [:], creationWorkspaceID: nil)
    try await repo.saveCloudState(state, key: "owner")
    let first = await repo.writeMetricsForTesting()
    XCTAssertEqual(first.counts["cloud-content"], 1)
    await repo.resetWriteMetricsForTesting()
    state.workspaces[0].resources[0].progress.page = 2
    try await repo.saveCloudState(state, key: "owner")
    let changed = await repo.writeMetricsForTesting()
    XCTAssertNil(changed.counts["cloud-content"])
    XCTAssertEqual(changed.counts["cloud-resource"], 1)
    XCTAssertLessThan(changed.bytes, 4000)
    let restored = try await repo.loadCloudState("owner")
    XCTAssertEqual(restored?.workspaces[0].resources[0].progress.page, 2)
    XCTAssertEqual(restored?.workspaces[19].resources[0].source_content?.lessons.count, source.lessons.count)
  }
}

extension CloudTests {
  @MainActor func testManualSyncRetryKeepsFailedEditsThenRefreshesAfterAcknowledgement() async throws {
    let repo = try LocalBookCourseRepository(
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let api = CloudAPI(baseURL: URL(string: "https://retry-\(UUID().uuidString).example")!,
      session: isolatedCloudSession())
    let account = cachedAccount()
    let cached = try await seedOfflineCache(repo, api: api, account: account)
    let store = AppStore(repository: repo, cloud: api)
    store.restoreCloudCache(cached)
    store.cloudState.authenticated = true
    store.cloudState.account = account
    store.cloudState.cacheKey = store.cloudCacheKey(account)
    store.locations["workspace::book"] = StudyLocation(page: 4)
    let pending = CloudPendingProgress(workspaceID: "workspace", resourceID: "resource",
      value: CloudProgress(page: 4))
    store.cloudState.pending["resource"] = pending
    store.cloudState.syncError = "上一次同步失败"
    store.saveCloudCacheOnly()
    await store.persistTask?.value

    OfflineCloudProtocol.respond { request in
      XCTAssertTrue(request.url!.path.hasSuffix("/progress"), "Failed writes must not be hidden by a metadata refresh")
      return (503, Data("{\"detail\":\"暂时无法保存进度\"}".utf8))
    }
    await store.retryCloudConnection()
    await store.persistTask?.value
    XCTAssertFalse(store.cloudState.isWorking)
    XCTAssertNotNil(store.cloudState.syncError)
    XCTAssertEqual(store.cloudState.pending["resource"]?.mutationID, pending.mutationID)
    let saved = try await repo.loadCloudState(store.cloudCacheKey(account))
    XCTAssertEqual(saved?.pending["resource"]?.value.page, 4)

    var workspaces = cached.workspaces
    workspaces[0].name = "已恢复连接的课程"
    workspaces[0].resources[0].progress = CloudProgress(revision: 1, page: 4)
    let bootstrap = CloudBootstrap(workspaces: workspaces, selection: nil, contract_version: 1,
      library: [], learner_preferences: cached.snapshot.preferences, catalog: [])
    OfflineCloudProtocol.respond { request in
      switch request.url!.path {
      case "/api/native/bootstrap": return (200, try JSONEncoder().encode(bootstrap))
      case let path where path.hasSuffix("/progress"):
        return (200, try JSONEncoder().encode(CloudProgress(revision: 1, page: 4)))
      case "/api/native/selection": return (200, Data("{\"ok\":true}".utf8))
      default:
        XCTFail("Retry must only synchronize existing learning state: \(request.url!.path)")
        return (404, Data("{}".utf8))
      }
    }
    await store.retryCloudConnection()
    await store.flush()
    XCTAssertFalse(store.cloudState.isWorking)
    XCTAssertNil(store.cloudState.syncError)
    XCTAssertTrue(store.cloudState.pending.isEmpty)
    XCTAssertEqual(store.courses.first?.name, "已恢复连接的课程")
    XCTAssertEqual(store.locations["workspace::book"]?.page, 4)
    XCTAssertEqual(store.notes.first?.id, "private-note")
  }

  @MainActor func testOfflineQueueResumesAfterAuthenticatedNetworkRecovery() async throws {
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let url = URL(string: "https://recover-\(UUID().uuidString).example")!
    let account = cachedAccount()
    CloudAPI.seedLegacyIdentityForTesting(url: url, account: account)
    let api = CloudAPI(baseURL: url, session: isolatedCloudSession())
    let cached = try await seedOfflineCache(repo, api: api, account: account)
    OfflineCloudProtocol.respond { _ in throw URLError(.notConnectedToInternet) }
    let store = AppStore(repository: repo, cloud: api)
    store.cloudState.retryDelay = .milliseconds(50)
    await store.boot()
    store.locations["workspace::book"] = StudyLocation(page: 4)
    store.persistCloud()
    await store.persistTask?.value
    XCTAssertFalse(store.cloudState.pending.isEmpty)
    let bootstrap = CloudBootstrap(workspaces: cached.workspaces, selection: nil, contract_version: 1, library: [], learner_preferences: cached.snapshot.preferences, catalog: [])
    OfflineCloudProtocol.respond { request in
      switch request.url!.path {
      case "/api/auth/me": return (200, try JSONEncoder().encode(account))
      case "/api/native/bootstrap": return (200, try JSONEncoder().encode(bootstrap))
      case let path where path.hasSuffix("/progress"):
        return (200, try JSONEncoder().encode(CloudProgress(revision: 1, page: 4)))
      default: return (200, Data("{\"ok\":true}".utf8))
      }
    }
    for _ in 0..<100 {
      if store.cloudState.pending.isEmpty && store.cloudState.syncError == nil { break }
      try await Task.sleep(for: .milliseconds(10))
    }
    XCTAssertTrue(store.cloudState.authenticated)
    XCTAssertTrue(store.cloudState.pending.isEmpty, store.cloudState.syncError ?? "")
    XCTAssertEqual(store.cloudState.workspaces[0].resources[0].progress.page, 4)
    store.cloudState.retryTask?.cancel()
    await store.flush()
    await api.clearCredentials()
  }
}

extension CloudTests {
  func testNonDefaultServicePortsNeverShareCredentialOrCacheOrigins() async throws {
    let host = "ports-\(UUID().uuidString.lowercased()).example"
    let standard = URL(string: "http://\(host):80/")!
    CloudAPI.seedLegacyIdentityForTesting(url: standard, account: cachedAccount())
    let api = CloudAPI(baseURL: URL(string: "http://\(host):8100")!, session: isolatedCloudSession())
    let identity = await api.verifiedAccount()
    XCTAssertNil(identity)
    let own = Set(api.cacheOrigins)
    XCTAssertTrue(own.contains("http://\(host):8100/"))
    XCTAssertTrue(own.isDisjoint(with: CloudAPI.compatibleOrigins(URL(string: "http://\(host)")!)))
    XCTAssertTrue(own.isDisjoint(with: CloudAPI.compatibleOrigins(URL(string: "http://\(host):8101")!)))
    let defaultAPI = CloudAPI(baseURL: standard, session: isolatedCloudSession())
    await defaultAPI.clearCredentials()
  }
}
