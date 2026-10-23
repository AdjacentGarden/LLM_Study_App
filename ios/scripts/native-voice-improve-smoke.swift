import Foundation
import CryptoKit
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
private struct CheckError: Error { let stage: String }
private struct ConfirmedBody: Codable, Sendable, Equatable {
    var revision: Int; var action: String; var transcript: String; var consent: Bool; var request_id: String
}
private struct Plan: Decodable {
    struct Confirmation: Decodable { var manual_confirmation: Bool; var synthetic_acceptance: Bool; var original_say_text_found: Bool }
    var note_id: String; var book_id: String; var complete_job_id: String
    var expected_audio_sha256: String; var expected_audio_bytes: Int
    var body: ConfirmedBody; var confirmation: Confirmation
}
private struct AuthorizedState: Decodable { var cookies: [String: String] }
private func canonical(_ data: Data) throws -> Data {
    try JSONSerialization.data(withJSONObject: JSONSerialization.jsonObject(with: data), options: [.sortedKeys, .withoutEscapingSlashes])
}
private actor GuardedTransport: HTTPTransport {
    let session = SessionTransport()
    let expected: ConfirmedBody
    let bodySHA: String
    let notePath: String
    let allowPost: Bool
    var postCalls = 0
    var postStatus: Int?
    var actualBodySHA: String?
    init(expected: ConfirmedBody, bodySHA: String, noteID: String, allowPost: Bool) {
        self.expected = expected; self.bodySHA = bodySHA; self.allowPost = allowPost
        notePath = "/api/studio/notes/" + noteID + "/analyze"
    }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        if request.httpMethod == "POST" {
            guard allowPost, postCalls == 0, request.url?.path == notePath,
                  let data = request.httpBody,
                  let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  Set(object.keys) == Set(["revision", "action", "transcript", "request_id", "consent"]),
                  try JSONDecoder().decode(ConfirmedBody.self, from: data) == expected,
                  sha(try canonical(data)) == bodySHA else { throw CheckError(stage: "native_body_does_not_match_confirmed_plan") }
            postCalls += 1; actualBodySHA = sha(try canonical(data))
            let response = try await session.send(request)
            postStatus = response.1.statusCode
            return response
        }
        guard request.httpMethod == "GET" else { throw CheckError(stage: "forbidden_note_or_audio_mutation") }
        return try await session.send(request)
    }
    func snapshot() -> (Int, Int?, String?) { (postCalls, postStatus, actualBodySHA) }
}

@main private struct NativeVoiceImproveSmoke {
    static func argument(_ flag: String) -> String? {
        guard let index = CommandLine.arguments.firstIndex(of: flag), CommandLine.arguments.count > index + 1 else { return nil }
        return CommandLine.arguments[index + 1]
    }
    static func main() async {
        let ios = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let runtime = ios.appendingPathComponent(".runtime/voice-review-live", isDirectory: true)
        let reportName = argument("--report-name") ?? "native-voice-improve-" + UUID().uuidString + "-results.json"
        guard !reportName.contains("/"), reportName.hasSuffix(".json"),
              !FileManager.default.fileExists(atPath: ios.appendingPathComponent(reportName).path) else {
            print("Use a fresh JSON report filename; existing acceptance evidence is preserved."); exit(2)
        }
        var report: [String: Any] = ["passed": false, "ios_build_verified": false, "iphone_verified": false,
            "transport": "Actual LearningCore APIClient and URLSession; no raw JSON submission",
            "confirmation_scope": "Manually edited synthetic acceptance; original say script unavailable; not proof of faithful transcription",
            "no_note_save_or_audio_put": true, "unknown_response_policy": "No replay; GET/SQL reconciliation required"]
        var guarded: GuardedTransport?
        var phase = "check_only"
        do {
            guard let planPath = argument("--plan"), let expectedPlanSHA = argument("--plan-sha"),
                  let expectedBodySHA = argument("--body-sha") else { throw CheckError(stage: "required_plan_and_hash_arguments") }
            let raw = try Data(contentsOf: URL(fileURLWithPath: planPath))
            guard sha(raw) == expectedPlanSHA else { throw CheckError(stage: "plan_hash_mismatch") }
            let plan = try JSONDecoder().decode(Plan.self, from: raw)
            guard plan.confirmation.manual_confirmation, plan.confirmation.synthetic_acceptance,
                  plan.body.action == "improve", plan.body.consent,
                  sha(try canonical(JSONEncoder().encode(plan.body))) == expectedBodySHA else { throw CheckError(stage: "confirmation_or_body_hash_mismatch") }
            try VoiceTranscriptReview.validate(plan.body.transcript)
            let submit = CommandLine.arguments.contains("--submit")
            let duplicate = argument("--repeat-known-job")
            guard !(submit && duplicate != nil) else { throw CheckError(stage: "choose_one_phase") }
            phase = submit ? "initial_submit" : (duplicate != nil ? "terminal_duplicate" : "check_only")
            let config = try ServerConfiguration(address: argument("--base") ?? "http://127.0.0.1:18126", allowLocalHTTP: true)
            let disk = try DiskStore(root: runtime)
            let identity = try JSONDecoder().decode(AuthorizedState.self, from: Data(contentsOf: ios.appendingPathComponent(".runtime/acceptance-client-state.json")))
            guard let visitor = identity.cookies["zhiwo_visitor"], !visitor.isEmpty else { throw CheckError(stage: "missing_private_visitor") }
            let cookies: [[String: Any]] = [["name": "zhiwo_visitor", "value": visitor, "domain": config.baseURL.host!, "path": "/", "secure": config.baseURL.scheme == "https"]]
            let cookieURL = runtime.appendingPathComponent("cookies.json")
            try JSONSerialization.data(withJSONObject: cookies).write(to: cookieURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: cookieURL.path)
            let transport = GuardedTransport(expected: plan.body, bodySHA: expectedBodySHA, noteID: plan.note_id, allowPost: submit || duplicate != nil)
            guarded = transport
            let api = APIClient(configuration: config, store: disk, transport: transport)
            try await api.restoreCookies()
            let note = try await api.note(plan.note_id)
            guard note.revision == plan.body.revision, note.book_id == plan.book_id,
                  note.input_mode == "voice", note.audio_ready == true else { throw CheckError(stage: "source_note_revision_mismatch") }
            let audio = try await api.downloadAudio(note: note)
            guard audio.count == plan.expected_audio_bytes, sha(audio) == plan.expected_audio_sha256 else { throw CheckError(stage: "source_audio_hash_mismatch") }
            let original = try await api.studioJob(plan.complete_job_id)
            guard original.status == "needs_confirmation" else { throw CheckError(stage: "original_asr_gate_changed") }
            report["original_complete_status"] = original.status
            report["audio_sha256"] = sha(audio); report["audio_bytes"] = audio.count
            report["plan_sha256"] = expectedPlanSHA; report["expected_body_sha256"] = expectedBodySHA
            report["request_id"] = plan.body.request_id; report["note_id"] = note.id; report["revision"] = note.revision
            if let duplicate {
                let known = try await api.studioJob(duplicate)
                guard ["succeeded", "failed"].contains(known.status), known.kind == "improve", known.note_id == note.id,
                      known.revision == note.revision else { throw CheckError(stage: "duplicate_requires_known_terminal_job") }
                report["known_job_id"] = known.id; report["known_status"] = known.status
            }
            if submit || duplicate != nil {
                let marker = phase + "-" + plan.body.request_id + ".json"
                guard try await disk.load([String: String].self, from: marker) == nil else { throw CheckError(stage: "attempt_already_marked_do_not_replay") }
                try await disk.save(["request_id": plan.body.request_id, "body_sha256": expectedBodySHA,
                    "phase": phase, "attempt_started": ISO8601DateFormatter().string(from: Date())], as: marker)
                // This call exercises the repaired explicit confirmation parameter.
                let job = try await api.analyze(note: note, transcript: "", requestID: plan.body.request_id,
                    action: "improve", confirmedVoiceTranscript: plan.body.transcript)
                guard job.note_id == note.id, job.revision == note.revision, job.kind == "improve",
                      duplicate == nil || job.id == duplicate else { throw CheckError(stage: "job_identity_mismatch") }
                report["job_id"] = job.id; report["status"] = job.status
                report["task_succeeded"] = job.status == "succeeded"
                print("Native improve \(phase): HTTP \((await transport.snapshot()).1 ?? 0), job \(job.id), status \(job.status)")
            }
            report["request_contract_passed"] = true
            report["passed"] = phase == "check_only" || report["task_succeeded"] as? Bool == true
        } catch let error as CheckError { report["failure_stage"] = error.stage }
        catch { report["failure_stage"] = "native_request_or_preflight_failed_reconcile_before_retry" }
        if let guarded {
            let snapshot = await guarded.snapshot()
            report["post_calls"] = snapshot.0; report["http_status"] = snapshot.1
            report["actual_body_sha256"] = snapshot.2
        }
        report["phase"] = phase; report["recorded_at"] = ISO8601DateFormatter().string(from: Date())
        do {
            let destination = ios.appendingPathComponent(reportName)
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: destination, options: .atomic)
        } catch { print("Could not save sanitized evidence"); exit(1) }
        if report["passed"] as? Bool != true { print("Native improve check failed; no automatic replay. See sanitized evidence."); exit(1) }
        if phase == "check_only" { print("PASS: native improve read-only preflight; POST count 0") }
    }
}
