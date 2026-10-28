import XCTest

final class StudyNavigationUITests: XCTestCase {
  override func setUpWithError() throws {
    continueAfterFailure = false
    XCUIDevice.shared.orientation = .portrait
  }

  private func launch(_ route: String, chapter: String? = nil) -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString,
      "--fixture-reduce-motion", "--route", route]
    if let chapter { app.launchArguments += ["--chapter", chapter] }
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

  private func evidence(_ name: String, app: XCUIApplication) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    add(attachment)
  }

  func testStudyReplacesHomeAndCreatesANewCourse() {
    let app = launch("home")
    let create = app.buttons["study.course.create"]
    XCTAssertTrue(app.buttons["study.course.switch"].waitForExistence(timeout: 20))
    let scroll = app.scrollViews["study.scroll"]
    for _ in 0..<8 {
      if create.exists && create.isHittable { break }
      scroll.swipeDown(velocity: .fast)
    }
    XCTAssertTrue(create.exists && create.isHittable)
    XCTAssertFalse(app.buttons["tab-home"].exists)
    XCTAssertTrue(app.buttons["tab-study"].exists)
    XCTAssertTrue(app.buttons["tab-community"].exists)
    XCTAssertTrue(app.buttons["tab-profile"].exists)
    XCTAssertFalse(app.buttons["添加资料"].exists)
    XCTAssertFalse(app.buttons["回到原书"].exists)
    XCTAssertFalse(app.buttons["继续学习"].exists)
    evidence("study-existing-style-ready", app: app)
    tap(create, app: app)
    XCTAssertTrue(app.buttons["upload.select"].waitForExistence(timeout: 10))
  }

  func testAllCoursesProvidesAddingMaterialsForTheChosenCourse() {
    let app = launch("study")
    tap(app.buttons["study.course.switch"], app: app)
    tap(app.buttons["全部课程"], app: app)
    XCTAssertTrue(app.staticTexts["全部课程"].firstMatch.waitForExistence(timeout: 10))
    let add = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "library.addMaterials.")).firstMatch
    evidence("study-all-courses-add-materials", app: app)
    tap(add, app: app)
    XCTAssertTrue(app.buttons["upload.select"].waitForExistence(timeout: 10))
    XCTAssertFalse(app.buttons["课程资料与偏好"].exists)
  }

  func testReturningFromLessonAutomaticallyExpandsTheLastStudiedSection() {
    let app = launch("lesson", chapter: "c2s1")
    tap(app.buttons["navigation.back"], app: app)
    let tool = app.buttons["study.section.c2s1.flashcards"]
    XCTAssertTrue(tool.waitForExistence(timeout: 15))
    XCTAssertTrue(app.buttons["study.chapter.c2"].exists)
    evidence("study-restored-last-lesson", app: app)
    tap(app.buttons["tab-profile"], app: app)
    tap(app.buttons["tab-study"], app: app)
    XCTAssertTrue(tool.waitForExistence(timeout: 10))
  }

  func testUnfinishedCourseReplacesTheDirectoryAndResumesSavedSetup() {
    let app = launch("courseSetup")
    tap(app.buttons["上一步"], app: app)
    let resume = app.buttons["study.preparation.resume"]
    XCTAssertTrue(resume.waitForExistence(timeout: 15))
    XCTAssertFalse(app.descendants(matching: .any)["study.plan.progress"].exists)
    XCTAssertFalse(app.buttons["study.chapter.c1"].exists)
    XCTAssertTrue(app.buttons["study.course.create"].exists)
    evidence("study-existing-style-unfinished", app: app)
    tap(resume, app: app)
    XCTAssertTrue(app.buttons["chapters.confirm"].waitForExistence(timeout: 10))
  }
}
