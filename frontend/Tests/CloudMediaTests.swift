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
      client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: code,
        httpVersion: nil, headerFields: headers)!, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: data)
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

@MainActor final class CloudMediaTests: XCTestCase {
  private let jobID = String(repeating: "a", count: 32)
  private let videoBytes = Data([0, 0, 0, 20]) + Data("ftypisom0000".utf8)
  private let capabilities: [String: Any] = [
    "image": true, "video": true, "voice_notes": true, "notes_ai": true, "draft_scope": "test-scope",
    "video_seconds": 6, "image_estimate": 0.025, "video_estimate": 2, "media_notice": "",
  ]

  private func api() -> CloudAPI {
    let url = URL(string: "https://\(UUID().uuidString).ts.net")!
    let account = CloudAccountState(account: .init(email: "media@example.com", stage: "university",
      interests: [], goal: "interest", is_demo: false), user_id: "media-test-account",
      email_available: true, demo_available: false)
    CloudAPI.seedLegacyIdentityForTesting(url: url, account: account)
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [MediaProtocol.self]
    return CloudAPI(baseURL: url, session: URLSession(configuration: config))
  }
  private func context() throws -> LessonMediaRequest {
    let sources = try ContentBundle.load([SourceContent].self, name: "sources")
    let lesson = try XCTUnwrap(sources.flatMap(\.lessons).first { !$0.blocks.isEmpty })
    return LessonMediaRequest(lesson: lesson, section: lesson.blocks.first, kind: .video)
  }
  private func storage() -> URL {
    FileManager.default.temporaryDirectory.appendingPathComponent("media-test-" + UUID().uuidString)
  }
  private func job(_ context: LessonMediaRequest, status: String, kind: String = "video",
    id: String? = nil, asset: String? = nil, requestID: String = "media_existing_request") -> [String: Any] {
    let id = id ?? jobID
    return ["id": id, "book_id": context.bookID, "kind": kind, "status": status,
      "result": ["title": "讲解短片"], "error": "", "created": 100, "request_id": requestID,
      "excerpt": context.excerpt, "chapter_title": context.chapterTitle,
      "asset_url": asset.map { $0 as Any }
        ?? (status == "succeeded" ? "/api/studio/jobs/\(id)/asset" as Any : NSNull())]
  }
  private func json(_ object: Any, status: Int = 200) throws -> (Int, [String: String], Data) {
    (status, ["Content-Type": "application/json"], try JSONSerialization.data(withJSONObject: object))
  }
  private func body(_ request: URLRequest) throws -> [String: JSONValue] {
    var data = request.httpBody ?? Data()
    if let stream = request.httpBodyStream {
      stream.open(); defer { stream.close() }
      var buffer = [UInt8](repeating: 0, count: 4096)
      while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count <= 0 { break }
        data.append(contentsOf: buffer.prefix(count))
      }
    }
    return try JSONDecoder().decode([String: JSONValue].self, from: data)
  }

  func testContextSendsSelectedLessonAndFigureUsingCloudWireContractWithoutInventedPages() async throws {
    let sources = try ContentBundle.load([SourceContent].self, name: "sources")
    var lesson = try XCTUnwrap(sources.flatMap(\.lessons).first)
    var block = try XCTUnwrap(lesson.blocks.first)
    block.content = String(repeating: "原文", count: 2000)
    block.citations = [LessonCitation(chunk_id: "chunk", page_start: 11, page_end: 12)]
    lesson.book_id = "private-book"
    let context = LessonMediaRequest(lesson: lesson, section: block, kind: .video)
    var sent: [String: JSONValue] = [:]
    MediaProtocol.handler = { request in
      XCTAssertEqual(request.url?.path, "/api/studio/media")
      sent = try self.body(request)
      return try self.json(self.job(context, status: "queued"))
    }
    _ = try await StudioService(api: api()).submitMedia(kind: context.kind, bookID: context.bookID,
      chapterID: context.chapterID, chapterTitle: context.chapterTitle, excerpt: context.excerpt,
      pages: context.pages, requestID: "stable_request")
    XCTAssertEqual(sent["book_id"], .string("private-book"))
    XCTAssertEqual(sent["chapter_id"], .string(lesson.chapter_id))
    XCTAssertEqual(sent["pages"], .array([.number(11), .number(12)]))
    XCTAssertEqual(sent["consent"], .bool(true))
    XCTAssertEqual(sent["kind"], .string("video"))
    XCTAssertEqual(sent["goal"], .string("意思"))
    XCTAssertNil(sent["lesson_id"])
    XCTAssertNil(sent["course_id"])
    XCTAssertEqual(context.excerpt.count, 3000)
  }

  func testRestoresExistingTaskPollsAndDownloadsWithAuthenticationWithoutPostingAgain() async throws {
    let context = try context(), api = api(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api, request: context, storageRoot: root)
    var posts = 0, polls = 0, downloads = 0
    MediaProtocol.handler = { request in
      XCTAssertEqual(request.value(forHTTPHeaderField: "Cookie"), "zhiwo_account=test-credential")
      if request.httpMethod == "POST" { posts += 1; throw URLError(.badServerResponse) }
      let path = request.url!.path
      if path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      if path.hasSuffix("/jobs") {
        return try self.json(["items": [self.job(context, status: "queued"),
          self.job(context, status: "succeeded", kind: "recognize", id: "note-task")]])
      }
      if path.hasSuffix("/asset") {
        downloads += 1
        return (200, ["Content-Type": "video/mp4"], self.videoBytes)
      }
      polls += 1
      return try self.json(self.job(context, status: polls == 1 ? "polling" : "succeeded"))
    }
    await model.load()
    XCTAssertEqual(model.history.count, 1)
    XCTAssertEqual(model.job?.status, "queued")
    await model.follow(pollInterval: .milliseconds(1))
    XCTAssertEqual(model.job?.status, "succeeded")
    XCTAssertEqual(polls, 2)
    XCTAssertEqual(downloads, 1)
    XCTAssertEqual(posts, 0)
    let url = try XCTUnwrap(model.videoURL)
    XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
    model.clearAsset()
    XCTAssertNil(model.videoURL)
    let reopened = CloudMediaModel(api: api, request: context, storageRoot: root)
    await reopened.load()
    XCTAssertEqual(reopened.receipt?.jobID, jobID)
    await reopened.follow(pollInterval: .milliseconds(1))
    XCTAssertEqual(reopened.job?.status, "succeeded")
    XCTAssertNotNil(reopened.videoURL)
    XCTAssertEqual(posts, 0, "Reopening a persisted cloud receipt must only read the original task")
  }

  func testAmbiguousSubmitPersistsRequestIdentityAndOnlyLooksUpOriginalAfterRelaunch() async throws {
    let context = try context(), api = api(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api, request: context, storageRoot: root)
    var posts = 0, originalRequestID: String?, lookups = 0
    MediaProtocol.handler = { request in
      let path = request.url!.path
      if path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      if request.httpMethod == "POST" {
        posts += 1
        originalRequestID = try self.body(request)["request_id"]?.stringValue
        throw URLError(.networkConnectionLost)
      }
      if path.hasSuffix("/jobs") {
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        if let requestID = query.first(where: { $0.name == "request_id" })?.value {
          lookups += 1
          XCTAssertEqual(requestID, originalRequestID)
          return try self.json(["items": [self.job(context, status: "queued", requestID: requestID)]])
        }
        return try self.json(["items": []])
      }
      if path.hasSuffix("/asset") { return (200, ["Content-Type": "video/mp4"], self.videoBytes) }
      return try self.json(self.job(context, status: "succeeded"))
    }
    await model.load()
    await model.submit()
    XCTAssertNotNil(model.error)
    XCTAssertEqual(posts, 1)
    XCTAssertTrue(model.receipt?.submissionUncertain == true)
    XCTAssertEqual(model.receipt?.requestID, originalRequestID)
    await model.submit()
    XCTAssertEqual(posts, 1, "Unknown submission must block a second paid POST")
    let reopened = CloudMediaModel(api: api, request: context, storageRoot: root)
    await reopened.load()
    XCTAssertEqual(reopened.receipt?.requestID, originalRequestID)
    await reopened.follow(pollInterval: .milliseconds(1))
    XCTAssertEqual(lookups, 1)
    XCTAssertEqual(reopened.job?.status, "succeeded")
    XCTAssertEqual(reopened.receipt?.requestID, originalRequestID)
    await reopened.submit()
    XCTAssertEqual(posts, 1)
  }

  func testDisabledProviderDoesNotSubmitAndServerErrorsAreVisible() async throws {
    let context = try context(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api(), request: context, storageRoot: root)
    var posts = 0
    MediaProtocol.handler = { request in
      if request.url!.path.hasSuffix("capabilities") {
        var disabled = self.capabilities
        disabled["image"] = false; disabled["video"] = false; disabled["media_notice"] = "维护中"
        return try self.json(disabled)
      }
      return try self.json(["items": []])
    }
    await model.load()
    XCTAssertEqual(model.capabilities?.media_notice, "维护中")
    await model.submit()
    XCTAssertNil(model.receipt)
    MediaProtocol.handler = { request in
      if request.url!.path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      posts += 1
      return try self.json(["detail": "今日生成次数已用完"], status: 429)
    }
    model.capabilities = try JSONDecoder().decode(StudioCapabilities.self,
      from: JSONSerialization.data(withJSONObject: capabilities))
    await model.submit()
    XCTAssertEqual(posts, 1)
    XCTAssertEqual(model.error, "今日生成次数已用完")
    XCTAssertFalse(model.receipt?.submissionUncertain ?? true)
  }

  func testAssetDownloadsRejectExternalURLsUnfinishedJobsInvalidMediaAndExpiredSession() async throws {
    let context = try context(), api = api()
    var requests = 0
    MediaProtocol.handler = { _ in requests += 1; return try self.json(["error": "not a video"]) }
    for spec in [job(context, status: "polling"),
      job(context, status: "succeeded", asset: "https://other.test/video.mp4"),
      job(context, status: "succeeded", id: "../auth/me")] {
      let value = try JSONDecoder().decode(StudioJob.self, from: JSONSerialization.data(withJSONObject: spec))
      do { _ = try await api.downloadMedia(value); XCTFail("Unsafe asset must be rejected") } catch { }
    }
    XCTAssertEqual(requests, 0)
    let value = try JSONDecoder().decode(StudioJob.self,
      from: JSONSerialization.data(withJSONObject: job(context, status: "succeeded")))
    do { _ = try await api.downloadMedia(value); XCTFail("JSON must not reach AVPlayer") } catch { }
    XCTAssertEqual(requests, 1)
    MediaProtocol.handler = { _ in try self.json(["detail": "登录已过期"], status: 401) }
    do { _ = try await api.downloadMedia(value); XCTFail("Expired session") }
    catch let error as CloudError { XCTAssertEqual(error.status, 401) }
  }

  func testDismissedPollingDoesNotGenerateAgainOrPublishAnAsset() async throws {
    let context = try context(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api(), request: context, storageRoot: root)
    MediaProtocol.handler = { request in
      if request.url!.path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      return try self.json(["items": [self.job(context, status: "queued")]])
    }
    await model.load()
    var requests = 0
    MediaProtocol.handler = { _ in requests += 1; throw URLError(.badServerResponse) }
    let task = Task { await model.follow(pollInterval: .seconds(20)) }
    task.cancel()
    await task.value
    XCTAssertEqual(requests, 0)
    XCTAssertNil(model.error)
    XCTAssertNil(model.videoURL)
  }

  func testAuthenticatedMP4DownloadProducesPlayableLocalVideo() async throws {
    let context = try context(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api(), request: context, storageRoot: root)
    let source = try XCTUnwrap(ContentBundle.url("/assets/lesson/meiosis-explainer-demo-v1.mp4"))
    let bytes = try Data(contentsOf: source)
    MediaProtocol.handler = { request in
      let path = request.url!.path
      if path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      if path.hasSuffix("/jobs") { return try self.json(["items": [self.job(context, status: "succeeded")]]) }
      if path.hasSuffix("/asset") { return (200, ["Content-Type": "video/mp4"], bytes) }
      return try self.json(self.job(context, status: "succeeded"))
    }
    await model.load()
    await model.follow()
    XCTAssertNil(model.error)
    let url = try XCTUnwrap(model.videoURL)
    let duration = try await AVURLAsset(url: url).load(.duration)
    XCTAssertGreaterThan(duration.seconds, 1)
    let player = AVPlayer(url: url)
    player.isMuted = true; player.play()
    defer { player.pause() }
    for _ in 0..<100 {
      if player.currentTime().seconds >= 0.3 { break }
      try await Task.sleep(for: .milliseconds(100))
    }
    XCTAssertGreaterThanOrEqual(player.currentTime().seconds, 0.3)
  }

  func testCloudModelsDecodeWithoutOptionalPresentationMetadataAndAcceptPNG() async throws {
    let value = try JSONDecoder().decode(StudioJob.self, from: JSONSerialization.data(withJSONObject: [
      "id": jobID, "book_id": "book", "kind": "image", "status": "succeeded", "result": [:],
      "asset_url": "/api/studio/jobs/\(jobID)/asset"]))
    XCTAssertNil(value.created); XCTAssertNil(value.excerpt); XCTAssertNil(value.chapter_title)
    let caps = try JSONDecoder().decode(StudioCapabilities.self, from: JSONSerialization.data(withJSONObject: [
      "image": true, "video": false, "voice_notes": false, "notes_ai": false, "draft_scope": "owner"]))
    XCTAssertNil(caps.video_estimate)
    XCTAssertNil(caps.video_notice)
    XCTAssertNil(caps.image_width)
    XCTAssertNil(caps.image_height)
    XCTAssertNil(caps.video_resolution)
    XCTAssertNil(caps.video_requires_image)
    XCTAssertNil(caps.generationTarget(.image))
    XCTAssertNil(caps.generationTarget(.video))
    XCTAssertNil(caps.generationRequirement(.video))
    let bytes = Data([137, 80, 78, 71, 13, 10, 26, 10, 0])
    MediaProtocol.handler = { _ in (200, ["Content-Type": "image/png"], bytes) }
    let downloaded = try await api().downloadMedia(value)
    XCTAssertEqual(downloaded, bytes)
  }

  func testVideoNoticeOverridesGlobalNoticeAndTargetCopyAppliesOnlyToNewGeneration() throws {
    var wire = capabilities
    wire["video_notice"] = " 视频维护中 "
    wire["media_notice"] = "媒体服务维护中"
    wire["image_width"] = 2048
    wire["image_height"] = 1536
    wire["video_resolution"] = "2K"
    var caps = try JSONDecoder().decode(StudioCapabilities.self,
      from: JSONSerialization.data(withJSONObject: wire))
    XCTAssertEqual(caps.unavailableNotice(.video), "视频维护中")
    XCTAssertEqual(caps.unavailableNotice(.image), "媒体服务维护中")
    XCTAssertEqual(caps.generationTarget(.image), "新生成图片目标尺寸：2048 × 1536 像素。")
    XCTAssertEqual(caps.generationTarget(.video), "新生成视频目标分辨率：2K。")
    caps.video_notice = " \n "
    XCTAssertEqual(caps.unavailableNotice(.video), "媒体服务维护中")
    caps.media_notice = ""
    XCTAssertEqual(caps.unavailableNotice(.video), "视频生成服务暂未就绪，请检查后端的视频服务配置。")
    XCTAssertEqual(caps.unavailableNotice(.image), "辅助图生成服务暂未就绪，请检查后端的图像服务配置。")
    caps.image_width = 0
    XCTAssertNil(caps.generationTarget(.image))
  }

  func testDisabledVideoShowsSpecificReasonWithoutSubmittingAnyGeneration() async throws {
    let context = try context(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api(), request: context, storageRoot: root)
    var posts = 0
    MediaProtocol.handler = { request in
      if request.httpMethod == "POST" { posts += 1; throw URLError(.badServerResponse) }
      if request.url!.path.hasSuffix("capabilities") {
        var wire = self.capabilities
        wire["video"] = false
        wire["video_notice"] = "视频服务暂停配置"
        return try self.json(wire)
      }
      return try self.json(["items": []])
    }
    await model.load()
    XCTAssertEqual(model.error, "视频服务暂停配置")
    XCTAssertFalse(model.canSubmit)
    await model.submit()
    XCTAssertEqual(posts, 0)
    XCTAssertNil(model.receipt)
  }

  func testOnlyImageToVideoModelExplainsCurrentExcerptImageRequirement() throws {
    var wire = capabilities
    wire["video_requires_image"] = true
    let imageToVideo = try JSONDecoder().decode(StudioCapabilities.self,
      from: JSONSerialization.data(withJSONObject: wire))
    XCTAssertEqual(imageToVideo.generationRequirement(.video), "低价视频需先生成当前选段的辅助图。")
    XCTAssertNil(imageToVideo.generationRequirement(.image))
    wire["video_requires_image"] = false
    let textToVideo = try JSONDecoder().decode(StudioCapabilities.self,
      from: JSONSerialization.data(withJSONObject: wire))
    XCTAssertNil(textToVideo.generationRequirement(.video))
  }

  func testHistoryCannotReplaceAnUnresolvedGenerationWithAnOlderFailedTask() async throws {
    let context = try context(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api(), request: context, storageRoot: root)
    var posts = 0
    MediaProtocol.handler = { request in
      if request.httpMethod == "POST" { posts += 1; throw URLError(.badServerResponse) }
      if request.url!.path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      if request.url!.path.hasSuffix("/jobs") {
        return try self.json(["items": [self.job(context, status: "uncertain")]])
      }
      return try self.json(self.job(context, status: "uncertain"))
    }
    await model.load()
    let failed = try JSONDecoder().decode(StudioJob.self, from: JSONSerialization.data(withJSONObject:
      job(context, status: "failed", id: String(repeating: "b", count: 32))))
    model.select(failed)
    XCTAssertEqual(model.receipt?.jobID, jobID)
    XCTAssertEqual(model.receipt?.state, "uncertain")
    XCTAssertFalse(model.canSubmit)
    await model.submit()
    XCTAssertEqual(posts, 0)
  }

  func testCompletedAccountScopedCacheRemainsReadableWhenServiceIsTemporarilyOffline() async throws {
    let context = try context(), api = api(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api, request: context, storageRoot: root)
    MediaProtocol.handler = { request in
      let path = request.url!.path
      if path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      if path.hasSuffix("/jobs") { return try self.json(["items": [self.job(context, status: "succeeded")]]) }
      if path.hasSuffix("/asset") { return (200, ["Content-Type": "video/mp4"], self.videoBytes) }
      return try self.json(self.job(context, status: "succeeded"))
    }
    await model.load()
    await model.follow()
    let cached = try XCTUnwrap(model.videoURL)
    MediaProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }
    let reopened = CloudMediaModel(api: api, request: context, storageRoot: root)
    await reopened.load()
    XCTAssertNotNil(reopened.error)
    XCTAssertEqual(reopened.videoURL, cached)
    XCTAssertEqual(try Data(contentsOf: cached), videoBytes)
    await api.clearCredentials()
    let signedOut = CloudMediaModel(api: api, request: context, storageRoot: root)
    await signedOut.load()
    XCTAssertNil(signedOut.videoURL)
    XCTAssertTrue(signedOut.history.isEmpty)
  }

  func testAccountChangeClearsHistoryAndStopsPollingBeforeAnyRequest() async throws {
    let context = try context(), api = api(), root = storage()
    defer { try? FileManager.default.removeItem(at: root) }
    let model = CloudMediaModel(api: api, request: context, storageRoot: root)
    MediaProtocol.handler = { request in
      if request.url!.path.hasSuffix("capabilities") { return try self.json(self.capabilities) }
      return try self.json(["items": [self.job(context, status: "queued")]])
    }
    await model.load()
    var requests = 0
    MediaProtocol.handler = { _ in requests += 1; throw URLError(.badServerResponse) }
    await api.clearCredentials()
    await model.follow()
    XCTAssertEqual(requests, 0)
    XCTAssertTrue(model.history.isEmpty)
    XCTAssertNil(model.videoURL)
    XCTAssertFalse(model.canSubmit)
  }
}
