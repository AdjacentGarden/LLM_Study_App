import AVFoundation
import PhotosUI
import SwiftUI

private struct AssistantSessionDeletion {
  var id: String
  var title: String
  var scope: String
  var epoch: UUID
  var isRunning: Bool
}

/// A conversation above the current page, with its own voice/text composer.
struct GlobalAssistantOverlay: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @Environment(\.scenePhase) private var scenePhase
  @State private var voice = AssistantVoiceInput.configured()
  @State private var keyboardTop = CGFloat.greatestFiniteMagnitude
  @State private var keyboardDismissalID = 0
  @State private var showingHistory = false
  @State private var showingHistorySearch = false
  @State private var historyQuery = ""
  @State private var pendingSessionDeletion: AssistantSessionDeletion?
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
  @State private var preparingSubmission = false
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
    store.assistantSessionContext
  }
  private var guideMessages: [ChatMessage] { session?.messages ?? [] }
  private var courseLinks: [String: [String]] { session?.courseLinks ?? [:] }
  private var busy: Bool { ready && history.busy }
  private var readOnly: Bool { session?.acceptsNewMessages == false }
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
    AssistantConversationPanel(isPad: store.isPad, voiceMode: voice.isVoiceMode,
      keyboardTop: keyboardTop, dismissKeyboard: dismissComposerKeyboard) {
        header
    } content: { bottomInset in
      VStack(spacing: 16) {
        if readOnly {
          Text(session?.isReadOnly == true
            ? "此对话已达到上下文上限，无法继续对话。请新建对话。"
            : "当前上下文已达到 900K，正在完成本次任务；暂不能发送新消息。")
            .appFont(.supporting).foregroundStyle(DS.secondary).padding(12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        }
        if let error = session?.error, !readOnly {
          HStack(spacing: 12) {
            Text(error).appFont(.supporting).foregroundStyle(DS.danger)
              .frame(maxWidth: .infinity, alignment: .leading)
            Button("重试") { retry() }.buttonFrame().disabled(busy)
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
            }.buttonFrame()
          }.padding(12).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        }
        conversation(bottomInset: bottomInset)
      }
    } composer: { textInset in
        AssistantComposer(voice: voice, keyboardDismissalID: keyboardDismissalID, busy: busy || preparingSubmission || !ready || readOnly,
          placeholder: sessionContext.discovery ? "想学什么？问问 AI…" : "问教材、问错题、问计划…",
          attachments: attachments, addingAttachments: addingAttachments,
          attachmentImportStatus: attachmentImportStatus,
          showAttachmentChoices: {
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.16)) { showingAttachmentChoices = true }
          },
          removeAttachment: removeAttachment, textHorizontalInset: textInset,
          contextTokens: session?.contextTokens ?? 0, contextEstimated: session?.contextEstimated ?? true,
          send: send)
          .disabled(readOnly)
    }
    .allowsHitTesting(!showingHistory && !showingAttachmentChoices)
    .accessibilityHidden(showingHistory || showingAttachmentChoices)
  }

  var body: some View {
    assistantPanel
    .ignoresSafeArea(.container, edges: .bottom)
    .ignoresSafeArea(.keyboard, edges: .bottom)
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
    .alert("已达会话数上限，请删除无用历史会话", isPresented: Binding(get: { history.showingSessionLimit }, set: { history.showingSessionLimit = $0 })) {
      Button("管理历史会话") { showingHistory = true }
      Button("取消", role: .cancel) { }
    }
    .alert("此对话已达到上下文上限", isPresented: Binding(
      get: { history.capacityAlertSessionID != nil && history.capacityAlertSessionID == history.archive.selectedID },
      set: { if !$0 { history.capacityAlertSessionID = nil } })) {
      Button("新建对话") { history.capacityAlertSessionID = nil; newSession() }
    } message: { Text("无法继续对话，请新建对话。") }
    .onChange(of: store.agent.active) { _, run in
      if let run { history.recordRun(run) }
    }
    .alert("AI 助手", isPresented: Binding(get: { attachmentError != nil },
      set: { if !$0 { attachmentError = nil } })) {
      Button("好", role: .cancel) { attachmentError = nil }
    } message: { Text(attachmentError ?? "请重新选择文件。") }
    .alert("删除这段对话？", isPresented: Binding(get: { pendingSessionDeletion != nil },
      set: { if !$0 { pendingSessionDeletion = nil } }), presenting: pendingSessionDeletion) { item in
      Button("取消", role: .cancel) { pendingSessionDeletion = nil }
        .accessibilityIdentifier("assistant-history-delete-cancel")
      Button("删除", role: .destructive) { confirmSessionDeletion(item) }
        .accessibilityIdentifier("assistant-history-delete-confirm")
    } message: { item in
      Text("将移除「\(item.title)」的聊天记录。\(item.isRunning ? "正在进行的回答会停止。" : "")已保存的笔记等学习内容会保留。")
    }
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
    .opacity(store.assistantState.screenshotExcludesAssistant ? 0 : 1)
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
    AssistantConversationHeader(title: title) {
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
          }.foregroundStyle(DS.text).iconButtonFrame()
            .contentShape(Circle()).modifier(AssistantGlassSurface(radius: 22))
        }.accessibilityLabel("历史对话").accessibilityIdentifier("assistant-history")
        Spacer()
        Button(action: newSession) {
          Image(systemName: "square.and.pencil").font(.system(size: 18, weight: .medium))
            .foregroundStyle(DS.text).iconButtonFrame().contentShape(Circle())
            .modifier(AssistantGlassSurface(radius: 22))
        }.disabled(!ready || preparingSubmission)
          .accessibilityLabel("新建对话").accessibilityIdentifier("assistant-new-conversation")
        Button(action: close) {
          Image(systemName: "xmark").font(.system(size: 17, weight: .medium))
            .foregroundStyle(DS.text).iconButtonFrame().contentShape(Circle())
            .modifier(AssistantGlassSurface(radius: 22))
        }.accessibilityLabel("收起 AI 助手").accessibilityIdentifier("global-assistant-close")
      }
    }
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
            .iconButtonFrame().contentShape(Rectangle())
        }.buttonStyle(.plain)
          .accessibilityLabel(showingHistorySearch ? "收起历史搜索" : "搜索历史对话")
          .accessibilityIdentifier("assistant-history-search-toggle")
      }
      .padding(.leading, 20).padding(.trailing, 8).padding(.top, 8)
      .padding(.bottom, showingHistorySearch ? 0 : 12)
      if showingHistorySearch {
        historySearchField.transition(.opacity)
      }
      Button(action: newSession) {
        Label("新建对话", systemImage: "square.and.pencil")
          .appFont(.supporting, weight: .semibold).foregroundStyle(DS.primary)
          .frame(maxWidth: .infinity, minHeight: 44)
          .background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 14))
      }.buttonStyle(.plain).disabled(!ready || preparingSubmission)
        .padding(.horizontal, 16).padding(.bottom, 12)
        .accessibilityIdentifier("assistant-history-new")
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
            HStack(spacing: 4) {
            Button { selectSession(item.id) } label: {
              HStack(alignment: .top, spacing: 12) {
                Image(systemName: item.id == history.archive.selectedID ? "checkmark.circle.fill" : "bubble.left")
                  .foregroundStyle(DS.primary).frame(width: 22).padding(.top, 3)
                VStack(alignment: .leading, spacing: 5) {
                  Text(item.title + (item.isReadOnly ? " · 只读" : "")).appFont(.cardTitle).foregroundStyle(DS.text).lineLimit(1)
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
              .accessibilityIdentifier("assistant-history-\(item.id)")
              Button { requestSessionDeletion(item) } label: {
                Image(systemName: "trash").font(.system(size: 17))
                  .foregroundStyle(DS.secondary).iconButtonFrame().contentShape(Rectangle())
              }.buttonStyle(.plain)
                .accessibilityLabel("删除对话：\(item.title)")
                .accessibilityIdentifier("assistant-history-delete-\(item.id)")
            }
              .listRowBackground(item.id == history.archive.selectedID ? DS.primarySoft : Color(.secondarySystemGroupedBackground))
              .swipeActions(allowsFullSwipe: false) {
                Button("删除", role: .destructive) {
                  requestSessionDeletion(item)
                }
              }
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
            .iconButtonFrame().contentShape(Rectangle())
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
              ForEach(Array(guideMessages.enumerated()), id: \.element.id) { index, message in
                if message.role == "user" {
                  messageView(message).id(message.id)
                  assistantTurn(after: index)
                    .id(message.id + ".reply")
                } else if !guideMessages.prefix(index).contains(where: { $0.role == "user" }) {
                  messageView(message).id(message.id)
                }
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

  @ViewBuilder private func messageView(_ message: ChatMessage) -> some View {
    let user = message.role == "user"
    if !user {
      AssistantReplyBubble(run: nil, hasAnswer: true) { EmptyView() } answer: { messageContent(message) }
    } else {
      HStack(alignment: .top, spacing: 0) {
        Spacer(minLength: 44)
        messageContent(message)
          .padding(16)
          .modifier(AssistantMessageSurface(user: true))
      }
      .accessibilityElement(children: .contain)
      .accessibilityIdentifier("guide-user-message")
    }
  }

  private func messageContent(_ message: ChatMessage) -> some View {
    let user = message.role == "user"
    return VStack(alignment: .leading, spacing: 10) {
        if let files = message.attachments, !files.isEmpty {
          AssistantAttachmentStrip(attachments: files)
        }
        AssistantChatText(message.text, user: user)
          .modifier(AssistantReplyFont(style: .body))
          .foregroundStyle(user ? .white : DS.text)
          .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        if !user, let label = message.sourceLabelText {
          Text(label).appFont(.caption).foregroundStyle(DS.secondary)
            .accessibilityIdentifier("assistant.sourceLabel")
        }
        if let ids = courseLinks[message.id] {
          ForEach(store.community.filter { ids.contains($0.id) }) { book in
            Button {
              close()
              store.selectedCommunityID = book.id
              store.go(.communityBook)
            } label: {
              HStack {
                VStack(alignment: .leading, spacing: 4) {
                  Text(book.catalogTitle).modifier(AssistantReplyFont(style: .supporting, weight: .semibold))
                  Text("\(book.grade) · \(book.subject) · \(book.version)")
                    .modifier(AssistantReplyFont(style: .caption)).foregroundStyle(DS.secondary)
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
              Text(citation.chapterTitle).modifier(AssistantReplyFont(style: .supporting, weight: .semibold))
              Text("查看\(citation.printedPage.map { "教材第 \($0) 页" } ?? "PDF 第 \(citation.page) 页")原文")
                .modifier(AssistantReplyFont(style: .caption))
              Text(citation.quote).modifier(AssistantReplyFont(style: .caption)).lineLimit(3)
            }.foregroundStyle(DS.primary).frame(maxWidth: .infinity, alignment: .leading)
              .padding(12).background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 14))
          }.buttonStyle(.plain)
        }
      }
  }

  @ViewBuilder private func assistantTurn(after index: Int) -> some View {
    let question = guideMessages[index]
    let replies = Array(guideMessages.dropFirst(index + 1).prefix { $0.role != "user" })
    let savedRuns = session?.runs?.filter { $0.context.messageID == question.id } ?? []
    let active = store.agent.active.flatMap { run in
      run.scope == store.noteScope && run.context.sessionID == session?.id
        && run.context.messageID == question.id ? run : nil
    }
    let runs = savedRuns.filter { $0.id != active?.id } + [active].compactMap { $0 }
    let run = runs.last
    let preparing = busy && index == guideMessages.count - 1
    if !replies.isEmpty || run != nil || preparing {
      AssistantReplyBubble(run: run, hasAnswer: !replies.isEmpty) {
        ForEach(runs) { record in agentRunDetails(record) }
      } answer: {
        ForEach(replies) { reply in messageContent(reply) }
        // The run can finish before the history store appends its answer.
        if replies.isEmpty, let text = run?.finalText, !text.isEmpty {
          AssistantChatText(text).modifier(AssistantReplyFont(style: .body)).foregroundStyle(DS.text).textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
        }
        if let run, run.status.isTerminal, run.scope == store.noteScope { AssistantResultActions(run: run) }
      }
    }
  }

  private func agentRunDetails(_ run: AgentRunRecord) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(run.context.bookTitle.isEmpty ? "本次请求" : "《\(run.context.bookTitle)》PDF 第 \(run.context.page) 页")
        .modifier(AssistantReplyFont(style: .supporting)).foregroundStyle(DS.text).lineLimit(2)
      HStack(spacing: 8) {
        Text(statusLabel(run.status)).modifier(AssistantReplyFont(style: .caption)).foregroundStyle(DS.secondary)
        if run.status == .running { ProgressView().scaleEffect(0.7).tint(DS.primary) }
        Spacer(minLength: 0)
        if run.status == .running || run.status == .waitingForUser {
          Button("取消") { if store.agent.active?.id == run.id { store.agent.cancel() } }
            .disabled(store.agent.active?.id != run.id)
            .modifier(AssistantReplyFont(style: .caption, weight: .semibold)).foregroundStyle(DS.danger)
            .accessibilityIdentifier("assistant.run.cancel")
        }
      }
      ForEach((run.events ?? []).filter { $0.toolCallID == nil || $0.attachmentPath != nil }) { event in
        VStack(alignment: .leading, spacing: 6) {
          Text(event.message).modifier(AssistantReplyFont(style: .caption)).foregroundStyle(DS.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
          if let path = event.attachmentPath, let image = UIImage(contentsOfFile: store.attachmentURL(path).path) {
            Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 260)
              .clipShape(RoundedRectangle(cornerRadius: 12))
              .accessibilityLabel("发送给模型的实际截图")
          }
        }
      }
      ForEach(run.steps, id: \.id) { step in
        ForEach(step.toolCalls, id: \.id) { call in
          Text(UnifiedAgentTools.label(call.name) + " · " + (["ok": "已完成", "error": "失败", "skipped": "已跳过"][step.outcome(call.id)?.status ?? ""] ?? "待确认"))
            .modifier(AssistantReplyFont(style: .caption)).foregroundStyle(DS.secondary)
        }
      }
      if let choice = run.pendingChoice {
        Text(choice.question).modifier(AssistantReplyFont(style: .supporting)).foregroundStyle(DS.text)
        if run.status == .waitingForUser {
          VStack(alignment: .leading, spacing: 8) {
            ForEach(choice.options, id: \.self) { option in
              Button { if store.agent.active?.id == run.id { store.agent.choose(option) } } label: {
                Text(option).modifier(AssistantReplyFont(style: .supporting)).foregroundStyle(DS.primary)
                  .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 12)
                  .frame(minHeight: 40)
                  .background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 12))
              }.buttonStyle(.plain)
                .disabled(store.agent.active?.id != run.id)
                .accessibilityIdentifier("assistant.run.option")
            }
          }
        }
      }
      if let failure = run.failure {
        Text(failure).modifier(AssistantReplyFont(style: .supporting)).foregroundStyle(DS.danger)
      }
      if let notice = store.agent.notice, !run.status.isTerminal {
        HStack(spacing: 8) {
          Text(notice).modifier(AssistantReplyFont(style: .supporting)).foregroundStyle(DS.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
          Button("继续") { if store.agent.active?.id == run.id { store.agent.resume() } }
            .disabled(store.agent.active?.id != run.id)
            .modifier(AssistantReplyFont(style: .supporting, weight: .semibold)).foregroundStyle(DS.primary)
        }
      }
      if run.status == .cancelled {
        Text("已取消，已保存的笔记仍保留").modifier(AssistantReplyFont(style: .caption)).foregroundStyle(DS.secondary)
      }
    }.frame(maxWidth: .infinity, alignment: .leading)
      .accessibilityElement(children: .contain)
      .accessibilityIdentifier("assistant.run.card")
  }

  private func statusLabel(_ status: AgentRunStatus) -> String {
    switch status {
    case .running: "正在整理…"
    case .waitingForUser: "等待你的选择"
    case .completed: "已完成"
    case .cancelled: "已取消"
    case .failed: "未能完成"
    case .uncertain: "结果待确认"
    }
  }
  private func dismissComposerKeyboard() {
    keyboardDismissalID += 1
    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
  }

  private func close() {
    cancelAttachmentImport()
    saveDraft()
    voice.returnToText()
    store.globalAssistantOpen = false
  }

  private func loadHistory() async {
    pendingSessionDeletion = nil
    let scope = store.assistantHistoryScope
    #if DEBUG
    if let scope, ContentBundle.fixtureMode,
      ProcessInfo.processInfo.arguments.contains("--fixture-history-controls") {
      let existing = try? await store.repository.loadAssistantHistory(scope: scope)
      if existing?.sessions.isEmpty != false, existing?.removedIDs.isEmpty != false {
        history.reset()
        await history.flush()
        // Exercise history controls without depending on a live model response.
        let samples = ["First conversation", "Second conversation"].enumerated().map { index, title in
          var sample = AssistantSession(id: index == 0 ? "fixture-first" : "fixture-second",
            title: title, context: store.assistantSessionContext)
          sample.messages = [ChatMessage(role: "user", text: title)]
          return sample
        }
        try? await store.repository.saveAssistantHistory(
          AssistantHistoryArchive(sessions: samples, selectedID: samples.first?.id), scope: scope)
      }
    }
    if let scope, ContentBundle.fixtureMode,
      ProcessInfo.processInfo.arguments.contains("--fixture-assistant-long-reply"),
      !history.sessions.contains(where: { $0.title == "长回答展示测试" }) {
      history.reset()
      await history.flush()
      var sample = AssistantSession(title: "长回答展示测试", context: store.assistantSessionContext)
      let question = ChatMessage(role: "user", text: "请详细说明学习方法。")
      let text = (1...18).map { index in
        "第 \(index) 部分：先理解核心概念，再结合例子练习。记录自己的问题，安排复习，并检查是否能够独立解释知识点。"
      }.joined(separator: "\n\n") + "\n\n完整回答结束：FULLSCREEN_END"
      sample.messages = [question, ChatMessage(role: "assistant", text: text)]
      var context = store.agent.frozenContext(session: sample.context)
      context.sessionID = sample.id
      context.messageID = question.id
      let now = Date()
      var run = AgentRunRecord(id: "preview-fixture", scope: context.scope, status: .completed,
        request: question.text, context: context, evidence: [], steps: [], finalText: text,
        createdAt: now.addingTimeInterval(-3), updatedAt: now)
      run.events = (1...20).map { AgentRunEvent(kind: "progress", message: "处理记录 \($0)：已检查课程资料和学习进度。") }
      sample.runs = [run]
      try? await store.repository.saveAssistantHistory(
        AssistantHistoryArchive(sessions: [sample], selectedID: sample.id), scope: scope)
    }
    #endif
    await history.load(scope: scope)
    guard !Task.isCancelled, scope == store.assistantHistoryScope, history.isReady(for: scope) else { return }
    do { try await store.syncAssistantHistory() }
    catch { /* Local history stays accessible; the next send retries server reconciliation. */ }
    guard scope == store.assistantHistoryScope else { return }
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
    guard ready, !preparingSubmission else { return }
    cancelAttachmentImport()
    saveDraft()
    voice.returnToText()
    guard history.startNew(context: store.assistantSessionContext) else { return }
    voice.draft = history.draft
    dismissHistory()
  }

  private func requestSessionDeletion(_ item: AssistantSession) {
    guard ready, let scope = loadedScope else { return }
    saveDraft()
    voice.returnToText()
    historySearchFocused = false
    dismissComposerKeyboard()
    pendingSessionDeletion = AssistantSessionDeletion(id: item.id, title: item.title, scope: scope,
      epoch: store.cloudState.epoch, isRunning: history.runningIDs.contains(item.id))
  }

  private func confirmSessionDeletion(_ item: AssistantSessionDeletion) {
    pendingSessionDeletion = nil
    guard ready, item.scope == store.assistantHistoryScope, item.epoch == store.cloudState.epoch else { return }
    let selected = history.archive.selectedID == item.id
    if selected { cancelAttachmentImport(); voice.returnToText() }
    Task { @MainActor in
      _ = await store.deleteAssistantSession(item.id, expectedScope: item.scope)
    }
  }

  private func send() {
    var question = voice.draft.trimmingCharacters(in: .whitespacesAndNewlines)
    guard ready, !busy, !readOnly, !addingAttachments, voice.phase == .text else { return }
    if question.isEmpty, let first = attachments.first {
      question = "请帮我分析附件“\(String(first.filename.prefix(120)))”中的内容。"
    }
    guard !question.isEmpty else { return }
    guard store.cloud != nil, store.processingPolicy.path.kind != .offline else {
      attachmentError = "AI 助手需要联网"; return
    }
    guard question.count <= 2000 else { attachmentError = "问题最多 2,000 字，请精简后发送。"; return }
    let owner = store.assistantHistoryScope
    let epoch = store.cloudState.epoch
    let selectedID = history.archive.selectedID
    let snapshot = store.assistantSessionContext
    guard !preparingSubmission else { return }
    preparingSubmission = true
    Task { @MainActor in
      defer { preparingSubmission = false }
      do {
        try await store.syncAssistantHistory()
        guard owner == store.assistantHistoryScope, epoch == store.cloudState.epoch,
          selectedID == history.archive.selectedID else { return }
        history.submit(question, context: snapshot) { [store] context, messages, question in
          guard owner == store.assistantHistoryScope, epoch == store.cloudState.epoch else { throw CancellationError() }
          return try await store.answerHistorySession(context: context, history: messages, question: question)
        }
        voice.draft = history.draft
      } catch { attachmentError = error.localizedDescription }
    }
  }

  private func beginAttachmentImport(count: Int,
    load: @escaping @MainActor (Int, @escaping AssistantAttachmentImporter.Progress) async throws -> AssistantAttachment) {
    guard ready, !readOnly, !addingAttachments, count > 0 else { return }
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
    guard !preparingSubmission, !readOnly else { return }
    let owner = store.assistantHistoryScope
    let epoch = store.cloudState.epoch
    let selectedID = history.archive.selectedID
    preparingSubmission = true
    Task { @MainActor in
      defer { preparingSubmission = false }
      do {
        try await store.syncAssistantHistory()
        guard owner == store.assistantHistoryScope, epoch == store.cloudState.epoch,
          selectedID == history.archive.selectedID else { return }
        history.retry { [store] context, messages, question in
          guard owner == store.assistantHistoryScope, epoch == store.cloudState.epoch else { throw CancellationError() }
          return try await store.answerHistorySession(context: context, history: messages, question: question)
        }
      } catch { attachmentError = error.localizedDescription }
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
struct AssistantAdaptiveTitle: View {
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

struct AssistantMessageSurface: ViewModifier {
  let user: Bool
  func body(content: Content) -> some View {
    content.modifier(AssistantGlassSurface(radius: 24, clear: false,
      tint: user ? DS.primary : nil))
  }
}

private struct AssistantReplyDetailKey: EnvironmentKey {
  static let defaultValue = false
}
private extension EnvironmentValues {
  var assistantReplyDetail: Bool {
    get { self[AssistantReplyDetailKey.self] }
    set { self[AssistantReplyDetailKey.self] = newValue }
  }
}

/// Full-screen reading has a larger type scale without changing conversation text.
private struct AssistantReplyFont: ViewModifier {
  var style: DS.Typography = .body
  var weight: Font.Weight? = nil
  @Environment(\.assistantReplyDetail) private var detail
  @ScaledMetric(relativeTo: .body) private var detailBodySize: CGFloat = 20
  @ViewBuilder func body(content: Content) -> some View {
    if detail {
      content.font(Font(DS.uiFont(style, weight: weight)
        .withSize(detailBodySize * style.size / DS.Typography.body.size)))
        .lineSpacing(style == .body ? 5 : 3)
    } else {
      content.appFont(style, weight: weight)
    }
  }
}

/// The container keeps the same identity from preparation through the saved reply.
struct AssistantReplyBubble<Operations: View, Answer: View>: View {
  let run: AgentRunRecord?
  let hasAnswer: Bool
  @ViewBuilder let operations: () -> Operations
  @ViewBuilder let answer: () -> Answer
  @State private var expanded = false
  @State private var showingFullScreen = false
  @State private var contentHeight: CGFloat = 0
  private let previewLimit: CGFloat = 240
  private var truncated: Bool { contentHeight > previewLimit + 0.5 }
  @Environment(\.bookCourseReduceMotion) private var reduceMotion

  private var finished: Bool { run?.status.isTerminal ?? hasAnswer }
  private var status: String {
    switch run?.status {
    case .completed: "已完成"
    case .cancelled: "已取消"
    case .failed: "未能完成"
    case .uncertain: "结果待确认"
    case .waitingForUser: "等待你的选择"
    case .running: "正在处理…"
    case nil: hasAnswer ? "已完成" : "正在准备回答…"
    }
  }
  private var duration: String? {
    guard let run, finished else { return nil }
    let seconds = max(0, Int(run.updatedAt.timeIntervalSince(run.createdAt)))
    return seconds >= 60 ? "\(seconds / 60)m\(seconds % 60)s" : "\(seconds)s"
  }

  var body: some View {
    HStack(alignment: .top, spacing: 0) {
      VStack(alignment: .leading, spacing: 12) {
        replyHeader(fullScreen: false)
        VStack(alignment: .leading, spacing: 12) { replyContent }
          .fixedSize(horizontal: false, vertical: true)
          .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { contentHeight = $0 }
          .frame(maxHeight: previewLimit, alignment: .top)
          .clipped()
          .contentShape(Rectangle())
          .mask(alignment: .bottom) {
            VStack(spacing: 0) {
              Color.black
              if truncated {
                LinearGradient(colors: [.black, .clear], startPoint: .top, endPoint: .bottom)
                  .frame(height: 24)
              }
            }
          }
          .accessibilityElement(children: .contain)
          .accessibilityIdentifier("assistant.reply.preview")
        if truncated {
          Text("… 内容已省略，请点右上角全屏查看")
            .appFont(.caption).foregroundStyle(DS.secondary)
            .accessibilityIdentifier("assistant.reply.truncated")
        }
      }
      .padding(16).frame(maxWidth: .infinity, alignment: .leading)
      .modifier(AssistantMessageSurface(user: false))
      Spacer(minLength: 20)
    }
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier("guide-ai-message")
    .onChange(of: finished) { _, _ in expanded = false }
    .fullScreenCover(isPresented: $showingFullScreen) {
      VStack(spacing: 0) {
        replyHeader(fullScreen: true)
          .padding(.horizontal, 20).padding(.vertical, 8)
        ScrollView {
          VStack(alignment: .leading, spacing: 12) {
            replyContent
          }
          .frame(maxWidth: .infinity, alignment: .leading)
          .padding(20)
          .environment(\.assistantReplyDetail, true)
        }
        .scrollBounceBehavior(.basedOnSize)
      }
      .modifier(AssistantGlassSurface(radius: 0))
      .background(Color(.systemBackground).ignoresSafeArea())
      .accessibilityElement(children: .contain)
      .accessibilityAddTraits(.isModal)
      .accessibilityIdentifier("assistant.reply.fullscreen")
      .accessibilityAction(.escape) { showingFullScreen = false }
    }
  }

  @ViewBuilder private var replyContent: some View {
    if !finished || expanded { operations() }
    answer()
  }

  private func replyHeader(fullScreen: Bool) -> some View {
    HStack(spacing: 12) {
      if finished {
        Button {
          withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { expanded.toggle() }
        } label: {
          HStack(spacing: 5) {
            if run?.status == .completed || run == nil { Image(systemName: "checkmark") }
            Text(status)
            if let duration { Text(duration).monospacedDigit() }
            if run != nil {
              Image(systemName: "chevron.right").font(.system(size: 10, weight: .medium))
                .rotationEffect(.degrees(expanded ? 90 : 0))
            }
          }
          .appFont(.caption).foregroundStyle(DS.secondary)
          .frame(minHeight: 44, alignment: .leading).contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(run == nil)
        .accessibilityIdentifier("assistant.run.disclosure")
        .accessibilityValue(expanded ? "已展开" : "已折叠")
        .accessibilityHint("展开或收起操作记录")
      } else {
        HStack(spacing: 8) {
          ProgressView().tint(DS.primary)
          Text(status).appFont(.caption).foregroundStyle(DS.secondary)
        }
      }
      Spacer(minLength: 0)
      Button {
        showingFullScreen = !fullScreen
      } label: {
        Image(systemName: fullScreen
          ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right")
          .font(.system(size: 15, weight: .medium))
          .foregroundStyle(DS.secondary)
          .iconButtonFrame().contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .accessibilityLabel(fullScreen ? "收起全屏回答" : "全屏查看回答")
      .accessibilityIdentifier(fullScreen ? "assistant.reply.collapse" : "assistant.reply.expand")
    }
  }
}

/// Preserve user input literally. Assistant prose uses inline Markdown without
/// collapsing paragraphs, numbered lists or the line breaks in equations.
struct AssistantChatText: View {
  let value: String
  let user: Bool
  init(_ value: String, user: Bool = false) { self.value = value; self.user = user }
  var body: some View {
    if user { Text(verbatim: value) }
    else {
      Text((try? AttributedString(markdown: value, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
        ?? AttributedString(value))
    }
  }
}

struct AssistantResultActions: View {
  @Environment(AppStore.self) private var store
  let run: AgentRunRecord
  var onNavigate: (() -> Void)? = nil
  private struct Result: Decodable {
    struct Resource: Decodable {
      var book_id: String
      var title: String?
      var chapters: [Chapter]?
    }
    struct TaskStatus: Decodable { var status: String?; var source: String }
    var course_id: String
    var show_outline: Bool?
    var resources: [Resource]?
    var tasks: [TaskStatus]?
  }
  private var results: [Result] {
    run.steps.flatMap { step in
      step.toolCalls.compactMap { call -> Result? in
        guard ["read_course_catalog", "read_learning_data"].contains(call.name),
          let outcome = step.outcome(call.id), outcome.status == "ok" else { return nil }
        return try? JSONDecoder().decode(Result.self, from: Data(outcome.resultJSON.utf8))
      }
    }
  }
  var body: some View {
    let receipts = results
    if let result = receipts.last(where: { $0.show_outline == true }) {
      ForEach(result.resources ?? [], id: \.book_id) { resource in
        if let chapters = resource.chapters, chapters.contains(where: { $0.parent_id != nil }) {
          DisclosureGroup((result.resources?.count ?? 0) > 1 ? "展开《" + (resource.title ?? "教材") + "》全部小节" : "展开全部小节") {
            ForEach(chapters.filter { $0.id != "frontmatter" }) { chapter in
              Text(chapter.title).frame(maxWidth: .infinity, alignment: .leading)
                .padding(.leading, CGFloat(min(4, max(0, chapter.level - 1))) * 10)
            }
          }.appFont(.supporting)
        }
      }
    }
    ForEach(run.committedNoteIDs, id: \.self) { id in
      if let note = store.notes.first(where: { $0.id == id }) {
        Button(run.committedNoteIDs.count > 1 ? "打开《" + note.title + "》" : "打开笔记") {
          onNavigate?()
          store.globalAssistantOpen = false
          store.openAgentNote(id)
        }.accessibilityIdentifier("assistant.run.openNote")
      }
    }
    if let progress = receipts.last(where: { $0.tasks != nil }),
      progress.tasks?.contains(where: { $0.status == "failed" && $0.source == "live" }) == true,
      let record = store.courseImport(for: progress.course_id), record.job.status == .failed {
      Button("继续整理") {
        onNavigate?()
        store.globalAssistantOpen = false
        store.continueCourseImport(record.id)
        Task { await store.startCourseImport(record.id, retryPreparation: true) }
      }
    }
  }
}

/// Shared geometry for every AI conversation, including original-page selections.
struct AssistantConversationPanel<Header: View, Content: View, Composer: View>: View {
  var isPad: Bool
  var voiceMode: Bool
  var keyboardTop: CGFloat
  var dismissKeyboard: () -> Void
  @ViewBuilder var header: () -> Header
  @ViewBuilder var content: (CGFloat) -> Content
  @ViewBuilder var composer: (CGFloat) -> Composer
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var revealed = false
  @State private var composerHeight: CGFloat = 58

  var body: some View {
    GeometryReader { geometry in
      let visibleHeight = max(180, voiceMode ? geometry.size.height :
        min(geometry.size.height, keyboardTop - geometry.frame(in: .global).minY))
      let keyboardVisible = !voiceMode && keyboardTop < geometry.frame(in: .global).maxY
      let windowInset = UIApplication.shared.connectedScenes.compactMap {
        ($0 as? UIWindowScene)?.keyWindow?.safeAreaInsets.bottom
      }.first ?? 0
      let bottomPadding = keyboardVisible ? 12 : max(geometry.safeAreaInsets.bottom, windowInset) + 12
      let panelWidth = isPad ? min(460, geometry.size.width - 48) : geometry.size.width
      let textInset = max(0, panelWidth * 0.08 - 16)
      VStack(spacing: 16) {
        header()
          .opacity(reduceMotion || revealed ? 1 : 0)
          .animation(reduceMotion ? nil : .easeOut(duration: 0.18).delay(0.04), value: revealed)
        content(composerHeight + bottomPadding + 16)
      }
      .padding(.horizontal, 16).padding(.top, 12)
      .frame(width: panelWidth, height: visibleHeight)
      .contentShape(Rectangle())
      .simultaneousGesture(TapGesture().onEnded { dismissKeyboard() })
      .overlay(alignment: .bottom) {
        composer(textInset)
          .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { composerHeight = $0 }
          .background(alignment: .bottom) {
            ProgressiveMaterial()
              .padding(.horizontal, -16).padding(.top, -36).padding(.bottom, -bottomPadding)
              .contentShape(Rectangle()).onTapGesture { dismissKeyboard() }
          }
          .opacity(reduceMotion || revealed ? 1 : 0)
          .scaleEffect(reduceMotion || revealed ? 1 : 0.97)
          .animation(reduceMotion ? nil : .easeOut(duration: 0.26), value: revealed)
          .padding(.horizontal, 16).padding(.bottom, bottomPadding)
      }
      .frame(maxWidth: .infinity, alignment: isPad ? .trailing : .center)
      .padding(.trailing, isPad ? 24 : 0)
      .accessibilityElement(children: .contain).accessibilityAddTraits(.isModal)
    }.onAppear { revealed = true }
  }
}

struct AssistantConversationHeader<Controls: View>: View {
  var title: String
  @ViewBuilder var controls: () -> Controls
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  var body: some View {
    ZStack {
      AssistantAdaptiveTitle(title: title).foregroundStyle(DS.text)
        .padding(.horizontal, 100).buttonFrame(fillsWidth: true)
        .contentTransition(.opacity)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: title)
        .accessibilityIdentifier("assistant-session-title")
      controls()
    }.frame(minHeight: 44)
  }
}
