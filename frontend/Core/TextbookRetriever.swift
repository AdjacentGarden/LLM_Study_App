import CryptoKit
import Foundation
import OnnxRuntimeBindings

struct SearchRequest: Sendable {
  var query: String
  var chapterID: String?
  var chapterScopeIDs: [String] = []
  var limit = 5
  var reliableOnly = true
}
struct SearchHit: Sendable {
  var chunk: SourceChunk
  var score: Double
  var lexicalScore: Double
  var reliable: Bool
}
struct SearchResult: Sendable {
  var method: String
  var hits: [SearchHit]
  var error: String?
}
protocol TextbookRetriever: Sendable { func search(_ request: SearchRequest) async -> SearchResult }
struct BM25Index: Decodable, Sendable {
  struct Posting: Decodable, Sendable {
    var idf: Double
    var postings: [[Double]]
  }
  var document_count: Int
  var average_document_length: Double
  var document_lengths: [Int]
  var k1: Double
  var b: Double
  var postings: [String: Posting]
}
struct RAGManifest: Decodable, Sendable {
  struct Embeddings: Decodable, Sendable {
    var model_id: String
    var revision: String
    var model_int8_sha256: String
    var dimension: Int
    var pooling: String
    var normalize: Bool
    var query_prefix: String
  }
  struct Retrieval: Decodable, Sendable {
    struct Weights: Decodable, Sendable {
      var semantic: Double
      var bm25: Double
      var chapterPrior: Double
    }
    var weights: Weights
    var minimum_evidence_threshold: Double
    var high_confidence_threshold: Double
    var lexical_fallback_threshold: Double
  }
  struct Artifact: Decodable, Sendable {
    var path: String
    var bytes: Int
    var sha256: String
  }
  var corpus_version: String
  var book_id: String
  var embeddings: Embeddings
  var retrieval: Retrieval
  var artifacts: [String: Artifact]
}
enum RetrievalMath {
  static let stop: Set<String> = ["的", "了", "和", "是", "在", "与", "及", "为", "中", "第"]
  static func tokenize(_ query: String) -> [String] {
    let text = query.precomposedStringWithCompatibilityMapping.lowercased(
      with: Locale(identifier: "zh_CN"))
    func matches(_ pattern: String) -> [String] {
      guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
      return regex.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap {
        Range($0.range, in: text).map { String(text[$0]) }
      }
    }
    var result = matches("[a-z0-9]+").filter { $0.count >= 2 }
    for run in matches("[\\u3400-\\u9fff]+") {
      let chars = Array(run)
      for i in chars.indices {
        result.append(String(chars[i]))
        if i + 1 < chars.count { result.append(String(chars[i...i + 1])) }
      }
    }
    return result.filter { !stop.contains($0) }
  }
  static func bm25(_ query: String, index: BM25Index) -> [Float] {
    var scores = Array(repeating: Float(0), count: index.document_count)
    var frequencies: [String: Int] = [:]
    let tokens = tokenize(query)
    var order: [String] = []
    for token in tokens {
      if frequencies[token] == nil { order.append(token) }
      frequencies[token, default: 0] += 1
    }
    for token in order {
      guard let posting = index.postings[token] else { continue }
      for pair in posting.postings where pair.count == 2 {
        let d = Int(pair[0])
        let frequency = pair[1]
        guard scores.indices.contains(d), index.document_lengths.indices.contains(d),
          index.average_document_length > 0
        else { continue }
        let denominator =
          frequency + index.k1
          * (1 - index.b + index.b * Double(index.document_lengths[d])
            / index.average_document_length)
        let contribution =
          posting.idf * ((frequency * (index.k1 + 1)) / denominator)
          * Double(min(frequencies[token] ?? 0, 3))
        scores[d] = Float(Double(scores[d]) + contribution)
      }
    }
    return scores
  }
  static func rank(
    query: String, chunks: [SourceChunk], index: BM25Index, vectors: [Float]? = nil,
    queryVector: [Float]? = nil, manifest: RAGManifest, chapterID: String? = nil,
    chapterScopeIDs: [String] = [], limit: Int = 5
  ) -> [SearchHit] {
    let lexical = bm25(query, index: index)
    let semantic = queryVector != nil && vectors != nil
    let dimension = manifest.embeddings.dimension
    let scope = Set(chapterScopeIDs)
    return chunks.enumerated().map { i, c in
      let bm = i < lexical.count ? Double(lexical[i]) : 0
      var score = bm
      if semantic, let vector = queryVector, let corpus = vectors, let position = c.vector_position,
        (position + 1) * dimension <= corpus.count, vector.count == dimension
      {
        let dot = (0..<dimension).reduce(0.0) {
          $0 + Double(vector[$1]) * Double(corpus[position * dimension + $1])
        }
        let prior =
          chapterID != nil
            && (chapterID == c.chapter_id || chapterID == c.section_id
              || scope.contains(c.chapter_id) || scope.contains(c.section_id ?? ""))
          ? 1.0 : 0
        score =
          manifest.retrieval.weights.semantic * dot + manifest.retrieval.weights.bm25
          * (bm > 0 ? bm / (bm + 8) : 0) + manifest.retrieval.weights.chapterPrior * prior
      }
      return SearchHit(
        chunk: c, score: score, lexicalScore: bm,
        reliable: score
          >= (semantic
            ? manifest.retrieval.minimum_evidence_threshold
            : manifest.retrieval.lexical_fallback_threshold))
    }.sorted {
      $0.score == $1.score
        ? $0.chunk.id.localizedCompare($1.chunk.id) == .orderedAscending : $0.score > $1.score
    }.prefix(max(0, limit)).map { $0 }
  }
}
/// The tokenizer mirrors tokenizer.json's cased BertNormalizer + BertPreTokenizer + WordPiece.
struct BertWordPiece: Sendable {
  let vocabulary: [String: Int64]
  init(vocabulary: String) {
    var result: [String: Int64] = [:]
    for (i, token) in vocabulary.components(separatedBy: "\n").enumerated() {
      result[token] = Int64(i)
    }
    self.vocabulary = result
  }
  func encode(_ text: String, maxLength: Int = 512) -> [Int64] {
    var basic: [String] = []
    var word = ""
    func flush() {
      if !word.isEmpty {
        basic.append(word)
        word = ""
      }
    }
    func chinese(_ n: UInt32) -> Bool {
      (0x4e00...0x9fff).contains(n) || (0x3400...0x4dbf).contains(n)
        || (0x20000...0x2ebef).contains(n) || (0xf900...0xfaff).contains(n)
        || (0x2f800...0x2fa1f).contains(n)
    }
    func process(_ fragment: String) {
      for scalar in fragment.unicodeScalars {
        let n = scalar.value
        if n == 0 || n == 0xfffd
          || (CharacterSet.controlCharacters.contains(scalar) && ![9, 10, 13].contains(n))
        {
          continue
        }
        if CharacterSet.whitespacesAndNewlines.contains(scalar) {
          flush()
        } else if chinese(n) || CharacterSet.punctuationCharacters.contains(scalar)
          || (33...47).contains(n) || (58...64).contains(n) || (91...96).contains(n)
          || (123...126).contains(n)
        {
          flush()
          basic.append(String(scalar))
        } else {
          word.unicodeScalars.append(scalar)
        }
      }
      flush()
    }
    let special = try! NSRegularExpression(pattern: #"\[(?:UNK|SEP|PAD|CLS|MASK)\]"#)
    var cursor = text.startIndex
    for match in special.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
      guard let range = Range(match.range, in: text) else { continue }
      process(String(text[cursor..<range.lowerBound]))
      basic.append(String(text[range]))
      cursor = range.upperBound
    }
    process(String(text[cursor...]))
    flush()
    var result: [Int64] = [vocabulary["[CLS]"] ?? 101]
    for token in basic {
      if ["[UNK]", "[SEP]", "[PAD]", "[CLS]", "[MASK]"].contains(token), let id = vocabulary[token]
      {
        result.append(id)
        continue
      }
      let chars = Array(token)
      var ids: [Int64] = []
      var start = 0
      var failed = chars.count > 100
      while start < chars.count && !failed {
        var end = chars.count
        var match: Int64?
        while end > start {
          let piece = (start > 0 ? "##" : "") + String(chars[start..<end])
          if let id = vocabulary[piece] {
            match = id
            break
          }
          end -= 1
        }
        if let match {
          ids.append(match)
          start = end
        } else {
          failed = true
        }
      }
      result += failed ? [vocabulary["[UNK]"] ?? 100] : ids
      if result.count >= maxLength { break }
    }
    result.append(vocabulary["[SEP]"] ?? 102)
    return Array(result.prefix(max(0, maxLength)))
  }
}
actor NativeTextbookRetriever: TextbookRetriever {
  private var corpus: (RAGManifest, [SourceChunk], BM25Index, [Float])?
  private var env: ORTEnv?
  private var session: ORTSession?
  private var tokenizer: BertWordPiece?
  private var semanticUnavailable = false
  static let corpusPath = "rag/biology-required-2-rag-v1/"
  func loadCorpus() throws -> (RAGManifest, [SourceChunk], BM25Index, [Float]) {
    if let corpus { return corpus }
    let manifest = try JSONDecoder().decode(
      RAGManifest.self, from: ContentBundle.data(Self.corpusPath + "manifest.json"))
    guard manifest.embeddings.dimension == 512, manifest.embeddings.pooling == "cls",
      manifest.embeddings.normalize, manifest.corpus_version == "biology-required-2-rag-v1"
    else { throw ContentError.invalid("检索索引版本不兼容") }
    func checked(_ key: String) throws -> Data {
      guard let artifact = manifest.artifacts[key] else { throw ContentError.missing(key) }
      let bytes = try ContentBundle.data(artifact.path)
      let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
      guard bytes.count == artifact.bytes, hash == artifact.sha256 else {
        throw ContentError.invalid("检索索引损坏：\(key)")
      }
      return bytes
    }
    struct Chunks: Decodable { var chunks: [SourceChunk] }
    let chunks = try JSONDecoder().decode(Chunks.self, from: checked("chunks")).chunks
    let index = try JSONDecoder().decode(BM25Index.self, from: checked("bm25"))
    let data = try checked("vectors")
    guard chunks.count == index.document_count, data.count == chunks.count * 512 * 4 else {
      throw ContentError.invalid("检索索引维度不匹配")
    }
    var vectors: [Float] = []
    vectors.reserveCapacity(data.count / 4)
    data.withUnsafeBytes { raw in
      for offset in stride(from: 0, to: raw.count, by: 4) {
        vectors.append(
          Float(
            bitPattern: UInt32(
              littleEndian: raw.loadUnaligned(fromByteOffset: offset, as: UInt32.self))))
      }
    }
    let loaded = (manifest, chunks, index, vectors)
    corpus = loaded
    return loaded
  }
  func embedding(_ query: String, manifest: RAGManifest) throws -> [Float] {
    guard !semanticUnavailable else { throw ContentError.invalid("模型不可用") }
    if session == nil {
      let prefix = "rag/models/Xenova/bge-small-zh-v1.5/"
      guard let model = ContentBundle.url(prefix + "onnx/model_int8.onnx") else {
        throw ContentError.missing("BGE 模型")
      }
      let bytes = try Data(contentsOf: model, options: .mappedIfSafe)
      let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
      guard hash == manifest.embeddings.model_int8_sha256 else {
        throw ContentError.invalid("本地模型校验失败")
      }
      env = try ORTEnv(loggingLevel: .warning)
      let options = try ORTSessionOptions()
      try options.setIntraOpNumThreads(1)
      session = try ORTSession(env: env!, modelPath: model.path, sessionOptions: options)
      tokenizer = BertWordPiece(
        vocabulary: try String(
          contentsOf: ContentBundle.url(prefix + "vocab.txt")!, encoding: .utf8))
    }
    guard let session, let tokenizer else { throw ContentError.invalid("模型无法启动") }
    let ids = tokenizer.encode(manifest.embeddings.query_prefix + query)
    let shape: [NSNumber] = [1, NSNumber(value: ids.count)]
    func value(_ numbers: [Int64]) throws -> ORTValue {
      let data = numbers.withUnsafeBufferPointer { Data(buffer: $0) }
      return try ORTValue(tensorData: NSMutableData(data: data), elementType: .int64, shape: shape)
    }
    var inputs: [String: ORTValue] = [:]
    for name in try session.inputNames() {
      switch name {
      case "input_ids": inputs[name] = try value(ids)
      case "attention_mask": inputs[name] = try value(Array(repeating: 1, count: ids.count))
      case "token_type_ids": inputs[name] = try value(Array(repeating: 0, count: ids.count))
      default: throw ContentError.invalid("模型输入不兼容：\(name)")
      }
    }
    let names = try session.outputNames()
    guard let outputName = names.first(where: { $0 == "last_hidden_state" }) ?? names.first else {
      throw ContentError.invalid("模型没有输出")
    }
    let output = try session.run(
      withInputs: inputs, outputNames: Set([outputName]), runOptions: nil)
    guard let tensor = output[outputName] else { throw ContentError.invalid("模型输出为空") }
    let data = try tensor.tensorData() as Data
    guard data.count >= 512 * 4 else { throw ContentError.invalid("模型输出维度错误") }
    var vector: [Float] = []
    data.withUnsafeBytes { raw in
      for i in 0..<512 { vector.append(raw.loadUnaligned(fromByteOffset: i * 4, as: Float.self)) }
    }
    let norm = sqrt(vector.reduce(0.0) { $0 + Double($1) * Double($1) })
    guard norm > 0, norm.isFinite else { throw ContentError.invalid("模型输出无效") }
    return vector.map { Float(Double($0) / norm) }
  }
  func search(_ request: SearchRequest) -> SearchResult {
    guard !request.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      return SearchResult(method: "unavailable", hits: [], error: nil)
    }
    do {
      let (manifest, chunks, index, vectors) = try loadCorpus()
      var vector: [Float]?
      var failure: String?
      do { vector = try embedding(request.query, manifest: manifest) } catch {
        semanticUnavailable = true
        failure = error.localizedDescription
      }
      var hits = RetrievalMath.rank(
        query: request.query, chunks: chunks, index: index, vectors: vector == nil ? nil : vectors,
        queryVector: vector, manifest: manifest, chapterID: request.chapterID,
        chapterScopeIDs: request.chapterScopeIDs, limit: request.limit)
      if vector != nil, request.reliableOnly, !hits.contains(where: { $0.reliable }) {
        let fallback = RetrievalMath.rank(
          query: request.query, chunks: chunks, index: index, manifest: manifest,
          chapterID: request.chapterID, chapterScopeIDs: request.chapterScopeIDs,
          limit: request.limit
        ).filter(\.reliable)
        if !fallback.isEmpty {
          return SearchResult(method: "on-device-bm25-fallback", hits: fallback, error: nil)
        }
      }
      if request.reliableOnly { hits = hits.filter(\.reliable) }
      return SearchResult(
        method: vector == nil ? "on-device-bm25-fallback" : "on-device-hybrid-rag", hits: hits,
        error: failure)
    } catch {
      return SearchResult(method: "unavailable", hits: [], error: error.localizedDescription)
    }
  }
  func lexicalSearch(_ request: SearchRequest) throws -> SearchResult {
    let (manifest, chunks, index, _) = try loadCorpus()
    var hits = RetrievalMath.rank(
      query: request.query, chunks: chunks, index: index, manifest: manifest,
      chapterID: request.chapterID, chapterScopeIDs: request.chapterScopeIDs, limit: request.limit)
    if request.reliableOnly { hits = hits.filter(\.reliable) }
    return SearchResult(method: "on-device-bm25-fallback", hits: hits, error: nil)
  }
}
