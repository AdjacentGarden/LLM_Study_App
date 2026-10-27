import ImageIO
import PDFKit
import UIKit
import UniformTypeIdentifiers
import Vision
import XCTest

@testable import BookCourse

final class AppleDocumentOCRTests: XCTestCase {
  private func fixture(_ name: String) throws -> URL {
    try XCTUnwrap(Bundle(for: Self.self).url(
      forResource: name, withExtension: nil, subdirectory: "ImportFixtures"))
  }
  private func directory() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("apple-ocr-\(UUID())")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
  }

  func testCapabilityMatchesAccurateVisionLanguageSupport() throws {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    let expected = try request.supportedRecognitionLanguages()
    let actual = AppleDocumentOCR.capability()
    XCTAssertEqual(actual.supportedLanguages, expected)
    XCTAssertEqual(actual.requestRevision, request.revision)
    XCTAssertEqual(actual.available, AppleDocumentOCR.languages.allSatisfy(expected.contains))
    XCTAssertEqual(actual.unavailableReason == nil, actual.available)
  }

  func testPDFTextLayerAvoidsOCRAndKeepsOriginalPageNumbers() async throws {
    let recognizer = OCRSpy()
    let progress = OCRProgressRecorder()
    let source = try await AppleDocumentOCR.extract(
      url: fixture("text.pdf"), bookID: "local", filename: "text.pdf", recognizer: recognizer,
      onProgress: { progress.append($0) })
    XCTAssertEqual(recognizer.count, 0)
    XCTAssertEqual(source.chunks.map(\.page_start), [1, 2])
    XCTAssertTrue(source.chunks[0].text.contains("Native learning page one"))
    XCTAssertTrue(source.chunks.allSatisfy {
      $0.source_metadata?["parser"] == .string("pdfkit-text")
        && $0.source_metadata?["network_required"] == .bool(false)
    })
    XCTAssertEqual(progress.values.filter { $0.phase == .completed }.map(\.completedPages), [1, 2])
    assertSourceOnly(source)
  }

  func testMixedPDFKeepsBlankPageAndPhysicalAnchorsWithoutInventedChapters() async throws {
    let folder = try directory()
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("mixed.pdf")
    let bounds = CGRect(x: 0, y: 0, width: 300, height: 400)
    let pdf = UIGraphicsPDFRenderer(bounds: bounds).pdfData { context in
      context.beginPage()
      ("Original text layer" as NSString).draw(at: CGPoint(x: 20, y: 30), withAttributes: [
        .font: UIFont.systemFont(ofSize: 20), .foregroundColor: UIColor.black,
      ])
      context.beginPage() // A genuinely blank physical page must not renumber page 3.
      context.beginPage()
    }
    try pdf.write(to: url)
    let recognizer = OCRSpy(texts: ["", "扫描资料 Chemistry " + String(repeating: "中文", count: 1300)])
    let source = try await AppleDocumentOCR.extract(
      url: url, bookID: "mixed", filename: "mixed.pdf", recognizer: recognizer)
    XCTAssertEqual(recognizer.count, 2)
    XCTAssertEqual(source.summary.page_count, 3)
    XCTAssertEqual(source.chunks.first?.page_start, 1)
    let thirdPage = source.chunks.filter { $0.page_start == 3 }
    XCTAssertGreaterThan(thirdPage.count, 1)
    XCTAssertEqual(thirdPage.map(\.text).joined(), "扫描资料 Chemistry " + String(repeating: "中文", count: 1300))
    XCTAssertTrue(thirdPage.allSatisfy {
      $0.page_end == 3 && $0.text.count <= 1200 && $0.printed_page_start == nil
        && $0.source_metadata?["parser"] == .string("apple-vision-ios")
        && $0.source_metadata?["physical_page_verified"] == .bool(true)
        && $0.source_metadata?["recognition_level"] == .string("accurate")
    })
    XCTAssertFalse(source.chunks.contains { $0.page_start == 2 })
    XCTAssertTrue(recognizer.sizes.allSatisfy {
      $0.width <= AppleDocumentOCR.maximumDimension && $0.height <= AppleDocumentOCR.maximumDimension
    })
    assertSourceOnly(source)
  }

  func testRotatedImageIsOrientedBeforeRecognitionAndSourcePersists() async throws {
    let folder = try directory()
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("rotated.jpg")
    let image = UIGraphicsImageRenderer(size: CGSize(width: 160, height: 90), format: {
      let format = UIGraphicsImageRendererFormat()
      format.scale = 1
      return format
    }()).image { context in UIColor.white.setFill(); context.fill(CGRect(x: 0, y: 0, width: 160, height: 90)) }
    let destination = try XCTUnwrap(CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil))
    CGImageDestinationAddImage(destination, try XCTUnwrap(image.cgImage), [kCGImagePropertyOrientation: 6] as CFDictionary)
    XCTAssertTrue(CGImageDestinationFinalize(destination))
    let recognizer = OCRSpy(texts: ["化学 Chemistry: 吸热反应吸收热量。"])
    let source = try await AppleDocumentOCR.extract(
      url: url, bookID: "saved", filename: "rotated.jpg", recognizer: recognizer)
    XCTAssertEqual(recognizer.sizes.first?.width, 90)
    XCTAssertEqual(recognizer.sizes.first?.height, 160)
    XCTAssertEqual(source.chunks.first?.source_metadata?["locator_kind"], .string("image"))
    XCTAssertEqual(source.chunks.first?.source_metadata?["physical_page_verified"], .bool(false))
    let repository = try LocalBookCourseRepository(directory: folder.appendingPathComponent("repository"))
    try await repository.saveSources([source])
    let reopened = try LocalBookCourseRepository(directory: folder.appendingPathComponent("repository"))
    let restored = try await reopened.loadSources()
    XCTAssertEqual(restored, [source])
    assertSourceOnly(source)
  }

  func testUnavailableRuntimeFailsExplicitlyWithoutRecognizingOrChangingOriginal() async throws {
    let url = try fixture("image.png")
    let original = try Data(contentsOf: url)
    let recognizer = OCRSpy(available: false)
    do {
      _ = try await AppleDocumentOCR.extract(
        url: url, bookID: "blocked", filename: "image.png", recognizer: recognizer)
      XCTFail("Unavailable device accepted OCR")
    } catch {
      XCTAssertTrue(error.localizedDescription.contains("本机"))
    }
    XCTAssertEqual(recognizer.count, 0)
    XCTAssertEqual(try Data(contentsOf: url), original)
  }

  func testCancelledTaskDoesNotReturnPartialContentOrLoseOriginal() async throws {
    let url = try fixture("scan.pdf")
    let recognizer = OCRSpy(cancelDuringRecognition: true)
    do {
      _ = try await AppleDocumentOCR.extract(
        url: url, bookID: "cancelled", filename: "scan.pdf", recognizer: recognizer)
      XCTFail("Cancelled OCR returned partial content")
    } catch { XCTAssertTrue(error is CancellationError) }
    XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
  }

  func testParentTaskCancellationStopsInFlightRecognition() async throws {
    let entered = expectation(description: "entered local recognition")
    let recognizer = OCRSpy(waitForCancellation: true, didStart: { entered.fulfill() })
    let url = try fixture("image.png")
    let task = Task {
      try await AppleDocumentOCR.extract(
        url: url, bookID: "parent-cancel", filename: "image.png", recognizer: recognizer)
    }
    await fulfillment(of: [entered], timeout: 3)
    task.cancel()
    do {
      _ = try await task.value
      XCTFail("Cancelled parent returned OCR content")
    } catch { XCTAssertTrue(error is CancellationError) }
    XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
  }

  func testInvalidFilesAndNonLocalURLsAreRejected() async throws {
    for (url, filename) in [
      (try fixture("corrupt.pdf"), "corrupt.pdf"),
      (try fixture("corrupt.png"), "corrupt.png"),
      (try fixture("plain.txt"), "plain.txt"),
      (try XCTUnwrap(URL(string: "https://example.invalid/document.pdf")), "document.pdf"),
    ] {
      let recognizer = OCRSpy()
      do {
        _ = try await AppleDocumentOCR.extract(url: url, bookID: "invalid", filename: filename, recognizer: recognizer)
        XCTFail("Invalid input accepted: \(filename)")
      } catch { XCTAssertFalse(error.localizedDescription.isEmpty) }
      XCTAssertEqual(recognizer.count, 0)
    }
  }

  /// Runs Apple's real OCR on synthetic non-private pixels, with no server API.
  func testRealVisionRecognizesSyntheticChineseAndEnglishImageOffline() async throws {
    let capability = AppleDocumentOCR.capability()
    guard capability.available else { throw XCTSkip(capability.unavailableReason ?? "Vision unavailable") }
    let folder = try directory()
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("synthetic.png")
    let image = UIGraphicsImageRenderer(size: CGSize(width: 800, height: 220)).image { context in
      UIColor.white.setFill()
      context.fill(CGRect(x: 0, y: 0, width: 800, height: 220))
      ("CHEMISTRY ENERGY" as NSString).draw(at: CGPoint(x: 35, y: 70), withAttributes: [
        .font: UIFont.systemFont(ofSize: 48), .foregroundColor: UIColor.black,
      ])
      ("化学反应与能量" as NSString).draw(at: CGPoint(x: 35, y: 140), withAttributes: [
        .font: UIFont.systemFont(ofSize: 40), .foregroundColor: UIColor.black,
      ])
    }
    try XCTUnwrap(image.pngData()).write(to: url)
    let source = try await AppleDocumentOCR.extract(url: url, bookID: "vision", filename: "synthetic.png")
    let text = source.chunks.map(\.text).joined().uppercased()
    XCTAssertTrue(text.contains("CHEMISTRY"), text)
    XCTAssertTrue(text.contains("ENERGY"), text)
    XCTAssertTrue(text.contains("化学"), text)
    XCTAssertTrue(text.contains("能量"), text)
    XCTAssertEqual(source.chunks.first?.source_metadata?["network_required"], .bool(false))
    assertSourceOnly(source)
  }

  private func assertSourceOnly(_ source: SourceContent, file: StaticString = #filePath, line: UInt = #line) {
    XCTAssertEqual(source.summary.status, "source_ready", file: file, line: line)
    XCTAssertEqual(source.summary.content_mode, "local_original", file: file, line: line)
    XCTAssertEqual(source.summary.average_confidence, 0, file: file, line: line)
    XCTAssertEqual(source.summary.chapter_count, 0, file: file, line: line)
    XCTAssertTrue(source.chapters.isEmpty && source.lessons.isEmpty, file: file, line: line)
    XCTAssertTrue(source.flashcards.isEmpty && source.quizzes.isEmpty, file: file, line: line)
    XCTAssertTrue(source.plan.tasks.isEmpty, file: file, line: line)
  }
}

private final class OCRSpy: AppleDocumentRecognizing, @unchecked Sendable {
  private let lock = NSLock()
  private var storedSizes: [(width: Int, height: Int)] = []
  let texts: [String]
  let available: Bool
  let cancelDuringRecognition: Bool
  let waitForCancellation: Bool
  let didStart: (@Sendable () -> Void)?
  init(texts: [String] = ["Recognized text"], available: Bool = true,
       cancelDuringRecognition: Bool = false, waitForCancellation: Bool = false,
       didStart: (@Sendable () -> Void)? = nil) {
    self.texts = texts
    self.available = available
    self.cancelDuringRecognition = cancelDuringRecognition
    self.waitForCancellation = waitForCancellation
    self.didStart = didStart
  }
  var count: Int { sizes.count }
  var sizes: [(width: Int, height: Int)] {
    lock.lock(); defer { lock.unlock() }
    return storedSizes
  }
  func capability() -> AppleDocumentOCR.Capability {
    .init(available: available, unavailableReason: available ? nil : "此设备的本机 OCR 不可用，文件不会上传",
          supportedLanguages: available ? AppleDocumentOCR.languages : [], requestRevision: 3)
  }
  func recognize(_ image: CGImage, cancellation: AppleDocumentOCRCancellation) throws -> String {
    lock.lock()
    let index = storedSizes.count
    storedSizes.append((image.width, image.height))
    lock.unlock()
    didStart?()
    if waitForCancellation {
      let deadline = Date().addingTimeInterval(4)
      while Date() < deadline {
        try cancellation.check()
        Thread.sleep(forTimeInterval: 0.01)
      }
      throw AppleDocumentOCR.Failure.invalid("Cancellation did not reach recognizer")
    }
    if cancelDuringRecognition { cancellation.cancel() }
    try cancellation.check()
    return texts[min(index, texts.count - 1)]
  }
}

private final class OCRProgressRecorder: @unchecked Sendable {
  private let lock = NSLock()
  private var stored: [AppleDocumentOCR.Progress] = []
  var values: [AppleDocumentOCR.Progress] {
    lock.lock(); defer { lock.unlock() }
    return stored
  }
  func append(_ value: AppleDocumentOCR.Progress) {
    lock.lock(); defer { lock.unlock() }
    stored.append(value)
  }
}
