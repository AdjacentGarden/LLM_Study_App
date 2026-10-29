import Foundation
import XCTest

@testable import BookCourse

private final class StudyPlanProbe: @unchecked Sendable {
  let lock = NSLock()
  var handler: (URLRequest) throws -> (Int, Data) = { _ in throw URLError(.unsupportedURL) }
  private var recorded: [URLRequest] = []
  func respond(_ request: URLRequest) throws -> (Int, Data) {
    lock.lock(); recorded.append(request); let action = handler; lock.unlock()
    return try action(request)
  }
  var requests: [URLRequest] { lock.lock(); defer { lock.unlock() }; return recorded }
}

private final class StudyPlanProtocol: URLProtocol, @unchecked Sendable {
  private static let lock = NSLock()
  nonisolated(unsafe) private static var probes: [String: StudyPlanProbe] = [:]
  static func install(_ host: String, _ probe: StudyPlanProbe?) {
    lock.lock(); defer { lock.unlock() }; probes[host] = probe
  }
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      Self.lock.lock(); let probe = Self.probes[request.url?.host ?? ""]; Self.lock.unlock()
      let (status, data) = try XCTUnwrap(probe).respond(request)
      let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
        headerFields: ["Content-Type": "application/json"])!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: data)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

@MainActor final class CloudStudyPlanTests: XCTestCase {
  private let day = "2026-10-28"
  private func wire(done: Bool = false, confirm: Bool = false, budget: Int = 20) -> Data {
    Data("""
    {"plan_id":"plan_real","day":"2026-10-28","budget_minutes":\(budget),"estimated_minutes":4,
    "needs_confirmation":\(confirm),"confirmed":\(!confirm),"revision":1,
    "preferences_changed":false,"remaining_task_count":\(done ? 0 : 1),
    "tasks":[{"task_id":"learn:resource:point","resource_id":"resource","book_id":"book",
    "chapter_id":"chapter","point_id":"point","title":"向量的含义","kind":"learn","minutes":4,
    "status":"\(done ? "done" : "pending")","requires":[]}],"queue":[]}
    """.utf8)
  }
  private func fixture() throws -> (AppStore, StudyPlanProbe, URLSession, String) {
    let probe = StudyPlanProbe(), host = UUID().uuidString.lowercased() + ".test"
    StudyPlanProtocol.install(host, probe)
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [StudyPlanProtocol.self]
    let session = URLSession(configuration: config)
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(repository: repo, cloud: CloudAPI(baseURL: URL(string: "http://\(host)")!, session: session))
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = host
    store.cloudState.workspaces = [CloudWorkspace(id: "workspace", name: "数学课", preferences: [:],
      created: 0, updated: 0, resources: [CloudResource(id: "resource", book_id: "book", workspace_id: "workspace",
        revision: 0, filename: "教材.pdf", status: "ready", courses: [], progress: CloudProgress())])]
    store.selectedCourseID = "workspace"
    store.selectedBookID = "book"
    return (store, probe, session, host)
  }

  func testLoadsServerBudgetAndExplicitLocalDateWithoutLegacyTaskFabrication() async throws {
    let (store, probe, session, host) = try fixture()
    defer { StudyPlanProtocol.install(host, nil); session.invalidateAndCancel() }
    let response = wire(budget: 90)
    probe.handler = { request in
      XCTAssertEqual(request.url?.path, "/api/workspaces/workspace/study-plan")
      XCTAssertEqual(request.url?.query, "day=2026-10-28")
      return (200, response)
    }
    await store.loadDailyStudyPlan(day: day)
    XCTAssertEqual(store.dailyStudyPlan(day)?.budget_minutes, 90)
    XCTAssertEqual(store.dailyStudyPlan(day)?.tasks.first?.point_id, "point")
    XCTAssertTrue(store.sources.isEmpty, "Server daily tasks must not be written into a resource's legacy plan")
    store.selectedCourseID = "another-course"
    XCTAssertNil(store.dailyStudyPlan(day))
  }

  func testConfirmationSendsServerRevisionAndSelectedTaskIDs() async throws {
    let (store, probe, session, host) = try fixture()
    defer { StudyPlanProtocol.install(host, nil); session.invalidateAndCancel() }
    let proposed = wire(confirm: true), saved = wire()
    probe.handler = { request in (200, request.httpMethod == "POST" ? saved : proposed) }
    await store.loadDailyStudyPlan(day: day)
    await store.confirmDailyStudyPlan(day: day)
    let request = try XCTUnwrap(probe.requests.last)
    XCTAssertEqual(request.url?.path, "/api/workspaces/workspace/study-plan/confirm")
    let body = try body(request)
    XCTAssertEqual(body["revision"] as? Int, 1)
    XCTAssertEqual(body["task_ids"] as? [String], ["learn:resource:point"])
    XCTAssertEqual(store.dailyStudyPlan(day)?.needs_confirmation, false)
  }

  func testCompletionAcknowledgementRefreshesPlanWithoutCompletingWholeChapter() async throws {
    let (store, probe, session, host) = try fixture()
    defer { StudyPlanProtocol.install(host, nil); session.invalidateAndCancel() }
    let pending = wire(), done = wire(done: true)
    let sent = StudyPlanProbe()
    sent.handler = { _ in (200, Data()) }
    probe.handler = { request in
      if request.httpMethod == "POST" {
        _ = try sent.respond(request)
        return (200, Data(#"{"ok":true,"duplicate":false}"#.utf8))
      }
      return (200, sent.requests.isEmpty ? pending : done)
    }
    await store.loadDailyStudyPlan(day: day)
    let task = try XCTUnwrap(store.dailyStudyPlan(day)?.tasks.first)
    await store.completeDailyStudyTask(task, day: day)
    XCTAssertEqual(store.dailyStudyPlan(day)?.completedCount, 1)
    XCTAssertTrue(store.dailyPlanState.pending.isEmpty)
    XCTAssertEqual(store.cloudResource?.progress.completed_chapters, [])
    XCTAssertEqual(probe.requests.filter { $0.httpMethod == "POST" }.count, 1)
    XCTAssertEqual(try body(sent.requests[0])["day"] as? String, day)
    await store.completeDailyStudyTask(task, day: day)
    XCTAssertEqual(probe.requests.filter { $0.httpMethod == "POST" }.count, 1,
      "A stale card or delayed reader callback must not send an acknowledged task again")
  }

  func testUnconfirmedProposalCannotBeMarkedComplete() async throws {
    let (store, probe, session, host) = try fixture()
    defer { StudyPlanProtocol.install(host, nil); session.invalidateAndCancel() }
    let response = wire(confirm: true)
    probe.handler = { _ in (200, response) }
    await store.loadDailyStudyPlan(day: day)
    await store.completeDailyStudyTask(try XCTUnwrap(store.dailyStudyPlan(day)?.tasks.first), day: day)
    XCTAssertTrue(store.dailyPlanState.pending.isEmpty)
    XCTAssertTrue(probe.requests.allSatisfy { $0.httpMethod == "GET" })
  }

  func testReviewAcknowledgementCompletesAttachedPlanTask() async throws {
    let (store, probe, session, host) = try fixture()
    defer { StudyPlanProtocol.install(host, nil); session.invalidateAndCancel() }
    let pending = wire(), done = wire(done: true)
    let completions = StudyPlanProbe()
    completions.handler = { _ in (200, Data()) }
    probe.handler = { request in
      if request.url?.path == "/api/review-card" {
        return (200, Data(#"{"duplicate":false,"review_state":{"algorithm":"fsrs","repetitions":1}}"#.utf8))
      }
      if request.httpMethod == "POST" {
        _ = try completions.respond(request)
        return (200, Data(#"{"ok":true,"duplicate":false}"#.utf8))
      }
      return (200, completions.requests.isEmpty ? pending : done)
    }
    await store.loadDailyStudyPlan(day: day)
    store.cloudState.pendingReviews = [CloudPendingReview(workspaceID: "workspace", resourceID: "resource",
      cardID: "card", path: "/api/review-card", rating: "good", seconds: 10,
      planCompletion: CloudPlanCompletion(workspaceID: "workspace", day: day, taskID: "learn:resource:point"))]
    store.saveCloudCacheOnly()
    store.startCloudSync()
    await store.flush()
    XCTAssertTrue(store.cloudState.pendingReviews.isEmpty)
    XCTAssertTrue(store.dailyPlanState.pending.isEmpty)
    XCTAssertEqual(store.dailyStudyPlan(day)?.completedCount, 1)
    XCTAssertEqual(probe.requests.filter { $0.httpMethod == "POST" }.map { $0.url!.path },
      ["/api/review-card", "/api/workspaces/workspace/study-plan/tasks/learn:resource:point/complete"])
  }

  func testLostResponsePersistsIntentAndRetriesSameEventAfterCacheRestore() async throws {
    let (store, probe, session, host) = try fixture()
    defer { StudyPlanProtocol.install(host, nil); session.invalidateAndCancel() }
    let pending = wire(), done = wire(done: true)
    probe.handler = { request in
      if request.httpMethod == "POST" { return (503, Data(#"{"detail":"暂不可用"}"#.utf8)) }
      return (200, pending)
    }
    await store.loadDailyStudyPlan(day: day)
    await store.completeDailyStudyTask(try XCTUnwrap(store.dailyStudyPlan(day)?.tasks.first), day: day)
    await store.persistTask?.value
    let loaded = try await store.repository.loadCloudState(host)
    let cached = try XCTUnwrap(loaded)
    let event = try XCTUnwrap(cached.pendingPlanCompletions?.first?.eventID)
    XCTAssertEqual(store.dailyStudyPlan(day)?.completedCount, 0, "Unacknowledged completion must remain pending")
    let restored = AppStore(repository: store.repository, cloud: store.cloud)
    restored.restoreCloudCache(cached)
    restored.cloudState.authenticated = true
    restored.cloudState.cacheKey = host
    probe.handler = { request in
      if request.httpMethod == "POST" { return (200, Data(#"{"ok":true,"duplicate":true}"#.utf8)) }
      return (200, done)
    }
    restored.startCloudSync()
    await restored.cloudState.syncTask?.value
    XCTAssertTrue(restored.dailyPlanState.pending.isEmpty)
    XCTAssertEqual(restored.dailyStudyPlan(day)?.completedCount, 1)
    let writes = probe.requests.filter { $0.httpMethod == "POST" }
    XCTAssertEqual(writes.count, 2)
    XCTAssertEqual(try body(writes[0])["event_id"] as? String, event)
    XCTAssertEqual(try body(writes[1])["event_id"] as? String, event)
  }

  func testLateResponseAfterAccountChangeCannotInstallOldPlan() async throws {
    let (store, probe, session, host) = try fixture()
    defer { StudyPlanProtocol.install(host, nil); session.invalidateAndCancel() }
    let started = expectation(description: "request started"), gate = DispatchSemaphore(value: 0)
    let response = wire()
    probe.handler = { _ in started.fulfill(); _ = gate.wait(timeout: .now() + 5); return (200, response) }
    let load = Task { await store.loadDailyStudyPlan(day: day) }
    await fulfillment(of: [started], timeout: 3)
    store.cloudState.epoch = UUID()
    store.dailyPlanState.reset()
    gate.signal()
    await load.value
    XCTAssertTrue(store.dailyPlanState.plans.isEmpty)
  }

  private func body(_ request: URLRequest) throws -> [String: Any] {
    var data = request.httpBody ?? Data()
    if data.isEmpty, let stream = request.httpBodyStream {
      stream.open(); defer { stream.close() }
      var buffer = [UInt8](repeating: 0, count: 4096)
      while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count <= 0 { break }
        data.append(contentsOf: buffer.prefix(count))
      }
    }
    return try JSONSerialization.jsonObject(with: data) as! [String: Any]
  }
}
