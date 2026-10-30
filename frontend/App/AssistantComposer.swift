import AVFoundation
import SwiftUI

struct AssistantGlassSurface: ViewModifier {
  var radius: CGFloat = 28
  var clear = false
  var tint: Color?
  var interactive = false
  @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
  @Environment(\.colorSchemeContrast) private var contrast

  @ViewBuilder func body(content: Content) -> some View {
    if reduceTransparency || contrast == .increased {
      content.background(tint ?? Color(.secondarySystemBackground),
        in: RoundedRectangle(cornerRadius: radius))
    } else if #available(iOS 26.0, *) {
      content.glassEffect((clear ? Glass.clear.tint(tint) : Glass.regular.tint(tint)).interactive(interactive),
        in: RoundedRectangle(cornerRadius: radius))
        .glassEffectTransition(.identity)
    } else {
      content.background(.regularMaterial, in: RoundedRectangle(cornerRadius: radius))
        .background(tint ?? .clear, in: RoundedRectangle(cornerRadius: radius))
        .overlay {
          RoundedRectangle(cornerRadius: radius).strokeBorder(.white.opacity(0.7), lineWidth: 0.7)
        }
    }
  }
}

struct AssistantComposer: View {
  @Bindable var voice: AssistantVoiceInput
  let busy: Bool
  let placeholder: String
  let prompts: [String]
  var textHorizontalInset: CGFloat = 0
  let send: () -> Void
  @FocusState private var focused: Bool
  @State private var keyboardFocusTask: Task<Void, Never>?
  @State private var preparingKeyboard = false
  @State private var voicePlaceholderHidden = false
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @Environment(\.scenePhase) private var scenePhase

  var body: some View {
    VStack(spacing: 10) {
      if let error = voice.error {
        Text(error).font(.footnote).foregroundStyle(DS.danger)
          .frame(maxWidth: .infinity, alignment: .leading).padding(12)
          .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
          .accessibilityIdentifier("guide-voice-error")
      }
      AssistantComposerLayout(voiceMode: voice.isVoiceMode) {
        textComposer
          .padding(.horizontal, textHorizontalInset)
          .animation(reduceMotion ? nil : .easeInOut(duration: 0.22)) { content in
            content.opacity(voice.isVoiceMode ? 0 : 1)
          }
          .allowsHitTesting(!voice.isVoiceMode)
          .accessibilityHidden(voice.isVoiceMode)
        AssistantVoiceControls(voice: voice,
          onKeyboard: showKeyboard, onClose: { voice.returnToText() })
          .animation(reduceMotion ? nil : .easeInOut(duration: 0.22)) { content in
            content.opacity(voice.isVoiceMode ? 1 : 0)
          }
          .allowsHitTesting(voice.isVoiceMode)
          .accessibilityHidden(!voice.isVoiceMode)
      }
    }
    .onChange(of: scenePhase) { _, phase in
      // Permission prompts also make the scene inactive. Do not cancel them.
      if phase == .background { keyboardFocusTask?.cancel(); voice.interrupt() }
    }
    .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)) {
      notification in
      if let raw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
        AVAudioSession.InterruptionType(rawValue: raw) == .began { voice.interrupt() }
    }
    .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.mediaServicesWereResetNotification)) {
      _ in voice.interrupt()
    }
    .onDisappear { keyboardFocusTask?.cancel(); voice.returnToText() }
  }

  private func showKeyboard() {
    keyboardFocusTask?.cancel()
    preparingKeyboard = true
    voice.returnToText()
    // Focus as soon as the field is mounted, so keyboard and crossfade start together.
    keyboardFocusTask = Task { @MainActor in
      await Task.yield()
      guard !Task.isCancelled, !voice.isVoiceMode else { return }
      focused = true
      preparingKeyboard = false
    }
  }

  private var textComposer: some View {
    HStack(alignment: .bottom, spacing: 6) {
      Menu {
        ForEach(prompts, id: \.self) { prompt in
          Button(prompt) { voice.draft = prompt; focused = true }
        }
      } label: {
        Image(systemName: "plus").font(.system(size: 24, weight: .medium))
          .foregroundStyle(DS.primary).frame(width: 44, height: 44)
      }
      .accessibilityLabel("更多提问方式")
      .accessibilityIdentifier(voice.isVoiceMode ? "guide-more-hidden" : "guide-more")
      .disabled(busy || voice.isVoiceMode)

      TextField("", text: $voice.draft,
        prompt: focused || preparingKeyboard || (voice.isVoiceMode && voicePlaceholderHidden) ? nil :
          Text(placeholder).foregroundColor(DS.secondary), axis: .vertical)
        .font(.custom("BookCourseNotoSansSC-Regular", size: 17, relativeTo: .body))
        .foregroundStyle(DS.text).tint(DS.primary)
        .lineLimit(1...4).padding(.vertical, 11)
        .focused($focused).submitLabel(.send)
        .onSubmit { if voice.hasText && !busy { send() } }
        .accessibilityLabel("向 AI 提问")
        .accessibilityIdentifier(voice.isVoiceMode ? "guide-input-hidden" : "guide-input")
        .disabled(voice.isVoiceMode)

      Button {
        if voice.hasText { send() }
        else {
          keyboardFocusTask?.cancel()
          preparingKeyboard = false
          voicePlaceholderHidden = focused
          focused = false
          voice.enterVoice()
        }
      } label: {
        AssistantMicrophoneOrb(size: 44, symbol: voice.hasText ? "arrow.up" : "mic.fill",
          working: busy, themeFill: true)
      }
      .disabled(busy || voice.isVoiceMode)
      .buttonStyle(.plain)
      .accessibilityLabel(voice.hasText ? "发送给 AI 助手" : "切换到语音输入")
      .accessibilityIdentifier(voice.isVoiceMode ? "guide-input-action-hidden" :
        (voice.hasText ? "guide-send" : "guide-mic"))
    }
    .padding(7).modifier(AssistantGlassSurface(clear: true))
    .contentShape(RoundedRectangle(cornerRadius: 28))
    .onTapGesture { if !voice.isVoiceMode { focused = true } }
  }
}

/// Both surfaces keep their identity and bottom anchor while only opacity animates.
/// The active surface alone reserves space for the conversation above it.
private struct AssistantComposerLayout: Layout {
  let voiceMode: Bool

  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    subviews[voiceMode ? 1 : 0].sizeThatFits(
      ProposedViewSize(width: proposal.width, height: nil))
  }

  func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
    subviews: Subviews, cache: inout ()) {
    for subview in subviews {
      let size = subview.sizeThatFits(ProposedViewSize(width: bounds.width, height: nil))
      subview.place(at: CGPoint(x: bounds.midX, y: bounds.maxY), anchor: .bottom,
        proposal: ProposedViewSize(width: bounds.width, height: size.height))
    }
  }
}

private struct AssistantMicrophoneOrb: View {
  let size: CGFloat
  var symbol = "mic.fill"
  var working = false
  var recording = false
  var themeFill = false
  var body: some View {
    Color.clear
      .frame(width: size, height: size)
      .background(themeFill ? DS.primary : .clear, in: Circle())
      .modifier(AssistantGlassSurface(radius: size / 2, clear: true,
        tint: DS.primary.opacity(themeFill ? 1 : (recording ? 0.46 : 0.32)), interactive: true))
      .overlay {
        // Keep the white glyph above the tinted glass material.
        if working { ProgressView().tint(.white).scaleEffect(size > 44 ? 1.25 : 1) }
        else {
          Image(systemName: symbol)
            .font(.system(size: size > 44 ? 29 : 21, weight: .medium))
            .symbolRenderingMode(.monochrome)
            .foregroundStyle(.white)
            .contentTransition(.symbolEffect(.replace))
        }
      }
  }
}

private struct AssistantVoiceControls: View {
  @Bindable var voice: AssistantVoiceInput
  let onKeyboard: () -> Void
  let onClose: () -> Void
  @GestureState private var touching = false
  @State private var holdStarted = false
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

  private var working: Bool {
    voice.phase == .transcribing || voice.phase == .requestingPermission
  }
  private var status: String {
    switch voice.phase {
    case .recording: return "松开结束 · \(Int(voice.seconds)) 秒"
    case .transcribing: return "正在转成文字…"
    case .requestingPermission: return "请允许麦克风权限"
    default: return "按住说话"
    }
  }

  var body: some View {
    VStack(spacing: 4) {
      Text(status).font(.subheadline.weight(.medium)).foregroundStyle(DS.text)
        .accessibilityIdentifier("guide-voice-status")
      HStack(spacing: 0) {
        Button(action: onKeyboard) {
          Image(systemName: "keyboard").font(.system(size: 21))
            .frame(width: 48, height: 48)
            .contentShape(RoundedRectangle(cornerRadius: 18))
            .modifier(AssistantGlassSurface(radius: 18))
        }.accessibilityLabel("切回文字输入")
          .accessibilityIdentifier(voice.isVoiceMode ? "guide-voice-keyboard" : "guide-voice-keyboard-hidden")
          .disabled(!voice.isVoiceMode)
        Spacer(minLength: 8)

        ZStack {
          ForEach(0..<3) { index in
            Circle()
              .fill(DS.primary.opacity(reduceTransparency ? 0.16 :
                0.045 + Double(index) * 0.035 + voice.level * 0.05))
              .overlay {
                Circle().strokeBorder(DS.primary.opacity(0.10 + voice.level * 0.14), lineWidth: 0.7)
              }
              .frame(width: CGFloat(128 - index * 14), height: CGFloat(128 - index * 14))
              .scaleEffect(1 + (reduceMotion ? 0 : voice.level * (0.28 - Double(index) * 0.065)))
              .accessibilityHidden(true)
          }
          AssistantMicrophoneOrb(size: 84, working: working, recording: voice.phase == .recording)
        }
        .frame(width: 168, height: 164).contentShape(Circle())
        .animation(reduceMotion ? nil : .easeOut(duration: 0.11), value: voice.level)
        .gesture(
          DragGesture(minimumDistance: 0)
            .updating($touching) { _, active, _ in active = true }
            .onChanged { _ in
              if !holdStarted { holdStarted = true; voice.beginHold() }
            }
            .onEnded { _ in holdStarted = false; voice.endHold() }
        )
        .onChange(of: touching) { _, active in
          if !active, holdStarted { holdStarted = false; voice.endHold() }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(status).accessibilityAddTraits(.isButton)
        .accessibilityHint("按住录音，松开后转成文字，确认后发送")
        .accessibilityIdentifier(voice.isVoiceMode ? "guide-voice-record" : "guide-voice-record-hidden")
        .accessibilityAction {
          if voice.phase == .recording { voice.endHold() } else { voice.beginHold() }
        }
        .accessibilityAction(named: "结束录音") { voice.endHold() }

        Spacer(minLength: 8)

        Button(action: onClose) {
          Image(systemName: "xmark").font(.system(size: 20))
            .frame(width: 48, height: 48)
            .contentShape(RoundedRectangle(cornerRadius: 18))
            .modifier(AssistantGlassSurface(radius: 18))
        }.accessibilityLabel("退出语音输入")
          .accessibilityIdentifier(voice.isVoiceMode ? "guide-voice-close" : "guide-voice-close-hidden")
          .disabled(!voice.isVoiceMode)
      }
      .foregroundStyle(DS.text)
    }.frame(maxWidth: .infinity)
  }
}
