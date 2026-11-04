import OSLog
import UIKit

/// Keep visual fixtures under the same portrait-only policy as the application.
@MainActor enum FixtureOrientation {
  private static let logger = Logger(
    subsystem: "com.littleri.bookcourseai.swiftui", category: "visual-fixture")
  static func apply() {
    let arguments = ProcessInfo.processInfo.arguments
    guard ContentBundle.fixtureMode,
      let index = arguments.firstIndex(of: "--fixture-orientation"),
      arguments.indices.contains(index + 1), arguments[index + 1] == "portrait"
    else { return } // Legacy landscape requests cannot override the app policy.
    let mask: UIInterfaceOrientationMask = .portrait
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
