import SwiftUI
import UIKit

func learningQuestionKind(_ question: QuizQuestion) -> String {
  question.question_type
    ?? (question.choices.isEmpty
      ? "short-answer"
      : question.choices.count == 2 && question.choices.contains("正确") ? "judgment" : "choice")
}

struct LearningAssignmentView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var index = 0
  @State private var answers: [String: String] = [:]
  @State private var shortAnswer = ""
  @State private var error: String?
  @State private var submitting = false
  @State private var cloudEventIDs: [String: String] = [:]
  @State private var questionStarted = Date()
  @State private var elapsed: [String: Double] = [:]
  @State private var confidence = 0.6
  @State private var diagnosticQuestions: [QuizQuestion] = []
  @State private var diagnosticContext: String?
  @State private var loadingDiagnostics = false

  private var contextKey: String {
    "\(store.noteScope ?? ""):\(store.selectedCourseID ?? ""):\(store.selectedBookID ?? ""):\(store.selectedChapterID ?? "")"
  }
  @State private var padKeyboardFrame = CGRect.null
  @State private var answerFrame = CGRect.null

  private var questions: [QuizQuestion] {
    let available = store.cloudState.privateLocalMode
      ? (diagnosticContext == contextKey ? diagnosticQuestions : []) : store.quizzes
    let planned = store.activeDailyStudyTask?.task.practice_item_id
    let focus = available.contains(where: { $0.id == planned }) ? planned : nil
    return available.filter {
      (store.selectedChapterID == nil || $0.chapter_id == store.selectedChapterID)
        && (store.learningState.agentFocusQuizID != nil || focus == nil || $0.id == focus)
    }
  }
  private var question: QuizQuestion? {
    questions.indices.contains(index) ? questions[index] : questions.first
  }
  private var canContinue: Bool {
    guard let question else { return false }
    return learningQuestionKind(question) == "short-answer"
      ? !shortAnswer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      : answers[question.id] != nil
  }
  private var last: Bool { index >= questions.count - 1 }
  private var answeredCount: Int {
    questions.filter { item in
      if item.id == question?.id, learningQuestionKind(item) == "short-answer" {
        return !shortAnswer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      }
      return !(answers[item.id]?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true)
    }.count
  }

  var body: some View {
    Group {
    if let question {
      GeometryReader { geometry in
        if geometry.size.width >= 720 {
          padLayout(question)
        } else {
          VStack(spacing: 12) {
            chapterHeader.padding(.horizontal, 16)
            questionPanel(question, phone: true)
          }
          .padding(.top, 12)
          .frame(maxWidth: .infinity)
          .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
      }
      .background(Color(hex: 0xd9d2ff))
      .overlay { AssignmentKeyboardDismissal().allowsHitTesting(false) }
      .task(id: "\(store.selectedBookID ?? ""):\(store.selectedChapterID ?? "")") {
        index = store.learningState.agentFocusQuizID.flatMap { target in questions.firstIndex { $0.id == target } } ?? 0
        answers = [:]
        shortAnswer = ""
        error = nil
        store.selectedQuizIndex = index
        store.learningState.visibleQuizID = questions.indices.contains(index) ? questions[index].id : nil
      }
      .onChange(of: store.learningState.agentFocusQuizID) { _, target in
        if let target, let found = questions.firstIndex(where: { $0.id == target }) {
          index = found; store.selectedQuizIndex = found; shortAnswer = answers[target] ?? ""
          store.learningState.visibleQuizID = target
        }
      }
      .onChange(of: question.id) { _, id in store.learningState.visibleQuizID = id }
      .onDisappear { store.learningState.visibleQuizID = nil; store.learningState.agentFocusQuizID = nil }
    } else {
      GeometryReader { geometry in
        if geometry.size.width >= 720 {
          padLayout(nil)
        } else {
          diagnosticEmpty
        }
      }
    }
    }.task(id: contextKey) { await loadDiagnostics() }
  }

  @MainActor private func loadDiagnostics() async {
    guard store.cloudState.privateLocalMode else { return }
    let context = contextKey
    diagnosticQuestions = []; diagnosticContext = nil; error = nil
    loadingDiagnostics = true
    defer { if context == contextKey { loadingDiagnostics = false } }
    guard let courseID = store.selectedCourseID, let bookID = store.selectedBookID else {
      error = "请先选择课程和教材"; return
    }
    do {
      let loaded = try await store.localAssignmentQuestions(courseID: courseID, bookID: bookID)
      guard context == contextKey, !Task.isCancelled else { return }
      diagnosticQuestions = loaded; diagnosticContext = context
      index = 0; answers = [:]; shortAnswer = ""
    } catch is CancellationError { }
    catch { if context == contextKey { self.error = error.localizedDescription } }
  }

  private var diagnosticEmpty: some View {
    Group {
      if loadingDiagnostics {
        ProgressView("正在读取本章诊断资料……")
      } else {
        VStack(spacing: 16) {
          LearningNoSource(title: "本章诊断资料尚未准备完成", message: error ?? "诊断题与章节讲解独立保存，请确认教材资料已完成下载。")
          Button("重新读取诊断资料") { Task { await loadDiagnostics() } }
        }
      }
    }.frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  private func padLayout(_ question: QuizQuestion?) -> some View {
    HStack(spacing: 0) {
      padSidebar(question)
        .frame(width: DS.padRailCollapsedWidth)
      Group {
        if let question {
          questionPanel(question, phone: false)
        } else {
          diagnosticEmpty
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(DS.surface, in: RoundedRectangle(cornerRadius: 24))
        }
      }.padding(20)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  private func padSidebar(_ question: QuizQuestion?) -> some View {
    VStack(alignment: .leading, spacing: 20) {
      Button { store.back() } label: {
        Label("返回", systemImage: "chevron.left")
          .font(DS.font(.smallButton)).foregroundStyle(DS.text)
          .buttonFrame(fillsWidth: true)
          .background(.white, in: Capsule())
      }.buttonStyle(BCPressStyle()).accessibilityLabel("返回")
        .accessibilityIdentifier("navigation.back")
      ScrollView {
        VStack(alignment: .leading, spacing: 20) {
          VStack(alignment: .leading, spacing: 10) {
            Text("作业诊断").font(DS.font(.sectionTitle)).foregroundStyle(DS.primary)
              .accessibilityAddTraits(.isHeader).accessibilityIdentifier("navigation.title")
            Text(store.chapters.first { $0.id == store.selectedChapterID }?.source_title ?? store.chapterTitle)
              .font(DS.font(.supporting)).foregroundStyle(DS.text).lineLimit(4)
              .fixedSize(horizontal: false, vertical: true)
              .accessibilityIdentifier("assignment.chapter-title")
          }
          VStack(alignment: .leading, spacing: 14) {
            Text("诊断进度").font(DS.font(.supporting, weight: .semibold)).foregroundStyle(DS.secondary)
            Text(question == nil ? "暂无题目" : "第 \(index + 1) 题")
              .font(DS.font(.sectionTitle)).foregroundStyle(DS.text)
              .accessibilityLabel(question == nil ? "暂无题目" : "第 \(index + 1) 题，共 \(questions.count) 题")
              .accessibilityIdentifier("assignment.question-position")
            Text("共 \(questions.count) 题").font(DS.font(.supporting)).foregroundStyle(DS.secondary)
            BCProgress(value: Double(answeredCount) / Double(max(1, questions.count)))
              .accessibilityLabel("已作答 \(answeredCount) 题，共 \(questions.count) 题")
              .accessibilityIdentifier("assignment.progress")
            Text("已作答 \(answeredCount) 题").font(DS.font(.supporting)).foregroundStyle(DS.secondary)
            Text("还剩 \(max(0, questions.count - answeredCount)) 题")
              .font(DS.font(.body, weight: .semibold)).foregroundStyle(DS.primary)
              .accessibilityIdentifier("assignment.remaining")
          }.accessibilityIdentifier("assignment.chapter-header")
        }.frame(maxWidth: .infinity, alignment: .leading)
      }.scrollIndicators(.hidden)
    }
    .padding(.horizontal, 12).padding(.vertical, 24)
    .background(Color(hex: 0xf2edff).ignoresSafeArea(.container, edges: [.top, .bottom]))
    .accessibilityIdentifier("assignment.sidebar")
  }

  private var chapterHeader: some View {
    VStack(spacing: 6) {
      HStack(spacing: 10) {
        StickerIcon("BookOpenCheck", size: 32, glyphSize: 17)
        LearningAssignmentTitle(title:
          store.chapters.first { $0.id == store.selectedChapterID }?.source_title ?? store.chapterTitle)
        Text("\(index + 1) / \(questions.count)")
          .font(DS.font(.supporting, weight: .semibold)).foregroundStyle(DS.secondary).fixedSize()
      }
      .frame(height: 32)
      BCProgress(value: Double(index + 1) / Double(max(1, questions.count)))
    }
    .padding(.horizontal, 16).padding(.vertical, 8)
    .frame(height: 60)
    .background(DS.surface, in: Capsule())
    .overlay(Capsule().stroke(DS.border.opacity(0.55), lineWidth: 1))
    .accessibilityIdentifier("assignment.chapter-header")
  }

  private func questionPanel(_ question: QuizQuestion, phone: Bool) -> some View {
    let shape = UnevenRoundedRectangle(
      topLeadingRadius: phone ? 48 : 24, bottomLeadingRadius: phone ? 0 : 24,
      bottomTrailingRadius: phone ? 0 : 24, topTrailingRadius: phone ? 48 : 24)
    return GeometryReader { panel in
      let keyboard = padKeyboardFrame.intersection(CGRect(origin: .zero, size: panel.size))
      let keyboardInset = !phone && !keyboard.isNull && keyboard.height > panel.safeAreaInsets.bottom + 1
        ? min(max(0, panel.size.height - 120), max(0, panel.size.height - keyboard.minY + 16)) : 0
      ScrollViewReader { proxy in
        ScrollView {
          questionContent(question, phone: phone)
            .padding(.horizontal, phone ? 26 : 32)
            .padding(.top, phone ? 24 : 32).padding(.bottom, phone ? 26 : 32)
            .frame(maxWidth: .infinity, minHeight: panel.size.height, alignment: .topLeading)
            .id(question.id)
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom, spacing: 0) {
          if keyboardInset > 0 {
            Color.clear.frame(height: keyboardInset).allowsHitTesting(false)
          }
        }
        .accessibilityIdentifier("assignment.question-scroll")
        .onChange(of: question.id) { _, id in proxy.scrollTo(id, anchor: .top) }
        .task(id: keyboardInset) {
          guard keyboardInset > 0, learningQuestionKind(question) == "short-answer" else { return }
          // The card keeps its original size; only its scroll viewport changes.
          await Task.yield()
          guard !Task.isCancelled else { return }
          let keyboardTop = panel.frame(in: .global).minY + keyboard.minY
          guard answerFrame.isNull || answerFrame.maxY + 16 > keyboardTop else { return }
          withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
            proxy.scrollTo("assignment.answer-editor", anchor: .top)
          }
        }
      }
      .overlay(alignment: .topLeading) {
        if !phone {
          AssistantKeyboardFrameReader { padKeyboardFrame = $0 }
            .frame(width: panel.size.width, height: panel.size.height)
            .allowsHitTesting(false)
        }
      }
    }
    .clipShape(shape)
    .background {
      // Extend only the white backing under the home indicator; keep controls in the safe area.
      shape.fill(DS.surface).ignoresSafeArea(.container, edges: phone ? .bottom : [])
    }
    .accessibilityIdentifier("assignment.question-panel")
  }

  private func questionContent(_ question: QuizQuestion, phone: Bool) -> some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack(spacing: 9) {
        StickerIcon(kindIcon(question), size: 36, glyphSize: 19)
        Text(phone ? "\(kindTitle(question)) · 第 \(index + 1) 题" : kindTitle(question)).font(DS.font(.supporting, weight: .semibold))
          .foregroundStyle(DS.primary)
      }
      Text(question.prompt).accessibilityIdentifier("assignment.question").font(
        DS.font(phone ? .sectionTitle : .pageTitle)
      ).lineSpacing(4)
        .fixedSize(horizontal: false, vertical: true).frame(
          maxWidth: phone ? 385 : .infinity, alignment: .leading)
      if learningQuestionKind(question) != "short-answer" {
        Text(
          question.instruction
            ?? (learningQuestionKind(question) == "judgment" ? "判断这句话是否正确" : "选择一个最准确的答案")
        )
        .font(DS.font(.supporting)).foregroundStyle(DS.secondary)
      }
      if learningQuestionKind(question) == "judgment" {
        judgmentOptions(question)
      } else if learningQuestionKind(question) == "choice" {
        choiceOptions(question)
      } else {
        shortAnswerField(question)
      }
      if let error {
        Text(error).font(DS.font(.supporting)).foregroundStyle(DS.danger).accessibilityAddTraits(
          .updatesFrequently)
      }
      if store.cloud != nil {
        VStack(alignment: .leading, spacing: 6) {
          Text("回答信心：\(Int(confidence * 100))%").font(DS.font(.supporting)).foregroundStyle(DS.secondary)
          Slider(value: $confidence, in: 0...1)
        }
      }
      Spacer(minLength: 32)
      if phone {
        if question.page_start > 0 && question.source_kind != "ai_supplement" {
          HStack {
            Spacer()
            sourceButton(question)
          }.padding(.bottom, 10)
        }
        submitButton(question, phone: true)
      } else {
        VStack(spacing: 64) {
          submitButton(question, phone: false)
          HStack {
            Spacer()
            if question.page_start > 0 && question.source_kind != "ai_supplement" {
              sourceButton(question)
            }
          }.frame(height: DS.ButtonSize.regular.height)
        }.padding(.bottom, 32)
      }
    }
  }

  private func sourceButton(_ question: QuizQuestion) -> some View {
    Button("查看原文") { openSource(question) }.font(DS.font(.smallButton)).buttonFrame()
      .foregroundStyle(DS.primary).buttonStyle(.plain)
  }

  private func submitButton(_ question: QuizQuestion, phone: Bool) -> some View {
    Button {
      if last { submit() } else { changeQuestion(1) }
    } label: {
      Text(submitting ? "正在生成诊断……" : phone
        ? (last ? "提交并查看诊断" : "提交答案") : (last ? "提交答案" : "下一题"))
        .font(DS.font(.button)).buttonFrame(.primary, fillsWidth: true)
        .foregroundStyle(canContinue ? Color.white : DS.secondary)
        .background(canContinue ? Color.clear : Color(hex: 0xedf1f5), in: Capsule())
        .overlay(Capsule().stroke(canContinue ? Color.clear : DS.border, lineWidth: 1))
        .primaryCapsuleSurface(active: canContinue)
    }.buttonStyle(.plain).disabled(
      submitting || (learningQuestionKind(question) != "short-answer" && !canContinue)
    )
    .accessibilityLabel(phone
      ? (last ? "提交作业" : "提交\(kindTitle(question))答案并进入下一题")
      : (last ? "提交答案" : "下一题"))
    .accessibilityIdentifier("assignment.submit")
  }

  private func judgmentOptions(_ question: QuizQuestion) -> some View {
    HStack(spacing: 12) {
      ForEach(["正确", "错误"], id: \.self) { value in
        let selected = answers[question.id] == value
        Button {
          select(value, question: question)
        } label: {
          VStack(spacing: 12) {
            Image(systemName: value == "正确" ? "checkmark" : "xmark").font(
              .system(size: 24, weight: .regular)
            )
            .frame(width: 44, height: 44).overlay(
              Circle().stroke(selected ? DS.primary : DS.text, lineWidth: 2))
            Text(value).font(DS.font(.cardTitle))
          }
          .frame(maxWidth: .infinity, minHeight: 116)
          .foregroundStyle(selected ? DS.primary : DS.text)
          .background(
            selected ? DS.primarySoft : DS.surface, in: RoundedRectangle(cornerRadius: 16)
          )
          .overlay(
            RoundedRectangle(cornerRadius: 16).stroke(
              selected ? DS.primary : DS.border, lineWidth: selected ? 2 : 1))
        }.buttonStyle(.plain).accessibilityIdentifier("assignment.judgment.\(value)")
          .accessibilityAddTraits(selected ? .isSelected : [])
      }
    }
  }

  private func choiceOptions(_ question: QuizQuestion) -> some View {
    VStack(spacing: 12) {
      ForEach(Array(question.choices.prefix(4).enumerated()), id: \.offset) { index, text in
        let key = String(UnicodeScalar(65 + index)!)
        let selected = answers[question.id] == key
        Button {
          select(key, question: question)
        } label: {
          HStack(alignment: .center, spacing: 12) {
            Text(key).font(DS.font(.supporting, weight: .semibold)).frame(width: 32, height: 32)
              .background(selected ? DS.primary : DS.background, in: Circle()).foregroundStyle(
                selected ? .white : DS.secondary)
            Text(text).font(DS.font(.reading)).lineSpacing(4).multilineTextAlignment(.leading)
            Spacer(minLength: 0)
            if selected {
              Image(systemName: "checkmark").font(DS.font(.cardTitle)).foregroundStyle(
                DS.primary)
            }
          }.padding(15)
            .foregroundStyle(DS.text)
            .background(
              selected ? DS.primarySoft : DS.surface, in: RoundedRectangle(cornerRadius: 14)
            )
            .overlay(
              RoundedRectangle(cornerRadius: 14).stroke(
                selected ? DS.primary : DS.border, lineWidth: selected ? 2 : 1))
        }.buttonStyle(.plain).accessibilityIdentifier("assignment.choice.\(key)")
          .accessibilityLabel("\(key)，\(text)").accessibilityAddTraits(
            selected ? .isSelected : [])
      }
    }
  }

  private func shortAnswerField(_ question: QuizQuestion) -> some View {
    VStack(alignment: .leading, spacing: 15) {
      HStack(alignment: .top, spacing: 9) {
        StickerIcon("Lightbulb", size: 24, glyphSize: 14)
        Text(question.instruction ?? "请结合本节核心概念，用完整的因果关系作答。")
          .font(DS.font(.supporting)).foregroundStyle(DS.secondary).lineSpacing(4)
      }
      HStack {
        Text("你的答案").font(DS.font(.supporting, weight: .semibold))
        Spacer()
        Button("完成输入") {
          UIApplication.shared.sendAction(
            #selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
        }.font(DS.font(.supporting, weight: .semibold)).foregroundStyle(DS.primary).buttonFrame().buttonStyle(.plain)
          .accessibilityIdentifier("learning.keyboard.done")
        Text("\(shortAnswer.count) / 300").font(DS.font(.caption)).foregroundStyle(DS.secondary)
        if !shortAnswer.isEmpty {
          Image(systemName: "checkmark.circle.fill").foregroundStyle(DS.primary).font(
            .system(size: 14))
        }
      }
      BCTextEditor(
        text: Binding(
          get: { shortAnswer },
          set: { value in
            if store.cloud != nil, answers[question.id] != String(value.prefix(300)) { cloudEventIDs.removeValue(forKey: question.id) }
            shortAnswer = String(value.prefix(300))
            if store.cloud != nil { answers[question.id] = shortAnswer }
            store.assignmentAnswer = shortAnswer
            error = nil
          }), placeholder: "在这里写下你的思路……"
      )
      .frame(minHeight: 210).accessibilityIdentifier("assignment.answer")
      .id("assignment.answer-editor")
      .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { answerFrame = $0 }
    }
  }

  private func select(_ answer: String, question: QuizQuestion) {
    if store.cloud != nil, answers[question.id] != answer { cloudEventIDs.removeValue(forKey: question.id) }
    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.16)) { answers[question.id] = answer }
    error = nil
  }
  private func changeQuestion(_ delta: Int) {
    if delta > 0 && !canContinue {
      error = "请先完成本题，再继续。"
      return
    }
    if store.cloud != nil, let question { elapsed[question.id] = Date().timeIntervalSince(questionStarted) }
    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) {
      index = min(max(0, index + delta), questions.count - 1)
    }
    store.selectedQuizIndex = index
    if store.cloud != nil {
      questionStarted = Date()
      shortAnswer = question.flatMap { answers[$0.id] } ?? ""
    }
    error = nil
  }
  private func submit() {
    guard canContinue else {
      error = "请先填写答案，再提交作业诊断。"
      return
    }
    if let missing = questions.firstIndex(where: {
      learningQuestionKind($0) != "short-answer" && answers[$0.id] == nil
    }) {
      index = missing
      error = "请先完成这道题。"
      return
    }
    guard let question else { return }
    if store.cloud != nil {
      if learningQuestionKind(question) == "short-answer" { answers[question.id] = shortAnswer }
      if let missing = questions.firstIndex(where: { answers[$0.id]?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true }) {
        index = missing; error = "请先完成这道题。"; return
      }
      elapsed[question.id] = Date().timeIntervalSince(questionStarted)
      for item in questions where cloudEventIDs[item.id] == nil { cloudEventIDs[item.id] = UUID().uuidString }
      let submissions = questions.map { item in
        CloudPracticeAnswer(question: item, answer: answers[item.id] ?? "", eventID: cloudEventIDs[item.id]!,
          seconds: elapsed[item.id] ?? 1, confidence: confidence)
      }
      submitting = true
      Task { @MainActor in
        defer { submitting = false }
        do { try await store.submitCloudAssignments(submissions) }
        catch { self.error = error.localizedDescription }
      }
      return
    }
    submitting = true
    let judgment =
      questions.first { learningQuestionKind($0) == "judgment" }.flatMap { answers[$0.id] } ?? "未作答"
    let choice =
      questions.first { learningQuestionKind($0) == "choice" }.flatMap { answers[$0.id] } ?? "未作答"
    let combined =
      "判断题：\(judgment)\n选择题：\(choice)\n简答题：\(shortAnswer.trimmingCharacters(in: .whitespacesAndNewlines))"
    store.assignmentAnswer = combined
    store.submitAssignment(question: question, answer: combined)
    submitting = false
    if store.latestDiagnosis != nil { store.go(.diagnosis) }
  }
  private func openSource(_ question: QuizQuestion) {
    guard let bookID = store.selectedBookID else { return }
    store.openSource(
      SourcePageTarget(
        bookID: bookID, title: store.chapterTitle, page: question.page_start,
        pageEnd: question.page_end, printedPage: question.printed_page_start,
        sourceText: question.source_quote, from: .assignment))
  }
  private func kindTitle(_ question: QuizQuestion) -> String {
    switch learningQuestionKind(question) {
    case "judgment": "判断题"
    case "choice": "选择题"
    default: "简答题"
    }
  }
  private func kindIcon(_ question: QuizQuestion) -> String {
    switch learningQuestionKind(question) {
    case "judgment": "CheckCircle2"
    case "choice": "ListChecks"
    default: "FileText"
    }
  }
}


/// Observe blank-area taps without putting a dismiss gesture on the editor's
/// SwiftUI ancestors. The recognizer never cancels normal scrolling or controls.
private struct AssignmentKeyboardDismissal: UIViewRepresentable {
  func makeUIView(context: Context) -> DismissalView { DismissalView() }
  func updateUIView(_ view: DismissalView, context: Context) { }
  static func dismantleUIView(_ view: DismissalView, coordinator: ()) { view.detach() }

  final class DismissalView: UIView, UIGestureRecognizerDelegate {
    private weak var observedWindow: UIWindow?
    private lazy var tap = UITapGestureRecognizer(target: self, action: #selector(dismissKeyboard))

    override init(frame: CGRect) {
      super.init(frame: frame)
      isUserInteractionEnabled = false
      tap.cancelsTouchesInView = false
      tap.delegate = self
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func didMoveToWindow() {
      super.didMoveToWindow()
      detach()
      observedWindow = window
      window?.addGestureRecognizer(tap)
    }

    func detach() {
      observedWindow?.removeGestureRecognizer(tap)
      observedWindow = nil
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
      guard window != nil, bounds.contains(touch.location(in: self)) else { return false }
      var touched = touch.view
      while let view = touched {
        if view is UITextView || view is UITextField || view is UIControl { return false }
        touched = view.superview
      }
      return true
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
      shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }

    @objc private func dismissKeyboard() { window?.endEditing(true) }
  }
}

/// As with the assistant title, actual rendered width selects the largest fitting tier.
/// Single-line text keeps the chapter header at the same height for every question.
private struct LearningAssignmentTitle: View {
  let title: String
  private var displayTitle: String { title.split(whereSeparator: \.isWhitespace).joined(separator: " ") }

  var body: some View {
    ViewThatFits(in: .horizontal) {
      Text(displayTitle).font(DS.font(.sectionTitle)).fixedSize()
      Text(displayTitle).font(DS.font(.cardTitle)).fixedSize()
      Text(displayTitle).font(DS.font(.body, weight: .semibold)).truncationMode(.tail)
    }
    .lineLimit(1)
    .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
    .accessibilityElement(children: .ignore).accessibilityLabel(title)
    .accessibilityIdentifier("assignment.chapter-title")
  }
}

struct LearningDiagnosisView: View {
  @Environment(AppStore.self) private var store

  var body: some View {
    if let diagnosis = store.latestDiagnosis {
      GeometryReader { geometry in
        if geometry.size.width >= 720 {
          padSummary(diagnosis)
        } else {
          LearningScroll(width: 1120) {
            ViewThatFits(in: .horizontal) {
              HStack(alignment: .top, spacing: 20) {
                analysis(diagnosis).frame(width: 560)
                nextSteps(diagnosis).frame(width: 320)
              }
              VStack(spacing: 20) {
                analysis(diagnosis)
                nextSteps(diagnosis)
              }
            }
          }
        }
      }
    } else {
      LearningScroll(width: 700) {
        EmptyStateView(title: "还没有诊断结果", message: "提交作业后，系统会对照教材依据定位卡点，并给出下一步复习建议。", icon: "ClipboardCheck")
        BCButton("去提交作业", icon: "checkmark.square") { store.go(.assignment) }
        BCButton("回到章节", style: .secondary) { store.go(.lesson) }
      }
    }
  }

  private func padSummary(_ diagnosis: Diagnosis) -> some View {
    let total = diagnosis.evaluated_questions ?? 0
    let wrong = diagnosis.incorrect_questions ?? -1
    let hasScores = total > 0 && wrong >= 0 && wrong <= total
    let rate = hasScores ? Double(wrong) / Double(total) : 0
    let chapterID = trainingChapterID(diagnosis)
    let hintChapter = hintChapterID(diagnosis)
    let providedHint = (diagnosis.summary_hint ?? diagnosis.hint).trimmingCharacters(in: .whitespacesAndNewlines)
    let hint = providedHint.isEmpty
      ? (diagnosis.stuck_point.isEmpty ? "本次没有额外提示，可以返回章节继续巩固。" : diagnosis.stuck_point)
      : providedHint

    return LearningScroll(width: 720, inlinePadding: 32, spacing: 24) {
      VStack(spacing: 28) {
        VStack(spacing: 16) {
          Text("本次错误率").font(DS.font(.pageTitle))
          ZStack {
            Circle().stroke(DS.primary.opacity(0.10), lineWidth: 10)
            if hasScores, wrong > 0 {
              Circle().trim(from: 0, to: rate)
                .stroke(DS.primary, style: StrokeStyle(lineWidth: 10, lineCap: .round))
                .rotationEffect(.degrees(-90))
            }
            Text(hasScores ? "\(Int((rate * 100).rounded()))%" : "—")
              .font(Font(DS.uiFont(.metric).withSize(72))).foregroundStyle(DS.primary)
              .lineLimit(1).minimumScaleFactor(0.6)
              .accessibilityLabel(hasScores
                ? "错误率 \(Int((rate * 100).rounded()))%，共 \(total) 道已判分\(diagnosis.grading_scope ?? "题目")"
                : "暂无已判分的题目")
              .accessibilityIdentifier("diagnosis.errorRate")
          }.frame(width: 240, height: 240)
        }
        if hasScores {
          HStack {
            Text("答对 \(total - wrong) 题").frame(maxWidth: .infinity)
              .accessibilityIdentifier("diagnosis.correctCount")
            Divider().frame(height: 26)
            Text("答错 \(wrong) 题").frame(maxWidth: .infinity)
              .accessibilityIdentifier("diagnosis.incorrectCount")
          }.font(DS.font(.sectionTitle))
        }
        Divider()
      }
      .padding(36).frame(maxWidth: .infinity)
      .background(DS.surface, in: RoundedRectangle(cornerRadius: 28))
      .overlay(RoundedRectangle(cornerRadius: 28).stroke(DS.border.opacity(0.7), lineWidth: 1))
      .accessibilityIdentifier("diagnosis.summary")

      VStack(alignment: .leading, spacing: 12) {
        HStack {
          Text("学习提示").font(DS.font(.sectionTitle))
          Spacer(minLength: 16)
          Button {
            openHintChapter(diagnosis)
          } label: {
            Label("查看章节", systemImage: "arrow.up.right")
              .font(DS.font(.smallButton)).foregroundStyle(DS.primary).buttonFrame()
          }.buttonStyle(.plain).disabled(hintChapter == nil)
            .accessibilityIdentifier("diagnosis.reviewChapter")
        }
        Text(hint).font(DS.font(.reading)).foregroundStyle(DS.secondary).lineSpacing(6)
          .fixedSize(horizontal: false, vertical: true)
          .accessibilityIdentifier("diagnosis.summaryHint")
      }
      .padding(24).frame(maxWidth: .infinity, alignment: .leading)
      .background(DS.surface, in: RoundedRectangle(cornerRadius: 28))

      HStack(spacing: 12) {
        BCButton("完成本节训练", style: .secondary, size: .primary) {
          guard let chapterID else { return }
          selectTrainingChapter(chapterID)
          store.learningState.studyReturnChapterID = chapterID
          store.go(.study)
        }.disabled(chapterID == nil)
          .accessibilityIdentifier("diagnosis.finishTraining")
        BCButton("进入闪卡复习") {
          guard let chapterID else { return }
          selectTrainingChapter(chapterID)
          store.learningState.agentFocusCardID = nil
          store.dailyPlanState.focus = nil
          store.go(.flashcards)
        }.disabled(chapterID == nil)
          .accessibilityIdentifier("diagnosis.flashcards")
      }.padding(.horizontal, 24).padding(.top, 16)
    }
  }

  private func trainingChapterID(_ diagnosis: Diagnosis) -> String? {
    let ids = Set(store.chapters.map(\.id))
    if let id = store.currentSource?.quizzes.first(where: { $0.id == diagnosis.assignment_id })?.chapter_id,
      ids.contains(id) { return id }
    if let id = store.selectedChapterID, ids.contains(id) { return id }
    return diagnosis.review_citations.first {
      $0.bookID == store.selectedBookID && ids.contains($0.chapterID)
    }?.chapterID
  }

  private func hintChapterID(_ diagnosis: Diagnosis) -> String? {
    let ids = Set(store.chapters.map(\.id))
    return diagnosis.review_citations.first {
      $0.bookID == store.selectedBookID && ids.contains($0.chapterID)
    }?.chapterID ?? trainingChapterID(diagnosis)
  }

  private func selectTrainingChapter(_ chapterID: String) {
    store.selectedChapterID = chapterID
    if !store.lessons.contains(where: { $0.id == store.selectedLessonID && $0.chapter_id == chapterID }) {
      store.selectedLessonID = store.lessons.first {
        $0.chapter_id == chapterID && $0.lesson_kind != "module_intro"
      }?.id
    }
    store.saveLocation()
  }

  private func openHintChapter(_ diagnosis: Diagnosis) {
    guard let chapterID = hintChapterID(diagnosis) else { return }
    if let lesson = store.lessons.first(where: {
      $0.chapter_id == chapterID && ["ready", "generated"].contains($0.status)
    }) {
      let chunks = Set(diagnosis.review_citations.map(\.chunkID))
      let block = lesson.blocks.firstIndex {
        !chunks.isDisjoint(with: $0.source_chunk_ids)
          || $0.citations.contains { chunks.contains($0.chunk_id) }
      } ?? lesson.blocks.firstIndex { block in
        diagnosis.knowledge_points.contains {
          block.title.localizedCaseInsensitiveContains($0) || block.content.localizedCaseInsensitiveContains($0)
        }
      }
      let key = "native.lesson.page.\(store.selectedCourseID ?? "none").\(store.selectedBookID ?? "none").\(lesson.id)"
      UserDefaults.standard.set(block.map { $0 + 1 } ?? 0, forKey: key)
    }
    store.dailyPlanState.focus = nil
    store.openChapterLearning(chapterID: chapterID)
  }

  private func analysis(_ diagnosis: Diagnosis) -> some View {
    VStack(alignment: .leading, spacing: 18) {
      BCCard {
        HStack(alignment: .top, spacing: 15) {
          StickerIcon("CheckCircle2", size: 46, glyphSize: 24)
          VStack(alignment: .leading, spacing: 12) {
            Text(diagnosis.mistake_recorded ? "需要再巩固" : "理解已到位")
              .font(DS.font(.cardTitle)).foregroundStyle(
                diagnosis.mistake_recorded ? DS.orange : DS.mint
              )
              .padding(.horizontal, 10).padding(.vertical, 7).background(
                diagnosis.mistake_recorded ? Color(hex: 0xfff0e2) : DS.mint.opacity(0.08),
                in: Capsule()
              )
              .overlay(
                Capsule().stroke(
                  diagnosis.mistake_recorded ? DS.orange.opacity(0.2) : DS.mint.opacity(0.2),
                  lineWidth: 1))
            Text(diagnosis.mistake_recorded ? "找到一个关键卡点" : "本题诊断完成").font(
              DS.font(.sectionTitle))
            Text(diagnosis.stuck_point).font(DS.font(.reading)).foregroundStyle(DS.secondary).lineSpacing(
              5)
          }
        }.padding(2)
      }
      VStack(alignment: .leading, spacing: 8) {
        BCProgress(value: 1)
        Text("诊断完成 · 已定位 \(diagnosis.knowledge_points.count) 个相关知识点").font(
          DS.font(.cardTitle)
        ).foregroundStyle(DS.secondary)
      }.padding(.horizontal, 3)
      BCCard {
        VStack(alignment: .leading, spacing: 20) {
          LearningSectionHeading(title: "诊断解析", subtitle: "为什么会卡住", icon: "BookOpenCheck")
          Text(diagnosis.result).font(DS.font(.reading)).foregroundStyle(DS.secondary).lineSpacing(7)
          if let citation = diagnosis.review_citations.first {
            HStack(alignment: .center, spacing: 12) {
              VStack(alignment: .leading, spacing: 4) {
                Text("教材依据 · 第 \(citation.page) 页").font(DS.font(.cardTitle))
                  .foregroundStyle(DS.primary)
                Text(citation.chapterTitle).font(DS.font(.cardTitle)).lineLimit(1)
                Text(citation.quote).font(DS.font(.reading)).foregroundStyle(DS.secondary).lineSpacing(6)
                  .lineLimit(2)
              }.frame(maxWidth: .infinity, alignment: .leading)
              Button("查看原文") { store.openCitation(citation) }.font(DS.font(.smallButton)).buttonFrame()
                .foregroundStyle(DS.primary).buttonStyle(.plain).fixedSize()
            }.padding(13).frame(maxWidth: .infinity, alignment: .leading).background(
              Color(hex: 0xf3f6fb), in: RoundedRectangle(cornerRadius: 14)
            )
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border, lineWidth: 1))

          } else {
            Text("这次没有找到可核对的教材页，建议回到章节重新查看上下文。")
              .font(DS.font(.caption)).foregroundStyle(DS.secondary).lineSpacing(5)
          }
        }
      }
    }
  }

  private func nextSteps(_ diagnosis: Diagnosis) -> some View {
    VStack(alignment: .leading, spacing: 18) {
      BCCard {
        VStack(alignment: .leading, spacing: 20) {
          LearningSectionHeading(title: "带着提示再想一次", subtitle: "建议先做这一步", icon: "Lightbulb")
          Text(diagnosis.hint).font(DS.font(.supporting)).foregroundStyle(DS.secondary).lineSpacing(4)
            .padding(14).frame(maxWidth: .infinity, alignment: .leading).background(
              Color(hex: 0xf1e7ff), in: RoundedRectangle(cornerRadius: 14)
            )
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(hex: 0xc9b8ff), lineWidth: 1))
          Text("相关知识点").font(DS.font(.cardTitle)).foregroundStyle(DS.secondary)
          LearningFlowLayout {
            ForEach(diagnosis.knowledge_points, id: \.self) { point in
              Text(point).font(DS.font(.body)).foregroundStyle(DS.secondary).padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color(hex: 0xf3f6fb), in: Capsule()).overlay(
                  Capsule().stroke(DS.border, lineWidth: 1))
            }
          }
        }
      }
      HStack(spacing: 12) {
        BCButton("重新作答", icon: "arrow.counterclockwise", style: .secondary) {
          store.go(.assignment)
        }
        BCButton("复习相关闪卡", style: .secondary) { store.go(.flashcards) }
      }
      BCButton(diagnosis.mistake_recorded ? "查看错题本" : "继续学习", icon: "bookmark") {
        store.go(diagnosis.mistake_recorded ? .mistakes : .lesson)
      }.accessibilityIdentifier("diagnosis.continue")
    }
  }
}
