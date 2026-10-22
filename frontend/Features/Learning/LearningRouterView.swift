import SwiftUI

/// Native learning surfaces. Global navigation chrome and sheets belong to the app shell.
struct LearningRouterView: View {
  let route: Route

  var body: some View {
    Group {
      switch route {
      case .study: StudyDirectoryView()
      case .plan: LearningPlanView()
      case .flashcards: LearningFlashcardsView()
      case .lesson: LessonReaderView()
      case .assignment: LearningAssignmentView()
      case .diagnosis: LearningDiagnosisView()
      case .mistakes: LearningMistakesView()
      case .export: LearningExportView()
      default: EmptyView()
      }
    }
    .font(DS.font(15))
    .foregroundStyle(DS.text)
  }
}

struct LearningScroll<Content: View>: View {
  var width: CGFloat = 1120
  var inlinePadding: CGFloat? = nil
  var spacing: CGFloat = 18
  @Environment(AppStore.self) private var store
  @ViewBuilder let content: () -> Content

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: spacing, content: content)
        .frame(maxWidth: width)
        .padding(.horizontal, inlinePadding ?? (store.isPad ? 32 : 16))
        .padding(.top, store.isPad ? 22 : 12)
        .padding(.bottom, 32)
        .frame(maxWidth: .infinity)
    }
    .scrollDismissesKeyboard(.interactively)
    .background(store.route == .mistakes ? DS.background : Color.clear)
  }
}

struct LearningSectionHeading: View {
  let title: String
  var subtitle: String? = nil
  var icon: String? = nil

  var body: some View {
    HStack(spacing: 10) {
      if let icon { StickerIcon(icon, size: 38, glyphSize: 19) }
      VStack(alignment: .leading, spacing: 2) {
        if let subtitle {
          Text(subtitle).font(DS.font(14, weight: .medium)).foregroundStyle(DS.secondary)
        }
        Text(title).font(DS.font(16, weight: .bold))
      }
    }
  }
}

struct LearningSourceButton: View {
  var page: Int
  var end: Int? = nil
  var printed: Int? = nil
  var printedEnd: Int? = nil
  var action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 8) {
        Image(systemName: "book.closed")
        Text(
          page > 0
            ? learningPageLabel(page, end: end, printed: printed, printedEnd: printedEnd)
            : "AI 补充 · 待原文核验")
        Spacer(minLength: 8)
        if page > 0 {
          Text("查看原文")
          Image(systemName: "arrow.up.right")
        }
      }
      .font(DS.font(12, weight: .semibold))
      .foregroundStyle(DS.primary)
      .padding(.vertical, 14)
      .padding(.horizontal, 12)
      .background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 12))
    }
    .buttonStyle(.plain)
    .disabled(page <= 0)
  }
}

struct LearningNoSource: View {
  @Environment(AppStore.self) private var store
  let title: String
  let message: String
  var icon: String? = nil
  var body: some View {
    LearningScroll {
      EmptyStateView(title: title, message: message, icon: icon)
      BCButton("查看课程库", icon: "books.vertical", style: .secondary) { store.go(.library) }
      BCButton("上传教材", icon: "arrow.up.doc") { store.go(.upload) }
    }
  }
}

func learningPageLabel(_ page: Int, end: Int? = nil, printed: Int? = nil, printedEnd: Int? = nil)
  -> String
{
  let start = printed ?? page
  let last = printedEnd ?? end.map { start + max(0, $0 - page) } ?? start
  return start == last ? "教材第 \(start) 页" : "教材第 \(start)–\(last) 页"
}

func learningProgress(_ tasks: [StudyTask]) -> Double {
  guard !tasks.isEmpty else { return 0 }
  return Double(tasks.filter { $0.status == "done" }.count) / Double(tasks.count)
}

func learningChapterProgress(_ tasks: [StudyTask], chapterIDs: Set<String>) -> Double {
  let scoped = tasks.filter { chapterIDs.contains($0.chapter_id ?? "") }
  guard !scoped.isEmpty else { return 0 }
  let total = scoped.reduce(0.0) { value, task in
    switch task.status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
    case "done", "completed": return value + 1
    case "in_progress", "processing": return value + 0.5
    default: return value
    }
  }
  return (total / Double(scoped.count) * 100).rounded() / 100
}

func learningTaskStatus(_ status: String) -> String {
  switch status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
  case "done", "completed": "已完成"
  case "in_progress", "processing": "进行中"
  default: "待完成"
  }
}

func learningResourceURL(_ relative: String) -> URL? {
  let path = relative.hasPrefix("/") ? String(relative.dropFirst()) : relative
  guard let root = Bundle.main.resourceURL else { return nil }
  for prefix in ["Content", "Resources/Content", ""] {
    let base = prefix.isEmpty ? root : root.appendingPathComponent(prefix)
    let candidate = base.appendingPathComponent(path)
    if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
  }
  return nil
}

/// A wrapping row for the source UI's content-sized concept chips.
struct LearningFlowLayout: Layout {
  var spacing: CGFloat = 8
  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let limit = proposal.width ?? .greatestFiniteMagnitude
    var x: CGFloat = 0
    var y: CGFloat = 0
    var row: CGFloat = 0
    var used: CGFloat = 0
    for view in subviews {
      let size = view.sizeThatFits(.unspecified)
      if x > 0 && x + size.width > limit {
        x = 0
        y += row + spacing
        row = 0
      }
      used = max(used, x + size.width)
      row = max(row, size.height)
      x += size.width + spacing
    }
    return CGSize(width: proposal.width ?? used, height: y + row)
  }
  func placeSubviews(
    in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()
  ) {
    var x = bounds.minX
    var y = bounds.minY
    var row: CGFloat = 0
    for view in subviews {
      let size = view.sizeThatFits(.unspecified)
      if x > bounds.minX && x + size.width > bounds.maxX {
        x = bounds.minX
        y += row + spacing
        row = 0
      }
      view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
      row = max(row, size.height)
      x += size.width + spacing
    }
  }
}
