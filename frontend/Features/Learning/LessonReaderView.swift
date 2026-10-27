import AVKit
import CryptoKit
import SwiftUI
import UIKit

/// WebKit's `text-wrap: pretty` keeps the minimum line count and balances the
/// remaining space. Plain SwiftUI Text uses greedy breaks, including splitting
/// Chinese words and leaving a short last line. Keep this native and width based.
enum LearningPrettyLineBreaker {
  static func lines(in text: String, width: CGFloat, advance: (Character) -> CGFloat) -> [String] {
    guard !text.isEmpty, width > 0 else { return [text] }
    let characters = Array(text)
    if characters.contains("\n") {
      return lines(
        in: text.replacingOccurrences(of: "\n", with: " "), width: width, advance: advance)
    }
    var prefix: [CGFloat] = [0]
    for character in characters { prefix.append(prefix.last! + advance(character)) }
    let opening = Set("（([“「『《〈【〔〖〘〚")
    let closing = Set("，。；：！？、）)]”」』》〉】〕〗〙〛")
    func word(_ character: Character) -> Bool {
      character.unicodeScalars.allSatisfy {
        (48...57).contains($0.value) || (65...90).contains($0.value)
          || (97...122).contains($0.value) || $0.value == 0x2236
      }
    }
    var candidates = [0]
    for index in 1...characters.count {
      if index == characters.count
        || (!closing.contains(characters[index]) && !opening.contains(characters[index - 1])
          && !(word(characters[index]) && word(characters[index - 1])))
      {
        candidates.append(index)
      }
    }
    // An unusually long unbroken token still has to fit, as with overflow-wrap:anywhere.
    for pair in zip(candidates, candidates.dropFirst())
    where prefix[pair.1] - prefix[pair.0] > width {
      candidates.append(contentsOf: (pair.0 + 1)..<pair.1)
    }
    candidates = Array(Set(candidates)).sorted()
    let count = candidates.count
    var greedy: [Int] = [0]
    var start = 0
    while start < count - 1 {
      var end = start + 1
      while end + 1 < count
        && prefix[candidates[end + 1]] - prefix[candidates[start]] <= width + 0.01
      { end += 1 }
      greedy.append(end)
      start = end
    }
    let lineCount = greedy.count - 1
    func strings(_ boundaries: [Int]) -> [String] {
      zip(boundaries, boundaries.dropFirst()).map {
        String(characters[candidates[$0.0]..<candidates[$0.1]])
      }
    }
    guard lineCount > 1, lineCount <= 64, characters.count <= 1600 else { return strings(greedy) }
    var costs = Array(
      repeating: Array(repeating: Double.infinity, count: count), count: lineCount + 1)
    var next = Array(repeating: Array(repeating: -1, count: count), count: lineCount + 1)
    costs[0][count - 1] = 0
    for remaining in 1...lineCount {
      for begin in stride(from: count - 2, through: 0, by: -1) {
        for end in (begin + 1)..<count {
          let used = prefix[candidates[end]] - prefix[candidates[begin]]
          if used > width + 0.01 { break }
          guard costs[remaining - 1][end].isFinite else { continue }
          let slack = Double(width - used)
          let cost = costs[remaining - 1][end] + slack * slack
          if cost < costs[remaining][begin] {
            costs[remaining][begin] = cost
            next[remaining][begin] = end
          }
        }
      }
    }
    guard costs[lineCount][0].isFinite else { return strings(greedy) }
    var boundaries = [0]
    var current = 0
    for remaining in stride(from: lineCount, through: 1, by: -1) {
      let end = next[remaining][current]
      guard end > current else { return strings(greedy) }
      boundaries.append(end)
      current = end
    }
    return strings(boundaries)
  }
}

private final class LearningReadingTextView: UITextView {
  override func copy(_ sender: Any?) {
    guard let selectedTextRange, let selected = text(in: selectedTextRange) else { return }
    // Generated line separators are presentation only, never copied into a note.
    UIPasteboard.general.string = selected.replacingOccurrences(of: "\u{2028}", with: "")
  }
}

private struct LearningPrettyReadingText: UIViewRepresentable {
  let text: String
  final class Coordinator {
    var text = ""
    var width: CGFloat = -1
    var height: CGFloat = 28
  }
  func makeCoordinator() -> Coordinator { Coordinator() }
  func makeUIView(context: Context) -> LearningReadingTextView {
    let view = LearningReadingTextView()
    view.backgroundColor = .clear
    view.isEditable = false
    view.isSelectable = true
    view.isScrollEnabled = false
    view.textContainerInset = .zero
    view.textContainer.lineFragmentPadding = 0
    view.dataDetectorTypes = []
    view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    return view
  }
  func updateUIView(_ uiView: LearningReadingTextView, context: Context) {
    if uiView.bounds.width > 0 {
      apply(width: uiView.bounds.width, view: uiView, cache: context.coordinator)
    }
  }
  func sizeThatFits(_ proposal: ProposedViewSize, uiView: LearningReadingTextView, context: Context)
    -> CGSize?
  {
    let width = min(721.5, proposal.width ?? 721.5)
    guard width.isFinite, width > 0 else { return nil }
    apply(width: width, view: uiView, cache: context.coordinator)
    return CGSize(width: width, height: context.coordinator.height)
  }
  private func apply(width: CGFloat, view: LearningReadingTextView, cache: Coordinator) {
    guard cache.text != text || abs(cache.width - width) > 0.1 else { return }
    let font = UIFont(name: "BookCourseNotoSansSC-Regular", size: 20) ?? .systemFont(ofSize: 20)
    let lines = LearningPrettyLineBreaker.lines(in: text, width: width) {
      (String($0) as NSString).size(withAttributes: [.font: font]).width
    }
    let style = NSMutableParagraphStyle()
    style.minimumLineHeight = 28
    style.maximumLineHeight = 28
    style.lineBreakMode = .byClipping
    view.attributedText = NSAttributedString(
      string: lines.joined(separator: "\u{2028}"),
      attributes: [.font: font, .foregroundColor: UIColor(DS.secondary), .paragraphStyle: style])
    view.accessibilityLabel = text
    cache.text = text
    cache.width = width
    cache.height = CGFloat(lines.count) * 28
  }
}

struct LearningReadingSection: Identifiable {
  let block: LessonBlock
  var assets: [BookAsset]
  var id: String { block.block_id }
}

/// The same two-pass illustration assignment as the source frontend: each knowledge
/// point receives one relevant image before a second slot is filled.
func learningReadingSections(blocks: [LessonBlock], assets: [BookAsset]) -> [LearningReadingSection]
{
  let candidates: [[BookAsset]] = blocks.map { block in
    let explicit = block.asset_ids.compactMap { id in assets.first { $0.id == id } }
    let chunkLinked = assets.filter {
      !Set($0.source_chunk_ids).isDisjoint(with: block.source_chunk_ids)
    }
    let conceptLinked = assets.filter { asset in
      asset.source_chunk_ids.isEmpty
        && asset.concepts.contains { concept in
          block.title.localizedCaseInsensitiveContains(concept)
            || block.content.localizedCaseInsensitiveContains(concept)
        }
    }
    var seen = Set<String>()
    let groups = [explicit, chunkLinked, conceptLinked]
    return groups.flatMap { group in
      group.sorted {
        ($0.source_type == "extracted" ? 0 : 1) < ($1.source_type == "extracted" ? 0 : 1)
      }
    }.filter { asset in
      guard seen.insert(asset.id).inserted else { return false }
      if asset.source_type == "extracted" {
        if asset.type.lowercased().contains("page") { return false }
        if let bbox = asset.bbox, bbox.count >= 4,
          bbox.prefix(4).allSatisfy({ (0...1).contains($0) }),
          bbox[0] <= 0.05, bbox[1] <= 0.05, bbox[2] >= 0.95, bbox[3] >= 0.95
        {
          return false
        }
      }
      return true
    }
  }
  var result = blocks.map { LearningReadingSection(block: $0, assets: []) }
  var used = Set<String>()
  for _ in 0..<2 {
    for index in blocks.indices {
      if let asset = candidates[index].first(where: { !used.contains($0.id) }) {
        result[index].assets.append(asset)
        used.insert(asset.id)
      }
    }
  }
  return result
}

struct LessonReaderView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var page = 0
  @State private var mediaRequest: LessonMediaRequest?
  @State private var concept: String?

  private var lesson: Lesson? {
    store.lessons.first { $0.chapter_id == store.selectedChapterID } ?? store.activeLesson
  }
  private var chapter: Chapter? { store.chapters.first { $0.id == store.selectedChapterID } }
  private var assets: [BookAsset] {
    store.currentSource?.assets.filter { asset in
      asset.chapter_id == lesson?.chapter_id
        || (asset.source_type == "extracted" && (asset.page ?? 0) >= (lesson?.page_start ?? 1)
          && (asset.page ?? 0) <= (lesson?.page_end ?? 0))
    } ?? []
  }
  private var sections: [LearningReadingSection] {
    learningReadingSections(blocks: lesson?.blocks ?? [], assets: assets)
  }
  private var pageCount: Int { sections.count + 1 }
  private var activeSection: LearningReadingSection? {
    page > 0 && page - 1 < sections.count ? sections[page - 1] : nil
  }
  private var overviewImage: BookAsset? {
    guard let lesson else { return nil }
    return lesson.asset_ids.compactMap { id in assets.first { $0.id == id } }.first {
      $0.source_type == "ai_generated"
    }
      ?? assets.first {
        lesson.lesson_id == "lesson_meiosis" && $0.id == "asset_ai_meiosis_fertilization_cycle_v1"
      }
  }
  private var citation: LessonCitation? {
    activeSection?.block.citations.first ?? sections.compactMap { $0.block.citations.first }.first
  }
  private var positionKey: String {
    "native.lesson.page.\(store.selectedCourseID ?? "none").\(store.selectedBookID ?? "none").\(lesson?.id ?? "none")"
  }

  var body: some View {
    Group {
      if let lesson {
        VStack(spacing: 0) {
          readerHeader(lesson)
          ScrollViewReader { proxy in
            ScrollView {
              VStack(spacing: 0) {
                pageStatus
                Group {
                  if let section = activeSection {
                    sectionContent(section)
                  } else {
                    introduction(lesson)
                  }
                }
                .padding(.horizontal, 19).padding(.top, 22).padding(.bottom, 70)
                .frame(maxWidth: .infinity, minHeight: 620, alignment: .topLeading)
                .overlay(alignment: .bottomTrailing) {
                  sourceEntry.padding(.trailing, 18).padding(.bottom, 14)
                }
              }
              .background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
              .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
              .clipShape(RoundedRectangle(cornerRadius: 16))
              .id("lesson-top")
              .frame(maxWidth: 1120, alignment: .leading)
              .padding(.horizontal, store.isPad ? 32 : 16).padding(.top, 6).padding(.bottom, 28)
              .frame(maxWidth: .infinity)
              mediaActions.padding(.horizontal, 20).padding(.bottom, 16)
            }
            .background(DS.background)
            .simultaneousGesture(
              DragGesture(minimumDistance: 35).onEnded { value in
                guard abs(value.translation.width) > abs(value.translation.height) * 1.4 else {
                  return
                }
                changePage(by: value.translation.width < 0 ? 1 : -1)
              }
            )
            .onChange(of: page) { _, _ in
              store.learningState.readerCompletionVisible = page == pageCount - 1
              UserDefaults.standard.set(page, forKey: positionKey)
              proxy.scrollTo("lesson-top", anchor: .top)
            }
          }
          if page == pageCount - 1 {
            HStack {
              Spacer()
              BCButton("完成本节", icon: "checkmark.circle") {
                store.completeLesson()
                store.go(.study)
              }
              .frame(maxWidth: 190)
              Spacer()
            }.padding(.horizontal, 20).padding(.bottom, 12)
          }
        }
        .task(id: positionKey) {
          store.selectedLessonID = lesson.id
          page = min(max(0, UserDefaults.standard.integer(forKey: positionKey)), pageCount - 1)
          store.learningState.readerCompletionVisible = page == pageCount - 1
        }
      } else {
        LearningScroll(width: 720) {
          BCHeader(chapter?.title ?? "等待章节课程", back: { store.back() })
          EmptyStateView(title: "本节课程尚未准备好", message: "课程内容准备好后，会按原书结构展示可追溯的图文讲解。你也可以直接打开原文。", icon: "BookOpen")
          if let chapter, let bookID = store.selectedBookID {
            BCButton("阅读原文", icon: "book") {
              store.openSource(
                SourcePageTarget(
                  bookID: bookID, title: chapter.source_title, page: chapter.page_start,
                  pageEnd: chapter.page_end, printedPage: chapter.printed_page_start, from: .lesson)
              )
            }
          }
          BCButton("返回课程目录", style: .secondary) { store.go(.study) }
        }
      }
    }
    .onDisappear {
      store.learningState.readerCompletionVisible = false
      store.learningState.readerOverlayVisible = false
    }
    .onChange(of: mediaRequest?.id) { _, _ in
      store.learningState.readerOverlayVisible = mediaRequest != nil || concept != nil
    }
    .onChange(of: concept) { _, _ in
      store.learningState.readerOverlayVisible = mediaRequest != nil || concept != nil
    }
    .overlay {
      if let concept { conceptPanel(concept) }
    }
    .sheet(item: $mediaRequest) { request in
      LearningMediaSheet(request: request, api: store.cloud ?? CloudAPI.configured())
    }
  }

  private func readerHeader(_ lesson: Lesson) -> some View {
    ZStack(alignment: .top) {
      Text("章节学习").font(DS.font(27.2, weight: .bold))
        .accessibilityAddTraits(.isHeader).accessibilityIdentifier("navigation.title")
      HStack {
        Button {
          store.back()
        } label: {
          BCGlyph("ArrowLeft").frame(width: 18, height: 18)
            .frame(width: 44, height: 44).background(
              DS.surface, in: RoundedRectangle(cornerRadius: 12)
            )
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.border, lineWidth: 1))
        }.buttonStyle(.plain).accessibilityLabel("返回")
          .accessibilityIdentifier("navigation.back")
        Spacer()
      }.offset(y: DS.navigationButtonOffset)
    }.frame(height: DS.navigationRowHeight, alignment: .top).padding(.horizontal, store.isPad ? 32 : 16)
      .padding(.top, NotesScreenMetrics.titleTopInset)
      .padding(.bottom, store.isPad ? 14.84 : 6)
      .foregroundStyle(DS.text)
      .navigationBarSurface(DS.surface)
  }

  private var pageStatus: some View {
    VStack(spacing: 10) {
      HStack(spacing: 10) {
        Text("第 \(page + 1) / \(pageCount) 页").accessibilityIdentifier("lesson.page")
          .accessibilityValue(String(page + 1)).font(
            DS.font(20, weight: .bold)
          ).foregroundStyle(
            DS.primary)
        Text(activeSection?.block.title ?? "本节导读").font(DS.font(16, weight: .bold)).foregroundStyle(
          DS.secondary
        ).lineLimit(1)
        Spacer(minLength: 0)
        Text("左右滑动").font(DS.font(14, weight: .semibold)).foregroundStyle(DS.secondary)
      }.frame(height: 28, alignment: .top)
      GeometryReader { geometry in
        ZStack(alignment: .leading) {
          Capsule().fill(DS.border)
          Capsule().fill(DS.primary).frame(
            width: geometry.size.width * Double(page + 1) / Double(pageCount))
        }
      }.frame(height: 5)
    }
    .padding(.horizontal, 17).padding(.vertical, 14).padding(.bottom, 1).padding(.top, 1)
    .background(Color(hex: 0xf3f6fb))
    .overlay(alignment: .bottom) { Rectangle().fill(DS.border).frame(height: 1) }
    .accessibilityElement(children: .combine)
  }

  private func introduction(_ lesson: Lesson) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      if store.isCloudCoursePreview {
        Text("课程预览 · 未做诊断 · 掌握度未知")
          .font(DS.font(14, weight: .semibold)).foregroundStyle(DS.secondary)
          .accessibilityIdentifier("lesson.preview.notice")
      }
      VStack(alignment: .leading, spacing: 4) {
        Text(lesson.title).font(DS.font(20.8, weight: .bold)).frame(height: 26, alignment: .leading)
        Text(
          "\(store.selectedBookID == "book_biology_2" ? "人教版高中生物必修二遗传与进化" : store.currentSource?.summary.title ?? store.currentTitle) · 原书\(learningPageLabel(lesson.page_start, end: lesson.page_end, printed: chapter?.printed_page_start, printedEnd: chapter?.printed_page_end).replacingOccurrences(of: "教材", with: ""))"
        )
        .font(DS.font(14)).foregroundStyle(DS.secondary).frame(height: 18.9)
      }.padding(.bottom, 15).overlay(alignment: .bottom) {
        Rectangle().fill(DS.border).frame(height: 1)
      }
      Button {
        concept = lesson.key_concepts.first ?? lesson.title
      } label: {
        Text("本节导读").font(DS.font(16, weight: .bold)).frame(height: 20.8, alignment: .leading)
      }.buttonStyle(.plain).accessibilityLabel("查看概念：\(lesson.key_concepts.first ?? lesson.title)")
        .accessibilityIdentifier("lesson.concept")
      paragraph(lesson.summary)
      if let warning = lesson.warnings.first {
        VStack(alignment: .leading, spacing: 0) {
          Rectangle().fill(DS.border).frame(height: 1)
          Text(warning).font(DS.font(20)).foregroundStyle(DS.secondary).lineSpacing(4)
            .padding(.vertical, 2).frame(maxWidth: 643.8, alignment: .leading).padding(.top, 18)
        }.padding(.top, 6)
      }
      if let overviewImage { figure(overviewImage) }
      if !lesson.objectives.isEmpty {
        VStack(alignment: .leading, spacing: 5) {
          ForEach(lesson.objectives, id: \.self) { objective in
            HStack(alignment: .top, spacing: 15) {
              Circle().fill(DS.primary).frame(width: 5, height: 5).padding(.top, 11)
              Text(objective).font(DS.font(20)).lineSpacing(4).padding(.vertical, 2)
                .foregroundStyle(DS.secondary)
            }
          }
        }
      }

    }
  }

  private func sectionContent(_ section: LearningReadingSection) -> some View {
    let paragraphs = section.block.content.components(separatedBy: "\n\n").map {
      $0.trimmingCharacters(in: .whitespacesAndNewlines)
    }.filter { !$0.isEmpty }
    return VStack(alignment: .leading, spacing: 20) {
      Button {
        concept = section.block.title
      } label: {
        Text(section.block.title).font(DS.font(16, weight: .bold)).lineSpacing(3)
      }.buttonStyle(.plain).accessibilityLabel("查看概念：\(section.block.title)")
        .accessibilityIdentifier("lesson.concept")
      ForEach(Array(paragraphs.enumerated()), id: \.offset) { index, text in
        paragraph(text)
        if index < section.assets.count { figure(section.assets[index]) }
      }
      ForEach(Array(section.assets.dropFirst(paragraphs.count))) { asset in figure(asset) }
    }
    .id(section.id)
    .transition(
      reduceMotion
        ? .opacity
        : .asymmetric(insertion: .opacity.combined(with: .offset(x: 12)), removal: .opacity))
  }

  private func paragraph(_ text: String) -> some View {
    LearningPrettyReadingText(text: text)
      .frame(maxWidth: 721.5, alignment: .leading)
      .contextMenu {
        Button("摘录为笔记", systemImage: "note.text.badge.plus") {
          store.selectedQuote = text
          store.noteState.contextCitation = citation
          store.openSheet(.note)
        }
        Button("询问 AI", systemImage: "sparkles") {
          store.selectedQuote = text
          store.openSheet(.chat)
        }
      }
  }

  @ViewBuilder private func figure(_ asset: BookAsset) -> some View {
    if ContentBundle.url(asset.image_url) != nil {
      VStack(spacing: 6) {
        if asset.source_type == "extracted" {
          Button {
            openAssetSource(asset)
          } label: {
            figureImage(asset)
          }.buttonStyle(.plain)
        } else {
          figureImage(asset)
        }
        HStack(spacing: 12) {
          Text(
            asset.source_type == "extracted"
              ? "教材原图\(asset.page.map { " · 第 \($0) 页" } ?? "")" : "AI 辅助示意"
          )
          .font(DS.font(20)).foregroundStyle(DS.secondary)
          Spacer(minLength: 0)
          Button {
            openMedia(kind: "video", asset: asset)
          } label: {
            HStack(spacing: 5) {
              Image(systemName: "play.circle").font(.system(size: 14))
              Text("生成讲解短片")
            }.font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary)
          }.buttonStyle(.plain).accessibilityIdentifier("lesson.video.open.\(asset.id)")
        }.frame(height: 28).frame(maxWidth: activeSection == nil ? 272 : 272).fixedSize(
          horizontal: false, vertical: true)
      }.frame(maxWidth: .infinity).padding(.vertical, 2)
        .accessibilityElement(children: .contain).accessibilityLabel(asset.caption)
    }
  }

  private func figureImage(_ asset: BookAsset) -> some View {
    AssetImage(asset.image_url).frame(
      maxWidth: activeSection == nil ? 272 : 216, maxHeight: activeSection == nil ? 168 : 152
    )
    .clipShape(RoundedRectangle(cornerRadius: 8))
  }

  @ViewBuilder private var sourceEntry: some View {
    if let citation, citation.page_start > 0 {
      Button {
        store.openCitation(citation)
      } label: {
        Label("查看原文", systemImage: "book").font(DS.font(20, weight: .bold)).foregroundStyle(
          DS.primary)
      }.buttonStyle(.plain).frame(minHeight: 44)
        .accessibilityLabel(
          "查看原文，\(learningPageLabel(citation.page_start, end: citation.page_end, printed: citation.printed_page_start, printedEnd: citation.printed_page_end))"
        )
    }
  }

  private var readerFooter: some View {
    HStack(spacing: 14) {
      Button {
        changePage(by: -1)
      } label: {
        Label("上一页", systemImage: "chevron.left").font(DS.font(12, weight: .semibold))
          .frame(minWidth: 66, minHeight: 44)
      }.disabled(page == 0).opacity(page == 0 ? 0.35 : 1)
      Spacer(minLength: 0)
      Button {
        store.openSheet(.chat)
      } label: {
        HStack(spacing: 6) {
          StickerIcon("Sparkles", size: 26)
          Text("问问 AI").font(DS.font(12, weight: .bold))
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(DS.primarySoft, in: Capsule())
      }
      Spacer(minLength: 0)
      if page == pageCount - 1 {
        Button {
          store.completeLesson()
          store.go(.study)
        } label: {
          Label("完成本节", systemImage: "checkmark.circle.fill").font(DS.font(12, weight: .bold))
            .foregroundStyle(.white).padding(.horizontal, 14).frame(minHeight: 44)
            .background(DS.primary, in: Capsule())
        }
      } else {
        Button {
          changePage(by: 1)
        } label: {
          HStack(spacing: 5) {
            Text("下一页")
            Image(systemName: "chevron.right")
          }
          .font(DS.font(12, weight: .semibold)).frame(minWidth: 66, minHeight: 44)
        }
      }
    }
    .buttonStyle(.plain).foregroundStyle(DS.primary)
    .padding(.horizontal, 18).padding(.vertical, 10)
    .background(DS.surface)
    .overlay(alignment: .top) { Rectangle().fill(DS.border).frame(height: 1) }
  }

  private func changePage(by delta: Int) {
    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) {
      page = min(max(0, page + delta), pageCount - 1)
    }
  }

  private func openAssetSource(_ asset: BookAsset) {
    guard let id = store.selectedBookID else { return }
    let start = asset.page ?? citation?.page_start ?? lesson?.page_start ?? 1
    store.openSource(SourcePageTarget(bookID: id, title: asset.caption, page: start, from: .lesson))
  }

  private var mediaActions: some View {
    HStack(spacing: 12) {
      BCButton("生成辅助图", icon: "photo", style: .secondary) { openMedia(kind: "image") }
      BCButton("生成讲解短片", icon: "video", style: .secondary) { openMedia(kind: "video") }
    }
  }

  private func openMedia(kind: String, asset: BookAsset? = nil) {
    guard let lesson, let bookID = store.selectedBookID else { return }
    let linked = asset?.source_chunk_ids.compactMap { id in
      store.currentSource?.chunks.first { $0.id == id }?.text
    }.first
    let sourceQuote = citation?.quote.flatMap { $0.isEmpty ? nil : $0 }
    let excerpt = linked ?? sourceQuote ?? activeSection?.block.content ?? lesson.summary
    let sourcePages = asset?.page.map { [$0] } ?? citation.map { [$0.page_start] } ?? [lesson.page_start]
    mediaRequest = LessonMediaRequest(
      kind: kind, bookID: bookID, chapterID: lesson.chapter_id,
      chapterTitle: chapter?.source_title ?? lesson.source_title,
      title: asset?.caption ?? lesson.title,
      excerpt: String(excerpt.trimmingCharacters(in: .whitespacesAndNewlines).prefix(3000)),
      pages: sourcePages.filter { $0 > 0 }, lessonID: lesson.id,
      courseID: store.selectedCourseID ?? "")
    let previous = LessonMediaRequest(lesson: lesson, section: activeSection?.block,
      asset: asset, kind: kind == "image" ? .image : .video)
    mediaRequest?.legacyExcerpt = previous.excerpt
    mediaRequest?.legacyChapterTitle = previous.chapterTitle
  }

  private func conceptPanel(_ title: String) -> some View {
    let section = sections.first {
      $0.block.title.contains(title) || $0.block.content.contains(title)
    }
    return ZStack {
      Color.black.opacity(0.25).ignoresSafeArea().onTapGesture { concept = nil }
      VStack(alignment: .leading, spacing: 18) {
        HStack {
          Text("核心概念").font(DS.font(13, weight: .semibold)).foregroundStyle(DS.secondary)
          Spacer()
          Button {
            concept = nil
          } label: {
            Image(systemName: "xmark").frame(width: 34, height: 34)
          }.buttonStyle(.plain).accessibilityLabel("关闭概念").accessibilityIdentifier(
            "lesson.concept.close")
        }
        Text(title).font(DS.font(23, weight: .bold))
        ScrollView {
          Text(section?.block.content ?? lesson?.summary ?? "").font(DS.font(15)).lineSpacing(7)
            .textSelection(.enabled)
        }.frame(maxHeight: 280)
        if let citation = section?.block.citations.first {
          LearningSourceButton(
            page: citation.page_start, end: citation.page_end, printed: citation.printed_page_start,
            printedEnd: citation.printed_page_end
          ) {
            concept = nil
            store.openCitation(citation)
          }
        }
        BCButton("保存为学习笔记", icon: "note.text.badge.plus") {
          store.selectedQuote = "\(title)\n\(section?.block.content ?? lesson?.summary ?? "")"
          concept = nil
          store.noteState.contextCitation =
            section?.block.citations.first ?? lesson?.blocks.flatMap(\.citations).first
          store.openSheet(.note)
        }
      }.padding(24).frame(maxWidth: 480).background(
        DS.surface, in: RoundedRectangle(cornerRadius: 24)
      ).padding(24)
        .accessibilityAddTraits(.isModal)
    }
  }
}

struct LessonMediaRequest: Identifiable {
  let kind: String
  let bookID: String
  let chapterID: String
  let chapterTitle: String
  let title: String
  let excerpt: String
  let pages: [Int]
  let lessonID: String
  let courseID: String
  // Lookup compatibility for tasks created before the cloud request model was adopted.
  var legacyExcerpt: String? = nil
  var legacyChapterTitle: String? = nil

  var id: String {
    let parts = [kind, bookID, chapterID, lessonID, courseID, excerpt,
                 pages.map(String.init).joined(separator: ",")]
    let data = (try? JSONEncoder().encode(parts)) ?? Data()
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
  }
}

struct LessonMediaReceipt: Codable, Equatable {
  var requestID: String
  var jobID: String?
  var state: String
  var submissionUncertain: Bool

  var permitsNewGeneration: Bool {
    !submissionUncertain && ["failed", "cancelled", "rejected"].contains(state)
  }

  func validate() throws {
    guard LessonMediaJobContract.validRequestID(requestID),
          LessonMediaJobContract.states.contains(state),
          jobID == nil || LessonMediaJobContract.validJobID(jobID!) else {
      throw ContentError.invalid("媒体任务回执无法核验，请保留原请求并联系管理员。")
    }
  }

  mutating func accept(_ value: StudioJob, bookID: String, kind: String) throws {
    try validate()
    guard ["image", "video"].contains(kind), value.book_id == bookID, value.kind == kind,
          LessonMediaJobContract.validJobID(value.id),
          LessonMediaJobContract.states.contains(value.status),
          jobID == nil || value.id == jobID,
          state != "succeeded" || value.status == "succeeded" else {
      throw ContentError.invalid("返回任务与当前教材、类型或状态不一致，已停止显示。")
    }
    // Content coalescing may return a canonical job's older request_id. Keep
    // our durable alias and bind all subsequent reads to the returned job ID.
    jobID = value.id
    state = value.status
    submissionUncertain = false
  }
}

enum LessonMediaJobContract {
  static let states: Set<String> = [
    "queued", "planning", "submitting", "polling", "reviewing", "succeeded",
    "failed", "cancelled", "rejected", "uncertain",
  ]

  static func validJobID(_ value: String) -> Bool {
    value.utf8.count == 32 && value.utf8.allSatisfy {
      (48...57).contains($0) || (97...102).contains($0)
    }
  }

  static func validRequestID(_ value: String) -> Bool {
    (8...100).contains(value.utf8.count) && value.utf8.allSatisfy {
      (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0)
        || $0 == 45 || $0 == 95
    }
  }
}
