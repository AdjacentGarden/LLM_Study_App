import CryptoKit
import XCTest
@testable import BookCourse

@MainActor final class LocalFirstStorageTests: XCTestCase {
  private func repository() throws -> LocalBookCourseRepository {
    try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
  }
  func testLegacyPrivateWritesAreBlockedWhileAuthPublicAndTransientRoutesRemainAvailable() {
    for path in ["/api/workspaces", "/api/workspaces/x/resources/r/progress", "/api/interviews/s/respond",
      "/api/native/learner-preferences", "/api/assistant/query"] {
      XCTAssertTrue(CloudAPI.isLegacyPrivateMutation(path: path, method: "POST"), path)
    }
    for path in ["/api/auth/code", "/api/public/library/course-groups", "/api/assistant/private-model",
      "/api/studio/notes", "/api/studio/media", "/api/studio/notes/n/analyze", "/api/assistant/turn", "/api/private/export/checkpoint", "/api/native/uploads"] {
      XCTAssertFalse(CloudAPI.isLegacyPrivateMutation(path: path, method: "POST"), path)
    }
    XCTAssertFalse(CloudAPI.isLegacyPrivateMutation(path: "/api/interviews/s", method: "GET"))
  }
  func testVerifiedMaterialFilesReuseHashesAndRejectCorruption() async throws {
    let repository = try repository()
    let data = Data("verified original".utf8)
    let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    let file = PublicMaterialFile(asset_id: "pdf", kind: "pdf", sha256: digest, bytes: data.count,
      media_type: "application/pdf", download_url: "/api/public/library/course-groups/g/assets/" + digest)
    let absent = try await repository.verifiedMaterialFile(file, scope: "owner")
    XCTAssertNil(absent)
    let accepted = try await repository.verifiedMaterialFile(file, scope: "owner", bytes: data)
    let replay = try await repository.verifiedMaterialFile(file, scope: "owner")
    XCTAssertEqual(replay, accepted)
    do {
      _ = try await repository.verifiedMaterialFile(file, scope: "owner", bytes: Data("bad".utf8))
      XCTFail("A mismatched checksum cannot enter the local registry")
    } catch {}
    let other = try await repository.verifiedMaterialFile(file, scope: "other")
    XCTAssertNil(other)
  }
  func testPersonalStateRetainsQuestionnaireProgressAndAccountIsolation() async throws {
    let repository = try repository()
    var snapshot = AppSnapshot()
    snapshot.courses = [Course(id: "local-course:A", name: "数学", resourceIDs: ["book"], diagnosis: ["goal": ["review"]])]
    snapshot.locations = ["local-course:A::book": StudyLocation(page: 12, completedChapterIDs: ["chapter"])]
    let state = LocalPersonalState(snapshot: snapshot, sources: [], workspaces: [], filePaths: [:],
      courseSources: [:], dailyPlans: [:], diagnostics: [:], initialized: true)
    try await repository.savePersonalState(state, scope: "A")
    let reopened = try await repository.loadPersonalState(scope: "A")
    XCTAssertEqual(reopened?.snapshot.courses.first?.diagnosis["goal"], ["review"])
    XCTAssertEqual(reopened?.snapshot.locations["local-course:A::book"]?.page, 12)
    let other = try await repository.loadPersonalState(scope: "B")
    XCTAssertNil(other)
  }
  func testLocalToolOperationIsIdempotentAndLearningReadOmitsNoteConversations() async throws {
    let repository = try repository()
    let store = AppStore(repository: repository)
    store.cloudState.privateLocalMode = true
    store.courses = [Course(id: "A", name: "数学", resourceIDs: ["book"])]
    let args: [String: JSONValue] = ["course_id": .string("A"), "book_id": .string("book"),
      "cards": .array([.object(["front": .string("问题"), "back": .string("答案")])])]
    let first = try await store.performLocalPersonalOperation(name: "create_flashcards", args: args, runID: "run", callID: "call")
    let second = try await store.performLocalPersonalOperation(name: "create_flashcards", args: args, runID: "run", callID: "call")
    XCTAssertEqual(first, second)
    let objects = try await repository.loadAgentLearningObjects(scope: LocalBookCourseRepository.localNoteScope)
    XCTAssertEqual(objects.count, 1)
    store.notes = [StudyNote(id: "note", kind: .text, title: "可读取笔记", body: "知识点",
      anchor: NoteAnchor(courseID: "A", bookID: "book", page: 1),
      conversation: [ChatMessage(role: "user", text: "不得跨会话读取的私人聊天")])]
    let result = try await store.readLocalPersonalLearning(args: ["course_id": .string("A"), "kind": .string("notes")])
    let encoded = String(decoding: try JSONEncoder().encode(result), as: UTF8.self)
    XCTAssertTrue(encoded.contains("知识点"))
    XCTAssertFalse(encoded.contains("不得跨会话"))
  }
  func testGeneratedMediaIsSearchableOnlyInItsCourseAndOwnerLibrary() async throws {
    let repo = try repository()
    let store = AppStore(repository: repo)
    let scope = try XCTUnwrap(store.noteScope)
    store.courses = ["first", "second"].map { Course(id: $0, name: $0, resourceIDs: ["book"]) }
    let folder = repo.directory.appendingPathComponent("lesson-media")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let asset = folder.appendingPathComponent("downloaded.jpg")
    try Data([1, 2, 3]).write(to: asset)
    let request = LessonMediaRequest(kind: "image", bookID: "book", chapterID: "ch", chapterTitle: "向量",
      title: "定向图解", excerpt: "向量有大小和方向", pages: [2], lessonID: "lesson", courseID: "first")
    for _ in 0..<2 { try await repo.saveLessonMedia(asset, id: "job", context: request, resourceID: "r1", scope: scope) }
    for (course, expected) in [("first", 1), ("second", 0)] {
      let result = try await store.readLocalPersonalLearning(args: ["course_id": .string(course),
        "kind": .string("media"), "keyword": .string("定向图解")])
      XCTAssertEqual(result["total"], .number(Double(expected)))
    }
    let foreign = try await repo.localLessonMedia(scope: "another-owner")
    XCTAssertTrue(foreign.isEmpty)
  }

  func testLegacyLearningKindAliasesReturnTheirCanonicalLocalRows() async throws {
    let repository = try repository()
    let store = AppStore(repository: repository)
    store.cloudState.privateLocalMode = true
    store.courses = [Course(id: "A", name: "数学", resourceIDs: ["book"])]
    store.notes = [StudyNote(id: "note", kind: .text, title: "批注", body: "原文理解",
      anchor: NoteAnchor(courseID: "A", bookID: "book", page: 1))]
    let rows: [[String: JSONValue]] = ["review_task", "submitted_answer"].enumerated().map { index, kind in
      ["id": .string("object-\(index)"), "revision": .number(1), "kind": .string(kind),
        "course_id": .string("A"), "data": .object(["title": .string("记录")])]
    }
    let objects = try rows.map { try JSONDecoder().decode(AgentLearningObject.self, from: JSONEncoder().encode($0)) }
    try await repository.saveAgentLearningObjects(objects, scope: LocalBookCourseRepository.localNoteScope)
    for (alias, canonical) in [("review_tasks", "reviews"), ("annotations", "notes"), ("attempts", "submitted_answers")] {
      let legacy = try await store.readLocalPersonalLearning(args: ["course_id": .string("A"), "kind": .string(alias)])
      let current = try await store.readLocalPersonalLearning(args: ["course_id": .string("A"), "kind": .string(canonical)])
      XCTAssertEqual(legacy, current, alias)
      XCTAssertEqual(legacy["total"], .number(1), alias)
    }
  }

  func testMigrationArchiveKeepsBothLegacyVersionsWithoutReplacingPersonalAuthority() async throws {
    let repository = try repository()
    let first = PrivateExportRecord(kind: "native_workspaces", id: "row", revision: "one", payload: ["name": .string("旧名称")], deleted: false)
    let second = PrivateExportRecord(kind: "native_workspaces", id: "row", revision: "two", payload: ["name": .string("旧版又修改")], deleted: false)
    try await repository.archivePrivateExport(first, scope: "A")
    try await repository.archivePrivateExport(second, scope: "A")
    let archived = try await repository.privateMigrationArchives(scope: "A")
    XCTAssertEqual(archived.count, 2)
    let other = try await repository.privateMigrationArchives(scope: "B")
    XCTAssertTrue(other.isEmpty)
  }
}
