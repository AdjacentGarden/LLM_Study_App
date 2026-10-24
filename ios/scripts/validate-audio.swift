import Foundation
import AVFoundation

/// Disk recovery integration check. This does not request microphone permission,
/// record hardware, play sound, or exercise the unavailable iOS branch.
@main
struct AudioPersistenceCheck {
    struct FixtureMetadata: Codable {
        var id: String
        var filename: String
        var createdAt: Date
        var updatedAt: Date
        var duration: Double
        var state: String
    }

    @MainActor
    static func main() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("cloudpath-audio-check-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let id = UUID().uuidString
        let audio = directory.appendingPathComponent("\(id).m4a")
        try createAAC(audio)
        let metadataURL = directory.appendingPathComponent("\(id).json")
        let now = Date()
        var metadata = FixtureMetadata(id: id, filename: "\(id).m4a", createdAt: now, updatedAt: now, duration: 0.25, state: "recording")
        try JSONEncoder().encode(metadata).write(to: metadataURL, options: .atomic)

        let capture = AudioCapture(directory: directory)
        capture.recover(id: id)
        precondition(capture.recordingID == id)
        precondition(capture.localURL == audio)
        precondition(capture.duration > 0)
        precondition(capture.error?.contains("中断") == true)
        precondition(!capture.isRecording)
        let actual = try capture.recordingData()
        let diskBytes = try Data(contentsOf: audio)
        precondition(actual == diskBytes)
        let finalized = try JSONDecoder().decode(FixtureMetadata.self, from: Data(contentsOf: metadataURL))
        precondition(finalized.state == "saved")

        let relaunched = AudioCapture(directory: directory)
        relaunched.recoverLatest()
        precondition(relaunched.recordingID == id)
        precondition(relaunched.localURL == audio)
        precondition(relaunched.error == nil)
        relaunched.delete()
        precondition(!FileManager.default.fileExists(atPath: audio.path))
        precondition(!FileManager.default.fileExists(atPath: metadataURL.path))

        let badID = UUID().uuidString
        let damaged = directory.appendingPathComponent("\(badID).m4a")
        try Data("not an audio container".utf8).write(to: damaged)
        metadata.id = badID
        metadata.filename = "\(badID).m4a"
        try JSONEncoder().encode(metadata).write(to: directory.appendingPathComponent("\(badID).json"), options: .atomic)
        let failed = AudioCapture(directory: directory)
        failed.recover(id: badID)
        precondition(failed.localURL == nil)
        precondition(failed.error != nil)
        precondition(FileManager.default.fileExists(atPath: damaged.path))
        failed.recover(id: "../../outside")
        precondition(failed.error == "录音标识无效。")
        print("PASS: real AAC bytes, interrupted-draft recovery, relaunch recovery, upload reads, explicit deletion, corrupt-file retention, invalid identifier rejection")
        print("Scope: macOS AVFoundation + filesystem only; microphone and iOS lifecycle remain untested.")
    }

    private static func createAAC(_ url: URL) throws {
        let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 6_000)!
        buffer.frameLength = 6_000
        for index in 0..<6_000 { buffer.floatChannelData![0][index] = sin(Float(index) * 0.08) * 0.05 }
        let output = try AVAudioFile(forWriting: url, settings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 24_000,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 64_000
        ])
        try output.write(from: buffer)
    }
}
