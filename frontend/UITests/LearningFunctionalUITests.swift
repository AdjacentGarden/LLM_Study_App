import XCTest

/// Real gestures, form input, navigation and relaunches against bundled content.
/// Tests use a fresh persisted fixture session and do not inject learning results.
final class LearningFunctionalUITests: XCTestCase {
  override func setUpWithError() throws {
    continueAfterFailure = false
    XCUIDevice.shared.orientation = .portrait
  }

  private func launch(_ route: String, session: String, reduceMotion: Bool = false) -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments = [
      "--fixture", "--fixture-session", session, "--route", route, "--chapter", "c2s1",
    ]
    if reduceMotion { app.launchArguments.append("--fixture-reduce-motion") }
    app.launch()
    XCTAssertTrue(app.wait(for: .runningForeground, timeout: 20))
    return app
  }

  private func wait(
    _ message: String, timeout: TimeInterval = 12, _ condition: @escaping () -> Bool
  ) {
    let expectation = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in condition() }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: timeout), .completed, message)
  }

  private func reveal(_ element: XCUIElement, app: XCUIApplication) {
    _ = element.waitForExistence(timeout: 2)
    let gutter: CGFloat = app.windows.firstMatch.frame.width < 700 ? 0.08 : 0.95
    for _ in 0..<16 {
      // LazyVGrid descendants do not exist in the accessibility tree until
      // scrolled near the viewport, especially on iPhone.
      if element.exists && element.isHittable { return }
      app.coordinate(withNormalizedOffset: CGVector(dx: gutter, dy: 0.78))
        .press(
          forDuration: 0.05,
          thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: gutter, dy: 0.30)))
    }
    XCTAssertTrue(element.exists, "Missing control after scrolling: \(element)")
    XCTAssertTrue(element.isHittable, "Control remained outside viewport: \(element)")
  }

  private func tap(_ element: XCUIElement, app: XCUIApplication) {
    reveal(element, app: app)
    XCTAssertTrue(element.isEnabled)
    element.tap()
  }

  private func dismissKeyboard(_ app: XCUIApplication) {
    let keyboard = app.keyboards.firstMatch
    guard keyboard.exists else { return }
    let hide = keyboard.buttons.matching(
      NSPredicate(format: "label IN %@", ["Hide keyboard", "隐藏键盘", "收起键盘"])
    ).firstMatch
    let done = app.buttons["learning.keyboard.done"].firstMatch
    if done.exists {
      XCTAssertTrue(
        done.isHittable, "Completion control should remain reachable above the keyboard")
      done.tap()
    } else if hide.exists {
      hide.tap()
    } else {
      XCTFail("Expected a reachable completion control or the system Hide Keyboard key")
    }
    wait("The system keyboard should dismiss before scrolling") { !keyboard.exists }
  }

  private func evidence(_ name: String, app: XCUIApplication) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    add(attachment)
  }

  func testStudyPlanCompactsOnScrollAndExpandsAtTop() throws {
    let app = launch("study", session: UUID().uuidString)
    let details = app.buttons["计划详情"].firstMatch
    let progress = app.descendants(matching: .any)["study.plan.progress"].firstMatch
    let scroll = app.scrollViews["study.scroll"]
    XCTAssertTrue(details.waitForExistence(timeout: 20))
    XCTAssertTrue(progress.exists)
    let expandedProgressY = progress.frame.minY
    let courseSwitch = app.buttons["study.course.switch"].firstMatch
    let initialCourseFrame = courseSwitch.frame
    let initialProgressValue = progress.value as? String
    evidence("study-plan-expanded", app: app)

    scroll.swipeUp(velocity: .slow)
    evidence("study-plan-after-scroll", app: app)
    wait("Scrolling should hide the plan details and keep only its pinned progress") {
      !details.exists && progress.exists && progress.frame.minY < expandedProgressY - 40
    }
    XCTAssertEqual(progress.value as? String, initialProgressValue)
    XCTAssertEqual(courseSwitch.frame.minY, initialCourseFrame.minY, accuracy: 1)
    XCTAssertTrue(courseSwitch.isHittable)

    for _ in 0..<6 {
      if details.exists && details.isHittable { break }
      scroll.swipeDown(velocity: .fast)
    }
    wait("Returning to the top should expand the plan again") {
      details.exists && details.isHittable && abs(progress.frame.minY - expandedProgressY) < 2
    }
    evidence("study-plan-expanded-again", app: app)
    details.tap()
    XCTAssertTrue(app.staticTexts["学习计划"].firstMatch.waitForExistence(timeout: 8))
  }

  func testStudyPlanThresholdsAndChapterTapsWithReducedMotion() throws {
    let app = launch("study", session: UUID().uuidString, reduceMotion: true)
    let details = app.buttons["计划详情"].firstMatch
    XCTAssertTrue(details.waitForExistence(timeout: 20))
    let scroll = app.scrollViews["study.scroll"]
    let chapter = app.buttons["study.chapter.c1"]
    // Set up the same directory state on both device sizes before measuring.
    if chapter.value as? String == "已展开" { chapter.tap() }
    let nextChapter = app.buttons["study.chapter.c2"]
    if nextChapter.value as? String != "已展开" { nextChapter.tap() }
    let initialChapterTop = chapter.frame.minY
    let expandedPlanBottom = initialChapterTop - 8
    let progress = app.descendants(matching: .any)["study.plan.progress"].firstMatch
    let expandedProgressY = progress.frame.minY

    func drag(upBy distance: CGFloat) {
      let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.55))
      start.press(
        forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: -distance)),
        withVelocity: .slow, thenHoldForDuration: 0.25)
    }

    drag(upBy: 30)
    XCTAssertTrue(details.exists, "A small scroll should leave the plan expanded")
    drag(upBy: 90)
    wait("Passing the collapse threshold should compact the plan without motion") {
      !details.exists && progress.frame.minY < expandedProgressY - 40
    }
    let compactProgressY = progress.frame.minY
    XCTAssertGreaterThan(chapter.frame.midY, progress.frame.maxY + 10)
    XCTAssertLessThan(
      chapter.frame.midY, expandedPlanBottom,
      "The chapter must be underneath the area released by the expanded plan")
    XCTAssertTrue(chapter.isHittable)
    XCTAssertEqual(chapter.value as? String, "已收起")
    chapter.tap()
    wait("The invisible expanded placeholder must not intercept chapter taps") {
      chapter.value as? String == "已展开"
    }
    XCTAssertFalse(details.exists, "Opening a chapter should not re-expand the pinned plan")
    XCTAssertEqual(progress.frame.minY, compactProgressY, accuracy: 1)
    // Gesture recognition consumes a few points differently on each device.
    // Measure the actual offset using the first chapter's stable document position.
    let scrollOffset = initialChapterTop - chapter.frame.minY
    if scrollOffset > 48 { drag(upBy: -(scrollOffset - 30)) }
    let partialOffset = initialChapterTop - chapter.frame.minY
    XCTAssertGreaterThan(partialOffset, 16)
    XCTAssertLessThanOrEqual(partialOffset, 48)
    XCTAssertFalse(details.exists, "Scrolling back partway should keep the plan compact")
    drag(upBy: -160)
    wait("Returning to the top should restore the full plan with reduced motion") {
      details.exists && details.isHittable
    }
    evidence("study-plan-reduced-motion-restored", app: app)
  }

  func testAssignmentDiagnosisMistakeRedoAndMasteryPersist() throws {
    let session = UUID().uuidString
    let app = launch("assignment", session: session)
    let submit = app.buttons["assignment.submit"].firstMatch
    XCTAssertTrue(submit.waitForExistence(timeout: 20))
    XCTAssertFalse(submit.isEnabled, "Unanswered objective questions must not advance")
    tap(app.buttons["assignment.judgment.错误"], app: app)
    tap(submit, app: app)
    XCTAssertTrue(app.buttons["assignment.choice.A"].waitForExistence(timeout: 10))
    XCTAssertFalse(submit.isEnabled)
    tap(app.buttons["assignment.choice.A"], app: app)
    tap(submit, app: app)
    let shortAnswer = app.textViews.firstMatch
    reveal(shortAnswer, app: app)
    shortAnswer.tap()
    shortAnswer.typeText("Functional test: a deliberately incomplete meiosis explanation.")
    XCTAssertTrue((shortAnswer.value as? String ?? "").contains("deliberately incomplete"))
    dismissKeyboard(app)
    tap(submit, app: app)
    XCTAssertTrue(app.staticTexts["诊断解析"].waitForExistence(timeout: 15))
    XCTAssertTrue(app.staticTexts["需要再巩固"].exists)
    evidence("learning-submitted-real-answers-diagnosis", app: app)
    tap(app.buttons["diagnosis.continue"], app: app)
    // Select the new objective-error record produced by this submission, not
    // the pre-existing demonstration mistake seeded with the textbook.
    let recordID = "mistake_quiz_c2s1_01"
    tap(app.buttons["mistake.select." + recordID], app: app)
    let redoID = "mistake.redo." + recordID
    let redo = app.buttons[redoID]
    reveal(redo, app: app)
    redo.tap()
    let compare = app.buttons["mistake.compare"]
    tap(compare, app: app)
    XCTAssertTrue(app.staticTexts["请先写下你的答案，再对照解析。"].waitForExistence(timeout: 5))
    let answer = app.textViews.firstMatch
    reveal(answer, app: app)
    answer.tap()
    answer.typeText("Homologous chromosomes separate first; sister chromatids separate later.")
    XCTAssertTrue((answer.value as? String ?? "").contains("Homologous"))
    dismissKeyboard(app)
    tap(compare, app: app)
    XCTAssertTrue(app.staticTexts["我的新答案"].waitForExistence(timeout: 10))
    tap(app.buttons["概念混淆"], app: app)
    let mastered = app.buttons["mistake.mastery.mastered"]
    tap(mastered, app: app)
    wait("Mastery selection should update") { mastered.value as? String == "selected" }
    evidence("learning-mistake-redone-and-mastered", app: app)
    tap(app.buttons["mistake.finish"], app: app)
    app.terminate()

    let reopened = launch("mistakes", session: session)
    tap(reopened.buttons["mistake.select." + recordID], app: reopened)
    tap(reopened.buttons[redoID], app: reopened)
    tap(reopened.buttons["填入上次答案"], app: reopened)
    tap(reopened.buttons["mistake.compare"], app: reopened)
    let restoredMastery = reopened.buttons["mistake.mastery.mastered"]
    reveal(restoredMastery, app: reopened)
    XCTAssertEqual(
      restoredMastery.value as? String, "selected", "Mastery must survive an application relaunch")
    evidence("learning-mistake-mastery-restored", app: reopened)
  }

  private func turnPage(_ app: XCUIApplication, forward: Bool) {
    let start = app.coordinate(withNormalizedOffset: CGVector(dx: forward ? 0.77 : 0.23, dy: 0.42))
    let end = app.coordinate(withNormalizedOffset: CGVector(dx: forward ? 0.23 : 0.77, dy: 0.42))
    start.press(forDuration: 0.08, thenDragTo: end)
  }

  private func returnToIntroduction(_ app: XCUIApplication) {
    let page = app.staticTexts["lesson.page"].firstMatch
    XCTAssertTrue(page.waitForExistence(timeout: 20))
    for _ in 0..<5 {
      if page.value as? String == "1" { return }
      let previous = page.value as? String
      turnPage(app, forward: false)
      wait("Backward page gesture should restore the introduction") {
        page.value as? String != previous
      }
    }
    XCTAssertEqual(page.value as? String, "1")
  }

  func testLessonPagingConceptDismissalAndCompletion() throws {
    let session = UUID().uuidString
    let app = launch("lesson", session: session)
    let page = app.staticTexts["lesson.page"].firstMatch
    XCTAssertTrue(page.waitForExistence(timeout: 20))
    returnToIntroduction(app)
    XCTAssertEqual(page.value as? String, "1")
    tap(app.buttons["lesson.concept"], app: app)
    let close = app.buttons["lesson.concept.close"]
    XCTAssertTrue(close.waitForExistence(timeout: 8))
    XCTAssertTrue(app.staticTexts["核心概念"].exists)
    close.tap()
    wait("Concept overlay should dismiss") { !close.exists }
    turnPage(app, forward: true)
    wait("Forward gesture should change the actual lesson page") { page.value as? String == "2" }
    turnPage(app, forward: false)
    wait("Backward gesture should restore introduction") { page.value as? String == "1" }
    for number in 2...5 {
      turnPage(app, forward: true)
      wait("Lesson page \(number) should be reachable") { page.value as? String == String(number) }
    }
    evidence("learning-last-lesson-page", app: app)
    tap(app.buttons["完成本节"].firstMatch, app: app)
    XCTAssertTrue(app.staticTexts["学习计划"].firstMatch.waitForExistence(timeout: 10))
    app.terminate()
    let reopened = launch("lesson", session: session)
    XCTAssertTrue(reopened.staticTexts["lesson.page"].firstMatch.waitForExistence(timeout: 20))
    XCTAssertEqual(
      reopened.staticTexts["lesson.page"].firstMatch.value as? String, "5",
      "Reading position must survive relaunch")
  }

  func testMediaSheetRequiresConfiguredBackendAndClosingPreservesReadingPosition() {
    let app = launch("lesson", session: UUID().uuidString)
    returnToIntroduction(app)
    let open = app.buttons["lesson.video.open.asset_ai_meiosis_dna_replication_v1"]
    let initialPage = app.staticTexts["lesson.page"].firstMatch.value as? String
    tap(open, app: app)
    XCTAssertTrue(app.staticTexts["lesson.media.error"].waitForExistence(timeout: 15))
    XCTAssertFalse(app.buttons["lesson.media.generate"].exists,
      "Fixture data must not submit an unconfigured provider job")
    XCTAssertEqual(app.buttons.matching(identifier: "lesson.media.close").count, 1)
    evidence("learning-single-cloud-media-sheet", app: app)
    tap(app.buttons["lesson.media.close"], app: app)
    XCTAssertEqual(app.staticTexts["lesson.page"].firstMatch.value as? String, initialPage)
    tap(open, app: app)
    XCTAssertTrue(app.staticTexts["lesson.media.error"].waitForExistence(timeout: 10))
    XCTAssertFalse(app.buttons["lesson.media.generate"].exists)
    tap(app.buttons["lesson.media.close"], app: app)
  }
}
