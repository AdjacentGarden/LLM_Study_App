import XCTest

final class CloudAccountUITests: XCTestCase {
  private var nativeTestBaseURL: String {
    ProcessInfo.processInfo.environment["BOOKCOURSE_NATIVE_TEST_BASE_URL"]
      ?? "http://127.0.0.1:18101"
  }

  @MainActor func testOnlineCourseProgressChangesAfterCompletingLessonAndSurvivesRelaunch() async throws {
    continueAfterFailure = false
    let config = URLSessionConfiguration.ephemeral
    config.connectionProxyDictionary = [:]
    config.timeoutIntervalForRequest = 5
    let session = URLSession(configuration: config)
    defer { session.invalidateAndCancel() }
    let base = try XCTUnwrap(URL(string: nativeTestBaseURL))
    let healthURL = base.appendingPathComponent("api/native-test/health")
    guard let (data, _) = try? await session.data(from: healthURL),
      let health = try? JSONDecoder().decode([String: Bool].self, from: data),
      health["fixture_adapters"] == true, health["local_default"] == true
    else { throw XCTSkip("Run scripts/test-cloud.sh with NATIVE_E2E_LOCAL_LOGIN=1") }

    func request(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> [String: Any] {
      var request = URLRequest(url: base.appendingPathComponent(path))
      request.httpMethod = method
      if let body {
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
      }
      let (data, response) = try await session.data(for: request)
      XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, path)
      return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    _ = try await request("api/native/local-login", method: "POST")
    _ = try await request("api/native/learner-preferences", method: "PUT", body: [
      "displayName": "进度回归测试", "learningStage": "high", "primaryGoal": "exam",
      "dailyTime": "30to60", "learningBarriers": ["memory"], "guidanceStyles": ["examples"],
      "planningSupport": "collaborative", "onboardingVersion": 2,
    ])
    let workspaceID = "ui-progress-" + UUID().uuidString
    let workspace = try await request("api/native/catalog/community_genetics/import", method: "POST",
      body: ["id": workspaceID, "name": "在线课程进度测试", "preferences": [:]])
    let resources = try XCTUnwrap(workspace["resources"] as? [[String: Any]])
    let resourceID = try XCTUnwrap(resources.first?["id"] as? String)
    let path = "api/workspaces/\(workspaceID)/resources/\(resourceID)"
    _ = try await request(path + "/progress", method: "PUT", body: [
      "revision": 0, "mutation_id": UUID().uuidString, "page": 1,
      "chapter_id": "c2s1", "lesson_id": "lesson_meiosis", "completed_chapters": [],
      "task_status": [:], "mastered_ids": [],
    ])

    let app = XCUIApplication()
    app.launchEnvironment["BOOKCOURSE_API_BASE_URL"] = nativeTestBaseURL
    app.launchArguments = ["--cloud-test-session", UUID().uuidString, "--fixture-reduce-motion"]
    app.launch()
    let progress = app.descendants(matching: .any)["study.plan.progress"].firstMatch
    XCTAssertTrue(progress.waitForExistence(timeout: 25))
    XCTAssertEqual(progress.value as? String, "0%")
    tap(app.buttons["study.section.c2s1.lesson"], app: app)
    let page = app.staticTexts["lesson.page"].firstMatch
    XCTAssertTrue(page.waitForExistence(timeout: 15))
    for _ in 0..<12 {
      if app.buttons["完成本节"].firstMatch.exists { break }
      let previous = page.value as? String
      app.coordinate(withNormalizedOffset: CGVector(dx: 0.77, dy: 0.42)).press(forDuration: 0.08,
        thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.23, dy: 0.42)))
      let changed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
        page.value as? String != previous
      }, object: nil)
      XCTAssertEqual(XCTWaiter.wait(for: [changed], timeout: 5), .completed)
    }
    tap(app.buttons["完成本节"].firstMatch, app: app)
    let updated = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
      progress.exists && progress.value as? String != "0%"
    }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [updated], timeout: 15), .completed)
    let percentage = try XCTUnwrap(progress.value as? String)
    let screenshot = XCTAttachment(screenshot: app.screenshot())
    screenshot.name = "Online course progress after completing a lesson"
    screenshot.lifetime = .keepAlways
    add(screenshot)
    app.terminate()
    app.launch()
    XCTAssertTrue(progress.waitForExistence(timeout: 25))
    XCTAssertEqual(progress.value as? String, percentage)
    let saved = try await request(path)
    let savedProgress = try XCTUnwrap(saved["progress"] as? [String: Any])
    XCTAssertTrue((savedProgress["completed_chapters"] as? [String] ?? []).contains("c2s1"))
  }

  @MainActor func testLoginDesignAndServiceSettings() async throws {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.timeoutIntervalForRequest = 1
    configuration.connectionProxyDictionary = [:]
    let session = URLSession(configuration: configuration)
    defer { session.invalidateAndCancel() }
    let url = URL(string: nativeTestBaseURL)!.appendingPathComponent("api/native-test/health")
    guard let (data, _) = try? await session.data(from: url),
      let health = try? JSONDecoder().decode([String: Bool].self, from: data),
      health["fixture_adapters"] == true, health["local_default"] == false
    else { throw XCTSkip("Use scripts/test-cloud.sh with email authentication enabled") }
    continueAfterFailure = false
    let app = XCUIApplication()
    app.launchEnvironment["BOOKCOURSE_API_BASE_URL"] = nativeTestBaseURL
    app.launchArguments = ["--cloud-test-session", UUID().uuidString]
    app.launch()
    let email = app.textFields["cloud.email"]
    // Credentials belong to the service origin and survive separate UI test data directories.
    let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
      !app.images["app.loading"].exists
        && (email.exists || app.buttons["tab-profile"].exists)
    }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 25), .completed)
    if app.buttons["tab-profile"].exists {
      tap(app.buttons["tab-profile"], app: app)
      tap(app.buttons["profile.settings"], app: app)
      tap(app.buttons["settings.logout"], app: app)
      tap(app.buttons["确认退出"], app: app)
    }
    XCTAssertTrue(email.waitForExistence(timeout: 20))
    XCTAssertTrue(app.textFields["cloud.code"].exists)
    XCTAssertTrue(app.buttons["cloud.settings"].isHittable)
    XCTAssertFalse(app.textFields["cloud.server"].exists)
    XCTAssertTrue(app.buttons["cloud.verify"].isEnabled)
    tap(app.buttons["cloud.verify"], app: app)
    XCTAssertTrue(app.staticTexts["cloud.error"].waitForExistence(timeout: 5))
    XCTAssertEqual(app.staticTexts["cloud.error"].label, "请先为当前邮箱获取验证码")
    tap(app.buttons["cloud.registerMode"], app: app)
    XCTAssertFalse(app.buttons["cloud.loginMode"].exists)
    XCTAssertFalse(app.textFields["cloud.nickname"].exists)
    XCTAssertFalse(app.pickers["学习阶段"].exists)
    XCTAssertTrue(app.buttons["cloud.backToLogin"].isHittable)
    let registration = XCTAttachment(screenshot: app.screenshot())
    registration.name = "Registration panel with back button and full white bottom"
    registration.lifetime = .keepAlways
    add(registration)
    let title = app.staticTexts["cloud.title"]
    let settingsButton = app.buttons["cloud.settings"]
    let scroll = app.scrollViews["cloud.scroll"]
    let titleY = title.frame.minY
    let settingsY = settingsButton.frame.minY
    let emailY = email.frame.minY
    scroll.swipeUp()
    XCTAssertEqual(title.frame.minY, titleY, accuracy: 1,
      "The account title stays pinned while the form scrolls up")
    XCTAssertEqual(settingsButton.frame.minY, settingsY, accuracy: 1)
    XCTAssertEqual(email.frame.minY, emailY, accuracy: 1,
      "Releasing an upward drag must restore the original form position")
    let raised = XCTAttachment(screenshot: app.screenshot())
    raised.name = "Registration restored after upward drag"
    raised.lifetime = .keepAlways
    add(raised)
    scroll.swipeDown()
    XCTAssertEqual(title.frame.minY, titleY, accuracy: 1)
    XCTAssertEqual(settingsButton.frame.minY, settingsY, accuracy: 1)
    XCTAssertEqual(email.frame.minY, emailY, accuracy: 1,
      "Releasing a downward drag must restore the original form position")
    // Repeated drags must never accumulate a permanent offset.
    scroll.swipeUp()
    scroll.swipeDown()
    XCTAssertEqual(email.frame.minY, emailY, accuracy: 1)
    email.tap()
    XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    XCTAssertTrue(email.isHittable)
    tap(app.buttons["cloud.keyboardDone"], app: app)
    XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
    XCTAssertEqual(email.frame.minY, emailY, accuracy: 1,
      "Dismissing the keyboard must restore the original form position")
    XCTAssertTrue(settingsButton.isHittable)
    let lowered = XCTAttachment(screenshot: app.screenshot())
    lowered.name = "Registration restored after downward drag and keyboard dismissal"
    lowered.lifetime = .keepAlways
    add(lowered)
    tap(app.buttons["cloud.backToLogin"], app: app)
    XCTAssertTrue(app.buttons["cloud.registerMode"].exists)
    XCTAssertFalse(app.buttons["cloud.backToLogin"].exists)
    let loginEmailY = email.frame.minY
    scroll.swipeUp()
    scroll.swipeDown()
    XCTAssertEqual(email.frame.minY, loginEmailY, accuracy: 1,
      "Login also returns to its original position after dragging")
    let login = XCTAttachment(screenshot: app.screenshot())
    login.name = "Figma login with original cloud artwork"
    login.lifetime = .keepAlways
    add(login)

    tap(app.buttons["cloud.settings"], app: app)
    let server = app.textFields["cloud.server"]
    XCTAssertTrue(server.waitForExistence(timeout: 5))
    XCTAssertEqual(server.value as? String, nativeTestBaseURL)
    server.tap()
    server.press(forDuration: 1.2)
    if app.menuItems["Select All"].exists { app.menuItems["Select All"].tap() }
    else if app.menuItems["全选"].exists { app.menuItems["全选"].tap() }
    else {
      server.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue,
        count: (server.value as? String ?? "").count))
    }
    server.typeText("invalid-address")
    app.buttons["cloud.connectServer"].tap()
    XCTAssertTrue(app.staticTexts["cloud.serverError"].waitForExistence(timeout: 5))
    XCTAssertTrue(server.exists)
    app.buttons["cloud.settingsDone"].tap()
    XCTAssertTrue(email.waitForExistence(timeout: 5))
    tap(app.buttons["cloud.settings"], app: app)
    XCTAssertEqual(server.value as? String, nativeTestBaseURL,
      "Closing without connecting must discard the invalid address")
    let settings = XCTAttachment(screenshot: app.screenshot())
    settings.name = "Learning service address in top-right settings"
    settings.lifetime = .keepAlways
    add(settings)
    app.buttons["cloud.connectServer"].tap()
    XCTAssertTrue(email.waitForExistence(timeout: 20))
    XCTAssertFalse(server.exists)
  }

  @MainActor func testConfiguredRemoteDemoAccountImportsCourseAndRestoresSession() async throws {
    guard let value = ProcessInfo.processInfo.environment["BOOKCOURSE_REMOTE_API_BASE_URL"],
      let email = ProcessInfo.processInfo.environment["BOOKCOURSE_REMOTE_DEMO_EMAIL"]
    else { throw XCTSkip("Set the remote HTTPS backend and an explicitly selected demo email") }
    continueAfterFailure = false
    XCTAssertTrue(email.hasSuffix("@zhiwo.test"), "This workflow only uses the isolated demo realm")
    let configuration = URLSessionConfiguration.ephemeral
    configuration.connectionProxyDictionary = [:]
    configuration.timeoutIntervalForRequest = 90
    let session = URLSession(configuration: configuration)
    defer { session.invalidateAndCancel() }
    struct DemoState: Decodable {
      struct User: Decodable { let email: String; let registered: Bool }
      let demo_available: Bool
      let demo_users: [User]
    }
    let base = try XCTUnwrap(URL(string: value))
    let (data, response) = try await session.data(from: base.appendingPathComponent("api/auth/me"))
    XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    let state = try JSONDecoder().decode(DemoState.self, from: data)
    XCTAssertTrue(state.demo_available, "The backend must explicitly enable demo accounts")
    let demo = try XCTUnwrap(state.demo_users.first { $0.email == email })
    let app = XCUIApplication()
    app.launchEnvironment["BOOKCOURSE_API_BASE_URL"] = value
    app.launchArguments = ["--cloud-test-session", UUID().uuidString]
    app.launch()
    let emailField = app.textFields["cloud.email"]
    let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
      !app.images["app.loading"].exists
        && (emailField.exists || app.buttons["tab-profile"].exists)
    }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 135), .completed)
    if app.buttons["tab-profile"].exists {
      tap(app.buttons["tab-profile"], app: app)
      tap(app.buttons["profile.settings"], app: app)
      tap(app.buttons["settings.logout"], app: app)
      tap(app.buttons["确认退出"], app: app)
    }
    XCTAssertTrue(emailField.waitForExistence(timeout: 75))
    if !demo.registered { tap(app.buttons["cloud.registerMode"], app: app) }
    emailField.tap()
    emailField.typeText(email)
    tap(app.buttons["cloud.sendCode"], app: app)
    let demoCode = app.staticTexts["cloud.demoCode"]
    XCTAssertTrue(demoCode.waitForExistence(timeout: 75))
    let code = String(demoCode.label.filter(\.isNumber).suffix(6))
    XCTAssertEqual(code.count, 6)
    let codeField = app.textFields["cloud.code"]
    tap(codeField, app: app)
    codeField.typeText(code)
    tap(app.buttons["cloud.verify"], app: app)
    let signedIn = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
      let name = app.textFields["onboarding.name"]
      let profile = app.buttons["tab-profile"]
      return !app.images["app.loading"].exists
        && ((name.exists && name.isHittable) || (profile.exists && profile.isHittable))
    }, object: nil)
    XCTAssertEqual(XCTWaiter.wait(for: [signedIn], timeout: 135), .completed)
    // An existing account can briefly expose the underlying onboarding view
    // while its saved preferences are still being hydrated from the backend.
    if !app.buttons["tab-profile"].waitForExistence(timeout: 15) {
      completePersonalization(app)
    }
    XCTAssertTrue(app.buttons["tab-profile"].waitForExistence(timeout: 45))
    tap(app.buttons["tab-community"], app: app)
    tap(app.buttons["community.course.community_genetics"], app: app)
    tap(app.buttons["community.import"], app: app)
    for (index, option) in ["正常节奏，按计划来", "整块固定时间", "应试备考", "重点和考点", "导学笔记沉淀", "定期复盘"].enumerated() {
      tap(app.buttons[option], app: app)
      if index >= 2 { tap(app.buttons["courseSetup.next"], app: app) }
    }
    XCTAssertTrue(app.buttons["进入学习"].waitForExistence(timeout: 135))
    tap(app.buttons["进入学习"], app: app)
    XCTAssertTrue(app.buttons["tab-profile"].waitForExistence(timeout: 45))
    XCTAssertTrue(app.staticTexts["遗传与进化高频考点课"].firstMatch.waitForExistence(timeout: 30))
    let imported = XCTAttachment(screenshot: app.screenshot())
    imported.name = "Course imported through the real Windows backend"
    imported.lifetime = .keepAlways
    add(imported)
    app.terminate()
    app.launch()
    XCTAssertTrue(app.buttons["tab-profile"].waitForExistence(timeout: 45))
    XCTAssertFalse(app.textFields["cloud.email"].exists)
    XCTAssertTrue(app.staticTexts["遗传与进化高频考点课"].firstMatch.waitForExistence(timeout: 30))
    let restored = XCTAttachment(screenshot: app.screenshot())
    restored.name = "Remote account and course restored after relaunch"
    restored.lifetime = .keepAlways
    add(restored)
  }

  @MainActor func testConfiguredRemoteServicePersistsAfterRelaunch() throws {
    guard let value = ProcessInfo.processInfo.environment["BOOKCOURSE_REMOTE_API_BASE_URL"] else {
      throw XCTSkip("Set BOOKCOURSE_REMOTE_API_BASE_URL to test a real HTTPS backend")
    }
    continueAfterFailure = false
    let app = XCUIApplication()
    app.launchEnvironment["BOOKCOURSE_API_BASE_URL"] = value
    app.launchArguments = ["--cloud-test-session", UUID().uuidString]
    app.launch()
    XCTAssertTrue(app.textFields["cloud.email"].waitForExistence(timeout: 75))
    XCTAssertFalse(app.buttons["cloud.localRetry"].exists)
    XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "无法连接学习服务")).firstMatch.exists)
    tap(app.buttons["cloud.settings"], app: app)
    XCTAssertEqual(app.textFields["cloud.server"].value as? String, value)
    tap(app.buttons["连接此服务"], app: app)
    XCTAssertTrue(app.textFields["cloud.email"].waitForExistence(timeout: 75))
    app.terminate()
    app.launchEnvironment.removeValue(forKey: "BOOKCOURSE_API_BASE_URL")
    app.launch()
    XCTAssertTrue(app.textFields["cloud.email"].waitForExistence(timeout: 75))
    XCTAssertFalse(app.buttons["cloud.localRetry"].exists)
    XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "无法连接学习服务")).firstMatch.exists)
    tap(app.buttons["cloud.settings"], app: app)
    XCTAssertEqual(app.textFields["cloud.server"].value as? String, value)
    let screenshot = XCTAttachment(screenshot: app.screenshot())
    screenshot.name = "Real HTTPS backend retained after relaunch"
    screenshot.lifetime = .keepAlways
    add(screenshot)
  }

  @MainActor func testLocalDefaultAccountEntersHomeWithoutEmailAndSurvivesRelaunch() async throws {
    continueAfterFailure = false
    let configuration = URLSessionConfiguration.ephemeral
    configuration.timeoutIntervalForRequest = 1
    configuration.connectionProxyDictionary = [:]
    let session = URLSession(configuration: configuration)
    let url = URL(string: nativeTestBaseURL)!.appendingPathComponent("api/native-test/health")
    guard let (data, _) = try? await session.data(from: url),
      let health = try? JSONDecoder().decode([String: Bool].self, from: data),
      health["fixture_adapters"] == true, health["local_default"] == true
    else { throw XCTSkip("Run scripts/test-cloud.sh with NATIVE_E2E_LOCAL_LOGIN=1") }
    let app = XCUIApplication()
    app.launchEnvironment["BOOKCOURSE_API_BASE_URL"] = nativeTestBaseURL
    app.launchArguments = ["--cloud-test-session", UUID().uuidString]
    app.launch()
    completePersonalization(app)
    XCTAssertTrue(app.buttons["tab-profile"].waitForExistence(timeout: 20))
    XCTAssertFalse(app.textFields["cloud.email"].exists)
    tap(app.buttons["tab-community"], app: app)
    tap(app.buttons["community.course.community_genetics"], app: app)
    tap(app.buttons["community.import"], app: app)
    for (index, option) in ["正常节奏，按计划来", "整块固定时间", "应试备考", "重点和考点", "导学笔记沉淀", "定期复盘"].enumerated() {
      tap(app.buttons[option], app: app)
      if index >= 2 { tap(app.buttons["courseSetup.next"], app: app) }
    }
    tap(app.buttons["进入学习"], app: app)
    XCTAssertTrue(app.buttons["tab-profile"].waitForExistence(timeout: 20))
    app.buttons["tab-profile"].tap()
    tap(app.buttons["profile.settings"], app: app)
    XCTAssertTrue(app.staticTexts["本地默认账号"].waitForExistence(timeout: 10))
    let screenshot = XCTAttachment(screenshot: app.screenshot())
    screenshot.name = "Local default account without email verification"
    screenshot.lifetime = .keepAlways
    add(screenshot)
    app.terminate()
    app.launch()
    XCTAssertTrue(app.buttons["tab-profile"].waitForExistence(timeout: 20))
    XCTAssertFalse(app.textFields["cloud.email"].exists)
    XCTAssertFalse(app.textFields["onboarding.name"].exists)
    XCTAssertTrue(app.staticTexts["遗传与进化高频考点课"].firstMatch.waitForExistence(timeout: 10))
    tap(app.buttons["tab-community"], app: app)
    XCTAssertTrue(
      app.buttons["community.course.community_genetics"].waitForExistence(timeout: 10),
      "The built-in course remains in discovery after importing and relaunching")
    let discovery = XCTAttachment(screenshot: app.screenshot())
    discovery.name = "Permanent biology discovery course after relaunch"
    discovery.lifetime = .keepAlways
    add(discovery)
  }

  @MainActor func testEmailRegistrationAndLoginSurviveAppRelaunch() async throws {
    continueAfterFailure = false
    let configuration = URLSessionConfiguration.ephemeral
    configuration.timeoutIntervalForRequest = 1
    configuration.connectionProxyDictionary = [:]
    let session = URLSession(configuration: configuration)
    let url = URL(string: nativeTestBaseURL)!.appendingPathComponent("api/native-test/health")
    guard let (data, _) = try? await session.data(from: url),
      let health = try? JSONDecoder().decode([String: Bool].self, from: data),
      health["fixture_adapters"] == true
    else {
      throw XCTSkip("Use scripts/test-cloud.sh to run the isolated native account UI test")
    }
    let app = XCUIApplication()
    app.launchEnvironment["BOOKCOURSE_API_BASE_URL"] = nativeTestBaseURL
    app.launchArguments = ["--cloud-test-session", UUID().uuidString]
    app.launch()
    let email = app.textFields["cloud.email"]
    XCTAssertTrue(email.waitForExistence(timeout: 15))
    app.buttons["cloud.registerMode"].tap()
    email.tap()
    email.typeText("xiaolin@zhiwo.test")
    XCTAssertFalse(app.textFields["cloud.nickname"].exists,
      "Registration must only need email and verification code")
    let resendAllowedAfter = Date().addingTimeInterval(61)
    tap(app.buttons["cloud.sendCode"], app: app)
    let demo = app.staticTexts["cloud.demoCode"]
    XCTAssertTrue(demo.waitForExistence(timeout: 15))
    let code = String(demo.label.filter(\.isNumber).suffix(6))
    XCTAssertEqual(code.count, 6)
    let codeField = app.textFields["cloud.code"]
    tap(codeField, app: app)
    codeField.typeText(code)
    tap(app.buttons["cloud.verify"], app: app)
    completePersonalization(app)
    let profile = app.buttons["tab-profile"]
    XCTAssertTrue(profile.waitForExistence(timeout: 20))
    XCTAssertFalse(email.exists)
    tap(profile, app: app)
    tap(app.buttons["profile.resetPreferences"], app: app)
    XCTAssertFalse(app.buttons["onboarding.choice.1.exam"].isSelected)
    for choice in ["onboarding.choice.1.interest", "onboarding.time.under30",
      "onboarding.choice.3.focus", "onboarding.choice.4.principles", "onboarding.choice.5.selfDirected"] {
      tap(app.buttons[choice], app: app)
    }
    XCTAssertTrue(app.buttons["profile.resetPreferences"].waitForExistence(timeout: 20))
    let screenshot = XCTAttachment(screenshot: app.screenshot())
    screenshot.name = "Cloud account connected with original native home"
    screenshot.lifetime = .keepAlways
    add(screenshot)
    app.terminate()
    app.launch()
    XCTAssertTrue(profile.waitForExistence(timeout: 20))
    XCTAssertFalse(email.exists, "The App must restore its authenticated account after relaunch")
    profile.tap()
    tap(app.buttons["profile.settings"], app: app)
    XCTAssertTrue(app.staticTexts["探索兴趣内容"].waitForExistence(timeout: 10))
    XCTAssertTrue(app.staticTexts["30分钟以内"].exists)
    XCTAssertTrue(app.staticTexts["Native Learner"].exists)
    tap(app.buttons["settings.logout"], app: app)
    tap(app.buttons["确认退出"], app: app)
    XCTAssertTrue(email.waitForExistence(timeout: 15))
    // Explicit logout must request new answers on the next login, even after relaunch.
    app.terminate()
    app.launch()
    XCTAssertTrue(email.waitForExistence(timeout: 20))
    email.tap()
    email.typeText("xiaolin@zhiwo.test")
    // Respect the real account endpoint's 60-second resend limit.
    try await Task.sleep(for: .seconds(max(0, resendAllowedAfter.timeIntervalSinceNow)))
    tap(app.buttons["cloud.sendCode"], app: app)
    let loginDemo = app.staticTexts["cloud.demoCode"]
    XCTAssertTrue(loginDemo.waitForExistence(timeout: 15))
    let loginCode = String(loginDemo.label.filter(\.isNumber).suffix(6))
    XCTAssertEqual(loginCode.count, 6)
    tap(app.textFields["cloud.code"], app: app)
    app.textFields["cloud.code"].typeText(loginCode)
    tap(app.buttons["cloud.verify"], app: app)
    XCTAssertTrue(email.waitForNonExistence(timeout: 20))
    XCTAssertTrue(app.textFields["onboarding.name"].waitForExistence(timeout: 20))
    XCTAssertTrue(app.textFields["onboarding.name"].isHittable)
    XCTAssertNotEqual(app.textFields["onboarding.name"].value as? String, "Native Learner")
    let freshQuestions = XCTAttachment(screenshot: app.screenshot())
    freshQuestions.name = "Fresh personalization after explicit logout and sign-in"
    freshQuestions.lifetime = .keepAlways
    add(freshQuestions)
    completePersonalization(app)
    XCTAssertTrue(profile.waitForExistence(timeout: 20))
    app.terminate()
    app.launch()
    XCTAssertTrue(profile.waitForExistence(timeout: 20))
    XCTAssertFalse(app.textFields["onboarding.name"].exists)
  }

  @MainActor private func completePersonalization(_ app: XCUIApplication) {
    // The underlying questionnaire also exists behind the login/loading overlays.
    // Wait for those overlays to leave before requesting keyboard focus.
    XCTAssertTrue(app.textFields["cloud.email"].waitForNonExistence(timeout: 20))
    XCTAssertTrue(app.images["app.loading"].waitForNonExistence(timeout: 20))
    let name = app.textFields["onboarding.name"]
    XCTAssertTrue(name.waitForExistence(timeout: 20))
    tap(app.buttons["onboarding.stage.university"], app: app)
    XCTAssertTrue(app.buttons["onboarding.stage.university"].isSelected)
    name.tap()
    name.typeText("Native Learner\n")
    tap(app.buttons["备考提升成绩"], app: app)
    tap(app.buttons["30分钟-60分钟"], app: app)
    tap(app.buttons["更深入理解"], app: app)
    // Auto-advance persists the destination step before the app is terminated.
    XCTAssertTrue(app.buttons["onboarding.choice.4.examples"].waitForExistence(timeout: 20))
    app.terminate()
    app.launch()
    XCTAssertTrue(app.buttons["onboarding.choice.4.examples"].waitForExistence(timeout: 20))
    tap(app.buttons["上一步"], app: app)
    XCTAssertTrue(app.buttons["onboarding.choice.3.understanding"].isSelected)
    XCTAssertFalse(app.buttons["onboarding.choice.3.memory"].isSelected)
    tap(app.buttons["更牢固记忆"], app: app)
    tap(app.buttons["结合示例"], app: app)
    tap(app.buttons["共同规划"], app: app)
  }

  @MainActor private func tap(_ element: XCUIElement, app: XCUIApplication) {
    XCTAssertTrue(element.waitForExistence(timeout: 15))
    let done = app.buttons["cloud.keyboardDone"]
    if app.keyboards.firstMatch.exists, done.exists, element != done { done.tap() }
    for _ in 0..<8 {
      if element.isHittable { break }
      let scroll = app.scrollViews.firstMatch
      scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.94, dy: 0.70)).press(
        forDuration: 0.05,
        thenDragTo: scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.94, dy: 0.25)))
    }
    XCTAssertTrue(element.isHittable)
    element.tap()
  }
}

/// Drives the App from host-side command files so a live backend flow can be inspected step by step.
final class LiveDriverUITests: XCTestCase {
  @MainActor func testHostDrivenSession() throws {
    let environment = ProcessInfo.processInfo.environment
    guard let root = environment["BOOKCOURSE_LIVE_DRIVER_DIR"],
      let base = environment["BOOKCOURSE_REMOTE_API_BASE_URL"]
    else { throw XCTSkip("Set BOOKCOURSE_LIVE_DRIVER_DIR and BOOKCOURSE_REMOTE_API_BASE_URL") }
    let directory = URL(fileURLWithPath: root, isDirectory: true)
    let app = XCUIApplication()
    app.launchEnvironment["BOOKCOURSE_API_BASE_URL"] = base
    app.launch()
    func element(_ key: String) -> XCUIElement {
      if key.hasPrefix("sb:") {
        return XCUIApplication(bundleIdentifier: "com.apple.springboard")
          .descendants(matching: .any)[String(key.dropFirst(3))].firstMatch
      }
      if key.contains("#") {
        let pieces = key.split(separator: "#", maxSplits: 1).map(String.init)
        return app.descendants(matching: .any).matching(identifier: pieces[0])
          .element(boundBy: Int(pieces[1]) ?? 0)
      }
      return app.descendants(matching: .any)[key].firstMatch
    }
    var sequence = 0
    while true {
      let command = directory.appendingPathComponent("cmd-\(sequence).txt")
      guard let line = try? String(contentsOf: command, encoding: .utf8) else {
        Thread.sleep(forTimeInterval: 0.4)
        continue
      }
      let parts = line.trimmingCharacters(in: .whitespacesAndNewlines)
        .split(separator: " ", maxSplits: 1).map(String.init)
      let argument = parts.count > 1 ? parts[1] : ""
      var output = "ok"
      switch parts.first ?? "" {
      case "tap":
        let target = element(argument)
        if target.waitForExistence(timeout: 10) { target.tap() } else { output = "missing \(argument)" }
      case "type":
        app.typeText(argument.replacingOccurrences(of: "\\n", with: "\n"))
      case "value": output = String(describing: element(argument).value ?? "")
      case "set":
        let values = argument.split(separator: " ", maxSplits: 1).map(String.init)
        let target = element(values[0])
        if target.waitForExistence(timeout: 10) {
          target.tap()
          let current = target.value as? String ?? ""
          target.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: current.count))
          if values.count > 1 { target.typeText(values[1]) }
        } else { output = "missing " + values[0] }
      case "dragxy":
        let xy = argument.split(separator: " ").compactMap { Double($0) }
        app.coordinate(withNormalizedOffset: CGVector(dx: xy[0], dy: xy[1]))
          .press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: xy[2], dy: xy[3])))
      case "tapxy":
        let xy = argument.split(separator: " ").compactMap { Double($0) }
        app.coordinate(withNormalizedOffset: CGVector(dx: xy[0], dy: xy[1])).tap()
      case "swipeup": app.swipeUp()
      case "swipedown": app.swipeDown()
      case "wait":
        let pieces = argument.split(separator: " ", maxSplits: 1).map(String.init)
        let seconds = pieces.count > 1 ? Double(pieces[1]) ?? 30 : 30
        output = element(pieces[0]).waitForExistence(timeout: seconds) ? "found" : "timeout"
      case "exists": output = element(argument).exists ? "yes" : "no"
      case "relaunch":
        app.terminate()
        app.launch()
      case "sleep": Thread.sleep(forTimeInterval: Double(argument) ?? 1)
      case "quit":
        try? "bye".write(to: directory.appendingPathComponent("res-\(sequence).txt"), atomically: true, encoding: .utf8)
        return
      default: break
      }
      try? app.screenshot().pngRepresentation.write(to: directory.appendingPathComponent("shot-\(sequence).png"))
      let tree = parts.first == "tree" ? app.debugDescription : ""
      try? (output + "\n" + tree).write(
        to: directory.appendingPathComponent("res-\(sequence).txt"), atomically: true, encoding: .utf8)
      sequence += 1
    }
  }
}
