import Foundation
import SwiftUI
#if canImport(LearningCore)
import LearningCore
#endif

@MainActor
final class AppModel: ObservableObject {
    @Published var configuration: ServerConfiguration?
    @Published var account: AccountState?
    @Published var books: [Book] = []
    @Published var state = StoredState()
    @Published var interviews: [String: Interview] = [:]
    @Published var error: String?
    @Published var notice: String?
    @Published var busy = false
    @Published var connected = false
    @Published var loaded = false
    @Published var readiness: Readiness?
    @Published var courseValidationMessages: [String: String] = [:]
    private(set) var api: APIClient?
    private var disk: DiskStore?
    private var workflow: ImportWorkflow?
    private var polling = false
    private var generation = UUID()
    private var nextPoll = Date.distantPast
    private var pollFailures = 0
    private var validatingCourses: [String: UUID] = [:]
    private var submittingVoiceNotes: Set<String> = []
    private let root: URL
    private let settings: DiskStore?
    private let transport: any HTTPTransport
    init(root override: URL? = nil, transport: any HTTPTransport = SessionTransport()) {
        self.transport = transport
        root = override ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("CloudPath", isDirectory: true)
        do { settings = try DiskStore(root: root) }
        catch { settings = nil; self.error = "无法初始化本地存储：\(error.localizedDescription)" }
    }
    func bootstrap() async {
        guard !loaded else { return }; loaded = true
        do {
            if let config = try await settings?.load(ServerConfiguration.self, from: "server.json") { await connect(config) }
        } catch { self.error = "无法恢复服务配置：\(error.localizedDescription)" }
    }
    func connect(_ config: ServerConfiguration) async {
        guard !busy else { return }; busy = true; generation = UUID(); nextPoll = .distantPast; pollFailures = 0; defer { busy = false }
        do {
            let disk = try DiskStore(root: root.appendingPathComponent(config.namespace, isDirectory: true))
            let api = APIClient(configuration: config, store: disk, transport: transport)
            try await api.restoreCookies()
            let stored = try await disk.load(StoredState.self, from: "state.json") ?? StoredState()
            self.configuration = config; self.disk = disk; self.api = api; self.state = stored
            self.books = stored.cachedBooks ?? []; self.interviews = [:]; self.account = nil; self.readiness = nil; connected = false
            self.validatingCourses = [:]; self.courseValidationMessages = [:]
            if state.courseValidation == nil { state.courseValidation = [:] }
            for key in state.courses.keys where state.courseValidation?[key] != "invalid" { state.courseValidation?[key] = "unverified" }
            self.workflow = ImportWorkflow(api: api, disk: disk)
            try await settings?.save(config, as: "server.json")
            for index in state.imports.indices { state.imports[index] = try await workflow!.recover(state.imports[index]) }
            try await refreshConnection()
            error = nil
        } catch { connected = false; self.error = error.localizedDescription }
    }
    func reconnect() async {
        guard let configuration else { return }; await connect(configuration)
    }
    private func refreshConnection() async throws {
        guard let api else { return }
        connected = false
        if state.courseValidation == nil { state.courseValidation = [:] }
        for key in state.courses.keys where state.courseValidation?[key] != "invalid" {
            state.courseValidation?[key] = "unverified"
            courseValidationMessages[key] = "正在重新核验连接；本机历史课程可阅读，核验前暂不提交评分。"
        }
        let health = try await api.checkHealth()
        let identity = try await api.account()
        state.acceptAccount(identity); account = identity
        books = try await api.books(); state.cachedBooks = books; connected = true
        readiness = try? await api.readiness()
        notice = health.ok ? nil : "服务器可连接，但部分后台服务尚未就绪。"
        try await persist()
        for book in books where book.diagnostics_ready != true { await refreshExistingPreparation(book.book_id) }
        for (book, session) in state.sessions {
            let chapters = state.courses.filter { $0.key.hasPrefix(session + ":") }.map { $0.value.chapter_id }
            for chapter in chapters { await validateCachedCourse(book: book, chapter: chapter) }
        }
    }
    func refresh() async { await perform { try await self.refreshConnection() } }
    func persist() async throws { guard let disk else { throw ClientError.configuration("请先连接学习服务") }; try await disk.save(state, as: "state.json") }
    func perform(_ operation: () async throws -> Void) async {
        guard !busy else { return }; busy = true; error = nil; defer { busy = false }
        do { try await operation() } catch { self.error = error.localizedDescription }
    }
    func importPDF(_ source: URL) async {
        await perform {
            guard let disk = self.disk else { throw ClientError.configuration("请先连接学习服务") }
            let access = source.startAccessingSecurityScopedResource(); defer { if access { source.stopAccessingSecurityScopedResource() } }
            let task = try await disk.stagePDF(from: source)
            self.state.imports.insert(task, at: 0); try await self.persist()
            await self.advanceImport(task.id, retry: false)
        }
    }
    func retryImport(_ id: String) async { await perform { await self.advanceImport(id, retry: true) } }
    private func advanceImport(_ id: String, retry: Bool) async {
        guard let workflow, let task = state.imports.first(where: { $0.id == id }) else { return }
        let epoch = generation
        let updated = await workflow.advance(task, retry: retry)
        guard generation == epoch, let index = state.imports.firstIndex(where: { $0.id == id }) else { return }
        state.imports[index] = updated
        do { try await persist() } catch { self.error = error.localizedDescription }
    }
    /// Foreground polling only. The server's jobs continue when the app is closed.
    func tick() async {
        guard connected, !busy, !polling, Date() >= nextPoll, let api else { return }
        polling = true; defer { polling = false }
        let epoch = generation
        let oldReady = state.imports.filter(\.isFinished).count
        var preparedBook = false
        for task in state.imports where !task.isFinished && task.error == nil && !["staged", "upload_uncertain", "uploading"].contains(task.stage) {
            await advanceImport(task.id, retry: false)
            guard epoch == generation else { return }
        }
        do {
            for (book, job) in state.bookPreparations ?? [:] where ["queued", "running"].contains(job.status) {
                do {
                    let updated = try await api.preparation(book)
                    guard epoch == generation else { return }
                    state.bookPreparations?[book] = updated
                    if updated.status == "succeeded" { preparedBook = true }
                } catch ClientError.http(let code, _) where code == 404 {
                    guard epoch == generation else { return }
                    state.bookPreparations?.removeValue(forKey: book); try await persist()
                }
            }
            for (key, job) in state.courseJobs where job.isPending && state.courseConflicts?[key] == nil {
                do {
                    let updated = try await api.pollCourse(job)
                    guard epoch == generation else { return }
                    guard state.courseJobs[key]?.job_id == job.job_id else { continue }
                    state.courseJobs[key] = updated
                    if let course = updated.course, updated.status == "succeeded" {
                        state.courses[key] = course
                        if state.courseValidation == nil { state.courseValidation = [:] }; state.courseValidation?[key] = "verified"
                    }
                } catch ClientError.http(let code, let message) where code == 409 {
                    guard epoch == generation else { return }
                    guard state.courseJobs[key]?.job_id == job.job_id else { continue }
                    if state.courseConflicts == nil { state.courseConflicts = [:] }
                    state.courseConflicts?[key] = message
                    if state.courseValidation == nil { state.courseValidation = [:] }; state.courseValidation?[key] = "invalid"
                }
            }
            if preparedBook || state.imports.filter(\.isFinished).count != oldReady {
                let refreshed = try await api.books(); guard epoch == generation else { return }; books = refreshed; state.cachedBooks = refreshed
            }
            try await persist()
            pollFailures = 0; nextPoll = .distantPast
        } catch {
            if epoch == generation {
                self.error = error.localizedDescription
                pollFailures = min(5, pollFailures + 1); nextPoll = Date().addingTimeInterval(min(30, pow(2, Double(pollFailures))))
            }
        }
    }
    /// Owned books may need fresh chapter/diagnostic preparation without another PDF upload or OCR pass.
    func refreshExistingPreparation(_ book: String) async {
        guard let api else { return }; let epoch = generation
        do {
            let job = try await api.preparation(book); guard epoch == generation else { return }
            if state.bookPreparations == nil { state.bookPreparations = [:] }; state.bookPreparations?[book] = job
            try await persist()
        } catch ClientError.http(let code, _) where code == 404 {
            // No task yet. A read must never create one.
            guard epoch == generation else { return }
            state.bookPreparations?.removeValue(forKey: book)
            do { try await persist() } catch { self.error = error.localizedDescription }
        } catch { if epoch == generation { self.error = error.localizedDescription } }
    }
    func prepareExistingBook(_ book: String, retry: Bool = false) async {
        await perform {
            guard let api = self.api else { throw ClientError.configuration("请先连接学习服务") }
            let job = try await api.prepare(book, retry: retry)
            if self.state.bookPreparations == nil { self.state.bookPreparations = [:] }; self.state.bookPreparations?[book] = job
            try await self.persist()
            if job.status == "succeeded" { self.books = try await api.books(); self.state.cachedBooks = self.books; try await self.persist() }
        }
    }
    func bookStructure(_ book: String) async throws -> BookStructure {
        guard let api else { throw ClientError.configuration("请先连接学习服务") }
        let epoch = generation; let structure = try await api.structure(book)
        guard epoch == generation else { throw CancellationError() }
        if state.bookStructures == nil { state.bookStructures = [:] }; state.bookStructures?[book] = structure
        try await persist(); return structure
    }
    /// Pure cache validation: never starts generation, including on 404/409 or a lost connection.
    func validateCachedCourse(book: String, chapter: String, allowMissing: Bool = false) async {
        guard let api, let session = state.sessions[book] else { return }
        let key = StoredState.courseKey(session: session, chapter: chapter)
        guard (state.courses[key] != nil || allowMissing), validatingCourses[key] == nil else { return }
        let courseID = state.courses[key]?.course_id, version = state.courses[key]?.version
        let requestID = state.courseRequestIDs?[key], jobID = state.courseJobs[key]?.job_id
        let pending = state.courseSubmissionPending?[key] == true
        let epoch = generation
        func stillCurrent() -> Bool {
            generation == epoch && state.courses[key]?.course_id == courseID && state.courses[key]?.version == version
                && state.courseRequestIDs?[key] == requestID && state.courseJobs[key]?.job_id == jobID
                && (state.courseSubmissionPending?[key] == true) == pending
        }
        validatingCourses[key] = epoch
        defer { if validatingCourses[key] == epoch { validatingCourses.removeValue(forKey: key) } }
        if state.courseValidation == nil { state.courseValidation = [:] }
        if state.courseValidation?[key] != "invalid" { state.courseValidation?[key] = "unverified" }
        courseValidationMessages.removeValue(forKey: key)
        do {
            let course = try await api.cachedCourse(session, chapter: chapter); guard stillCurrent() else { return }
            state.courses[key] = course; state.courseValidation?[key] = "verified"
            state.courseConflicts?.removeValue(forKey: key)
        } catch ClientError.http(let code, let message) where code == 404 || code == 409 {
            guard stillCurrent() else { return }
            state.courseValidation?[key] = "invalid"
            if state.courseConflicts == nil { state.courseConflicts = [:] }
            state.courseConflicts?[key] = code == 404 ? "服务端已无当前可用课程，请明确重新准备。" : message
        } catch {
            guard stillCurrent() else { return }
            courseValidationMessages[key] = (state.courses[key] == nil ? "当前课程尚未通过联网核验，暂不展示或提交评分：" : "当前课程未通过联网核验，仅显示本机历史副本：") + error.localizedDescription
        }
        do { try await persist() } catch { self.error = error.localizedDescription }
    }
    func openInterview(book: Book) async {
        await perform {
            guard let api = self.api, let account = self.account else { throw ClientError.configuration("请先连接学习服务") }
            let interview: Interview
            if let session = self.state.sessions[book.book_id] { interview = try await api.resume(session) }
            else { interview = try await api.start(book: book.book_id, user: account.user_id) }
            self.accept(interview, book: book.book_id); try await self.persist()
        }
    }
    private func accept(_ interview: Interview, book: String) {
        interviews[book] = interview; state.sessions[book] = interview.session_id
    }
    func answerInterview(book: String, text: String, selected: [String], confidence: Double, seconds: Double) async {
        await perform {
            guard let api = self.api, let current = self.interviews[book] else { return }
            let turn = current.turn
            let labels = turn.options.filter { selected.contains($0.id) }.map(\.label)
            let answer = text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? labels.joined(separator: "、") : text
            let next: Interview
            do {
                if turn.phase == "profile_confirmation" {
                    next = try await api.confirm(current.session_id, confirmed: !selected.contains("edit"))
                } else if turn.phase == "adaptive_diagnosis" && turn.item == nil {
                    next = try await api.next(current.session_id)
                } else if let item = turn.item {
                    let reply = try await api.respond(current.session_id, answer: Answer(itemID: item.item_id, answer: answer.isEmpty ? "不确定" : answer, selected: selected, confidence: confidence, seconds: seconds))
                    next = Interview(sessionID: current.session_id, turn: reply.next_turn)
                } else { next = try await api.profile(current.session_id, answer: answer.isEmpty ? "不确定" : answer, selected: selected) }
            } catch {
                // Read after an uncertain write; never replay an old answer into the next question.
                if let restored = try? await api.resume(current.session_id), restored.turn.turn_id != turn.turn_id {
                    self.accept(restored, book: book); try await self.persist()
                    self.notice = "上次回答已由服务器处理，当前步骤已同步。"
                    return
                }
                throw error
            }
            self.accept(next, book: book); try await self.persist()
        }
    }
    /// Reads a known task only. A job payload never makes its embedded course current.
    func checkCourseTask(book: String, chapter: String) async {
        await perform {
            guard let api = self.api, let session = self.state.sessions[book] else { return }
            let key = StoredState.courseKey(session: session, chapter: chapter)
            guard let job = self.state.courseJobs[key] else { return }
            let epoch = self.generation
            let requestID = self.state.courseRequestIDs?[key]
            let pending = self.state.courseSubmissionPending?[key] == true
            let updated = try await api.pollCourse(job)
            guard self.generation == epoch, self.state.sessions[book] == session,
                  self.state.courseJobs[key]?.job_id == job.job_id,
                  self.state.courseRequestIDs?[key] == requestID,
                  (self.state.courseSubmissionPending?[key] == true) == pending else { return }
            guard updated.job_id == job.job_id, updated.session_id == session, updated.chapter_id == chapter else {
                throw ClientError.configuration("返回的任务与当前章节不匹配，原任务已保留。")
            }
            self.state.courseJobs[key] = updated
            try await self.persist()
            if updated.status == "succeeded" {
                // Reuse current-publication GET and its epoch/request/course guards.
                await self.validateCachedCourse(book: book, chapter: chapter, allowMissing: true)
            }
        }
    }

    func requestCourse(book: String, chapter: String, retry: Bool = false, newRequest: Bool = false) async {
        await perform {
            guard let api = self.api, let session = self.state.sessions[book] else { throw ClientError.configuration("请先完成学习诊断") }
            let key = StoredState.courseKey(session: session, chapter: chapter)
            let uncertain = self.state.courseSubmissionPending?[key] == true
            if uncertain && newRequest { throw ClientError.configuration("上次准备请求的回执尚未确认，请先重试同一请求。") }
            if self.state.courseConflicts?[key] != nil && !newRequest && !uncertain { throw ClientError.configuration("请先核对教材和诊断状态，再点“按当前状态重新准备”。") }
            if !retry && !newRequest && !uncertain && self.state.courseJobs[key] == nil {
                do {
                    let cached = try await api.cachedCourse(session, chapter: chapter)
                    self.state.courses[key] = cached
                    if self.state.courseValidation == nil { self.state.courseValidation = [:] }; self.state.courseValidation?[key] = "verified"
                    try await self.persist(); return
                } catch ClientError.http(let code, _) where code == 404 {
                    // A missing cache alone permits the explicitly requested job POST.
                } catch ClientError.http(let code, let message) where code == 409 {
                    if self.state.courseConflicts == nil { self.state.courseConflicts = [:] }; self.state.courseConflicts?[key] = message
                    if self.state.courseValidation == nil { self.state.courseValidation = [:] }; self.state.courseValidation?[key] = "invalid"
                    try await self.persist(); throw ClientError.http(code, message)
                }
            }
            if self.state.courseRequestIDs == nil { self.state.courseRequestIDs = [:] }
            if self.state.courseRequestIDs?[key] == nil || newRequest {
                self.state.courseRequestIDs?[key] = UUID().uuidString
                self.state.courseConflicts?.removeValue(forKey: key)
                if newRequest { self.state.courseJobs.removeValue(forKey: key) }
            }
            if self.state.courseSubmissionPending == nil { self.state.courseSubmissionPending = [:] }
            self.state.courseSubmissionPending?[key] = true
            try await self.persist() // Commit the id before a request that may lose its response.
            let job: CourseJob
            do { job = try await api.courseJob(session, chapter: chapter, retry: retry, requestID: self.state.courseRequestIDs?[key]) }
            catch ClientError.http(let code, let message) where code == 409 {
                if self.state.courseConflicts == nil { self.state.courseConflicts = [:] }; self.state.courseConflicts?[key] = message
                if self.state.courseValidation == nil { self.state.courseValidation = [:] }; self.state.courseValidation?[key] = "invalid"
                self.state.courseSubmissionPending?.removeValue(forKey: key)
                try await self.persist(); throw ClientError.http(code, message)
            }
            self.state.courseJobs[key] = job
            self.state.courseSubmissionPending?.removeValue(forKey: key)
            self.state.courseConflicts?.removeValue(forKey: key)
            if let course = job.course, job.status == "succeeded" {
                self.state.courses[key] = course
                if self.state.courseValidation == nil { self.state.courseValidation = [:] }; self.state.courseValidation?[key] = "verified"
            }
            try await self.persist()
        }
    }
    func key(book: String, chapter: String) -> String? {
        guard let session = state.sessions[book] else { return nil }; return StoredState.courseKey(session: session, chapter: chapter)
    }
    func createDraft(book: String) async -> String? {
        let draft = NoteDraft(bookID: book); state.notes.insert(draft, at: 0)
        do { try await persist(); return draft.id } catch { self.error = error.localizedDescription; return nil }
    }
    func saveDraft(_ draft: NoteDraft) async {
        guard let index = state.notes.firstIndex(where: { $0.id == draft.id }) else { return }
        state.notes[index] = draft
        do { try await persist() } catch { self.error = error.localizedDescription }
    }
    func openVoiceNote(_ note: Note) async throws -> String {
        guard note.input_mode == "voice", note.audio_ready == true else { throw ClientError.configuration("这篇笔记没有可核对的录音。") }
        // A new local recording may still refer to the old remote note ID.
        // Never relabel that unuploaded audio as this server version.
        if let index = state.notes.firstIndex(where: {
            $0.uploadComplete && $0.remoteNote?.id == note.id && $0.remoteNote?.revision == note.revision
        }) {
            state.notes[index].remoteNote = note; state.notes[index].uploadComplete = true
            try await persist(); return state.notes[index].id
        }
        var draft = NoteDraft(bookID: note.book_id)
        draft.title = note.title; draft.remoteNote = note; draft.uploadComplete = true
        state.notes.insert(draft, at: 0); try await persist(); return draft.id
    }
    func beginVoiceTranscriptReview(draftID: String, job: StudioJob) async throws {
        guard let index = state.notes.firstIndex(where: { $0.id == draftID }) else { throw ClientError.configuration("本机笔记不存在。") }
        let previous = state.notes[index]
        try state.notes[index].beginVoiceTranscriptReview(job)
        do { try await persist() } catch { state.notes[index] = previous; throw error }
    }
    func updateVoiceTranscriptReview(draftID: String, text: String, confirmed: Bool) async throws {
        guard let index = state.notes.firstIndex(where: { $0.id == draftID }) else { throw ClientError.configuration("本机笔记不存在。") }
        let previous = state.notes[index]
        try state.notes[index].updateVoiceTranscriptReview(text: text, confirmed: confirmed)
        do { try await persist() } catch { state.notes[index] = previous; throw error }
    }
    /// No POST occurs until the exact intent and confirmation snapshot are durable.
    func submitVoiceAnalysis(draftID: String, action: String, retry: Bool = false) async throws -> StudioJob {
        guard !submittingVoiceNotes.contains(draftID), let api,
              let index = state.notes.firstIndex(where: { $0.id == draftID }),
              let note = state.notes[index].remoteNote else { throw ClientError.configuration("请等待当前提交完成并确认录音已上传。") }
        submittingVoiceNotes.insert(draftID); defer { submittingVoiceNotes.remove(draftID) }
        let epoch = generation
        let submission = try state.notes[index].prepareVoiceAnalysis(action: action, retry: retry)
        try await persist() // A write failure must stop before inference submission.
        guard epoch == generation,
              state.notes.first(where: { $0.id == draftID })?.voiceContext == submission.context,
              state.notes.first(where: { $0.id == draftID })?.analysisSubmission?.requestID == submission.requestID else { throw CancellationError() }
        let job = try await api.analyze(note: note, transcript: "", requestID: submission.requestID,
            action: submission.action, confirmedVoiceTranscript: submission.confirmedTranscript)
        guard epoch == generation, let current = state.notes.firstIndex(where: { $0.id == draftID }),
              state.notes[current].voiceContext == submission.context,
              state.notes[current].analysisSubmission?.requestID == submission.requestID else { throw CancellationError() }
        try state.notes[current].acknowledgeVoiceAnalysis(job, requestID: submission.requestID)
        try await persist()
        return job
    }
    /// Re-entering or foreground polling is GET-only, including unknown POSTs.
    func refreshVoiceJobs(draftID: String) async throws -> [StudioJob] {
        guard let api, let index = state.notes.firstIndex(where: { $0.id == draftID }),
              let context = state.notes[index].voiceContext else { return [] }
        let epoch = generation; let book = state.notes[index].bookID
        state.notes[index].restoreLegacyVoiceSubmission()
        let trackedID = state.notes[index].analysisSubmission?.jobID
        var fetched = try await api.studioJobs(book: book)
        if let trackedID, !fetched.contains(where: { $0.id == trackedID }) {
            fetched.insert(try await api.studioJob(trackedID), at: 0)
        }
        guard epoch == generation, let current = state.notes.firstIndex(where: { $0.id == draftID }),
              state.notes[current].voiceContext == context else { throw CancellationError() }
        let jobs = fetched.filter { $0.note_id == context.noteID && $0.revision == context.revision }
        if let submission = state.notes[current].analysisSubmission,
           let job = jobs.first(where: { $0.id == submission.jobID }) {
            try state.notes[current].acknowledgeVoiceAnalysis(job, requestID: submission.requestID)
        }
        try await persist()
        return jobs
    }
    func cacheAudio(note: Note) async throws -> URL {
        guard let api, let disk else { throw ClientError.configuration("请先连接学习服务") }
        let epoch = generation
        let data = try await api.downloadAudio(note: note)
        guard epoch == generation else { throw CancellationError() }
        let url = try await disk.file("audio-" + UUID().uuidString + ".m4a")
        guard epoch == generation else { throw CancellationError() }
        try data.write(to: url, options: .atomic); return url
    }
    func available(_ feature: String) -> Bool { readiness?.features[feature] != false }
    func sourcePDF(book: String) async throws -> URL {
        guard let api, let disk else { throw ClientError.configuration("请先连接学习服务") }
        let epoch = generation
        if let local = state.imports.first(where: { $0.bookID == book || $0.canonicalBookID == book }) {
            let url = try await disk.file(local.localFile)
            if FileManager.default.fileExists(atPath: url.path) { return url }
        }
        if let cached = state.pdfFiles?[book] {
            let url = try await disk.file(cached)
            if FileManager.default.fileExists(atPath: url.path) { return url }
        }
        let data = try await api.sourcePDF(book)
        guard epoch == generation else { throw CancellationError() }
        let filename = "source-" + UUID().uuidString + ".pdf"; let url = try await disk.file(filename)
        guard epoch == generation else { throw CancellationError() }
        try data.write(to: url, options: .atomic)
        if state.pdfFiles == nil { state.pdfFiles = [:] }; state.pdfFiles?[book] = filename
        try await persist(); return url
    }
}
