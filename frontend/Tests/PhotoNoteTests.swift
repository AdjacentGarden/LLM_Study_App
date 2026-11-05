import CryptoKit
import UIKit
import XCTest
@testable import BookCourse

final class PhotoNoteTests: XCTestCase {
  private func folder() -> URL { FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString) }
  private func note(_ id: String, photo: Bool = false, course: String = "course-a") -> StudyNote {
    StudyNote(id: id, kind: photo ? .photo : .text, title: id, body: photo ? "" : "已有笔记",
      anchor: NoteAnchor(courseID: course, courseResourceID: course + ":book", bookID: "book", page: 2),
      photos: photo ? [NotePhoto(originalPath: "attachments/original.heic", preparedPath: "attachments/recognition.jpg",
        imageSHA256: String(repeating: "a", count: 64), recognizedText: "DNA只复制1次。[待确认]", uncertain: ["单位"])] : nil)
  }
  func testLegacyNotesDecodeWithoutPhotoFields() throws {
    let original = note("legacy")
    var json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as! [String: Any]
    json.removeValue(forKey: "photos")
    let restored = try JSONDecoder().decode(StudyNote.self, from: JSONSerialization.data(withJSONObject: json))
    XCTAssertNil(restored.photos)
    XCTAssertEqual(restored.body, "已有笔记")
  }
  @MainActor func testImagePreparationKeepsOrientationAndBoundsAndRejectsInvalidData() throws {
    let renderer = UIGraphicsImageRenderer(size: CGSize(width: 1500, height: 2500))
    let data = renderer.jpegData(withCompressionQuality: 0.95) { ctx in
      UIColor.white.setFill(); ctx.fill(CGRect(x: 0, y: 0, width: 1500, height: 2500))
    }
    let prepared = try PhotoNoteImage.prepare(data)
    let image = try XCTUnwrap(UIImage(data: prepared.jpeg))
    XCTAssertLessThanOrEqual(max(image.size.width, image.size.height), 4096)
    XCTAssertFalse(prepared.jpeg.isEmpty)
    XCTAssertThrowsError(try PhotoNoteImage.prepare(Data("invalid".utf8)))
  }
  func testPhotoAndFaithfulRawTextSurviveSaveAndRepositoryReopen() async throws {
    let path = folder(); defer { try? FileManager.default.removeItem(at: path) }
    let repository = try LocalBookCourseRepository(directory: path)
    let originalBytes = Data([1, 2, 3, 4])
    let originalPath = try await repository.saveAttachment(originalBytes, extension: "heic")
    var draft = note("draft", photo: true); draft.photos?[0].originalPath = originalPath
    let saved = try await repository.mutateNote(draft, scope: "owner", condition: .absent)
    let committed = try await repository.commitPhotoNote(draftID: draft.id, draftRevision: saved.revision,
      targetID: nil, targetRevision: nil, title: "减数分裂", transcript: "用户核对后的原文", organized: "独立整理版", scope: "owner")
    let reopened = try LocalBookCourseRepository(directory: path)
    let restored = try await reopened.loadStoredNote(draft.id, scope: "owner")
    XCTAssertEqual(restored, committed)
    XCTAssertEqual(restored?.note.photos?.first?.recognizedText, "DNA只复制1次。[待确认]")
    XCTAssertEqual(restored?.note.body, "用户核对后的原文")
    XCTAssertEqual(try Data(contentsOf: reopened.attachmentURL(originalPath)), originalBytes)
  }
  func testAppendCommitsOnceKeepsTargetOriginalAndDeletesTemporaryDraft() async throws {
    let path = folder(); defer { try? FileManager.default.removeItem(at: path) }
    let repository = try LocalBookCourseRepository(directory: path)
    let draft = try await repository.mutateNote(note("draft", photo: true), scope: "owner", condition: .absent)
    let target = try await repository.mutateNote(note("target"), scope: "owner", condition: .absent)
    let committed = try await repository.commitPhotoNote(draftID: "draft", draftRevision: draft.revision,
      targetID: "target", targetRevision: target.revision, title: "照片要点", transcript: "忠实原文", organized: "整理版", scope: "owner")
    XCTAssertTrue(committed.note.body.hasPrefix("已有笔记"))
    XCTAssertEqual(committed.note.kind, .text)
    XCTAssertEqual(committed.note.photos?.count, 1)
    let removed = try await repository.loadStoredNote("draft", scope: "owner")
    XCTAssertNil(removed)
    do {
      _ = try await repository.commitPhotoNote(draftID: "draft", draftRevision: draft.revision,
        targetID: "target", targetRevision: target.revision, title: "照片要点", transcript: "忠实原文", organized: "整理版", scope: "owner")
      XCTFail("A repeated append must not duplicate the photo or text")
    } catch {}
    let current = try await repository.loadStoredNote("target", scope: "owner")
    XCTAssertEqual(current, committed)
  }
  func testConcurrentTargetEditRetainsBothLatestNoteAndPhotoDraft() async throws {
    let path = folder(); defer { try? FileManager.default.removeItem(at: path) }
    let repository = try LocalBookCourseRepository(directory: path)
    let draft = try await repository.mutateNote(note("draft", photo: true), scope: "owner", condition: .absent)
    let target = try await repository.mutateNote(note("target"), scope: "owner", condition: .absent)
    var edited = target.note; edited.body = "生成期间的新修改"
    let newer = try await repository.mutateNote(edited, scope: "owner", condition: .revision(target.revision))
    do {
      _ = try await repository.commitPhotoNote(draftID: "draft", draftRevision: draft.revision,
        targetID: "target", targetRevision: target.revision, title: "照片要点", transcript: "原文", organized: "整理版", scope: "owner")
      XCTFail("A stale target must conflict")
    } catch {}
    let retained = try await repository.loadStoredNote("draft", scope: "owner")
    let current = try await repository.loadStoredNote("target", scope: "owner")
    XCTAssertEqual(retained, draft); XCTAssertEqual(current, newer)
  }
  func testAppendCannotCrossCourseOrAccountAndDeletedDraftCannotReturn() async throws {
    let path = folder(); defer { try? FileManager.default.removeItem(at: path) }
    let repository = try LocalBookCourseRepository(directory: path)
    let draft = try await repository.mutateNote(note("draft", photo: true), scope: "owner", condition: .absent)
    let other = try await repository.mutateNote(note("target", course: "course-b"), scope: "owner", condition: .absent)
    for scope in ["owner", "other-account"] {
      do {
        _ = try await repository.commitPhotoNote(draftID: "draft", draftRevision: draft.revision,
          targetID: "target", targetRevision: other.revision, title: "照片要点", transcript: "原文", organized: "整理版", scope: scope)
        XCTFail("Cross-scope writes must fail")
      } catch {}
    }
    try await repository.deleteNote("draft", scope: "owner")
    do {
      _ = try await repository.commitPhotoNote(draftID: "draft", draftRevision: draft.revision,
        targetID: nil, targetRevision: nil, title: "照片要点", transcript: "原文", organized: "整理版", scope: "owner")
      XCTFail("Deleted drafts must stay deleted")
    } catch {}
  }
  func testSharedPhotosAreReclaimedOnlyAfterLastOwningNoteIsDeleted() async throws {
    let path = folder(); defer { try? FileManager.default.removeItem(at: path) }
    let repository = try LocalBookCourseRepository(directory: path)
    let attachment = try await repository.saveAttachment(Data([1, 2, 3, 4, 5]), extension: "jpg")
    var first = note("first", photo: true); first.photos?[0].originalPath = attachment; first.photos?[0].preparedPath = attachment
    var second = note("second", photo: true, course: "course-b"); second.photos?[0].originalPath = attachment; second.photos?[0].preparedPath = attachment
    _ = try await repository.mutateNote(first, scope: "owner", condition: .absent)
    _ = try await repository.mutateNote(second, scope: "owner", condition: .absent)
    try await repository.deleteNote("first", scope: "owner")
    XCTAssertTrue(FileManager.default.fileExists(atPath: repository.attachmentURL(attachment).path))
    try await repository.deleteNote("second", scope: "owner")
    XCTAssertFalse(FileManager.default.fileExists(atPath: repository.attachmentURL(attachment).path))
  }

}

private final class PhotoNoteHTTPProtocol: URLProtocol {
  static var handler: ((URLRequest) throws -> (Int, [String: Any]))?
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      let (status, payload) = try Self.handler!(request)
      let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
        headerFields: ["Content-Type": "application/json"])!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: try JSONSerialization.data(withJSONObject: payload))
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
  static func body(_ request: URLRequest) -> Data {
    if let data = request.httpBody { return data }
    guard let stream = request.httpBodyStream else { return Data() }
    stream.open(); defer { stream.close() }
    var data = Data(), buffer = [UInt8](repeating: 0, count: 4096)
    while stream.hasBytesAvailable {
      let count = stream.read(&buffer, maxLength: buffer.count)
      guard count > 0 else { break }
      data.append(contentsOf: buffer.prefix(count))
    }
    return data
  }
}

extension PhotoNoteTests {
  private func photoService() -> (StudioService, NotePhoto, Data) {
    let bytes = Data("test-photo-transport-bytes".utf8)
    let photo = NotePhoto(originalPath: "original.jpg", preparedPath: "prepared.jpg",
      imageSHA256: SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined())
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [PhotoNoteHTTPProtocol.self]
    return (StudioService(api: CloudAPI(baseURL: URL(string: "http://127.0.0.1:19286")!,
      session: URLSession(configuration: configuration))), photo, bytes)
  }
  private var photoCapabilities: [String: Any] {
    ["image": false, "video": false, "voice_notes": false, "notes_ai": false,
      "draft_scope": "test-" + name, "photo_notes": true]
  }
  private func photoJob(_ request: String, _ photo: NotePhoto, status: String) -> [String: Any] {
    ["id": "job-test", "book_id": "book-test", "request_id": request, "kind": "photo_note", "status": status,
      "result": ["image_sha256": photo.imageSHA256, "transcript": "忠实原文", "polished": "整理笔记"],
      "error": "任务未完成"]
  }
  func testLostPhotoSubmissionResponseNeverBlindlyReposts() async throws {
    let (service, photo, bytes) = photoService()
    let caps = photoCapabilities
    var posts = 0
    PhotoNoteHTTPProtocol.handler = { request in
      if request.url!.path.hasSuffix("capabilities") { return (200, caps) }
      if request.httpMethod == "POST" { posts += 1; throw URLError(.networkConnectionLost) }
      return (200, ["items": []])
    }
    defer { PhotoNoteHTTPProtocol.handler = nil }
    for _ in 0..<2 {
      do { _ = try await service.generatePhoto(photo: photo, bookID: "book-test", jpeg: bytes, retryFailed: true); XCTFail("Lost responses must remain pending") }
      catch {}
    }
    XCTAssertEqual(posts, 1)
  }
  func testUncertainPhotoTaskRemainsQueryOnlyEvenWhenRetryRequested() async throws {
    let (service, photo, bytes) = photoService()
    let caps = photoCapabilities, job = photoJob(photo.requestID, photo, status: "uncertain")
    var posts = 0
    PhotoNoteHTTPProtocol.handler = { request in
      if request.url!.path.hasSuffix("capabilities") { return (200, caps) }
      if request.httpMethod == "POST" { posts += 1 }
      return (200, ["items": [job]])
    }
    defer { PhotoNoteHTTPProtocol.handler = nil }
    do { _ = try await service.generatePhoto(photo: photo, bookID: "book-test", jpeg: bytes, retryFailed: true); XCTFail("Uncertain tasks must not restart") }
    catch {}
    XCTAssertEqual(posts, 0)
  }
  func testConfirmedPhotoFailureRetriesOnceAndRecoversLatestReceipt() async throws {
    let (service, photo, bytes) = photoService()
    let caps = photoCapabilities
    var posts = 0, latest: [String: Any]?
    PhotoNoteHTTPProtocol.handler = { request in
      if request.url!.path.hasSuffix("capabilities") { return (200, caps) }
      if request.httpMethod == "POST" {
        posts += 1
        let body = try JSONSerialization.jsonObject(with: PhotoNoteHTTPProtocol.body(request)) as! [String: Any]
        XCTAssertNotEqual(body["request_id"] as? String, photo.requestID)
        latest = self.photoJob(body["request_id"] as! String, photo, status: "succeeded")
        return (200, latest!)
      }
      return (200, ["items": [latest ?? self.photoJob(photo.requestID, photo, status: "failed")]])
    }
    defer { PhotoNoteHTTPProtocol.handler = nil }
    let first = try await service.generatePhoto(photo: photo, bookID: "book-test", jpeg: bytes, retryFailed: true)
    let repeated = try await service.generatePhoto(photo: photo, bookID: "book-test", jpeg: bytes, retryFailed: true)
    XCTAssertEqual(first.id, repeated.id)
    XCTAssertEqual(posts, 1)
  }
}
