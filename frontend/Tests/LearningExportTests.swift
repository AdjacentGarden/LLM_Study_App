import PDFKit
import XCTest

@testable import BookCourse

final class LearningExportTests: XCTestCase {
  @MainActor func testExportCreatesReadableMultipagePDFWithActualText() throws {
    let body = (0..<200).map { "第 \($0) 条：来自用户原稿的学习笔记。" }.joined(separator: "\n")
    let url = try LearningPDFExporter.write(title: "个人学习记录", sections: [("我的文字笔记", body)])
    defer { try? FileManager.default.removeItem(at: url) }
    let pdf = try XCTUnwrap(PDFDocument(url: url))
    XCTAssertGreaterThan(pdf.pageCount, 1)
    let content = try XCTUnwrap(pdf.string)
    XCTAssertTrue(content.contains("个人学习记录"), "Extracted title: \(content.prefix(100))")
    XCTAssertTrue(content.contains("第 199 条"), "The last page must not be silently truncated: \(content.suffix(150))")
    XCTAssertTrue(try Data(contentsOf: url).starts(with: Data("%PDF-".utf8)))
  }

  @MainActor func testEmptyModulesDoNotReportSuccessfulExport() {
    XCTAssertThrowsError(try LearningPDFExporter.write(title: "尚无内容", sections: [("导学", "   \n")]))
  }
}
