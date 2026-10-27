import Foundation
import XCTest

@testable import BookCourse

@MainActor final class LocalOCRPersistenceTests: XCTestCase {
  private func repository() throws -> LocalBookCourseRepository {
    try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory
      .appendingPathComponent("ocr-persistence-\(UUID())"))
  }

  private func serverSource(title: String = "服务端教材") -> SourceContent {
    let chunk = SourceChunk(chunk_id: "server-reference", book_id: "book", chapter_id: "chapter",
      page_start: 1, page_end: 1, content_type: "text", text: "服务端原文与已有引用", asset_ids: [], key_concepts: [])
    let lesson = Lesson(book_id: "book", lesson_id: "lesson", chapter_id: "chapter", title: title,
      source_title: "真实章节", page_start: 1, page_end: 2, lesson_kind: "generated", status: "ready",
      confidence: 0, objectives: [], key_concepts: [], summary: "最新服务端课程",
      blocks: [LessonBlock(block_id: "block", block_type: "explanation", title: "讲解", content: "已生成教学",
        citations: [LessonCitation(chunk_id: chunk.id, page_start: 1, page_end: 1, quote: chunk.text)],
        source_chunk_ids: [chunk.id], asset_ids: [], ai_generated: true)],
      source_chunk_ids: [chunk.id], asset_ids: [], warnings: [])
    return SourceContent(summary: SourceSummary(book_id: "book", title: title, cover_url: "/fresh-cover",
      filename: "chemistry.pdf", status: "ready", page_count: 2, chapter_count: 1,
      chunk_count: 1, asset_count: 0, average_confidence: 0, updated_at: 1, content_mode: "server"),
      chapters: [Chapter(chapter_id: "chapter", level: 1, source_title: "真实章节", ai_title: "真实章节",
        page_start: 1, page_end: 2, confidence: 0, status: "source_available", source: "server")],
      lessons: [lesson], chunks: [chunk], assets: [], flashcards: [], quizzes: [],
      plan: StudyPlan(user_id: "", book_id: "book", days: 0, daily_minutes: 0, tasks: []))
  }

  private func localSource(_ server: SourceContent, parser: String = "apple-vision-ios") -> SourceContent {
    var source = server
    source.summary.page_count = 3
    source.summary.updated_at = 2
    source.chunks.append(SourceChunk(chunk_id: "book:p3:0", book_id: "book", chapter_id: "",
      page_start: 3, page_end: 3, content_type: "text", text: "仅本账号保存的 iPhone 本机原文",
      asset_ids: [], key_concepts: [], source_metadata: [
        "execution_device": .string("ios-device"), "network_required": .bool(false),
        "parser": .string(parser), "physical_page_verified": .bool(true),
      ]))
    source.summary.chunk_count = source.chunks.count
    return source
  }

  private func resource(_ source: SourceContent, key: String = "server-v1") -> CloudResource {
    CloudResource(id: "resource", book_id: source.id, workspace_id: "workspace", revision: 0,
      filename: "chemistry.pdf", status: "ready", courses: [], progress: CloudProgress(),
      source_content: source, content_key: key)
  }

  private func snapshot(source: SourceContent, resource: CloudResource) -> CloudLocalSnapshot {
    CloudLocalSnapshot(snapshot: AppSnapshot(), sources: [source], workspaces: [
      CloudWorkspace(id: "workspace", name: "化学", preferences: [:], created: 0, updated: 0,
        resources: [resource]),
    ], filePaths: [:], pending: [:])
  }

  func testRemoteBookDeviceTextSurvivesReopenWithoutReplacingServerTeachingOrReferences() async throws {
    let repo = try repository()
    let server = serverSource(), local = localSource(server)
    try await repo.saveCloudState(snapshot(source: local, resource: resource(server)), key: "account-a")
    let reopened = try LocalBookCourseRepository(directory: repo.directory)
    let loaded = try await reopened.loadCloudState("account-a")
    let cached = try XCTUnwrap(loaded)
    let source = try XCTUnwrap(cached.sources.first)
    XCTAssertEqual(cached.sources.count, 1)
    XCTAssertEqual(source.chunks.map(\.id), ["server-reference", "book:p3:0"])
    XCTAssertEqual(source.chunks.last?.text, local.chunks.last?.text)
    XCTAssertEqual(source.lessons, server.lessons)
    XCTAssertEqual(source.chapters, server.chapters)
    XCTAssertEqual(source.summary.title, server.summary.title)
    XCTAssertEqual(source.summary.cover_url, server.summary.cover_url)
    XCTAssertEqual(source.summary.page_count, 3)
    XCTAssertEqual(source.lessons.first?.source_chunk_ids, [source.chunks[0].id])
    let body = try await reopened.loadCloudContent("server-v1", account: "account-a")
    XCTAssertEqual(body?.source_content?.chunks, server.chunks, "Local OCR must not mutate versioned server bodies")
  }

  func testAccountLocalOverlayNeverEntersOtherAccountOrSharedBuiltinBody() async throws {
    let repo = try repository(), server = serverSource()
    let shared = resource(server, key: "builtin:shared")
    try await repo.saveCloudState(snapshot(source: localSource(server), resource: shared), key: "account-a")
    try await repo.saveCloudState(snapshot(source: server, resource: shared), key: "account-b")
    let loadedA = try await repo.loadCloudState("account-a")
    let loadedB = try await repo.loadCloudState("account-b")
    let a = try XCTUnwrap(loadedA)
    let b = try XCTUnwrap(loadedB)
    XCTAssertEqual(a.sources[0].chunks.count, 2)
    XCTAssertEqual(b.sources[0].chunks, server.chunks)
    let sharedBody = try await repo.loadCloudContent("builtin:shared", account: "account-b")
    XCTAssertEqual(sharedBody?.source_content?.chunks, server.chunks)
    let missing = try await repo.loadCloudState("account-c")
    XCTAssertNil(missing)
  }

  func testCloudSourceRefreshPreservesDeviceOverlayAndUsesFreshServerMetadata() throws {
    let repo = try repository()
    let store = AppStore(repository: repo)
    let old = serverSource(), refreshed = serverSource(title: "更新后的教材标题")
    store.sources = [localSource(old, parser: "pdfkit-text")]
    store.cloudState.workspaces = snapshot(source: refreshed, resource: resource(refreshed)).workspaces
    store.installCloudSources(workspaceID: "workspace")
    store.installCloudSources(workspaceID: "workspace")
    XCTAssertEqual(store.sources.count, 1)
    XCTAssertEqual(store.sources[0].summary.title, refreshed.summary.title)
    XCTAssertEqual(store.sources[0].lessons, refreshed.lessons)
    XCTAssertEqual(store.sources[0].chunks.map(\.id), ["server-reference", "book:p3:0"])
    XCTAssertEqual(store.sources[0].chunks.last?.source_metadata?["parser"], .string("pdfkit-text"))
  }

  func testOnlyDeviceOCRIsMergedAndFreshServerChunkWinsOnSameID() {
    let server = serverSource()
    var local = localSource(server)
    local.chunks[0].text = "旧的服务端文字不得覆盖新内容"
    var unrelated = local.chunks[1]
    unrelated.chunk_id = "unrelated"
    unrelated.source_metadata = ["parser": .string("remote-ocr")]
    local.chunks.append(unrelated)
    let once = LocalBookCourseRepository.mergingLocalOCR(from: local, into: server)
    let twice = LocalBookCourseRepository.mergingLocalOCR(from: local, into: once)
    XCTAssertEqual(once, twice)
    XCTAssertEqual(once.chunks.first, server.chunks.first)
    XCTAssertEqual(once.chunks.map(\.id), ["server-reference", "book:p3:0"])
    var repeatedOCR = localSource(server)
    repeatedOCR.chunks[1].text = "重新识别后的本机文字"
    let refreshedOCR = LocalBookCourseRepository.mergingLocalOCR(from: repeatedOCR, into: once)
    XCTAssertEqual(refreshedOCR.chunks.count, 2)
    XCTAssertEqual(refreshedOCR.chunks.last?.text, "重新识别后的本机文字")
    local.summary.book_id = "other-book"
    XCTAssertEqual(LocalBookCourseRepository.mergingLocalOCR(from: local, into: server), server)
  }
}
