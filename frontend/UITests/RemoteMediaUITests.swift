import XCTest

/// Read-only live check. The backend must already have a task created by the
/// user/test operator; this test never clicks generation or spends provider credit.
final class RemoteMediaUITests: XCTestCase {
  func testExistingIllustrationOpensRealBackendTaskAndRestoresItAfterRelaunch() throws {
    guard let base = ProcessInfo.processInfo.environment["BOOKCOURSE_REMOTE_API_BASE_URL"],
      let asset = ProcessInfo.processInfo.environment["BOOKCOURSE_REMOTE_MEDIA_ASSET"],
      let expected = ProcessInfo.processInfo.environment["BOOKCOURSE_REMOTE_MEDIA_STATUS"]
    else { throw XCTSkip("Requires an authenticated remote course and an existing media task") }
    continueAfterFailure = false
    let app = XCUIApplication()
    app.launchEnvironment["BOOKCOURSE_API_BASE_URL"] = base
    app.launch()
    open(app, asset: asset, expected: expected)
    let first = XCTAttachment(screenshot: app.screenshot())
    first.name = "Existing illustration uses the real Windows video task"
    first.lifetime = .keepAlways
    add(first)
    app.buttons["lesson.media.close"].tap()
    app.buttons["lesson.video.open.\(asset)"].tap()
    XCTAssertTrue(app.staticTexts["lesson.media.status"].waitForExistence(timeout: 90))
    XCTAssertEqual(app.staticTexts["lesson.media.status"].label, expected)
    app.terminate()
    app.launch()
    open(app, asset: asset, expected: expected)
    let restored = XCTAttachment(screenshot: app.screenshot())
    restored.name = "Video task restored after a normal app relaunch"
    restored.lifetime = .keepAlways
    add(restored)
    app.buttons["lesson.media.close"].tap()
  }

  private func open(_ app: XCUIApplication, asset: String, expected: String) {
    let ready = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        !app.images["app.loading"].exists
          && (app.buttons["tab-profile"].isHittable || app.staticTexts["lesson.page"].isHittable)
      }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 135), .completed)
    if !app.staticTexts["lesson.page"].isHittable {
      let resume = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "继续学习"))
      let visible = resume.allElementsBoundByIndex.first { $0.isHittable }
      XCTAssertNotNil(visible)
      visible?.tap()
    }
    let button = app.buttons["lesson.video.open.\(asset)"]
    XCTAssertTrue(button.waitForExistence(timeout: 30))
    for _ in 0..<5 {
      if button.isHittable { break }
      app.swipeUp()
    }
    button.tap()
    XCTAssertTrue(app.staticTexts["lesson.media.status"].waitForExistence(timeout: 90))
    XCTAssertEqual(app.staticTexts["lesson.media.status"].label, expected)
    XCTAssertFalse(app.staticTexts["这张图暂时无法生成视频"].exists)
    XCTAssertFalse(app.buttons["生成图解"].exists)
    XCTAssertFalse(app.buttons["生成短片"].exists)
  }
}
