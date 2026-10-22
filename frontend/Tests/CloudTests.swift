import Foundation
import XCTest

@testable import BookCourse

final class CloudTests: XCTestCase {
  func testBuiltinBiologyCatalogSurvivesEmptyRemoteCatalogAndDeduplicatesRefresh() throws {
    let bundled = try BuiltinCourseCatalog.merging([])
    let biology = try XCTUnwrap(bundled.first { $0.id == "community_genetics" })
    XCTAssertEqual(biology.owner, "原项目已有课程")
    XCTAssertEqual(biology.flashcardCount, 90)
    XCTAssertTrue(biology.recommended == true)
    XCTAssertEqual(biology.progress, 0)
    XCTAssertNotNil(ContentBundle.image(biology.cover))
    var updated = biology
    updated.description = "服务端更新的课程说明"
    let refreshed = try BuiltinCourseCatalog.merging([updated, updated])
    XCTAssertEqual(refreshed.filter { $0.id == biology.id }.count, 1)
    XCTAssertEqual(refreshed.first { $0.id == biology.id }?.description, updated.description)
    XCTAssertEqual(try BuiltinCourseCatalog.merging([]).first { $0.id == biology.id }, biology)
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
    let restored = AppStore(
      repository: try LocalBookCourseRepository(directory: root.appendingPathComponent("fresh")),
      cloud: CloudAPI(baseURL: base))
    await restored.boot()
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
  }

  @MainActor func testNativeLoginUploadCourseQARestoresProgressFromServer() async throws {
    // Explicit loopback harness replaces external OCR/model adapters, never the app/API/storage.
    let base = URL(string: "http://127.0.0.1:18101")!
    let probeConfig = URLSessionConfiguration.ephemeral
    probeConfig.timeoutIntervalForRequest = 1
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
      cloud: api)
    await store.boot()
    XCTAssertTrue(store.cloudState.authenticated, store.cloudState.error ?? "")
    XCTAssertTrue(store.sources.isEmpty, "Online accounts must not load bundled demo textbooks")
    let (pdf, _) = try await probe.data(from: base.appendingPathComponent("api/native-test/pdf"))
    let file = root.appendingPathComponent("生物教材.pdf")
    try pdf.write(to: file)
    await store.importFile(file)
    XCTAssertNil(store.importError)
    XCTAssertEqual(store.draft.resourceIDs.count, 1)
    await store.processImport()
    XCTAssertNil(store.importError, store.importError ?? "")
    XCTAssertTrue(store.draft.parseCompleted)
    await store.confirmCloudChapters()
    XCTAssertTrue(store.cloudState.showDiagnosis, store.cloudState.error ?? "")
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
    let bookID = try XCTUnwrap(store.selectedBookID)
    let workspaceID = try XCTUnwrap(store.selectedCourseID)
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
