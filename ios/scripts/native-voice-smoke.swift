import Foundation
import AVFoundation
import CryptoKit
import Darwin

private struct AuthorizedVoiceState: Decodable {
    var cookies: [String: String]
    var book_id: String
    var session_id: String
}
private struct VoiceCheckError: Error { let reason: String }

@main
private struct NativeVoiceSmoke {
    @MainActor
    static func main() async {
        let check = VoiceLiveCheck()
        do {
            try await check.run()
            check.report["passed"] = true
            check.report["status"] = "succeeded"
        } catch {
            check.report["passed"] = false
            check.report["status"] = "failed"
            check.report["failed_stage"] = check.stage
            if let failure = error as? VoiceCheckError { check.report["failure"] = failure.reason }
            else if case ClientError.http(let code, _) = error { check.report["failure"] = "HTTP \(code)" }
            else { let failure = error as NSError; check.report["failure"] = "\(failure.domain) code \(failure.code)" }
        }
        check.finish()
        do { try check.saveReport() }
        catch { print("FAIL: could not write sanitized acceptance report"); exit(1) }
        if check.report["passed"] as? Bool == true { print("PASS: native AAC upload/download and GPU recognition acceptance; no audio playback or transcript output") }
        else { print("FAIL: native voice acceptance at stage \(check.stage); see sanitized report"); exit(1) }
    }
}

@MainActor
private final class VoiceLiveCheck {
    let ios = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
    var report: [String: Any] = [
        "passed": false, "status": "started", "transport": "real native URLSession on macOS",
        "live_network_used": true, "live_gpu_asr_requested": false, "requested_action": "recognize",
        "paid_llm_requested": false, "iphone_runtime_verified": false, "iphone_microphone_verified": false,
        "microphone_permission_verified": false, "audio_played": false,
        "scope": "Existing visitor-owned speech AIFF converted to AAC with macOS AVFoundation; new private note only; no microphone capture, iPhone build, permission, playback, or paid LLM validation"
    ]
    var stage = "initialize"
    private var timings: [String: Double] = [:]
    private var stageStarted = Date()
    private let started = Date()

    func mark(_ name: String) {
        timings[stage] = Date().timeIntervalSince(stageStarted)
        stage = name; stageStarted = Date()
        print("Native voice stage: \(name)")
    }
    func finish() {
        timings[stage] = Date().timeIntervalSince(stageStarted)
        report["stage_seconds"] = timings
        report["total_seconds"] = Date().timeIntervalSince(started)
        report["created_at"] = ISO8601DateFormatter().string(from: Date())
    }
    func saveReport() throws {
        try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            .write(to: ios.appendingPathComponent("native-voice-live-results.json"), options: .atomic)
    }
    private func require(_ ok: Bool, _ reason: String) throws { if !ok { throw VoiceCheckError(reason: reason) } }
    private func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    private func writePrivate(_ data: Data, to url: URL) throws {
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
    private func audioInfo(_ data: Data, file: URL, format: String) throws -> [String: Any] {
        let player = try AVAudioPlayer(contentsOf: file)
        try require(player.duration > 0, "Native AVAudioPlayer found no audio duration")
        let audio = try AVAudioFile(forReading: file)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: audio.processingFormat, frameCapacity: 4096) else { throw VoiceCheckError(reason: "PCM decode buffer unavailable") }
        var frames: AVAudioFramePosition = 0
        while audio.framePosition < audio.length {
            try audio.read(into: buffer)
            if buffer.frameLength == 0 { break }
            frames += AVAudioFramePosition(buffer.frameLength)
        }
        try require(frames > 0, "Native AVAudioFile could not decode PCM frames")
        return ["format": format, "bytes": data.count, "sha256": sha(data), "duration_seconds": player.duration,
                "sample_rate_hz": audio.processingFormat.sampleRate, "channels": audio.processingFormat.channelCount,
                "decoded_pcm_frames": frames, "player": "AVAudioPlayer initialized without play()", "decoder": "AVAudioFile full PCM read"]
    }

    func run() async throws {
        mark("private_identity")
        let runtime = ios.appendingPathComponent(".runtime/native-voice-live", isDirectory: true)
        try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let state = try JSONDecoder().decode(AuthorizedVoiceState.self, from: Data(contentsOf: ios.appendingPathComponent(".runtime/acceptance-client-state.json")))
        guard let visitor = state.cookies["zhiwo_visitor"], !visitor.isEmpty else { throw VoiceCheckError(reason: "Authorized visitor cookie is unavailable") }
        let config = try ServerConfiguration(address: "http://127.0.0.1:18126", allowLocalHTTP: true)
        let cookie: [[String: Any]] = [["name": "zhiwo_visitor", "value": visitor, "domain": "127.0.0.1", "path": "/", "secure": false]]
        try writePrivate(JSONSerialization.data(withJSONObject: cookie), to: runtime.appendingPathComponent("cookies.json"))
        let api = APIClient(configuration: config, store: try DiskStore(root: runtime))
        try await api.restoreCookies()
        let account = try await api.account()
        try require(account.account == nil, "Acceptance identity must be a visitor")
        report["visitor_identity_confirmed"] = true

        mark("source_speech_download_and_decode")
        let books = try await api.books()
        guard let book = books.first(where: { $0.diagnostics_ready == true }) ?? books.first else { throw VoiceCheckError(reason: "Visitor has no canonical book") }
        let notes = try await api.notes(book: book.book_id)
        var sourceData: Data?
        let sourceURL = runtime.appendingPathComponent("existing-speech.aiff")
        for note in notes where note.audio_ready == true {
            let data = try await api.downloadAudio(note: note)
            guard data.count > 12, data.starts(with: Data("FORM".utf8)), [Data("AIFF".utf8), Data("AIFC".utf8)].contains(data.subdata(in: 8..<12)) else { continue }
            let player = try AVAudioPlayer(data: data)
            guard (20...40).contains(player.duration) else { continue }
            sourceData = data; break
        }
        guard let sourceData else { throw VoiceCheckError(reason: "Existing visitor-owned 20–40 second AIFF speech note was not found") }
        try writePrivate(sourceData, to: sourceURL)
        let input = try audioInfo(sourceData, file: sourceURL, format: "AIFF/AIFC existing speech")
        report["input_audio"] = input
        report["book_id"] = book.book_id

        mark("native_aac_export_and_decode")
        let outputURL = runtime.appendingPathComponent("native-" + UUID().uuidString + ".m4a")
        guard let exporter = AVAssetExportSession(asset: AVURLAsset(url: sourceURL), presetName: AVAssetExportPresetAppleM4A) else { throw VoiceCheckError(reason: "Native AppleM4A exporter unavailable") }
        try await exporter.export(to: outputURL, as: .m4a)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: outputURL.path)
        let aac = try Data(contentsOf: outputURL)
        try require(aac.count > 32 && aac.count <= 20 * 1024 * 1024 && aac.subdata(in: 4..<8) == Data("ftyp".utf8), "AAC MP4 container is invalid or exceeds the upload limit")
        let output = try audioInfo(aac, file: outputURL, format: "AAC in MPEG-4 audio (.m4a), audio/mp4")
        report["output_audio"] = output
        let duration = output["duration_seconds"] as! Double
        try require(abs(duration - (input["duration_seconds"] as! Double)) < 0.5, "AAC export changed the speech duration unexpectedly")

        let noteID = "nativevoice_" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
        let requestID = UUID().uuidString
        report["new_note_id"] = noteID
        try writePrivate(JSONSerialization.data(withJSONObject: ["new_note_id": noteID, "request_id": requestID, "book_id": book.book_id, "audio_sha256": sha(aac)]), to: runtime.appendingPathComponent("request-ledger.json"))
        mark("new_note_save")
        // A fresh random identifier is checked before POST; the original note is never saved or overwritten.
        do { _ = try await api.note(noteID); throw VoiceCheckError(reason: "Fresh acceptance note identifier already exists") }
        catch ClientError.http(let status, _) where status == 404 { }
        var note = try await api.saveNote(Note(id: noteID, bookID: book.book_id, title: "Native AAC GPU ASR acceptance"))
        try require(note.id == noteID && note.revision == 1, "Fresh voice note revision contract failed")
        report["saved_revision"] = note.revision

        mark("aac_upload")
        note = try await api.uploadAudio(note: note, data: aac, seconds: duration)
        try require(note.audio_ready == true, "Server did not confirm uploaded audio")
        report["uploaded_revision"] = note.revision
        mark("authenticated_roundtrip_download_and_decode")
        let roundtrip = try await api.downloadAudio(note: note)
        try require(sha(roundtrip) == sha(aac), "Uploaded and downloaded AAC SHA-256 differ")
        let downloadedURL = runtime.appendingPathComponent("roundtrip.m4a")
        try writePrivate(roundtrip, to: downloadedURL)
        report["roundtrip_audio"] = try audioInfo(roundtrip, file: downloadedURL, format: "Server-returned AAC .m4a")
        report["roundtrip_sha256_matches"] = true

        mark("gpu_recognize_submit")
        report["live_gpu_asr_requested"] = true
        var job = try await api.analyze(note: note, transcript: "", requestID: requestID, action: "recognize")
        report["job_id"] = job.id
        try require(job.kind == "recognize", "Server returned an unexpected analysis action")
        mark("gpu_recognize_wait")
        let deadline = Date().addingTimeInterval(120)
        var polls = 0
        while job.isPending && Date() < deadline {
            try await Task.sleep(for: .seconds(1))
            if let updated = try await api.studioJobs(book: book.book_id).first(where: { $0.id == job.id }) { job = updated }
            polls += 1
        }
        report["job_status"] = job.status
        report["poll_count"] = polls
        report["transcript_character_count"] = job.result.transcript?.count ?? 0
        try require(job.status == "succeeded", job.isPending ? "GPU recognition exceeded the 120 second wait limit" : "GPU recognition did not succeed")
        try require(!(job.result.transcript ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, "GPU recognition returned an empty transcript")
        report["original_note_unchanged_by_client"] = true
    }
}
