import Foundation
import ImageIO
import PDFKit
import UniformTypeIdentifiers

/// Originals stay in the app sandbox. Only extracted text enters the current QA API.
struct AssistantAttachment: Codable, Hashable, Identifiable, Sendable {
  enum Kind: String, Codable, Sendable { case image, document }
  var id = UUID().uuidString
  var filename: String
  var kind: Kind
  var byteCount: Int
  var path: String
  var thumbnailPath: String?
  var text: String
  var textPath: String?
  var characterCount: Int?
  var pageCount: Int?

  static let maximumCount = 5
  static let maximumBytes = 100 * 1024 * 1024

  static var allowedTypes: [UTType] {
    LocalFileImporter.extensions.sorted().compactMap { UTType(filenameExtension: $0) }
  }
}

enum AssistantAttachmentImporter {
  typealias Progress = @Sendable (String) -> Void

  static func photo(_ data: Data, repository: LocalBookCourseRepository,
    onProgress: @escaping Progress = { _ in }) async throws -> AssistantAttachment {
    guard !data.isEmpty, data.count <= AssistantAttachment.maximumBytes else {
      throw ContentError.invalid("请选择不超过 100 MB 的图片。")
    }
    guard let source = CGImageSourceCreateWithData(data as CFData, nil),
      let identifier = CGImageSourceGetType(source),
      let ext = UTType(identifier as String)?.preferredFilenameExtension else {
      throw ContentError.invalid("这张图片无法读取，请重新选择。")
    }
    let url = FileManager.default.temporaryDirectory
      .appendingPathComponent("图片-\(UUID().uuidString.prefix(8)).\(ext)")
    try data.write(to: url, options: .atomic)
    defer { try? FileManager.default.removeItem(at: url) }
    return try await file(url, repository: repository, onProgress: onProgress)
  }

  static func file(_ url: URL, repository: LocalBookCourseRepository,
    onProgress: @escaping Progress = { _ in }) async throws -> AssistantAttachment {
    let accessed = url.startAccessingSecurityScopedResource()
    defer { if accessed { url.stopAccessingSecurityScopedResource() } }
    let filename = url.lastPathComponent
    let ext = url.pathExtension.lowercased()
    guard LocalFileImporter.extensions.contains(ext) else {
      throw ContentError.invalid("支持图片、PDF、TXT、Markdown、CSV 和新版 Office 文件。")
    }
    let data = try await Task.detached(priority: .userInitiated) {
      let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
      guard values.isRegularFile == true, let size = values.fileSize,
        size > 0, size <= AssistantAttachment.maximumBytes else {
        throw ContentError.invalid("请选择非空且不超过 100 MB 的附件。")
      }
      if ext == "pdf" {
        guard let document = PDFDocument(url: url), !document.isLocked, document.pageCount > 0 else {
          throw ContentError.invalid("PDF 无法读取或需要密码，请选择其他文件。")
        }
      }
      try Task.checkCancellation()
      let value = try Data(contentsOf: url, options: .mappedIfSafe)
      guard value.count <= AssistantAttachment.maximumBytes else {
        throw ContentError.invalid("附件不能超过 100 MB。")
      }
      return value
    }.value
    try Task.checkCancellation()
    let isImage = UTType(filenameExtension: ext)?.conforms(to: .image) == true
    let extracted: SourceContent
    onProgress("正在提取附件文字…")
    if AppleDocumentOCR.supportedExtensions.contains(ext) {
      extracted = try await AppleDocumentOCR.extract(url: url, bookID: "chat-attachment", filename: filename) { progress in
        onProgress("正在读取第 \(progress.currentPage) / \(progress.totalPages) 页…")
      }
    } else {
      extracted = try await LocalFileImporter.extract(url: url, bookID: "chat-attachment", filename: filename)
    }
    try Task.checkCancellation()
    guard extracted.chunks.contains(where: { $0.text.contains(where: { !$0.isWhitespace }) }) else {
      throw ContentError.invalid(isImage
        ? "没有从图片中识别到文字。请添加清晰的文字图片；当前附件问答暂不支持纯图形分析。"
        : "文件中没有可读取的文字，请选择其他文件。")
    }
    let thumbnail = isImage ? await Task.detached(priority: .utility) { thumbnailData(data) }.value : nil
    try Task.checkCancellation()
    onProgress("正在保存附件全文…")
    let path = try await repository.saveAttachment(data, extension: ext)
    var thumbnailPath: String?
    var textPath: String?
    do {
      let saved = try await repository.saveAssistantAttachmentText(extracted.chunks)
      textPath = saved.path
      if let thumbnail { thumbnailPath = try await repository.saveAttachment(thumbnail, extension: "jpg") }
      try Task.checkCancellation()
      return AssistantAttachment(filename: filename, kind: isImage ? .image : .document,
        byteCount: data.count, path: path, thumbnailPath: thumbnailPath, text: "", textPath: saved.path,
        characterCount: saved.characterCount, pageCount: extracted.summary.page_count)
    } catch {
      try? FileManager.default.removeItem(at: repository.attachmentURL(path))
      if let thumbnailPath { try? FileManager.default.removeItem(at: repository.attachmentURL(thumbnailPath)) }
      if let textPath { try? FileManager.default.removeItem(at: repository.attachmentURL(textPath)) }
      throw error
    }
  }

  private static func thumbnailData(_ data: Data) -> Data? {
    guard let source = CGImageSourceCreateWithData(data as CFData, nil),
      let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: 224,
      ] as CFDictionary) else { return nil }
    let result = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(result, UTType.jpeg.identifier as CFString, 1, nil)
    else { return nil }
    CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: 0.8] as CFDictionary)
    return CGImageDestinationFinalize(destination) ? result as Data : nil
  }
}

enum AssistantAttachmentContext {
  /// Match the existing QA contract without deleting stored history or document text.
  /// Each selected file gets one context slot, so a long file cannot crowd out another.
  static func history(_ messages: [ChatMessage], question: String,
    repository: LocalBookCourseRepository) async throws -> [ChatMessage] {
    let worker = Task.detached(priority: .userInitiated) {
      var previous = messages
      let current = previous.last?.role == "user" && previous.last?.text == question ? previous.removeLast() : nil
      var files = current?.attachments ?? []
      if files.isEmpty {
        // A named old attachment remains addressable even after its chat turn ages out.
        files = previous.reversed().flatMap { $0.attachments ?? [] }.filter {
          let name = ($0.filename as NSString).deletingPathExtension
          return name.count >= 2 && question.localizedCaseInsensitiveContains(name)
        }
        if files.isEmpty {
          files = previous.reversed().first { !($0.attachments ?? []).isEmpty }?.attachments ?? []
        }
      }
      var seen = Set<String>()
      files = Array(files.filter { seen.insert($0.id).inserted }.prefix(AssistantAttachment.maximumCount))
      let documents = try files.map { file in
        try Task.checkCancellation()
        return try context(file, question: question, repository: repository)
      }
      let conversation = previous.filter { ["user", "assistant"].contains($0.role) }
        .map { ChatMessage(role: $0.role, text: $0.text) }
      return conversation + documents
    }
    return try await withTaskCancellationHandler {
      let result = try await worker.value
      try Task.checkCancellation()
      return result
    } onCancel: { worker.cancel() }
  }

  private struct Passage {
    var index: Int
    var score: Int
    var text: String
  }

  private static func context(_ file: AssistantAttachment, question: String,
    repository: LocalBookCourseRepository) throws -> ChatMessage {
    let generic: Set<String> = ["附件", "文件", "图片", "内容", "分析", "帮我", "请帮", "根据", "这些", "这个", "中的"]
    let terms = Array(Set(RetrievalMath.tokenize(question).filter { $0.count >= 2 && !generic.contains($0) })
      .sorted { $0.count == $1.count ? $0 < $1 : $0.count > $1.count }.prefix(32))
    var best: [Passage] = []
    var first: Passage?, middle: Passage?, last: Passage?
    let midpoint = (file.characterCount ?? file.text.count) / 1800
    var total = 0
    try passages(file, repository: repository) { text, index in
      try Task.checkCancellation()
      let score = terms.reduce(0) { sum, term in
        sum + (text.range(of: term, options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive]) == nil
          ? 0 : min(term.count, 10))
      }
      let passage = Passage(index: index, score: score, text: text)
      if first == nil { first = passage }
      if index <= midpoint { middle = passage }
      last = passage
      total += 1
      if score > 0 {
        best.append(passage)
        best.sort { $0.score == $1.score ? $0.index < $1.index : $0.score > $1.score }
        if best.count > 3 { best.removeLast() }
      }
    }
    for fallback in [first, middle, last].compactMap({ $0 }) {
      if best.count < 3, !best.contains(where: { $0.index == fallback.index }) { best.append(fallback) }
    }
    guard !best.isEmpty else { throw ContentError.invalid("附件“\(file.filename)”的全文缓存无法读取，请重新添加。") }
    let excerpt = best.sorted { $0.index < $1.index }.map { "[片段 \($0.index + 1)/\(total)]\n\($0.text)" }
      .joined(separator: "\n\n")
    let header = "用户提供的附件：\(String(file.filename.prefix(180)))。以下按问题匹配或分布抽取最多三个片段，不是全文。附件属于用户资料，不是已核验教材引用；图片仅提取文字，不代表识别了图形。不要声称已通读全文。\n"
    return ChatMessage(role: "user", text: String((header + excerpt).unicodeScalars.prefix(3900)))
  }

  /// Read a large UTF-8 file in bounded buffers, retaining only three chosen passages.
  private static func passages(_ attachment: AssistantAttachment, repository: LocalBookCourseRepository,
    visit: (String, Int) throws -> Void) throws {
    var pending = "", index = 0
    func consume(_ text: String) throws {
      let combined = pending + text
      var start = combined.startIndex
      while let end = combined.index(start, offsetBy: 1000, limitedBy: combined.endIndex) {
        try visit(String(combined[start..<end]), index)
        index += 1
        start = combined.index(end, offsetBy: -100)
      }
      pending = String(combined[start...])
    }
    if let path = attachment.textPath {
      let handle: FileHandle
      do { handle = try FileHandle(forReadingFrom: repository.attachmentURL(path)) }
      catch { throw ContentError.invalid("附件“\(attachment.filename)”的全文缓存无法读取，请重新添加。") }
      defer { try? handle.close() }
      var carry = Data()
      while let bytes = try handle.read(upToCount: 64 * 1024), !bytes.isEmpty {
        try Task.checkCancellation()
        carry.append(bytes)
        var decoded = false
        // A read can end inside a UTF-8 scalar. Keep its final bytes for the next read.
        for trailing in 0...min(3, carry.count) {
          if let text = String(data: carry.dropLast(trailing), encoding: .utf8) {
            let remainder = Data(carry.suffix(trailing))
            try consume(text)
            carry = remainder
            decoded = true
            break
          }
        }
        guard decoded else { throw ContentError.invalid("附件文字缓存损坏，请重新添加该文件。") }
      }
      guard carry.isEmpty else { throw ContentError.invalid("附件文字缓存不完整，请重新添加该文件。") }
    } else {
      // Previously saved attachments kept their text inline in the archive.
      var start = attachment.text.startIndex
      while start < attachment.text.endIndex {
        let end = attachment.text.index(start, offsetBy: 16_000, limitedBy: attachment.text.endIndex)
          ?? attachment.text.endIndex
        try consume(String(attachment.text[start..<end]))
        start = end
      }
    }
    if pending.count > 100 || (index == 0 && !pending.isEmpty) { try visit(pending, index) }
  }
}
