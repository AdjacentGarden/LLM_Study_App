import Foundation
import SQLite3
import XCTest

@testable import BookCourse

private final class CourseJobProtocol: URLProtocol, @unchecked Sendable {
  typealias Handler = @Sendable (URLRequest) throws -> (Int, Data)
  static let lock = NSLock()
  nonisolated(unsafe) static var handlers: [String: Handler] = [:]
  nonisolated(unsafe) static var errorCodes: [String: String] = [:]
  static func install(_ host: String, _ handler: Handler?, errorCode: String? = nil) {
    lock.withLock { handlers[host] = handler; errorCodes[host] = errorCode }
  }
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      guard let handler = Self.lock.withLock({ Self.handlers[request.url?.host ?? ""] }) else {
        throw URLError(.unsupportedURL)
      }
      let (status, data) = try handler(request)
      var headers = ["Content-Type": "application/json"]
      if let code = Self.lock.withLock({ Self.errorCodes[request.url?.host ?? ""] }) {
        headers["X-CloudPath-Error-Code"] = code
      }
      let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
        headerFields: headers)!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: data)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

private final class CourseRequests: @unchecked Sendable {
  let lock = NSLock()
  private var values: [(String, [String: Any])] = []
  private var urls: [URL] = []
  func append(_ request: URLRequest) throws -> Int {
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
    let body = data.isEmpty ? [:] : try JSONSerialization.jsonObject(with: data) as! [String: Any]
    return lock.withLock {
      values.append((request.httpMethod ?? "GET", body)); urls.append(request.url!); return values.count
    }
  }
  var all: [(String, [String: Any])] { lock.withLock { values } }
  var allURLs: [URL] { lock.withLock { urls } }
}

private actor CourseJobSignal {
  private var marked = false
  private var waiter: CheckedContinuation<Void, Never>?
  func mark() { marked = true; waiter?.resume(); waiter = nil }
  func wait() async { if !marked { await withCheckedContinuation { waiter = $0 } } }
}

private actor CoursePhaseUpdates {
  var stages: [String] = []
  func append(_ job: CloudCourseJob) { stages.append(job.stage) }
}

final class CourseJobServiceTests: XCTestCase, @unchecked Sendable {
  private func fixture(errorCode: String? = nil, _ handler: @escaping CourseJobProtocol.Handler) throws
    -> (CloudAPI, URLSession, String, LocalBookCourseRepository) {
    let host = "course-job-\(UUID().uuidString.lowercased()).example"
    CourseJobProtocol.install(host, handler, errorCode: errorCode)
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [CourseJobProtocol.self]
    let session = URLSession(configuration: config)
    let repository = try LocalBookCourseRepository(directory:
      FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    return (CloudAPI(baseURL: URL(string: "https://\(host)")!, session: session), session, host, repository)
  }
  private func job(_ status: String, chapter: String = "chapter", course: Bool = false) throws -> Data {
    var value: [String: Any] = ["job_id": "course_job_owned", "session_id": "session",
      "chapter_id": chapter, "status": status, "stage": status == "running" ? "generating" : status,
      "attempts": status == "queued" ? 0 : 1, "retryable": status == "failed"]
    if status == "failed" { value["error"] = "课程没有通过独立审核" }
    if course {
      value["course"] = ["course_id": "approved", "version": 1, "chapter_id": "chapter",
        "chapter_title": "真实章节", "opening": "导入", "summary": "总结", "original_reading": [],
        "knowledge_points": [["point_id": "p1", "title": "概念", "explanation": "原文解释",
          "mastery": 0.0, "citations": []]], "flashcards": [], "worked_examples": [],
        "checkpoint_questions": [], "practice_items": [["item_id": "practice", "point_id": "p1",
          "prompt": "说明理由", "response_type": "explanation", "options": [], "estimated_seconds": 30]],
        "estimated_minutes": 5, "created_at": "2026-10-24T00:00:00Z", "unresolved_source_warnings": []]
    }
    return try JSONSerialization.data(withJSONObject: value)
  }
  private func diskRequestIDs(_ directory: URL) throws -> [String] {
    var db: OpaquePointer?
    guard sqlite3_open_v2(directory.appendingPathComponent("bookcourse.sqlite").path, &db,
      SQLITE_OPEN_READONLY, nil) == SQLITE_OK else { throw URLError(.cannotOpenFile) }
    defer { sqlite3_close(db) }
    var statement: OpaquePointer?
    guard sqlite3_prepare_v2(db, "SELECT payload FROM records WHERE kind='cloud-course-job'", -1,
      &statement, nil) == SQLITE_OK else { throw URLError(.cannotDecodeRawData) }
    defer { sqlite3_finalize(statement) }
    var ids: [String] = []
    while sqlite3_step(statement) == SQLITE_ROW {
      let data = Data(bytes: sqlite3_column_blob(statement, 0)!, count: Int(sqlite3_column_bytes(statement, 0)))
      ids.append(try JSONDecoder().decode(CloudCourseJobReceipt.self, from: data).requestID)
    }
    return ids
  }

  func testPublishedReviewQuestionsRemainReadableTextWithoutInventedGradingIdentity() throws {
    let questions = [
      "有人认为有条件的结论可以省略前提。应如何判断这一说法？",
      "温度改变后，如何核对原来的测量判断能否使用？",
      "比较不同条件下的结果时，应先检查哪些适用前提？",
    ]
    var response = try JSONSerialization.jsonObject(with: job("succeeded", course: true)) as! [String: Any]
    var course = response["course"] as! [String: Any]
    course["checkpoint_questions"] = questions + ["", " \n" + questions[1] + "  ", questions[0]]
    response["course"] = course
    let dto = try JSONDecoder().decode(CloudCourseJob.self,
      from: JSONSerialization.data(withJSONObject: response))
    let published = try XCTUnwrap(dto.course)
    XCTAssertEqual(published.checkpoint_questions.prefix(3), questions[...])
    let resource = CloudResource(id: "resource", book_id: "book", workspace_id: "workspace",
      revision: 0, filename: "教材.pdf", status: "structured",
      structure: CloudStructure(title: "教材", summary: "章节原文", source_page_count: 3,
        chapters: [CloudChapter(chapter_id: "chapter", order: 1, title: "真实章节", start_page: 1,
          end_page: 3, summary: "原文摘要", knowledge_points: ["概念"], source_block_ids: [])]),
      courses: [published], progress: CloudProgress())
    let source = CloudMapping.source(resource)
    let blocks = try XCTUnwrap(source.lessons.first).blocks.filter { $0.block_type == "review_question" }
    XCTAssertEqual(blocks.map(\.content), questions, "Keep quoted/question roles verbatim, including a claim to evaluate")
    XCTAssertEqual(blocks.map(\.title), ["本章回顾问题 1（自测）", "本章回顾问题 2（自测）", "本章回顾问题 3（自测）"])
    XCTAssertEqual(Set(blocks.map(\.id)).count, 3)
    XCTAssertTrue(blocks.allSatisfy { $0.citations.isEmpty && $0.source_chunk_ids.isEmpty && $0.asset_ids.isEmpty })
    XCTAssertEqual(source.quizzes.map(\.id), ["practice"], "Only server-identified practice remains submit-capable")
    XCTAssertEqual(source.quizzes.first?.lesson_id, published.course_id)
    XCTAssertEqual(source.quizzes.first?.question_type, "short-answer")
    XCTAssertEqual(source.quizzes.first?.answer, "")
    XCTAssertEqual(source.quizzes.first?.explanation, "")
    let restored = try JSONDecoder().decode(CloudResource.self, from: JSONEncoder().encode(resource))
    XCTAssertEqual(CloudMapping.source(restored).lessons.first?.blocks.filter {
      $0.block_type == "review_question"
    }.map(\.id), blocks.map(\.id), "Cache reload must not add another copy or change identity")
  }

  func testIntentIsCommittedBeforePOSTAndCompletionUsesOnlyGET() async throws {
    let requests = CourseRequests()
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let repository = try LocalBookCourseRepository(directory: root)
    let (api, session, host, _) = try fixture { request in
      let count = try requests.append(request)
      XCTAssertEqual(request.timeoutInterval, 20)
      if request.httpMethod == "POST" {
        XCTAssertEqual(try self.diskRequestIDs(root), [requests.all[0].1["request_id"] as! String])
        XCTAssertEqual(requests.all[0].1["retry"] as? Bool, false)
        return (202, try self.job("queued"))
      }
      XCTAssertEqual(request.url?.path, "/api/interviews/session/course-jobs/course_job_owned")
      return (200, try self.job(count == 2 ? "running" : "succeeded", course: count > 2))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let result = try await CourseJobService(api: api, repository: repository, accountKey: "owner")
      .waitForCourse(sessionID: "session", chapterID: "chapter", pollInterval: .milliseconds(1)) { _ in }
    XCTAssertEqual(result.course_id, "approved")
    XCTAssertEqual(requests.all.map(\.0), ["POST", "GET", "GET"])
  }

  func testRunningPhaseMessagesAreUserFacingAndTerminalStatusWins() throws {
    let expected = ["planning": "整理本章重点", "writing": "编写课程",
      "reviewing": "检查教学内容", "refining": "完善教学内容"]
    for (phase, message) in expected {
      var response = try JSONSerialization.jsonObject(with: job("running")) as! [String: Any]
      response["stage"] = phase
      let current = try JSONDecoder().decode(CloudCourseJob.self,
        from: JSONSerialization.data(withJSONObject: response))
      XCTAssertTrue(current.isPending)
      XCTAssertTrue(current.message.contains(message))
      response["status"] = "succeeded"
      let done = try JSONDecoder().decode(CloudCourseJob.self,
        from: JSONSerialization.data(withJSONObject: response))
      XCTAssertFalse(done.isPending)
      XCTAssertTrue(done.message.contains("课程已生成"))
    }
    var response = try JSONSerialization.jsonObject(with: job("running")) as! [String: Any]
    response["stage"] = "PRIVATE_PROVIDER_PAYLOAD"
    let unknown = try JSONDecoder().decode(CloudCourseJob.self,
      from: JSONSerialization.data(withJSONObject: response))
    XCTAssertFalse(unknown.message.contains("PRIVATE"))
    XCTAssertTrue(unknown.isPending)
    response["stage"] = "ready"
    let notPublished = try JSONDecoder().decode(CloudCourseJob.self,
      from: JSONSerialization.data(withJSONObject: response))
    XCTAssertTrue(notPublished.isPending)
    XCTAssertFalse(notPublished.message.contains("课程已生成"))
  }

  func testInFlightPhasesAreObservedThroughGETWithoutAnotherSubmission() async throws {
    let requests = CourseRequests()
    let stages = ["queued", "planning", "writing", "reviewing", "refining", "reviewing", "ready"]
    let (api, session, host, repository) = try fixture { request in
      let index = try requests.append(request) - 1
      XCTAssertTrue(index < stages.count)
      let status = index == 0 ? "queued" : (index == stages.count - 1 ? "succeeded" : "running")
      var response = try JSONSerialization.jsonObject(
        with: self.job(status, course: status == "succeeded")) as! [String: Any]
      response["stage"] = stages[index]
      return (status == "succeeded" ? 200 : 202, try JSONSerialization.data(withJSONObject: response))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let updates = CoursePhaseUpdates()
    let result = try await CourseJobService(api: api, repository: repository, accountKey: "owner")
      .waitForCourse(sessionID: "session", chapterID: "chapter", pollInterval: .milliseconds(1)) {
        await updates.append($0)
      }
    let observed = await updates.stages
    XCTAssertEqual(observed, stages)
    XCTAssertEqual(result.course_id, "approved")
    XCTAssertEqual(requests.all.map(\.0), ["POST"] + Array(repeating: "GET", count: stages.count - 1))
    XCTAssertEqual(try diskRequestIDs(repository.directory).count, 1)
  }

  func testLostPOSTRestoresSameDurableRequestAndNeverRetriesFailedJob() async throws {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture { request in
      let count = try requests.append(request)
      if count == 1 { throw URLError(.timedOut) }
      return (200, try self.job("failed"))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let first = CourseJobService(api: api, repository: repository, accountKey: "owner")
    do { _ = try await first.current(sessionID: "session", chapterID: "chapter"); XCTFail("Expected lost reply") }
    catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    let reopened = try LocalBookCourseRepository(directory: repository.directory)
    let resumed = CourseJobService(api: api, repository: reopened, accountKey: "owner")
    let failed = try await resumed.current(sessionID: "session", chapterID: "chapter")
    XCTAssertEqual(failed.status, .failed)
    do { _ = try await resumed.waitForCourse(sessionID: "session", chapterID: "chapter") { _ in }; XCTFail("Must remain failed") }
    catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    XCTAssertEqual(requests.all.map(\.0), ["POST", "POST", "GET"])
    XCTAssertEqual(requests.all[0].1["request_id"] as? String, requests.all[1].1["request_id"] as? String)
    XCTAssertTrue(requests.all.filter { $0.0 == "POST" }.allSatisfy { ($0.1["retry"] as? Bool) == false })
  }

  func testLocalWriteFailurePreventsAnyNetworkCommand() async throws {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture { request in
      _ = try requests.append(request); return (202, try self.job("queued"))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    await repository.setWriteFaultForTesting(true)
    do {
      _ = try await CourseJobService(api: api, repository: repository, accountKey: "owner")
        .current(sessionID: "session", chapterID: "chapter")
      XCTFail("Unpersisted intent must not submit")
    } catch {}
    XCTAssertTrue(requests.all.isEmpty)
  }

  func testExplicitRetryArchivesHistoryAndLostReplyRecoversWithoutAnotherRetry() async throws {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture { request in
      let count = try requests.append(request)
      if count == 3 { throw URLError(.timedOut) }
      var value = try JSONSerialization.jsonObject(with: self.job("failed")) as! [String: Any]
      value["attempts"] = count >= 4 ? 2 : 1
      return (200, try JSONSerialization.data(withJSONObject: value))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let service = CourseJobService(api: api, repository: repository, accountKey: "owner")
    _ = try await service.current(sessionID: "session", chapterID: "chapter")
    do { _ = try await service.restart(sessionID: "session", chapterID: "chapter"); XCTFail("Lost retry reply") }
    catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    let reopened = try LocalBookCourseRepository(directory: repository.directory)
    let resumed = CourseJobService(api: api, repository: reopened, accountKey: "owner")
    let retried = try await resumed.current(sessionID: "session", chapterID: "chapter")
    XCTAssertEqual(retried.job_id, "course_job_owned")
    XCTAssertEqual(retried.attempts, 2)
    _ = try await resumed.current(sessionID: "session", chapterID: "chapter")
    let all = requests.all
    XCTAssertEqual(all.map(\.0), ["POST", "GET", "POST", "POST", "GET"])
    let original = all[0].1["request_id"] as? String
    let retry = all[2].1["request_id"] as? String
    XCTAssertTrue(original != retry)
    XCTAssertEqual(retry, all[3].1["request_id"] as? String)
    XCTAssertEqual(all[2].1["retry"] as? Bool, true)
    XCTAssertEqual(all[3].1["retry"] as? Bool, false)
    XCTAssertEqual(Set(try diskRequestIDs(repository.directory)), Set([original!, retry!]))
  }

  func testExhaustedFailureCannotBeRestartedEvenByExplicitAction() async throws {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture { request in
      _ = try requests.append(request)
      var value = try JSONSerialization.jsonObject(with: self.job("failed")) as! [String: Any]
      value["retryable"] = false
      return (200, try JSONSerialization.data(withJSONObject: value))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let service = CourseJobService(api: api, repository: repository, accountKey: "owner")
    _ = try await service.current(sessionID: "session", chapterID: "chapter")
    do { _ = try await service.restart(sessionID: "session", chapterID: "chapter"); XCTFail("Exhausted task") }
    catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    XCTAssertEqual(requests.all.map(\.0), ["POST", "GET"])
    XCTAssertEqual(try diskRequestIDs(repository.directory).count, 1)
  }

  func testCancellationStopsSleepAndReopenedServiceOnlyQueriesSavedJob() async throws {
    let requests = CourseRequests()
    let signal = CourseJobSignal()
    let (api, session, host, repository) = try fixture { request in
      _ = try requests.append(request); return (202, try self.job("running"))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let service = CourseJobService(api: api, repository: repository, accountKey: "owner")
    let task = Task {
      try await service.waitForCourse(sessionID: "session", chapterID: "chapter", pollInterval: .seconds(30)) {
        _ in await signal.mark()
      }
    }
    await signal.wait()
    task.cancel()
    do { _ = try await task.value; XCTFail("Cancellation must stop polling") } catch is CancellationError {}
    let reopened = try LocalBookCourseRepository(directory: repository.directory)
    _ = try await CourseJobService(api: api, repository: reopened, accountKey: "owner")
      .current(sessionID: "session", chapterID: "chapter")
    XCTAssertEqual(requests.all.map(\.0), ["POST", "GET"])
  }

  func testReceiptsAreIsolatedByAccountOriginAndChapter() async throws {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture { request in
      _ = try requests.append(request)
      return (202, try self.job("queued", chapter: requests.all.last?.1["chapter_id"] as? String ?? "chapter"))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    for (account, chapter) in [("owner-a", "chapter"), ("owner-b", "chapter"), ("owner-a", "other")] {
      _ = try await CourseJobService(api: api, repository: repository, accountKey: account)
        .current(sessionID: "session", chapterID: chapter)
    }
    let other = CloudAPI(baseURL: URL(string: "http://\(host):8100")!, session: session)
    _ = try await CourseJobService(api: other, repository: repository, accountKey: "owner-a")
      .current(sessionID: "session", chapterID: "chapter")
    XCTAssertEqual(requests.all.map(\.0), ["POST", "POST", "POST", "POST"])
    XCTAssertEqual(Set(try diskRequestIDs(repository.directory)).count, 4)
  }

  func testForeignJobAndSucceededWithoutCourseNeverPublish() async throws {
    for fault in ["foreign", "missing-course"] {
      let (api, session, host, repository) = try fixture { _ in
        (200, try self.job("succeeded", chapter: fault == "foreign" ? "other" : "chapter"))
      }
      defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
      do {
        _ = try await CourseJobService(api: api, repository: repository, accountKey: "owner")
          .waitForCourse(sessionID: "session", chapterID: "chapter") { _ in }
        XCTFail("Incomplete or foreign course must not publish")
      } catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    }
  }

  @MainActor func testAppStoreRequiresThePublishedCourseInTheReloadedResourceBeforeSuccess() async throws {
    for includePublished in [false, true] {
      let published = try XCTUnwrap(JSONDecoder().decode(CloudCourseJob.self,
        from: job("succeeded", course: true)).course)
      let resource = CloudResource(id: "resource", book_id: "book", workspace_id: "workspace",
        revision: 0, filename: "教材.pdf", status: "structured",
        structure: CloudStructure(title: "教材", summary: "章节原文", source_page_count: 3,
          chapters: [CloudChapter(chapter_id: "chapter", order: 1, title: "章节", start_page: 3,
            end_page: 3, summary: "真实原文", knowledge_points: [], source_block_ids: [])]),
        courses: includePublished ? [published] : [], progress: CloudProgress())
      let workspace = CloudWorkspace(id: "workspace", name: "课程", preferences: [:], created: 0,
        updated: 0, resources: [resource])
      let bootstrap = CloudBootstrap(workspaces: [workspace], contract_version: 1)
      let requests = CourseRequests()
      let (api, session, host, repository) = try fixture { request in
        _ = try requests.append(request)
        if request.url?.path == "/api/native/bootstrap" { return (200, try JSONEncoder().encode(bootstrap)) }
        if request.url?.path.contains("/course-jobs") == true {
          return (200, try self.job("succeeded", course: true))
        }
        throw URLError(.notConnectedToInternet)
      }
      defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
      let store = AppStore(repository: repository, cloud: api)
      store.cloudState.authenticated = true
      store.cloudState.cacheKey = "owner"
      store.cloudState.workspaces = [workspace]
      store.cloudState.diagnosisWorkspaceID = workspace.id
      store.cloudState.diagnosisResourceID = resource.id
      store.cloudState.requestedChapterID = "chapter"
      store.cloudState.showCompletionOnCompile = true
      store.route = .study
      store.cloudState.interview = CloudInterview(session_id: "session", phase: "complete",
        turn: CloudTurn(turn_id: "done", phase: "complete", message: "已完成诊断", options: [], progress: 1),
        profile: .object([:]))
      store.selectedCourseID = workspace.id
      store.selectedBookID = resource.book_id
      do {
        try await store.compileCloudChapter()
        XCTAssertTrue(includePublished)
        XCTAssertEqual(store.selectedLessonID, published.course_id)
        XCTAssertEqual(store.route, .study)
        XCTAssertFalse(store.cloudState.showDiagnosis)
      } catch let error as CloudError {
        XCTAssertFalse(includePublished)
        XCTAssertEqual(error.status, 409)
        XCTAssertTrue(store.route != .courseReady)
        XCTAssertTrue(store.cloudState.showDiagnosis)
      }
      XCTAssertEqual(requests.all.filter { $0.0 == "POST" && $0.1["chapter_id"] != nil }.count, 1)
      store.cloudState.syncTask?.cancel()
      store.cloudState.retryTask?.cancel()
      await store.flush()
    }
  }

  @MainActor func testImportResumeRetainsOriginalAndRetriesPreparationWithoutRepeatingOCR() async throws {
    let requests = CourseRequests()
    let structure = CloudStructure(title: "教材", summary: "真实目录", source_page_count: 3,
      chapters: [CloudChapter(chapter_id: "chapter", order: 1, title: "章节", start_page: 3,
        end_page: 3, summary: "原文", knowledge_points: [], source_block_ids: [])])
    let (api, session, host, repository) = try fixture { request in
      _ = try requests.append(request)
      switch request.url?.path {
      case "/api/books/book/status":
        return (200, try JSONEncoder().encode(CloudBookStatus(book_id: "book", status: "ocr_ready",
          progress: 1, current_step: "原文已保存", retryable: false, needs_human_review: false)))
      case "/api/books/book/prepare":
        if request.httpMethod == "POST" {
          let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
          XCTAssertEqual(query.first { $0.name == "retry" }?.value, "true")
          XCTAssertNotNil(UUID(uuidString: query.first { $0.name == "retry_request_id" }?.value ?? ""))
        }
        let submitted = requests.all.contains { $0.0 == "POST" }
        return (200, try JSONSerialization.data(withJSONObject: ["book_id": "book",
          "status": submitted ? "succeeded" : "failed", "stage": submitted ? "ready" : "diagnostics",
          "updated": submitted ? 2.0 : 1.0, "next_step": submitted ? 3 : 2]))
      case "/api/native/books/book/readiness":
        return (200, try JSONSerialization.data(withJSONObject: ["structure_ready": true, "rag_ready": true]))
      case "/api/books/book/diagnostics":
        return (200, try JSONSerialization.data(withJSONObject: ["book_id": "book", "item_count": 3,
          "chapter_count": 1, "choice_count": 3, "explanation_count": 0]))
      case "/api/books/book/structure": return (200, try JSONEncoder().encode(structure))
      case "/api/native/bootstrap":
        return (200, try JSONEncoder().encode(CloudBootstrap(workspaces: [], contract_version: 1)))
      default: XCTFail("Unexpected endpoint: \(request.url?.path ?? "")"); throw URLError(.unsupportedURL)
      }
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let original = Data("Saved original PDF bytes".utf8)
    let path = try await repository.saveAttachment(original, extension: "pdf")
    let store = AppStore(repository: repository, cloud: api,
      processingPolicy: stableWiFiProcessingPolicyForTesting())
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = "import-owner"
    store.draft.resourceIDs = ["book"]
    store.filePaths = ["book": path]
    store.sources = [CloudMapping.source(CloudResource(id: "staged:book", book_id: "book",
      workspace_id: "", revision: 0, filename: "原书.pdf", status: "ocr_ready",
      structure: nil, courses: [], progress: CloudProgress()))]
    store.courses = [Course(id: "local-course:book", name: "原书", resourceIDs: ["book"], activeResourceID: "book")]
    store.selectedBookID = "book"
    store.selectedCourseID = "local-course:book"
    await store.processImport()
    await store.flush()
    XCTAssertEqual(store.importJob?.status, .failed)
    XCTAssertEqual(store.importJob?.preparationFailed, true)
    XCTAssertFalse(store.draft.parseCompleted)
    XCTAssertFalse(requests.all.contains { $0.0 == "POST" })
    let loadedCache = try await repository.loadCloudState("import-owner")
    let cached = try XCTUnwrap(loadedCache)
    let restored = AppStore(repository: try LocalBookCourseRepository(directory: repository.directory), cloud: api,
      processingPolicy: stableWiFiProcessingPolicyForTesting())
    restored.cloudState.authenticated = true
    restored.cloudState.cacheKey = "import-owner"
    restored.restoreCloudCache(cached)
    XCTAssertEqual(restored.importJob?.preparationBookID, "book")
    await restored.processImport(retryPreparation: true)
    await restored.flush()
    XCTAssertTrue(restored.draft.parseCompleted)
    XCTAssertEqual(restored.importJob?.status, .completed)
    XCTAssertEqual(restored.sources.first { $0.id == "book" }?.chapters.count, 1)
    XCTAssertEqual(requests.all.filter { $0.0 == "POST" }.count, 1)
    XCTAssertEqual(try Data(contentsOf: repository.attachmentURL(path)), original)
    store.cloudState.retryTask?.cancel()
    restored.cloudState.retryTask?.cancel()
  }

  func testProductionLANDefaultRetainsManualVPNAndLoopbackConfiguration() {
    XCTAssertEqual(CloudAPI.defaultServer, "http://10.249.44.137:8100")
    XCTAssertNotNil(CloudAPI.validatedURL(CloudAPI.defaultServer))
    XCTAssertNotNil(CloudAPI.validatedURL("http://100.86.143.88:8100"))
    XCTAssertNotNil(CloudAPI.validatedURL("http://127.0.0.1:8100"))
  }

  private func diagnosisResource(sessionID: String? = nil) -> CloudResource {
    CloudResource(id: "resource", book_id: "book", workspace_id: "workspace",
      revision: 0, session_id: sessionID, filename: "教材.pdf", status: "structured",
      courses: [], progress: CloudProgress())
  }

  @MainActor private func diagnosisStore(api: CloudAPI, repository: LocalBookCourseRepository,
    sessionID: String? = nil) -> AppStore {
    let resource = diagnosisResource(sessionID: sessionID)
    let store = AppStore(repository: repository, cloud: api)
    store.cloudState.authenticated = true
    store.cloudState.workspaces = [CloudWorkspace(id: "workspace", name: "课程", preferences: [:],
      created: 0, updated: 0, resources: [resource])]
    store.selectedCourseID = "workspace"
    store.selectedBookID = "book"
    return store
  }

  @MainActor func testUncertainDiagnosisOnlyExplicitActionGrantsOneInFlightNonce() async throws {
    let requests = CourseRequests()
    let started = CourseJobSignal()
    let (api, session, host, repository) = try fixture(errorCode: "diagnostic_submission_uncertain") { request in
      _ = try requests.append(request)
      if request.httpMethod == "GET" {
        XCTAssertEqual(request.url?.path, "/api/workspaces/workspace/resources/resource")
        return (200, try JSONEncoder().encode(self.diagnosisResource()))
      }
      XCTAssertEqual(request.url?.path, "/api/workspaces/workspace/resources/resource/session")
      if request.url?.query == nil {
        return (409, try JSONSerialization.data(withJSONObject: ["detail": "上次诊断提交结果待确认，请确认后点击重试诊断"]))
      }
      Task { await started.mark() }
      Thread.sleep(forTimeInterval: 0.08)
      let interview = CloudInterview(session_id: "session", phase: "profile_confirmation",
        turn: CloudTurn(turn_id: "confirm", phase: "profile_confirmation", message: "请确认学习偏好",
          question: "是否确认？", response_type: "single_choice", options: [.init(id: "yes", label: "确认")],
          item: nil, why_asked: nil, progress: 0.5), profile: .object([:]))
      return (200, try JSONEncoder().encode(interview))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let store = diagnosisStore(api: api, repository: repository)
    await store.beginCloudLearning(chapterID: "chapter")
    XCTAssertTrue(store.cloudState.showDiagnosis)
    XCTAssertTrue(store.canRetryCloudDiagnosis)
    // Recovery must be GET-only; even POST/session without retry may start
    // unstarted chapters on the backend.
    let originalError = store.cloudState.error
    await store.restoreCloudDiagnosis()
    XCTAssertEqual(store.cloudState.error, originalError)
    XCTAssertTrue(store.canRetryCloudDiagnosis)
    let retry = Task { @MainActor in await store.beginCloudLearning(chapterID: "chapter", retryDiagnosis: true) }
    await started.wait()
    XCTAssertTrue(store.cloudState.isWorking)
    XCTAssertFalse(store.canRetryCloudDiagnosis)
    await store.beginCloudLearning(chapterID: "chapter", retryDiagnosis: true)
    await retry.value
    XCTAssertEqual(requests.all.count, 3)
    XCTAssertEqual(requests.all.map(\.0), ["POST", "GET", "POST"])
    XCTAssertEqual(store.cloudState.interview?.session_id, "session")
    XCTAssertFalse(store.canRetryCloudDiagnosis)
    XCTAssertTrue(requests.all.map(\.1).allSatisfy(\.isEmpty))
    XCTAssertTrue(requests.allURLs.prefix(2).allSatisfy { $0.query == nil })
    let query = URLComponents(url: requests.allURLs[2], resolvingAgainstBaseURL: false)!.queryItems!
    XCTAssertEqual(Set(query.map(\.name)), ["retry", "retry_request_id"])
    XCTAssertEqual(query.first { $0.name == "retry" }?.value, "true")
    XCTAssertNotNil(UUID(uuidString: query.first { $0.name == "retry_request_id" }?.value ?? ""))
  }

  @MainActor func testDiagnosisLookupOnlyReadsAndRestoresALaterAttachedCompletedSession() async throws {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture(errorCode: "diagnostic_submission_uncertain") { request in
      let count = try requests.append(request)
      if request.httpMethod == "POST" {
        XCTAssertEqual(count, 1, "Only the initial start is allowed to POST")
        return (409, try JSONSerialization.data(withJSONObject: ["detail": "上次诊断提交结果待确认"]))
      }
      if request.url?.path == "/api/workspaces/workspace/resources/resource" {
        return (200, try JSONEncoder().encode(self.diagnosisResource(sessionID: count > 2 ? "session" : nil)))
      }
      XCTAssertEqual(request.url?.path, "/api/interviews/session")
      let interview = CloudInterview(session_id: "session", phase: "complete",
        turn: CloudTurn(turn_id: "complete", phase: "complete", message: "诊断已完成",
          question: nil, response_type: nil, options: [], item: nil, why_asked: nil, progress: 1),
        profile: .object([:]))
      return (200, try JSONEncoder().encode(interview))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let store = diagnosisStore(api: api, repository: repository)
    await store.beginCloudLearning(chapterID: "chapter")
    let originalError = store.cloudState.error
    await store.restoreCloudDiagnosis()
    XCTAssertTrue(store.canRetryCloudDiagnosis)
    XCTAssertEqual(store.cloudState.error, originalError)
    XCTAssertNil(store.cloudState.interview)
    await store.restoreCloudDiagnosis()
    XCTAssertEqual(requests.all.map(\.0), ["POST", "GET", "GET", "GET"])
    XCTAssertTrue(requests.allURLs.allSatisfy { $0.query == nil })
    XCTAssertEqual(store.cloudState.interview?.session_id, "session")
    XCTAssertEqual(store.cloudState.interview?.phase, "complete")
    XCTAssertEqual(store.cloudResource?.session_id, "session")
    XCTAssertNil(store.cloudState.error)
    XCTAssertNil(store.cloudState.courseJob, "Read-only recovery must not start course generation either")
    XCTAssertFalse(store.canRetryCloudDiagnosis)
  }

  @MainActor func testGenericConflictAndAttachedOrChangedResourceCannotGrantDiagnosisRetry() async throws {
    let cases: [(Int, String?, String?)] = [
      (401, nil, nil),
      (409, nil, nil), (409, "revision_conflict", nil),
      (503, nil, nil), (503, "unrelated_service_failure", nil),
      (409, "diagnostic_generation_rejected", nil), (502, "diagnostic_submission_uncertain", nil),
      (409, "diagnostic_submission_uncertain", "already-attached"),
      (502, "diagnostic_generation_rejected", "already-attached"),
    ]
    for (status, code, sessionID) in cases {
      let requests = CourseRequests()
      let (api, session, host, repository) = try fixture(errorCode: code) { request in
        _ = try requests.append(request)
        return (status, try JSONSerialization.data(withJSONObject: ["detail": "冲突，需要查询"]))
      }
      defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
      let store = diagnosisStore(api: api, repository: repository,
        sessionID: sessionID)
      await store.beginCloudLearning(chapterID: "chapter")
      XCTAssertFalse(store.canRetryCloudDiagnosis)
      XCTAssertFalse(store.cloudState.isWorking, "An epoch change on expired auth must not leave controls busy")
      await store.beginCloudLearning(chapterID: "chapter", retryDiagnosis: true)
      XCTAssertEqual(requests.all.count, 1)
      store.cloudState.diagnosisRetryAvailable = true
      store.cloudState.diagnosisRetryEpoch = store.cloudState.epoch
      store.selectedBookID = "another-book"
      XCTAssertFalse(store.canRetryCloudDiagnosis)
      await store.beginCloudLearning(chapterID: "chapter", retryDiagnosis: true)
      XCTAssertEqual(requests.all.count, 1)
      store.selectedBookID = "book"
      store.cloudState.epoch = UUID()
      XCTAssertFalse(store.canRetryCloudDiagnosis)
      await store.beginCloudLearning(chapterID: "chapter", retryDiagnosis: true)
      XCTAssertEqual(requests.all.count, 1)
    }
  }

  @MainActor func testKnownRejectedOrNotAdmittedDiagnosisCanOnlyRestartByExplicitNonceAction() async throws {
    for (status, code) in [(502, "diagnostic_generation_rejected"), (503, "diagnostic_generation_not_admitted")] {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture(errorCode: code) { request in
      let count = try requests.append(request)
      if count == 1 {
        return (status, try JSONSerialization.data(withJSONObject: ["detail": "诊断题尚未完成"]))
      }
      if request.httpMethod == "GET" {
        return (200, try JSONEncoder().encode(self.diagnosisResource()))
      }
      let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
      XCTAssertEqual(query.first { $0.name == "retry" }?.value, "true")
      XCTAssertNotNil(UUID(uuidString: query.first { $0.name == "retry_request_id" }?.value ?? ""))
      let interview = CloudInterview(session_id: "session", phase: "profile_confirmation",
        turn: CloudTurn(turn_id: "confirm", phase: "profile_confirmation", message: "请确认学习偏好",
          question: "是否确认？", response_type: "single_choice", options: [], item: nil,
          why_asked: nil, progress: 0.5), profile: .object([:]))
      return (200, try JSONEncoder().encode(interview))
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let store = diagnosisStore(api: api, repository: repository)
    await store.beginCloudLearning(chapterID: "chapter")
    XCTAssertTrue(store.canRetryCloudDiagnosis)
    let error = store.cloudState.error
    await store.restoreCloudDiagnosis()
    XCTAssertEqual(store.cloudState.error, error)
    XCTAssertTrue(store.canRetryCloudDiagnosis)
    await store.beginCloudLearning(chapterID: "chapter", retryDiagnosis: true)
    XCTAssertEqual(requests.all.map(\.0), ["POST", "GET", "POST"])
    XCTAssertEqual(store.cloudState.interview?.session_id, "session")
    XCTAssertFalse(store.canRetryCloudDiagnosis)
    }
  }

  func testPreviewReadinessRequiresExplicitPurposeAndSurvivesResourceCache() throws {
    let turn = CloudTurn(turn_id: "preview", phase: "preview_ready", message: "未做诊断",
      options: [], progress: 0)
    let preview = CloudInterview(session_id: "session", phase: "preview_ready", turn: turn,
      profile: .object([:]), purpose: "preview")
    XCTAssertTrue(preview.canGenerateCourse)
    let unmarked = CloudInterview(session_id: "session", phase: "preview_ready", turn: turn,
      profile: .object([:]))
    XCTAssertFalse(unmarked.canGenerateCourse, "An unknown phase cannot bypass learning preparation")
    var inconsistent = preview
    inconsistent.phase = "complete"
    XCTAssertFalse(inconsistent.canGenerateCourse, "A preview cannot claim a completed diagnosis")
    inconsistent.purpose = "unknown"
    XCTAssertFalse(inconsistent.canGenerateCourse)
    var resource = diagnosisResource(sessionID: "session")
    resource.session_purpose = "preview"
    let decoded = try JSONDecoder().decode(CloudResource.self, from: JSONEncoder().encode(resource))
    XCTAssertTrue(CloudContent(resource: decoded).applying(to: decoded).isPreview)
    let legacy = Data("""
      {"session_id":"old","phase":"complete","turn":{"turn_id":"done", "phase":"complete",
       "message":"完成","options":[],"progress":1},"profile":{}}
      """.utf8)
    let restored = try JSONDecoder().decode(CloudInterview.self, from: legacy)
    XCTAssertTrue(restored.canGenerateCourse)
    XCTAssertFalse(restored.isPreview)
  }

  @MainActor func testNewResourceOffersPreviewWithoutSubmittingDiagnosisOrCreatingEvidence() async throws {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture { request in
      _ = try requests.append(request)
      throw URLError(.unsupportedURL)
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let store = diagnosisStore(api: api, repository: repository)
    store.requestCloudLearning(chapterID: "chapter")
    XCTAssertTrue(store.cloudState.choosingLearningMode)
    XCTAssertTrue(store.cloudState.showDiagnosis)
    XCTAssertEqual(store.cloudState.requestedChapterID, "chapter")
    XCTAssertNil(store.cloudState.interview)
    XCTAssertTrue(requests.all.isEmpty, "Selecting a pending lesson first asks for learning mode")
    store.pauseCloudCourseGeneration()
    XCTAssertFalse(store.cloudState.choosingLearningMode)
  }

  @MainActor func testPreviewSessionAndManualContinuationNeverAnswerProfileOrDiagnosticQuestions() async throws {
    let requests = CourseRequests()
    let preview = CloudInterview(session_id: "session", phase: "preview_ready",
      turn: CloudTurn(turn_id: "preview", phase: "preview_ready", message: "课程预览，未做诊断",
        options: [], progress: 0), profile: .object([:]), purpose: "preview")
    let (api, session, host, repository) = try fixture { request in
      _ = try requests.append(request)
      switch request.url?.path {
      case "/api/workspaces/workspace/resources/resource/preview-session":
        XCTAssertEqual(request.httpMethod, "POST")
        return (200, try JSONEncoder().encode(preview))
      case "/api/interviews/session":
        XCTAssertEqual(request.httpMethod, "GET", "Recovery never creates diagnostic responses")
        return (200, try JSONEncoder().encode(preview))
      case "/api/interviews/session/course-jobs", "/api/interviews/session/course-jobs/course_job_owned":
        return (200, try self.job("failed"))
      default:
        XCTFail("Preview must not submit a diagnosis or profile answer: \(request.url?.path ?? "")")
        throw URLError(.unsupportedURL)
      }
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let store = diagnosisStore(api: api, repository: repository)
    store.cloudState.cacheKey = "owner"
    await store.beginCloudLearning(chapterID: "chapter", preview: true)
    XCTAssertTrue(store.isCloudCoursePreview)
    XCTAssertEqual(store.cloudResource?.session_id, "session")
    XCTAssertEqual(store.cloudState.interview?.purpose, "preview")
    let card = Flashcard(card_id: "card", book_id: "book", lesson_id: "lesson", chapter_id: "chapter",
      front: "概念", back: "原文解释", concept: "概念", source_chunk_ids: [], page_start: 1,
      page_end: 1, mastery: 0, reason: "预览")
    XCTAssertEqual(store.flashcardFamiliarityLabel(card), "尚未评估")
    XCTAssertEqual(requests.allURLs.first?.path,
      "/api/workspaces/workspace/resources/resource/preview-session")
    XCTAssertTrue(requests.allURLs.dropFirst().allSatisfy { $0.path.contains("/course-jobs") })
    // Even manual continuation from preview_ready may only query/generate a course.
    await store.advanceCloudInterview(selected: [], confidence: 0, seconds: 1)
    XCTAssertTrue(requests.allURLs.dropFirst().allSatisfy {
      $0.path.contains("/course-jobs") || $0.path == "/api/interviews/session"
    })
    XCTAssertTrue(store.mastered.isEmpty)
    store.cloudState.syncTask?.cancel()
    store.cloudState.retryTask?.cancel()
    await store.flush()
  }

  @MainActor func testExistingLearningSessionCannotBeReplacedWithPreview() async throws {
    let requests = CourseRequests()
    let (api, session, host, repository) = try fixture { request in
      _ = try requests.append(request)
      throw URLError(.unsupportedURL)
    }
    defer { CourseJobProtocol.install(host, nil); session.invalidateAndCancel() }
    let store = diagnosisStore(api: api, repository: repository, sessionID: "existing-learning")
    await store.beginCloudLearning(chapterID: "chapter", preview: true)
    XCTAssertTrue(requests.all.isEmpty)
    XCTAssertEqual(store.cloudResource?.session_id, "existing-learning")
  }
}
