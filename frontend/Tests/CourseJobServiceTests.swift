import Foundation
import SQLite3
import XCTest

@testable import BookCourse

private final class CourseJobProtocol: URLProtocol, @unchecked Sendable {
  typealias Handler = @Sendable (URLRequest) throws -> (Int, Data)
  static let lock = NSLock()
  nonisolated(unsafe) static var handlers: [String: Handler] = [:]
  static func install(_ host: String, _ handler: Handler?) { lock.withLock { handlers[host] = handler } }
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      guard let handler = Self.lock.withLock({ Self.handlers[request.url?.host ?? ""] }) else {
        throw URLError(.unsupportedURL)
      }
      let (status, data) = try handler(request)
      let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
        headerFields: ["Content-Type": "application/json"])!
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
    return lock.withLock { values.append((request.httpMethod ?? "GET", body)); return values.count }
  }
  var all: [(String, [String: Any])] { lock.withLock { values } }
}

private actor CourseJobSignal {
  private var marked = false
  private var waiter: CheckedContinuation<Void, Never>?
  func mark() { marked = true; waiter?.resume(); waiter = nil }
  func wait() async { if !marked { await withCheckedContinuation { waiter = $0 } } }
}

final class CourseJobServiceTests: XCTestCase, @unchecked Sendable {
  private func fixture(_ handler: @escaping CourseJobProtocol.Handler) throws
    -> (CloudAPI, URLSession, String, LocalBookCourseRepository) {
    let host = "course-job-\(UUID().uuidString.lowercased()).example"
    CourseJobProtocol.install(host, handler)
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
      store.cloudState.interview = CloudInterview(session_id: "session", phase: "complete",
        turn: CloudTurn(turn_id: "done", phase: "complete", message: "已完成诊断", options: [], progress: 1),
        profile: .object([:]))
      store.selectedCourseID = workspace.id
      store.selectedBookID = resource.book_id
      do {
        try await store.compileCloudChapter()
        XCTAssertTrue(includePublished)
        XCTAssertEqual(store.selectedLessonID, published.course_id)
        XCTAssertEqual(store.route, .courseReady)
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
        if request.httpMethod == "POST" { XCTAssertEqual(request.url?.query, "retry=true") }
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
    let store = AppStore(repository: repository, cloud: api)
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
    let cached = try XCTUnwrap(try await repository.loadCloudState("import-owner"))
    let restored = AppStore(repository: try LocalBookCourseRepository(directory: repository.directory), cloud: api)
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
}
