import SwiftUI

/// Material tokens scoped to the tools inside an expanded learning section.
enum StudyToolTint {
  case blue, mint, pink, violet

  init(route: Route) {
    switch route {
    case .flashcards: self = .blue
    case .assignment: self = .mint
    case .mistakes: self = .pink
    default: self = .violet
    }
  }

  private var colors: (UInt, UInt, UInt, UInt) {
    switch self {
    case .blue: (0x91acff, 0xdbe5ff, 0xcedaff, 0xf2f3ff)
    case .mint: (0x69ddbc, 0xd6f8ea, 0xc8f7e9, 0xeff8f8)
    case .pink: (0xd999f4, 0xf4ddff, 0xf0d7ff, 0xfaf0ff)
    case .violet: (0xac7ae8, 0xe8d8ff, 0xe5d5ff, 0xf4eeff)
    }
  }

  var backing: LinearGradient {
    LinearGradient(colors: [Color(hex: colors.0), Color(hex: colors.1)],
      startPoint: .topTrailing, endPoint: .bottomLeading)
  }
  var transmission: LinearGradient {
    LinearGradient(colors: [Color(hex: colors.2), Color(hex: colors.3)],
      startPoint: .topLeading, endPoint: .bottomTrailing)
  }
  var sampleBacking: Color {
    switch self {
    case .blue: Color(hex: 0xbaccff)
    case .mint: Color(hex: 0xb0e9d7)
    case .pink: Color(hex: 0xe6cdf7)
    case .violet: Color(hex: 0xd3bef4)
    }
  }
}

/// A clear colored backing rim and one continuous, filtered front pane.
/// Shadows are applied after compositing the decorative layers, never to text.
struct StudyToolGlassSurface: View {
  let tint: StudyToolTint
  var cornerRadius: CGFloat = 20
  var depth: CGFloat = 5
  var pressed = false

  var body: some View {
    let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
    let separation = pressed ? depth * 0.45 : depth
    ZStack {
      shape.fill(tint.backing).offset(x: separation * 0.8, y: -separation)
      // The foreground is the transmitted color field itself, so the backing's
      // outline cannot create a hard seam through the title and description.
      shape.fill(tint.transmission)
      shape.fill(.white.opacity(0.13))
      shape.fill(LinearGradient(stops: [
        .init(color: .white.opacity(0.25), location: 0),
        .init(color: .clear, location: 0.42),
        .init(color: .white.opacity(0.08), location: 1),
      ], startPoint: .topLeading, endPoint: .bottomTrailing))
      shape.stroke(LinearGradient(stops: [
        .init(color: .white.opacity(0.95), location: 0),
        .init(color: .white.opacity(0.38), location: 0.38),
        .init(color: .white.opacity(0.18), location: 0.58),
        .init(color: .white.opacity(0.85), location: 1),
      ], startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 0.9)
        .blur(radius: 0.15)
    }
    .compositingGroup()
    .shadow(color: Color.black.opacity(0.10), radius: 8, x: 0, y: 4)
    .allowsHitTesting(false).accessibilityHidden(true)
  }
}

/// Figma 532:81: 130×183, 24pt corners, backing offset (5, -5),
/// and a 10% white glass pane. The white information panel belongs to the label.
private struct StudyToolSampleSurface: View {
  let tint: StudyToolTint
  let pressed: Bool

  var body: some View {
    let shape = RoundedRectangle(cornerRadius: 24, style: .circular)
    let separation: CGFloat = pressed ? 2.25 : 5
    ZStack {
      shape.fill(tint.sampleBacking).blur(radius: 3)
        .offset(x: separation, y: -separation)
      // Reconstruct the broad frosted transmission: a pale ambient field with
      // a saturated inset that diffuses toward the edges instead of flat paint.
      shape.fill(DS.background)
      shape.fill(tint.sampleBacking.opacity(0.48))
      shape.inset(by: 8).fill(tint.sampleBacking.opacity(0.50))
        .blur(radius: 14).clipShape(shape)
      shape.fill(.white.opacity(0.10))
      shape.stroke(DS.text.opacity(0.07), lineWidth: 6)
        .blur(radius: 5).clipShape(shape)
      shape.fill(LinearGradient(colors: [.white.opacity(0.12), .clear, .white.opacity(0.06)],
        startPoint: .top, endPoint: .bottom))
      shape.stroke(LinearGradient(colors: [.white.opacity(0.85), .white.opacity(0.15), .white.opacity(0.65)],
        startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 0.7)
        .blur(radius: 0.2)
    }
    .compositingGroup()
    .shadow(color: Color.black.opacity(0.10), radius: 8, x: 0, y: 4)
    .allowsHitTesting(false).accessibilityHidden(true)
  }
}

struct StudyToolCardStyle: ButtonStyle {
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  let tint: StudyToolTint

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .compositingGroup()
      .clipShape(RoundedRectangle(cornerRadius: 24, style: .circular))
      .background {
        StudyToolSampleSurface(tint: tint, pressed: configuration.isPressed && !reduceMotion)
      }
      .padding(.top, 5).padding(.trailing, 5)
      .contentShape(Rectangle())
      .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
      .opacity(configuration.isPressed ? 0.94 : 1)
      .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: configuration.isPressed)
  }
}

struct StudyToolEntryStyle: ButtonStyle {
  var tint: Color? = DS.primary
  var showsBorder = true
  @Environment(\.bookCourseReduceMotion) private var reduceMotion

  func makeBody(configuration: Configuration) -> some View {
    materialLabel(configuration)
      .contentShape(Capsule())
      .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
      .opacity(configuration.isPressed ? 0.94 : 1)
      .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: configuration.isPressed)
  }

  private var fallbackColors: [Color] {
    guard let tint else { return [.white.opacity(0.96), .white.opacity(0.88)] }
    if tint == DS.primary { return [Color(hex: 0x9b53ff), DS.primary, Color(hex: 0x6c2bd9)] }
    return [tint.opacity(0.85), tint, tint]
  }

  @ViewBuilder private func materialLabel(_ configuration: Configuration) -> some View {
    if #available(iOS 26.0, *) {
      if let tint {
        configuration.label
          .glassEffect(.regular.tint(tint).interactive(!reduceMotion), in: Capsule())
      } else {
        configuration.label
          .glassEffect(.regular.interactive(!reduceMotion), in: Capsule())
      }
    } else {
      configuration.label.background {
        Capsule().fill(LinearGradient(colors: fallbackColors,
          startPoint: .topLeading, endPoint: .bottomTrailing))
          .overlay {
            Capsule().fill(LinearGradient(colors: [.white.opacity(0.22), .clear, .clear],
              startPoint: .top, endPoint: .bottom))
          }
          .overlay {
            if showsBorder {
              Capsule().stroke(LinearGradient(colors: [.white.opacity(0.8), .white.opacity(0.12)],
                startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 0.8)
            }
          }
          .compositingGroup()
          .shadow(color: Color.black.opacity(0.10), radius: 8, x: 0, y: 4)
      }
    }
  }
}

struct StudyToolPreviewSurface: View {
  var body: some View {
    RoundedRectangle(cornerRadius: 12, style: .circular).fill(.white)
      .compositingGroup()
      .shadow(color: Color.black.opacity(0.10), radius: 8, x: 0, y: 4)
      .allowsHitTesting(false).accessibilityHidden(true)
  }
}
