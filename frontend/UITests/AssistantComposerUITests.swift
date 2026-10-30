import XCTest

final class AssistantComposerUITests: XCTestCase {
  override func setUpWithError() throws { continueAfterFailure = false }

  private func launch(reduceMotion: Bool = true) -> XCUIApplication {
    XCUIDevice.shared.orientation = .portrait
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString,
      "--route", "study", "--assistant", "--fixture-assistant-voice"]
    if reduceMotion { app.launchArguments += ["--fixture-reduce-motion"] }
    app.launch()
    let ready = app.buttons["guide-mic"].waitForExistence(timeout: 20)
    if !ready { print(app.debugDescription) }
    XCTAssertTrue(ready)
    return app
  }

  private func input(_ app: XCUIApplication) -> XCUIElement {
    app.descendants(matching: .any).matching(identifier: "guide-input").firstMatch
  }

  private func screenshot(_ name: String, app: XCUIApplication) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    add(attachment)
  }

  func testTextMicSwitchMultilineKeyboardAndSend() {
    let app = launch()
    XCTAssertFalse(app.buttons["guide-send"].exists)
    XCTAssertFalse(app.staticTexts["随时问，一起弄明白"].exists)
    XCTAssertFalse(app.descendants(matching: .any)["guide-ai-message"].exists)
    let mic = app.buttons["guide-mic"]
    let more = app.buttons["guide-more"]
    let barWidth = mic.frame.maxX - more.frame.minX + 14
    if app.windows.firstMatch.frame.width < 720 {
      XCTAssertEqual(barWidth / app.windows.firstMatch.frame.width, 0.84, accuracy: 0.015)
    }
    XCTAssertEqual(mic.frame.height + 14, 58, accuracy: 1)
    screenshot("assistant-glass-empty", app: app)
    let field = input(app)
    XCTAssertEqual(field.placeholderValue, "问教材、问错题、问计划…")
    field.tap()
    XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    XCTAssertTrue((field.placeholderValue ?? "").isEmpty)
    screenshot("assistant-glass-focused-empty", app: app)
    field.typeText("Explain meiosis and why cells divide twice. ")
    XCTAssertTrue(app.buttons["guide-send"].waitForExistence(timeout: 5))
    XCTAssertFalse(app.buttons["guide-mic"].exists)
    XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    XCTAssertLessThanOrEqual(field.frame.maxY, app.keyboards.firstMatch.frame.minY + 1)
    field.typeText(String(repeating: "Please explain with examples. ", count: 4))
    XCTAssertLessThanOrEqual(app.buttons["guide-send"].frame.maxY,
      app.keyboards.firstMatch.frame.minY + 1)
    screenshot("assistant-glass-text", app: app)
    let text = field.value as? String ?? ""
    field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: text.count))
    XCTAssertTrue(app.buttons["guide-mic"].waitForExistence(timeout: 5))
    XCTAssertTrue((field.placeholderValue ?? "").isEmpty)
    field.typeText("Hi")
    app.buttons["guide-send"].tap()
    XCTAssertTrue(app.descendants(matching: .any)["guide-user-message"].firstMatch
      .waitForExistence(timeout: 10))
    XCTAssertTrue(app.descendants(matching: .any)["guide-ai-message"].firstMatch
      .waitForExistence(timeout: 20))
    screenshot("assistant-glass-conversation", app: app)
    app.buttons["global-assistant-close"].tap()
    XCTAssertTrue(app.buttons["tab-study"].isHittable)
  }

  func testVoiceReleaseBackfillsUntilExplicitSendAndCloseRestoresText() {
    // Recorded audio and recognition are injected; this validates the UI contract,
    // not simulator microphone hardware or Apple's speech recognition quality.
    let app = launch()
    app.buttons["guide-mic"].tap()
    let record = app.buttons["guide-voice-record"]
    XCTAssertTrue(record.waitForExistence(timeout: 5))
    XCTAssertFalse(input(app).exists)
    XCTAssertFalse(app.staticTexts["松开后转为文字，确认后发送"].exists)
    screenshot("assistant-glass-voice", app: app)
    record.press(forDuration: 1.2)
    XCTAssertTrue(app.buttons["guide-send"].waitForExistence(timeout: 10))
    XCTAssertTrue(input(app).isHittable)
    XCTAssertEqual(input(app).value as? String, "请解释减数分裂为什么需要两次分裂。")
    XCTAssertTrue(app.buttons["guide-send"].exists)
    XCTAssertFalse(app.descendants(matching: .any)["guide-user-message"].exists)
    screenshot("assistant-glass-voice-draft", app: app)
    app.buttons["guide-send"].tap()
    XCTAssertTrue(app.descendants(matching: .any)["guide-user-message"].firstMatch
      .waitForExistence(timeout: 10))
    XCTAssertTrue(app.buttons["guide-mic"].waitForExistence(timeout: 20))
    XCTAssertTrue(app.buttons["guide-mic"].isEnabled)
    app.buttons["guide-mic"].tap()
    app.buttons["guide-voice-close"].tap()
    XCTAssertTrue(input(app).waitForExistence(timeout: 5))
    app.buttons["guide-mic"].tap()
    app.buttons["guide-voice-keyboard"].tap()
    XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    XCTAssertTrue(input(app).isHittable)
    app.buttons["global-assistant-close"].tap()
    XCTAssertFalse(app.buttons["guide-voice-record"].exists)
  }

  func testAnimatedTextVoiceTransitionsFinishWithWorkingControls() {
    let app = launch(reduceMotion: false)
    app.buttons["guide-mic"].tap()
    let record = app.buttons["guide-voice-record"]
    XCTAssertTrue(record.waitForExistence(timeout: 5))
    let keyboard = app.buttons["guide-voice-keyboard"]
    let close = app.buttons["guide-voice-close"]
    XCTAssertTrue(keyboard.isHittable)
    XCTAssertTrue(close.isHittable)
    XCTAssertEqual(record.frame.midX, (keyboard.frame.midX + close.frame.midX) / 2, accuracy: 3)
    screenshot("assistant-purple-glass-voice", app: app)

    keyboard.tap()
    XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    XCTAssertTrue(input(app).isHittable)
    XCTAssertTrue((input(app).placeholderValue ?? "").isEmpty)
    app.buttons["guide-mic"].tap()
    XCTAssertTrue(record.waitForExistence(timeout: 5))
    XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
    record.press(forDuration: 1.2)
    XCTAssertTrue(app.buttons["guide-send"].waitForExistence(timeout: 10))
    XCTAssertTrue(input(app).isHittable)
    XCTAssertEqual(input(app).value as? String, "请解释减数分裂为什么需要两次分裂。")
    XCTAssertFalse(app.descendants(matching: .any)["guide-user-message"].exists)
    app.buttons["global-assistant-close"].tap()
    XCTAssertTrue(app.buttons["tab-study"].isHittable)
  }
}
