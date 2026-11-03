import SwiftUI

/// Figma 493:510, measured on its 402 × 874 canvas. Keep the artwork separate
/// from the screen so the launch storyboard and live startup use the same render.
struct StartupLockup: View {
  var image: (String) -> Image = { Image($0) }
  var iconLift: CGFloat = 0
  var iconScale: CGFloat = 1
  var englishOpacity: Double = 1
  var chineseOpacity: Double = 1
  var englishOffset: CGFloat = 0
  var chineseOffset: CGFloat = 0

  var body: some View {
    VStack(spacing: 0) {
      Color.white.frame(width: 152, height: 152)
      .overlay(alignment: .topLeading) {
        image("StartupCloudBase").resizable()
          .frame(width: 267.337, height: 89.112)
          .offset(x: -59.193, y: 84.817)
      }
      .overlay(alignment: .topLeading) {
        image("StartupCloudReaders").resizable()
          .frame(width: 149.585, height: 144.881)
          .clipShape(RoundedRectangle(cornerRadius: 45.691))
          .offset(x: -0.073, y: -2.035)
      }
      .frame(width: 152, height: 152)
      .clipShape(RoundedRectangle(cornerRadius: 38, style: .circular))
      .shadow(color: .black.opacity(0.16), radius: 12.75, x: 4, y: 4)
      .scaleEffect(iconScale)
      .offset(y: iconLift)
      Spacer().frame(height: 63)
      wordmark("CloudPath", width: 206, slope: 0.227)
        .opacity(englishOpacity).offset(y: englishOffset)
      Spacer().frame(height: 16)
      wordmark("云径", width: 80, slope: 0.090)
        .opacity(chineseOpacity).offset(y: chineseOffset)
    }
    .frame(width: 206, height: 327)
  }

  private func wordmarkFont(_ title: String) -> Font {
    guard title == "云径" else {
      return .custom("InterVariable", fixedSize: 40).weight(.bold)
    }
    #if canImport(UIKit)
      // Use the app's glyph-checked Chinese fallback on Simulator runtimes
      // whose system cascade returns LastResort instead of Chinese glyphs.
      return Font(DS.uiFont(.sectionTitle, weight: .bold).withSize(40))
    #else
      return .custom("PingFangSC-Semibold", fixedSize: 40)
    #endif
  }

  private func wordmark(_ title: String, width: CGFloat, slope: CGFloat) -> some View {
    Text(title)
      .font(wordmarkFont(title))
      .fixedSize()
      .foregroundStyle(LinearGradient(
        stops: [
          .init(color: Color(red: 124 / 255, green: 58 / 255, blue: 237 / 255).opacity(0.61), location: 0.0096558),
          .init(color: .white, location: 1),
        ],
        startPoint: UnitPoint(x: 0, y: 0.5 - slope / 2),
        endPoint: UnitPoint(x: 1, y: 0.5 + slope / 2)))
      .frame(width: width, height: 48)
      .shadow(color: Color(white: 150 / 255).opacity(0.25), radius: 2, x: 4, y: 4)
  }
}

struct StartupView: View {
  var isReady = true
  var reduceMotion = false
  var holdForPreview = false
  var onFinished: () -> Void = {}

  @State private var iconLift: CGFloat = 0
  @State private var iconScale: CGFloat = 1
  @State private var englishOpacity: Double = 0
  @State private var chineseOpacity: Double = 0
  @State private var englishOffset: CGFloat = 8
  @State private var chineseOffset: CGFloat = 8
  @State private var entranceFinished = false
  @State private var screenOpacity: Double = 1

  private var staticArtwork: Bool { reduceMotion || holdForPreview }
  private var canDismiss: Bool { isReady && entranceFinished && !holdForPreview }

  var body: some View {
    GeometryReader { geometry in
      let scale = min(1, geometry.size.width / 402, geometry.size.height / 874)
      StartupLockup(
        iconLift: staticArtwork ? 0 : iconLift,
        iconScale: staticArtwork ? 1 : iconScale,
        englishOpacity: staticArtwork ? 1 : englishOpacity,
        chineseOpacity: staticArtwork ? 1 : chineseOpacity,
        englishOffset: staticArtwork ? 0 : englishOffset,
        chineseOffset: staticArtwork ? 0 : chineseOffset)
        .scaleEffect(scale)
        .position(x: geometry.size.width / 2,
          y: geometry.size.height * (477.5 / 874))
    }
    .background(Color.white)
    .ignoresSafeArea()
    .opacity(screenOpacity)
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("CloudPath 云径，正在加载学习空间")
    .accessibilityAddTraits(.isImage)
    .accessibilityIdentifier("app.loading")
    .task {
      guard !holdForPreview else { return }
      do {
        if staticArtwork {
          try await Task.sleep(for: .milliseconds(950))
        } else {
          // Begin at the system launch artwork's resting icon, then float up.
          withAnimation(.spring(duration: 0.48, bounce: 0.18)) {
            iconLift = -6
            iconScale = 1.035
          }
          try await Task.sleep(for: .milliseconds(220))
          withAnimation(.easeOut(duration: 0.30)) {
            englishOpacity = 1
            englishOffset = 0
          }
          try await Task.sleep(for: .milliseconds(120))
          withAnimation(.easeOut(duration: 0.30)) {
            chineseOpacity = 1
            chineseOffset = 0
          }
          try await Task.sleep(for: .milliseconds(160))
          withAnimation(.spring(duration: 0.42, bounce: 0.20)) {
            iconLift = 0
            iconScale = 1
          }
          try await Task.sleep(for: .milliseconds(450))
        }
        entranceFinished = true
      } catch { return }
    }
    .task(id: canDismiss) {
      guard canDismiss else { return }
      // Loading overlaps the entrance. Slow services hold the settled artwork
      // and dismiss as soon as ready, without replaying or adding another delay.
      if !reduceMotion {
        withAnimation(.easeOut(duration: 0.22)) { screenOpacity = 0 }
        do { try await Task.sleep(for: .milliseconds(220)) } catch { return }
      }
      guard !Task.isCancelled else { return }
      onFinished()
    }
  }
}
