import CryptoKit
import UIKit
import XCTest

@testable import BookCourse

final class CoreTests: XCTestCase {
  @MainActor func testProductionFirstLaunchDoesNotSeedContentOrBalance() async throws {
    let repo = try LocalBookCourseRepository(
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(repository: repo)
    await store.boot()
    XCTAssertFalse(repo.includesFixtureContent)
    XCTAssertTrue(store.sources.isEmpty)
    XCTAssertTrue(store.courses.isEmpty)
    XCTAssertTrue(store.community.isEmpty)
    XCTAssertTrue(store.notes.isEmpty)
    XCTAssertTrue(store.mistakes.isEmpty)
    XCTAssertEqual(store.credits, 0)
    do { try await store.refreshCommunity(); XCTFail("No configured backend") }
    catch { XCTAssertTrue(store.community.isEmpty) }
    await store.flush()
  }


  func testLegacyPreviewRetirementPreservesEditedContentAndActualOriginals() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let old = try LocalBookCourseRepository(directory: root, includeFixtures: true)
    var previews = try ContentBundle.load([SourceContent].self, name: "sources")
    let importedID = previews[0].id
    let editedID = previews[1].id
    previews[1].summary.title = "用户修改后的教材名称"
    try await old.saveSources(previews)
    let actualBytes = Data("the user's original".utf8)
    let path = try await old.saveAttachment(actualBytes, extension: "pdf")
    try await old.saveFilePaths([importedID: path])
    try await old.seedDemoNotesIfNeeded()
    let personal = StudyNote(id: "personal", kind: .text, title: "用户笔记", body: "保留用户输入")
    try await old.saveNote(personal)
    let current = try LocalBookCourseRepository(directory: root)
    let sources = try await current.loadSources()
    let notes = try await current.loadNotes()
    XCTAssertEqual(Set(sources.map(\.id)), [importedID, editedID])
    XCTAssertEqual(sources.first { $0.id == editedID }?.summary.title, "用户修改后的教材名称")
    XCTAssertEqual(notes, [personal])
    XCTAssertEqual(try Data(contentsOf: current.attachmentURL(path)), actualBytes)
    let reopened = try LocalBookCourseRepository(directory: root)
    let afterRestart = try await reopened.loadSources()
    XCTAssertEqual(Set(afterRestart.map(\.id)), [importedID, editedID])
  }

  @MainActor func testLastSelectedLearningCourseSurvivesRelaunch() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = AppStore(repository: try LocalBookCourseRepository(includeFixtures: true, directory: directory))
    await store.boot()
    let course = try XCTUnwrap(store.courses.first {
      $0.resourceIDs.contains("catalog_high_school_math_required_2")
    })
    store.selectCourse(course.id)
    store.go(.study)
    await store.flush()

    let reopened = AppStore(repository: try LocalBookCourseRepository(includeFixtures: true, directory: directory))
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
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: folder)
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
    let reopened = AppStore(repository: try LocalBookCourseRepository(includeFixtures: true, directory: folder))
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
    let store = AppStore(repository: try LocalBookCourseRepository(includeFixtures: true, directory: folder))
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
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: folder)
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
    let store = AppStore(repository: try LocalBookCourseRepository(includeFixtures: true, directory: folder))
    await store.boot()
    XCTAssertEqual(store.route, .study)
    XCTAssertEqual(store.resumableCourseRoute, .parseReady)
    XCTAssertEqual(store.importJob?.status, .interrupted)
    XCTAssertEqual(try Data(contentsOf: file), original)
    store.go(try XCTUnwrap(store.resumableCourseRoute))
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
    let a = try LocalBookCourseRepository(includeFixtures: true, directory: directory)
    var snapshot = AppSnapshot()
    snapshot.preferences = LearnerPreferences(displayName: "小明同学", primaryGoal: nil, dailyTime: nil)
    snapshot.credits = 82
    snapshot.locations["course-a::book-a"] = StudyLocation(
      chapterID: "chapter-1", lessonID: "lesson-1", page: 9)
    try await a.saveSnapshot(snapshot)
    let b = try LocalBookCourseRepository(includeFixtures: true, directory: directory)
    let restored = try await b.loadSnapshot()
    XCTAssertEqual(restored?.credits, 82)
    XCTAssertEqual(restored?.locations["course-a::book-a"]?.page, 9)
    XCTAssertEqual(restored?.preferences?.displayName, "小明同学")
  }
  @MainActor func testSourceSelectionAndCitationKeepResourceIdentity() async throws {
    let store = AppStore(
      repository: try LocalBookCourseRepository(includeFixtures: true,
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
    let a = AppStore(repository: try LocalBookCourseRepository(includeFixtures: true, directory: directory))
    _ = try await a.repository.recoverCredits(startingBalance: 100)
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
    let b = AppStore(repository: try LocalBookCourseRepository(includeFixtures: true, directory: directory))
    await b.boot()
    XCTAssertEqual(b.credits, 99)
    await b.flush()
  }
  @MainActor func testLogoutPreservesCoursesNotesAndCredits() async throws {
    let store = AppStore(
      repository: try LocalBookCourseRepository(includeFixtures: true,
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
    XCTAssertEqual(Route.allCases.count, 30)
    XCTAssertTrue(Route.allCases.contains(.preferenceSetup))
    XCTAssertEqual(SheetRoute.allCases.count, 7)
  }
  @MainActor func testFailedCreditWriteDoesNotSpendOrStartOperation() async throws {
    let repository = try LocalBookCourseRepository(includeFixtures: true,
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    _ = try await repository.recoverCredits(startingBalance: 100)
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
      repository: try LocalBookCourseRepository(includeFixtures: true,
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
      repository: try LocalBookCourseRepository(includeFixtures: true,
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
    let repository = try LocalBookCourseRepository(includeFixtures: true,
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    try await repository.seedDemoNotesIfNeeded()
    try await repository.deleteNote("ink-demo-needs-confirmation")
    try await repository.seedDemoNotesIfNeeded()
    let notes = try await repository.loadNotes()
    XCTAssertFalse(notes.contains { $0.id == "ink-demo-needs-confirmation" })
  }

}

extension CoreTests {
  @MainActor func testImportedCourseKeepsItsTaskAfterStartingAnotherDraft() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let repo = try LocalBookCourseRepository(directory: root.appendingPathComponent("state"))
    let store = AppStore(repository: repo)
    for name in ["first", "second"] {
      let file = root.appendingPathComponent(name + ".txt")
      try Data("Course source \(name)".utf8).write(to: file)
      await store.importFile(file)
    }
    XCTAssertEqual(store.courses.count, 1, "One import batch belongs to one shelf course")
    let first = try XCTUnwrap(store.importState.courses.first)
    XCTAssertEqual(first.books.count, 2)
    XCTAssertEqual(store.courses[0].resourceIDs.count, 2)
    store.updateCourseImport(first.id) { $0.started = true; $0.job.status = .running; $0.job.progress = 0.4 }
    store.courseFeatureStartDraft()
    XCTAssertNil(store.draft.importCourseID)
    let other = root.appendingPathComponent("other.txt")
    try Data("A different course".utf8).write(to: other)
    await store.importFile(other)
    XCTAssertEqual(store.courses.count, 2)
    XCTAssertEqual(store.importState.courses.count, 2)
    let newDraft = store.draft
    store.openCourse(try XCTUnwrap(store.courses.first { $0.id == first.id }))
    XCTAssertEqual(store.route, .study)
    XCTAssertEqual(store.importState.viewedCourseID, first.id)
    XCTAssertEqual(store.draft, newDraft, "Viewing another task cannot overwrite the new creation draft")
    XCTAssertEqual(store.courseImport(for: first.id)?.ringProgress, 0.4)
    await store.flush()
    let saved = try await repo.loadSnapshot()
    XCTAssertEqual(saved?.courseImports?.count, 2)
    XCTAssertEqual(saved?.courseImports?.first?.books.count, 2)
    let restarted = AppStore(repository: repo)
    await restarted.boot()
    XCTAssertEqual(restarted.courses.count, 2)
    XCTAssertEqual(restarted.courseImport(for: first.id)?.job.status, .interrupted)
    XCTAssertEqual(restarted.courseImport(for: first.id)?.books.count, 2)
    restarted.openCourse(try XCTUnwrap(restarted.courses.first { $0.id == first.id }))
    XCTAssertEqual(restarted.importState.viewedCourseID, first.id)
    await restarted.flush()
  }

  @MainActor func testCompletingImportedCourseReusesItsShelfIdentity() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let repo = try LocalBookCourseRepository(directory: root.appendingPathComponent("state"))
    let store = AppStore(repository: repo)
    let file = root.appendingPathComponent("course.txt")
    try Data("Original course material".utf8).write(to: file)
    await store.importFile(file)
    let id = try XCTUnwrap(store.draft.importCourseID)
    let bookIDs = store.draft.resourceIDs
    await store.processImport()
    XCTAssertEqual(store.courseImport(for: id)?.job.status, .completed)
    XCTAssertNil(store.courseImport(for: id)?.ringProgress)
    store.openCourse(try XCTUnwrap(store.courses.first))
    XCTAssertEqual(store.route, .study, "The existing setup remains available after parsing")
    store.continueCourseImport(id)
    XCTAssertEqual(store.route, .courseSetup)
    XCTAssertNil(store.draft.editingCourseID, "A new import must retain the new-course questionnaire flow")
    store.createCourse(name: "Finished course", resourceIDs: bookIDs)
    XCTAssertEqual(store.courses.count, 1)
    XCTAssertEqual(store.courses.first?.id, id)
    XCTAssertEqual(store.courseImport(for: id)?.setupCompleted, true)
    store.openCourse(try XCTUnwrap(store.courses.first))
    XCTAssertEqual(store.route, .study)
    await store.flush()
  }

  @MainActor func testCoursePreparationDoesNotInventWholeBookPercentage() async throws {
    let repo = try LocalBookCourseRepository(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(repository: repo)
    store.draft = CourseDraft(name: "Pending", resourceIDs: ["book_local_pending"])
    let id = try XCTUnwrap(store.registerCourseImport())
    store.updateCourseImport(id) { $0.job.status = .running; $0.job.progress = 0.7 }
    XCTAssertEqual(store.courseImport(for: id)?.ringProgress, 0.7)
    store.updateCourseImport(id) { $0.job.preparationStage = "diagnostics" }
    XCTAssertNil(store.courseImport(for: id)?.ringProgress, "Preparation has stages rather than a reliable total percentage")
    store.rebindCourseImports(localID: "book_local_pending", bookID: "server-book")
    XCTAssertEqual(store.courseImport(for: id)?.books.first?.id, "server-book")
    XCTAssertEqual(store.courseImport(for: id)?.draft.resourceIDs, ["server-book"])
    let encoded = try JSONEncoder().encode(store.snapshot())
    let decoded = try JSONDecoder().decode(AppSnapshot.self, from: encoded)
    XCTAssertEqual(decoded.courseImports?.first?.job.preparationStage, "diagnostics")
    await store.flush()
  }

  @MainActor func testUploadRebindingMergesPlaceholderWithoutLosingPreparationProgress() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = AppStore(repository: try LocalBookCourseRepository(directory: root))
    store.draft = CourseDraft(name: "教材", resourceIDs: ["book_local_pending", "server-book"])
    let id = try XCTUnwrap(store.registerCourseImport())
    store.updateCourseImport(id) {
      $0.books[1].status = .running
      $0.books[1].stage = "diagnostics"
      $0.books[1].message = "诊断题已完成 11／15 题"
    }
    store.bindUploadedBook(localID: "book_local_pending",
      uploaded: CloudUpload(book_id: "server-book", filename: "教材.pdf", status: "uploaded"))
    let record = try XCTUnwrap(store.courseImport(for: id))
    XCTAssertEqual(record.books.count, 1)
    XCTAssertEqual(record.books.first?.stage, "diagnostics")
    XCTAssertEqual(record.books.first?.message, "诊断题已完成 11／15 题")
    XCTAssertEqual(record.draft.resourceIDs, ["server-book"])
    XCTAssertEqual(record.job.bookIDs, ["server-book"])
    XCTAssertEqual(store.courses.first?.resourceIDs, ["server-book"])
    await store.flush()
  }
}
