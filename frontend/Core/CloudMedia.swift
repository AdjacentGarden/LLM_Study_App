import CryptoKit
import Foundation
import Observation
import UIKit

// The lesson context and all wire models come from the cloud implementation.
enum LearningMediaKind: String, Codable, Sendable { case image, video }

extension LessonMediaRequest {
  init(lesson: Lesson, section: LessonBlock?, asset: BookAsset? = nil, kind: LearningMediaKind) {
    let text = section?.content ?? lesson.summary
    let excerpt = ([asset?.caption, text].compactMap { $0 }.filter { !$0.isEmpty }).joined(separator: "\n")
    let citations = section?.citations ?? lesson.blocks.flatMap(\.citations)
    let pages = citations.flatMap {
      Array($0.page_start...max($0.page_start, min($0.page_end, $0.page_start + 29)))
    }.filter { (1...100000).contains($0) }
    self.init(kind: kind.rawValue, bookID: lesson.book_id, chapterID: lesson.chapter_id,
      chapterTitle: lesson.title, title: asset?.caption ?? lesson.title,
      excerpt: String(excerpt.prefix(3000)),
      pages: Array(Set(pages.isEmpty ? [asset?.page ?? lesson.page_start].filter { $0 > 0 } : pages)).sorted().prefix(30).map { $0 },
      lessonID: lesson.id, courseID: "")
  }
}

extension StudioCapabilities {
  func available(_ kind: LearningMediaKind) -> Bool { kind == .image ? image : video }
}

extension StudioJob: Identifiable {
  var isRunning: Bool { ["queued", "planning", "submitting", "polling", "reviewing"].contains(status) }
  var statusText: String {
    switch status {
    case "queued": "正在排队"
    case "planning": "正在设计讲解"
    case "submitting": "正在提交生成"
    case "polling": "正在生成短片"
    case "reviewing": "正在检查内容"
    case "succeeded": "已生成"
    case "failed", "rejected": "生成未完成"
    case "cancelled": "任务已取消"
    case "uncertain": "结果待确认"
    default: "请刷新任务状态"
    }
  }
}

extension CloudAPI {
  func downloadMedia(_ job: StudioJob) async throws -> Data {
    guard job.status == "succeeded", ["image", "video"].contains(job.kind),
      LessonMediaJobContract.validJobID(job.id), job.asset_url == "/api/studio/jobs/\(job.id)/asset"
    else { throw CloudError(status: 422, message: "生成结果尚不可用") }
    return try await downloadStudioAsset(jobID: job.id, kind: job.kind)
  }
}

/// Presentation state around the cloud service and durable receipt contract.
/// Only explicit submission creates a job; reopening and uncertain replies use GET.
@MainActor @Observable final class CloudMediaModel {
  let api: CloudAPI?
  let context: LessonMediaRequest
  var kind: LearningMediaKind { LearningMediaKind(rawValue: context.kind) ?? .video }
  var capabilities: StudioCapabilities?
  var jobs: [StudioJob] = []
  var job: StudioJob?
  private(set) var receipt: LessonMediaReceipt?
  var loading = false
  var submitting = false
  var downloading = false
  var error: String?
  var authenticationExpired = false
  var imageData: Data?
  var videoURL: URL?
  var refreshID = 0
  private var accountID: String?
  private var invalidated = false
  private var receiptURL: URL?
  private var cacheURL: URL?
  private var cacheKey: String?
  private let receiptRoot: URL
  private let cacheRoot: URL

  init(api: CloudAPI?, request: LessonMediaRequest, storageRoot: URL? = nil) {
    self.api = api
    context = request
    let files = FileManager.default
    receiptRoot = storageRoot?.appendingPathComponent("receipts", isDirectory: true)
      ?? files.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("CloudPath/MediaReceipts", isDirectory: true)
    cacheRoot = storageRoot?.appendingPathComponent("artifacts", isDirectory: true)
      ?? files.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("CloudPath/MediaArtifacts", isDirectory: true)
  }

  var history: [StudioJob] {
    jobs.filter { $0.book_id == context.bookID && $0.kind == context.kind }
  }
  var canSubmit: Bool {
    accountID != nil && !invalidated && !loading && !submitting
      && capabilities?.available(kind) == true && context.excerpt.count >= 4
      && (receipt == nil || receipt?.permitsNewGeneration == true)
  }

  func load() async {
    guard !loading, !invalidated else { return }
    loading = true
    error = nil
    defer { loading = false; refreshID += 1 }
    do {
      guard let api, ["image", "video"].contains(context.kind) else {
        throw ContentError.invalid("请先配置并连接教材服务器。")
      }
      guard !context.bookID.hasPrefix("book_local_"), context.excerpt.count >= 4 else {
        throw ContentError.invalid("请先将教材上传到服务器，并选择至少四个字的教材原文。原文件仍可阅读。")
      }
      guard let identity = await api.verifiedAccount(), identity.account != nil else {
        throw ContentError.invalid("请先登录已配置的服务器账号，再生成辅助素材。")
      }
      accountID = identity.user_id
      let scope = [api.baseURL.absoluteString, identity.user_id, context.id]
      let key = SHA256.hash(data: try JSONEncoder().encode(scope)).map { String(format: "%02x", $0) }.joined()
      try FileManager.default.createDirectory(at: receiptRoot, withIntermediateDirectories: true)
      try FileManager.default.createDirectory(at: cacheRoot, withIntermediateDirectories: true)
      receiptURL = receiptRoot.appendingPathComponent(key + ".json")
      cacheKey = key
      if let receiptURL, FileManager.default.fileExists(atPath: receiptURL.path) {
        receipt = try JSONDecoder().decode(LessonMediaReceipt.self, from: Data(contentsOf: receiptURL))
        try receipt?.validate()
        updateCacheURL()
        if receipt?.state == "succeeded", let cacheURL,
          FileManager.default.fileExists(atPath: cacheURL.path) {
          let bytes = try Data(contentsOf: cacheURL, options: .mappedIfSafe)
          if kind == .image { imageData = bytes } else { videoURL = cacheURL }
        }
      }
      let service = StudioService(api: api)
      capabilities = try await service.capabilities()
      try await requireIdentity()
      jobs = try await service.mediaJobs(bookID: context.bookID)
      try await requireIdentity()
      if receipt == nil, let previous = history.first(where: {
        ($0.excerpt == context.excerpt && $0.chapter_title == context.chapterTitle)
          || (context.legacyExcerpt != nil && $0.excerpt == context.legacyExcerpt
            && $0.chapter_title == context.legacyChapterTitle)
      }) {
        try selectJob(previous)
      }
      refreshID += 1
      if receipt == nil, capabilities?.available(kind) != true {
        error = capabilities?.media_notice ?? "媒体生成服务暂未就绪，请检查连接与模型配置后重试。"
      }
    } catch is CancellationError { } catch { await handleFailure(error) }
  }

  func select(_ value: StudioJob) {
    guard !loading, !submitting, !invalidated else { return }
    // An unresolved POST remains bound to its original request, even when browsing history.
    guard receipt?.submissionUncertain != true,
      !(["queued", "planning", "submitting", "polling", "reviewing", "uncertain"].contains(receipt?.state ?? "")
        && value.id != receipt?.jobID) else {
      error = "当前任务尚未完成，请先刷新核对原任务。"
      return
    }
    do { try selectJob(value) } catch { self.error = error.localizedDescription }
  }

  private func selectJob(_ value: StudioJob) throws {
    var next = LessonMediaReceipt(requestID: "media_" + UUID().uuidString.replacingOccurrences(of: "-", with: ""),
      state: "queued", submissionUncertain: false)
    try next.accept(value, bookID: context.bookID, kind: context.kind)
    clearAsset()
    receipt = next
    updateCacheURL()
    job = value
    error = nil
    try saveReceipt()
    refreshID += 1
  }

  func submit() async {
    guard canSubmit, let api else { return }
    submitting = true
    error = nil
    defer { submitting = false }
    var sent = false
    do {
      try await requireIdentity()
      let service = StudioService(api: api)
      capabilities = try await service.capabilities()
      try await requireIdentity()
      guard capabilities?.available(kind) == true else {
        throw ContentError.invalid("媒体生成服务暂不可用，未提交生成。")
      }
      receipt = LessonMediaReceipt(requestID: "media_" + UUID().uuidString.replacingOccurrences(of: "-", with: ""),
        state: "submitting", submissionUncertain: true)
      job = nil
      clearAsset()
      try saveReceipt()
      sent = true
      let accepted = try await service.submitMedia(kind: context.kind, bookID: context.bookID,
        chapterID: context.chapterID, chapterTitle: context.chapterTitle, excerpt: context.excerpt,
        pages: context.pages, requestID: receipt!.requestID)
      try await requireIdentity()
      try accept(accepted)
      refreshID += 1
    } catch is CancellationError { } catch {
      if !sent || ([400, 401, 403, 404, 409, 422, 429].contains((error as? CloudError)?.status ?? 0)
        && receipt?.jobID == nil) {
        receipt?.submissionUncertain = false
        receipt?.state = "failed"
        try? saveReceipt()
      }
      await handleFailure(error)
      if !invalidated, receipt?.submissionUncertain == true {
        self.error = "提交结果尚未确认，已保留请求编号。请核对原任务，避免重复生成。"
      }
    }
  }

  func follow(pollInterval: Duration = .seconds(2)) async {
    guard let api, receipt != nil, !loading, !submitting, !invalidated else { return }
    let generation = refreshID
    do {
      let service = StudioService(api: api)
      try await requireIdentity()
      if let receipt, receipt.jobID == nil {
        guard let found = try await service.job(requestID: receipt.requestID, bookID: context.bookID) else {
          error = "服务器尚未查到已确认任务。原请求编号已保留，请稍后继续核对。"
          return
        }
        try await requireIdentity()
        guard generation == refreshID else { return }
        try accept(found)
      }
      guard let jobID = receipt?.jobID else { return }
      for _ in 0..<300 {
        try await requireIdentity()
        guard generation == refreshID else { return }
        let latest = try await service.job(id: jobID)
        try await requireIdentity()
        guard generation == refreshID else { return }
        try accept(latest)
        if latest.status == "succeeded" {
          try await download(latest, generation: generation)
          return
        }
        if !latest.isRunning {
          error = latest.error.flatMap { $0.isEmpty ? nil : $0 } ?? "服务器未能确认生成完成，请核对原任务。"
          return
        }
        try await Task.sleep(for: pollInterval)
      }
      error = "任务仍在服务器处理中，可以关闭页面，稍后刷新原任务。"
    } catch is CancellationError { } catch { await handleFailure(error) }
  }

  func refresh() async {
    if accountID == nil { await load(); return }
    error = nil
    refreshID += 1
  }

  private func accept(_ value: StudioJob) throws {
    guard var next = receipt else { throw ContentError.storage("缺少原请求回执，已停止显示任务。") }
    try next.accept(value, bookID: context.bookID, kind: context.kind)
    receipt = next
    updateCacheURL()
    job = value
    if let index = jobs.firstIndex(where: { $0.id == value.id }) { jobs[index] = value }
    else { jobs.insert(value, at: 0) }
    try saveReceipt()
  }

  private func updateCacheURL() {
    guard let cacheKey, let jobID = receipt?.jobID else { cacheURL = nil; return }
    cacheURL = cacheRoot.appendingPathComponent(cacheKey + "-" + jobID + (kind == .image ? ".jpg" : ".mp4"))
  }

  private func saveReceipt() throws {
    guard let receipt, let receiptURL else { throw ContentError.storage("无法保存媒体任务回执。") }
    try JSONEncoder().encode(receipt).write(to: receiptURL,
      options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
  }

  private func requireIdentity() async throws {
    try Task.checkCancellation()
    guard !invalidated, let api, let accountID, await api.verifiedAccount()?.user_id == accountID else {
      invalidateIdentity()
      throw ContentError.invalid("服务或账号已变化，请关闭此页面后重新打开。")
    }
  }

  private func handleFailure(_ failure: Error) async {
    if let authentication = failure as? CloudError, authentication.status == 401 {
      authenticationExpired = true
      invalidateIdentity()
      await api?.clearCredentials()
    }
    error = failure.localizedDescription
  }

  func invalidateIdentity() {
    invalidated = true
    refreshID += 1
    capabilities = nil
    jobs = []
    job = nil
    clearAsset()
    error = "服务或账号已变化，请关闭此页面后重新打开。"
  }

  private func download(_ value: StudioJob, generation: Int) async throws {
    guard imageData == nil, videoURL == nil, let api, let cacheURL else { return }
    downloading = true
    defer { downloading = false }
    let bytes = try await api.downloadMedia(value)
    try await requireIdentity()
    if kind == .image, UIImage(data: bytes) == nil {
      throw ContentError.invalid("服务器返回的图片无法读取，请刷新原任务后重试下载。")
    }
    guard generation == refreshID, job?.id == value.id else { return }
    try bytes.write(to: cacheURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    if kind == .image { imageData = bytes } else { videoURL = cacheURL }
  }

  func clearAsset() {
    imageData = nil
    videoURL = nil
  }
}
