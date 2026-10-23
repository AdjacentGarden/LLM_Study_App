import Foundation
import LearningCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private actor StubTransport: HTTPTransport {
    struct Reply: Sendable { var data: Data; var status: Int = 200; var headers: [String: String] = [:] }
    var replies: [Reply]; var requests: [URLRequest] = []
    init(_ values: [Reply]) { replies = values }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        guard !replies.isEmpty else { throw URLError(.networkConnectionLost) }
        let reply = replies.removeFirst()
        return (reply.data, HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: reply.headers)!)
    }
    func allRequests() -> [URLRequest] { requests }
}
private func bytes(_ text: String) -> Data { Data(text.utf8) }
private func temp() throws -> URL {
    let sourceRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let url = sourceRoot.appendingPathComponent(".build/core-test-data/" + UUID().uuidString)
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}
private func client(_ transport: StubTransport, root: URL, address: String = "https://study.example.com") throws -> APIClient {
    APIClient(configuration: try ServerConfiguration(address: address, allowLocalHTTP: address.hasPrefix("http:")), store: try DiskStore(root: root), transport: transport)
}
private let accountJSON = #"{"account":null,"user_id":"visitor_1","email_available":false,"learning_sessions":{"b1":"s1"}}"#
private let prepareJSON = #"{"book_id":"b1","status":"running","stage":"diagnostics","error":"","updated":123.5}"#
private let queuedJSON = #"{"job_id":"j1","session_id":"s1","chapter_id":"ch1","status":"queued","stage":"queued","attempts":1,"error":null,"retryable":false,"created_at":"2026-10-22T01:00:00Z","updated_at":"2026-10-22T01:00:00Z","course":null}"#

func serverConfigurationRestrictsHTTP() throws {
    try expectThrows { try ServerConfiguration(address: "http://10.249.186.201:8100") }
    for address in ["http://example.com", "http://8.8.8.8", "http://10.foo.1.2.3", "https://u:p@example.com", "https://example.com/api", "file:///tmp/a"] {
        try expectThrows { try ServerConfiguration(address: address, allowLocalHTTP: true) }
    }
    for address in ["http://10.249.186.201:8100", "http://192.168.1.2", "http://172.16.1.1", "http://localhost:8100"] {
        try check(try ServerConfiguration(address: address, allowLocalHTTP: true).baseURL.scheme == "http")
    }
    try check(try ServerConfiguration(address: "https://study.example.com/").baseURL.absoluteString == "https://study.example.com")
    try check(try ServerConfiguration(address: "https://a.example.com").namespace != ServerConfiguration(address: "https://b.example.com").namespace)
}
func diskRoundtripAndCorruptionAreExplicit() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try DiskStore(root: root)
    var state = StoredState(); state.sessions["b"] = "s"; state.notes = [NoteDraft(bookID: "b")]
    try await store.save(state, as: "state.json")
    let recovered = try require(await store.load(StoredState.self, from: "state.json"))
    try check(recovered.sessions["b"] == "s"); try check(recovered.notes.first?.bookID == "b")
    try bytes("not json").write(to: root.appendingPathComponent("state.json"))
    try await expectAsyncThrows { try await store.load(StoredState.self, from: "state.json") }
    try await expectAsyncThrows { try await store.file("../cookies.json") }
}
func stagePDFSurvivesOriginalDeletion() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("input.pdf"); try bytes("%PDF-1.7\nfixture").write(to: source)
    let store = try DiskStore(root: root.appendingPathComponent("store")); let task = try await store.stagePDF(from: source)
    try FileManager.default.removeItem(at: source)
    try check(try Data(contentsOf: await store.file(task.localFile)).starts(with: bytes("%PDF-")))
    let invalid = root.appendingPathComponent("bad.pdf"); try bytes("not PDF").write(to: invalid)
    try await expectAsyncThrows { try await store.stagePDF(from: invalid) }
}
func visitorCookieSurvivesRelaunchAndKeepsIdentity() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let first = StubTransport([.init(data: bytes(accountJSON), headers: ["Set-Cookie": "visitor=opaque; Path=/; HttpOnly; Secure"])])
    let api = try client(first, root: root); _ = try await api.account()
    let second = StubTransport([.init(data: bytes(accountJSON))]); let restored = try client(second, root: root)
    try await restored.restoreCookies(); _ = try await restored.account()
    try check(await second.allRequests().first?.value(forHTTPHeaderField: "Cookie") == "visitor=opaque")
    var state = StoredState(); state.sessions["old"] = "session"
    state.identity = "different"; state.acceptAccount(try JSONDecoder().decode(AccountState.self, from: bytes(accountJSON)))
    try check(state.sessions["old"] == nil); try check(state.sessions["b1"] == "s1")
}
func secureCookieNeverTravelsOverLocalHTTP() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let transport = StubTransport([.init(data: bytes(accountJSON), headers: ["Set-Cookie": "visitor=secret; Secure; Path=/"]), .init(data: bytes(accountJSON))])
    let api = try client(transport, root: root, address: "http://10.249.186.201:8100")
    _ = try await api.account(); _ = try await api.account()
    try check(await transport.allRequests().last?.value(forHTTPHeaderField: "Cookie") == nil)
}
func nonSecureVisitorCookieSurvivesHTTPRelaunch() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let first = StubTransport([.init(data: bytes(accountJSON), headers: ["Set-Cookie": "visitor=local-opaque; Path=/; HttpOnly; Max-Age=31536000"])])
    _ = try await client(first, root: root, address: "http://127.0.0.1:8100").account()
    let next = StubTransport([.init(data: bytes(accountJSON))]); let api = try client(next, root: root, address: "http://127.0.0.1:8100")
    try await api.restoreCookies(); _ = try await api.account()
    try check(await next.allRequests().first?.value(forHTTPHeaderField: "Cookie") == "visitor=local-opaque")
}
func endpointContractAndCourseRetry() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let transport = StubTransport([.init(data: bytes(queuedJSON), status: 202), .init(data: bytes(queuedJSON)), .init(data: bytes(prepareJSON))])
    let api = try client(transport, root: root)
    let job = try await api.courseJob("s1", chapter: "ch1", retry: true, requestID: "durable-request")
    _ = try await api.pollCourse(job); _ = try await api.prepare("b1", retry: true)
    let requests = await transport.allRequests()
    try check(requests[0].url?.path == "/api/interviews/s1/course-jobs")
    let body = try require(JSONSerialization.jsonObject(with: requests[0].httpBody!) as? [String: Any])
    try check(body["retry"] as? Bool == true); try check(body["chapter_id"] as? String == "ch1")
    try check(body["request_id"] as? String == "durable-request")
    try check(requests[1].httpMethod == "GET"); try check(requests[1].url?.path == "/api/interviews/s1/course-jobs/j1")
    try check(requests[2].url?.query == "retry=true")
    try check(job.course == nil); try check(job.isPending)
}
func HTTPFailuresAreNotAutomaticallyRetried() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let transport = StubTransport([.init(data: bytes(#"{"detail":"课程尚未就绪"}"#), status: 409)])
    let api = try client(transport, root: root)
    do { _ = try await api.courseJob("s1", chapter: "ch1"); throw CheckFailure(message: "expected HTTP failure") }
    catch { try check(error.localizedDescription.contains("课程尚未就绪")); try check(error.localizedDescription.contains("409")) }
    try check(await transport.allRequests().count == 1)
    try await expectAsyncThrows { try await api.url("https://outside.example/api/books") }
    try await expectAsyncThrows { try await api.url("/api/../secret") }
}
func malformedSuccessIsNotTreatedAsAnEmptyLibrary() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let transport = StubTransport([.init(data: bytes("{}"))]); let api = try client(transport, root: root)
    try await expectAsyncThrows { try await api.books() }
}
func interruptedUploadRequiresExplicitRetry() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try DiskStore(root: root); var task = ImportTask(filename: "x.pdf", localFile: "x.pdf"); task.stage = "uploading"
    try await store.save(task, as: "import-\(task.id).json")
    let transport = StubTransport([]); let workflow = ImportWorkflow(api: try client(transport, root: root), disk: store)
    let recovered = try await workflow.recover(task)
    try check(recovered.stage == "upload_uncertain"); try check(recovered.error != nil)
    let untouched = await workflow.advance(recovered)
    try check(untouched.stage == "upload_uncertain"); try check(await transport.allRequests().isEmpty)
}
func OCRThenPreparationIsPersistedWithoutReprocessing() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let transport = StubTransport([
        .init(data: bytes(#"{"book_id":"b1","status":"ocr_ready","progress":1,"current_step":"ready","retryable":false}"#)),
        .init(data: bytes(prepareJSON)), .init(data: bytes(prepareJSON))
    ])
    let store = try DiskStore(root: root); let workflow = ImportWorkflow(api: try client(transport, root: root), disk: store)
    var task = ImportTask(filename: "x.pdf", localFile: "x.pdf"); task.stage = "ocr"; task.bookID = "b1"
    let next = await workflow.advance(task); try check(next.stage == "preparing")
    let recovered = try await workflow.recover(task); try check(recovered.stage == "preparing")
    _ = await workflow.advance(recovered)
    try check(await transport.allRequests().map { $0.url!.path } == ["/api/books/b1/status", "/api/books/b1/prepare", "/api/books/b1/prepare"])
}
func practiceClampsDurationAndKeepsEventID() throws {
    let answer = Answer(eventID: "stable-event", answer: "答案", selected: ["A"], confidence: 2, seconds: 9999)
    let object = try require(JSONSerialization.jsonObject(with: JSONEncoder().encode(answer)) as? [String: Any])
    try check(object["event_id"] as? String == "stable-event"); try check(object["confidence"] as? Double == 1)
    try check(object["response_seconds"] as? Double == 3600)
}
func multipartStreamsStagedFileAndPreservesFilename() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let file = root.appendingPathComponent("input.pdf"); let content = bytes("%PDF-1.7\nPDF fixture bytes"); try content.write(to: file)
    let transport = StubTransport([.init(data: bytes(#"{"book_id":"b1","filename":"生物.pdf","status":"uploaded"}"#))])
    let api = try client(transport, root: root); let response = try await api.uploadPDF(file: file, filename: "生物.pdf")
    let request = try require(await transport.allRequests().first)
    let body = try require(request.httpBody); let text = String(decoding: body, as: UTF8.self)
    try check(text.contains("filename=\"生物.pdf\"")); try check(body.range(of: content) != nil)
    try check(request.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=CloudPath-") == true)
    try check(response.book_id == "b1")
    try check(FileManager.default.contentsOfDirectory(atPath: root.path).filter { $0.hasPrefix("multipart-") }.isEmpty)
}
func voiceListAndStrictSaveContract() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let noteJSON = #"{"id":"note_12345678","book_id":"b1","revision":1,"title":"录音","input_mode":"voice","audio_ready":false,"audio_duration_seconds":0}"#
    let transport = StubTransport([.init(data: bytes("{\"items\":[" + noteJSON + "]}")), .init(data: bytes(noteJSON)), .init(data: bytes(noteJSON))])
    let api = try client(transport, root: root); let notes = try await api.notes(book: "b1")
    try check(notes.first?.strokes == nil)
    let fresh = Note(id: "note_12345678", bookID: "b1", title: "录音")
    let saved = try await api.saveNote(fresh)
    _ = try await api.uploadAudio(note: saved, data: bytes("m4a-fixture"), seconds: 2.5)
    let requests = await transport.allRequests()
    let body = try require(JSONSerialization.jsonObject(with: requests[1].httpBody!) as? [String: Any])
    try check(body["revision"] as? Int == 0); try check(body["edition"] == nil)
    try check(body["chapter_id"] as? String == ""); try check((body["strokes"] as? [Any])?.isEmpty == true)
    try check(requests[2].httpMethod == "PUT"); try check(requests[2].value(forHTTPHeaderField: "X-Note-Revision") == "1")
    try check(requests[2].value(forHTTPHeaderField: "Content-Type") == "audio/mp4")
}
func pendingAnswerAndReviewRecoverWithSameEvents() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let disk = try DiskStore(root: root); var state = StoredState()
    state.pendingAnswers = ["s:c:i": Answer(eventID: "practice-stable", answer: "选择第二项", selected: ["1"], confidence: 0.5, seconds: 4)]
    state.pendingReviews = ["s:c:f": ReviewSubmission(eventID: "review-stable", rating: "good", seconds: 3)]
    try await disk.save(state, as: "state.json")
    let restored = try require(await disk.load(StoredState.self, from: "state.json"))
    try check(restored.pendingAnswers?["s:c:i"]?.event_id == "practice-stable")
    try check(restored.pendingAnswers?["s:c:i"]?.selected_option_ids == ["1"])
    try check(restored.pendingReviews?["s:c:f"]?.eventID == "review-stable")
}
func reviewedCourseAndStudyUnitsRecoverWithoutPrivateAnswers() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let courseJSON = #"{"course_id":"course1","version":2,"chapter_id":"ch1","chapter_title":"测试章节","opening":"导学","summary":"总结","original_reading":[],"knowledge_points":[],"flashcards":[],"worked_examples":[],"checkpoint_questions":[],"practice_items":[{"item_id":"p1","point_id":"k1","prompt":"解释这个概念","response_type":"explanation","options":[],"estimated_seconds":60}],"estimated_minutes":25,"created_at":"2026-10-22T01:00:00Z","unresolved_source_warnings":[],"study_units":[{"unit_id":"u1","title":"第一单元","point_ids":["k1"],"estimated_minutes":10}]}"#
    let course = try JSONDecoder().decode(Course.self, from: bytes(courseJSON))
    var state = StoredState(); state.courses["s1:ch1"] = course
    let disk = try DiskStore(root: root); try await disk.save(state, as: "state.json")
    let restored = try require(await disk.load(StoredState.self, from: "state.json"))
    try check(restored.courses["s1:ch1"]?.study_units?.first?.point_ids == ["k1"])
    let encoded = String(decoding: try JSONEncoder().encode(course), as: UTF8.self)
    try check(!encoded.contains("expected_answer")); try check(!encoded.contains("correct_option_ids"))
}
func voiceAnalysisKeepsLongDraftIndependentOfTranscript() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let job = #"{"id":"job1","book_id":"b1","kind":"note","status":"queued","result":{},"error":"","note_id":"note_12345678","revision":1}"#
    let transport = StubTransport([.init(data: bytes(job)), .init(data: bytes(job))]); let api = try client(transport, root: root)
    let note = try JSONDecoder().decode(Note.self, from: bytes(#"{"id":"note_12345678","book_id":"b1","revision":1,"title":"Voice","input_mode":"voice","audio_ready":true,"audio_duration_seconds":5}"#))
    let disk = try DiskStore(root: root)
    for (length, action) in [(12001, "recognize"), (20000, "complete")] {
        var draft = NoteDraft(bookID: "b1"); draft.text = String(repeating: "文", count: length); draft.remoteNote = note; draft.analysisRequestID = "stable-" + action
        try await disk.save(draft, as: "draft.json")
        let result = try await api.analyze(note: note, transcript: draft.text, requestID: draft.analysisRequestID!, action: action)
        let request = try require(await transport.allRequests().last)
        let body = try require(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any])
        try check(body["transcript"] as? String == ""); try check(body["request_id"] as? String == draft.analysisRequestID)
        try check(body["revision"] as? Int == 1); try check(result.revision == 1)
        let restored = try require(await disk.load(NoteDraft.self, from: "draft.json"))
        try check(restored.text.count == length && restored.text == draft.text)
    }
}

private func voiceFixtureNote() -> Note {
    var note = Note(id: "note_voice_review", bookID: "b1", title: "语音核对", revision: 3)
    note.audio_ready = true; note.audio_duration_seconds = 8
    return note
}
private func voiceFixtureDraft() -> NoteDraft {
    var draft = NoteDraft(bookID: "b1")
    draft.recordingID = "recording-current"; draft.remoteNote = voiceFixtureNote(); draft.uploadComplete = true
    draft.text = String(repeating: "独立笔记", count: 5000)
    return draft
}
private func voiceFixtureJob(id: String = "recognized-current", kind: String = "recognize", status: String = "succeeded", noteID: String = "note_voice_review", revision: Int? = 3, transcript: String? = "原始转写内容") throws -> StudioJob {
    var value: [String: Any] = ["id": id, "book_id": "b1", "kind": kind, "status": status,
                              "note_id": noteID, "error": "", "result": [:]]
    if let revision { value["revision"] = revision }
    if let transcript { value["result"] = ["transcript": transcript] }
    return try JSONDecoder().decode(StudioJob.self, from: JSONSerialization.data(withJSONObject: value))
}
private func requestBody(_ request: URLRequest) throws -> [String: Any] {
    try require(JSONSerialization.jsonObject(with: require(request.httpBody)) as? [String: Any])
}

func voiceTranscriptReviewRequiresExplicitConfirmationAndCurrentVersion() throws {
    var draft = voiceFixtureDraft(); let ordinaryText = draft.text
    for job in [
        try voiceFixtureJob(noteID: "another-note"), try voiceFixtureJob(revision: 2),
        try voiceFixtureJob(revision: nil), try voiceFixtureJob(kind: "image"),
        try voiceFixtureJob(status: "queued"), try voiceFixtureJob(transcript: nil),
    ] {
        var fresh = draft
        try expectThrows { try fresh.beginVoiceTranscriptReview(job) }
        try check(fresh.transcriptReview == nil && fresh.analysisSubmission == nil)
    }
    try draft.beginVoiceTranscriptReview(voiceFixtureJob())
    try check(draft.transcriptReview?.sourceJobID == "recognized-current")
    try check(draft.transcriptReview?.confirmed == false && draft.text == ordinaryText)
    try expectThrows { try draft.prepareVoiceAnalysis(action: "improve") }
    let corrected = "人工校正后的转写，保留原意。"
    try draft.updateVoiceTranscriptReview(text: corrected, confirmed: true)
    try check(draft.transcriptReview?.text == corrected && draft.transcriptReview?.confirmed == false)
    try expectThrows { try draft.prepareVoiceAnalysis(action: "improve") }
    try draft.updateVoiceTranscriptReview(text: corrected, confirmed: true)
    try check(draft.transcriptReview?.confirmed == true)
    var stale = draft; stale.remoteNote?.revision = 4
    try expectThrows { try stale.prepareVoiceAnalysis(action: "improve") }
    stale = draft; stale.recordingID = "recording-new"
    try expectThrows { try stale.prepareVoiceAnalysis(action: "improve") }
    let intent = try draft.prepareVoiceAnalysis(action: "improve")
    try check(intent.confirmedTranscript == corrected && intent.context.revision == 3)
    try check(intent.context.recordingID == "recording-current" && draft.voiceAnalysisLocked)
    try expectThrows { try draft.updateVoiceTranscriptReview(text: "新的校正", confirmed: true) }
    try expectThrows { try draft.beginVoiceTranscriptReview(voiceFixtureJob(id: "late-result")) }
    try expectThrows { try draft.prepareVoiceAnalysis(action: "complete") }
    try check(draft.analysisSubmission?.requestID == intent.requestID && draft.text == ordinaryText)
}

func voiceImproveWireUsesOnlyExplicitConfirmedTextAndChecksScalarLimits() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let job = try voiceFixtureJob(id: "improved-current", kind: "improve", status: "queued", transcript: nil)
    let transport = StubTransport([.init(data: try JSONEncoder().encode(job)), .init(data: try JSONEncoder().encode(job))])
    let api = try client(transport, root: root); let note = voiceFixtureNote()
    let ordinaryText = String(repeating: "草稿", count: 10000)
    let corrected = "  人工核对的转写。\n保留标点和首尾空白。  "
    _ = try await api.analyze(note: note, transcript: ordinaryText, requestID: "improve-explicit", action: "improve", confirmedVoiceTranscript: corrected)
    let first = try requestBody(require(await transport.allRequests().first))
    try check(first["transcript"] as? String == corrected)
    try check(first["action"] as? String == "improve" && first["request_id"] as? String == "improve-explicit")
    try check(first["revision"] as? Int == 3 && first["consent"] as? Bool == true)
    try check(Set(first.keys) == ["revision", "action", "transcript", "request_id", "consent"])
    let overScalarLimit = String(repeating: "e\u{301}", count: 6001)
    try check(overScalarLimit.count < 12000 && overScalarLimit.unicodeScalars.count > 12000)
    for invalid: String? in [nil, "", " \n ", "字", "还有[待确认]的内容", String(repeating: "字", count: 12001), overScalarLimit] {
        try await expectAsyncThrows {
            try await api.analyze(note: note, transcript: ordinaryText, requestID: "invalid-confirmation", action: "improve", confirmedVoiceTranscript: invalid)
        }
    }
    try check(await transport.allRequests().count == 1)
    let atScalarLimit = String(repeating: "e\u{301}", count: 6000)
    try check(atScalarLimit.unicodeScalars.count == 12000)
    _ = try await api.analyze(note: note, transcript: ordinaryText, requestID: "improve-limit", action: "improve", confirmedVoiceTranscript: atScalarLimit)
    let second = try requestBody(require(await transport.allRequests().last))
    let sent = await transport.allRequests()
    try check(second["transcript"] as? String == atScalarLimit && sent.count == 2)
}

func voiceLostAcknowledgementRestoresFrozenSubmissionWithoutChangingDraft() async throws {
    let root = try temp(); defer { try? FileManager.default.removeItem(at: root) }
    let disk = try DiskStore(root: root); var draft = voiceFixtureDraft()
    try draft.beginVoiceTranscriptReview(voiceFixtureJob(transcript: "已经人工核对的独立转写。"))
    try draft.updateVoiceTranscriptReview(text: "已经人工核对的独立转写。", confirmed: true)
    let intent = try draft.prepareVoiceAnalysis(action: "improve")
    var state = StoredState(); state.notes = [draft]
    try await disk.save(state, as: "state.json") // The immutable ID/body precedes the uncertain POST.
    let lost = StubTransport([]); let firstAPI = try client(lost, root: root)
    try await expectAsyncThrows {
        try await firstAPI.analyze(note: require(draft.remoteNote), transcript: draft.text, requestID: intent.requestID,
                                   action: intent.action, confirmedVoiceTranscript: intent.confirmedTranscript)
    }
    let firstBody = try requestBody(require(await lost.allRequests().first))
    state = try require(await disk.load(StoredState.self, from: "state.json"))
    var restored = try require(state.notes.first)
    try check(restored.voiceAnalysisLocked && restored.analysisSubmission?.isUnacknowledged == true)
    try check(restored.transcriptReview?.confirmed == true && restored.recordingID == draft.recordingID)
    restored.text = String(repeating: "新笔记", count: 6000)
    try expectThrows { try restored.updateVoiceTranscriptReview(text: "不能替换未知提交的文本", confirmed: true) }
    let resumed = try restored.prepareVoiceAnalysis(action: "improve")
    try check(resumed.requestID == intent.requestID && resumed.context == intent.context)
    try check(resumed.confirmedTranscript == intent.confirmedTranscript)
    let job = try voiceFixtureJob(id: "improved-current", kind: "improve", status: "queued", transcript: nil)
    let retryTransport = StubTransport([.init(data: try JSONEncoder().encode(job))])
    let retryAPI = try client(retryTransport, root: root)
    try check(await retryTransport.allRequests().isEmpty) // Recovery itself never posts.
    let acknowledged = try await retryAPI.analyze(note: require(restored.remoteNote), transcript: restored.text,
        requestID: resumed.requestID, action: resumed.action, confirmedVoiceTranscript: resumed.confirmedTranscript)
    let retryBody = try requestBody(require(await retryTransport.allRequests().first))
    try check(NSDictionary(dictionary: firstBody).isEqual(to: retryBody))
    for wrong in [try voiceFixtureJob(kind: "recognize", status: "queued"),
                  try voiceFixtureJob(kind: "improve", status: "queued", noteID: "another-note"),
                  try voiceFixtureJob(kind: "improve", status: "queued", revision: 2),
                  try voiceFixtureJob(kind: "improve", status: "queued", revision: nil)] {
        try expectThrows { try restored.acknowledgeVoiceAnalysis(wrong, requestID: resumed.requestID) }
    }
    try expectThrows { try restored.acknowledgeVoiceAnalysis(acknowledged, requestID: "another-request") }
    try check(restored.analysisSubmission?.jobID == nil)
    try restored.acknowledgeVoiceAnalysis(acknowledged, requestID: resumed.requestID)
    try check(restored.analysisSubmission?.jobID == acknowledged.id && restored.voiceAnalysisLocked)
    try restored.acknowledgeVoiceAnalysis(voiceFixtureJob(id: acknowledged.id, kind: "improve"), requestID: resumed.requestID)
    try check(!restored.voiceAnalysisLocked && restored.text.count == 18000)
    try check(restored.transcriptReview?.text == intent.confirmedTranscript && restored.recordingID == "recording-current")
    let duplicate = try restored.prepareVoiceAnalysis(action: "improve")
    try check(duplicate.requestID == resumed.requestID)
    state.notes = [restored]; try await disk.save(state, as: "state.json")
    let final = try require(await disk.load(StoredState.self, from: "state.json")).notes[0]
    try check(final.text == restored.text && final.analysisSubmission?.requestID == resumed.requestID)
}

func legacyVoiceDraftRestoresAudioIntentWithoutImpliedTranscriptConfirmation() throws {
    let note = try require(JSONSerialization.jsonObject(with: JSONEncoder().encode(voiceFixtureNote())) as? [String: Any])
    let old: [String: Any] = ["id": "legacy-draft", "bookID": "b1", "title": "旧草稿", "text": "独立文字草稿不能充当已确认转写",
        "recordingID": "recording-current", "remoteNote": note, "analysisRequestID": "legacy-complete-request",
        "analysisAction": "complete", "uploadComplete": true]
    var draft = try JSONDecoder().decode(NoteDraft.self, from: JSONSerialization.data(withJSONObject: old))
    try check(draft.transcriptReview == nil && draft.analysisSubmission == nil)
    draft.restoreLegacyVoiceSubmission()
    let submission = try draft.prepareVoiceAnalysis(action: "complete")
    try check(submission.requestID == "legacy-complete-request" && submission.confirmedTranscript == nil)
    try check(submission.context.recordingID == "recording-current" && draft.voiceAnalysisLocked)
    try expectThrows { try draft.prepareVoiceAnalysis(action: "improve") }
    try check(draft.transcriptReview == nil && draft.text == old["text"] as? String)
}

private struct CheckFailure: Error { var message: String }
private func check(_ condition: Bool, file: StaticString = #fileID, line: UInt = #line) throws {
    if !condition { throw CheckFailure(message: "Assertion failed at \(file):\(line)") }
}
private func require<T>(_ value: T?) throws -> T {
    guard let value else { throw CheckFailure(message: "Required value missing") }; return value
}
private func expectThrows<T>(_ action: () throws -> T) throws {
    do { _ = try action() } catch { return }; throw CheckFailure(message: "Expected error")
}
private func expectAsyncThrows<T>(_ action: () async throws -> T) async throws {
    do { _ = try await action() } catch { return }; throw CheckFailure(message: "Expected async error")
}
@main struct CoreChecks {
    static func main() async throws {
        let checks: [(String, () async throws -> Void)] = [
            ("serverConfigurationRestrictsHTTP", { try serverConfigurationRestrictsHTTP() }),
            ("diskRoundtripAndCorruptionAreExplicit", diskRoundtripAndCorruptionAreExplicit),
            ("stagePDFSurvivesOriginalDeletion", stagePDFSurvivesOriginalDeletion),
            ("visitorCookieSurvivesRelaunchAndKeepsIdentity", visitorCookieSurvivesRelaunchAndKeepsIdentity),
            ("secureCookieNeverTravelsOverLocalHTTP", secureCookieNeverTravelsOverLocalHTTP),
            ("nonSecureVisitorCookieSurvivesHTTPRelaunch", nonSecureVisitorCookieSurvivesHTTPRelaunch),
            ("endpointContractAndCourseRetry", endpointContractAndCourseRetry),
            ("HTTPFailuresAreNotAutomaticallyRetried", HTTPFailuresAreNotAutomaticallyRetried),
            ("malformedSuccessIsNotTreatedAsAnEmptyLibrary", malformedSuccessIsNotTreatedAsAnEmptyLibrary),
            ("interruptedUploadRequiresExplicitRetry", interruptedUploadRequiresExplicitRetry),
            ("OCRThenPreparationIsPersistedWithoutReprocessing", OCRThenPreparationIsPersistedWithoutReprocessing),
            ("practiceClampsDurationAndKeepsEventID", { try practiceClampsDurationAndKeepsEventID() })
            ,("multipartStreamsStagedFileAndPreservesFilename", multipartStreamsStagedFileAndPreservesFilename)
            ,("voiceListAndStrictSaveContract", voiceListAndStrictSaveContract)
            ,("pendingAnswerAndReviewRecoverWithSameEvents", pendingAnswerAndReviewRecoverWithSameEvents)
            ,("reviewedCourseAndStudyUnitsRecoverWithoutPrivateAnswers", reviewedCourseAndStudyUnitsRecoverWithoutPrivateAnswers)
            ,("voiceAnalysisKeepsLongDraftIndependentOfTranscript", voiceAnalysisKeepsLongDraftIndependentOfTranscript)
            ,("voiceTranscriptReviewRequiresExplicitConfirmationAndCurrentVersion", { try voiceTranscriptReviewRequiresExplicitConfirmationAndCurrentVersion() })
            ,("voiceImproveWireUsesOnlyExplicitConfirmedTextAndChecksScalarLimits", voiceImproveWireUsesOnlyExplicitConfirmedTextAndChecksScalarLimits)
            ,("voiceLostAcknowledgementRestoresFrozenSubmissionWithoutChangingDraft", voiceLostAcknowledgementRestoresFrozenSubmissionWithoutChangingDraft)
            ,("legacyVoiceDraftRestoresAudioIntentWithoutImpliedTranscriptConfirmation", { try legacyVoiceDraftRestoresAudioIntentWithoutImpliedTranscriptConfirmation() })
        ]
        var results: [[String: String]] = []
        for (name, action) in checks {
            do { try await action(); results.append(["name":name,"status":"passed"]); print("PASS \(name)") }
            catch { results.append(["name":name,"status":"failed","error":String(describing:error)]); print("FAIL \(name): \(error)") }
        }
        let report: [String: Any] = ["platform":"macOS Foundation core; no iOS UI or live inference", "passed":results.filter{$0["status"] == "passed"}.count, "total":results.count, "checks":results]
        let data = try JSONSerialization.data(withJSONObject: report, options:[.prettyPrinted,.sortedKeys])
        if let index = CommandLine.arguments.firstIndex(of:"--report"), CommandLine.arguments.count > index+1 {
            try data.write(to:URL(fileURLWithPath:CommandLine.arguments[index+1]),options:.atomic)
        }
        if results.contains(where:{$0["status"] == "failed"}) { throw CheckFailure(message:"Core checks failed") }
    }
}
