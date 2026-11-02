import SwiftUI

/// Shared presentation for server daily plans and locally stored course progress.
/// The two original Figma silhouettes remain separate from the live text and progress.
struct StudyProgressCard: View {
  var compact: Bool
  var progress: Double
  var taskTitle: String
  var issue: String? = nil
  let openPlan: () -> Void

  private var fraction: Double { progress.isFinite ? min(1, max(0, progress)) : 0 }

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      Text("学习进度").font(DS.font(.sectionTitle)).foregroundStyle(DS.text)
        .accessibilityIdentifier("study.plan.title")
      GeometryReader { geometry in
        ZStack(alignment: .leading) {
          Capsule().fill(.white)
          Capsule().fill(DS.primary).frame(width: geometry.size.width * fraction)
        }
      }
      .frame(height: 9).padding(.top, compact ? 14 : 15)
      .accessibilityElement(children: .ignore)
      .accessibilityLabel("学习进度")
      .accessibilityValue("\(Int((fraction * 100).rounded()))%")
      .accessibilityIdentifier("study.plan.progress")
      if !compact {
        VStack(alignment: .leading, spacing: 6) {
          Text("今日计划").font(DS.font(.body)).foregroundStyle(DS.secondary)
          Text(taskTitle).font(DS.font(.cardTitle, weight: .medium))
            .foregroundStyle(DS.text).fixedSize(horizontal: false, vertical: true)
          if let issue {
            Text(issue).font(DS.font(.supporting)).foregroundStyle(DS.danger)
          }
        }
        .padding(.top, 18)
        .transition(.opacity)
        HStack {
          Spacer()
          Button(action: openPlan) {
            HStack(spacing: 3) {
              Text("计划详情")
              Image(systemName: "chevron.right")
            }.font(DS.font(.body, weight: .medium)).foregroundStyle(DS.primary)
              .frame(minHeight: 44)
          }
          .buttonStyle(.plain).accessibilityLabel("计划详情")
        }.transition(.opacity)
      }
    }
    .padding(.horizontal, 21)
    .padding(.top, compact ? 9 : 12)
    .padding(.bottom, compact ? 21 : 4)
    .frame(maxWidth: .infinity, minHeight: compact ? 75 : 178, alignment: .topLeading)
    .background {
      ZStack {
        surface(compact: false).opacity(compact ? 0 : 1)
        surface(compact: true).opacity(compact ? 1 : 0)
      }
      // Cast one shadow from the finished pane; separate translucent layers
      // would otherwise shadow each other and muddy the lavender transmission.
      .compositingGroup()
      .shadow(color: Color(white: 0.462).opacity(0.25), radius: 4.45, x: 4, y: 4)
      .allowsHitTesting(false).accessibilityHidden(true)
    }
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier("study.plan.card")
  }

  private func surface(compact: Bool) -> some View {
    let prefix = compact ? "compact" : "expanded"
    let glassShape = SVGAssetShape(assetPath: "assets/study-progress/\(prefix)-glass.svg")
    return ZStack {
      silhouette(prefix + "-base", compact: compact, paintFrom: "expanded-base")
      // The pane shows a filtered backdrop, replacing the sharp backing under
      // it. A translucent blur over the original leaves its horizontal edge
      // visible and the raised title strip almost uncolored.
      glassShape.fill(DS.background)
      silhouette(prefix + "-base", compact: compact, paintFrom: "expanded-base")
        .blur(radius: 25).clipShape(glassShape)
      silhouette(prefix + "-glass", compact: compact)
      .overlay {
        LinearGradient(stops: [
          .init(color: .white.opacity(0.16), location: 0),
          .init(color: .white.opacity(0.02), location: 0.38),
          .init(color: .clear, location: 0.7),
          .init(color: .white.opacity(0.08), location: 1),
        ], startPoint: .top, endPoint: .bottom).clipShape(glassShape)
      }
      .overlay {
        // The full front silhouette stays visible around the title and notch.
        glassShape.stroke(
          LinearGradient(stops: [
            .init(color: .white.opacity(0.85), location: 0),
            .init(color: .white.opacity(0.32), location: 0.35),
            .init(color: .white.opacity(0.16), location: 0.55),
            .init(color: .white.opacity(0.70), location: 1),
          ], startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 0.85)
          .blur(radius: 0.2)
      }
    }
  }

  private func silhouette(_ name: String, compact: Bool, paintFrom: String? = nil) -> some View {
    GeometryReader { geometry in
      let height: CGFloat = compact ? 75 : 178
      SVGAsset(path: "assets/study-progress/\(name).svg",
        paintFrom: paintFrom.map { "assets/study-progress/\($0).svg" })
        .frame(width: 375, height: height)
        .scaleEffect(x: geometry.size.width / 375, y: geometry.size.height / height, anchor: .topLeading)
    }
  }
}
