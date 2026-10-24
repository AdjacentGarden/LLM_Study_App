import Foundation
import Observation

enum LearningMediaKind: String, Codable, Sendable {
  case image, video
}

struct LearningMediaContext: Identifiable, Sendable {
  let id = UUID()
  let bookID: String
  let chapterID: String
  let chapterTitle: String
  let excerpt: String
  let pages: [Int]
  let kind: LearningMediaKind

  init(lesson: Lesson, section: LessonBlock?, asset: BookAsset? = nil, kind: LearningMediaKind) {
    bookID = lesson.book_id
    chapterID = lesson.chapter_id
    chapterTitle = lesson.title
    let text = section?.content ?? lesson.summary
    excerpt = String(
      ([asset?.caption, text].compactMap { $0 }.filter { !$0.isEmpty })
        .joined(separator: "\n").prefix(3000))
    let citations = section?.citations ?? lesson.blocks.flatMap(\.citations)
    let sourcePages = citations.flatMap { citation in
      Array(
        citation
          .page_start...max(citation.page_start, min(citation.page_end, citation.page_start + 29)))
    }.filter { (1...100000).contains($0) }
    pages = Array(
      Set(sourcePages.isEmpty ? [asset?.page ?? lesson.page_start].filter { $0 > 0 } : sourcePages)
    )
    .sorted().prefix(30).map { $0 }
    self.kind = kind
  }

  func body(kind: LearningMediaKind, requestID: String) -> [String: JSONValue] {
    [
      "book_id": .string(bookID), "chapter_id": .string(chapterID),
      "chapter_title": .string(chapterTitle), "excerpt": .string(excerpt),
      "pages": .array(pages.map { .number(Double($0)) }), "kind": .string(kind.rawValue),
      "goal": .string("过程"), "level": .string("标准"),
      "request_id": .string(requestID), "consent": .bool(true),
    ]
  }
}

struct CloudMediaCapabilities: Decodable, Sendable {
  let image: Bool
  let video: Bool
  let video_seconds: Int?
  let image_estimate: Double?
  let video_estimate: Double?
  let media_notice: String?
  func available(_ kind: LearningMediaKind) -> Bool { kind == .image ? image : video }
}

struct CloudMediaJob: Decodable, Identifiable, Sendable {
  let id: String
  let book_id: String
  let kind: String
  let status: String
  let result: [String: JSONValue]
  let error: String?
  let created: Double
  let excerpt: String
  let chapter_title: String
  let asset_url: String?

  var isRunning: Bool {
    ["queued", "planning", "submitting", "polling", "reviewing"].contains(status)
  }
  var statusText: String {
    switch status {
    case "queued": "正在排队"
    case "planning": "正在设计讲解"
    case "submitting": "正在提交生成"
    case "polling": "正在生成短片"
    case "reviewing": "正在检查内容"
    case "succeeded": "已生成"
    case "failed": "生成未完成"
    case "uncertain": "结果待确认"
    default: "请刷新任务状态"
    }
  }
}

struct CloudMediaJobs: Decodable, Sendable { let items: [CloudMediaJob] }

extension CloudAPI {
  /// Download through the account's credential jar; AVPlayer/AsyncImage must not
  /// make unauthenticated requests or receive provider URLs and credentials.
  func downloadMedia(_ job: CloudMediaJob) async throws -> Data {
    guard job.status == "succeeded", ["image", "video"].contains(job.kind), !job.id.isEmpty,
      job.id.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }),
      job.asset_url == "/api/studio/jobs/\(job.id)/asset"
    else { throw CloudError(status: 0, message: "生成结果尚不可用") }
    let (data, response) = try await sendMediaAsset(job.asset_url!)
    let valid =
      job.kind == "image"
      ? response.mimeType == "image/jpeg" && data.starts(with: [0xff, 0xd8, 0xff])
      : response.mimeType == "video/mp4" && data.count > 12
        && data.subdata(in: 4..<8) == Data("ftyp".utf8)
    guard valid, data.count <= 60_000_000 else {
      throw CloudError(status: 0, message: "生成结果格式无效，请刷新后重试")
    }
    return data
  }
}

/// This state belongs to one presented lesson and one backend/account. Jobs live
/// on the server and can be recovered after dismissal/relaunch without a new POST.
@MainActor @Observable final class CloudMediaModel {
  let api: CloudAPI
  let context: LearningMediaContext
  let kind: LearningMediaKind
  var capabilities: CloudMediaCapabilities?
  var jobs: [CloudMediaJob] = []
  var job: CloudMediaJob?
  var loading = false
  var submitting = false
  var downloading = false
  var error: String?
  var imageData: Data?
  var videoURL: URL?
  var refreshID = 0
  private var requestIDs: [LearningMediaKind: String] = [:]

  init(api: CloudAPI, context: LearningMediaContext) {
    self.api = api
    self.context = context
    kind = context.kind
  }

  var history: [CloudMediaJob] {
    jobs.filter { $0.book_id == context.bookID && $0.kind == kind.rawValue }
  }

  func load() async {
    guard !loading else { return }
    loading = true
    error = nil
    defer { loading = false }
    do {
      capabilities = try await api.request("/api/studio/capabilities")
      let book =
        context.bookID.addingPercentEncoding(
          withAllowedCharacters: .urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&=+#")))
        ?? ""
      let response: CloudMediaJobs = try await api.request("/api/studio/jobs?book_id=\(book)")
      try Task.checkCancellation()
      jobs = response.items
      if job == nil,
        let previous = history.first(where: {
          $0.excerpt == context.excerpt && $0.chapter_title == context.chapterTitle
        })
      {
        select(previous)
      }
    } catch is CancellationError {} catch { self.error = error.localizedDescription }
  }

  func select(_ value: CloudMediaJob) {
    guard value.book_id == context.bookID, value.kind == kind.rawValue else { return }
    clearAsset()
    error = nil
    job = value
    refreshID += 1
  }

  func submit() async {
    guard !submitting, capabilities?.available(kind) == true,
      context.excerpt.count >= 4, job == nil || job?.status == "failed"
    else { return }
    submitting = true
    error = nil
    defer { submitting = false }
    // A failed HTTP response may hide a successfully queued job. Keep this ID
    // for explicit transport retries. Only a confirmed failed job permits a new ID.
    if job?.status == "failed" {
      requestIDs[kind] = nil
      job = nil
      clearAsset()
    }
    let requestID = requestIDs[kind] ?? UUID().uuidString
    requestIDs[kind] = requestID
    do {
      let value: CloudMediaJob = try await api.request(
        "/api/studio/media", method: "POST",
        body: context.body(kind: kind, requestID: requestID))
      try Task.checkCancellation()
      guard value.book_id == context.bookID, value.kind == kind.rawValue else {
        throw CloudError(status: 0, message: "生成任务与当前教材不匹配")
      }
      jobs.removeAll { $0.id == value.id }
      jobs.insert(value, at: 0)
      select(value)
    } catch is CancellationError {} catch { self.error = error.localizedDescription }
  }

  func follow(pollInterval: Duration = .seconds(3)) async {
    guard let initial = job else { return }
    do {
      while job?.id == initial.id {
        try Task.checkCancellation()
        guard let current = job else { return }
        if !current.isRunning {
          if current.status == "succeeded" { try await download(current) }
          return
        }
        try await Task.sleep(for: pollInterval)
        let next: CloudMediaJob = try await api.request("/api/studio/jobs/\(initial.id)")
        try Task.checkCancellation()
        guard job?.id == initial.id, next.id == initial.id,
          next.book_id == context.bookID, next.kind == kind.rawValue
        else { return }
        job = next
        if let index = jobs.firstIndex(where: { $0.id == next.id }) { jobs[index] = next }
      }
    } catch is CancellationError {} catch { self.error = error.localizedDescription }
  }

  func refresh() async {
    guard let current = job else {
      await load()
      return
    }
    error = nil
    do {
      let next: CloudMediaJob = try await api.request("/api/studio/jobs/\(current.id)")
      try Task.checkCancellation()
      guard job?.id == current.id else { return }
      select(next)
    } catch is CancellationError {} catch { self.error = error.localizedDescription }
  }

  private func download(_ value: CloudMediaJob) async throws {
    guard imageData == nil, videoURL == nil else { return }
    downloading = true
    defer { downloading = false }
    let bytes = try await api.downloadMedia(value)
    try Task.checkCancellation()
    guard job?.id == value.id else { return }
    if value.kind == "image" {
      imageData = bytes
    } else {
      let url = FileManager.default.temporaryDirectory.appendingPathComponent(
        "learning-media-\(UUID().uuidString).mp4")
      try bytes.write(to: url, options: [.atomic, .completeFileProtection])
      videoURL = url
    }
  }

  func clearAsset() {
    imageData = nil
    if let videoURL { try? FileManager.default.removeItem(at: videoURL) }
    videoURL = nil
  }
}
