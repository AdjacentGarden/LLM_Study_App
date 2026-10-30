import AVFoundation
import Foundation
import Observation

enum AssistantMicrophonePermission { case undetermined, granted, denied }

struct AssistantVoiceRecording {
  let url: URL
  let duration: TimeInterval
}

struct AssistantAudioSample {
  let power: Float
  let duration: TimeInterval
  let recording: Bool
}

@MainActor protocol AssistantVoiceCapture: AnyObject {
  var permission: AssistantMicrophonePermission { get }
  func requestPermission() async -> Bool
  func start() throws
  func sample() -> AssistantAudioSample
  func stop() -> AssistantVoiceRecording?
  func cancel()
}

/// Chat recordings are temporary and never enter the notes or upload workflows.
@MainActor final class SystemAssistantVoiceCapture: AssistantVoiceCapture {
  private var recorder: AVAudioRecorder?
  private var sessionActive = false

  var permission: AssistantMicrophonePermission {
    switch AVAudioApplication.shared.recordPermission {
    case .granted: return .granted
    case .denied: return .denied
    default: return .undetermined
    }
  }

  func requestPermission() async -> Bool {
    await withCheckedContinuation { continuation in
      AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
    }
  }

  func start() throws {
    cancel()
    let session = AVAudioSession.sharedInstance()
    do {
      try session.setCategory(.playAndRecord, mode: .default,
        options: [.defaultToSpeaker, .allowBluetoothHFP])
      try session.setActive(true)
      sessionActive = true
      let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("assistant-voice-\(UUID().uuidString).m4a")
      let audio = try AVAudioRecorder(url: url, settings: [
        AVFormatIDKey: kAudioFormatMPEG4AAC,
        AVSampleRateKey: 44100.0,
        AVNumberOfChannelsKey: 1,
        AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
      ])
      recorder = audio
      audio.isMeteringEnabled = true
      guard audio.prepareToRecord(), audio.record() else {
        throw CocoaError(.fileWriteUnknown)
      }
    } catch {
      cancel()
      throw error
    }
  }

  func sample() -> AssistantAudioSample {
    recorder?.updateMeters()
    return AssistantAudioSample(power: recorder?.averagePower(forChannel: 0) ?? -80,
      duration: recorder?.currentTime ?? 0, recording: recorder?.isRecording == true)
  }

  func stop() -> AssistantVoiceRecording? {
    guard let recorder else { deactivate(); return nil }
    let recording = AssistantVoiceRecording(url: recorder.url, duration: recorder.currentTime)
    recorder.stop()
    self.recorder = nil
    deactivate()
    return recording
  }

  func cancel() {
    if let recording = stop() { try? FileManager.default.removeItem(at: recording.url) }
  }

  private func deactivate() {
    guard sessionActive else { return }
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    sessionActive = false
  }
}

enum AssistantInputPhase: Equatable {
  case text, voiceReady, requestingPermission, recording, transcribing
}

/// Recognition can only populate a draft; sending remains an explicit composer action.
@MainActor @Observable final class AssistantVoiceInput {
  var draft = ""
  private(set) var phase: AssistantInputPhase = .text
  private(set) var level: Double = 0
  private(set) var seconds: TimeInterval = 0
  private(set) var error: String?
  private let capture: any AssistantVoiceCapture
  private let transcribe: @MainActor (URL) async throws -> String
  private let availability: @MainActor () -> String?
  private var operation: Task<Void, Never>?
  private var meter: Task<Void, Never>?
  private var generation = UUID()

  convenience init() {
    let recognizer = AppleSpeechTranscriber()
    self.init(capture: SystemAssistantVoiceCapture(),
      availability: {
        let capability = recognizer.capability
        return capability.available ? nil : capability.message
      }, transcribe: { try await recognizer.transcribe(fileURL: $0) })
  }

  static func configured() -> AssistantVoiceInput {
    #if DEBUG
      // Explicit UI-test fixture: no microphone, recognizer, or network is used.
      if ContentBundle.fixtureMode,
        ProcessInfo.processInfo.arguments.contains("--fixture-assistant-voice") {
        return AssistantVoiceInput(capture: AssistantVoicePreviewCapture()) { _ in
          try await Task.sleep(for: .milliseconds(300))
          return "请解释减数分裂为什么需要两次分裂。"
        }
      }
    #endif
    return AssistantVoiceInput()
  }

  init(capture: any AssistantVoiceCapture,
    availability: @escaping @MainActor () -> String? = { nil },
    transcribe: @escaping @MainActor (URL) async throws -> String) {
    self.capture = capture
    self.availability = availability
    self.transcribe = transcribe
  }

  var hasText: Bool { !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
  var isVoiceMode: Bool { phase != .text }

  func enterVoice() {
    guard phase == .text, !hasText else { return }
    error = nil
    seconds = 0
    phase = .voiceReady
  }

  func beginHold() {
    guard phase == .voiceReady else { return }
    error = nil
    switch capture.permission {
    case .undetermined:
      phase = .requestingPermission
      let token = generation
      operation = Task { @MainActor [weak self] in
        guard let self else { return }
        let granted = await capture.requestPermission()
        guard token == generation, !Task.isCancelled else { return }
        // The permission dialog breaks the original touch. Require a fresh hold.
        phase = .voiceReady
        if !granted { error = "麦克风权限未开启，请在系统设置中允许，或切回文字输入。" }
      }
    case .denied:
      error = "麦克风权限未开启，请在系统设置中允许，或切回文字输入。"
    case .granted:
      do {
        try capture.start()
        phase = .recording
        seconds = 0
        level = 0
        meter = Task { @MainActor [weak self] in
          while !Task.isCancelled {
            guard let self, phase == .recording else { return }
            refreshMeter()
            do { try await Task.sleep(for: .milliseconds(33)) } catch { return }
          }
        }
      } catch {
        capture.cancel()
        self.error = "无法开始录音，请重试或使用文字输入。"
      }
    }
  }

  func endHold() {
    guard phase == .recording else { return }
    meter?.cancel()
    meter = nil
    level = 0
    guard let recording = capture.stop() else {
      phase = .voiceReady
      error = "录音未完成，请按住麦克风重试。"
      return
    }
    seconds = recording.duration
    guard recording.duration >= 0.35 else {
      try? FileManager.default.removeItem(at: recording.url)
      phase = .voiceReady
      error = "录音太短，请按住麦克风说话后再松开。"
      return
    }
    // Metering works even when this device has no installed recognition model.
    // Capability is only required once a completed recording needs transcription.
    if let unavailable = availability() {
      try? FileManager.default.removeItem(at: recording.url)
      phase = .voiceReady
      error = unavailable
      return
    }
    phase = .transcribing
    let token = generation
    operation = Task { @MainActor [weak self] in
      defer { try? FileManager.default.removeItem(at: recording.url) }
      guard let self else { return }
      do {
        let text = try await transcribe(recording.url)
          .trimmingCharacters(in: .whitespacesAndNewlines)
        guard token == generation, !Task.isCancelled else { return }
        guard !text.isEmpty else { throw AppleSpeechError.emptyTranscription }
        draft = text
        phase = .text
      } catch {
        guard token == generation, !Task.isCancelled else { return }
        phase = .voiceReady
        self.error = error.localizedDescription
      }
    }
  }

  func returnToText() {
    generation = UUID()
    operation?.cancel()
    operation = nil
    meter?.cancel()
    meter = nil
    capture.cancel()
    level = 0
    phase = .text
    error = nil
  }

  func interrupt() {
    let interrupted = phase == .recording || phase == .transcribing
    returnToText()
    if interrupted { error = "语音输入已中断，请重新录音或输入文字。" }
  }

  /// Also used by tests to check that metering responds to real sample power.
  func refreshMeter() {
    guard phase == .recording else { return }
    let sample = capture.sample()
    guard sample.recording else { interrupt(); return }
    seconds = sample.duration
    let power = sample.power.isFinite ? Double(sample.power) : -80
    let normalized = min(1, max(0, (power + 60) / 42))
    let target = pow(normalized, 0.65)
    level += (target - level) * (target > level ? 0.45 : 0.16)
    if seconds >= 60 { endHold() }
  }
}

#if DEBUG
@MainActor private final class AssistantVoicePreviewCapture: AssistantVoiceCapture {
  let permission: AssistantMicrophonePermission = .granted
  private var started: Date?
  private var url: URL?
  func requestPermission() async -> Bool { true }
  func start() throws {
    let file = FileManager.default.temporaryDirectory.appendingPathComponent("voice-ui-fixture-\(UUID()).m4a")
    try Data([1, 2, 3]).write(to: file)
    url = file
    started = Date()
  }
  func sample() -> AssistantAudioSample {
    let elapsed = started.map { Date().timeIntervalSince($0) } ?? 0
    return .init(power: Float(-28 + sin(elapsed * 8) * 14), duration: elapsed, recording: started != nil)
  }
  func stop() -> AssistantVoiceRecording? {
    guard let started, let url else { return nil }
    self.started = nil
    self.url = nil
    return .init(url: url, duration: Date().timeIntervalSince(started))
  }
  func cancel() {
    if let recording = stop() { try? FileManager.default.removeItem(at: recording.url) }
  }
}
#endif
