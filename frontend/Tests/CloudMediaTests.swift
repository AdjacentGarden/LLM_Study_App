import AVFoundation
import Foundation
import XCTest

@testable import BookCourse

private final class MediaProtocol: URLProtocol, @unchecked Sendable {
  nonisolated(unsafe) static var handler: ((URLRequest) throws -> (Int, [String: String], Data))?
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      let (code, headers, data) = try Self.handler!(request)
      client?.urlProtocol(
        self,
        didReceive: HTTPURLResponse(
          url: request.url!, statusCode: code,
          httpVersion: nil, headerFields: headers)!, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: data)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

@MainActor final class CloudMediaTests: XCTestCase {
  private func api() -> CloudAPI {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [MediaProtocol.self]
    return CloudAPI(
      baseURL: URL(string: "https://\(UUID().uuidString).ts.net")!,
      session: URLSession(configuration: config))
  }
  private func context() throws -> LearningMediaContext {
    let sources = try ContentBundle.load([SourceContent].self, name: "sources")
    let lesson = try XCTUnwrap(sources.flatMap(\.lessons).first { !$0.blocks.isEmpty })
    return LearningMediaContext(lesson: lesson, section: lesson.blocks.first, kind: .video)
  }
  private func job(
    _ context: LearningMediaContext, status: String, kind: String = "video",
    id: String = "task12345678", asset: String? = nil
  ) -> [String: Any] {
    [
      "id": id, "book_id": context.bookID, "kind": kind, "status": status,
      "result": ["title": "讲解短片"], "error": "", "created": 100,
      "excerpt": context.excerpt, "chapter_title": context.chapterTitle,
      "asset_url": asset.map { $0 as Any }
        ?? (status == "succeeded" ? "/api/studio/jobs/\(id)/asset" as Any : NSNull()),
    ]
  }
  private func json(_ object: Any, status: Int = 200, headers: [String: String] = [:]) throws -> (
    Int, [String: String], Data
  ) {
    (
      status, ["Content-Type": "application/json"].merging(headers) { _, b in b },
      try JSONSerialization.data(withJSONObject: object)
    )
  }
  private let capabilities: [String: Any] = [
    "image": true, "video": true,
    "video_seconds": 6, "image_estimate": 0.025, "video_estimate": 2, "media_notice": "",
  ]

  func testContextSendsSelectedLessonAndFigureWithoutInventedPages() throws {
    let sources = try ContentBundle.load([SourceContent].self, name: "sources")
    var lesson = try XCTUnwrap(sources.flatMap(\.lessons).first)
    var block = try XCTUnwrap(lesson.blocks.first)
    block.content = String(repeating: "原文", count: 2000)
    block.citations = [LessonCitation(chunk_id: "chunk", page_start: 11, page_end: 12)]
    lesson.book_id = "private-book"
    let context = LearningMediaContext(lesson: lesson, section: block, kind: .video)
    let body = context.body(kind: .video, requestID: "stable-request")
    XCTAssertEqual(body["book_id"], .string("private-book"))
    XCTAssertEqual(body["chapter_id"], .string(lesson.chapter_id))
    XCTAssertEqual(body["pages"], .array([.number(11), .number(12)]))
    XCTAssertEqual(body["consent"], .bool(true))
    XCTAssertEqual(body["kind"], .string("video"))
    XCTAssertEqual(context.excerpt.count, 3000)
  }

  func testRestoresExistingTaskPollsAndDownloadsWithAuthenticationWithoutPostingAgain() async throws
  {
    let context = try context()
    let api = api()
    let model = CloudMediaModel(api: api, context: context)
    var posts = 0
    var polls = 0
    var downloads = 0
    MediaProtocol.handler = { request in
      let path = request.url!.path
      if path == "/api/auth/me" {
        return try self.json(
          ["ok": true],
          headers: ["Set-Cookie": "zhiwo_account=media-test; Path=/; Secure; HttpOnly"])
      }
      XCTAssertEqual(request.value(forHTTPHeaderField: "Cookie"), "zhiwo_account=media-test")
      if request.httpMethod == "POST" {
        posts += 1
        throw URLError(.badServerResponse)
      }
      if path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      if path.hasSuffix("/jobs") {
        XCTAssertEqual(
          URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first?
            .value, context.bookID)
        return try self.json([
          "items": [
            self.job(context, status: "queued"),
            self.job(context, status: "succeeded", kind: "recognize", id: "note-task"),
          ]
        ])
      }
      if path.hasSuffix("/asset") {
        downloads += 1
        return (
          200, ["Content-Type": "video/mp4"], Data([0, 0, 0, 20]) + Data("ftypisom0000".utf8)
        )
      }
      polls += 1
      return try self.json(self.job(context, status: polls == 1 ? "polling" : "succeeded"))
    }
    let _: CloudOK = try await api.request("/api/auth/me")
    await model.load()
    XCTAssertEqual(model.history.count, 1, "Non-media note jobs must not break history decoding")
    XCTAssertEqual(model.job?.status, "queued")
    await model.follow(pollInterval: .milliseconds(1))
    XCTAssertEqual(model.job?.status, "succeeded")
    XCTAssertEqual(polls, 2)
    XCTAssertEqual(downloads, 1)
    XCTAssertEqual(posts, 0)
    let url = try XCTUnwrap(model.videoURL)
    XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
    model.clearAsset()
    XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
  }

  func testAmbiguousSubmitRetriesKeepRequestIdentityAndNeverResubmitAnAcceptedTask() async throws {
    let context = try context()
    let model = CloudMediaModel(api: api(), context: context)
    model.capabilities = try JSONDecoder().decode(
      CloudMediaCapabilities.self, from: JSONSerialization.data(withJSONObject: capabilities))
    var bodies: [Data] = []
    MediaProtocol.handler = { request in
      XCTAssertEqual(request.url?.path, "/api/studio/media")
      XCTAssertEqual(request.httpMethod, "POST")
      var body = request.httpBody ?? Data()
      if let stream = request.httpBodyStream {
        stream.open()
        defer { stream.close() }
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
          let count = stream.read(&buffer, maxLength: buffer.count)
          if count <= 0 { break }
          body.append(contentsOf: buffer.prefix(count))
        }
      }
      bodies.append(body)
      if bodies.count == 1 { throw URLError(.networkConnectionLost) }
      return try self.json(self.job(context, status: "queued"))
    }
    await model.submit()
    XCTAssertNotNil(model.error)
    XCTAssertEqual(
      bodies.count, 1, "A paid POST must never be automatically replayed by the transport")
    await model.submit()
    XCTAssertNil(model.error)
    XCTAssertEqual(bodies.count, 2)
    let first = try JSONDecoder().decode([String: JSONValue].self, from: bodies[0])
    let second = try JSONDecoder().decode([String: JSONValue].self, from: bodies[1])
    XCTAssertEqual(
      first, second, "An explicit retry must use the same idempotent request ID and context")
    XCTAssertEqual(first["book_id"], .string(context.bookID))
    await model.submit()
    XCTAssertEqual(bodies.count, 2)
    model.select(
      try JSONDecoder().decode(
        CloudMediaJob.self,
        from: JSONSerialization.data(withJSONObject: job(context, status: "uncertain"))))
    await model.submit()
    XCTAssertEqual(
      bodies.count, 2, "An uncertain provider result must not trigger a second paid generation")
  }

  func testDisabledProviderDoesNotSubmitAndServerErrorsAreVisible() async throws {
    let context = try context()
    let model = CloudMediaModel(api: api(), context: context)
    var requests = 0
    MediaProtocol.handler = { request in
      requests += 1
      if request.url!.path.hasSuffix("capabilities") {
        return try self.json(["image": false, "video": false, "media_notice": "维护中"])
      }
      return try self.json(["items": []])
    }
    await model.load()
    XCTAssertEqual(model.capabilities?.media_notice, "维护中")
    await model.submit()
    XCTAssertEqual(requests, 2)
    model.capabilities = try JSONDecoder().decode(
      CloudMediaCapabilities.self, from: JSONSerialization.data(withJSONObject: capabilities))
    MediaProtocol.handler = { _ in try self.json(["detail": "今日生成次数已用完"], status: 429) }
    await model.submit()
    XCTAssertEqual(model.error, "今日生成次数已用完")
  }

  func testAssetDownloadsRejectExternalURLsUnfinishedJobsInvalidMediaAndExpiredSession()
    async throws
  {
    let context = try context()
    let api = api()
    var requests = 0
    MediaProtocol.handler = { _ in
      requests += 1
      return try self.json(["error": "not a video"])
    }
    for spec in [
      job(context, status: "polling"),
      job(context, status: "succeeded", asset: "https://other.test/video.mp4"),
      job(context, status: "succeeded", id: "../auth/me"),
    ] {
      let value = try JSONDecoder().decode(
        CloudMediaJob.self, from: JSONSerialization.data(withJSONObject: spec))
      do {
        _ = try await api.downloadMedia(value)
        XCTFail("Unsafe asset must be rejected")
      } catch {}
    }
    XCTAssertEqual(requests, 0)
    let value = try JSONDecoder().decode(
      CloudMediaJob.self,
      from: JSONSerialization.data(withJSONObject: job(context, status: "succeeded")))
    do {
      _ = try await api.downloadMedia(value)
      XCTFail("JSON must not reach AVPlayer")
    } catch {}
    XCTAssertEqual(requests, 1)
    MediaProtocol.handler = { _ in try self.json(["detail": "登录已过期"], status: 401) }
    do {
      _ = try await api.downloadMedia(value)
      XCTFail("Expired credentials cannot download an asset")
    } catch let error as CloudError { XCTAssertEqual(error.status, 401) }
  }

  func testDismissedPollingDoesNotGenerateAgainOrPublishAnAsset() async throws {
    let context = try context()
    let model = CloudMediaModel(api: api(), context: context)
    model.select(
      try JSONDecoder().decode(
        CloudMediaJob.self,
        from: JSONSerialization.data(withJSONObject: job(context, status: "queued"))))
    var requests = 0
    MediaProtocol.handler = { _ in
      requests += 1
      throw URLError(.badServerResponse)
    }
    let task = Task { await model.follow(pollInterval: .seconds(20)) }
    await Task.yield()
    task.cancel()
    await task.value
    XCTAssertEqual(requests, 0)
    XCTAssertNil(model.error)
    XCTAssertNil(model.videoURL)
  }

  func testAuthenticatedMP4DownloadProducesPlayableLocalVideo() async throws {
    let context = try context()
    let model = CloudMediaModel(api: api(), context: context)
    let source = try XCTUnwrap(ContentBundle.url("/assets/lesson/meiosis-explainer-demo-v1.mp4"))
    let bytes = try Data(contentsOf: source)
    MediaProtocol.handler = { request in
      XCTAssertEqual(request.url?.path, "/api/studio/jobs/task12345678/asset")
      return (200, ["Content-Type": "video/mp4"], bytes)
    }
    let value = try JSONDecoder().decode(
      CloudMediaJob.self,
      from: JSONSerialization.data(withJSONObject: job(context, status: "succeeded")))
    model.select(value)
    defer { model.clearAsset() }
    await model.follow()
    XCTAssertNil(model.error)
    let url = try XCTUnwrap(model.videoURL)
    let asset = AVURLAsset(url: url)
    let duration = try await asset.load(.duration)
    XCTAssertGreaterThan(duration.seconds, 1)
    let player = AVPlayer(url: url)
    player.isMuted = true
    player.play()
    defer { player.pause() }
    for _ in 0..<100 {
      if player.currentTime().seconds >= 0.3 { break }
      try await Task.sleep(for: .milliseconds(100))
    }
    XCTAssertGreaterThanOrEqual(
      player.currentTime().seconds, 0.3,
      "The authenticated download must actually play, not merely produce a URL")
  }
}
