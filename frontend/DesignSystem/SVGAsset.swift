import Foundation
import SwiftUI

/// Small SVG renderer for the exact path/circle/rect/line artwork exported from the source.
/// No network, WebView, icon substitution or rasterization is involved.
struct SVGAsset: View {
  let path: String
  var tint: Color? = nil
  var body: some View {
    let document = SVGCache.document(path)
    Canvas { context, size in
      guard let document else { return }
      let scale = min(size.width / document.size.width, size.height / document.size.height)
      context.translateBy(
        x: (size.width - document.size.width * scale) / 2,
        y: (size.height - document.size.height * scale) / 2)
      context.scaleBy(x: scale, y: scale)
      for item in document.items {
        if let fill = item.fill {
          context.fill(
            Path(item.path), with: .color((tint ?? fill).opacity(item.opacity * item.fillOpacity)))
        }
        if let stroke = item.stroke, item.width > 0 {
          context.stroke(
            Path(item.path), with: .color((tint ?? stroke).opacity(item.opacity)),
            style: StrokeStyle(lineWidth: item.width, lineCap: item.cap, lineJoin: .round))
        }
      }
    }.accessibilityHidden(true)
  }
}
private enum SVGCache {
  static var values: [String: SVGDocument] = [:]
  static func document(_ path: String) -> SVGDocument? {
    if let cached = values[path] { return cached }
    guard let data = try? ContentBundle.data(path) else { return nil }
    let delegate = SVGDocument()
    let parser = XMLParser(data: data)
    parser.delegate = delegate
    guard parser.parse() else { return nil }
    values[path] = delegate
    return delegate
  }
}
private final class SVGDocument: NSObject, XMLParserDelegate {
  struct Style {
    var fill: Color? = .black
    var stroke: Color?
    var width: CGFloat = 1
    var opacity: Double = 1
    var fillOpacity: Double = 1
    var cap: CGLineCap = .round
  }
  struct Item {
    let path: CGPath
    let fill: Color?
    let stroke: Color?
    let width: CGFloat
    let opacity: Double
    let fillOpacity: Double
    let cap: CGLineCap
  }
  var size = CGSize(width: 24, height: 24)
  var items: [Item] = []
  var styles: [Style] = [Style()]
  var transforms: [CGAffineTransform] = [.identity]
  func parser(
    _ parser: XMLParser, didStartElement name: String, namespaceURI: String?,
    qualifiedName: String?, attributes a: [String: String]
  ) {
    var style = styles.last ?? Style()
    if let fill = a["fill"] { style.fill = color(fill) }
    if let stroke = a["stroke"] { style.stroke = color(stroke) }
    if let width = a["stroke-width"].flatMap(Double.init) { style.width = width }
    if let opacity = a["opacity"].flatMap(Double.init) { style.opacity *= opacity }
    if let opacity = a["fill-opacity"].flatMap(Double.init) { style.fillOpacity = opacity }
    if let cap = a["stroke-linecap"] {
      style.cap = cap == "square" ? .square : cap == "butt" ? .butt : .round
    }
    styles.append(style)
    transforms.append(
      (transforms.last ?? .identity).concatenating(Self.transform(a["transform"] ?? "")))
    func number(_ key: String, _ fallback: CGFloat = 0) -> CGFloat {
      CGFloat(Double(a[key] ?? "") ?? Double(fallback))
    }
    if name == "svg", let box = a["viewBox"] {
      let values = box.split(whereSeparator: { $0 == " " || $0 == "," }).compactMap { Double($0) }
      if values.count == 4 { size = CGSize(width: values[2], height: values[3]) }
    }
    let p = CGMutablePath()
    switch name {
    case "path": if let d = a["d"] { p.addPath(SVGPath.parse(d)) }
    case "circle":
      let r = number("r")
      p.addEllipse(
        in: CGRect(x: number("cx") - r, y: number("cy") - r, width: r * 2, height: r * 2))
    case "ellipse":
      let rx = number("rx")
      let ry = number("ry")
      p.addEllipse(
        in: CGRect(x: number("cx") - rx, y: number("cy") - ry, width: rx * 2, height: ry * 2))
    case "rect":
      let r = CGRect(
        x: number("x"), y: number("y"), width: number("width"), height: number("height"))
      p.addRoundedRect(in: r, cornerWidth: number("rx"), cornerHeight: number("ry", number("rx")))
    case "line":
      p.move(to: CGPoint(x: number("x1"), y: number("y1")))
      p.addLine(to: CGPoint(x: number("x2"), y: number("y2")))
    case "polyline", "polygon":
      let values = (a["points"] ?? "").split(whereSeparator: { $0 == " " || $0 == "," }).compactMap
      { Double($0) }
      if values.count >= 2 {
        p.move(to: CGPoint(x: values[0], y: values[1]))
        for i in stride(from: 2, to: values.count - 1, by: 2) {
          p.addLine(to: CGPoint(x: values[i], y: values[i + 1]))
        }
        if name == "polygon" { p.closeSubpath() }
      }
    default: return
    }
    var transform = transforms.last ?? .identity
    items.append(
      Item(
        path: p.copy(using: &transform) ?? p, fill: style.fill, stroke: style.stroke,
        width: style.width, opacity: style.opacity, fillOpacity: style.fillOpacity, cap: style.cap))
  }
  func parser(
    _ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?
  ) {
    if styles.count > 1 { styles.removeLast() }
    if transforms.count > 1 { transforms.removeLast() }
  }
  static func transform(_ value: String) -> CGAffineTransform {
    let regex = try! NSRegularExpression(pattern: #"(rotate|translate|scale|matrix)\s*\(([^)]*)\)"#)
    var result = CGAffineTransform.identity
    for match in regex.matches(in: value, range: NSRange(value.startIndex..., in: value)) {
      let name = String(value[Range(match.range(at: 1), in: value)!])
      let numbers = value[Range(match.range(at: 2), in: value)!].split(whereSeparator: {
        $0 == " " || $0 == ","
      }).compactMap { Double($0) }
      guard let first = numbers.first else { continue }
      switch name {
      case "rotate":
        let x = numbers.count > 2 ? numbers[1] : 0
        let y = numbers.count > 2 ? numbers[2] : 0
        result = result.translatedBy(x: x, y: y).rotated(by: first * .pi / 180).translatedBy(
          x: -x, y: -y)
      case "translate":
        result = result.translatedBy(x: first, y: numbers.count > 1 ? numbers[1] : 0)
      case "scale": result = result.scaledBy(x: first, y: numbers.count > 1 ? numbers[1] : first)
      case "matrix":
        if numbers.count == 6 {
          result = result.concatenating(
            CGAffineTransform(
              a: numbers[0], b: numbers[1], c: numbers[2], d: numbers[3], tx: numbers[4],
              ty: numbers[5]))
        }
      default: break
      }
    }
    return result
  }
  func color(_ value: String) -> Color? {
    if value == "none" { return nil }
    if value == "white" { return .white }
    if value == "black" { return .black }
    if value == "currentColor" { return DS.text }
    let hex = value.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
    if let number = UInt(hex, radix: 16) {
      if hex.count == 3 {
        let r = (number >> 8) & 15
        let g = (number >> 4) & 15
        let b = number & 15
        return Color(hex: (r * 17 << 16) | (g * 17 << 8) | b * 17)
      }
      return Color(hex: number)
    }
    return nil
  }
}
enum SVGPath {
  static func parse(_ input: String) -> CGPath {
    let regex = try! NSRegularExpression(
      pattern: #"[a-zA-Z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"#)
    let tokens = regex.matches(in: input, range: NSRange(input.startIndex..., in: input)).map {
      String(input[Range($0.range, in: input)!])
    }
    var index = 0
    var command = "M"
    var previous = ""
    var point = CGPoint.zero
    var start = CGPoint.zero
    var control = CGPoint.zero
    let path = CGMutablePath()
    func isCommand(_ s: String) -> Bool { s.count == 1 && s.first!.isLetter }
    func read() -> CGFloat {
      guard index < tokens.count, let n = Double(tokens[index]) else { return 0 }
      index += 1
      return CGFloat(n)
    }
    while index < tokens.count {
      if isCommand(tokens[index]) {
        command = tokens[index]
        index += 1
      }
      let relative = command.lowercased() == command
      let upper = command.uppercased()
      let origin = point
      func coordinate() -> CGPoint {
        let x = read()
        let y = read()
        return CGPoint(x: x + (relative ? origin.x : 0), y: y + (relative ? origin.y : 0))
      }
      let count =
        ["M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0][upper]
        ?? -1
      guard count >= 0, index + count <= tokens.count else { break }
      if count > 0 && (index >= tokens.count || isCommand(tokens[index])) { break }
      switch upper {
      case "M":
        point = coordinate()
        path.move(to: point)
        start = point
        command = relative ? "l" : "L"
      case "L":
        point = coordinate()
        path.addLine(to: point)
      case "H":
        point.x = read() + (relative ? origin.x : 0)
        path.addLine(to: point)
      case "V":
        point.y = read() + (relative ? origin.y : 0)
        path.addLine(to: point)
      case "C":
        let c1 = coordinate()
        let c2 = coordinate()
        let end = coordinate()
        path.addCurve(to: end, control1: c1, control2: c2)
        control = c2
        point = end
      case "S":
        let c1 =
          (previous == "C" || previous == "S")
          ? CGPoint(x: 2 * point.x - control.x, y: 2 * point.y - control.y) : point
        let c2 = coordinate()
        let end = coordinate()
        path.addCurve(to: end, control1: c1, control2: c2)
        control = c2
        point = end
      case "Q":
        let c = coordinate()
        let end = coordinate()
        path.addQuadCurve(to: end, control: c)
        control = c
        point = end
      case "T":
        let c =
          (previous == "Q" || previous == "T")
          ? CGPoint(x: 2 * point.x - control.x, y: 2 * point.y - control.y) : point
        let end = coordinate()
        path.addQuadCurve(to: end, control: c)
        control = c
        point = end
      case "A":
        let rx = read()
        let ry = read()
        let rotation = read()
        let large = read() != 0
        let sweep = read() != 0
        let end = coordinate()
        addArc(
          path, from: point, to: end, rx: rx, ry: ry, rotation: rotation, large: large, sweep: sweep
        )
        point = end
      case "Z":
        path.closeSubpath()
        point = start
        command = "M"
      default: index = tokens.count
      }
      previous = upper
    }
    return path
  }
  // SVG endpoint-parameterized elliptical arcs converted into cubic Bezier segments.
  private static func addArc(
    _ path: CGMutablePath, from p0: CGPoint, to p1: CGPoint, rx inputX: CGFloat, ry inputY: CGFloat,
    rotation: CGFloat, large: Bool, sweep: Bool
  ) {
    var rx = abs(inputX)
    var ry = abs(inputY)
    guard rx > 0, ry > 0, p0 != p1 else {
      path.addLine(to: p1)
      return
    }
    let phi = rotation * .pi / 180
    let cp = cos(phi)
    let sp = sin(phi)
    let dx = (p0.x - p1.x) / 2
    let dy = (p0.y - p1.y) / 2
    let x = cp * dx + sp * dy
    let y = -sp * dx + cp * dy
    let lambda = x * x / (rx * rx) + y * y / (ry * ry)
    if lambda > 1 {
      rx *= sqrt(lambda)
      ry *= sqrt(lambda)
    }
    let numerator = max(0, rx * rx * ry * ry - rx * rx * y * y - ry * ry * x * x)
    let denominator = rx * rx * y * y + ry * ry * x * x
    let factor = (large == sweep ? CGFloat(-1) : 1) * sqrt(numerator / max(denominator, 0.000001))
    let cx1 = factor * rx * y / ry
    let cy1 = -factor * ry * x / rx
    let cx = cp * cx1 - sp * cy1 + (p0.x + p1.x) / 2
    let cy = sp * cx1 + cp * cy1 + (p0.y + p1.y) / 2
    let ux = (x - cx1) / rx
    let uy = (y - cy1) / ry
    let vx = (-x - cx1) / rx
    let vy = (-y - cy1) / ry
    let theta = atan2(uy, ux)
    var delta = atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    if !sweep && delta > 0 { delta -= 2 * .pi }
    if sweep && delta < 0 { delta += 2 * .pi }
    let segments = max(1, Int(ceil(abs(delta) / (.pi / 2))))
    let step = delta / CGFloat(segments)
    func transform(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
      CGPoint(x: cx + cp * rx * x - sp * ry * y, y: cy + sp * rx * x + cp * ry * y)
    }
    for i in 0..<segments {
      let a = theta + CGFloat(i) * step
      let b = a + step
      let k = 4 / 3 * tan((b - a) / 4)
      path.addCurve(
        to: transform(cos(b), sin(b)),
        control1: transform(cos(a) - k * sin(a), sin(a) + k * cos(a)),
        control2: transform(cos(b) + k * sin(b), sin(b) - k * cos(b)))
    }
  }
}
