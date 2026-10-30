import SwiftUI

/// A conversation above the current page, with its own voice/text composer.
struct GlobalAssistantOverlay: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var voice = AssistantVoiceInput.configured()
  @State private var keyboardTop = CGFloat.greatestFiniteMagnitude
  @State private var revealed = false
  @State private var guideMessages: [ChatMessage] = []
  @State private var courseLinks: [String: [String]] = [:]
  @State private var busy = false
  @State private var error: String?
  @State private var request: Task<Void, Never>?
  private var discovery: Bool { store.route == .community }
  private var suggestions: [String] {
    if discovery { return ["按学科推荐", "按目标推荐", "查看全部课程"] }
    let concepts = store.activeLesson?.key_concepts ?? []
    let first = concepts.first ?? store.chapterTitle
    return ["用一句话解释“\(first)”",
      concepts.count > 1 ? "比较“\(first)”和“\(concepts[1])”" : "围绕“\(store.chapterTitle)”给我出一道题"]
  }
  private var context: String {
    discovery ? "帮你发现适合的课程" : (store.activeLesson?.title ?? store.chapterTitle)
  }

  var body: some View {
    GeometryReader { geometry in
      let visibleHeight = max(180,
        voice.isVoiceMode ? geometry.size.height :
          min(geometry.size.height, keyboardTop - geometry.frame(in: .global).minY))
      // Voice controls fade in at the bottom even while the keyboard is dismissing.
      let keyboardVisible = !voice.isVoiceMode && keyboardTop < geometry.frame(in: .global).maxY
      let windowInset = UIApplication.shared.connectedScenes.compactMap {
        ($0 as? UIWindowScene)?.keyWindow?.safeAreaInsets.bottom
      }.first ?? 0
      let bottomPadding = keyboardVisible ? 12 : max(geometry.safeAreaInsets.bottom, windowInset) + 12
      let panelWidth = store.isPad ? min(460, geometry.size.width - 48) : geometry.size.width
      // The reference composer spans about 84% of the phone, centered horizontally.
      let textInset = max(0, panelWidth * 0.08 - 16)
      VStack(spacing: 16) {
        header
          .opacity(reduceMotion || revealed ? 1 : 0)
          .animation(reduceMotion ? nil : .easeOut(duration: 0.18).delay(0.04), value: revealed)
        if let error {
          Text(error).font(.footnote).foregroundStyle(DS.danger)
            .frame(maxWidth: .infinity, alignment: .leading).padding(12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        }
        conversation
        AssistantComposer(voice: voice, busy: busy,
          placeholder: discovery ? "想学什么？问问 AI…" : "问教材、问错题、问计划…",
          prompts: suggestions, textHorizontalInset: textInset, send: send)
          .opacity(reduceMotion || revealed ? 1 : 0)
          .scaleEffect(reduceMotion || revealed ? 1 : 0.97, anchor: .center)
          .animation(reduceMotion ? nil : .easeOut(duration: 0.26), value: revealed)
      }
      .padding(.horizontal, 16).padding(.top, 12).padding(.bottom, bottomPadding)
      .frame(width: panelWidth, height: visibleHeight)
      .frame(maxWidth: .infinity, alignment: store.isPad ? .trailing : .center)
      .padding(.trailing, store.isPad ? 24 : 0)
      .accessibilityElement(children: .contain)
      .accessibilityAddTraits(.isModal)
    }
    .ignoresSafeArea(.container, edges: .bottom)
    .ignoresSafeArea(.keyboard, edges: .bottom)
    .onAppear { revealed = true }
    .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillChangeFrameNotification)) {
      notification in
      let top = (notification.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect)?.minY
        ?? .greatestFiniteMagnitude
      withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) { keyboardTop = top }
    }
    .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification)) { _ in
      withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) {
        keyboardTop = .greatestFiniteMagnitude
      }
    }
    .onDisappear { request?.cancel(); voice.returnToText() }
  }

  private var header: some View {
    HStack(spacing: 10) {
      HStack(spacing: 9) {
        Image(systemName: "sparkles").font(.system(size: 18)).foregroundStyle(DS.primary)
        VStack(alignment: .leading, spacing: 3) {
          Text("AI 导学助手").font(.subheadline.weight(.semibold)).foregroundStyle(DS.text)
          Text(context).font(.caption).foregroundStyle(DS.secondary).lineLimit(1)
        }
      }
      .padding(.horizontal, 14).padding(.vertical, 10)
      .modifier(AssistantGlassSurface(radius: 24))
      Spacer(minLength: 8)
      Button(action: close) {
        Image(systemName: "xmark").font(.system(size: 17, weight: .medium))
          .foregroundStyle(DS.text).frame(width: 44, height: 44)
          .modifier(AssistantGlassSurface(radius: 22))
      }.accessibilityLabel("收起 AI 助手").accessibilityIdentifier("global-assistant-close")
    }
  }

  private var conversation: some View {
    ScrollViewReader { proxy in
      GeometryReader { viewport in
        ScrollView {
          VStack(alignment: .leading, spacing: 16) {
            ForEach(guideMessages) { message in
              messageView(message).id(message.id)
            }
            if busy {
              HStack(spacing: 8) {
                ProgressView().tint(DS.primary)
                Text("正在准备回答…").font(.subheadline).foregroundStyle(DS.secondary)
              }.padding(16).modifier(AssistantGlassSurface(radius: 22, clear: true))
            }
            Color.clear.frame(height: 1).id("guide-bottom")
          }
          .padding(.top, 8).padding(.bottom, 4)
          .frame(maxWidth: .infinity, minHeight: viewport.size.height, alignment: .bottomLeading)
        }
        .scrollDismissesKeyboard(.interactively)
        .onChange(of: guideMessages.count) { _, _ in
          withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
            proxy.scrollTo("guide-bottom", anchor: .bottom)
          }
        }
        .onChange(of: viewport.size.height) { _, _ in
          proxy.scrollTo("guide-bottom", anchor: .bottom)
        }
      }
    }
  }

  private func messageView(_ message: ChatMessage) -> some View {
    let user = message.role == "user"
    return HStack(alignment: .top, spacing: 0) {
      if user { Spacer(minLength: 44) }
      VStack(alignment: .leading, spacing: 10) {
        if !user {
          Label("AI 导学助手", systemImage: "sparkles")
            .font(.caption.weight(.semibold)).foregroundStyle(DS.primary)
        }
        Text(message.text)
          .font(.custom("BookCourseNotoSansSC-Regular", size: 17, relativeTo: .body))
          .foregroundStyle(user ? .white : DS.text)
          .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        if let ids = courseLinks[message.id] {
          ForEach(store.community.filter { ids.contains($0.id) }) { book in
            Button {
              close()
              store.selectedCommunityID = book.id
              store.go(.communityBook)
            } label: {
              HStack {
                VStack(alignment: .leading, spacing: 4) {
                  Text(book.catalogTitle).font(.subheadline.weight(.semibold))
                  Text("\(book.grade) · \(book.subject) · \(book.version)")
                    .font(.caption).foregroundStyle(DS.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.caption)
              }.foregroundStyle(DS.text).padding(12)
                .background(.white.opacity(0.7), in: RoundedRectangle(cornerRadius: 14))
            }.buttonStyle(.plain).accessibilityLabel("查看课程：" + book.catalogTitle)
          }
        }
        ForEach(message.citations) { citation in
          Button { close(); store.openCitation(citation) } label: {
            VStack(alignment: .leading, spacing: 4) {
              Text(citation.chapterTitle).font(.subheadline.weight(.semibold))
              Text("查看\(citation.printedPage.map { "教材第 \($0) 页" } ?? "PDF 第 \(citation.page) 页")原文")
                .font(.caption)
              Text(citation.quote).font(.caption).lineLimit(3)
            }.foregroundStyle(DS.primary).frame(maxWidth: .infinity, alignment: .leading)
              .padding(12).background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 14))
          }.buttonStyle(.plain)
        }
      }
      .padding(16)
      .modifier(AssistantMessageSurface(user: user))
      if !user { Spacer(minLength: 20) }
    }
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier(user ? "guide-user-message" : "guide-ai-message")
  }

  private func close() {
    request?.cancel()
    voice.returnToText()
    store.globalAssistantOpen = false
  }

  private func send() {
    let question = voice.draft.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !question.isEmpty, !busy, voice.phase == .text else { return }
    voice.draft = ""
    busy = true
    error = nil
    guideMessages.append(ChatMessage(role: "user", text: question))
    request = Task { @MainActor in
      do {
        try Task.checkCancellation()
        if discovery {
          let answer = answerCommunityCourseQuery(question, books: store.community)
          let message = ChatMessage(role: "assistant", text: answer.text)
          courseLinks[message.id] = answer.courseIds
          guideMessages.append(message)
        } else {
          let response = try await store.answerAssistant(
            question: question, bookID: store.selectedBookID ?? "",
            bookIDs: store.currentCourse?.resourceIDs ?? [], chapterID: store.selectedChapterID,
            sources: store.sources, history: Array(guideMessages.suffix(8)))
          try Task.checkCancellation()
          guideMessages.append(ChatMessage(role: "assistant", text: response.answer, citations: response.citations))
        }
      } catch {
        if !Task.isCancelled { self.error = error.localizedDescription }
      }
      busy = false
    }
  }
}

private struct AssistantMessageSurface: ViewModifier {
  let user: Bool
  func body(content: Content) -> some View {
    content.modifier(AssistantGlassSurface(radius: 24, clear: !user,
      tint: user ? DS.primary : nil))
  }
}
