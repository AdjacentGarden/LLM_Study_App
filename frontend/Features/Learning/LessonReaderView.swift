import AVKit
import CryptoKit
import NaturalLanguage
import SwiftUI
import UIKit

/// WebKit's `text-wrap: pretty` keeps the minimum line count and balances the
/// remaining space. Plain SwiftUI Text uses greedy breaks, including splitting
/// Chinese words and leaving a short last line. Keep this native and width based.
enum LearningPrettyLineBreaker {
  static func lines(in text: String, width: CGFloat, advance: (Character) -> CGFloat) -> [String] {
    guard !text.isEmpty, width > 0 else { return [text] }
    let characters = Array(text)
    if characters.contains("\n") {
      return lines(
        in: text.replacingOccurrences(of: "\n", with: " "), width: width, advance: advance)
    }
    var prefix: [CGFloat] = [0]
    for character in characters { prefix.append(prefix.last! + advance(character)) }
    let opening = Set("（([“「『《〈【〔〖〘〚")
    let closing = Set("，。；：！？、）)]”」』》〉】〕〗〙〛")
    func word(_ character: Character) -> Bool {
      character.unicodeScalars.allSatisfy {
        (48...57).contains($0.value) || (65...90).contains($0.value)
          || (97...122).contains($0.value) || $0.value == 0x2236
      }
    }
    var candidates = [0]
    for index in 1...characters.count {
      if index == characters.count
        || (!closing.contains(characters[index]) && !opening.contains(characters[index - 1])
          && !(word(characters[index]) && word(characters[index - 1])))
      {
        candidates.append(index)
      }
    }
    // An unusually long unbroken token still has to fit, as with overflow-wrap:anywhere.
    for pair in zip(candidates, candidates.dropFirst())
    where prefix[pair.1] - prefix[pair.0] > width {
      candidates.append(contentsOf: (pair.0 + 1)..<pair.1)
    }
    candidates = Array(Set(candidates)).sorted()
    let count = candidates.count
    var greedy: [Int] = [0]
    var start = 0
    while start < count - 1 {
      var end = start + 1
      while end + 1 < count
        && prefix[candidates[end + 1]] - prefix[candidates[start]] <= width + 0.01
      { end += 1 }
      greedy.append(end)
      start = end
    }
    let lineCount = greedy.count - 1
    func strings(_ boundaries: [Int]) -> [String] {
      zip(boundaries, boundaries.dropFirst()).map {
        String(characters[candidates[$0.0]..<candidates[$0.1]])
      }
    }
    guard lineCount > 1, lineCount <= 64, characters.count <= 1600 else { return strings(greedy) }
    var costs = Array(
      repeating: Array(repeating: Double.infinity, count: count), count: lineCount + 1)
    var next = Array(repeating: Array(repeating: -1, count: count), count: lineCount + 1)
    costs[0][count - 1] = 0
    for remaining in 1...lineCount {
      for begin in stride(from: count - 2, through: 0, by: -1) {
        for end in (begin + 1)..<count {
          let used = prefix[candidates[end]] - prefix[candidates[begin]]
          if used > width + 0.01 { break }
          guard costs[remaining - 1][end].isFinite else { continue }
          let slack = Double(width - used)
          let cost = costs[remaining - 1][end] + slack * slack
          if cost < costs[remaining][begin] {
            costs[remaining][begin] = cost
            next[remaining][begin] = end
          }
        }
      }
    }
    guard costs[lineCount][0].isFinite else { return strings(greedy) }
    var boundaries = [0]
    var current = 0
    for remaining in stride(from: lineCount, through: 1, by: -1) {
      let end = next[remaining][current]
      guard end > current else { return strings(greedy) }
      boundaries.append(end)
      current = end
    }
    return strings(boundaries)
  }
}

/// Presentation-only formatting also applies to lessons saved by older clients.
/// The stored lesson, source quotes and citation bytes remain unchanged.
enum LearningReadingTypography {
  enum Kind { case body, heading, quote, code }
  struct Link {
    var range: NSRange
    var url: URL
  }
  struct Paragraph {
    var text: String
    var kind: Kind = .body
    var emphasizedRanges: [NSRange] = []
    var italicRanges: [NSRange] = []
    var codeRanges: [NSRange] = []
    var links: [Link] = []
  }
  private struct EmphasisCandidate {
    var range: NSRange
    var priority: Int
  }

  static func paragraphs(in source: String, keyConcepts: [String] = []) -> [Paragraph] {
    let normalized = source.replacingOccurrences(of: "\r\n", with: "\n")
      .replacingOccurrences(of: "\r", with: "\n")
    var result: [Paragraph] = []
    var isCode = false
    let phrases = keyPhrases(from: keyConcepts)
    for line in normalized.components(separatedBy: "\n") {
      let trimmed = line.trimmingCharacters(in: .whitespaces)
      if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") { isCode.toggle(); continue }
      if isCode {
        result.append(Paragraph(text: line, kind: .code))
        continue
      }
      guard !trimmed.isEmpty else { continue }
      var text = trimmed
      var kind = Kind.body
      if let heading = text.range(of: #"^#{1,6}\s+"#, options: .regularExpression) {
        text.removeSubrange(heading)
        kind = .heading
      } else if text.hasPrefix("> ") {
        text.removeFirst(2)
        kind = .quote
      } else if let bullet = text.range(of: #"^[-+*]\s+"#, options: .regularExpression) {
        text.replaceSubrange(bullet, with: "• ")
      }
      let parsed = inlineMarkdown(text)
      let pieces = kind == .heading ? [parsed.text] : splitQuestionItems(parsed.text).flatMap(splitLongParagraph)
      let displayed = parsed.text as NSString
      var offset = 0
      for piece in pieces {
        let range = displayed.range(of: piece, range: NSRange(location: offset, length: displayed.length - offset))
        guard range.location != NSNotFound else { continue }
        var paragraph = slice(parsed, range: range)
        paragraph.kind = kind
        if kind != .heading {
          paragraph.emphasizedRanges = emphasis(in: paragraph, keyConcepts: phrases)
        }
        result.append(paragraph)
        offset = NSMaxRange(range)
      }
    }
    return result
  }

  static func plainText(in source: String, keyConcepts: [String] = []) -> String {
    paragraphs(in: source, keyConcepts: keyConcepts).map(\.text).joined(separator: "\n")
  }

  static func attributedText(in source: String, keyConcepts: [String] = []) -> NSAttributedString {
    let result = NSMutableAttributedString(string: "")
    for paragraph in paragraphs(in: source, keyConcepts: keyConcepts) {
      if result.length > 0 { result.append(NSAttributedString(string: "\n")) }
      let style = NSMutableParagraphStyle()
      style.minimumLineHeight = DS.readingLineHeight
      style.lineSpacing = 3
      style.paragraphSpacing = 13
      style.lineBreakMode = .byWordWrapping
      style.lineBreakStrategy = .pushOut
      if paragraph.kind == .quote {
        style.firstLineHeadIndent = 12
        style.headIndent = 12
      }
      let font = paragraph.kind == .code
        ? UIFont.monospacedSystemFont(ofSize: DS.uiFont(.reading).pointSize - 1, weight: .regular)
        : DS.uiFont(.reading, weight: paragraph.kind == .heading ? .semibold : .regular)
      let value = NSMutableAttributedString(string: paragraph.text, attributes: [
        .font: font, .foregroundColor: UIColor(DS.secondary), .paragraphStyle: style,
      ])
      for range in paragraph.italicRanges {
        if let descriptor = font.fontDescriptor.withSymbolicTraits(.traitItalic) {
          value.addAttribute(.font, value: UIFont(descriptor: descriptor, size: font.pointSize), range: range)
        }
      }
      for range in paragraph.codeRanges {
        value.addAttribute(.font, value: UIFont.monospacedSystemFont(ofSize: font.pointSize - 1, weight: .regular), range: range)
      }
      for range in paragraph.emphasizedRanges {
        value.addAttribute(.font, value: DS.uiFont(.reading, weight: .semibold), range: range)
        value.addAttribute(.foregroundColor, value: UIColor(DS.text), range: range)
      }
      for link in paragraph.links { value.addAttribute(.link, value: link.url, range: link.range) }
      result.append(value)
    }
    return result
  }

  private static func inlineMarkdown(_ text: String) -> Paragraph {
    // Older lessons use literal multiplication/subscript tokens as well as
    // Markdown. Intraword operators are formula characters, not emphasis marks.
    let characters = Array(text)
    var markdown = ""
    var isInlineCode = false
    func formulaCharacter(_ value: Character) -> Bool {
      let mathematical = value.unicodeScalars.allSatisfy { $0.isASCII || (0x0370...0x03FF).contains($0.value) || (0x1F00...0x1FFF).contains($0.value) || (0x1D400...0x1D7FF).contains($0.value) }
      return mathematical && (value.isLetter || value.isNumber || "()[]".contains(value))
    }
    for (index, character) in characters.enumerated() {
      if character == "`" { isInlineCode.toggle() }
      if !isInlineCode, "*_".contains(character), index > 0, index + 1 < characters.count,
        formulaCharacter(characters[index - 1]), formulaCharacter(characters[index + 1]) {
        markdown.append("\\")
      }
      markdown.append(character)
    }
    guard let parsed = try? AttributedString(markdown: markdown,
      options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) else {
      return Paragraph(text: text)
    }
    var paragraph = Paragraph(text: String(parsed.characters))
    var offset = 0
    for run in parsed.runs {
      let content = String(parsed[run.range].characters)
      let range = NSRange(location: offset, length: content.utf16.count)
      if let intent = run.inlinePresentationIntent {
        if intent.contains(.stronglyEmphasized) { paragraph.emphasizedRanges.append(range) }
        if intent.contains(.emphasized) { paragraph.italicRanges.append(range) }
        if intent.contains(.code) { paragraph.codeRanges.append(range) }
      }
      if let url = run.link { paragraph.links.append(Link(range: range, url: url)) }
      offset += range.length
    }
    return paragraph
  }

  private static func slice(_ paragraph: Paragraph, range: NSRange) -> Paragraph {
    func local(_ original: NSRange) -> NSRange? {
      let overlap = NSIntersectionRange(original, range)
      guard overlap.length > 0 else { return nil }
      return NSRange(location: overlap.location - range.location, length: overlap.length)
    }
    return Paragraph(text: (paragraph.text as NSString).substring(with: range), kind: paragraph.kind,
      emphasizedRanges: paragraph.emphasizedRanges.compactMap(local),
      italicRanges: paragraph.italicRanges.compactMap(local), codeRanges: paragraph.codeRanges.compactMap(local),
      links: paragraph.links.compactMap { link in local(link.range).map { Link(range: $0, url: link.url) } })
  }

  private static func keyPhrases(from concepts: [String]) -> [String] {
    let generic: Set<String> = ["本节", "概念", "练习", "例题", "解答", "知识点", "核心概念", "重点", "分析", "答案",
      "性质", "物质", "内容", "过程", "情况", "结果", "方法", "作用", "反应", "具有", "形成", "化学", "不同", "相同"]
    var result: [String] = []
    var seen = Set<String>()
    func append(_ value: String) {
      let phrase = value.trimmingCharacters(in: .whitespacesAndNewlines)
      guard (2...16).contains(phrase.count), !generic.contains(phrase),
        seen.insert(phrase.lowercased()).inserted else { return }
      result.append(phrase)
    }
    // Already-curated short concepts outrank terms inferred from old summaries.
    for concept in concepts where concept.count <= 16 { append(concept) }
    for concept in concepts where concept.count > 16 {
      if let regex = try? NSRegularExpression(pattern:
        #"^([\p{Han}A-Za-z0-9·-]{2,12}?)(?:是|具有|位于|属于|通常|能够|可以)"#),
        let match = regex.firstMatch(in: concept, range: NSRange(location: 0, length: (concept as NSString).length)) {
        append((concept as NSString).substring(with: match.range(at: 1)))
      }
      let tagger = NLTagger(tagSchemes: [.lexicalClass])
      tagger.string = concept
      var nouns: [String] = []
      var previousEnd: String.Index?
      var phrase = ""
      tagger.enumerateTags(in: concept.startIndex..<concept.endIndex, unit: .word, scheme: .lexicalClass,
        options: [.omitWhitespace, .omitPunctuation, .joinNames]) { tag, range in
        let word = String(concept[range])
        if tag == .noun {
          if previousEnd == range.lowerBound, !phrase.isEmpty, (phrase + word).count <= 12 {
            phrase += word
            nouns.append(phrase)
          } else { phrase = word }
          nouns.append(word)
          previousEnd = range.upperBound
        } else if word == "最" {
          phrase = word
          previousEnd = range.upperBound
        } else {
          if tag == .otherWord, word.count >= 3, word.unicodeScalars.contains(where: { (0x4E00...0x9FFF).contains($0.value) }) {
            nouns.append(word)
          }
          phrase = ""
          previousEnd = nil
        }
        return true
      }
      // Longer noun phrases tend to identify the concept better than its parts.
      for noun in nouns.sorted(by: { $0.count > $1.count }) { append(noun) }
      if result.count >= 36 { break }
    }
    return Array(result.prefix(48))
  }

  private static func splitQuestionItems(_ text: String) -> [String] {
    let pattern = #"(?<![A-Za-z0-9_])(?:[（(][1-9][0-9]?[）)]|[1-9][0-9]?[、.]\s+)"#
    guard let regex = try? NSRegularExpression(pattern: pattern) else { return [text] }
    let source = text as NSString
    let matches = regex.matches(in: text, range: NSRange(location: 0, length: source.length))
    var starts: [Int] = [0]
    for match in matches {
      let prefix = source.substring(to: match.range.location).trimmingCharacters(in: .whitespaces)
      let suffix = source.substring(from: NSMaxRange(match.range)).trimmingCharacters(in: .whitespaces)
      guard !suffix.isEmpty, !"与和及或、，。；）)".contains(suffix.first!) else { continue }
      let atBoundary = prefix.isEmpty || prefix.last.map { "：:；;。！？!?，,".contains($0) } == true
      let asksQuestion = suffix.range(of: #"^(?:请|指出|说明|写出|计算|判断|分析|比较|选择|填|求|证明|解释|回答|简述|完成|为什么|如何)"#,
        options: .regularExpression) != nil
      guard atBoundary || asksQuestion else { continue }
      if match.range.location > 0 { starts.append(match.range.location) }
    }
    guard starts.count > 1 else { return [text] }
    starts.append(source.length)
    return zip(starts, starts.dropFirst()).map {
      source.substring(with: NSRange(location: $0.0, length: $0.1 - $0.0))
        .trimmingCharacters(in: .whitespaces)
    }.filter { !$0.isEmpty }
  }

  private static func splitLongParagraph(_ text: String) -> [String] {
    guard text.count > 180 else { return [text] }
    var pieces: [String] = []
    var start = text.startIndex
    var count = 0
    var index = start
    while index < text.endIndex {
      count += 1
      let character = text[index]
      let next = text.index(after: index)
      if count >= 90, "。！？!?；".contains(character), text.distance(from: next, to: text.endIndex) >= 40 {
        // A closing quote belongs to its sentence, so wait for a later boundary.
        if next == text.endIndex || !"”’」』）)".contains(text[next]) {
          pieces.append(String(text[start..<next]).trimmingCharacters(in: .whitespaces))
          start = next
          count = 0
        }
      }
      index = next
    }
    pieces.append(String(text[start...]).trimmingCharacters(in: .whitespaces))
    return pieces.filter { !$0.isEmpty }
  }

  private static func emphasis(in paragraph: Paragraph, keyConcepts: [String]) -> [NSRange] {
    let text = paragraph.text as NSString
    let count = paragraph.text.filter { !$0.isWhitespace }.count
    let target = min(36, Int(Double(count) * 0.15))
    let maximum = min(42, Int(Double(count) * 0.20))
    let spanLimit = count < 80 ? 1 : count < 200 ? 2 : 3
    guard maximum >= 2 else { return [] }
    var candidates = paragraph.emphasizedRanges.map { EmphasisCandidate(range: $0, priority: 0) }
    let generic: Set<String> = ["本节", "概念", "练习", "例题", "解答", "知识点", "核心概念", "重点", "分析", "答案"]
    var seenConcepts = Set<String>()
    for (index, raw) in keyConcepts.enumerated() {
      let concept = raw.trimmingCharacters(in: .whitespacesAndNewlines)
      guard (2...16).contains(concept.count), !generic.contains(concept),
        seenConcepts.insert(concept.lowercased()).inserted else { continue }
      var search = NSRange(location: 0, length: text.length)
      while search.length > 0 {
        let range = text.range(of: concept, options: .caseInsensitive, range: search)
        guard range.location != NSNotFound else { break }
        let isLatin = concept.unicodeScalars.allSatisfy { $0.isASCII }
        func tokenCharacter(_ offset: Int) -> Bool {
          guard offset >= 0, offset < text.length else { return false }
          let value = text.character(at: offset)
          return (48...57).contains(value) || (65...90).contains(value) || (97...122).contains(value) || value == 95
        }
        if !isLatin || (!tokenCharacter(range.location - 1) && !tokenCharacter(NSMaxRange(range))) {
          candidates.append(EmphasisCandidate(range: range, priority: 10 + index))
        }
        search = NSRange(location: NSMaxRange(range), length: text.length - NSMaxRange(range))
      }
    }
    if let definition = try? NSRegularExpression(pattern:
      #"^(?:[（(][0-9]+[）)]\s*)?([\p{Han}A-Za-z][\p{Han}A-Za-z0-9·-]{1,11}?)(?:是指|指的是|定义为|是)"#),
      let match = definition.firstMatch(in: paragraph.text, range: NSRange(location: 0, length: text.length)) {
      let range = match.range(at: 1)
      if !["这", "这个", "它", "本节", "这里", "我们"].contains(text.substring(with: range)) {
        candidates.append(EmphasisCandidate(range: range, priority: 20))
      }
    }
    if let conditions = try? NSRegularExpression(pattern:
      #"(?:富含|缺少|过量|足量|等量)[\p{Han}A-Za-z0-9]{1,6}?(?=的|时|[，。；：]|$)|(?:同|不同)[\p{Han}]{1,4}(?=[，。；：])"#) {
      for match in conditions.matches(in: paragraph.text, range: NSRange(location: 0, length: text.length)) {
        candidates.append(EmphasisCandidate(range: match.range, priority: 5))
      }
    }
    candidates.sort {
      if $0.priority != $1.priority { return $0.priority < $1.priority }
      if $0.range.location != $1.range.location { return $0.range.location < $1.range.location }
      return $0.range.length > $1.range.length
    }
    var selected: [NSRange] = []
    var terms = Set<String>()
    var used = 0
    for candidate in candidates {
      guard selected.count < spanLimit else { break }
      let length = text.substring(with: candidate.range).filter { !$0.isWhitespace }.count
      let term = text.substring(with: candidate.range).lowercased()
      guard length >= 2, !terms.contains(term),
        selected.allSatisfy({ NSIntersectionRange($0, candidate.range).length == 0 }),
        paragraph.codeRanges.allSatisfy({ NSIntersectionRange($0, candidate.range).length == 0 }),
        used + length <= target || (selected.isEmpty && length <= maximum) else { continue }
      selected.append(candidate.range)
      terms.insert(term)
      used += length
    }
    return selected.sorted { $0.location < $1.location }
  }
}

private struct LearningPrettyReadingText: UIViewRepresentable {
  let text: String
  var keyConcepts: [String] = []
  final class Coordinator {
    var text = ""
    var keyConcepts: [String] = []
    var width: CGFloat = -1
    var height: CGFloat = 28
  }
  func makeCoordinator() -> Coordinator { Coordinator() }
  func makeUIView(context: Context) -> UITextView {
    let view = UITextView()
    view.backgroundColor = .clear
    view.isEditable = false
    view.isSelectable = true
    view.isScrollEnabled = false
    view.textContainerInset = .zero
    view.textContainer.lineFragmentPadding = 0
    view.dataDetectorTypes = []
    view.accessibilityIdentifier = "lesson.readingText"
    view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    return view
  }
  func updateUIView(_ uiView: UITextView, context: Context) {
    if uiView.bounds.width > 0 {
      apply(width: uiView.bounds.width, view: uiView, cache: context.coordinator)
    }
  }
  func sizeThatFits(_ proposal: ProposedViewSize, uiView: UITextView, context: Context)
    -> CGSize?
  {
    let width = min(721.5, proposal.width ?? 721.5)
    guard width.isFinite, width > 0 else { return nil }
    apply(width: width, view: uiView, cache: context.coordinator)
    return CGSize(width: width, height: context.coordinator.height)
  }
  private func apply(width: CGFloat, view: UITextView, cache: Coordinator) {
    guard cache.text != text || cache.keyConcepts != keyConcepts || abs(cache.width - width) > 0.1 else { return }
    if cache.text != text || cache.keyConcepts != keyConcepts {
      view.attributedText = LearningReadingTypography.attributedText(in: text, keyConcepts: keyConcepts)
      view.accessibilityLabel = view.attributedText.string
    }
    cache.text = text
    cache.keyConcepts = keyConcepts
    cache.width = width
    cache.height = ceil(view.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude)).height)
  }
}

struct LearningReadingSection: Identifiable {
  let block: LessonBlock
  var assets: [BookAsset]
  var id: String { block.block_id }
}

/// The same two-pass illustration assignment as the source frontend: each knowledge
/// point receives one relevant image before a second slot is filled.
func learningReadingSections(blocks: [LessonBlock], assets: [BookAsset]) -> [LearningReadingSection]
{
  let candidates: [[BookAsset]] = blocks.map { block in
    let explicit = block.asset_ids.compactMap { id in assets.first { $0.id == id } }
    let chunkLinked = assets.filter {
      !Set($0.source_chunk_ids).isDisjoint(with: block.source_chunk_ids)
    }
    let conceptLinked = assets.filter { asset in
      asset.source_chunk_ids.isEmpty
        && asset.concepts.contains { concept in
          block.title.localizedCaseInsensitiveContains(concept)
            || block.content.localizedCaseInsensitiveContains(concept)
        }
    }
    var seen = Set<String>()
    let groups = [explicit, chunkLinked, conceptLinked]
    return groups.flatMap { group in
      group.sorted {
        ($0.source_type == "extracted" ? 0 : 1) < ($1.source_type == "extracted" ? 0 : 1)
      }
    }.filter { asset in
      guard seen.insert(asset.id).inserted else { return false }
      if asset.source_type == "extracted" {
        if asset.type.lowercased().contains("page") { return false }
        if let bbox = asset.bbox, bbox.count >= 4,
          bbox.prefix(4).allSatisfy({ (0...1).contains($0) }),
          bbox[0] <= 0.05, bbox[1] <= 0.05, bbox[2] >= 0.95, bbox[3] >= 0.95
        {
          return false
        }
      }
      return true
    }
  }
  var result = blocks.map { LearningReadingSection(block: $0, assets: []) }
  var used = Set<String>()
  for _ in 0..<2 {
    for index in blocks.indices {
      if let asset = candidates[index].first(where: { !used.contains($0.id) }) {
        result[index].assets.append(asset)
        used.insert(asset.id)
      }
    }
  }
  return result
}

struct LessonReaderView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var page = 0
  @State private var mediaRequest: LessonMediaRequest?
  @State private var concept: String?

  private var lesson: Lesson? {
    store.lessons.first { $0.chapter_id == store.selectedChapterID } ?? store.activeLesson
  }
  private var chapter: Chapter? { store.chapters.first { $0.id == store.selectedChapterID } }
  private var cloudCourse: CloudCourse? {
    store.cloudResource?.courses.first { $0.chapter_id == lesson?.chapter_id }
  }
  private var needsPreferenceUpdate: Bool {
    guard let chapterID = lesson?.chapter_id else { return false }
    return store.cloudResource?.needsPreferenceUpdate(chapterID: chapterID) == true
  }
  private var assets: [BookAsset] {
    store.currentSource?.assets.filter { asset in
      asset.chapter_id == lesson?.chapter_id
        || (asset.source_type == "extracted" && (asset.page ?? 0) >= (lesson?.page_start ?? 1)
          && (asset.page ?? 0) <= (lesson?.page_end ?? 0))
    } ?? []
  }
  private var sections: [LearningReadingSection] {
    learningReadingSections(blocks: lesson?.blocks ?? [], assets: assets)
  }
  private var pageCount: Int { sections.count + 1 }
  private var activeSection: LearningReadingSection? {
    page > 0 && page - 1 < sections.count ? sections[page - 1] : nil
  }
  private var overviewImage: BookAsset? {
    guard let lesson else { return nil }
    return lesson.asset_ids.compactMap { id in assets.first { $0.id == id } }.first {
      $0.source_type == "ai_generated"
    }
      ?? assets.first {
        lesson.lesson_id == "lesson_meiosis" && $0.id == "asset_ai_meiosis_fertilization_cycle_v1"
      }
  }
  private var citation: LessonCitation? {
    activeSection?.block.citations.first ?? sections.compactMap { $0.block.citations.first }.first
  }
  private var positionKey: String {
    "native.lesson.page.\(store.selectedCourseID ?? "none").\(store.selectedBookID ?? "none").\(lesson?.id ?? "none")"
  }

  var body: some View {
    Group {
      if let lesson {
        VStack(spacing: 0) {
          readerHeader(lesson)
          ScrollViewReader { proxy in
            ScrollView {
              VStack(spacing: 0) {
                if needsPreferenceUpdate {
                  VStack(alignment: .leading, spacing: 10) {
                    Text("学习偏好已更新").font(DS.font(.cardTitle))
                    Text("本章仍保留原来的学习内容。更新后会按当前课程设置重新整理，学习记录会保留。")
                      .font(DS.font(.supporting)).foregroundStyle(DS.secondary)
                    BCButton("按当前偏好更新本章", icon: "arrow.clockwise") {
                      store.requestCloudLearning(chapterID: lesson.chapter_id)
                    }.disabled(store.cloudState.isWorking)
                      .accessibilityIdentifier("lesson.updatePreferences")
                  }.frame(maxWidth: .infinity, alignment: .leading).padding(16)
                    .background(DS.primarySoft)
                    .accessibilityIdentifier("lesson.preferencesChanged")
                }
                pageStatus
                Group {
                  if let section = activeSection {
                    sectionContent(section)
                  } else {
                    introduction(lesson)
                  }
                }
                .padding(.horizontal, 19).padding(.top, 22).padding(.bottom, 70)
                .frame(maxWidth: .infinity, minHeight: 620, alignment: .topLeading)
                .overlay(alignment: .bottomTrailing) {
                  sourceEntry.padding(.trailing, 18).padding(.bottom, 14)
                }
              }
              .background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
              .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
              .clipShape(RoundedRectangle(cornerRadius: 16))
              .id("lesson-top")
              .frame(maxWidth: 1120, alignment: .leading)
              .padding(.horizontal, store.isPad ? 32 : 16).padding(.top, 6).padding(.bottom, 28)
              .frame(maxWidth: .infinity)
              mediaActions.padding(.horizontal, 20).padding(.bottom, 16)
            }
            .background(DS.background)
            .simultaneousGesture(
              DragGesture(minimumDistance: 35).onEnded { value in
                guard abs(value.translation.width) > abs(value.translation.height) * 1.4 else {
                  return
                }
                changePage(by: value.translation.width < 0 ? 1 : -1)
              }
            )
            .onChange(of: page) { _, _ in
              store.learningState.readerCompletionVisible = page == pageCount - 1
              UserDefaults.standard.set(page, forKey: positionKey)
              proxy.scrollTo("lesson-top", anchor: .top)
            }
          }
          if page == pageCount - 1 {
            HStack {
              Spacer()
              BCButton("完成本节", icon: "checkmark.circle") {
                store.completeLesson()
                store.go(.study)
              }
              .frame(maxWidth: 190)
              Spacer()
            }.padding(.horizontal, 20).padding(.bottom, 12)
          }
        }
        .task(id: positionKey) {
          store.selectedLessonID = lesson.id
          page = min(max(0, UserDefaults.standard.integer(forKey: positionKey)), pageCount - 1)
          store.learningState.readerCompletionVisible = page == pageCount - 1
        }
      } else {
        LearningScroll(width: 720) {
          BCHeader(chapter?.title ?? "等待章节课程", back: { store.back() })
          EmptyStateView(title: "本节课程尚未准备好", message: "课程内容准备好后，会按原书结构展示可追溯的图文讲解。你也可以直接打开原文。", icon: "BookOpen")
          if let chapter, let bookID = store.selectedBookID {
            BCButton("阅读原文", icon: "book") {
              store.openSource(
                SourcePageTarget(
                  bookID: bookID, title: chapter.source_title, page: chapter.page_start,
                  pageEnd: chapter.page_end, printedPage: chapter.printed_page_start, from: .lesson)
              )
            }
          }
          BCButton("返回课程目录", style: .secondary) { store.go(.study) }
        }
      }
    }
    .onDisappear {
      store.learningState.readerCompletionVisible = false
      store.learningState.readerOverlayVisible = false
    }
    .onChange(of: mediaRequest?.id) { _, _ in
      store.learningState.readerOverlayVisible = mediaRequest != nil || concept != nil
    }
    .onChange(of: concept) { _, _ in
      store.learningState.readerOverlayVisible = mediaRequest != nil || concept != nil
    }
    .overlay {
      if let concept { conceptPanel(concept) }
    }
    .sheet(item: $mediaRequest) { request in
      LearningMediaSheet(request: request, store: store)
    }
  }

  private func readerHeader(_ lesson: Lesson) -> some View {
    ZStack(alignment: .top) {
      Text("章节学习").font(DS.font(.pageTitle))
        .accessibilityAddTraits(.isHeader).accessibilityIdentifier("navigation.title")
      HStack {
        Button {
          store.back()
        } label: {
          BCGlyph("ArrowLeft").frame(width: 18, height: 18)
            .iconButtonFrame().background(
              DS.surface, in: RoundedRectangle(cornerRadius: 12)
            )
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.border, lineWidth: 1))
        }.buttonStyle(.plain).accessibilityLabel("返回")
          .accessibilityIdentifier("navigation.back")
        Spacer()
      }.offset(y: DS.navigationButtonOffset)
    }.frame(height: DS.navigationRowHeight, alignment: .top).padding(.horizontal, store.isPad ? 32 : 16)
      .padding(.top, NotesScreenMetrics.titleTopInset)
      .padding(.bottom, store.isPad ? 14.84 : 6)
      .foregroundStyle(DS.text)
      .navigationBarSurface(DS.surface)
  }

  private var pageStatus: some View {
    VStack(spacing: 10) {
      HStack(spacing: 10) {
        Text("第 \(page + 1) / \(pageCount) 页").accessibilityIdentifier("lesson.page")
          .accessibilityValue(String(page + 1)).font(
            DS.font(.cardTitle)
          ).foregroundStyle(
            DS.primary)
        Text(activeSection?.block.title ?? "本节导读").font(DS.font(.cardTitle)).foregroundStyle(
          DS.secondary
        ).lineLimit(1)
        Spacer(minLength: 0)
        Text("左右滑动").font(DS.font(.supporting, weight: .semibold)).foregroundStyle(DS.secondary)
      }.frame(height: 28, alignment: .top)
      GeometryReader { geometry in
        ZStack(alignment: .leading) {
          Capsule().fill(DS.border)
          Capsule().fill(DS.primary).frame(
            width: geometry.size.width * Double(page + 1) / Double(pageCount))
        }
      }.frame(height: 5)
    }
    .padding(.horizontal, 17).padding(.vertical, 14).padding(.bottom, 1).padding(.top, 1)
    .background(Color(hex: 0xf3f6fb))
    .overlay(alignment: .bottom) { Rectangle().fill(DS.border).frame(height: 1) }
    .accessibilityElement(children: .combine)
  }

  private func introduction(_ lesson: Lesson) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      VStack(alignment: .leading, spacing: 4) {
        Text(lesson.title).font(DS.font(.sectionTitle)).frame(minHeight: 32, alignment: .leading)
        Text(
          "\(store.selectedBookID == "book_biology_2" ? "人教版高中生物必修二遗传与进化" : store.currentSource?.summary.title ?? store.currentTitle) · 原书\(learningPageLabel(lesson.page_start, end: lesson.page_end, printed: chapter?.printed_page_start, printedEnd: chapter?.printed_page_end).replacingOccurrences(of: "教材", with: ""))"
        )
        .font(DS.font(.supporting)).foregroundStyle(DS.secondary).frame(minHeight: 20)
      }.padding(.bottom, 15).overlay(alignment: .bottom) {
        Rectangle().fill(DS.border).frame(height: 1)
      }
      Button {
        concept = lesson.key_concepts.first ?? lesson.title
      } label: {
        Text("本节导读").font(DS.font(.cardTitle)).buttonFrame()
      }.buttonStyle(.plain).accessibilityLabel("查看概念：\(lesson.key_concepts.first ?? lesson.title)")
        .accessibilityIdentifier("lesson.concept")
      paragraph(lesson.summary)
      if let summary = cloudCourse?.personalizationSummary {
        VStack(alignment: .leading, spacing: 6) {
          Text("本章学习安排").font(DS.font(.cardTitle))
          Text(summary).font(DS.font(.supporting)).foregroundStyle(DS.secondary)
        }.accessibilityIdentifier("lesson.personalization")
      }
      if let overviewImage { figure(overviewImage) }
      if !lesson.objectives.isEmpty {
        VStack(alignment: .leading, spacing: 5) {
          ForEach(lesson.objectives, id: \.self) { objective in
            HStack(alignment: .top, spacing: 15) {
              Circle().fill(DS.primary).frame(width: 5, height: 5).padding(.top, 11)
              Text(objective).font(DS.font(.reading)).lineSpacing(4).padding(.vertical, 2)
                .foregroundStyle(DS.secondary)
            }
          }
        }
      }

    }
  }

  private func sectionContent(_ section: LearningReadingSection) -> some View {
    // A fenced block may contain blank lines; keep its parser state together.
    let content = section.block.content
    let groups = content.contains("```") || content.contains("~~~")
      ? [content] : content.components(separatedBy: "\n\n")
    let paragraphs = groups.map {
      $0.trimmingCharacters(in: .whitespacesAndNewlines)
    }.filter { !$0.isEmpty }
    return VStack(alignment: .leading, spacing: 20) {
      Button {
        concept = section.block.title
      } label: {
        Text(section.block.title).font(DS.font(.cardTitle)).lineSpacing(3).buttonFrame()
      }.buttonStyle(.plain).accessibilityLabel("查看概念：\(section.block.title)")
        .accessibilityIdentifier("lesson.concept")
      ForEach(Array(paragraphs.enumerated()), id: \.offset) { index, text in
        paragraph(text, keyConcepts: (lesson?.key_concepts ?? []) + [section.block.title])
        if index < section.assets.count { figure(section.assets[index]) }
      }
      ForEach(Array(section.assets.dropFirst(paragraphs.count))) { asset in figure(asset) }
    }
    .id(section.id)
    .transition(
      reduceMotion
        ? .opacity
        : .asymmetric(insertion: .opacity.combined(with: .offset(x: 12)), removal: .opacity))
  }

  private func paragraph(_ text: String, keyConcepts: [String]? = nil) -> some View {
    LearningPrettyReadingText(text: text, keyConcepts: keyConcepts ?? lesson?.key_concepts ?? [])
      .frame(maxWidth: 721.5, alignment: .leading)
      .contextMenu {
        Button("摘录为笔记", systemImage: "note.text.badge.plus") {
          store.selectedQuote = LearningReadingTypography.plainText(in: text)
          store.noteState.contextCitation = citation
          store.openSheet(.note)
        }
        Button("询问 AI", systemImage: "sparkles") {
          store.selectedQuote = LearningReadingTypography.plainText(in: text)
          store.openSheet(.chat)
        }
      }
  }

  @ViewBuilder private func figure(_ asset: BookAsset) -> some View {
    if ContentBundle.url(asset.image_url) != nil {
      VStack(spacing: 6) {
        if asset.source_type == "extracted" {
          Button {
            openAssetSource(asset)
          } label: {
            figureImage(asset)
          }.buttonStyle(.plain)
        } else {
          figureImage(asset)
        }
        HStack(spacing: 12) {
          if asset.source_type == "extracted", let page = asset.page, page > 0 {
            Text("第 \(page) 页").font(DS.font(.body)).foregroundStyle(DS.secondary)
          }
          Spacer(minLength: 0)
          Button {
            openMedia(kind: "video", asset: asset)
          } label: {
            HStack(spacing: 5) {
              Image(systemName: "play.circle").font(.system(size: 14))
              Text("生成讲解短片")
            }.font(DS.font(.smallButton)).foregroundStyle(DS.primary).buttonFrame()
          }.buttonStyle(.plain).accessibilityIdentifier("lesson.video.open.\(asset.id)")
        }.frame(height: DS.ButtonSize.regular.height).frame(maxWidth: 272).fixedSize(
          horizontal: false, vertical: true)
      }.frame(maxWidth: .infinity).padding(.vertical, 2)
        .accessibilityElement(children: .contain).accessibilityLabel(asset.caption)
    }
  }

  private func figureImage(_ asset: BookAsset) -> some View {
    AssetImage(asset.image_url).frame(
      maxWidth: activeSection == nil ? 272 : 216, maxHeight: activeSection == nil ? 168 : 152
    )
    .clipShape(RoundedRectangle(cornerRadius: 8))
  }

  @ViewBuilder private var sourceEntry: some View {
    if let citation, citation.page_start > 0 {
      Button {
        store.openCitation(citation)
      } label: {
        Label("查看原文", systemImage: "book").font(DS.font(.smallButton)).foregroundStyle(
          DS.primary)
      }.buttonStyle(.plain).buttonFrame()
        .accessibilityLabel(
          "查看原文，\(learningPageLabel(citation.page_start, end: citation.page_end, printed: citation.printed_page_start, printedEnd: citation.printed_page_end))"
        )
    }
  }

  private var readerFooter: some View {
    HStack(spacing: 14) {
      Button {
        changePage(by: -1)
      } label: {
        Label("上一页", systemImage: "chevron.left").font(DS.font(.smallButton))
          .frame(minWidth: 66).buttonFrame()
      }.disabled(page == 0).opacity(page == 0 ? 0.35 : 1)
      Spacer(minLength: 0)
      Button {
        store.openSheet(.chat)
      } label: {
        HStack(spacing: 6) {
          StickerIcon("Sparkles", size: 26)
          Text("问问 AI").font(DS.font(.smallButton))
        }
        .padding(.horizontal, DS.ButtonSize.regular.horizontalPadding).buttonFrame()
        .background(DS.primarySoft, in: Capsule())
      }
      Spacer(minLength: 0)
      if page == pageCount - 1 {
        Button {
          store.completeLesson()
          store.go(.study)
        } label: {
          Label("完成本节", systemImage: "checkmark.circle.fill").font(DS.font(.button))
            .foregroundStyle(.white).padding(.horizontal, DS.ButtonSize.primary.horizontalPadding)
            .buttonFrame(.primary)
            .background(DS.primary, in: Capsule())
        }
      } else {
        Button {
          changePage(by: 1)
        } label: {
          HStack(spacing: 5) {
            Text("下一页")
            Image(systemName: "chevron.right")
          }
          .font(DS.font(.smallButton)).frame(minWidth: 66).buttonFrame()
        }
      }
    }
    .buttonStyle(.plain).foregroundStyle(DS.primary)
    .padding(.horizontal, 18).padding(.vertical, 10)
    .background(DS.surface)
    .overlay(alignment: .top) { Rectangle().fill(DS.border).frame(height: 1) }
  }

  private func changePage(by delta: Int) {
    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) {
      page = min(max(0, page + delta), pageCount - 1)
    }
  }

  private func openAssetSource(_ asset: BookAsset) {
    guard let id = store.selectedBookID else { return }
    let start = asset.page ?? citation?.page_start ?? lesson?.page_start ?? 1
    store.openSource(SourcePageTarget(bookID: id, title: asset.caption, page: start, from: .lesson))
  }

  private var mediaActions: some View {
    HStack(spacing: 12) {
      BCButton("生成辅助图", icon: "photo", style: .secondary) { openMedia(kind: "image") }
      BCButton("生成讲解短片", icon: "video", style: .secondary) { openMedia(kind: "video") }
    }
  }

  private func openMedia(kind: String, asset: BookAsset? = nil) {
    guard let lesson, let bookID = store.selectedBookID else { return }
    let linked = asset?.source_chunk_ids.compactMap { id in
      store.currentSource?.chunks.first { $0.id == id }?.text
    }.first
    let sourceQuote = citation?.quote.flatMap { $0.isEmpty ? nil : $0 }
    let excerpt = linked ?? sourceQuote ?? activeSection?.block.content ?? lesson.summary
    let sourcePages = asset?.page.map { [$0] } ?? citation.map { [$0.page_start] } ?? [lesson.page_start]
    mediaRequest = LessonMediaRequest(
      kind: kind, bookID: bookID, chapterID: lesson.chapter_id,
      chapterTitle: chapter?.source_title ?? lesson.source_title,
      title: asset?.caption ?? lesson.title,
      excerpt: String(excerpt.trimmingCharacters(in: .whitespacesAndNewlines).prefix(3000)),
      pages: sourcePages.filter { $0 > 0 }, lessonID: lesson.id,
      courseID: store.selectedCourseID ?? "")
    let previous = LessonMediaRequest(lesson: lesson, section: activeSection?.block,
      asset: asset, kind: kind == "image" ? .image : .video)
    mediaRequest?.legacyExcerpt = previous.excerpt
    mediaRequest?.legacyChapterTitle = previous.chapterTitle
  }

  private func conceptPanel(_ title: String) -> some View {
    let section = sections.first {
      $0.block.title.contains(title) || $0.block.content.contains(title)
    }
    return ZStack {
      Color.black.opacity(0.25).ignoresSafeArea().onTapGesture { concept = nil }
      VStack(alignment: .leading, spacing: 18) {
        HStack {
          Text("核心概念").font(DS.font(.supporting, weight: .semibold)).foregroundStyle(DS.secondary)
          Spacer()
          Button {
            concept = nil
          } label: {
            Image(systemName: "xmark").iconButtonFrame()
          }.buttonStyle(.plain).accessibilityLabel("关闭概念").accessibilityIdentifier(
            "lesson.concept.close")
        }
        Text(title).font(DS.font(.sectionTitle))
        ScrollView {
          LearningPrettyReadingText(text: section?.block.content ?? lesson?.summary ?? "",
            keyConcepts: (lesson?.key_concepts ?? []) + [title])
        }.frame(maxHeight: 280)
        if let citation = section?.block.citations.first {
          LearningSourceButton(
            page: citation.page_start, end: citation.page_end, printed: citation.printed_page_start,
            printedEnd: citation.printed_page_end
          ) {
            concept = nil
            store.openCitation(citation)
          }
        }
        BCButton("保存为学习笔记", icon: "note.text.badge.plus") {
          store.selectedQuote = "\(title)\n" + LearningReadingTypography.plainText(in: section?.block.content ?? lesson?.summary ?? "")
          concept = nil
          store.noteState.contextCitation =
            section?.block.citations.first ?? lesson?.blocks.flatMap(\.citations).first
          store.openSheet(.note)
        }
      }.padding(24).frame(maxWidth: 480).background(
        DS.surface, in: RoundedRectangle(cornerRadius: 24)
      ).padding(24)
        .accessibilityAddTraits(.isModal)
    }
  }
}

struct LessonMediaRequest: Identifiable {
  let kind: String
  let bookID: String
  let chapterID: String
  let chapterTitle: String
  let title: String
  let excerpt: String
  let pages: [Int]
  let lessonID: String
  let courseID: String
  // Lookup compatibility for tasks created before the cloud request model was adopted.
  var legacyExcerpt: String? = nil
  var legacyChapterTitle: String? = nil

  var id: String {
    let parts = [kind, bookID, chapterID, lessonID, courseID, excerpt,
                 pages.map(String.init).joined(separator: ",")]
    let data = (try? JSONEncoder().encode(parts)) ?? Data()
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
  }
}

struct LessonMediaReceipt: Codable, Equatable {
  var requestID: String
  var jobID: String?
  var state: String
  var submissionUncertain: Bool

  var permitsNewGeneration: Bool {
    !submissionUncertain && ["failed", "cancelled", "rejected"].contains(state)
  }

  func validate() throws {
    guard LessonMediaJobContract.validRequestID(requestID),
          LessonMediaJobContract.states.contains(state),
          jobID == nil || LessonMediaJobContract.validJobID(jobID!) else {
      throw ContentError.invalid("媒体任务回执无法核验，请保留原请求并联系管理员。")
    }
  }

  mutating func accept(_ value: StudioJob, bookID: String, kind: String) throws {
    try validate()
    guard ["image", "video"].contains(kind), value.book_id == bookID, value.kind == kind,
          LessonMediaJobContract.validJobID(value.id),
          LessonMediaJobContract.states.contains(value.status),
          jobID == nil || value.id == jobID,
          state != "succeeded" || value.status == "succeeded" else {
      throw ContentError.invalid("返回任务与当前教材、类型或状态不一致，已停止显示。")
    }
    // Content coalescing may return a canonical job's older request_id. Keep
    // our durable alias and bind all subsequent reads to the returned job ID.
    jobID = value.id
    state = value.status
    submissionUncertain = false
  }
}

enum LessonMediaJobContract {
  static let states: Set<String> = [
    "queued", "planning", "submitting", "polling", "reviewing", "succeeded",
    "failed", "cancelled", "rejected", "uncertain",
  ]

  static func validJobID(_ value: String) -> Bool {
    value.utf8.count == 32 && value.utf8.allSatisfy {
      (48...57).contains($0) || (97...102).contains($0)
    }
  }

  static func validRequestID(_ value: String) -> Bool {
    (8...100).contains(value.utf8.count) && value.utf8.allSatisfy {
      (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0)
        || $0 == 45 || $0 == 95
    }
  }
}
