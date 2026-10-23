import CoreText
import UIKit

/// Renders the selected, already persisted learning material; it never fabricates content.
@MainActor enum LearningPDFExporter {
  static func write(title: String, sections: [(String, String)]) throws -> URL {
    let nonempty = sections.filter { !$0.1.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    guard !nonempty.isEmpty else { throw ContentError.invalid("所选模块还没有可导出的内容。") }
    let style = NSMutableParagraphStyle()
    style.lineSpacing = 5
    style.paragraphSpacing = 10
    let text = NSMutableAttributedString(
      string: title + "\n\n",
      attributes: [.font: UIFont.boldSystemFont(ofSize: 21), .foregroundColor: UIColor.black])
    for (heading, body) in nonempty {
      text.append(NSAttributedString(string: heading + "\n", attributes: [
        .font: UIFont.boldSystemFont(ofSize: 15), .foregroundColor: UIColor.black,
        .paragraphStyle: style,
      ]))
      text.append(NSAttributedString(string: body + "\n\n", attributes: [
        .font: UIFont.systemFont(ofSize: 12), .foregroundColor: UIColor.black,
        .paragraphStyle: style,
      ]))
    }
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("LearningExports")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let url = directory.appendingPathComponent("学习笔记-" + UUID().uuidString + ".pdf")
    let bounds = CGRect(x: 0, y: 0, width: 595, height: 842)
    let framesetter = CTFramesetterCreateWithAttributedString(text)
    var position = 0
    var invalidLayout = false
    do {
      try UIGraphicsPDFRenderer(bounds: bounds).writePDF(to: url) { context in
        while position < text.length {
          context.beginPage()
          let cg = context.cgContext
          cg.saveGState()
          cg.translateBy(x: 0, y: bounds.height)
          cg.scaleBy(x: 1, y: -1)
          cg.textMatrix = .identity
          let path = CGPath(rect: bounds.insetBy(dx: 40, dy: 45), transform: nil)
          let frame = CTFramesetterCreateFrame(framesetter, CFRange(location: position, length: 0), path, nil)
          let visible = CTFrameGetVisibleStringRange(frame)
          guard visible.length > 0 else {
            cg.restoreGState()
            invalidLayout = true
            break
          }
          CTFrameDraw(frame, cg)
          cg.restoreGState()
          position += visible.length
        }
      }
      guard !invalidLayout, position == text.length else {
        throw ContentError.invalid("PDF 排版失败，请减少所选内容后重试。")
      }
      return url
    } catch {
      try? FileManager.default.removeItem(at: url)
      throw error
    }
  }
}
