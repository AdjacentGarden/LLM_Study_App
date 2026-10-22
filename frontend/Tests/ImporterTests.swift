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

  func testPDFTextKeepsPageNumbersAndOriginalCitations() throws {
    let source = try extract("text.pdf")
    XCTAssertEqual(source.summary.page_count, 2)
    XCTAssertEqual(source.lessons.count, 2)
    XCTAssertEqual(source.chunks.map(\.page_start), [1, 2])
    XCTAssertTrue(source.chunks[0].text.contains("Native learning page one"))
    XCTAssertTrue(source.chunks[1].text.contains("Native learning page two"))
    XCTAssertEqual(source.chapters.first?.page_end, 2)
    for lesson in source.lessons {
      for block in lesson.blocks {
        XCTAssertFalse(block.ai_generated)
        XCTAssertTrue(
          block.citations.allSatisfy {
            $0.page_start == lesson.page_start && $0.page_end == lesson.page_end
          })
        XCTAssertTrue(
          block.source_chunk_ids.allSatisfy { id in
            source.chunks.contains { $0.id == id && $0.book_id == "book_local_test" }
          })
      }
    }
  }

  func testScannedPDFAndImageRemainReadableWithoutInventedText() throws {
    for name in ["scan.pdf", "image.png"] {
      let source = try extract(name)
      XCTAssertEqual(source.summary.page_count, 1, name)
      XCTAssertTrue(source.chunks.isEmpty, name)
      XCTAssertTrue(source.lessons.first?.blocks.isEmpty == true, name)
      XCTAssertFalse(source.lessons.first?.warnings.isEmpty ?? true, name)
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
      XCTAssertTrue(source.lessons.allSatisfy { $0.warnings.isEmpty }, name)
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
    XCTAssertEqual(source.plan.tasks.map(\.day), [1, 2])
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
}
