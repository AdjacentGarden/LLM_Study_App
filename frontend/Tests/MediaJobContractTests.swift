import Foundation
import XCTest

#if canImport(BookCourse)
  @testable import BookCourse
#endif

final class MediaJobContractTests: XCTestCase {
  private let canonicalID = String(repeating: "a", count: 32)

  private func job(
    id: String? = nil, bookID: String = "book-1", kind: String = "video",
    state: String = "polling", requestID: String? = "older_canonical_request"
  ) -> StudioJob {
    StudioJob(
      id: id ?? canonicalID, book_id: bookID, kind: kind, status: state, result: [:],
      error: nil, asset_url: nil, request_id: requestID)
  }

  private func intent() -> LessonMediaReceipt {
    LessonMediaReceipt(requestID: "media_new_alias_123", state: "submitting", submissionUncertain: true)
  }

  func testCoalescedOlderRequestIDBindsCanonicalJobAndPreservesNewAlias() throws {
    var receipt = intent()
    try receipt.accept(job(), bookID: "book-1", kind: "video")
    XCTAssertEqual(receipt.requestID, "media_new_alias_123")
    XCTAssertEqual(receipt.jobID, canonicalID)
    XCTAssertFalse(receipt.submissionUncertain)
    XCTAssertFalse(receipt.permitsNewGeneration)
    try receipt.accept(job(state: "succeeded"), bookID: "book-1", kind: "video")
    let reloaded = try JSONDecoder().decode(
      LessonMediaReceipt.self, from: JSONEncoder().encode(receipt))
    XCTAssertEqual(reloaded, receipt)
  }

  func testUnknownSubmissionAndProviderUncertaintyCannotStartAnotherGeneration() {
    XCTAssertFalse(intent().permitsNewGeneration)
    for state in ["queued", "planning", "submitting", "polling", "reviewing", "succeeded", "uncertain"] {
      let receipt = LessonMediaReceipt(
        requestID: "media_original_123", jobID: canonicalID, state: state, submissionUncertain: false)
      XCTAssertFalse(receipt.permitsNewGeneration, state)
    }
    for state in ["failed", "cancelled", "rejected"] {
      var receipt = LessonMediaReceipt(
        requestID: "media_original_123", jobID: canonicalID, state: state, submissionUncertain: false)
      XCTAssertTrue(receipt.permitsNewGeneration, state)
      receipt.submissionUncertain = true
      XCTAssertFalse(receipt.permitsNewGeneration, state)
    }
  }

  func testWrongBookKindOrUnknownStatusCannotReplaceReceipt() {
    for invalid in [
      job(bookID: "another-book"), job(kind: "image"), job(kind: "recognize"),
      job(state: "completed"), job(state: "needs_confirmation"), job(state: ""),
    ] {
      var receipt = intent()
      let before = receipt
      XCTAssertThrowsError(try receipt.accept(invalid, bookID: "book-1", kind: "video"))
      XCTAssertEqual(receipt, before)
    }
  }

  func testBoundJobAndSucceededStateCannotRegress() throws {
    var receipt = intent()
    try receipt.accept(job(state: "succeeded"), bookID: "book-1", kind: "video")
    let before = receipt
    XCTAssertThrowsError(try receipt.accept(job(state: "polling"), bookID: "book-1", kind: "video"))
    XCTAssertEqual(receipt, before)
    XCTAssertThrowsError(try receipt.accept(
      job(id: String(repeating: "b", count: 32), state: "succeeded"),
      bookID: "book-1", kind: "video"))
    XCTAssertEqual(receipt, before)
  }

  func testMalformedIdentifiersAreRejectedBeforeAnyFollowupRequest() {
    for identifier in ["", "../private", "https://other.example", String(repeating: "a", count: 32) + "\n",
                       String(repeating: "z", count: 32), String(repeating: "a", count: 31)] {
      var receipt = intent()
      XCTAssertThrowsError(try receipt.accept(job(id: identifier), bookID: "book-1", kind: "video"))
      XCTAssertNil(receipt.jobID)
    }
    for identifier in ["short", "request_123\n", "../request_123", "请求编号12345678"] {
      let receipt = LessonMediaReceipt(requestID: identifier, state: "submitting", submissionUncertain: true)
      XCTAssertThrowsError(try receipt.validate())
    }
    let unknown = LessonMediaReceipt(requestID: "request_valid_123", state: "unknown_state", submissionUncertain: true)
    XCTAssertThrowsError(try unknown.validate())
  }

  func testContextKeySeparatesBookLessonKindAndExcerpt() {
    func request(_ kind: String = "video", bookID: String = "book-1",
                 lessonID: String = "lesson-1", excerpt: String = "真实教材选段") -> LessonMediaRequest {
      LessonMediaRequest(
        kind: kind, bookID: bookID, chapterID: "chapter-1", chapterTitle: "第一章",
        title: "辅助讲解", excerpt: excerpt, pages: [1], lessonID: lessonID, courseID: "course-1")
    }
    let first = request()
    XCTAssertEqual(first.id, request().id)
    XCTAssertEqual(first.id.count, 64)
    XCTAssertNotEqual(first.id, request("image").id)
    XCTAssertNotEqual(first.id, request(bookID: "book-2").id)
    XCTAssertNotEqual(first.id, request(lessonID: "lesson-2").id)
    XCTAssertNotEqual(first.id, request(excerpt: "不同的教材选段").id)
  }
}
