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

  func testAnimatedStartupHandsOffToAnInteractiveStudyPage() {
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString,
      "--route", "study"]
    app.launch()
    assertStartupHandsOff(app)
  }

  func testReducedMotionStartupHandsOffToAnInteractiveStudyPage() {
    assertStartupHandsOff(launch("study"))
  }

  private func assertStartupHandsOff(_ app: XCUIApplication) {
    let course = app.buttons["study.course.switch"]
    XCTAssertTrue(course.waitForExistence(timeout: 20))
    XCTAssertTrue(app.images["app.loading"].waitForNonExistence(timeout: 10),
      "The startup layer must finish and release the app after loading")
    tap(course, app: app)
    XCTAssertTrue(app.buttons["全部课程"].waitForExistence(timeout: 10),
      "The dismissed startup layer must not intercept course selection")
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
    XCTAssertTrue(app.buttons["tab-study"].waitForExistence(timeout: 10))
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
    assertExpandedEntry("study.chapter.c2", app: app)
    assertRestoredTitleVisible("study.chapter.c2", app: app)
    assertRestoredTitleVisible("study.section.c2s1.title", app: app)
    evidence("study-restored-last-lesson", app: app)
    tap(app.buttons["tab-profile"], app: app)
    tap(app.buttons["tab-study"], app: app)
    XCTAssertTrue(tool.waitForExistence(timeout: 10))
    assertRestoredTitleVisible("study.section.c2s1.title", app: app)
  }

  private func assertRestoredTitleVisible(_ identifier: String, app: XCUIApplication) {
    let title = app.buttons[identifier]
    let plan = app.descendants(matching: .any)["study.plan.card"].firstMatch
    let visible = NSPredicate { _, _ in
      title.exists && plan.exists && title.isHittable
        && title.frame.minY >= plan.frame.maxY
        && title.frame.maxY < app.frame.maxY - 80
    }
    let check = XCTNSPredicateExpectation(predicate: visible, object: app)
    XCTAssertEqual(XCTWaiter.wait(for: [check], timeout: 10), .completed,
      "Restored title \(title.frame) must be below progress \(plan.frame)")
  }

  private func assertExpandedEntry(_ identifier: String, app: XCUIApplication) {
    let title = app.buttons[identifier]
    let plan = app.descendants(matching: .any)["study.plan.card"].firstMatch
    let ready = NSPredicate { _, _ in
      title.exists && title.isHittable && plan.exists
        && app.buttons["计划详情"].firstMatch.isHittable
        && title.frame.minY >= plan.frame.maxY
        && title.frame.minY - plan.frame.maxY <= 24
    }
    XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: ready, object: app)],
      timeout: 15), .completed,
      "Entry must show the expanded plan immediately followed by the current title")
  }

  func testAnimatedReturnAndTabSwitchKeepTheRestoredTitleStable() {
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString,
      "--route", "lesson", "--chapter", "c2s2"]
    app.launch()
    tap(app.buttons["navigation.back"], app: app)
    assertRestoredTitleStable("study.section.c2s2.title", app: app)
    tap(app.buttons["tab-profile"], app: app)
    tap(app.buttons["tab-study"], app: app)
    assertRestoredTitleStable("study.section.c2s2.title", app: app)
    evidence("study-restored-without-position-jumps", app: app)
  }

  private func assertRestoredTitleStable(_ identifier: String, app: XCUIApplication) {
    assertExpandedEntry(identifier, app: app)
    assertRestoredTitleVisible(identifier, app: app)
    let title = app.buttons[identifier]
    let plan = app.descendants(matching: .any)["study.plan.card"].firstMatch
    let initialTitle = title.frame
    let initialPlan = plan.frame
    // Sample beyond the former delayed second scroll and plan transition.
    for _ in 0..<6 {
      Thread.sleep(forTimeInterval: 0.1)
      XCTAssertEqual(title.frame.minY, initialTitle.minY, accuracy: 1)
      XCTAssertEqual(plan.frame.maxY, initialPlan.maxY, accuracy: 1)
      XCTAssertTrue(title.isHittable)
    }
  }

  func testOpeningStudyAlignsALaterSectionTitleBelowThePinnedPlan() {
    let app = launch("study", chapter: "c2s2")
    assertExpandedEntry("study.section.c2s2.title", app: app)
    evidence("study-restored-later-section-title", app: app)
  }

  func testMathDirectoryRemainsUsableAcrossTabSwitches() {
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", UUID().uuidString,
      "--fixture-reduce-motion", "--route", "study",
      "--book", "catalog_high_school_math_required_2", "--chapter", "math-c6-s1"]
    app.launch()
    let chapter = app.buttons["study.chapter.math-c6"]
    let visible = NSPredicate { _, _ in chapter.exists && chapter.isHittable }
    for _ in 0..<2 {
      XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: visible, object: app)],
        timeout: 15), .completed, "The course body must remain visible and interactive")
      assertExpandedEntry("study.chapter.math-c6", app: app)
      tap(app.buttons["tab-profile"], app: app)
      tap(app.buttons["tab-study"], app: app)
    }
    evidence("study-math-directory-visible", app: app)
    tap(app.buttons["study.course.switch"], app: app)
    XCTAssertTrue(app.buttons["全部课程"].waitForExistence(timeout: 10))
  }

  func testProgressCardExpandsAndCollapsesWithoutAnEmptyHeaderSpacer() {
    let app = launch("study", chapter: "c1s1")
    let plan = app.descendants(matching: .any)["study.plan.card"].firstMatch
    let scroll = app.scrollViews["study.scroll"]
    let chapter = app.buttons["study.chapter.c1"]
    assertExpandedEntry("study.chapter.c1", app: app)
    let expandedHeight = plan.frame.height
    scroll.swipeUp(velocity: .slow)
    let compact = NSPredicate { _, _ in
      plan.frame.height < expandedHeight - 60 && !app.buttons["计划详情"].firstMatch.exists
    }
    XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: compact, object: app)],
      timeout: 5), .completed)
    XCTAssertTrue(plan.isHittable)
    evidence("study-progress-collapsed-no-gap", app: app)
    for _ in 0..<6 {
      if app.buttons["计划详情"].firstMatch.isHittable { break }
      scroll.swipeDown(velocity: .fast)
    }
    let expanded = NSPredicate { _, _ in
      abs(plan.frame.height - expandedHeight) < 2 && app.buttons["计划详情"].firstMatch.isHittable
    }
    XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: expanded, object: app)],
      timeout: 5), .completed)
    XCTAssertLessThanOrEqual(chapter.frame.minY - plan.frame.maxY, 24)
    evidence("study-progress-expanded-no-gap", app: app)
  }

  func testSwitchingBackReopensPlanAndRestoresCurrentTitleAfterScrolling() {
    let app = launch("study", chapter: "c2s2")
    assertExpandedEntry("study.section.c2s2.title", app: app)
    app.scrollViews["study.scroll"].swipeUp(velocity: .slow)
    XCTAssertTrue(app.buttons["计划详情"].firstMatch.waitForNonExistence(timeout: 5))
    tap(app.buttons["tab-profile"], app: app)
    tap(app.buttons["tab-study"], app: app)
    assertExpandedEntry("study.section.c2s2.title", app: app)
    evidence("study-reentry-expanded-current-title", app: app)
  }

  func testManualScrollingInEitherDirectionCollapsesTheEntryPlan() {
    for direction in [-1.0, 1.0] {
      let app = launch("study", chapter: "c3s1")
      assertExpandedEntry("study.chapter.c3", app: app)
      let scroll = app.scrollViews["study.scroll"]
      let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4))
      start.press(forDuration: 0.05,
        thenDragTo: start.withOffset(CGVector(dx: 0, dy: direction * 70)),
        withVelocity: .slow, thenHoldForDuration: 0.2)
      XCTAssertTrue(app.buttons["计划详情"].firstMatch.waitForNonExistence(timeout: 5),
        "A manual scroll away from the directory top must collapse the entry plan")
    }
  }

  func testSwitchingCoursesReopensThePlanAndRestoresEachCurrentTitle() {
    let app = launch("study", chapter: "c2s2")
    assertExpandedEntry("study.section.c2s2.title", app: app)
    app.scrollViews["study.scroll"].swipeUp(velocity: .slow)
    XCTAssertTrue(app.buttons["计划详情"].firstMatch.waitForNonExistence(timeout: 5))
    tap(app.buttons["study.course.switch"], app: app)
    tap(app.buttons["study.course.course:catalog_high_school_math_required_2"], app: app)
    assertExpandedEntry("study.chapter.math-c6", app: app)
    tap(app.buttons["study.course.switch"], app: app)
    tap(app.buttons["study.course.course:book_biology_2"], app: app)
    assertExpandedEntry("study.section.c2s2.title", app: app)
    evidence("study-course-switch-expanded-current-title", app: app)
  }

  func testReturningToTopExpandsProgressWithoutAnExtraPull() {
    let app = launch("study", chapter: "c2s1")
    let scroll = app.scrollViews["study.scroll"]
    let firstChapter = app.buttons["study.chapter.c1"]
    let plan = app.descendants(matching: .any)["study.plan.card"].firstMatch
    XCTAssertTrue(firstChapter.waitForExistence(timeout: 15))
    assertExpandedEntry("study.chapter.c2", app: app)
    scroll.swipeUp(velocity: .slow)
    XCTAssertTrue(app.buttons["计划详情"].firstMatch.waitForNonExistence(timeout: 5))
    let compactHeight = plan.frame.height

    // Include touch-recognition slop in each drag, then hold before releasing.
    // Do not depend on a separate rubber-band pull after returning to the top.
    for _ in 0..<12 {
      let remaining = scroll.frame.minY - firstChapter.frame.minY
      if remaining <= 1 { break }
      let distance = min(remaining + 12, scroll.frame.height * 0.4)
      let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.2))
      start.press(forDuration: 0.05,
        thenDragTo: start.withOffset(CGVector(dx: 0, dy: distance)),
        withVelocity: .slow, thenHoldForDuration: 0.3)
    }
    let expanded = NSPredicate { _, _ in
      plan.frame.height > compactHeight + 60
        && app.buttons["计划详情"].firstMatch.isHittable
    }
    XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: expanded, object: app)],
      timeout: 5), .completed, "Reaching the top must expand the progress card without another drag")
    // Touch recognition can leave the last few points of a short drag
    // unconsumed. Expansion must already work in this near-top position.
    XCTAssertGreaterThanOrEqual(scroll.frame.minY - firstChapter.frame.minY, -2)
    XCTAssertLessThanOrEqual(scroll.frame.minY - firstChapter.frame.minY, 16)
    XCTAssertTrue(firstChapter.isHittable)
    XCTAssertLessThanOrEqual(firstChapter.frame.minY - plan.frame.maxY, 24)
    evidence("study-returned-to-top-expanded", app: app)
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
    // The containing preparation card can supply the accessibility identifier
    // on this runtime; the visible action label remains stable.
    let resume = app.buttons.matching(NSPredicate(format: "identifier == %@ OR label == %@",
      "study.preparation.resume", "继续")).firstMatch
    XCTAssertTrue(resume.waitForExistence(timeout: 15))
    XCTAssertFalse(app.descendants(matching: .any)["study.plan.progress"].exists)
    XCTAssertFalse(app.buttons["study.chapter.c1"].exists)
    XCTAssertTrue(app.buttons["study.course.create"].exists)
    evidence("study-existing-style-unfinished", app: app)
    tap(resume, app: app)
    XCTAssertTrue(app.buttons["chapters.confirm"].waitForExistence(timeout: 10))
  }
}
