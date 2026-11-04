import CoreText
import SwiftUI
import UIKit
import XCTest

@testable import BookCourse

final class LearningTests: XCTestCase {
  func testBilingualFontResolvesLatinAndChineseWithMatchingWeights() {
    let cases: [(Font.Weight, String, String)] = [
      (.light, "Helvetica-Light", "PingFangSC-Light"),
      (.regular, "Helvetica", "PingFangSC-Regular"),
      (.medium, "Helvetica", "PingFangSC-Medium"),
      (.semibold, "Helvetica-Bold", "PingFangSC-Semibold"),
      (.bold, "Helvetica-Bold", "PingFangSC-Semibold"),
    ]
    let text = "CloudPath 123 中文学习，。" as NSString
    for (weight, latin, chinese) in cases {
      let font = DS.uiFont(.reading, weight: weight)
      XCTAssertEqual(font.pointSize, 16)
      let line = CTLineCreateWithAttributedString(NSAttributedString(
        string: text as String, attributes: [.font: font]))
      let runs = CTLineGetGlyphRuns(line) as! [CTRun]
      var checkedLatin = false
      var checkedChinese = false
      for run in runs {
        let attributes = CTRunGetAttributes(run) as NSDictionary
        let runFont = attributes[kCTFontAttributeName] as! CTFont
        let name = CTFontCopyPostScriptName(runFont) as String
        let range = CTRunGetStringRange(run)
        let fragment = text.substring(with: NSRange(location: range.location, length: range.length))
        if fragment.contains(where: { $0.isASCII && !$0.isWhitespace }) {
          XCTAssertEqual(name, latin, "Latin run: \(fragment)")
          checkedLatin = true
        }
        if fragment.contains(where: { "中文学习，。".contains($0) }) {
          XCTAssertEqual(name, chinese, "Chinese run: \(fragment)")
          checkedChinese = true
        }
      }
      XCTAssertTrue(checkedLatin)
      XCTAssertTrue(checkedChinese)
    }
  }

  func testBilingualLineHeightIncludesChineseMetrics() throws {
    let chinese = try XCTUnwrap(UIFont(name: "PingFangSC-Semibold", size: 16))
    XCTAssertGreaterThanOrEqual(DS.lineHeight(.reading, weight: .semibold), chinese.lineHeight)
    XCTAssertGreaterThanOrEqual(DS.lineHeight(.reading, weight: .semibold), DS.uiFont(.reading, weight: .semibold).lineHeight)
  }

  func testPrettyReadingPreservesMixedTextAndFitsPhoneAndPadWidths() {
    let font = DS.uiFont(.reading)
    let text = "孟德尔以豌豆的一对相对性状为切口，把复杂遗传现象拆成可计数的后代比例：F1 只表现一个亲本性状，F2 又出现另一个性状且接近 3∶1；用成对遗传因子在形成配子时分离的假说，并用测交检验，便能把现象转化为分离定律。"
    func advance(_ character: Character) -> CGFloat {
      (String(character) as NSString).size(withAttributes: [.font: font]).width
    }
    for width: CGFloat in [331.84375, 721.5] {
      let lines = LearningPrettyLineBreaker.lines(in: text, width: width, advance: advance)
      XCTAssertEqual(lines.joined(), text)
      XCTAssertGreaterThan(lines.count, 1)
      for line in lines {
        XCTAssertLessThanOrEqual(line.reduce(0) { $0 + advance($1) }, width + 0.01)
        XCTAssertFalse(line.hasSuffix("F"), "Keep Latin tokens together")
        XCTAssertFalse("，。；：！？、）".contains(line.first!))
      }
    }
  }

  func testDirectoryKeepsFormalSectionsAndLocalPagesButOmitsFrontmatter() {
    let root = chapter("c1", title: "第1章 遗传因子", level: 1)
    let section = chapter("c1s1", title: "第1节 孟德尔的实验", level: 2, parent: "c1")
    let subheading = chapter("detail", title: "提出问题", level: 3, parent: "c1")
    let local = chapter("page", title: "导入资料第一页", level: 2, parent: "c1", source: "local-original")
    let front = chapter("frontmatter", title: "前言", level: 1)
    let result = learningDirectory([front, root, section, subheading, local])
    XCTAssertEqual(result.map(\.id), ["c1"])
    XCTAssertEqual(result[0].sections.map(\.id), ["c1s1", "page"])
  }

  func testSharedIllustrationsAreDistributedBeforeSecondSlots() {
    let first = block("first", assets: ["shared", "second"])
    let second = block("second", assets: ["shared", "second"])
    let result = learningReadingSections(
      blocks: [first, second], assets: [asset("shared"), asset("second")])
    XCTAssertEqual(result[0].assets.map(\.id), ["shared"])
    XCTAssertEqual(result[1].assets.map(\.id), ["second"])
    XCTAssertEqual(Set(result.flatMap { $0.assets.map(\.id) }).count, 2)
  }

  func testPageScansExcludedButExtractedFiguresRetained() {
    var scan = asset("scan")
    scan.source_type = "extracted"
    scan.type = "page_image"
    var normalizedScan = asset("normalized")
    normalizedScan.source_type = "extracted"
    normalizedScan.bbox = [0, 0, 1, 1]
    var figure = asset("figure")
    figure.source_type = "extracted"
    figure.bbox = [0.2, 0.3, 0.8, 0.9]
    let result = learningReadingSections(
      blocks: [block("content", assets: ["scan", "normalized", "figure"])],
      assets: [scan, normalizedScan, figure])
    XCTAssertEqual(result[0].assets.map(\.id), ["figure"])
  }

  func testPreferredToolsHonorGoalsWithoutDuplicatingCards() {
    let routes = learningPreferredTools([
      "goals": ["exam", "systematic"], "contentFoci": ["notes"],
      "reviews": ["periodic", "mistakes"],
    ])
    XCTAssertEqual(routes, [.assignment, .mistakes, .flashcards, .notes])
  }

  func testChapterProgressCountsInProgressAsHalfAndIgnoresOtherChapters() {
    let tasks = [
      StudyTask(
        task_id: "one", user_id: "u", day: 1, title: "一", task_type: "lesson", minutes: 25,
        chapter_id: "c2s1", status: "in_progress", weak_points: []),
      StudyTask(
        task_id: "two", user_id: "u", day: 2, title: "二", task_type: "lesson", minutes: 25,
        chapter_id: "c2s1", status: "pending", weak_points: []),
      StudyTask(
        task_id: "other", user_id: "u", day: 3, title: "三", task_type: "lesson", minutes: 25,
        chapter_id: "c3s1", status: "done", weak_points: []),
    ]
    XCTAssertEqual(learningChapterProgress(tasks, chapterIDs: ["c2s1"]), 0.25)
    XCTAssertEqual(learningReadingProgress(tasks, chapterIDs: ["c2s1"],
      completedChapterIDs: ["c2s1"]), 1, "Pending plan tasks must not hide a completed lesson")
    XCTAssertEqual(learningReadingProgress([], chapterIDs: ["c2s1", "c2s2"],
      completedChapterIDs: ["c2s1"]), 0.5)
    XCTAssertEqual(learningTaskStatus("processing"), "进行中")
  }

  func testPrintedPageRangePreservesOriginalSpan() {
    XCTAssertEqual(learningPageLabel(11, end: 13, printed: 16), "教材第 16–18 页")
    XCTAssertEqual(learningPageLabel(11, printed: 16), "教材第 16 页")
    XCTAssertEqual(learningPageLabel(0, end: 0, printed: 2, printedEnd: 8), "教材第 2–8 页")
  }

  func testQuestionKindUsesExplicitTypeBeforeChoiceCount() {
    var question = QuizQuestion(
      question_id: "q", book_id: "book", lesson_id: "lesson", chapter_id: "c1", prompt: "判断内容",
      choices: ["正确", "错误"], answer: "正确", explanation: "依据", concept: "概念", source_chunk_ids: [],
      page_start: 1, page_end: 1)
    XCTAssertEqual(learningQuestionKind(question), "judgment")
    question.question_type = "short-answer"
    XCTAssertEqual(learningQuestionKind(question), "short-answer")
  }

  private func chapter(
    _ id: String, title: String, level: Int, parent: String? = nil, source: String = "parser"
  ) -> Chapter {
    Chapter(
      chapter_id: id, level: level, source_title: title, ai_title: title, page_start: 1,
      page_end: 5, confidence: 100, status: "confirmed", source: source, parent_id: parent)
  }
  private func block(_ id: String, assets: [String]) -> LessonBlock {
    LessonBlock(
      block_id: id, block_type: "knowledge", title: id, content: "内容", citations: [],
      source_chunk_ids: [], asset_ids: assets, ai_generated: false)
  }
  private func asset(_ id: String) -> BookAsset {
    BookAsset(
      asset_id: id, book_id: "book", source_type: "ai_generated", type: "figure", caption: id,
      image_url: "/assets/\(id).webp", thumbnail_url: "", source_chunk_ids: [], concepts: [])
  }
}
