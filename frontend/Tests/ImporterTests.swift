import ImageIO
import PDFKit
import XCTest

@testable import BookCourse

final class ImporterTests: XCTestCase {
  private func fixture(_ name: String) throws -> URL {
    try XCTUnwrap(
      Bundle(for: ImporterTests.self).url(
        forResource: name, withExtension: nil, subdirectory: "ImportFixtures"),
      "Add Tests/Fixtures/ImportFixtures to the test target as a folder resource")
  }
  private func extract(_ name: String) throws -> SourceContent {
    try LocalFileImporter.extractSynchronously(
      url: fixture(name), bookID: "book_local_test", filename: name)
  }
  private func temporaryDirectory() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(
      "import-tests-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
  }

  func testPDFTextKeepsPageNumbersAndOriginalSourceAnchors() throws {
    let source = try extract("text.pdf")
    XCTAssertEqual(source.summary.page_count, 2)
    XCTAssertTrue(source.lessons.isEmpty)
    XCTAssertTrue(source.chapters.isEmpty)
    XCTAssertEqual(source.chunks.map(\.page_start), [1, 2])
    XCTAssertTrue(source.chunks[0].text.contains("Native learning page one"))
    XCTAssertTrue(source.chunks[1].text.contains("Native learning page two"))
    XCTAssertEqual(source.summary.status, "source_ready")
    XCTAssertEqual(source.summary.content_mode, "local_original")
    XCTAssertEqual(source.summary.chapter_count, 0)
    XCTAssertEqual(source.summary.average_confidence, 0)
    XCTAssertTrue(source.plan.tasks.isEmpty)
    for chunk in source.chunks {
      XCTAssertTrue(chunk.chapter_id.isEmpty)
      XCTAssertEqual(chunk.source_metadata?["locator_kind"], .string("pdf_page"))
      XCTAssertEqual(chunk.source_metadata?["physical_page_verified"], .bool(true))
      XCTAssertEqual(chunk.page_start, chunk.page_end)
      XCTAssertTrue(chunk.chunk_id.hasPrefix("book_local_test:p\(chunk.page_start):"))
    }
  }

  func testScannedPDFAndImageRemainReadableWithoutInventedText() throws {
    for name in ["scan.pdf", "image.png"] {
      let source = try extract(name)
      XCTAssertEqual(source.summary.page_count, 1, name)
      XCTAssertTrue(source.chunks.isEmpty, name)
      XCTAssertTrue(source.lessons.isEmpty, name)
      XCTAssertTrue(source.chapters.isEmpty, name)
      XCTAssertTrue(source.plan.tasks.isEmpty, name)
      XCTAssertEqual(source.summary.status, "source_ready", name)
      XCTAssertTrue(source.summary.next_title?.contains("暂无可提取文本") == true, name)
    }
    let pdf = try XCTUnwrap(PDFDocument(url: fixture("scan.pdf")))
    XCTAssertEqual(pdf.pageCount, 1)
    XCTAssertNotNil(pdf.page(at: 0)?.thumbnail(of: CGSize(width: 60, height: 80), for: .mediaBox))
    XCTAssertNotNil(CGImageSourceCreateWithURL(try fixture("image.png") as CFURL, nil))
  }

  func testOfficeWordRunsPresentationNumericOrderingAndSpreadsheetCells() throws {
    let word = try extract("document.docx")
    XCTAssertEqual(word.summary.page_count, 1)
    XCTAssertTrue(word.chunks.map(\.text).joined().contains("Office 文档导入\nDNA 复制一次"))
    let slides = try extract("slides.pptx")
    XCTAssertEqual(slides.summary.page_count, 3)
    XCTAssertEqual(slides.chunks.map(\.text), ["Slide 1 教材", "Slide 2 教材", "Slide 10 教材"])
    let sheet = try extract("workbook.xlsx")
    XCTAssertEqual(sheet.summary.page_count, 2)
    XCTAssertEqual(sheet.chunks.map(\.text), ["概念\t页码\nDNA 复制\t18\t42", "第二张表"])
  }

  func testTextMarkdownCSVPreserveOriginalUTF8Content() throws {
    for name in ["plain.txt", "notes.md", "table.csv"] {
      let source = try extract(name)
      let expected = try String(contentsOf: fixture(name), encoding: .utf8)
      XCTAssertEqual(source.chunks.map(\.text).joined(), expected, name)
      XCTAssertEqual(source.summary.page_count, 1, name)
      XCTAssertEqual(source.chunks.first?.page_start, 1, name)
      XCTAssertTrue(source.lessons.isEmpty, name)
      XCTAssertTrue(source.chapters.isEmpty, name)
      XCTAssertEqual(source.summary.status, "source_ready", name)
      XCTAssertEqual(source.summary.average_confidence, 0, name)
      XCTAssertEqual(source.chunks.first?.source_metadata?["physical_page_verified"], .bool(false), name)
    }
  }

  func testLongUnicodeTextSplitsWithoutLosingCharactersOrChangingPage() throws {
    let source = try extract("long.txt")
    let expected = try String(contentsOf: fixture("long.txt"), encoding: .utf8)
    XCTAssertGreaterThan(source.chunks.count, 1)
    XCTAssertEqual(source.chunks.map(\.text).joined(), expected)
    XCTAssertTrue(
      source.chunks.allSatisfy { $0.text.count <= 1200 && $0.page_start == 1 && $0.page_end == 1 })
    XCTAssertEqual(Set(source.chunks.map(\.id)).count, source.chunks.count)
  }

  func testAsyncExtractionUsesSameBookAndPageIdentity() async throws {
    let url = try fixture("text.pdf")
    let source = try await LocalFileImporter.extract(
      url: url, bookID: "my-book", filename: "renamed.PDF")
    XCTAssertEqual(source.summary.book_id, "my-book")
    XCTAssertEqual(source.summary.title, "renamed")
    XCTAssertTrue(
      source.chunks.allSatisfy { $0.book_id == "my-book" && $0.chunk_id.hasPrefix("my-book:") })
    XCTAssertTrue(source.plan.tasks.isEmpty)
    XCTAssertEqual(source.plan.days, 0)
    XCTAssertEqual(source.plan.daily_minutes, 0)
  }

  func testCorruptUnsupportedMissingXMLAndOversizedOfficeEntriesFail() throws {
    for name in [
      "corrupt.pdf", "corrupt.png", "corrupt.docx", "unsupported.bin", "invalid-utf8.txt",
      "missing-part.docx", "malformed-xml.docx", "no-slides.pptx", "oversized-entry.docx",
    ] {
      XCTAssertThrowsError(try extract(name), name)
    }
  }

  func testRepeatedContentGetsStableBookIDAndSeparateOriginalFiles() async throws {
    let folder = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(directory: folder)
    let first = try await LocalFileImporter.stage(url: fixture("plain.txt"), repository: repository)
    let second = try await LocalFileImporter.stage(
      url: fixture("plain.txt"), repository: repository)
    XCTAssertEqual(first.source.id, second.source.id)
    XCTAssertTrue(first.source.id.hasPrefix("book_local_"))
    XCTAssertNotEqual(first.relativePath, second.relativePath)
    XCTAssertEqual(first.source.summary.status, "uploaded")
    XCTAssertEqual(first.source.summary.average_confidence, 0)
    XCTAssertTrue(first.source.chapters.isEmpty && first.source.lessons.isEmpty)
    XCTAssertTrue(first.source.plan.tasks.isEmpty)
    XCTAssertEqual(first.source.plan.daily_minutes, 0)
    let expected = try Data(contentsOf: fixture("plain.txt"))
    XCTAssertEqual(try Data(contentsOf: repository.attachmentURL(first.relativePath)), expected)
    XCTAssertEqual(try Data(contentsOf: repository.attachmentURL(second.relativePath)), expected)
  }

  func testExtractionFailureLeavesStagedOriginalAvailableForRetry() async throws {
    let folder = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(directory: folder)
    let staged = try await LocalFileImporter.stage(
      url: fixture("unsupported.bin"), repository: repository)
    let original = repository.attachmentURL(staged.relativePath)
    XCTAssertThrowsError(
      try LocalFileImporter.extractSynchronously(
        url: original, bookID: staged.source.id, filename: "unsupported.bin"))
    XCTAssertEqual(try Data(contentsOf: original), try Data(contentsOf: fixture("unsupported.bin")))
  }

  @MainActor func testCancellationPreservesOriginalAndAllowsRetry() async throws {
    let folder = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(directory: folder)
    let staged = try await LocalFileImporter.stage(url: fixture("text.pdf"), repository: repository)
    let original = repository.attachmentURL(staged.relativePath)
    let task = Task { @MainActor in
      try await LocalFileImporter.extract(
        url: original, bookID: staged.source.id, filename: "text.pdf")
    }
    task.cancel()
    do {
      _ = try await task.value
      XCTFail("Cancelled extraction returned content")
    } catch { XCTAssertTrue(error is CancellationError) }
    XCTAssertTrue(FileManager.default.fileExists(atPath: original.path))
    let retried = try await LocalFileImporter.extract(
      url: original, bookID: staged.source.id, filename: "text.pdf")
    XCTAssertEqual(retried.summary.page_count, 2)
  }

  func testEmptyOversizedAndDirectoryInputsAreRejectedBeforeCopying() async throws {
    let folder = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: folder) }
    let repository = try LocalBookCourseRepository(
      directory: folder.appendingPathComponent("repository"))
    let oversized = folder.appendingPathComponent("oversized.txt")
    XCTAssertTrue(FileManager.default.createFile(atPath: oversized.path, contents: Data()))
    let handle = try FileHandle(forWritingTo: oversized)
    try handle.truncate(atOffset: UInt64(LocalFileImporter.maximumBytes + 1))
    try handle.close()
    for url in [try fixture("empty.txt"), oversized, folder] {
      do {
        _ = try await LocalFileImporter.stage(url: url, repository: repository)
        XCTFail("Invalid input accepted: \(url.lastPathComponent)")
      } catch {
        XCTAssertFalse(error.localizedDescription.isEmpty)
      }
    }
    let attachments = try FileManager.default.contentsOfDirectory(
      at: folder.appendingPathComponent("repository/attachments"), includingPropertiesForKeys: nil)
    XCTAssertTrue(attachments.isEmpty)
  }

  func testEveryLocalFormatRemainsSourceOnlyWithoutInventedTeachingOrPlan() throws {
    for name in ["text.pdf", "scan.pdf", "image.png", "document.docx", "slides.pptx",
                 "workbook.xlsx", "plain.txt", "notes.md", "table.csv", "long.txt"] {
      let source = try extract(name)
      XCTAssertEqual(source.summary.status, "source_ready", name)
      XCTAssertEqual(source.summary.chapter_count, 0, name)
      XCTAssertEqual(source.summary.average_confidence, 0, name)
      XCTAssertTrue(source.chapters.isEmpty && source.lessons.isEmpty, name)
      XCTAssertTrue(source.flashcards.isEmpty && source.quizzes.isEmpty, name)
      XCTAssertTrue(source.plan.tasks.isEmpty, name)
      XCTAssertEqual(source.plan.days, 0, name)
      XCTAssertEqual(source.plan.daily_minutes, 0, name)
      XCTAssertTrue(source.chunks.allSatisfy { $0.chapter_id.isEmpty }, name)
    }
  }

  func testOfficeLocationKindsAreNotClaimedAsPhysicalPdfPages() throws {
    for (name, kind) in [("document.docx", "document"), ("slides.pptx", "slide"),
                         ("workbook.xlsx", "worksheet")] {
      let source = try extract(name)
      XCTAssertTrue(source.chunks.allSatisfy {
        $0.source_metadata?["locator_kind"] == .string(kind)
          && $0.source_metadata?["physical_page_verified"] == .bool(false)
      }, name)
    }
  }

  func testUploadedServerDirectoryHasUnknownQualityAndNoInventedLessonSchedule() {
    let chapter = CloudChapter(chapter_id: "actual-chapter", order: 1, title: "真实目录标题",
      start_page: 2, end_page: 4, summary: "服务端原文摘要", knowledge_points: [], source_block_ids: [])
    let resource = CloudResource(id: "actual-resource", book_id: "actual-book", workspace_id: "workspace",
      revision: 0, filename: "actual.pdf", status: "ocr_ready",
      structure: CloudStructure(title: "Actual source", summary: "", source_page_count: 5, chapters: [chapter]),
      courses: [], progress: CloudProgress())
    let source = CloudMapping.source(resource)
    XCTAssertEqual(source.summary.status, "source_ready")
    XCTAssertEqual(source.summary.average_confidence, 0)
    XCTAssertEqual(source.chapters[0].confidence, 0)
    XCTAssertNotEqual(source.chapters[0].status, "confirmed")
    XCTAssertEqual(source.lessons[0].status, "pending")
    XCTAssertEqual(source.lessons[0].confidence, 0)
    XCTAssertFalse(source.lessons[0].warnings.isEmpty)
    XCTAssertTrue(source.plan.tasks.isEmpty)
    XCTAssertEqual(source.plan.daily_minutes, 0)
  }

  func testPublishedServerLessonKeepsRealContentAndWarningsWithoutInventedQuality() {
    let chapter = CloudChapter(chapter_id: "actual-chapter", order: 1, title: "真实目录标题",
      start_page: 2, end_page: 4, summary: "原文摘要", knowledge_points: [], source_block_ids: [])
    let lesson = CloudCourse(course_id: "published-course", version: 2, chapter_id: chapter.chapter_id,
      chapter_title: "已发布教学", opening: "服务端导学", summary: "已发布摘要", original_reading: [],
      knowledge_points: [], flashcards: [], worked_examples: [], checkpoint_questions: [],
      practice_items: [], estimated_minutes: 17, created_at: "2026-10-23T00:00:00Z",
      unresolved_source_warnings: ["原始来源警告"])
    let resource = CloudResource(id: "actual-resource", book_id: "actual-book", workspace_id: "workspace",
      revision: 0, filename: "actual.pdf", status: "ocr_ready",
      structure: CloudStructure(title: "Actual source", summary: "", source_page_count: 5, chapters: [chapter]),
      courses: [lesson], progress: CloudProgress())
    let source = CloudMapping.source(resource)
    XCTAssertEqual(source.summary.status, "ready")
    XCTAssertEqual(source.lessons[0].id, lesson.course_id)
    XCTAssertEqual(source.lessons[0].summary, lesson.summary)
    XCTAssertEqual(source.lessons[0].confidence, 0)
    XCTAssertTrue(source.lessons[0].warnings.contains("原始来源警告"))
    XCTAssertTrue(source.plan.tasks.isEmpty)  // The course DTO does not contain a real daily plan.
  }

}
