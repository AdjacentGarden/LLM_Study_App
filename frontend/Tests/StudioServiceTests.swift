import CryptoKit
import Foundation
import PencilKit
import XCTest

@testable import BookCourse

private final class StudioContractProtocol: URLProtocol, @unchecked Sendable {
  typealias Handler = (URLRequest) throws -> (Int, String, Data)
  static let lock = NSLock()
  nonisolated(unsafe) static var handlers: [String: Handler] = [:]
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  static func install(host: String, handler: Handler?) {
    lock.withLock { handlers[host] = handler }
  }
  override func startLoading() {
    do {
      let handler = Self.lock.withLock { Self.handlers[request.url?.host ?? ""] }
      guard let handler else { throw URLError(.unsupportedURL) }
      let (status, mime, bytes) = try handler(request)
      let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
        headerFields: ["Content-Type": mime])!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: bytes)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

private final class StudioRequests: @unchecked Sendable {
  let lock = NSLock()
  private var values: [URLRequest] = []
  func append(_ request: URLRequest) { lock.withLock { values.append(request) } }
  var all: [URLRequest] { lock.withLock { values } }
}

final class StudioServiceTests: XCTestCase {
  private func client(accountID: String? = nil,
    _ handler: @escaping StudioContractProtocol.Handler) -> (CloudAPI, URLSession, String) {
    let host = "studio-\(UUID().uuidString.lowercased()).example"
    let url = URL(string: "https://\(host)")!
    if let accountID {
      CloudAPI.seedLegacyIdentityForTesting(url: url, account: account(accountID))
    }
    StudioContractProtocol.install(host: host, handler: handler)
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [StudioContractProtocol.self]
    let session = URLSession(configuration: config)
    return (CloudAPI(baseURL: url, session: session), session, host)
  }
  private func account(_ id: String) -> CloudAccountState {
    CloudAccountState(account: .init(email: "studio@example.com", stage: "university", interests: [],
      goal: "interest", is_demo: false), user_id: id, email_available: true, demo_available: false)
  }
  private func json(_ object: [String: Any], status: Int = 200) throws -> (Int, String, Data) {
    (status, "application/json", try JSONSerialization.data(withJSONObject: object))
  }
  private func body(_ request: URLRequest) throws -> [String: Any] {
    let bytes: Data
    if let value = request.httpBody { bytes = value }
    else if let stream = request.httpBodyStream {
      stream.open(); defer { stream.close() }
      var result = Data()
      var buffer = [UInt8](repeating: 0, count: 4096)
      while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count <= 0 { break }
        result.append(contentsOf: buffer.prefix(count))
      }
      bytes = result
    } else { throw URLError(.cannotDecodeRawData) }
    return try XCTUnwrap(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
  }

  private func analysisJob(_ note: StudioNote, id: String, status: String, requestID: String) -> [String: Any] {
    ["id": id, "book_id": note.book_id, "kind": "improve", "status": status,
      "result": [:], "error": "", "request_id": requestID]
  }

  private func clearAnalysisReceipts(api: CloudAPI, note: StudioNote, transcript: String, requestIDs: [String]) {
    func digest(_ value: String) -> String {
      SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    let original = "native_" + digest(api.baseURL.absoluteString + "|" + note.id + "|\(note.revision)|improve|" + transcript)
    UserDefaults.standard.removeObject(forKey: "native.studio.note.latest." + digest(original))
    for requestID in Set(requestIDs + [original]) {
      let key = "native.studio.note." + digest(requestID)
      UserDefaults.standard.removeObject(forKey: key + ".job")
      UserDefaults.standard.removeObject(forKey: key + ".submitted")
    }
  }

  func testUnavailableVoiceServicePreservesOriginalAndDoesNotSubmit() async throws {
    let requests = StudioRequests()
    let (api, session, host) = client { request in
      requests.append(request)
      return try self.json(["image": false, "video": false, "voice_notes": false,
        "notes_ai": false, "draft_scope": "test-owner"])
    }
    defer { StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel() }
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".m4a")
    let original = Data(repeating: 7, count: 64)
    try original.write(to: url)
    defer { try? FileManager.default.removeItem(at: url) }
    let note = StudyNote(kind: .voice, title: "自己的录音", anchor: NoteAnchor(bookID: "own-book", page: 3), duration: 4)
    do {
      _ = try await StudioService(api: api).prepareVoice(note: note, fileURL: url)
      XCTFail("Unavailable service must not fabricate a transcript")
    } catch let error as CloudError { XCTAssertEqual(error.status, 503) }
    XCTAssertEqual(try Data(contentsOf: url), original)
    XCTAssertNil(note.transcript)
    XCTAssertEqual(requests.all.map { $0.url!.path }, ["/api/studio/capabilities"])
    await api.clearCredentials()
  }

  func testVoiceUsesRealNoteRevisionAndFileUploadBeforeAnalyze() async throws {
    let requests = StudioRequests()
    let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".m4a")
    var original = Data(repeating: 0, count: 64)
    original.replaceSubrange(4..<12, with: Data("ftypM4A ".utf8))
    try original.write(to: file)
    defer { try? FileManager.default.removeItem(at: file) }
    let hash = SHA256.hash(data: original).map { String(format: "%02x", $0) }.joined()
    let (api, session, host) = client { request in
      requests.append(request)
      if request.url!.path == "/api/studio/capabilities" {
        return try self.json(["image": false, "video": false, "voice_notes": true,
          "notes_ai": false, "draft_scope": "test-owner"])
      }
      if request.httpMethod == "GET" { return try self.json(["detail": "笔记不存在"], status: 404) }
      if request.httpMethod == "POST" {
        let body = try self.body(request)
        XCTAssertEqual(body["book_id"] as? String, "own-book")
        XCTAssertEqual(body["input_mode"] as? String, "voice")
        XCTAssertEqual(body["revision"] as? Int, 0)
        XCTAssertEqual(body["pages"] as? [Int], [3])
        XCTAssertTrue((body["logical_note_id"] as? String)?.hasPrefix("native_") == true)
        return try self.json(["id": body["id"]!, "book_id": "own-book", "revision": 1,
          "audio_ready": false, "audio_sha256": ""])
      }
      XCTAssertEqual(request.httpMethod, "PUT")
      XCTAssertEqual(request.value(forHTTPHeaderField: "X-Note-Revision"), "1")
      XCTAssertEqual(request.value(forHTTPHeaderField: "X-Audio-Duration"), "4.0")
      XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "audio/mp4")
      let id = request.url!.deletingLastPathComponent().lastPathComponent
      return try self.json(["id": id, "book_id": "own-book", "revision": 2,
        "audio_ready": true, "audio_sha256": hash])
    }
    defer { StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel() }
    let note = StudyNote(kind: .voice, title: "自己的录音", anchor: NoteAnchor(bookID: "own-book", page: 3), duration: 4)
    let remote = try await StudioService(api: api).prepareVoice(note: note, fileURL: file)
    XCTAssertEqual(remote.revision, 2)
    XCTAssertTrue(remote.audio_ready)
    XCTAssertEqual(remote.audio_sha256, hash)
    XCTAssertEqual(try Data(contentsOf: file), original)
    XCTAssertFalse(requests.all.contains { $0.url!.path.hasSuffix("/analyze") })
    await api.clearCredentials()
  }

  func testLostAnalyzeResponseOnlyQueriesOriginalRequestAndNeverReposts() async throws {
    let requests = StudioRequests()
    let (api, session, host) = client { request in
      requests.append(request)
      if request.httpMethod == "POST" { throw URLError(.timedOut) }
      let parts = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
      let requestID = parts.queryItems?.first { $0.name == "request_id" }?.value
      let value: [String: Any] = ["id": "existing-job", "book_id": "own-book", "kind": "recognize",
        "status": "succeeded", "result": ["transcript": "真实录音返回内容"], "error": NSNull(),
        "request_id": requestID ?? "", "asset_url": NSNull()]
      return try self.json(request.url!.path == "/api/studio/jobs" ? ["items": [value]] : value)
    }
    defer { StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel() }
    let service = StudioService(api: api)
    let note = StudioNote(id: "remote-\(UUID().uuidString)", book_id: "own-book", revision: 2,
      audio_ready: true, audio_sha256: "hash")
    do { _ = try await service.analyze(note: note, action: "recognize"); XCTFail("Expected lost response") }
    catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    let restored = try await service.analyze(note: note, action: "recognize")
    let again = try await service.analyze(note: note, action: "recognize")
    XCTAssertEqual(restored.id, "existing-job")
    XCTAssertEqual(restored.transcript, "真实录音返回内容")
    XCTAssertEqual(again.id, restored.id)
    XCTAssertEqual(requests.all.filter { $0.httpMethod == "POST" }.count, 1)
    let submitted = try body(XCTUnwrap(requests.all.first))
    XCTAssertEqual(submitted["consent"] as? Bool, true)
    XCTAssertEqual(submitted["request_id"] as? String, restored.request_id)
    await api.clearCredentials()
  }

  func testExistingVoiceInputLinksStableLocalIdentityAndOnlyKnownInputsFromTheSameBook() async throws {
    let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".m4a")
    let bytes = Data(repeating: 3, count: 64)
    try bytes.write(to: file)
    defer { try? FileManager.default.removeItem(at: file) }
    let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    var note = StudyNote(kind: .voice, title: "原题目", anchor: NoteAnchor(bookID: "book-1", page: 3), duration: 4)
    var bookID = "book-1"
    var scopes = "owner-1"
    var logicalIDs: [String] = []
    var legacyIDs: [[String]] = []
    var inputIDs: [String] = []
    let (api, session, host) = client { request in
      if request.url!.path.hasSuffix("capabilities") {
        return try self.json(["image": false, "video": false, "voice_notes": true,
          "notes_ai": false, "draft_scope": scopes])
      }
      let binding = request.url!.path.hasSuffix("/logical-group")
      let id = binding ? request.url!.deletingLastPathComponent().lastPathComponent : request.url!.lastPathComponent
      if binding {
        XCTAssertEqual(request.httpMethod, "PUT")
        let payload = try self.body(request)
        logicalIDs.append(try XCTUnwrap(payload["logical_note_id"] as? String))
        legacyIDs.append(try XCTUnwrap(payload["legacy_note_ids"] as? [String]))
        inputIDs.append(id)
      } else { XCTAssertEqual(request.httpMethod, "GET") }
      return try self.json(["id": id, "book_id": bookID, "revision": 2,
        "audio_ready": true, "audio_sha256": hash])
    }
    defer { StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel() }
    let service = StudioService(api: api)
    let original = try await service.prepareVoice(note: note, fileURL: file)
    note.title = "改过的题目"; note.anchor?.page = 4; note.anchor?.quote = "新的原文"
    let edited = try await service.prepareVoice(note: note, fileURL: file)
    XCTAssertNotEqual(original.id, edited.id)
    XCTAssertEqual(logicalIDs[0], logicalIDs[1], "Title and anchor edits retain the same logical note")
    XCTAssertEqual(legacyIDs[0], [])
    XCTAssertEqual(legacyIDs[1], [original.id])
    XCTAssertEqual(original.revision, 2); XCTAssertEqual(edited.revision, 2)
    bookID = "book-2"; note.anchor?.bookID = bookID
    _ = try await service.prepareVoice(note: note, fileURL: file)
    XCTAssertEqual(logicalIDs[2], logicalIDs[0])
    XCTAssertEqual(legacyIDs[2], [], "Inputs from a different book must never be proposed for merging")
    scopes = "owner-2"
    _ = try await service.prepareVoice(note: note, fileURL: file)
    XCTAssertNotEqual(logicalIDs[3], logicalIDs[0], "Account scopes remain independent")
    XCTAssertEqual(legacyIDs[3], [])
    let keys = [
      logicalIDs[0] + "|book-1", logicalIDs[2] + "|book-2", logicalIDs[3] + "|book-2"
    ]
    for value in keys {
      let digest = SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
      UserDefaults.standard.removeObject(forKey: "native.studio.note.inputs." + digest)
    }
    XCTAssertEqual(inputIDs.count, 4)
    await api.clearCredentials()
  }

  func testDeletedLogicalNoteArchiveOutboxSurvivesOfflineAndOnlyFlushesForItsAccountScope() async throws {
    let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".m4a")
    let bytes = Data(repeating: 3, count: 64)
    try bytes.write(to: file)
    defer { try? FileManager.default.removeItem(at: file) }
    let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    let deleted = StudyNote(kind: .voice, title: "待删除", anchor: NoteAnchor(bookID: "book-1", page: 3), duration: 4)
    let remaining = StudyNote(kind: .voice, title: "仍保留", anchor: NoteAnchor(bookID: "book-1", page: 4), duration: 4)
    var scope = "scope-A", offline = false, missing = false
    var archivePaths: [String] = [], paths: [String] = []
    let (api, session, host) = client(accountID: "account-A") { request in
      let path = request.url!.path
      paths.append(path)
      if path.hasSuffix("capabilities") {
        return try self.json(["image": false, "video": false, "voice_notes": true,
          "notes_ai": false, "draft_scope": scope])
      }
      if path.hasSuffix("/archive") {
        XCTAssertEqual(request.httpMethod, "PUT")
        XCTAssertEqual(try self.body(request)["book_id"] as? String, "book-1")
        archivePaths.append(path)
        if offline { throw URLError(.notConnectedToInternet) }
        if missing { return try self.json(["detail": "不存在"], status: 404) }
        return try self.json(["logical_note_id": request.url!.deletingLastPathComponent().lastPathComponent,
          "book_id": "book-1", "archived": true])
      }
      let binding = path.hasSuffix("/logical-group")
      let id = binding ? request.url!.deletingLastPathComponent().lastPathComponent : request.url!.lastPathComponent
      return try self.json(["id": id, "book_id": "book-1", "revision": 2,
        "audio_ready": true, "audio_sha256": hash])
    }
    defer { StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel() }
    _ = try await StudioService(api: api).prepareVoice(note: deleted, fileURL: file)
    offline = true
    await StudioService(api: api).deleteLogicalNote(note: deleted)
    XCTAssertEqual(archivePaths.count, 1)
    await api.rememberAccount(account("account-B")); scope = "scope-B"; offline = false
    _ = try await StudioService(api: api).prepareVoice(note: remaining, fileURL: file)
    XCTAssertEqual(archivePaths.count, 1, "A different account must not flush account A's outbox")
    await api.rememberAccount(account("account-A")); scope = "scope-A"
    let before = paths.count
    _ = try await StudioService(api: api).prepareVoice(note: remaining, fileURL: file)
    XCTAssertEqual(archivePaths.count, 2)
    XCTAssertEqual(archivePaths[0], archivePaths[1], "Reopening uses the original queued logical identity")
    let retried = Array(paths.dropFirst(before))
    XCTAssertTrue(try XCTUnwrap(retried.firstIndex { $0.hasSuffix("/archive") })
      < XCTUnwrap(retried.firstIndex { $0.hasPrefix("/api/studio/notes/") }), "Cleanup precedes preparation")
    _ = try await StudioService(api: api).prepareVoice(note: remaining, fileURL: file)
    XCTAssertEqual(archivePaths.count, 2, "A successful archive is removed from the durable outbox")
    missing = true
    await StudioService(api: api).deleteLogicalNote(note: deleted)
    XCTAssertEqual(archivePaths.count, 3)
    _ = try await StudioService(api: api).prepareVoice(note: remaining, fileURL: file)
    XCTAssertEqual(archivePaths.count, 3, "An already absent group does not stay in the outbox")
    func digest(_ value: String) -> String {
      SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    for note in [deleted, remaining] {
      UserDefaults.standard.removeObject(forKey: "native.studio.note.references."
        + digest(api.baseURL.absoluteString + "|" + note.id + "|book-1"))
      for scope in ["scope-A", "scope-B"] {
        let logicalID = "native_" + digest(api.baseURL.absoluteString + "|" + scope + "|" + note.id)
        UserDefaults.standard.removeObject(forKey: "native.studio.note.inputs." + digest(logicalID + "|book-1"))
        UserDefaults.standard.removeObject(forKey: "native.studio.note.archives." + digest(api.baseURL.absoluteString + "|" + scope))
      }
    }
    await api.clearCredentials()
  }

  func testUnresolvedReceiptCannotCauseAnotherAnalyzeSubmission() async throws {
    let requests = StudioRequests()
    let (api, session, host) = client { request in
      requests.append(request)
      if request.httpMethod == "POST" { throw URLError(.networkConnectionLost) }
      return try self.json(["items": []])
    }
    defer { StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel() }
    let service = StudioService(api: api)
    let note = StudioNote(id: "remote-\(UUID().uuidString)", book_id: "own-book", revision: 1,
      audio_ready: true, audio_sha256: "hash")
    do { _ = try await service.analyze(note: note, action: "recognize"); XCTFail("Expected network loss") } catch {}
    do { _ = try await service.analyze(note: note, action: "recognize"); XCTFail("Must remain unresolved") }
    catch let error as CloudError { XCTAssertEqual(error.status, 409) }
    XCTAssertEqual(requests.all.filter { $0.httpMethod == "POST" }.count, 1)
    await api.clearCredentials()
  }

  func testExplicitInkRetryCreatesNewAttemptOnlyAfterConfirmedTerminalFailureAndRestoresIt() async throws {
    for status in ["failed", "cancelled", "rejected"] {
      let note = StudioNote(id: "ink_\(UUID().uuidString)", book_id: "own-book", revision: 1,
        audio_ready: false, audio_sha256: "")
      let transcript = "已确认的同一份文字"
      var requestIDs: [String] = []
      let (api, session, host) = client { request in
        if request.httpMethod == "POST" {
          let requestID = try XCTUnwrap(self.body(request)["request_id"] as? String)
          requestIDs.append(requestID)
          return try self.json(self.analysisJob(note, id: requestIDs.count == 1 ? "initial-job" : "retry-job",
            status: requestIDs.count == 1 ? status : "queued", requestID: requestID))
        }
        let retry = request.url!.lastPathComponent == "retry-job"
        return try self.json(self.analysisJob(note, id: retry ? "retry-job" : "initial-job",
          status: retry ? "queued" : status, requestID: retry ? requestIDs[1] : requestIDs[0]))
      }
      defer {
        clearAnalysisReceipts(api: api, note: note, transcript: transcript, requestIDs: requestIDs)
        StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel()
      }
      let service = StudioService(api: api)
      let original = try await service.analyze(note: note, action: "improve", transcript: transcript)
      XCTAssertEqual(original.status, status)
      let queried = try await service.analyze(note: note, action: "improve", transcript: transcript)
      XCTAssertEqual(queried.id, original.id)
      XCTAssertEqual(requestIDs.count, 1, "A normal query does not start another generation")
      let retried = try await service.analyze(note: note, action: "improve", transcript: transcript, retryFailed: true)
      XCTAssertEqual(retried.id, "retry-job")
      XCTAssertEqual(requestIDs.count, 2)
      XCTAssertNotEqual(requestIDs[0], requestIDs[1])
      let reopened = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript,
        retryFailed: true)
      XCTAssertEqual(reopened.id, retried.id)
      XCTAssertEqual(requestIDs.count, 2, "A queued retry remains the durable latest attempt")
      await api.clearCredentials()
    }
  }

  func testExplicitInkRetryKeepsRunningUncertainAndSuccessfulJobsOnOriginalRequest() async throws {
    for status in ["queued", "planning", "submitting", "polling", "reviewing", "uncertain", "succeeded", "needs_confirmation"] {
      let note = StudioNote(id: "ink_\(UUID().uuidString)", book_id: "own-book", revision: 1,
        audio_ready: false, audio_sha256: "")
      let transcript = "相同内容"
      var requestIDs: [String] = []
      let (api, session, host) = client { request in
        if request.httpMethod == "POST" {
          requestIDs.append(try XCTUnwrap(self.body(request)["request_id"] as? String))
        }
        return try self.json(self.analysisJob(note, id: "original-job", status: status,
          requestID: requestIDs[0]))
      }
      defer {
        clearAnalysisReceipts(api: api, note: note, transcript: transcript, requestIDs: requestIDs)
        StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel()
      }
      let initial = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript)
      let result = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript,
        retryFailed: true)
      XCTAssertEqual(result.id, initial.id, status)
      XCTAssertEqual(requestIDs.count, 1, status)
      await api.clearCredentials()
    }
  }

  func testLostInkRetryResponseRecoversLatestAttemptAfterReopenWithoutThirdPost() async throws {
    let note = StudioNote(id: "ink_\(UUID().uuidString)", book_id: "own-book", revision: 1,
      audio_ready: false, audio_sha256: "")
    let transcript = "同一确认文字"
    var requestIDs: [String] = []
    let (api, session, host) = client { request in
      if request.httpMethod == "POST" {
        let requestID = try XCTUnwrap(self.body(request)["request_id"] as? String)
        requestIDs.append(requestID)
        if requestIDs.count == 2 { throw URLError(.networkConnectionLost) }
        return try self.json(self.analysisJob(note, id: "initial-job", status: "failed", requestID: requestID))
      }
      if request.url!.path == "/api/studio/jobs" {
        let requestID = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?
          .first { $0.name == "request_id" }?.value
        XCTAssertEqual(requestID, requestIDs[1])
        return try self.json(["items": [self.analysisJob(note, id: "retry-job", status: "queued", requestID: requestIDs[1])]])
      }
      let retry = request.url!.lastPathComponent == "retry-job"
      return try self.json(self.analysisJob(note, id: retry ? "retry-job" : "initial-job",
        status: retry ? "queued" : "failed", requestID: retry ? requestIDs[1] : requestIDs[0]))
    }
    defer {
      clearAnalysisReceipts(api: api, note: note, transcript: transcript, requestIDs: requestIDs)
      StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel()
    }
    _ = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript)
    do {
      _ = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript, retryFailed: true)
      XCTFail("Expected a lost retry response")
    } catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    let reopened = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript,
      retryFailed: true)
    XCTAssertEqual(reopened.id, "retry-job")
    let again = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript,
      retryFailed: true)
    XCTAssertEqual(again.id, reopened.id)
    XCTAssertEqual(requestIDs.count, 2)
    await api.clearCredentials()
  }

  func testInkRetryDoesNotSubmitWhenFailedJobCannotBeReconfirmed() async throws {
    let note = StudioNote(id: "ink_\(UUID().uuidString)", book_id: "own-book", revision: 1,
      audio_ready: false, audio_sha256: "")
    let transcript = "同一确认文字"
    var requestIDs: [String] = []
    let (api, session, host) = client { request in
      guard request.httpMethod == "POST" else { throw URLError(.notConnectedToInternet) }
      requestIDs.append(try XCTUnwrap(self.body(request)["request_id"] as? String))
      return try self.json(self.analysisJob(note, id: "initial-job", status: "failed", requestID: requestIDs[0]))
    }
    defer {
      clearAnalysisReceipts(api: api, note: note, transcript: transcript, requestIDs: requestIDs)
      StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel()
    }
    _ = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript)
    do {
      _ = try await StudioService(api: api).analyze(note: note, action: "improve", transcript: transcript, retryFailed: true)
      XCTFail("An unavailable original receipt must stop a retry")
    } catch let error as CloudError { XCTAssertTrue(error.transportFailure) }
    XCTAssertEqual(requestIDs.count, 1)
    await api.clearCredentials()
  }

  func testUnconfirmedAndFailedJobsNeverBecomePolishedNotes() async throws {
    let (api, session, host) = client { _ in throw URLError(.unsupportedURL) }
    defer { StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel() }
    let service = StudioService(api: api)
    let pending = StudioJob(id: "pending", book_id: "own-book", kind: "recognize", status: "needs_confirmation",
      result: ["transcript": .string("[待确认]原始内容")])
    let result = try await service.waitForJob(pending)
    XCTAssertEqual(result.status, "needs_confirmation")
    XCTAssertEqual(result.transcript, "[待确认]原始内容")
    XCTAssertTrue(result.polished.isEmpty)
    let failure = StudioJob(id: "failed", book_id: "own-book", kind: "improve", status: "failed",
      result: [:], error: "没有足够教材依据")
    do { _ = try await service.waitForJob(failure); XCTFail("Must preserve failure") }
    catch let error as CloudError { XCTAssertEqual(error.message, "没有足够教材依据") }
    await api.clearCredentials()
  }

  func testAssetDownloadRejectsHTMLAndUnsafeJobPath() async throws {
    let requests = StudioRequests()
    let (api, session, host) = client { request in
      requests.append(request)
      return (200, "text/html", Data("<html>not a generated video</html>".utf8))
    }
    defer { StudioContractProtocol.install(host: host, handler: nil); session.invalidateAndCancel() }
    do { _ = try await api.downloadStudioAsset(jobID: "../other"); XCTFail("Unsafe ID") }
    catch let error as CloudError { XCTAssertEqual(error.status, 422) }
    XCTAssertTrue(requests.all.isEmpty)
    do { _ = try await api.downloadStudioAsset(jobID: "real-job"); XCTFail("HTML is not media") }
    catch let error as CloudError { XCTAssertEqual(error.status, 422) }
    XCTAssertEqual(requests.all.count, 1)
    await api.clearCredentials()
  }

  func testInkContractContainsActualTransformedPointsWithoutChangingOriginal() throws {
    let points = [
      PKStrokePoint(location: CGPoint(x: 10, y: 20), timeOffset: 0, size: CGSize(width: 4, height: 4), opacity: 1, force: 0.4, azimuth: 0, altitude: .pi / 2),
      PKStrokePoint(location: CGPoint(x: 80, y: 90), timeOffset: 0.1, size: CGSize(width: 4, height: 4), opacity: 1, force: 0.7, azimuth: 0, altitude: .pi / 2),
    ]
    let stroke = PKStroke(ink: PKInk(.pen, color: .black), path: PKStrokePath(controlPoints: points, creationDate: .now),
      transform: CGAffineTransform(translationX: 100, y: 200), mask: nil)
    let original = PKDrawing(strokes: [stroke]).dataRepresentation()
    let parsed = try StudioService.inkStrokes(original)
    guard case .object(let converted) = try XCTUnwrap(parsed.first), case .array(let convertedPoints) = converted["points"],
      case .object(let first) = try XCTUnwrap(convertedPoints.first) else { return XCTFail("Missing real strokes") }
    XCTAssertEqual(first["x"]?.numberValue, 110)
    XCTAssertEqual(first["y"]?.numberValue, 220)
    XCTAssertEqual(try PKDrawing(data: original).strokes.count, 1)
    XCTAssertThrowsError(try StudioService.inkStrokes(PKDrawing().dataRepresentation()))
  }
}
