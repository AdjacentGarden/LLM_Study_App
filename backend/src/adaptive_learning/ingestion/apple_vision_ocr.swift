// Local macOS OCR helper. Input PDF pixels and recognized text stay on this Mac.
import Foundation
import CoreGraphics
import Vision

enum OCRError: Error, CustomStringConvertible {
    case invalid(String)
    var description: String { switch self { case .invalid(let message): return message } }
}

func writeJSON(_ value: Any, to url: URL) throws {
    try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
        .write(to: url, options: .atomic)
}

func render(_ page: CGPDFPage, dpi: Double) throws -> CGImage {
    let box = page.getBoxRect(.cropBox)
    let rotated = abs(page.rotationAngle) % 180 == 90
    let widthPoints = rotated ? box.height : box.width
    let heightPoints = rotated ? box.width : box.height
    guard widthPoints.isFinite, heightPoints.isFinite,
          widthPoints > 0, heightPoints > 0 else {
        throw OCRError.invalid("PDF page has invalid dimensions")
    }
    let scale = min(dpi / 72, sqrt(30_000_000 / (widthPoints * heightPoints)))
    let width = max(1, Int(ceil(widthPoints * scale)))
    let height = max(1, Int(ceil(heightPoints * scale)))
    guard let context = CGContext(data: nil, width: width, height: height,
                                  bitsPerComponent: 8, bytesPerRow: width * 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
        throw OCRError.invalid("Cannot allocate PDF page bitmap")
    }
    let rect = CGRect(x: 0, y: 0, width: width, height: height)
    context.setFillColor(CGColor(gray: 1, alpha: 1))
    context.fill(rect)
    context.concatenate(page.getDrawingTransform(.cropBox, rect: rect,
                                                rotate: 0, preserveAspectRatio: true))
    context.drawPDFPage(page)
    guard let image = context.makeImage() else { throw OCRError.invalid("Cannot render PDF page") }
    return image
}

func main() throws {
    let arguments = CommandLine.arguments
    guard arguments.count == 7, arguments[1] == "--source",
          arguments[3] == "--output", arguments[5] == "--dpi",
          let dpi = Double(arguments[6]), dpi >= 72, dpi <= 420 else {
        throw OCRError.invalid("Usage: apple-vision-ocr --source PDF --output NEW_DIRECTORY --dpi 200")
    }
    let source = URL(fileURLWithPath: arguments[2])
    let output = URL(fileURLWithPath: arguments[4], isDirectory: true)
    guard let document = CGPDFDocument(source as CFURL), !document.isEncrypted,
          document.numberOfPages > 0 else { throw OCRError.invalid("Cannot open nonempty unencrypted PDF") }
    guard !FileManager.default.fileExists(atPath: output.path) else {
        throw OCRError.invalid("OCR output directory must not exist")
    }
    try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
    let started = Date()
    var blocks: [[String: Any]] = []
    var pageTimings: [[String: Any]] = []
    var emptyPages: [Int] = []
    var revision = 0
    var supportedLanguages: [String] = []
    for pageNumber in 1...document.numberOfPages {
        try autoreleasepool {
            let pageStarted = Date()
            guard let page = document.page(at: pageNumber) else {
                throw OCRError.invalid("PDF omitted page \(pageNumber)")
            }
            let image = try render(page, dpi: dpi)
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.usesLanguageCorrection = true
            supportedLanguages = try request.supportedRecognitionLanguages()
            guard request.recognitionLanguages.allSatisfy(supportedLanguages.contains) else {
                throw OCRError.invalid("Vision does not support configured Chinese and English languages")
            }
            revision = request.revision
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
            // Text-line order only. This does not classify columns or reconstruct tables.
            let observations = (request.results ?? []).sorted {
                let firstRow = Int(($0.boundingBox.midY * 1000).rounded() / 10)
                let secondRow = Int(($1.boundingBox.midY * 1000).rounded() / 10)
                if firstRow != secondRow { return firstRow > secondRow }
                return $0.boundingBox.minX < $1.boundingBox.minX
            }
            var lineCount = 0
            for observation in observations {
                guard let candidate = observation.topCandidates(1).first,
                      !candidate.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
                let box = observation.boundingBox
                blocks.append([
                    "type": "text", "text": candidate.string, "page_idx": pageNumber - 1,
                    "bbox": [box.minX * 1000, (1 - box.maxY) * 1000,
                             box.maxX * 1000, (1 - box.minY) * 1000],
                    "confidence": candidate.confidence, "ocr_backend": "apple-vision",
                    "ocr_line_id": "p\(pageNumber)-l\(lineCount + 1)"
                ])
                lineCount += 1
            }
            if lineCount == 0 { emptyPages.append(pageNumber) }
            pageTimings.append(["page_number": pageNumber, "line_count": lineCount,
                                "duration_seconds": Date().timeIntervalSince(pageStarted),
                                "render_width": image.width, "render_height": image.height])
        }
    }
    let duration = Date().timeIntervalSince(started)
    let capabilities: [String: Any] = [
        "engine": "apple-vision", "framework": "Vision", "api": "VNRecognizeTextRequest",
        "revision": revision, "recognition_languages": ["zh-Hans", "en-US"],
        "supported_recognition_languages": supportedLanguages, "recognition_level": "accurate",
        "render_dpi": dpi, "maximum_render_pixels": 30_000_000, "resident_gpu": false,
        "execution_provider": "Apple Vision automatic on-device compute", "network_required": false,
        "layout_detection": false, "table_structure": false, "formula_specialist": false,
        "reading_order": "top-to-bottom text lines; columns are not classified",
        "reading_order_quality_verified": false
    ]
    try writeJSON(blocks, to: output.appendingPathComponent("vision_content_list.json"))
    let markdown = (1...document.numberOfPages).map { number in
        let text = blocks.filter { ($0["page_idx"] as? Int) == number - 1 }
            .compactMap { $0["text"] as? String }.joined(separator: "\n")
        return "<!-- PDF page \(number) -->\n\n\(text)"
    }.joined(separator: "\n\n") + "\n"
    try markdown.write(to: output.appendingPathComponent("vision.md"), atomically: true, encoding: .utf8)
    try writeJSON(["status": "complete", "pages": document.numberOfPages,
                   "page_numbers": Array(1...document.numberOfPages), "empty_pages": emptyPages,
                   "duration_seconds": duration, "capabilities": capabilities,
                   "page_timings": pageTimings],
                  to: output.appendingPathComponent("fast_ocr_metadata.json"))
    try "Apple Vision completed \(document.numberOfPages) pages in \(duration) seconds.\n"
        .write(to: output.appendingPathComponent("vision.log"), atomically: true, encoding: .utf8)
    print("Apple Vision completed \(document.numberOfPages) pages in \(duration) seconds.")
}

do { try main() } catch {
    FileHandle.standardError.write(Data("Apple Vision OCR failed: \(error)\n".utf8))
    exit(1)
}
