import XCTest

/// Real user actions in isolated repository fixtures. These tests do not use
/// visual-baseline state, change device layout, or access production data.
final class CourseFunctionalUITests: XCTestCase {
  override func setUpWithError() throws {
    continueAfterFailure = false
    XCUIDevice.shared.orientation = .portrait
  }

  private func launch(_ route: String) -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString, "--route", route]
    app.launch()
    XCTAssertTrue(app.wait(for: .runningForeground, timeout: 20))
    return app
  }

  private func tap(_ element: XCUIElement, in app: XCUIApplication) {
    XCTAssertTrue(element.waitForExistence(timeout: 15), "Missing \(element)")
    // Exercise the form's explicit keyboard exit rather than dragging from
    // coordinates which can belong to the software keyboard on an iPhone.
    let keyboardDone = app.buttons["chapter.keyboardDone"]
    if app.keyboards.firstMatch.exists, keyboardDone.exists {
      keyboardDone.tap()
      XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
    }
    for _ in 0..<8 {
      if element.isHittable { break }
      let surface = app.scrollViews.allElementsBoundByIndex.last ?? app
      surface.coordinate(withNormalizedOffset: CGVector(dx: 0.94, dy: 0.70)).press(
        forDuration: 0.05,
        thenDragTo: surface.coordinate(withNormalizedOffset: CGVector(dx: 0.94, dy: 0.25)))
    }
    XCTAssertTrue(element.isHittable, "Unreachable \(element)")
    element.tap()
  }

  private func replace(_ field: XCUIElement, with value: String) {
    XCTAssertTrue(field.waitForExistence(timeout: 10))
    // A center tap can place the insertion point in the middle of a Chinese
    // title. Tap its trailing edge before backspacing the complete value.
    field.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.5)).tap()
    let current = field.value as? String ?? ""
    field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: current.count) + value)
    XCTAssertEqual(field.value as? String, value)
  }

  private func finishQuestionnaire(_ app: XCUIApplication) {
    let choices = ["很紧急，短期要学完", "碎片化零散时间", "应试备考", "重点和考点", "AI 视频讲解", "自由复习"]
    let questions = [
      "你当前学习的紧迫程度？", "你的学习时间是哪种类型？", "这门课程主要用于什么？", "你希望内容更侧重什么？", "你最需要哪些学习辅助？", "你习惯怎样巩固这门课程？",
    ]
    for (index, choice) in choices.enumerated() {
      XCTAssertTrue(app.staticTexts[questions[index]].firstMatch.waitForExistence(timeout: 10))
      tap(
        app.buttons.containing(NSPredicate(format: "label CONTAINS %@", choice)).firstMatch, in: app
      )
      tap(app.buttons["courseSetup.next"], in: app)
    }
  }

  func testOnboardingCompletesSixStepsWithSelectionLimitAndBackNavigation() {
    let app = launch("onboarding")
    tap(app.buttons["onboarding.next"], in: app)
    tap(
      app.buttons.containing(NSPredicate(format: "label CONTAINS %@", "备考提升成绩")).firstMatch, in: app
    )
    tap(app.buttons["onboarding.next"], in: app)
    tap(app.buttons["30分钟以内"], in: app)
    tap(app.buttons["onboarding.next"], in: app)
    tap(app.buttons["onboarding.choice.3.understanding"], in: app)
    tap(app.buttons["onboarding.choice.3.memory"], in: app)
    tap(app.buttons["onboarding.choice.3.focus"], in: app)
    XCTAssertTrue(app.staticTexts["最多选择两项，可先取消一项"].exists)
    XCTAssertFalse(app.buttons["onboarding.choice.3.focus"].isSelected)
    tap(app.buttons["上一步"], in: app)
    XCTAssertTrue(app.buttons["onboarding.time.under30"].isSelected)
    tap(app.buttons["onboarding.next"], in: app)
    XCTAssertTrue(app.buttons["onboarding.choice.3.understanding"].isSelected)
    XCTAssertTrue(app.buttons["onboarding.choice.3.memory"].isSelected)
    tap(app.buttons["onboarding.next"], in: app)
    tap(app.buttons["onboarding.choice.4.examples"], in: app)
    tap(app.buttons["onboarding.choice.4.stepByStep"], in: app)
    tap(app.buttons["onboarding.next"], in: app)
    tap(app.buttons["onboarding.choice.5.collaborative"], in: app)
    tap(app.buttons["上一步"], in: app)
    XCTAssertTrue(app.buttons["onboarding.choice.4.examples"].isSelected)
    tap(app.buttons["onboarding.next"], in: app)
    XCTAssertTrue(app.buttons["onboarding.choice.5.collaborative"].isSelected)
    tap(app.buttons["onboarding.next"], in: app)
    XCTAssertTrue(
      app.staticTexts["Hi，小明同学"].firstMatch.waitForExistence(timeout: 10))
  }

  func testOnboardingCanSkipEveryOptionalQuestion() {
    let app = launch("onboarding")
    tap(app.buttons["onboarding.next"], in: app)
    for _ in 1...5 { tap(app.buttons["onboarding.skip"], in: app) }
    XCTAssertTrue(app.staticTexts["Hi，小明同学"].firstMatch.waitForExistence(timeout: 10))
    tap(app.buttons["tab-profile"], in: app)
    tap(app.buttons["profile.settings"], in: app)
    XCTAssertTrue(app.staticTexts["暂未设置"].firstMatch.waitForExistence(timeout: 10))
  }

  func testQuestionnaireChapterCancelSaveRemoveCancelAndConfirm() {
    let app = launch("courseSetup")
    finishQuestionnaire(app)
    XCTAssertTrue(app.staticTexts["确认目录"].firstMatch.waitForExistence(timeout: 10))
    XCTAssertTrue(app.staticTexts["原文页码待补充"].firstMatch.exists)
    let edit = app.buttons["chapter.edit.c2"]
    tap(edit, in: app)
    let title = app.textFields["课程章节名称"]
    XCTAssertTrue(title.waitForExistence(timeout: 10))
    let original = title.value as? String ?? ""
    replace(title, with: "Cancelled chapter draft")
    tap(app.buttons["关闭"].firstMatch, in: app)
    tap(edit, in: app)
    XCTAssertEqual(title.value as? String, original, "Closing an unsaved editor must discard edits")
    replace(title, with: "Saved chapter draft")
    tap(app.buttons["chapter.save"], in: app)
    tap(edit, in: app)
    XCTAssertEqual(title.value as? String, "Saved chapter draft")
    tap(app.buttons["移除此章节"], in: app)
    tap(app.buttons["取消"].firstMatch, in: app)
    XCTAssertTrue(title.exists, "Cancelling removal must retain the chapter editor")
    tap(app.buttons["关闭"].firstMatch, in: app)
    tap(app.buttons["chapters.confirm"], in: app)
    XCTAssertTrue(app.staticTexts["生成成功"].firstMatch.waitForExistence(timeout: 10))
    tap(app.buttons["进入学习"], in: app)
    XCTAssertTrue(app.staticTexts["学习计划"].firstMatch.waitForExistence(timeout: 10))
  }

  func testCommunityImportCompletesQuestionnaireAndOpensStudy() {
    let app = launch("communityBook")
    tap(app.buttons["community.import"], in: app)
    finishQuestionnaire(app)
    XCTAssertTrue(app.staticTexts["导入成功"].firstMatch.waitForExistence(timeout: 10))
    tap(app.buttons["进入学习"], in: app)
    XCTAssertTrue(app.staticTexts["学习计划"].firstMatch.waitForExistence(timeout: 10))
    XCTAssertTrue(app.buttons["添加资料"].firstMatch.exists)
  }
}
