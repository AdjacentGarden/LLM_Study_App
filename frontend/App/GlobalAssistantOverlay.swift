import AVFoundation
import PhotosUI
import SwiftUI

/// A conversation above the current page, with its own voice/text composer.
struct GlobalAssistantOverlay: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @Environment(\.scenePhase) private var scenePhase
  @State private var voice = AssistantVoiceInput.configured()
  @State private var keyboardTop = CGFloat.greatestFiniteMagnitude
  @State private var composerHeight: CGFloat = 58
  @State private var revealed = false
  @State private var showingHistory = false
  @State private var showingHistorySearch = false
  @State private var historyQuery = ""
  @FocusState private var historySearchFocused: Bool
  @State private var loadedScope: String?
  @State private var showingAttachmentChoices = false
  @State private var showingPhotos = false
  @State private var showingFiles = false
  @State private var cameraRequest: AssistantCameraRequest?
  @State private var cameraResult: (request: AssistantCameraRequest, result: Result<Data, Error>)?
  @State private var cameraPermissionTask: Task<Void, Never>?
  @State private var selectedPhotos: [PhotosPickerItem] = []
  @State private var addingAttachments = false
  @State private var attachmentImportStatus = "正在读取附件…"
  @State private var attachmentError: String?
  @State private var attachmentImportTask: Task<Void, Never>?
  @State private var attachmentImportID = UUID()
  private var history: AssistantHistoryStore { store.assistantHistory }
  private var ready: Bool {
    loadedScope != nil && loadedScope == store.assistantHistoryScope && history.isReady(for: loadedScope)
  }
  private var session: AssistantSession? { ready ? history.current : nil }
  private var sessionContext: AssistantSessionContext {
    session?.context ?? (ready ? history.archive.newContext : nil) ?? store.assistantSessionContext
  }
  private var guideMessages: [ChatMessage] { session?.messages ?? [] }
  private var courseLinks: [String: [String]] { session?.courseLinks ?? [:] }
  private var busy: Bool { ready && history.busy }
  private var attachments: [AssistantAttachment] { ready ? history.attachments : [] }
  private var storageIssue: String? {
    history.scope == store.assistantHistoryScope ? history.storageError : nil
  }
  private var title: String { session?.title ?? "AI问" }
  private var loadID: String { (store.assistantHistoryScope ?? "signed-out") + store.cloudState.epoch.uuidString }
  private var searchTerms: [String] { historyQuery.split(whereSeparator: \.isWhitespace).map(String.init) }
  private var matchingSessions: [AssistantSession] {
    guard ready else { return [] }
    let terms = searchTerms
    guard !terms.isEmpty else { return history.sessions }
    return history.sessions.filter { item in
      let fields = [item.title, item.context.courseTitle, item.context.chapterTitle]
        + item.messages.map(\.text)
        + item.messages.flatMap { ($0.attachments ?? []).map(\.filename) }
      return terms.allSatisfy { term in fields.contains { matches($0, term: term) } }
    }
  }

  private var assistantPanel: some View {
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
        if let error = session?.error {
          HStack(spacing: 12) {
            Text(error).appFont(.supporting).foregroundStyle(DS.danger)
              .frame(maxWidth: .infinity, alignment: .leading)
            Button("重试") { retry() }.disabled(busy)
          }.padding(12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        }
        if let issue = storageIssue {
          HStack {
            Text(issue).appFont(.supporting).foregroundStyle(DS.danger)
            Spacer()
            Button("重试") {
              Task {
                if ready { await history.flush() }
                else { await loadHistory() }
              }
            }
          }.padding(12).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        }
        conversation(bottomInset: composerHeight + bottomPadding + 16)
      }
      .padding(.horizontal, 16).padding(.top, 12)
      .frame(width: panelWidth, height: visibleHeight)
      .overlay(alignment: .bottom) {
        AssistantComposer(voice: voice, busy: busy || !ready,
          placeholder: sessionContext.discovery ? "想学什么？问问 AI…" : "问教材、问错题、问计划…",
          attachments: attachments, addingAttachments: addingAttachments,
          attachmentImportStatus: attachmentImportStatus,
          showAttachmentChoices: {
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.16)) { showingAttachmentChoices = true }
          },
          removeAttachment: removeAttachment, textHorizontalInset: textInset, send: send)
          .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { composerHeight = $0 }
          .background(alignment: .bottom) {
            // The footer stays translucent; messages can scroll behind the glass composer.
            ProgressiveMaterial()
              .padding(.horizontal, -16)
              .padding(.top, -36)
              .padding(.bottom, -bottomPadding)
          }
          .opacity(reduceMotion || revealed ? 1 : 0)
          .scaleEffect(reduceMotion || revealed ? 1 : 0.97, anchor: .center)
          .animation(reduceMotion ? nil : .easeOut(duration: 0.26), value: revealed)
          .padding(.horizontal, 16).padding(.bottom, bottomPadding)
      }
      .frame(maxWidth: .infinity, alignment: store.isPad ? .trailing : .center)
      .padding(.trailing, store.isPad ? 24 : 0)
      .accessibilityElement(children: .contain)
      .accessibilityAddTraits(.isModal)
      .allowsHitTesting(!showingHistory && !showingAttachmentChoices)
      .accessibilityHidden(showingHistory || showingAttachmentChoices)
    }
  }

  var body: some View {
    assistantPanel
    .ignoresSafeArea(.container, edges: .bottom)
    .ignoresSafeArea(.keyboard, edges: .bottom)
    .onAppear { revealed = true }
    .task(id: loadID) { await loadHistory() }
    .overlayPreferenceValue(AssistantComposerBoundsKey.self) { anchor in
      if showingAttachmentChoices, let anchor {
        attachmentChoices(anchor: anchor)
          .transition(.opacity)
      }
    }
    .overlay { historySidebar }
    .fullScreenCover(item: $cameraRequest, onDismiss: importCameraPhoto) { request in
      AssistantCameraPicker(complete: { result in
        cameraResult = (request, result)
        cameraRequest = nil
      }, cancel: { cameraRequest = nil })
        .ignoresSafeArea()
    }
    .photosPicker(isPresented: $showingPhotos, selection: $selectedPhotos,
      maxSelectionCount: max(1, AssistantAttachment.maximumCount - attachments.count), matching: .images)
    .fileImporter(isPresented: $showingFiles, allowedContentTypes: AssistantAttachment.allowedTypes,
      allowsMultipleSelection: true) { result in
      do {
        let urls = try result.get()
        beginAttachmentImport(count: urls.count) { index, progress in
          try await AssistantAttachmentImporter.file(urls[index], repository: store.repository, onProgress: progress)
        }
      } catch {
        if (error as? CocoaError)?.code != .userCancelled { attachmentError = error.localizedDescription }
      }
    }
    .onChange(of: selectedPhotos) { _, items in
      guard !items.isEmpty else { return }
      beginAttachmentImport(count: items.count) { index, progress in
        guard let data = try await items[index].loadTransferable(type: Data.self) else {
          throw ContentError.invalid("这张照片未能读取，请重新选择。")
        }
        return try await AssistantAttachmentImporter.photo(data, repository: store.repository, onProgress: progress)
      }
    }
    .alert("无法添加附件", isPresented: Binding(get: { attachmentError != nil },
      set: { if !$0 { attachmentError = nil } })) {
      Button("好", role: .cancel) { attachmentError = nil }
    } message: { Text(attachmentError ?? "请重新选择文件。") }
    .onChange(of: voice.draft) { _, _ in saveDraft() }
    .onChange(of: history.archive.selectedID) { _, _ in
      guard ready else { return }
      showingAttachmentChoices = false
      cancelAttachmentImport()
      voice.returnToText()
      voice.draft = history.draft
    }
    .onChange(of: scenePhase) { _, phase in
      if phase == .background { saveDraft(); Task { [history] in await history.flush() } }
    }
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
    .onDisappear {
      cameraPermissionTask?.cancel()
      cancelAttachmentImport()
      saveDraft()
      voice.returnToText()
      Task { [history] in await history.flush() }
    }
  }

  private func attachmentChoices(anchor: Anchor<CGRect>) -> some View {
    GeometryReader { geometry in
      let input = geometry[anchor]
      let width = min(280, input.width)
      let availableHeight = max(0, input.minY - 12)
      ZStack(alignment: .topLeading) {
        Color.clear.contentShape(Rectangle())
          .onTapGesture { dismissAttachmentChoices() }
          .accessibilityLabel("收起附件选项").accessibilityAddTraits(.isButton)
        VStack(spacing: 0) {
          Spacer(minLength: 0)
          // Preserve the compact card; large text or a short landscape window can scroll.
          ViewThatFits(in: .vertical) {
            attachmentMenu.fixedSize(horizontal: false, vertical: true)
            ScrollView { attachmentMenu }.scrollBounceBehavior(.basedOnSize)
          }
        }
        .frame(width: width, height: availableHeight, alignment: .bottom)
        .offset(x: input.minX)
      }
      .frame(width: geometry.size.width, height: geometry.size.height, alignment: .topLeading)
      .accessibilityAction(.escape) { dismissAttachmentChoices() }
    }
  }

  private var attachmentMenu: some View {
    AssistantAttachmentMenu(chooseCamera: chooseCamera, choosePhotos: {
      dismissAttachmentChoices()
      showingPhotos = true
    }, chooseFiles: {
      dismissAttachmentChoices()
      showingFiles = true
    })
  }

  private func dismissAttachmentChoices() {
    withAnimation(reduceMotion ? nil : .easeOut(duration: 0.16)) { showingAttachmentChoices = false }
  }

  private func chooseCamera() {
    dismissAttachmentChoices()
    guard ready, !busy, !addingAttachments,
      attachments.count < AssistantAttachment.maximumCount, let scope = loadedScope else { return }
    guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
      attachmentError = "当前设备不支持拍照，请在真机上使用相机，或选择已有图片。"
      return
    }
    let request = AssistantCameraRequest(scope: scope, epoch: store.cloudState.epoch,
      sessionID: history.archive.selectedID)
    cameraPermissionTask?.cancel()
    cameraPermissionTask = Task { @MainActor in
      defer { cameraPermissionTask = nil }
      let authorized: Bool
      switch AVCaptureDevice.authorizationStatus(for: .video) {
      case .authorized: authorized = true
      case .notDetermined: authorized = await AVCaptureDevice.requestAccess(for: .video)
      default: authorized = false
      }
      guard !Task.isCancelled, ready, !busy, !addingAttachments,
        attachments.count < AssistantAttachment.maximumCount,
        request.scope == store.assistantHistoryScope,
        request.epoch == store.cloudState.epoch, request.sessionID == history.archive.selectedID else { return }
      guard authorized else {
        attachmentError = "请在系统设置中允许 CloudPath 使用相机，再重新拍摄。"
        return
      }
      cameraResult = nil
      cameraRequest = request
    }
  }

  private func importCameraPhoto() {
    guard let capture = cameraResult else { return }
    cameraResult = nil
    guard ready, capture.request.scope == store.assistantHistoryScope,
      capture.request.epoch == store.cloudState.epoch,
      capture.request.sessionID == history.archive.selectedID else { return }
    switch capture.result {
    case .success(let data):
      beginAttachmentImport(count: 1) { _, progress in
        try await AssistantAttachmentImporter.photo(data, repository: store.repository, onProgress: progress)
      }
    case .failure(let error): attachmentError = error.localizedDescription
    }
  }

  private var header: some View {
    ZStack {
      Menu {
        Button("新对话", systemImage: "square.and.pencil") { newSession() }
          .disabled(!ready).accessibilityIdentifier("assistant-new-conversation")
      } label: {
        AssistantAdaptiveTitle(title: title).foregroundStyle(DS.text)
          .padding(.horizontal, 56).frame(maxWidth: .infinity)
          .contentShape(Rectangle()).contentTransition(.opacity)
          .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: title)
      }
      .menuIndicator(.hidden)
      .accessibilityHint("点按可新建对话").accessibilityIdentifier("assistant-session-title")
      HStack {
        Button {
          saveDraft()
          voice.returnToText()
          UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
          historySearchFocused = false
          showingHistorySearch = false
          historyQuery = ""
          showingHistory = true
        } label: {
          VStack(alignment: .leading, spacing: 4) {
            Capsule().frame(width: 19, height: 3)
            Capsule().frame(width: 13, height: 3)
          }.foregroundStyle(DS.text).frame(width: 44, height: 44)
            .contentShape(Circle()).modifier(AssistantGlassSurface(radius: 22))
        }.accessibilityLabel("历史对话").accessibilityIdentifier("assistant-history")
        Spacer()
        Button(action: close) {
          Image(systemName: "xmark").font(.system(size: 17, weight: .medium))
            .foregroundStyle(DS.text).frame(width: 44, height: 44).contentShape(Circle())
            .modifier(AssistantGlassSurface(radius: 22))
        }.accessibilityLabel("收起 AI 助手").accessibilityIdentifier("global-assistant-close")
      }
    }.frame(minHeight: 44)
  }

  private var historySidebar: some View {
    GeometryReader { geometry in
      let insets = UIApplication.shared.connectedScenes.compactMap {
        ($0 as? UIWindowScene)?.keyWindow?.safeAreaInsets
      }.first ?? .zero
      let topInset = max(geometry.safeAreaInsets.top, insets.top)
      let keyboardOverlap = max(0, geometry.frame(in: .global).maxY - keyboardTop)
      let bottomInset = max(insets.bottom, keyboardOverlap)
      ZStack(alignment: .leading) {
        if showingHistory {
          Rectangle().fill(.ultraThinMaterial)
            .overlay { Color.black.opacity(0.28) }
            .contentShape(Rectangle()).onTapGesture { dismissHistory() }
            .accessibilityLabel("关闭历史对话").accessibilityAddTraits(.isButton)
            .transition(.opacity).zIndex(0)
          historyPanel
            .padding(.top, topInset).padding(.bottom, bottomInset)
            .frame(width: min(380, geometry.size.width * 0.82), height: geometry.size.height)
            .background(Color(.systemBackground))
            .shadow(color: .black.opacity(0.12), radius: 24, x: 8)
            .simultaneousGesture(DragGesture(minimumDistance: 24).onEnded { value in
              if value.translation.width < -60,
                abs(value.translation.width) > abs(value.translation.height) { dismissHistory() }
            })
            .accessibilityElement(children: .contain).accessibilityAddTraits(.isModal)
            .transition(.move(edge: .leading)).zIndex(1)
        }
      }
      .frame(width: geometry.size.width, height: geometry.size.height, alignment: .leading)
      .animation(reduceMotion ? nil : .easeOut(duration: 0.24), value: showingHistory)
    }
    .ignoresSafeArea(.container).ignoresSafeArea(.keyboard, edges: .bottom)
    .allowsHitTesting(showingHistory).accessibilityHidden(!showingHistory)
  }

  private var historyPanel: some View {
    VStack(spacing: 0) {
      HStack {
        Text("历史对话").appFont(.sectionTitle, weight: .semibold).foregroundStyle(DS.text)
        Spacer()
        Button(action: toggleHistorySearch) {
          Image(systemName: "magnifyingglass").font(.system(size: 20, weight: .medium))
            .foregroundStyle(showingHistorySearch ? DS.primary : DS.secondary)
            .frame(width: 44, height: 44).contentShape(Rectangle())
        }.buttonStyle(.plain)
          .accessibilityLabel(showingHistorySearch ? "收起历史搜索" : "搜索历史对话")
          .accessibilityIdentifier("assistant-history-search-toggle")
      }
      .padding(.leading, 20).padding(.trailing, 8).padding(.top, 8)
      .padding(.bottom, showingHistorySearch ? 0 : 12)
      if showingHistorySearch {
        historySearchField.transition(.opacity)
      }
      Group {
        if !ready {
          if history.loading { ProgressView("正在读取历史对话…") }
          else { ContentUnavailableView("历史对话暂不可用", systemImage: "exclamationmark.bubble",
            description: Text(storageIssue ?? "请先登录，再打开对话。")) }
        } else if history.sessions.isEmpty {
          ContentUnavailableView("还没有历史对话", systemImage: "clock.arrow.circlepath",
            description: Text("发送第一条消息后会自动保存。"))
        } else if matchingSessions.isEmpty {
          ContentUnavailableView("没有找到相关对话", systemImage: "magnifyingglass",
            description: Text("试试其他标题、关键词或聊天内容。"))
        } else {
          List(matchingSessions) { item in
            Button { selectSession(item.id) } label: {
              HStack(alignment: .top, spacing: 12) {
                Image(systemName: item.id == history.archive.selectedID ? "checkmark.circle.fill" : "bubble.left")
                  .foregroundStyle(DS.primary).frame(width: 22).padding(.top, 3)
                VStack(alignment: .leading, spacing: 5) {
                  Text(item.title).appFont(.cardTitle).foregroundStyle(DS.text).lineLimit(1)
                  Text(historyPreview(item)).appFont(.supporting)
                    .foregroundStyle(DS.secondary).lineLimit(2)
                  Text(item.context.courseTitle + " · " + item.context.chapterTitle)
                    .appFont(.caption).foregroundStyle(DS.muted).lineLimit(1)
                  HStack {
                    Text(item.updatedAt, format: .dateTime.month().day().hour().minute())
                    if history.runningIDs.contains(item.id) { Text("正在回答…") }
                    else if item.pendingQuestion != nil { Text("回答未完成") }
                  }.appFont(.caption).foregroundStyle(DS.muted)
                }
                Spacer(minLength: 0)
              }.padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }.buttonStyle(.plain)
              .listRowBackground(item.id == history.archive.selectedID ? DS.primarySoft : Color(.secondarySystemGroupedBackground))
              .accessibilityIdentifier("assistant-history-\(item.id)")
          }
          .listStyle(.plain).scrollContentBackground(.hidden)
          .scrollDismissesKeyboard(.interactively)
          .accessibilityIdentifier("assistant-history-list")
        }
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
  }

  private var historySearchField: some View {
    HStack(spacing: 8) {
      Image(systemName: "magnifyingglass").foregroundStyle(DS.secondary)
      TextField("搜索标题或聊天内容", text: $historyQuery)
        .appFont(.supporting).foregroundStyle(DS.text).tint(DS.primary)
        .textInputAutocapitalization(.never).autocorrectionDisabled()
        .focused($historySearchFocused).submitLabel(.search)
        .onSubmit { historySearchFocused = false }
        .accessibilityLabel("搜索历史对话").accessibilityIdentifier("assistant-history-search")
        .task { @MainActor in
          await Task.yield()
          guard !Task.isCancelled, showingHistory, showingHistorySearch else { return }
          historySearchFocused = true
        }
      if !historyQuery.isEmpty {
        Button { historyQuery = "" } label: {
          Image(systemName: "xmark.circle.fill").foregroundStyle(DS.secondary)
            .frame(width: 32, height: 44).contentShape(Rectangle())
        }.accessibilityLabel("清除搜索")
      }
    }
    .padding(.leading, 12).padding(.trailing, historyQuery.isEmpty ? 12 : 4)
    .frame(minHeight: 44).background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
    .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 12)
  }

  private func toggleHistorySearch() {
    historySearchFocused = false
    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) {
      showingHistorySearch.toggle()
      if !showingHistorySearch { historyQuery = "" }
    }
  }

  private func matches(_ text: String, term: String) -> Bool {
    text.range(of: term, options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive]) != nil
  }

  private func historyPreview(_ item: AssistantSession) -> String {
    let terms = searchTerms
    if !terms.isEmpty, let hit = item.messages.last(where: { message in
      terms.contains { matches(message.text, term: $0) }
    }) { return hit.text }
    return item.messages.last?.text ?? item.draft
  }

  private func dismissHistory() {
    historySearchFocused = false
    showingHistory = false
  }

  private func conversation(bottomInset: CGFloat) -> some View {
    ScrollViewReader { proxy in
      GeometryReader { viewport in
        ScrollView {
          VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 16) {
              ForEach(guideMessages) { message in
                messageView(message).id(message.id)
              }
              if busy {
                HStack(spacing: 8) {
                  ProgressView().tint(DS.primary)
                  Text("正在准备回答…").appFont(.supporting).foregroundStyle(DS.secondary)
                }.padding(16).modifier(AssistantGlassSurface(radius: 22, clear: true))
              }
            }
            .padding(.top, 8).padding(.bottom, bottomInset)
            // Keep the scroll target after the inset so the last reply clears the input bar.
            Color.clear.frame(height: 1).id("guide-bottom")
          }
          .frame(maxWidth: .infinity, minHeight: viewport.size.height, alignment: .bottomLeading)
        }
        .scrollDismissesKeyboard(.interactively)
        .accessibilityIdentifier("guide-conversation")
        .onChange(of: guideMessages.count) { _, _ in
          withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
            proxy.scrollTo("guide-bottom", anchor: .bottom)
          }
        }
        .onChange(of: history.archive.selectedID) { _, _ in
          proxy.scrollTo("guide-bottom", anchor: .bottom)
        }
        .onAppear { proxy.scrollTo("guide-bottom", anchor: .bottom) }
        .onChange(of: viewport.size.height) { _, _ in
          proxy.scrollTo("guide-bottom", anchor: .bottom)
        }
        .onChange(of: bottomInset) { _, _ in
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
            .appFont(.caption, weight: .semibold).foregroundStyle(DS.primary)
        }
        if let files = message.attachments, !files.isEmpty {
          AssistantAttachmentStrip(attachments: files)
        }
        Text(message.text)
          .appFont(.body)
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
                  Text(book.catalogTitle).appFont(.supporting, weight: .semibold)
                  Text("\(book.grade) · \(book.subject) · \(book.version)")
                    .appFont(.caption).foregroundStyle(DS.secondary)
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
              Text(citation.chapterTitle).appFont(.supporting, weight: .semibold)
              Text("查看\(citation.printedPage.map { "教材第 \($0) 页" } ?? "PDF 第 \(citation.page) 页")原文")
                .appFont(.caption)
              Text(citation.quote).appFont(.caption).lineLimit(3)
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
    cancelAttachmentImport()
    saveDraft()
    voice.returnToText()
    store.globalAssistantOpen = false
  }

  private func loadHistory() async {
    let scope = store.assistantHistoryScope
    await history.load(scope: scope)
    guard !Task.isCancelled, scope == store.assistantHistoryScope, history.isReady(for: scope) else { return }
    loadedScope = scope
    voice.draft = history.draft
  }

  private func saveDraft() {
    guard ready else { return }
    history.setDraft(voice.draft, context: sessionContext)
  }

  private func selectSession(_ id: String) {
    cancelAttachmentImport()
    saveDraft()
    voice.returnToText()
    history.select(id)
    voice.draft = history.draft
    dismissHistory()
  }

  private func newSession() {
    cancelAttachmentImport()
    saveDraft()
    voice.returnToText()
    history.startNew(context: store.assistantSessionContext)
    voice.draft = ""
    dismissHistory()
  }

  private func send() {
    var question = voice.draft.trimmingCharacters(in: .whitespacesAndNewlines)
    guard ready, !busy, !addingAttachments, voice.phase == .text else { return }
    if question.isEmpty, let first = attachments.first {
      question = "请帮我分析附件“\(String(first.filename.prefix(120)))”中的内容。"
    }
    guard !question.isEmpty else { return }
    guard question.count <= 2000 else { attachmentError = "问题最多 2,000 字，请精简后发送。"; return }
    let owner = store.assistantHistoryScope
    let epoch = store.cloudState.epoch
    history.submit(question, context: sessionContext) { [store] context, messages, question in
      guard owner == store.assistantHistoryScope, epoch == store.cloudState.epoch else { throw CancellationError() }
      return try await store.answerHistorySession(context: context, history: messages, question: question)
    }
    voice.draft = history.draft
  }

  private func beginAttachmentImport(count: Int,
    load: @escaping @MainActor (Int, @escaping AssistantAttachmentImporter.Progress) async throws -> AssistantAttachment) {
    guard ready, !addingAttachments, count > 0 else { return }
    guard count + attachments.count <= AssistantAttachment.maximumCount else {
      selectedPhotos = []
      attachmentError = "每条消息最多添加 5 个附件，请减少选择数量。"
      return
    }
    let owner = loadedScope, epoch = store.cloudState.epoch
    let sessionID = history.archive.selectedID, context = sessionContext
    let token = UUID()
    attachmentImportID = token
    addingAttachments = true
    attachmentImportTask = Task { @MainActor in
      defer {
        if attachmentImportID == token {
          addingAttachments = false
          selectedPhotos = []
          attachmentImportTask = nil
        }
      }
      for index in 0..<count {
        do {
          attachmentImportStatus = "正在读取第 \(index + 1) / \(count) 个附件…"
          let file = try await load(index) { value in
            Task { @MainActor in
              guard attachmentImportID == token, !Task.isCancelled else { return }
              attachmentImportStatus = value
            }
          }
          guard !Task.isCancelled, attachmentImportID == token, ready,
            owner == store.assistantHistoryScope, epoch == store.cloudState.epoch,
            sessionID == history.archive.selectedID else {
            try? FileManager.default.removeItem(at: store.attachmentURL(file.path))
            if let path = file.thumbnailPath { try? FileManager.default.removeItem(at: store.attachmentURL(path)) }
            if let path = file.textPath { try? FileManager.default.removeItem(at: store.attachmentURL(path)) }
            return
          }
          history.setAttachments(history.attachments + [file], context: context)
        } catch is CancellationError { return }
        catch {
          guard attachmentImportID == token, !Task.isCancelled else { return }
          attachmentError = error.localizedDescription
          return
        }
      }
    }
  }

  private func cancelAttachmentImport() {
    attachmentImportTask?.cancel()
    attachmentImportTask = nil
    attachmentImportID = UUID()
    addingAttachments = false
    selectedPhotos = []
  }

  private func removeAttachment(_ id: String) {
    guard ready, !addingAttachments else { return }
    history.setAttachments(attachments.filter { $0.id != id }, context: sessionContext)
  }

  private func retry() {
    let owner = store.assistantHistoryScope
    let epoch = store.cloudState.epoch
    history.retry { [store] context, messages, question in
      guard owner == store.assistantHistoryScope, epoch == store.cloudState.epoch else { throw CancellationError() }
      return try await store.answerHistorySession(context: context, history: messages, question: question)
    }
  }
}

private struct AssistantCameraRequest: Identifiable {
  let id = UUID()
  let scope: String
  let epoch: UUID
  let sessionID: String?
}

/// Pick the largest tier that fits the space between the two header controls.
/// Intrinsic text width handles Chinese, Latin, numbers and Dynamic Type alike.
private struct AssistantAdaptiveTitle: View {
  let title: String

  var body: some View {
    ViewThatFits(in: .horizontal) {
      Text(title).appFont(.pageTitle).fixedSize()
      Text(title).appFont(.sectionTitle).fixedSize()
      Text(title).appFont(.cardTitle)
        .lineLimit(1).truncationMode(.tail)
    }
    .frame(maxWidth: .infinity)
    .accessibilityElement(children: .ignore).accessibilityLabel(title)
  }
}

private struct AssistantMessageSurface: ViewModifier {
  let user: Bool
  func body(content: Content) -> some View {
    content.modifier(AssistantGlassSurface(radius: 24, clear: false,
      tint: user ? DS.primary : nil))
  }
}
