import Foundation
import XCTest
@testable import BookCourse

private final class CourseUploadProtocol: URLProtocol, @unchecked Sendable {
  struct Reply { var status = 200; var body: Data; var delay = 0.15 }
  private static let lock = NSLock()
  nonisolated(unsafe) private static var handlers: [String: (URLRequest) -> Reply] = [:]
  private var work: DispatchWorkItem?
  static func register(_ host: String, _ handler: @escaping (URLRequest) -> Reply) {
    lock.lock(); defer { lock.unlock() }; handlers[host] = handler
  }
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    Self.lock.lock(); let handler = Self.handlers[request.url!.host!]; Self.lock.unlock()
    let reply = handler!(request)
    let work = DispatchWorkItem { [weak self] in
      guard let self else { return }
      let response = HTTPURLResponse(url: self.request.url!, statusCode: reply.status,
        httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
      self.client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      self.client?.urlProtocol(self, didLoad: reply.body)
      self.client?.urlProtocolDidFinishLoading(self)
    }
    self.work = work
    DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay, execute: work)
  }
  override func stopLoading() { work?.cancel(); work = nil }
}

@MainActor final class CourseUploadTests: XCTestCase {
  private func makeStore(reply: @escaping (URLRequest) -> CourseUploadProtocol.Reply) throws -> AppStore {
    let host = "upload-" + UUID().uuidString.lowercased() + ".example"
    CourseUploadProtocol.register(host, reply)
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [CourseUploadProtocol.self]
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString), includeFixtures: false)
    let policy = ProcessingNetworkPolicy(monitoring: false, wifiStability: 0, cellularDebounce: 0)
    policy.updatePath(.init(kind: .wifi))
    let store = AppStore(repository: repo,
      cloud: CloudAPI(baseURL: URL(string: "https://\(host)")!, session: URLSession(configuration: config)),
      processingPolicy: policy)
    store.isLoading = false
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = host
    store.restoreCourseUploads()
    return store
  }
  private func pdf(_ store: AppStore, name: String, contents: String = "original") throws -> URL {
    let url = store.repository.directory.appendingPathComponent(name)
    try Data(("%PDF-1.4\n" + contents).utf8).write(to: url)
    return url
  }
  private func wait(_ message: String, _ condition: () -> Bool) async throws {
    let deadline = ContinuousClock.now.advanced(by: .seconds(8))
    while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(20)) }
    XCTAssertTrue(condition(), message)
  }
  private func success(_ request: URLRequest, delay: Double = 0.15) -> CourseUploadProtocol.Reply {
    let id = request.value(forHTTPHeaderField: "X-CloudPath-Upload-ID") ?? "test"
    return .init(body: Data("{\"book_id\":\"server-\(id)\",\"filename\":\"original.pdf\",\"status\":\"uploaded\"}".utf8), delay: delay)
  }

  func testBackgroundCompletionDoesNotOverwriteTheSelectedCourseOrAnotherDraft() async throws {
    let store = try makeStore { request in
      let id = request.value(forHTTPHeaderField: "X-CloudPath-Upload-ID")!
      return .init(body: Data("{\"book_id\":\"server-\(id)\",\"filename\":\"one.pdf\",\"status\":\"uploaded\"}".utf8), delay: 0.4)
    }
    store.courses = [Course(id: "local-course:other", name: "正在学习", resourceIDs: [])]
    store.selectedCourseID = "local-course:other"
    store.draft = CourseDraft(name: "上传课程")
    let id = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "one.pdf")]))
    let owner = try XCTUnwrap(store.uploadState.batches.first?.courseID)
    XCTAssertEqual(store.courseCreationLabel(try XCTUnwrap(store.courses.first { $0.id == owner })), "上传中")
    store.backgroundCourseUpload(id)
    store.openCourse(try XCTUnwrap(store.courses.first { $0.id == owner }))
    XCTAssertEqual(store.uploadState.presentedID, id, "Course switching must reopen the exact upload batch")
    store.backgroundCourseUpload(id)
    store.selectCourse("local-course:other")
    let otherDraft = CourseDraft(name: "另一个课程的问卷", diagnosis: ["goals": ["exam"]], step: 3)
    store.draft = otherDraft
    try await wait("The server-confirmed upload should complete") { store.uploadState.batches.first?.isComplete == true }
    XCTAssertEqual(store.selectedCourseID, "local-course:other")
    XCTAssertEqual(store.draft, otherDraft)
    XCTAssertEqual(store.route, .study)
    XCTAssertTrue(try XCTUnwrap(store.courses.first { $0.id == owner }).resourceIDs.first!.hasPrefix("server-"))
    XCTAssertEqual(store.courses.first { $0.id == "local-course:other" }?.resourceIDs, [])
    XCTAssertFalse(store.uploadState.batches[0].completionAcknowledged)
    store.continueCourseUpload(id)
    XCTAssertEqual(store.selectedCourseID, owner)
    XCTAssertEqual(store.route, .processing)
    XCTAssertEqual(store.draft.name, "上传课程")
    XCTAssertTrue(store.uploadState.batches[0].completionAcknowledged)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testPartialFailureRetryKeepsCompletedFilesAndTheirServerIdentities() async throws {
    let counts = UploadRequestCounts()
    let store = try makeStore { request in
      let count = counts.increment()
      if count == 2 { return .init(status: 503, body: Data("{\"detail\":\"请重试\"}".utf8)) }
      return .init(body: Data("{\"book_id\":\"server-\(count)\",\"filename\":\"book.pdf\",\"status\":\"uploaded\"}".utf8))
    }
    let id = try XCTUnwrap(store.beginCourseUpload([
      pdf(store, name: "one.pdf", contents: "first"), pdf(store, name: "two.pdf", contents: "second")]))
    try await wait("Both upload attempts should finish") { store.uploadState.batches.first?.isRunning == false }
    let completed = try XCTUnwrap(store.uploadState.batches[0].files.first { $0.status == .completed })
    XCTAssertTrue(store.uploadState.batches[0].hasFailures)
    XCTAssertFalse(store.uploadState.batches[0].isComplete)
    XCTAssertEqual(counts.value, 2)
    store.retryCourseUpload(id)
    try await wait("Only the failed file should be retried") { store.uploadState.batches[0].isComplete }
    XCTAssertEqual(counts.value, 3)
    XCTAssertEqual(store.uploadState.batches[0].files.first { $0.id == completed.id }?.bookID, completed.bookID)
    XCTAssertEqual(store.uploadState.batches[0].files.first { $0.id == completed.id }?.requestID, completed.requestID)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testCancellationKeepsTheOriginalAndNeverMarksUnconfirmedBytesComplete() async throws {
    let store = try makeStore { _ in .init(body: Data("{}".utf8), delay: 3) }
    let original = try pdf(store, name: "cancel.pdf")
    let id = try XCTUnwrap(store.beginCourseUpload([original]))
    try await wait("The original must be staged before cancellation") {
      store.uploadState.batches[0].files[0].status == .uploading
    }
    let path = try XCTUnwrap(store.uploadState.batches[0].files[0].relativePath)
    store.cancelCourseUpload(id)
    try await Task.sleep(for: .milliseconds(200))
    XCTAssertEqual(store.uploadState.batches[0].files[0].status, .cancelled)
    XCTAssertFalse(store.uploadState.batches[0].isComplete)
    XCTAssertEqual(try Data(contentsOf: store.repository.attachmentURL(path)), try Data(contentsOf: original))
    store.suspendCourseUploads()
    await store.flush()
  }

  func testCompletedBatchRestoresWithoutUploadingAgainAndRemainsAccountScoped() async throws {
    let counts = UploadRequestCounts()
    let store = try makeStore { _ in
      _ = counts.increment()
      return .init(body: Data("{\"book_id\":\"server-one\",\"filename\":\"one.pdf\",\"status\":\"uploaded\"}".utf8))
    }
    let id = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "one.pdf")]))
    try await wait("Upload should finish") { store.uploadState.batches[0].isComplete }
    store.suspendCourseUploads()
    store.restoreCourseUploads()
    XCTAssertEqual(store.uploadState.batches.first?.id, id)
    XCTAssertTrue(store.uploadState.batches[0].isComplete)
    XCTAssertEqual(counts.value, 1)
    store.cloudState.cacheKey = "another-account"
    store.restoreCourseUploads()
    XCTAssertTrue(store.uploadState.batches.isEmpty)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testStallHintIsOneBriefToastPerStallInsteadOfPermanentProgressCopy() throws {
    let store = try makeStore { _ in .init(body: Data()) }
    let now = Date.now
    let file = CourseUploadFile(filename: "slow.pdf", totalBytes: 100,
      status: .uploading, lastProgressAt: now.addingTimeInterval(-16))
    let batch = CourseUploadBatch(courseID: "course", courseName: "课程", scope: store.courseUploadScope,
      draft: CourseDraft(), addingMaterials: false, files: [file])
    store.uploadState.batches = [batch]
    store.checkCourseUploadStalls(at: now)
    let toastID = store.toast?.id
    XCTAssertEqual(store.toast?.title, "上传较慢，请保持网络连接")
    store.checkCourseUploadStalls(at: now.addingTimeInterval(20))
    XCTAssertEqual(store.toast?.id, toastID, "A stalled upload should not refresh the toast every second")
    store.suspendCourseUploads()
  }

  func testNativeBackgroundTransferReceiptPreventsASecondHTTPUpload() async throws {
    guard let base = ProcessInfo.processInfo.environment["BOOKCOURSE_UPLOAD_TEST_SERVER"], let url = URL(string: base) else {
      throw XCTSkip("Set BOOKCOURSE_UPLOAD_TEST_SERVER for the native URLSession background integration test")
    }
    let api = CloudAPI(baseURL: url)
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let file = root.appendingPathComponent("background.pdf")
    try Data(("%PDF-1.4\n" + String(repeating: "upload-bytes", count: 10000)).utf8).write(to: file)
    let identity = BackgroundUploadIdentity(id: UUID(), scope: UUID().uuidString)
    let first = try await api.uploadPDF(file, filename: "background.pdf", background: identity)
    let second = try await api.uploadPDF(file, filename: "background.pdf", background: identity)
    XCTAssertEqual(first.book_id, second.book_id)
    let counts: [String: Int] = try await api.request("/api/upload-test/counts")
    XCTAssertEqual(counts[identity.id.uuidString], 1)
    BackgroundUploadTransport.client(origin: url, scope: identity.scope).acknowledge(identity.id)
  }
}

private final class UploadRequestCounts: @unchecked Sendable {
  private let lock = NSLock()
  private var count = 0
  func increment() -> Int { lock.lock(); defer { lock.unlock() }; count += 1; return count }
  var value: Int { lock.lock(); defer { lock.unlock() }; return count }
}
