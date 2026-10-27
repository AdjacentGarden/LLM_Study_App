import SwiftUI
import UniformTypeIdentifiers

/// The course feature owns the presentation of the original course workflow.
/// AppStore remains the only owner of persisted course/resource identities.
struct CoursesRouterView: View {
  let route: Route
  @Environment(AppStore.self) private var store
  var body: some View {
    Group {
      switch route {
      case .home: CourseHomeView()
      case .onboarding: LearnerOnboardingView()
      case .preferenceSetup: LearnerOnboardingView(resettingPreferences: true)
      case .courseSetup: CourseSetupView()
      case .courseDetail: CourseDetailView()
      case .upload: CourseUploadView()
      case .parseReady: CourseParseReadyView()
      case .processing, .courseImportProcessing:
        CourseProcessingView(importing: route == .courseImportProcessing)
      case .chapterConfirm: CourseChapterConfirmationView()
      case .courseReady, .courseImportReady, .communityImport:
        CourseCompletionView(imported: route != .courseReady, community: route == .communityImport)
      case .library: CourseLibraryView()
      case .community: CourseDiscoveryView()
      case .communityBook: CommunityBookDetailView()
      case .profile: LearnerProfileView()
      case .settings: LearnerSettingsView()
      default: EmptyView()
      }
    }
    .font(DS.font()).foregroundStyle(DS.text)
  }
}

struct CourseSheetView: View {
  let sheet: SheetRoute
  var body: some View {
    switch sheet {
    case .editChapter: EditCourseChapterView()
    case .bookSwitcher: CourseSwitcherView()
    case .addMaterials: CourseUploadView(addingToCurrentCourse: true)
    case .shareBook: CommunitySharingSheet()
    default: EmptyView()
    }
  }
}

struct CourseFeaturePage<Content: View>: View {
  var width: CGFloat = 1120
  var topInset: CGFloat = 16
  var spacing: CGFloat = 16
  var useSourceLayoutTop = false
  @Environment(AppStore.self) private var store
  @ViewBuilder let content: () -> Content
  var body: some View {
    GeometryReader { geometry in
      ScrollView {
        VStack(alignment: .leading, spacing: spacing, content: content)
          .frame(maxWidth: width, alignment: .leading)
          .padding(.horizontal, min(32, max(16, geometry.size.width * 0.04)))
          .padding(
            .top,
            useSourceLayoutTop
              ? min(
                24,
                max(
                  12,
                  (geometry.size.width
                    + (store.isPad && store.route.showsPrimaryNavigation ? 58.6667 : 0)) * 0.025))
              : topInset
          ).padding(.bottom, 28)
          .frame(maxWidth: .infinity, alignment: .top)
      }
      .scrollDismissesKeyboard(.interactively)
    }
  }
}

struct CourseSectionHeading: View {
  let title: String
  var detail: String? = nil
  var detailSize: CGFloat = 14
  var detailWeight: Font.Weight = .regular
  var body: some View {
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      Text(title).font(DS.font(20.8, weight: .bold))
      if let detail {
        Text(detail).font(DS.font(detailSize, weight: detailWeight)).foregroundStyle(DS.secondary)
      }
      Spacer(minLength: 0)
    }.frame(minHeight: 44)
  }
}

struct CourseChoice: View {
  let label: String
  let selected: Bool
  var icon: String? = nil
  var tall = false
  var illustrated = false
  var toneIndex = 0
  var labelSize: CGFloat = 20
  let action: () -> Void
  var body: some View {
    Button(action: action) {
      Group {
        if tall {
          VStack(spacing: 16) {
            if let icon { StickerIcon(icon, size: 64).frame(maxHeight: .infinity) }
            Text(label).font(DS.font(16, weight: .bold))
          }.frame(maxWidth: .infinity).frame(minHeight: 122)
        } else {
          HStack(spacing: 12) {
            if let icon {
              if illustrated {
                CourseBadgeIcon(name: icon, index: toneIndex)
              } else {
                Image(systemName: icon).font(.system(size: 20, weight: .semibold))
              }
            }
            Text(label).font(DS.font(labelSize, weight: .bold)).frame(
              maxWidth: .infinity, alignment: .leading)
            Image(systemName: "checkmark").opacity(selected ? 1 : 0)
          }.frame(minHeight: illustrated ? 40 : 20)
        }
      }
      .padding(14).foregroundStyle(selected ? DS.primaryPressed : DS.text)
      .background(selected ? DS.primarySoft : DS.surface)
      .clipShape(RoundedRectangle(cornerRadius: tall ? 18 : 14))
      .overlay(
        RoundedRectangle(cornerRadius: tall ? 18 : 14).stroke(
          selected ? DS.primary : DS.border, lineWidth: selected ? 2 : 1))
    }.buttonStyle(.plain).accessibilityAddTraits(selected ? .isSelected : [])
  }
}

enum CourseOptions {
  static let goals: [(String, String, String)] = [
    ("systematic", "系统掌握知识", "LibraryBig"), ("exam", "备考提升成绩", "Target"),
    ("growth", "提升实际能力", "TrendingUp"), ("interest", "探索兴趣内容", "Heart"),
  ]
  static let times: [(String, String)] = [
    ("under30", "30分钟以内"), ("30to60", "30分钟-60分钟"), ("1to2h", "1-2小时"), ("over2h", "2小时以上"),
  ]
  static let barriers: [(String, String, String)] = [
    ("starting", "更容易开始", "Sparkles"), ("focus", "更专注投入", "Target"),
    ("understanding", "更深入理解", "Lightbulb"), ("memory", "更牢固记忆", "BrainCircuit"),
  ]
  static let guidance: [(String, String, String)] = [
    ("keyPoints", "抓住重点", "ListChecks"), ("principles", "讲透原理", "Lightbulb"),
    ("examples", "结合示例", "BookOpenText"), ("stepByStep", "循序展开", "Layers3"),
  ]
  static let planning: [(String, String, String)] = [
    ("selfDirected", "自主安排", "BookOpen"), ("collaborative", "共同规划", "CalendarCheck2"),
    ("structured", "提供完整计划", "ListChecks"), ("explore", "先自由体验", "Sparkles"),
  ]
  static func selectionLabel(_ values: [String], options: [(String, String, String)]) -> String {
    let labels = options.filter { values.contains($0.0) }.map(\.1)
    return labels.isEmpty ? "暂未设置" : labels.joined(separator: "、")
  }
  static func planningLabel(_ value: String?) -> String {
    planning.first { $0.0 == value }?.1 ?? "暂未设置"
  }
  static func goalLabel(_ value: String?) -> String {
    goals.first { $0.0 == value }?.1 ?? "系统学习（默认）"
  }
  static func timeLabel(_ value: String?) -> String {
    times.first { $0.0 == value }?.1 ?? "45 分钟（默认）"
  }
  static func budget(_ value: String?) -> Int {
    switch value {
    case "under30": 20
    case "1to2h": 90
    case "over2h": 120
    default: 45
    }
  }
}

struct CourseDiagnosisQuestion: Identifiable {
  let id: String
  let title: String
  let icon: String
  let multiple: Bool
  let options: [(String, String, String)]
  static let all: [Self] = [
    .init(
      id: "urgency", title: "你当前学习的紧迫程度？", icon: "AlarmClock", multiple: false,
      options: [
        ("urgent", "很紧急，短期要学完", "AlarmClock"), ("steady", "正常节奏，按计划来", "CalendarDays"),
        ("relaxed", "不着急，慢慢看", "Coffee"),
      ]),
    .init(
      id: "timePattern", title: "你的学习时间是哪种类型？", icon: "Clock3", multiple: false,
      options: [("block", "整块固定时间", "CalendarDays"), ("fragmented", "碎片化零散时间", "Puzzle")]),
    .init(
      id: "goals", title: "这门课程主要用于什么？", icon: "Target", multiple: true,
      options: [
        ("systematic", "系统学习", "BookOpen"), ("exam", "应试备考", "Target"),
        ("professional", "专业提升", "TrendingUp"), ("interest", "兴趣阅读", "Heart"),
        ("gaps", "查漏补缺", "Search"),
      ]),
    .init(
      id: "contentFoci", title: "你希望内容更侧重什么？", icon: "Lightbulb", multiple: true,
      options: [
        ("keyPoints", "重点和考点", "ListChecks"), ("principles", "原理和逻辑", "Lightbulb"),
        ("practice", "联系实际", "Globe2"), ("exercises", "多做练习", "Dumbbell"),
        ("notes", "生成笔记", "NotebookPen"),
      ]),
    .init(
      id: "aids", title: "你最需要哪些学习辅助？", icon: "MessageCircle", multiple: true,
      options: [
        ("chat", "AI 对话讲解", "MessageCircle"), ("video", "AI 视频讲解", "Video"),
        ("mistakes", "错题智能复盘", "RotateCcw"), ("guidedNotes", "导学笔记沉淀", "NotebookPen"),
      ]),
    .init(
      id: "reviews", title: "你习惯怎样巩固这门课程？", icon: "RotateCcw", multiple: true,
      options: [
        ("alongside", "边学边练", "PenLine"), ("notes", "学完整理笔记", "NotebookPen"),
        ("periodic", "定期复盘", "CalendarDays"), ("mistakes", "错题重点突破", "CircleAlert"),
        ("free", "自由复习", "Shuffle"),
      ]),
  ]
  func toggle(_ value: String, in current: [String]) -> [String] {
    if !multiple { return [value] }
    if current.contains(value) { return current.filter { $0 != value } }
    if value == "free" { return [value] }
    return current.filter { $0 != "free" } + [value]
  }
}

extension AppStore {
  func courseFeatureStartDraft(catalogID: String? = nil) {
    draft.name = ""
    draft.resourceIDs = []
    draft.diagnosis = [:]
    draft.step = 0
    draft.editingCourseID = nil
    draft.catalogBookID = catalogID
    draft.parseCompleted = false
    importedFileURL = nil
    importError = nil
    if let catalogID, let book = community.first(where: { $0.id == catalogID }) {
      draft.name = book.title
      draft.resourceIDs = [book.id]
    }
    persist()
  }
  func courseFeatureCover(_ course: Course) -> String? {
    let bookID = course.activeResourceID ?? course.resourceIDs.first
    return cloudState.workspaces.first { $0.id == course.id }?.resources.first { $0.book_id == bookID }?.cover_url
      ?? sources.first { $0.id == bookID }?.summary.cover_url
      ?? community.first { $0.id == bookID }?.cover
      ?? shelf.first { $0.bookId == bookID }?.coverUrl
  }
  func courseFeatureProgress(_ course: Course) -> Double {
    let tasks = sources.filter { course.resourceIDs.contains($0.id) }.flatMap { $0.plan.tasks }
    return CourseRecommendations.repositoryProgress(tasks: tasks)
  }
}

struct CourseBadgeIcon: View {
  let name: String
  var index = 0
  var size: CGFloat = 34
  private var color: Color {
    [
      Color(hex: 0xFFE883), Color(hex: 0xB7EFD8), Color(hex: 0xBCDFFF), Color(hex: 0xFFC4D5),
      Color(hex: 0xFFD3A0),
    ][index % 5]
  }
  var body: some View {
    let shape = UnevenRoundedRectangle(
      topLeadingRadius: size * 10 / 34, bottomLeadingRadius: size * 12 / 34,
      bottomTrailingRadius: size * 9 / 34, topTrailingRadius: size * 13 / 34)
    SVGAsset(path: "icons/lucide-\(name).svg").frame(width: size * 20 / 34, height: size * 20 / 34)
      .frame(width: size, height: size).background(color, in: shape)
      .overlay(shape.stroke(Color(hex: 0x252436), lineWidth: 2))
      .shadow(color: Color(hex: 0x252436), radius: 0, x: 2, y: 2)
      .rotationEffect(.degrees([-4.0, 4, -2, 3, -3][index % 5]))
  }
}

/// Shared source composition: the cover crosses from the lavender stage into the white sheet.
struct CourseCoverImage: View {
  @Environment(AppStore.self) private var store
  @State private var remoteCover = CloudBookCoverModel()
  let path: String?
  var contentMode: ContentMode = .fill
  var documentKind: String? = nil

  private var remoteRequest: CloudBookCoverRequest? {
    guard let path, CloudAPI.isBookCoverPath(path), let api = store.cloud,
      store.cloudState.authenticated, let account = store.cloudState.account, account.account != nil else { return nil }
    return CloudBookCoverRequest(path: path, origin: api.baseURL.absoluteString,
      accountID: account.user_id, epoch: store.cloudState.epoch)
  }

  var body: some View {
    Group {
      if let path, ContentBundle.url(path) != nil {
        AssetImage(path, contentMode: contentMode)
      } else if let request = remoteRequest, request == remoteCover.request,
        let data = remoteCover.data, let image = UIImage(data: data) {
        Image(uiImage: image).resizable().aspectRatio(contentMode: contentMode)
      } else {
        ZStack {
          DS.primarySoft
          if let documentKind {
            VStack(spacing: 12) {
              StickerIcon("FileText", size: 56)
              Text(documentKind).font(DS.font(20, weight: .bold))
              Text("AI 课程资料").font(DS.font(14)).foregroundStyle(DS.secondary)
            }
          } else {
            BCGlyph("FileText", color: DS.primary).frame(width: 30, height: 30)
          }
        }
      }
    }.accessibilityHidden(true)
      .task(id: remoteRequest) { await remoteCover.load(remoteRequest, api: store.cloud) }
  }
}

struct CourseCoverToContent<Content: View, Action: View>: View {
  let cover: String?
  var parsing = false
  var documentKind: String? = nil
  @ViewBuilder let content: () -> Content
  @ViewBuilder let action: () -> Action
  @Environment(AppStore.self) private var store
  var body: some View {
    GeometryReader { geometry in
      let screenHeight = geometry.size.height + (store.isPad ? 130 : 144)
      let compact = geometry.size.height < 500
      let visual: CGFloat =
        store.isPad ? min(272, max(144, screenHeight * 0.24)) : compact ? 168 : 224
      let coverWidth: CGFloat =
        parsing
        ? (store.isPad
          ? min(320, max(112, screenHeight * 0.48 - 178))
          : compact ? min(208, geometry.size.width * 0.46) : min(240, geometry.size.width * 0.54))
        : min(320, geometry.size.width * 0.64)
      let coverHeight = coverWidth * 4 / 3
      let topPadding: CGFloat =
        parsing
        ? (store.isPad
          ? coverHeight - visual + 92 : compact ? 144 : min(160, max(144, screenHeight * 0.18)))
        : min(292, max(160, geometry.size.width * 0.85333 - 136))
      let layoutTop = min(24, max(12, geometry.size.width * 0.025))
      let buttonWidth = min(
        328, min(640, geometry.size.width - min(32, max(16, geometry.size.width * 0.04)) * 2) * 0.64
      )
      ScrollView {
        VStack(spacing: 0) {
          Color.clear.frame(height: visual - 56)
          VStack(spacing: 8, content: content)
            .padding(.top, topPadding).padding(.horizontal, 16).padding(
              .bottom, parsing ? (store.isPad ? min(112, max(64, screenHeight * 0.08)) : 16) : 24
            )
            .frame(maxWidth: .infinity).frame(minHeight: 416, alignment: .top)
            .background(
              DS.surface, in: UnevenRoundedRectangle(topLeadingRadius: 56, topTrailingRadius: 56))
        }
        .overlay(alignment: .top) {
          CourseCoverImage(path: cover, documentKind: documentKind).frame(
            width: coverWidth - 8, height: coverHeight - 8
          ).clipped().padding(4).background(.white, in: RoundedRectangle(cornerRadius: 8)).shadow(
            color: DS.text.opacity(0.18), radius: 4, x: 0, y: 4
          ).padding(.top, store.isPad ? 20 : 16)
        }.padding(.top, layoutTop)
        if parsing && !store.isPad {
          action().frame(width: buttonWidth).padding(.top, 24).padding(.bottom, 16).frame(
            maxWidth: .infinity)
        }
      }
      .background {
        VStack(spacing: 0) {
          Color(hex: 0xDDD6FE).frame(height: visual + layoutTop)
          DS.surface
        }
      }
      .safeAreaInset(edge: .bottom, spacing: 0) {
        if !parsing || store.isPad {
          action().frame(width: buttonWidth).padding(.vertical, 12).frame(maxWidth: .infinity)
            .background(DS.surface)
        }
      }
    }.background(DS.surface.ignoresSafeArea(edges: .bottom))
  }
}
