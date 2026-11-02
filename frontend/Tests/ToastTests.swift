import XCTest

@testable import BookCourse

final class ToastTests: XCTestCase {
  @MainActor func testRepeatedMessageReplacesAndOutlivesTheOriginalDeadline() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = AppStore(repository: try LocalBookCourseRepository(directory: directory))
    defer { store.dismissToast() }

    store.showToast("已保存到学习笔记", kind: .success)
    let firstID = try XCTUnwrap(store.toast?.id)
    try await Task.sleep(for: .milliseconds(1600))
    store.showToast("已保存到学习笔记", kind: .success)
    let secondID = try XCTUnwrap(store.toast?.id)
    XCTAssertNotEqual(firstID, secondID)
    store.dismissToast(id: firstID)
    XCTAssertEqual(store.toast?.id, secondID, "A stale dismissal must not hide the replacement")

    try await Task.sleep(for: .milliseconds(1900))
    XCTAssertEqual(store.toast?.id, secondID, "The original deadline must not hide a repeated message")
    try await Task.sleep(for: .milliseconds(1600))
    XCTAssertNil(store.toast, "The replacement should disappear after its own display interval")
  }

  @MainActor func testLoadingSurvivesTheTimeoutThenSuccessDismissesNormally() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = AppStore(repository: try LocalBookCourseRepository(directory: directory))
    defer { store.dismissToast() }

    store.showToast("正在保存笔记…", kind: .loading)
    let loadingID = try XCTUnwrap(store.toast?.id)
    try await Task.sleep(for: .milliseconds(3500))
    XCTAssertEqual(store.toast?.id, loadingID)

    store.showToast("课程已移除", kind: .success, subtitle: "原资料和笔记仍保留")
    XCTAssertEqual(store.toast?.kind, .success)
    XCTAssertEqual(store.toast?.announcement, "课程已移除。原资料和笔记仍保留")
    try await Task.sleep(for: .milliseconds(3500))
    XCTAssertNil(store.toast)
  }
}
