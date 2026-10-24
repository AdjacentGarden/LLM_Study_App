import SwiftUI
import UniformTypeIdentifiers
#if canImport(LearningCore)
import LearningCore
#endif

@main
struct CloudPathApp: App {
    @StateObject private var model = AppModel()
    var body: some Scene {
        WindowGroup { RootView().environmentObject(model) }
    }
}

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        VStack(spacing: 0) {
            if let error = model.error {
                VStack(alignment: .leading, spacing: 8) {
                    Text(error).font(.callout).textSelection(.enabled)
                    HStack {
                        Button("重新连接") { Task { await model.reconnect() } }
                        Spacer(); Button("收起") { model.error = nil }
                    }
                }.padding().background(.red.opacity(0.08)).accessibilityIdentifier("connection-error")
            }
            TabView {
                NavigationStack { ShelfView() }.id(model.configuration?.namespace ?? "no-service").tabItem { Label("书架", systemImage: "books.vertical") }
                NavigationStack { TasksView() }.tabItem { Label("任务", systemImage: "arrow.triangle.2.circlepath") }
                NavigationStack { SettingsView() }.tabItem { Label("服务与身份", systemImage: "gearshape") }
            }
        }
        .tint(.indigo)
        .task {
            await model.bootstrap()
            while !Task.isCancelled {
                if scenePhase == .active { await model.tick() }
                try? await Task.sleep(for: .seconds(2))
            }
        }
        .onChange(of: scenePhase) { _, phase in if phase == .active && model.configuration != nil { Task { await model.refresh() } } }
    }
}

struct ShelfView: View {
    @EnvironmentObject var model: AppModel
    @State private var importing = false
    var body: some View {
        List {
            if model.configuration == nil {
                ContentUnavailableView("连接你的学习服务", systemImage: "network", description: Text("先在“服务与身份”填写同局域网或 VPN 内的后端地址。"))
            } else {
                Section {
                    Label(model.connected ? "已连接学习服务" : "连接尚未完成", systemImage: model.connected ? "checkmark.circle" : "wifi.exclamationmark")
                    if let account = model.account { Text(account.account?.email ?? "游客学习空间").font(.caption).foregroundStyle(.secondary) }
                }
                if model.books.isEmpty { ContentUnavailableView("书架还没有教材", systemImage: "book.closed", description: Text("导入 PDF 后，识别和整理将在服务器后台执行。")) }
                ForEach(model.books) { book in
                    NavigationLink { BookView(book: book) } label: {
                        VStack(alignment: .leading, spacing: 7) {
                            Text(book.title).font(.headline)
                            Text("\(book.page_count.map(String.init) ?? "—") 页 · \(book.chapter_count) 章").font(.caption).foregroundStyle(.secondary)
                            Text(book.diagnostics_ready == true ? "可开始学习诊断" : "教材准备未完成").font(.caption).foregroundStyle(book.diagnostics_ready == true ? .green : .secondary)
                        }.padding(.vertical, 5)
                    }
                }
            }
        }
        .navigationTitle("知我 · 书架")
        .toolbar {
            ToolbarItem(placement: .primaryAction) { Button { importing = true } label: { Label("导入 PDF", systemImage: "plus") }.disabled(!model.connected || model.busy) }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.pdf], allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls): if let url = urls.first { Task { await model.importPDF(url) } }
            case .failure(let error): model.error = error.localizedDescription
            }
        }
        .refreshable { await model.refresh() }
        .overlay { if model.busy { ProgressView("正在处理…").padding().background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12)) } }
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @State private var address = "http://10.249.186.201:8100"
    @State private var localHTTP = false
    @State private var validation: String?
    var body: some View {
        Form {
            Section("学习服务") {
                TextField("https://study.example.com", text: $address).autocorrectionDisabled().accessibilityIdentifier("server-address")
                Toggle("允许局域网 HTTP 开发连接", isOn: $localHTTP)
                Text("HTTP 仅支持私网、本机或 .local 地址；请让手机与服务器处于同一局域网或 VPN。首次连接可能需要允许系统的“本地网络”权限。正式使用建议 HTTPS。").font(.footnote).foregroundStyle(.secondary)
                if let validation { Text(validation).foregroundStyle(.red) }
                Button(model.busy ? "连接中…" : "保存并检查连接") {
                    do {
                        let config = try ServerConfiguration(address: address, allowLocalHTTP: localHTTP)
                        validation = nil; Task { await model.connect(config) }
                    } catch { validation = error.localizedDescription }
                }.disabled(model.busy)
                if model.connected { Label("服务与游客身份已连接", systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                if let notice = model.notice { Text(notice).foregroundStyle(.orange) }
            }
            Section("当前身份") {
                if let account = model.account {
                    Text(account.account?.email ?? "游客学习")
                    Text("书架、会话和后台任务绑定当前服务的游客 Cookie，并在本机保存恢复信息。更换服务会使用独立空间。").font(.footnote)
                    if !account.email_available { Text("该服务尚未开启邮箱登录，当前可使用游客学习。").font(.footnote).foregroundStyle(.secondary) }
                } else { Text("连接后由服务器建立游客学习空间。") }
            }
            Section("设备与模型") {
                Text("这是原生 SwiftUI 客户端；教材 OCR、课程和答疑由你配置的服务器处理。手机不保存模型 API 密钥。")
                Text("录音只在明确点击录制后使用麦克风，先保存到本机，再由你选择上传。")
                if let readiness = model.readiness {
                    Text(readiness.blocked_features.isEmpty ? "后台功能已配置，等待具体操作验证。" : "部分后台功能暂未就绪，请稍后重试。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("功能配置与进程检查不等于模型推理实测通过。").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("服务与身份")
        .onAppear { if let config = model.configuration { address = config.baseURL.absoluteString; localHTTP = config.allowLocalHTTP } }
    }
}

struct TasksView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        List {
            Section("PDF 导入与整理") {
                if model.state.imports.isEmpty { Text("没有导入任务").foregroundStyle(.secondary) }
                ForEach(model.state.imports) { task in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(task.filename).font(.headline)
                        Text(task.message).font(.callout)
                        if task.stage == "ocr" { ProgressView(value: min(1, max(0, task.progress))) }
                        else if task.isFinished { Label("已完成", systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                        else if task.error == nil { ProgressView() }
                        if let error = task.error { Text(error).font(.caption).foregroundStyle(.red) }
                        if task.stage == "staged" || (task.error != nil && task.retryable) {
                            Button(task.stage == "upload_uncertain" ? "从本机 PDF 重新上传" : "重试当前步骤") { Task { await model.retryImport(task.id) } }.disabled(model.busy)
                        }
                    }.padding(.vertical, 5)
                }
            }
            Section("章节课程") {
                if model.state.courseJobs.isEmpty { Text("没有课程生成任务").foregroundStyle(.secondary) }
                ForEach(Array(model.state.courseJobs.values).sorted { $0.created_at > $1.created_at }, id: \.job_id) { job in
                    let key = StoredState.courseKey(session: job.session_id, chapter: job.chapter_id)
                    let conflict = model.state.courseConflicts?[key]
                    VStack(alignment: .leading, spacing: 6) {
                        Text("章节 \(job.chapter_id)").font(.headline)
                        Text(job.stage == "ready" ? "课程已审核并保存" : "\(job.stage) · 第 \(job.attempts) 次尝试")
                        if let conflict { Text("需核对并重新准备：" + conflict).font(.caption).foregroundStyle(.red) }
                        else if job.isPending { ProgressView() }
                        if let failure = job.clientFailureMessage { Text(failure).font(.caption).foregroundStyle(.red) }
                        else if let error = job.error { Text(error).font(.caption).foregroundStyle(.red) }
                    }
                }
            }
            Section { Text("排队或执行中的服务器任务会在离开 App 后继续；失败任务不会自动重启。再次打开会恢复已有编号并查询进度，不会重复提交生成。PDF 上传使用前台传输；上传中断时会保留本机文件并提示确认重传。").font(.footnote).foregroundStyle(.secondary) }
        }.navigationTitle("任务与恢复")
    }
}

struct BookView: View {
    @EnvironmentObject var model: AppModel
    let book: Book
    @State private var structure: BookStructure?
    @State private var error: String?
    @State private var structureUnverified = false
    private var currentBook: Book { model.books.first { $0.book_id == book.book_id } ?? book }
    private var preparation: PreparationJob? { model.state.bookPreparations?[book.book_id] }
    var body: some View {
        List {
            Section {
                Text(book.summary.isEmpty ? (structure?.summary ?? "暂无教材摘要") : book.summary)
                NavigationLink { PDFReaderView(bookID: book.book_id, title: book.title) } label: { Label("阅读原书 PDF", systemImage: "doc.richtext") }
                NavigationLink { InterviewView(book: book) } label: { Label("学习目标与诊断", systemImage: "person.crop.circle.badge.questionmark") }
                    .disabled(currentBook.diagnostics_ready != true || !model.connected)
                NavigationLink { QuestionView(book: book) } label: { Label("向教材提问", systemImage: "bubble.left.and.text.bubble.right") }.disabled(!model.available("qa"))
                NavigationLink { NotesView(book: book) } label: { Label("文字与语音笔记", systemImage: "mic.and.signal.meter") }
            }
            if currentBook.diagnostics_ready != true {
                Section("更新学习准备") {
                    Text("章节或诊断题尚未就绪。可在服务器后台重新整理已有教材，不会重新上传或重跑 OCR。")
                    if let preparation {
                        Text("\(preparation.stage) · \(preparation.status)").font(.caption)
                        if !preparation.error.isEmpty { Text(preparation.error).foregroundStyle(.red) }
                    }
                    if let preparation, ["queued", "running"].contains(preparation.status) {
                        ProgressView("后台整理中，可离开此页面；下次连接会恢复进度")
                    } else {
                        Button(preparation?.status == "failed" ? "重试整理与诊断题" : "整理章节与诊断题") {
                            Task { await model.prepareExistingBook(book.book_id, retry: preparation?.status == "failed") }
                        }.disabled(model.busy || !model.connected)
                    }
                    Button("查询准备状态") { Task { await model.refreshExistingPreparation(book.book_id) } }.disabled(model.busy || !model.connected)
                }
            }
            if let structure {
                Section("章节") {
                    if structureUnverified { Text("正在使用本机保存的章节目录，尚未完成联网核验。").font(.caption).foregroundStyle(.orange) }
                    ForEach(structure.chapters.sorted { $0.order < $1.order }) { chapter in
                        NavigationLink { ChapterView(book: book, chapter: chapter) } label: {
                            VStack(alignment: .leading, spacing: 5) { Text(chapter.title); Text("原书第 \(chapter.start_page)–\(chapter.end_page) 页").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                }
            } else if let error {
                Section { Text(error).foregroundStyle(.red); Button("重新读取章节") { Task { await load() } } }
            } else { ProgressView("读取章节…") }
        }.navigationTitle(book.title).task { await load() }
    }
    private func load() async {
        if structure == nil { structure = model.state.bookStructures?[book.book_id]; structureUnverified = structure != nil }
        do { structure = try await model.bookStructure(book.book_id); error = nil; structureUnverified = false }
        catch { if structure == nil { self.error = error.localizedDescription } else { structureUnverified = true } }
        if currentBook.diagnostics_ready != true { await model.refreshExistingPreparation(book.book_id) }
    }
}

struct InterviewView: View {
    @EnvironmentObject var model: AppModel
    let book: Book
    @State private var input = ""
    @State private var selected: [String] = []
    @State private var confidence = 0.65
    @State private var started = Date()
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let interview = model.interviews[book.book_id] {
                    if let notice = model.notice { Text(notice).font(.caption).foregroundStyle(.secondary) }
                    if interview.phase == "complete" {
                        Label("学习方向已确认", systemImage: "checkmark.seal.fill").font(.title2)
                        Text(interview.turn.message)
                        Text("返回教材目录，选择章节生成课程。").foregroundStyle(.secondary)
                    } else {
                        let turn = interview.turn
                        ProgressView(value: min(1, max(0, turn.progress > 1 ? turn.progress / 100 : turn.progress)))
                        Text(turn.message).font(.headline)
                        if let question = turn.question { Text(question) }
                        if let item = turn.item { Text(item.prompt).font(.title3) }
                        ForEach(turn.options) { option in
                            Button { choose(option.id, multiple: turn.response_type == "multiple_choice") } label: {
                                HStack { Text(option.label).multilineTextAlignment(.leading); Spacer(); if selected.contains(option.id) { Image(systemName: "checkmark.circle.fill") } }.padding().background(selected.contains(option.id) ? Color.indigo.opacity(0.12) : Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                            }.buttonStyle(.plain)
                        }
                        TextField("也可以输入你的回答", text: $input, axis: .vertical).textFieldStyle(.roundedBorder).lineLimit(3...8)
                        if turn.item != nil { VStack(alignment: .leading) { Text("把握程度：\(Int(confidence * 100))% "); Slider(value: $confidence, in: 0...1) } }
                        if let why = turn.why_asked { Text(why).font(.caption).foregroundStyle(.secondary) }
                        Button(model.busy ? "提交中…" : "确认并继续") {
                            Task {
                                await model.answerInterview(book: book.book_id, text: input, selected: selected, confidence: confidence, seconds: Date().timeIntervalSince(started))
                            }
                        }.buttonStyle(.borderedProminent).disabled(model.busy || (input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && selected.isEmpty && !(turn.phase == "adaptive_diagnosis" && turn.item == nil)))
                        Button("重新同步当前步骤") { Task { await model.openInterview(book: book) } }.font(.caption).disabled(model.busy)
                    }
                } else {
                    Text("通过学习目标与几道诊断题，为你生成有依据的章节课程。")
                    Button("开始或恢复诊断") { Task { await model.openInterview(book: book) } }.buttonStyle(.borderedProminent).disabled(model.busy)
                }
            }.padding()
        }
        .navigationTitle("学习诊断")
        .onChange(of: model.interviews[book.book_id]?.turn.turn_id) { _, _ in input = ""; selected = []; confidence = 0.65; started = Date() }
        .task { if model.state.sessions[book.book_id] != nil { await model.openInterview(book: book) } }
    }
    private func choose(_ id: String, multiple: Bool) {
        if multiple { if selected.contains(id) { selected.removeAll { $0 == id } } else { selected.append(id) } }
        else { selected = [id] }
    }
}
