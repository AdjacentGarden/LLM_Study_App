import Foundation
import Combine
import AVFoundation
#if os(iOS)
import UIKit
#endif

/// Native audio stays in Application Support until the user explicitly deletes it.
/// The caller must stop before navigating away or uploading the recording.
@MainActor
final class AudioCapture: NSObject, ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var isPlaying = false
    @Published private(set) var localURL: URL?
    @Published private(set) var duration: Double = 0
    @Published var error: String?
    @Published private(set) var recordingID: String?

    let contentType = "audio/mp4"
    private let directory: URL
    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var progress: Task<Void, Never>?
    private var starting = false
    private var metadata: RecordingMetadata?

    private struct RecordingMetadata: Codable {
        var id: String
        var filename: String
        var createdAt: Date
        var updatedAt: Date
        var duration: Double
        var state: String
    }

    init(directory: URL? = nil) {
        self.directory = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("CloudPath/recordings", isDirectory: true)
        super.init()
        do {
            try FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            var folder = self.directory
            try folder.setResourceValues(values)
        } catch {
            self.error = "无法创建录音目录：\(error.localizedDescription)"
        }
        #if os(iOS)
        NotificationCenter.default.addObserver(self, selector: #selector(stopForInterruption), name: AVAudioSession.interruptionNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(stopForInterruption), name: UIApplication.willResignActiveNotification, object: nil)
        #endif
    }

    func start() async {
        guard !isRecording, !starting else { return }
        starting = true
        defer { starting = false }
        error = nil
        let granted = await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { allowed in
                continuation.resume(returning: allowed)
            }
        }
        guard granted else {
            error = "麦克风权限未开启。请在系统设置中允许 CloudPath 使用麦克风。"
            return
        }
        stopPlayback()
        let previousMetadata = metadata
        var pendingID: String?
        do {
            #if os(iOS)
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothHFP])
            try session.setActive(true)
            #endif
            let id = UUID().uuidString
            pendingID = id
            let url = directory.appendingPathComponent(id).appendingPathExtension("m4a")
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 24_000,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 64_000,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue
            ]
            let newRecorder = try AVAudioRecorder(url: url, settings: settings)
            guard newRecorder.prepareToRecord() else { throw AudioError.unavailable }
            let now = Date()
            metadata = RecordingMetadata(id: id, filename: url.lastPathComponent, createdAt: now, updatedAt: now, duration: 0, state: "recording")
            try persistMetadata()
            guard newRecorder.record(forDuration: 600) else { throw AudioError.unavailable }
            recorder = newRecorder
            recordingID = id
            localURL = url
            duration = 0
            isRecording = true
            pendingID = nil
            progress?.cancel()
            progress = Task { [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .seconds(1)) } catch { return }
                    guard let self else { return }
                    if let recorder = self.recorder, recorder.isRecording {
                        self.duration = recorder.currentTime
                        self.metadata?.duration = self.duration
                        self.metadata?.updatedAt = Date()
                        do { try self.persistMetadata() }
                        catch { self.error = "录音已写入，但状态保存失败：\(error.localizedDescription)" }
                    } else {
                        self.stop()
                        return
                    }
                }
            }
        } catch {
            if let pendingID {
                try? FileManager.default.removeItem(at: directory.appendingPathComponent(pendingID).appendingPathExtension("m4a"))
                try? FileManager.default.removeItem(at: metadataURL(pendingID))
            }
            metadata = previousMetadata
            self.error = "无法开始录音：\(error.localizedDescription)"
            isRecording = false
            recorder = nil
        }
    }

    /// Synchronously finalizes the AAC file before callers read or leave this screen.
    func stop() {
        progress?.cancel()
        progress = nil
        if let recorder {
            duration = max(duration, recorder.currentTime)
            recorder.stop()
        }
        recorder = nil
        isRecording = false
        if localURL != nil {
            metadata?.duration = duration
            metadata?.updatedAt = Date()
            metadata?.state = "saved"
            do { try persistMetadata() }
            catch { self.error = "录音文件已保留，状态保存失败：\(error.localizedDescription)" }
        }
        #if os(iOS)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    /// Restores a known draft. A recording interrupted by process termination is
    /// checked for playability and never silently treated as an empty new note.
    func recover(id: String) {
        stop()
        stopPlayback()
        error = nil
        guard UUID(uuidString: id) != nil else {
            error = "录音标识无效。"
            return
        }
        do {
            let data = try Data(contentsOf: metadataURL(id))
            var saved = try JSONDecoder().decode(RecordingMetadata.self, from: data)
            guard saved.id == id, saved.filename == "\(id).m4a" else { throw AudioError.invalidMetadata }
            let url = directory.appendingPathComponent(saved.filename)
            let recovered = try AVAudioPlayer(contentsOf: url)
            guard recovered.duration > 0 else { throw AudioError.empty }
            recordingID = id
            localURL = url
            duration = recovered.duration
            let wasInterrupted = saved.state == "recording"
            saved.duration = duration
            saved.state = "saved"
            saved.updatedAt = Date()
            metadata = saved
            try persistMetadata()
            if wasInterrupted { error = "已恢复上次中断的录音，请试听确认后上传。" }
        } catch {
            recordingID = id
            localURL = nil
            metadata = nil
            duration = 0
            self.error = "无法恢复这段录音。原文件仍保留在设备上：\(error.localizedDescription)"
        }
    }

    /// Useful for a new screen before a draft has been selected explicitly.
    func recoverLatest() {
        guard !isRecording else { return }
        do {
            let urls = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            let candidates = urls.filter { $0.pathExtension == "json" }.compactMap { url -> RecordingMetadata? in
                guard let data = try? Data(contentsOf: url) else { return nil }
                return try? JSONDecoder().decode(RecordingMetadata.self, from: data)
            }
            if let latest = candidates.max(by: { $0.updatedAt < $1.updatedAt }) { recover(id: latest.id) }
        } catch { self.error = "无法读取本地录音：\(error.localizedDescription)" }
    }

    func playback() {
        stop()
        error = nil
        guard let localURL else { error = "请先录音或恢复录音。"; return }
        do {
            #if os(iOS)
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
            try AVAudioSession.sharedInstance().setActive(true)
            #endif
            let newPlayer = try AVAudioPlayer(contentsOf: localURL)
            guard newPlayer.prepareToPlay(), newPlayer.play() else { throw AudioError.unavailable }
            player = newPlayer
            isPlaying = true
            progress?.cancel()
            progress = Task { [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
                    guard let self else { return }
                    if self.player?.isPlaying != true { self.stopPlayback(); return }
                }
            }
        } catch { self.error = "无法播放录音：\(error.localizedDescription)" }
    }

    func stopPlayback() {
        player?.stop()
        player = nil
        isPlaying = false
        if !isRecording { progress?.cancel(); progress = nil }
    }

    /// Data is read from the finalized file, so a failed upload can be retried.
    func recordingData() throws -> Data {
        stop()
        guard let localURL else { throw AudioError.empty }
        let bytes = try Data(contentsOf: localURL, options: .mappedIfSafe)
        guard !bytes.isEmpty else { throw AudioError.empty }
        guard bytes.count <= 20 * 1024 * 1024 else { throw AudioError.tooLarge }
        return bytes
    }

    func delete() {
        stop()
        stopPlayback()
        do {
            if let recordingID {
                let audio = directory.appendingPathComponent(recordingID).appendingPathExtension("m4a")
                if FileManager.default.fileExists(atPath: audio.path) { try FileManager.default.removeItem(at: audio) }
                let info = metadataURL(recordingID)
                if FileManager.default.fileExists(atPath: info.path) { try FileManager.default.removeItem(at: info) }
            }
            localURL = nil
            recordingID = nil
            duration = 0
            metadata = nil
            error = nil
        } catch { self.error = "删除失败，录音仍保留：\(error.localizedDescription)" }
    }

    private func metadataURL(_ id: String) -> URL {
        directory.appendingPathComponent(id).appendingPathExtension("json")
    }

    private func persistMetadata() throws {
        guard let metadata else { return }
        try JSONEncoder().encode(metadata).write(to: metadataURL(metadata.id), options: .atomic)
        #if os(iOS)
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: metadataURL(metadata.id).path)
        #endif
    }

    #if os(iOS)
    @objc nonisolated private func stopForInterruption(_ notification: Notification) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            if self.isRecording { self.stop() }
            self.stopPlayback()
        }
    }
    #endif

    private enum AudioError: LocalizedError {
        case unavailable, empty, tooLarge, invalidMetadata
        var errorDescription: String? {
            switch self {
            case .unavailable: "音频设备不可用。"
            case .empty: "没有可用的录音内容。"
            case .tooLarge: "录音超过服务器的 20 MB 限制。"
            case .invalidMetadata: "录音恢复信息不匹配。"
            }
        }
    }
}
