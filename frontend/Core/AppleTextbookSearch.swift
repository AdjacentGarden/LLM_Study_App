import CryptoKit
import Foundation
import NaturalLanguage

enum AppleTextbookEmbeddingMode: String, Sendable {
  case sentence = "apple-sentence-embedding+bm25"
  case word = "apple-word-embedding+bm25"
  case lexical = "apple-local-bm25"
}
struct AppleTextbookSearchCapability: Sendable {
  var mode: AppleTextbookEmbeddingMode
  var notice: String
}

/// NLEmbedding instances are not thread-safe. All access is serialized by this
/// actor; only caller-supplied cached source chunks are searched.
actor AppleTextbookSearch {
  private let sentence: NLEmbedding?
  private let word: NLEmbedding?
  private var vectorCache: [String: [Double]] = [:]
  init(useSystemEmbeddings: Bool = true) {
    let availableSentence = useSystemEmbeddings ? NLEmbedding.sentenceEmbedding(for: .simplifiedChinese) : nil
    sentence = availableSentence
    word = availableSentence == nil && useSystemEmbeddings ? NLEmbedding.wordEmbedding(for: .simplifiedChinese) : nil
  }
  func capability() -> AppleTextbookSearchCapability {
    if sentence != nil { return .init(mode: .sentence, notice: "使用 Apple 系统中文句向量与本机关键词检索。") }
    if word != nil { return .init(mode: .word, notice: "系统未提供中文句向量，使用 Apple 中文词向量平均与本机关键词检索。") }
    return .init(mode: .lexical, notice: "系统中文向量模型不可用，当前仅使用本机关键词检索；没有下载模型或切换云端。")
  }
  func search(query: String, sources: [SourceContent], bookIDs: Set<String>? = nil,
    limit: Int = 5) async throws -> SearchResult {
    try Task.checkCancellation()
    let mode = capability().mode
    let question = String(query.trimmingCharacters(in: .whitespacesAndNewlines).prefix(1000))
    let queryTokens = Set(Self.tokens(question))
    guard !queryTokens.isEmpty else {
      return SearchResult(method: mode.rawValue, hits: [], error: "请提供具体的教材知识点。")
    }
    var seen = Set<String>()
    let chunks = sources.filter { bookIDs?.contains($0.id) ?? true }.flatMap { source in
      source.chunks.filter {
        $0.book_id == source.id && !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
          && seen.insert($0.book_id + "|" + $0.id).inserted
      }
    }
    guard !chunks.isEmpty else {
      return SearchResult(method: mode.rawValue, hits: [], error: "本机尚未缓存这些教材的原文，请先连接 Wi-Fi 准备教材后再检索。")
    }
    var documents: [[String: Int]] = []
    var lengths: [Int] = []
    var frequency: [String: Int] = [:]
    for (index, chunk) in chunks.enumerated() {
      if index % 64 == 0 { try Task.checkCancellation() }
      let tokens = Self.tokens(chunk.text)
      documents.append(tokens.reduce(into: [:]) { $0[$1, default: 0] += 1 })
      lengths.append(max(1, tokens.count))
      for token in Set(tokens) { frequency[token, default: 0] += 1 }
    }
    let averageLength = Double(lengths.reduce(0, +)) / Double(chunks.count)
    let queryVector = vector(question, tokens: Array(queryTokens))
    var hits: [SearchHit] = []
    for (index, chunk) in chunks.enumerated() {
      if index % 32 == 0 { try Task.checkCancellation() }
      var bm25 = 0.0
      for token in queryTokens {
        let count = Double(documents[index][token] ?? 0)
        guard count > 0 else { continue }
        let df = Double(frequency[token] ?? 0), n = Double(chunks.count)
        let idf = log(1 + (n - df + 0.5) / (df + 0.5))
        bm25 += idf * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * Double(lengths[index]) / max(1, averageLength)))
      }
      let semantic = queryVector.flatMap { queryVector in
        vector(chunk.text, tokens: Array(documents[index].keys)).map { Self.cosine(queryVector, $0) }
      } ?? 0
      guard bm25 > 0 || semantic >= 0.75 else { continue }
      let score = mode == .lexical ? bm25 : 0.65 * bm25 / (bm25 + 3) + 0.35 * max(0, semantic)
      hits.append(SearchHit(chunk: chunk, score: score, lexicalScore: bm25, reliable: chunk.page_start > 0))
    }
    hits.sort {
      if $0.score != $1.score { return $0.score > $1.score }
      return ($0.chunk.book_id, $0.chunk.page_start, $0.chunk.id) < ($1.chunk.book_id, $1.chunk.page_start, $1.chunk.id)
    }
    return SearchResult(method: mode.rawValue, hits: Array(hits.prefix(max(1, min(20, limit)))),
      error: hits.isEmpty ? "已缓存的教材原文中没有找到相关内容，请换一个具体知识点；本次未调用大模型。" : nil)
  }

  private func vector(_ text: String, tokens: [String]) -> [Double]? {
    guard sentence != nil || word != nil else { return nil }
    let key = SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    if let cached = vectorCache[key] { return cached }
    let value: [Double]?
    if let sentence { value = sentence.vector(for: String(text.prefix(1600))) }
    else if let word {
      value = Self.average(tokens.sorted().prefix(256).compactMap { word.vector(for: $0) })
    } else { value = nil }
    guard let value, !value.isEmpty, value.allSatisfy(\.isFinite) else { return nil }
    if vectorCache.count >= 4096 { vectorCache.removeAll(keepingCapacity: true) }
    vectorCache[key] = value
    return value
  }
  static func average(_ vectors: [[Double]]) -> [Double]? {
    guard let first = vectors.first, !first.isEmpty else { return nil }
    let valid = vectors.filter { $0.count == first.count && $0.allSatisfy(\.isFinite) }
    guard !valid.isEmpty else { return nil }
    return (0..<first.count).map { column in valid.reduce(0) { $0 + $1[column] } / Double(valid.count) }
  }
  static func cosine(_ lhs: [Double], _ rhs: [Double]) -> Double {
    guard !lhs.isEmpty, lhs.count == rhs.count else { return 0 }
    let a = sqrt(lhs.reduce(0) { $0 + $1 * $1 }), b = sqrt(rhs.reduce(0) { $0 + $1 * $1 })
    guard a > 0, b > 0 else { return 0 }
    return max(-1, min(1, zip(lhs, rhs).reduce(0) { $0 + $1.0 * $1.1 } / (a * b)))
  }
  static func tokens(_ text: String) -> [String] {
    let normalized = text.lowercased()
    let tokenizer = NLTokenizer(unit: .word)
    tokenizer.string = normalized
    tokenizer.setLanguage(.simplifiedChinese)
    let stop: Set<String> = ["的", "是", "了", "在", "和", "与", "请", "我", "什么", "如何", "为什么", "一下", "教材"]
    var result: [String] = []
    tokenizer.enumerateTokens(in: normalized.startIndex..<normalized.endIndex) { range, _ in
      let token = normalized[range].filter { $0.isLetter || $0.isNumber }
      if !token.isEmpty && !stop.contains(token) { result.append(token) }
      return true
    }
    var run: [Character] = []
    func appendRun() {
      if run.count >= 2 {
        for index in 0..<(run.count - 1) { result.append(String(run[index...index + 1])) }
      }
      run.removeAll(keepingCapacity: true)
    }
    for character in normalized {
      if character.unicodeScalars.allSatisfy({ (0x4e00...0x9fff).contains(Int($0.value)) }) { run.append(character) }
      else { appendRun() }
    }
    appendRun()
    return result
  }
}
