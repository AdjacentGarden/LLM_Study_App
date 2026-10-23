import SwiftUI
import AVFoundation
#if canImport(LearningCore)
import LearningCore
#endif

private struct DraftRoute: Identifiable { let id: String }

private struct NoteQualityDisclosure: View {
    let result: StudioResult
    let status: String
    var body: some View {
        if let notice = result.qualityDisclosure(status: status) {
            VStack(alignment: .leading, spacing: 4) {
                Label("审核范围提示", systemImage: "info.circle").font(.caption.bold())
                Text(notice).font(.callout)
                if status == "succeeded", result.provisional != true, result.review?.passed != false,
                   let count = result.excluded_optional_count, count > 0 {
                    Text("未纳入的 AI 可选补充：\(count) 条").font(.caption)
                }
            }.foregroundStyle(.orange).accessibilityElement(children: .combine)
        }
    }
}
struct NotesView: View {
    @EnvironmentObject var model: AppModel
    let book: Book
    @State private var route: DraftRoute?
    @State private var remote: [Note] = []
    @State private var error: String?
    var body: some View {
        List {
            Section("本机草稿与录音") {
                let drafts = model.state.notes.filter { $0.bookID == book.book_id }
                if drafts.isEmpty { Text("还没有笔记，点击右上角新增。").foregroundStyle(.secondary) }
                ForEach(drafts) { draft in
                    NavigationLink { NoteEditor(draftID: draft.id) } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(draft.title.isEmpty ? "未命名笔记" : draft.title)
                            Text(draft.recordingID == nil ? "文字草稿已在本机保存" : (draft.uploadComplete ? "录音已上传，本机副本保留" : "录音保存在本机，尚未上传")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Section("服务器语音笔记") {
                ForEach(remote) { note in NavigationLink { RemoteNoteView(note: note) } label: { Label(note.title, systemImage: "waveform") } }
                if remote.isEmpty { Text("没有服务器语音笔记").foregroundStyle(.secondary) }
                if let error { Text(error).font(.caption).foregroundStyle(.red) }
            }
        }.navigationTitle("学习笔记")
        .toolbar { ToolbarItem(placement: .primaryAction) { Button { Task { if let id = await model.createDraft(book: book.book_id) { route = DraftRoute(id: id) } } } label: { Label("新建笔记", systemImage: "square.and.pencil") } } }
        .sheet(item: $route) { route in NavigationStack { NoteEditor(draftID: route.id).toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { self.route = nil } } } }.environmentObject(model) }
        .task { await load() }.refreshable { await load() }
    }
    private func load() async {
        do { guard let api = model.api else { return }; remote = try await api.notes(book: book.book_id); error = nil }
        catch { self.error = error.localizedDescription }
    }
}

struct NoteEditor: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    let draftID: String
    @StateObject private var audio = AudioCapture()
    @State private var title = ""
    @State private var text = ""
    @State private var draft: NoteDraft?
    @State private var status: String?
    @State private var jobs: [StudioJob] = []
    @State private var consent = false
    @State private var analysisAction = "recognize"
    @State private var reviewText = ""
    @State private var reviewConfirmed = false
    @State private var serverPlayer: AVAudioPlayer?
    @State private var loaded = false
    @State private var saving = false
    @State private var saveTask: Task<Void, Never>?
    private func analysisFailed(_ action: String) -> Bool {
        jobs.first { $0.id == draft?.analysisJobID && $0.kind == action }?.status == "failed"
    }
    private var analysisLocked: Bool { draft?.voiceAnalysisLocked == true || jobs.contains(where: \.isPending) }
    var body: some View {
        Form {
            Section("笔记内容 · 自动保存在本机") {
                TextField("标题", text: $title).disabled(saving || model.busy).onChange(of: title) { _, _ in queueSave() }
                TextEditor(text: $text).frame(minHeight: 180).disabled(saving || model.busy).onChange(of: text) { _, _ in queueSave() }
                Button("同步文字到服务器") { Task { await syncText() } }.disabled(model.busy || saving || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || text.count > 20000 || title.count > 120)
                if text.count > 20000 { Text("服务器文字笔记最多 20000 字，本机草稿仍保留。").font(.caption).foregroundStyle(.orange) }
            }
            Section("原生语音录音") {
                Text("\(Int(audio.duration / 60)) 分 \(Int(audio.duration) % 60) 秒").monospacedDigit()
                if audio.isRecording {
                    Button("停止并保存录音", role: .destructive) { audio.stop(); queueSave(immediate: true) }
                } else {
                    Button(audio.localURL == nil ? "开始录音" : "另录一段（保留旧录音文件）") {
                        Task {
                            await audio.start()
                            // Save the file's identifier as soon as recording starts.
                            await saveLocal()
                        }
                    }.disabled(saving || model.busy || analysisLocked)
                }
                if audio.localURL != nil {
                    Button(audio.isPlaying ? "停止试听" : "试听本机录音") { if audio.isPlaying { audio.stopPlayback() } else { audio.playback() } }.disabled(audio.isRecording)
                    Button("上传这段录音") { Task { await uploadRecording() } }.disabled(audio.isRecording || model.busy || saving || analysisLocked || title.count > 120)
                    Text(draft?.uploadComplete == true ? "上传已确认，本机音频仍保留。" : "本机录音未上传。离开页面会先停止并保存，上传失败仍可恢复。").font(.caption).foregroundStyle(.secondary)
                }
                if audio.localURL == nil && !audio.isRecording { Button("找回最近一段本机录音") { audio.recoverLatest(); queueSave() }.disabled(analysisLocked || model.busy || saving) }
                if draft?.uploadComplete == true, let note = draft?.remoteNote {
                    Button("下载并试听当前服务器录音") {
                        Task {
                            await model.perform {
                                guard let api = model.api, try await api.note(note.id).revision == note.revision else { throw ClientError.configuration("录音已有新版本，请重新打开服务器笔记。") }
                                let url = try await model.cacheAudio(note: note)
                                guard draft?.remoteNote?.id == note.id, draft?.remoteNote?.revision == note.revision else { throw CancellationError() }
                                let player = try AVAudioPlayer(contentsOf: url)
                                player.prepareToPlay(); guard player.play() else { throw ClientError.invalidResponse("录音暂时无法播放") }
                                serverPlayer = player
                            }
                        }
                    }.disabled(audio.isRecording || model.busy || saving)
                    Button("停止服务器录音试听") { serverPlayer?.stop() }
                }
                if let error = audio.error { Text(error).foregroundStyle(.red) }
                Text("单段最多 10 分钟、20 MB。系统中断或切到后台会停止并保存。强制终止进程时，正在写入的音频容器可能无法恢复。").font(.caption).foregroundStyle(.secondary)
            }
            if draft?.uploadComplete == true {
                Section("语音转写与整理") {
                    Picker("处理方式", selection: $analysisAction) {
                        Text("仅语音转写").tag("recognize")
                        Text("转写并整理").tag("complete")
                    }.disabled(model.busy || analysisLocked).onChange(of: analysisAction) { _, _ in queueSave() }
                    Toggle("同意将这段录音交给自部署服务转写与整理", isOn: $consent)
                    Button(analysisFailed(analysisAction) ? (analysisAction == "recognize" ? "明确重试语音转写" : "明确重试转写与整理") : (analysisAction == "recognize" ? "开始语音转写" : "开始转写与整理")) { Task { await analyze(action: analysisAction, retry: analysisFailed(analysisAction)) } }.disabled(!consent || model.busy || saving || analysisLocked || !model.available("voice_notes") || (analysisAction == "complete" && !model.available("notes_ai")))
                    if let submission = draft?.analysisSubmission, submission.isUnacknowledged {
                        Text("上次提交的回执尚未确认。确认文字和请求编号已在本机保存；恢复会使用完全相同的内容，不会重新创建任务。").font(.caption).foregroundStyle(.orange)
                        Button("按原编号恢复提交") { Task { await analyze(action: submission.action) } }.disabled(!consent || model.busy || saving)
                    }
                    Button("刷新已有任务（不发起新整理）") { Task { await loadJobs() } }.disabled(model.busy || saving)
                    if !model.available("voice_notes") { Text("当前服务的语音转写尚未就绪，本机录音和文字草稿仍可使用。").font(.caption).foregroundStyle(.secondary) }
                    ForEach(jobs) { job in
                        VStack(alignment: .leading, spacing: 8) {
                            if job.isPending { ProgressView("正在处理：\(job.status)") }
                            if !job.error.isEmpty { Text(job.error).foregroundStyle(.red) }
                            NoteQualityDisclosure(result: job.result, status: job.status)
                            if let transcript = job.result.transcript { Text("转写").font(.headline); Text(transcript).textSelection(.enabled) }
                            if job.status == "needs_confirmation" { Text("识别存在疑词，尚未进入整理。请结合录音逐字核对。").foregroundStyle(.orange) }
                            if let uncertain = job.result.uncertain, !uncertain.isEmpty { Text("待核对：" + uncertain.joined(separator: "、")).font(.caption).foregroundStyle(.orange) }
                            if ["succeeded", "needs_confirmation"].contains(job.status), !(job.result.transcript ?? "").isEmpty {
                                Button("核对这份转写") { Task { await beginReview(job) } }.disabled(analysisLocked || model.busy || saving)
                            }
                            if let summary = job.result.summary { Text(summary).textSelection(.enabled) }
                            if let polished = job.result.polished { Text(polished).textSelection(.enabled) }
                            if let caution = job.result.caution { Text(caution).foregroundStyle(.secondary) }
                        }
                    }
                }
                if let review = draft?.transcriptReview, review.context == draft?.voiceContext {
                    Section("人工核对转写 · 与笔记草稿分开保存") {
                        Text("请试听录音并修正识别文字。修改文字会撤销确认；不会改写上方笔记草稿或删除录音。").font(.caption).foregroundStyle(.secondary)
                        TextEditor(text: $reviewText).frame(minHeight: 160).disabled(model.busy || saving || analysisLocked)
                            .onChange(of: reviewText) { _, _ in reviewConfirmed = false; queueSave() }
                        Text("\(reviewText.unicodeScalars.count) / 12000 字符").font(.caption).foregroundStyle(.secondary)
                        Toggle("我已核对并确认以上转写，允许使用这份文字整理", isOn: $reviewConfirmed)
                            .disabled(model.busy || saving || analysisLocked).onChange(of: reviewConfirmed) { _, _ in queueSave() }
                        Button(analysisFailed("improve") ? "明确重试确认文字整理" : "使用确认后的转写整理") { Task { await analyze(action: "improve", retry: analysisFailed("improve")) } }
                            .disabled(!reviewConfirmed || model.busy || saving || analysisLocked || !model.available("notes_ai"))
                        if analysisLocked { Text("正在处理或等待原提交回执，确认内容已冻结；请先刷新或按原编号恢复。").font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
            if let status { Section { Text(status).font(.callout) } }
        }
        .navigationTitle("笔记草稿")
        .task {
            if !loaded {
                loaded = true
                draft = model.state.notes.first { $0.id == draftID }
                title = draft?.title ?? ""; text = draft?.text ?? ""
                analysisAction = draft?.analysisAction ?? "recognize"
                if analysisAction == "improve" { analysisAction = "recognize" }
                reviewText = draft?.transcriptReview?.text ?? ""; reviewConfirmed = draft?.transcriptReview?.confirmed ?? false
                if let id = draft?.recordingID { audio.recover(id: id) }
            }
            // A retained editor can reappear after its previous task was cancelled.
            // Always refresh and restart polling without resetting local edits.
            await loadJobs()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(3)) } catch { return }
                if scenePhase == .active, jobs.contains(where: \.isPending) || draft?.voiceAnalysisLocked == true { await loadJobs() }
            }
        }
        .onChange(of: audio.recordingID) { _, id in
            serverPlayer?.stop()
            if id != draft?.recordingID { jobs = [] }
            queueSave(immediate: true)
        }
        .onChange(of: audio.isRecording) { _, recording in if !recording { queueSave(immediate: true) } }
        .onChange(of: scenePhase) { _, phase in if phase != .active { audio.stop(); queueSave(immediate: true) } }
        .onDisappear { audio.stop(); audio.stopPlayback(); serverPlayer?.stop(); queueSave(immediate: true) }
    }
    private func queueSave(immediate: Bool = false) {
        guard loaded else { return }; saveTask?.cancel()
        saveTask = Task {
            if !immediate {
                do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            }
            await saveLocal()
        }
    }
    private func saveLocal() async {
        guard var value = model.state.notes.first(where: { $0.id == draftID }) ?? draft else { return }
        value.title = title; value.text = text
        // Ordinary prose edits cannot change a voice job's stable request body.
        if value.analysisSubmission == nil { value.analysisAction = analysisAction }
        if value.recordingID != audio.recordingID {
            do { try value.replaceVoiceRecording(with: audio.recordingID) }
            catch {
                status = error.localizedDescription; audio.stop()
                if let previous = value.recordingID { audio.recover(id: previous) }
                draft = value; await model.saveDraft(value); return
            }
            jobs = []
            reviewText = ""; reviewConfirmed = false
        }
        value.recordingID = audio.recordingID
        if value.transcriptReview != nil, !value.voiceAnalysisLocked {
            do { try value.updateVoiceTranscriptReview(text: reviewText, confirmed: reviewConfirmed) }
            catch { reviewConfirmed = false; status = error.localizedDescription }
            reviewConfirmed = value.transcriptReview?.confirmed ?? false
        }
        draft = value; await model.saveDraft(value)
    }
    private func syncText() async {
        await saveLocal(); guard let value = draft else { return }
        saving = true; defer { saving = false }
        await model.perform {
            guard let api = model.api else { throw ClientError.configuration("请先连接学习服务") }
            let response = try await api.saveTextNote(book: value.bookID, title: title.isEmpty ? "学习笔记" : title, text: text, resourceID: value.remoteTextID)
            await updateRemote { $0.remoteTextID = response.id }
            status = "文字笔记已同步"
        }
    }
    private func uploadRecording() async {
        guard !analysisLocked else { status = "请先恢复当前任务；处理中不能覆盖录音。"; return }
        audio.stop(); await saveLocal(); guard let value = draft else { return }
        saving = true; defer { saving = false }
        jobs = []
        // A save or PUT may change the server revision even if its response is lost.
        // Confirm this upload before enabling analysis of the resulting version.
        await updateRemote { $0.uploadComplete = false; $0.clearVoiceAnalysis() }
        reviewText = ""; reviewConfirmed = false
        await model.perform {
            guard let api = model.api else { throw ClientError.configuration("请先连接学习服务") }
            let data = try audio.recordingData()
            guard data.count <= 20 * 1024 * 1024, audio.duration > 0, audio.duration <= 602 else { throw ClientError.configuration("录音需在 10 分钟、20 MB 以内") }
            var note = value.remoteNote ?? Note(id: "note_" + value.id, bookID: value.bookID, title: title.isEmpty ? "语音笔记" : title)
            // Resolve an uncertain earlier save before changing its revision or title.
            do { note = try await api.note(note.id) }
            catch ClientError.http(let code, _) where code == 404 { }
            note.title = title.isEmpty ? "语音笔记" : title
            note = try await api.saveNote(note)
            await updateRemote { $0.remoteNote = note }
            note = try await api.uploadAudio(note: note, data: data, seconds: audio.duration)
            await updateRemote { $0.remoteNote = note; $0.uploadComplete = true; $0.clearVoiceAnalysis() }
            await loadJobs()
            status = "录音已上传；可在服务器语音笔记中重新下载试听"
        }
    }
    private func analyze(action: String, retry: Bool = false) async {
        saveTask?.cancel()
        await saveLocal()
        guard consent || action == "improve", let value = draft, value.uploadComplete,
              value.recordingID == audio.recordingID else { return }
        saving = true; defer { saving = false; draft = model.state.notes.first { $0.id == draftID } }
        await model.perform {
            let job = try await model.submitVoiceAnalysis(draftID: draftID, action: action, retry: retry)
            draft = model.state.notes.first { $0.id == draftID }
            jobs.removeAll { $0.id == job.id }; jobs.insert(job, at: 0)
            status = action == "improve" ? "已提交确认后的转写整理，退出后服务器继续处理" : "转写任务已保存，退出后服务器继续处理"
        }
    }
    private func beginReview(_ job: StudioJob) async {
        await saveLocal()
        await model.perform {
            try await model.beginVoiceTranscriptReview(draftID: draftID, job: job)
            draft = model.state.notes.first { $0.id == draftID }
            reviewText = draft?.transcriptReview?.text ?? ""; reviewConfirmed = false
            status = "转写已放入独立核对区；请试听、修正并明确确认。"
        }
    }
    private func loadJobs() async {
        guard draft?.uploadComplete == true else {
            jobs = []
            return
        }
        let recordingID = draft?.recordingID
        do {
            let fetched = try await model.refreshVoiceJobs(draftID: draftID)
            // Discard a response for an earlier recording or upload. Server history
            // stays intact; a job without a revision is not current-version evidence.
            guard audio.recordingID == recordingID else { return }
            draft = model.state.notes.first { $0.id == draftID }
            jobs = fetched
        }
        catch { status = error.localizedDescription }
    }
    private func updateRemote(_ update: (inout NoteDraft) -> Void) async {
        guard var latest = draft else { return }
        update(&latest); latest.title = title; latest.text = text
        draft = latest; await model.saveDraft(latest)
    }
}

struct RemoteNoteView: View {
    @EnvironmentObject var model: AppModel
    let note: Note
    @State private var player: AVAudioPlayer?
    @State private var status: String?
    @State private var detail: Note?
    @State private var editorRoute: DraftRoute?
    var body: some View {
        Form {
            Text(note.title).font(.headline)
            if note.audio_ready == true {
                Button("核对转写与整理") {
                    Task {
                        await model.perform {
                            guard let api = model.api else { throw ClientError.configuration("请先连接学习服务") }
                            let current = try await api.note(note.id)
                            editorRoute = DraftRoute(id: try await model.openVoiceNote(current))
                        }
                    }
                }.disabled(model.busy)
                Button("从服务器下载并试听录音") {
                    Task {
                        await model.perform {
                            let url = try await model.cacheAudio(note: note)
                            let nativePlayer = try AVAudioPlayer(contentsOf: url)
                            nativePlayer.prepareToPlay(); guard nativePlayer.play() else { throw ClientError.invalidResponse("音频暂时无法播放") }
                            player = nativePlayer; status = "正在试听服务器保存的录音"
                        }
                    }
                }.disabled(model.busy)
                Button("停止试听") { player?.stop() }
            } else { Text("这篇笔记没有已上传录音") }
            if let edition = detail?.edition {
                // The server's saved edition is a completed job; a provisional flag still wins.
                NoteQualityDisclosure(result: edition, status: "succeeded")
                if let text = edition.transcript { Text(text).textSelection(.enabled) }
                if let text = edition.polished { Text(text).textSelection(.enabled) }
            }
            if let status { Text(status).font(.caption) }
        }.navigationTitle("服务器笔记")
        .sheet(item: $editorRoute) { route in NavigationStack { NoteEditor(draftID: route.id).toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { editorRoute = nil } } } }.environmentObject(model) }
        .onDisappear { player?.stop() }.task {
            do { detail = try await model.api?.note(note.id) } catch { status = error.localizedDescription }
        }
    }
}
