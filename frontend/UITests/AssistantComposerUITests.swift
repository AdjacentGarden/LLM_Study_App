import XCTest

final class AssistantComposerUITests: XCTestCase {
  override func setUpWithError() throws { continueAfterFailure = false }

  private func launch(reduceMotion: Bool = true, longReply: Bool = false, historyFixture: Bool = false) -> XCUIApplication {
    XCUIDevice.shared.orientation = .portrait
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString,
      "--route", "study", "--assistant", "--fixture-assistant-voice"]
    if reduceMotion { app.launchArguments += ["--fixture-reduce-motion"] }
    if longReply { app.launchArguments += ["--fixture-assistant-long-reply"] }
    if historyFixture { app.launchArguments += ["--fixture-history-controls"] }
    app.launch()
    let ready = app.buttons["guide-mic"].waitForExistence(timeout: 20)
    if !ready { print(app.debugDescription) }
    XCTAssertTrue(ready)
    let actionable = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in app.buttons["guide-mic"].isHittable }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [actionable], timeout: 10), .completed)
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

  func testVisibleNewAndDeleteConversationControlsPersistAcrossRelaunch() {
    let app = launch(historyFixture: true)
    let field = input(app)
    XCTAssertTrue(app.descendants(matching: .any)["guide-user-message"].firstMatch.waitForExistence(timeout: 5))
    field.tap()
    field.typeText("Keep my unsent draft")

    let newConversation = app.buttons["assistant-new-conversation"]
    XCTAssertTrue(newConversation.isHittable)
    newConversation.tap()
    XCTAssertFalse(app.descendants(matching: .any)["guide-user-message"].exists)
    XCTAssertFalse(app.descendants(matching: .any)["guide-ai-message"].exists)
    app.buttons["assistant-history"].tap()
    XCTAssertTrue(app.buttons["assistant-history-new"].waitForExistence(timeout: 5))
    app.buttons["assistant-history-fixture-first"].tap()
    XCTAssertEqual(field.value as? String, "Keep my unsent draft")
    app.buttons["assistant-history"].tap()
    let removeFirst = app.buttons["删除对话：First conversation"]
    XCTAssertTrue(removeFirst.isHittable)
    screenshot("history-visible-new-and-delete", app: app)
    removeFirst.tap()
    let confirmation = app.alerts["删除这段对话？"]
    XCTAssertTrue(confirmation.waitForExistence(timeout: 5))
    app.buttons["assistant-history-delete-cancel"].firstMatch.tap()
    XCTAssertTrue(removeFirst.exists)
    removeFirst.tap()
    app.buttons["assistant-history-delete-confirm"].firstMatch.tap()
    XCTAssertTrue(removeFirst.waitForNonExistence(timeout: 5))
    XCTAssertTrue(app.buttons["删除对话：Second conversation"].exists)
    screenshot("history-after-delete", app: app)

    app.terminate()
    app.launch()
    XCTAssertTrue(app.buttons["assistant-history"].waitForExistence(timeout: 20))
    app.buttons["assistant-history"].tap()
    XCTAssertTrue(app.buttons["删除对话：Second conversation"].waitForExistence(timeout: 5))
    XCTAssertFalse(app.buttons["删除对话：First conversation"].exists)
    app.buttons["assistant-history-new"].tap()
    XCTAssertTrue(app.buttons["guide-mic"].waitForExistence(timeout: 5))
    XCTAssertFalse(app.descendants(matching: .any)["guide-user-message"].exists)
  }

  func testLongReplyIsCappedAndFullScreenPreservesAllContent() {
    let app = launch(longReply: true)
    let preview = app.descendants(matching: .any)["assistant.reply.preview"].firstMatch
    XCTAssertTrue(app.staticTexts["assistant.reply.truncated"].waitForExistence(timeout: 10))
    XCTAssertLessThanOrEqual(preview.frame.height, 241)
    let bubble = app.descendants(matching: .any)["guide-ai-message"].firstMatch
    XCTAssertLessThanOrEqual(bubble.frame.height, 370)
    screenshot("assistant-long-reply-preview", app: app)
    app.buttons["assistant.reply.expand"].tap()
    let detail = app.descendants(matching: .any)["assistant.reply.fullscreen"].firstMatch
    XCTAssertTrue(detail.waitForExistence(timeout: 5))
    let answer = detail.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "FULLSCREEN_END")).firstMatch
    screenshot("assistant-long-reply-large-detail", app: app)
    XCTAssertTrue(answer.waitForExistence(timeout: 5), "The full-screen view must retain the entire original answer")
    XCTAssertFalse(detail.staticTexts["assistant.reply.truncated"].exists)
    app.buttons["assistant.reply.collapse"].tap()
    XCTAssertTrue(detail.waitForNonExistence(timeout: 5))
    app.buttons["assistant.run.disclosure"].tap()
    XCTAssertLessThanOrEqual(preview.frame.height, 241, "Expanded operation logs also obey the preview limit")
    XCTAssertLessThanOrEqual(bubble.frame.height, 370)
    app.buttons["assistant.reply.expand"].tap()
    XCTAssertTrue(detail.waitForExistence(timeout: 5))
    XCTAssertTrue(detail.staticTexts["处理记录 20：已检查课程资料和学习进度。"].waitForExistence(timeout: 5))
    XCTAssertTrue(answer.exists)
    app.buttons["assistant.reply.collapse"].tap()
    XCTAssertTrue(detail.waitForNonExistence(timeout: 5))
    XCTAssertEqual(app.buttons.matching(identifier: "assistant.reply.expand").count, 1)
  }

  func testTapAboveComposerDismissesKeyboardAndPreservesDraft() {
    let app = launch()
    let field = input(app)
    field.tap()
    XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    field.typeText("Keep this draft")
    // Empty conversation space must work before the first reply exists.
    let conversation = app.scrollViews["guide-conversation"]
    conversation.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.15)).tap()
    XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
    XCTAssertEqual(field.value as? String, "Keep this draft")
    field.tap()
    XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    field.typeText(" and continue")
    XCTAssertTrue(app.keyboards.firstMatch.exists)
    // Refocusing can place the caret at the tapped position rather than the end.
    let editedDraft = field.value as? String ?? ""
    XCTAssertTrue(editedDraft.contains("Keep this draft"))
    XCTAssertTrue(editedDraft.contains(" and continue"))
    app.staticTexts["assistant.context.capacity"].tap()
    XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
    XCTAssertEqual(field.value as? String, editedDraft)
    XCTAssertFalse(app.descendants(matching: .any)["guide-user-message"].exists)
  }

  func testTextMicSwitchMultilineKeyboardAndSend() {
    let app = launch()
    XCTAssertFalse(app.buttons["guide-send"].exists)
    XCTAssertFalse(app.staticTexts["随时问，一起弄明白"].exists)
    XCTAssertFalse(app.descendants(matching: .any)["guide-ai-message"].exists)
    let mic = app.buttons["guide-mic"]
    let more = app.buttons["guide-more"]
    for button in [mic, more, app.buttons["global-assistant-close"]] {
      XCTAssertEqual(button.frame.width, 44, accuracy: 1)
      XCTAssertEqual(button.frame.height, 44, accuracy: 1)
    }
    let barWidth = mic.frame.maxX - more.frame.minX + 14
    if app.windows.firstMatch.frame.width < 720 {
      XCTAssertEqual(barWidth / app.windows.firstMatch.frame.width, 0.84, accuracy: 0.015)
    }
    XCTAssertEqual(mic.frame.height + 14, 58, accuracy: 1)
    // The viewport continues below the composer; only content padding reserves its space.
    // A separate VStack footer used to clip replies above the input bar with a hard edge.
    XCTAssertGreaterThan(app.scrollViews["guide-conversation"].frame.maxY, mic.frame.maxY)
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
    XCTAssertLessThanOrEqual(app.descendants(matching: .any)["guide-ai-message"].firstMatch.frame.maxY,
      app.buttons["guide-mic"].frame.minY - 4)
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
    for button in [record, keyboard, close] {
      XCTAssertEqual(button.frame.width, 44, accuracy: 1)
      XCTAssertEqual(button.frame.height, 44, accuracy: 1)
    }
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
