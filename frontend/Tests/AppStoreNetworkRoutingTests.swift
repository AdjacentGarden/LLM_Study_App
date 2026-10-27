import Foundation
import XCTest

@testable import BookCourse

/// Explicit test injection; no NWPathMonitor, mobile-data prompt, or real clock.
@MainActor func stableWiFiProcessingPolicyForTesting() -> ProcessingNetworkPolicy {
  let policy = ProcessingNetworkPolicy(monitoring: false, automaticallySchedules: false,
    now: { 0 }, wifiStability: 0)
  policy.updatePath(ProcessingNetworkPath(kind: .wifi))
  return policy
}

private final class NetworkRoutingProbe: @unchecked Sendable {
  typealias Handler = (URLRequest) throws -> (Int, Data)
  private let lock = NSLock()
  private var observed: [URLRequest] = []
  private var handler: Handler?
  var requests: [URLRequest] {
    lock.lock(); defer { lock.unlock() }
    return observed
  }
  func respond(with handler: @escaping Handler) {
    lock.lock(); defer { lock.unlock() }
    self.handler = handler
  }
  func response(to request: URLRequest) throws -> (Int, Data) {
    lock.lock()
    observed.append(request)
    let response = handler
    lock.unlock()
    guard let response else { throw URLError(.unsupportedURL) }
    return try response(request)
  }
}

private final class NetworkRoutingProtocol: URLProtocol, @unchecked Sendable {
  private static let lock = NSLock()
  nonisolated(unsafe) private static var probes: [String: NetworkRoutingProbe] = [:]
  static func install(host: String, probe: NetworkRoutingProbe?) {
    lock.lock(); defer { lock.unlock() }
    probes[host] = probe
  }
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      Self.lock.lock()
      let probe = Self.probes[request.url?.host ?? ""]
      Self.lock.unlock()
      guard let probe else { throw URLError(.unsupportedURL) }
      let (status, data) = try probe.response(to: request)
      let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
        headerFields: ["Content-Type": "application/json"])!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: data)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

@MainActor private final class NetworkRoutingFixture {
  let directory: URL
  let repository: LocalBookCourseRepository
  let api: CloudAPI
  let store: AppStore
  let policy: ProcessingNetworkPolicy
  let probe = NetworkRoutingProbe()
  private let host: String
  private let session: URLSession

  init(network: ProcessingNetworkKind) throws {
    directory = FileManager.default.temporaryDirectory.appendingPathComponent("routing-" + UUID().uuidString)
    repository = try LocalBookCourseRepository(directory: directory)
    host = "routing-" + UUID().uuidString.lowercased() + ".example"
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [NetworkRoutingProtocol.self]
    session = URLSession(configuration: configuration)
    api = CloudAPI(baseURL: URL(string: "https://" + host)!, session: session)
    policy = ProcessingNetworkPolicy(monitoring: false, automaticallySchedules: false,
      now: { 0 }, wifiStability: 0, cellularDebounce: 0, unknownGrace: 0)
    policy.updatePath(ProcessingNetworkPath(kind: network))
    store = AppStore(repository: repository, cloud: api, processingPolicy: policy,
      appleTextbookSearch: AppleTextbookSearch(useSystemEmbeddings: false))
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = "isolated-routing-owner"
    NetworkRoutingProtocol.install(host: host, probe: probe)
  }

  func close() {
    policy.cancelPending()
    store.cloudState.syncTask?.cancel()
    store.cloudState.retryTask?.cancel()
    session.invalidateAndCancel()
    NetworkRoutingProtocol.install(host: host, probe: nil)
    try? FileManager.default.removeItem(at: directory)
  }
}

@MainActor final class AppStoreNetworkRoutingTests: XCTestCase {
  private func pdf() throws -> URL {
    try XCTUnwrap(Bundle(for: Self.self).url(forResource: "text.pdf", withExtension: nil,
      subdirectory: "ImportFixtures"))
  }

  private func prompt(_ policy: ProcessingNetworkPolicy) async throws -> ProcessingNetworkPrompt {
    let deadline = ContinuousClock.now + .seconds(5)
    while policy.prompt == nil, ContinuousClock.now < deadline {
      try await Task.sleep(for: .milliseconds(10))
    }
    return try XCTUnwrap(policy.prompt)
  }

  private func waitForSource(_ store: AppStore) async throws {
    let deadline = ContinuousClock.now + .seconds(5)
    while store.route != .source, ContinuousClock.now < deadline {
      try await Task.sleep(for: .milliseconds(10))
    }
    XCTAssertEqual(store.route, .source)
  }

  private func citation(_ source: SourceContent) throws -> Citation {
    let chunk = try XCTUnwrap(source.chunks.first)
    return Citation(bookID: source.id, chapterID: chunk.chapter_id, chapterTitle: source.summary.title,
      page: chunk.page_start, chunkID: chunk.id, quote: chunk.text, score: 1)
  }

  func testOfflineCitationOpensCachedTextWithoutDownloadingMissingRemotePDF() async throws {
    let fixture = try NetworkRoutingFixture(network: .offline)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "known-server-book", filename: "text.pdf")
    let reference = try citation(source)
    fixture.store.sources = [source]
    fixture.store.route = .lesson
    XCTAssertNil(fixture.store.sourceFileURL(source.id))
    fixture.store.openCitation(reference)
    try await waitForSource(fixture.store)
    await fixture.store.flush()
    XCTAssertEqual(fixture.store.selectedBookID, source.id)
    XCTAssertEqual(fixture.store.sourceTarget?.sourceText, reference.quote)
    XCTAssertEqual(fixture.store.sourceTarget?.page, reference.page)
    XCTAssertNil(fixture.store.sourceFileURL(source.id))
    XCTAssertTrue(fixture.probe.requests.isEmpty, "A cached source citation must not download its PDF or hydrate cloud content in local mode.")
  }

  func testCellularLocalCitationChoiceOpensTextWithoutHTTP() async throws {
    let fixture = try NetworkRoutingFixture(network: .cellular)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "server-citation", filename: "text.pdf")
    let reference = try citation(source)
    fixture.store.sources = [source]
    fixture.store.route = .lesson
    fixture.store.openCitation(reference)
    let choice = try await prompt(fixture.policy)
    XCTAssertEqual(choice.operations, [.retrieval])
    XCTAssertTrue(fixture.probe.requests.isEmpty)
    fixture.policy.resolvePrompt(.onDevice, promptID: choice.id)
    try await waitForSource(fixture.store)
    await fixture.store.flush()
    XCTAssertEqual(fixture.store.sourceTarget?.sourceText, reference.quote)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
  }

  func testMissingLocalBookPDFNeverRequestsRemoteOriginalEvenOnWiFi() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "book_local_text_only", filename: "text.pdf")
    fixture.store.sources = [source]
    fixture.store.route = .lesson
    fixture.store.openCitation(try citation(source))
    XCTAssertEqual(fixture.store.route, .source)
    await fixture.store.flush()
    XCTAssertNil(fixture.policy.prompt)
    XCTAssertNil(fixture.store.sourceFileURL(source.id))
    XCTAssertTrue(fixture.probe.requests.isEmpty, "A local ID cannot be requested from the cloud original endpoint.")
  }

  func testCancellingCitationNetworkChoiceDoesNotOpenReaderOrDownload() async throws {
    let fixture = try NetworkRoutingFixture(network: .cellular)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "server-cancelled", filename: "text.pdf")
    fixture.store.sources = [source]
    fixture.store.route = .lesson
    fixture.store.openCitation(try citation(source))
    let choice = try await prompt(fixture.policy)
    fixture.policy.resolvePrompt(.cancelled, promptID: choice.id)
    try await Task.sleep(for: .milliseconds(30))
    XCTAssertEqual(fixture.store.route, .lesson)
    XCTAssertNil(fixture.store.sourceTarget)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
  }

  func testObsoleteCitationChoiceCannotOpenReaderAfterAccountOrSelectionChange() async throws {
    for changeAccount in [false, true] {
      let fixture = try NetworkRoutingFixture(network: .cellular)
      defer { fixture.close() }
      let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "server-stale", filename: "text.pdf")
      fixture.store.sources = [source]
      fixture.store.route = .lesson
      fixture.store.openCitation(try citation(source))
      let choice = try await prompt(fixture.policy)
      if changeAccount { fixture.store.cloudState.epoch = UUID() }
      else { fixture.store.selectedBookID = "another-book"; fixture.store.selectionGeneration += 1 }
      fixture.policy.resolvePrompt(.onDevice, promptID: choice.id)
      try await Task.sleep(for: .milliseconds(30))
      XCTAssertEqual(fixture.store.route, .lesson)
      XCTAssertNil(fixture.store.sourceTarget)
      XCTAssertTrue(fixture.probe.requests.isEmpty)
    }
  }

  func testExistingLocalPDFOpensImmediatelyWithoutAnotherNetworkQuestion() async throws {
    let fixture = try NetworkRoutingFixture(network: .cellular)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "server-already-cached", filename: "text.pdf")
    fixture.store.sources = [source]
    fixture.store.selectedBookID = source.id
    fixture.store.filePaths[source.id] = try await fixture.repository.saveAttachment(Data(contentsOf: pdf()), extension: "pdf")
    fixture.store.route = .lesson
    fixture.store.openCitation(try citation(source))
    XCTAssertEqual(fixture.store.route, .source)
    XCTAssertNil(fixture.policy.prompt)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
  }

  func testCellularLocalPDFImportExtractionAndSearchKeepOriginalAndMakeZeroHTTPRequests() async throws {
    let fixture = try NetworkRoutingFixture(network: .cellular)
    defer { fixture.close() }
    let file = try pdf(), bytes = try Data(contentsOf: file)
    let importing = Task { await fixture.store.importFile(file) }
    let choice = try await prompt(fixture.policy)
    XCTAssertEqual(choice.operations, [.ocr])
    XCTAssertTrue(fixture.probe.requests.isEmpty)
    fixture.policy.resolvePrompt(.onDevice, promptID: choice.id)
    await importing.value
    let id = try XCTUnwrap(fixture.store.selectedBookID)
    XCTAssertTrue(id.hasPrefix("book_local_"))
    let original = try XCTUnwrap(fixture.store.sourceFileURL(id))
    XCTAssertEqual(try Data(contentsOf: original), bytes)
    await fixture.store.processImport()
    await fixture.store.flush()
    XCTAssertNil(fixture.store.importError)
    XCTAssertEqual(fixture.store.importJob?.status, .completed)
    XCTAssertTrue(fixture.store.draft.parseCompleted)
    let source = try XCTUnwrap(fixture.store.sources.first { $0.id == id })
    XCTAssertEqual(source.summary.page_count, 2)
    XCTAssertEqual(source.chunks.map(\.page_start), [1, 2])
    XCTAssertTrue(source.chunks.first?.text.contains("Native learning page one") == true)
    XCTAssertEqual(source.chunks.first?.source_metadata?["parser"], .string("pdfkit-text"))
    XCTAssertTrue(source.lessons.isEmpty && source.chapters.isEmpty && source.quizzes.isEmpty)
    let answer = try await fixture.store.answerAssistant(question: "Native learning page one",
      bookID: id, bookIDs: [id], chapterID: nil, sources: fixture.store.sources, history: [])
    XCTAssertTrue(answer.answer.contains("Native learning page one"))
    XCTAssertFalse(answer.citations.isEmpty)
    XCTAssertTrue(answer.citations.allSatisfy { $0.bookID == id && (1...2).contains($0.page) })
    XCTAssertTrue(fixture.probe.requests.isEmpty, "Local OCR/search must not upload, prepare, or query cloud QA.")
    let cached = try await fixture.repository.loadCloudState("isolated-routing-owner")
    XCTAssertEqual(cached?.filePaths[id], fixture.store.filePaths[id])
    XCTAssertEqual(cached?.sources.first { $0.id == id }?.chunks.count, 2)
    XCTAssertEqual(try Data(contentsOf: original), bytes)
  }

  func testCancellingImportWhileCellularQuestionIsOpenNeverUploads() async throws {
    let fixture = try NetworkRoutingFixture(network: .cellular)
    defer { fixture.close() }
    let file = try pdf()
    let importing = Task { await fixture.store.importFile(file) }
    _ = try await prompt(fixture.policy)
    importing.cancel()
    await importing.value
    await fixture.store.flush()
    XCTAssertNil(fixture.policy.prompt)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
    XCTAssertTrue(fixture.store.sources.isEmpty)
    XCTAssertTrue(fixture.store.draft.resourceIDs.isEmpty)
    XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))
  }

  func testCancelImportWhileProcessingWaitsForNetworkChoiceKeepsOriginalAndSendsNoCancelHTTP() async throws {
    let fixture = try NetworkRoutingFixture(network: .offline)
    defer { fixture.close() }
    await fixture.store.importFile(try pdf())
    let id = try XCTUnwrap(fixture.store.selectedBookID)
    let original = try XCTUnwrap(fixture.store.sourceFileURL(id))
    fixture.policy.reset()
    fixture.policy.updatePath(ProcessingNetworkPath(kind: .cellular))
    let processing = Task { await fixture.store.processImport() }
    _ = try await prompt(fixture.policy)
    fixture.store.cancelImport()
    await processing.value
    await fixture.store.flush()
    XCTAssertNil(fixture.policy.prompt)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
    XCTAssertFalse(fixture.store.draft.parseCompleted)
    XCTAssertTrue(FileManager.default.fileExists(atPath: original.path))
  }

  func testLocalRetrievalUsesOnlyRequestedCachedBookAndNeverCloudQA() async throws {
    let fixture = try NetworkRoutingFixture(network: .offline)
    defer { fixture.close() }
    let first = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "remote-first", filename: "first.pdf")
    let second = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "remote-second", filename: "second.pdf")
    fixture.store.sources = [first, second]
    let answer = try await fixture.store.answerAssistant(question: "Native learning page one",
      bookID: first.id, bookIDs: [first.id], chapterID: nil, sources: [first, second], history: [])
    XCTAssertFalse(answer.citations.isEmpty)
    XCTAssertTrue(answer.citations.allSatisfy { $0.bookID == first.id })
    XCTAssertTrue(answer.answer.contains("本机教材检索"))
    XCTAssertTrue(fixture.probe.requests.isEmpty)
    let missing = try await fixture.store.answerAssistant(question: "Native learning page one",
      bookID: "not-cached", bookIDs: ["not-cached"], chapterID: nil, sources: [first, second], history: [])
    XCTAssertTrue(missing.citations.isEmpty)
    XCTAssertTrue(fixture.probe.requests.isEmpty, "Missing local evidence cannot trigger a cloud fallback.")
  }

  func testStableWiFiRetainsExistingPDFUploadInsteadOfSilentlySwitchingToLocal() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    fixture.probe.respond { request in
      XCTAssertEqual(request.httpMethod, "POST")
      XCTAssertEqual(request.url?.path, "/api/native/uploads")
      return (200, try JSONEncoder().encode(CloudUpload(book_id: "server-upload",
        filename: "text.pdf", status: "uploaded")))
    }
    await fixture.store.importFile(try pdf())
    await fixture.store.flush()
    XCTAssertNil(fixture.store.importError)
    XCTAssertNil(fixture.policy.prompt)
    XCTAssertEqual(fixture.store.selectedBookID, "server-upload")
    XCTAssertEqual(fixture.probe.requests.count, 1)
    XCTAssertNotNil(fixture.store.sourceFileURL("server-upload"))
    await fixture.api.clearCredentials()
  }

  func testPreparingLocalCourseOnWiFiPreservesOtherMaterialsAndWaitsForExplicitStart() async throws {
    let fixture = try NetworkRoutingFixture(network: .cellular)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "book_local_one", filename: "first.pdf")
    let other = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "book_local_two", filename: "second.pdf")
    let course = Course(id: "local-course:both", name: "两本教材", resourceIDs: [source.id, other.id])
    fixture.store.sources = [source, other]
    fixture.store.courses = [course]
    fixture.store.selectedCourseID = course.id
    fixture.store.route = .courseDetail
    fixture.store.prepareLocalCourseOnWiFi(bookID: source.id)
    XCTAssertEqual(fixture.store.route, .courseDetail)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
    fixture.policy.updatePath(.init(kind: .wifi))
    fixture.store.prepareLocalCourseOnWiFi(bookID: source.id)
    XCTAssertEqual(fixture.store.route, .parseReady)
    XCTAssertEqual(fixture.store.draft.editingCourseID, course.id)
    XCTAssertEqual(fixture.store.draft.resourceIDs, course.resourceIDs)
    XCTAssertFalse(fixture.store.draft.parseCompleted)
    XCTAssertTrue(fixture.probe.requests.isEmpty, "Preparation should not upload before the user starts it")
    await fixture.store.flush()
  }

  func testStableWiFiRetainsExistingServerQA() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "server-book", filename: "text.pdf")
    fixture.store.sources = [source]
    fixture.probe.respond { request in
      XCTAssertEqual(request.httpMethod, "POST")
      XCTAssertEqual(request.url?.path, "/api/books/server-book/qa")
      let answer = CloudQA(book_id: "server-book", status: "answered", answer: "Existing server answer",
        claims: [.init(text: "Original evidence", citations: [.init(page_number: 1, block_id: nil,
          quote: "Native learning page one")])], confidence: 0.9)
      return (200, try JSONEncoder().encode(answer))
    }
    let answer = try await fixture.store.answerAssistant(question: "Native learning page one",
      bookID: source.id, bookIDs: [source.id], chapterID: nil, sources: [source], history: [])
    XCTAssertEqual(answer.answer, "Existing server answer")
    XCTAssertEqual(answer.citations.first?.bookID, source.id)
    XCTAssertEqual(fixture.probe.requests.count, 1)
    XCTAssertNil(fixture.policy.prompt)
    await fixture.api.clearCredentials()
  }
}
