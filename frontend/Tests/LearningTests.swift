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
    let source = "## 核心概念\n原子序数等于核电荷数，**同主族元素**通常具有相同的最外层电子数。\n\n- 比较周期与族。\n- 再根据电子层判断位置。"
    let paragraphs = LearningReadingTypography.paragraphs(in: source)
    XCTAssertEqual(paragraphs.count, 4)
    XCTAssertEqual(paragraphs[0].text, "核心概念")
    XCTAssertEqual(paragraphs[0].kind, .heading)
    XCTAssertTrue(paragraphs[2].text.hasPrefix("• "))
    XCTAssertFalse(paragraphs.map(\.text).joined().contains("**"))
    XCTAssertEqual(paragraphs[1].emphasizedRanges.map {
      (paragraphs[1].text as NSString).substring(with: $0)
    }, ["同主族元素"])
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
    XCTAssertEqual(LearningReadingTypography.plainText(in: "保留``x*y``与``a_b``的原式。"), "保留x*y与a_b的原式。")
    let marked = paragraph.emphasizedRanges.map { (paragraph.text as NSString).substring(with: $0) }
    XCTAssertFalse(marked.contains("Fe"))
    if let ion = paragraph.emphasizedRanges.first(where: { (paragraph.text as NSString).substring(with: $0) == "ion" }) {
      XCTAssertEqual(ion.location, (paragraph.text as NSString).range(of: "ion，").location)
    }
    let fenced = "```text\n  a*b*c\n\n  Fe(OH)3\n```"
    XCTAssertEqual(LearningReadingTypography.plainText(in: fenced), "  a*b*c\n\n  Fe(OH)3")
  }

  func testOrganicIntroductionEmphasizesTheLocalLearningThread() {
    let summary = "本章从碳原子的成键特点入手，介绍有机化合物数量众多、性质各异的原因。碳原子间可形成单键、双键或三键，多个碳原子可结合成碳链或碳环，构成不同的碳骨架。\n\n以甲烷、乙烯、乙炔为代表，学习烷烃、烯烃、炔烃的结构与性质，认识取代反应、加成反应和聚合反应，了解乙烯、乙醇、乙酸等重要有机物及其官能团。\n\n最后介绍糖类、蛋白质、油脂等基本营养物质，以及塑料、橡胶、纤维等有机高分子材料，体现结构决定性质、性质决定用途的观念。"
    let paragraphs = LearningReadingTypography.paragraphs(in: summary,
      keyConcepts: organicKnowledgeAssertions, focusConcepts: ["第七章 有机化合物"])
    XCTAssertEqual(paragraphs.count, 3)
    XCTAssertEqual(markedTerms(paragraphs[0]), ["有机化合物"])
    XCTAssertEqual(markedTerms(paragraphs[1]), ["取代反应"])
    XCTAssertEqual(markedTerms(paragraphs[2]), ["结构决定性质"])
    XCTAssertEqual(paragraphs.map(\.text).joined(separator: "\n\n"), summary)
    assertRestrainedEmphasis(paragraphs)
    let reordered = LearningReadingTypography.paragraphs(in: summary,
      keyConcepts: Array(organicKnowledgeAssertions.reversed()) + organicKnowledgeAssertions,
      focusConcepts: ["第七章 有机化合物"])
    XCTAssertEqual(reordered.map(markedTerms), paragraphs.map(markedTerms), "Metadata order and duplicated objectives do not determine importance")
    for assertion in organicKnowledgeAssertions {
      let reading = LearningReadingTypography.paragraphs(in: assertion, keyConcepts: organicKnowledgeAssertions)
      XCTAssertEqual(reading.map(\.text).joined(), assertion)
      XCTAssertFalse(reading.flatMap(markedTerms).contains("4个电子"))
      assertRestrainedEmphasis(reading)
    }
  }

  func testReadingRejectsTermsInsideLargerTermsEvenWhenExplicitlyMarked() {
    for source in ["有机高分子材料由很多重复单元连接而成，需要结合链结构与材料性质理解。",
      "分子结构与碳原子结构有区别，需要结合具体物质进行判断。",
      "有机高**分子**材料与碳**原子**的成键特点有关，不能把术语截成半词。",
      "多个碳原子结合成碳链或碳环后，还应核对其具体成键方式与价键饱和情况。"] {
      let paragraphs = LearningReadingTypography.paragraphs(in: source, keyConcepts: ["分子", "原子", "原子结合"])
      XCTAssertTrue(paragraphs.flatMap(markedTerms).allSatisfy { !["分子", "原子", "原子结合"].contains($0) })
    }
    let formula = "观察**Fe**(OH)3与Na2CO3·10H2O，完整保留式中的元素、括号与数字，不单独突出半个化学式。"
    let paragraphs = LearningReadingTypography.paragraphs(in: formula, keyConcepts: ["Fe", "OH"])
    XCTAssertFalse(paragraphs.flatMap(markedTerms).contains("Fe"))
    XCTAssertEqual(paragraphs[0].text, formula.replacingOccurrences(of: "**", with: ""))
    let ion = "溶液中的 Na+ 表示钠离子，Na 表示钠元素，两种符号的含义需要结合具体条件加以区别。"
    let ions = LearningReadingTypography.paragraphs(in: ion, keyConcepts: ["Na"], focusConcepts: ["Na"])
    let partial = (ion as NSString).range(of: "Na+")
    XCTAssertFalse(ions[0].emphasizedRanges.contains { NSIntersectionRange($0, partial).length > 0 && $0 != partial })
  }

  func testReadingAllowsACompleteTermWhenItIsActuallyBeingDefined() {
    for (text, expected) in [
      ("甲烷是最简单的烷烃，学习时需要观察它的完整空间结构。", "甲烷"),
      ("分子是保持物质化学性质的最小微粒，需要在具体物质中理解这一概念。", "分子"),
      ("碳原子是组成有机物碳骨架的重要结构单位，需要结合其成键特点进行判断。", "碳原子"),
    ] {
      let paragraphs = LearningReadingTypography.paragraphs(in: text, keyConcepts: ["甲烷", "分子", "原子"])
      XCTAssertEqual(paragraphs.flatMap(markedTerms), [expected])
    }
    let amount = "碳原子最外层有4个电子，分析成键时需要注意完整结构与实际反应条件，不能只看孤立数量。"
    let uncertain = LearningReadingTypography.paragraphs(in: amount, keyConcepts: ["4个电子", "电子", "原子"])
    XCTAssertTrue(uncertain.flatMap(markedTerms).isEmpty)
  }

  func testReadingKeepsNegationAndDirectionInCompleteRelationships() {
    for source in ["并不是性质决定结构，而是结构决定性质。",
      "性质不决定结构，应注意论证中的因果方向与适用条件，避免把结论倒置。",
      "不能据此认为结构决定性质，而应结合给定条件检验这一推断，避免断章取义。",
      "不能得出以下结论：结构决定性质。这里仍需核对当前研究对象及其实际适用条件，避免把猜测当作规律。",
      "只有满足给定条件时才能采用这一判断：性质决定用途。这里还需要检查条件。"] {
      let reading = LearningReadingTypography.paragraphs(in: source)
      let terms = reading.flatMap(markedTerms)
      XCTAssertFalse(terms.contains("并不"))
      XCTAssertFalse(terms.contains("决定结构"))
      XCTAssertFalse(terms.contains("性质决定结构"))
      if source.hasPrefix("不能据此") { XCTAssertFalse(terms.contains("结构决定性质")) }
      if source.contains("以下结论：") { XCTAssertFalse(terms.contains("结构决定性质")) }
      if source.hasPrefix("只有") { XCTAssertFalse(terms.contains("性质决定用途")) }
      XCTAssertEqual(reading.map(\.text).joined(), source)
      assertRestrainedEmphasis(reading)
    }
  }

  private func markedTerms(_ paragraph: LearningReadingTypography.Paragraph) -> [String] {
    paragraph.emphasizedRanges.map { (paragraph.text as NSString).substring(with: $0) }
  }

  private var organicKnowledgeAssertions: [String] { [
    "有机化合物组成元素不复杂，但数量众多、性质各异，研究需基于碳原子成键规律认识分子结构和特征基团。",
    "碳原子间可形成单键、双键或三键，多个碳原子可结合成碳链或碳环，构成链状或环状碳骨架。",
    "甲烷分子中碳原子以最外层4个电子分别与4个氢原子形成4个C—H共价键，5个原子形成正四面体空间结构。",
    "烷烃是碳原子间以单键结合、剩余价键均与氢原子结合的饱和烃，最简单的烷烃是甲烷。",
    "烷烃均为难溶于水的无色物质，熔点、沸点和密度一般随碳原子数增加而升高，常温下状态由气态变为液态再到固态。",
    "甲烷与氯气在光照下发生取代反应，依次生成一氯甲烷、二氯甲烷、三氯甲烷和四氯化碳。",
    "乙烯分子中含有碳碳双键，使乙烯表现出较活泼的化学性质，能发生氧化反应、加成反应和聚合反应。",
    "加成反应是有机物分子中的不饱和碳原子与其他原子或原子团直接结合生成新化合物的反应。",
    "乙醇可以看成是乙烷分子中一个氢原子被羟基取代的产物，羟基是乙醇的官能团。",
    "乙酸的化学性质主要由羧基决定，具有酸性，能与乙醇发生酯化反应生成乙酸乙酯。",
    "官能团对有机物性质具有决定作用，含有相同官能团的有机物在性质上具有相似之处，可据此对有机物分类。",
    "糖类、蛋白质和油脂是人体必需的基本营养物质，也是食品工业的重要原料。",
  ] }

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


@MainActor final class ChapterLearningEntryTests: XCTestCase {
  private func makeStore() async throws -> AppStore {
    let repository = try LocalBookCourseRepository(
      directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
    let store = AppStore(repository: repository)
    var source = try XCTUnwrap(ContentBundle.load([SourceContent].self, name: "sources").first)
    source.summary.book_id = "chapter-entry-book"
    source.summary.content_mode = "local_material"
    source.summary.page_count = 2
    source.chapters = ["first", "second"].enumerated().map { index, id in
      Chapter(chapter_id: id, level: 1, source_title: id, ai_title: id,
        page_start: index + 1, page_end: index + 1, confidence: 1,
        status: "source_available", source: "test")
    }
    source.lessons = []
    source.chunks = [SourceChunk(chunk_id: "original-first", book_id: source.id,
      chapter_id: "first", page_start: 1, page_end: 1, content_type: "text",
      text: "原子由原子核和核外电子构成。", asset_ids: [], key_concepts: [])]
    store.cloudState.privateLocalMode = true
    store.courses = [Course(id: "chapter-course", name: "课程", resourceIDs: [source.id])]
    store.sources = [source]
    store.selectedCourseID = "chapter-course"
    store.selectedBookID = source.id
    try await store.ensureCourseDeletionState(scope: try XCTUnwrap(store.noteScope))
    return store
  }

  func testDownloadedChapterWithoutLessonOpensRealPreparationChoice() async throws {
    let store = try await makeStore()
    store.openChapterLearning(chapterID: "second")
    XCTAssertTrue(store.lessons.isEmpty, "Opening a chapter must not synthesize teaching content")
    XCTAssertEqual(store.selectedChapterID, "second")
    XCTAssertNil(store.selectedLessonID)
    XCTAssertTrue(store.cloudState.showDiagnosis)
    XCTAssertTrue(store.cloudState.choosingLearningMode)
    XCTAssertEqual(store.cloudState.requestedChapterID, "second")
    XCTAssertNotEqual(store.route, .lesson, "A missing lesson must not open an empty reader")
  }

  func testBusyGenerationKeepsItsIdentityAndExplainsAnotherPendingChapter() async throws {
    let store = try await makeStore()
    let session = LocalDiagnosticSession(id: "existing", courseID: "chapter-course",
      bookID: "chapter-entry-book", chapterID: "first", items: [], grades: [], position: 0, results: [:])
    store.cloudState.interview = store.localInterview(session)
    store.cloudState.diagnosisWorkspaceID = "chapter-course"
    store.cloudState.requestedChapterID = "first"
    store.cloudState.isWorking = true
    store.openChapterLearning(chapterID: "second")
    XCTAssertEqual(store.cloudState.requestedChapterID, "first")
    XCTAssertEqual(store.cloudState.interview?.session_id, "existing")
    XCTAssertTrue(store.cloudState.showDiagnosis)
    XCTAssertNotNil(store.cloudState.error)
    XCTAssertFalse(store.cloudState.choosingLearningMode)
    XCTAssertTrue(store.lessons.isEmpty)
  }

  func testReadyChapterRemainsReadableWhileAnotherChapterIsGenerating() async throws {
    let store = try await makeStore()
    var lesson = try XCTUnwrap(ContentBundle.load([SourceContent].self, name: "sources").first?.lessons.first)
    lesson.book_id = "chapter-entry-book"
    lesson.chapter_id = "first"
    lesson.lesson_id = "ready-first"
    lesson.status = "ready"
    store.sources[0].lessons = [lesson]
    store.cloudState.isWorking = true
    store.cloudState.requestedChapterID = "second"
    store.openChapterLearning(chapterID: "first")
    XCTAssertEqual(store.route, .lesson)
    XCTAssertEqual(store.selectedLessonID, "ready-first")
    XCTAssertEqual(store.cloudState.requestedChapterID, "second")
    XCTAssertTrue(store.cloudState.isWorking)
  }

  func testMissingDiagnosticBankRetainsAnExplicitDirectLearningPath() async throws {
    let store = try await makeStore()
    do {
      try await store.beginLocalDiagnosis(chapterID: "first")
      XCTFail("A missing question bank should explain the direct-learning alternative")
    } catch {
      XCTAssertTrue(error.localizedDescription.contains("直接预览课程"))
    }
    XCTAssertTrue(store.cloudState.choosingLearningMode)
    XCTAssertFalse(store.cloudState.isWorking)
    XCTAssertTrue(store.cloudState.localDiagnostics.isEmpty)
    do {
      try await store.beginLocalDiagnosis(chapterID: "first", preview: true)
      XCTFail("This isolated store has no model service")
    } catch {
      XCTAssertTrue(error.localizedDescription.contains("请连接支持本机个人数据的学习服务"))
    }
    XCTAssertEqual(store.cloudState.localDiagnostics.count, 1)
    XCTAssertTrue(store.cloudState.localDiagnostics.values.first?.items.isEmpty == true)
    XCTAssertFalse(store.cloudState.isWorking)
  }

  func testFailedChapterCanReopenPreparationWithoutFakeReadyContent() async throws {
    let store = try await makeStore()
    store.cloudState.requestedChapterID = "second"
    store.cloudState.error = "上次生成未完成"
    store.openChapterLearning(chapterID: "second")
    XCTAssertTrue(store.cloudState.choosingLearningMode)
    XCTAssertNil(store.cloudState.error)
    XCTAssertTrue(store.lessons.isEmpty)
    XCTAssertEqual(store.cloudState.requestedChapterID, "second")
  }

  func testLateGenerationStaysWithItsOwningCourseAndCannotRecreateRemovedCourse() async throws {
    let store = try await makeStore()
    var owned = try XCTUnwrap(store.currentSource)
    owned.summary.title = "课程A内容"
    var other = owned
    other.summary.title = "课程B内容"
    store.courses.append(Course(id: "other-course", name: "另一课程", resourceIDs: [other.id]))
    store.cloudState.localCourseSources["chapter-course"] = [owned]
    store.cloudState.localCourseSources["other-course"] = [other]
    store.selectedCourseID = "other-course"
    store.sources = [other]
    owned.summary.title = "课程A生成结果"
    store.installGeneratedLocalSource(owned, courseID: "chapter-course")
    XCTAssertEqual(store.sources.first?.summary.title, "课程B内容")
    XCTAssertEqual(store.cloudState.localCourseSources["chapter-course"]?.first?.summary.title, "课程A生成结果")
    store.courses.removeAll { $0.id == "chapter-course" }
    store.cloudState.localCourseSources["chapter-course"] = nil
    store.installGeneratedLocalSource(owned, courseID: "chapter-course")
    XCTAssertNil(store.cloudState.localCourseSources["chapter-course"])
    XCTAssertEqual(store.sources.first?.summary.title, "课程B内容")
  }

  func testUnknownGenerationOutcomeRestoresOriginalSessionInsteadOfStartingAnother() async throws {
    let store = try await makeStore()
    let scope = try XCTUnwrap(store.noteScope)
    let session = LocalDiagnosticSession(id: "uncertain-session", courseID: "chapter-course",
      bookID: "chapter-entry-book", chapterID: "first", items: [], grades: [], position: 0, results: [:])
    store.cloudState.localDiagnostics[session.id] = session
    let id = "lecture_" + AgentDigest.sha256(session.id)
    try await store.repository.saveLocalGeneration(LocalGenerationRecord(id: id, courseID: session.courseID,
      bookID: session.bookID, chapterID: session.chapterID, operation: "lecture",
      body: [:], state: "uncertain"), scope: scope)
    do {
      try await store.beginLocalDiagnosis(chapterID: "first", preview: true)
      XCTFail("The isolated test store has no model service")
    } catch {
      XCTAssertTrue(error.localizedDescription.contains("请连接支持本机个人数据的学习服务"))
    }
    XCTAssertEqual(store.cloudState.interview?.session_id, session.id)
    XCTAssertEqual(store.cloudState.localDiagnostics.count, 1)
    let records = try await store.repository.localGenerations(scope: scope)
    XCTAssertEqual(records.count, 1)
    XCTAssertEqual(records.first?.id, id)
    XCTAssertEqual(records.first?.state, "uncertain")
  }

  private func waitForGate(_ gate: ChapterLearningOperationGate) async throws {
    let deadline = ContinuousClock.now + .seconds(5)
    while !gate.started, ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
    XCTAssertTrue(gate.started)
  }

  func testDeletingHeldGenerationAllowsAnotherCourseAndLateDeferCannotReleaseItsBusyState() async throws {
    let store = try await makeStore()
    let scope = try XCTUnwrap(store.noteScope)
    store.courses.append(Course(id: "other-course", name: "另一课程", resourceIDs: []))
    let first = ChapterLearningOperationGate(), second = ChapterLearningOperationGate()
    let old = Task { @MainActor in
      try await store.withLocalCourseLearning(courseID: "chapter-course", scope: scope) {
        await first.wait()
        try Task.checkCancellation()
      }
    }
    try await waitForGate(first)
    XCTAssertTrue(store.cloudState.isWorking)
    try await store.performLocalCourseDeletion(courseID: "chapter-course", scope: scope)
    XCTAssertFalse(store.cloudState.isWorking)
    let next = Task { @MainActor in
      try await store.withLocalCourseLearning(courseID: "other-course", scope: scope) {
        await second.wait()
        try Task.checkCancellation()
      }
    }
    try await waitForGate(second)
    let nextOwner = try XCTUnwrap(store.cloudState.localLearningOwner)
    store.cloudState.error = "另一课程仍在准备"
    first.finish()
    do { try await old.value; XCTFail("A retired generation must cancel") } catch is CancellationError {} catch { XCTFail("\(error)") }
    XCTAssertEqual(store.cloudState.localLearningOwner, nextOwner)
    XCTAssertTrue(store.cloudState.isWorking)
    XCTAssertEqual(store.cloudState.error, "另一课程仍在准备")
    second.finish()
    try await next.value
    XCTAssertFalse(store.cloudState.isWorking)
    XCTAssertNil(store.cloudState.localLearningOwner)
  }

  func testNestedLearningOperationCannotBorrowAnotherCoursesOwner() async throws {
    let store = try await makeStore()
    let scope = try XCTUnwrap(store.noteScope)
    store.courses.append(Course(id: "other-course", name: "另一课程", resourceIDs: []))
    var enteredWrongCourse = false
    do {
      try await store.withLocalCourseLearning(courseID: "chapter-course", scope: scope) {
        try await store.withLocalCourseLearning(courseID: "other-course", scope: scope) {
          enteredWrongCourse = true
        }
      }
      XCTFail("A nested model step must preserve its owning course")
    } catch is CancellationError {} catch { XCTFail("\(error)") }
    XCTAssertFalse(enteredWrongCourse)
    XCTAssertFalse(store.cloudState.isWorking)
  }

  func testOldEpochGenerationCannotReleaseSameCourseSuccessor() async throws {
    let store = try await makeStore()
    let scope = try XCTUnwrap(store.noteScope)
    let first = ChapterLearningOperationGate(), second = ChapterLearningOperationGate()
    let old = Task { @MainActor in
      try await store.withLocalCourseLearning(courseID: "chapter-course", scope: scope) {
        await first.wait()
        try Task.checkCancellation()
      }
    }
    try await waitForGate(first)
    let oldOwner = try XCTUnwrap(store.cloudState.localLearningOwner)
    store.cloudState.epoch = UUID()
    let next = Task { @MainActor in
      try await store.withLocalCourseLearning(courseID: "chapter-course", scope: scope) {
        await second.wait()
        try Task.checkCancellation()
      }
    }
    try await waitForGate(second)
    XCTAssertNotEqual(store.cloudState.localLearningOwner?.token, oldOwner.token)
    let nextOwner = store.cloudState.localLearningOwner
    first.finish()
    do { try await old.value; XCTFail("An old account epoch must cancel") } catch is CancellationError {} catch { XCTFail("\(error)") }
    XCTAssertEqual(store.cloudState.localLearningOwner, nextOwner)
    XCTAssertTrue(store.cloudState.isWorking)
    second.finish()
    try await next.value
    XCTAssertFalse(store.cloudState.isWorking)
  }

  func testDefiniteGenerationFailureRetriesUnderNewRequestIDOnlyWhenStartedAgain() async throws {
    let store = try await makeStore()
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [ChapterGenerationRetryProtocol.self]
    let session = URLSession(configuration: configuration)
    defer { session.invalidateAndCancel() }
    ChapterGenerationRetryProtocol.reset()
    store.cloud = CloudAPI(baseURL: URL(string: "https://chapter-retry.invalid")!, session: session)
    store.cloudState.authenticated = true
    store.cloudState.privateLocalMode = true
    store.cloudState.cacheKey = "chapter-retry-scope"
    let scope = try XCTUnwrap(store.noteScope)
    try await store.ensureCourseDeletionState(scope: scope)
    do {
      _ = try await store.performPrivateGeneration(id: "retry-job", operation: "lecture",
        courseID: "chapter-course", bookID: "chapter-entry-book", chapterID: "first", messages: [], schema: [:])
      XCTFail("The first attempt reports a definite failure")
    } catch {
      XCTAssertTrue(error.localizedDescription.contains("private_model_output_limit"))
    }
    var records = try await store.repository.localGenerations(scope: scope)
    XCTAssertEqual(records.first?.state, "failed")
    XCTAssertEqual(ChapterGenerationRetryProtocol.posted(), ["retry-job"])
    let retried = try await store.performPrivateGeneration(id: "retry-job", operation: "lecture",
      courseID: "chapter-course", bookID: "chapter-entry-book", chapterID: "first", messages: [], schema: [:])
    XCTAssertEqual(retried, "retried result")
    XCTAssertEqual(ChapterGenerationRetryProtocol.posted(), ["retry-job", "retry-job_a1"])
    records = try await store.repository.localGenerations(scope: scope)
    XCTAssertEqual(records.count, 1)
    XCTAssertEqual(records.first?.state, "completed")
    XCTAssertEqual(records.first?.body["max_tokens"], .number(32768))
  }

  private func contractStore(mode: String) async throws -> (AppStore, URLSession) {
    let store = try await makeStore()
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [LocalCourseContractProtocol.self]
    let session = URLSession(configuration: configuration)
    LocalCourseContractProtocol.reset(mode: mode)
    store.cloud = CloudAPI(baseURL: URL(string: "https://course-contract.invalid")!, session: session)
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = "contract-scope"
    try await store.ensureCourseDeletionState(scope: try XCTUnwrap(store.noteScope))
    store.cloudState.localCourseSources["chapter-course"] = store.sources
    store.cloudState.localDiagnostics["contract-session"] = LocalDiagnosticSession(id: "contract-session",
      courseID: "chapter-course", bookID: "chapter-entry-book", chapterID: "first", items: [], grades: [], position: 0, results: [:])
    return (store, session)
  }

  func testTypedLectureContractGeneratesContentAndPlanWithoutServerJob() async throws {
    let (store, session) = try await contractStore(mode: "valid")
    defer { session.invalidateAndCancel() }
    try await store.generateLocalPersonalChapter(sessionID: "contract-session")
    XCTAssertTrue(store.hasStudyContent)
    XCTAssertFalse(store.sources.first!.plan.tasks.isEmpty)
    XCTAssertEqual(LocalCourseContractProtocol.operations(), ["lecture", "plan"])
    XCTAssertTrue(store.courseCreationSteps[3].completed)
    XCTAssertTrue(store.courseCreationSteps[4].completed)
    XCTAssertEqual(store.courseCreationSteps[3].title, "准备学习内容")
    let schema = try XCTUnwrap(LocalCourseContractProtocol.lectureSchema())
    let root = try XCTUnwrap(schema["properties"] as? [String: Any])
    let course = try XCTUnwrap(root["course"] as? [String: Any])
    let properties = try XCTUnwrap(course["properties"] as? [String: Any])
    for key in ["original_reading", "worked_examples"] {
      let list = try XCTUnwrap(properties[key] as? [String: Any])
      let item = try XCTUnwrap(list["items"] as? [String: Any])
      XCTAssertEqual(Set(try XCTUnwrap(item["required"] as? [String])), Set(["title", "content", "purpose", "citations"]))
    }
  }

  func testDisabledQualityReviewAcceptsUsableCourseWithoutExactQuoteMatch() async throws {
    let (store, session) = try await contractStore(mode: "nonexact-quote")
    defer { session.invalidateAndCancel() }
    XCTAssertFalse(LocalLectureSchema.qualityReviewEnabled(environment: [:]))
    XCTAssertTrue(LocalLectureSchema.qualityReviewEnabled(environment: ["BOOKCOURSE_COURSE_QUALITY_REVIEW": "1"]))
    try await store.generateLocalPersonalChapter(sessionID: "contract-session")
    XCTAssertTrue(store.hasStudyContent)
    XCTAssertFalse(store.sources.first!.plan.tasks.isEmpty)
    XCTAssertEqual(LocalCourseContractProtocol.operations(), ["lecture", "plan"])
  }

  func testCitationOnlyLectureFailsBeforePlanAndKeepsDurableFailure() async throws {
    let (store, session) = try await contractStore(mode: "citation-only")
    defer { session.invalidateAndCancel() }
    do {
      try await store.generateLocalPersonalChapter(sessionID: "contract-session")
      XCTFail("Citation objects cannot stand in for teaching sections")
    } catch { XCTAssertTrue(error.localizedDescription.contains("课程结构")) }
    XCTAssertEqual(LocalCourseContractProtocol.operations(), ["lecture"])
    XCTAssertFalse(store.hasStudyContent)
    XCTAssertEqual(store.courseCreationSteps[3].detail, "生成失败，可重试")
    XCTAssertEqual(store.courseCreationSteps[4].detail, "等待学习内容")
    store.cloudState.localGenerationStatuses = [:]
    try await store.restoreLocalGenerationStatuses(scope: try XCTUnwrap(store.noteScope))
    XCTAssertEqual(store.courseCreationSteps[3].detail, "生成失败，可重试")
    XCTAssertFalse(store.courseCreationSteps[3].active)
  }

  func testFailedPlanRetainsContentAndRetriesOnlyPlan() async throws {
    let (store, session) = try await contractStore(mode: "invalid-plan-first")
    defer { session.invalidateAndCancel() }
    do {
      try await store.generateLocalPersonalChapter(sessionID: "contract-session")
      XCTFail("An invalid plan must remain retryable")
    } catch { XCTAssertTrue(error.localizedDescription.contains("学习计划")) }
    var draft = CourseDraft()
    draft.name = "课程"; draft.resourceIDs = ["chapter-entry-book"]; draft.importCourseID = "chapter-course"
    store.draft = draft
    let recordID = try XCTUnwrap(store.registerCourseImport())
    let index = try XCTUnwrap(store.importState.courses.firstIndex { $0.id == recordID })
    store.importState.courses[index].setupCompleted = true
    store.importState.courses[index].job.status = .completed
    XCTAssertTrue(store.hasStudyContent)
    XCTAssertEqual(store.courseCreationSteps[4].detail, "生成失败，可重试")
    XCTAssertEqual(store.studyCoursePreparation, .generation)
    XCTAssertTrue(store.coursePreparationRequiresAction(.generation))
    try await store.generateLocalPersonalChapter(sessionID: "contract-session")
    XCTAssertEqual(LocalCourseContractProtocol.operations(), ["lecture", "plan", "plan"])
    XCTAssertTrue(LocalCourseContractProtocol.requestIDs().last!.hasSuffix("_a1"))
    XCTAssertTrue(store.courseCreationSteps[4].completed)
    XCTAssertNil(store.studyCoursePreparation)
  }

  func testRejectedPrivateRequestIsFailedAndRetriesOnlyOnNextAction() async throws {
    let (store, session) = try await contractStore(mode: "request-rejected-first")
    defer { session.invalidateAndCancel() }
    do {
      _ = try await store.performPrivateGeneration(id: "rejected", operation: "lecture", courseID: "chapter-course",
        bookID: "chapter-entry-book", chapterID: "first", messages: [], schema: [:])
      XCTFail("The schema rejection must surface")
    } catch { XCTAssertEqual((error as? CloudError)?.status, 422) }
    let scope = try XCTUnwrap(store.noteScope)
    let records = try await store.repository.localGenerations(scope: scope)
    XCTAssertEqual(records.first?.state, "failed")
    XCTAssertEqual(LocalCourseContractProtocol.requestIDs(), ["rejected"])
    _ = try await store.performPrivateGeneration(id: "rejected", operation: "lecture", courseID: "chapter-course",
      bookID: "chapter-entry-book", chapterID: "first", messages: [], schema: [:])
    XCTAssertEqual(LocalCourseContractProtocol.requestIDs(), ["rejected", "rejected_a1"])
  }

  func testMissingClaimRequiresAnotherActionAndReusesSameRequestID() async throws {
    let (store, session) = try await contractStore(mode: "not-started-once")
    defer { session.invalidateAndCancel() }
    let scope = try XCTUnwrap(store.noteScope)
    let record = LocalGenerationRecord(id: "missing-claim", courseID: "chapter-course", bookID: "chapter-entry-book",
      chapterID: "first", operation: "lecture", body: ["request_id": .string("missing-claim"), "operation": .string("lecture"),
        "messages": .array([]), "response_schema": .object([:]), "max_tokens": .number(32768)], state: "uncertain", attempt: nil)
    try await store.repository.saveLocalGeneration(record, scope: scope)
    do {
      _ = try await store.performPrivateGeneration(id: "missing-claim", operation: "lecture", courseID: "chapter-course",
        bookID: "chapter-entry-book", chapterID: "first", messages: [], schema: [:])
      XCTFail("Querying must not silently submit")
    } catch { XCTAssertTrue(error.localizedDescription.contains("尚未接收")) }
    XCTAssertTrue(LocalCourseContractProtocol.operations().isEmpty)
    _ = try await store.performPrivateGeneration(id: "missing-claim", operation: "lecture", courseID: "chapter-course",
      bookID: "chapter-entry-book", chapterID: "first", messages: [], schema: [:])
    XCTAssertEqual(LocalCourseContractProtocol.requestIDs(), ["missing-claim"])
  }

  func testUnstartedAndUncertainGenerationDoNotPretendToRun() async throws {
    let store = try await makeStore()
    XCTAssertEqual(store.courseCreationSteps[3].detail, "需要继续")
    let record = LocalGenerationRecord(id: "held", courseID: "chapter-course", bookID: "chapter-entry-book",
      chapterID: "first", operation: "lecture", body: [:], state: "uncertain", attempt: nil)
    store.cloudState.localGenerationStatuses[record.statusKey] = LocalGenerationStatus(record)
    XCTAssertEqual(store.courseCreationSteps[3].detail, "结果待确认")
    XCTAssertFalse(store.courseCreationSteps[3].active)
  }

  func testPersistedModelResultSurvivesAcknowledgementFailureWithoutAnotherModelPost() async throws {
    let store = try await makeStore()
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [ChapterGenerationAckLossProtocol.self]
    let session = URLSession(configuration: configuration)
    defer { session.invalidateAndCancel() }
    ChapterGenerationAckLossProtocol.reset()
    store.cloud = CloudAPI(baseURL: URL(string: "https://chapter-generation.invalid")!, session: session)
    store.cloudState.authenticated = true
    store.cloudState.cacheKey = "chapter-ack-scope"
    try await store.ensureCourseDeletionState(scope: try XCTUnwrap(store.noteScope))
    let first = try await store.performPrivateGeneration(id: "ack-loss-job", operation: "lecture",
      courseID: "chapter-course", bookID: "chapter-entry-book", chapterID: "first", messages: [], schema: [:])
    let replay = try await store.performPrivateGeneration(id: "ack-loss-job", operation: "lecture",
      courseID: "chapter-course", bookID: "chapter-entry-book", chapterID: "first", messages: [], schema: [:])
    XCTAssertEqual(first, "durable result")
    XCTAssertEqual(replay, first)
    let counts = ChapterGenerationAckLossProtocol.counts()
    XCTAssertEqual(counts.modelPosts, 1)
    XCTAssertGreaterThanOrEqual(counts.acknowledgements, 1)
    let scope = try XCTUnwrap(store.noteScope)
    let records = try await store.repository.localGenerations(scope: scope)
    XCTAssertEqual(records.first?.state, "completed")
    XCTAssertEqual(records.first?.content, "durable result")
  }
}


private final class ChapterGenerationRetryProtocol: URLProtocol, @unchecked Sendable {
  private static let lock = NSLock()
  nonisolated(unsafe) private static var requestIDs: [String] = []

  static func reset() { lock.lock(); requestIDs = []; lock.unlock() }
  static func posted() -> [String] { lock.lock(); defer { lock.unlock() }; return requestIDs }
  override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "chapter-retry.invalid" }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    guard let url = request.url else { return }
    let state: [String: Any]
    if url.path.hasSuffix("/ack") { state = [:] }
    else {
      var data = request.httpBody ?? Data()
      if data.isEmpty, let stream = request.httpBodyStream {
        stream.open()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while case let count = stream.read(&buffer, maxLength: buffer.count), count > 0 { data.append(contentsOf: buffer.prefix(count)) }
        stream.close()
      }
      let id = ((try? JSONSerialization.jsonObject(with: data)) as? [String: Any])?["request_id"] as? String ?? ""
      Self.lock.lock()
      Self.requestIDs.append(id)
      let first = Self.requestIDs.count == 1
      Self.lock.unlock()
      state = first ? ["request_id": id, "state": "failed", "content": NSNull(), "error": "private_model_output_limit"]
        : ["request_id": id, "state": "completed", "content": "retried result"]
    }
    let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
    client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: (try? JSONSerialization.data(withJSONObject: state)) ?? Data())
    client?.urlProtocolDidFinishLoading(self)
  }
  override func stopLoading() {}
}


private final class ChapterGenerationAckLossProtocol: URLProtocol, @unchecked Sendable {
  private static let lock = NSLock()
  nonisolated(unsafe) private static var modelPosts = 0
  nonisolated(unsafe) private static var acknowledgements = 0

  static func reset() {
    lock.lock()
    defer { lock.unlock() }
    modelPosts = 0
    acknowledgements = 0
  }
  static func counts() -> (modelPosts: Int, acknowledgements: Int) {
    lock.lock()
    defer { lock.unlock() }
    return (modelPosts, acknowledgements)
  }
  override class func canInit(with request: URLRequest) -> Bool {
    request.url?.host == "chapter-generation.invalid"
  }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    guard let url = request.url else { return }
    if url.path.hasSuffix("/ack") {
      Self.lock.lock()
      Self.acknowledgements += 1
      Self.lock.unlock()
      client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
      return
    }
    guard request.httpMethod == "POST", url.path == "/api/assistant/private-model" else {
      client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
      return
    }
    Self.lock.lock()
    Self.modelPosts += 1
    Self.lock.unlock()
    do {
      let data = try JSONSerialization.data(withJSONObject: [
        "request_id": "ack-loss-job", "state": "completed", "content": "durable result",
      ])
      let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil,
        headerFields: ["Content-Type": "application/json"])!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: data)
      client?.urlProtocolDidFinishLoading(self)
    } catch {
      client?.urlProtocol(self, didFailWithError: error)
    }
  }
  override func stopLoading() {}
}


@MainActor private final class ChapterLearningOperationGate {
  private var continuation: CheckedContinuation<Void, Never>?
  private(set) var started = false
  func wait() async {
    started = true
    await withCheckedContinuation { continuation = $0 }
  }
  func finish() {
    continuation?.resume()
    continuation = nil
  }
}

private final class LocalCourseContractProtocol: URLProtocol, @unchecked Sendable {
  private static let lock = NSLock()
  nonisolated(unsafe) private static var mode = "valid"
  nonisolated(unsafe) private static var observed: [String] = []
  nonisolated(unsafe) private static var ids: [String] = []
  nonisolated(unsafe) private static var schema: [String: Any]?
  static func reset(mode value: String) { lock.lock(); defer { lock.unlock() }; mode = value; observed = []; ids = []; schema = nil }
  static func operations() -> [String] { lock.lock(); defer { lock.unlock() }; return observed }
  static func requestIDs() -> [String] { lock.lock(); defer { lock.unlock() }; return ids }
  static func lectureSchema() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return schema }
  override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "course-contract.invalid" }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    do {
      var result: [String: Any] = ["ok": true]
      var statusCode = 200
      if request.httpMethod == "POST" {
        let values = try JSONDecoder().decode([String: JSONValue].self, from: routingRequestBodyForContract(request))
        let operation = values["operation"]?.stringValue ?? ""
        let requestID = values["request_id"]?.stringValue ?? ""
        let declaredSchema: [String: Any]?
        if operation == "lecture", case .object(let declared)? = values["response_schema"] {
          declaredSchema = try JSONSerialization.jsonObject(with: JSONEncoder().encode(declared)) as? [String: Any]
        } else { declaredSchema = nil }
        Self.lock.lock()
        Self.observed.append(operation); Self.ids.append(requestID)
        let selectedMode = Self.mode
        let planNumber = Self.observed.filter { $0 == "plan" }.count
        let requestNumber = Self.observed.count
        if let declaredSchema { Self.schema = declaredSchema }
        Self.lock.unlock()
        let citation: [String: Any] = ["page_number": 1,
          "quote": selectedMode == "nonexact-quote" ? "原子由原子核和核外电子组成。" : "原子由原子核和核外电子构成。"]
        let section: [String: Any] = ["title": "原子结构", "content": "原子由原子核和核外电子构成。", "purpose": "explanation", "kind": "explanation", "point_id": "p1", "citations": [citation]]
        let courseID = "local-lecture:contract-session"
        let output: [String: Any]
        if operation == "lecture" {
          var course: [String: Any] = ["course_id": courseID, "version": 1, "chapter_id": "first", "chapter_title": "原子结构",
            "opening": "认识原子", "summary": "原子的组成", "original_reading": [section],
            "knowledge_points": [["point_id": "p1", "title": "原子结构", "explanation": "原子由原子核和核外电子构成。", "mastery": 0, "citations": [citation]]],
            "flashcards": [], "worked_examples": [], "checkpoint_questions": [], "practice_items": [],
            "estimated_minutes": 10, "created_at": "2026-11-06T00:00:00Z", "unresolved_source_warnings": [],
            "units": [["unit_id": "u1", "title": "原子结构", "knowledge_point_ids": ["p1"], "sections": [section], "estimated_minutes": 10]]]
          if selectedMode == "citation-only" { course["original_reading"] = [citation] }
          output = ["course": course, "practice_grading": []]
        } else if selectedMode == "invalid-plan-first" && planNumber == 1 {
          output = [:]
        } else {
          output = ["user_id": "test-user", "book_id": "chapter-entry-book", "days": 1, "daily_minutes": 10,
            "tasks": [["task_id": "t1", "user_id": "test-user", "day": 1, "title": "学习原子结构", "task_type": "learn", "minutes": 10,
              "chapter_id": "first", "lesson_id": courseID, "status": "pending", "weak_points": []]]]
        }
        let content = String(decoding: try JSONSerialization.data(withJSONObject: output), as: UTF8.self)
        if selectedMode == "request-rejected-first" && requestNumber == 1 {
          statusCode = 422; result = ["detail": "个人生成请求或输出格式无效"]
        } else { result = ["request_id": requestID, "state": "completed", "content": content, "error": ""] }
      } else if request.httpMethod == "GET" {
        Self.lock.lock(); let selectedMode = Self.mode; Self.lock.unlock()
        if selectedMode == "not-started-once" {
          result = ["request_id": request.url!.lastPathComponent, "state": "not_started", "content": NSNull()]
        }
      }
      let response = HTTPURLResponse(url: request.url!, statusCode: statusCode, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: try JSONSerialization.data(withJSONObject: result))
      client?.urlProtocolDidFinishLoading(self)
    } catch { client?.urlProtocol(self, didFailWithError: error) }
  }
  override func stopLoading() {}
}

private func routingRequestBodyForContract(_ request: URLRequest) throws -> Data {
  if let data = request.httpBody { return data }
  guard let stream = request.httpBodyStream else { return Data() }
  stream.open(); defer { stream.close() }
  var data = Data(), buffer = [UInt8](repeating: 0, count: 4096)
  while true {
    let count = stream.read(&buffer, maxLength: buffer.count)
    guard count >= 0 else { throw URLError(.cannotDecodeRawData) }
    if count == 0 { return data }
    data.append(contentsOf: buffer.prefix(count))
  }
}
