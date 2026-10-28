import CryptoKit
import Foundation

struct CloudBookPreparationJob: Codable, Sendable {
  enum Status: String, Codable, Sendable { case queued, running, succeeded, failed, cancelled }
  var book_id: String
  var status: Status
  var stage: String
  var error: String?
  var updated: Double
  var next_step: Int
  var serverMessage: String? = nil
  var diagnostics_completed: Int? = nil
  var diagnostics_total: Int? = nil
  enum CodingKeys: String, CodingKey {
    case book_id, status, stage, error, updated, next_step
    case structure_ready, rag_ready, diagnostics_ready, diagnostics_completed, diagnostics_total
    case serverMessage = "message"
  }
  // Enriched only from successful read-only readiness/diagnostic responses.
  var structure_ready: Bool? = nil
  var rag_ready: Bool? = nil
  var diagnostics_ready: Bool? = nil

  var isPending: Bool { status == .queued || status == .running }
  var progress: Double { min(1, max(0, Double(next_step) / 3)) }
  var marker: String { "\(stage)|\(next_step)|\(updated.bitPattern)" }
  var message: String {
    if status == .failed {
      return "课程内容暂未准备完成，已保留进度，可以继续整理。"
    }
    if status == .cancelled { return "教材准备已暂停，已完成的内容会保留。" }
    if status == .succeeded { return "教材内容已准备完成。" }
    if stage == "diagnostics" {
      let detail = "正在准备学习诊断。"
      if let completed = diagnostics_completed, let total = diagnostics_total, total > 0 {
        return "诊断题已完成 \(completed)／\(total) 题 · \(detail)"
      }
      return detail
    }
    if status == .queued { return "教材准备已排队，可以稍后继续查看。" }
    switch stage {
    case "indexing": return "正在整理教材内容。"
    case "structuring": return "正在整理目录与章节。"
    case "diagnostics": return "正在准备学习诊断，已完成的进度会保留。"
    default: return "正在准备教材。"
    }
  }
}

struct CloudBookPreparationReceipt: Codable, Sendable {
  var bookID: String
  var creationAttempted = false
  var retriedFailure: String? = nil
  var refreshedSuccess: String? = nil
  var submissionEpoch: Int? = nil
  var retryRequestID: String? = nil
  var lastOutcome: String? = nil
}

/// The stable owner/book queue is the authority. Local receipts record commands,
/// never fabricated job progress. Cancellation stops observation, not server work.
actor BookPreparationService {
  let api: CloudAPI
  let repository: LocalBookCourseRepository
  let accountKey: String
  private var commands: [String: Task<CloudBookPreparationJob, Error>] = [:]

  init(api: CloudAPI, repository: LocalBookCourseRepository, accountKey: String) {
    self.api = api
    self.repository = repository
    self.accountKey = accountKey
  }

  func receiptKey(bookID: String) throws -> String {
    let data = try JSONEncoder().encode([api.baseURL.absoluteString, accountKey, bookID])
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
  }

  private func path(_ bookID: String) throws -> String {
    guard !accountKey.isEmpty, !bookID.isEmpty, bookID.count <= 200,
      let encoded = bookID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) else {
      throw CloudError(status: 409, message: "请先登录并选择已上传的教材。")
    }
    return "/api/books/\(encoded)"
  }

  private func validate(_ job: CloudBookPreparationJob, bookID: String) throws {
    guard job.book_id == bookID, job.updated.isFinite, job.updated >= 0,
      (0...3).contains(job.next_step),
      ["queued", "indexing", "structuring", "diagnostics", "ready"].contains(job.stage),
      job.status != .succeeded || (job.stage == "ready" && job.next_step == 3) else {
      throw CloudError(status: 409, message: "教材准备状态不匹配，请检查服务版本后重新查询原任务。")
    }
  }

  private func get(bookID: String) async throws -> CloudBookPreparationJob {
    try Task.checkCancellation()
    let job: CloudBookPreparationJob = try await api.request(try path(bookID) + "/prepare")
    try validate(job, bookID: bookID)
    return job
  }

  /// Strictly read-only recovery for a course card; never creates or refreshes a job.
  func observe(bookID: String) async throws -> CloudBookPreparationJob {
    let job = try await get(bookID: bookID)
    if job.status == .succeeded {
      guard let verified = try await verifiedSuccess(job, bookID: bookID) else {
        throw CloudError(status: 409, message: "教材准备尚未全部就绪，请继续查询原任务。")
      }
      return verified
    }
    return job
  }

  private enum Command: Sendable { case create(explicitRecovery: Bool), retry(String), refresh(String) }

  private func postOnce(bookID: String, command: Command) async throws -> CloudBookPreparationJob {
    let key = try receiptKey(bookID: bookID)
    // Coalesce reentrant callers on this service; a second observer cannot issue
    // the same command while the first repository write or HTTP request awaits.
    if let active = commands[key] { return try await active.value }
    try Task.checkCancellation()
    let task = Task { try await self.performPost(bookID: bookID, key: key, command: command) }
    commands[key] = task
    defer { commands[key] = nil }
    let job = try await task.value
    try Task.checkCancellation()
    return job
  }

  private func performPost(bookID: String, key: String, command: Command) async throws -> CloudBookPreparationJob {
    var receipt = try await repository.loadBookPreparationReceipt(key)
      ?? CloudBookPreparationReceipt(bookID: bookID)
    guard receipt.bookID == bookID else {
      throw CloudError(status: 409, message: "本机保存的教材任务归属不匹配，原记录已保留。")
    }
    let suffix: String
    switch command {
    case .create(let explicitRecovery):
      // Only a fresh GET404 plus a new user recovery action may resend an
      // uncertain create. The server's book_id unique key coalesces this POST;
      // retry=false cannot restart an existing failed job.
      guard !receipt.creationAttempted || explicitRecovery else { throw unresolvedSubmission() }
      receipt.creationAttempted = true
      suffix = "?retry=false"
    case .retry(let marker):
      // This command is reached only once per explicit user action, after GET
      // confirms a failed row. Observation and restart never infer retry=true.
      receipt.retriedFailure = marker
      let retryRequestID = UUID().uuidString
      receipt.retryRequestID = retryRequestID
      suffix = "?retry=true&retry_request_id=\(retryRequestID)"
    case .refresh(let marker):
      guard receipt.refreshedSuccess != marker else {
        throw CloudError(status: 409, message: "诊断内容仍未就绪，已保留原任务，请检查服务后继续查询。")
      }
      receipt.refreshedSuccess = marker
      suffix = "?retry=false"
    }
    receipt.submissionEpoch = (receipt.submissionEpoch ?? 0) + 1
    receipt.lastOutcome = "submitting"
    // A failed durable write prevents the command; no supplier request is sent.
    try await repository.saveBookPreparationReceipt(receipt, key: key)
    try Task.checkCancellation()
    do {
      let job: CloudBookPreparationJob = try await api.request(
        try path(bookID) + "/prepare" + suffix, method: "POST")
      try validate(job, bookID: bookID)
      receipt.lastOutcome = "accepted"
      try await repository.saveBookPreparationReceipt(receipt, key: key)
      return job
    } catch let error as CloudError where error.transportFailure {
      // A reply can be lost after enqueue. Only GET may reconcile that outcome.
      receipt.lastOutcome = "unknown"
      try await repository.saveBookPreparationReceipt(receipt, key: key)
      do { return try await get(bookID: bookID) }
      catch let missing as CloudError where missing.status == 404 { throw unresolvedSubmission() }
    } catch let error as CloudError {
      // These responses reject admission. Keep an auditable outcome but release
      // the intent so a later explicit action is not permanently locked out.
      if [401, 403, 409, 422, 429, 503].contains(error.status) {
        receipt.lastOutcome = "rejected"
        switch command {
        case .create: receipt.creationAttempted = false
        case .retry: receipt.retriedFailure = nil
        case .refresh: receipt.refreshedSuccess = nil
        }
        try await repository.saveBookPreparationReceipt(receipt, key: key)
      }
      throw error
    }
  }

  private func unresolvedSubmission() -> CloudError {
    CloudError(status: 0, message: "教材准备的提交结果尚未确认，原书和请求已保留，请稍后继续查询。", transportFailure: true)
  }

  private struct Readiness: Decodable, Sendable {
    var structure_ready: Bool
    var rag_ready: Bool
  }
  private struct DiagnosticSummary: Decodable, Sendable {
    var book_id: String
    var item_count: Int
    var chapter_count: Int
    var choice_count: Int
    var explanation_count: Int
  }

  /// A historical succeeded queue row alone cannot prove a current reviewed bank.
  private func verifiedSuccess(_ job: CloudBookPreparationJob, bookID: String) async throws -> CloudBookPreparationJob? {
    let encoded = try path(bookID).dropFirst("/api/books/".count)
    let ready: Readiness = try await api.request("/api/native/books/\(encoded)/readiness")
    let summary: DiagnosticSummary
    do { summary = try await api.request(try path(bookID) + "/diagnostics") }
    catch let missing as CloudError where missing.status == 404 { return nil }
    guard ready.structure_ready, ready.rag_ready, summary.book_id == bookID,
      summary.item_count > 0, summary.chapter_count > 0,
      summary.choice_count == summary.item_count, summary.explanation_count == 0 else {
      throw CloudError(status: 409, message: "课程内容仍在准备中，已保留进度，请稍后继续。")
    }
    var verified = job
    verified.structure_ready = true
    verified.rag_ready = true
    verified.diagnostics_ready = true
    return verified
  }

  func waitForBook(bookID: String, retry: Bool = false, pollInterval: Duration = .seconds(2),
    observationTimeout: Duration? = nil,
    onUpdate: @Sendable (CloudBookPreparationJob) async throws -> Void) async throws -> CloudBookPreparationJob {
    _ = try path(bookID)
    let clock = ContinuousClock()
    let deadline = observationTimeout.map { clock.now.advanced(by: $0) }
    var job: CloudBookPreparationJob
    do { job = try await get(bookID: bookID) }
    catch let missing as CloudError where missing.status == 404 {
      job = try await postOnce(bookID: bookID, command: .create(explicitRecovery: retry))
    }
    if retry && (job.status == .failed || job.status == .cancelled) {
      job = try await postOnce(bookID: bookID, command: .retry(job.marker))
    }
    var refreshed = false
    while true {
      try Task.checkCancellation()
      guard deadline.map({ clock.now < $0 }) ?? true else {
        throw CloudError(status: 408, message: "教材仍在后台处理，已保留进度，请稍后继续查看。", transportFailure: true)
      }
      switch job.status {
      case .cancelled:
        try await onUpdate(job)
        throw CancellationError()
      case .failed:
        try await onUpdate(job)
        throw CloudError(status: 409, message: job.message)
      case .succeeded:
        if let verified = try await verifiedSuccess(job, bookID: bookID) {
          try Task.checkCancellation()
          try await onUpdate(verified)
          return verified
        }
        guard !refreshed else {
          throw CloudError(status: 409, message: "诊断内容仍未就绪，已保留原任务，请检查服务后继续查询。")
        }
        refreshed = true
        // Refresh a protocol-stale success once in this observation and once
        // durably for its exact server revision.
        job = try await postOnce(bookID: bookID, command: .refresh(job.marker))
        continue
      case .queued, .running:
        try await onUpdate(job)
      }
      guard deadline.map({ clock.now < $0 }) ?? true else {
        throw CloudError(status: 408, message: "教材仍在后台处理，已保留进度，请稍后继续查看。", transportFailure: true)
      }
      try await Task.sleep(for: pollInterval)
      do { job = try await get(bookID: bookID) }
      catch let error as CloudError where error.transportFailure {
        job.serverMessage = "连接暂时中断，正在重新查询后台任务。"
        try await onUpdate(job)
        try await Task.sleep(for: .seconds(10))
      }
    }
  }
}
