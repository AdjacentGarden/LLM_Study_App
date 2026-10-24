import SwiftUI
import UniformTypeIdentifiers

struct LearnerOnboardingView: View {
  let resettingPreferences: Bool
  @Environment(AppStore.self) private var store
  @State private var step: Int
  @State private var name = ""
  @State private var goal: String?
  @State private var dailyTime: String?
  @State private var learningBarriers: [String] = []
  @State private var guidanceStyles: [String] = []
  @State private var planningSupport: String?
  @State private var error: String?
  @State private var restoredDraft = false
  @State private var restoredScope: String?
  @State private var completed = false
  @State private var saving = false
  @FocusState private var nameFocused: Bool

  init(resettingPreferences: Bool = false) {
    self.resettingPreferences = resettingPreferences
    _step = State(initialValue: resettingPreferences ? 1 : 0)
  }

  private let titles = [
    "我们怎么称呼你？", "你希望通过学习收获什么？", "你每天可以投入多少学习时间？",
    "学习时，你最想改善什么？", "你更喜欢怎样的学习引导？", "你希望怎样安排学习节奏？",
  ]
  private let icons = [
    "Sparkles", "BookOpen", "Clock3", "BrainCircuit", "Lightbulb", "CalendarCheck2",
  ]
  private let tones = ["yellow", "blue", "peach", "mint", "yellow", "violet"]
  private var firstStep: Int { resettingPreferences ? 1 : 0 }
  private var lastStep: Int { LearnerOnboardingDraft.stepCount - 1 }
  var body: some View {
    GeometryReader { geometry in
      ScrollView {
        VStack(spacing: 0) {
          if resettingPreferences {
            HStack {
              Text("重新设置学习偏好").font(DS.font(16, weight: .bold)).foregroundStyle(DS.primary)
              Spacer()
              Button("取消") {
                nameFocused = false
                store.back()
              }.font(DS.font(16)).frame(minWidth: 44, minHeight: 44).buttonStyle(.plain)
                .disabled(saving).accessibilityIdentifier("preferences.cancel")
            }.padding(.bottom, 8)
          }
          Text(titles[step]).font(
            DS.font(min(34, max(22, geometry.size.width * 0.056)) * 0.8, weight: .bold)
          ).multilineTextAlignment(.center).frame(height: 44, alignment: .top).padding(.bottom, 12)
          flowTop(
            step: step - firstStep, total: LearnerOnboardingDraft.stepCount - firstStep,
            hideBack: step == firstStep
          ) {
            guard step > firstStep, !saving else { return }
            error = nil
            withAnimation { step -= 1 }
          }.disabled(saving)
          Spacer(minLength: 30)
          VStack(spacing: 28) {
            StickerIcon(icons[step], tone: tones[step], size: 48)
            if step == 0 {
              VStack(alignment: .leading, spacing: 8) {
                Text("你的称呼").font(DS.font(20, weight: .bold)).foregroundStyle(DS.secondary).frame(
                  height: 28, alignment: .top)
                TextField("输入你的姓名或昵称", text: $name).font(DS.font(20)).multilineTextAlignment(
                  .center
                ).textContentType(.nickname).padding(16).frame(minHeight: 58).background(
                  DS.surface, in: RoundedRectangle(cornerRadius: 16)
                ).overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border)).focused(
                  $nameFocused
                ).accessibilityIdentifier("onboarding.name").submitLabel(.next).onSubmit { next() }
                  .onChange(of: name) { _, value in
                    if value.count > 30 { name = String(value.prefix(30)) }
                    error = nil
                  }
              }
            } else if step == 2 {
              LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                ForEach(CourseOptions.times, id: \.0) { value, label in
                  Button {
                    dailyTime = value
                  } label: {
                    Text(label).font(DS.font(20, weight: .bold)).multilineTextAlignment(.center)
                      .frame(maxWidth: .infinity).frame(height: 50)
                      .foregroundStyle(dailyTime == value ? DS.primaryPressed : DS.text)
                      .background(dailyTime == value ? DS.primarySoft : DS.surface)
                      .clipShape(RoundedRectangle(cornerRadius: 14))
                      .overlay(
                        RoundedRectangle(cornerRadius: 14).stroke(
                          dailyTime == value ? DS.primary : DS.border,
                          lineWidth: dailyTime == value ? 2 : 1))
                  }.buttonStyle(.plain).accessibilityIdentifier("onboarding.time.\(value)")
                    .accessibilityAddTraits(
                      dailyTime == value ? .isSelected : [])
                }
              }.accessibilityLabel("每日学习时间")
            } else {
              VStack(spacing: 12) {
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                  ForEach(choices, id: \.0) { value, label, icon in
                    CourseChoice(label: label, selected: isSelected(value), icon: icon, tall: true)
                    {
                      choose(value)
                    }.accessibilityIdentifier("onboarding.choice.\(step).\(value)")
                  }
                }.accessibilityLabel(titles[step])
                if step == 3 || step == 4 {
                  Text("最多选择两项").font(DS.font(14)).foregroundStyle(DS.secondary)
                }
              }
            }
          }.disabled(saving)
          Spacer(minLength: 32)
          VStack(spacing: 12) {
            if let error {
              Text(error).font(DS.font(14)).foregroundStyle(DS.danger).accessibilityAddTraits(
                .updatesFrequently)
            }
            CourseFlowPrimaryButton(title: primaryButtonTitle)
            {
              next()
            }.clipShape(
              Capsule()
            ).disabled(saving).accessibilityIdentifier("onboarding.next")
            if step > 0 {
              Button("跳过") {
                switch step {
                case 1: goal = nil
                case 2: dailyTime = nil
                case 3: learningBarriers = []
                case 4: guidanceStyles = []
                case 5: planningSupport = nil
                default: break
                }
                next()
              }.font(DS.font(20)).foregroundStyle(DS.secondary).frame(maxWidth: .infinity)
                .frame(height: 44).buttonStyle(.plain).disabled(saving)
                .accessibilityIdentifier("onboarding.skip")
            }
          }
        }.frame(maxWidth: 560).frame(
          minHeight: max(440, geometry.size.height - (store.isPad ? 60 : 40))
        ).padding(
          .horizontal, 20
        ).padding(.top, store.isPad ? 42 : 4).padding(.bottom, 20).frame(maxWidth: .infinity)
      }.scrollDismissesKeyboard(.interactively)
    }
    .onAppear { restoreDraftIfReady() }
    .onChange(of: store.isLoading) { _, _ in restoreDraftIfReady() }
    .onChange(of: store.cloudState.isHydrating) { _, _ in restoreDraftIfReady() }
    .onChange(of: store.cloudState.cacheKey) { _, _ in restoreDraftIfReady() }
    .onChange(of: step) { _, _ in persistDraft() }
    .onChange(of: name) { _, _ in persistDraft() }
    .onChange(of: goal) { _, _ in persistDraft() }
    .onChange(of: dailyTime) { _, _ in persistDraft() }
    .onChange(of: learningBarriers) { _, _ in persistDraft() }
    .onChange(of: guidanceStyles) { _, _ in persistDraft() }
    .onChange(of: planningSupport) { _, _ in persistDraft() }
  }
  private var primaryButtonTitle: String {
    if saving { return "正在保存…" }
    if step == lastStep { return resettingPreferences ? "完成并保存" : "开始专属学习之旅" }
    return resettingPreferences ? "下一题" : "下一页"
  }
  private func restoreDraftIfReady() {
    guard !store.isLoading, !store.cloudState.isHydrating else { return }
    if store.cloud != nil {
      guard store.cloudState.authenticated, store.cloudState.cacheKey != nil else { return }
    }
    guard !restoredDraft || restoredScope != store.cloudState.cacheKey else { return }
    restoredScope = store.cloudState.cacheKey
    restoredDraft = true
    if resettingPreferences {
      // Keep the account name and start the preference questions with no selected answers.
      step = firstStep
      name = store.preferences?.displayName ?? ""
      goal = nil
      dailyTime = nil
      learningBarriers = []
      guidanceStyles = []
      planningSupport = nil
      return
    }
    guard !ContentBundle.fixtureMode else {
      name = "小明同学"
      goal = "systematic"
      dailyTime = "30to60"
      let arguments = ProcessInfo.processInfo.arguments
      if let index = arguments.firstIndex(of: "--fixture-step"),
        arguments.indices.contains(index + 1),
        let requested = Int(arguments[index + 1])
      {
        step = min(lastStep, max(0, requested))
        learningBarriers = ["understanding", "memory"]
        guidanceStyles = ["examples", "stepByStep"]
        planningSupport = "collaborative"
      }
      return
    }
    let draft = LearnerOnboardingDraft.load(
      scope: store.cloudState.cacheKey, preferences: store.preferences)
    step = draft.step
    name = draft.name
    goal = draft.goal
    dailyTime = draft.dailyTime
    learningBarriers = draft.learningBarriers
    guidanceStyles = draft.guidanceStyles
    planningSupport = draft.planningSupport
  }
  private var choices: [(String, String, String)] {
    switch step {
    case 3: CourseOptions.barriers
    case 4: CourseOptions.guidance
    case 5: CourseOptions.planning
    default: CourseOptions.goals
    }
  }
  private func isSelected(_ value: String) -> Bool {
    switch step {
    case 3: learningBarriers.contains(value)
    case 4: guidanceStyles.contains(value)
    case 5: planningSupport == value
    default: goal == value
    }
  }
  private func choose(_ value: String) {
    error = nil
    switch step {
    case 3: toggle(value, in: &learningBarriers)
    case 4: toggle(value, in: &guidanceStyles)
    case 5: planningSupport = value
    default: goal = value
    }
  }
  private func toggle(_ value: String, in selection: inout [String]) {
    if let index = selection.firstIndex(of: value) {
      selection.remove(at: index)
    } else if selection.count < 2 {
      selection.append(value)
    } else {
      error = "最多选择两项，可先取消一项"
    }
  }
  private func persistDraft() {
    guard !resettingPreferences, restoredDraft, !completed, !store.isLoading,
      !store.cloudState.isHydrating,
      restoredScope == store.cloudState.cacheKey,
      !ContentBundle.fixtureMode
    else {
      return
    }
    LearnerOnboardingDraft(
      step: step, name: name, goal: goal, dailyTime: dailyTime, learningBarriers: learningBarriers,
      guidanceStyles: guidanceStyles, planningSupport: planningSupport
    ).save(
      scope: store.cloudState.cacheKey)
  }
  private func next() {
    guard !saving else { return }
    if step == 0 && name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      error = "请先填写称呼"
      nameFocused = true
      return
    }
    nameFocused = false
    error = nil
    if step < lastStep {
      withAnimation { step += 1 }
    } else {
      if store.cloud != nil {
        saving = true
        Task { @MainActor in
          defer { saving = false }
          do {
            try await store.saveCloudPreferences(
              name: name.trimmingCharacters(in: .whitespacesAndNewlines), goal: goal,
              dailyTime: dailyTime, learningBarriers: learningBarriers,
              guidanceStyles: guidanceStyles, planningSupport: planningSupport)
            completed = true
            if resettingPreferences {
              finishPreferencesReset()
            } else {
              LearnerOnboardingDraft.clear(scope: store.cloudState.cacheKey)
              store.replace(.home)
            }
          } catch { self.error = error.localizedDescription }
        }
        return
      }
      completed = true
      if resettingPreferences {
        store.preferences = LearnerPreferences(
          displayName: name.trimmingCharacters(in: .whitespacesAndNewlines), primaryGoal: goal,
          dailyTime: dailyTime, learningBarriers: learningBarriers,
          guidanceStyles: guidanceStyles, planningSupport: planningSupport)
        store.persist()
        finishPreferencesReset()
        return
      }
      if !ContentBundle.fixtureMode { LearnerOnboardingDraft.clear() }
      store.completeOnboarding(
        name: name.trimmingCharacters(in: .whitespacesAndNewlines), goal: goal,
        dailyTime: dailyTime,
        learningBarriers: learningBarriers, guidanceStyles: guidanceStyles,
        planningSupport: planningSupport
      )
    }
  }
  private func finishPreferencesReset() {
    store.back()
    store.showToast("学习偏好已重新设置")
  }
}

@ViewBuilder private func flowTop(
  step: Int, total: Int, hideBack: Bool = false, back: @escaping () -> Void
) -> some View {
  HStack {
    Button(action: back) {
      Image(systemName: "arrow.left").frame(width: 44, height: 44).background(
        DS.surface, in: Circle())
    }.buttonStyle(.plain).accessibilityLabel("上一步").opacity(hideBack ? 0 : 1)
      .disabled(hideBack).accessibilityHidden(hideBack)
    Spacer()
    HStack(spacing: 3) {
      Text("\(step + 1)").font(DS.font(20, weight: .bold))
      Text("/\(total)").font(DS.font(20)).foregroundStyle(DS.muted)
    }.accessibilityLabel("第 \(step + 1) 步，共 \(total) 步")
    Spacer()
    Color.clear.frame(width: 44, height: 44)
  }.frame(minHeight: 52)
}

struct CourseSetupView: View {
  @Environment(AppStore.self) private var store
  @State private var error: String?
  @State private var saving = false
  @State private var selectingFile = false
  var body: some View {
    @Bindable var store = store
    GeometryReader { geometry in
      ScrollView {
        VStack(spacing: 12) {
          Text(
            store.draft.step < 0
              ? "调整课程" : CourseDiagnosisQuestion.all[min(5, store.draft.step)].title
          ).font(DS.font(min(34, max(22, geometry.size.width * 0.056)) * 0.8, weight: .bold))
            .multilineTextAlignment(.center).frame(height: 44, alignment: .top)
          flowTop(step: max(0, store.draft.step), total: 6) { previous() }
          if store.draft.step < 0 {
            basicInformation
          } else {
            let question = CourseDiagnosisQuestion.all[min(5, store.draft.step)]
            Spacer(minLength: 16)
            VStack(spacing: 28) {
              CourseBadgeIcon(name: question.icon, index: 3, size: 48)
              // The original flow exposes the question as a group label and keeps the canvas focused on the choices.
              CourseChoicesLayout(
                columns: question.options.count == 5
                  || (geometry.size.width <= 600 && question.multiple)
                  ? 2 : geometry.size.width <= 600 ? 1 : nil
              ) {
                ForEach(question.options, id: \.0) { value, label, icon in
                  CourseChoice(
                    label: label,
                    selected: store.draft.diagnosis[question.id]?.contains(value) == true,
                    icon: icon, illustrated: true,
                    toneIndex: question.options.firstIndex { $0.0 == value } ?? 0
                  ) {
                    store.draft.diagnosis[question.id] = question.toggle(
                      value, in: store.draft.diagnosis[question.id] ?? [])
                    error = nil
                    store.persist()
                  }
                }
              }.accessibilityElement(children: .contain).accessibilityLabel(question.title)
            }
            Spacer(minLength: 24)
          }
          VStack(spacing: 12) {
            if let error { Text(error).font(DS.font(14)).foregroundStyle(DS.danger) }
            CourseFlowPrimaryButton(
              title: saving ? "正在准备课程…" : store.draft.step < 5 ? "下一页" : completionTitle
            ) { next() }.clipShape(Capsule()).disabled(saving)
              .accessibilityIdentifier("courseSetup.next")
            if store.draft.editingCourseID != nil {
              BCButton("返回课程，草稿已保存", style: .text) {
                store.persist()
                store.back()
              }
            }
          }
        }.frame(maxWidth: 560).frame(
          minHeight: max(440, geometry.size.height - (store.isPad ? 56 : 40))
        ).padding(
          .horizontal, 20
        ).padding(.top, store.isPad ? 42 : 4).padding(.bottom, 20).frame(maxWidth: .infinity)
      }.scrollDismissesKeyboard(.interactively)
    }.fileImporter(
      isPresented: $selectingFile,
      allowedContentTypes: store.cloud == nil ? CourseUploadView.allowedTypes : [.pdf],
      allowsMultipleSelection: true
    ) { result in
      Task { @MainActor in
        switch result {
        case .success(let urls):
          for url in urls {
            await store.importFile(url)
            if let issue = store.importError {
              error = issue
              return
            }
          }
          if let id = store.draft.resourceIDs.last { store.selectSource(id) }
          store.replace(.courseSetup)
        case .failure(let issue): error = issue.localizedDescription
        }
      }
    }
  }
  private var completionTitle: String {
    if store.draft.editingCourseID != nil { return "保存，返回课程" }
    if store.draft.catalogBookID != nil { return "完成，导入课程" }
    return "完成问卷，查看课程目录"
  }
  private var basicInformation: some View {
    @Bindable var store = store
    return VStack(alignment: .leading, spacing: 16) {
      StickerIcon("BookOpen", size: 48).frame(maxWidth: .infinity)
      Text("课程名称").font(DS.font(14, weight: .bold))
      BCTextField("例如：期末生物复习", text: $store.draft.name)
      CourseSectionHeading(title: "加入书籍资料", detail: "至少一份")
      ForEach(store.sources) { source in
        CourseChoice(
          label: source.summary.title, selected: store.draft.resourceIDs.contains(source.id),
          icon: "book", labelSize: 16
        ) {
          if store.draft.resourceIDs.contains(source.id) {
            store.draft.resourceIDs.removeAll { $0 == source.id }
          } else {
            store.draft.resourceIDs.append(source.id)
          }
          store.persist()
        }
      }
      BCButton("添加本地资料", icon: "doc.badge.plus", style: .secondary) { selectingFile = true }
    }
  }
  private func previous() {
    error = nil
    if store.draft.step > 0 {
      store.draft.step -= 1
    } else if store.draft.step == 0 && store.draft.editingCourseID != nil {
      store.draft.step = -1
    } else {
      store.back()
    }
    store.persist()
  }
  private func next() {
    if store.draft.step < 0 {
      guard !store.draft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
        error = "请填写课程名称"
        return
      }
      guard !store.draft.resourceIDs.isEmpty else {
        error = "请至少选择或添加一份书籍资料"
        return
      }
      store.draft.step = 0
      return
    }
    let question = CourseDiagnosisQuestion.all[min(5, store.draft.step)]
    guard !(store.draft.diagnosis[question.id]?.isEmpty ?? true) else {
      error = "请至少选择一项再继续"
      return
    }
    error = nil
    if store.draft.step < 5 {
      withAnimation { store.draft.step += 1 }
      store.persist()
      return
    }
    saving = true
    if store.draft.editingCourseID != nil {
      if store.cloud != nil {
        Task { @MainActor in
          defer { saving = false }
          do {
            try await store.saveCloudCourse(
              name: store.draft.name, resourceIDs: store.draft.resourceIDs,
              diagnosis: store.draft.diagnosis)
            store.draft = CourseDraft()
            store.persist()
            store.replace(.courseDetail)
          } catch { self.error = error.localizedDescription }
        }
        return
      }
      store.createCourse(
        name: store.draft.name.trimmingCharacters(in: .whitespacesAndNewlines),
        resourceIDs: store.draft.resourceIDs, diagnosis: store.draft.diagnosis)
      store.replace(.courseDetail)
    } else if let catalogID = store.draft.catalogBookID,
      let book = store.community.first(where: { $0.id == catalogID })
    {
      if store.cloud != nil {
        Task { @MainActor in
          defer { saving = false }
          do {
            try await store.importCloudCommunity(book)
            store.replace(.communityImport)
          } catch { self.error = error.localizedDescription }
        }
        return
      }
      store.importCommunity(book)
      store.replace(.communityImport)
    } else {
      store.replace(.chapterConfirm)
    }
    saving = false
  }
}

struct CourseUploadView: View {
  var addingToCurrentCourse = false
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var panelPresented = false
  @State private var files: [URL] = []
  @State private var pickerOpen = false
  @State private var busy = false
  @State private var error: String?
  @State private var saveTask: Task<Void, Never>?
  @State private var operationID = UUID()
  @State private var transfer = CourseFileTransfer()
  @State private var pendingResourceIDs: [String] = []
  @State private var cancelRequested = false
  static var allowedTypes: [UTType] {
    [
      .pdf, .image, .plainText, .commaSeparatedText, .presentation, .spreadsheet,
      UTType(filenameExtension: "docx") ?? .data, .data,
    ]
  }
  var body: some View {
    GeometryReader { geometry in
      ZStack(alignment: .top) {
        if addingToCurrentCourse {
          DS.surface
        } else {
          Color(hex: 0xDCCAFA).ignoresSafeArea()
        }
        GeometryReader { panel in
          panelContents(in: panel.size)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .background(
          DS.surface,
          in: UnevenRoundedRectangle(
            topLeadingRadius: addingToCurrentCourse ? 0 : 54,
            topTrailingRadius: addingToCurrentCourse ? 0 : 54)
        )
        .accessibilityElement(children: .contain).accessibilityIdentifier("upload.panel")
        .offset(y: addingToCurrentCourse || panelPresented ? 0 : geometry.size.height + 80)
        .padding(.top, addingToCurrentCourse ? 0 : 16)
      }
    }
    .ignoresSafeArea(.container, edges: addingToCurrentCourse ? [] : .bottom)
    .task {
      guard !panelPresented else { return }
      if reduceMotion || addingToCurrentCourse {
        panelPresented = true
      } else {
        withAnimation(.spring(response: 0.52, dampingFraction: 0.88)) { panelPresented = true }
      }
    }
    .onChange(of: store.cloudState.epoch) { _, _ in
      if busy { cancelUpload() }
      pickerOpen = false
      files = []
      pendingResourceIDs = []
      error = nil
    }
    .fileImporter(
      isPresented: $pickerOpen,
      allowedContentTypes: store.cloud == nil ? Self.allowedTypes : [.pdf],
      allowsMultipleSelection: true
    ) { result in
      switch result {
      case .success(let urls):
        files.append(contentsOf: urls)
        error = nil
        store.importError = nil
      case .failure(let issue):
        let issue = issue as NSError
        if issue.domain != NSCocoaErrorDomain || issue.code != NSUserCancelledError {
          error = issue.localizedDescription
        }
      }
    }
    .fullScreenCover(isPresented: $busy) {
      CourseFileUploadProgressView(
        transfer: transfer, processingProgress: store.processingProgress,
        processingMessage: store.processingMessage, cancelRequested: cancelRequested,
        cancel: cancelUpload
      )
      .interactiveDismissDisabled()
      .onDisappear {
        if busy { cancelUpload() }
      }
    }
  }
  private var failure: String? { error ?? store.importError }
  private var canContinueSetup: Bool {
    !addingToCurrentCourse && !store.draft.resourceIDs.isEmpty
  }

  @ViewBuilder private func panelContents(in size: CGSize) -> some View {
    let compact = size.height < 500
    let gutter = min(40, max(24, size.width * 0.08))
    if size.width > size.height * 1.2 {
      HStack(alignment: .top, spacing: 24) {
        fileTile(size: min(160, max(96, size.height - 40)))
        controls(compact: true).frame(maxWidth: .infinity)
      }.frame(maxWidth: 760).padding(.horizontal, gutter).padding(.vertical, 12)
        .frame(maxWidth: .infinity)
    } else {
      let top: CGFloat = compact ? 20 : 48
      let bottom: CGFloat = compact ? 20 : 40
      let gap: CGFloat = compact ? 16 : 24
      let controlCount =
        2 + (failure == nil ? 0 : 1) + (canContinueSetup ? 1 : 0)
      let controlHeight: CGFloat =
        24 + 54 + (failure == nil ? 0 : compact ? 20 : 42)
        + (canContinueSetup ? 54 : 0)
        + CGFloat(controlCount - 1) * (compact ? 8 : 16)
      let tileSize = min(210, max(80, size.height - top - bottom - gap - controlHeight))
      VStack(spacing: gap) {
        fileTile(size: tileSize)
        controls(compact: compact)
      }.frame(maxWidth: 560).padding(.horizontal, gutter)
        .padding(.top, top).padding(.bottom, bottom).frame(maxWidth: .infinity)
    }
  }

  private func controls(compact: Bool) -> some View {
    VStack(spacing: compact ? 8 : 16) {
      Text(files.isEmpty ? "添加课程资料" : "继续添加")
        .font(DS.font(16, weight: .bold)).frame(height: 24)
      if let failure {
        Text(failure).font(DS.font(14)).foregroundStyle(DS.danger)
          .lineLimit(compact ? 1 : 2).frame(maxWidth: .infinity, alignment: .leading)
          .accessibilityIdentifier("upload.error")
      }
      BCButton(files.isEmpty && !pendingResourceIDs.isEmpty ? "重试资料整理" : "保存资料并继续") {
        saveFiles()
      }.disabled((files.isEmpty && pendingResourceIDs.isEmpty) || busy)
        .accessibilityIdentifier("upload.save")
      if canContinueSetup {
        BCButton("继续设置课程", style: .secondary) {
          store.go(store.draft.parseCompleted ? .courseSetup : .parseReady)
        }
      }
    }
  }

  private func fileTile(size: CGFloat) -> some View {
    Group {
      if files.isEmpty {
        addFileButton.frame(width: size, height: size)
      } else {
        ScrollView {
          LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 56, maximum: 56), spacing: 12)],
            alignment: .leading, spacing: 12
          ) {
            ForEach(Array(files.enumerated()), id: \.offset) { index, url in
              selectedFile(url, at: index)
            }
            addFileButton.frame(width: 56, height: 56)
          }
          .padding(20)
        }.scrollIndicators(.hidden).accessibilityIdentifier("upload.files")
      }
    }
    .frame(width: size, height: size)
    .background(DS.surface, in: RoundedRectangle(cornerRadius: 18))
      .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color(hex: 0xE1E6EE), lineWidth: 1.5))
      .shadow(color: DS.text.opacity(0.12), radius: 14, x: 0, y: 12)
  }

  private var addFileButton: some View {
    Button {
      pickerOpen = true
    } label: {
      Image(systemName: "plus")
        .font(.system(size: files.isEmpty ? 38 : 30, weight: .medium))
        .foregroundStyle(DS.text)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .contentShape(Rectangle())
    }.buttonStyle(.plain).disabled(busy)
      .accessibilityLabel(files.isEmpty ? "选择课程资料" : "继续添加资料")
      .accessibilityIdentifier("upload.select")
  }

  private func selectedFile(_ url: URL, at index: Int) -> some View {
    StickerIcon("FileText", size: 40)
      .overlay(alignment: .bottomTrailing) {
        Text("\(index + 1)").font(DS.font(8, weight: .bold)).foregroundStyle(.white)
          .frame(minWidth: 12, minHeight: 12)
          .background(DS.primary, in: Capsule()).offset(x: 6, y: 6)
          .accessibilityHidden(true)
      }
      .frame(width: 56, height: 56)
      .overlay(alignment: .topTrailing) {
        Button {
          files.remove(at: index)
        } label: {
          Image(systemName: "minus").font(.system(size: 10, weight: .bold))
            .foregroundStyle(.white).frame(width: 16, height: 16)
            .background(DS.danger, in: Circle())
            .frame(width: 44, height: 44).contentShape(Rectangle())
        }.buttonStyle(.plain).disabled(busy)
          .offset(x: 12, y: -12)
          .accessibilityLabel("移除 \(url.lastPathComponent)")
          .accessibilityIdentifier("upload.remove.\(index)")
      }
  }

  private func cancelUpload() {
    guard !cancelRequested else { return }
    cancelRequested = true
    saveTask?.cancel()
    if transfer.stage == .processing { store.cancelImport() }
  }

  private func saveFiles() {
    guard !busy, !files.isEmpty || !pendingResourceIDs.isEmpty else { return }
    let requestID = UUID()
    operationID = requestID
    let selectedFiles = files
    transfer = CourseFileTransfer(fileCount: selectedFiles.count)
    cancelRequested = false
    busy = true
    error = nil
    store.importError = nil
    let targetCourseID = addingToCurrentCourse ? store.currentCourse?.id : nil
    let originalDraft = store.draft
    let epoch = store.cloudState.epoch
    saveTask = Task { @MainActor in
      defer {
        if targetCourseID != nil, Task.isCancelled, epoch == store.cloudState.epoch {
          store.draft = originalDraft
          store.persist()
        }
        if operationID == requestID {
          busy = false
          cancelRequested = false
          saveTask = nil
        }
      }
      if targetCourseID != nil {
        store.draft = CourseDraft()
        store.draft.resourceIDs = pendingResourceIDs
      }
      var issues: [String] = []
      for (index, url) in selectedFiles.enumerated() {
        guard epoch == store.cloudState.epoch, !Task.isCancelled else { return }
        transfer = CourseFileTransfer(
          filename: url.lastPathComponent, fileIndex: index + 1, fileCount: selectedFiles.count)
        await store.importFile(url, targetCourseID: targetCourseID) { progress in
          guard epoch == store.cloudState.epoch, operationID == requestID, busy, !cancelRequested,
            transfer.fileIndex == index + 1, transfer.stage != .processing
          else { return }
          transfer.stage = .uploading
          if progress.sentBytes > transfer.progress.sentBytes { transfer.updatedAt = .now }
          transfer.progress = CloudUploadProgress(
            sentBytes: max(transfer.progress.sentBytes, progress.sentBytes),
            totalBytes: progress.totalBytes)
        }
        guard epoch == store.cloudState.epoch, !Task.isCancelled else { return }
        if let issue = store.importError {
          issues.append("\(url.lastPathComponent)：\(issue)")
          continue
        }
        if let id = store.draft.resourceIDs.last {
          if !pendingResourceIDs.contains(id) { pendingResourceIDs.append(id) }
          store.selectSource(id)
          if let targetCourseID {
            do { try await store.addSource(id, to: targetCourseID) }
            catch {
              guard epoch == store.cloudState.epoch, !Task.isCancelled else { return }
              issues.append("\(url.lastPathComponent)：\(error.localizedDescription)")
              continue
            }
          }
        }
        if let index = files.firstIndex(of: url) { files.remove(at: index) }
      }
      if issues.isEmpty, targetCourseID != nil {
        store.processingProgress = 0
        store.processingMessage = "准备解析资料"
        transfer.stage = .processing
        await store.processImport()
        guard epoch == store.cloudState.epoch, !Task.isCancelled else { return }
        if let issue = store.importError { issues.append(issue) }
      }
      if !issues.isEmpty {
        error = issues.joined(separator: "；")
        if targetCourseID != nil {
          store.draft = originalDraft
          store.persist()
        }
        return
      }
      if store.draft.name.isEmpty {
        store.draft.name = selectedFiles.first?.deletingPathExtension().lastPathComponent ?? "我的课程"
      }
      files = []
      pendingResourceIDs = []
      busy = false
      store.persist()
      if targetCourseID != nil {
        store.draft = originalDraft
        store.persist()
        store.closeSheet()
        store.showToast("资料已添加，可以继续学习")
      } else {
        store.draft.step = max(0, store.draft.step)
        store.processingProgress = 0
        store.processingMessage = "准备解析资料"
        store.persist()
        store.go(.processing)
      }
    }
  }
}

private struct CourseFileTransfer {
  enum Stage { case preparing, uploading, processing }
  var filename = ""
  var fileIndex = 1
  var fileCount = 1
  var progress = CloudUploadProgress()
  var updatedAt = Date.now
  var stage: Stage = .preparing
}

private struct CourseFileUploadProgressView: View {
  let transfer: CourseFileTransfer
  let processingProgress: Double
  let processingMessage: String
  let cancelRequested: Bool
  let cancel: () -> Void

  private var processing: Bool { transfer.stage == .processing }
  private var waiting: Bool { transfer.stage == .uploading && transfer.progress.fraction >= 1 }
  private var title: String {
    if cancelRequested { return "正在取消…" }
    if processing { return "正在解析资料" }
    if transfer.stage == .preparing { return "正在准备文件" }
    return waiting ? "等待服务器确认" : "文件正在上传"
  }
  private var bytesLabel: String {
    let sent = ByteCountFormatter.string(fromByteCount: transfer.progress.sentBytes, countStyle: .file)
    let total = ByteCountFormatter.string(fromByteCount: transfer.progress.totalBytes, countStyle: .file)
    return "已发送 \(sent) / \(total)"
  }

  var body: some View {
    VStack(spacing: 0) {
      BCHeader(processing ? "整理课程资料" : "上传课程资料")
        .padding(.top, 8).padding(.bottom, 20)
      ScrollView {
        VStack(spacing: 24) {
          Image(systemName: processing ? "doc.text.magnifyingglass" : "icloud.and.arrow.up")
            .font(.system(size: 42, weight: .medium)).foregroundStyle(DS.primary)
            .frame(width: 104, height: 104)
            .background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 30))
            .accessibilityHidden(true)
          VStack(spacing: 10) {
            Text(title).font(DS.font(24, weight: .bold))
              .accessibilityIdentifier("upload.progress.title")
            if transfer.fileCount > 1 && !processing {
              Text("第 \(transfer.fileIndex) / \(transfer.fileCount) 份资料")
                .font(DS.font(14)).foregroundStyle(DS.secondary)
            }
            Text(transfer.filename).font(DS.font(16)).foregroundStyle(DS.secondary)
              .multilineTextAlignment(.center).lineLimit(3)
          }
          VStack(spacing: 16) {
            if transfer.stage == .preparing || waiting {
              ProgressView().tint(DS.primary).controlSize(.large)
            } else {
              Text("\(Int((processing ? processingProgress : transfer.progress.fraction) * 100))%")
                .font(DS.font(40, weight: .bold)).monospacedDigit().foregroundStyle(DS.primary)
                .accessibilityIdentifier("upload.progress.percent")
            }
            BCProgress(value: processing ? processingProgress : transfer.progress.fraction)
            if transfer.stage == .uploading {
              Text(bytesLabel).font(DS.font(14)).monospacedDigit().foregroundStyle(DS.secondary)
                .accessibilityIdentifier("upload.progress.bytes")
            }
            TimelineView(.periodic(from: .now, by: 1)) { context in
              let seconds = Int(context.date.timeIntervalSince(transfer.updatedAt))
              let slow = transfer.stage == .uploading && seconds >= 15
              Text(
                processing ? processingMessage
                  : slow ? "网络或服务器响应较慢，已 \(seconds) 秒没有新的进展，可取消后重试。"
                  : waiting ? "文件已发送，收到服务器确认后将自动开始解析。"
                  : "请保持网络连接，上传完成后将自动开始解析资料。"
              )
              .font(DS.font(14)).foregroundStyle(slow ? DS.warning : DS.secondary)
              .multilineTextAlignment(.center).frame(minHeight: 44)
            }
          }.padding(24).background(DS.surface, in: RoundedRectangle(cornerRadius: 20))
          BCButton(processing ? "取消整理" : "取消上传", style: .secondary, action: cancel)
            .disabled(cancelRequested).accessibilityIdentifier("upload.cancel")
        }
        .frame(maxWidth: 520).padding(.horizontal, 24).padding(.vertical, 24)
        .frame(maxWidth: .infinity)
      }.scrollIndicators(.hidden)
    }
    .background(DS.background.ignoresSafeArea()).foregroundStyle(DS.text)
    .accessibilityIdentifier("upload.progress.screen")
  }
}

struct CourseParseReadyView: View {
  @Environment(AppStore.self) private var store
  private var fileKind: String {
    let ext = store.importedFileURL?.pathExtension.lowercased() ?? "pdf"
    return ext == "pdf" ? "PDF 教材" : ext.uppercased()
  }
  private var fileSize: String {
    guard let url = store.importedFileURL,
      let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize
    else { return "大小未知" }
    return ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file)
  }
  private var title: String {
    let filename =
      store.importedFileURL?.lastPathComponent ?? store.currentSource?.summary.filename
      ?? store.currentTitle
    let value = (filename as NSString).deletingPathExtension
    return value.components(separatedBy: CharacterSet(charactersIn: "（(")).first ?? value
  }

  var body: some View {
    if let source = store.currentSource {
      CourseCoverToContent(
        cover: source.summary.cover_url, parsing: true, documentKind: fileKind
      ) {
        HStack(spacing: 6) {
          StickerIcon("CheckCircle2", size: 24)
          Text(source.id.hasPrefix("book_local_") || store.cloud == nil ? "已保存在本机 · 待解析" : "已上传 · 待解析").font(DS.font(20)).foregroundStyle(DS.primaryPressed)
        }.frame(maxWidth: .infinity)
        Text(title).font(DS.font(20.8, weight: .bold)).multilineTextAlignment(.center).frame(
          maxWidth: 220)
        Text(
          "\(fileKind) · \(fileSize)"
            + (ContentBundle.fixtureMode ? " · 17:41" : "")
        ).font(DS.font(20)).foregroundStyle(DS.secondary)
        HStack(spacing: 10) {
          metric("文件类型", value: fileKind)
          metric("文件大小", value: fileSize)
          metric("下一步", value: "后台解析")
        }.padding(.top, 14)
        if let issue = store.importError {
          Text(issue).font(DS.font(14)).foregroundStyle(DS.danger)
        }
      } action: {
        BCButton(
          store.draft.parseCompleted ? "填写学习方式问卷" : store.importError == nil ? "开始解析" : "重新解析"
        ) {
          if store.draft.parseCompleted {
            store.draft.step = 0
            store.go(.courseSetup)
          } else {
            store.go(.processing)
          }
        }.accessibilityIdentifier("parse.start")
      }
    } else {
      CourseFeaturePage {
        EmptyStateView(title: "还没有选择学习资料", message: "先上传 PDF、图片或文档，再进入解析确认。", icon: "FileText")
        BCButton("去上传", icon: "folder") { store.replace(.upload) }
      }
    }
  }
  private func metric(_ label: String, value: String) -> some View {
    VStack(spacing: 6) {
      Text(value).font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary)
      Text(label).font(DS.font(14)).foregroundStyle(DS.secondary)
    }.frame(maxWidth: .infinity).frame(height: 82).background(
      DS.surface, in: RoundedRectangle(cornerRadius: 14)
    ).overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
  }
}

struct CourseProcessingView: View {
  var importing = false
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var started = false
  @State private var importStage = 0
  private var stages: [String] {
    importing
      ? ["整理课程资料", "识别章节与重点", "结合学习偏好", "安排学习计划", "准备学习内容"]
      : ["读取资料与版面", "识别目录与正文", "保存课程资料", "整理检索片段", "准备学习内容"]
  }
  private var progress: Double {
    importing
      ? Double(importStage) / 5
      : min(
        1, store.processingProgress > 1 ? store.processingProgress / 100 : store.processingProgress)
  }
  private var activeStage: Int {
    if ProcessInfo.processInfo.arguments.contains("--visual-baseline") { return importing ? -1 : 0 }
    return progress <= 0 ? -1 : min(4, Int(progress * 5))
  }
  var body: some View {
    GeometryReader { geometry in
      let screenHeight = geometry.size.height + (store.isPad ? 130 : 144)
      let spriteWidth = min(208, max(168, geometry.size.width * 0.46))
      let primaryHeight = max(min(256, max(208, screenHeight * 0.26)), spriteWidth * 4 / 3)
      let flowHeight = max(0, screenHeight - 144)
      let free = max(
        0, (flowHeight - primaryHeight - 344 - (importing ? 0 : 96)) / (importing ? 2 : 3))
      ScrollView {
        VStack(spacing: 0) {
          CourseSpriteStrip(
            path: "/assets/brand/loading/cloud-course-loading-strip-v1.png", duration: 1.2,
            loops: true
          )
          .frame(width: spriteWidth, height: spriteWidth * 4 / 3).offset(y: -40)
          .frame(maxWidth: .infinity).frame(height: primaryHeight + free, alignment: .top)
          VStack(spacing: 12) {
            VStack(spacing: 8) {
              ForEach(Array(stages.enumerated()), id: \.offset) { index, stage in
                let done = index < activeStage || progress >= 1
                let active = index == activeStage && progress < 1
                HStack(spacing: 10) {
                  Group {
                    if done {
                      StickerIcon("CheckCircle2", tone: "mint", size: 30)
                    } else {
                      Text("\(index + 1)").font(DS.font(20, weight: .bold)).foregroundStyle(
                        active ? DS.primaryPressed : DS.muted
                      ).frame(width: 30, height: 30).background(DS.background, in: Circle())
                    }
                  }.frame(width: 30, height: 30)
                  Text(stage).font(DS.font(16, weight: .bold)).frame(
                    maxWidth: .infinity, alignment: .leading)
                  Text(done ? "已完成" : active ? "处理中" : "等待中").font(DS.font(14, weight: .bold))
                    .foregroundStyle(done ? DS.mint : active ? DS.primaryPressed : DS.muted)
                }.padding(.horizontal, 12).frame(height: 48).background(
                  active ? Color(hex: 0xFAF7FF) : DS.surface, in: RoundedRectangle(cornerRadius: 12)
                ).overlay(
                  RoundedRectangle(cornerRadius: 12).stroke(
                    active ? Color(hex: 0xDDD6FE) : DS.border))
              }
            }.padding(8).background(Color(hex: 0xF4F7FB), in: RoundedRectangle(cornerRadius: 14))
            Text(store.importError ?? (importing ? "正在为你整理资料，准备专属学习之旅…" : store.processingMessage))
              .font(DS.font(20)).foregroundStyle(store.importError == nil ? DS.muted : DS.danger)
              .multilineTextAlignment(.center).frame(minHeight: 44).padding(.horizontal, 8)
              .accessibilityAddTraits(.updatesFrequently)
          }.frame(height: 344 + free, alignment: .top)
          if !importing {
            Group {
              if store.importError != nil {
                BCButton("返回重新解析", style: .secondary) { store.replace(.parseReady) }
              } else if progress >= 1 {
                BCButton("填写学习方式问卷") {
                  store.draft.step = 0
                  store.go(.courseSetup)
                }
              } else {
                Button {
                  store.go(.home)
                } label: {
                  Text("后台运行，先回首页").font(DS.font(20, weight: .bold)).foregroundStyle(
                    DS.primaryPressed
                  ).frame(maxWidth: .infinity).frame(height: 56).background(
                    DS.primarySoft, in: Capsule()
                  ).overlay(Capsule().stroke(Color(hex: 0xDDD6FE)))
                }.buttonStyle(.plain)
              }
            }.padding(.top, min(40, max(24, screenHeight * 0.04))).frame(
              height: 96 + free, alignment: .top)
          }
        }.frame(maxWidth: 704).padding(.horizontal, min(32, max(16, geometry.size.width * 0.04)))
          .padding(.top, min(24, max(12, geometry.size.width * 0.025))).frame(maxWidth: .infinity)
      }
    }
    .task {
      guard !started else { return }
      started = true
      if importing {
        if ContentBundle.fixtureMode {
          importStage = 2
          return
        }
        for step in 1...5 {
          if !reduceMotion { try? await Task.sleep(for: .milliseconds(360)) }
          guard !Task.isCancelled else { return }
          importStage = step
        }
        store.replace(.courseImportReady)
      } else if !store.draft.parseCompleted {
        await store.processImport()
      }
    }
  }
}

struct CourseChapterConfirmationView: View {
  @Environment(AppStore.self) private var store
  @State private var expanded: Set<String> = []
  @State private var error: String?
  var body: some View {
    CourseFeaturePage(width: 920, useSourceLayoutTop: true) {
      BCCard {
        HStack(alignment: .top, spacing: 14) {
          AssetImage(store.currentSource?.summary.cover_url ?? "").frame(width: 68, height: 90)
            .clipped().clipShape(RoundedRectangle(cornerRadius: 5))
          VStack(alignment: .leading, spacing: 8) {
            BCPill("本次文件解析结果")
            Text(
              ((store.currentSource?.summary.filename ?? store.currentTitle) as NSString)
                .deletingPathExtension.components(separatedBy: CharacterSet(charactersIn: "（("))
                .first ?? store.currentTitle
            ).font(DS.font(20.8, weight: .bold))
            Text(
              "已识别 \(store.chapters.count) 个目录项 · \(store.currentSource?.summary.page_count ?? 0) 页" + (store.chapters.allSatisfy { $0.confidence > 0 } && !store.chapters.isEmpty ? " · 平均置信度 \(Int((store.chapters.map(\.confidence).reduce(0, +) / Double(store.chapters.count)).rounded()))%" : " · 识别置信度未知，请核对原文")
            ).font(DS.font(14)).foregroundStyle(DS.secondary)
          }
          Spacer(minLength: 0)
        }
      }
      HStack {
        Text("本次解析目录").font(DS.font(20.8, weight: .bold))
        Spacer()
        Text("\(store.chapters.filter { $0.parent_id == nil }.count) 章").font(DS.font(14))
          .foregroundStyle(DS.secondary)
      }
      if store.chapters.isEmpty { EmptyStateView(title: "尚未识别到章节", message: "返回检查资料内容后重新解析。") }
      ForEach(store.chapters) { chapter in
        if chapter.parent_id == nil || expanded.contains(chapter.parent_id ?? "")
          || chapter.level == 1
        {
          HStack(spacing: 10) {
            if store.chapters.contains(where: { $0.parent_id == chapter.id }) {
              Button {
                withAnimation {
                  if expanded.contains(chapter.id) {
                    expanded.remove(chapter.id)
                  } else {
                    expanded.insert(chapter.id)
                  }
                }
              } label: {
                Image(systemName: expanded.contains(chapter.id) ? "chevron.down" : "chevron.right")
                  .frame(width: 44, height: 44)
              }.buttonStyle(.plain)
            } else {
              Color.clear.frame(width: 12)
            }
            Button {
              store.selectedChapterID = chapter.id
              store.openSheet(.editChapter)
            } label: {
              VStack(alignment: .leading, spacing: 6) {
                Text(chapter.title).font(DS.font(16, weight: .bold))
                HStack {
                  Text(
                    chapter.page_start < 1 || chapter.page_end < chapter.page_start
                      ? "原文页码待补充" : "PDF \(chapter.page_start)–\(chapter.page_end) 页")
                  Text(chapter.status)
                }.font(DS.font(14)).foregroundStyle(DS.secondary)
              }.frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain).accessibilityIdentifier("chapter.edit.\(chapter.id)")
            Image(systemName: "pencil").font(.system(size: 15)).foregroundStyle(DS.secondary)
          }.padding(8).padding(.leading, CGFloat(max(0, chapter.level - 1)) * 14).background(
            Color(hex: 0xF6F8FC), in: RoundedRectangle(cornerRadius: 14)
          ).overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
        }
      }
      if let error { Text(error).font(DS.font(14)).foregroundStyle(DS.danger) }

    }.safeAreaInset(edge: .bottom, spacing: 0) {
      VStack(spacing: 6) {
        BCButton(store.cloudState.isWorking ? "正在准备课程…" : "确认生成课程") {
          guard !store.chapters.isEmpty else {
            error = "请至少保留一个章节"
            return
          }
          store.confirmChapters()
        }.frame(maxWidth: store.isPad ? 460 : .infinity).disabled(
          store.chapters.isEmpty || store.cloudState.isWorking
        )
        .accessibilityIdentifier("chapters.confirm")
      }.padding(12).frame(maxWidth: .infinity).background(
        DS.surface.opacity(0.94), in: RoundedRectangle(cornerRadius: store.isPad ? 20 : 0)
      ).overlay(
        RoundedRectangle(cornerRadius: store.isPad ? 20 : 0).stroke(
          store.isPad ? DS.border : .clear)
      ).shadow(color: DS.text.opacity(store.isPad ? 0.12 : 0), radius: 15, x: 0, y: 6).padding(
        .horizontal, store.isPad ? 32 : 16
      ).padding(.bottom, store.isPad ? 20.85 : 6)
    }
  }
}

struct CourseCompletionView: View {
  var imported = false
  var community = false
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var phase = 0
  private var completionAnnouncement: String {
    if store.cloud != nil, !community {
      return
        "生成成功。已准备好 \(store.lessons.filter { $0.status == "ready" }.count) 节 AI 课时，其他章节可以在学习时继续生成。"
    }
    if community, let book = store.community.first(where: { $0.id == store.selectedCommunityID }) {
      return
        "导入成功。《\(book.title)》已加入你的课程，可在课程中继续添加教材和文件。\(book.chapters.count) 个目录项，\(book.flashcardCount) 张概念闪卡。"
    }
    return imported
      ? "资料已整理好，专属学习计划也准备好了。" : "已将《\(store.currentTitle)》编排为 \(store.lessons.count) 节 AI 课时。"
  }
  var body: some View {
    GeometryReader { geometry in
      let screenHeight = geometry.size.height + (store.isPad ? 130 : 144)
      let layout = min(24, max(12, geometry.size.width * 0.025))
      let compact = !store.isPad && geometry.size.width > screenHeight && screenHeight < 600
      let spriteWidth = compact ? 144 : store.isPad ? 216 : min(192, geometry.size.width * 0.52)
      let bottom: CGFloat = store.isPad ? 12 : 34
      let focusHeight = screenHeight - (store.isPad ? 24 : 62) - (store.isPad ? 0 : 34) - layout * 2
      let stageHeight =
        compact
        ? 256
        : max(
          store.isPad ? 512 : screenHeight <= 720 ? 392 : min(528, max(432, screenHeight * 0.66)),
          focusHeight - 120 - bottom)
      let sceneTop = compact ? 8 : min(52, max(24, screenHeight * 0.05))
      let copyTop: CGFloat =
        compact
        ? 164
        : store.isPad ? 284 : screenHeight <= 720 ? 212 : min(276, max(236, screenHeight * 0.31))
      ScrollView {
        Group {
          if compact {
            HStack(spacing: 16) {
              completionStage(
                width: spriteWidth, height: stageHeight, sceneTop: sceneTop, copyTop: copyTop,
                screenHeight: screenHeight)
              completionActions
            }
          } else {
            VStack(spacing: 0) {
              completionStage(
                width: spriteWidth, height: stageHeight, sceneTop: sceneTop, copyTop: copyTop,
                screenHeight: screenHeight)
              completionActions.frame(maxWidth: store.isPad ? 448 : .infinity)
            }
          }
        }.padding(.bottom, bottom).frame(maxWidth: store.isPad ? 608 : 544).padding(
          .horizontal, min(32, max(16, geometry.size.width * 0.04))
        ).padding(.top, layout).frame(maxWidth: .infinity)
      }
    }.task {
      if reduceMotion {
        phase = 3
        return
      }
      try? await Task.sleep(for: .milliseconds(900))
      guard !Task.isCancelled else { return }
      withAnimation(.timingCurve(0.22, 1, 0.36, 1, duration: 0.45)) { phase = 1 }
      try? await Task.sleep(for: .milliseconds(450))
      guard !Task.isCancelled else { return }
      withAnimation(.easeOut(duration: 0.18)) { phase = 2 }
      try? await Task.sleep(for: .milliseconds(200))
      guard !Task.isCancelled else { return }
      withAnimation(.easeOut(duration: 0.18)) { phase = 3 }
    }.onChange(of: reduceMotion) { _, reduced in if reduced { phase = 3 } }
  }
  private var completionActions: some View {
    VStack(spacing: 12) {
      BCButton("进入学习") { store.replace(store.currentSource == nil ? .courseDetail : .study) }
      BCButton("查看学习计划", style: .secondary) { store.replace(.plan) }
    }.opacity(phase >= 3 || reduceMotion ? 1 : 0).allowsHitTesting(phase >= 3 || reduceMotion)
  }
  private func completionStage(
    width: CGFloat, height: CGFloat, sceneTop: CGFloat, copyTop: CGFloat, screenHeight: CGFloat
  ) -> some View {
    ZStack(alignment: .top) {
      Group {
        if phase == 0 && !reduceMotion {
          CourseSpriteStrip(
            path: "/assets/brand/success/cloud-mascot-success-strip-v1.png", duration: 0.9,
            loops: false)
        } else {
          AssetImage("/assets/brand/cloud-mascot-success-transparent-v1.png").frame(
            width: width, height: width * 784 / 1158, alignment: .top)
        }
      }.frame(width: width, height: width * 4 / 3, alignment: .top)
        .scaleEffect(phase == 0 && !reduceMotion ? 1.24 : 1, anchor: UnitPoint(x: 0.5, y: 0.45))
        .offset(
          y: sceneTop + (phase == 0 && !reduceMotion ? min(140, max(108, screenHeight * 0.16)) : 0))
      if phase >= 2 || reduceMotion {
        StickerIcon("CheckCircle2", tone: "mint", size: 32, glyphSize: 24).offset(y: copyTop)
          .accessibilityLabel(completionAnnouncement).transition(
            .opacity.combined(with: .scale(scale: 0.85)))
      }
    }.frame(maxWidth: .infinity).frame(height: height, alignment: .top)
  }

}

/// Uses the original eight-frame 3:4 artwork. SwiftUI only changes which frame is clipped.
private struct CourseSpriteStrip: View {
  let path: String
  let duration: Double
  let loops: Bool
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var epoch = Date()
  var body: some View {
    if reduceMotion {
      GeometryReader { geometry in
        AssetImage(path, contentMode: .fill).frame(
          width: geometry.size.width * 8, height: geometry.size.height
        ).frame(width: geometry.size.width, height: geometry.size.height, alignment: .leading)
          .clipped()
      }
    } else {
      TimelineView(.animation(minimumInterval: duration / 8)) { timeline in
        let elapsed = max(0, timeline.date.timeIntervalSince(epoch))
        let frame = loops ? Int(elapsed / duration * 8) % 8 : min(7, Int(elapsed / duration * 7))
        GeometryReader { geometry in
          AssetImage(path, contentMode: .fill)
            .frame(width: geometry.size.width * 8, height: geometry.size.height)
            .offset(x: -CGFloat(frame) * geometry.size.width)
        }.clipped()
      }
    }
  }
}

/// The source uses centered flex-wrap, including a centered fifth option.
private struct CourseChoicesLayout: Layout {
  var columns: Int?
  var spacing: CGFloat = 10
  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let width = proposal.width ?? 560
    let rows = arrange(width: width, subviews: subviews)
    return CGSize(width: width, height: rows.last.map { $0.y + $0.height } ?? 0)
  }
  func placeSubviews(
    in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()
  ) {
    for row in arrange(width: bounds.width, subviews: subviews) {
      var x = bounds.minX + (bounds.width - row.width) / 2
      for item in row.items {
        subviews[item.index].place(
          at: CGPoint(x: x, y: bounds.minY + row.y), anchor: .topLeading,
          proposal: ProposedViewSize(width: item.width, height: row.height))
        x += item.width + spacing
      }
    }
  }
  private struct Row {
    var items: [(index: Int, width: CGFloat)]
    var width: CGFloat
    var height: CGFloat
    var y: CGFloat
  }
  private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
    var result: [Row] = []
    var row = Row(items: [], width: 0, height: 68, y: 0)
    for index in subviews.indices {
      let ideal = subviews[index].sizeThatFits(.unspecified)
      let itemWidth =
        columns.map { (width - CGFloat($0 - 1) * spacing) / CGFloat($0) }
        ?? min(width, max(135, ideal.width))
      if !row.items.isEmpty && row.width + spacing + itemWidth > width + 0.5 {
        result.append(row)
        row = Row(items: [], width: 0, height: 68, y: row.y + row.height + spacing)
      }
      row.width += (row.items.isEmpty ? 0 : spacing) + itemWidth
      row.height = max(
        row.height,
        subviews[index].sizeThatFits(ProposedViewSize(width: itemWidth, height: nil)).height)
      row.items.append((index, itemWidth))
    }
    if !row.items.isEmpty { result.append(row) }
    return result
  }
}

private struct CourseFlowPrimaryButton: View {
  let title: String
  let action: () -> Void
  @Environment(\.isEnabled) private var enabled
  var body: some View {
    Button(action: action) {
      Text(title).font(DS.font(20, weight: .bold)).multilineTextAlignment(.center)
        .frame(maxWidth: .infinity).frame(minHeight: 54)
        .foregroundStyle(enabled ? .white : DS.secondary)
        .background(enabled ? DS.primary : DS.border, in: Capsule())
    }.buttonStyle(BCPressStyle())
  }
}
