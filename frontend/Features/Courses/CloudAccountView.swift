import SwiftUI

struct CloudAccountView: View {
  @Environment(AppStore.self) private var store
  @State private var email = ""
  @State private var code = ""
  @State private var nickname = ""
  @State private var stage = "university"
  @State private var goal = "interest"
  @State private var registering = false
  @State private var challenge: CloudChallenge?
  @State private var challengeEmail = ""
  @State private var challengePurpose = ""
  @State private var resendDate = Date.distantPast
  @State private var busy = false
  @State private var error: String?
  @State private var server = ""
  @FocusState private var focused: Bool
  var body: some View {
    ScrollView {
      VStack(spacing: 24) {
        StickerIcon("Sparkles", tone: "yellow", size: 64).padding(.top, 44)
        Text("开始你的云径学习").font(DS.font(28, weight: .bold))
        Text(store.cloudState.localLogin ? "正在使用本地默认账号，无需邮箱验证。" : "登录后，在 iPhone 和 iPad 继续学习。")
          .font(DS.font(16)).foregroundStyle(DS.secondary)
        if store.cloudState.localLogin {
          if let message = error ?? store.cloudState.error {
            Text(message).font(DS.font(14)).foregroundStyle(DS.danger)
          }
          BCButton("连接本地服务并进入 App") {
            store.isLoading = true
            Task { await store.bootCloud() }
          }.accessibilityIdentifier("cloud.localRetry")
        } else {
          HStack(spacing: 12) {
            modeButton("登录", register: false)
            modeButton("注册", register: true)
          }
          VStack(alignment: .leading, spacing: 16) {
            TextField("邮箱地址", text: $email).textInputAutocapitalization(.never)
              .focused($focused)
              .keyboardType(.emailAddress).textContentType(.emailAddress)
              .accessibilityIdentifier("cloud.email").padding(16).background(
                DS.surface, in: RoundedRectangle(cornerRadius: 16)
              )
              .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border))
            if registering {
              BCTextField("你的称呼", text: $nickname).accessibilityIdentifier("cloud.nickname")
                .focused($focused)
              Picker("学习阶段", selection: $stage) {
                Text("初中").tag("middle")
                Text("高中").tag("high")
                Text("大学").tag("university")
                Text("工作").tag("working")
                Text("其他").tag("other")
              }
              Picker("学习目标", selection: $goal) {
                Text("准备考试").tag("exam")
                Text("工作应用").tag("work")
                Text("兴趣学习").tag("interest")
              }
            }
            TimelineView(.periodic(from: .now, by: 1)) { context in
              let remaining = max(0, Int(ceil(resendDate.timeIntervalSince(context.date))))
              BCButton(remaining > 0 ? "\(remaining) 秒后可重新发送" : "获取邮箱验证码", style: .secondary) {
                Task { await sendCode() }
              }.disabled(busy || email.isEmpty || remaining > 0).accessibilityIdentifier(
                "cloud.sendCode")
            }
            if let challenge, email == challengeEmail,
              challengePurpose == (registering ? "register" : "login")
            {
              Text(challenge.message).font(DS.font(14)).foregroundStyle(DS.secondary)
              if let demo = challenge.demo_code {
                Text("测试账号验证码：\(demo)").font(DS.font(14, weight: .bold)).foregroundStyle(DS.primary)
                  .accessibilityIdentifier("cloud.demoCode")
              }
              TextField("六位验证码", text: $code).keyboardType(.numberPad).textContentType(.oneTimeCode)
                .focused($focused)
                .padding(16).background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
                .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border))
                .accessibilityIdentifier("cloud.code")
              BCButton(busy ? "正在登录…" : registering ? "创建账号并开始" : "登录并继续学习") {
                Task { await verifyCode() }
              }.disabled(
                busy || code.count != 6
                  || (registering
                    && nickname.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
              )
              .accessibilityIdentifier("cloud.verify")
            }
            if let message = error ?? store.cloudState.error {
              Text(message).font(DS.font(14)).foregroundStyle(DS.danger).accessibilityIdentifier(
                "cloud.error")
            }
          }
        }
        DisclosureGroup("学习服务地址") {
          VStack(spacing: 12) {
            TextField(CloudAPI.defaultServer, text: $server).textInputAutocapitalization(.never)
              .keyboardType(.URL).font(DS.font(14)).padding(12).background(
                DS.surface, in: RoundedRectangle(cornerRadius: 12)
              )
              .accessibilityIdentifier("cloud.server")
            BCButton("连接此服务", style: .secondary) {
              guard let url = CloudAPI.validatedURL(server) else {
                error = "请输入有效的 HTTP 或 HTTPS 服务地址"
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
              resendDate = .distantPast
              store.isLoading = true
              Task { await store.boot() }
            }.disabled(busy)
            Text("同一局域网使用 Windows 学习服务地址；通过 VPN 可填写 http://100.86.143.88:8100。")
              .font(DS.font(12)).foregroundStyle(DS.secondary)
          }.padding(.top, 12)
        }.font(DS.font(14)).foregroundStyle(DS.secondary)
        Spacer(minLength: 24)
      }.frame(maxWidth: 480).padding(.horizontal, 24).frame(maxWidth: .infinity)
    }.scrollDismissesKeyboard(.interactively)
      .background(DS.background.ignoresSafeArea())
      .onAppear { server = store.cloud?.baseURL.absoluteString ?? CloudAPI.defaultServer }
      .toolbar {
        ToolbarItemGroup(placement: .keyboard) {
          Spacer()
          Button("完成") { focused = false }.accessibilityIdentifier("cloud.keyboardDone")
        }
      }
  }
  private func modeButton(_ title: String, register: Bool) -> some View {
    Button {
      registering = register
      challenge = nil
      code = ""
      error = nil
    } label: {
      Text(title).font(DS.font(18, weight: .bold)).frame(maxWidth: .infinity).padding(14)
        .background(registering == register ? DS.primarySoft : DS.surface, in: Capsule())
        .foregroundStyle(registering == register ? DS.primary : DS.secondary)
    }.buttonStyle(.plain).disabled(busy).accessibilityIdentifier(
      register ? "cloud.registerMode" : "cloud.loginMode")
  }
  private func sendCode() async {
    focused = false
    guard let cloud = store.cloud else { return }
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
      email = normalized
      challengeEmail = normalized
      challengePurpose = purpose
      resendDate = Date().addingTimeInterval(Double(challenge?.retry_after ?? 60))
    } catch { self.error = error.localizedDescription }
  }
  private func verifyCode() async {
    focused = false
    guard let cloud = store.cloud, let challenge else { return }
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
          "nickname": .string(nickname), "stage": .string(stage),
          "interests": .array([]), "goal": .string(goal),
        ])
      }
      let _: CloudAccountState = try await cloud.request(
        "/api/auth/verify", method: "POST", body: body)
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
          Text("准备专属课程").font(DS.font(24, weight: .bold))
          Spacer()
          Button("稍后继续") { store.pauseCloudCourseGeneration() }.disabled(
            store.cloudState.isWorking && store.cloudState.courseTask == nil)
        }
        if let job = store.cloudState.courseJob {
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
          BCProgress(value: turn.progress)
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
                ? "确认并生成课程" : turn.phase == "complete" ? "生成或继续本章课程" : "继续"
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
      .onDisappear { store.cloudState.courseTask?.cancel() }
      .onChange(of: turn?.turn_id) { _, _ in
        selected = []
        started = Date()
      }
  }
}
