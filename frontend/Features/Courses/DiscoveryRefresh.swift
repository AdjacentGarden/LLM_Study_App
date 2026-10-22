import SwiftUI
import UIKit

enum DiscoveryRefreshPhase {
  case idle, loading, launching, failed, settling
}

/// The scene occupies only the space exposed by overscrolling, below the fixed search field.
struct DiscoveryRefreshScene: View {
  let distance: CGFloat
  let phase: DiscoveryRefreshPhase
  @Environment(\.bookCourseReduceMotion) private var reduceMotion

  private var height: CGFloat {
    max(0, distance, phase == .idle || phase == .settling ? 0 : 60)
  }

  // The PNGs retain their transparent margins. Anchor their visible lower edge,
  // rather than the canvas edge, to the bottom of the exposed refresh region.
  private let perchedSize = CGSize(width: 76, height: 76.0 * 941 / 1672)
  private let perchedBaseline = UnitPoint(x: 0.5, y: 798.0 / 941)
  private let launchBaseline = UnitPoint(x: 0.5, y: 1040.0 / 1254)

  var body: some View {
    TimelineView(.animation(minimumInterval: 1 / 30, paused: phase != .loading || reduceMotion)) {
      timeline in
      let progress = min(1, distance / 110)
      let breath = phase == .loading && !reduceMotion
        ? sin(timeline.date.timeIntervalSinceReferenceDate * 8) * 0.025 : 0
      GeometryReader { geometry in
        ZStack {
          LinearGradient(
            colors: [Color(hex: 0xDDF1FF), Color(hex: 0xEAE3FF)],
            startPoint: .top, endPoint: .bottom)

          AssetImage("/assets/brand/discovery/cloud-refresh-perched-v1.png")
            .frame(width: perchedSize.width, height: perchedSize.height)
            .scaleEffect(
              x: reduceMotion ? 1 : 1 + progress * 0.03,
              y: reduceMotion ? 1 : min(1, distance / 40) * (1 - progress * 0.04 + breath),
              anchor: perchedBaseline)
            .position(
              x: geometry.size.width / 2,
              y: height + perchedSize.height * (0.5 - perchedBaseline.y))
            .opacity(phase == .launching || phase == .settling ? 0 : 1)

          AssetImage("/assets/brand/discovery/cloud-refresh-v1.png")
            .frame(width: 64, height: 64)
            .background(alignment: .top) {
              if phase == .launching && !reduceMotion {
                HStack(spacing: 0) {
                  ForEach([0x8CCFFF, 0xAF9CFA, 0xF5B8DE], id: \.self) { color in
                    Rectangle().fill(Color(hex: UInt(color))).frame(width: 6)
                  }
                }
                .frame(height: height + 80)
                .mask(LinearGradient(colors: [.white, .clear], startPoint: .top, endPoint: .bottom))
                .offset(y: 48).transition(.opacity)
              }
            }
            .scaleEffect(
              x: reduceMotion || phase != .launching ? 1 : 0.72,
              y: reduceMotion || phase != .launching ? 1 : 1.35,
              anchor: launchBaseline)
            .position(
              x: geometry.size.width / 2,
              y: height + 64 * (0.5 - launchBaseline.y))
            .offset(y: phase == .launching && !reduceMotion ? -height - 80 : 0)
            .animation(
              reduceMotion || phase != .launching ? nil : .easeIn(duration: 0.42), value: phase)
            .opacity(phase == .launching ? 1 : 0)
        }
      }
    }
    .frame(height: height).clipped().accessibilityHidden(true)
  }
}

/// Keep UIKit's native release threshold, rubber-banding and cancellation behavior.
/// Only the artwork is replaced; no competing drag recognizer is added to the course grid.
struct DiscoveryRefreshBridge: UIViewRepresentable {
  let request: Int
  let onPull: (CGFloat) -> Void
  let onPhase: (DiscoveryRefreshPhase) -> Void
  let refresh: () async throws -> Void

  func makeCoordinator() -> Coordinator { Coordinator(self) }

  func makeUIView(context: Context) -> Probe {
    let probe = Probe()
    probe.isUserInteractionEnabled = false
    probe.attach = { [weak coordinator = context.coordinator] view in
      coordinator?.attach(from: view)
    }
    return probe
  }

  func updateUIView(_ uiView: Probe, context: Context) {
    context.coordinator.parent = self
    context.coordinator.attach(from: uiView)
    if context.coordinator.lastRequest != request {
      context.coordinator.lastRequest = request
      DispatchQueue.main.async { [weak coordinator = context.coordinator] in
        coordinator?.beginFromAccessibility()
      }
    }
  }

  static func dismantleUIView(_ uiView: Probe, coordinator: Coordinator) {
    uiView.attach = nil
    coordinator.detach()
  }

  final class Probe: UIView {
    var attach: ((UIView) -> Void)?
    override func didMoveToWindow() {
      super.didMoveToWindow()
      DispatchQueue.main.async { [weak self] in
        guard let self, self.window != nil else { return }
        self.attach?(self)
      }
    }
  }

  @MainActor final class Coordinator: NSObject {
    var parent: DiscoveryRefreshBridge
    var lastRequest: Int
    private weak var scroll: UIScrollView?
    private let control = UIRefreshControl()
    private var observation: NSKeyValueObservation?
    private var work: Task<Void, Never>?
    private var restingTop: CGFloat = 0
    private var settling = false

    init(_ parent: DiscoveryRefreshBridge) {
      self.parent = parent
      lastRequest = parent.request
      super.init()
      control.tintColor = .clear
      control.accessibilityLabel = "刷新课程"
      control.addTarget(self, action: #selector(refreshReleased), for: .valueChanged)
    }

    func attach(from view: UIView) {
      guard view.window != nil else { return }
      var ancestor = view.superview
      while let candidate = ancestor {
        if let candidate = candidate as? UIScrollView {
          guard scroll !== candidate else { return }
          detach()
          scroll = candidate
          let restingTop = candidate.adjustedContentInset.top
          self.restingTop = restingTop
          candidate.alwaysBounceVertical = true
          candidate.refreshControl = control
          observation = candidate.observe(\.contentOffset, options: [.new]) {
            [weak self] scroll, _ in
            let distance = max(0, -scroll.contentOffset.y - restingTop)
            DispatchQueue.main.async { [weak self] in
              self?.parent.onPull(distance)
              self?.finishSettlingIfAtRest()
            }
          }
          return
        }
        ancestor = candidate.superview
      }
    }

    func detach() {
      work?.cancel()
      work = nil
      settling = false
      observation = nil
      control.endRefreshing()
      if scroll?.refreshControl === control { scroll?.refreshControl = nil }
      scroll = nil
    }

    func beginFromAccessibility() {
      guard let scroll, work == nil else { return }
      control.beginRefreshing()
      scroll.setContentOffset(CGPoint(x: 0, y: -restingTop - 60), animated: true)
      refreshReleased()
    }

    @objc private func refreshReleased() {
      guard work == nil else { return }
      settling = false
      parent.onPhase(.loading)
      work = Task { [weak self] in
        guard let self else { return }
        let started = ContinuousClock.now
        var succeeded = true
        do {
          try await self.parent.refresh()
        } catch is CancellationError {
          return
        } catch {
          succeeded = false
        }
        // Let the cloud finish its anticipation even when the local catalog loads instantly.
        let remaining = Duration.milliseconds(500) - started.duration(to: .now)
        do {
          if remaining > .zero { try await Task.sleep(for: remaining) }
          self.parent.onPhase(succeeded ? .launching : .failed)
          UIAccessibility.post(
            notification: .announcement, argument: succeeded ? "课程已更新" : "刷新失败，请重试")
          try await Task.sleep(for: .milliseconds(450))
        } catch { return }
        self.settling = true
        self.parent.onPhase(.settling)
        self.control.endRefreshing()
        self.work = nil
        self.finishSettlingIfAtRest()
      }
    }

    private func finishSettlingIfAtRest() {
      guard settling, let scroll,
        -scroll.contentOffset.y - restingTop <= 0.5
      else { return }
      settling = false
      parent.onPhase(.idle)
    }
  }
}
