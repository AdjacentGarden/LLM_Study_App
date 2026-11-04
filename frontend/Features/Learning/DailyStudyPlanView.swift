import SwiftUI

struct DailyStudyPlanSummary: View {
  @Environment(AppStore.self) private var store
  @Environment(\.scenePhase) private var scenePhase
  var compact = false
  @State private var day = DailyStudyPlanState.day()

  var body: some View {
    StudyProgressCard(compact: compact, progress: store.courseLearningProgress,
      taskTitle: taskTitle, materialCount: store.currentCourse?.resourceIDs.count ?? 1,
      issue: planIssue) { store.go(.plan) }
    .task(id: store.dailyStudyPlanLoadKey + day) { await store.loadDailyStudyPlan(day: day) }
    .onChange(of: scenePhase) { _, phase in
      if phase == .active {
        day = DailyStudyPlanState.day()
        Task { await store.loadDailyStudyPlan(day: day) }
      }
    }
  }
  private var planIssue: String? {
    guard let wid = store.selectedCourseID else { return nil }
    return store.dailyPlanState.errors[DailyStudyPlanState.key(wid, day)]
  }

  private var taskTitle: String {
    guard let plan = store.dailyStudyPlan(day) else {
      return planIssue == nil ? "正在获取今日计划…" : "今日计划暂不可用"
    }
    return plan.tasks.first(where: { !$0.isDone })?.title
      ?? (plan.tasks.isEmpty ? "今日暂无安排" : "今日计划已完成")
  }
}

struct DailyStudyPlanView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.scenePhase) private var scenePhase
  @State private var selectedDate = Date.now
  @State private var observedToday = DailyStudyPlanState.day()
  private var day: String { DailyStudyPlanState.day(selectedDate) }
  private var key: String { DailyStudyPlanState.key(store.selectedCourseID ?? "", day) }
  private var plan: CloudStudyPlan? { store.dailyStudyPlan(day) }

  var body: some View {
    LearningScroll(spacing: 16) {
      BCCard {
        VStack(alignment: .leading, spacing: 12) {
          Text(store.currentCourse?.name ?? store.currentTitle).font(DS.font(.sectionTitle))
          DatePicker("学习日期", selection: $selectedDate, displayedComponents: .date)
            .datePickerStyle(.compact).font(DS.font(.body))
            .accessibilityIdentifier("plan.date")
          if let plan {
            Text("每日 \(plan.budget_minutes) 分钟 · 本日安排 \(plan.estimated_minutes) 分钟")
              .font(DS.font(.body)).foregroundStyle(DS.secondary)
            BCProgress(value: plan.progress)
            Text("已完成 \(plan.completedCount) / \(plan.tasks.count) 项")
              .font(DS.font(.body)).foregroundStyle(DS.secondary)
              .accessibilityIdentifier("plan.completion")
          }
        }
      }
      if let error = store.dailyPlanState.errors[key] {
        BCCard {
          VStack(alignment: .leading, spacing: 12) {
            Text(error).font(DS.font(.supporting)).foregroundStyle(DS.danger)
            BCButton("重新同步", style: .secondary) {
              Task {
                await store.loadDailyStudyPlan(day: day)
                store.startCloudSync()
              }
            }.accessibilityIdentifier("plan.retry")
          }
        }
      }
      if let plan {
        if plan.preferences_changed {
          Text("学习偏好已更新，本日已确认或开始的安排会保留，新偏好用于后续计划。")
            .font(DS.font(.supporting)).foregroundStyle(DS.secondary)
        }
        if plan.tasks.isEmpty {
          EmptyStateView(title: "本日暂无学习任务",
            message: plan.remaining_task_count == 0
              ? "已生成内容的任务已完成，或课程内容仍在准备中。"
              : "剩余任务暂时无法放入每日时间预算，可调整每日学习时间后刷新。", icon: "CalendarDays")
        } else {
          if plan.needs_confirmation {
            BCButton(store.dailyPlanState.confirming.contains(key) ? "正在确认…" : "确认本日计划") {
              Task { await store.confirmDailyStudyPlan(day: day) }
            }.disabled(store.dailyPlanState.confirming.contains(key))
              .accessibilityIdentifier("plan.confirm")
          }
          ForEach(plan.tasks) { task in taskCard(task, plan: plan) }
          if let next = plan.tasks.first(where: { !$0.isDone }) {
            BCButton("继续本日学习", icon: "play.fill") { store.openDailyStudyTask(next, day: day) }
              .disabled(plan.needs_confirmation).accessibilityIdentifier("plan.start")
          }
        }
      } else if store.dailyPlanState.requests[key] != nil {
        ProgressView("正在安排学习任务…").frame(maxWidth: .infinity).padding(24)
      }
    }
    .onChange(of: day, initial: true) { _, day in store.learningState.visiblePlanDay = day }
    .onDisappear { store.learningState.visiblePlanDay = nil }
    .onAppear { focusAgentDate() }
    .onChange(of: store.learningState.agentFocusPlanDate) { _, _ in focusAgentDate() }
    .refreshable { await store.loadDailyStudyPlan(day: day) }
    .task(id: store.dailyStudyPlanLoadKey + day) {
      store.dailyPlanState.focus = nil
      await store.loadDailyStudyPlan(day: day)
    }
    .onChange(of: scenePhase) { _, phase in
      if phase == .active {
        let today = DailyStudyPlanState.day()
        if day == observedToday { selectedDate = .now }
        observedToday = today
        Task { await store.loadDailyStudyPlan(day: day) }
      }
    }
  }

  private func focusAgentDate() {
    guard let value = store.learningState.agentFocusPlanDate else { return }
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "yyyy-MM-dd"
    if let date = formatter.date(from: value) { selectedDate = date }
  }

  private func taskCard(_ task: CloudStudyPlanTask, plan: CloudStudyPlan) -> some View {
    let pending = store.dailyPlanState.pending.contains {
      $0.workspaceID == store.selectedCourseID && $0.taskID == task.id
    }
    return BCCard {
      VStack(alignment: .leading, spacing: 12) {
        HStack {
          BCPill(task.kindLabel, color: DS.primary)
          Spacer()
          Text("\(task.minutes) 分钟").font(DS.font(.supporting)).foregroundStyle(DS.secondary)
        }
        Button { store.openDailyStudyTask(task, day: day) } label: {
          HStack(alignment: .top, spacing: 8) {
            Text(task.title).font(DS.font(.cardTitle)).multilineTextAlignment(.leading)
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").foregroundStyle(DS.primary)
          }.buttonFrame(fillsWidth: true)
        }.buttonStyle(.plain).disabled(plan.needs_confirmation)
          .accessibilityIdentifier("plan.open.\(task.id)")
        if let resource = store.cloudState.workspaces.first(where: { $0.id == store.selectedCourseID })?
          .resources.first(where: { $0.id == task.resource_id }) {
          Text(resource.structure?.title ?? resource.filename).font(DS.font(.supporting)).foregroundStyle(DS.secondary)
        }
        if task.isDone {
          Label("已完成", systemImage: "checkmark.circle.fill").font(DS.font(.supporting)).foregroundStyle(DS.primary)
        } else {
          Button {
            Task { await store.completeDailyStudyTask(task, day: day) }
          } label: {
            Label(pending ? "完成记录待同步 · 重试" : "标记完成", systemImage: pending ? "arrow.clockwise" : "circle")
              .font(DS.font(.supporting, weight: .semibold)).foregroundStyle(DS.primary).buttonFrame()
          }.buttonStyle(.plain).disabled(plan.needs_confirmation || store.cloudState.syncing && pending)
            .accessibilityIdentifier("plan.complete.\(task.id)")
        }
      }
    }
  }
}
