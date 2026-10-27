import NaturalLanguage
import XCTest

@testable import BookCourse

final class AppleTextbookSearchTests: XCTestCase {
  private func source(_ id: String, text: String, page: Int = 7) -> SourceContent {
    SourceContent(summary: SourceSummary(book_id: id, title: id, status: "ready", page_count: 20,
      chapter_count: 0, chunk_count: 1, asset_count: 0, average_confidence: 0, updated_at: 0),
      chapters: [], lessons: [], chunks: text.isEmpty ? [] : [SourceChunk(chunk_id: "chunk-1",
        book_id: id, chapter_id: "", page_start: page, page_end: page,
        content_type: "text", text: text, asset_ids: [], key_concepts: [])],
      assets: [], flashcards: [], quizzes: [],
      plan: StudyPlan(user_id: "local", book_id: id, days: 0, daily_minutes: 0, tasks: []))
  }
  func testSearchUsesAllCallerCachedBooksAndPreservesSourceAnchors() async throws {
    let search = AppleTextbookSearch(useSystemEmbeddings: false)
    let chemistry = source("my-chemistry", text: "氧化还原反应涉及电子转移。", page: 17)
    let biology = source("my-biology", text: "光合作用中的电子转移发生在叶绿体。", page: 28)
    let result = try await search.search(query: "电子转移", sources: [chemistry, biology])
    XCTAssertEqual(result.method, "apple-local-bm25")
    XCTAssertEqual(Set(result.hits.map { $0.chunk.book_id }), ["my-chemistry", "my-biology"])
    XCTAssertTrue(result.hits.contains { $0.chunk.book_id == chemistry.id && $0.chunk.page_start == 17 })
    XCTAssertTrue(result.hits.allSatisfy { $0.chunk.chunk_id == "chunk-1" && $0.reliable })
    XCTAssertNil(result.error)
  }
  func testScopeCannotBorrowAnotherBooksMatchingText() async throws {
    let search = AppleTextbookSearch(useSystemEmbeddings: false)
    let chemistry = source("chemistry", text: "电解质溶液中的离子。")
    let biology = source("biology", text: "细胞分裂与染色体变化。")
    let result = try await search.search(query: "染色体", sources: [chemistry, biology], bookIDs: [chemistry.id])
    XCTAssertTrue(result.hits.isEmpty)
    XCTAssertTrue(result.error?.contains("没有找到") == true)
  }
  func testMissingCachedOriginalRequestsWiFiPreparation() async throws {
    let search = AppleTextbookSearch(useSystemEmbeddings: false)
    let result = try await search.search(query: "化学", sources: [source("empty", text: "")])
    XCTAssertTrue(result.hits.isEmpty)
    XCTAssertTrue(result.error?.contains("Wi-Fi") == true)
    let capability = await search.capability()
    XCTAssertEqual(capability.mode, .lexical)
    XCTAssertTrue(capability.notice.contains("仅使用本机关键词"))
  }
  func testUpdatedCallerSourceCannotReturnRemovedCachedText() async throws {
    let search = AppleTextbookSearch(useSystemEmbeddings: false)
    let first = try await search.search(query: "氧化", sources: [source("chem", text: "氧化反应。")])
    XCTAssertEqual(first.hits.count, 1)
    let second = try await search.search(query: "氧化", sources: [source("chem", text: "溶液中的离子。")])
    XCTAssertTrue(second.hits.isEmpty)
  }
  func testForeignBookChunksAreRejectedAndPhysicalUnknownIsNotReliable() async throws {
    let search = AppleTextbookSearch(useSystemEmbeddings: false)
    var value = source("local", text: "氧化反应。", page: 0)
    var foreign = value.chunks[0]
    foreign.book_id = "another-owner"; foreign.chunk_id = "foreign"
    value.chunks.append(foreign)
    let result = try await search.search(query: "氧化", sources: [value])
    XCTAssertEqual(result.hits.count, 1)
    XCTAssertEqual(result.hits.first?.chunk.id, "chunk-1")
    XCTAssertEqual(result.hits.first?.reliable, false)
  }
  func testWordVectorAverageAndCosineIgnoreInvalidVectors() {
    XCTAssertEqual(AppleTextbookSearch.average([[1, 0], [0, 1], [.nan, 1], [7]]), [0.5, 0.5])
    XCTAssertNil(AppleTextbookSearch.average([]))
    XCTAssertEqual(AppleTextbookSearch.cosine([1, 0], [1, 0]), 1, accuracy: 0.0001)
    XCTAssertEqual(AppleTextbookSearch.cosine([1, 0], [0, 1]), 0, accuracy: 0.0001)
    XCTAssertEqual(AppleTextbookSearch.cosine([], []), 0)
  }
  func testCancelledSearchDoesNotReturnCachedHits() async throws {
    let search = AppleTextbookSearch(useSystemEmbeddings: false)
    let sources = [source("local", text: "化学反应。")]
    let task = Task {
      withUnsafeCurrentTask { $0?.cancel() }
      return try await search.search(query: "化学", sources: sources)
    }
    do { _ = try await task.value; XCTFail("Cancelled search returned evidence") }
    catch { XCTAssertTrue(error is CancellationError) }
  }
  func testInstalledSystemEmbeddingCapabilityWithoutDownloadingAssets() async {
    let capability = await AppleTextbookSearch().capability()
    let sentence = NLEmbedding.sentenceEmbedding(for: .simplifiedChinese)
    let word = sentence == nil ? NLEmbedding.wordEmbedding(for: .simplifiedChinese) : nil
    let expected: AppleTextbookEmbeddingMode = sentence != nil ? .sentence : word != nil ? .word : .lexical
    print("APPLE_TEXTBOOK_CAPABILITY mode=\(capability.mode.rawValue) sentenceInstalled=\(sentence != nil) wordFallbackInstalled=\(word != nil) notice=\(capability.notice)")
    XCTAssertEqual(capability.mode, expected)
    XCTAssertFalse(capability.notice.isEmpty)
    XCTAssertEqual(capability.notice.contains("仅使用本机关键词"), expected == .lexical)
  }
}
