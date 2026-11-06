import OSLog
import UIKit
import SwiftUI

/// iPadOS 26+ needs a scene-level orientation-lock preference in addition to
/// the app's portrait mask. Preserve the existing SwiftUI host as a child.
struct PortraitSceneLock: UIViewRepresentable {
  func makeUIView(context: Context) -> LockInstallerView { LockInstallerView() }
  func updateUIView(_ view: LockInstallerView, context: Context) { view.installIfNeeded() }

  final class LockInstallerView: UIView {
    override func didMoveToWindow() {
      super.didMoveToWindow()
      installIfNeeded()
    }

    func installIfNeeded() {
      guard UIDevice.current.userInterfaceIdiom == .pad, let window else { return }
      DispatchQueue.main.async { [weak window] in
        guard let window, let root = window.rootViewController,
          !(root is PortraitSceneController) else { return }
        window.rootViewController = PortraitSceneController(content: root)
      }
    }
  }
}

private final class PortraitSceneController: UIViewController {
  private let content: UIViewController

  init(content: UIViewController) {
    self.content = content
    super.init(nibName: nil, bundle: nil)
  }
  required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

  override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .portrait }
  override var preferredInterfaceOrientationForPresentation: UIInterfaceOrientation { .portrait }
  override var shouldAutorotate: Bool { false }
  @available(iOS 26.0, *)
  override var prefersInterfaceOrientationLocked: Bool { true }
  override var childForStatusBarHidden: UIViewController? { content }
  override var childForStatusBarStyle: UIViewController? { content }
  override var childForHomeIndicatorAutoHidden: UIViewController? { content }
  override var childForScreenEdgesDeferringSystemGestures: UIViewController? { content }

  override func viewDidLoad() {
    super.viewDidLoad()
    view.backgroundColor = .systemBackground
    addChild(content)
    content.view.translatesAutoresizingMaskIntoConstraints = false
    view.addSubview(content.view)
    NSLayoutConstraint.activate([
      content.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
      content.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
      content.view.topAnchor.constraint(equalTo: view.topAnchor),
      content.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
    ])
    content.didMove(toParent: self)
  }

  override func viewDidAppear(_ animated: Bool) {
    super.viewDidAppear(animated)
    setNeedsUpdateOfSupportedInterfaceOrientations()
    if #available(iOS 26.0, *) { setNeedsUpdateOfPrefersInterfaceOrientationLocked() }
    if let scene = view.window?.windowScene, scene.interfaceOrientation != .portrait {
      scene.requestGeometryUpdate(.iOS(interfaceOrientations: .portrait))
    }
  }
}

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
