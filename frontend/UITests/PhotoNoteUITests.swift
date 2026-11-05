import XCTest

final class PhotoNoteUITests: XCTestCase {
  override func setUpWithError() throws { continueAfterFailure = false }
  private func launch(_ session: String) -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments = ["--fixture", "--fixture-session", session, "--route", "notes", "--chapter", "c2s1",
      "--fixture-reduce-motion", "--fixture-photo-server", "http://127.0.0.1:19286"]
    app.launch()
    XCTAssertTrue(app.wait(for: .runningForeground, timeout: 20))
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
  private func importPhoto(_ app: XCUIApplication) {
    tap(app.buttons["notes.photo.import"].firstMatch, app: app)
    tap(app.buttons["photo.library"].firstMatch, app: app)
    let photo = app.images.matching(identifier: "PXGGridLayout-Info").firstMatch
    XCTAssertTrue(photo.waitForExistence(timeout: 15), app.debugDescription)
    photo.tap()
    XCTAssertTrue(app.buttons["photo.generate"].waitForExistence(timeout: 15), app.debugDescription)
  }
  private func generate(_ app: XCUIApplication) {
    tap(app.buttons["photo.generate"].firstMatch, app: app)
    XCTAssertTrue(app.textViews["photo.organized"].waitForExistence(timeout: 45), app.debugDescription)
    XCTAssertTrue((app.textViews["photo.transcript"].value as? String ?? "").contains("2n → n"))
    XCTAssertTrue((app.textViews["photo.organized"].value as? String ?? "").contains("不是有丝分裂"))
  }
  func testAlbumImportGeneratePreviewSaveAndRelaunchKeepsOriginal() throws {
    let session = UUID().uuidString
    let app = launch(session)
    importPhoto(app); generate(app)
    tap(app.textViews["photo.transcript"], app: app)
    app.textViews["photo.transcript"].typeText("\n核对完成")
    tap(app.buttons["photo.keyboard.done"].firstMatch, app: app)
    tap(app.buttons["photo.save"].firstMatch, app: app)
    XCTAssertTrue(app.buttons["notes.photo.original"].firstMatch.waitForExistence(timeout: 15))
    let screenshot = XCTAttachment(screenshot: app.screenshot()); screenshot.name = "photo-note-saved"; screenshot.lifetime = .keepAlways; add(screenshot)
    app.terminate()
    let reopened = launch(session)
    XCTAssertTrue(reopened.buttons["notes.photo.original"].firstMatch.waitForExistence(timeout: 15))
    tap(reopened.buttons["打开照片笔记"].firstMatch, app: reopened)
    XCTAssertTrue(reopened.textViews["photo.organized"].waitForExistence(timeout: 15))
    XCTAssertTrue((reopened.textViews["photo.transcript"].value as? String ?? "").contains("核对完成"))
  }
  func testAppendAddsSecondPhotoToExistingNoteWithoutLosingFirstOriginal() throws {
    let app = launch(UUID().uuidString)
    importPhoto(app); generate(app)
    tap(app.buttons["photo.save"].firstMatch, app: app)
    XCTAssertTrue(app.buttons["notes.photo.original"].firstMatch.waitForExistence(timeout: 15))
    importPhoto(app); generate(app)
    tap(app.buttons["photo.destination"].firstMatch, app: app)
    let destination = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "追加到：减数分裂照片笔记")).firstMatch
    XCTAssertTrue(destination.waitForExistence(timeout: 5), app.debugDescription)
    destination.tap()
    tap(app.buttons["photo.save"].firstMatch, app: app)
    let originals = app.buttons.matching(identifier: "notes.photo.original")
    XCTAssertTrue(originals.firstMatch.waitForExistence(timeout: 15))
    XCTAssertEqual(originals.count, 2)
  }
  func testClosingDuringCaptureKeepsOriginalDraftForLaterGeneration() throws {
    let session = UUID().uuidString
    let app = launch(session)
    importPhoto(app)
    app.buttons["关闭"].firstMatch.tap()
    app.terminate()
    let reopened = launch(session)
    XCTAssertTrue(reopened.buttons["notes.photo.original"].firstMatch.waitForExistence(timeout: 15))
    tap(reopened.buttons["打开照片笔记"].firstMatch, app: reopened)
    XCTAssertTrue(reopened.buttons["photo.generate"].waitForExistence(timeout: 15))
    generate(reopened)
  }
}
