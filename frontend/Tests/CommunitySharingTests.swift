import Foundation
import XCTest

@testable import BookCourse

private final class CommunitySharingProtocol: URLProtocol, @unchecked Sendable {
  struct Reply {
    var status = 200
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
          headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        self.client?.urlProtocol(self, didLoad: reply.data)
        self.client?.urlProtocolDidFinishLoading(self)
      }
      pending = work
      DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay, execute: work)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() { pending?.cancel() }
}

@MainActor final class CommunitySharingTests: XCTestCase {
  private func account(_ id: String = "owner") -> CloudAccountState {
    CloudAccountState(account: .init(email: "share@example.com", stage: "university", interests: [],
      goal: "interest", is_demo: false), user_id: id, email_available: true, demo_available: false)
  }
  private func api() -> CloudAPI {
    let url = URL(string: "https://share-\(UUID().uuidString).ts.net")!
    CloudAPI.seedLegacyIdentityForTesting(url: url, account: account())
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [CommunitySharingProtocol.self]
    return CloudAPI(baseURL: url, session: URLSession(configuration: configuration))
  }
  private func storage() -> UserDefaults {
    UserDefaults(suiteName: "community-sharing-test-" + UUID().uuidString)!
  }
  private func candidates() -> [CommunityBookShareCandidate] {
    ["chemistry", "biology"].map { id in
      CommunityBookShareCandidate(book_id: id, title: id == "chemistry" ? "化学" : "生物",
        filename: id + ".pdf", page_count: 138, chapter_count: 4, summary: "教材简介",
        cover_url: "/api/books/\(id)/cover?v=first", publication_status: .unshared, can_withdraw: false)
    }
  }
  private func reply<T: Encodable>(_ value: T, status: Int = 200,
    delay: TimeInterval = 0) throws -> CommunitySharingProtocol.Reply {
    .init(status: status, data: try JSONEncoder().encode(value), delay: delay)
  }
  private func body(_ request: URLRequest) throws -> [String: JSONValue] {
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
    return try JSONDecoder().decode([String: JSONValue].self, from: bytes)
  }

  func testOpeningAndChoosingBooksOnlyReadsAndRightsGateExplicitPublish() async throws {
    let api = api(), model = CommunitySharingModel(api: api, accountID: "owner", epoch: UUID(),
      preferredBookID: "biology", storage: storage())
    var posts = 0
    CommunitySharingProtocol.handler = { request in
      XCTAssertEqual(request.value(forHTTPHeaderField: "Cookie"), "zhiwo_account=test-credential")
      if request.httpMethod == "POST" { posts += 1; throw URLError(.badServerResponse) }
      return try self.reply(self.candidates())
    }
    await model.load()
    XCTAssertEqual(model.selectedBookID, "biology")
    XCTAssertEqual(model.selectedCandidate?.title, "生物")
    model.description = "只发布教材简介"
    await model.publish()
    XCTAssertEqual(posts, 0)
    model.rightsConfirmed = true
    XCTAssertTrue(model.canPublish)
    model.selectedBookID = "chemistry"
    XCTAssertEqual(model.description, "")
    XCTAssertFalse(model.rightsConfirmed)
    XCTAssertFalse(model.canPublish)
    XCTAssertEqual(posts, 0)
  }

  func testPublishPostsOnlyBookMetadataAndDuplicateResolvesOwnershipByRead() async throws {
    let api = api(), model = CommunitySharingModel(api: api, accountID: "owner", epoch: UUID(), storage: storage())
    var posts = 0, publicationReads = 0
    CommunitySharingProtocol.handler = { request in
      if request.httpMethod == "POST" {
        posts += 1
        XCTAssertEqual(request.url?.path, "/api/community/share")
        XCTAssertEqual(try self.body(request), ["kind": .string("book"), "book_id": .string("chemistry"),
          "description": .string("我的教材简介"), "rights_confirmed": .bool(true)])
        return try self.reply(CommunityShareReceipt(post_id: "already_post", status: "already_shared"))
      }
      if request.url!.path.hasSuffix("/publication") {
        publicationReads += 1
        return try self.reply(CommunityBookPublication(book_id: "chemistry", post_id: "already_post",
          publication_status: .sharedByOther, can_withdraw: false))
      }
      return try self.reply(self.candidates())
    }
    await model.load()
    model.description = " 我的教材简介 "
    model.rightsConfirmed = true
    await model.publish()
    XCTAssertEqual(posts, 1)
    XCTAssertEqual(publicationReads, 1)
    XCTAssertEqual(model.publicationStatus, .sharedByOther)
    XCTAssertEqual(model.publishedPostID, "already_post")
    XCTAssertFalse(model.canWithdraw)
    XCTAssertFalse(model.canPublish)
    XCTAssertEqual(model.publicationRevision, 1)
    XCTAssertFalse(model.submissionUncertain)
    await model.publish()
    XCTAssertEqual(posts, 1)
  }

  func testUnshareablePreferredBookNeverSilentlySelectsAnotherOwnedBook() async throws {
    let api = api(), model = CommunitySharingModel(api: api, accountID: "owner", epoch: UUID(),
      preferredBookID: "imported_other_book", storage: storage())
    var posts = 0
    CommunitySharingProtocol.handler = { request in
      if request.httpMethod == "POST" { posts += 1; throw URLError(.badServerResponse) }
      return try self.reply(self.candidates())
    }
    await model.load()
    XCTAssertEqual(model.candidates.count, 2)
    XCTAssertEqual(model.selectedBookID, "")
    XCTAssertNil(model.selectedCandidate)
    XCTAssertEqual(model.message, "这份资料不在可分享教材中，请另选自己上传的 PDF。")
    model.rightsConfirmed = true
    await model.publish()
    XCTAssertEqual(posts, 0)
    model.selectedBookID = "biology"
    XCTAssertEqual(model.selectedCandidate?.title, "生物")
    XCTAssertFalse(model.rightsConfirmed)
    XCTAssertNil(model.message)
  }

  func testTimedOutPublishNeverRetriesAndReopenedWindowOnlyReconcilesByGet() async throws {
    let api = api(), store = storage(), epoch = UUID()
    let model = CommunitySharingModel(api: api, accountID: "owner", epoch: epoch, storage: store)
    var posts = 0, published = false
    CommunitySharingProtocol.handler = { request in
      if request.httpMethod == "POST" { posts += 1; throw URLError(.networkConnectionLost) }
      if request.url!.path.hasSuffix("/publication") {
        return try self.reply(CommunityBookPublication(book_id: "chemistry", post_id: published ? "saved_post" : nil,
          publication_status: published ? .shared : .unshared, can_withdraw: published))
      }
      return try self.reply(self.candidates())
    }
    await model.load()
    model.rightsConfirmed = true
    await model.publish()
    XCTAssertEqual(posts, 1, "Tailnet transport must not replay a community publish POST")
    XCTAssertTrue(model.submissionUncertain)
    XCTAssertFalse(model.canPublish)
    await model.publish()
    let reopened = CommunitySharingModel(api: api, accountID: "owner", epoch: UUID(), storage: store)
    await reopened.load()
    XCTAssertTrue(reopened.submissionUncertain)
    await reopened.refreshStatus()
    XCTAssertFalse(reopened.submissionUncertain)
    reopened.rightsConfirmed = true
    XCTAssertTrue(reopened.canPublish, "An explicit successful read must permit a fresh user-confirmed attempt")
    XCTAssertEqual(posts, 1, "Checking an unshared result must not automatically retry the write")
    published = true
    await reopened.refreshStatus()
    XCTAssertFalse(reopened.submissionUncertain)
    XCTAssertEqual(reopened.publicationStatus, .shared)
    XCTAssertTrue(reopened.canWithdraw)
    XCTAssertEqual(reopened.publicationRevision, 1)
    XCTAssertEqual(posts, 1)
  }

  func testUnconfirmedWithdrawalCanBeExplicitlyRetriedAfterReadOnlyStatusCheck() async throws {
    let api = api(), model = CommunitySharingModel(api: api, accountID: "owner", epoch: UUID(), storage: storage())
    var posts = 0, accept = false
    CommunitySharingProtocol.handler = { request in
      if request.httpMethod == "POST" {
        posts += 1
        XCTAssertEqual(request.url?.path, "/api/community/own_post/withdraw")
        if !accept { throw URLError(.networkConnectionLost) }
        return try self.reply(CloudOK(ok: true))
      }
      if request.url!.path.hasSuffix("/publication") {
        return try self.reply(CommunityBookPublication(book_id: "chemistry", post_id: "own_post",
          publication_status: .shared, can_withdraw: true))
      }
      var values = self.candidates()
      values[0].post_id = "own_post"; values[0].publication_status = .shared; values[0].can_withdraw = true
      return try self.reply(values)
    }
    await model.load()
    await model.withdraw()
    XCTAssertTrue(model.submissionUncertain)
    XCTAssertFalse(model.canWithdraw)
    await model.refreshStatus()
    XCTAssertFalse(model.submissionUncertain)
    XCTAssertTrue(model.canWithdraw)
    XCTAssertEqual(posts, 1, "Read-only recovery must never retry withdrawal automatically")
    accept = true
    await model.withdraw()
    XCTAssertEqual(posts, 2)
    XCTAssertEqual(model.publicationStatus, .unshared)
  }

  func testPendingSubmissionIsIsolatedByServiceAndAccount() async throws {
    let api = api(), store = storage()
    CommunitySharingProtocol.handler = { request in
      if request.httpMethod == "POST" { throw URLError(.timedOut) }
      return try self.reply(self.candidates())
    }
    let model = CommunitySharingModel(api: api, accountID: "owner", epoch: UUID(), storage: store)
    await model.load(); model.rightsConfirmed = true; await model.publish()
    XCTAssertTrue(model.submissionUncertain)
    let otherService = CommunitySharingModel(api: self.api(), accountID: "owner", epoch: UUID(), storage: store)
    await otherService.load()
    XCTAssertFalse(otherService.submissionUncertain)
    await api.rememberAccount(account("another-owner"))
    let otherAccount = CommunitySharingModel(api: api, accountID: "another-owner", epoch: UUID(), storage: store)
    await otherAccount.load()
    XCTAssertFalse(otherAccount.submissionUncertain)
  }

  func testSubmissionStaysDisabledDuringWriteAndLateResponseCannotCrossIdentityEpoch() async throws {
    let api = api(), model = CommunitySharingModel(api: api, accountID: "owner", epoch: UUID(), storage: storage())
    let started = expectation(description: "explicit publish started")
    var posts = 0
    CommunitySharingProtocol.handler = { request in
      if request.httpMethod == "POST" {
        posts += 1; started.fulfill()
        return try self.reply(CommunityShareReceipt(post_id: "new_post", status: "shared"), delay: 0.15)
      }
      return try self.reply(self.candidates())
    }
    await model.load(); model.rightsConfirmed = true
    let task = Task { await model.publish() }
    await fulfillment(of: [started], timeout: 2)
    XCTAssertTrue(model.submitting)
    XCTAssertFalse(model.canPublish)
    await model.publish()
    model.invalidateIdentity()
    await task.value
    XCTAssertEqual(posts, 1)
    XCTAssertTrue(model.candidates.isEmpty)
    XCTAssertNil(model.publishedPostID)
    XCTAssertEqual(model.publicationRevision, 0)
  }

  func testConfirmedValidationRejectionDoesNotLockAndWithdrawRefreshesPublication() async throws {
    let api = api(), model = CommunitySharingModel(api: api, accountID: "owner", epoch: UUID(), storage: storage())
    var posts = 0, reject = true
    CommunitySharingProtocol.handler = { request in
      if request.httpMethod == "POST" {
        posts += 1
        if reject { return .init(status: 403, data: Data(#"{"detail":"无权分享这本教材"}"#.utf8)) }
        if request.url!.path.hasSuffix("/withdraw") { return try self.reply(CloudOK(ok: true)) }
        return try self.reply(CommunityShareReceipt(post_id: "own_post", status: "shared"))
      }
      if request.url!.path.hasSuffix("/publication") {
        return try self.reply(CommunityBookPublication(book_id: "chemistry", post_id: "own_post",
          publication_status: .shared, can_withdraw: true))
      }
      return try self.reply(self.candidates())
    }
    await model.load(); model.rightsConfirmed = true; await model.publish()
    XCTAssertFalse(model.submissionUncertain)
    XCTAssertEqual(model.error, "无权分享这本教材")
    XCTAssertTrue(model.canPublish)
    reject = false
    await model.publish()
    XCTAssertTrue(model.canWithdraw)
    await model.withdraw()
    XCTAssertEqual(posts, 3)
    XCTAssertEqual(model.publicationStatus, .unshared)
    XCTAssertNil(model.publishedPostID)
    XCTAssertFalse(model.canWithdraw)
    XCTAssertEqual(model.publicationRevision, 2)
  }

  func testServiceRejectsStaleAccountAndUnsafeIdentifiersBeforeRequests() async throws {
    let api = api()
    var requests = 0
    CommunitySharingProtocol.handler = { _ in requests += 1; throw URLError(.badServerResponse) }
    let wrong = CommunitySharingService(api: api, accountID: "wrong-owner")
    do { _ = try await wrong.candidates(); XCTFail("Stale account accepted") }
    catch let error as CloudError { XCTAssertEqual(error.status, 401) }
    let service = CommunitySharingService(api: api, accountID: "owner")
    do { _ = try await service.publish(bookID: "chemistry", description: "", rightsConfirmed: false)
      XCTFail("Rights confirmation required")
    } catch let error as CloudError { XCTAssertEqual(error.status, 422) }
    do { _ = try await service.publication(bookID: "../another"); XCTFail("Unsafe book ID accepted") }
    catch let error as CloudError { XCTAssertEqual(error.status, 422) }
    XCTAssertEqual(requests, 0)
  }
}
