import CoreGraphics
import Foundation
import ImageIO
import PDFKit
import Vision

/// Offline source extraction on the iPhone/iPad running this app. Vision OCR is
/// independent of Apple Intelligence and never submits the document to a server.
enum AppleDocumentOCR {
  static let languages = ["zh-Hans", "en-US"]
  static let maximumDimension = 2400
  static let supportedExtensions: Set<String> = ["pdf", "png", "jpg", "jpeg", "webp", "gif", "heic", "heif"]

  struct Capability: Sendable, Equatable {
    var available: Bool
    var unavailableReason: String?
    var supportedLanguages: [String]
    var requestRevision: Int
  }

  struct Progress: Sendable, Equatable {
    enum Phase: String, Sendable { case readingTextLayer, recognizing, completed }
    var completedPages: Int
    var totalPages: Int
    /// The one-based physical page, including pages without recognized text.
    var currentPage: Int
    var phase: Phase
  }

  enum Failure: LocalizedError {
    case unavailable(String)
    case invalid(String)
    case recognition(String)
    var errorDescription: String? {
      switch self {
      case .unavailable(let reason), .invalid(let reason): return reason
      case .recognition(let reason): return "本机文字识别未完成：\(reason)。原文件已保留，可稍后重试。"
      }
    }
  }

  static func capability() -> Capability { VisionDocumentRecognizer().capability() }

  static func extract(
    url: URL, bookID: String, filename: String,
    onProgress: @escaping @Sendable (Progress) -> Void = { _ in }
  ) async throws -> SourceContent {
    try await extract(
      url: url, bookID: bookID, filename: filename,
      recognizer: VisionDocumentRecognizer(), onProgress: onProgress)
  }

  // The recognizer seam keeps page mapping, cancellation and persistence tests
  // deterministic without replacing the actual production Vision implementation.
  static func extract(
    url: URL, bookID: String, filename: String, recognizer: any AppleDocumentRecognizing,
    onProgress: @escaping @Sendable (Progress) -> Void = { _ in }
  ) async throws -> SourceContent {
    try Task.checkCancellation()
    let cancellation = AppleDocumentOCRCancellation()
    let worker = Task.detached(priority: .userInitiated) {
      try extractSynchronously(
        url: url, bookID: bookID, filename: filename, recognizer: recognizer,
        cancellation: cancellation, onProgress: onProgress)
    }
    return try await withTaskCancellationHandler {
      let source = try await worker.value
      try Task.checkCancellation()
      return source
    } onCancel: {
      cancellation.cancel()
      worker.cancel()
    }
  }

  private struct PageText {
    var text: String
    var usedVision: Bool
  }

  private static func extractSynchronously(
    url: URL, bookID: String, filename: String, recognizer: any AppleDocumentRecognizing,
    cancellation: AppleDocumentOCRCancellation,
    onProgress: @Sendable (Progress) -> Void
  ) throws -> SourceContent {
    try cancellation.check()
    guard url.isFileURL else { throw Failure.invalid("本机识别只能读取已保存的本地文件") }
    let accessed = url.startAccessingSecurityScopedResource()
    defer { if accessed { url.stopAccessingSecurityScopedResource() } }
    let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
    guard values.isRegularFile == true, let bytes = values.fileSize,
      bytes > 0, bytes <= 100 * 1024 * 1024
    else { throw Failure.invalid("请选择非空且不超过 100 MB 的本地 PDF 或图片") }
    let ext = (filename as NSString).pathExtension.lowercased()
    guard supportedExtensions.contains(ext) else {
      throw Failure.invalid("Apple 本机文字识别支持 PDF 和图片；原文件已保留")
    }
    let capability = recognizer.capability()
    guard capability.available else {
      throw Failure.unavailable(capability.unavailableReason ?? "此设备暂不支持中英文本机文字识别")
    }
    try cancellation.check()
    let document: PDFDocument?
    let pageCount: Int
    if ext == "pdf" {
      guard let pdf = PDFDocument(url: url), !pdf.isLocked, pdf.pageCount > 0 else {
        throw Failure.invalid("PDF 无法读取或需要密码，原文件已保留")
      }
      document = pdf
      pageCount = pdf.pageCount
    } else {
      document = nil
      pageCount = 1
    }
    var chunks: [SourceChunk] = []
    for index in 0..<pageCount {
      try cancellation.check()
      let pageNumber = index + 1
      // Release rendered pixels and Vision observations before loading the next
      // page. Only the recognized text survives this page's autorelease pool.
      let pageText = try autoreleasepool { () throws -> PageText in
        if let document {
          onProgress(.init(completedPages: index, totalPages: pageCount,
                           currentPage: pageNumber, phase: .readingTextLayer))
          guard let page = document.page(at: index) else {
            throw Failure.invalid("无法读取 PDF 第 \(pageNumber) 页，原文件已保留")
          }
          if let text = page.string?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty {
            return PageText(text: text, usedVision: false)
          }
          try cancellation.check()
          onProgress(.init(completedPages: index, totalPages: pageCount,
                           currentPage: pageNumber, phase: .recognizing))
          let image = try render(page)
          return PageText(text: try recognizer.recognize(image, cancellation: cancellation),
                          usedVision: true)
        }
        onProgress(.init(completedPages: index, totalPages: pageCount,
                         currentPage: pageNumber, phase: .recognizing))
        let image = try loadImage(url)
        return PageText(text: try recognizer.recognize(image, cancellation: cancellation),
                        usedVision: true)
      }
      try cancellation.check()
      let metadata: [String: JSONValue] = [
        "filename": .string(filename),
        "parser": .string(pageText.usedVision ? "apple-vision-ios" : "pdfkit-text"),
        "engine": .string(pageText.usedVision ? "Apple Vision VNRecognizeTextRequest" : "PDFKit text layer"),
        "execution_device": .string("ios-device"), "network_required": .bool(false),
        "locator_kind": .string(ext == "pdf" ? "pdf_page" : "image"),
        "physical_page_verified": .bool(ext == "pdf"),
        "recognition_languages": .array(pageText.usedVision ? languages.map(JSONValue.string) : []),
        "recognition_level": pageText.usedVision ? .string("accurate") : .null,
        "recognition_revision": pageText.usedVision ? .number(Double(capability.requestRevision)) : .null,
      ]
      var cursor = pageText.text.startIndex
      var part = 0
      while cursor < pageText.text.endIndex {
        try cancellation.check()
        let end = pageText.text.index(cursor, offsetBy: 1200, limitedBy: pageText.text.endIndex)
          ?? pageText.text.endIndex
        chunks.append(SourceChunk(
          chunk_id: "\(bookID):p\(pageNumber):\(part)", book_id: bookID, chapter_id: "",
          page_start: pageNumber, page_end: pageNumber, content_type: "text",
          text: String(pageText.text[cursor..<end]), asset_ids: [], key_concepts: [],
          source_metadata: metadata))
        cursor = end
        part += 1
      }
      onProgress(.init(completedPages: pageNumber, totalPages: pageCount,
                       currentPage: pageNumber, phase: .completed))
    }
    try cancellation.check()
    let summary = SourceSummary(
      book_id: bookID, title: (filename as NSString).deletingPathExtension, filename: filename,
      status: "source_ready", page_count: pageCount, chapter_count: 0, chunk_count: chunks.count,
      asset_count: 0, average_confidence: 0, updated_at: Date().timeIntervalSince1970,
      content_mode: "local_original", directory_unit_label: "资料原文",
      next_title: chunks.isEmpty ? "本机识别完成，未检测到可读文字；原文件可阅读，尚未生成教学内容"
        : "本机原文可阅读，尚未识别教材章节或生成教学内容")
    return SourceContent(
      summary: summary, chapters: [], lessons: [], chunks: chunks, assets: [], flashcards: [],
      quizzes: [], plan: StudyPlan(user_id: "", book_id: bookID, days: 0, daily_minutes: 0, tasks: []))
  }

  private static func render(_ page: PDFPage) throws -> CGImage {
    guard let pdf = page.pageRef else { throw Failure.invalid("PDF 页面无法渲染，原文件已保留") }
    let bounds = pdf.getBoxRect(.mediaBox)
    guard bounds.width.isFinite, bounds.height.isFinite, bounds.width > 0, bounds.height > 0 else {
      throw Failure.invalid("PDF 页面尺寸无效，原文件已保留")
    }
    let rotated = abs(pdf.rotationAngle % 180) == 90
    let width = rotated ? bounds.height : bounds.width
    let height = rotated ? bounds.width : bounds.height
    let scale = min(200.0 / 72.0, CGFloat(maximumDimension) / max(width, height))
    let pixelWidth = max(1, Int(ceil(width * scale)))
    let pixelHeight = max(1, Int(ceil(height * scale)))
    guard let context = CGContext(
      data: nil, width: pixelWidth, height: pixelHeight, bitsPerComponent: 8, bytesPerRow: 0,
      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { throw Failure.invalid("没有足够内存渲染 PDF 页面，请稍后重试") }
    let rectangle = CGRect(x: 0, y: 0, width: pixelWidth, height: pixelHeight)
    context.setFillColor(CGColor(gray: 1, alpha: 1))
    context.fill(rectangle)
    context.concatenate(pdf.getDrawingTransform(.mediaBox, rect: rectangle, rotate: 0, preserveAspectRatio: true))
    context.drawPDFPage(pdf)
    guard let image = context.makeImage() else { throw Failure.invalid("PDF 页面渲染未完成") }
    return image
  }

  private static func loadImage(_ url: URL) throws -> CGImage {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      CGImageSourceGetCount(source) > 0,
      let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: maximumDimension,
        kCGImageSourceShouldCacheImmediately: true,
      ] as CFDictionary)
    else { throw Failure.invalid("图片无法读取，原文件已保留") }
    return image
  }
}

protocol AppleDocumentRecognizing: Sendable {
  func capability() -> AppleDocumentOCR.Capability
  func recognize(_ image: CGImage, cancellation: AppleDocumentOCRCancellation) throws -> String
}

/// Cancelling the parent Task also cancels an in-flight synchronous Vision request.
final class AppleDocumentOCRCancellation: @unchecked Sendable {
  private let lock = NSLock()
  private var cancelled = false
  private var request: VNRecognizeTextRequest?
  func cancel() {
    lock.lock()
    cancelled = true
    let active = request
    lock.unlock()
    active?.cancel()
  }
  func check() throws {
    try Task.checkCancellation()
    lock.lock()
    let stopped = cancelled
    lock.unlock()
    if stopped { throw CancellationError() }
  }
  func install(_ request: VNRecognizeTextRequest?) throws {
    lock.lock()
    let stopped = cancelled
    if !stopped { self.request = request }
    lock.unlock()
    if stopped {
      request?.cancel()
      throw CancellationError()
    }
  }
}

private struct VisionDocumentRecognizer: AppleDocumentRecognizing {
  func capability() -> AppleDocumentOCR.Capability {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    do {
      let supported = try request.supportedRecognitionLanguages()
      let missing = AppleDocumentOCR.languages.filter { !supported.contains($0) }
      return .init(available: missing.isEmpty,
                   unavailableReason: missing.isEmpty ? nil : "此设备本机 OCR 不支持所需语言：\(missing.joined(separator: "、"))。文件不会上传。",
                   supportedLanguages: supported, requestRevision: request.revision)
    } catch {
      return .init(available: false, unavailableReason: "无法检查此设备的 Apple 本机文字识别能力：\(error.localizedDescription)。文件不会上传。",
                   supportedLanguages: [], requestRevision: request.revision)
    }
  }
  func recognize(_ image: CGImage, cancellation: AppleDocumentOCRCancellation) throws -> String {
    try cancellation.check()
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = AppleDocumentOCR.languages
    request.usesLanguageCorrection = true
    try cancellation.install(request)
    defer { try? cancellation.install(nil) }
    do {
      try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
      try cancellation.check()
      return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        .joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    } catch {
      try cancellation.check()
      throw AppleDocumentOCR.Failure.recognition(error.localizedDescription)
    }
  }
}
