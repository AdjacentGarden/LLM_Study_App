import SwiftUI

struct CloudAccountView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var email = ""
  @State private var code = ""
  @State private var registering = false
  @State private var challenge: CloudChallenge?
  @State private var challengeEmail = ""
  @State private var challengePurpose = ""
  @State private var resendDate = Date.distantPast
  @State private var busy = false
  @State private var error: String?
  @State private var server = ""
  @State private var showingSettings = false
  @State private var panelPresented = false
  @State private var settingsError: String?
  @FocusState private var focused: Bool
  @GestureState(resetTransaction: Transaction(animation: .spring(response: 0.38, dampingFraction: 0.82)))
  private var dragOffset: CGFloat = 0

  private var hasCurrentChallenge: Bool {
    challenge != nil && email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == challengeEmail
      && challengePurpose == (registering ? "register" : "login")
  }

  var body: some View {
    GeometryReader { geometry in
      let width = min(geometry.size.width, 480)
      let scale = width / 402
      let heroHeight = max(190, 332 * scale - geometry.safeAreaInsets.top)
      ZStack(alignment: .top) {
        ScrollViewReader { proxy in
          ScrollView {
            VStack(spacing: 0) {
              illustration(width: width, scale: scale, topInset: geometry.safeAreaInsets.top)
                .frame(height: heroHeight + 58 * scale, alignment: .top)
                .clipped()
                .padding(.bottom, -58 * scale)
              accountForm(scale: scale)
                .frame(minHeight: max(542 * scale,
                  geometry.size.height - heroHeight + geometry.safeAreaInsets.bottom))
                .background {
                  // A continuous white surface has no bottom-edge shadow at the safe area.
                  UnevenRoundedRectangle(topLeadingRadius: 58 * scale, topTrailingRadius: 58 * scale)
                    .fill(DS.surface)
                }
                .offset(y: panelPresented ? 0 : geometry.size.height + geometry.safeAreaInsets.bottom)
                .allowsHitTesting(panelPresented)
                .accessibilityHidden(!panelPresented)
            }
            .frame(width: width)
            .frame(maxWidth: .infinity)
            .id("cloud.formTop")
            .offset(y: reduceMotion ? 0 : dragOffset)
          }
          .accessibilityIdentifier("cloud.scroll")
          .scrollIndicators(.hidden)
          .scrollDismissesKeyboard(.interactively)
          // Normal dragging only stretches the presentation; it cannot leave a scroll offset.
          // Text input may still reveal fields above the keyboard.
          .scrollDisabled(!focused)
          .simultaneousGesture(
            DragGesture(minimumDistance: 12).updating($dragOffset) { value, offset, transaction in
              guard !focused, panelPresented else { return }
              transaction.animation = nil
              let distance = value.translation.height
              offset = distance * 0.28 / (1 + abs(distance) / 360)
            })
          .onChange(of: focused) { _, isFocused in
            if !isFocused { proxy.scrollTo("cloud.formTop", anchor: .top) }
          }
        }

        accountHeader(scale: scale, topInset: geometry.safeAreaInsets.top)
          .frame(width: width)
      }
      .background(alignment: .top) {
        accountBackground(width: width, scale: scale).ignoresSafeArea()
      }
    }
    .background(DS.surface.ignoresSafeArea())
    .onAppear { server = store.cloud?.baseURL.absoluteString ?? CloudAPI.defaultServer }
    .task {
      if reduceMotion {
        panelPresented = true
      } else {
        // Reveal the illustration first, then spring the entire white panel upward.
        do { try await Task.sleep(for: .milliseconds(120)) } catch { return }
        withAnimation(.spring(response: 0.62, dampingFraction: 0.86)) {
          panelPresented = true
        }
      }
    }
    .sheet(isPresented: $showingSettings) { serviceSettings }
    .toolbar {
      ToolbarItemGroup(placement: .keyboard) {
        Spacer()
        Button("完成") { focused = false }.accessibilityIdentifier("cloud.keyboardDone")
      }
    }
  }

  private func accountBackground(width: CGFloat, scale: CGFloat) -> some View {
    ZStack(alignment: .top) {
      DS.surface
      LinearGradient(
        stops: [.init(color: Color(hex: 0xdccafa), location: 0),
          .init(color: Color(hex: 0xfcfaff), location: 0.5),
          .init(color: DS.surface, location: 1)],
        startPoint: .top, endPoint: .bottom
      ).frame(height: 478 * scale)
      AssetImage("/assets/brand/login/hero-decoration.png")
        .frame(width: width, height: 478 * scale)
        .mask {
          LinearGradient(stops: [.init(color: .white, location: 0),
            .init(color: .white, location: 0.75), .init(color: .clear, location: 1)],
            startPoint: .top, endPoint: .bottom)
        }
    }
    .allowsHitTesting(false)
    .accessibilityHidden(true)
  }

  private func illustration(width: CGFloat, scale: CGFloat, topInset: CGFloat) -> some View {
    AssetImage("/assets/brand/login/cloud-hero.png")
      .frame(width: width, height: 276 * scale)
      .opacity(0.8)
      .offset(x: -scale, y: 93 * scale - topInset)
  }

  private func accountHeader(scale: CGFloat, topInset: CGFloat) -> some View {
    HStack {
      Spacer().frame(width: 44)
      Text(registering ? "注册" : "登录")
        .font(DS.font(24, weight: .bold)).foregroundStyle(DS.primary)
        .accessibilityAddTraits(.isHeader)
        .accessibilityIdentifier("cloud.title")
        .frame(maxWidth: .infinity)
      Button {
        focused = false
        server = store.cloud?.baseURL.absoluteString ?? CloudAPI.defaultServer
        settingsError = nil
        showingSettings = true
      } label: {
        BCGlyph("Settings", color: DS.primary)
          .frame(width: 16, height: 16)
          .frame(width: 31, height: 31)
          .background(Color(hex: 0xf7f3ff), in: RoundedRectangle(cornerRadius: 8))
          .frame(width: 44, height: 44)
          .contentShape(Rectangle())
      }
      .buttonStyle(BCPressStyle())
      .accessibilityLabel("设置")
      .accessibilityIdentifier("cloud.settings")
      .disabled(busy)
    }
    .padding(.horizontal, 12 * scale)
    .padding(.top, max(16, 63 * scale - topInset))
  }

  private func accountForm(scale: CGFloat) -> some View {
    VStack(spacing: 0) {
      VStack(alignment: .leading, spacing: 17) {
        if store.cloudState.localLogin {
          Text("正在使用本地默认账号，无需邮箱验证。")
            .font(DS.font(16)).foregroundStyle(DS.secondary)
          BCButton("连接本地服务并进入 App") {
            store.isLoading = true
            Task { await store.bootCloud() }
          }.accessibilityIdentifier("cloud.localRetry")
        } else {
          TextField("邮箱账号", text: $email)
            .textInputAutocapitalization(.never).autocorrectionDisabled()
            .keyboardType(.emailAddress).textContentType(.emailAddress)
            .focused($focused)
            .accessibilityLabel("邮箱地址").accessibilityIdentifier("cloud.email")
            .padding(.horizontal, 21).frame(minHeight: 47)
            .background(Color(.systemGray6), in: Capsule())
          HStack(spacing: 4) {
            TextField("邮箱验证码", text: $code)
              .keyboardType(.numberPad).textContentType(.oneTimeCode)
              .focused($focused)
              .accessibilityLabel("六位验证码").accessibilityIdentifier("cloud.code")
              .onChange(of: code) { _, value in
                code = String(value.filter { $0.isASCII && $0.isNumber }.prefix(6))
              }
            TimelineView(.periodic(from: .now, by: 1)) { context in
              let remaining = max(0, Int(ceil(resendDate.timeIntervalSince(context.date))))
              Button {
                Task { await sendCode() }
              } label: {
                Text(remaining > 0 ? "\(remaining) 秒" : busy ? "发送中…" : "获取验证码")
                  .font(DS.font(12, weight: .semibold)).foregroundStyle(DS.primary)
                  .fixedSize().frame(minHeight: 44)
              }
              .buttonStyle(BCPressStyle())
              .disabled(busy || email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || remaining > 0)
              .opacity(busy || remaining > 0 ? 0.55 : 1)
              .accessibilityIdentifier("cloud.sendCode")
            }
          }
          .padding(.horizontal, 21).frame(minHeight: 47)
          .background(Color(.systemGray6), in: Capsule())
          if let challenge, hasCurrentChallenge {
            Text(challenge.message).font(DS.font(13)).foregroundStyle(DS.secondary)
            if let demo = challenge.demo_code {
              Text("测试账号验证码：\(demo)")
                .font(DS.font(14, weight: .bold)).foregroundStyle(DS.primary)
                .accessibilityIdentifier("cloud.demoCode")
            }
          }
        }
        if let message = error ?? store.cloudState.error {
          Text(message).font(DS.font(14)).foregroundStyle(DS.danger)
            .accessibilityIdentifier("cloud.error")
        }
      }
      .font(DS.font(20)).tint(DS.primary)
      .frame(maxWidth: .infinity, minHeight: registering ? 186 * scale : 256 * scale, alignment: .topLeading)
      .padding(.horizontal, 50 * scale)
      if !store.cloudState.localLogin {
        HStack(spacing: 27 * scale) {
          Button {
            Task { await verifyCode() }
          } label: {
            Text(busy ? "请稍候…" : registering ? "注册" : "登录")
              .font(DS.font(24)).foregroundStyle(DS.primary)
              .frame(maxWidth: .infinity, minHeight: 47)
              .background(DS.primarySoft, in: Capsule())
          }
          .buttonStyle(BCPressStyle()).disabled(busy)
          .accessibilityIdentifier("cloud.verify")
          if !registering {
            Button { setRegistering(true) } label: {
              Text("注册")
                .font(DS.font(24)).foregroundStyle(DS.secondary)
                .frame(maxWidth: .infinity, minHeight: 47)
                .background(Color(hex: 0xeef1f5), in: Capsule())
            }
            .buttonStyle(BCPressStyle()).disabled(busy)
            .accessibilityLabel("切换到注册")
            .accessibilityIdentifier("cloud.registerMode")
          }
        }.padding(.horizontal, 33 * scale)
      }
      Spacer(minLength: 80)
    }
    .padding(.top, registering ? 90 * scale : 100 * scale)
    .overlay(alignment: .topLeading) {
      if registering {
        Button { setRegistering(false) } label: {
          Label("返回登录", systemImage: "chevron.left")
            .font(DS.font(16, weight: .semibold)).foregroundStyle(DS.primary)
            .frame(minHeight: 44).contentShape(Rectangle())
        }
        .buttonStyle(BCPressStyle()).disabled(busy)
        .accessibilityIdentifier("cloud.backToLogin")
        .padding(.leading, 33 * scale).padding(.top, 18 * scale)
      }
    }
  }

  private func setRegistering(_ value: Bool) {
    focused = false
    registering = value
    challenge = nil
    code = ""
    error = nil
    store.cloudState.error = nil
  }

  private var serviceSettings: some View {
    NavigationStack {
      Form {
        Section {
          TextField(CloudAPI.defaultServer, text: $server)
            .textInputAutocapitalization(.never).autocorrectionDisabled()
            .keyboardType(.URL).textContentType(.URL)
            .accessibilityLabel("学习服务地址").accessibilityIdentifier("cloud.server")
          if let settingsError {
            Text(settingsError).foregroundStyle(DS.danger)
              .accessibilityIdentifier("cloud.serverError")
          }
          Button("连接此服务", action: connectService)
            .foregroundStyle(DS.primary)
            .accessibilityIdentifier("cloud.connectServer")
        } header: {
          Text("学习服务地址")
        } footer: {
          Text("同一局域网使用 Windows 学习服务地址；通过 VPN 可填写 http://100.86.143.88:8100。")
        }
      }
      .navigationTitle("设置").navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .principal) {
          Text("设置").font(DS.font(18, weight: .bold)).accessibilityAddTraits(.isHeader)
        }
        ToolbarItem(placement: .confirmationAction) {
          Button("完成") { showingSettings = false }
            .accessibilityIdentifier("cloud.settingsDone")
        }
      }
    }
    .tint(DS.primary)
  }

  private func connectService() {
    guard let url = CloudAPI.validatedURL(server) else {
      settingsError = "请输入有效的 HTTP 或 HTTPS 服务地址"
      return
    }
    UserDefaults.standard.set(url.absoluteString, forKey: "cloudpath.server")
    store.cloudState.courseTask?.cancel()
    store.cloudState.courseTask = nil
    store.cloudState.courseJob = nil
    store.cloud = CloudAPI(baseURL: url)
    store.cloudState.epoch = UUID()
    store.cloudState.account = nil
    challenge = nil
    code = ""
    error = nil
    settingsError = nil
    resendDate = .distantPast
    showingSettings = false
    store.isLoading = true
    Task { await store.boot() }
  }
  private func sendCode() async {
    focused = false
    guard !busy else { return }
    guard let cloud = store.cloud else {
      error = "请先在设置中连接学习服务"
      return
    }
    busy = true
    error = nil
    store.cloudState.error = nil
    defer { busy = false }
    do {
      let normalized = email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
      let purpose = registering ? "register" : "login"
      challenge = try await cloud.request(
        "/api/auth/code", method: "POST",
        body: ["email": .string(normalized), "purpose": .string(purpose)])
      code = ""
      email = normalized
      challengeEmail = normalized
      challengePurpose = purpose
      resendDate = Date().addingTimeInterval(Double(challenge?.retry_after ?? 60))
    } catch { self.error = error.localizedDescription }
  }
  private func verifyCode() async {
    focused = false
    guard !busy else { return }
    guard let cloud = store.cloud else {
      error = "请先在设置中连接学习服务"
      return
    }
    guard let challenge, hasCurrentChallenge else {
      error = "请先为当前邮箱获取验证码"
      return
    }
    guard code.count == 6 else {
      error = "请输入六位邮箱验证码"
      return
    }
    busy = true
    error = nil
    store.cloudState.error = nil
    defer { busy = false }
    do {
      var body: [String: JSONValue] = [
        "challenge_id": .string(challenge.challenge_id), "code": .string(code),
      ]
      if registering {
        body["registration"] = .object([
          // Keep compatibility with existing services; answers are collected after authentication.
          "nickname": .string("新同学"), "stage": .string("other"),
          "interests": .array([]), "goal": .string("interest"),
        ])
      }
      let account: CloudAccountState = try await cloud.request(
        "/api/auth/verify", method: "POST", body: body)
      try await store.prepareCloudLogin(account)
      store.isLoading = true
      await store.boot()
    } catch { self.error = error.localizedDescription }
  }
}

struct CloudLearningSetupView: View {
  @Environment(AppStore.self) private var store
  @State private var selected: [String] = []
  @State private var confidence = 0.6
  @State private var started = Date()
  private var turn: CloudTurn? { store.cloudState.interview?.turn }
  private var options: [CloudTurn.Option] {
    if let item = turn?.item {
      return item.options.enumerated().map { .init(id: String($0.offset), label: $0.element) }
    }
    return turn?.options ?? []
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 22) {
        HStack {
          Text(store.isCloudCoursePreview ? "准备预览课程" : "准备专属课程")
            .font(DS.font(24, weight: .bold))
          Spacer()
          Button("稍后继续") { store.pauseCloudCourseGeneration() }
        }
        if store.cloudState.choosingLearningMode {
          VStack(alignment: .leading, spacing: 16) {
            Text("选择学习方式").font(DS.font(21, weight: .bold))
            Text("先预览本章讲解、闪卡和练习，或先做诊断再生成适合自己的课程。")
              .font(DS.font(16)).foregroundStyle(DS.secondary)
            BCButton("直接预览课程") {
              Task { await store.beginCloudLearning(
                chapterID: store.cloudState.requestedChapterID, preview: true) }
            }.accessibilityIdentifier("cloud.learning.preview")
            BCButton("先做学习诊断", style: .secondary) {
              Task { await store.beginCloudLearning(chapterID: store.cloudState.requestedChapterID) }
            }.accessibilityIdentifier("cloud.learning.diagnosis")
          }
        } else if let job = store.cloudState.courseJob {
          VStack(alignment: .leading, spacing: 16) {
            if job.isPending { ProgressView() }
            Text(job.message).font(DS.font(18, weight: .bold)).lineSpacing(5)
            Text("退出此页不会停止服务器任务；再次打开本章可继续查询原任务。")
              .font(DS.font(14)).foregroundStyle(DS.secondary)
            if let error = store.cloudState.error {
              Text(error).font(DS.font(14)).foregroundStyle(DS.danger)
            }
            if !store.cloudState.isWorking {
              BCButton("查询原任务状态", style: .secondary) {
                Task { await store.beginCloudLearning(chapterID: store.cloudState.requestedChapterID) }
              }
              if job.status == .failed, job.retryable {
                BCButton("重新生成课程") { Task { await store.restartCloudCourseGeneration() } }
              }
            }
          }.accessibilityIdentifier("cloud.courseJob")
        } else if let turn {
          if store.cloudState.interview?.isPreview != true { BCProgress(value: turn.progress) }
          Text(turn.message).font(DS.font(17)).lineSpacing(5)
          if let question = turn.item?.prompt ?? turn.question {
            Text(question).font(DS.font(21, weight: .bold)).lineSpacing(4)
          }
          ForEach(options) { option in
            Button {
              if turn.response_type == "multiple_choice" {
                if selected.contains(option.id) {
                  selected.removeAll { $0 == option.id }
                } else {
                  selected.append(option.id)
                }
              } else {
                selected = [option.id]
              }
            } label: {
              HStack(spacing: 12) {
                Image(systemName: selected.contains(option.id) ? "checkmark.circle.fill" : "circle")
                Text(option.label).multilineTextAlignment(.leading).frame(
                  maxWidth: .infinity, alignment: .leading)
              }.font(DS.font(17)).padding(18).foregroundStyle(
                selected.contains(option.id) ? DS.primary : DS.text
              )
              .background(
                selected.contains(option.id) ? DS.primarySoft : DS.surface,
                in: RoundedRectangle(cornerRadius: 16)
              )
              .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border))
            }.buttonStyle(.plain).disabled(store.cloudState.isWorking).accessibilityIdentifier(
              "cloud.diagnosis.option.\(option.id)")
          }
          if turn.item != nil {
            VStack(alignment: .leading) {
              Text("对这次回答的信心：\(Int(confidence * 100))%")
              Slider(value: $confidence, in: 0...1)
            }.font(DS.font(14)).foregroundStyle(DS.secondary)
          }
          if let why = turn.why_asked { Text(why).font(DS.font(14)).foregroundStyle(DS.secondary) }
          if let error = store.cloudState.error {
            Text(error).font(DS.font(14)).foregroundStyle(DS.danger)
          }
          BCButton(
            store.cloudState.isWorking
              ? "正在准备…"
              : turn.phase == "profile_confirmation"
                ? "确认并生成课程"
                : store.cloudState.interview?.canGenerateCourse == true ? "生成或继续本章课程" : "继续"
          ) {
            Task {
              await store.advanceCloudInterview(
                selected: selected, confidence: confidence,
                seconds: Date().timeIntervalSince(started))
            }
          }.disabled(store.cloudState.isWorking || (!options.isEmpty && selected.isEmpty))
            .accessibilityIdentifier("cloud.diagnosis.next")
        } else {
          if let error = store.cloudState.error {
            Text("诊断准备尚未完成").font(DS.font(20, weight: .bold))
            Text(error).font(DS.font(14)).foregroundStyle(DS.danger)
            if !store.cloudState.isWorking {
              BCButton("继续查询诊断", style: .secondary) {
                Task { await store.restoreCloudDiagnosis() }
              }
              if store.canRetryCloudDiagnosis {
                Text("重试会重新准备未完成的诊断题；已完成的内容会保留。")
                  .font(DS.font(14)).foregroundStyle(DS.secondary)
                BCButton("重试诊断") {
                  Task { await store.beginCloudLearning(
                    chapterID: store.cloudState.requestedChapterID, retryDiagnosis: true) }
                }.accessibilityIdentifier("cloud.diagnosis.retry")
              }
            }
          } else {
            ProgressView("正在恢复诊断…")
          }
        }
      }.padding(24).frame(maxWidth: 660).frame(maxWidth: .infinity)
    }.background(DS.background.ignoresSafeArea())
      .interactiveDismissDisabled(store.cloudState.isWorking && store.cloudState.courseTask == nil)
      .onChange(of: turn?.turn_id) { _, _ in
        selected = []
        started = Date()
      }
  }
}
