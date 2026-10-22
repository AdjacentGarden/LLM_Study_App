import SwiftUI
import UIKit

extension Color {
  init(hex: UInt) {
    self.init(
      .sRGB, red: Double((hex >> 16) & 255) / 255, green: Double((hex >> 8) & 255) / 255,
      blue: Double(hex & 255) / 255, opacity: 1)
  }
}
enum DS {
  static let primary = Color(hex: 0x7c3aed), primaryPressed = Color(hex: 0x5b21b6),
    primarySoft = Color(hex: 0xf3e8ff)
  static let background = Color(hex: 0xf6f8fb), surface = Color.white, text = Color(hex: 0x20263a),
    secondary = Color(hex: 0x4f5e75), muted = Color(hex: 0x596780), border = Color(hex: 0xd7dee8)
  static let warning = Color(hex: 0x985014), danger = Color(hex: 0xb4233c),
    mint = Color(hex: 0x1e765d), orange = Color(hex: 0x985014)
  static let navigationRowHeight: CGFloat = 44
  // Align controls with the visible cap height of the Noto SC navigation title.
  static let navigationButtonOffset: CGFloat = -4
  static func font(_ size: CGFloat = 20, weight: Font.Weight = .regular) -> Font {
    .custom(
      weight == .regular || weight == .light
        ? "BookCourseNotoSansSC-Regular" : "BookCourseNotoSansSC-Bold", fixedSize: size)
  }
  static let globalAnimation = Animation.timingCurve(0.25, 1, 0.5, 1, duration: 0.35)
  static let localAnimation = Animation.timingCurve(0.65, 0, 0.35, 1, duration: 0.18)
  static let panelAnimation = Animation.spring(response: 0.48, dampingFraction: 0.90)
  static let flipAnimation = Animation.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.34)
  static func tone(_ name: String) -> Color {
    switch name {
    case "pink": Color(hex: 0xffe3eb)
    case "blue": Color(hex: 0xdff0ff)
    case "mint": Color(hex: 0xdff5e8)
    case "yellow": Color(hex: 0xfff2bf)
    case "peach": Color(hex: 0xffe5d5)
    default: Color(hex: 0xeee4ff)
    }
  }
}
enum BCButtonStyle { case primary, secondary, text, danger }
struct BCButton: View {
  let title: String
  var icon: String?
  var style: BCButtonStyle
  let action: () -> Void
  @Environment(\.isEnabled) private var enabled
  init(
    _ title: String, icon: String? = nil, style: BCButtonStyle = .primary,
    action: @escaping () -> Void
  ) {
    self.title = title
    self.icon = icon
    self.style = style
    self.action = action
  }
  var body: some View {
    Button(action: action) {
      HStack(spacing: 8) {
        if let icon {
          BCGlyph(
            icon,
            color: !enabled
              ? DS.muted : style == .primary ? .white : style == .danger ? DS.danger : DS.text
          ).frame(width: 18, height: 18)
        }
        Text(title).font(DS.font(20, weight: .semibold)).multilineTextAlignment(.center)
      }
      .frame(maxWidth: .infinity).frame(minHeight: 54)
      .foregroundStyle(
        !enabled
          ? DS.muted
          : style == .primary
            ? .white : style == .danger ? DS.danger : style == .text ? DS.primary : DS.text
      )
      .padding(.horizontal, 14)
      .background(
        enabled
          ? (style == .primary
            ? DS.primary
            : style == .secondary ? Color.white : style == .danger ? Color(hex: 0xffedf0) : .clear)
          : Color(hex: 0xeef1f5), in: Capsule()
      )
      .overlay(
        Capsule().stroke(
          style == .secondary
            ? Color(hex: 0xb8c5d7) : style == .danger ? DS.danger.opacity(0.28) : .clear,
          lineWidth: 1)
      )
      .contentShape(Capsule())
    }.buttonStyle(BCPressStyle())
  }
}
struct BCPressStyle: ButtonStyle {
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  func makeBody(configuration: Configuration) -> some View {
    configuration.label.scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1).opacity(
      configuration.isPressed ? 0.88 : 1
    ).animation(reduceMotion ? nil : .easeOut(duration: 0.09), value: configuration.isPressed)
  }
}
struct BCCard<Content: View>: View {
  @ViewBuilder let content: () -> Content
  var body: some View {
    content().padding(16).frame(maxWidth: .infinity, alignment: .leading).background(
      DS.surface, in: RoundedRectangle(cornerRadius: 14)
    ).overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border, lineWidth: 1))
  }
}
struct BCPill: View {
  let title: String
  var color: Color
  init(_ title: String, color: Color = DS.primary) {
    self.title = title
    self.color = color
  }
  var body: some View {
    Text(title).font(DS.font(12, weight: .semibold)).foregroundStyle(color).padding(.horizontal, 9)
      .padding(.vertical, 5).background(color.opacity(0.09), in: Capsule())
  }
}
struct BCProgress: View {
  var value: Double
  var body: some View {
    GeometryReader { geometry in
      ZStack(alignment: .leading) {
        Capsule().fill(DS.border.opacity(0.55))
        Capsule().fill(DS.primary).frame(width: max(0, geometry.size.width * min(1, max(0, value))))
      }
    }.frame(height: 6).accessibilityElement().accessibilityLabel("进度").accessibilityValue(
      "\(Int(min(1,max(0,value))*100))%")
  }
}
struct BCHeader: View {
  let title: String
  var titleColor: Color = DS.text
  var subtitle: String?
  var back: (() -> Void)?
  var actionTitle: String?
  var action: (() -> Void)?
  init(
    _ title: String, subtitle: String? = nil, back: (() -> Void)? = nil, actionTitle: String? = nil,
    action: (() -> Void)? = nil, titleColor: Color = DS.text
  ) {
    self.title = title
    self.titleColor = titleColor
    self.subtitle = subtitle
    self.back = back
    self.actionTitle = actionTitle
    self.action = action
  }
  var body: some View {
    ZStack(alignment: .top) {
      VStack(spacing: 2) {
        Text(title).font(DS.font(27.2, weight: .bold)).foregroundStyle(titleColor).lineLimit(1)
          .accessibilityAddTraits(.isHeader).accessibilityIdentifier("navigation.title")
        if let subtitle {
          Text(subtitle).font(DS.font(14)).foregroundStyle(DS.secondary).lineLimit(1)
        }
      }.padding(.horizontal, 44).frame(maxWidth: .infinity)
      HStack {
        if let back {
          Button(action: back) {
            BCGlyph("ArrowLeft").frame(width: 18, height: 18).frame(width: 44, height: 44)
              .background(.white, in: RoundedRectangle(cornerRadius: 12)).overlay(
                RoundedRectangle(cornerRadius: 12).stroke(DS.border, lineWidth: 1))
          }.accessibilityLabel("返回").accessibilityIdentifier("navigation.back")
        }
        Spacer(minLength: 0)
        if let actionTitle, let action {
          Button(action: action) {
            Text(actionTitle).font(DS.font(14, weight: .semibold)).foregroundStyle(DS.primary)
              .frame(minHeight: 44)
          }
        }
      }.offset(y: DS.navigationButtonOffset)
    }.foregroundStyle(DS.text).frame(height: DS.navigationRowHeight)
      .padding(.horizontal, 16).padding(.top, 4)
  }
}
private final class AssetCache { static let images = NSCache<NSString, UIImage>() }
struct AssetImage: View {
  let path: String
  let contentMode: ContentMode
  init(_ path: String, contentMode: ContentMode = .fit) {
    self.path = path
    self.contentMode = contentMode
  }
  private var image: UIImage? {
    if let cached = AssetCache.images.object(forKey: path as NSString) { return cached }
    if let image = ContentBundle.image(path) {
      AssetCache.images.setObject(image, forKey: path as NSString)
      return image
    }
    return nil
  }
  var body: some View {
    Group {
      if let image {
        Image(uiImage: image).resizable().aspectRatio(contentMode: contentMode)
      } else {
        Color.clear
      }
    }.accessibilityHidden(true)
  }
}
private enum StickerMetadata {
  static let tones: [String: String] = {
    guard let data = try? ContentBundle.data("Data/sticker-artwork.json"),
      let values = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]]
    else { return [:] }
    return values.compactMapValues { $0["tone"] as? String }
  }()
  static func color(_ name: String) -> Color {
    switch name {
    case "pink": Color(hex: 0xffc9d8)
    case "blue": Color(hex: 0xbcdfff)
    case "mint": Color(hex: 0xb7efd8)
    case "yellow": Color(hex: 0xffe883)
    case "peach", "orange": Color(hex: 0xffd3a0)
    default: Color(hex: 0xddd0ff)
    }
  }
}
struct StickerIcon: View {
  let name: String
  let tone: String?
  let size: CGFloat
  let glyphSize: CGFloat?
  init(_ name: String, tone: String? = nil, size: CGFloat = 40, glyphSize: CGFloat? = nil) {
    self.name = name
    self.tone = tone
    self.size = size
    self.glyphSize = glyphSize
  }
  private var shape: UnevenRoundedRectangle {
    UnevenRoundedRectangle(
      topLeadingRadius: 9, bottomLeadingRadius: 11, bottomTrailingRadius: 8, topTrailingRadius: 12)
  }
  var body: some View {
    SVGAsset(path: "icons/sticker-\(name).svg").frame(
      width: glyphSize ?? size * 0.5, height: glyphSize ?? size * 0.5
    ).frame(width: size, height: size).background(
      StickerMetadata.color(tone ?? StickerMetadata.tones[name] ?? "violet"), in: shape
    ).overlay(shape.strokeBorder(Color(hex: 0x252436), lineWidth: 1.7)).shadow(
      color: Color(hex: 0x252436), radius: 0, x: 2, y: 2
    ).rotationEffect(.degrees(-3)).accessibilityHidden(true)
  }
}
struct EmptyStateView: View {
  let title: String
  let message: String
  var asset: String? = nil
  var icon: String? = nil
  var body: some View {
    VStack(spacing: 14) {
      if let asset {
        AssetImage(asset).frame(maxWidth: 250, maxHeight: 190)
      } else if let icon {
        BCGlyph(icon, color: DS.primary).frame(width: 34, height: 34).accessibilityHidden(true)
      }
      Text(title).font(DS.font(21, weight: .bold))
      Text(message).font(DS.font(14)).lineSpacing(6).foregroundStyle(DS.secondary)
        .multilineTextAlignment(.center)
    }.padding(28).frame(maxWidth: .infinity)
  }
}
struct BCTextField: View {
  let placeholder: String
  @Binding var text: String
  init(_ placeholder: String, text: Binding<String>) {
    self.placeholder = placeholder
    self._text = text
  }
  var body: some View {
    TextField(placeholder, text: $text).font(DS.font(18)).padding(.horizontal, 14).frame(
      minHeight: 52
    ).background(.white, in: RoundedRectangle(cornerRadius: 14)).overlay(
      RoundedRectangle(cornerRadius: 14).stroke(DS.border, lineWidth: 1)
    ).tint(DS.primary)
  }
}
struct BCTextEditor: View {
  @Binding var text: String
  var placeholder: String = ""
  var body: some View {
    ZStack(alignment: .topLeading) {
      if text.isEmpty {
        Text(placeholder).font(DS.font(16)).foregroundStyle(DS.muted).padding(13).allowsHitTesting(
          false)
      }
      TextEditor(text: $text).font(DS.font(16)).scrollContentBackground(.hidden).padding(8).tint(
        DS.primary
      ).opacity(text.isEmpty ? 0.8 : 1)
    }.frame(minHeight: 120).background(.white, in: RoundedRectangle(cornerRadius: 14)).overlay(
      RoundedRectangle(cornerRadius: 14).stroke(DS.border, lineWidth: 1))
  }
}
struct ProgressiveMaterial: View {
  var top = false
  var body: some View {
    Rectangle().fill(.ultraThinMaterial).mask(
      LinearGradient(
        colors: top ? [.black, .clear] : [.clear, .black], startPoint: .top, endPoint: .bottom)
    ).allowsHitTesting(false).accessibilityHidden(true)
  }
}

/// Keep the full-screen dimmer stationary while the panel travels above it.
/// Scaling or moving a combined panel/dimmer also moves the status-bar coverage.
struct BottomPanelLayer<Content: View>: View {
  let presentationID: String?
  var dimOpacity: Double = 0.46
  let onDismiss: () -> Void
  @ViewBuilder var content: () -> Content
  @Environment(\.bookCourseReduceMotion) private var reduceMotion

  var body: some View {
    ZStack {
      Color(hex: 0x20263a).opacity(presentationID == nil ? 0 : dimOpacity)
        .ignoresSafeArea()
        .contentShape(Rectangle()).onTapGesture(perform: onDismiss)
        .accessibilityLabel("关闭弹层").accessibilityIdentifier("modal.backdrop")
        .accessibilityHidden(presentationID == nil)
        .animation(
          reduceMotion ? nil : .easeInOut(duration: 0.20), value: presentationID != nil)
      content()
    }
    .allowsHitTesting(presentationID != nil)
    .animation(reduceMotion ? nil : DS.panelAnimation, value: presentationID)
  }
}

private struct NavigationBarSurface: ViewModifier {
  let color: Color
  let fadesIntoContent: Bool

  func body(content: Content) -> some View {
    content
      .background { color.ignoresSafeArea(.container, edges: .top) }
      .overlay(alignment: .bottom) {
        // Extend the transition into the scroll content without taking space
        // from the layout or intercepting controls beneath the header.
        if fadesIntoContent {
          ProgressiveMaterial(top: true).frame(height: 24).offset(y: 24)
        }
      }
      .zIndex(1)
  }
}

extension View {
  func navigationBarSurface(_ color: Color = DS.background, fadesIntoContent: Bool = true)
    -> some View
  {
    modifier(NavigationBarSurface(color: color, fadesIntoContent: fadesIntoContent))
  }
}

struct BCGlyph: View {
  let name: String
  let color: Color
  init(_ name: String, color: Color = DS.text) {
    self.name = name
    self.color = color
  }
  var body: some View {
    if ContentBundle.url("icons/lucide-\(name).svg") != nil {
      SVGAsset(path: "icons/lucide-\(name).svg", tint: color)
    } else {
      Image(systemName: name).resizable().scaledToFit()
    }
  }
}

private struct BookCourseReduceMotionKey: EnvironmentKey { static let defaultValue = false }
extension EnvironmentValues {
  var bookCourseReduceMotion: Bool {
    get { self[BookCourseReduceMotionKey.self] || accessibilityReduceMotion }
    set { self[BookCourseReduceMotionKey.self] = newValue }
  }
}
