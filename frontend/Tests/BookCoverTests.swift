import Foundation
import UIKit
import XCTest

@testable import BookCourse

private final class BookCoverProtocol: URLProtocol, @unchecked Sendable {
  struct Reply {
    var status = 200
    var mime = "image/png"
    var data: Data
    var delay: TimeInterval = 0
  }
  nonisolated(unsafe) static var handler: ((URLRequest) throws -> Reply)?
  private var pending: DispatchWorkItem?
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      let reply = try Self.handler!(request)
      let work = DispatchWorkItem { [weak self] in
        guard let self else { return }
        self.client?.urlProtocol(self, didReceive: HTTPURLResponse(url: self.request.url!,
          statusCode: reply.status, httpVersion: nil,
          headerFields: ["Content-Type": reply.mime])!, cacheStoragePolicy: .notAllowed)
        self.client?.urlProtocol(self, didLoad: reply.data)
        self.client?.urlProtocolDidFinishLoading(self)
      }
      pending = work
      DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay, execute: work)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() { pending?.cancel() }
}

@MainActor final class BookCoverTests: XCTestCase {
  private let path = "/api/books/book_test/cover?v=first"
  private func account(_ id: String = "cover-owner") -> CloudAccountState {
    CloudAccountState(account: .init(email: "cover@example.com", stage: "university",
      interests: [], goal: "interest", is_demo: false), user_id: id,
      email_available: true, demo_available: false)
  }
  private func api() -> CloudAPI {
    let url = URL(string: "https://cover-\(UUID().uuidString).example")!
    CloudAPI.seedLegacyIdentityForTesting(url: url, account: account())
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [BookCoverProtocol.self]
    return CloudAPI(baseURL: url, session: URLSession(configuration: configuration))
  }
  private func png(_ color: UIColor = .blue) -> Data {
    UIGraphicsImageRenderer(size: CGSize(width: 3, height: 4)).pngData { context in
      color.setFill(); context.fill(CGRect(x: 0, y: 0, width: 3, height: 4))
    }
  }
  private func request(_ path: String, api: CloudAPI, epoch: UUID = UUID()) -> CloudBookCoverRequest {
    CloudBookCoverRequest(path: path, origin: api.baseURL.absoluteString,
      accountID: "cover-owner", epoch: epoch)
  }
  private func resource() -> CloudResource {
    CloudResource(id: "resource", book_id: "book_test", workspace_id: "workspace", revision: 0,
      filename: "化学.pdf", status: "ready", courses: [], progress: CloudProgress())
  }

  func testResourceCoverIsOptionalAndMapsBothGeneratedAndPublishedSources() throws {
    var resource = resource()
    let legacy = try JSONDecoder().decode(CloudResource.self, from: JSONEncoder().encode(resource))
    XCTAssertNil(legacy.cover_url)
    resource.cover_url = path
    XCTAssertEqual(CloudMapping.source(resource).summary.cover_url, path)
    var source = try ContentBundle.load([SourceContent].self, name: "sources")[0]
    source.summary.cover_url = "/assets/old-cover.png"
    resource.source_content = source
    let mapped = CloudMapping.source(resource)
    XCTAssertEqual(mapped.summary.cover_url, path)
    XCTAssertEqual(mapped.lessons.first?.blocks, source.lessons.first?.blocks)
    resource.cover_url = nil
    XCTAssertEqual(CloudMapping.source(resource).summary.cover_url, source.summary.cover_url)
  }

  func testHomePrefersFreshWorkspaceCoverAndRefreshUpdatesUnselectedSourceWithoutReplacingLessons() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let store = AppStore(repository: try LocalBookCourseRepository(directory: directory), cloud: api())
    var resource = resource()
    resource.cover_url = path
    var source = CloudMapping.source(resource)
    source.summary.cover_url = "/assets/stale-cover.png"
    store.sources = [source]
    store.cloudState.workspaces = [CloudWorkspace(id: "workspace", name: "化学", preferences: [:],
      created: 0, updated: 0, resources: [resource])]
    let course = Course(id: "workspace", name: "化学", resourceIDs: [resource.book_id],
      activeResourceID: resource.book_id, createdAt: .now)
    store.selectedCourseID = "another-workspace"
    XCTAssertEqual(store.courseFeatureCover(course), path)
    store.rebuildCloudCourses()
    XCTAssertEqual(store.sources[0].summary.cover_url, path)
    XCTAssertEqual(store.sources[0].lessons, source.lessons)
  }

  func testAuthenticatedDownloadCachesByVersionAccountEpochAndService() async throws {
    let api = api(), epoch = UUID(), bytes = png()
    var reads = 0
    BookCoverProtocol.handler = { request in
      reads += 1
      XCTAssertEqual(request.httpMethod, "GET")
      XCTAssertEqual(request.value(forHTTPHeaderField: "Cookie"), "zhiwo_account=test-credential")
      XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalCacheData)
      return .init(data: bytes)
    }
    for _ in 0..<2 {
      let value = try await api.downloadBookCover(path, accountID: "cover-owner", epoch: epoch)
      XCTAssertEqual(value, bytes)
    }
    XCTAssertEqual(reads, 1)
    _ = try await api.downloadBookCover(path + "-new", accountID: "cover-owner", epoch: epoch)
    _ = try await api.downloadBookCover(path, accountID: "cover-owner", epoch: UUID())
    await api.rememberAccount(account("other-owner"))
    _ = try await api.downloadBookCover(path, accountID: "other-owner", epoch: epoch)
    let otherService = self.api()
    _ = try await otherService.downloadBookCover(path, accountID: "cover-owner", epoch: epoch)
    XCTAssertEqual(reads, 5)
    await api.clearCredentials()
    do {
      _ = try await api.downloadBookCover(path, accountID: "other-owner", epoch: epoch)
      XCTFail("Signing out must not expose a cached private cover")
    } catch let error as CloudError { XCTAssertEqual(error.status, 401) }
    XCTAssertEqual(reads, 5)
  }

  func testCoverRejectsUnownedBooksExternalPathsAndInvalidImages() async throws {
    let api = api(), epoch = UUID()
    var reads = 0
    BookCoverProtocol.handler = { _ in reads += 1; return .init(status: 403, mime: "application/json",
      data: Data(#"{"detail":"无权查看教材封面"}"#.utf8)) }
    for bad in ["https://other.example" + path, "//other.example" + path,
      "/api/books/../private/cover", "/api/books/book_test/cover?other=first",
      "/api/books/book%2Fprivate/cover?v=first", path + "#fragment"] {
      do { _ = try await api.downloadBookCover(bad, accountID: "cover-owner", epoch: epoch)
        XCTFail("Invalid cover route accepted: \(bad)")
      } catch let error as CloudError { XCTAssertEqual(error.status, 422) }
    }
    XCTAssertEqual(reads, 0)
    do { _ = try await api.downloadBookCover(path, accountID: "cover-owner", epoch: epoch)
      XCTFail("Unowned book accepted")
    } catch let error as CloudError { XCTAssertEqual(error.status, 403) }
    for (mime, bytes) in [("application/json", Data("{}".utf8)),
      ("image/png", Data([137, 80, 78, 71, 13, 10, 26, 10]))] {
      BookCoverProtocol.handler = { _ in reads += 1; return .init(mime: mime, data: bytes) }
      do { _ = try await api.downloadBookCover(path, accountID: "cover-owner", epoch: epoch)
        XCTFail("Invalid image accepted")
      } catch let error as CloudError { XCTAssertEqual(error.status, 422) }
    }
    XCTAssertEqual(reads, 3)
  }

  func testLateCoverResponseCannotReplaceNewBookOrIdentityEpoch() async throws {
    let api = api(), model = CloudBookCoverModel(), blue = png(), red = png(.red)
    let old = request(path, api: api), current = request(path + "-new", api: api)
    let started = expectation(description: "old cover request started")
    BookCoverProtocol.handler = { request in
      if request.url?.query == "v=first" {
        started.fulfill()
        return .init(data: blue, delay: 0.15)
      }
      return .init(data: red)
    }
    let oldTask = Task { await model.load(old, api: api) }
    await fulfillment(of: [started], timeout: 2)
    await model.load(current, api: api)
    await oldTask.value
    XCTAssertEqual(model.request, current)
    XCTAssertEqual(model.data, red)
    await model.load(nil, api: api)
    XCTAssertNil(model.data)
    XCTAssertNil(model.request)
  }
}
