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
  private func client(_ handler: @escaping StudioContractProtocol.Handler) -> (CloudAPI, URLSession, String) {
    let host = "studio-\(UUID().uuidString.lowercased()).example"
    StudioContractProtocol.install(host: host, handler: handler)
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [StudioContractProtocol.self]
    let session = URLSession(configuration: config)
    return (CloudAPI(baseURL: URL(string: "https://\(host)")!, session: session), session, host)
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
