import AVFoundation
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

  func start() async {
    error = nil
    state = .requesting
    isSample = false
    let allowed = await withCheckedContinuation { continuation in
      AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
    }
    guard allowed else {
      state = .idle
      error = "未获得麦克风权限。请在系统设置中允许录音，或使用示例录音。"
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
    if seconds < 0.1 { error = "录音过短，请重新录制或使用示例录音。" }
    onFinish?()
  }
  func load(url: URL, duration: Double) {
    stopPlayback()
    recorder?.stop()
    timer?.invalidate()
    audioURL = url
    seconds = duration
    state = .ready
    completed = true
    if let player = try? AVAudioPlayer(contentsOf: url) { playbackDuration = player.duration }
  }
  func reset() {
    stopPlayback()
    recorder?.stop()
    recorder = nil
    timer?.invalidate()
    timer = nil
    audioURL = nil
    seconds = 0
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
  func shutdown() {
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
  @State private var transcript = ""
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
  private var anchor: NoteAnchor? { captureAnchor ?? currentNote?.anchor }
  private var linkedChapterTitle: String {
    if let anchor,
      let chapter = store.sources.first(where: { $0.id == anchor.bookID })?.chapters.first(where: {
        $0.id == anchor.chapterID
      })
    {
      return chapter.source_title.replacingOccurrences(
        of: "^第\\s*[一二三四五六七八九十0-9]+\\s*[章节]\\s*", with: "", options: .regularExpression)
    }
    return store.activeLesson?.title ?? "自由语音笔记"
  }
  private var title: String {
    switch audio.state {
    case .recording: "正在听你说…"
    case .paused: "录音已暂停"
    case .ready: saved ? "这段想法已经记下" : "录音尚未保存到设备"
    default: "录一段语音笔记"
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
                NotesBadge(title: "真实录音 · 本地整理演示")
                HStack(spacing: 10) {
                  BCGlyph("BookOpenCheck", color: DS.primary).frame(width: 20, height: 20)
                  VStack(alignment: .leading, spacing: 3) {
                    Text(linkedChapterTitle).font(DS.font(16, weight: .bold))
                    Text(anchor.map { "教材第 \($0.printedPage ?? $0.page) 页" } ?? "可在整理前补充章节").font(
                      DS.font(12)
                    ).foregroundStyle(DS.muted)
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
        }.padding(.horizontal, embedded ? 16 : store.isPad ? 32 : 16).padding(
          .top, embedded ? 16 : store.isPad ? 21 : 12
        ).padding(.bottom, 20).frame(maxWidth: 1120).frame(maxWidth: .infinity)
      }.accessibilityIdentifier("voice.scroll")
    }.background(
      GeometryReader { geometry in
        Color.clear.onAppear { voiceWidth = geometry.size.width }.onChange(of: geometry.size.width)
        { _, value in voiceWidth = value }
      }
    )
    .task { initialize() }
    .onChange(of: scenePhase) { _, phase in if phase != .active { audio.interrupt() } }
    .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)) {
      _ in audio.interrupt()
    }
    .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)) {
      notification in
      if let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
        reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue
      {
        audio.interrupt()
      }
    }
    .onDisappear { audio.shutdown() }
    .onChange(of: closeRequest) { _, _ in finishAndClose() }
    .interactiveDismissDisabled(
      audio.state == .recording || audio.state == .paused || saving
        || (!saved && audio.audioURL != nil)
    )
    .alert("重新录制？", isPresented: $showReset) {
      Button("取消", role: .cancel) {}
      Button("重新录制", role: .destructive) {
        audio.reset()
        saved = false
        transcript = ""
        organized = ""
        phase = "idle"
        currentNote = nil
      }
    } message: {
      Text("已保存的旧录音仍可在笔记列表中找到。")
    }
  }
  @ViewBuilder private var recorderCard: some View {
    if embedded { recorderContent } else { BCCard { recorderContent } }
  }
  private var recorderContent: some View {
    VStack(spacing: embedded ? 10 : 14) {
      HStack(alignment: .center, spacing: embedded ? 2 : 4) {
        ForEach(Array(audio.waveform.enumerated()), id: \.offset) { _, value in
          Capsule().fill(
            LinearGradient(
              colors: [Color(hex: 0x71c9f2), DS.primary], startPoint: .top, endPoint: .bottom)
          ).frame(width: embedded ? 2 : 4, height: max(4, value * (embedded ? 36 : 90)))
        }
      }.frame(maxWidth: .infinity).frame(height: embedded ? 64 : store.isPad ? 146 : 110)
        .background(
          RadialGradient(
            colors: [DS.primary.opacity(0.14), Color(hex: 0xf7f4ff)], center: .center,
            startRadius: 0, endRadius: 160), in: RoundedRectangle(cornerRadius: 18)
        )
        .accessibilityLabel("实时录音波形").animation(
          reduceMotion ? nil : .easeOut(duration: 0.12), value: audio.waveform)
      Text(formatTime(audio.seconds)).font(DS.font(embedded ? 18 : 20, weight: .bold)).frame(
        minHeight: 29
      ).monospacedDigit().foregroundStyle(DS.primary)
      Text(title).font(DS.font(embedded ? 16 : 20, weight: .bold)).frame(minHeight: 29)
        .multilineTextAlignment(.center)
      Text(audio.state == .ready ? "先回放确认，再生成可编辑逐字稿。" : "最长 10 分钟；原始录音会与整理版分别保留。")
        .font(DS.font(embedded ? 13 : 20)).foregroundStyle(DS.muted).lineSpacing(5)
        .multilineTextAlignment(.center)
      if audio.isSample { BCPill("示例静音录音 · 非真实语音", color: DS.warning) }
      if let error = saveError ?? audio.error {
        VStack(alignment: .leading, spacing: 8) {
          Text("录音提示").font(DS.font(14, weight: .bold))
          Text(error).font(DS.font(13))
          HStack {
            if saveError != nil {
              BCButton("重试保存", style: .secondary) { Task { _ = await saveCurrent() } }
            } else {
              BCButton("重新尝试", style: .secondary) { Task { await audio.start() } }
            }
            BCButton("使用示例录音", style: .text) { audio.makeSample() }
          }
        }.padding(12).foregroundStyle(DS.danger).background(
          Color(hex: 0xfff0f3), in: RoundedRectangle(cornerRadius: 14))
      }
      controls
      if audio.state == .ready {
        if saving { ProgressView("正在保存原始录音…").font(DS.font(13)) }
        if phase == "idle" {
          BCButton("完成并整理", icon: "Sparkles") { transcribe() }.disabled(!saved || saving)
        }
        BCTextField("补充这段录音的想法", text: $annotation)
        BCButton("保存批注文字", style: .text) { Task { _ = await saveCurrent() } }.disabled(saving)
      }
      if audio.state == .idle && audio.error == nil {
        Button {
          audio.makeSample()
        } label: {
          Text("使用示例录音").font(DS.font(embedded ? 13 : 20, weight: .bold)).foregroundStyle(
            DS.primary
          ).frame(minHeight: 48)
        }
      }
      if phase == "transcribing" || phase == "organizing" {
        ProgressView(phase == "transcribing" ? "正在载入演示逐字稿…" : "正在整理笔记…").font(DS.font(13))
          .foregroundStyle(DS.primary)
      }
    }.padding(2).padding(.bottom, embedded ? 0 : 7)
  }
  @ViewBuilder private var controls: some View {
    HStack(spacing: 14) {
      switch audio.state {
      case .recording, .paused:
        roundControl(
          audio.state == .paused ? "Play" : "Pause",
          title: audio.state == .paused ? "继续录音" : "暂停录音", main: false
        ) { audio.togglePause() }
        roundControl("Square", title: "结束录音", main: true, stop: true) { audio.stop() }
      case .ready:
        roundControl("RotateCcw", title: "重新录制", main: false) { showReset = true }
        roundControl(
          audio.playing ? "Pause" : "Play", title: audio.playing ? "暂停回放" : "播放录音", main: false
        ) { audio.togglePlayback() }
        if audio.playbackDuration > 0 {
          VStack(spacing: 3) {
            Slider(
              value: Binding(get: { audio.playbackPosition }, set: { audio.seek($0) }),
              in: 0...max(0.01, audio.playbackDuration)
            ).tint(DS.primary).accessibilityLabel("录音回放位置")
            Text("\(formatTime(audio.playbackPosition)) / \(formatTime(audio.playbackDuration))")
              .font(DS.font(10)).foregroundStyle(DS.muted)
          }
        }
      case .requesting: ProgressView("正在请求麦克风权限…").font(DS.font(13)).frame(height: 68)
      case .idle: roundControl("Mic2", title: "开始录音", main: true) { Task { await audio.start() } }
      }
    }.frame(maxWidth: .infinity).frame(minHeight: embedded ? 56 : 68)
  }
  private func roundControl(
    _ icon: String, title: String, main: Bool, stop: Bool = false, action: @escaping () -> Void
  ) -> some View {
    Button(action: action) {
      NotesGlyph(name: icon, size: main ? (embedded ? 24 : 30) : (embedded ? 20 : 24))
        .foregroundStyle(main ? .white : DS.primary).frame(
          width: main ? (embedded ? 56 : 68) : (embedded ? 44 : 52),
          height: main ? (embedded ? 56 : 68) : (embedded ? 44 : 52)
        ).background(
          main ? (stop ? Color(hex: 0xe04462) : DS.primary) : DS.primarySoft, in: Circle()
        ).shadow(color: main ? DS.primary.opacity(0.22) : .clear, radius: 11, y: 6)
    }.buttonStyle(.plain).accessibilityLabel(title)
  }
  private var results: some View {
    VStack(spacing: 16) {
      if phase == "needs_confirmation" {
        BCCard {
          VStack(alignment: .leading, spacing: 14) {
            BCPill("请确认逐字稿", color: Color(hex: 0x3c9fcc))
            Text("AI 不会替你猜测原话").font(DS.font(20, weight: .bold))
            Text("本 Demo 使用固定转写夹具，请按刚才的真实录音修正后再继续。").font(DS.font(13)).foregroundStyle(DS.muted)
            BCTextEditor(text: $transcript, placeholder: "请修正逐字稿").frame(minHeight: 190)
              .accessibilityIdentifier("voice.transcript")
            BCButton("确认并继续整理", icon: "Save") { organize() }.disabled(
              transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || saving)
          }
        }
      }
      if phase == "complete" && !organized.isEmpty {
        BCCard {
          VStack(alignment: .leading, spacing: 14) {
            BCPill("独立整理版 · 本地演示", color: DS.mint)
            Text("独立整理版").font(DS.font(20, weight: .bold))
            Text(organized).font(DS.font(14)).lineSpacing(6).textSelection(.enabled)
            if anchor?.bookID == "book_biology_2" { NotesDemoEvidence() }
            Text("原始录音与确认后的逐字稿均已保留，整理版不会覆盖它们。").font(DS.font(12)).foregroundStyle(DS.muted)
            BCButton("保留整理版", style: .secondary) {
              Task { if await saveCurrent() { store.showToast("整理版已保留") } }
            }
            BCButton("修改逐字稿", style: .text) { phase = "needs_confirmation" }
          }
        }
      }
    }
  }
  private func initialize() {
    guard !initialized else { return }
    initialized = true
    let record =
      existingNote
      ?? NotesCaptureContext.existingNoteID.flatMap { id in store.notes.first { $0.id == id } }
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
      transcript = record.transcript ?? ""
      annotation = record.body
      organized = record.organizedText ?? ""
      phase = !organized.isEmpty ? "complete" : !transcript.isEmpty ? "needs_confirmation" : "idle"
      if let path = record.audioPath {
        audio.load(url: store.attachmentURL(path), duration: record.duration)
        saved = true
      }
    }
    audio.onFinish = { Task { _ = await saveCurrent() } }
  }
  @discardableResult private func saveCurrent() async -> Bool {
    guard let url = audio.audioURL else { return true }
    guard !saving else { return false }
    saving = true
    saveError = nil
    var note =
      currentNote
      ?? StudyNote(
        kind: .voice, title: "\(captureTitle.isEmpty ? "语音笔记" : captureTitle) · 语音",
        body: annotation)
    note = NotesAnnotationRecord.mergingConversation(
      draft: note, stored: store.notes.first { $0.id == note.id })
    note.body = annotation
    note.anchor = anchor
    note.duration = audio.seconds
    note.updatedAt = .now
    note.transcript = transcript.isEmpty ? nil : transcript
    note.organizedText = organized.isEmpty ? nil : organized
    note.position = capturePosition
    do {
      if note.audioPath == nil || !saved {
        let bytes = try Data(contentsOf: url)
        guard !bytes.isEmpty else {
          throw NSError(
            domain: "NotesAudio", code: 4, userInfo: [NSLocalizedDescriptionKey: "录音文件为空，请重新录制。 "])
        }
        note.audioPath = try await store.saveAttachment(
          bytes, extension: url.pathExtension.isEmpty ? "m4a" : url.pathExtension)
      }
      try await store.saveNote(note)
      currentNote = note
      saved = true
      saving = false
      return true
    } catch {
      saveError = "录音保存失败，原始文件仍保留，可重试：\(error.localizedDescription)"
      saved = false
      saving = false
      return false
    }
  }
  private func finishAndClose() {
    audio.onFinish = nil
    audio.stop()
    Task {
      while saving { try? await Task.sleep(for: .milliseconds(50)) }
      if await saveCurrent() {
        if let onClose { onClose() } else if embedded { dismiss() } else { store.back() }
      } else {
        audio.onFinish = { Task { _ = await saveCurrent() } }
      }
    }
  }
  private func transcribe() {
    phase = "transcribing"
    Task {
      try? await Task.sleep(for: .milliseconds(500))
      transcript = NotesFixtures.voiceTranscript
      phase = "needs_confirmation"
      _ = await saveCurrent()
    }
  }
  private func organize() {
    phase = "organizing"
    Task {
      guard await saveCurrent() else {
        phase = "needs_confirmation"
        return
      }
      try? await Task.sleep(for: .milliseconds(700))
      organized =
        anchor?.bookID == "book_biology_2"
        ? NotesFixtures.organizedVoice : "## 语音笔记整理（演示）\n\n\(transcript)\n\n请结合教材原文核对以上记录。"
      phase = "complete"
      _ = await saveCurrent()
    }
  }
  private func formatTime(_ value: Double) -> String {
    let value = max(0, Int(value))
    return String(format: "%02d:%02d", value / 60, value % 60)
  }
}

enum NotesFixtures {
  static let voiceTranscript = "我想记住，减数第一次分裂时同源染色体分离，减数第二次分裂时姐妹染色单体分离。这样形成的配子染色体数目会减半。"
  static let recognizedInk = "DNA 在复制时边解旋边复制；减数分裂使配子中的染色体数目减半。"
  static let organizedVoice =
    "## 两次分裂的核心区别\n\n1. 减数第一次分裂：同源染色体彼此分离，染色体数目减半。\n2. 减数第二次分裂：着丝粒分裂，姐妹染色单体彼此分开。\n3. 复习时应先判断题目询问的是“同源染色体”还是“姐妹染色单体”，再确定发生在哪一次分裂。"
  static let organizedInk =
    "## 减数分裂与 DNA 复制\n\n- DNA 在减数分裂开始前完成一次复制，复制过程表现为边解旋边复制。\n- 随后的细胞连续分裂两次，因此最终形成的配子中染色体数目减半。\n- 这条笔记记录的是过程关键词，仍需要结合教材图区分同源染色体与姐妹染色单体。"
}
struct NotesDemoEvidence: View {
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      evidence("教材第 16 页", text: "减数分裂过程中，染色体只复制一次，而细胞连续分裂两次。")
      evidence("教材第 18 页", text: "减数第一次分裂后期同源染色体分离，减数第二次分裂后期姐妹染色单体分开。")
      Text("以上为固定演示核验片段，并非实时识别结果。").font(DS.font(11)).foregroundStyle(DS.muted)
    }
  }
  private func evidence(_ title: String, text: String) -> some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(title).font(DS.font(12, weight: .bold)).foregroundStyle(DS.primary)
      Text(text).font(DS.font(12)).foregroundStyle(DS.secondary)
    }
    .padding(10).frame(maxWidth: .infinity, alignment: .leading).background(
      Color(hex: 0xf3f8ff), in: RoundedRectangle(cornerRadius: 12))
  }
}
