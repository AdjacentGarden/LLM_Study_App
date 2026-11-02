import AlertToast
import SwiftUI

/// Preserve the AlertToast HUD layout with native regular Liquid Glass on iOS 26.
/// AppStore owns timing; older systems use AlertToast's original material.
private struct ToastHUD: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  let isEnabled: Bool

  var body: some View {
    Group {
      if isEnabled, let notice = store.toast {
        surface(notice)
        .padding(.horizontal, 20)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(notice.announcement)
        .accessibilityValue(notice.kind.rawValue)
        .accessibilityIdentifier("app.toast")
        .transition(
          reduceMotion ? .opacity : .move(edge: .top).combined(with: .opacity))
      }
    }
    .allowsHitTesting(false)
    .transaction { if reduceMotion { $0.animation = nil } }
  }

  @ViewBuilder private func surface(_ notice: ToastNotice) -> some View {
    if #available(iOS 26.0, *) {
      HStack(spacing: 16) {
        if notice.kind == .loading {
          ProgressView().controlSize(.regular)
            .frame(width: 20, height: 20)
        } else {
          Image(systemName: symbol(notice.kind))
            .resizable().scaledToFit().frame(width: 20, height: 20)
            .foregroundStyle(symbolColor(notice.kind))
        }
        VStack(alignment: .leading, spacing: 2) {
          Text(notice.title).appFont(.body, weight: .semibold).multilineTextAlignment(.center)
          if let subtitle = notice.subtitle {
            Text(subtitle).appFont(.supporting).opacity(0.7).multilineTextAlignment(.center)
          }
        }
      }
      .fixedSize(horizontal: false, vertical: true)
      .padding(.horizontal, 24).padding(.vertical, 8)
      .frame(minHeight: 50)
      .glassEffect(.regular, in: Capsule())
      .padding(.top, 16)
    } else {
      AlertToast(
        displayMode: .hud, type: alertType(notice.kind), title: notice.title,
        subTitle: notice.subtitle)
    }
  }

  private func symbol(_ kind: ToastNotice.Kind) -> String {
    switch kind {
    case .success: "checkmark"
    case .info: "info.circle"
    case .warning: "exclamationmark.triangle"
    case .error: "xmark"
    case .loading: ""
    }
  }

  private func symbolColor(_ kind: ToastNotice.Kind) -> Color {
    switch kind {
    case .success: .green
    case .info: .blue
    case .warning: .orange
    case .error: .red
    case .loading: .primary
    }
  }

  private func alertType(_ kind: ToastNotice.Kind) -> AlertToast.AlertType {
    switch kind {
    case .success: .complete(.green)
    case .info: .systemImage("info.circle", .blue)
    case .warning: .systemImage("exclamationmark.triangle", .orange)
    case .error: .error(.red)
    case .loading: .loading
    }
  }
}

extension View {
  func toastHUD(isEnabled: Bool = true) -> some View {
    overlay(alignment: .top) { ToastHUD(isEnabled: isEnabled) }
  }
}
