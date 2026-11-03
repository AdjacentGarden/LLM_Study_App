import XCTest

final class CourseUploadUITests: XCTestCase {
  override func setUpWithError() throws {
    continueAfterFailure = false
    XCUIDevice.shared.orientation = .portrait
  }
  private func launch(_ flags: [String] = []) -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString,
      "--fixture-reduce-motion", "--route", "study", "--fixture-upload-progress"] + flags
    app.launch()
    XCTAssertTrue(app.buttons["upload.background"].waitForExistence(timeout: 15))
    return app
  }
  private func evidence(_ name: String, app: XCUIApplication) {
    let shot = XCTAttachment(screenshot: app.screenshot())
    shot.name = name; shot.lifetime = .keepAlways; add(shot)
  }
  func testPerTextbookCardsAndCourseSwitcherRestoreTheRunningUpload() {
    let app = launch()
    XCTAssertTrue(app.staticTexts["高中数学必修第二册.pdf"].exists)
    XCTAssertTrue(app.staticTexts["高中数学学习资料.pdf"].exists)
    XCTAssertTrue(app.staticTexts["完成"].exists)
    XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "没有新的进展")).firstMatch.exists)
    evidence("upload-per-textbook-cards", app: app)
    app.buttons["upload.background"].tap()
    let switcher = app.buttons["study.course.switch"]
    XCTAssertTrue(switcher.waitForExistence(timeout: 10)); switcher.tap()
    let uploadingCourse = app.buttons["study.course.local-course:fixture-upload"]
    XCTAssertTrue(uploadingCourse.waitForExistence(timeout: 10)); uploadingCourse.tap()
    XCTAssertTrue(app.buttons["upload.background"].waitForExistence(timeout: 10))
    XCTAssertTrue(app.staticTexts["高中数学学习资料.pdf"].exists)
  }
  func testBackgroundSuccessShowsAnActionablePopupWithoutChangingTheCurrentCourse() {
    let app = launch(["--fixture-upload-finish"])
    app.buttons["upload.background"].tap()
    let course = app.staticTexts["study.course.title"].label
    let alert = app.alerts["上传成功"]
    XCTAssertTrue(alert.waitForExistence(timeout: 20))
    XCTAssertTrue(alert.buttons["继续整理"].exists)
    XCTAssertTrue(alert.buttons["稍后处理"].exists)
    evidence("upload-background-success-popup", app: app)
    alert.buttons["稍后处理"].tap()
    XCTAssertEqual(app.staticTexts["study.course.title"].label, course)
    XCTAssertFalse(app.alerts["上传成功"].exists)
  }
  func testSlowUploadUsesATransientToastAndCancellationRetainsCompletedCards() {
    let app = launch(["--fixture-upload-slow"])
    let toast = app.descendants(matching: .any)["app.toast"].firstMatch
    XCTAssertTrue(toast.waitForExistence(timeout: 8))
    XCTAssertEqual(toast.label, "上传较慢，请保持网络连接")
    evidence("upload-slow-transient-toast", app: app)
    XCTAssertTrue(toast.waitForNonExistence(timeout: 6))
    XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "没有新的进展")).firstMatch.exists)
    app.buttons["upload.cancel"].tap()
    XCTAssertTrue(app.buttons["upload.retry"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["完成"].exists)
    XCTAssertTrue(app.staticTexts["已取消"].exists)
  }

  func testForegroundSuccessCanContinueToCoursePreparation() {
    let app = launch(["--fixture-upload-finish"])
    let alert = app.alerts["上传成功"]
    XCTAssertTrue(alert.waitForExistence(timeout: 20))
    alert.buttons["继续整理"].tap()
    XCTAssertTrue(app.staticTexts["本机识别原文"].waitForExistence(timeout: 15))
    XCTAssertEqual(app.staticTexts["navigation.title"].label, "解析教材")
    XCTAssertFalse(app.buttons["upload.background"].exists)
  }
}
