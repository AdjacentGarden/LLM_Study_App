import AVFoundation
import PencilKit
import XCTest

@testable import BookCourse

final class NoteTests: XCTestCase {
  func testNoteMediaAndCourseIdentitySurviveRepositoryReopen() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: directory)
    let audio = Data([0x52, 0x49, 0x46, 0x46])
    let path = try await repository.saveAttachment(audio, extension: "wav")
    let note = StudyNote(
      id: "first", kind: .voice, title: "同一教材的课程甲", body: "保留原始记录",
      anchor: NoteAnchor(
        courseID: "course-a", bookID: "biology", chapterID: "chapter-a", page: 16, printedPage: 18),
      position: UnitPointValue(x: 0.25, y: 0.75), audioPath: path, duration: 3,
      transcript: "确认后的逐字稿", organizedText: "独立整理版")
    let other = StudyNote(
      id: "second", kind: .text, title: "同一教材的课程乙", body: "独立笔记",
      anchor: NoteAnchor(courseID: "course-b", bookID: "biology", page: 16))
    try await repository.saveNote(note)
    try await repository.saveNote(other)
    let reopened = try LocalBookCourseRepository(includeFixtures: true, directory: directory)
    let restored = try await reopened.loadNotes()
    XCTAssertEqual(restored.count, 2)
    XCTAssertEqual(restored.first { $0.id == "first" }, note)
    XCTAssertEqual(try Data(contentsOf: reopened.attachmentURL(path)), audio)
    try await reopened.deleteNote("first")
    let remaining = try await repository.loadNotes()
    XCTAssertEqual(remaining.map(\.id), ["second"])
    XCTAssertEqual(remaining.first?.anchor?.courseID, "course-b")
  }

  @MainActor func testOriginalPageRestoresAcrossRestartWithoutCrossingCourseOrBook() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: directory)
    let store = AppStore(repository: repository)
    await store.boot()
    store.selectedCourseID = "course-a"
    store.selectedBookID = "book_biology_2"
    store.selectedChapterID = "c2s1"
    store.selectedLessonID = "lesson_meiosis"
    NotesReadingPosition.save(37, courseID: "course-a", bookID: "book_biology_2", store: store)
    NotesReadingPosition.save(18, courseID: "course-b", bookID: "book_biology_2", store: store)
    NotesReadingPosition.save(6, courseID: "course-a", bookID: "another-book", store: store)
    await store.flush()
    let reopened = try LocalBookCourseRepository(includeFixtures: true, directory: directory)
    let loaded = try await reopened.loadSnapshot()
    let snapshot = try XCTUnwrap(loaded)
    XCTAssertEqual(
      NotesReadingPosition.initialPage(
        courseID: "course-a", bookID: "book_biology_2", target: nil, locations: snapshot.locations,
        pageCount: 125), 37)
    XCTAssertEqual(
      NotesReadingPosition.initialPage(
        courseID: "course-b", bookID: "book_biology_2", target: nil, locations: snapshot.locations,
        pageCount: 125), 18)
    XCTAssertEqual(
      NotesReadingPosition.initialPage(
        courseID: "course-a", bookID: "another-book", target: nil, locations: snapshot.locations,
        pageCount: 10), 6)
    XCTAssertEqual(snapshot.locations["course-a::book_biology_2"]?.chapterID, "c2s1")
    XCTAssertNil(snapshot.locations["course-a::another-book"]?.chapterID)
    let citation = SourcePageTarget(bookID: "book_biology_2", title: "原文引用", page: 11)
    XCTAssertEqual(
      NotesReadingPosition.initialPage(
        courseID: "course-a", bookID: "book_biology_2", target: citation,
        locations: snapshot.locations, pageCount: 125), 11)
    XCTAssertEqual(
      NotesReadingPosition.initialPage(
        courseID: "course-a", bookID: "another-book", target: citation,
        locations: snapshot.locations, pageCount: 10), 6)
    XCTAssertEqual(
      NotesReadingPosition.initialPage(
        courseID: "course-a", bookID: "book_biology_2", target: citation,
        locations: snapshot.locations, pageCount: 7), 7)
  }

  @MainActor func testTextAutosaveDebouncesToLatestAndSurvivesRepositoryReopen() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: folder)
    let saved = expectation(description: "Debounced draft saved")
    var writes: [String] = []
    let note = StudyNote(
      id: "autosave", kind: .text, title: "Original", body: "",
      anchor: NoteAnchor(courseID: "course-a", bookID: "biology", page: 11),
      position: UnitPointValue(x: 0.3, y: 0.7))
    let draft = NotesTextDraftPersistence(note: note) { record in
      try await repository.saveNote(record)
      writes.append(record.body)
      saved.fulfill()
    }
    draft.stage(
      title: "Edited", body: "Obsolete partial text", anchor: note.anchor, position: note.position)
    try await Task.sleep(for: .milliseconds(200))
    let lastEdit = Date()
    draft.stage(
      title: "Edited", body: "Latest durable understanding", anchor: note.anchor,
      position: note.position)
    await fulfillment(of: [saved], timeout: 3)
    XCTAssertGreaterThanOrEqual(Date().timeIntervalSince(lastEdit), 0.45)
    XCTAssertEqual(writes, ["Latest durable understanding"])
    let reopened = try LocalBookCourseRepository(includeFixtures: true, directory: folder)
    let restored = try await reopened.loadNotes()
    XCTAssertEqual(restored.count, 1)
    XCTAssertEqual(restored.first?.id, "autosave")
    XCTAssertEqual(restored.first?.body, "Latest durable understanding")
    XCTAssertEqual(restored.first?.anchor, note.anchor)
    XCTAssertFalse(draft.hasUnsavedContent)
  }

  @MainActor func testLateTextWriteCannotOverwriteNewerDraft() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: folder)
    let firstStarted = expectation(description: "First save started")
    var writes: [String] = []
    let note = StudyNote(id: "serial", kind: .text, title: "Serial", body: "")
    let draft = NotesTextDraftPersistence(note: note) { record in
      if record.body == "First" {
        firstStarted.fulfill()
        try await Task.sleep(for: .milliseconds(120))
      }
      try await repository.saveNote(record)
      writes.append(record.body)
    }
    draft.stage(title: note.title, body: "First", anchor: nil, position: nil)
    let first = Task { await draft.flush() }
    await fulfillment(of: [firstStarted], timeout: 2)
    draft.stage(title: note.title, body: "Newest", anchor: nil, position: nil)
    let second = await draft.flush()
    let initial = await first.value
    XCTAssertTrue(initial)
    XCTAssertTrue(second)
    XCTAssertEqual(writes, ["First", "Newest"])
    let restored = try await repository.loadNotes()
    XCTAssertEqual(restored.first?.body, "Newest")
    XCTAssertFalse(draft.hasUnsavedContent)
  }

  @MainActor func testTextUpgradeToVoiceKeepsIDBodyAnchorAndSingleRecord() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: folder)
    let note = StudyNote(
      id: "one-annotation", kind: .text, title: "My thought", body: "",
      anchor: NoteAnchor(courseID: "course-a", bookID: "biology", chapterID: "c2s1", page: 11),
      position: UnitPointValue(x: 0.45, y: 0.6))
    let draft = NotesTextDraftPersistence(note: note) { try await repository.saveNote($0) }
    draft.stage(
      title: note.title, body: "Text entered before recording", anchor: note.anchor,
      position: note.position)
    let upgraded = await draft.upgradeToVoice()
    var voice = try XCTUnwrap(upgraded)
    XCTAssertEqual(voice.id, note.id)
    XCTAssertEqual(voice.kind, .voice)
    XCTAssertEqual(voice.anchor, note.anchor)
    XCTAssertEqual(voice.position, note.position)
    XCTAssertEqual(voice.body, "Text entered before recording")
    let beforeAudio = try await repository.loadNotes()
    XCTAssertEqual(beforeAudio.first?.kind, .text)
    XCTAssertEqual(beforeAudio.first?.body, voice.body)
    let audio = NotesAudioController()
    audio.makeSample()
    let url = try XCTUnwrap(audio.audioURL)
    defer {
      audio.shutdown()
      try? FileManager.default.removeItem(at: url)
    }
    voice.audioPath = try await repository.saveAttachment(Data(contentsOf: url), extension: "wav")
    voice.duration = 8
    try await repository.saveNote(voice)
    // The departing text editor must not revert the recorder's new kind/media.
    draft.stage(title: "Old editor", body: "Late text callback", anchor: nil, position: nil)
    _ = await draft.flush()
    let reopened = try LocalBookCourseRepository(includeFixtures: true, directory: folder)
    let restored = try await reopened.loadNotes()
    XCTAssertEqual(restored.count, 1)
    XCTAssertEqual(restored.first, voice)
    let path = try XCTUnwrap(restored.first?.audioPath)
    XCTAssertEqual(
      try AVAudioPlayer(contentsOf: reopened.attachmentURL(path)).duration, 8, accuracy: 0.01)
  }

  @MainActor func testTextSaveFailurePreservesDraftForRetry() async throws {
    enum Failure: Error { case simulatedDiskFailure }
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: folder)
    var shouldFail = true
    let draft = NotesTextDraftPersistence(
      note: StudyNote(id: "retry-text", kind: .text, title: "Retry", body: "")
    ) { note in
      if shouldFail { throw Failure.simulatedDiskFailure }
      try await repository.saveNote(note)
    }
    draft.stage(title: "Retry", body: "Preserved draft", anchor: nil, position: nil)
    let failed = await draft.flush()
    XCTAssertFalse(failed)
    XCTAssertNotNil(draft.error)
    XCTAssertEqual(draft.latest.body, "Preserved draft")
    XCTAssertTrue(draft.hasUnsavedContent)
    shouldFail = false
    let retried = await draft.flush()
    XCTAssertTrue(retried)
    XCTAssertNil(draft.error)
    let notes = try await repository.loadNotes()
    XCTAssertEqual(notes.first?.body, "Preserved draft")
  }

  func testAnnotationConversationSurvivesBodyEditUpgradeAndRepositoryReopen() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let repository = try LocalBookCourseRepository(includeFixtures: true, directory: directory)
    let original = StudyNote(id: "annotation", kind: .text, title: "原文批注", body: "最初理解",
      anchor: NoteAnchor(courseID: "course", bookID: "biology", page: 11))
    try await repository.saveNote(original)
    var replied = original
    replied.conversation = [ChatMessage(role: "user", text: "我的理解对吗？"),
                           ChatMessage(role: "assistant", text: "请结合教材原文核对。")]
    try await repository.saveNote(replied)
    var staleDraft = original
    staleDraft.body = "结合问答修正后的理解"
    var merged = NotesAnnotationRecord.mergingConversation(draft: staleDraft, stored: replied)
    XCTAssertEqual(merged.body, staleDraft.body)
    XCTAssertEqual(merged.conversation, replied.conversation)
    merged.kind = .voice
    try await repository.saveNote(merged)
    let reopened = try LocalBookCourseRepository(includeFixtures: true, directory: directory)
    let restored = try await reopened.loadNotes()
    XCTAssertEqual(restored.count, 1)
    XCTAssertEqual(restored[0].id, original.id)
    XCTAssertEqual(restored[0].kind, .voice)
    XCTAssertEqual(restored[0].body, staleDraft.body)
    XCTAssertEqual(restored[0].conversation, replied.conversation)
    var unrelated = replied
    unrelated.id = "another-annotation"
    XCTAssertNil(NotesAnnotationRecord.mergingConversation(draft: original, stored: unrelated).conversation)
    let oldJSON = try JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as! [String: Any]
    XCTAssertNil(oldJSON["conversation"])
    XCTAssertNil(try JSONDecoder().decode(StudyNote.self, from: JSONSerialization.data(withJSONObject: oldJSON)).conversation)
  }

  func testVoicePreviewShowsFinishedNoteAndNeverLegacyTranscript() {
    let note = StudyNote(kind: .voice, title: "语音批注", body: "录音前输入的想法", transcript: "确认后的逐字稿")
    XCTAssertEqual(NotesAnnotationRecord.voicePreview(note), "录音前输入的想法")
    var beforeTranscription = note
    beforeTranscription.body = ""
    XCTAssertEqual(NotesAnnotationRecord.voicePreview(beforeTranscription), "录音已保存，点击生成笔记。")
    XCTAssertEqual(NotesAnnotationRecord.voiceText(beforeTranscription), "")
    var finished = note
    finished.organizedText = "吸热反应会吸收热量。"
    XCTAssertEqual(NotesAnnotationRecord.voicePreview(finished), "吸热反应会吸收热量。")
    XCTAssertEqual(NotesAnnotationRecord.voiceText(finished), "吸热反应会吸收热量。")
    XCTAssertEqual(finished.transcript, "确认后的逐字稿")
  }

  func testDrawingKeepsRelativeLocationWhenPageWidthChanges() throws {
    let points = [CGPoint(x: 80, y: 160), CGPoint(x: 160, y: 240)].enumerated().map {
      index, point in
      PKStrokePoint(
        location: point, timeOffset: Double(index) / 10, size: CGSize(width: 3, height: 3),
        opacity: 1, force: 0.8, azimuth: 0, altitude: .pi / 2)
    }
    let stroke = PKStroke(
      ink: PKInk(.pen, color: .purple),
      path: PKStrokePath(controlPoints: points, creationDate: Date(timeIntervalSince1970: 0)))
    let original = PKDrawing(strokes: [stroke])
    let normalized = NotesDrawingCoordinates.normalized(original, displayWidth: 400)
    let restored = try PKDrawing(data: normalized.dataRepresentation())
    let wide = NotesDrawingCoordinates.displayed(restored, displayWidth: 800)
    let same = NotesDrawingCoordinates.displayed(restored, displayWidth: 400)
    XCTAssertEqual(
      wide.strokes[0].renderBounds.midX / 800, original.strokes[0].renderBounds.midX / 400,
      accuracy: 0.001)
    XCTAssertEqual(
      wide.strokes[0].renderBounds.midY / 800, original.strokes[0].renderBounds.midY / 400,
      accuracy: 0.001)
    XCTAssertEqual(same.bounds.minX, original.bounds.minX, accuracy: 0.01)
    XCTAssertEqual(same.bounds.maxY, original.bounds.maxY, accuracy: 0.01)
  }

  @MainActor func testSampleAudioIsDecodableAndExplicitlyMarkedAsSample() throws {
    let controller = NotesAudioController()
    controller.makeSample()
    let url = try XCTUnwrap(controller.audioURL)
    defer {
      controller.shutdown()
      try? FileManager.default.removeItem(at: url)
    }
    XCTAssertTrue(controller.isSample)
    XCTAssertNil(controller.error)
    let decoded = try AVAudioPlayer(contentsOf: url)
    XCTAssertEqual(decoded.duration, 8, accuracy: 0.01)
  }
}
