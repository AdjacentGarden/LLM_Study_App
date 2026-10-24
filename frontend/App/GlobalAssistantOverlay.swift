import SwiftUI

/// The floating guide has its own presentation, separate from the lesson's question sheet.
struct GlobalAssistantOverlay: View {
  @Environment(AppStore.self) private var store
  @State private var input = ""
  @State private var keyboardTop = CGFloat.greatestFiniteMagnitude
  @State private var guideMessages: [ChatMessage] = []
  @State private var courseLinks: [String: [String]] = [:]
  @State private var busy = false
  @State private var error: String?
  @State private var request: Task<Void, Never>?
  private var discovery: Bool { store.route == .community }
  private var concepts: [String] { store.activeLesson?.key_concepts ?? [] }
  private var suggestions: [String] {
    let first = concepts.first ?? store.chapterTitle
    return [
      "用一句话解释“\(first)”",
      concepts.count > 1 ? "比较“\(first)”和“\(concepts[1])”" : "围绕“\(store.chapterTitle)”给我出一道题",
    ]
  }
  private var pageLabel: String {
    guard let chapter = store.chapters.first(where: { $0.id == store.selectedChapterID }) else {
      return "当前课程"
    }
    let start = chapter.printed_page_start ?? chapter.page_start
    let end = chapter.printed_page_end ?? chapter.page_end
    return start > 0 ? "原书 \(start)\(end != start ? "–\(end)":"") 页" : "当前课程"
  }
  var body: some View {
    GeometryReader { geometry in
      let visibleHeight = max(
        180, min(geometry.size.height, keyboardTop - geometry.frame(in: .global).minY))
      let width = store.isPad ? min(420, geometry.size.width - 64) : geometry.size.width
      ZStack(alignment: store.isPad ? .topTrailing : .bottom) {
        VStack(spacing: 12) {
          HStack(spacing: 8) {
            AssetImage("/assets/brand/ai-assistant/avatar-v1.png").frame(width: 42, height: 34)
            Text("AI 导学助手").font(DS.font(20.8, weight: .bold))
            Spacer(minLength: 0)
            Button {
              close()
            } label: {
              BCGlyph("X").frame(width: 18, height: 18).frame(width: 44, height: 44).background(
                .white, in: RoundedRectangle(cornerRadius: 12)
              ).overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.border, lineWidth: 1))
            }.accessibilityLabel("收起 AI 助手").accessibilityIdentifier("global-assistant-close")
          }
          Text(error ?? (store.cloud == nil ? "离线检索当前教材原文" : "基于当前教材原文回答")).font(DS.font(20)).foregroundStyle(
            error == nil ? DS.secondary : DS.danger
          ).frame(maxWidth: .infinity).lineLimit(2)
          ScrollViewReader { proxy in
            ScrollView {
              VStack(alignment: .leading, spacing: 16) {
                VStack(spacing: 8) {
                  Text("下拉查看历史对话").font(DS.font(20)).foregroundStyle(DS.secondary)
                  Text(discovery ? "想学什么？我来帮你选课" : "Hi，我是你的“AI学习助手”～").font(
                    DS.font(16, weight: .bold))
                  Text(discovery ? "输入课程名、学科、年级或学习目标，我会从发现页课程中推荐。" : "学习相关的问题，都可以问我哦。").font(
                    DS.font(20, weight: .bold)
                  ).foregroundStyle(DS.secondary)
                }.multilineTextAlignment(.center).frame(maxWidth: .infinity).padding(.top, 18)
                if !discovery {
                  VStack(alignment: .leading, spacing: 4) {
                    HStack {
                      Text("当前课程").font(DS.font(16, weight: .bold))
                      Spacer()
                      Text(pageLabel).font(DS.font(20, weight: .bold))
                    }.foregroundStyle(DS.secondary)
                    Text(store.activeLesson?.title ?? store.chapterTitle).font(
                      DS.font(16, weight: .bold)
                    ).fixedSize(horizontal: false, vertical: true)
                  }
                  if guideMessages.isEmpty, input.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                      Text("你可能感兴趣").font(DS.font(20)).foregroundStyle(DS.secondary).frame(
                        maxWidth: .infinity)
                      ForEach(Array(suggestions.enumerated()), id: \.offset) { index, prompt in
                        Button {
                          input = prompt
                        } label: {
                          HStack(spacing: 0) {
                            BCGlyph("MessageCircle").frame(width: 15, height: 15)
                            Text(prompt).font(DS.font(20, weight: .bold)).multilineTextAlignment(
                              .leading)
                          }.padding(.horizontal, 12).padding(.vertical, 10.5).background(
                            index == 0 ? Color(hex: 0xf3f6fc) : Color(hex: 0xf3fff9),
                            in: RoundedRectangle(cornerRadius: 12)
                          ).overlay(
                            RoundedRectangle(cornerRadius: 12).stroke(DS.border, lineWidth: 1))
                        }.buttonStyle(.plain)
                      }
                    }
                  }
                }
                ForEach(guideMessages) { message in
                  VStack(alignment: .leading, spacing: 8) {
                    if message.role != "user" {
                      Text("AI 导学助手").font(DS.font(14, weight: .bold)).foregroundStyle(DS.secondary)
                    }
                    Text(message.text).font(DS.font(20)).textSelection(.enabled).fixedSize(
                      horizontal: false, vertical: true)
                    if let ids = courseLinks[message.id] {
                      ForEach(store.community.filter { ids.contains($0.id) }) { book in
                        Button {
                          close()
                          store.selectedCommunityID = book.id
                          store.go(.communityBook)
                        } label: {
                          HStack {
                            VStack(alignment: .leading, spacing: 4) {
                              Text(book.catalogTitle).font(DS.font(16, weight: .bold))
                              Text("\(book.grade) · \(book.subject) · \(book.version)").font(
                                DS.font(14)
                              ).foregroundStyle(DS.secondary)
                            }
                            Spacer()
                            BCGlyph("ChevronRight").frame(width: 16, height: 16)
                          }.padding(12).background(.white, in: RoundedRectangle(cornerRadius: 12))
                            .overlay(
                              RoundedRectangle(cornerRadius: 12).stroke(DS.border, lineWidth: 1))
                        }.buttonStyle(.plain).accessibilityLabel("查看课程：" + book.catalogTitle)
                      }
                    }
                    ForEach(message.citations) { citation in
                      Button {
                        close()
                        store.openCitation(citation)
                      } label: {
                        VStack(alignment: .leading, spacing: 4) {
                          Text(citation.chapterTitle).font(DS.font(16, weight: .bold))
                          Text(
                            "查看\(citation.printedPage.map { "教材第 \($0) 页" } ?? "PDF 第 \(citation.page) 页")原文"
                          ).font(DS.font(14))
                          Text(citation.quote).font(DS.font(14)).lineLimit(3)
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(12).background(
                          DS.primarySoft, in: RoundedRectangle(cornerRadius: 12))
                      }
                    }
                  }.padding(12).frame(maxWidth: .infinity, alignment: .leading).background(
                    message.role == "user" ? DS.primarySoft : Color(hex: 0xf4f7fb),
                    in: RoundedRectangle(cornerRadius: 18)
                  ).id(message.id)
                }
                if busy {
                  HStack {
                    ProgressView()
                    Text("正在准备回答…").font(DS.font(16))
                  }
                }
                if guideMessages.isEmpty {
                  HStack(spacing: 18) {
                    ForEach(
                      discovery ? ["按学科推荐", "按目标推荐", "查看全部课程"] : ["本节讲解", "举例理解", "随堂测验"],
                      id: \.self
                    ) { mode in
                      Button(mode) { input = mode }.font(DS.font(20, weight: .bold))
                        .foregroundStyle(DS.secondary)
                    }
                  }.padding(.top, 22)
                }
              }.padding(.bottom, 16)
            }.onChange(of: guideMessages.count) { _, _ in
              if let id = guideMessages.last?.id { proxy.scrollTo(id, anchor: .bottom) }
            }
          }
          HStack(spacing: 4) {
            TextField(
              "", text: $input,
              prompt: Text(discovery ? "输入课程名、学科或学习目标..." : "问教材、问错题、问计划...").foregroundColor(
                DS.secondary)
            ).font(DS.font(20)).submitLabel(.send).onSubmit { send() }.accessibilityIdentifier(
              "guide-input")
            Button {
              send()
            } label: {
              BCGlyph("SendHorizontal", color: .white).frame(width: 20, height: 20).frame(
                width: 46, height: 46
              ).background(DS.primary, in: RoundedRectangle(cornerRadius: 17)).shadow(
                color: DS.text.opacity(0.14), radius: 4, y: 2)
            }.disabled(busy || input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
              .accessibilityLabel("发送给 AI 助手").accessibilityIdentifier("guide-send")
          }.padding(4).padding(.leading, 10).frame(minHeight: 54).background(
            Color(hex: 0xf4f7fb), in: Capsule()
          ).overlay(Capsule().stroke(DS.border, lineWidth: 1))
        }.padding(14).padding(
          .bottom,
          store.isPad
            ? 0
            : max(
              geometry.safeAreaInsets.bottom,
              UIApplication.shared.connectedScenes.compactMap {
                ($0 as? UIWindowScene)?.keyWindow?.safeAreaInsets.bottom
              }.first ?? 0)
        )
        .frame(
          width: width,
          height: store.isPad ? max(120, visibleHeight - 33) : max(120, visibleHeight - 44)
        )
        .background(.white, in: RoundedRectangle(cornerRadius: 28))
        .clipShape(RoundedRectangle(cornerRadius: 28))
        .compositingGroup().shadow(
          color: DS.text.opacity(0.16), radius: 24, y: 8
        )
        .padding(.trailing, store.isPad ? 32 : 0).padding(.top, store.isPad ? 13 : 0)
      }.frame(
        width: geometry.size.width, height: visibleHeight,
        alignment: store.isPad ? .topTrailing : .bottom
      ).accessibilityAddTraits(.isModal)
    }.ignoresSafeArea(.container, edges: .bottom)
      .onReceive(
        NotificationCenter.default.publisher(for: UIResponder.keyboardWillChangeFrameNotification)
      ) { notification in
        keyboardTop =
          (notification.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect)?.minY
          ?? .greatestFiniteMagnitude
      }
      .onReceive(
        NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification)
      ) { _ in keyboardTop = .greatestFiniteMagnitude }
      .onDisappear { request?.cancel() }
  }
  private func close() {
    request?.cancel()
    store.globalAssistantOpen = false
  }
  private func send() {
    let question = input.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !question.isEmpty, !busy else { return }
    input = ""
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
          guideMessages.append(
            ChatMessage(role: "assistant", text: response.answer, citations: response.citations))
        }
      } catch {
        if !Task.isCancelled { self.error = error.localizedDescription }
      }
      busy = false
    }
  }
}
