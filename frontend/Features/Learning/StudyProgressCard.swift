import SwiftUI

/// Shared presentation for server daily plans and locally stored course progress.
/// Clouds sit between the colored backing and the frosted foreground pane.
struct StudyProgressCard: View {
  @Environment(\.horizontalSizeClass) private var horizontalSizeClass
  var compact: Bool
  var progress: Double
  var taskTitle: String
  var materialCount: Int = 1
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
              .buttonFrame()
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
      .studyProgressShadow()
      .allowsHitTesting(false).accessibilityHidden(true)
    }
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier("study.plan.card")
    // Reserve the protruding heads in the header so they cannot cover its
    // course selector or Create Course action, especially at iPad widths.
    .padding(.top, compact ? 0 : (horizontalSizeClass == .regular ? 22 : 6))
  }

  private func surface(compact: Bool) -> some View {
    let prefix = compact ? "compact" : "expanded"
    let glassShape = SVGAssetShape(assetPath: "assets/study-progress/\(prefix)-glass.svg")
    let backingShape = SVGAssetShape(assetPath: "assets/study-progress/\(prefix)-base.svg")
    return ZStack {
      backingShape.fill(Color(hex: 0xba9ded))
      clouds(compact: compact)
      // The pane shows a filtered backdrop, replacing the sharp backing under
      // it. A translucent blur over the original leaves its horizontal edge
      // visible and the raised title strip almost uncolored.
      glassShape.fill(DS.background)
      silhouette(prefix + "-base", compact: compact, paintFrom: "expanded-base")
        .blur(radius: 25).clipShape(glassShape)
      // The same characters continue underneath the pane. Only the portions
      // above its notched edge stay sharp; the rest transmit through the frost.
      clouds(compact: compact)
        .blur(radius: compact ? 7 : 9).opacity(0.32).clipShape(glassShape)
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

  private struct CloudPose {
    let asset: String
    let x: CGFloat
    let y: CGFloat
    let size: CGFloat
    var angle: Double = 0
  }

  // Stable arrangements avoid reshuffling characters during scrolling or sync.
  // Every tier includes the same four peeking characters and adds interior ones.
  private var interiorClouds: [CloudPose] {
    let sparse = [
      CloudPose(asset: "01-blue-wave", x: 0.13, y: 0.63, size: 0.23, angle: -12),
      CloudPose(asset: "05-pink-love", x: 0.84, y: 0.72, size: 0.25, angle: 12),
    ]
    let medium = sparse + [
      CloudPose(asset: "02-lavender-think", x: 0.43, y: 0.48, size: 0.21, angle: -8),
      CloudPose(asset: "03-mint-read", x: 0.64, y: 0.80, size: 0.22, angle: 8),
      CloudPose(asset: "06-yellow-sleep", x: 0.30, y: 0.91, size: 0.20, angle: 14),
    ]
    guard materialCount > 2 else { return sparse }
    guard materialCount > 5 else { return medium }
    return medium + [
      CloudPose(asset: "04-peach-cheer", x: 0.91, y: 0.37, size: 0.19, angle: -15),
      CloudPose(asset: "05-pink-love", x: 0.08, y: 0.96, size: 0.18, angle: 10),
      CloudPose(asset: "01-blue-wave", x: 0.53, y: 0.97, size: 0.19, angle: -10),
    ]
  }

  private func clouds(compact: Bool) -> some View {
    GeometryReader { geometry in
      let width = geometry.size.width
      let height = geometry.size.height
      let rim = (compact ? 13.1522 / 75 : 20.0094 / 178) * height
      let topAssets = ["02-lavender-think", "03-mint-read", "06-yellow-sleep", "04-peach-cheer"]
      ZStack(alignment: .topLeading) {
        ZStack {
          ForEach(Array(interiorClouds.enumerated()), id: \.offset) { _, pose in
            AssetImage("/assets/brand/study-progress/\(pose.asset).png")
              .frame(width: width * pose.size * 0.65, height: width * pose.size * 0.65)
              .rotationEffect(.degrees(pose.angle))
              .position(x: width * pose.x, y: height * pose.y)
          }
        }
        .clipShape(SVGAssetShape(assetPath: "assets/study-progress/\(compact ? "compact" : "expanded")-glass.svg"))
        ForEach(Array(topAssets.enumerated()), id: \.offset) { index, asset in
          let size = width * 0.125
          AssetImage("/assets/brand/study-progress/\(asset).png")
            .frame(width: size, height: size)
            .scaleEffect(x: index == 3 ? -1 : 1, y: 1)
            .offset(x: width * (0.60 + CGFloat(index) * 0.085),
              y: rim - size * (compact ? 0.28 : 0.73))
            .zIndex(index == 1 ? 4 : index == 2 ? 3 : index == 0 ? 2 : 1)
        }
      }
      .frame(width: width, height: height, alignment: .topLeading)
      // Allow heads above the top edge, but never let bodies escape underneath
      // the short collapsed pane or beyond either side of the backing.
      .padding(.top, width)
      .frame(width: width, height: height + width)
      .clipped()
      .offset(y: -width)
    }
    .allowsHitTesting(false).accessibilityHidden(true)
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

// Keep the directory selection and progress pane on the same elevation.
extension View {
  func studyProgressShadow(enabled: Bool = true) -> some View {
    shadow(color: Color.black.opacity(enabled ? 0.10 : 0), radius: 8, x: 0, y: 4)
  }
}
