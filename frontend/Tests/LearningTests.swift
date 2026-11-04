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
          let fallback = chinese == "PingFangSC-Semibold" ? "BookCourseNotoSansSC-Bold" : "BookCourseNotoSansSC-Regular"
          XCTAssertTrue([chinese, fallback].contains(name), "Chinese run: \(fragment)")
          checkedChinese = true
        }
      }
      XCTAssertTrue(checkedLatin)
      XCTAssertTrue(checkedChinese)
    }
  }

  func testBilingualLineHeightIncludesChineseMetrics() throws {
    let base = DS.uiFont(.reading, weight: .semibold) as CTFont
    let chinese = CTFontCreateForString(base, "学习" as CFString, CFRange(location: 0, length: 2)) as UIFont
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

  func testExistingChemistryQuestionGetsStemAndThreeReadableSubquestions() {
    let source = "已知硫与氧同主族，硫原子比氧原子多一个电子层。请回答：（1）硫位于第几周期、第几族，最外层有几个电子？（2）硫在化学反应中容易得到几个电子、形成什么价态的化合物？（3）为什么在富含氧气的地表附近，含硫化合物中硫常呈+4价或+6价？"
    let concepts = ["硫位于元素周期表第三周期第ⅥA族，最外层有6个电子，化学反应中易得2个电子形成-2价硫的化合物。",
      "二氧化硫是无色、有刺激性气味的有毒气体，密度比空气大，易溶于水，是酸性氧化物，与水反应生成亚硫酸。"]
    let paragraphs = LearningReadingTypography.paragraphs(in: source, keyConcepts: concepts)
    XCTAssertEqual(paragraphs.count, 4)
    XCTAssertTrue(paragraphs[1].text.hasPrefix("（1）"))
    XCTAssertTrue(paragraphs[2].text.hasPrefix("（2）"))
    XCTAssertTrue(paragraphs[3].text.hasPrefix("（3）"))
    XCTAssertEqual(paragraphs.map(\.text).joined(), source)
    XCTAssertTrue(paragraphs[3].text.contains("+4价或+6价"))
    let emphasized = paragraphs.flatMap { paragraph in
      paragraph.emphasizedRanges.map { (paragraph.text as NSString).substring(with: $0) }
    }
    XCTAssertFalse(emphasized.isEmpty, "Old full-sentence concepts still yield short display emphasis")
    XCTAssertFalse(emphasized.contains("硫"))
    assertRestrainedEmphasis(paragraphs)
  }

  func testReadingPreservesMarkdownParagraphsAndUsesNativeWrapping() {
    let source = "## 核心概念\n原子序数等于核电荷数，**同主族**元素通常具有相同的最外层电子数。\n\n- 比较周期与族。\n- 再根据电子层判断位置。"
    let paragraphs = LearningReadingTypography.paragraphs(in: source)
    XCTAssertEqual(paragraphs.count, 4)
    XCTAssertEqual(paragraphs[0].text, "核心概念")
    XCTAssertEqual(paragraphs[0].kind, .heading)
    XCTAssertTrue(paragraphs[2].text.hasPrefix("• "))
    XCTAssertFalse(paragraphs.map(\.text).joined().contains("**"))
    XCTAssertEqual(paragraphs[1].emphasizedRanges.map {
      (paragraphs[1].text as NSString).substring(with: $0)
    }, ["同主族"])
    let attributed = LearningReadingTypography.attributedText(in: source)
    XCTAssertTrue(attributed.string.contains("\n"))
    XCTAssertFalse(attributed.string.contains("\u{2028}"))
    let bodyRange = (attributed.string as NSString).range(of: "原子序数")
    let style = attributed.attribute(.paragraphStyle, at: bodyRange.location, effectiveRange: nil) as? NSParagraphStyle
    XCTAssertEqual(style?.lineBreakMode, .byWordWrapping)
    XCTAssertGreaterThan(style?.paragraphSpacing ?? 0, 0)
    XCTAssertEqual(style?.maximumLineHeight, 0, "Native layout must accommodate bold glyph metrics")
  }

  func testReadingDoesNotSplitChemicalFormulaeDecimalsOrFunctionArguments() {
    let source = "将Fe(OH)3、Al2(SO4)3与Na2CO3·10H2O分别加入0.25 mol/L溶液；比较x(1)=2与x(2)=3，不把化学式中的数字当成子问。"
    let paragraphs = LearningReadingTypography.paragraphs(in: source)
    XCTAssertEqual(paragraphs.count, 1)
    XCTAssertEqual(paragraphs[0].text, source)
    let multiplication = "若a*b*c>0，结合H_2O与x_1，分析2*3*4和SO4^2-，保留原式。"
    XCTAssertEqual(LearningReadingTypography.plainText(in: multiplication), multiplication)
    let greek = "当a*β*γ > 0时，比较α*β*γ与原式，保留希腊字母及乘号。"
    XCTAssertEqual(LearningReadingTypography.plainText(in: greek), greek)
    for nested in ["（1）比较式（1）与式（2）的条件；（2）说明原因。",
      "（1）已知 f (2)=3，求其值；（2）说明原因。"] {
      let questions = LearningReadingTypography.paragraphs(in: nested)
      XCTAssertEqual(questions.count, 2)
      XCTAssertEqual(questions.map(\.text).joined(), nested)
      XCTAssertTrue(questions[1].text.hasPrefix("（2）说明"))
    }
  }

  func testReadingOnlySplitsLongParagraphsAtSentenceBoundaries() {
    let sentence = "通过比较元素在周期表中的位置与原子结构，判断它们的共同性质，再用具体实验现象检验推断。"
    let source = String(repeating: sentence, count: 6)
    let paragraphs = LearningReadingTypography.paragraphs(in: source)
    XCTAssertGreaterThan(paragraphs.count, 1)
    XCTAssertEqual(paragraphs.map(\.text).joined(), source)
    XCTAssertTrue(paragraphs.dropLast().allSatisfy { $0.text.hasSuffix("。") })
  }

  func testReadingDoesNotCarryWholeParagraphBoldIntoOldLessons() {
    let source = "**" + String(repeating: "最外层电子决定元素的部分化学性质，需要结合具体条件判断，不能把相似位置等同于全部性质相同。", count: 3) + "**"
    let paragraphs = LearningReadingTypography.paragraphs(in: source, keyConcepts: ["最外层电子", "化学性质", "具体条件"])
    XCTAssertFalse(paragraphs.map(\.text).joined().contains("**"))
    XCTAssertTrue(paragraphs.contains { !$0.emphasizedRanges.isEmpty })
    assertRestrainedEmphasis(paragraphs)
  }

  func testReadingPrioritizesExplicitShortEmphasisAndAllowsNoEmphasis() {
    let source = "对比实验时，先保持**温度**相同，再观察溶液颜色与反应现象，最后解释差异。"
    let paragraph = LearningReadingTypography.paragraphs(in: source, keyConcepts: ["溶液颜色", "反应现象"])[0]
    let marked = paragraph.emphasizedRanges.map { (paragraph.text as NSString).substring(with: $0) }
    XCTAssertEqual(marked, ["温度"])
    let short = LearningReadingTypography.paragraphs(in: "先读题，再作答。", keyConcepts: ["概念"])[0]
    XCTAssertTrue(short.emphasizedRanges.isEmpty)
    let repeated = LearningReadingTypography.paragraphs(in:
      "电子层与最外层电子相关。比较电子层时，应先数电子层，再看电子层中的电子数，避免混淆电子层与电子数。", keyConcepts: ["电子层"])
    XCTAssertEqual(repeated[0].emphasizedRanges.count, 1)
  }

  func testReadingRespectsCodeAndLatinWordBoundaries() {
    let source = "请保留`Fe(OH)3`与`a*b*c`的原始写法，formation并非独立的ion，正确区分完整英文词。"
    let paragraph = LearningReadingTypography.paragraphs(in: source, keyConcepts: ["Fe", "ion"])[0]
    XCTAssertTrue(paragraph.text.contains("Fe(OH)3"))
    XCTAssertTrue(paragraph.text.contains("a*b*c"))
    let marked = paragraph.emphasizedRanges.map { (paragraph.text as NSString).substring(with: $0) }
    XCTAssertFalse(marked.contains("Fe"))
    if let ion = paragraph.emphasizedRanges.first(where: { (paragraph.text as NSString).substring(with: $0) == "ion" }) {
      XCTAssertEqual(ion.location, (paragraph.text as NSString).range(of: "ion，").location)
    }
    let fenced = "```text\n  a*b*c\n\n  Fe(OH)3\n```"
    XCTAssertEqual(LearningReadingTypography.plainText(in: fenced), "  a*b*c\n\n  Fe(OH)3")
  }

  private func assertRestrainedEmphasis(_ paragraphs: [LearningReadingTypography.Paragraph], file: StaticString = #filePath, line: UInt = #line) {
    for paragraph in paragraphs where paragraph.kind != .heading {
      let count = paragraph.text.filter { !$0.isWhitespace }.count
      let boldCount = paragraph.emphasizedRanges.reduce(0) {
        $0 + (paragraph.text as NSString).substring(with: $1).filter { !$0.isWhitespace }.count
      }
      XCTAssertLessThanOrEqual(boldCount, Int(Double(count) * 0.20), file: file, line: line)
      XCTAssertLessThanOrEqual(paragraph.emphasizedRanges.count, count < 80 ? 1 : count < 200 ? 2 : 3, file: file, line: line)
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
