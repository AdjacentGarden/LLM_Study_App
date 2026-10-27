import SwiftUI

private struct LearningCardTheme {
  let surface: Color
  let answer: Color
  let ink: Color
  let muted: Color
  let accent: Color
  let pill: Color

  static let all: [LearningCardTheme] = [
    .init(
      surface: Color(hex: 0xece8ff), answer: Color(hex: 0xe3deff), ink: Color(hex: 0x26233c),
      muted: Color(hex: 0x5b5674), accent: Color(hex: 0x5b4bd6), pill: Color(hex: 0xd9d2ff)),
    .init(
      surface: Color(hex: 0xdff1ff), answer: Color(hex: 0xd3ebfc), ink: Color(hex: 0x193047),
      muted: Color(hex: 0x4d6479), accent: Color(hex: 0x2477b6), pill: Color(hex: 0xc7e6fa)),
    .init(
      surface: Color(hex: 0xddf5ea), answer: Color(hex: 0xd2efdf), ink: Color(hex: 0x18382e),
      muted: Color(hex: 0x4b6a60), accent: Color(hex: 0x22795f), pill: Color(hex: 0xc5e9d8)),
    .init(
      surface: Color(hex: 0xffe8d8), answer: Color(hex: 0xffdfca), ink: Color(hex: 0x472a1f),
      muted: Color(hex: 0x765749), accent: Color(hex: 0xa94f2d), pill: Color(hex: 0xffd4bc)),
    .init(
      surface: Color(hex: 0xfbe4ec), answer: Color(hex: 0xf5d9e4), ink: Color(hex: 0x432532),
      muted: Color(hex: 0x765461), accent: Color(hex: 0xa9436c), pill: Color(hex: 0xf0ccd9)),
    .init(
      surface: Color(hex: 0xfff2c9), answer: Color(hex: 0xfbe9b3), ink: Color(hex: 0x42351d),
      muted: Color(hex: 0x736344), accent: Color(hex: 0x7e5f17), pill: Color(hex: 0xf5dfa0)),
  ]
}

/// Hide the away-facing side continuously while the core rotates; a conditional
/// opacity transition would show mirrored letters halfway through the flip.
private struct LearningCardFaceRotation: AnimatableModifier {
  var angle: Double
  let perspective: CGFloat
  var animatableData: Double {
    get { angle }
    set { angle = newValue }
  }
  func body(content: Content) -> some View {
    content
      .opacity(cos(angle * .pi / 180) > 0 ? 1 : 0)
      .rotation3DEffect(.degrees(angle), axis: (x: 0, y: 1, z: 0), perspective: perspective)
  }
}

struct LearningFlashcardsView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var index = 0
  @State private var reviewStarted = Date()
  @State private var revealed = false
  @State private var drag: CGFloat = 0
  @State private var isTransitioning = false
  @State private var switchTask: Task<Void, Never>?
  @State private var flipAngle: Double = 0
  @State private var isFlipping = false
  @State private var flipTask: Task<Void, Never>?

  private var cards: [Flashcard] {
    store.flashcards.filter {
      store.selectedChapterID == nil || $0.chapter_id == store.selectedChapterID
    }
  }
  private var current: Flashcard? { cards.indices.contains(index) ? cards[index] : cards.first }
  private var theme: LearningCardTheme {
    LearningCardTheme.all[index % LearningCardTheme.all.count]
  }
  private var last: Bool { index >= cards.count - 1 }

  var body: some View {
    if let current {
      LearningScroll(width: store.isPad ? 680 : 440, inlinePadding: 16) {
        HStack(alignment: .bottom, spacing: 12) {
          VStack(alignment: .leading, spacing: 5) {
            Text("\(cards.count) 张今天复习").font(DS.font(16, weight: .bold))
            Text("\(cards.count) 张卡片").font(DS.font(14, weight: .semibold)).foregroundStyle(
              DS.secondary)
          }
          Spacer()
          Text(last ? "最后一张 · 完成后结束本轮" : "点击卡片翻面 · 左滑下一张")
            .font(DS.font(14)).foregroundStyle(DS.secondary).multilineTextAlignment(.trailing)
        }.padding(.horizontal, 4).padding(.vertical, 2).background(DS.surface)
        ZStack(alignment: .top) {
          if index + 2 < cards.count {
            RoundedRectangle(cornerRadius: 16).fill(DS.surface)
              .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
              .padding(.horizontal, 21).offset(y: 26)
          }
          if index + 1 < cards.count {
            RoundedRectangle(cornerRadius: 16).fill(DS.surface)
              .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
              .padding(.horizontal, 12).offset(y: 14)
          }
          memoryCard(current)
            .offset(x: drag)
            .rotationEffect(.degrees(Double(drag) / 28))
            .opacity(isTransitioning ? 0 : 1)
            .simultaneousGesture(
              DragGesture(minimumDistance: 20, coordinateSpace: .global)
                .onChanged { value in
                  guard !isTransitioning, !isFlipping,
                    abs(value.translation.width) > abs(value.translation.height) * 1.2
                  else { return }
                  drag = min(160, max(-160, value.translation.width))
                }
                .onEnded { value in
                  guard !isFlipping, !isTransitioning else { return }
                  if value.translation.width < -65
                    && abs(value.translation.width) > abs(value.translation.height) * 1.2 && !last
                  {
                    advance()
                  } else {
                    withAnimation(reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 0.8))
                    { drag = 0 }
                  }
                })
        }
        .padding(.horizontal, store.isPad ? 8 : 2).padding(.bottom, 30)
        if last {
          BCButton("完成记忆", icon: "checkmark") {
            store.showToast("已完成本轮闪卡记忆")
            store.back()
          }
          .disabled(isFlipping || isTransitioning)
          .accessibilityIdentifier("flashcard-finish")
        }
        if revealed {
          HStack(spacing: 12) {
            ratingButton("还不熟", helper: "稍后再复习", icon: "arrow.counterclockwise", primary: false) {
              store.reviewFlashcard(
                current, mastered: false, seconds: Date().timeIntervalSince(reviewStarted))
              store.showToast("已加入复习队列")
              if last { store.back() } else { advance() }
            }
            ratingButton("记住了", helper: last ? "完成本轮" : "进入下一张", icon: "checkmark", primary: true) {
              store.reviewFlashcard(
                current, mastered: true, seconds: Date().timeIntervalSince(reviewStarted))
              store.showToast("已记录为掌握")
              if last { store.back() } else { advance() }
            }
          }
          .transition(.opacity.combined(with: .offset(y: 6)))
        }
      }
      .onChange(of: index) { _, _ in reviewStarted = .now }
      .onChange(of: store.selectedCourseID) { _, _ in resetDeck() }
      .onChange(of: store.selectedChapterID) { _, _ in resetDeck() }
      .onChange(of: store.selectedBookID) { _, _ in resetDeck() }
      .onChange(of: reduceMotion) { _, reduced in
        if reduced { settleFlip() }
      }
      .onDisappear {
        switchTask?.cancel()
        settleFlip()
      }
    } else {
      LearningScroll(width: 600) {
        BCCard {
          VStack(alignment: .leading, spacing: 15) {
            BCPill("需要课程", color: DS.orange)
            Text("本章暂无结构化闪卡").font(DS.font(23, weight: .bold))
            Text("闪卡会根据课程目标、核心概念和原文证据生成。请先回到章节学习页查看本章课程。")
              .font(DS.font(14)).foregroundStyle(DS.secondary).lineSpacing(5)
          }
        }
        BCButton("回到章节", icon: "book") { store.go(.lesson) }
        BCButton("返回课程目录", style: .secondary) { store.go(.study) }
      }
    }
  }

  private func memoryCard(_ card: Flashcard) -> some View {
    VStack(spacing: 0) {
      Button(action: flip) {
        VStack(alignment: .leading, spacing: 16) {
          HStack {
            Text(card.due == "today" || card.due == nil ? "今天复习" : card.due ?? "今天复习").font(
              DS.font(20, weight: .bold)
            ).foregroundStyle(DS.secondary).padding(.horizontal, 9).padding(.vertical, 6)
              .background(theme.pill, in: Capsule())
            Spacer()
            Text(store.flashcardFamiliarityLabel(card))
            .font(DS.font(14, weight: .bold)).foregroundStyle(theme.muted)
            .padding(.horizontal, 9).padding(.vertical, 6).background(
              theme.accent.opacity(0.1), in: Capsule())
          }
          GeometryReader { geometry in
            ZStack {
              cardFace(card, answer: false)
                .modifier(
                  LearningCardFaceRotation(
                    angle: flipAngle, perspective: geometry.size.width / 1000))
              cardFace(card, answer: true)
                .modifier(
                  LearningCardFaceRotation(
                    angle: flipAngle - 180, perspective: geometry.size.width / 1000))
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
          }
          Text(revealed ? "点击返回问题" : "点击查看答案").font(DS.font(14, weight: .semibold)).foregroundStyle(
            theme.muted
          ).frame(maxWidth: .infinity, alignment: .center)
        }
        .padding(18).frame(maxWidth: .infinity).frame(
          height: store.isPad ? 800 : 420, alignment: .leading
        )
        .foregroundStyle(theme.ink)
        .background(revealed ? theme.answer : theme.surface)
        .contentShape(Rectangle())
      }.buttonStyle(.plain)
        .disabled(isFlipping || isTransitioning)
        .accessibilityIdentifier("flashcard-face")
        .accessibilityValue(store.isMastered(card.id) ? "mastered" : "unmastered")
        .accessibilityLabel("\(revealed ? "参考答案" : "问题")：\(revealed ? card.back : card.front)")
        .accessibilityHint(revealed ? "点按返回问题，向左轻扫下一张" : "点按查看答案，向左轻扫下一张")
        .accessibilityAction(named: "下一张") { if !last { advance() } }
      VStack(spacing: 10) {
        HStack {
          Text(
            card.page_start > 0 && card.source_kind != "ai_supplement"
              ? learningPageLabel(
                card.page_start, end: card.page_end, printed: card.printed_page_start,
                printedEnd: card.printed_page_end) : "AI 补充 · 待原文核验"
          )
          .font(DS.font(14)).foregroundStyle(theme.muted)
          Spacer()
          if card.page_start > 0 && card.source_kind != "ai_supplement" {
            Button("查看原文") { openSource(card) }.font(DS.font(14, weight: .bold)).foregroundStyle(
              theme.accent
            ).buttonStyle(.plain)
          }
        }
        VStack(spacing: 6) {
          HStack {
            Text("本轮进度").fontWeight(.bold)
            Spacer()
            Text("\(index + 1) / \(cards.count)").fontWeight(.bold)
          }
          .font(DS.font(14)).foregroundStyle(theme.muted)
          GeometryReader { geometry in
            ZStack(alignment: .leading) {
              Capsule().fill(theme.surface)
              Capsule().fill(theme.accent).frame(
                width: geometry.size.width * Double(index + 1) / Double(max(1, cards.count)))
            }
          }.frame(height: 4)
        }.accessibilityElement(children: .combine)
      }.padding(.horizontal, 16).padding(.top, 9).padding(.bottom, 16).background(DS.surface)
    }
    .clipShape(RoundedRectangle(cornerRadius: 16))
    .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
  }

  private func ratingButton(
    _ title: String, helper: String, icon: String, primary: Bool, action: @escaping () -> Void
  ) -> some View {
    Button(action: action) {
      HStack(spacing: 9) {
        Image(systemName: icon).font(DS.font(17, weight: .semibold))
        VStack(alignment: .leading, spacing: 3) {
          Text(title).font(DS.font(14, weight: .bold))
          Text(helper).font(DS.font(14)).opacity(0.75)
        }
      }.frame(maxWidth: .infinity, minHeight: 58)
        .foregroundStyle(primary ? Color.white : DS.primary)
        .background(primary ? DS.primary : DS.surface, in: RoundedRectangle(cornerRadius: 14))
        .overlay(
          RoundedRectangle(cornerRadius: 14).stroke(primary ? DS.primary : DS.border, lineWidth: 1))
    }.buttonStyle(.plain).disabled(isTransitioning || isFlipping)
      .accessibilityIdentifier(primary ? "flashcard-remember" : "flashcard-again")
  }

  private func cardFace(_ card: Flashcard, answer: Bool) -> some View {
    VStack(alignment: .leading, spacing: 15) {
      Text(answer ? "参考答案" : "问题 · \(card.concept)")
        .font(DS.font(20, weight: .bold)).foregroundStyle(theme.accent)
      Text(answer ? card.back : card.front)
        .font(DS.font(20.8, weight: .bold)).lineSpacing(4)
        .frame(maxWidth: .infinity, alignment: .leading)
      if answer {
        Text(card.reason.isEmpty ? "基于本节核心概念生成" : card.reason)
          .font(DS.font(14)).foregroundStyle(theme.muted).lineSpacing(5)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityHidden(true)
  }

  private func flip() {
    guard !isTransitioning, !isFlipping else { return }
    flipTask?.cancel()
    if reduceMotion {
      revealed.toggle()
      settleFlip()
      return
    }
    isFlipping = true
    // The source defines a generic 340ms flip token, but this card's actual
    // keyframes use local-slow (200ms) and the local-state cubic Bézier.
    withAnimation(.timingCurve(0.65, 0, 0.35, 1, duration: 0.2)) {
      revealed.toggle()
      flipAngle = revealed ? 180 : 0
    }
    flipTask = Task { @MainActor in
      do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
      guard !Task.isCancelled else { return }
      isFlipping = false
    }
  }

  private func settleFlip() {
    flipTask?.cancel()
    var transaction = Transaction()
    transaction.disablesAnimations = true
    withTransaction(transaction) {
      flipAngle = revealed ? 180 : 0
      isFlipping = false
    }
  }

  private func advance() {
    guard !last, !isTransitioning, !isFlipping else { return }
    if reduceMotion {
      index += 1
      revealed = false
      settleFlip()
      drag = 0
      return
    }
    withAnimation(.easeIn(duration: 0.14)) {
      isTransitioning = true
      drag = -90
    }
    let bookID = store.selectedBookID
    let chapterID = store.selectedChapterID
    switchTask?.cancel()
    switchTask = Task { @MainActor in
      do { try await Task.sleep(for: .milliseconds(150)) } catch { return }
      guard !Task.isCancelled, store.selectedBookID == bookID, store.selectedChapterID == chapterID,
        !cards.isEmpty
      else { return }
      index = min(index + 1, cards.count - 1)
      revealed = false
      settleFlip()
      drag = 30
      withAnimation(.easeOut(duration: 0.22)) {
        drag = 0
        isTransitioning = false
      }
    }
  }

  private func resetDeck() {
    switchTask?.cancel()
    index = 0
    reviewStarted = .now
    revealed = false
    settleFlip()
    drag = 0
    isTransitioning = false
  }

  private func openSource(_ card: Flashcard) {
    guard let id = store.selectedBookID else { return }
    store.openSource(
      SourcePageTarget(
        bookID: id, title: card.concept, page: card.page_start, pageEnd: card.page_end,
        printedPage: card.printed_page_start, from: .flashcards))
  }
}

struct LearningExportView: View {
  @Environment(AppStore.self) private var store
  private let modules = ["本章学习目标", "AI 导学笔记", "原文引用页码", "重点概念", "小测与错题诊断", "复习建议", "用户文字笔记"]
  @State private var selected: Set<String> = [
    "本章学习目标", "AI 导学笔记", "原文引用页码", "重点概念", "小测与错题诊断", "复习建议", "用户文字笔记",
  ]

  @State private var exportURL: URL?
  @State private var exportError: String?

  var body: some View {
    LearningScroll(width: 1120) {
      BCCard {
        VStack(alignment: .leading, spacing: 20) {
          Text("导出 PDF 预览").font(DS.font(20.8, weight: .bold))
          Text("导出当前章节已有的学习内容与文字笔记。尚未生成的模块会跳过，手写和录音原件保留在本机。")
            .font(DS.font(20)).foregroundStyle(DS.secondary).lineSpacing(5).padding(.bottom, 25)
        }.frame(maxWidth: .infinity, alignment: .leading)
      }.padding(.bottom, 5)
      VStack(spacing: 10) {
        ForEach(modules, id: \.self) { module in
          Button {
            withAnimation(.easeInOut(duration: 0.18)) {
              if selected.contains(module) {
                selected.remove(module)
              } else {
                selected.insert(module)
              }
            }
          } label: {
            HStack(spacing: 10) {
              ZStack {
                RoundedRectangle(cornerRadius: 7).fill(
                  selected.contains(module) ? Color(hex: 0x7e73ff) : DS.surface)
                RoundedRectangle(cornerRadius: 7).stroke(
                  selected.contains(module) ? Color(hex: 0x6d66ef) : DS.border, lineWidth: 2)
                if selected.contains(module) {
                  Image(systemName: "checkmark").font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white)
                }
              }.frame(width: 22, height: 22).frame(width: 24, height: 24)
              Text(module).font(DS.font(20)).foregroundStyle(DS.secondary)
              Spacer()
            }.padding(.horizontal, 13).frame(minHeight: 56).contentShape(Rectangle())
              .background(DS.surface, in: RoundedRectangle(cornerRadius: 14))
              .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border, lineWidth: 1))
          }
          .buttonStyle(.plain)
          .accessibilityLabel(module)
          .accessibilityValue(selected.contains(module) ? "已选择" : "未选择")
          .accessibilityAddTraits(selected.contains(module) ? .isSelected : [])
        }
      }.background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
        .padding(.bottom, -5)
      if let exportError { Text(exportError).foregroundStyle(DS.danger) }
      BCButton("生成 PDF", icon: "arrow.down.to.line") {
        exportURL = nil
        exportError = nil
        do {
          guard let source = store.currentSource else { throw ContentError.invalid("请先选择教材。") }
          exportURL = try LearningPDFExporter.write(title: source.summary.title, sections: sections(source))
        } catch { exportError = error.localizedDescription }
      }
      .disabled(selected.isEmpty || store.currentSource == nil)
      if let exportURL {
        ShareLink(item: exportURL) { Label("保存或分享 PDF", systemImage: "square.and.arrow.up") }
          .font(DS.font(18, weight: .bold)).padding(12)
      }
    }
  }
  private func sections(_ source: SourceContent) -> [(String, String)] {
    let lesson = store.activeLesson
    let chapterID = store.selectedChapterID
    let quizzes = source.quizzes.filter { chapterID == nil || $0.chapter_id == chapterID }
    let tasks = source.plan.tasks.filter { chapterID == nil || $0.chapter_id == chapterID }
    let notes = store.notes.filter {
      $0.anchor?.bookID == source.id && (chapterID == nil || $0.anchor?.chapterID == chapterID)
        && ($0.anchor?.courseID == nil || $0.anchor?.courseID == store.selectedCourseID)
    }
    let citations = lesson?.blocks.flatMap(\.citations) ?? []
    let values: [String: String] = [
      "本章学习目标": lesson?.objectives.joined(separator: "\n") ?? "",
      "AI 导学笔记": lesson?.blocks.filter(\.ai_generated).map { $0.title + "\n" + $0.content }.joined(separator: "\n\n") ?? "",
      "原文引用页码": citations.map {
        "PDF 第 \($0.page_start)–\($0.page_end) 页" + ($0.quote.map { "：" + $0 } ?? "")
      }.joined(separator: "\n"),
      "重点概念": lesson?.key_concepts.joined(separator: "、") ?? "",
      "小测与错题诊断": (quizzes.map { $0.prompt + "\n" + $0.choices.joined(separator: "\n") + "\n" + $0.explanation }
        + store.mistakes.filter { $0.book_id == source.id && ($0.course_id == nil || $0.course_id == store.selectedCourseID) }.map {
          $0.question + "\n我的答案：" + $0.answer + "\n" + ($0.explanation ?? $0.stuck_point)
        }).joined(separator: "\n\n"),
      "复习建议": tasks.map { $0.title + "（" + $0.status + "）" }.joined(separator: "\n"),
      "用户文字笔记": notes.compactMap { note in
        let body = note.kind == .voice ? NotesAnnotationRecord.voiceText(note)
          : note.organizedText ?? note.transcript ?? note.body
        return body.isEmpty ? nil : note.title + "\n" + body
      }.joined(separator: "\n\n"),
    ]
    return modules.filter { selected.contains($0) }.map { ($0, values[$0] ?? "") }
  }

}
