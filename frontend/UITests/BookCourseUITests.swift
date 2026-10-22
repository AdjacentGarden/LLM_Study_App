import XCTest
import UIKit

final class BookCourseUITests: XCTestCase {
  override func setUpWithError() throws { continueAfterFailure = false }
  private func launch(
    _ route: String, sheet: String? = nil, session: String = UUID().uuidString,
    chapter: String = "c2s1", visual: Bool = false
  ) -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments = [
      "--fixture", "--fixture-session", session, "--route", route, "--chapter", chapter,
    ]
    if visual { app.launchArguments += ["--fixture-reduce-motion", "--visual-baseline"] }
    if let sheet { app.launchArguments += ["--sheet", sheet] }
    app.launch()
    XCTAssertTrue(app.wait(for: .runningForeground, timeout: 20))
    return app
  }
  func testTabsAndCourseLearningEntry() throws {
    let app = launch("home")
    let study = app.buttons["tab-study"]
    XCTAssertTrue(study.waitForExistence(timeout: 20))
    study.tap()
    XCTAssertTrue(app.staticTexts["学习计划"].firstMatch.waitForExistence(timeout: 8))
    app.buttons["tab-community"].tap()
    XCTAssertTrue(app.textFields["community.search"].waitForExistence(timeout: 8))
    app.buttons["tab-profile"].tap()
    XCTAssertTrue(app.staticTexts["PROFILE"].firstMatch.waitForExistence(timeout: 8))
  }

  func testProfileHeaderCentersOnScrollAndRestoresSettingsAtTop() throws {
    XCUIDevice.shared.orientation = .portrait
    let app = launch("profile")
    let title = app.staticTexts["profile.title"]
    let settings = app.buttons["profile.settings"]
    let scroll = app.scrollViews.firstMatch
    XCTAssertTrue(title.waitForExistence(timeout: 20))
    XCTAssertTrue(settings.isHittable)
    let lastAction = app.buttons["查看今日计划"].firstMatch
    if lastAction.isHittable && lastAction.frame.maxY < scroll.frame.maxY - 20 {
      // On tall layouts the default cards can fit without scrolling. Expand
      // preferences to exercise a real scroll rather than a transient bounce.
      app.buttons["编辑学习偏好"].tap()
      XCTAssertTrue(app.staticTexts["每日学习时间"].waitForExistence(timeout: 10))
    }
    let initialY = title.frame.minY
    let contentCenter = scroll.frame.midX
    XCTAssertLessThan(title.frame.midX, contentCenter - 50)
    XCTAssertGreaterThan(settings.frame.midX, contentCenter + 50)
    keepScreenshot("profile-header-at-top", app: app)

    scroll.swipeUp(velocity: .slow)
    let compact = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        title.isHittable && !settings.exists
          && abs(title.frame.midX - contentCenter) < 2
          && abs(title.frame.minY - initialY) < 2
      }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [compact], timeout: 10), .completed)
    keepScreenshot("profile-header-scrolled", app: app)

    for _ in 0..<5 {
      if settings.exists && settings.isHittable { break }
      scroll.swipeDown(velocity: .fast)
    }
    let expanded = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        settings.isHittable && title.frame.midX < contentCenter - 50
          && abs(title.frame.minY - initialY) < 2
      }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [expanded], timeout: 10), .completed)
    keepScreenshot("profile-header-restored", app: app)
    settings.tap()
    XCTAssertTrue(app.staticTexts["设置"].firstMatch.waitForExistence(timeout: 10))
    app.buttons["返回"].firstMatch.tap()
    XCTAssertTrue(settings.waitForExistence(timeout: 10))
    XCTAssertTrue(settings.isHittable)
  }

  func testStudySheetsPreserveStatusBarAndUnderlyingScrollPosition() throws {
    XCUIDevice.shared.orientation = .portrait
    let app = launch("study")
    let addMaterials = app.buttons["添加资料"].firstMatch
    let switchCourse = app.buttons["study.course.switch"]
    XCTAssertTrue(addMaterials.waitForExistence(timeout: 20))
    let original = switchCourse.frame

    func assertStatusBar(dimmed: Bool) throws {
      let image = try XCTUnwrap(XCUIScreen.main.screenshot().image.cgImage)
      let scale = CGFloat(image.width) / app.windows.firstMatch.frame.width
      let context = try XCTUnwrap(CGContext(
        data: nil, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
      let expected = dimmed ? [148, 151, 162] : [246, 248, 251]
      for fraction in [0.30, 0.70] {
        for y: CGFloat in [4, 16] {
          let pixel = try XCTUnwrap(image.cropping(to: CGRect(
            x: CGFloat(image.width) * fraction, y: y * scale, width: 1, height: 1)))
          context.draw(pixel, in: CGRect(x: 0, y: 0, width: 1, height: 1))
          let bytes = try XCTUnwrap(context.data).assumingMemoryBound(to: UInt8.self)
          for channel in 0..<3 {
            XCTAssertEqual(Int(bytes[channel]), expected[channel], accuracy: 3)
          }
        }
      }
    }

    try assertStatusBar(dimmed: false)
    for index in 0..<2 {
      addMaterials.tap()
      XCTAssertTrue(app.buttons["upload.select"].waitForExistence(timeout: 10))
      XCTAssertTrue(app.buttons["upload.select"].isHittable)
      let close = app.buttons["overlay-close"]
      XCTAssertTrue(close.isHittable)
      XCTAssertGreaterThan(app.buttons["upload.select"].frame.minY, close.frame.maxY)
      try assertStatusBar(dimmed: true)
      keepScreenshot("study-add-materials-\(index)", app: app)
      app.buttons["overlay-close"].tap()
      XCTAssertTrue(app.buttons["overlay-close"].waitForNonExistence(timeout: 10))
      try assertStatusBar(dimmed: false)
      XCTAssertEqual(switchCourse.frame.minY, original.minY, accuracy: 1)
      XCTAssertTrue(addMaterials.isHittable)
    }
    switchCourse.tap()
    XCTAssertTrue(app.buttons["创建新课程"].waitForExistence(timeout: 10))
    try assertStatusBar(dimmed: true)
    keepScreenshot("study-switch-course-sheet", app: app)
    // Outside taps continue to dismiss the panel after the backdrop is separated.
    app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.10)).tap()
    XCTAssertTrue(app.buttons["overlay-close"].waitForNonExistence(timeout: 10))
    try assertStatusBar(dimmed: false)
    XCTAssertEqual(switchCourse.frame.minY, original.minY, accuracy: 1)
  }

  func testHomeShelfCentersCurrentCourseAndFollowsStudySelection() throws {
    XCUIDevice.shared.orientation = .portrait
    defer { XCUIDevice.shared.orientation = .portrait }
    let app = launch("home")
    let biology = "course:book_biology_2"
    let math = "course:catalog_high_school_math_required_2"
    let shelf = app.scrollViews["home.course.shelf"]

    func assertCentered(_ id: String) {
      let cover = app.buttons["home.course." + id]
      XCTAssertTrue(cover.waitForExistence(timeout: 10))
      let centered = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
        let frame = cover.frame
        let viewport = shelf.frame
        return cover.isSelected && abs(frame.midX - viewport.midX) < 2
          && frame.minX >= viewport.minX && frame.maxX <= viewport.maxX
      }, object: nil)
      XCTAssertEqual(XCTWaiter.wait(for: [centered], timeout: 10), .completed,
        "\(id): selected=\(cover.isSelected), cover=\(cover.frame), shelf=\(shelf.frame)")
      XCTAssertTrue(cover.isHittable)
    }

    assertCentered(biology)
    keepScreenshot("home-shelf-restored-biology", app: app)
    // A swipe must settle on one book, including the first item in the shelf.
    shelf.swipeRight()
    assertCentered(math)
    app.buttons["home.course." + math].tap()
    XCTAssertTrue(app.buttons["study.course.switch"].waitForExistence(timeout: 10))
    app.buttons["study.course.switch"].tap()
    let biologyChoice = app.buttons["study.course." + biology]
    XCTAssertTrue(biologyChoice.waitForExistence(timeout: 10))
    biologyChoice.tap()
    app.buttons["tab-home"].tap()
    assertCentered(biology)
    keepScreenshot("home-shelf-return-from-study", app: app)

    // Native tabs retain the Home view. Later changes still need to move its viewport.
    app.buttons["tab-study"].tap()
    app.buttons["study.course.switch"].tap()
    let mathChoice = app.buttons["study.course." + math]
    XCTAssertTrue(mathChoice.waitForExistence(timeout: 10))
    mathChoice.tap()
    app.buttons["tab-home"].tap()
    assertCentered(math)
    keepScreenshot("home-shelf-restored-math", app: app)
  }

  func testSharedBottomPanelsKeepContentAndCloseReachable() throws {
    XCUIDevice.shared.orientation = .portrait
    for sheet in ["noteType", "note", "editChapter", "chat"] {
      let app = launch("study", sheet: sheet)
      let close = app.buttons["overlay-close"]
      XCTAssertTrue(close.waitForExistence(timeout: 20), sheet)
      XCTAssertTrue(close.isHittable, sheet)
      XCTAssertTrue(app.windows.firstMatch.frame.contains(close.frame), sheet)
      if sheet == "noteType" {
        XCTAssertTrue(app.buttons.matching(
          NSPredicate(format: "label CONTAINS %@", "文字笔记")).firstMatch.isHittable)
      } else if sheet == "editChapter" {
        XCTAssertTrue(app.textFields["课程章节名称"].isHittable)
      } else if sheet == "chat" {
        XCTAssertTrue(app.textFields["assistant-input"].isHittable)
      }
      keepScreenshot("bottom-panel-" + sheet, app: app)
      close.tap()
      XCTAssertTrue(close.waitForNonExistence(timeout: 10), sheet)
      XCTAssertTrue(app.buttons["添加资料"].firstMatch.isHittable, sheet)
      app.terminate()
    }
  }

  func testUploadPanelStaysFixedAndCoversBottomSafeArea() throws {
    XCUIDevice.shared.orientation = .portrait
    defer { XCUIDevice.shared.orientation = .portrait }
    let app = launch("home")
    let create = app.buttons["创建课程"].firstMatch
    XCTAssertTrue(create.waitForExistence(timeout: 20))
    create.tap()
    let select = app.buttons["upload.select"]
    let save = app.buttons["upload.save"]
    let panel = app.otherElements["upload.panel"].firstMatch
    XCTAssertTrue(select.waitForExistence(timeout: 10))
    XCTAssertTrue(panel.waitForExistence(timeout: 10))
    XCTAssertTrue(select.isHittable)
    XCTAssertTrue(save.exists)
    XCTAssertFalse(save.isEnabled)
    XCTAssertFalse(app.scrollViews.firstMatch.exists)
    XCTAssertEqual(panel.frame.maxY, app.windows.firstMatch.frame.maxY, accuracy: 1)
    let original = select.frame
    app.swipeUp()
    app.swipeDown()
    XCTAssertEqual(select.frame.minY, original.minY, accuracy: 1)
    keepScreenshot("upload-fixed-portrait", app: app)

    // Verify actual rendered colors, including the area below the home indicator.
    let screenshot = XCUIScreen.main.screenshot()
    let image = try XCTUnwrap(screenshot.image.cgImage)
    let window = app.windows.firstMatch.frame
    let scale = CGFloat(image.width) / window.width
    let context = try XCTUnwrap(CGContext(
      data: nil, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
    for (point, expected) in [
      (CGPoint(x: window.midX, y: panel.frame.minY - 4), [220, 202, 250]),
      (CGPoint(x: window.width * 0.4, y: window.maxY - 3), [255, 255, 255]),
      (CGPoint(x: window.width * 0.6, y: window.maxY - 3), [255, 255, 255]),
    ] {
      let pixel = try XCTUnwrap(image.cropping(to: CGRect(
        x: point.x * scale, y: point.y * scale, width: 1, height: 1)))
      context.draw(pixel, in: CGRect(x: 0, y: 0, width: 1, height: 1))
      let bytes = try XCTUnwrap(context.data).assumingMemoryBound(to: UInt8.self)
      for channel in 0..<3 { XCTAssertEqual(Int(bytes[channel]), expected[channel], accuracy: 3) }
    }

    select.tap()
    let cancel = app.buttons.matching(
      NSPredicate(format: "label IN %@", ["取消", "Cancel"])).firstMatch
    XCTAssertTrue(cancel.waitForExistence(timeout: 15))
    cancel.tap()
    XCTAssertTrue(select.waitForExistence(timeout: 10))
    XCTAssertTrue(select.isHittable)
    XCTAssertFalse(app.staticTexts["upload.error"].exists)
    XCTAssertEqual(select.frame.minY, original.minY, accuracy: 1)

    XCUIDevice.shared.orientation = .landscapeLeft
    let landscape = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        let bounds = app.windows.firstMatch.frame
        return bounds.width > bounds.height && select.isHittable
          && bounds.contains(select.frame) && bounds.contains(save.frame)
          && abs(panel.frame.maxY - bounds.maxY) < 1
      }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [landscape], timeout: 15), .completed)
    XCTAssertTrue(select.isHittable)
    XCTAssertTrue(save.exists)
    XCTAssertFalse(app.scrollViews.firstMatch.exists)
    XCTAssertEqual(panel.frame.maxY, app.windows.firstMatch.frame.maxY, accuracy: 1)
    let landscapeShot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
    landscapeShot.name = "upload-fixed-landscape"
    landscapeShot.lifetime = .keepAlways
    add(landscapeShot)
  }

  func testDiscoveryPullRefreshPreservesFiltersAndSearch() throws {
    XCUIDevice.shared.orientation = .portrait
    let app = launch("community")
    let search = app.textFields["community.search"]
    let scroll = app.scrollViews["community.scroll"]
    XCTAssertTrue(search.waitForExistence(timeout: 20))
    let searchY = search.frame.minY
    XCTAssertFalse(app.staticTexts.matching(
      NSPredicate(format: "label BEGINSWITH %@", "为你推荐")).firstMatch.exists)

    func pull(_ fraction: CGFloat) {
      let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.12))
      let end = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: fraction))
      start.press(forDuration: 0.08, thenDragTo: end, withVelocity: .slow,
        thenHoldForDuration: 0.25)
    }

    // A short, cancelled pull must not reload the catalog.
    pull(0.20)
    XCTAssertEqual(scroll.value as? String, "下拉刷新课程")
    let math = app.buttons["数学"].firstMatch
    math.tap()
    pull(0.82)
    let refreshed = XCTNSPredicateExpectation(
      predicate: NSPredicate(format: "value == %@", "课程已更新"), object: scroll)
    XCTAssertEqual(XCTWaiter.wait(for: [refreshed], timeout: 10), .completed)
    XCTAssertTrue(math.isSelected)
    XCTAssertEqual(search.frame.minY, searchY, accuracy: 1)
    keepScreenshot("discovery-refreshed-category", app: app)

    search.tap()
    search.typeText("不存在的课程xyz\n")
    XCTAssertTrue(app.staticTexts["没有找到相关课程"].waitForExistence(timeout: 5))
    pull(0.82)
    XCTAssertEqual(search.value as? String, "不存在的课程xyz")
    XCTAssertEqual(scroll.value as? String, "课程已更新")
    XCTAssertTrue(app.staticTexts["没有找到相关课程"].exists)
    app.buttons["清除搜索内容"].tap()
    XCTAssertTrue(app.buttons.matching(
      NSPredicate(format: "label CONTAINS %@", "查看课程")).firstMatch.isHittable)
    keepScreenshot("discovery-refresh-restored", app: app)
  }

  func testDiscoveryStatusBarBackgroundAfterScrolling() throws {
    XCUIDevice.shared.orientation = .portrait
    let app = launch("community")
    XCTAssertTrue(app.textFields["community.search"].waitForExistence(timeout: 20))
    guard app.windows.firstMatch.frame.width < 720 else {
      throw XCTSkip("Checks the phone status bar above the pinned discovery header")
    }

    func assertStatusBarBackground(_ name: String) throws {
      // Capture the device: app screenshots can omit system status-bar pixels.
      let screenshot = XCUIScreen.main.screenshot()
      let attachment = XCTAttachment(screenshot: screenshot)
      attachment.name = name
      attachment.lifetime = .keepAlways
      add(attachment)
      let image = try XCTUnwrap(screenshot.image.cgImage)
      let scale = CGFloat(image.width) / app.windows.firstMatch.frame.width
      let colorSpace = CGColorSpaceCreateDeviceRGB()
      let context = try XCTUnwrap(CGContext(
        data: nil, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
        space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
      // Sample above the clock, island and indicators, away from rounded corners.
      for fraction in [0.08, 0.30, 0.70, 0.92] {
        let pixel = try XCTUnwrap(image.cropping(to: CGRect(
          x: CGFloat(image.width) * fraction, y: 16 * scale, width: 1, height: 1)))
        context.draw(pixel, in: CGRect(x: 0, y: 0, width: 1, height: 1))
        let bytes = try XCTUnwrap(context.data).assumingMemoryBound(to: UInt8.self)
        for (channel, expected) in [246, 248, 251].enumerated() {
          XCTAssertEqual(Int(bytes[channel]), expected, accuracy: 3,
            "\(name): scrolling content must not paint the status bar at x=\(fraction)")
        }
      }
    }

    try assertStatusBarBackground("discovery-status-bar-initial")
    app.scrollViews.firstMatch.swipeUp(velocity: .slow)
    try assertStatusBarBackground("discovery-status-bar-scrolled")
    XCTAssertTrue(app.textFields["community.search"].isHittable)
    app.buttons["tab-home"].tap()
    XCTAssertTrue(app.staticTexts["我的课程"].firstMatch.waitForExistence(timeout: 10))
    app.buttons["tab-community"].tap()
    try assertStatusBarBackground("discovery-status-bar-after-tab-switch")
  }

  func testNativePhoneTabBarRoutesAndReselection() throws {
    guard #available(iOS 26.0, *) else { throw XCTSkip("Requires native Liquid Glass tabs") }
    XCUIDevice.shared.orientation = .portrait
    defer { XCUIDevice.shared.orientation = .portrait }
    let app = launch("courseDetail")
    guard app.windows.firstMatch.frame.width < 720 else {
      XCTAssertFalse(app.tabBars.firstMatch.exists, "Wide iPad layouts use the navigation rail")
      return
    }
    let bar = app.tabBars.firstMatch
    XCTAssertTrue(bar.waitForExistence(timeout: 20))
    let home = bar.buttons["tab-home"]
    XCTAssertTrue(home.isSelected, "Course details belong to the Home tab")
    home.tap()
    XCTAssertTrue(app.staticTexts["我的课程"].firstMatch.waitForExistence(timeout: 10))
    bar.buttons["tab-study"].tap()
    let details = app.buttons["计划详情"].firstMatch
    XCTAssertTrue(details.waitForExistence(timeout: 10))
    let initialBarFrame = bar.frame
    app.scrollViews["study.scroll"].swipeUp(velocity: .slow)
    XCTAssertEqual(bar.frame.minY, initialBarFrame.minY, accuracy: 1)
    XCTAssertTrue(bar.buttons["tab-study"].isSelected)
    app.scrollViews["study.scroll"].swipeDown(velocity: .fast)
    XCTAssertTrue(details.isHittable)
    details.tap()
    XCTAssertFalse(app.tabBars.firstMatch.exists, "The plan is a full-page detail route")
    app.buttons["返回"].firstMatch.tap()
    XCTAssertTrue(bar.waitForExistence(timeout: 10))
    XCTAssertTrue(bar.buttons["tab-study"].isSelected)
    keepScreenshot("liquid-glass-study", app: app)
    bar.buttons["tab-community"].tap()
    XCTAssertTrue(app.textFields["community.search"].waitForExistence(timeout: 8))
    bar.buttons["tab-profile"].tap()
    XCTAssertTrue(app.staticTexts["PROFILE"].firstMatch.waitForExistence(timeout: 8))
    bar.buttons["tab-home"].tap()
    XCTAssertTrue(app.staticTexts["我的课程"].firstMatch.waitForExistence(timeout: 8))
    keepScreenshot("liquid-glass-home", app: app)
  }

  func testNativePhoneTabBarBottomContentAndRotation() throws {
    guard #available(iOS 26.0, *) else { throw XCTSkip("Requires native Liquid Glass tabs") }
    XCUIDevice.shared.orientation = .portrait
    defer { XCUIDevice.shared.orientation = .portrait }
    let app = launch("home")
    guard app.windows.firstMatch.frame.width < 720 else { return }
    let bar = app.tabBars.firstMatch
    XCTAssertTrue(bar.waitForExistence(timeout: 20))
    let materials = app.buttons["课程资料"].firstMatch
    for _ in 0..<4 {
      if materials.exists && materials.isHittable && materials.frame.maxY < bar.frame.minY { break }
      app.scrollViews.firstMatch.swipeUp(velocity: .slow)
    }
    XCTAssertTrue(materials.isHittable)
    XCTAssertLessThanOrEqual(materials.frame.maxY, bar.frame.minY)
    materials.tap()
    XCTAssertTrue(bar.buttons["tab-home"].isSelected)
    bar.buttons["tab-home"].tap()
    XCUIDevice.shared.orientation = .landscapeLeft
    let rotated = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        app.windows.firstMatch.frame.width > app.windows.firstMatch.frame.height
      }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [rotated], timeout: 15), .completed)
    for id in ["tab-home", "tab-study", "tab-community", "tab-profile"] {
      XCTAssertTrue(bar.buttons[id].isHittable)
    }
    XCTAssertGreaterThan(bar.frame.minY, app.windows.firstMatch.frame.height * 0.65)
    keepScreenshot("liquid-glass-landscape", app: app)
  }

  func testNativePhoneTabBarAssistantAndKeyboardAvoidance() throws {
    guard #available(iOS 26.0, *) else { throw XCTSkip("Requires native Liquid Glass tabs") }
    XCUIDevice.shared.orientation = .portrait
    let app = launch("home")
    guard app.windows.firstMatch.frame.width < 720 else {
      XCTAssertFalse(app.tabBars.firstMatch.exists)
      return
    }
    let bar = app.tabBars.firstMatch
    XCTAssertTrue(bar.waitForExistence(timeout: 20))
    let orb = app.buttons["assistant-entry"].firstMatch
    XCTAssertTrue(orb.waitForExistence(timeout: 10))
    orb.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).press(
      forDuration: 0.1,
      thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.97)))
    XCTAssertLessThanOrEqual(orb.frame.maxY, bar.frame.minY - 8)
    orb.tap()
    let input = app.textFields["guide-input"]
    XCTAssertTrue(input.waitForExistence(timeout: 8))
    input.tap()
    input.typeText("Review my learning plan")
    XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    XCTAssertLessThanOrEqual(input.frame.maxY, app.keyboards.firstMatch.frame.minY + 1)
    let close = app.buttons["global-assistant-close"]
    XCTAssertTrue(close.isHittable)
    keepScreenshot("liquid-glass-assistant-keyboard", app: app)
    close.tap()
    bar.buttons["tab-study"].tap()
    XCTAssertTrue(app.staticTexts["学习计划"].firstMatch.waitForExistence(timeout: 10))
    XCTAssertTrue(bar.buttons["tab-study"].isHittable)
  }
  func testAllRoutesAndGlobalSheetsLaunch() throws {
    let routes = [
      "home", "onboarding", "courseSetup", "courseDetail", "upload", "parseReady", "processing",
      "courseImportProcessing", "chapterConfirm", "courseReady", "courseImportReady", "library",
      "community", "communityBook", "communityImport", "study", "plan", "flashcards", "lesson",
      "assignment", "diagnosis", "mistakes", "notes", "voiceNote", "source", "export", "profile",
      "settings",
    ]
    for route in routes {
      let app = launch(route)
      if route == "source" {
        XCTAssertTrue(
          app.descendants(matching: .any)["reader.page"].firstMatch.waitForExistence(timeout: 20),
          route)
      } else {
        XCTAssertTrue(app.staticTexts.firstMatch.waitForExistence(timeout: 20), route)
      }
      let screenshot = XCTAttachment(screenshot: app.screenshot())
      screenshot.name = "route-" + route
      screenshot.lifetime = .keepAlways
      add(screenshot)
      app.terminate()
    }
    for sheet in ["chat", "note", "noteType", "editChapter", "bookSwitcher", "addMaterials"] {
      let app = launch("study", sheet: sheet)
      XCTAssertTrue(app.buttons["关闭"].firstMatch.waitForExistence(timeout: 20), sheet)
      let screenshot = XCTAttachment(screenshot: app.screenshot())
      screenshot.name = "sheet-" + sheet
      screenshot.lifetime = .keepAlways
      add(screenshot)
      app.terminate()
    }
  }
  /// This uses real screen actions to create the note, then reopens the same
  /// isolated SQLite fixture directory. No note data is injected into the fixture.
  func testCourseLessonCitationNoteSurvivesRelaunch() throws {
    let session = UUID().uuidString
    let suffix = String(session.prefix(8))
    let title = "Flow Note " + suffix
    let body = "Durable understanding from the original textbook " + suffix
    let app = launch("library", session: session)
    let course = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "生物 必修 2 遗传与进化"))
      .firstMatch
    try reveal(course, in: app)
    course.tap()

    // Explicitly select chapter and section, even if the restored directory
    // already opens the section chosen by the deterministic fixture.
    let chapter = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "第 2 章 基因和染色体的关系"))
      .firstMatch
    try reveal(chapter, in: app)
    if chapter.isSelected { chapter.tap() }
    chapter.tap()
    let section = app.buttons.matching(
      NSPredicate(format: "label BEGINSWITH %@", "第 1 节 减数分裂和受精作用")
    ).firstMatch
    try reveal(section, in: app)
    let enter = app.buttons["进入学习"].firstMatch
    if enter.exists { section.tap() }
    section.tap()
    try reveal(enter, in: app)
    enter.tap()

    let original = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "查看原文，"))
      .firstMatch
    try reveal(original, in: app)
    original.tap()
    let page = app.descendants(matching: .any)["reader.page"].firstMatch
    XCTAssertTrue(page.waitForExistence(timeout: 15))
    XCTAssertTrue(
      app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "PDF第11页")).firstMatch.exists)
    keepScreenshot("flow-original-citation", app: app)

    let annotate = app.buttons["批注"]
    XCTAssertTrue(annotate.waitForExistence(timeout: 10))
    annotate.tap()
    // A normalized point is intentionally used on the page surface: the
    // persisted note must carry a real book/page anchor and page position.
    page.coordinate(withNormalizedOffset: CGVector(dx: 0.55, dy: 0.35)).tap()
    let titleField = app.textFields["notes.title"].firstMatch
    XCTAssertTrue(titleField.waitForExistence(timeout: 10))
    titleField.tap()
    let oldTitle = titleField.value as? String ?? ""
    titleField.typeText(
      String(repeating: XCUIKeyboardKey.delete.rawValue, count: oldTitle.count) + title)
    let editor = app.textViews["notes.body"].firstMatch
    XCTAssertTrue(editor.waitForExistence(timeout: 5))
    editor.tap()
    editor.typeText(body)
    let save = app.buttons["notes.save"].firstMatch
    try reveal(save, in: app)
    XCTAssertTrue(save.isEnabled)
    save.tap()
    let dismissed = XCTNSPredicateExpectation(
      predicate: NSPredicate(format: "exists == false"), object: save)
    XCTAssertEqual(XCTWaiter.wait(for: [dismissed], timeout: 15), .completed)
    XCTAssertTrue(page.exists)
    keepScreenshot("flow-note-saved-at-source", app: app)
    app.terminate()

    // Reuse only the storage session. Launching the notes route does not
    // seed this title/body, so both assertions depend on the durable save.
    let reopened = launch("notes", session: session)
    XCTAssertTrue(
      reopened.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", title)).firstMatch
        .waitForExistence(timeout: 15))
    XCTAssertTrue(reopened.staticTexts[body].firstMatch.waitForExistence(timeout: 10))
    keepScreenshot("flow-note-restored-after-relaunch", app: reopened)
  }

  private func reveal(_ element: XCUIElement, in app: XCUIApplication) throws {
    XCTAssertTrue(element.waitForExistence(timeout: 15), "Expected flow control was not present")
    for _ in 0..<8 {
      if element.isHittable { return }
      // Swipe in the outside gutter so a focused multiline editor cannot
      // consume the gesture instead of scrolling its containing sheet.
      let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.78))
      let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.30))
      start.press(forDuration: 0.05, thenDragTo: end)
    }
    XCTAssertTrue(element.isHittable, "Expected flow control stayed outside the visible viewport")
  }
  private func keepScreenshot(_ name: String, app: XCUIApplication) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    add(attachment)
  }

  func testLandscapeCorePages() throws {
    // A portrait geometry fixture can leave physical orientation at landscapeLeft.
    // Force a genuine orientation change for every route instead of assigning
    // an unchanged physical value, which produces no window rotation event.
    XCUIDevice.shared.orientation = .portrait
    defer { XCUIDevice.shared.orientation = .portrait }
    for route in [
      "home", "onboarding", "courseSetup", "community", "study", "lesson", "source", "assignment",
      "notes", "profile", "settings",
    ] {
      XCUIDevice.shared.orientation = .portrait
      let app = launch(route, chapter: "c1s1", visual: true)
      XCUIDevice.shared.orientation = .landscapeLeft
      let landscape = XCTNSPredicateExpectation(
        predicate: NSPredicate { _, _ in
          app.windows.firstMatch.frame.width > app.windows.firstMatch.frame.height
        }, object: nil)
      XCTAssertEqual(
        XCTWaiter.wait(for: [landscape], timeout: 15), .completed,
        "Landscape did not take effect on \(route)")
      let ready = XCTNSPredicateExpectation(
        predicate: NSPredicate { _, _ in !app.staticTexts["正在准备学习空间"].exists }, object: nil)
      XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 15), .completed)
      Thread.sleep(forTimeInterval: 0.8)
      keepScreenshot("landscape-" + route, app: app)
      app.terminate()
    }
  }

  func testNotesLocalPopupsHandwritingRegionAndVoiceSample() throws {
    let app = launch("source")
    let page = app.descendants(matching: .any)["reader.page"].firstMatch
    XCTAssertTrue(page.waitForExistence(timeout: 20))
    app.buttons["批注"].tap()
    page.coordinate(withNormalizedOffset: CGVector(dx: 0.55, dy: 0.35)).tap()
    let body = app.textViews["notes.body"].firstMatch
    XCTAssertTrue(body.waitForExistence(timeout: 10))
    body.tap()
    body.typeText("Popup closing must preserve this draft.")
    keepScreenshot("popup-text-keyboard", app: app)
    app.buttons["关闭"].firstMatch.tap()
    let closed = XCTNSPredicateExpectation(
      predicate: NSPredicate(format: "exists == false"), object: body)
    XCTAssertEqual(XCTWaiter.wait(for: [closed], timeout: 15), .completed)
    XCTAssertTrue(
      app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "打开批注：")).firstMatch.exists)
    keepScreenshot("popup-text-saved-on-close", app: app)

    // Native PencilKit accepts a finger stroke after enabling the original
    // hand tool; this verifies the simulator path, not physical Pencil.
    app.buttons["批注"].tap()
    app.buttons["钢笔"].tap()
    app.buttons["手指书写"].tap()
    page.coordinate(withNormalizedOffset: CGVector(dx: 0.30, dy: 0.40)).press(
      forDuration: 0.1,
      thenDragTo: page.coordinate(withNormalizedOffset: CGVector(dx: 0.60, dy: 0.45)))
    let organize = app.buttons["识别并整理"].firstMatch
    XCTAssertTrue(organize.waitForExistence(timeout: 10))
    keepScreenshot("popup-native-pencilkit-stroke", app: app)
    organize.tap()
    XCTAssertTrue(app.staticTexts["请确认识别文字"].firstMatch.waitForExistence(timeout: 10))
    keepScreenshot("popup-ink-demo-confirmation", app: app)
    app.buttons["收起整理面板"].firstMatch.tap()

    app.buttons["圈选问 AI"].tap()
    page.coordinate(withNormalizedOffset: CGVector(dx: 0.20, dy: 0.20)).press(
      forDuration: 0.1,
      thenDragTo: page.coordinate(withNormalizedOffset: CGVector(dx: 0.65, dy: 0.45)))
    let question = app.textFields["reader.regionInput"].firstMatch
    XCTAssertTrue(question.waitForExistence(timeout: 10))
    question.tap()
    question.typeText("Explain this textbook region")
    keepScreenshot("popup-region-keyboard", app: app)
    let regionSend = app.buttons["reader.regionSend"].firstMatch
    try reveal(regionSend, in: app)
    regionSend.tap()
    XCTAssertTrue(
      app.descendants(matching: .any)["reader.regionReply"].firstMatch.waitForExistence(timeout: 45)
    )
    keepScreenshot("popup-region-reply", app: app)
    app.buttons["关闭"].firstMatch.tap()
    XCTAssertFalse(question.exists)

    app.buttons["批注"].tap()
    page.coordinate(withNormalizedOffset: CGVector(dx: 0.60, dy: 0.55)).tap()
    let voice = app.buttons["开始语音批注"].firstMatch
    try reveal(voice, in: app)
    voice.tap()
    let sample = app.buttons["使用示例录音"].firstMatch
    XCTAssertTrue(sample.waitForExistence(timeout: 10))
    if !sample.isHittable { app.scrollViews["voice.scroll"].firstMatch.swipeUp() }
    XCTAssertTrue(sample.isHittable)
    sample.tap()
    XCTAssertTrue(app.staticTexts["00:08"].firstMatch.waitForExistence(timeout: 10))
    keepScreenshot("popup-voice-sample-stopped", app: app)
    let voiceClose = app.buttons.matching(identifier: "关闭").allElementsBoundByIndex.last!
    voiceClose.tap()
    let voiceClosed = XCTNSPredicateExpectation(
      predicate: NSPredicate(format: "exists == false"), object: sample)
    XCTAssertEqual(XCTWaiter.wait(for: [voiceClosed], timeout: 10), .completed)
    keepScreenshot("popup-voice-close-keeps-parent", app: app)
  }

  func testSourceTextAutosaveSurvivesTermination() throws {
    let session = UUID().uuidString
    let text = "Autosaved before termination " + String(session.prefix(8))
    let app = launch("source", session: session)
    let page = app.descendants(matching: .any)["reader.page"].firstMatch
    XCTAssertTrue(page.waitForExistence(timeout: 20))
    app.buttons["批注"].tap()
    page.coordinate(withNormalizedOffset: CGVector(dx: 0.55, dy: 0.35)).tap()
    let editor = app.textViews["notes.body"].firstMatch
    XCTAssertTrue(editor.waitForExistence(timeout: 10))
    editor.tap()
    editor.typeText(text)
    XCTAssertTrue(app.staticTexts["已保存"].firstMatch.waitForExistence(timeout: 10))
    keepScreenshot("autosave-before-termination", app: app)
    // No Done/Close action is performed. The debounced persisted record must
    // survive terminating the app with its editor and keyboard still open.
    app.terminate()
    let reopened = launch("notes", session: session)
    XCTAssertTrue(reopened.staticTexts[text].firstMatch.waitForExistence(timeout: 15))
    keepScreenshot("autosave-restored-after-termination", app: reopened)
  }

  func testAssistantDockAndFlashcardFlipMasterySurvivesRelaunch() throws {
    let session = UUID().uuidString
    let home = launch("home", session: session)
    let orb = home.buttons["assistant-entry"].firstMatch
    XCTAssertTrue(orb.waitForExistence(timeout: 20))
    let end = home.coordinate(withNormalizedOffset: CGVector(dx: 0.06, dy: 0.44))
    orb.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
      .press(forDuration: 0.1, thenDragTo: end)
    let docked = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        orb.frame.minX < home.windows.firstMatch.frame.width * 0.1
      }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [docked], timeout: 5), .completed)
    keepScreenshot("motion-assistant-docked-left", app: home)
    orb.tap()
    let close = home.buttons["global-assistant-close"]
    XCTAssertTrue(close.waitForExistence(timeout: 10))
    close.tap()
    XCTAssertTrue(orb.waitForExistence(timeout: 5))
    home.terminate()

    let app = launch("flashcards", session: session)
    let face = app.buttons["flashcard-face"].firstMatch
    XCTAssertTrue(face.waitForExistence(timeout: 15))
    let firstQuestion = face.label
    XCTAssertTrue(firstQuestion.hasPrefix("问题："))
    XCTAssertEqual(face.value as? String, "unmastered")
    face.tap()
    let flipped = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        face.label.hasPrefix("参考答案：") && face.isEnabled
      }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [flipped], timeout: 5), .completed)
    keepScreenshot("motion-flashcard-answer", app: app)
    let remember = app.buttons["flashcard-remember"].firstMatch
    try reveal(remember, in: app)
    remember.tap()
    let advanced = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        face.isEnabled && face.label.hasPrefix("问题：") && face.label != firstQuestion
      }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [advanced], timeout: 5), .completed)
    let secondQuestion = face.label
    print("Flashcard visible frame before swipe: \(face.frame), \(face.label)")
    face.coordinate(withNormalizedOffset: CGVector(dx: 0.85, dy: 0.5)).press(
      forDuration: 0.05,
      thenDragTo: face.coordinate(withNormalizedOffset: CGVector(dx: 0.15, dy: 0.5)))
    let swiped = XCTNSPredicateExpectation(
      predicate: NSPredicate { _, _ in
        face.isEnabled && face.label.hasPrefix("问题：") && face.label != secondQuestion
      }, object: nil)
    let swipeResult = XCTWaiter.wait(for: [swiped], timeout: 5)
    keepScreenshot("motion-flashcard-after-swipe", app: app)
    XCTAssertEqual(swipeResult, .completed, "After swipe: \(face.frame), \(face.label)")
    keepScreenshot("motion-flashcard-next", app: app)
    app.terminate()

    let reopened = launch("flashcards", session: session)
    let restored = reopened.buttons["flashcard-face"].firstMatch
    XCTAssertTrue(restored.waitForExistence(timeout: 15))
    XCTAssertEqual(restored.label, firstQuestion)
    XCTAssertEqual(restored.value as? String, "mastered")
    keepScreenshot("motion-flashcard-mastery-restored", app: reopened)
  }

  func testLastFlashcardCompletionDoesNotMarkUnratedCardMastered() throws {
    let session = UUID().uuidString
    func lastCard(in app: XCUIApplication) -> XCUIElement {
      let face = app.buttons["flashcard-face"].firstMatch
      XCTAssertTrue(face.waitForExistence(timeout: 15))
      for _ in 0..<5 {
        let previous = face.label
        face.coordinate(withNormalizedOffset: CGVector(dx: 0.85, dy: 0.5)).press(
          forDuration: 0.05,
          thenDragTo: face.coordinate(withNormalizedOffset: CGVector(dx: 0.15, dy: 0.5)))
        let advanced = XCTNSPredicateExpectation(
          predicate: NSPredicate { _, _ in face.isEnabled && face.label != previous }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [advanced], timeout: 5), .completed)
      }
      return face
    }
    let app = launch("flashcards", session: session, visual: true)
    let last = lastCard(in: app)
    XCTAssertEqual(last.value as? String, "unmastered")
    let finish = app.buttons["flashcard-finish"]
    try reveal(finish, in: app)
    keepScreenshot("last-flashcard-existing-finish", app: app)
    finish.tap()
    app.terminate()

    let reopened = launch("flashcards", session: session, visual: true)
    let restored = lastCard(in: reopened)
    XCTAssertEqual(restored.value as? String, "unmastered")
    if restored.label.hasPrefix("问题：") { restored.tap() }
    XCTAssertTrue(restored.label.hasPrefix("参考答案："))
    let again = reopened.buttons["flashcard-again"]
    let remember = reopened.buttons["flashcard-remember"]
    try reveal(again, in: reopened)
    XCTAssertTrue(remember.exists && remember.isEnabled)
    keepScreenshot("last-flashcard-explicit-rating", app: reopened)
    again.tap()
  }

  func testPlanCalendarKeepsSevenColumnsAndSelectsLastDay() throws {
    let app = launch("plan", visual: true)
    let first = app.buttons["第 1 天"].firstMatch
    let seventh = app.buttons["第 7 天"].firstMatch
    let eighth = app.buttons["第 8 天"].firstMatch
    let last = app.buttons["第 14 天"].firstMatch
    XCTAssertTrue(first.waitForExistence(timeout: 15))
    XCTAssertTrue(seventh.exists && eighth.exists && last.exists)
    if app.windows.firstMatch.frame.width >= 384 {
      XCTAssertEqual(first.frame.minY, seventh.frame.minY, accuracy: 1)
      XCTAssertGreaterThan(eighth.frame.minY, first.frame.minY + 20)
    }
    last.tap()
    XCTAssertTrue(last.isSelected)
    XCTAssertTrue(app.staticTexts["学习任务 · 第 14 天"].firstMatch.waitForExistence(timeout: 5))
    keepScreenshot("plan-calendar-seven-columns-last-day", app: app)
  }

  func testAnnotationConversationAndGuideActionsRemainAvailable() throws {
    let session = UUID().uuidString
    let body = "Annotation conversation " + String(session.prefix(8))
    let app = launch("source", session: session)
    let page = app.descendants(matching: .any)["reader.page"].firstMatch
    XCTAssertTrue(page.waitForExistence(timeout: 20))
    app.buttons["批注"].tap()
    page.coordinate(withNormalizedOffset: CGVector(dx: 0.48, dy: 0.4)).tap()
    let editor = app.textViews["notes.body"].firstMatch
    XCTAssertTrue(editor.waitForExistence(timeout: 10))
    editor.tap()
    editor.typeText(body)
    let ask = app.buttons["问 AI"].firstMatch
    try reveal(ask, in: app)
    ask.tap()
    let input = app.textFields["reader.regionInput"].firstMatch
    XCTAssertTrue(input.waitForExistence(timeout: 10))
    input.tap()
    input.typeText("你好")
    app.buttons["reader.regionSend"].firstMatch.tap()
    let reply = app.descendants(matching: .any)["reader.regionReply"].firstMatch
    XCTAssertTrue(reply.waitForExistence(timeout: 45))
    let replyText = reply.label
    XCTAssertFalse(replyText.isEmpty)
    keepScreenshot("annotation-chat-reply-persisted", app: app)
    app.buttons["关闭"].firstMatch.tap()
    XCTAssertTrue(editor.waitForExistence(timeout: 10))
    try reveal(ask, in: app)
    ask.tap()
    XCTAssertTrue(reply.waitForExistence(timeout: 10))
    XCTAssertEqual(reply.label, replyText)
    keepScreenshot("annotation-chat-restored-on-reopen", app: app)
    app.terminate()

    let reopened = launch("notes", session: session)
    XCTAssertTrue(reopened.staticTexts[body].firstMatch.waitForExistence(timeout: 15))
    reopened.buttons["notes.guide"].firstMatch.tap()
    let detail = reopened.staticTexts["notes.guideDetail"].firstMatch
    XCTAssertTrue(detail.waitForExistence(timeout: 10))
    let firstTitle = detail.label
    let second = reopened.buttons["notes.guideItem.1"].firstMatch
    try reveal(second, in: reopened)
    second.tap()
    XCTAssertNotEqual(detail.label, firstTitle)
    let export = reopened.buttons["notes.export"].firstMatch
    try reveal(export, in: reopened)
    export.tap()
    XCTAssertTrue(reopened.buttons["确认导出"].firstMatch.waitForExistence(timeout: 10))
    reopened.terminate()

    let flashcards = launch("notes", session: session)
    flashcards.buttons["notes.guide"].firstMatch.tap()
    let flashcardEntry = flashcards.buttons["notes.flashcards"].firstMatch
    try reveal(flashcardEntry, in: flashcards)
    flashcardEntry.tap()
    XCTAssertTrue(flashcards.buttons["flashcard-face"].firstMatch.waitForExistence(timeout: 10))
  }

  func testOfflineAssistantGreeting() throws {
    let app = launch("home", sheet: "chat")
    let input = app.textFields["assistant-input"]
    XCTAssertTrue(input.waitForExistence(timeout: 20))
    input.tap()
    input.typeText("你好")
    keepScreenshot("chat-keyboard", app: app)
    let send = app.buttons["assistant-send"]
    try reveal(send, in: app)
    send.tap()
    XCTAssertTrue(
      app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "你好！我是你的学习助手")).firstMatch
        .waitForExistence(timeout: 10))
  }
}
