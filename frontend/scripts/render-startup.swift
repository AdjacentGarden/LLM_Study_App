// Rebuild the static iOS launch artwork from the live SwiftUI composition:
// swiftc App/StartupView.swift scripts/render-startup.swift -o /tmp/render-startup
// /tmp/render-startup /absolute/path/to/frontend
import AppKit
import CoreText
import SwiftUI

@main
struct RenderStartup {
  @MainActor static func main() throws {
    let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
    let font = root.appendingPathComponent("Resources/Fonts/InterVariable.ttf")
    CTFontManagerRegisterFontsForURL(font as CFURL, .process, nil)
    let lockup = StartupLockup(image: { name in
      let filename = name == "StartupCloudBase" ? "cloud-base.png" : "cloud-readers.png"
      let path = root.appendingPathComponent("Resources/Assets.xcassets/\(name).imageset/\(filename)")
      return Image(nsImage: NSImage(contentsOf: path)!)
    }, englishOpacity: 0, chineseOpacity: 0)
    // Match the animation's first frame: the resting icon, before text fades in.
    // Include shadow overflow on the launch screen's white surface; no device chrome.
    let renderer = ImageRenderer(content: lockup.frame(width: 270, height: 391).background(Color.white))
    renderer.scale = 3
    renderer.isOpaque = true
    guard let cgImage = renderer.cgImage,
      let data = NSBitmapImageRep(cgImage: cgImage).representation(using: .png, properties: [:])
    else { fatalError("Could not render launch artwork") }
    let output = root.appendingPathComponent("Resources/Assets.xcassets/StartupLockup.imageset")
    try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
    try data.write(to: output.appendingPathComponent("startup-lockup@3x.png"))
    try """
    {"images":[{"idiom":"universal","scale":"3x","filename":"startup-lockup@3x.png"}],"info":{"version":1,"author":"xcode"}}
    """.write(to: output.appendingPathComponent("Contents.json"), atomically: true, encoding: .utf8)
  }
}
