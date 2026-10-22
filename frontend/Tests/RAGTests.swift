import XCTest

@testable import BookCourse

@MainActor
final class RAGTests: XCTestCase {
  private let corpusPath = "rag/biology-required-2-rag-v1/"
  private let modelPath = "rag/models/Xenova/bge-small-zh-v1.5/"

  func testPinnedModelAndCasedNormalizerContract() async throws {
    let retriever = NativeTextbookRetriever()
    let (manifest, chunks, index, vectors) = try await retriever.loadCorpus()
    XCTAssertEqual(manifest.corpus_version, "biology-required-2-rag-v1")
    XCTAssertEqual(manifest.embeddings.model_id, "Xenova/bge-small-zh-v1.5")
    XCTAssertEqual(manifest.embeddings.revision, "75c43b069aac4d136ba6bc1122f995fedcfd2781")
    XCTAssertEqual(manifest.embeddings.dimension, 512)
    XCTAssertEqual(manifest.embeddings.pooling, "cls")
    XCTAssertTrue(manifest.embeddings.normalize)
    XCTAssertEqual(manifest.embeddings.query_prefix, "为这个句子生成表示以用于检索相关文章：")
    XCTAssertEqual(index.document_count, chunks.count)
    XCTAssertEqual(vectors.count, chunks.count * 512)
    XCTAssertTrue(vectors.allSatisfy(\.isFinite))
    let tokenizerJSON = try XCTUnwrap(
      try JSONSerialization.jsonObject(with: ContentBundle.data(modelPath + "tokenizer.json"))
        as? [String: Any])
    let normalizer = try XCTUnwrap(tokenizerJSON["normalizer"] as? [String: Any])
    XCTAssertEqual(normalizer["type"] as? String, "BertNormalizer")
    XCTAssertEqual(
      normalizer["lowercase"] as? Bool, false,
      "BGE's tokenizer is cased. Lowercasing would invalidate the existing vectors.")
    XCTAssertEqual(normalizer["clean_text"] as? Bool, true)
  }

  func testTokenizerMatchesAllSourceGoldenCases() throws {
    let golden = try ContentBundle.load(TokenizerGolden.self, name: "tokenizer-golden")
    let vocabularyURL = try XCTUnwrap(ContentBundle.url(modelPath + "vocab.txt"))
    let tokenizer = BertWordPiece(
      vocabulary: try String(contentsOf: vocabularyURL, encoding: .utf8))
    XCTAssertEqual(golden.cases.count, 61)
    for item in golden.cases {
      XCTAssertEqual(
        tokenizer.encode(item.text, maxLength: item.max_length), item.input_ids,
        "Tokenizer mismatch: \(item.id)")
    }
    XCTAssertNotEqual(
      tokenizer.encode("DNA mRNA"), tokenizer.encode("dna mrna"),
      "Do not silently switch to an uncased normalizer.")
  }

  func testBM25MatchesSourceGoldenRanksScoresAndReliability() async throws {
    let golden = try ContentBundle.load([BM25Golden].self, name: "rag-golden")
    let retriever = NativeTextbookRetriever()
    for item in golden {
      let result = try await retriever.lexicalSearch(
        SearchRequest(query: item.query, chapterID: nil, reliableOnly: false))
      XCTAssertEqual(result.method, "on-device-bm25-fallback")
      XCTAssertNil(result.error)
      XCTAssertEqual(result.hits.map { $0.chunk.id }, item.hits.map(\.id), item.query)
      for (actual, expected) in zip(result.hits, item.hits) {
        XCTAssertEqual(
          actual.score, expected.score, accuracy: 0.0001,
          "BM25 score: \(item.query) / \(expected.id)")
        XCTAssertEqual(actual.reliable, expected.reliable, "BM25 reliability: \(item.query)")
      }
    }
  }

  func testBM25FiftyQueryCalibrationMatchesCommittedReference() async throws {
    let evaluation = try read(EvaluationSet.self, "evaluation-set.json")
    let reference = try read(EvaluationReport.self, "evaluation-report.json")
    let retriever = NativeTextbookRetriever()
    var outcomes: [EvaluationOutcome] = []
    XCTAssertEqual(evaluation.queries.count, 50)
    for item in evaluation.queries {
      let result = try await retriever.lexicalSearch(
        SearchRequest(query: item.query, chapterID: nil, reliableOnly: false))
      let baseline = try XCTUnwrap(reference.results.first { $0.id == item.id })
      XCTAssertEqual(
        result.hits.map { $0.chunk.id }, baseline.lexical_top.map(\.chunk_id),
        "Lexical ranks: \(item.id)")
      XCTAssertEqual(
        result.hits.first?.score ?? 0, baseline.lexical_top_score, accuracy: 0.0001,
        "Lexical score: \(item.id)")
      outcomes.append(EvaluationOutcome(query: item, hits: result.hits))
    }
    let metrics = EvaluationMetrics(outcomes)
    XCTAssertGreaterThan(
      metrics.cited, 0, "A fallback that rejects every query is not a valid calibration.")
    XCTAssertGreaterThanOrEqual(metrics.precision, 0.95)
    XCTAssertLessThanOrEqual(metrics.falseCitationRate, 0.05)
    // The manifest rounds the calibrated threshold to 58.6248; its current runtime accepts 2/34 positives.
    XCTAssertGreaterThanOrEqual(metrics.acceptedPositiveRecall, 0.05)
    attach(metrics.description, name: "BM25 50-query metrics")
  }

  func testNativeBGEProducesFiniteNormalized512DimensionEmbedding() async throws {
    let retriever = NativeTextbookRetriever()
    let (manifest, _, _, _) = try await retriever.loadCorpus()
    let vector = try await retriever.embedding("减数分裂的特点是什么？", manifest: manifest)
    XCTAssertEqual(vector.count, 512)
    XCTAssertTrue(vector.allSatisfy(\.isFinite))
    let norm = sqrt(vector.reduce(0.0) { $0 + Double($1) * Double($1) })
    XCTAssertEqual(norm, 1, accuracy: 0.00001)
    let result = await retriever.search(
      SearchRequest(query: "减数分裂的特点是什么？", chapterID: nil, reliableOnly: false))
    XCTAssertEqual(
      result.method, "on-device-hybrid-rag",
      "The semantic model must run natively; lexical fallback cannot satisfy this test.")
    XCTAssertNil(result.error)
    XCTAssertFalse(result.hits.isEmpty)
  }

  func testNativeBGEFiftyQueryEvaluationPreservesRetrievalQuality() async throws {
    let evaluation = try read(EvaluationSet.self, "evaluation-set.json")
    let reference = try read(EvaluationReport.self, "evaluation-report.json")
    let retriever = NativeTextbookRetriever()
    let (manifest, chunks, index, vectors) = try await retriever.loadCorpus()
    var outcomes: [EvaluationOutcome] = []
    XCTAssertEqual(evaluation.queries.count, 50)
    XCTAssertEqual(evaluation.queries.filter(\.expected_rag).count, 34)
    for item in evaluation.queries {
      // No chapter prior: this is the same unbiased evaluation used to calibrate the source manifest.
      let vector = try await retriever.embedding(item.query, manifest: manifest)
      XCTAssertEqual(vector.count, 512, item.id)
      let hits = RetrievalMath.rank(
        query: item.query, chunks: chunks, index: index, vectors: vectors, queryVector: vector,
        manifest: manifest, limit: 5)
      XCTAssertEqual(hits.count, 5, item.id)
      let baseline = try XCTUnwrap(reference.results.first { $0.id == item.id })
      // Quantized kernels can vary slightly across CPU/runtime versions. The acceptance gates below remain strict.
      XCTAssertEqual(
        hits.first?.score ?? 0, baseline.top_score, accuracy: 0.02,
        "Native BGE score drift: \(item.id)")
      outcomes.append(EvaluationOutcome(query: item, hits: hits))
    }
    let metrics = EvaluationMetrics(outcomes)
    XCTAssertGreaterThanOrEqual(metrics.recallAt5, 0.99)
    XCTAssertGreaterThanOrEqual(metrics.top3HitRate, 0.95)
    XCTAssertGreaterThan(metrics.cited, 0)
    XCTAssertGreaterThanOrEqual(metrics.precision, 0.95)
    XCTAssertLessThanOrEqual(metrics.falseCitationRate, 0.05)
    XCTAssertGreaterThanOrEqual(metrics.acceptedPositiveRecall, 0.55)
    attach(metrics.description, name: "Native BGE 50-query metrics")
  }

  func testChapterPriorOnlyBoostsTheRequestedScope() async throws {
    let retriever = NativeTextbookRetriever()
    let (manifest, chunks, index, vectors) = try await retriever.loadCorpus()
    let vector = try await retriever.embedding("减数分裂", manifest: manifest)
    let plain = RetrievalMath.rank(
      query: "减数分裂", chunks: chunks, index: index, vectors: vectors, queryVector: vector,
      manifest: manifest, limit: chunks.count)
    let scoped = RetrievalMath.rank(
      query: "减数分裂", chunks: chunks, index: index, vectors: vectors, queryVector: vector,
      manifest: manifest, chapterID: "c2s1", chapterScopeIDs: ["c2s1a", "c2s1b", "c2s1c"],
      limit: chunks.count)
    let plainScores = Dictionary(uniqueKeysWithValues: plain.map { ($0.chunk.id, $0.score) })
    for hit in scoped {
      let inScope =
        ["c2s1", "c2s1a", "c2s1b", "c2s1c"].contains(hit.chunk.chapter_id)
        || ["c2s1", "c2s1a", "c2s1b", "c2s1c"].contains(hit.chunk.section_id ?? "")
      XCTAssertEqual(
        hit.score - (plainScores[hit.chunk.id] ?? 0),
        inScope ? manifest.retrieval.weights.chapterPrior : 0, accuracy: 0.0000001)
    }
  }

  func testEmptyQueryDoesNotInventEvidence() async {
    let retriever = NativeTextbookRetriever()
    let result = await retriever.search(SearchRequest(query: " \n\t ", chapterID: "c2s1"))
    XCTAssertTrue(result.hits.isEmpty)
  }

  func testOfflineAssistantGreetsWithoutTextbookCitation() async throws {
    let assistant = AssistantService(forceOffline: true)
    let answer = try await assistant.ask(
      question: "你好", bookID: "book_biology_2", bookIDs: ["book_biology_2"], chapterID: nil,
      sources: [], history: [])
    XCTAssertFalse(answer.answer.isEmpty)
    XCTAssertTrue(answer.citations.isEmpty)
  }

  func testOfflineAssistantPreservesImportedDocumentIdentity() async throws {
    let fixtures = try ContentBundle.load([SourceContent].self, name: "sources")
    var local = try XCTUnwrap(fixtures.first)
    local.summary.book_id = "book_local_fixture"
    local.summary.title = "海洋科学资料"
    local.chapters = [
      Chapter(
        chapter_id: "local-section", level: 1, source_title: "盐度观察", ai_title: "盐度观察",
        page_start: 3, page_end: 3, confidence: 100, status: "confirmed", source: "local-original")
    ]
    let evidence = "海洋科学研究海水盐度变化。蒸发增强会使局部海水盐度升高，淡水注入会使盐度降低。"
    local.chunks = [
      SourceChunk(
        chunk_id: "local-evidence", book_id: local.id, chapter_id: "local-section", page_start: 3,
        page_end: 3, content_type: "text", text: evidence, asset_ids: [], key_concepts: [])
    ]
    let assistant = AssistantService(forceOffline: true)
    let answer = try await assistant.ask(
      question: "海洋科学研究海水盐度变化", bookID: local.id, bookIDs: [local.id], chapterID: "local-section",
      sources: fixtures + [local], history: [])
    XCTAssertFalse(answer.citations.isEmpty)
    XCTAssertTrue(
      answer.citations.allSatisfy {
        $0.bookID == local.id && $0.chunkID == "local-evidence" && $0.page == 3
      })
    XCTAssertTrue(answer.answer.contains("蒸发增强"))
    XCTAssertTrue(answer.citations.allSatisfy { evidence.contains($0.quote) })
  }

  func testOfflineAssistantDoesNotBorrowBiologyForAnotherCourse() async throws {
    let sources = try ContentBundle.load([SourceContent].self, name: "sources")
    let other = try XCTUnwrap(sources.first { $0.id != "book_biology_2" && $0.chunks.isEmpty })
    let assistant = AssistantService(forceOffline: true)
    let answer = try await assistant.ask(
      question: "减数分裂的特点是什么？", bookID: other.id, bookIDs: [other.id], chapterID: nil,
      sources: sources, history: [])
    XCTAssertTrue(answer.citations.isEmpty)
    XCTAssertTrue(answer.answer.contains("没有找到足够可靠的原文依据"))
  }

  private func read<T: Decodable>(_ type: T.Type, _ filename: String) throws -> T {
    try JSONDecoder().decode(type, from: ContentBundle.data(corpusPath + filename))
  }
  private func attach(_ text: String, name: String) {
    let attachment = XCTAttachment(string: text)
    attachment.name = name
    attachment.lifetime = .keepAlways
    add(attachment)
  }
}

private struct TokenizerGolden: Decodable {
  struct Item: Decodable {
    let id: String
    let text: String
    let max_length: Int
    let input_ids: [Int64]
  }
  let cases: [Item]
}
private struct BM25Golden: Decodable {
  struct Hit: Decodable {
    let id: String
    let score: Double
    let reliable: Bool
  }
  let query: String
  let hits: [Hit]
}
private struct EvaluationSet: Decodable { let queries: [EvaluationQuery] }
private struct EvaluationQuery: Decodable {
  let id: String
  let query: String
  let expected_rag: Bool
  let expected_chapter_id: String?
  let expected_pdf_page_range: [Int]?
  func matches(_ chunk: SourceChunk) -> Bool {
    guard expected_rag else { return false }
    let section = chunk.section_id ?? chunk.chapter_id
    let overlap =
      expected_pdf_page_range.map {
        $0.count == 2 && chunk.page_start <= $0[1] && chunk.page_end >= $0[0]
      } ?? false
    return section == expected_chapter_id
      || (expected_chapter_id.map { section.hasPrefix($0) && overlap } ?? false) || overlap
  }
}
private struct EvaluationReport: Decodable {
  struct Entry: Decodable {
    struct Hit: Decodable { let chunk_id: String }
    let id: String
    let top_score: Double
    let lexical_top_score: Double
    let lexical_top: [Hit]
  }
  let results: [Entry]
}
private struct EvaluationOutcome {
  let query: EvaluationQuery
  let hits: [SearchHit]
  var cited: Bool { hits.first?.reliable == true }
  var correctCitation: Bool { hits.first.map { query.matches($0.chunk) } == true }
}
private struct EvaluationMetrics {
  let recallAt5: Double
  let top3HitRate: Double
  let precision: Double
  let falseCitationRate: Double
  let acceptedPositiveRecall: Double
  let cited: Int

  init(_ results: [EvaluationOutcome]) {
    let positives = results.filter { $0.query.expected_rag }
    let negatives = results.filter { !$0.query.expected_rag }
    let accepted = results.filter(\.cited)
    cited = accepted.count
    recallAt5 =
      Double(positives.filter { item in item.hits.contains { item.query.matches($0.chunk) } }.count)
      / Double(max(1, positives.count))
    top3HitRate =
      Double(
        positives.filter { item in item.hits.prefix(3).contains { item.query.matches($0.chunk) } }
          .count) / Double(max(1, positives.count))
    precision = Double(accepted.filter(\.correctCitation).count) / Double(max(1, accepted.count))
    falseCitationRate = Double(negatives.filter(\.cited).count) / Double(max(1, negatives.count))
    acceptedPositiveRecall =
      Double(positives.filter { $0.cited && $0.correctCitation }.count)
      / Double(max(1, positives.count))
  }
  var description: String {
    "recall@5=\(recallAt5), top3=\(top3HitRate), precision=\(precision), falseCitationRate=\(falseCitationRate), acceptedPositiveRecall=\(acceptedPositiveRecall), cited=\(cited)"
  }
}
