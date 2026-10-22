import OSLog
import UIKit

/// App geometry requests are best effort. UI tests set physical simulator orientation
/// with XCUIDevice and assert the resulting window dimensions independently.
@MainActor enum FixtureOrientation {
  private static let logger = Logger(
    subsystem: "com.littleri.bookcourseai.swiftui", category: "visual-fixture")
  static func apply() {
    let arguments = ProcessInfo.processInfo.arguments
    guard arguments.contains("--fixture"),
      let index = arguments.firstIndex(of: "--fixture-orientation"),
      arguments.indices.contains(index + 1)
    else { return }
    let mask: UIInterfaceOrientationMask =
      arguments[index + 1] == "landscape" ? .landscapeLeft : .portrait
    Task { @MainActor in
      for delay in [600, 900, 1500] {
        try? await Task.sleep(for: .milliseconds(delay))
        guard !Task.isCancelled else { return }
        for case let scene as UIWindowScene in UIApplication.shared.connectedScenes
        where scene.activationState == .foregroundActive {
          scene.keyWindow?.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations()
          scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { error in
            logger.error(
              "Fixture orientation request failed: \(error.localizedDescription,privacy:.public)")
          }
        }
      }
    }
  }
}
