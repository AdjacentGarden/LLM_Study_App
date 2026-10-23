import CryptoKit
import Foundation
import ImageIO
import PDFKit
import ZIPFoundation

struct StagedSource: Sendable {
  var source: SourceContent
  var relativePath: String
}
enum LocalFileImporter {
  static let maximumBytes = 100 * 1024 * 1024
  static let extensions: Set<String> = [
    "pdf", "png", "jpg", "jpeg", "webp", "gif", "heic", "heif", "docx", "pptx", "xlsx", "txt", "md",
    "csv",
  ]
  static func stage(url: URL, repository: LocalBookCourseRepository) async throws -> StagedSource {
    let accessed = url.startAccessingSecurityScopedResource()
    defer { if accessed { url.stopAccessingSecurityScopedResource() } }
    try Task.checkCancellation()
    let values = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
    guard values.isRegularFile == true else { throw ContentError.invalid("请选择一个资料文件") }
    guard let size = values.fileSize, size > 0, size <= maximumBytes else {
      throw ContentError.invalid("请选择非空且不超过 100 MB 的文件")
    }
    let data = try Data(contentsOf: url, options: .mappedIfSafe)
    guard data.count <= maximumBytes else { throw ContentError.invalid("资料不能超过 100 MB") }
    let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    let id = "book_local_" + hash.prefix(24)
    try Task.checkCancellation()
    let relative = try await repository.saveAttachment(data, extension: url.pathExtension)
    let summary = SourceSummary(
      book_id: id, title: url.deletingPathExtension().lastPathComponent,
      filename: url.lastPathComponent, status: "uploaded", page_count: 0, chapter_count: 0,
      chunk_count: 0, asset_count: 0, average_confidence: 0,
      updated_at: Date().timeIntervalSince1970, content_mode: "local_original",
      directory_unit_label: "资料原文", next_title: "原文件已保存，尚未提取文字或生成教学内容")
    return StagedSource(
      source: SourceContent(
        summary: summary, chapters: [], lessons: [], chunks: [], assets: [], flashcards: [],
        quizzes: [],
        plan: StudyPlan(user_id: "", book_id: id, days: 0, daily_minutes: 0, tasks: [])),
      relativePath: relative)
  }
  static func extract(url: URL, bookID: String, filename: String) async throws -> SourceContent {
    try Task.checkCancellation()
    let task = Task.detached(priority: .userInitiated) {
      try extractSynchronously(url: url, bookID: bookID, filename: filename)
    }
    return try await withTaskCancellationHandler {
      try await task.value
    } onCancel: {
      task.cancel()
    }
  }
  static func extractSynchronously(url: URL, bookID: String, filename: String) throws
    -> SourceContent
  {
    try Task.checkCancellation()
    let ext = (filename as NSString).pathExtension.lowercased()
    guard extensions.contains(ext) else {
      throw ContentError.invalid("原文件已保存。此格式暂不能整理，请添加 PDF、图片或新版 Office 文件。")
    }
    var pages: [String] = []
    if ext == "pdf" {
      guard let pdf = PDFDocument(url: url), !pdf.isLocked, pdf.pageCount > 0 else {
        throw ContentError.invalid("PDF 无法读取或需要密码，原文件已保留")
      }
      for index in 0..<pdf.pageCount {
        try Task.checkCancellation()
        pages.append(
          pdf.page(at: index)?.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "")
      }
    } else if ["docx", "pptx", "xlsx"].contains(ext) {
      let archive = try Archive(url: url, accessMode: .read)
      func bytes(_ name: String) throws -> Data {
        guard let entry = archive[name] else { throw ContentError.invalid("文档结构不完整，原文件已保留") }
        guard entry.uncompressedSize <= 128 * 1024 * 1024 else {
          throw ContentError.invalid("文档解压后的内容过大")
        }
        try Task.checkCancellation()
        var data = Data()
        _ = try archive.extract(entry) {
          try Task.checkCancellation()
          data.append($0)
        }
        return data
      }
      if ext == "docx" { pages = [try XMLTextReader.text(bytes("word/document.xml"))] }
      if ext == "pptx" {
        let entries = archive.map(\.path).filter {
          $0.range(of: #"^ppt/slides/slide\d+\.xml$"#, options: .regularExpression) != nil
        }.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        pages = try entries.map { try XMLTextReader.text(bytes($0)) }
      }
      if ext == "xlsx" {
        let shared: [String]
        if archive["xl/sharedStrings.xml"] != nil {
          shared = try XMLTextReader.items(bytes("xl/sharedStrings.xml"), element: "si")
        } else {
          shared = []
        }
        let entries = archive.map(\.path).filter {
          $0.range(of: #"^xl/worksheets/sheet\d+\.xml$"#, options: .regularExpression) != nil
        }.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        pages = try entries.map { name in
          let delegate = SpreadsheetXMLReader(shared: shared)
          let parser = XMLParser(data: try bytes(name))
          parser.delegate = delegate
          guard parser.parse() else { throw ContentError.invalid("表格内容无法读取") }
          return delegate.rows.joined(separator: "\n")
        }
      }
    } else if ["txt", "md", "csv"].contains(ext) {
      let data = try Data(contentsOf: url)
      guard let text = String(data: data, encoding: .utf8) else {
        throw ContentError.invalid("文本不是 UTF-8 编码，请重新导出后添加")
      }
      pages = [text]
    } else {
      guard let image = CGImageSourceCreateWithURL(url as CFURL, nil),
        CGImageSourceGetCount(image) > 0, CGImageSourceCreateImageAtIndex(image, 0, nil) != nil
      else { throw ContentError.invalid("图片无法读取，原文件已保留") }
      pages = [""]
    }
    guard !pages.isEmpty else { throw ContentError.invalid("未找到可读取的页面，原文件仍保留在课程资料中") }
    let title = (filename as NSString).deletingPathExtension
    var chunks: [SourceChunk] = []
    let hasText = pages.contains { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    let locatorKind: String
    switch ext {
    case "pdf": locatorKind = "pdf_page"
    case "pptx": locatorKind = "slide"
    case "xlsx": locatorKind = "worksheet"
    case "png", "jpg", "jpeg", "webp", "gif", "heic", "heif": locatorKind = "image"
    default: locatorKind = "document"
    }
    for (index, text) in pages.enumerated() {
      try Task.checkCancellation()
      let page = index + 1
      var cursor = text.startIndex
      var part = 0
      while cursor < text.endIndex {
        try Task.checkCancellation()
        let end = text.index(cursor, offsetBy: 1200, limitedBy: text.endIndex) ?? text.endIndex
        chunks.append(
          SourceChunk(
            chunk_id: "\(bookID):p\(page):\(part)", book_id: bookID, chapter_id: "",
            page_start: page, page_end: page, content_type: "text",
            text: String(text[cursor..<end]), asset_ids: [], key_concepts: [],
            source_metadata: ["filename": .string(filename), "parser": .string("local-original"),
                              "locator_kind": .string(locatorKind),
                              "physical_page_verified": .bool(ext == "pdf")]))
        cursor = end
        part += 1
      }
    }
    // Text extraction is a source operation. Page/slide/worksheet order is not
    // a detected textbook chapter, an AI lesson, a quality score or a study plan.
    let summary = SourceSummary(
      book_id: bookID, title: title, filename: filename, status: "source_ready", page_count: pages.count,
      chapter_count: 0, chunk_count: chunks.count, asset_count: 0,
      average_confidence: 0, updated_at: Date().timeIntervalSince1970,
      content_mode: "local_original", directory_unit_label: "资料原文",
      next_title: hasText ? "原文可阅读，尚未识别教材章节或生成教学内容"
        : "原文件可阅读，暂无可提取文本；尚未生成教学内容")
    return SourceContent(
      summary: summary, chapters: [], lessons: [], chunks: chunks, assets: [],
      flashcards: [], quizzes: [],
      plan: StudyPlan(user_id: "", book_id: bookID, days: 0, daily_minutes: 0, tasks: []))
  }
}
private final class XMLTextReader: NSObject, XMLParserDelegate {
  var result: [String] = []
  var text = ""
  var level = 0
  let element: String
  init(element: String) { self.element = element }
  static func items(_ bytes: Data, element: String) throws -> [String] {
    let d = XMLTextReader(element: element)
    let p = XMLParser(data: bytes)
    p.delegate = d
    guard p.parse() else { throw ContentError.invalid("文档内容无法读取，请重新导出后添加") }
    return d.result
  }
  static func text(_ bytes: Data) throws -> String {
    try items(bytes, element: "p").joined(separator: "\n")
  }
  func parser(
    _ parser: XMLParser, didStartElement name: String, namespaceURI: String?,
    qualifiedName: String?, attributes: [String: String]
  ) {
    if name.split(separator: ":").last.map(String.init) == element {
      level += 1
      if level == 1 { text = "" }
    }
  }
  func parser(_ parser: XMLParser, foundCharacters string: String) {
    if level > 0 { text += string }
  }
  func parser(
    _ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?
  ) {
    if name.split(separator: ":").last.map(String.init) == element {
      level -= 1
      if level == 0 {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.isEmpty { result.append(value) }
      }
    }
  }
}
private final class SpreadsheetXMLReader: NSObject, XMLParserDelegate {
  let shared: [String]
  var rows: [String] = []
  var cells: [String] = []
  var content = ""
  var cellType = ""
  var capturing = false
  init(shared: [String]) { self.shared = shared }
  func parser(
    _ parser: XMLParser, didStartElement name: String, namespaceURI: String?,
    qualifiedName: String?, attributes: [String: String]
  ) {
    let n = name.split(separator: ":").last ?? ""
    if n == "row" { cells = [] }
    if n == "c" {
      cellType = attributes["t"] ?? ""
      content = ""
    }
    if n == "v" || n == "t" { capturing = true }
  }
  func parser(_ parser: XMLParser, foundCharacters string: String) {
    if capturing { content += string }
  }
  func parser(
    _ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?
  ) {
    let n = name.split(separator: ":").last ?? ""
    if n == "v" || n == "t" { capturing = false }
    if n == "c" {
      cells.append(
        cellType == "s"
          ? (Int(content).flatMap { shared.indices.contains($0) ? shared[$0] : nil } ?? "")
          : content)
    }
    if n == "row" { rows.append(cells.joined(separator: "\t")) }
  }
}
