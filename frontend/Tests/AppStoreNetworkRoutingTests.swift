import Foundation
import XCTest

@testable import BookCourse

private func routingRequestBody(_ request: URLRequest) throws -> [String: JSONValue] {
  var data = request.httpBody ?? Data()
  if request.httpBody == nil, let stream = request.httpBodyStream {
    stream.open()
    defer { stream.close() }
    var buffer = [UInt8](repeating: 0, count: 4096)
    while true {
      let count = stream.read(&buffer, maxLength: buffer.count)
      guard count >= 0 else { throw URLError(.cannotDecodeRawData) }
      if count == 0 { break }
      data.append(contentsOf: buffer.prefix(count))
    }
  }
  return try JSONDecoder().decode([String: JSONValue].self, from: data)
}

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

  func testRepeatedPDFImportReusesRunningPreparationAcrossCacheRestore() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    fixture.probe.respond { _ in
      (200, try JSONEncoder().encode(CloudUpload(book_id: "server-upload",
        filename: "text.pdf", status: "uploaded")))
    }
    let file = try pdf()
    await fixture.store.importFile(file)
    let courseID = try XCTUnwrap(fixture.store.draft.importCourseID)
    fixture.store.updateCourseImport(courseID) {
      $0.started = true
      $0.job.status = .running
      $0.job.preparationStage = "diagnostics"
      $0.books[0].status = .running
      $0.books[0].stage = "diagnostics"
      $0.books[0].message = "诊断题已完成 11／15 题"
    }
    await fixture.store.importFile(file)
    await fixture.store.flush()
    XCTAssertEqual(fixture.probe.requests.count, 1, "Identical bytes must not create another server book")
    XCTAssertEqual(fixture.store.courseImport(for: courseID)?.books.count, 1)
    XCTAssertEqual(fixture.store.courseImport(for: courseID)?.books.first?.stage, "diagnostics")

    let saved = try await fixture.repository.loadCloudState("isolated-routing-owner")
    let cached = try XCTUnwrap(saved)
    let restored = AppStore(repository: fixture.repository, cloud: fixture.api,
      processingPolicy: stableWiFiProcessingPolicyForTesting())
    restored.cloudState.authenticated = true
    restored.cloudState.cacheKey = "isolated-routing-owner"
    restored.restoreCloudCache(cached)
    let renamed = fixture.directory.appendingPathComponent("renamed.pdf")
    try Data(contentsOf: file).write(to: renamed)
    await restored.importFile(renamed)
    await restored.flush()
    XCTAssertNil(restored.importError)
    XCTAssertEqual(fixture.probe.requests.count, 1, "The content alias survives restart and renaming")
    XCTAssertEqual(restored.draft.resourceIDs, ["server-upload"])
    XCTAssertEqual(restored.courseImport(for: courseID)?.books.count, 1)
    XCTAssertEqual(restored.courseImport(for: courseID)?.books.first?.message, "诊断题已完成 11／15 题")
  }

  func testDifferentPDFContentsWithSameFilenameRemainSeparate() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    fixture.probe.respond { _ in
      (200, try JSONEncoder().encode(CloudUpload(book_id: UUID().uuidString,
        filename: "text.pdf", status: "uploaded")))
    }
    await fixture.store.importFile(try pdf())
    let different = fixture.directory.appendingPathComponent("text.pdf")
    var bytes = try Data(contentsOf: pdf())
    bytes.append(Data("\n%different edition\n".utf8))
    try bytes.write(to: different)
    await fixture.store.importFile(different)
    await fixture.store.flush()
    XCTAssertEqual(fixture.probe.requests.count, 2)
    XCTAssertEqual(fixture.store.draft.resourceIDs.count, 2)
    XCTAssertEqual(fixture.store.importState.courses.first?.books.count, 2)
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

  func testStableWiFiUsesUnifiedAssistantAndMapsCrossBookEvidence() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "server-book", filename: "text.pdf")
    fixture.store.sources = [source]
    fixture.probe.respond { request in
      XCTAssertEqual(request.httpMethod, "POST")
      XCTAssertEqual(request.url?.path, "/api/assistant/query")
      XCTAssertEqual(request.timeoutInterval, 330)
      let answer = CloudAssistantQuery(answer: "Unified answer", citations: [
        .init(book_id: source.id, page_number: 1, block_id: nil,
          quote: "Native learning page one", evidence_id: "evidence-1"),
        .init(book_id: "another-book", page_number: 2, block_id: "block-2",
          quote: "Cross-book evidence", evidence_id: "evidence-2"),
      ])
      return (200, try JSONEncoder().encode(answer))
    }
    let answer = try await fixture.store.answerAssistant(question: "Native learning page one",
      bookID: source.id, bookIDs: [source.id], chapterID: nil, sources: [source], history: [])
    XCTAssertEqual(answer.answer, "Unified answer")
    XCTAssertEqual(answer.citations.first?.bookID, source.id)
    XCTAssertEqual(answer.citations.last?.bookID, "another-book")
    XCTAssertEqual(fixture.probe.requests.count, 1)
    XCTAssertNil(fixture.policy.prompt)
    await fixture.api.clearCredentials()
  }

  func testSelectionRemainsAHintAndOnlyDeviceCoursesAreClientMetadata() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let primary = "server-book", second = "server-second", local = "book_local_pending"
    let workspace = "workspace-qa"
    let original = Data("retained local original".utf8)
    let path = try await fixture.repository.saveAttachment(original, extension: "pdf")
    fixture.store.filePaths[local] = path
    fixture.store.selectedCourseID = workspace
    fixture.store.selectedBookID = primary
    fixture.store.courses = [Course(id: workspace, name: "在线课程",
      resourceIDs: [primary, local, second], activeResourceID: primary),
      Course(id: "local-course:pending", name: "本机待处理课程", resourceIDs: [local])]
    fixture.store.cloudState.workspaces = [CloudWorkspace(id: workspace, name: "在线课程",
      preferences: [:], created: 0, updated: 0, resources: [primary, second].map {
        CloudResource(id: "resource-" + $0, book_id: $0, workspace_id: workspace,
          revision: 0, filename: "教材.pdf", status: "structured", structure: nil,
          courses: [], progress: CloudProgress())
      })]
    fixture.probe.respond { request in
      XCTAssertEqual(request.url?.path, "/api/assistant/query")
      let body = try routingRequestBody(request)
      guard case .object(let context)? = body["context"], case .array(let locals)? = body["local_courses"] else {
        XCTFail("Expected separate page hint and local metadata"); throw URLError(.badServerResponse)
      }
      XCTAssertEqual(context["book_ids"], .array([primary, local, "foreign-book", second].map(JSONValue.string)))
      XCTAssertEqual(locals.count, 1, "Server courses are loaded from authenticated tools, never client facts")
      guard case .object(let pending) = locals[0] else { throw URLError(.badServerResponse) }
      XCTAssertEqual(pending["id"], .string("local-course:pending"))
      return (200, try JSONEncoder().encode(CloudAssistantQuery(answer: "回答可用", citations: [])))
    }
    let answer = try await fixture.store.answerAssistant(question: "请解释", bookID: primary,
      bookIDs: [primary, local, "foreign-book", second, second], chapterID: nil,
      sources: [], history: [])
    XCTAssertEqual(answer.answer, "回答可用")
    XCTAssertEqual(fixture.probe.requests.count, 1)
    XCTAssertEqual(fixture.store.currentCourse?.resourceIDs, [primary, local, second])
    XCTAssertEqual(fixture.store.filePaths[local], path)
    XCTAssertEqual(try Data(contentsOf: fixture.repository.attachmentURL(path)), original)
    await fixture.api.clearCredentials()
  }

  func testLongReaderHintsUseTheirOwnBoundedFieldsWithoutChangingTheQuestion() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let question = String(repeating: "问", count: 1999)
    let reading = CloudReadingContext(pdfPage: 3,
      pageText: String(repeating: "👨‍👩‍👧‍👦", count: 1200), noteText: String(repeating: "注", count: 4001))
    fixture.probe.respond { request in
      let body = try routingRequestBody(request)
      XCTAssertEqual(body["question"], .string(question))
      guard case .object(let pageContext)? = body["context"],
        case .object(let context)? = pageContext["reading_context"] else {
        XCTFail("Reading hints must not be concatenated into question"); throw URLError(.badServerResponse)
      }
      XCTAssertEqual(context["pdf_page"], .number(3))
      XCTAssertEqual(context["note_text"]?.stringValue?.unicodeScalars.count, 4000)
      XCTAssertEqual(context["page_text"]?.stringValue?.unicodeScalars.count, 1200)
      XCTAssertEqual(context["note_truncated"], .bool(true))
      XCTAssertEqual(request.url?.path, "/api/assistant/query")
      return (200, try JSONEncoder().encode(CloudAssistantQuery(answer: "独立上下文已收到", citations: [])))
    }
    let result = try await fixture.store.answerAssistant(question: question,
      bookID: "library-book", bookIDs: ["library-book"], chapterID: nil,
      sources: [], history: [], readingContext: reading)
    XCTAssertEqual(result.answer, "独立上下文已收到")
    XCTAssertEqual(fixture.probe.requests.count, 1)
    await fixture.api.clearCredentials()
  }

  func testDiscoveryAndNoSelectedBookUseTheSameConversationEndpoint() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    fixture.store.route = .community
    let context = fixture.store.assistantSessionContext
    XCTAssertTrue(context.discovery)
    XCTAssertTrue(context.bookID.isEmpty)
    var conversation: [ChatMessage] = []
    let questions = ["当前有哪些课程？", "目前正在学习的课程有哪些？", "当前正在学习的课程"]
    fixture.probe.respond { request in
      XCTAssertEqual(request.url?.path, "/api/assistant/query")
      let body = try routingRequestBody(request)
      XCTAssertNotNil(body["request_id"]?.stringValue.flatMap(UUID.init(uuidString:)))
      guard case .object(let hint)? = body["context"] else { throw URLError(.badServerResponse) }
      XCTAssertEqual(hint["book_id"], .null)
      return (200, try JSONEncoder().encode(CloudAssistantQuery(answer: "你有两门课程。", citations: [])))
    }
    for question in questions {
      conversation.append(ChatMessage(role: "user", text: question))
      let response = try await fixture.store.answerHistorySession(context: context, history: conversation,
        question: question)
      XCTAssertEqual(response.text, "你有两门课程。")
      XCTAssertTrue(response.courseIDs.isEmpty)
      conversation.append(ChatMessage(role: "assistant", text: response.text))
    }
    XCTAssertEqual(fixture.probe.requests.count, 3)
    let last = try routingRequestBody(XCTUnwrap(fixture.probe.requests.last))
    guard case .array(let history)? = last["history"] else { throw URLError(.badServerResponse) }
    XCTAssertEqual(history.count, 4, "Prior clarification and user replies must remain available to the same model")
    XCTAssertEqual(last["question"], .string(questions[2]))
    await fixture.api.clearCredentials()
  }

  func testUnifiedChatOnDeviceChoiceNeverPostsConversationOrCourseMetadata() async throws {
    let fixture = try NetworkRoutingFixture(network: .cellular)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "cached-book", filename: "text.pdf")
    fixture.store.sources = [source]
    fixture.store.selectedBookID = source.id
    let context = fixture.store.assistantSessionContext
    let question = "Native learning page one"
    let task = Task {
      try await fixture.store.answerHistorySession(context: context,
        history: [ChatMessage(role: "user", text: question)], question: question)
    }
    let choice = try await prompt(fixture.policy)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
    fixture.policy.resolvePrompt(.onDevice, promptID: choice.id)
    let response = try await task.value
    XCTAssertTrue(response.text.contains("本机教材检索"))
    XCTAssertFalse(response.citations.isEmpty)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
  }

  func testLongConversationBoundsOnlyTheRequestAndKeepsRecentMessages() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let conversation = (0..<100).map { index in
      ChatMessage(role: index.isMultiple(of: 2) ? "user" : "assistant",
        text: "message-\(index):" + String(repeating: "字", count: 4500))
    }
    fixture.probe.respond { request in
      let body = try routingRequestBody(request)
      guard case .array(let turns)? = body["history"] else { throw URLError(.badServerResponse) }
      XCTAssertLessThanOrEqual(turns.count, 40)
      let contents = turns.compactMap { value -> String? in
        guard case .object(let item) = value else { return nil }
        return item["content"]?.stringValue
      }
      XCTAssertLessThanOrEqual(contents.reduce(0) { $0 + $1.unicodeScalars.count }, 50_000)
      XCTAssertTrue(contents.last?.hasPrefix("message-99:") == true)
      XCTAssertTrue(contents.allSatisfy { $0.unicodeScalars.count <= 4000 })
      return (200, try JSONEncoder().encode(CloudAssistantQuery(answer: "继续回答", citations: [])))
    }
    let answer = try await fixture.store.answerAssistant(question: "继续", bookID: "", bookIDs: [],
      chapterID: nil, sources: [], history: conversation)
    XCTAssertEqual(answer.answer, "继续回答")
    XCTAssertEqual(conversation.count, 100)
    XCTAssertTrue(conversation[0].text.hasPrefix("message-0:"))
    XCTAssertGreaterThan(conversation[0].text.count, 4000)
    await fixture.api.clearCredentials()
  }

  func testRepeatedQuestionDoesNotReuseAnOlderHistoryMessageID() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let oldQuestion = ChatMessage(role: "user", text: "再解释一下")
    let history = [oldQuestion, ChatMessage(role: "assistant", text: "之前的解释")]
    fixture.probe.respond { _ in
      (200, try JSONEncoder().encode(CloudAssistantQuery(answer: "新的解释", citations: [])))
    }
    for _ in 0..<2 {
      _ = try await fixture.store.answerAssistant(question: oldQuestion.text, bookID: "", bookIDs: [],
        chapterID: nil, sources: [], history: history)
    }
    let ids = try fixture.probe.requests.map { try routingRequestBody($0)["request_id"]?.stringValue }
    XCTAssertEqual(ids.count, 2)
    XCTAssertNotEqual(ids[0], oldQuestion.id)
    XCTAssertNotEqual(ids[0], ids[1])
    await fixture.api.clearCredentials()
  }

  private func waitForHistory(_ history: AssistantHistoryStore) async throws {
    let deadline = ContinuousClock.now + .seconds(5)
    while !history.runningIDs.isEmpty, ContinuousClock.now < deadline {
      try await Task.sleep(for: .milliseconds(10))
    }
    XCTAssertTrue(history.runningIDs.isEmpty)
    await history.flush()
  }

  func testFailedTurnPersistsExactPayloadAndRetryReusesItAfterRelaunch() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let history = fixture.store.assistantHistory
    await history.load(scope: fixture.store.assistantHistoryScope)
    fixture.store.selectedBookID = "first-book"
    fixture.store.courses = [Course(id: "local-course:first", name: "第一门待处理课", resourceIDs: ["book_local_first"])]
    let context = fixture.store.assistantSessionContext
    let question = "当前有哪些课程？"
    fixture.probe.respond { _ in (503, Data("{\"detail\":\"稍后重试\"}".utf8)) }
    history.submit(question, context: context) { context, messages, question in
      try await fixture.store.answerHistorySession(context: context, history: messages, question: question)
    }
    try await waitForHistory(history)
    XCTAssertNotNil(history.current?.error)
    let initial = try routingRequestBody(XCTUnwrap(fixture.probe.requests.first))
    let saved = try await fixture.repository.loadAssistantHistory(scope: "isolated-routing-owner")
    XCTAssertEqual(saved?.sessions.first?.messages.first?.unifiedQueryPayload, initial)

    history.reset()
    await history.load(scope: fixture.store.assistantHistoryScope)
    fixture.store.selectedBookID = "second-book"
    fixture.store.courses = [Course(id: "local-course:second", name: "第二门待处理课", resourceIDs: ["book_local_second"])]
    fixture.probe.respond { request in
      XCTAssertEqual(try routingRequestBody(request), initial, "Retry must not bill a changed request with the old ID")
      return (200, try JSONEncoder().encode(CloudAssistantQuery(answer: "已恢复回答", citations: [])))
    }
    history.retry { context, messages, question in
      XCTAssertEqual(context.bookID, "first-book")
      return try await fixture.store.answerHistorySession(context: context, history: messages, question: question)
    }
    try await waitForHistory(history)
    XCTAssertEqual(fixture.probe.requests.count, 2)
    XCTAssertEqual(history.current?.messages.last?.text, "已恢复回答")
    XCTAssertNil(history.current?.pendingQuestion)
    XCTAssertNil(history.current?.messages.first?.unifiedQueryPayload)
    await fixture.api.clearCredentials()
  }

  func testNewTurnCapturesCurrentPageInsteadOfTheOriginalSessionScope() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let history = fixture.store.assistantHistory
    await history.load(scope: fixture.store.assistantHistoryScope)
    fixture.store.selectedBookID = "first-book"
    let first = fixture.store.assistantSessionContext
    history.submit("先看第一本", context: first) { context, _, _ in
      XCTAssertEqual(context.bookID, "first-book")
      return AssistantHistoryAnswer(text: "第一本")
    }
    try await waitForHistory(history)
    let sessionID = history.current?.id
    fixture.store.selectedBookID = "second-book"
    let second = fixture.store.assistantSessionContext
    history.submit("这本书呢？", context: second) { context, messages, _ in
      XCTAssertEqual(context.bookID, "second-book")
      XCTAssertEqual(messages.map(\.text), ["先看第一本", "第一本", "这本书呢？"])
      return AssistantHistoryAnswer(text: "第二本")
    }
    try await waitForHistory(history)
    XCTAssertEqual(history.current?.id, sessionID)
    XCTAssertEqual(history.current?.context.bookID, "second-book")
  }
}
