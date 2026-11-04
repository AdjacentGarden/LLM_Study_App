import CoreText
import Foundation
import ImageIO
import UIKit

/// All content is copied into the native bundle. No source-repository path is used at runtime.
enum ContentBundle {
  /// Fixed data is opt-in for Debug UI tests; Release always uses real storage/services.
  static var fixtureMode: Bool {
    #if DEBUG
      ProcessInfo.processInfo.arguments.contains("--fixture")
    #else
      false
    #endif
  }

  static var root: URL { Bundle.main.resourceURL! }
  static func registerMaterialURL(_ url: URL) { LocalMaterialURLs.register(url) }
  static func url(_ path: String) -> URL? {
    if let approved = LocalMaterialURLs.url(path) { return approved }
    let relative = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    guard !relative.split(separator: "/").contains("..") else { return nil }
    for base in [
      root.appendingPathComponent("Content"), root,
      root.appendingPathComponent("Resources/Content"),
    ] {
      let candidate = base.appendingPathComponent(relative)
      if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
    }
    return nil
  }
  static func data(_ path: String) throws -> Data {
    if let url = url(path) { return try Data(contentsOf: url) }
    for prefix in ["", "Resources/"] {
      let candidate = root.appendingPathComponent(prefix + path)
      if FileManager.default.fileExists(atPath: candidate.path) {
        return try Data(contentsOf: candidate)
      }
    }
    throw ContentError.missing(path)
  }
  static func load<T: Decodable>(_ type: T.Type, name: String) throws -> T {
    try JSONDecoder().decode(type, from: data("Data/" + name + ".json"))
  }
  static func image(_ path: String) -> UIImage? {
    guard let url = url(path), let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let cg = CGImageSourceCreateImageAtIndex(source, 0, nil)
    else { return nil }
    return UIImage(cgImage: cg)
  }
  static func registerFonts() {
    for name in ["BookCourseNotoSansSC-Regular", "BookCourseNotoSansSC-Bold"] {
      guard let u = Bundle.main.url(forResource: name, withExtension: "ttf", subdirectory: "Fonts")
      else { continue }
      CTFontManagerRegisterFontsForURL(u as CFURL, .process, nil)
    }
  }
}
enum ContentError: LocalizedError {
  case missing(String)
  case invalid(String)
  case storage(String)
  var errorDescription: String? {
    switch self {
    case .missing(let s): "资源缺失：\(s)"
    case .invalid(let s): s
    case .storage(let s): "保存失败：\(s)"
    }
  }
}

/// Only checksummed material files explicitly registered by the library can be
/// loaded as file URLs. Model text and remote asset URLs cannot grant this access.
private enum LocalMaterialURLs {
  private static let lock = NSLock()
  nonisolated(unsafe) private static var approved: [String: URL] = [:]
  static func register(_ url: URL) {
    guard url.isFileURL, FileManager.default.fileExists(atPath: url.path) else { return }
    lock.lock(); defer { lock.unlock() }
    approved[url.absoluteString] = url
  }
  static func url(_ path: String) -> URL? {
    lock.lock(); defer { lock.unlock() }
    return approved[path]
  }
}
