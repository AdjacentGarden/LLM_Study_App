import Foundation
import XCTest

@testable import BookCourse

@MainActor private final class ChatCaptureFixture: AssistantVoiceCapture {
  var permission: AssistantMicrophonePermission = .granted
  var starts = 0
  var stops = 0
  var cancels = 0
  var power: Float = -80
  var duration: TimeInterval = 2
  var running = false
  var authorization: CheckedContinuation<Bool, Never>?
  let url = FileManager.default.temporaryDirectory.appendingPathComponent("chat-capture-\(UUID()).m4a")
  func requestPermission() async -> Bool {
    await withCheckedContinuation { authorization = $0 }
  }
  func start() throws { starts += 1; running = true; try Data([1, 2, 3]).write(to: url) }
  func sample() -> AssistantAudioSample {
    .init(power: power, duration: duration, recording: running)
  }
  func stop() -> AssistantVoiceRecording? {
    guard running else { return nil }
    stops += 1
    running = false
    return .init(url: url, duration: duration)
  }
  func cancel() { cancels += 1; running = false; try? FileManager.default.removeItem(at: url) }
}

@MainActor final class AssistantVoiceInputTests: XCTestCase {
  private func settle(_ condition: @escaping () -> Bool) async {
    for _ in 0..<100 {
      if condition() { return }
      try? await Task.sleep(for: .milliseconds(5))
    }
    XCTFail("Voice operation did not settle")
  }

  func testReleasePopulatesDraftAndCleansTemporaryRecording() async {
    let capture = ChatCaptureFixture()
    var recognitions = 0
    let voice = AssistantVoiceInput(capture: capture) { url in
      recognitions += 1
      XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
      return "  请解释减数分裂。\n"
    }
    voice.enterVoice()
    XCTAssertEqual(voice.phase, .voiceReady)
    XCTAssertEqual(capture.starts, 0)
    voice.beginHold()
    XCTAssertEqual(voice.phase, .recording)
    voice.endHold()
    voice.endHold()
    await settle { voice.phase == .text }
    XCTAssertEqual(voice.draft, "请解释减数分裂。")
    XCTAssertTrue(voice.hasText)
    XCTAssertEqual(recognitions, 1)
    XCTAssertEqual(capture.stops, 1)
    XCTAssertFalse(FileManager.default.fileExists(atPath: capture.url.path))
    voice.returnToText()
  }

  func testFirstPermissionGrantRequiresAnotherHoldAndCannotRecordAfterExit() async {
    let capture = ChatCaptureFixture()
    capture.permission = .undetermined
    let voice = AssistantVoiceInput(capture: capture) { _ in "不应识别" }
    voice.enterVoice()
    voice.beginHold()
    await settle { capture.authorization != nil }
    voice.endHold()
    capture.permission = .granted
    capture.authorization?.resume(returning: true)
    await settle { voice.phase == .voiceReady }
    XCTAssertEqual(capture.starts, 0)
    voice.beginHold()
    XCTAssertEqual(capture.starts, 1)
    voice.returnToText()
    XCTAssertFalse(capture.running)

    capture.permission = .undetermined
    capture.authorization = nil
    voice.enterVoice()
    voice.beginHold()
    await settle { capture.authorization != nil }
    voice.returnToText()
    capture.authorization?.resume(returning: true)
    try? await Task.sleep(for: .milliseconds(15))
    XCTAssertEqual(voice.phase, .text)
    XCTAssertEqual(capture.starts, 1)
  }

  func testDeniedMicrophoneNeverStartsCapture() {
    let capture = ChatCaptureFixture()
    capture.permission = .denied
    let voice = AssistantVoiceInput(capture: capture) { _ in "不应识别" }
    voice.enterVoice()
    voice.beginHold()
    XCTAssertNotNil(voice.error)
    XCTAssertEqual(capture.starts, 0)
    voice.returnToText()
  }

  func testUnavailableTranscriptionStillAllowsLiveMeteringUntilRelease() {
    let capture = ChatCaptureFixture()
    let unavailable = AssistantVoiceInput(capture: capture,
      availability: { "本机识别模型不可用" }, transcribe: { _ in
        XCTFail("Unavailable recognition must not run or upload audio"); return "不应识别"
      })
    unavailable.enterVoice()
    unavailable.beginHold()
    XCTAssertEqual(unavailable.phase, .recording)
    capture.power = -20
    unavailable.refreshMeter()
    XCTAssertGreaterThan(unavailable.level, 0)
    XCTAssertNil(unavailable.error)
    unavailable.endHold()
    XCTAssertEqual(unavailable.phase, .voiceReady)
    XCTAssertEqual(unavailable.error, "本机识别模型不可用")
    XCTAssertEqual(capture.starts, 1)
    XCTAssertFalse(capture.running)
    XCTAssertFalse(FileManager.default.fileExists(atPath: capture.url.path))
    unavailable.returnToText()
  }

  func testMeterFollowsPowerAndShortAudioIsNotRecognized() {
    let capture = ChatCaptureFixture()
    let voice = AssistantVoiceInput(capture: capture) { _ in
      XCTFail("Too short recordings must not be recognized"); return ""
    }
    voice.enterVoice()
    voice.beginHold()
    voice.refreshMeter()
    XCTAssertEqual(voice.level, 0)
    capture.power = -10
    voice.refreshMeter()
    XCTAssertGreaterThan(voice.level, 0.4)
    let loud = voice.level
    capture.power = -70
    voice.refreshMeter()
    XCTAssertLessThan(voice.level, loud)
    capture.duration = 0.1
    voice.endHold()
    XCTAssertEqual(voice.phase, .voiceReady)
    XCTAssertNotNil(voice.error)
    XCTAssertFalse(FileManager.default.fileExists(atPath: capture.url.path))
    voice.returnToText()
  }

  func testCancelledRecognitionCannotOverwriteNewDraft() async {
    let capture = ChatCaptureFixture()
    var result: CheckedContinuation<String, Never>?
    let voice = AssistantVoiceInput(capture: capture) { _ in
      await withCheckedContinuation { result = $0 }
    }
    voice.enterVoice()
    voice.beginHold()
    voice.endHold()
    await settle { result != nil }
    voice.returnToText()
    voice.draft = "保留我新输入的内容"
    result?.resume(returning: "迟到的识别内容")
    await settle { !FileManager.default.fileExists(atPath: capture.url.path) }
    XCTAssertEqual(voice.draft, "保留我新输入的内容")
    XCTAssertEqual(voice.phase, .text)
  }

  func testInterruptionStopsCaptureAndPreservesDraft() {
    let capture = ChatCaptureFixture()
    let voice = AssistantVoiceInput(capture: capture) { _ in "不应识别" }
    voice.enterVoice()
    voice.beginHold()
    voice.interrupt()
    XCTAssertEqual(voice.phase, .text)
    XCTAssertFalse(capture.running)
    XCTAssertNotNil(voice.error)
    XCTAssertFalse(FileManager.default.fileExists(atPath: capture.url.path))
    voice.draft = "   \n"
    XCTAssertFalse(voice.hasText)
    voice.draft = "一个问题"
    voice.enterVoice()
    XCTAssertEqual(voice.phase, .text)
  }
}
