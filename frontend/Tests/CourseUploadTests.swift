import Foundation
import XCTest
@testable import BookCourse

private final class CourseUploadProtocol: URLProtocol, @unchecked Sendable {
  struct Reply { var status = 200; var body: Data; var delay = 0.15 }
  private static let lock = NSLock()
  nonisolated(unsafe) private static var handlers: [String: (URLRequest) -> Reply] = [:]
  private var work: DispatchWorkItem?
  static func register(_ host: String, _ handler: @escaping (URLRequest) -> Reply) {
    lock.lock(); defer { lock.unlock() }; handlers[host] = handler
  }
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    Self.lock.lock(); let handler = Self.handlers[request.url!.host!]; Self.lock.unlock()
    let reply = handler!(request)
    let work = DispatchWorkItem { [weak self] in
      guard let self else { return }
      let response = HTTPURLResponse(url: self.request.url!, statusCode: reply.status,
        httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
      self.client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      self.client?.urlProtocol(self, didLoad: reply.body)
      self.client?.urlProtocolDidFinishLoading(self)
    }
    self.work = work
    DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay, execute: work)
  }
  override func stopLoading() { work?.cancel(); work = nil }
}

@MainActor private final class CourseUploadInitializationBarrier {
  var calls: [String: Int] = [:]
  private var continuations: [String: [CheckedContinuation<Void, Never>]] = [:]
  func wait(_ scope: String) async {
    calls[scope, default: 0] += 1
    await withCheckedContinuation { continuation in continuations[scope, default: []].append(continuation) }
  }
  func releaseAll() {
    let pending = continuations.values.flatMap { $0 }; continuations = [:]
    for continuation in pending { continuation.resume() }
  }
  func releaseNext(_ scope: String) {
    guard var pending = continuations[scope], !pending.isEmpty else { return }
    let next = pending.removeFirst(); continuations[scope] = pending; next.resume()
  }
}

@MainActor final class CourseUploadTests: XCTestCase {
  private func makeStore(reply: @escaping (URLRequest) -> CourseUploadProtocol.Reply) throws -> AppStore {
    let host = "upload-" + UUID().uuidString.lowercased() + ".example"
    CourseUploadProtocol.register(host) { request in
      if request.url?.path == "/api/native/local-first/capabilities" {
        return .init(body: Data(#"{"schema_version":1,"public_library":true,"private_forwarding":true,"private_export":true,"legacy_writes_active":true}"#.utf8))
      }
      if request.url?.path == "/api/public/library/course-groups" {
        return .init(body: Data(#"{"id":"group_upload","title":"测试课程","description":"","book_ids":[],"status":"pending","manifest_url":""}"#.utf8))
      }
      // Transfer tests intentionally model an unavailable draft-sync service.
      // A draft-save failure must not turn an accepted upload into a failed upload.
      if request.url?.path.contains("/creation-draft") == true {
        return .init(status: 503, body: Data("{\"detail\":\"Draft sync unavailable in transfer fixture\"}".utf8))
      }
      return reply(request)
    }
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [CourseUploadProtocol.self]
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString), includeFixtures: false)
    let policy = ProcessingNetworkPolicy(monitoring: false, wifiStability: 0, cellularDebounce: 0)
    policy.updatePath(.init(kind: .wifi))
    let store = AppStore(repository: repo,
      cloud: CloudAPI(baseURL: URL(string: "https://\(host)")!, session: URLSession(configuration: config)),
      processingPolicy: policy)
    store.isLoading = false
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = host
    store.restoreCourseUploads()
    return store
  }
  private func pdf(_ store: AppStore, name: String, contents: String = "original") throws -> URL {
    let url = store.repository.directory.appendingPathComponent(name)
    try Data(("%PDF-1.4\n" + contents).utf8).write(to: url)
    return url
  }
  private func wait(_ message: String, _ condition: () -> Bool) async throws {
    let deadline = ContinuousClock.now.advanced(by: .seconds(8))
    while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(20)) }
    XCTAssertTrue(condition(), message)
  }
  private func success(_ request: URLRequest, delay: Double = 0.15) -> CourseUploadProtocol.Reply {
    let id = request.value(forHTTPHeaderField: "X-CloudPath-Upload-ID") ?? "test"
    return .init(body: Data("{\"book_id\":\"server-\(id)\",\"filename\":\"original.pdf\",\"status\":\"uploaded\"}".utf8), delay: delay)
  }

  private func countingStore(_ counts: UploadRequestCounts, delay: Double = 0.05) throws -> AppStore {
    try makeStore { request in
      guard request.url?.path == "/api/native/uploads" else {
        return .init(status: 404, body: Data("{}".utf8), delay: 0)
      }
      let count = counts.increment()
      return .init(body: Data("{\"book_id\":\"server-\(count)\",\"filename\":\"book.pdf\",\"status\":\"uploaded\"}".utf8), delay: delay)
    }
  }

  func testRenamedDuplicateReopensExistingCourseWithoutUploadingOrAddingPreparation() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    store.draft.name = "正在学习的数学"
    let first = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "math.pdf")]))
    try await wait("Initial material should finish uploading") { store.uploadState.batches.first?.isComplete == true }
    let existingID = store.uploadState.batches[0].courseID
    let oldBook = try XCTUnwrap(store.courses.first?.resourceIDs.first)
    store.acknowledgeCourseUpload(first)
    store.importState.courses[0].job.status = .completed
    store.importState.courses[0].setupCompleted = true
    store.importState.courses[0].books[0].status = .completed
    let note = StudyNote(kind: .text, title: "保留批注", anchor: NoteAnchor(courseID: existingID, bookID: oldBook, page: 3))
    store.notes = [note]
    store.locations[existingID + "::" + oldBook] = StudyLocation(page: 7)
    let duplicate = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "renamed.pdf")]))
    try await wait("Same bytes must reuse the existing course") {
      store.uploadState.batches.first { $0.id == duplicate }?.reusedCourseID == existingID
    }
    XCTAssertEqual(counts.value, 1)
    XCTAssertEqual(store.courses.count, 1)
    XCTAssertEqual(store.selectedCourseID, existingID)
    XCTAssertEqual(store.importState.courses.count, 1)
    XCTAssertEqual(store.importState.courses[0].job.status, .completed)
    XCTAssertEqual(store.notes, [note])
    XCTAssertEqual(store.locations[existingID + "::" + oldBook]?.page, 7)
    XCTAssertEqual(store.toast?.title, "这本教材已在「正在学习的数学」中，已为你打开")
    XCTAssertTrue(try XCTUnwrap(store.uploadState.batches.first { $0.id == duplicate }).completionAcknowledged)
    store.suspendCourseUploads()
    store.restoreCourseUploads()
    XCTAssertEqual(store.courses.count, 1, "A restored duplicate receipt must not resurrect its placeholder")
    XCTAssertEqual(counts.value, 1)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testSameBatchDuplicatesUploadOnlyUniqueBytesAndKeepNewMaterials() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    let id = try XCTUnwrap(store.beginCourseUpload([
      pdf(store, name: "one.pdf", contents: "same"),
      pdf(store, name: "renamed.pdf", contents: "same"),
      pdf(store, name: "new.pdf", contents: "different"),
    ]))
    try await wait("Two distinct materials should finish") { store.uploadState.batches.first?.isComplete == true }
    let batch = try XCTUnwrap(store.uploadState.batches.first { $0.id == id })
    XCTAssertEqual(counts.value, 2)
    XCTAssertEqual(store.courses.count, 1)
    XCTAssertEqual(batch.files.filter(\.isSkippedDuplicate).count, 1)
    XCTAssertEqual(Set(batch.files.compactMap(\.bookID)).count, 2)
    XCTAssertEqual(store.courses[0].resourceIDs.count, 2)
    XCTAssertEqual(store.importState.courses[0].books.count, 2)
    XCTAssertEqual(batch.draft.resourceIDs.count, 2)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testMixedExistingAndNewMaterialsKeepOldCourseLearningInPlace() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    _ = store.beginCourseUpload([try pdf(store, name: "old.pdf", contents: "old")])
    try await wait("Old material should be ready") { store.uploadState.batches.first?.isComplete == true }
    let oldCourse = try XCTUnwrap(store.courses.first)
    let oldBook = try XCTUnwrap(oldCourse.resourceIDs.first)
    let note = StudyNote(kind: .text, title: "旧课笔记", anchor: NoteAnchor(courseID: oldCourse.id, bookID: oldBook, page: 2))
    store.notes = [note]
    let id = try XCTUnwrap(store.beginCourseUpload([
      pdf(store, name: "copy.pdf", contents: "old"), pdf(store, name: "fresh.pdf", contents: "fresh"),
    ]))
    try await wait("Only the new material should upload") { store.uploadState.batches.first { $0.id == id }?.isComplete == true }
    let batch = try XCTUnwrap(store.uploadState.batches.first { $0.id == id })
    let newCourse = try XCTUnwrap(store.courses.first { $0.id == batch.courseID })
    XCTAssertEqual(counts.value, 2)
    XCTAssertEqual(store.courses.count, 2)
    XCTAssertEqual(newCourse.resourceIDs.count, 1)
    XCTAssertFalse(newCourse.resourceIDs.contains(oldBook))
    XCTAssertEqual(store.courseImport(for: newCourse.id)?.books.count, 1)
    XCTAssertEqual(store.courses.first { $0.id == oldCourse.id }?.resourceIDs, [oldBook])
    XCTAssertEqual(store.notes, [note])
    XCTAssertEqual(batch.files.filter(\.isSkippedDuplicate).count, 1)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testAddingAnExistingMaterialDoesNotChangeItsPreparationOrUploadAgain() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    _ = store.beginCourseUpload([try pdf(store, name: "existing.pdf")])
    try await wait("Initial upload should finish") { store.uploadState.batches.first?.isComplete == true }
    let course = try XCTUnwrap(store.courses.first)
    store.importState.courses[0].job.status = .failed
    store.importState.courses[0].job.message = "保留原进度"
    let id = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "same.pdf")], targetCourseID: course.id))
    try await wait("Repeated attachment should be skipped") { store.uploadState.batches.first { $0.id == id }?.isComplete == true }
    XCTAssertEqual(counts.value, 1)
    XCTAssertEqual(store.courses.count, 1)
    XCTAssertEqual(store.courses[0].resourceIDs.count, 1)
    XCTAssertEqual(store.importState.courses[0].job.status, .failed)
    XCTAssertEqual(store.importState.courses[0].job.message, "保留原进度")
    XCTAssertEqual(store.toast?.title, "教材已在「\(course.name)」中，无需重复导入")
    store.suspendCourseUploads()
    await store.flush()
  }

  func testDifferentBytesWithTheSameFilenameRemainDistinctAndKeepOriginals() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    let original = try pdf(store, name: "same-name.pdf", contents: "first-edition")
    let originalBytes = try Data(contentsOf: original)
    _ = store.beginCourseUpload([original])
    try await wait("First edition should upload") { store.uploadState.batches.first?.isComplete == true }
    let firstPath = try XCTUnwrap(store.uploadState.batches[0].files[0].relativePath)
    let second = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "same-name.pdf", contents: "second-edition")]))
    try await wait("Changed bytes should upload separately") { store.uploadState.batches.first { $0.id == second }?.isComplete == true }
    XCTAssertEqual(counts.value, 2)
    XCTAssertEqual(store.courses.count, 2)
    XCTAssertNotEqual(store.uploadState.batches[0].files[0].bookID, store.uploadState.batches[1].files[0].bookID)
    XCTAssertEqual(try Data(contentsOf: store.repository.attachmentURL(firstPath)), originalBytes)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testConcurrentDuplicateImportsShareOneUploadAndOneCourse() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts, delay: 0.3)
    let original = try pdf(store, name: "concurrent.pdf")
    let first = try XCTUnwrap(store.beginCourseUpload([original]))
    let second = try XCTUnwrap(store.beginCourseUpload([original]))
    try await wait("Overlapping selections should share one transfer") {
      store.uploadState.batches.count == 2 && store.uploadState.batches.allSatisfy(\.isComplete)
    }
    let batches = store.uploadState.batches
    XCTAssertEqual(counts.value, 1)
    XCTAssertEqual(store.courses.count, 1)
    XCTAssertEqual(Set(batches.map(\.courseID)).count, 1)
    XCTAssertEqual(Set(batches.flatMap { $0.files.compactMap(\.bookID) }).count, 1)
    XCTAssertEqual(Set(batches.map(\.id)), Set([first, second]))
    XCTAssertEqual(batches.filter { $0.reusedCourseID != nil }.count, 1)
    XCTAssertEqual(store.importState.courses.count, 1)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testFreshRepositoryRecognizesDuplicateFromDurableIdentityWithoutWarmup() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    _ = store.beginCourseUpload([try pdf(store, name: "before-restart.pdf")])
    try await wait("First import should complete") { store.uploadState.batches.first?.isComplete == true }
    await store.flush()
    let repository = try LocalBookCourseRepository(directory: store.repository.directory, includeFixtures: false)
    let restarted = AppStore(repository: repository, cloud: store.cloud, processingPolicy: store.processingPolicy)
    restarted.isLoading = false
    restarted.cloudState.authenticated = true
    restarted.cloudState.cacheKey = store.cloudState.cacheKey
    // Model the hydrated account snapshot, with a new repository actor and no
    // in-memory hash claims or historical-file warmup.
    restarted.courses = store.courses
    restarted.sources = store.sources
    restarted.filePaths = store.filePaths
    restarted.importState.courses = store.importState.courses
    store.suspendCourseUploads()
    restarted.restoreCourseUploads()
    let id = try XCTUnwrap(restarted.beginCourseUpload([pdf(restarted, name: "after-restart.pdf")]))
    try await wait("Durable full-content identity should match after restart") {
      restarted.uploadState.batches.first { $0.id == id }?.reusedCourseID != nil
    }
    XCTAssertEqual(counts.value, 1)
    XCTAssertEqual(restarted.courses.count, 1)
    XCTAssertEqual(restarted.sources.count, 1)
    restarted.suspendCourseUploads()
    await restarted.flush()
  }

  func testAccountSwitchRetiresInFlightClaimAndCannotReuseAnotherAccountsMaterial() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts, delay: 0.4)
    let original = try pdf(store, name: "account.pdf")
    _ = store.beginCourseUpload([original])
    try await wait("First account should begin uploading") { counts.value == 1 }
    store.cloudState.epoch = UUID()
    store.suspendCourseUploads()
    store.courses = []; store.sources = []; store.filePaths = [:]; store.importState.courses = []
    store.cloudState.cacheKey = "second-account"
    store.restoreCourseUploads()
    let second = try XCTUnwrap(store.beginCourseUpload([original]))
    try await wait("Second account needs its own transfer") { store.uploadState.batches.first { $0.id == second }?.isComplete == true }
    XCTAssertEqual(counts.value, 2)
    XCTAssertEqual(store.courses.count, 1)
    XCTAssertEqual(store.sources.map(\.id), ["server-2"])
    XCTAssertNil(store.uploadState.batches[0].reusedCourseID)
    XCTAssertEqual(store.uploadState.batches[0].scope, "second-account")
    store.suspendCourseUploads()
    await store.flush()
  }

  func testDuplicateChoosesCourseWithLearningAcrossAllFullHashBookAliases() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    let original = try pdf(store, name: "known.pdf")
    let staged = try await LocalFileImporter.stage(url: original, repository: store.repository)
    let hash = try XCTUnwrap(staged.contentHash)
    var emptySource = staged.source
    emptySource.summary.book_id = "server-empty"
    emptySource.plan.book_id = emptySource.id
    var learnedSource = staged.source
    learnedSource.summary.book_id = "server-learned"
    learnedSource.plan.book_id = learnedSource.id
    store.sources = [emptySource, learnedSource]
    store.filePaths = [emptySource.id: staged.relativePath, learnedSource.id: staged.relativePath]
    store.courses = [
      Course(id: "local-course:empty", name: "未完成的同教材课", resourceIDs: [emptySource.id], createdAt: .distantPast),
      Course(id: "learned-course", name: "已有学习记录", resourceIDs: [learnedSource.id]),
    ]
    let note = StudyNote(kind: .text, title: "已有学习笔记", anchor: NoteAnchor(courseID: "learned-course", bookID: learnedSource.id, page: 8))
    store.notes = [note]
    // The most recently stored identity points to the empty draft. The course
    // choice must still inspect every full-hash match and preserve learning.
    for bookID in [learnedSource.id, emptySource.id] {
      try await store.repository.recordContentIdentity(contentHash: hash, bookID: bookID,
        relativePath: staged.relativePath, scope: store.courseUploadScope)
    }
    let id = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "another-name.pdf")]))
    try await wait("A matching alias with learning should win over an older empty course") {
      store.uploadState.batches.first { $0.id == id }?.reusedCourseID == "learned-course"
    }
    XCTAssertEqual(counts.value, 0)
    XCTAssertEqual(store.courses.count, 2, "An import may remove only its own temporary course")
    XCTAssertEqual(store.selectedCourseID, "learned-course")
    XCTAssertEqual(store.uploadState.batches.first { $0.id == id }?.files.first?.bookID, learnedSource.id)
    XCTAssertEqual(store.notes, [note])
    store.suspendCourseUploads()
    await store.flush()
  }

  func testRestoreFinishesDuplicateRedirectIfProcessStoppedBeforeRemovingPlaceholder() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    let staged = try await LocalFileImporter.stage(url: pdf(store, name: "restored.pdf"), repository: store.repository)
    let existing = Course(id: "existing-course", name: "已有课程", resourceIDs: [staged.source.id])
    let placeholder = Course(id: "local-course:upload:interrupted", name: "重复课程", resourceIDs: [])
    store.courses = [existing, placeholder]
    store.sources = [staged.source]
    store.filePaths = [staged.source.id: staged.relativePath]
    let file = CourseUploadFile(filename: "restored.pdf", relativePath: staged.relativePath,
      bookID: staged.source.id, contentHash: staged.contentHash, skippedDuplicate: true, status: .completed)
    let batch = CourseUploadBatch(courseID: placeholder.id, courseName: placeholder.name, scope: store.courseUploadScope,
      draft: CourseDraft(importCourseID: placeholder.id), addingMaterials: false, files: [file])
    store.uploadState.batches = [batch]
    XCTAssertTrue(store.persistCourseUploads())
    store.suspendCourseUploads()
    store.restoreCourseUploads()
    try await store.uploadState.restorationTask?.value
    XCTAssertEqual(store.uploadState.restoredScope, store.courseUploadScope)
    XCTAssertEqual(store.uploadState.restoredEpoch, store.cloudState.epoch)
    XCTAssertEqual(store.courses.map(\.id), [existing.id])
    XCTAssertEqual(store.uploadState.batches.first?.reusedCourseID, existing.id)
    XCTAssertEqual(counts.value, 0)
    XCTAssertTrue(store.importState.courses.isEmpty)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testArchivedDuplicateUploadLedgerCannotRecreateOrUploadItsOldCourse() async throws {
    let counts = UploadRequestCounts()
    let store = try countingStore(counts)
    let staged = try await LocalFileImporter.stage(url: pdf(store, name: "archived.pdf"), repository: store.repository)
    let existing = Course(id: "retained-course", name: "保留的课程", resourceIDs: [staged.source.id])
    store.courses = [existing]
    store.sources = [staged.source]
    store.filePaths = [staged.source.id: staged.relativePath]
    store.cloudState.archivedDuplicateCourseAliases = ["local-course:old-duplicate": existing.id]
    let file = CourseUploadFile(filename: "archived.pdf", relativePath: staged.relativePath,
      bookID: staged.source.id, status: .queued)
    store.uploadState.batches = [CourseUploadBatch(courseID: "local-course:old-duplicate", courseName: "旧重复课程",
      scope: store.courseUploadScope, draft: CourseDraft(), addingMaterials: false, files: [file])]
    XCTAssertTrue(store.persistCourseUploads())
    store.suspendCourseUploads()
    store.restoreCourseUploads()
    try await store.uploadState.restorationTask?.value
    XCTAssertEqual(store.uploadState.restoredScope, store.courseUploadScope)
    XCTAssertEqual(store.uploadState.restoredEpoch, store.cloudState.epoch)
    XCTAssertEqual(store.courses.map(\.id), [existing.id])
    XCTAssertEqual(store.uploadState.batches.first?.courseID, existing.id)
    XCTAssertEqual(store.uploadState.batches.first?.reusedCourseID, existing.id)
    XCTAssertTrue(store.uploadState.batches.first?.isComplete == true)
    XCTAssertTrue(store.uploadState.tasks.isEmpty)
    XCTAssertEqual(counts.value, 0)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testBackgroundCompletionDoesNotOverwriteTheSelectedCourseOrAnotherDraft() async throws {
    let store = try makeStore { request in
      let id = request.value(forHTTPHeaderField: "X-CloudPath-Upload-ID")!
      return .init(body: Data("{\"book_id\":\"server-\(id)\",\"filename\":\"one.pdf\",\"status\":\"uploaded\"}".utf8), delay: 0.4)
    }
    store.courses = [Course(id: "local-course:other", name: "正在学习", resourceIDs: [])]
    store.selectedCourseID = "local-course:other"
    store.draft = CourseDraft(name: "上传课程")
    let id = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "one.pdf")]))
    let owner = try XCTUnwrap(store.uploadState.batches.first?.courseID)
    XCTAssertEqual(store.courseCreationLabel(try XCTUnwrap(store.courses.first { $0.id == owner })), "上传中")
    store.backgroundCourseUpload(id)
    store.openCourse(try XCTUnwrap(store.courses.first { $0.id == owner }))
    XCTAssertEqual(store.uploadState.presentedID, id, "Course switching must reopen the exact upload batch")
    store.backgroundCourseUpload(id)
    store.selectCourse("local-course:other")
    let otherDraft = CourseDraft(name: "另一个课程的问卷", diagnosis: ["goals": ["exam"]], step: 3)
    store.draft = otherDraft
    try await wait("The server-confirmed upload should complete") { store.uploadState.batches.first?.isComplete == true }
    XCTAssertEqual(store.selectedCourseID, "local-course:other")
    XCTAssertEqual(store.draft, otherDraft)
    XCTAssertEqual(store.route, .study)
    XCTAssertTrue(try XCTUnwrap(store.courses.first { $0.id == owner }).resourceIDs.first!.hasPrefix("server-"))
    XCTAssertEqual(store.courses.first { $0.id == "local-course:other" }?.resourceIDs, [])
    XCTAssertFalse(store.uploadState.batches[0].completionAcknowledged)
    store.continueCourseUpload(id)
    XCTAssertEqual(store.selectedCourseID, owner)
    XCTAssertEqual(store.route, .processing)
    XCTAssertEqual(store.draft.name, "上传课程")
    XCTAssertTrue(store.uploadState.batches[0].completionAcknowledged)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testPartialFailureRetryKeepsCompletedFilesAndTheirServerIdentities() async throws {
    let counts = UploadRequestCounts()
    let store = try makeStore { request in
      let count = counts.increment()
      if count == 2 { return .init(status: 503, body: Data("{\"detail\":\"请重试\"}".utf8)) }
      return .init(body: Data("{\"book_id\":\"server-\(count)\",\"filename\":\"book.pdf\",\"status\":\"uploaded\"}".utf8))
    }
    let id = try XCTUnwrap(store.beginCourseUpload([
      pdf(store, name: "one.pdf", contents: "first"), pdf(store, name: "two.pdf", contents: "second")]))
    try await wait("Both upload attempts should finish") { store.uploadState.batches.first?.isRunning == false }
    let completed = try XCTUnwrap(store.uploadState.batches[0].files.first { $0.status == .completed })
    XCTAssertTrue(store.uploadState.batches[0].hasFailures)
    XCTAssertFalse(store.uploadState.batches[0].isComplete)
    XCTAssertEqual(counts.value, 2)
    store.retryCourseUpload(id)
    try await wait("Only the failed file should be retried") { store.uploadState.batches[0].isComplete }
    XCTAssertEqual(counts.value, 3)
    XCTAssertEqual(store.uploadState.batches[0].files.first { $0.id == completed.id }?.bookID, completed.bookID)
    XCTAssertEqual(store.uploadState.batches[0].files.first { $0.id == completed.id }?.requestID, completed.requestID)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testCancellationKeepsTheOriginalAndNeverMarksUnconfirmedBytesComplete() async throws {
    let store = try makeStore { _ in .init(body: Data("{}".utf8), delay: 3) }
    let original = try pdf(store, name: "cancel.pdf")
    let id = try XCTUnwrap(store.beginCourseUpload([original]))
    try await wait("The original must be staged before cancellation") {
      store.uploadState.batches[0].files[0].status == .uploading
    }
    let path = try XCTUnwrap(store.uploadState.batches[0].files[0].relativePath)
    store.cancelCourseUpload(id)
    try await Task.sleep(for: .milliseconds(200))
    XCTAssertEqual(store.uploadState.batches[0].files[0].status, .cancelled)
    XCTAssertFalse(store.uploadState.batches[0].isComplete)
    XCTAssertEqual(try Data(contentsOf: store.repository.attachmentURL(path)), try Data(contentsOf: original))
    store.suspendCourseUploads()
    await store.flush()
  }

  func testCompletedBatchRestoresWithoutUploadingAgainAndRemainsAccountScoped() async throws {
    let counts = UploadRequestCounts()
    let store = try makeStore { _ in
      _ = counts.increment()
      return .init(body: Data("{\"book_id\":\"server-one\",\"filename\":\"one.pdf\",\"status\":\"uploaded\"}".utf8))
    }
    let id = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "one.pdf")]))
    try await wait("Upload should finish") { store.uploadState.batches[0].isComplete }
    store.suspendCourseUploads()
    store.restoreCourseUploads()
    XCTAssertEqual(store.uploadState.batches.first?.id, id)
    XCTAssertTrue(store.uploadState.batches[0].isComplete)
    XCTAssertEqual(counts.value, 1)
    store.cloudState.cacheKey = "another-account"
    store.restoreCourseUploads()
    XCTAssertTrue(store.uploadState.batches.isEmpty)
    store.suspendCourseUploads()
    await store.flush()
  }

  func testStallHintIsOneBriefToastPerStallInsteadOfPermanentProgressCopy() throws {
    let store = try makeStore { _ in .init(body: Data()) }
    let now = Date.now
    let file = CourseUploadFile(filename: "slow.pdf", totalBytes: 100,
      status: .uploading, lastProgressAt: now.addingTimeInterval(-16))
    let batch = CourseUploadBatch(courseID: "course", courseName: "课程", scope: store.courseUploadScope,
      draft: CourseDraft(), addingMaterials: false, files: [file])
    store.uploadState.batches = [batch]
    store.checkCourseUploadStalls(at: now)
    let toastID = store.toast?.id
    XCTAssertEqual(store.toast?.title, "上传较慢，请保持网络连接")
    store.checkCourseUploadStalls(at: now.addingTimeInterval(20))
    XCTAssertEqual(store.toast?.id, toastID, "A stalled upload should not refresh the toast every second")
    store.suspendCourseUploads()
  }

  func testNativeBackgroundTransferReceiptPreventsASecondHTTPUpload() async throws {
    guard let base = ProcessInfo.processInfo.environment["BOOKCOURSE_UPLOAD_TEST_SERVER"], let url = URL(string: base) else {
      throw XCTSkip("Set BOOKCOURSE_UPLOAD_TEST_SERVER for the native URLSession background integration test")
    }
    let api = CloudAPI(baseURL: url)
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let file = root.appendingPathComponent("background.pdf")
    try Data(("%PDF-1.4\n" + String(repeating: "upload-bytes", count: 10000)).utf8).write(to: file)
    let identity = BackgroundUploadIdentity(id: UUID(), scope: UUID().uuidString)
    let first = try await api.uploadPDF(file, filename: "background.pdf", background: identity)
    let second = try await api.uploadPDF(file, filename: "background.pdf", background: identity)
    XCTAssertEqual(first.book_id, second.book_id)
    let counts: [String: Int] = try await api.request("/api/upload-test/counts")
    XCTAssertEqual(counts[identity.id.uuidString], 1)
    BackgroundUploadTransport.client(origin: url, scope: identity.scope).acknowledge(identity.id)
  }

  func testUploadWaitsForSingleFlightDeletionInitialization() async throws {
    let counts = UploadRequestCounts(), store = try countingStore(counts)
    let scope = try XCTUnwrap(store.noteScope), barrier = CourseUploadInitializationBarrier()
    defer { barrier.releaseAll() }
    store.courseDeletionState.initializationBarrierForTesting = { await barrier.wait($0) }
    let original = try pdf(store, name: "held-initialization.pdf")
    let id = try XCTUnwrap(store.beginCourseUpload([original]))
    let joined = Task { try await store.ensureCourseDeletionState(scope: scope) }
    store.restoreCourseUploads(); store.restoreCourseUploads()
    try await wait("One initialization must serve both upload and learning callers") { barrier.calls[scope] == 1 }
    XCTAssertEqual(counts.value, 0)
    XCTAssertNil(store.uploadState.restoredScope)
    XCTAssertEqual(store.uploadState.loadedScope, scope)
    barrier.releaseNext(scope)
    try await joined.value
    try await wait("Queued import must start after the durable scope gate is ready") {
      store.uploadState.batches.first { $0.id == id }?.isComplete == true
    }
    XCTAssertEqual(counts.value, 1)
    XCTAssertEqual(barrier.calls[scope], 1)
    try await store.ensureCourseDeletionState(scope: scope)
    XCTAssertEqual(barrier.calls[scope], 1, "Ready checks must use the cached result")
    store.suspendCourseUploads(); await store.flush()
  }

  func testDeletionInitializationFailureBlocksUploadAndCanRetry() async throws {
    let counts = UploadRequestCounts(), store = try countingStore(counts)
    let scope = try XCTUnwrap(store.noteScope)
    var attempts = 0
    store.courseDeletionState.initializationBarrierForTesting = { _ in
      attempts += 1
      if attempts == 1 { throw ContentError.storage("injected deletion ledger read failure") }
    }
    let original = try pdf(store, name: "retry-initialization.pdf")
    let first = try XCTUnwrap(store.beginCourseUpload([original]))
    try await wait("Failed initialization must surface without sending the file") {
      store.uploadState.restorationError != nil && store.uploadState.tasks[first] == nil
    }
    XCTAssertEqual(counts.value, 0)
    XCTAssertNil(store.courseDeletionState.readyEpoch)
    XCTAssertNil(store.uploadState.restoredScope)
    store.restoreCourseUploads()
    try await wait("A failed account initialization must be retryable") { store.uploadState.restoredScope == scope }
    let retried = try XCTUnwrap(store.beginCourseUpload([original]))
    try await wait("Reselected file can transfer after recovery") {
      store.uploadState.batches.first { $0.id == retried }?.isComplete == true
    }
    XCTAssertEqual(counts.value, 1)
    XCTAssertEqual(attempts, 2)
    store.suspendCourseUploads(); await store.flush()
  }

  func testHeldInitializationAccountCycleCannotOverwriteSuccessor() async throws {
    let counts = UploadRequestCounts(), store = try countingStore(counts)
    let firstScope = try XCTUnwrap(store.noteScope), barrier = CourseUploadInitializationBarrier()
    defer { barrier.releaseAll() }
    store.courseDeletionState.initializationBarrierForTesting = { scope in
      if scope == firstScope { await barrier.wait(scope) }
    }
    store.restoreCourseUploads()
    try await wait("First account initialization should be held") { barrier.calls[firstScope] == 1 }
    store.cloudState.epoch = UUID(); store.suspendCourseUploads()
    store.courses = []; store.sources = []; store.filePaths = [:]; store.importState.courses = []
    store.cloudState.cacheKey = "held-second-account"
    store.restoreCourseUploads()
    try await wait("Second account must initialize independently") { store.uploadState.restoredScope == "held-second-account" }
    store.cloudState.epoch = UUID(); store.suspendCourseUploads()
    store.courses = []; store.sources = []; store.filePaths = [:]; store.importState.courses = []
    store.cloudState.cacheKey = firstScope
    store.restoreCourseUploads()
    try await wait("Returning account needs a new epoch initialization") { barrier.calls[firstScope] == 2 }
    let successorToken = store.courseDeletionState.initializationToken
    barrier.releaseNext(firstScope) // Retired first-epoch operation arrives late.
    for _ in 0..<8 { await Task.yield() }
    XCTAssertEqual(store.courseDeletionState.initializationToken, successorToken)
    XCTAssertNotNil(store.courseDeletionState.initializationTask)
    XCTAssertNil(store.courseDeletionState.readyEpoch)
    XCTAssertNil(store.uploadState.restoredScope)
    XCTAssertNil(store.uploadState.restorationError)
    barrier.releaseNext(firstScope)
    try await wait("Successor should become ready after its own barrier") { store.uploadState.restoredScope == firstScope }
    XCTAssertEqual(store.courseDeletionState.readyEpoch, store.cloudState.epoch)
    XCTAssertEqual(store.courseDeletionState.scope, firstScope)
    XCTAssertEqual(counts.value, 0)
    store.suspendCourseUploads(); await store.flush()
  }


  func testSameAccountEpochChangeReinitializesBeforeNextUpload() async throws {
    let counts = UploadRequestCounts(), store = try countingStore(counts)
    let scope = try XCTUnwrap(store.noteScope)
    let first = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "before-epoch.pdf", contents: "first")] ))
    try await wait("First epoch upload should finish") { store.uploadState.batches.first { $0.id == first }?.isComplete == true }
    let oldEpoch = store.cloudState.epoch
    // A failed logout can renew the epoch while retaining authentication and
    // cacheKey. No explicit upload suspension is guaranteed on that error path.
    store.cloudState.epoch = UUID()
    let second = try XCTUnwrap(store.beginCourseUpload([pdf(store, name: "after-epoch.pdf", contents: "second")] ))
    try await wait("Same-scope new epoch must initialize and transfer") { store.uploadState.batches.first { $0.id == second }?.isComplete == true }
    XCTAssertNotEqual(oldEpoch, store.cloudState.epoch)
    XCTAssertEqual(store.courseDeletionState.readyEpoch, store.cloudState.epoch)
    XCTAssertEqual(store.uploadState.loadedEpoch, store.cloudState.epoch)
    XCTAssertEqual(store.uploadState.restoredEpoch, store.cloudState.epoch)
    XCTAssertEqual(store.uploadState.restoredScope, scope)
    XCTAssertEqual(counts.value, 2)
    store.suspendCourseUploads(); await store.flush()
  }

}

private final class UploadRequestCounts: @unchecked Sendable {
  private let lock = NSLock()
  private var count = 0
  func increment() -> Int { lock.lock(); defer { lock.unlock() }; count += 1; return count }
  var value: Int { lock.lock(); defer { lock.unlock() }; return count }
}
