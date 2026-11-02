import Foundation

/// Ephemeral feedback; never persisted with course or account data.
struct ToastNotice: Identifiable, Equatable {
  enum Kind: String { case success, info, warning, error, loading }

  enum Timing {
    static let appearance: TimeInterval = 0.24
    static let hold: TimeInterval = 3
    static let disappearance: TimeInterval = 0.20
  }

  let id = UUID()
  let title: String
  let subtitle: String?
  let kind: Kind

  var announcement: String {
    [title, subtitle].compactMap { $0 }.joined(separator: "。")
  }
}

#if DEBUG
extension AppStore {
  /// Opt-in recordings use the existing isolated fixture store and the real HUD.
  func playToastGalleryIfRequested() async {
    guard ContentBundle.fixtureMode, repository.includesFixtureContent,
      ProcessInfo.processInfo.arguments.contains("--fixture-toast-gallery") else { return }
    let scenarios: [(String, String, ToastNotice.Kind, String?)] = [
      ("success", "已保存到学习笔记", .success, nil),
      ("info", "此内容暂无原文页", .info, nil),
      ("warning", "请先选择教材", .warning, nil),
      ("error", "保存失败，请重试", .error, nil),
      ("loading", "正在保存笔记…", .loading, nil),
      ("detail", "课程已移除", .success, "原资料和笔记仍保留"),
    ]
    var events: [String: Double] = [:]
    let eventFile = repository.directory.appendingPathComponent("toast-gallery-events.json")
    func mark(_ name: String) {
      events[name] = Date().timeIntervalSince1970
      if let data = try? JSONEncoder().encode(events) {
        try? data.write(to: eventFile, options: .atomic)
      }
    }
    do {
      while isLoading { try await Task.sleep(for: .milliseconds(100)) }
      try await Task.sleep(for: .seconds(1))
      for (name, title, kind, subtitle) in scenarios {
        mark(name)
        showToast(title, kind: kind, subtitle: subtitle)
        if kind == .loading {
          try await Task.sleep(for: .milliseconds(3800))
          mark("loading-complete")
          showToast("已保存到学习笔记", kind: .success)
        }
        try await Task.sleep(for: .milliseconds(4500))
      }
      mark("finished")
    } catch { dismissToast() }
  }
}
#endif
