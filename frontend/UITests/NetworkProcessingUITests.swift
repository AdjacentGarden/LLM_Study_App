import XCTest
import UIKit

/// Debug fixtures isolate storage and networking. These tests never record,
/// upload a file, submit cloud work, or press the voice generation button.
final class NetworkProcessingUITests: XCTestCase {
  override func setUpWithError() throws { continueAfterFailure = false }

  private func launch(_ route: String = "home", voiceFixture: Bool = false) -> XCUIApplication {
    XCUIDevice.shared.orientation = .portrait
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString,
      "--fixture-reduce-motion", "--route", route, "--chapter", "c2s1",
      "--fixture-network", "cellular"]
    if voiceFixture { app.launchArguments += ["--fixture-voice-note", "raw_only"] }
    else { app.launchArguments += ["--sheet", "chat"] }
    app.launch()
    XCTAssertTrue(app.wait(for: .runningForeground, timeout: 20))
    return app
  }

  private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
    XCTAssertTrue(element.waitForExistence(timeout: 15))
    for _ in 0..<10 {
      if element.isHittable { return }
      let chat = app.scrollViews["assistant.scroll"].firstMatch
      let voice = app.scrollViews["voice.scroll"].firstMatch
      let viewport = chat.exists ? chat.frame : voice.exists ? voice.frame : app.scrollViews.firstMatch.frame
      let keyboardTop = app.keyboards.firstMatch.exists ? app.keyboards.firstMatch.frame.minY : app.frame.maxY
      let top = max(viewport.minY + 12, app.frame.minY + 100)
      let bottom = min(viewport.maxY - 12, keyboardTop - 18)
      let origin = app.coordinate(withNormalizedOffset: .zero)
      // Swipe inside the visible document, never across the keyboard's swipe typing area.
      let start = origin.withOffset(CGVector(dx: viewport.midX, dy: bottom - 12))
      let end = origin.withOffset(CGVector(dx: viewport.midX, dy: top + 12))
      start.press(forDuration: 0.05, thenDragTo: end)
    }
    XCTAssertTrue(element.isHittable)
  }

  private func ask(_ text: String, in app: XCUIApplication) {
    let input = app.textFields["assistant-input"].firstMatch
    reveal(input, in: app)
    input.tap()
    input.typeText(text)
    let send = app.buttons["assistant-send"].firstMatch
    XCTAssertTrue(send.waitForExistence(timeout: 10))
    XCTAssertTrue(send.isEnabled)
    if send.isHittable { send.tap() }
    else {
      // SwiftUI's nested custom panel can report a false AX hit-test after a
      // modal closes. Verify the visible touch point and exercise the real tap.
      let point = CGPoint(x: send.frame.midX, y: send.frame.midY)
      XCTAssertTrue(app.windows.firstMatch.frame.contains(point))
      if app.keyboards.firstMatch.exists {
        XCTAssertLessThan(point.y, app.keyboards.firstMatch.frame.minY)
      }
      app.coordinate(withNormalizedOffset: .zero)
        .withOffset(CGVector(dx: point.x, dy: point.y)).tap()
    }
  }

  private func checkPrompt(_ app: XCUIApplication) {
    let panel = app.descendants(matching: .any)["network.choice"].firstMatch
    let displayed = panel.waitForExistence(timeout: 15)
    screenshot("network-choice-before-layout-assertions", app: app)
    let tree = XCTAttachment(string: app.debugDescription)
    tree.name = "network-choice-accessibility-tree"
    tree.lifetime = .keepAlways
    add(tree)
    XCTAssertTrue(displayed)
    XCTAssertTrue(app.staticTexts["当前正在使用流量"].exists)
    XCTAssertTrue(app.staticTexts["要改用 iPhone 本机处理吗？"].exists)
    XCTAssertTrue(app.staticTexts["本机识别录音，仅将文字发送给 DeepSeek 整理。"].exists)
    for id in ["network.useLocal", "network.useExisting", "network.cancel"] {
      let action = app.buttons[id]
      XCTAssertTrue(action.exists, id)
      XCTAssertTrue(action.isHittable, id)
      XCTAssertTrue(app.windows.firstMatch.frame.contains(action.frame), id)
    }
    XCTAssertFalse(app.textViews["voice.transcript"].exists)
  }

  private func waitForLocalAnswers(_ count: Int, in app: XCUIApplication) {
    let answers = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "本机教材检索结果："))
    let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
      answers.count >= count && app.buttons["assistant-send"].firstMatch.isEnabled
    }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 20), .completed)
    XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "PDF 第")).firstMatch.exists,
      "The locally retrieved text must have an actual source-page citation.")
  }

  private func screenshot(_ name: String, app: XCUIApplication) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    add(attachment)
  }

  func testCellularLocalChoiceShowsCitationsAndIsReusedForNextQuestion() {
    let app = launch()
    ask("减数分裂中染色体复制几次？", in: app)
    checkPrompt(app)
    screenshot("network-cellular-choice", app: app)
    app.buttons["network.useLocal"].tap()
    XCTAssertTrue(app.buttons["network.useLocal"].waitForNonExistence(timeout: 10))
    waitForLocalAnswers(1, in: app)
    screenshot("network-local-textbook-citations", app: app)
    let nextQuestion = app.buttons["给我出一道题"].firstMatch
    reveal(nextQuestion, in: app)
    nextQuestion.tap()
    waitForLocalAnswers(2, in: app)
    XCTAssertFalse(app.descendants(matching: .any)["network.choice"].firstMatch.exists)
    screenshot("network-cellular-choice-reused", app: app)
  }

  func testCancellingCellularChoiceDoesNotAuthorizeTheNextQuestion() {
    let app = launch()
    ask("减数分裂中染色体复制几次？", in: app)
    checkPrompt(app)
    app.buttons["network.cancel"].tap()
    XCTAssertTrue(app.buttons["network.cancel"].waitForNonExistence(timeout: 10))
    let idle = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
      app.buttons["assistant-send"].firstMatch.isEnabled
    }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [idle], timeout: 10), .completed)
    XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "本机教材检索结果：")).firstMatch.exists)
    XCTAssertFalse(app.descendants(matching: .any)["network.choice"].firstMatch.exists)
    screenshot("network-cancel-returns-without-generation", app: app)
    ask("染色体在减数分裂中怎样变化？", in: app)
    checkPrompt(app)
    screenshot("network-cancel-asks-again", app: app)
    app.buttons["network.useLocal"].tap()
    waitForLocalAnswers(1, in: app)
  }

  func testVoiceNoteExplainsLocalRecognitionWithoutShowingLegacyTranscript() {
    let app = launch("notes", voiceFixture: true)
    XCTAssertTrue(app.staticTexts["兼容测试：旧语音笔记"].firstMatch.waitForExistence(timeout: 20))
    let open = app.buttons["打开语音笔记"].firstMatch
    reveal(open, in: app)
    open.tap()
    XCTAssertTrue(app.buttons["播放录音"].firstMatch.waitForExistence(timeout: 15))
    let generate = app.buttons["voice.generate"].firstMatch
    reveal(generate, in: app)
    XCTAssertTrue(generate.isEnabled)
    XCTAssertTrue(app.staticTexts[
      "按当前网络选择语音识别方式；选择本机识别时，只将识别文字交给 DeepSeek 整理，录音不上传。"
    ].exists)
    let raw = "LEGACY_VOICE_RAW_PRIVATE_20261027"
    XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", raw)).firstMatch.exists)
    XCTAssertFalse(app.textViews.matching(NSPredicate(format: "value CONTAINS %@", raw)).firstMatch.exists)
    XCTAssertFalse(app.textViews["voice.transcript"].exists)
    XCTAssertFalse(app.buttons["确认并继续整理"].exists)
    XCTAssertFalse(app.buttons["保留整理版"].exists)
    XCTAssertFalse(app.descendants(matching: .any)["network.choice"].firstMatch.exists,
      "Opening a saved recording cannot start recognition or request network consent.")
    screenshot("network-voice-local-recognition-explained", app: app)
  }
}
