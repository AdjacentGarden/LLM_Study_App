import SwiftUI
import UIKit

@main struct BookCourseApp: App {
  @State private var store: AppStore?
  private var startupError: String?
  init() {
    ContentBundle.registerFonts()
    do {
      let args = ProcessInfo.processInfo.arguments
      let directory: URL?
      #if DEBUG
        let cloudSession = args.firstIndex(of: "--cloud-test-session").flatMap {
          args.indices.contains($0 + 1) ? args[$0 + 1] : nil
        }
      #else
        let cloudSession: String? = nil
      #endif
      if let i = args.firstIndex(of: "--data-directory"), args.indices.contains(i + 1) {
        directory = URL(fileURLWithPath: args[i + 1], isDirectory: true)
      } else if args.contains("--fixture") {
        let i = args.firstIndex(of: "--fixture-session")
        let session = i.flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil } ?? "default"
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(
          "VisualFixtures/" + session, isDirectory: true)
      } else if let cloudSession {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("CloudUITests/" + cloudSession, isDirectory: true)
      } else {
        directory = nil
      }
      self._store = State(
        initialValue: AppStore(repository: try LocalBookCourseRepository(directory: directory), cloud: CloudAPI.configured()))
    } catch { startupError = error.localizedDescription }
  }
  var body: some Scene {
    WindowGroup {
      if let store {
        AppRootView().environment(store).environment(
          \.bookCourseReduceMotion,
          ProcessInfo.processInfo.arguments.contains("--fixture-reduce-motion")
            || UIAccessibility.isReduceMotionEnabled
        ).preferredColorScheme(.light).task { await store.boot() }
      } else {
        VStack(spacing: 20) {
          Text("本地存储暂时无法打开").font(.title2)
          Text(startupError ?? "请检查设备存储空间后重新打开应用").multilineTextAlignment(.center)
        }.padding()
      }
    }
  }
}
