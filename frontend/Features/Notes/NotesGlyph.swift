import SwiftUI

/// Original reader/recorder controls use Lucide outline geometry, without sticker chrome.
struct NotesGlyph: View {
  let name: String
  var size: CGFloat = 20
  var body: some View {
    NotesGlyphShape(name: name).stroke(
      style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round)
    ).frame(width: size, height: size).accessibilityHidden(true)
  }
}
// Exact Lucide v1.18.0 geometry used by the existing frontend. ISC license in LICENSE.lucide.txt.
private struct NotesGlyphShape: Shape {
  let name: String
  private static let paths: [String: [String]] = [
    "X": ["M18 6 6 18", "m6 6 12 12"],
    "MessageSquareText": [
      "M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z",
      "M7 11h10", "M7 15h6", "M7 7h8",
    ],
    "Mic2": [
      "m11 7.601-5.994 8.19a1 1 0 0 0 .1 1.298l.817.818a1 1 0 0 0 1.314.087L15.09 12",
      "M16.5 21.174C15.5 20.5 14.372 20 13 20c-2.058 0-3.928 2.356-6 2-2.072-.356-2.775-3.369-1.5-4.5",
      "M11.0 7.0a5.0 5.0 0 1 0 10.0 0a5.0 5.0 0 1 0 -10.0 0",
    ],
    "Play": ["M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"],
    "Pause": [
      "M15.0 3.0H18.0a1.0 1.0 0 0 1 1.0 1.0V20.0a1.0 1.0 0 0 1 -1.0 1.0H15.0a1.0 1.0 0 0 1 -1.0 -1.0V4.0a1.0 1.0 0 0 1 1.0 -1.0Z",
      "M6.0 3.0H9.0a1.0 1.0 0 0 1 1.0 1.0V20.0a1.0 1.0 0 0 1 -1.0 1.0H6.0a1.0 1.0 0 0 1 -1.0 -1.0V4.0a1.0 1.0 0 0 1 1.0 -1.0Z",
    ],
    "Square": [
      "M5.0 3.0H19.0a2.0 2.0 0 0 1 2.0 2.0V19.0a2.0 2.0 0 0 1 -2.0 2.0H5.0a2.0 2.0 0 0 1 -2.0 -2.0V5.0a2.0 2.0 0 0 1 2.0 -2.0Z"
    ],
    "RotateCcw": ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5"],
    "Undo2": ["M9 14 4 9l5-5", "M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"],
    "Redo2": ["m15 14 5-5-5-5", "M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13"],
    "Trash2": [
      "M10 11v6", "M14 11v6", "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6", "M3 6h18",
      "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",
    ],
    "PenLine": [
      "M13 21h8",
      "M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z",
    ],
    "Highlighter": [
      "m9 11-6 6v3h9l3-3", "m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4",
    ],
    "Eraser": [
      "M21 21H8a2 2 0 0 1-1.42-.587l-3.994-3.999a2 2 0 0 1 0-2.828l10-10a2 2 0 0 1 2.829 0l5.999 6a2 2 0 0 1 0 2.828L12.834 21",
      "m5.082 11.09 8.828 8.828",
    ],
    "FileText": [
      "M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z",
      "M14 2v5a1 1 0 0 0 1 1h5", "M10 9H8", "M16 13H8", "M16 17H8",
    ],
    "NotebookPen": [
      "M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4", "M2 6h4", "M2 10h4",
      "M2 14h4", "M2 18h4",
      "M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z",
    ],
    "Hand": [
      "M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2", "M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2",
      "M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8",
      "M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15",
    ],
    "Sparkles": [
      "M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z",
      "M20 2v4", "M22 4h-4", "M2.0 20.0a2.0 2.0 0 1 0 4.0 0a2.0 2.0 0 1 0 -4.0 0",
    ],
  ]
  func path(in rect: CGRect) -> Path {
    var path = Path()
    for data in Self.paths[name] ?? [] { path.addPath(Path(SVGPath.parse(data))) }
    return path.applying(CGAffineTransform(scaleX: rect.width / 24, y: rect.height / 24))
  }
}

struct NotesSaveCelebration: View {
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var start = Date()
  var body: some View {
    TimelineView(.animation(minimumInterval: 0.12, paused: reduceMotion)) { timeline in
      let frame =
        reduceMotion ? 5 : min(5, Int(max(0, timeline.date.timeIntervalSince(start)) / 0.12))
      VStack(spacing: 12) {
        ZStack {
          Circle().fill(DS.primary.opacity(0.12)).frame(width: 190, height: 190).blur(radius: 15)
          Circle().stroke(DS.primary.opacity(0.25), lineWidth: 2).frame(width: 168, height: 168)
          AssetImage("/assets/brand/success/cloud-mascot-success-strip-v1.png").frame(
            width: 840, height: 140
          ).offset(x: -CGFloat(frame) * 140)
            .frame(width: 140, height: 140, alignment: .leading).clipped()
        }.frame(width: 210, height: 210)
        Text("摘录笔记保存成功").font(DS.font(15, weight: .bold)).foregroundStyle(DS.primary)
      }.padding(20).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 24))
    }.allowsHitTesting(false).accessibilityLabel("摘录笔记保存成功")
  }
}

@MainActor enum NotesScreenMetrics {
  static var titleTopInset: CGFloat {
    let top =
      UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
      .first(where: \.isKeyWindow)?.safeAreaInsets.top ?? 24
    return max(0, 66 - top)
  }
}
struct NotesPageHeader: View {
  let title: String
  let back: () -> Void
  @Environment(AppStore.self) private var store
  var body: some View {
    ZStack(alignment: .top) {
      Text(title).font(DS.font(27.2, weight: .bold)).foregroundStyle(DS.text).frame(
        height: DS.navigationRowHeight, alignment: .top)
        .accessibilityAddTraits(.isHeader).accessibilityIdentifier("navigation.title")
      HStack {
        Button(action: back) {
          Image(systemName: "arrow.left").font(.system(size: 18)).foregroundStyle(DS.text).frame(
            width: 44, height: 44
          ).background(.white, in: RoundedRectangle(cornerRadius: 12)).overlay(
            RoundedRectangle(cornerRadius: 12).stroke(DS.border))
        }.accessibilityLabel("返回").accessibilityIdentifier("navigation.back")
        Spacer()
      }.offset(y: DS.navigationButtonOffset)
    }.frame(height: DS.navigationRowHeight).padding(.horizontal, store.isPad ? 32 : 16).padding(
      .top, NotesScreenMetrics.titleTopInset)
      .navigationBarSurface()
  }
}

/// Reader popups retain the source's white 28pt surface and page-relative
/// placement. They stay in the reader instead of adopting a system form sheet.
struct NotesPopup<Content: View>: View {
  var width: CGFloat
  var height: CGFloat
  var anchor: UnitPointValue? = nil
  var topInset: CGFloat = 0
  var bottomAligned = false
  var rightInset: CGFloat? = nil
  var dim = false
  var identifier = "notes.localPopup"
  var onOutside: () -> Void
  @ViewBuilder var content: () -> Content
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var keyboardTop = CGFloat.greatestFiniteMagnitude
  var body: some View {
    GeometryReader { geometry in
      let visible = min(geometry.size.height, keyboardTop - geometry.frame(in: .global).minY)
      let available = max(100, visible - topInset - 16)
      let popupWidth = min(width, max(100, geometry.size.width - 16))
      let popupHeight = min(height, available)
      let pointX = CGFloat(anchor?.x ?? 0.5) * geometry.size.width
      let pointY = CGFloat(anchor?.y ?? 0.5) * geometry.size.height
      let left = rightInset.map { max(8, geometry.size.width - popupWidth - $0) } ?? (
        anchor == nil
        ? (geometry.size.width - popupWidth) / 2
        : max(
          8,
          min(
            geometry.size.width - popupWidth - 8,
            pointX + popupWidth + 20 <= geometry.size.width ? pointX + 16 : pointX - popupWidth - 16
          )))
      let idealTop =
        bottomAligned
        ? visible - popupHeight - 12
        : anchor == nil
          ? topInset + (available - popupHeight) / 2 + 8
          : pointY + popupHeight + 24 <= visible ? pointY + 20 : pointY - popupHeight - 20
      let top = max(topInset + 8, min(visible - popupHeight - 8, idealTop))
      ZStack(alignment: .topLeading) {
        (dim ? Color.black.opacity(0.32) : Color.black.opacity(0.001))
          .contentShape(Rectangle()).onTapGesture(perform: onOutside)
          .accessibilityLabel("关闭弹层并保留内容")
        content()
          .frame(width: popupWidth, height: popupHeight)
          .background(.white, in: RoundedRectangle(cornerRadius: 28))
          .clipShape(RoundedRectangle(cornerRadius: 28))
          .shadow(color: Color(hex: 0x20263a).opacity(0.22), radius: 30, y: 24)
          .accessibilityElement(children: .contain)
          .accessibilityAddTraits(.isModal)
          .accessibilityIdentifier(identifier)
          .position(x: left + popupWidth / 2, y: top + popupHeight / 2)
      }
      .animation(reduceMotion ? nil : .easeInOut(duration: 0.22), value: keyboardTop)
    }
    .onReceive(
      NotificationCenter.default.publisher(for: UIResponder.keyboardWillChangeFrameNotification)
    ) { notification in
      keyboardTop =
        (notification.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect)?.minY
        ?? .greatestFiniteMagnitude
    }
    .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification))
    { _ in keyboardTop = .greatestFiniteMagnitude }
    .transition(.opacity.combined(with: .scale(scale: reduceMotion ? 1 : 0.94)))
  }
}
