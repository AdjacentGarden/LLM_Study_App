import CryptoKit
import Foundation
import XCTest

@testable import BookCourse

private final class VoiceGenerationProtocol: URLProtocol, @unchecked Sendable {
  typealias Handler = (URLRequest) throws -> (Int, Data)
  nonisolated(unsafe) static var handler: Handler?
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      let (status, bytes) = try Self.handler!(request)
      client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status,
        httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: bytes)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

/// A transport fixture; the bytes are never sent to a speech service.
private final class VoiceGenerationFixture: @unchecked Sendable {
  let file: URL
  let note: StudyNote
  let api: CloudAPI
  var remoteExists = true
  var remoteReady = true
  var remoteRevision = 2
  var remoteHash: String
  var analysisPosts = 0
  var editionPosts = 0
  var uploaded = 0
  var requests: [URLRequest] = []
  var requestIDs: [String] = []
  var initialStatus = "succeeded"
  var polledStatus = "succeeded"
  var loseAnalyzeReply = false
  var missingJob = false
  var polished = "整理后的学习笔记：先说明观察，再解释依据。"
  var transcriptOnly = false
  var currentJobID = "voice-job"
  private let session: URLSession

  init() throws {
    file = FileManager.default.temporaryDirectory.appendingPathComponent("voice-generation-\(UUID().uuidString).m4a")
    var bytes = Data(repeating: 9, count: 64)
    bytes.replaceSubrange(4..<12, with: Data("ftypM4A ".utf8))
    try bytes.write(to: file)
    remoteHash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    note = StudyNote(kind: .voice, title: "我的语音", anchor: NoteAnchor(bookID: "book_voice", page: 3), duration: 4)
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [VoiceGenerationProtocol.self]
    session = URLSession(configuration: configuration)
    api = CloudAPI(baseURL: URL(string: "https://voice-generation-\(UUID().uuidString).ts.net")!, session: session)
    VoiceGenerationProtocol.handler = { [weak self] request in
      guard let self else { throw URLError(.cancelled) }
      return try self.respond(request)
    }
  }
  deinit {
    session.invalidateAndCancel()
    try? FileManager.default.removeItem(at: file)
  }
  private func json(_ value: Any, status: Int = 200) throws -> (Int, Data) {
    (status, try JSONSerialization.data(withJSONObject: value))
  }
  func body(_ request: URLRequest) throws -> [String: Any] {
    var bytes = request.httpBody ?? Data()
    if let stream = request.httpBodyStream {
      stream.open(); defer { stream.close() }
      var buffer = [UInt8](repeating: 0, count: 2048)
      while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count <= 0 { break }
        bytes.append(contentsOf: buffer.prefix(count))
      }
    }
    return try XCTUnwrap(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
  }
  private func remote(_ id: String) -> [String: Any] {
    ["id": id, "book_id": "book_voice", "revision": remoteRevision,
      "audio_ready": remoteReady, "audio_sha256": remoteHash]
  }
  private func job(_ status: String) -> [String: Any] {
    let result: [String: Any] = transcriptOnly ? ["transcript": "原始转写秘密"]
      : ["polished": polished, "summary": "核心想法", "phase": status == "succeeded" ? "succeeded" : "organizing"]
    return ["id": currentJobID, "book_id": "book_voice", "kind": "voice_note", "status": status,
      "result": result, "request_id": requestIDs.last ?? "", "error": status == "failed" ? "上游暂时不可用" : ""]
  }
  private func respond(_ request: URLRequest) throws -> (Int, Data) {
    requests.append(request)
    let path = request.url!.path
    if path == "/api/studio/capabilities" {
      return try json(["image": false, "video": false, "voice_notes": true, "notes_ai": false,
        "draft_scope": "voice-owner"])
    }
    if path.hasSuffix("/edition") {
      editionPosts += 1
      return try json(["ok": true])
    }
    if path.hasSuffix("/analyze") {
      analysisPosts += 1
      let payload = try body(request)
      requestIDs.append(try XCTUnwrap(payload["request_id"] as? String))
      if loseAnalyzeReply { throw URLError(.networkConnectionLost) }
      return try json(job(initialStatus))
    }
    if path == "/api/studio/notes", request.httpMethod == "POST" {
      let payload = try body(request)
      remoteExists = true; remoteReady = false; remoteRevision = 1
      return try json(remote(try XCTUnwrap(payload["id"] as? String)))
    }
    if path.hasSuffix("/audio") {
      uploaded += 1; remoteReady = true; remoteRevision += 1
      return try json(remote(request.url!.deletingLastPathComponent().lastPathComponent))
    }
    if path.hasSuffix("/logical-group") {
      XCTAssertEqual(request.httpMethod, "PUT")
      XCTAssertNotNil(try body(request)["logical_note_id"] as? String)
      return try json(remote(request.url!.deletingLastPathComponent().lastPathComponent))
    }
    if path.hasPrefix("/api/studio/notes/") {
      return remoteExists ? try json(remote(request.url!.lastPathComponent))
        : try json(["detail": "笔记不存在"], status: 404)
    }
    if path == "/api/studio/jobs" { return try json(["items": missingJob ? [] : [job(polledStatus)]]) }
    if path.hasPrefix("/api/studio/jobs/") {
      return missingJob ? try json(["detail": "任务不存在"], status: 404) : try json(job(polledStatus))
    }
    throw URLError(.unsupportedURL)
  }
}

@MainActor final class VoiceGenerationServiceTests: XCTestCase {
  func testOneActionUploadsAudioGeneratesOnlyFinalNoteAndAcceptsIt() async throws {
    let fixture = try VoiceGenerationFixture()
    fixture.remoteExists = false
    fixture.initialStatus = "planning"
    let service = StudioService(api: fixture.api)
    var phases: [String] = []
    let result = try await service.generateVoice(note: fixture.note, fileURL: fixture.file,
      onProgress: { phases.append($0) })
    XCTAssertEqual(fixture.uploaded, 1)
    XCTAssertEqual(fixture.analysisPosts, 1)
    XCTAssertEqual(result.job.polished, fixture.polished)
    XCTAssertEqual(result.job.status, "succeeded")
    XCTAssertTrue(phases.contains("organizing"))
    XCTAssertTrue(phases.contains("succeeded"))
    let payload = try fixture.body(XCTUnwrap(fixture.requests.first { $0.url!.path.hasSuffix("/analyze") }))
    XCTAssertEqual(payload["action"] as? String, "voice_note")
    XCTAssertEqual(payload["transcript"] as? String, "")
    XCTAssertEqual(payload["consent"] as? Bool, true)
    try await service.accept(note: result.remoteNote, job: result.job)
    XCTAssertEqual(fixture.editionPosts, 1)
  }

  func testOpeningUnsubmittedRecordingNeverUploadsOrGenerates() async throws {
    let fixture = try VoiceGenerationFixture()
    fixture.remoteExists = false
    let result = try await StudioService(api: fixture.api).restoreVoice(note: fixture.note, fileURL: fixture.file)
    XCTAssertNil(result)
    XCTAssertTrue(fixture.requests.allSatisfy { $0.httpMethod == "GET" })
    XCTAssertEqual(fixture.analysisPosts, 0)
    XCTAssertEqual(fixture.uploaded, 0)
  }

  func testLostSubmitReplyReopensByGetAndNeverStartsAnotherPaidAttempt() async throws {
    let fixture = try VoiceGenerationFixture()
    fixture.loseAnalyzeReply = true
    let service = StudioService(api: fixture.api)
    do { _ = try await service.generateVoice(note: fixture.note, fileURL: fixture.file); XCTFail("Expected lost reply") }
    catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    let requestsBeforeRestore = fixture.requests.count
    let reopened = StudioService(api: fixture.api)
    let restored = try await reopened.restoreVoice(note: fixture.note, fileURL: fixture.file)
    XCTAssertEqual(restored?.job.polished, fixture.polished)
    XCTAssertTrue(fixture.requests.dropFirst(requestsBeforeRestore).allSatisfy { $0.httpMethod == "GET" })
    _ = try await reopened.generateVoice(note: fixture.note, fileURL: fixture.file)
    XCTAssertEqual(fixture.analysisPosts, 1)
  }

  func testMissingUncertainSubmissionNeverBlindlyRepostsEvenOnRetry() async throws {
    let fixture = try VoiceGenerationFixture()
    fixture.loseAnalyzeReply = true; fixture.missingJob = true
    let service = StudioService(api: fixture.api)
    do { _ = try await service.generateVoice(note: fixture.note, fileURL: fixture.file); XCTFail("Expected lost reply") } catch { }
    do {
      _ = try await service.generateVoice(note: fixture.note, fileURL: fixture.file, retryFailed: true)
      XCTFail("Ambiguous command must remain read-only")
    } catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    do { _ = try await service.restoreVoice(note: fixture.note, fileURL: fixture.file); XCTFail("Must preserve uncertainty") }
    catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    XCTAssertEqual(fixture.analysisPosts, 1)
  }

  func testConfirmedFailureAllowsOnlyExplicitNewAttemptWithNewRequestID() async throws {
    let fixture = try VoiceGenerationFixture()
    fixture.initialStatus = "failed"; fixture.polledStatus = "failed"
    let service = StudioService(api: fixture.api)
    do { _ = try await service.generateVoice(note: fixture.note, fileURL: fixture.file); XCTFail("Expected failure") } catch { }
    do { _ = try await service.generateVoice(note: fixture.note, fileURL: fixture.file); XCTFail("Must not retry automatically") } catch { }
    XCTAssertEqual(fixture.analysisPosts, 1)
    fixture.initialStatus = "succeeded"
    let result = try await service.generateVoice(note: fixture.note, fileURL: fixture.file, retryFailed: true)
    XCTAssertEqual(result.job.polished, fixture.polished)
    XCTAssertEqual(fixture.analysisPosts, 2)
    XCTAssertEqual(Set(fixture.requestIDs).count, 2)
  }

  func testUncertainProviderJobDoesNotPermitExplicitNewAttempt() async throws {
    let fixture = try VoiceGenerationFixture()
    fixture.initialStatus = "uncertain"; fixture.polledStatus = "uncertain"
    let service = StudioService(api: fixture.api)
    do { _ = try await service.generateVoice(note: fixture.note, fileURL: fixture.file); XCTFail("Expected uncertain job") } catch { }
    do { _ = try await service.generateVoice(note: fixture.note, fileURL: fixture.file, retryFailed: true)
      XCTFail("Uncertain job must not be resubmitted")
    } catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    XCTAssertEqual(fixture.analysisPosts, 1)
  }

  func testRawTranscriptAloneCannotBecomeGeneratedNoteOrProgress() async throws {
    let fixture = try VoiceGenerationFixture()
    fixture.transcriptOnly = true
    var phases: [String] = []
    do {
      _ = try await StudioService(api: fixture.api).generateVoice(note: fixture.note, fileURL: fixture.file,
        onProgress: { phases.append($0) })
      XCTFail("A raw transcript must never appear as the final note")
    } catch let error as CloudError {
      XCTAssertEqual(error.status, 422)
      XCTAssertFalse(error.message.contains("原始转写秘密"))
    }
    XCTAssertFalse(phases.contains { $0.contains("原始转写秘密") })
  }

  func testReopeningDifferentAudioNeverRestoresPreviousAudioResult() async throws {
    let fixture = try VoiceGenerationFixture(), service = StudioService(api: fixture.api)
    _ = try await service.generateVoice(note: fixture.note, fileURL: fixture.file)
    try Data(repeating: 8, count: 64).write(to: fixture.file)
    let readsBeforeRestore = fixture.requests.count
    let result = try await service.restoreVoice(note: fixture.note, fileURL: fixture.file)
    XCTAssertNil(result)
    XCTAssertTrue(fixture.requests.dropFirst(readsBeforeRestore).allSatisfy { $0.httpMethod == "GET" })
    XCTAssertEqual(fixture.analysisPosts, 1)
  }
}
