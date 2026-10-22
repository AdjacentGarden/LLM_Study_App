import CryptoKit
import UIKit
import XCTest

@testable import BookCourse

final class CoreTests: XCTestCase {
  @MainActor func testLastSelectedLearningCourseSurvivesRelaunch() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = AppStore(repository: try LocalBookCourseRepository(directory: directory))
    await store.boot()
    let course = try XCTUnwrap(store.courses.first {
      $0.resourceIDs.contains("catalog_high_school_math_required_2")
    })
    store.selectCourse(course.id)
    store.go(.study)
    await store.flush()

    let reopened = AppStore(repository: try LocalBookCourseRepository(directory: directory))
    await reopened.boot()
    XCTAssertEqual(reopened.currentCourse?.id, course.id)
    XCTAssertEqual(reopened.selectedBookID, "catalog_high_school_math_required_2")
    await reopened.flush()
  }

  func testEveryBundledSourceDecodesWithoutLosingIDs() throws {
    let sources = try ContentBundle.load([SourceContent].self, name: "sources")
    XCTAssertEqual(sources.count, 6)
    XCTAssertEqual(Set(sources.map(\.id)).count, 6)
    let biology = try XCTUnwrap(sources.first { $0.id == "book_biology_2" })
    XCTAssertFalse(biology.lessons.isEmpty)
    XCTAssertFalse(biology.quizzes.isEmpty)
    XCTAssertEqual(biology.summary.page_count, 125)
    for source in sources {
      let chapters = Set(source.chapters.map(\.id))
      for lesson in source.lessons {
        XCTAssertEqual(lesson.book_id, source.id)
        XCTAssertTrue(chapters.contains(lesson.chapter_id))
      }
    }
  }
  func testPublishedPagesAreCompleteAndHashesMatch() throws {
    struct Page: Decodable {
      var book_id: String
      var pdf_page: Int
      var url: String
      var sha256: String
    }
    struct Manifest: Decodable { var assets: [Page] }
    let pages = try ContentBundle.load(Manifest.self, name: "published-pages").assets
    XCTAssertEqual(pages.count, 125)
    XCTAssertEqual(Set(pages.map(\.pdf_page)), Set(1...125))
    for page in pages {
      XCTAssertEqual(page.book_id, "book_biology_2")
      let url = try XCTUnwrap(ContentBundle.url(page.url))
      let hash = SHA256.hash(data: try Data(contentsOf: url)).map { String(format: "%02x", $0) }
        .joined()
      XCTAssertEqual(hash, page.sha256, "PDF page \(page.pdf_page)")
    }
  }
  @MainActor func testChapterEditsRemainDraftUntilExplicitConfirmation() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(directory: folder)
    let store = AppStore(repository: repository)
    await store.boot()
    store.makeSampleCourse()
    let original = try XCTUnwrap(store.chapters.first(where: { $0.id == "c2s1" }))
    var edited = original
    edited.ai_title = "Edited draft title"
    store.updateChapter(edited)
    await store.flush()
    XCTAssertEqual(store.chapters.first(where: { $0.id == original.id })?.title, edited.title)
    let before = try await repository.loadSources()
    XCTAssertEqual(
      before.first(where: { $0.id == "book_biology_2" })?.chapters.first(where: {
        $0.id == original.id
      })?.title, original.title)
    let reopened = AppStore(repository: try LocalBookCourseRepository(directory: folder))
    await reopened.boot()
    reopened.selectSource("book_biology_2")
    XCTAssertEqual(reopened.chapters.first(where: { $0.id == original.id })?.title, edited.title)
    reopened.chapterDrafts["another-book"] = [edited]
    reopened.confirmChapters()
    await reopened.flush()
    let after = try await repository.loadSources()
    XCTAssertEqual(
      after.first(where: { $0.id == "book_biology_2" })?.chapters.first(where: {
        $0.id == original.id
      })?.title, edited.title)
    XCTAssertNil(reopened.chapterDrafts["book_biology_2"])
    XCTAssertNotNil(reopened.chapterDrafts["another-book"])
  }

  @MainActor func testCrossSourceCitationReturnsToOriginalCourseAndLesson() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = AppStore(repository: try LocalBookCourseRepository(directory: folder))
    await store.boot()
    store.makeSampleCourse()
    let originCourse = try XCTUnwrap(store.selectedCourseID)
    store.selectedChapterID = "c2s1"
    store.selectedLessonID = "lesson_meiosis"
    store.route = .lesson
    store.saveLocation()
    let otherBook = "catalog_high_school_math_required_2"
    store.openSource(SourcePageTarget(bookID: otherBook, title: "另一份资料", page: 3, from: .lesson))
    XCTAssertEqual(store.route, .source)
    XCTAssertEqual(store.selectedBookID, otherBook)
    NotesReadingPosition.save(3, courseID: store.selectedCourseID, bookID: otherBook, store: store)
    store.back()
    XCTAssertEqual(store.route, .lesson)
    XCTAssertEqual(store.selectedCourseID, originCourse)
    XCTAssertEqual(store.selectedBookID, "book_biology_2")
    XCTAssertEqual(store.selectedChapterID, "c2s1")
    XCTAssertEqual(store.selectedLessonID, "lesson_meiosis")
    XCTAssertNil(store.readerState.origin)
    XCTAssertNil(store.sourceTarget)
    await store.flush()
    let restored = try await store.repository.loadSnapshot()
    XCTAssertEqual(restored?.selectedBookID, "book_biology_2")
    XCTAssertEqual(restored?.locations[originCourse + "::book_biology_2"]?.chapterID, "c2s1")
    store.openSource(SourcePageTarget(bookID: "unavailable-book", title: "Unavailable", page: 11))
    XCTAssertEqual(store.route, .lesson)
    XCTAssertEqual(store.selectedBookID, "book_biology_2")
    XCTAssertNil(store.readerState.origin)
  }

  @MainActor func testInterruptedImportRetainsOriginalAndResumes() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(directory: folder)
    let original = Data("Recovered original text".utf8)
    let relative = try await repository.saveAttachment(original, extension: "txt")
    let file = repository.attachmentURL(relative)
    var source = try LocalFileImporter.extractSynchronously(
      url: file, bookID: "book_local_recovery", filename: "Recovered.txt")
    source.summary.status = "uploaded"
    try await repository.saveSources([source])
    try await repository.saveFilePaths([source.id: relative])
    var snapshot = AppSnapshot()
    snapshot.preferences = LearnerPreferences(
      displayName: "Fixture", primaryGoal: nil, dailyTime: nil)
    snapshot.draft = CourseDraft(name: "Recovered", resourceIDs: [source.id])
    snapshot.importJob = ImportJob(bookIDs: [source.id], status: .running, progress: 0.4)
    try await repository.saveSnapshot(snapshot)
    let store = AppStore(repository: try LocalBookCourseRepository(directory: folder))
    await store.boot()
    XCTAssertEqual(store.route, .parseReady)
    XCTAssertEqual(store.importJob?.status, .interrupted)
    XCTAssertEqual(try Data(contentsOf: file), original)
    await store.processImport()
    await store.flush()
    XCTAssertTrue(store.draft.parseCompleted)
    XCTAssertEqual(store.importJob?.status, .completed)
    XCTAssertEqual(
      store.sources.first(where: { $0.id == source.id })?.chunks.first?.text,
      "Recovered original text")
    let saved = try await repository.loadSnapshot()
    XCTAssertEqual(saved?.importJob?.status, .completed)
  }

  func testSQLiteSnapshotAndSourceSurviveReopen() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let a = try LocalBookCourseRepository(directory: directory)
    var snapshot = AppSnapshot()
    snapshot.preferences = LearnerPreferences(displayName: "小明同学", primaryGoal: nil, dailyTime: nil)
    snapshot.credits = 82
    snapshot.locations["course-a::book-a"] = StudyLocation(
      chapterID: "chapter-1", lessonID: "lesson-1", page: 9)
    try await a.saveSnapshot(snapshot)
    let b = try LocalBookCourseRepository(directory: directory)
    let restored = try await b.loadSnapshot()
    XCTAssertEqual(restored?.credits, 82)
    XCTAssertEqual(restored?.locations["course-a::book-a"]?.page, 9)
    XCTAssertEqual(restored?.preferences?.displayName, "小明同学")
  }
  @MainActor func testSourceSelectionAndCitationKeepResourceIdentity() async throws {
    let store = AppStore(
      repository: try LocalBookCourseRepository(
        directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    )
    await store.boot()
    store.makeSampleCourse()
    let lesson = try XCTUnwrap(store.lessons.first { $0.chapter_id == "c2s1" })
    store.openLesson(lesson)
    store.openCitation(
      Citation(
        bookID: "book_biology_2", chapterID: "c2s1", chapterTitle: "减数分裂", page: 11,
        printedPage: 16, chunkID: "test", quote: "原文", score: 1))
    XCTAssertEqual(store.route, .source)
    XCTAssertEqual(store.sourceTarget?.bookID, "book_biology_2")
    XCTAssertEqual(store.sourceTarget?.page, 11)
    store.back()
    XCTAssertEqual(store.route, .lesson)
    store.selectSource("catalog_high_school_math_required_2")
    XCTAssertTrue(store.lessons.isEmpty)
    XCTAssertNotEqual(store.selectedBookID, "book_biology_2")
    await store.flush()
  }
  @MainActor func testCreditReservationRefundAndInterruptedRecovery() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let a = AppStore(repository: try LocalBookCourseRepository(directory: directory))
    await a.boot()
    let token = try await a.reserveCredits(10)
    XCTAssertEqual(a.credits, 90)
    await a.refundCredits(token)
    XCTAssertEqual(a.credits, 100)
    let spent = try await a.reserveCredits(1)
    await a.completeCredits(spent)
    XCTAssertEqual(a.credits, 99)
    _ = try await a.reserveCredits(10)
    await a.flush()
    let b = AppStore(repository: try LocalBookCourseRepository(directory: directory))
    await b.boot()
    XCTAssertEqual(b.credits, 99)
    await b.flush()
  }
  @MainActor func testLogoutPreservesCoursesNotesAndCredits() async throws {
    let store = AppStore(
      repository: try LocalBookCourseRepository(
        directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    )
    await store.boot()
    store.completeOnboarding(name: "小明", goal: nil, dailyTime: nil)
    let count = store.courses.count
    let note = StudyNote(kind: .text, title: "测试笔记", body: "保留", anchor: store.makeAnchor())
    try await store.saveNote(note)
    store.logout()
    XCTAssertNil(store.preferences)
    XCTAssertEqual(store.route, .onboarding)
    XCTAssertEqual(store.courses.count, count)
    XCTAssertTrue(store.notes.contains { $0.id == note.id })
    await store.flush()
  }
  func testFontsHaveUniquePostScriptNames() {
    ContentBundle.registerFonts()
    XCTAssertNotNil(UIFont(name: "BookCourseNotoSansSC-Regular", size: 20))
    XCTAssertNotNil(UIFont(name: "BookCourseNotoSansSC-Bold", size: 20))
  }
  func testRoutesAndSheetsCoverSourceContracts() {
    XCTAssertEqual(Route.allCases.count, 28)
    XCTAssertEqual(SheetRoute.allCases.count, 6)
  }
  @MainActor func testFailedCreditWriteDoesNotSpendOrStartOperation() async throws {
    let repository = try LocalBookCourseRepository(
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(repository: repository)
    await store.boot()
    await store.flush()
    await repository.setWriteFaultForTesting(true)
    do {
      _ = try await store.reserveCredits(10)
      XCTFail("A failed reservation must not start the operation")
    } catch { XCTAssertEqual(store.credits, 100) }
    await repository.setWriteFaultForTesting(false)
    let ledger = try await repository.creditLedger()
    XCTAssertEqual(ledger.balance, 100)
    XCTAssertTrue(ledger.reservations.isEmpty)
  }
  @MainActor func testGroupedAssignmentChecksObjectiveQuestionsSeparatelyFromShortAnswer()
    async throws
  {
    let store = AppStore(
      repository: try LocalBookCourseRepository(
        directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    )
    await store.boot()
    store.makeSampleCourse()
    let judgment = try XCTUnwrap(store.quizzes.first { $0.question_type == "judgment" })
    let choice = try XCTUnwrap(store.quizzes.first { $0.question_type == "choice" })
    let short = try XCTUnwrap(store.quizzes.first { $0.question_type == "short-answer" })
    let index = try XCTUnwrap(choice.choices.firstIndex(of: choice.answer))
    let letter = Array("ABCD")[index]
    store.submitAssignment(
      question: short, answer: "判断题：\(judgment.answer)\n选择题：\(letter)｜\(choice.answer)\n简答题：我会继续复习")
    XCTAssertEqual(store.latestDiagnosis?.mistake_recorded, false)
    XCTAssertTrue(store.latestDiagnosis?.result.contains("已答对 2 题") == true)
    await store.flush()
  }
  @MainActor func testAllCommunityEntriesUseExactContentOrTheirOwnDirectory() async throws {
    let store = AppStore(
      repository: try LocalBookCourseRepository(
        directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    )
    await store.boot()
    for book in store.community {
      store.draft.name = book.title
      store.draft.diagnosis = ["goals": ["exam"]]
      store.importCommunity(book)
      XCTAssertEqual(store.currentCourse?.name, book.title)
      XCTAssertEqual(store.currentCourse?.diagnosis["goals"], ["exam"])
      if [
        "community_ecology", "community_functions", "community_motion",
        "community_theoretical_mechanics",
      ].contains(book.id) {
        XCTAssertEqual(store.currentSource?.id, book.id)
        XCTAssertTrue(store.currentSource?.lessons.isEmpty == true)
        XCTAssertTrue(store.currentSource?.chapters.allSatisfy { $0.page_start == 0 } == true)
      }
    }
    await store.flush()
  }
  func testDeletedDemoNoteIsNotSeededAgain() async throws {
    let repository = try LocalBookCourseRepository(
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    try await repository.seedDemoNotesIfNeeded()
    try await repository.deleteNote("ink-demo-needs-confirmation")
    try await repository.seedDemoNotesIfNeeded()
    let notes = try await repository.loadNotes()
    XCTAssertFalse(notes.contains { $0.id == "ink-demo-needs-confirmation" })
  }

}
