import Foundation
import SQLite3
import XCTest

@testable import BookCourse

private final class BookPreparationProtocol: URLProtocol, @unchecked Sendable {
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

private final class BookPreparationRequests: @unchecked Sendable {
  struct Entry: Equatable {
    var method: String
    var path: String
    var query: String?
  }
  let lock = NSLock()
  private var entries: [Entry] = []
  @discardableResult func append(_ request: URLRequest) -> Int {
    lock.withLock {
      entries.append(Entry(method: request.httpMethod ?? "GET", path: request.url!.path,
        query: request.url!.query))
      return entries.count
    }
  }
  var all: [Entry] { lock.withLock { entries } }
  func count(_ method: String, _ path: String) -> Int {
    lock.withLock { entries.filter { $0.method == method && $0.path == path }.count }
  }
}

private actor BookPreparationSignal {
  private var marked = false
  private var waiter: CheckedContinuation<Void, Never>?
  func mark() { marked = true; waiter?.resume(); waiter = nil }
  func wait() async { if !marked { await withCheckedContinuation { waiter = $0 } } }
}

final class BookPreparationServiceTests: XCTestCase, @unchecked Sendable {
  private let prepare = "/api/books/book/prepare"
  private let readiness = "/api/native/books/book/readiness"
  private let diagnostics = "/api/books/book/diagnostics"
  private func fixture(_ handler: @escaping BookPreparationProtocol.Handler) throws
    -> (CloudAPI, URLSession, String, LocalBookCourseRepository) {
    let host = "book-prepare-\(UUID().uuidString.lowercased()).example"
    BookPreparationProtocol.install(host, handler)
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [BookPreparationProtocol.self]
    let session = URLSession(configuration: config)
    let repository = try LocalBookCourseRepository(directory:
      FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    return (CloudAPI(baseURL: URL(string: "https://\(host)")!, session: session), session, host, repository)
  }
  private func json(_ value: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: value) }
  private func job(_ status: String, book: String = "book", step: Int? = nil, updated: Double = 1) throws -> Data {
    let next = step ?? (status == "succeeded" ? 3 : status == "failed" ? 2 : 0)
    var value: [String: Any] = ["book_id": book, "status": status,
      "stage": status == "succeeded" ? "ready" : status == "queued" ? "queued" : "diagnostics",
      "updated": updated, "next_step": next]
    if status == "failed" { value["error"] = "诊断题未通过独立审核，已保留完成步骤" }
    return try json(value)
  }
  private func ready(_ rag: Bool = true) throws -> Data {
    try json(["structure_ready": true, "rag_ready": rag])
  }
  private func bank(_ book: String = "book", count: Int = 6) throws -> Data {
    try json(["book_id": book, "item_count": count, "chapter_count": 2,
      "choice_count": count, "explanation_count": 0])
  }
  private func receipts(_ directory: URL) throws -> [CloudBookPreparationReceipt] {
    var db: OpaquePointer?
    guard sqlite3_open_v2(directory.appendingPathComponent("bookcourse.sqlite").path, &db,
      SQLITE_OPEN_READONLY, nil) == SQLITE_OK else { throw URLError(.cannotOpenFile) }
    defer { sqlite3_close(db) }
    var statement: OpaquePointer?
    guard sqlite3_prepare_v2(db, "SELECT payload FROM records WHERE kind='cloud-book-preparation'", -1,
      &statement, nil) == SQLITE_OK else { throw URLError(.cannotDecodeRawData) }
    defer { sqlite3_finalize(statement) }
    var result: [CloudBookPreparationReceipt] = []
    while sqlite3_step(statement) == SQLITE_ROW {
      let data = Data(bytes: sqlite3_column_blob(statement, 0)!, count: Int(sqlite3_column_bytes(statement, 0)))
      result.append(try JSONDecoder().decode(CloudBookPreparationReceipt.self, from: data))
    }
    return result
  }

  func testCreatePersistsIntentBeforePOSTAndVerifiesActualReadiness() async throws {
    let requests = BookPreparationRequests()
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let repository = try LocalBookCourseRepository(directory: root)
    let (api, session, host, _) = try fixture { request in
      requests.append(request)
      switch request.url!.path {
      case self.prepare:
        if request.httpMethod == "POST" {
          let receipt = try XCTUnwrap(self.receipts(root).first)
          XCTAssertTrue(receipt.creationAttempted)
          XCTAssertEqual(receipt.lastOutcome, "submitting")
          XCTAssertEqual(receipt.submissionEpoch, 1)
          XCTAssertEqual(request.url!.query, "retry=false")
          return (200, try self.job("queued"))
        }
        let count = requests.count("GET", self.prepare)
        if count == 1 { return (404, try self.json(["detail": "not started"])) }
        return (200, try self.job(count == 2 ? "running" : "succeeded"))
      case self.readiness: return (200, try self.ready())
      case self.diagnostics: return (200, try self.bank())
      default: XCTFail("Unexpected network operation"); throw URLError(.unsupportedURL)
      }
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    let service = BookPreparationService(api: api, repository: repository, accountKey: "owner")
    let result = try await service.waitForBook(bookID: "book", pollInterval: .milliseconds(1)) { _ in }
    XCTAssertEqual(result.status, .succeeded)
    XCTAssertEqual(result.structure_ready, true)
    XCTAssertEqual(result.rag_ready, true)
    XCTAssertEqual(result.diagnostics_ready, true)
    XCTAssertEqual(requests.all.map(\.method), ["GET", "POST", "GET", "GET", "GET", "GET"])
    XCTAssertEqual(try receipts(root).first?.lastOutcome, "accepted")
  }

  func testFailureSurvivesReopenAndOrdinaryObservationNeverRetries() async throws {
    let requests = BookPreparationRequests()
    let signal = BookPreparationSignal()
    let (api, session, host, repository) = try fixture { request in
      requests.append(request); return (200, try self.job("failed"))
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    for repo in [repository, try LocalBookCourseRepository(directory: repository.directory)] {
      do {
        _ = try await BookPreparationService(api: api, repository: repo, accountKey: "owner")
          .waitForBook(bookID: "book") { job in
            XCTAssertEqual(job.status, .failed)
            XCTAssertEqual(job.next_step, 2)
            await signal.mark()
          }
        XCTFail("Failed preparation must not succeed")
      } catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    }
    await signal.wait()
    XCTAssertEqual(requests.all.map(\.method), ["GET", "GET"])
  }

  func testExplicitRetryKeepsCheckpointAndRejectedAttemptCanRecover() async throws {
    let requests = BookPreparationRequests()
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let repository = try LocalBookCourseRepository(directory: root)
    let (api, session, host, _) = try fixture { request in
      requests.append(request)
      if request.url!.path == self.prepare {
        if request.httpMethod == "POST" {
          let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
          XCTAssertEqual(query.first { $0.name == "retry" }?.value, "true")
          let nonce = try XCTUnwrap(query.first { $0.name == "retry_request_id" }?.value)
          XCTAssertNotNil(UUID(uuidString: nonce))
          let receipt = try XCTUnwrap(self.receipts(root).first)
          XCTAssertEqual(receipt.retryRequestID, nonce, "The nonce must be committed before the POST")
          XCTAssertEqual(receipt.lastOutcome, "submitting")
          if requests.count("POST", self.prepare) == 1 { return (503, try self.json(["detail": "cooldown"])) }
          return (200, try self.job("running", step: 2))
        }
        return (200, try self.job(requests.count("POST", self.prepare) < 2 ? "failed" : "succeeded"))
      }
      if request.url!.path == self.readiness { return (200, try self.ready()) }
      return (200, try self.bank())
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    let service = BookPreparationService(api: api, repository: repository, accountKey: "owner")
    do { _ = try await service.waitForBook(bookID: "book", retry: true) { _ in }; XCTFail("Rejected") }
    catch let error as CloudError { XCTAssertEqual(error.status, 503) }
    XCTAssertEqual(try receipts(repository.directory).first?.lastOutcome, "rejected")
    let result = try await service.waitForBook(bookID: "book", retry: true, pollInterval: .milliseconds(1)) { job in
      if job.status == .running { XCTAssertEqual(job.next_step, 2) }
    }
    XCTAssertEqual(result.status, .succeeded)
    XCTAssertEqual(requests.count("POST", prepare), 2)
    XCTAssertEqual(try receipts(repository.directory).first?.submissionEpoch, 2)
    let retryQueries = requests.all.filter { $0.method == "POST" }.compactMap(\.query)
    XCTAssertEqual(Set(retryQueries).count, 2, "A later explicit action owns a new nonce")
  }

  func testLegacyPreparationReceiptDecodesWithoutARetryNonce() throws {
    let value = try JSONDecoder().decode(CloudBookPreparationReceipt.self,
      from: json(["bookID": "book", "creationAttempted": true, "submissionEpoch": 1]))
    XCTAssertEqual(value.bookID, "book")
    XCTAssertNil(value.retryRequestID)
    XCTAssertEqual(value.submissionEpoch, 1)
  }

  func testUnknownCreateOnlyQueriesUntilExplicitRecoveryAndDoesNotRepeatOCR() async throws {
    let requests = BookPreparationRequests()
    let (api, session, host, repository) = try fixture { request in
      requests.append(request)
      if request.url!.path == self.prepare {
        if request.httpMethod == "POST" {
          XCTAssertEqual(request.url!.query, "retry=false")
          if requests.count("POST", self.prepare) == 1 { throw URLError(.timedOut) }
          return (200, try self.job("queued"))
        }
        if requests.count("POST", self.prepare) < 2 { return (404, try self.json(["detail": "not started"])) }
        return (200, try self.job("succeeded"))
      }
      if request.url!.path == self.readiness { return (200, try self.ready()) }
      if request.url!.path == self.diagnostics { return (200, try self.bank()) }
      XCTFail("Recovery must not restart OCR"); throw URLError(.unsupportedURL)
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    let first = BookPreparationService(api: api, repository: repository, accountKey: "owner")
    do { _ = try await first.waitForBook(bookID: "book") { _ in }; XCTFail("Unknown result") }
    catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    let reopened = try LocalBookCourseRepository(directory: repository.directory)
    let resumed = BookPreparationService(api: api, repository: reopened, accountKey: "owner")
    do { _ = try await resumed.waitForBook(bookID: "book") { _ in }; XCTFail("Do not auto-resubmit") }
    catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    XCTAssertEqual(requests.count("POST", prepare), 1)
    let result = try await resumed.waitForBook(bookID: "book", retry: true, pollInterval: .milliseconds(1)) { _ in }
    XCTAssertEqual(result.status, .succeeded)
    XCTAssertEqual(requests.count("POST", prepare), 2)
    XCTAssertEqual(try receipts(repository.directory).first?.submissionEpoch, 2)
  }

  func testLostCreateReplyReconcilesQueuedServerJobWithoutAnotherPOST() async throws {
    let requests = BookPreparationRequests()
    let (api, session, host, repository) = try fixture { request in
      requests.append(request)
      if request.url!.path == self.prepare {
        if request.httpMethod == "POST" { throw URLError(.networkConnectionLost) }
        let count = requests.count("GET", self.prepare)
        if count == 1 { return (404, try self.json(["detail": "not started"])) }
        return (200, try self.job(count == 2 ? "queued" : "succeeded"))
      }
      if request.url!.path == self.readiness { return (200, try self.ready()) }
      return (200, try self.bank())
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    _ = try await BookPreparationService(api: api, repository: repository, accountKey: "owner")
      .waitForBook(bookID: "book", pollInterval: .milliseconds(1)) { _ in }
    XCTAssertEqual(requests.count("POST", prepare), 1)
  }

  func testReceiptWriteFailurePreventsPOST() async throws {
    let requests = BookPreparationRequests()
    let (api, session, host, repository) = try fixture { request in
      requests.append(request); return (404, try self.json(["detail": "not started"]))
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    await repository.setWriteFaultForTesting(true)
    do {
      _ = try await BookPreparationService(api: api, repository: repository, accountKey: "owner")
        .waitForBook(bookID: "book") { _ in }
      XCTFail("Unpersisted command must not send")
    } catch {}
    XCTAssertEqual(requests.all.map(\.method), ["GET"])
  }

  func testCancellationStopsObservationAndReopenResumesWithGET() async throws {
    let requests = BookPreparationRequests()
    let signal = BookPreparationSignal()
    let (api, session, host, repository) = try fixture { request in
      requests.append(request)
      if request.url!.path == self.prepare {
        return (200, try self.job(requests.count("GET", self.prepare) == 1 ? "running" : "succeeded"))
      }
      if request.url!.path == self.readiness { return (200, try self.ready()) }
      return (200, try self.bank())
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    let service = BookPreparationService(api: api, repository: repository, accountKey: "owner")
    let task = Task {
      try await service.waitForBook(bookID: "book", pollInterval: .seconds(30)) { _ in await signal.mark() }
    }
    await signal.wait()
    task.cancel()
    do { _ = try await task.value; XCTFail("Cancellation must stop polling") } catch is CancellationError {}
    let reopened = try LocalBookCourseRepository(directory: repository.directory)
    _ = try await BookPreparationService(api: api, repository: reopened, accountKey: "owner")
      .waitForBook(bookID: "book") { _ in }
    XCTAssertTrue(requests.all.allSatisfy { $0.method == "GET" })
  }

  func testStaleSucceededRowRefreshesOnceAndOnlyPublishesVerifiedBank() async throws {
    let requests = BookPreparationRequests()
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let repository = try LocalBookCourseRepository(directory: root)
    let (api, session, host, _) = try fixture { request in
      requests.append(request)
      if request.url!.path == self.prepare {
        if request.httpMethod == "POST" {
          XCTAssertEqual(request.url!.query, "retry=false")
          XCTAssertNotNil(try self.receipts(root).first?.refreshedSuccess)
          return (200, try self.job("queued", updated: 2))
        }
        return (200, try self.job("succeeded", updated: requests.count("POST", self.prepare) == 0 ? 1 : 3))
      }
      if request.url!.path == self.readiness { return (200, try self.ready()) }
      if requests.count("GET", self.diagnostics) == 1 { return (404, try self.json(["detail": "stale bank"])) }
      return (200, try self.bank())
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    let result = try await BookPreparationService(api: api, repository: repository, accountKey: "owner")
      .waitForBook(bookID: "book", pollInterval: .milliseconds(1)) { job in
        if job.status == .succeeded { XCTAssertEqual(job.diagnostics_ready, true) }
      }
    XCTAssertEqual(result.diagnostics_ready, true)
    XCTAssertEqual(requests.count("POST", prepare), 1)
    XCTAssertEqual(requests.count("GET", diagnostics), 2)
  }

  func testPersistentStaleSuccessDoesNotLoopOrRefreshAgainAfterReopen() async throws {
    let requests = BookPreparationRequests()
    let (api, session, host, repository) = try fixture { request in
      requests.append(request)
      if request.url!.path == self.prepare { return (200, try self.job("succeeded")) }
      if request.url!.path == self.readiness { return (200, try self.ready()) }
      return (404, try self.json(["detail": "missing bank"]))
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    for repo in [repository, try LocalBookCourseRepository(directory: repository.directory)] {
      do {
        _ = try await BookPreparationService(api: api, repository: repo, accountKey: "owner")
          .waitForBook(bookID: "book") { _ in }
        XCTFail("Stale queue success is insufficient")
      } catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    }
    XCTAssertEqual(requests.count("POST", prepare), 1)
  }

  func testForeignOrIncompleteSuccessAndMalformedCheckpointNeverPublish() async throws {
    for fault in ["foreign", "checkpoint", "status", "rag", "bank"] {
      let (api, session, host, repository) = try fixture { request in
        if request.url!.path == self.prepare {
          return (200, try self.job(fault == "status" ? "invented" : "succeeded",
            book: fault == "foreign" ? "foreign" : "book", step: fault == "checkpoint" ? 2 : 3))
        }
        if request.url!.path == self.readiness { return (200, try self.ready(fault != "rag")) }
        return (200, try self.bank(count: fault == "bank" ? 0 : 6))
      }
      defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
      do {
        _ = try await BookPreparationService(api: api, repository: repository, accountKey: "owner")
          .waitForBook(bookID: "book") { _ in XCTFail("Unverified success must never publish") }
        XCTFail("Invalid success must fail")
      } catch {}
    }
  }

  func testReceiptScopesAccountOriginAndBookAndObservationHasDeadline() async throws {
    let requests = BookPreparationRequests()
    let (api, session, host, repository) = try fixture { request in
      requests.append(request); return (200, try self.job("running"))
    }
    defer { BookPreparationProtocol.install(host, nil); session.invalidateAndCancel() }
    let service = BookPreparationService(api: api, repository: repository, accountKey: "owner")
    let base = try await service.receiptKey(bookID: "book")
    let account = try await BookPreparationService(api: api, repository: repository, accountKey: "other")
      .receiptKey(bookID: "book")
    let otherBook = try await service.receiptKey(bookID: "other")
    let otherAPI = CloudAPI(baseURL: URL(string: "http://\(host):8100")!, session: session)
    let origin = try await BookPreparationService(api: otherAPI, repository: repository, accountKey: "owner")
      .receiptKey(bookID: "book")
    XCTAssertEqual(Set([base, account, otherBook, origin]).count, 4)
    do {
      _ = try await service.waitForBook(bookID: "book", pollInterval: .milliseconds(2),
        observationTimeout: .milliseconds(1)) { _ in }
      XCTFail("Observation must have a deadline")
    } catch let error as CloudError { XCTAssertEqual(error.status, 408) }
    XCTAssertTrue(requests.all.allSatisfy { $0.method == "GET" })
  }
}
