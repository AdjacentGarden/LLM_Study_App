import Foundation
import CryptoKit
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private struct Failure: Error { let message: String }
private func require(_ value: Bool, _ message: String) throws { if !value { throw Failure(message: message) } }
@MainActor private func expectFailure(_ body: @MainActor () async throws -> Void) async throws {
    do { try await body() } catch { return }; throw Failure(message: "Expected a rejected operation")
}
private struct Body: Codable, Sendable, Equatable {
    var revision: Int; var action: String; var transcript: String; var request_id: String; var consent: Bool
}
private struct Step: Sendable {
    var method: String; var path: String; var body: String
    var disconnected = false; var held = false
}
private struct Seen: Sendable {
    var method: String; var path: String; var body: Body?; var durableBody: Body?
}
private actor Transport: HTTPTransport {
    var steps: [Step]; var seen: [Seen] = []; var problems: [String] = []
    var continuation: CheckedContinuation<Void, Never>?
    let stateURL: URL
    init(_ steps: [Step], stateURL: URL) { self.steps = steps; self.stateURL = stateURL }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let body = request.httpBody.flatMap { try? JSONDecoder().decode(Body.self, from: $0) }
        let stored = (try? Data(contentsOf: stateURL)).flatMap { try? JSONDecoder().decode(StoredState.self, from: $0) }
        let intent = stored?.notes.first?.analysisSubmission
        let durable = intent.map { Body(revision: $0.context.revision, action: $0.action,
            transcript: $0.confirmedTranscript ?? "", request_id: $0.requestID, consent: true) }
        let observed = Seen(method: request.httpMethod ?? "GET", path: request.url!.path, body: body, durableBody: durable)
        seen.append(observed)
        guard !steps.isEmpty else { problems.append("Unexpected request"); throw URLError(.badServerResponse) }
        let step = steps.removeFirst()
        guard step.method == observed.method, step.path == observed.path else {
            problems.append("Unexpected method/path"); throw URLError(.badServerResponse)
        }
        if step.disconnected { throw URLError(.networkConnectionLost) }
        if step.held { await withCheckedContinuation { continuation = $0 } }
        return (Data(step.body.utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!)
    }
    func observations() -> [Seen] { seen }
    func isHeld() -> Bool { continuation != nil }
    func release() { continuation?.resume(); continuation = nil }
    func verify() throws { try require(steps.isEmpty && problems.isEmpty, "Transport sequence was not fully satisfied") }
}
private let path = "/api/studio/notes/note_fixture/analyze"
private let jobsPath = "/api/studio/jobs"
private let reviewedText = "已经人工核对的测试笔记，讨论条件与结论之间的关系。"
private func job(_ id: String = "improve_fixture", kind: String = "improve", status: String = "queued", note: String = "note_fixture", revision: Int? = 2) -> String {
    var value: [String: Any] = ["id": id, "book_id": "b1", "kind": kind, "status": status,
        "result": ["transcript": "识别原稿，尚须核对。"], "error": "", "note_id": note]
    if let revision { value["revision"] = revision }
    return String(decoding: try! JSONSerialization.data(withJSONObject: value), as: UTF8.self)
}
private let connection: [Step] = [
    Step(method: "GET", path: "/api/health", body: #"{"ok":true}"#),
    Step(method: "GET", path: "/api/auth/me", body: #"{"account":null,"user_id":"visitor_fixture","email_available":false,"learning_sessions":{}}"#),
    Step(method: "GET", path: "/api/library", body: "[]"),
    Step(method: "GET", path: "/api/readiness", body: #"{"ok":true,"check_scope":"fixtures","external_inference_verified":false,"features":{},"blocked_features":[]}"#)
]

@main private struct VoiceReviewChecks {
    @MainActor static func setup(_ root: URL, steps: [Step]) async throws -> (AppModel, Transport, ServerConfiguration, URL) {
        let config = try ServerConfiguration(address: "https://fixture.invalid")
        let state = root.appendingPathComponent(config.namespace).appendingPathComponent("state.json")
        let transport = Transport(connection + steps, stateURL: state)
        let model = AppModel(root: root, transport: transport); await model.connect(config)
        try require(model.connected && model.error == nil, "Fixture connection failed")
        return (model, transport, config, state)
    }
    @MainActor static func confirmedDraft(_ model: AppModel) async throws -> String {
        var note = Note(id: "note_fixture", bookID: "b1", title: "语音", revision: 2)
        note.audio_ready = true
        let id = try await model.openVoiceNote(note)
        model.state.notes[0].recordingID = "audio-preserved.m4a"
        model.state.notes[0].text = String(repeating: "独立草稿", count: 4000)
        await model.saveDraft(model.state.notes[0])
        let recognized = try JSONDecoder().decode(StudioJob.self, from: Data(job("recognize_fixture", kind: "complete", status: "needs_confirmation").utf8))
        try await model.beginVoiceTranscriptReview(draftID: id, job: recognized)
        try await model.updateVoiceTranscriptReview(draftID: id, text: reviewedText, confirmed: false)
        try await model.updateVoiceTranscriptReview(draftID: id, text: reviewedText, confirmed: true)
        return id
    }
    @MainActor static func lostResponse(_ root: URL) async throws {
        let steps = [Step(method: "POST", path: path, body: "", disconnected: true)] + connection + [
            Step(method: "GET", path: jobsPath, body: #"{"items":[]}"#),
            Step(method: "POST", path: path, body: job()),
            Step(method: "GET", path: jobsPath, body: "{\"items\":[" + job(status: "succeeded") + "]}")]
        let (model, transport, config, stateURL) = try await setup(root, steps: steps)
        let id = try await confirmedDraft(model)
        let audio = stateURL.deletingLastPathComponent().appendingPathComponent("audio-preserved.m4a")
        let bytes = Data("unchanged-AAC-fixture-bytes".utf8); try bytes.write(to: audio)
        try await expectFailure { _ = try await model.submitVoiceAnalysis(draftID: id, action: "improve") }
        let intent = model.state.notes[0].analysisSubmission!
        try require(intent.isUnacknowledged && intent.confirmedTranscript == reviewedText, "Lost response did not retain frozen intent")
        let resumed = AppModel(root: root, transport: transport); await resumed.connect(config)
        _ = try await resumed.refreshVoiceJobs(draftID: id)
        try require(await transport.observations().filter { $0.method == "POST" }.count == 1, "Relaunch/GET automatically resubmitted inference")
        resumed.state.notes[0].text = "重新编辑的普通笔记"; await resumed.saveDraft(resumed.state.notes[0])
        try await expectFailure { try await resumed.updateVoiceTranscriptReview(draftID: id, text: "不得替换未知提交", confirmed: true) }
        _ = try await resumed.submitVoiceAnalysis(draftID: id, action: "improve")
        _ = try await resumed.refreshVoiceJobs(draftID: id)
        let posts = await transport.observations().filter { $0.method == "POST" }
        try require(posts.count == 2 && posts[0].body == posts[1].body, "Explicit recovery changed the immutable request")
        try require(posts.allSatisfy { $0.body == $0.durableBody }, "POST preceded durable intent persistence")
        try require(resumed.state.notes[0].analysisSubmission?.jobStatus == "succeeded", "Terminal job did not recover")
        try require(resumed.state.notes[0].text == "重新编辑的普通笔记", "Ordinary draft was overwritten")
        try require(try Data(contentsOf: audio) == bytes, "Audio bytes were changed/deleted")
        try await transport.verify()
    }
    @MainActor static func diskFailure(_ root: URL) async throws {
        let (model, transport, _, stateURL) = try await setup(root, steps: [])
        let id = try await confirmedDraft(model)
        try FileManager.default.removeItem(at: stateURL)
        try FileManager.default.createDirectory(at: stateURL, withIntermediateDirectories: false)
        try await expectFailure { _ = try await model.submitVoiceAnalysis(draftID: id, action: "improve") }
        try require(await transport.observations().allSatisfy { $0.method == "GET" }, "Failed persistence still triggered a POST")
        try await transport.verify()
    }
    @MainActor static func duplicateAndLateResponse(_ root: URL) async throws {
        let (model, transport, _, _) = try await setup(root, steps: [Step(method: "POST", path: path, body: job(), held: true)])
        let id = try await confirmedDraft(model)
        let first = Task { try await model.submitVoiceAnalysis(draftID: id, action: "improve") }
        for _ in 0..<200 { if await transport.isHeld() { break }; try await Task.sleep(for: .milliseconds(10)) }
        try require(await transport.isHeld(), "POST was never held")
        try await expectFailure { _ = try await model.submitVoiceAnalysis(draftID: id, action: "improve") }
        model.state.notes[0].recordingID = "new-recording.m4a"; model.state.notes[0].uploadComplete = false
        model.state.notes[0].clearVoiceAnalysis(); await model.saveDraft(model.state.notes[0])
        await transport.release()
        try await expectFailure { _ = try await first.value }
        try require(model.state.notes[0].analysisSubmission == nil && model.state.notes[0].recordingID == "new-recording.m4a", "Late old response overwrote the new recording")
        try require(await transport.observations().filter { $0.method == "POST" }.count == 1, "Double tap created another POST")
        try await transport.verify()
    }
    @MainActor static func filteringAndEdits(_ root: URL) async throws {
        let list = "{\"items\":[" + [job("current", kind: "complete", status: "needs_confirmation"),
            job("old", revision: 1), job("foreign", note: "other_note"), job("no_revision", revision: nil)].joined(separator: ",") + "]}"
        let (model, transport, _, _) = try await setup(root, steps: [Step(method: "GET", path: jobsPath, body: list)])
        let id = try await confirmedDraft(model)
        try await model.updateVoiceTranscriptReview(draftID: id, text: "尚在编辑的人工校正稿", confirmed: false)
        let jobs = try await model.refreshVoiceJobs(draftID: id)
        try require(jobs.map(\.id) == ["current"], "Wrong note/revision/absent revision passed the filter")
        try require(model.state.notes[0].transcriptReview?.text == "尚在编辑的人工校正稿" && model.state.notes[0].transcriptReview?.confirmed == false, "GET replaced edits or granted confirmation")
        try require(await transport.observations().allSatisfy { $0.method == "GET" }, "Review/polling triggered inference")
        try await transport.verify()
    }
    @MainActor static func terminalDuplicate(_ root: URL) async throws {
        let (model, transport, _, _) = try await setup(root, steps: [
            Step(method: "POST", path: path, body: job(status: "succeeded")),
            Step(method: "POST", path: path, body: job(status: "succeeded"))])
        let id = try await confirmedDraft(model)
        _ = try await model.submitVoiceAnalysis(draftID: id, action: "improve")
        _ = try await model.submitVoiceAnalysis(draftID: id, action: "improve")
        let posts = await transport.observations().filter { $0.method == "POST" }
        try require(posts.count == 2 && posts[0].body == posts[1].body, "Repeated known submission generated another request ID/body")
        try await transport.verify()
    }
    @MainActor static func recordingIsolation(_ root: URL) async throws {
        let (model, transport, _, _) = try await setup(root, steps: [])
        let oldID = try await confirmedDraft(model)
        // A is still referenced after recording an unuploaded B locally.
        let noteA = model.state.notes[0].remoteNote!
        try model.state.notes[0].replaceVoiceRecording(with: "unuploaded-B.m4a")
        await model.saveDraft(model.state.notes[0])
        let serverID = try await model.openVoiceNote(noteA)
        try require(serverID != oldID && model.state.notes.count == 2, "Server A reused/relabelled the unuploaded B draft")
        let old = model.state.notes.first { $0.id == oldID }!
        let server = model.state.notes.first { $0.id == serverID }!
        try require(old.recordingID == "unuploaded-B.m4a" && !old.uploadComplete && old.voiceContext == nil, "Unuploaded B or its state was lost")
        try require(server.recordingID == nil && server.voiceContext?.noteID == noteA.id, "Server A was falsely bound to local B")
        let recognition = try JSONDecoder().decode(StudioJob.self, from: Data(job("recognized_A", kind: "recognize", status: "succeeded").utf8))
        try await model.beginVoiceTranscriptReview(draftID: serverID, job: recognition)
        try await model.updateVoiceTranscriptReview(draftID: serverID, text: reviewedText, confirmed: false)
        try await model.updateVoiceTranscriptReview(draftID: serverID, text: reviewedText, confirmed: true)
        let index = model.state.notes.firstIndex { $0.id == serverID }!
        let intent = try model.state.notes[index].prepareVoiceAnalysis(action: "improve")
        do {
            try model.state.notes[index].replaceVoiceRecording(with: "replacement-C.m4a")
            throw Failure(message: "Unknown intent allowed recording replacement")
        } catch is ClientError { }
        try require(model.state.notes[index].analysisSubmission?.requestID == intent.requestID &&
            model.state.notes[index].analysisSubmission?.confirmedTranscript == reviewedText &&
            model.state.notes[index].uploadComplete && model.state.notes[index].recordingID == nil,
            "Rejected replacement erased the frozen confirmation/request")
        try await transport.verify()
    }
    @MainActor static func main() async throws {
        let ios = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let root = ios.appendingPathComponent(".build/voice-review-fixtures/" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let checks: [(String, @MainActor (URL) async throws -> Void)] = [
            ("lost_ack_relaunch_get_only_explicit_same_body_recovery", lostResponse),
            ("disk_write_failure_stops_before_post", diskFailure),
            ("double_tap_and_late_old_recording_response", duplicateAndLateResponse),
            ("revision_filter_and_poll_preserves_human_edits", filteringAndEdits),
            ("terminal_repeat_preserves_request_identity", terminalDuplicate),
            ("server_note_isolated_from_unuploaded_audio_and_unknown_lock", recordingIsolation)]
        var results: [[String: Any]] = []
        for (name, check) in checks {
            do { try await check(root.appendingPathComponent(name)); results.append(["name": name, "passed": true]); print("PASS \(name)") }
            catch { results.append(["name": name, "passed": false, "error": String(describing: error)]); print("FAIL \(name): \(error)") }
        }
        let passed = results.allSatisfy { $0["passed"] as? Bool == true }
        let report: [String: Any] = ["passed": passed, "total": results.count, "results": results,
            "fixtures_only": true, "real_network_used": false, "ios_build_verified": false, "iphone_ui_verified": false,
            "scope": "macOS Swift 6 AppModel/API/disk fixtures; no microphone, playback, ASR or model inference"]
        try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            .write(to: ios.appendingPathComponent("voice-review-app-model-results.json"), options: .atomic)
        try require(passed, "Voice review fixture failures")
    }
}
