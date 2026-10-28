import XCTest

final class CloudAccountUITests: XCTestCase {
  private var nativeTestBaseURL: String {
    ProcessInfo.processInfo.environment["BOOKCOURSE_NATIVE_TEST_BASE_URL"]
      ?? "http://127.0.0.1:18101"
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
    XCTAssertTrue(email.waitForExistence(timeout: 20))
    XCTAssertTrue(app.textFields["cloud.code"].exists)
    XCTAssertTrue(app.buttons["cloud.settings"].isHittable)
    XCTAssertFalse(app.textFields["cloud.server"].exists)
    XCTAssertFalse(app.buttons["cloud.verify"].isEnabled)
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
    if !demo.registered {
      let nickname = app.textFields["cloud.nickname"]
      tap(nickname, app: app)
      nickname.typeText("Remote Test Learner")
    }
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
    for option in ["正常节奏，按计划来", "整块固定时间", "应试备考", "重点和考点", "导学笔记沉淀", "定期复盘"] {
      tap(app.buttons[option], app: app)
      tap(app.buttons["courseSetup.next"], app: app)
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
    for option in ["正常节奏，按计划来", "整块固定时间", "应试备考", "重点和考点", "导学笔记沉淀", "定期复盘"] {
      tap(app.buttons[option], app: app)
      tap(app.buttons["courseSetup.next"], app: app)
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
    let nickname = app.textFields["cloud.nickname"]
    nickname.tap()
    nickname.typeText("Native UI Learner")
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
