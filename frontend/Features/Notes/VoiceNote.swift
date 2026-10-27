import AVFoundation
import CryptoKit
import Observation
import SwiftUI

private enum NotesRecordingState { case idle, requesting, recording, paused, ready }

@MainActor @Observable
final class NotesAudioController: NSObject, AVAudioRecorderDelegate, AVAudioPlayerDelegate {
  fileprivate var state: NotesRecordingState = .idle
  var seconds: Double = 0
  var waveform: [Double] = Array(repeating: 0.08, count: 32)
  var error: String?
  var audioURL: URL?
  var playing = false
  var playbackPosition: Double = 0
  var playbackDuration: Double = 0
  var isSample = false
  var onFinish: (() -> Void)?
  private var recorder: AVAudioRecorder?
  private var player: AVAudioPlayer?
  private var timer: Timer?
  private var completed = false
  private var captureGeneration = UUID()

  func start() async {
    let generation = UUID()
    captureGeneration = generation
    error = nil
    state = .requesting
    isSample = false
    let allowed = await withCheckedContinuation { continuation in
      AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
    }
    guard generation == captureGeneration, state == .requesting else { return }
    guard allowed else {
      state = .idle
      error = "未获得麦克风权限。请在系统设置中允许录音，已有文字笔记仍可编辑。"
      return
    }
    do {
      stopPlayback()
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(
        .playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothHFP])
      try session.setActive(true)
      let url = FileManager.default.temporaryDirectory.appendingPathComponent(
        "voice-\(UUID().uuidString).m4a")
      let audio = try AVAudioRecorder(
        url: url,
        settings: [
          AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44100.0, AVNumberOfChannelsKey: 1,
          AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ])
      audio.delegate = self
      audio.isMeteringEnabled = true
      guard audio.prepareToRecord(), audio.record() else {
        throw NSError(
          domain: "NotesAudio", code: 1, userInfo: [NSLocalizedDescriptionKey: "录音设备暂时不可用，请重新尝试。 "])
      }
      recorder = audio
      audioURL = url
      seconds = 0
      playbackPosition = 0
      playbackDuration = 0
      completed = false
      state = .recording
      waveform = Array(repeating: 0.08, count: 32)
      startTimer()
    } catch {
      state = .idle
      self.error = error.localizedDescription
    }
  }
  func togglePause() {
    if state == .recording {
      recorder?.pause()
      state = .paused
    } else if state == .paused {
      do {
        try AVAudioSession.sharedInstance().setActive(true)
        guard recorder?.record() == true else {
          throw NSError(
            domain: "NotesAudio", code: 2,
            userInfo: [NSLocalizedDescriptionKey: "无法继续录音，请保存已有录音后重试。 "])
        }
        state = .recording
      } catch { self.error = error.localizedDescription }
    }
  }
  func interrupt() {
    if state == .recording {
      recorder?.pause()
      state = .paused
      error = "录音被系统中断，已有内容仍保留。准备好后可以继续或结束。"
    }
    stopPlayback()
  }
  func stop() {
    guard state == .recording || state == .paused else { return }
    seconds = recorder?.currentTime ?? seconds
    recorder?.stop()
    finishCapture()
  }
  private func finishCapture() {
    guard !completed else { return }
    completed = true
    timer?.invalidate()
    timer = nil
    state = .ready
    if seconds < 0.1 { error = "录音过短，请重新录制。" }
    onFinish?()
  }
  func load(url: URL, duration: Double) {
    stopPlayback()
    recorder?.stop()
    timer?.invalidate()
    audioURL = url
    seconds = duration
    playbackPosition = 0
    playbackDuration = 0
    state = .ready
    completed = true
    if let player = try? AVAudioPlayer(contentsOf: url) { playbackDuration = player.duration }
  }
  func reset() {
    captureGeneration = UUID()
    stopPlayback()
    recorder?.stop()
    recorder = nil
    timer?.invalidate()
    timer = nil
    audioURL = nil
    seconds = 0
    playbackPosition = 0
    playbackDuration = 0
    state = .idle
    completed = false
    error = nil
    isSample = false
    waveform = Array(repeating: 0.08, count: 32)
  }
  func togglePlayback() {
    if playing {
      player?.pause()
      playing = false
      return
    }
    guard let url = audioURL else { return }
    do {
      try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
      try AVAudioSession.sharedInstance().setActive(true)
      if player == nil {
        let value = try AVAudioPlayer(contentsOf: url)
        value.delegate = self
        value.currentTime = playbackPosition
        player = value
        playbackDuration = value.duration
      }
      guard player?.play() == true else {
        throw NSError(
          domain: "NotesAudio", code: 3,
          userInfo: [NSLocalizedDescriptionKey: "无法播放这段录音，请确认文件仍在设备中。 "])
      }
      playing = true
      startTimer()
    } catch { self.error = error.localizedDescription }
  }
  func seek(_ position: Double) {
    player?.currentTime = max(0, min(playbackDuration, position))
    playbackPosition = position
  }
  private func stopPlayback() {
    player?.stop()
    player = nil
    playing = false
  }
  private func startTimer() {
    timer?.invalidate()
    timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
      Task { @MainActor [weak self] in self?.tick() }
    }
  }
  private func tick() {
    if state == .recording, let recorder {
      recorder.updateMeters()
      seconds = recorder.currentTime
      let value = min(1, max(0.08, Double(pow(10, recorder.averagePower(forChannel: 0) / 40))))
      waveform.removeFirst()
      waveform.append(value)
      if seconds >= 600
        || ((try? recorder.url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) >= 20 * 1024
          * 1024
      {
        stop()
      }
    }
    if playing { playbackPosition = player?.currentTime ?? 0 }
  }
  #if DEBUG
  // A file fixture for recorder persistence tests; no production UI calls this helper.
  func makeSample() {
    reset()
    isSample = true
    do {
      let sampleRate: UInt32 = 16000
      let count: UInt32 = sampleRate * 8
      let bytes = count * 2
      var data = Data()
      func appendString(_ text: String) { data.append(contentsOf: text.utf8) }
      func append32(_ value: UInt32) {
        var value = value.littleEndian
        withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
      }
      func append16(_ value: UInt16) {
        var value = value.littleEndian
        withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
      }
      appendString("RIFF")
      append32(36 + bytes)
      appendString("WAVEfmt ")
      append32(16)
      append16(1)
      append16(1)
      append32(sampleRate)
      append32(sampleRate * 2)
      append16(2)
      append16(16)
      appendString("data")
      append32(bytes)
      data.append(Data(repeating: 0, count: Int(bytes)))
      let url = FileManager.default.temporaryDirectory.appendingPathComponent(
        "sample-\(UUID().uuidString).wav")
      try data.write(to: url, options: .atomic)
      audioURL = url
      seconds = 8
      state = .ready
      completed = true
      onFinish?()
    } catch { self.error = error.localizedDescription }
  }
  #endif
  func shutdown() {
    captureGeneration = UUID()
    if state == .requesting { state = .idle }
    stop()
    onFinish = nil
    stopPlayback()
    timer?.invalidate()
    timer = nil
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
  }
  nonisolated func audioRecorderDidFinishRecording(
    _ recorder: AVAudioRecorder, successfully flag: Bool
  ) {
    Task { @MainActor [weak self] in
      if !flag { self?.error = "录音意外结束，已有文件仍保留，请回放确认并重试保存。" }
      self?.finishCapture()
    }
  }
  nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
    let message = error?.localizedDescription ?? "录音编码出错。"
    Task { @MainActor [weak self] in
      self?.error = message
      self?.finishCapture()
    }
  }
  nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
    Task { @MainActor [weak self] in
      self?.playing = false
      self?.playbackPosition = 0
    }
  }
}

struct NotesVoiceNoteView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.dismiss) private var dismiss
  @Environment(\.scenePhase) private var scenePhase
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  var existingNote: StudyNote?
  var embedded = false
  var closeRequest = 0
  var onClose: (() -> Void)?
  @State private var audio = NotesAudioController()
  @State private var currentNote: StudyNote?
  @State private var annotation = ""
  @State private var organized = ""
  @State private var phase = "idle"
  @State private var saving = false
  @State private var saved = false
  @State private var saveError: String?
  @State private var showReset = false
  @State private var initialized = false
  @State private var captureAnchor: NoteAnchor?
  @State private var capturePosition: UnitPointValue?
  @State private var captureTitle = ""
  @State private var voiceWidth: CGFloat = 834
  @State private var studioTask: Task<Void, Never>?
  @State private var studioError: String?
  @State private var usedOnDeviceRecognition = false
  @State private var recordingGeneration = UUID()
  @State private var generateOnFinish = false
  @State private var active = false
  private var anchor: NoteAnchor? { captureAnchor ?? currentNote?.anchor }
  private var working: Bool { studioTask != nil }
  private var linkedChapterTitle: String {
    if let anchor,
      let chapter = store.sources.first(where: { $0.id == anchor.bookID })?.chapters.first(where: {
        $0.id == anchor.chapterID
      }) {
      return chapter.source_title.replacingOccurrences(
        of: "^第\\s*[一二三四五六七八九十0-9]+\\s*[章节]\\s*", with: "", options: .regularExpression)
    }
    return store.activeLesson?.title ?? "自由语音笔记"
  }
  private var title: String {
    switch audio.state {
    case .recording: "正在听你说…"
    case .paused: "录音已暂停"
    case .ready: saved ? "原始录音已保存" : "正在保存录音"
    default: "说出你的想法，记成一份笔记"
    }
  }
  private var progressTitle: String {
    switch phase {
    case "waitingNetwork": "正在选择语音识别方式…"
    case "recognizingLocally": "正在本机识别中文语音…"
    case "uploading": "正在准备录音…"
    case "transcribing": "正在理解录音内容…"
    case "restoring": "正在读取生成结果…"
    default: "DeepSeek 正在整理笔记…"
    }
  }
  var body: some View {
    VStack(spacing: 0) {
      if !embedded { NotesPageHeader(title: "语音笔记", back: { finishAndClose() }) }
      ScrollView {
        VStack(alignment: .leading, spacing: 12) {
          if !embedded {
            BCCard {
              VStack(alignment: .leading, spacing: 10) {
                NotesBadge(title: "原始录音保存在本机")
                HStack(spacing: 10) {
                  BCGlyph("BookOpenCheck", color: DS.primary).frame(width: 20, height: 20)
                  VStack(alignment: .leading, spacing: 3) {
                    Text(linkedChapterTitle).font(DS.font(16, weight: .bold))
                    Text(anchor.map { "教材第 \($0.printedPage ?? $0.page) 页" } ?? "自由语音笔记")
                      .font(DS.font(12)).foregroundStyle(DS.muted)
                  }
                }
                if let quote = anchor?.quote { NotesQuote(quote: quote, rounded: true) }
              }.padding(.bottom, 3)
            }
          } else {
            HStack {
              Text("语音批注").font(DS.font(18, weight: .bold))
              Spacer()
              Button("关闭") { finishAndClose() }
            }.padding(.horizontal, 4)
          }
          if store.isPad && !embedded {
            HStack(alignment: .top, spacing: 16) {
              recorderCard.frame(width: max(300, (voiceWidth - 80) * 0.54))
              results.frame(maxWidth: .infinity)
            }
          } else {
            recorderCard
            results
          }
        }.padding(.horizontal, embedded ? 16 : store.isPad ? 32 : 16)
          .padding(.top, embedded ? 16 : store.isPad ? 21 : 12)
          .padding(.bottom, 20).frame(maxWidth: 1120).frame(maxWidth: .infinity)
      }.accessibilityIdentifier("voice.scroll")
    }.background(
      GeometryReader { geometry in
        Color.clear.onAppear { voiceWidth = geometry.size.width }.onChange(of: geometry.size.width) {
          _, value in voiceWidth = value
        }
      }
    )
    .onAppear { active = true }
    .task { initialize() }
    .onChange(of: scenePhase) { _, phase in if phase != .active { audio.interrupt() } }
    .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)) {
      _ in audio.interrupt()
    }
    .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)) { notification in
      if let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
        reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue { audio.interrupt() }
    }
    .onDisappear {
      active = false
      generateOnFinish = false
      studioTask?.cancel()
      audio.shutdown()
    }
    .onChange(of: store.cloudState.epoch) { _, _ in
      generateOnFinish = false
      recordingGeneration = UUID()
      studioTask?.cancel()
      studioTask = nil
      audio.onFinish = nil
      audio.shutdown()
      currentNote = nil
      organized = ""
      annotation = ""
      saved = false
      phase = "idle"
    }
    .onChange(of: closeRequest) { _, _ in finishAndClose() }
    .interactiveDismissDisabled(audio.state == .recording || audio.state == .paused || saving
      || (!saved && audio.audioURL != nil))
    .alert("重新录制？", isPresented: $showReset) {
      Button("取消", role: .cancel) {}
      Button("重新录制", role: .destructive) {
        generateOnFinish = false
        studioTask?.cancel()
        studioTask = nil
        recordingGeneration = UUID()
        audio.reset()
        saved = false
        organized = ""
        annotation = ""
        phase = "idle"
        currentNote = nil
        studioError = nil
      }
    } message: { Text("已保存的旧录音和笔记仍可在笔记列表中找到。") }
  }
  @ViewBuilder private var recorderCard: some View {
    if embedded { recorderContent } else { BCCard { recorderContent } }
  }
  private var recorderContent: some View {
    VStack(spacing: embedded ? 10 : 14) {
      HStack(alignment: .center, spacing: embedded ? 2 : 4) {
        ForEach(Array(audio.waveform.enumerated()), id: \.offset) { _, value in
          Capsule().fill(LinearGradient(colors: [Color(hex: 0x71c9f2), DS.primary],
            startPoint: .top, endPoint: .bottom))
            .frame(width: embedded ? 2 : 4, height: max(4, value * (embedded ? 36 : 90)))
        }
      }.frame(maxWidth: .infinity).frame(height: embedded ? 64 : store.isPad ? 146 : 110)
        .background(RadialGradient(colors: [DS.primary.opacity(0.14), Color(hex: 0xf7f4ff)],
          center: .center, startRadius: 0, endRadius: 160), in: RoundedRectangle(cornerRadius: 18))
        .accessibilityLabel("实时录音波形")
        .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: audio.waveform)
      Text(formatTime(audio.seconds)).font(DS.font(embedded ? 18 : 20, weight: .bold))
        .frame(minHeight: 29).monospacedDigit().foregroundStyle(DS.primary)
      Text(title).font(DS.font(embedded ? 16 : 20, weight: .bold)).frame(minHeight: 29)
        .multilineTextAlignment(.center)
      Text(audio.state == .ready
        ? "录音可随时回放，整理后的笔记会自动保存。"
        : "最长 10 分钟。结束并生成笔记后，DeepSeek 会结合教材整理你的想法。")
        .font(DS.font(embedded ? 13 : 16)).foregroundStyle(DS.muted)
        .lineSpacing(5).multilineTextAlignment(.center)
      controls
      if saving { ProgressView("正在保存录音与笔记…").font(DS.font(13)) }
      if let error = saveError ?? audio.error {
        Text(error).font(DS.font(13)).foregroundStyle(DS.danger)
        if saveError != nil {
          BCButton("重试保存", style: .secondary) { Task { _ = await saveCurrent() } }.disabled(saving)
        }
      }
      if audio.state == .ready && organized.isEmpty && !working {
        Text("按当前网络选择语音识别方式；选择本机识别时，只将识别文字交给 DeepSeek 整理，录音不上传。")
          .font(DS.font(12)).foregroundStyle(DS.muted)
        BCButton(studioError == nil ? "生成笔记" : "重试生成笔记", icon: "Sparkles") {
          generateNote(retryFailed: studioError != nil)
        }.disabled(!saved || saving).accessibilityIdentifier("voice.generate")
      }
      if let studioError { Text(studioError).font(DS.font(13)).foregroundStyle(DS.danger) }
      if studioError != nil, usedOnDeviceRecognition, !working,
        store.processingPolicy.connection == .cellular {
        Button("重新选择处理方式") {
          store.processingPolicy.reconsiderCellularChoice()
          generateNote(retryFailed: true)
        }.font(DS.font(14, weight: .semibold)).foregroundStyle(DS.primary)
          .accessibilityIdentifier("voice.reconsiderProcessing")
      }
      if working {
        ProgressView(progressTitle).font(DS.font(14)).foregroundStyle(DS.primary)
          .accessibilityIdentifier("voice.generating")
        Text("你可以离开此页，稍后回来继续查看。")
          .font(DS.font(12)).foregroundStyle(DS.muted)
      }
    }.padding(2).padding(.bottom, embedded ? 0 : 7)
  }
  @ViewBuilder private var controls: some View {
    HStack(spacing: 14) {
      switch audio.state {
      case .recording, .paused:
        roundControl(audio.state == .paused ? "Play" : "Pause",
          title: audio.state == .paused ? "继续录音" : "暂停录音", main: false) { audio.togglePause() }
        VStack(spacing: 5) {
          roundControl("Square", title: "结束并生成笔记", main: true, stop: true) {
            generateOnFinish = true
            audio.stop()
          }
          Text("结束并生成笔记").font(DS.font(12)).foregroundStyle(DS.primary)
        }
      case .ready:
        roundControl("RotateCcw", title: "重新录制", main: false) { showReset = true }.disabled(working || saving)
        roundControl(audio.playing ? "Pause" : "Play",
          title: audio.playing ? "暂停回放" : "播放录音", main: false) { audio.togglePlayback() }
        if audio.playbackDuration > 0 {
          VStack(spacing: 3) {
            Slider(value: Binding(get: { audio.playbackPosition }, set: { audio.seek($0) }),
              in: 0...max(0.01, audio.playbackDuration)).tint(DS.primary)
              .accessibilityLabel("录音回放位置")
            Text("\(formatTime(audio.playbackPosition)) / \(formatTime(audio.playbackDuration))")
              .font(DS.font(10)).foregroundStyle(DS.muted)
          }
        }
      case .requesting: ProgressView("正在请求麦克风权限…").font(DS.font(13)).frame(height: 68)
      case .idle: roundControl("Mic2", title: "开始录音", main: true) { Task { await audio.start() } }
      }
    }.frame(maxWidth: .infinity).frame(minHeight: embedded ? 56 : 68)
  }
  private func roundControl(_ icon: String, title: String, main: Bool, stop: Bool = false,
    action: @escaping () -> Void) -> some View {
    Button(action: action) {
      NotesGlyph(name: icon, size: main ? (embedded ? 24 : 30) : (embedded ? 20 : 24))
        .foregroundStyle(main ? .white : DS.primary)
        .frame(width: main ? (embedded ? 56 : 68) : (embedded ? 44 : 52),
          height: main ? (embedded ? 56 : 68) : (embedded ? 44 : 52))
        .background(main ? (stop ? Color(hex: 0xe04462) : DS.primary) : DS.primarySoft, in: Circle())
        .shadow(color: main ? DS.primary.opacity(0.22) : .clear, radius: 11, y: 6)
    }.buttonStyle(.plain).accessibilityLabel(title)
  }
  @ViewBuilder private var results: some View {
    if !organized.isEmpty {
      BCCard {
        VStack(alignment: .leading, spacing: 14) {
          BCPill(saving ? "正在保存" : saveError == nil ? "已保存" : "待保存", color: DS.mint)
          Text("整理后的笔记").font(DS.font(20, weight: .bold))
          Text(organized).font(DS.font(16)).lineSpacing(7).textSelection(.enabled)
            .accessibilityIdentifier("voice.organized")
          Text("原始录音已保留，可随时回放。")
            .font(DS.font(12)).foregroundStyle(DS.muted)
          if !embedded { BCButton("完成", style: .secondary) { finishAndClose() } }
        }
      }
    }
  }
  private func initialize() {
    guard !initialized else { return }
    initialized = true
    let record = existingNote ?? NotesCaptureContext.existingNoteID.flatMap { id in
      store.notes.first { $0.id == id }
    }
    NotesCaptureContext.existingNoteID = nil
    NotesCaptureContext.pendingKind = nil
    currentNote = record
    let target = store.sourceTarget.flatMap { $0.bookID == store.selectedBookID ? $0 : nil }
    var freshAnchor = store.makeAnchor(page: target?.page ?? store.activeLesson?.page_start ?? 1)
    freshAnchor.printedPage = target?.printedPage
    captureAnchor = record?.anchor ?? (store.currentSource == nil ? nil : freshAnchor)
    capturePosition = record?.position ?? NotesCaptureContext.position
    captureTitle = record?.title ?? store.activeLesson?.title ?? store.chapterTitle
    if let record {
      annotation = record.body
      organized = record.organizedText ?? ""
      phase = organized.isEmpty ? "idle" : "complete"
      if let path = record.audioPath {
        audio.load(url: store.attachmentURL(path), duration: record.duration)
        saved = true
      }
    }
    audio.onFinish = {
      let shouldGenerate = generateOnFinish
      let generation = recordingGeneration, epoch = store.cloudState.epoch
      generateOnFinish = false
      Task {
        if await saveCurrent(), shouldGenerate, active,
          generation == recordingGeneration, epoch == store.cloudState.epoch { generateNote() }
      }
    }
    if organized.isEmpty && saved { restoreGeneratedNote() }
  }
  @discardableResult private func saveCurrent() async -> Bool {
    guard let url = audio.audioURL else { return true }
    guard !saving else { return false }
    saving = true
    let generation = recordingGeneration, epoch = store.cloudState.epoch
    saveError = nil
    var note = currentNote ?? StudyNote(kind: .voice,
      title: "\(captureTitle.isEmpty ? "语音笔记" : captureTitle) · 语音", body: annotation)
    note = NotesAnnotationRecord.mergingConversation(draft: note,
      stored: store.notes.first { $0.id == note.id })
    note.body = annotation
    note.anchor = anchor
    note.duration = audio.seconds
    note.updatedAt = .now
    // Local recognition text is retained privately with its recording digest.
    // Neither local nor legacy raw transcripts are displayed in this view.
    note.organizedText = organized.isEmpty ? nil : organized
    note.position = capturePosition
    do {
      if note.audioPath == nil || !saved {
        let bytes = try Data(contentsOf: url)
        guard !bytes.isEmpty else { throw CloudError(status: 422, message: "录音文件为空，请重新录制。") }
        note.audioPath = try await store.saveAttachment(bytes,
          extension: url.pathExtension.isEmpty ? "m4a" : url.pathExtension)
      }
      guard generation == recordingGeneration, epoch == store.cloudState.epoch else { saving = false; return false }
      try await store.saveNote(note)
      guard generation == recordingGeneration, epoch == store.cloudState.epoch else { saving = false; return false }
      currentNote = note
      saved = true
      saving = false
      return true
    } catch {
      saveError = "保存失败，原始录音仍保留，可重试：\(error.localizedDescription)"
      saving = false
      return false
    }
  }
  private func finishAndClose() {
    generateOnFinish = false
    studioTask?.cancel()
    audio.onFinish = nil
    audio.stop()
    Task {
      while saving { try? await Task.sleep(for: .milliseconds(50)) }
      if await saveCurrent() {
        studioTask?.cancel()
        if let onClose { onClose() } else if embedded { dismiss() } else { store.back() }
      }
    }
  }
  private func generateNote(retryFailed: Bool = false) {
    guard studioTask == nil else { return }
    studioError = nil
    phase = "waitingNetwork"
    let generation = recordingGeneration, epoch = store.cloudState.epoch
    studioTask = Task { @MainActor in
      defer { if generation == recordingGeneration, epoch == store.cloudState.epoch { studioTask = nil } }
      do {
        guard await saveCurrent(), var note = currentNote, let path = note.audioPath else {
          throw CloudError(status: 409, message: "请先保存原始录音，再重试生成笔记。")
        }
        try Task.checkCancellation()
        guard active, generation == recordingGeneration, epoch == store.cloudState.epoch else { throw CancellationError() }
        let route = await store.processingPolicy.route(for: .asr)
        try Task.checkCancellation()
        guard active, generation == recordingGeneration, epoch == store.cloudState.epoch,
          currentNote?.id == note.id else { throw CancellationError() }
        if route == .cancelled { throw CancellationError() }
        usedOnDeviceRecognition = route == .onDevice
        guard let api = store.cloud, store.cloudState.authenticated else {
          throw CloudError(status: 503, message: "学习服务尚未连接。录音已保存在本机，连接后可生成笔记。")
        }
        let service = StudioService(api: api)
        let onProgress: @MainActor @Sendable (String) -> Void = { next in
          guard generation == recordingGeneration, epoch == store.cloudState.epoch else { return }
          phase = next
        }
        let fileURL = store.attachmentURL(path)
        let result: (remoteNote: StudioNote, job: StudioJob)
        switch route {
        case .cancelled: throw CancellationError()
        case .existing:
          phase = "uploading"
          result = try await service.generateVoice(note: note, fileURL: fileURL,
            retryFailed: retryFailed, onProgress: onProgress)
        case .onDevice:
          phase = "recognizingLocally"
          let hash = try recordingDigest(fileURL)
          let text: String
          if let existing = note.localSpeechTranscript(audioSHA256: hash) { text = existing }
          else {
            text = try await AppleSpeechTranscriber().transcribe(fileURL: fileURL)
            try Task.checkCancellation()
            guard active, generation == recordingGeneration, epoch == store.cloudState.epoch,
              currentNote?.id == note.id else { throw CancellationError() }
            note.transcript = text
            note.voiceRecognitionSource = "apple-speech-on-device"
            note.voiceRecognitionAudioSHA256 = hash
            note.updatedAt = .now
            try await store.saveNote(note)
            try Task.checkCancellation()
            guard active, generation == recordingGeneration, epoch == store.cloudState.epoch else { throw CancellationError() }
            currentNote = note
          }
          phase = "organizing"
          result = try await service.generateVoiceFromTranscript(note: note, transcript: text,
            retryFailed: retryFailed, onProgress: onProgress)
        }
        try Task.checkCancellation()
        guard generation == recordingGeneration, epoch == store.cloudState.epoch,
          currentNote?.id == note.id else { return }
        try await service.accept(note: result.remoteNote, job: result.job)
        guard generation == recordingGeneration, epoch == store.cloudState.epoch else { return }
        organized = result.job.polished
        phase = "complete"
        _ = await saveCurrent()
      } catch is CancellationError {
        if generation == recordingGeneration { phase = organized.isEmpty ? "idle" : "complete" }
      } catch {
        guard generation == recordingGeneration, epoch == store.cloudState.epoch else { return }
        studioError = error.localizedDescription
        phase = organized.isEmpty ? "idle" : "complete"
      }
    }
  }
  private func restoreGeneratedNote() {
    guard studioTask == nil, let api = store.cloud, store.cloudState.authenticated,
      let note = currentNote, let path = note.audioPath else { return }
    phase = "restoring"
    let generation = recordingGeneration, epoch = store.cloudState.epoch
    studioTask = Task { @MainActor in
      defer { if generation == recordingGeneration, epoch == store.cloudState.epoch { studioTask = nil } }
      do {
        let service = StudioService(api: api)
        let onProgress: @MainActor @Sendable (String) -> Void = { next in
          guard generation == recordingGeneration, epoch == store.cloudState.epoch else { return }
          phase = next
        }
        let fileURL = store.attachmentURL(path)
        let restored: (remoteNote: StudioNote, job: StudioJob)?
        if note.voiceRecognitionSource == "apple-speech-on-device" {
          guard let text = note.localSpeechTranscript(audioSHA256: try recordingDigest(fileURL)) else {
            phase = "idle"; return
          }
          restored = try await service.restoreVoiceFromTranscript(note: note, transcript: text, onProgress: onProgress)
        } else {
          restored = try await service.restoreVoice(note: note, fileURL: fileURL, onProgress: onProgress)
        }
        try Task.checkCancellation()
        guard generation == recordingGeneration, epoch == store.cloudState.epoch else { throw CancellationError() }
        guard let result = restored else { phase = "idle"; return }
        try Task.checkCancellation()
        guard generation == recordingGeneration, epoch == store.cloudState.epoch,
          currentNote?.id == note.id else { return }
        organized = result.job.polished
        phase = "complete"
        _ = await saveCurrent()
      } catch is CancellationError {
        if generation == recordingGeneration { phase = organized.isEmpty ? "idle" : "complete" }
      } catch {
        guard generation == recordingGeneration, epoch == store.cloudState.epoch else { return }
        studioError = error.localizedDescription
        phase = organized.isEmpty ? "idle" : "complete"
      }
    }
  }
  private func recordingDigest(_ url: URL) throws -> String {
    let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
    guard values.isRegularFile == true, let size = values.fileSize, size > 0, size <= 20 * 1024 * 1024 else {
      throw CloudError(status: 413, message: "原始录音暂不可读取或超过 20 MB，未上传任何录音。")
    }
    return SHA256.hash(data: try Data(contentsOf: url, options: .mappedIfSafe))
      .map { String(format: "%02x", $0) }.joined()
  }
  private func formatTime(_ value: Double) -> String {
    let value = max(0, Int(value))
    return String(format: "%02d:%02d", value / 60, value % 60)
  }
}
