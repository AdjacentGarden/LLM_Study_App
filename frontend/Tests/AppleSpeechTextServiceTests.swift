import CryptoKit
import Foundation
import XCTest

@testable import BookCourse

private final class SpeechTextProtocol: URLProtocol, @unchecked Sendable {
  nonisolated(unsafe) static var handler: ((URLRequest) throws -> (Int, Data))?
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      let (status, data) = try Self.handler!(request)
      client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status,
        httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: data)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

private final class SpeechTextFixture: @unchecked Sendable {
  let api: CloudAPI
  let note = StudyNote(kind: .voice, title: "本机语音", anchor: NoteAnchor(bookID: "chemistry", page: 8))
  let text = "化学反应前后总能量保持守恒。"
  var requests: [URLRequest] = []
  var requestIDs: [String] = []
  var supportsText = true
  var exists = false
  var ready = false
  var hash = ""
  var revision = 1
  var analysisPosts = 0
  var textPuts = 0
  var loseAnalysisReply = false
  var jobMissing = false
  var status = "succeeded"
  var postStatus = "succeeded"
  private let session: URLSession
  init() {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [SpeechTextProtocol.self]
    session = URLSession(configuration: config)
    api = CloudAPI(baseURL: URL(string: "https://speech-text-\(UUID()).ts.net")!, session: session)
    SpeechTextProtocol.handler = { [weak self] request in
      guard let self else { throw URLError(.cancelled) }
      return try self.respond(request)
    }
  }
  deinit { session.invalidateAndCancel() }
  func body(_ request: URLRequest) throws -> [String: Any] {
    var data = request.httpBody ?? Data()
    if let stream = request.httpBodyStream {
      stream.open(); defer { stream.close() }
      var bytes = [UInt8](repeating: 0, count: 4096)
      while stream.hasBytesAvailable {
        let count = stream.read(&bytes, maxLength: bytes.count)
        if count <= 0 { break }
        data.append(contentsOf: bytes.prefix(count))
      }
    }
    return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
  }
  private func json(_ value: Any, status: Int = 200) throws -> (Int, Data) {
    (status, try JSONSerialization.data(withJSONObject: value))
  }
  private func remote(_ id: String) -> [String: Any] {
    ["id": id, "book_id": "chemistry", "revision": revision, "audio_ready": false,
      "audio_sha256": "", "speech_ready": ready, "speech_sha256": hash]
  }
  private func job(_ status: String) -> [String: Any] {
    ["id": "text-job-\(analysisPosts)", "book_id": "chemistry", "kind": "voice_note", "status": status,
      "request_id": requestIDs.last ?? "", "result": ["polished": "整理后的能量守恒笔记。", "summary": "能量守恒"],
      "error": status == "failed" ? "整理失败" : ""]
  }
  private func respond(_ request: URLRequest) throws -> (Int, Data) {
    requests.append(request)
    let path = request.url!.path
    if path == "/api/studio/capabilities" {
      return try json(["image": false, "video": false, "voice_notes": false, "notes_ai": false,
        "draft_scope": "local-owner", "voice_text_notes": supportsText])
    }
    if path.hasSuffix("/speech-text") {
      XCTAssertEqual(request.httpMethod, "PUT")
      let payload = try body(request)
      let value = try XCTUnwrap(payload["text"] as? String)
      hash = SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
      ready = true; revision += 1; textPuts += 1
      return try json(remote(request.url!.deletingLastPathComponent().lastPathComponent))
    }
    if path.hasSuffix("/analyze") {
      analysisPosts += 1
      let payload = try body(request)
      requestIDs.append(try XCTUnwrap(payload["request_id"] as? String))
      if loseAnalysisReply { throw URLError(.networkConnectionLost) }
      return try json(job(postStatus))
    }
    if path == "/api/studio/notes", request.httpMethod == "POST" {
      exists = true
      return try json(remote(try XCTUnwrap(body(request)["id"] as? String)))
    }
    if path.hasPrefix("/api/studio/notes/") {
      return exists ? try json(remote(request.url!.lastPathComponent))
        : try json(["detail": "不存在"], status: 404)
    }
    if path == "/api/studio/jobs" { return try json(["items": jobMissing ? [] : [job(status)]]) }
    if path.hasPrefix("/api/studio/jobs/") {
      return jobMissing ? try json(["detail": "不存在"], status: 404) : try json(job(status))
    }
    throw URLError(.unsupportedURL)
  }
}

@MainActor final class AppleSpeechTextServiceTests: XCTestCase {
  func testOnDeviceTextPathSendsOnlyTextAndFinalVoiceAction() async throws {
    let fixture = SpeechTextFixture()
    let result = try await StudioService(api: fixture.api).generateVoiceFromTranscript(
      note: fixture.note, transcript: "  \n" + fixture.text + "\n")
    XCTAssertEqual(result.job.polished, "整理后的能量守恒笔记。")
    XCTAssertEqual(fixture.textPuts, 1)
    XCTAssertEqual(fixture.analysisPosts, 1)
    let request = try XCTUnwrap(fixture.requests.first { $0.url!.path.hasSuffix("/speech-text") })
    let payload = try fixture.body(request)
    XCTAssertEqual(payload["text"] as? String, fixture.text)
    XCTAssertEqual(payload["revision"] as? Int, 1)
    XCTAssertEqual(payload["source"] as? String, "apple-speech-on-device")
    XCTAssertEqual(payload["language"] as? String, "zh-CN")
    let analyze = try fixture.body(XCTUnwrap(fixture.requests.first { $0.url!.path.hasSuffix("/analyze") }))
    XCTAssertEqual(analyze["action"] as? String, "voice_note")
    XCTAssertEqual(analyze["transcript"] as? String, "")
    XCTAssertFalse(fixture.requests.contains { $0.url!.path.hasSuffix("/audio")
      || ($0.value(forHTTPHeaderField: "Content-Type") ?? "").contains("audio") })
  }
  func testRepeatExplicitGenerateReadsExistingResultWithoutNewTextOrModelPost() async throws {
    let fixture = SpeechTextFixture(), service = StudioService(api: fixture.api)
    _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text)
    let before = fixture.requests.count
    _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text)
    XCTAssertTrue(fixture.requests.dropFirst(before).allSatisfy { $0.httpMethod == "GET" })
    XCTAssertEqual(fixture.textPuts, 1)
    XCTAssertEqual(fixture.analysisPosts, 1)
  }
  func testReopeningNeverUploadsTextOrAudioOrSubmitsAnalysis() async throws {
    let fixture = SpeechTextFixture()
    let result = try await StudioService(api: fixture.api).restoreVoiceFromTranscript(
      note: fixture.note, transcript: fixture.text)
    XCTAssertNil(result)
    XCTAssertTrue(fixture.requests.allSatisfy { $0.httpMethod == "GET" })
  }
  func testLostAnalysisReplyRestoresByGetWithoutNewPaidAttempt() async throws {
    let fixture = SpeechTextFixture()
    fixture.loseAnalysisReply = true
    do {
      _ = try await StudioService(api: fixture.api).generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text)
      XCTFail("Lost response must remain visible")
    } catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    let before = fixture.requests.count
    let restored = try await StudioService(api: fixture.api).restoreVoiceFromTranscript(
      note: fixture.note, transcript: fixture.text)
    XCTAssertEqual(restored?.job.status, "succeeded")
    XCTAssertEqual(fixture.analysisPosts, 1)
    XCTAssertTrue(fixture.requests.dropFirst(before).allSatisfy { $0.httpMethod == "GET" })
  }
  func testUnavailableTextServiceCannotFallBackToAudio() async throws {
    let fixture = SpeechTextFixture()
    fixture.supportsText = false
    do {
      _ = try await StudioService(api: fixture.api).generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text)
      XCTFail("Text-only path cannot silently upload audio")
    } catch let error as CloudError { XCTAssertEqual(error.status, 503) }
    XCTAssertTrue(fixture.requests.allSatisfy { $0.httpMethod == "GET" })
    XCTAssertEqual(fixture.textPuts, 0)
    XCTAssertEqual(fixture.analysisPosts, 0)
  }
  func testChangedTranscriptCannotRestorePreviousTextResult() async throws {
    let fixture = SpeechTextFixture(), service = StudioService(api: fixture.api)
    _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text)
    let before = fixture.requests.count
    let result = try await service.restoreVoiceFromTranscript(note: fixture.note, transcript: "更换后的本机识别结果")
    XCTAssertNil(result)
    XCTAssertTrue(fixture.requests.dropFirst(before).allSatisfy { $0.httpMethod == "GET" })
    XCTAssertEqual(fixture.analysisPosts, 1)
  }
  func testFailureNeedsExplicitRetryWithDifferentRequestID() async throws {
    let fixture = SpeechTextFixture(), service = StudioService(api: fixture.api)
    fixture.postStatus = "failed"; fixture.status = "failed"
    do { _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text) } catch { }
    do { _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text) } catch { }
    XCTAssertEqual(fixture.analysisPosts, 1)
    fixture.postStatus = "succeeded"
    _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text, retryFailed: true)
    XCTAssertEqual(fixture.analysisPosts, 2)
    XCTAssertEqual(Set(fixture.requestIDs).count, 2)
    XCTAssertEqual(fixture.textPuts, 1)
  }
  func testLegacyRemoteNoteDecodesWithoutSpeechFields() throws {
    let data = Data(#"{"id":"legacy","book_id":"book","revision":1,"audio_ready":true,"audio_sha256":"abc"}"#.utf8)
    let note = try JSONDecoder().decode(StudioNote.self, from: data)
    XCTAssertNil(note.speech_ready)
    XCTAssertNil(note.speech_sha256)
  }
  func testAudioReceiptUsesDistinctIdentityFromLocalSpeechReceipt() async throws {
    let fixture = SpeechTextFixture(), service = StudioService(api: fixture.api)
    _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text)
    let textNoteURL = try XCTUnwrap(fixture.requests.first {
      $0.httpMethod == "GET" && $0.url!.path.hasPrefix("/api/studio/notes/")
    }?.url)
    let before = fixture.requests.count
    let restored = try await service.restoreVoice(note: fixture.note,
      fileURL: FileManager.default.temporaryDirectory.appendingPathComponent("never-uploaded.m4a"))
    XCTAssertNil(restored)
    let audioNoteURL = try XCTUnwrap(fixture.requests.dropFirst(before).first {
      $0.url!.path.hasPrefix("/api/studio/notes/")
    }?.url)
    XCTAssertNotEqual(textNoteURL, audioNoteURL)
    XCTAssertTrue(fixture.requests.dropFirst(before).allSatisfy { $0.httpMethod == "GET" })
  }
  func testUnconfirmedTextSubmissionCannotBeRetriedAsNewAttempt() async throws {
    let fixture = SpeechTextFixture(), service = StudioService(api: fixture.api)
    fixture.loseAnalysisReply = true; fixture.jobMissing = true
    do { _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text) } catch { }
    do {
      _ = try await service.generateVoiceFromTranscript(note: fixture.note, transcript: fixture.text, retryFailed: true)
      XCTFail("An unknown receipt must be checked, never blindly resubmitted")
    } catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    XCTAssertEqual(fixture.analysisPosts, 1)
  }
  func testLocalRecognitionProvenanceAndExactAudioDigestSurviveNoteReopen() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent("local-speech-note-\(UUID())")
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(directory: folder)
    let note = StudyNote(kind: .voice, title: "原始录音", transcript: "  本机识别的学习想法。  ",
      voiceRecognitionSource: "apple-speech-on-device", voiceRecognitionAudioSHA256: "exact-file-hash")
    try await repository.saveNote(note)
    let reopened = try LocalBookCourseRepository(directory: folder)
    let notes = try await reopened.loadNotes()
    let restored = try XCTUnwrap(notes.first)
    XCTAssertEqual(restored.localSpeechTranscript(audioSHA256: "exact-file-hash"), "本机识别的学习想法。")
    XCTAssertNil(restored.localSpeechTranscript(audioSHA256: "replacement-file-hash"))
    XCTAssertNil(restored.localSpeechTranscript(audioSHA256: ""))
  }
  func testLegacyTranscriptNeverImpersonatesOnDeviceRecognition() throws {
    let old = StudyNote(kind: .voice, title: "旧录音", transcript: "旧服务器逐字稿")
    var payload = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(old)) as? [String: Any])
    payload.removeValue(forKey: "voiceRecognitionSource")
    payload.removeValue(forKey: "voiceRecognitionAudioSHA256")
    let decoded = try JSONDecoder().decode(StudyNote.self,
      from: JSONSerialization.data(withJSONObject: payload))
    XCTAssertEqual(decoded.transcript, "旧服务器逐字稿")
    XCTAssertNil(decoded.voiceRecognitionSource)
    XCTAssertNil(decoded.localSpeechTranscript(audioSHA256: "some-audio"))
  }
}
