import CryptoKit
import Foundation

struct CloudCourseJob: Codable, Sendable {
  struct Progress: Codable, Sendable {
    var completed: Int
    var total: Int
  }
  enum Status: String, Codable, Sendable { case queued, running, succeeded, failed }
  var job_id: String
  var session_id: String
  var chapter_id: String
  var status: Status
  var stage: String
  var attempts: Int
  var error: String?
  var retryable: Bool
  var course: CloudCourse?
  var progress: Progress?

  var isPending: Bool { status == .queued || status == .running }
  private var progressMessage: String {
    guard let progress, progress.total > 0 else { return "" }
    return "已完成 \(progress.completed)/\(progress.total) 项内容。"
  }
  var message: String {
    switch status {
    case .queued:
      if stage == "refining" { progressMessage + "正在完善课程，可以稍后继续查看。" }
      else { "课程已排队，可以稍后继续查看。" }
    case .running:
      switch stage {
      case "planning": "正在整理本章重点，可以稍后继续查看。"
      case "writing": "正在编写课程，可以稍后继续查看。"
      case "reviewing": progressMessage + "正在检查教学内容，可以稍后继续查看。"
      case "refining": progressMessage + "正在完善教学内容，可以稍后继续查看。"
      default: "正在生成课程，可以稍后继续查看。"
      }
    case .succeeded: "课程已生成，正在同步学习内容。"
    case .failed: "本章课程暂未准备完成，已有内容和学习进度已保留。"
    }
  }
}

struct CloudCourseJobReceipt: Codable, Sendable {
  var requestID: String
  var jobID: String?
  var lastStatus: String?
  var lastError: String?
  var attempts: Int?
}

/// Durable intent precedes POST. Recovery reuses the same request with retry=false,
/// or only GETs a known job; inspecting a failed task never buys another attempt.
actor CourseJobService {
  let api: CloudAPI
  let repository: LocalBookCourseRepository
  let accountKey: String

  init(api: CloudAPI, repository: LocalBookCourseRepository, accountKey: String) {
    self.api = api
    self.repository = repository
    self.accountKey = accountKey
  }

  func receiptKey(sessionID: String, chapterID: String) throws -> String {
    let values = [api.baseURL.absoluteString, accountKey, sessionID, chapterID]
    let bytes = try JSONEncoder().encode(values)
    return SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
  }

  func current(sessionID: String, chapterID: String) async throws -> CloudCourseJob {
    guard !accountKey.isEmpty, !sessionID.isEmpty, !chapterID.isEmpty else {
      throw CloudError(status: 409, message: "请先登录并选择教材章节。")
    }
    let key = try receiptKey(sessionID: sessionID, chapterID: chapterID)
    var receipt: CloudCourseJobReceipt
    if let saved = try await repository.loadCourseJobReceipt(key) { receipt = saved }
    else {
      receipt = CloudCourseJobReceipt(requestID: "native_course_" + UUID().uuidString)
      // A failed local write must prevent the paid command entirely.
      try await repository.saveCourseJobReceipt(receipt, key: key)
    }
    return try await exchange(receipt, key: key, sessionID: sessionID, chapterID: chapterID)
  }

  /// This entry point requires a separate, explicit user action. Its one retry
  /// POST gets a new durable ID; a lost reply subsequently recovers with false.
  func restart(sessionID: String, chapterID: String) async throws -> CloudCourseJob {
    let previous = try await current(sessionID: sessionID, chapterID: chapterID)
    guard previous.status == .failed, previous.retryable else {
      throw CloudError(status: 409, message: previous.status == .failed ? previous.message : "原任务仍在处理，请继续查询。")
    }
    let key = try receiptKey(sessionID: sessionID, chapterID: chapterID)
    if let old = try await repository.loadCourseJobReceipt(key) {
      try await repository.saveCourseJobReceipt(old, key: key + ":history:" + old.requestID)
    }
    let receipt = CloudCourseJobReceipt(requestID: "native_course_" + UUID().uuidString)
    try await repository.saveCourseJobReceipt(receipt, key: key)
    return try await exchange(receipt, key: key, sessionID: sessionID, chapterID: chapterID, retry: true)
  }

  private func exchange(_ saved: CloudCourseJobReceipt, key: String, sessionID: String,
    chapterID: String, retry: Bool = false) async throws -> CloudCourseJob {
    var receipt = saved
    try Task.checkCancellation()
    let path = "/api/interviews/\(sessionID)/course-jobs"
    let job: CloudCourseJob
    if let id = receipt.jobID { job = try await api.request(path + "/" + id) }
    else {
      job = try await api.request(path, method: "POST", body: [
        "chapter_id": .string(chapterID), "request_id": .string(receipt.requestID),
        "retry": .bool(retry),
      ])
    }
    guard job.session_id == sessionID, job.chapter_id == chapterID, !job.job_id.isEmpty,
      receipt.jobID == nil || receipt.jobID == job.job_id else {
      throw CloudError(status: 409, message: "服务返回了其他课程的任务，原请求已保留，请重新查询。")
    }
    receipt.jobID = job.job_id
    receipt.lastStatus = job.status.rawValue
    receipt.lastError = job.error
    receipt.attempts = job.attempts
    try await repository.saveCourseJobReceipt(receipt, key: key)
    try Task.checkCancellation()
    return job
  }

  func waitForCourse(sessionID: String, chapterID: String, pollInterval: Duration = .seconds(2),
    observationTimeout: Duration = .seconds(900),
    onConnectionChange: (@Sendable (String?) async throws -> Void)? = nil,
    onUpdate: @Sendable (CloudCourseJob) async throws -> Void) async throws -> CloudCourse {
    let deadline = ContinuousClock.now + observationTimeout
    let key = try receiptKey(sessionID: sessionID, chapterID: chapterID)
    var trackedJobID = try await repository.loadCourseJobReceipt(key)?.jobID
    var connectionFailures = 0
    let retryDelays: [Duration] = [.seconds(2), .seconds(4), .seconds(8), .seconds(15)]
    while true {
      try Task.checkCancellation()
      guard ContinuousClock.now < deadline else {
        throw CloudError(status: 408, message: "课程仍在处理，原任务已保留；稍后可继续查询，不会自动重新生成。")
      }
      let job: CloudCourseJob
      do {
        if let id = trackedJobID {
          // Once observed, this loop can only GET that exact durable job.
          // A missing/replaced receipt must never turn recovery into creation.
          guard let receipt = try await repository.loadCourseJobReceipt(key), receipt.jobID == id else {
            throw CloudError(status: 409, message: "课程任务记录已更新，请重新打开本章继续查询。")
          }
          job = try await exchange(receipt, key: key, sessionID: sessionID, chapterID: chapterID)
        } else {
          job = try await current(sessionID: sessionID, chapterID: chapterID)
          trackedJobID = job.job_id
        }
      } catch let error as CloudError where trackedJobID != nil
        && (error.transportFailure || [408, 429, 500, 502, 503, 504].contains(error.status)) {
        try Task.checkCancellation()
        connectionFailures += 1
        if connectionFailures == 3 {
          try await onConnectionChange?("连接暂时不稳定，正在继续查询课程进度。")
        }
        try await Task.sleep(for: retryDelays[min(connectionFailures - 1, retryDelays.count - 1)])
        continue
      }
      if connectionFailures > 0 {
        try await onConnectionChange?(nil)
        connectionFailures = 0
      }
      try await onUpdate(job)
      switch job.status {
      case .queued, .running: try await Task.sleep(for: pollInterval)
      case .failed: throw CloudError(status: 409, message: job.message)
      case .succeeded:
        guard let course = job.course, course.chapter_id == chapterID,
          !course.course_id.isEmpty, !course.knowledge_points.isEmpty,
          !course.practice_items.isEmpty else {
          throw CloudError(status: 409, message: "任务已完成，但未返回完整课程，请重新查询原任务。")
        }
        return course
      }
    }
  }
}
