import Foundation
import XCTest

@testable import BookCourse

private final class NoteNetworkLog: @unchecked Sendable {
  private let lock = NSLock()
  private var requests = 0
  func record() { lock.withLock { requests += 1 } }
  var count: Int { lock.withLock { requests } }
}

private final class NoteNoNetworkProtocol: URLProtocol, @unchecked Sendable {
  static let log = NoteNetworkLog()
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    Self.log.record()
    client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
  }
  override func stopLoading() {}
}

final class LocalNotePersistenceTests: XCTestCase {
  @MainActor func testNewTextNoteReopensExactlyAndStaysWithinAccountAndOrigin() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [NoteNoNetworkProtocol.self]
    let session = URLSession(configuration: configuration)
    defer { session.invalidateAndCancel() }
    let beforeRequests = NoteNoNetworkProtocol.log.count
    let host = "note-local-\(UUID().uuidString.lowercased()).example"
    let api = CloudAPI(baseURL: URL(string: "https://\(host)")!, session: session)
    let account = CloudAccountState(
      account: .init(email: "a@example.com", stage: "university", interests: [],
        goal: "interest", is_demo: false),
      user_id: "owner-a", email_available: true, demo_available: false)
    let repository = try LocalBookCourseRepository(directory: root)
    let store = AppStore(repository: repository, cloud: api)
    store.cloudState.authenticated = true
    store.cloudState.account = account
    store.cloudState.cacheKey = store.cloudCacheKey(account)
    let anchor = NoteAnchor(courseID: "my-course", bookID: "book_local_original",
      chapterID: "chapter-2", page: 17, printedPage: 12,
      quote: "我手动选中的原文位置\n第二行")
    let note = StudyNote(id: "same-local-note-id", kind: .text,
      title: "我的新建文字笔记", body: "第一行：α + β ≠ 0。\n\n保留空行、中文和🙂。",
      anchor: anchor, createdAt: Date(timeIntervalSince1970: 1_700_000_000),
      organizedText: "这是用户确认保存的整理文字。")
    try await store.saveNote(note)
    await store.flush()
    let saved = try XCTUnwrap(store.notes.first)
    XCTAssertEqual(saved.body, note.body)
    XCTAssertEqual(saved.anchor, anchor)
    XCTAssertEqual(saved.organizedText, note.organizedText)
    XCTAssertEqual(saved.createdAt, note.createdAt)

    let reopenedRepository = try LocalBookCourseRepository(directory: root)
    let reopened = AppStore(repository: reopenedRepository, cloud: api)
    let loaded = try await reopened.loadCloudCache(account)
    let cached = try XCTUnwrap(loaded)
    reopened.restoreCloudCache(cached)
    XCTAssertEqual(reopened.notes, [saved])
    // Configured-service notes live in the local account cache, not the
    // anonymous note table or a backend notes endpoint.
    let anonymous = try await reopenedRepository.loadNotes()
    XCTAssertTrue(anonymous.isEmpty)

    var otherAccount = account
    otherAccount.user_id = "owner-b"
    let other = AppStore(repository: try LocalBookCourseRepository(directory: root), cloud: api)
    XCTAssertTrue(other.cloudCacheKey(otherAccount) != store.cloudCacheKey(account))
    let absentAccount = try await other.loadCloudCache(otherAccount)
    XCTAssertNil(absentAccount)
    XCTAssertTrue(other.notes.isEmpty)
    other.cloudState.authenticated = true
    other.cloudState.account = otherAccount
    other.cloudState.cacheKey = other.cloudCacheKey(otherAccount)
    try await other.saveNote(StudyNote(id: note.id, kind: .text,
      title: "另一账号的同名记录", body: "不应覆盖账号A的正文"))
    await other.flush()

    let again = AppStore(repository: try LocalBookCourseRepository(directory: root), cloud: api)
    let loadedAgain = try await again.loadCloudCache(account)
    again.restoreCloudCache(try XCTUnwrap(loadedAgain))
    XCTAssertEqual(again.notes, [saved])
    let restoredOther = try await again.loadCloudCache(otherAccount)
    XCTAssertEqual(restoredOther?.notes?.first?.body, "不应覆盖账号A的正文")

    let otherAPI = CloudAPI(baseURL: URL(string: "https://other-\(host)")!, session: session)
    let otherOrigin = AppStore(repository: try LocalBookCourseRepository(directory: root), cloud: otherAPI)
    XCTAssertTrue(otherOrigin.cloudCacheKey(account) != store.cloudCacheKey(account))
    let absentOrigin = try await otherOrigin.loadCloudCache(account)
    XCTAssertNil(absentOrigin)
    XCTAssertTrue(otherOrigin.notes.isEmpty)
    XCTAssertEqual(NoteNoNetworkProtocol.log.count, beforeRequests)
  }
}
