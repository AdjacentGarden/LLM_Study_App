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

  func testChapterGlassToolsKeepAllFiveDestinations() {
    let entries = [
      ("flashcards", "知识点闪卡"), ("assignment", "作业练习"),
      ("mistakes", "错题集"), ("notes", "学习笔记"), ("lesson", ""),
    ]
    for (index, entry) in entries.enumerated() {
      let app = launch("study", chapter: "c2s1")
      let button = app.buttons["study.section.c2s1." + entry.0]
      XCTAssertTrue(button.waitForExistence(timeout: 20))
      if index == 0 {
        let tools = app.descendants(matching: .any)["study.section.c2s1.tools"].firstMatch
        let plan = app.descendants(matching: .any)["study.plan.card"].firstMatch
        let scroll = app.scrollViews["study.scroll"]
        // Align the local tool panel below the pinned progress before capturing.
        let delta = tools.frame.minY - plan.frame.maxY - 12
        if delta > 15 {
          let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.7))
          start.press(forDuration: 0.05,
            thenDragTo: start.withOffset(CGVector(dx: 0, dy: -min(delta, 350))),
            withVelocity: .slow, thenHoldForDuration: 0.2)
        }
        evidence("study-section-layered-glass-tools", app: app)
      }
      tap(button, app: app)
      if entry.0 == "lesson" {
        XCTAssertTrue(app.staticTexts["lesson.page"].firstMatch.waitForExistence(timeout: 15))
      } else {
        let title = app.staticTexts["navigation.title"].firstMatch
        XCTAssertTrue(title.waitForExistence(timeout: 15))
        XCTAssertEqual(title.label, entry.1)
      }
    }
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
