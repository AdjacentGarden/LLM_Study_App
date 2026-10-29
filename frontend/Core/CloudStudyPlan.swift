import Foundation
import Observation

struct CloudStudyPlanTask: Codable, Sendable, Identifiable, Hashable {
  var task_id: String
  var resource_id: String
  var book_id: String
  var chapter_id: String?
  var lesson_id: String?
  var point_id: String?
  var practice_item_id: String?
  var card_id: String?
  var title: String
  var kind: String
  var minutes: Int
  var status: String
  var requires: [String]
  var source_task_id: String?
  var id: String { task_id }
  var isDone: Bool { ["done", "completed"].contains(status) }
  var kindLabel: String {
    switch kind { case "practice": "练习"; case "recall": "复习"; default: "学习" }
  }
}

struct CloudStudyPlan: Codable, Sendable {
  var plan_id: String
  var day: String
  var budget_minutes: Int
  var estimated_minutes: Int
  var needs_confirmation: Bool
  var confirmed: Bool
  var revision: Int
  var tasks: [CloudStudyPlanTask]
  var queue: [CloudStudyPlanTask]
  var preferences_changed: Bool
  var remaining_task_count: Int
  var completedCount: Int { tasks.filter(\.isDone).count }
  var progress: Double { tasks.isEmpty ? 0 : Double(completedCount) / Double(tasks.count) }
}

struct CloudPlanCompletion: Codable, Sendable, Equatable {
  var eventID = UUID().uuidString
  var workspaceID: String
  var day: String
  var taskID: String
}

struct CloudPlanFocus: Sendable {
  var workspaceID: String
  var day: String
  var task: CloudStudyPlanTask
}

@MainActor @Observable final class DailyStudyPlanState {
  var plans: [String: CloudStudyPlan] = [:]
  var pending: [CloudPlanCompletion] = []
  var errors: [String: String] = [:]
  var requests: [String: UUID] = [:]
  var confirming: Set<String> = []
  var focus: CloudPlanFocus?

  nonisolated static func day(_ date: Date = .now) -> String {
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = .current
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter.string(from: date)
  }
  nonisolated static func key(_ workspaceID: String, _ day: String) -> String { workspaceID + ":" + day }
  func reset() {
    plans = [:]; pending = []; errors = [:]; requests = [:]; confirming = []; focus = nil
  }
}

extension AppStore {
  var usesCloudDailyPlan: Bool {
    cloud != nil && selectedCourseID.map { id in cloudState.workspaces.contains { $0.id == id } } == true
  }

  func dailyStudyPlan(_ day: String = DailyStudyPlanState.day()) -> CloudStudyPlan? {
    selectedCourseID.flatMap { dailyPlanState.plans[DailyStudyPlanState.key($0, day)] }
  }

  var dailyStudyPlanLoadKey: String {
    // Course content and preferences can change while this screen remains mounted.
    let workspace = cloudState.workspaces.first { $0.id == selectedCourseID }
    let content = workspace?.resources.map {
      "\($0.id):\($0.content_key ?? ""):\($0.courses.map(\.course_id).joined(separator: ","))"
    }.joined(separator: "|") ?? ""
    return "\(cloudState.epoch):\(selectedCourseID ?? ""):\(DailyStudyPlanState.day()):\(preferences?.dailyTime ?? ""):\(content)"
  }

  func loadDailyStudyPlan(day: String = DailyStudyPlanState.day(), workspaceID: String? = nil) async {
    guard let cloud, cloudState.authenticated, let wid = workspaceID ?? selectedCourseID,
      cloudState.workspaces.contains(where: { $0.id == wid }) else { return }
    let key = DailyStudyPlanState.key(wid, day)
    guard dailyPlanState.requests[key] == nil else { return }
    let requestID = UUID(), epoch = cloudState.epoch
    dailyPlanState.requests[key] = requestID
    defer {
      if dailyPlanState.requests[key] == requestID { dailyPlanState.requests[key] = nil }
    }
    do {
      let plan: CloudStudyPlan = try await cloud.request("/api/workspaces/\(wid)/study-plan?day=\(day)")
      guard epoch == cloudState.epoch, dailyPlanState.requests[key] == requestID,
        !Task.isCancelled else { return }
      guard plan.day == day else { throw CloudError(status: 409, message: "计划日期不匹配，请刷新后重试") }
      dailyPlanState.plans[key] = plan
      dailyPlanState.errors[key] = nil
      saveCloudCacheOnly()
      startCloudSync()
    } catch {
      guard epoch == cloudState.epoch, dailyPlanState.requests[key] == requestID,
        !Task.isCancelled else { return }
      dailyPlanState.errors[key] = (error as? CloudError)?.status == 404
        ? "学习计划暂不可用，请确认服务已更新并重新打开课程。" : error.localizedDescription
      if (error as? CloudError)?.status == 401 { handleCloudError(error) }
    }
  }

  func confirmDailyStudyPlan(day: String) async {
    guard let cloud, let wid = selectedCourseID, let plan = dailyStudyPlan(day) else { return }
    let key = DailyStudyPlanState.key(wid, day), epoch = cloudState.epoch
    guard !dailyPlanState.confirming.contains(key) else { return }
    dailyPlanState.confirming.insert(key)
    defer { dailyPlanState.confirming.remove(key) }
    do {
      let saved: CloudStudyPlan = try await cloud.request(
        "/api/workspaces/\(wid)/study-plan/confirm", method: "POST", body: [
          "day": .string(day), "plan_id": .string(plan.plan_id),
          "task_ids": .array(plan.tasks.map { .string($0.id) }), "revision": .number(Double(plan.revision)),
        ])
      guard epoch == cloudState.epoch else { return }
      guard saved.day == day else { throw CloudError(status: 409, message: "计划日期不匹配，请刷新后重试") }
      dailyPlanState.requests[key] = nil
      dailyPlanState.plans[key] = saved
      dailyPlanState.errors[key] = nil
      saveCloudCacheOnly()
    } catch {
      guard epoch == cloudState.epoch else { return }
      if (error as? CloudError)?.status == 409 { await loadDailyStudyPlan(day: day, workspaceID: wid) }
      dailyPlanState.errors[key] = error.localizedDescription
      if (error as? CloudError)?.status == 401 { handleCloudError(error) }
    }
  }

  func completeDailyStudyTask(_ task: CloudStudyPlanTask, day: String, workspaceID: String? = nil,
    waitForSync: Bool = true) async {
    guard cloudState.authenticated, let wid = workspaceID ?? selectedCourseID else { return }
    let key = DailyStudyPlanState.key(wid, day)
    let epoch = cloudState.epoch
    guard let plan = dailyPlanState.plans[key], let savedTask = plan.tasks.first(where: { $0.id == task.id }),
      !plan.needs_confirmation, !savedTask.isDone else { return }
    if !dailyPlanState.pending.contains(where: { $0.workspaceID == wid && $0.taskID == task.id }) {
      dailyPlanState.pending.append(CloudPlanCompletion(workspaceID: wid, day: day, taskID: task.id))
    }
    dailyPlanState.errors[key] = nil
    saveCloudCacheOnly()
    await persistTask?.value
    guard epoch == cloudState.epoch else { return }
    guard cloudState.cacheWriteError == nil else {
      dailyPlanState.errors[key] = "完成记录尚未保存，请重试。"
      return
    }
    startCloudSync()
    if waitForSync { await cloudState.syncTask?.value }
  }

  /// Called by the existing serialized sync worker, using durable event IDs on every retry.
  func syncDailyStudyCompletions(epoch: UUID) async throws {
    guard let cloud else { return }
    while let entry = dailyPlanState.pending.first {
      await persistTask?.value
      guard epoch == cloudState.epoch, !Task.isCancelled else { throw CancellationError() }
      guard cloudState.cacheWriteError == nil else { throw ContentError.storage("完成记录尚未保存，请重试。") }
      let key = DailyStudyPlanState.key(entry.workspaceID, entry.day)
      do {
        var safe = CharacterSet.urlPathAllowed
        safe.remove(charactersIn: "/?#%")
        guard let taskID = entry.taskID.addingPercentEncoding(withAllowedCharacters: safe) else { return }
        let result: CloudOK = try await cloud.request(
          "/api/workspaces/\(entry.workspaceID)/study-plan/tasks/\(taskID)/complete", method: "POST",
          body: ["event_id": .string(entry.eventID), "day": .string(entry.day)])
        guard epoch == cloudState.epoch else { throw CancellationError() }
        guard result.ok else { throw CloudError(status: 409, message: "完成状态尚未确认，请重试。") }
        dailyPlanState.pending.removeAll { $0.eventID == entry.eventID }
        // Prevent an older in-flight GET from reverting an acknowledged completion.
        dailyPlanState.requests[key] = nil
        for planKey in Array(dailyPlanState.plans.keys) where planKey.hasPrefix(entry.workspaceID + ":") {
          guard var plan = dailyPlanState.plans[planKey] else { continue }
          for index in plan.tasks.indices where plan.tasks[index].id == entry.taskID { plan.tasks[index].status = "done" }
          for index in plan.queue.indices where plan.queue[index].id == entry.taskID { plan.queue[index].status = "done" }
          dailyPlanState.plans[planKey] = plan
        }
        dailyPlanState.errors[key] = nil
        saveCloudCacheOnly()
        await persistTask?.value
        await loadDailyStudyPlan(day: entry.day, workspaceID: entry.workspaceID)
      } catch {
        if epoch == cloudState.epoch { dailyPlanState.errors[key] = error.localizedDescription }
        throw error
      }
    }
  }

  func openDailyStudyTask(_ task: CloudStudyPlanTask, day: String) {
    guard let wid = selectedCourseID,
      let resource = cloudState.workspaces.first(where: { $0.id == wid })?.resources.first(where: { $0.id == task.resource_id }),
      resource.book_id == task.book_id else { showToast("这项任务的课程资料暂不可用，请刷新计划。"); return }
    guard sources.contains(where: { $0.id == task.book_id }) else {
      showToast("这份课程资料尚未加载，请联网刷新后重试。")
      return
    }
    selectSource(task.book_id)
    guard let lesson = lessons.first(where: { $0.id == task.lesson_id || $0.chapter_id == task.chapter_id }),
      lesson.status == "ready" else { showToast("课程内容尚未准备好，请稍后刷新。"); return }
    dailyPlanState.focus = CloudPlanFocus(workspaceID: wid, day: day, task: task)
    selectedChapterID = task.chapter_id
    selectedLessonID = lesson.id
    switch task.kind {
    case "practice": go(.assignment)
    case "recall": go(.flashcards)
    default: openLesson(lesson)
    }
  }

  var activeDailyStudyTask: CloudPlanFocus? {
    guard let focus = dailyPlanState.focus, focus.workspaceID == selectedCourseID,
      focus.task.book_id == selectedBookID, focus.task.chapter_id == selectedChapterID else { return nil }
    return focus
  }

  func finishDailyStudyTask(_ focus: CloudPlanFocus?, kind: String, itemIDs: Set<String> = []) async {
    guard let focus, focus.task.kind == kind else { return }
    if kind == "practice", let item = focus.task.practice_item_id, !itemIDs.contains(item) { return }
    await completeDailyStudyTask(focus.task, day: focus.day, workspaceID: focus.workspaceID, waitForSync: false)
  }
}
