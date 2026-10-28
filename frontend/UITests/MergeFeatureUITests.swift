import XCTest

/// Uses a separate fixture database and never submits provider jobs.
final class MergeFeatureUITests: XCTestCase {
  override func setUpWithError() throws {
    continueAfterFailure = false
    XCUIDevice.shared.orientation = .portrait
  }

  private func launch(_ route: String, session: String) -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", session, "--fixture-reduce-motion",
      "--route", route, "--chapter", "c2s1"]
    app.launch()
    return app
  }
  private func tap(_ element: XCUIElement, app: XCUIApplication) {
    XCTAssertTrue(element.waitForExistence(timeout: 15))
    for _ in 0..<8 {
      if element.isHittable { break }
      app.swipeUp()
    }
    XCTAssertTrue(element.isHittable)
    element.tap()
  }

  func testPreferenceResetClearsAnswersPreservesNameAndSavesWithoutChangingOtherState() {
    let session = UUID().uuidString
    let app = launch("profile", session: session)
    tap(app.buttons["profile.resetPreferences"], app: app)
    XCTAssertFalse(app.textFields["onboarding.name"].exists)
    let goal = app.buttons["onboarding.choice.1.interest"]
    XCTAssertTrue(goal.waitForExistence(timeout: 10))
    XCTAssertFalse(goal.isSelected)
    tap(goal, app: app)
    tap(app.buttons["onboarding.time.under30"], app: app)
    tap(app.buttons["onboarding.choice.3.focus"], app: app)
    tap(app.buttons["onboarding.choice.4.principles"], app: app)
    tap(app.buttons["onboarding.choice.5.selfDirected"], app: app)
    XCTAssertTrue(app.buttons["profile.resetPreferences"].waitForExistence(timeout: 15))
    tap(app.buttons["profile.resetPreferences"], app: app)
    XCTAssertFalse(app.buttons["onboarding.choice.1.interest"].isSelected)
    tap(app.buttons["preferences.cancel"], app: app)
    tap(app.buttons["profile.settings"], app: app)
    XCTAssertTrue(app.staticTexts["小明同学"].waitForExistence(timeout: 10))
    XCTAssertTrue(app.staticTexts["探索兴趣内容"].exists)
    XCTAssertTrue(app.staticTexts["30分钟以内"].exists)
    XCTAssertTrue(app.staticTexts["更专注投入"].exists)
    XCTAssertTrue(app.staticTexts["讲透原理"].exists)
    let screenshot = XCTAttachment(screenshot: app.screenshot())
    screenshot.name = "Merged preference reset saved with the original learner name"
    screenshot.lifetime = .keepAlways
    add(screenshot)
  }
}
