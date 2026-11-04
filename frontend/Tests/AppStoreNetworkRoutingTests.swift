import CryptoKit
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

  init(network: ProcessingNetworkKind, legacyQuery: Bool = false) throws {
    directory = FileManager.default.temporaryDirectory.appendingPathComponent("routing-" + UUID().uuidString)
    repository = try LocalBookCourseRepository(directory: directory)
    host = "routing-" + UUID().uuidString.lowercased() + ".example"
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [NetworkRoutingProtocol.self]
    session = URLSession(configuration: configuration)
    api = CloudAPI(baseURL: URL(string: "https://" + host)!, session: session, legacyPrivateSyncEnabled: legacyQuery)
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
    let cached = try await fixture.repository.loadPersonalState(scope: "isolated-routing-owner")
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
    fixture.store.cloudState.localFirstChecked = true
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
    fixture.store.cloudState.localFirstChecked = true
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

    let saved = try await fixture.repository.loadPersonalState(scope: "isolated-routing-owner")
    let cached = try XCTUnwrap(saved)
    let restored = AppStore(repository: fixture.repository, cloud: fixture.api,
      processingPolicy: stableWiFiProcessingPolicyForTesting())
    restored.cloudState.authenticated = true
    restored.cloudState.cacheKey = "isolated-routing-owner"
    restored.cloudState.privateLocalMode = true
    restored.restorePrivateState(cached)
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
    fixture.store.cloudState.localFirstChecked = true
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
    let fixture = try NetworkRoutingFixture(network: .wifi, legacyQuery: true)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "server-book", filename: "text.pdf")
    fixture.store.sources = [source]
    fixture.probe.respond { request in
      XCTAssertEqual(request.httpMethod, "POST")
      XCTAssertEqual(request.url?.path, "/api/assistant/query")
      XCTAssertEqual(request.timeoutInterval, 290)
      let answer = CloudAssistantQuery(answer: "Unified answer", citations: [
        .init(book_id: source.id, page_number: 1, block_id: nil,
          quote: "Native learning page one", evidence_id: "evidence-1"),
        .init(book_id: "another-book", page_number: 2, block_id: "block-2",
          quote: "Cross-book evidence", evidence_id: "evidence-2"),
      ], source_label: "textbook")
      return (200, try JSONEncoder().encode(answer))
    }
    let answer = try await fixture.store.answerAssistant(question: "Native learning page one",
      bookID: source.id, bookIDs: [source.id], chapterID: nil, sources: [source], history: [])
    XCTAssertEqual(answer.answer, "Unified answer")
    XCTAssertEqual(answer.sourceLabel, "textbook")
    XCTAssertEqual(answer.citations.first?.bookID, source.id)
    XCTAssertEqual(answer.citations.last?.bookID, "another-book")
    XCTAssertEqual(fixture.probe.requests.count, 1)
    XCTAssertNil(fixture.policy.prompt)
    await fixture.api.clearCredentials()
  }

  func testPendingLocalImportSendsOnlyDeviceEvidenceNeverServerScope() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi, legacyQuery: true)
    defer { fixture.close() }
    let localID = "book_local_pending_evidence"
    let serverID = "server-pending-scope"
    let local = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: localID, filename: "local.pdf")
    let server = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: serverID, filename: "server.pdf")
    fixture.store.sources = [local, server]
    fixture.probe.respond { _ in
      (200, try JSONEncoder().encode(CloudAssistantQuery(answer: "已收到本机证据", citations: [])))
    }
    let question = "Native learning page one"
    _ = try await fixture.store.answerAssistant(question: question, bookID: serverID,
      bookIDs: [serverID, localID], chapterID: nil, sources: [local, server], history: [])
    let withLocal = try routingRequestBody(XCTUnwrap(fixture.probe.requests.last))
    guard case .array(let evidence)? = withLocal["device_evidence"] else {
      XCTFail("Pending local books must send a device_evidence array"); throw URLError(.badServerResponse)
    }
    XCTAssertFalse(evidence.isEmpty)
    for item in evidence {
      guard case .object(let entry) = item else { throw URLError(.badServerResponse) }
      XCTAssertEqual(entry["book_id"], .string(localID))
      XCTAssertGreaterThanOrEqual(entry["page_number"]?.numberValue ?? 0, 1)
      XCTAssertFalse(entry["quote"]?.stringValue?.isEmpty ?? true)
    }
    XCTAssertFalse(evidence.contains { item in
      guard case .object(let entry) = item else { return false }
      return entry["book_id"] == .string(serverID)
    })

    _ = try await fixture.store.answerAssistant(question: question, bookID: serverID,
      bookIDs: [serverID], chapterID: nil, sources: [local, server], history: [])
    let withoutLocal = try routingRequestBody(XCTUnwrap(fixture.probe.requests.last))
    XCTAssertNil(withoutLocal["device_evidence"],
      "Server-scoped books must never be described as device evidence")
    await fixture.api.clearCredentials()
  }

  func testHistoryStorePersistsExecutorSourceLabelAndCourseLinks() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let known = "community_functions"
    let history = fixture.store.assistantHistory
    await history.load(scope: fixture.store.assistantHistoryScope)
    history.submit("我之前学到哪里了？", context: fixture.store.assistantSessionContext) { _, _, _ in
      AssistantHistoryAnswer(text: "已根据学习记录回答", courseIDs: [known], sourceLabel: "learning_records")
    }
    try await waitForHistory(history)
    let message = try XCTUnwrap(history.current?.messages.last)
    XCTAssertEqual(message.role, "assistant")
    XCTAssertEqual(message.sourceLabel, "learning_records")
    XCTAssertEqual(history.current?.courseLinks[message.id], [known])
    await history.flush()
    let reopened = AssistantHistoryStore(repository: fixture.repository)
    await reopened.load(scope: fixture.store.assistantHistoryScope)
    XCTAssertEqual(reopened.current?.messages.last?.sourceLabel, "learning_records")
    XCTAssertEqual(reopened.current?.courseLinks[message.id], [known])
    XCTAssertTrue(fixture.probe.requests.isEmpty)
  }

  func testLegacyChatMessageWithoutSourceLabelDecodesAsNil() throws {
    let data = Data(#"{"id":"legacy-message","role":"assistant","text":"旧回答","citations":[]}"#.utf8)
    let message = try JSONDecoder().decode(ChatMessage.self, from: data)
    XCTAssertEqual(message.id, "legacy-message")
    XCTAssertEqual(message.text, "旧回答")
    XCTAssertNil(message.sourceLabel)
    XCTAssertNil(message.sourceLabelText)
  }

  func testSelectionRemainsAHintAndOnlyDeviceCoursesAreClientMetadata() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi, legacyQuery: true)
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
    let fixture = try NetworkRoutingFixture(network: .wifi, legacyQuery: true)
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

  func testDiscoveryAndNoSelectedBookUseUnifiedAgentTurnWithoutQuery() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    fixture.store.route = .community
    let context = fixture.store.assistantSessionContext
    XCTAssertTrue(context.discovery)
    XCTAssertTrue(context.bookID.isEmpty)
    fixture.probe.respond { request in
      if request.url?.path.hasSuffix("/ack") == true { return (200, Data("{}".utf8)) }
      if request.url?.path == "/api/assistant/conversations" {
        return (200, Data("[]".utf8))
      }
      XCTAssertEqual(request.url?.path, "/api/assistant/turn")
      let body = try routingRequestBody(request)
      let object: [String: Any] = [
        "contract_version": 3,
        "run_id": body["run_id"]?.stringValue ?? "",
        "step_id": body["step_id"]?.stringValue ?? "",
        "step_index": 0, "state": "completed", "attempt": 1,
        "message": ["content": "你有两门课程。", "tool_calls": []] as [String: Any],
        "budget": ["steps": 1, "steps_max": 8, "posts": 1, "posts_max": 24,
          "input_tokens": 0, "input_tokens_max": 100000, "output_tokens": 0,
          "output_tokens_max": 10000, "model_seconds": 0, "model_seconds_max": 120,
          "cached_input_tokens": 0],
      ]
      return (200, try JSONSerialization.data(withJSONObject: object))
    }
    let response = try await fixture.store.answerHistorySession(context: context,
      history: [ChatMessage(role: "user", text: "当前有哪些课程？")], question: "当前有哪些课程？")
    XCTAssertEqual(response.text, "你有两门课程。")
    XCTAssertFalse(fixture.probe.requests.contains { $0.url?.path == "/api/assistant/query" })
    XCTAssertEqual(fixture.probe.requests.filter { $0.url?.path == "/api/assistant/turn" }.count, 1)
    await fixture.api.clearCredentials()
  }

  func testRetainedQueryTransportOnDeviceChoiceNeverPostsConversationOrCourseMetadata() async throws {
    let fixture = try NetworkRoutingFixture(network: .cellular)
    defer { fixture.close() }
    let source = try LocalFileImporter.extractSynchronously(url: pdf(), bookID: "cached-book", filename: "text.pdf")
    fixture.store.sources = [source]
    fixture.store.selectedBookID = source.id
    let question = "Native learning page one"
    let task = Task {
      try await fixture.store.answerAssistant(question: question, bookID: source.id,
        bookIDs: [source.id], chapterID: nil, sources: [source], history: [])
    }
    let choice = try await prompt(fixture.policy)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
    fixture.policy.resolvePrompt(.onDevice, promptID: choice.id)
    let response = try await task.value
    XCTAssertTrue(response.answer.contains("本机教材检索"))
    XCTAssertFalse(response.citations.isEmpty)
    XCTAssertTrue(fixture.probe.requests.isEmpty)
  }

  func testLongConversationBoundsOnlyTheRequestAndKeepsRecentMessages() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi, legacyQuery: true)
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
    let fixture = try NetworkRoutingFixture(network: .wifi, legacyQuery: true)
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

  func testRetainedQueryTransportPersistsExactPayloadAndRetryReusesItAfterRelaunch() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi, legacyQuery: true)
    defer { fixture.close() }
    let history = fixture.store.assistantHistory
    await history.load(scope: fixture.store.assistantHistoryScope)
    fixture.store.route = .study
    fixture.store.selectedCourseID = "local-course:first"
    fixture.store.selectedBookID = "first-book"
    fixture.store.courses = [Course(id: "local-course:first", name: "第一门待处理课", resourceIDs: ["first-book"])]
    let context = fixture.store.assistantSessionContext
    let question = "当前有哪些课程？"
    fixture.probe.respond { _ in (503, Data("{\"detail\":\"稍后重试\"}".utf8)) }
    history.submit(question, context: context) { context, messages, question in
      let result = try await fixture.store.answerAssistant(question: question, bookID: context.bookID,
        bookIDs: context.bookIDs, chapterID: context.chapterID, sources: [], history: messages,
        bindToCurrentSelection: false, requestID: context.messageID, requestContext: context)
      return AssistantHistoryAnswer(text: result.answer, citations: result.citations, sourceLabel: result.sourceLabel)
    }
    try await waitForHistory(history)
    XCTAssertNotNil(history.current?.error)
    let initial = try routingRequestBody(XCTUnwrap(fixture.probe.requests.first))
    let saved = try await fixture.repository.loadAssistantHistory(scope: "isolated-routing-owner")
    XCTAssertEqual(saved?.sessions.first?.messages.first?.unifiedQueryPayload, initial)

    history.reset()
    await history.load(scope: fixture.store.assistantHistoryScope)
    fixture.store.selectedCourseID = "local-course:second"
    fixture.store.selectedBookID = "second-book"
    fixture.store.courses = [Course(id: "local-course:second", name: "第二门待处理课", resourceIDs: ["second-book"])]
    fixture.probe.respond { request in
      XCTAssertEqual(try routingRequestBody(request), initial, "Retry must not bill a changed request with the old ID")
      return (200, try JSONEncoder().encode(CloudAssistantQuery(answer: "已恢复回答", citations: [])))
    }
    history.retry { context, messages, question in
      XCTAssertEqual(context.bookID, "first-book")
      let result = try await fixture.store.answerAssistant(question: question, bookID: context.bookID,
        bookIDs: context.bookIDs, chapterID: context.chapterID, sources: [], history: messages,
        bindToCurrentSelection: false, requestID: context.messageID, requestContext: context)
      return AssistantHistoryAnswer(text: result.answer, citations: result.citations, sourceLabel: result.sourceLabel)
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
    fixture.store.route = .study
    fixture.store.courses = [
      Course(id: "course-first", name: "第一门课程", resourceIDs: ["first-book"]),
      Course(id: "course-second", name: "第二门课程", resourceIDs: ["second-book"]),
    ]
    fixture.store.selectedCourseID = "course-first"
    fixture.store.selectedBookID = "first-book"
    let first = fixture.store.assistantSessionContext
    history.submit("先看第一本", context: first) { context, _, _ in
      XCTAssertEqual(context.bookID, "first-book")
      return AssistantHistoryAnswer(text: "第一本")
    }
    try await waitForHistory(history)
    let sessionID = history.current?.id
    fixture.store.selectedCourseID = "course-second"
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

extension AppStoreNetworkRoutingTests {
  func testJoinedBooksPersonalLessonAndVoiceNoteRemainSearchableAfterRestart() async throws {
    let fixture = try NetworkRoutingFixture(network: .wifi)
    defer { fixture.close() }
    let store = fixture.store
    let base = "/api/public/library/course-groups/group_test"
    var downloads: [String: Data] = [:]
    let books = try ["book-a", "book-b"].map { id in
      let chapter = CloudChapter(chapter_id: "ch", order: 1, title: "向量", start_page: 1, end_page: 1,
        summary: "", knowledge_points: [], source_block_ids: [])
      let files = try [("pdf", Data(contentsOf: pdf())), ("ocr_pages", Data("{\"page_number\":1,\"text\":\"向量有方向 \(id)\"}".utf8)),
        ("diagnostics", Data("{\"items\":[]}".utf8)), ("grading", Data("{\"items\":[]}".utf8))].map { kind, data in
          let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
          let path = base + "/assets/" + hash
          downloads[path] = data
          return PublicMaterialFile(asset_id: kind, kind: kind, sha256: hash, bytes: data.count,
            media_type: kind == "pdf" ? "application/pdf" : "application/json", download_url: path)
        }
      return PublicMaterialBook(book_id: id, title: id, content_version: "v1", structure: CloudStructure(
        title: id, summary: "", source_page_count: 1, chapters: [chapter]), files: files, figures: [])
    }
    downloads[base + "/manifest"] = try JSONEncoder().encode(PublicMaterialManifest(schema_version: 1, group_id: "group_test",
      title: "数学", description: "", version: "v1", books: books))
    downloads["/api/native/local-first/capabilities"] = Data(#"{"schema_version":1,"public_library":true,"private_forwarding":true,"private_export":true,"legacy_writes_active":true}"#.utf8)
    fixture.probe.respond { request in
      let path = try XCTUnwrap(request.url?.path)
      if let bytes = downloads[path] { return (200, bytes) }
      if path.hasSuffix("/ack") { return (200, Data("{}".utf8)) }
      XCTAssertEqual(path, "/api/assistant/private-model")
      let body = try routingRequestBody(request)
      guard case .array(let messages) = body["messages"], case .object(let message) = messages.last else { throw URLError(.badServerResponse) }
      let input = try JSONDecoder().decode([String: JSONValue].self,
        from: Data(try XCTUnwrap(message["content"]?.stringValue).utf8))
      let result: [String: Any]
      if body["operation"] == .string("lecture") {
        XCTAssertEqual(input["preferences"], .object(["goal": .array([.string("复习")])]))
        let citation: [String: Any] = ["page_number": 1, "quote": "向量有方向"]
        let section: [String: Any] = ["title": "定向练习", "content": "画一个向量", "purpose": "复习", "citations": [citation]]
        let course: [String: Any] = ["course_id": try XCTUnwrap(input["publication_id"]?.stringValue), "version": 1,
          "chapter_id": "ch", "chapter_title": "向量", "opening": "定向练习", "summary": "定向练习",
          "original_reading": [section], "knowledge_points": [["point_id":"p","title":"方向","explanation":"定向练习","mastery":0,"citations":[citation]]],
          "units": [["unit_id":"u","title":"定向练习","knowledge_point_ids":["p"],"sections":[section],"estimated_minutes":5]],
          "flashcards": [], "worked_examples": [], "checkpoint_questions": [], "practice_items": [], "estimated_minutes": 5,
          "created_at": "2026-11-04", "unresolved_source_warnings": []]
        result = ["course": course, "practice_grading": []]
      } else {
        XCTAssertEqual(body["operation"], .string("plan"))
        result = ["user_id":"owner","book_id":"book-a","days":1,"daily_minutes":5,
          "tasks":[["task_id":"task","user_id":"owner","day":1,"title":"练习","task_type":"learn","minutes":5,
            "chapter_id":"ch","lesson_id":try XCTUnwrap(input["lesson_id"]?.stringValue),"status":"pending","weak_points":[]]]]
      }
      let content = String(decoding: try JSONSerialization.data(withJSONObject: result), as: UTF8.self)
      return (200, try JSONEncoder().encode(PrivateModelStepResponseDTO(request_id: try XCTUnwrap(body["request_id"]?.stringValue),
        state: "completed", content: content)))
    }
    try await store.verifyLocalFirstCapabilities()
    let course = try await store.acquirePublicCourseGroup(catalogID: "group_test", name: "数学")
    XCTAssertEqual(course.resourceIDs, ["book-a", "book-b"])
    XCTAssertTrue(store.draft.diagnosis.isEmpty)
    store.draft.diagnosis = ["goal": ["复习"]]
    await store.confirmCloudChapters()
    try await store.beginLocalDiagnosis(chapterID: "ch", preview: true)
    try await store.saveNote(StudyNote(kind: .voice, title: "语音", anchor: NoteAnchor(courseID: course.id, bookID: "book-a", page: 1),
      transcript: "录音里的向量例子"))
    store.completeLesson()
    try await store.persistPrivateState()
    let requestsBeforeSearch = fixture.probe.requests.count
    let reopened = AppStore(repository: try LocalBookCourseRepository(directory: fixture.directory), cloud: fixture.api)
    reopened.cloudState.authenticated = true
    reopened.cloudState.cacheKey = store.cloudState.cacheKey
    reopened.cloudState.privateLocalMode = true
    let state = try await reopened.repository.loadPersonalState(scope: try XCTUnwrap(store.noteScope))
    reopened.restorePrivateState(try XCTUnwrap(state))
    await reopened.reloadNotes(resumeAgent: false)
    for (kind, keyword) in [("lessons","定向练习"), ("notes","录音里的向量例子"), ("plans","练习"), ("progress","ch")] {
      let response = try await reopened.readLocalPersonalLearning(args: ["course_id": .string(course.id),
        "kind": .string(kind), "keyword": .string(keyword)])
      XCTAssertGreaterThan(response["total"]?.numberValue ?? 0, 0, kind)
    }
    let hits = try await AppleTextbookSearch(useSystemEmbeddings: false).search(query: "向量有方向", sources: reopened.sources,
      bookIDs: Set(course.resourceIDs), limit: 8)
    XCTAssertEqual(Set(hits.hits.filter(\.reliable).map(\.chunk.book_id)), Set(course.resourceIDs))
    XCTAssertEqual(fixture.probe.requests.count, requestsBeforeSearch)
  }
}
