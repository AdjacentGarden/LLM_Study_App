import PDFKit
import PencilKit
import SwiftUI
import UIKit

enum NotesDrawingCoordinates {
  static let width: CGFloat = 1000
  static func normalized(_ drawing: PKDrawing, displayWidth: CGFloat) -> PKDrawing {
    guard displayWidth > 0 else { return drawing }
    return drawing.transformed(
      using: CGAffineTransform(scaleX: width / displayWidth, y: width / displayWidth))
  }
  static func displayed(_ drawing: PKDrawing, displayWidth: CGFloat) -> PKDrawing {
    return drawing.transformed(
      using: CGAffineTransform(scaleX: displayWidth / width, y: displayWidth / width))
  }
}

/// Original-page position is separate for every course and resource. A fresh citation
/// deliberately overrides the last browsing position; an ordinary reader open restores it.
enum NotesReadingPosition {
  static func key(courseID: String?, bookID: String) -> String { "\(courseID ?? "")::\(bookID)" }
  static func initialPage(
    courseID: String?, bookID: String, target: SourcePageTarget?,
    locations: [String: StudyLocation], pageCount: Int
  ) -> Int {
    let explicit = target.flatMap { $0.bookID == bookID ? $0.page : nil }
    return min(
      max(1, pageCount),
      max(1, explicit ?? locations[key(courseID: courseID, bookID: bookID)]?.page ?? 1))
  }
  @MainActor static func save(_ page: Int, courseID: String?, bookID: String, store: AppStore) {
    guard !bookID.isEmpty else { return }
    let locationKey = key(courseID: courseID, bookID: bookID)
    var location = store.locations[locationKey] ?? StudyLocation()
    if store.selectedCourseID == courseID && store.selectedBookID == bookID {
      location.chapterID = store.selectedChapterID
      location.lessonID = store.selectedLessonID
    }
    location.page = max(1, page)
    store.locations[locationKey] = location
    store.persist()
  }
}

private enum NotesReaderMode { case read, ink, text, ai }
private enum NotesPen { case pen, marker, eraser }
struct NotesPageManifest: Decodable {
  struct Asset: Decodable {
    let book_id: String
    let pdf_page: Int
    let url: String
  }
  let assets: [Asset]
  static let assets: [Asset] = {
    guard let data = try? ContentBundle.data("Data/published-pages.json"),
      let value = try? JSONDecoder().decode(Self.self, from: data)
    else { return [] }
    return value.assets
  }()
}

struct NotesSourceReaderView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var page = 1
  @State private var pageInput = "1"
  @State private var mode: NotesReaderMode = .read
  @State private var pen: NotesPen = .pen
  @State private var color = UIColor(red: 124 / 255, green: 58 / 255, blue: 237 / 255, alpha: 1)
  @State private var width: CGFloat = 3
  @State private var colorHex: UInt = 0x7c3aed
  @State private var finger = false
  @State private var drawingData = Data()
  @State private var inkNote: StudyNote?
  @State private var inkDirty = false
  @State private var saveTask: Task<Void, Never>?
  @State private var saving = false
  @State private var error: String?
  @State private var showNotes = false
  @State private var showClear = false
  @State private var editor: StudyNote?
  @State private var editorCloseRequest = 0
  @State private var region: CGRect?
  @State private var regionPreview: UIImage?
  @State private var showRegion = false
  @State private var regionNote: StudyNote?
  @State private var regionSession = UUID()
  @State private var selectedText = ""
  @State private var canvas = NotesCanvasController()
  @State private var zoom: CGFloat = 1
  @State private var magnifyBase: CGFloat = 1
  @State private var pageForward = true
  @State private var regionStart: CGPoint?
  @State private var regionPoints: [CGPoint] = []
  @State private var ready = false
  @State private var availableWidth: CGFloat = 0
  @State private var availableHeight: CGFloat = 0
  @State private var showPageControls = false
  @State private var showInkReview = false
  @State private var recognizedText = ""
  @State private var organizedText = ""
  @State private var inkPhase = "idle"
  @State private var loadGeneration = UUID()
  @State private var capturedTarget: SourcePageTarget?
  @State private var capturedCourseID: String?
  @State private var capturedBookID: String?
  @State private var lastSavedDrawing = Data()
  @State private var cachedPDF: PDFDocument?
  @State private var cachedPageImage: UIImage?
  @State private var cachedMediaBookID = ""
  private var bookID: String {
    capturedBookID ?? capturedTarget?.bookID ?? store.selectedBookID ?? ""
  }
  private var source: SourceContent? { store.sources.first { $0.id == bookID } }
  private var maxPage: Int { max(1, source?.summary.page_count ?? capturedTarget?.pageEnd ?? 1) }
  private var chapter: Chapter? {
    let matching = source?.chapters.filter { $0.page_start <= page && $0.page_end >= page } ?? []
    let sections = matching.filter { $0.level <= 2 }
    return (sections.isEmpty ? matching : sections).sorted { lhs, rhs in
      lhs.level == rhs.level
        ? lhs.page_end - lhs.page_start < rhs.page_end - rhs.page_start : lhs.level > rhs.level
    }.first
  }
  private var heading: String {
    chapter?.source_title ?? capturedTarget?.title ?? source?.summary.title ?? "教材原文"
  }
  private var imagePath: String? {
    NotesPageManifest.assets.first { $0.book_id == bookID && $0.pdf_page == page }?.url
  }
  private var fileURL: URL? { store.sourceFileURL(bookID) }
  private var pdf: PDFDocument? { cachedPDF }
  private var pageImage: UIImage? { cachedPageImage }
  private func refreshMedia() {
    if cachedMediaBookID != bookID {
      cachedMediaBookID = bookID
      cachedPDF = fileURL.flatMap {
        $0.pathExtension.lowercased() == "pdf" ? PDFDocument(url: $0) : nil
      }
    }
    if let path = imagePath, let image = ContentBundle.image(path) {
      cachedPageImage = image
    } else if let pdfPage = cachedPDF?.page(at: page - 1) {
      cachedPageImage = pdfPage.thumbnail(of: CGSize(width: 1200, height: 1700), for: .mediaBox)
    } else if let fileURL {
      cachedPageImage = UIImage(contentsOfFile: fileURL.path)
    } else {
      cachedPageImage = nil
    }
  }
  private var pageText: String {
    let chunks =
      source?.chunks.filter { $0.page_start <= page && $0.page_end >= page }.map(\.text).joined(
        separator: "\n\n") ?? ""
    if !chunks.isEmpty { return chunks }
    if let text = pdf?.page(at: page - 1)?.string, !text.isEmpty { return text }
    return page == capturedTarget?.page ? capturedTarget?.sourceText ?? "" : ""
  }
  private var pageNotes: [StudyNote] {
    store.notes.filter {
      $0.anchor?.bookID == bookID && $0.anchor?.page == page
        && ($0.anchor?.courseID == nil || $0.anchor?.courseID == capturedCourseID)
    }
  }
  var body: some View {
    VStack(spacing: 0) {
      header
      tools
      if let error {
        HStack {
          Text(error).font(DS.font(13)).foregroundStyle(DS.danger)
          Spacer()
          Button("重试保存") { Task { _ = await flushInk() } }
        }
        .padding(12).background(Color(hex: 0xfff0f3))
      }
      GeometryReader { bounds in
        ScrollView([.vertical, .horizontal]) {
          pageContent.id("\(bookID):\(page)")
            .transition(
              .asymmetric(
                insertion: .offset(x: pageForward ? 24 : -24).combined(with: .opacity),
                removal: .opacity)
            )
            .frame(minWidth: bounds.size.width, minHeight: bounds.size.height, alignment: .center)
        }.background(store.isPad ? DS.background : .white)
          .onAppear {
            availableWidth = bounds.size.width
            availableHeight = bounds.size.height
          }
          .onChange(of: bounds.size) { _, size in
            availableWidth = size.width
            availableHeight = size.height
          }
          .overlay(alignment: .topTrailing) {
            if showNotes && !pageNotes.isEmpty {
              ScrollView { pageNotesList }.frame(maxWidth: 320, maxHeight: 420).padding(8).shadow(
                color: DS.text.opacity(0.15), radius: 12, y: 8)
            }
          }
          .overlay(alignment: .bottom) {
            if showInkReview && mode == .ink {
              ScrollView { inkReview }.frame(maxWidth: 380, maxHeight: 440).padding(8)
            } else if mode == .ink {
              BCButton("识别并整理", icon: "Sparkles", style: .secondary) {
                Task {
                  guard await flushInk() else { return }
                  recognizedText =
                    inkNote?.body.isEmpty == false && inkNote?.body != "手写笔迹已保存，尚未整理。"
                    ? inkNote!.body : NotesFixtures.recognizedInk
                  showInkReview = true
                  inkPhase = "confirmation"
                }
              }.frame(maxWidth: 220).padding(12)
            } else if mode == .ai {
              Text("用笔圈出区域，松手后弹出 AI 对话").font(DS.font(13)).foregroundStyle(DS.primary).padding(10)
                .background(.white, in: Capsule()).padding(10).allowsHitTesting(false)
            } else if mode == .text {
              Text("点击原文任意位置，添加文字或语音批注").font(DS.font(13)).foregroundStyle(DS.primary).padding(10)
                .background(.white, in: Capsule()).padding(10).allowsHitTesting(false)
            }
          }
      }
    }.background(.white).ignoresSafeArea(edges: .bottom)
      .task { await initialize() }
      .onChange(of: drawingData) { _, _ in
        guard ready, drawingData != lastSavedDrawing else { return }
        inkDirty = true
        saveTask?.cancel()
        saveTask = Task {
          try? await Task.sleep(for: .milliseconds(500))
          guard !Task.isCancelled else { return }
          _ = await flushInk()
        }
      }
      .onDisappear {
        saveTask?.cancel()
        if inkDirty { Task { _ = await flushInk() } }
      }
      .onChange(of: store.sourceTarget) { _, value in
        guard value != capturedTarget else { return }
        Task {
          guard await flushInk() else { return }
          capturedTarget = value
          capturedCourseID = store.selectedCourseID
          capturedBookID = value?.bookID ?? store.selectedBookID
          page = NotesReadingPosition.initialPage(
            courseID: capturedCourseID, bookID: bookID, target: value, locations: store.locations,
            pageCount: maxPage)
          pageInput = "\(page)"
          NotesReadingPosition.save(page, courseID: capturedCourseID, bookID: bookID, store: store)
          ready = false
          closeRegion()
          editor = nil
          selectedText = ""
          zoom = 1
          magnifyBase = 1
          showInkReview = false
          refreshMedia()
          await loadInk()
        }
      }
      .overlay {
        if let note = editor, !showRegion {
          NotesPopup(
            width: 340, height: note.kind == .voice ? 650 : 390, anchor: note.position,
            topInset: 100, identifier: "reader.annotationDialog",
            onOutside: { editorCloseRequest += 1 }
          ) {
            if note.kind == .voice {
              NotesVoiceNoteView(
                existingNote: note, embedded: true, closeRequest: editorCloseRequest,
                onClose: { editor = nil })
            } else {
              NotesTextEditor(
                note: note, onSaved: { editor = nil }, compact: true,
                closeRequest: editorCloseRequest, onClose: { editor = nil },
                onAskAI: { savedNote in
                  editor = savedNote
                  regionNote = savedNote
                  regionSession = UUID()
                  showRegion = true
                })
            }
          }.id(note.id)
        }
      }
      .overlay {
        if showRegion {
          NotesPopup(
            width: store.isPad ? min(520, availableWidth - 48) : availableWidth - 48,
            height: min(720, availableHeight * 0.82), topInset: 100,
            bottomAligned: true, rightInset: 24, identifier: "reader.regionDialog", onOutside: closeRegion
          ) {
            NotesRegionAIView(bookID: bookID, chapterID: chapter?.id, page: page,
                              pageText: pageText, referenceImage: regionPreview,
                              note: regionNote, onClose: closeRegion).id(regionSession)
          }
        }
      }
      .overlay {
        if showClear {
          NotesPopup(width: 400, height: 210, dim: true, onOutside: { showClear = false }) {
            VStack(alignment: .leading, spacing: 18) {
              Text("确认清空本页全部批注吗？").font(DS.font(18, weight: .bold))
              Text("取消将保留当前笔迹；清空后仍可使用撤销恢复。").font(DS.font(14)).foregroundStyle(DS.secondary)
              HStack {
                BCButton("取消", style: .secondary) { showClear = false }
                BCButton("清空", style: .danger) {
                  canvas.clear()
                  showClear = false
                }
              }
            }.padding(24)
          }
        }
      }
  }
  private var pageWidth: CGFloat {
    let ratio = pageImage.map { $0.size.height / $0.size.width } ?? 920 / 650
    guard availableWidth > 0, availableHeight > 0 else {
      return min(650, UIScreen.main.bounds.width)
    }
    return min(availableWidth, availableHeight / ratio)
  }
  private var header: some View {
    HStack(alignment: .top, spacing: 0) {
      Button {
        Task { if await flushInk() { store.back() } }
      } label: {
        Image(systemName: "arrow.left").font(.system(size: 18)).foregroundStyle(DS.text).frame(
          width: 44, height: 44
        )
        .background(.white, in: RoundedRectangle(cornerRadius: 12)).overlay(
          RoundedRectangle(cornerRadius: 12).stroke(DS.border))
      }.offset(y: DS.navigationButtonOffset)
        .accessibilityLabel("返回").accessibilityIdentifier("navigation.back")
      Button {
        showPageControls = true
      } label: {
        Text(heading).font(DS.font(27.2, weight: .bold)).foregroundStyle(DS.text).lineLimit(1)
          .frame(maxWidth: .infinity, alignment: .leading)
      }
      .buttonStyle(.plain).accessibilityLabel("\(heading)，PDF第\(page)页，点击跳转")
      .accessibilityIdentifier("reader.heading")
      .popover(isPresented: $showPageControls) {
        VStack(spacing: 14) {
          Text("PDF 第 \(page) 页 / \(maxPage) 页").font(DS.font(14, weight: .bold))
          pager
          Button("缩放 \(Int(zoom * 100))%") {
            zoom = zoom >= 2 ? 1 : zoom + 0.25
            magnifyBase = zoom
          }.font(DS.font(14)).padding(10)
        }.padding(20).frame(width: 330).presentationCompactAdaptation(.popover)
      }
      if saving { ProgressView().scaleEffect(0.7).accessibilityLabel("正在保存笔迹") }
    }.frame(height: DS.navigationRowHeight).padding(.horizontal, store.isPad ? 32 : 16)
      .padding(.top, NotesScreenMetrics.titleTopInset).padding(.bottom, 1)
      .background { Color.white.ignoresSafeArea(edges: .top) }
  }
  private var tools: some View {
    HStack(spacing: 4) {
      tool("Sparkles", label: "圈选问 AI", selected: mode == .ai) { changeMode(.ai, toggle: true) }
        .foregroundStyle(DS.primary)
      Divider().frame(height: 34)
      if !store.isPad {
        noteShortcuts
        Divider().frame(height: 34)
      }
      ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 3) {
          tool("PenLine", label: "钢笔", selected: mode == .ink && pen == .pen) { selectPen(.pen) }
            .disabled(mode == .text || pageImage == nil)
          tool("Highlighter", label: "荧光笔", selected: mode == .ink && pen == .marker) {
            selectPen(.marker)
          }.disabled(mode == .text || pageImage == nil)
          tool("Eraser", label: "整笔擦除", selected: mode == .ink && pen == .eraser) {
            selectPen(.eraser)
          }.disabled(mode == .text || pageImage == nil)
          tool("Undo2", label: "撤销") { canvas.undo() }.opacity(mode == .ink ? 1 : 0.42)
          tool("Redo2", label: "重做") { canvas.redo() }.opacity(mode == .ink ? 1 : 0.42)
          tool("Trash2", label: "清空本页批注") { showClear = true }.opacity(mode == .ink ? 1 : 0.42)
          ForEach([0x7c3aed, 0x2563eb, 0xdc2626, 0x20263a], id: \.self) { value in
            Button {
              colorHex = UInt(value)
              color = UIColor(Color(hex: UInt(value)))
              if mode != .ink { changeMode(.ink) }
            } label: {
              Circle().fill(Color(hex: UInt(value))).frame(width: 20, height: 20).padding(7)
                .overlay(
                  Circle().stroke(DS.primary, lineWidth: 2).padding(1).opacity(
                    colorHex == UInt(value) ? 1 : 0)
                )
                .frame(width: 44, height: 44)
            }.accessibilityLabel("笔迹颜色 \(value)")
          }
          ForEach([CGFloat(1.5), 3, 6], id: \.self) { value in
            Button {
              width = value
              if mode != .ink { changeMode(.ink) }
            } label: {
              Circle().fill(width == value ? DS.primary : DS.secondary).frame(
                width: value + 4, height: value + 4
              ).frame(width: 44, height: 44).background(
                width == value ? DS.primarySoft : .clear, in: RoundedRectangle(cornerRadius: 10))
            }.accessibilityLabel("笔迹粗细 \(Int(value))")
          }
          tool("Hand", label: "手指书写", selected: finger) { finger.toggle() }
        }
      }
      if store.isPad {
        Divider().frame(height: 34)
        noteShortcuts
      }
    }.padding(.horizontal, 8).frame(height: 56).background(.white).overlay(alignment: .bottom) {
      Rectangle().fill(DS.border).frame(height: 1)
    }
  }
  private var noteShortcuts: some View {
    HStack(spacing: 2) {
      tool("FileText", label: "批注", selected: mode == .text) { changeMode(.text, toggle: true) }
      tool("NotebookPen", label: "本页笔记 \(pageNotes.count) 条", selected: showNotes) {
        showNotes.toggle()
        changeMode(.read)
      }.opacity(pageNotes.isEmpty ? 0.45 : 1)
    }
  }
  private func tool(
    _ icon: String, label: String, selected: Bool = false, action: @escaping () -> Void
  ) -> some View {
    Button(action: action) {
      NotesGlyph(name: icon, size: 20).foregroundStyle(
        icon == "Sparkles" || selected ? DS.primary : DS.secondary
      ).frame(width: 44, height: 44).background(
        selected ? DS.primarySoft : .clear, in: RoundedRectangle(cornerRadius: 10))
    }
    .buttonStyle(.plain).accessibilityLabel(label).accessibilityAddTraits(
      selected ? .isSelected : [])
  }
  private var pageContent: some View {
    let ratio = pageImage.map { $0.size.height / $0.size.width } ?? 1.4
    let size = CGSize(width: pageWidth, height: pageWidth * ratio)
    return ZStack(alignment: .topLeading) {
      if let imagePath {
        AssetImage(imagePath).frame(width: size.width, height: size.height)
      } else if let pdf {
        NotesPDFPage(document: pdf, page: page, selectedText: $selectedText).allowsHitTesting(
          mode == .read)
      } else if let image = pageImage {
        Image(uiImage: image).resizable().scaledToFit()
      } else {
        VStack(alignment: .leading, spacing: 18) {
          Text(pageText.isEmpty ? "原文资源未发布" : heading).font(DS.font(24, weight: .bold))
          Text(pageText.isEmpty ? "当前教材只有目录或尚未提供该页原文。请返回课程资料查看。" : pageText).font(DS.font(17))
            .lineSpacing(9).textSelection(.enabled)
        }.padding(24).frame(width: size.width, height: size.height, alignment: .topLeading)
          .background(.white)
      }
      NotesDrawingCanvas(
        data: $drawingData, controller: canvas, pen: pen, color: color, width: width,
        finger: finger, active: mode == .ink
      )
      .allowsHitTesting(mode == .ink)
      if mode == .text {
        Color.clear.contentShape(Rectangle()).onTapGesture { location in
          createAnnotation(
            at: UnitPointValue(
              x: min(1, max(0, location.x / size.width)),
              y: min(1, max(0, location.y / size.height))))
        }
      }
      ForEach(pageNotes.filter { $0.kind != .ink }) { note in
        if let point = note.position {
          Button {
            editor = note
          } label: {
            StickerIcon(note.kind.notesIcon, tone: note.kind.notesTone, size: 28).padding(3)
              .background(.white, in: Circle()).shadow(radius: 3)
          }
          .position(x: CGFloat(point.x) * size.width, y: CGFloat(point.y) * size.height)
          .accessibilityLabel("打开批注：\(note.title)")
        }
      }
      if mode == .ai {
        Color.clear.contentShape(Rectangle()).gesture(
          DragGesture(minimumDistance: 2).onChanged { value in
            if regionStart == nil {
              regionStart = value.startLocation
              regionPoints = [value.startLocation]
            }
            regionPoints.append(value.location)
            let xs = regionPoints.map(\.x)
            let ys = regionPoints.map(\.y)
            region = CGRect(
              x: xs.min() ?? 0, y: ys.min() ?? 0, width: (xs.max() ?? 0) - (xs.min() ?? 0),
              height: (ys.max() ?? 0) - (ys.min() ?? 0))
          }.onEnded { _ in captureRegion(size: size) })
        if region != nil {
          Path { path in
            if let start = regionPoints.first {
              path.move(to: start)
              path.addLines(regionPoints)
              if showRegion { path.closeSubpath() }
            }
          }.stroke(
            DS.primary,
            style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round, dash: [6, 3])
          ).allowsHitTesting(false)
        }
      }
    }.frame(width: size.width, height: size.height).background(.white).clipped()
      .scaleEffect(zoom, anchor: .topLeading).frame(
        width: size.width * zoom, height: size.height * zoom, alignment: .topLeading
      )
      .accessibilityElement(children: .contain)
      .accessibilityIdentifier("reader.page")
      .simultaneousGesture(
        MagnifyGesture().onChanged { value in
          guard mode == .read else { return }
          zoom = min(3, max(1, magnifyBase * value.magnification))
        }.onEnded { _ in magnifyBase = zoom }
      )
      .simultaneousGesture(
        DragGesture(minimumDistance: 60).onEnded { value in
          guard mode == .read, zoom == 1,
            abs(value.translation.width) > max(65, abs(value.translation.height) * 1.5)
          else { return }
          changePage(page + (value.translation.width < 0 ? 1 : -1))
        })
  }
  private var pager: some View {
    HStack(spacing: 10) {
      Button("上一页") { changePage(page - 1) }.disabled(page <= 1)
      Spacer(minLength: 4)
      TextField("页码", text: $pageInput).keyboardType(.numberPad).multilineTextAlignment(.center)
        .frame(width: 48, height: 40).background(.white, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(DS.border)).accessibilityIdentifier(
          "reader.pageInput")
      Button("跳转") { changePage(Int(pageInput) ?? page) }.accessibilityIdentifier("reader.jump")
      Spacer(minLength: 4)
      Button("下一页") { changePage(page + 1) }.disabled(page >= maxPage)
    }.font(DS.font(13, weight: .bold)).foregroundStyle(DS.primary).padding(.vertical, 6)
  }
  private var inkReview: some View {
    BCCard {
      VStack(alignment: .leading, spacing: 12) {
        if !showInkReview {
          Text("原始笔迹会自动保存在本机，整理版不会覆盖它们。").font(DS.font(12)).foregroundStyle(DS.muted)
          BCButton("识别并整理", icon: "Sparkles", style: .secondary) {
            Task {
              guard await flushInk() else { return }
              recognizedText =
                inkNote?.body == "手写笔迹已保存，尚未整理。"
                ? NotesFixtures.recognizedInk : inkNote?.body ?? NotesFixtures.recognizedInk
              organizedText = inkNote?.organizedText ?? ""
              showInkReview = true
              inkPhase = "confirmation"
            }
          }.disabled(drawingData.isEmpty)
        } else {
          BCPill("手写识别 · 固定演示")
          Text("请确认识别文字").font(DS.font(18, weight: .bold))
          Text("本 Demo 不识别你的真实笔迹，以下为演示文字。请按原始笔迹修正后继续。").font(DS.font(12)).foregroundStyle(DS.muted)
          BCTextEditor(text: $recognizedText, placeholder: "修正识别结果").frame(minHeight: 120)
          BCButton("确认并继续整理", icon: "Save") {
            Task {
              guard var note = inkNote else { return }
              note.body = recognizedText
              note.updatedAt = .now
              note.organizedText =
                bookID == "book_biology_2"
                ? NotesFixtures.organizedInk : "## 手写笔记整理（演示）\n\n\(recognizedText)"
              do {
                try await store.saveNote(note)
                inkNote = note
                organizedText = note.organizedText ?? ""
                inkPhase = "complete"
              } catch { self.error = error.localizedDescription }
            }
          }.disabled(recognizedText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
          if inkPhase == "complete" {
            Text("独立整理版").font(DS.font(15, weight: .bold)).foregroundStyle(DS.primary)
            Text(organizedText).font(DS.font(14)).lineSpacing(5).textSelection(.enabled)
            if bookID == "book_biology_2" { NotesDemoEvidence() }
          }
          BCButton("收起整理面板", style: .text) { showInkReview = false }
        }
      }
    }
  }
  private var pageNotesList: some View {
    BCCard {
      VStack(alignment: .leading, spacing: 10) {
        Text("本页已有笔记 · \(pageNotes.count)").font(DS.font(13, weight: .bold))
        ForEach(pageNotes) { note in
          Button {
            if note.kind == .ink {
              mode = .ink
              Task { await loadInk(note) }
            } else {
              editor = note
            }
          } label: {
            HStack {
              Text(note.kind.notesLabel).foregroundStyle(DS.primary)
              Text(note.title).lineLimit(1)
              Spacer()
            }.font(DS.font(13)).padding(10).background(
              DS.primarySoft, in: RoundedRectangle(cornerRadius: 10))
          }
        }
      }
    }
  }
  private func closeRegion() {
    if let note = regionNote { editor = store.notes.first { $0.id == note.id } ?? note }
    showRegion = false
    regionNote = nil
    region = nil
    regionPoints = []
    regionStart = nil
  }
  private func captureRegion(size: CGSize) {
    guard let rect = region?.intersection(CGRect(origin: .zero, size: size)), rect.width > 8,
      rect.height > 8
    else {
      region = nil
      return
    }
    regionPreview = nil
    if let image = pageImage, let cg = image.cgImage {
      let sx = CGFloat(cg.width) / size.width
      let sy = CGFloat(cg.height) / size.height
      if let crop = cg.cropping(
        to: CGRect(
          x: rect.minX * sx, y: rect.minY * sy, width: rect.width * sx, height: rect.height * sy))
      {
        regionPreview = UIImage(cgImage: crop)
      }
    }
    regionNote = nil
    regionSession = UUID()
    showRegion = true
    regionStart = nil
  }
  private func makeAnchor() -> NoteAnchor {
    var anchor = NoteAnchor(bookID: bookID, page: page)
    anchor.courseID = capturedCourseID
    anchor.chapterID = chapter?.chapter_id
    anchor.printedPage = chapter?.printed_page_start.map {
      $0 + page - (chapter?.page_start ?? page)
    }
    anchor.quote = selectedText.isEmpty ? nil : selectedText
    return anchor
  }
  private func createAnnotation(at position: UnitPointValue) {
    var note = StudyNote(kind: .text, title: heading, body: "")
    note.anchor = makeAnchor()
    note.position = position
    editor = note
  }
  private func selectPen(_ next: NotesPen) {
    guard mode != .text, pageImage != nil else { return }
    let selected = mode == .ink && pen == next
    pen = next
    changeMode(selected ? .read : .ink)
  }
  private func changeMode(_ next: NotesReaderMode, toggle: Bool = false) {
    Task {
      if await flushInk() {
        mode = toggle && next == mode ? .read : next
        region = nil
      }
    }
  }
  private func changePage(_ candidate: Int) {
    let next = min(maxPage, max(1, candidate))
    guard next != page else {
      pageInput = "\(page)"
      return
    }
    Task {
      guard await flushInk() else { return }
      pageForward = next > page
      withAnimation(reduceMotion ? nil : DS.globalAnimation) {
        page = next
        pageInput = "\(page)"
      }
      NotesReadingPosition.save(page, courseID: capturedCourseID, bookID: bookID, store: store)
      closeRegion()
      editor = nil
      selectedText = ""
      zoom = 1
      magnifyBase = 1
      showInkReview = false
      ready = false
      drawingData = Data()
      inkNote = nil
      refreshMedia()
      await loadInk()
    }
  }
  private func initialize() async {
    capturedTarget = store.sourceTarget
    capturedCourseID = store.selectedCourseID
    capturedBookID = capturedTarget?.bookID ?? store.selectedBookID
    page = NotesReadingPosition.initialPage(
      courseID: capturedCourseID, bookID: bookID, target: capturedTarget,
      locations: store.locations, pageCount: maxPage)
    pageInput = "\(page)"
    NotesReadingPosition.save(page, courseID: capturedCourseID, bookID: bookID, store: store)
    refreshMedia()
    if let pending = NotesCaptureContext.pendingKind {
      mode = pending == .ink ? .ink : pending == .text ? .text : .read
    }
    let existing = NotesCaptureContext.existingNoteID.flatMap { id in
      store.notes.first { $0.id == id }
    }
    NotesCaptureContext.pendingKind = nil
    NotesCaptureContext.existingNoteID = nil
    if let existing, existing.kind != .ink { editor = existing }
    await loadInk(existing?.kind == .ink ? existing : nil)
  }
  private func loadInk(_ existing: StudyNote? = nil) async {
    ready = false
    let generation = UUID()
    loadGeneration = generation
    let record = existing ?? pageNotes.first { $0.kind == .ink }
    var data = Data()
    if let path = record?.drawingPath {
      data = (try? Data(contentsOf: store.attachmentURL(path))) ?? Data()
    }
    guard loadGeneration == generation else { return }
    inkNote = record
    lastSavedDrawing = data
    drawingData = data
    inkDirty = false
    if record?.id == "ink-demo-needs-confirmation", mode == .ink {
      recognizedText = record?.body ?? ""
      organizedText = record?.organizedText ?? ""
      showInkReview = true
      inkPhase = "confirmation"
    }
    await Task.yield()
    guard loadGeneration == generation else { return }
    ready = true
  }
  @discardableResult private func flushInk() async -> Bool {
    guard inkDirty else { return true }
    guard !saving else { return false }
    saving = true
    error = nil
    let data = drawingData
    let notePage = page
    var note = inkNote ?? StudyNote(kind: .ink, title: "\(heading) · 手写批注", body: "手写笔迹已保存，尚未整理。")
    note.anchor = makeAnchor()
    note.updatedAt = .now
    do {
      note.drawingPath = try await store.saveAttachment(data, extension: "drawing")
      try await store.saveNote(note)
      if page == notePage {
        inkNote = note
        lastSavedDrawing = data
        inkDirty = drawingData != data
      }
      saving = false
      return true
    } catch {
      self.error = "笔迹保存失败：\(error.localizedDescription)"
      saving = false
      return false
    }
  }
}

@MainActor final class NotesCanvasController {
  weak var canvas: PKCanvasView?
  func undo() { canvas?.undoManager?.undo() }
  func redo() { canvas?.undoManager?.redo() }
  func clear() {
    canvas?.drawing = PKDrawing()
    if let canvas { canvas.delegate?.canvasViewDrawingDidChange?(canvas) }
  }
}

private struct NotesDrawingCanvas: UIViewRepresentable {
  @Binding var data: Data
  let controller: NotesCanvasController
  let pen: NotesPen
  let color: UIColor
  let width: CGFloat
  let finger: Bool
  let active: Bool
  func makeCoordinator() -> Coordinator { Coordinator(self) }
  func makeUIView(context: Context) -> PKCanvasView {
    let view = NotesSizedCanvas()
    view.backgroundColor = .clear
    view.isOpaque = false
    view.isScrollEnabled = false
    view.delegate = context.coordinator
    view.minimumZoomScale = 1
    view.maximumZoomScale = 1
    controller.canvas = view
    view.onSize = { [weak coordinator = context.coordinator] size in coordinator?.resize(size) }
    return view
  }
  func updateUIView(_ view: PKCanvasView, context: Context) {
    context.coordinator.owner = self
    view.isUserInteractionEnabled = active
    view.drawingPolicy = finger ? .anyInput : .pencilOnly
    view.tool =
      pen == .eraser
      ? PKEraserTool(.vector)
      : PKInkingTool(
        pen == .marker ? .marker : .pen, color: color, width: pen == .marker ? width * 5 : width)
    context.coordinator.apply(data)
  }
  final class Coordinator: NSObject, PKCanvasViewDelegate {
    var owner: NotesDrawingCanvas
    var lastData = Data()
    var width: CGFloat = 0
    var applying = false
    init(_ owner: NotesDrawingCanvas) { self.owner = owner }
    func resize(_ size: CGSize) {
      guard size.width > 0, width != size.width, let canvas = owner.controller.canvas else {
        return
      }
      applying = true
      if width > 0 {
        canvas.drawing = canvas.drawing.transformed(
          using: CGAffineTransform(scaleX: size.width / width, y: size.width / width))
      } else if let normalized = try? PKDrawing(data: owner.data) {
        canvas.drawing = NotesDrawingCoordinates.displayed(normalized, displayWidth: size.width)
      }
      width = size.width
      applying = false
    }
    func apply(_ data: Data) {
      guard lastData != data, let canvas = owner.controller.canvas else { return }
      applying = true
      canvas.drawing = NotesDrawingCoordinates.displayed(
        (try? PKDrawing(data: data)) ?? PKDrawing(), displayWidth: max(width, 1))
      lastData = data
      applying = false
    }
    func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) {
      guard !applying, width > 0 else { return }
      let normalized = NotesDrawingCoordinates.normalized(canvasView.drawing, displayWidth: width)
        .dataRepresentation()
      lastData = normalized
      owner.data = normalized
    }
  }
}
private final class NotesSizedCanvas: PKCanvasView {
  var onSize: ((CGSize) -> Void)?
  override func layoutSubviews() {
    super.layoutSubviews()
    onSize?(bounds.size)
  }
}
private struct NotesPDFPage: UIViewRepresentable {
  func makeCoordinator() -> Coordinator { Coordinator(self) }
  final class Coordinator: NSObject {
    var owner: NotesPDFPage
    var page = -1
    weak var view: PDFView?
    init(_ owner: NotesPDFPage) { self.owner = owner }
    @objc func selectionChanged() { owner.selectedText = view?.currentSelection?.string ?? "" }
    deinit { NotificationCenter.default.removeObserver(self) }
  }
  let document: PDFDocument
  let page: Int
  @Binding var selectedText: String
  func makeUIView(context: Context) -> PDFView {
    let view = PDFView()
    view.backgroundColor = .white
    view.autoScales = true
    view.displayMode = .singlePage
    view.displayDirection = .vertical
    context.coordinator.view = view
    NotificationCenter.default.addObserver(
      context.coordinator, selector: #selector(Coordinator.selectionChanged),
      name: .PDFViewSelectionChanged, object: view)
    return view
  }
  func updateUIView(_ view: PDFView, context: Context) {
    context.coordinator.owner = self
    guard context.coordinator.page != page else { return }
    context.coordinator.page = page
    let one = PDFDocument()
    if let source = document.page(at: page - 1)?.copy() as? PDFPage { one.insert(source, at: 0) }
    view.document = one
    view.autoScales = true
  }
}

/// The reader keeps its own conversation. A source crop and a text annotation
/// never inherit unrelated messages from the global assistant.
struct NotesRegionAIView: View {
  @Environment(AppStore.self) private var store
  let bookID: String
  let chapterID: String?
  let page: Int
  let pageText: String
  let referenceImage: UIImage?
  let note: StudyNote?
  var onClose: () -> Void
  @State private var messages: [ChatMessage] = []
  @State private var question = ""
  @State private var loading = false
  @State private var issue: String?
  @State private var failedQuestion: String?
  @State private var request: Task<Void, Never>?
  @State private var reservationID: String?
  @FocusState private var inputFocused: Bool
  private var isTextNote: Bool { note != nil }
  private var referenceLabel: String { "PDF 第 \(page) 页 · \(isTextNote ? "文字批注" : "圈选区域")" }
  var body: some View {
    GeometryReader { viewport in
      let compact = viewport.size.height < 260
      VStack(alignment: .leading, spacing: 10) {
        if viewport.size.height >= 160 {
        HStack(spacing: 10) {
          NotesGlyph(name: "Sparkles", size: 16).foregroundStyle(.white)
            .frame(width: 34, height: 34)
            .background(DS.primary, in: RoundedRectangle(cornerRadius: 11))
          Text(isTextNote ? "批注 AI 对话" : "提问").font(DS.font(15, weight: .bold))
          Spacer(minLength: 0)
          Button(action: onClose) {
            NotesGlyph(name: "X", size: 18).frame(width: 40, height: 40)
          }.foregroundStyle(DS.secondary).accessibilityLabel("关闭")
        }.frame(height: 40)
        }
        if !compact {
        Text("剩余 \(store.credits) 积分 · 每次提问消耗 1 积分")
          .font(DS.font(12)).foregroundStyle(DS.secondary)
        }
        if !compact && (!inputFocused || viewport.size.height >= 430) {
          reference
        }
        ScrollViewReader { proxy in
          ScrollView {
            VStack(alignment: .leading, spacing: 10) {
              if messages.isEmpty {
                bubble(isTextNote
                  ? "我会结合你的批注和所在教材页一起讨论。可以检查你的理解，也可以继续追问。"
                  : "已收到你圈出的这一块原文。想问什么？例如这一处讲了什么、图里的箭头代表什么。",
                  user: false)
              }
              ForEach(messages) { message in
                VStack(alignment: .leading, spacing: 8) {
                  bubble(message.text, user: message.role == "user")
                    .accessibilityIdentifier(message.role == "user" ? "reader.regionQuestion" : loading ? "reader.regionPendingReply" : "reader.regionReply")
                  if !message.citations.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                      HStack(spacing: 6) {
                        BCGlyph("BookOpenCheck", color: DS.primary).frame(width: 13, height: 13)
                        Text("教材原文依据").font(DS.font(12, weight: .bold)).foregroundStyle(DS.primary)
                      }
                      ForEach(message.citations) { citation in
                        VStack(alignment: .leading, spacing: 4) {
                          Text("\(citation.chapterTitle) · 教材第 \(citation.printedPage ?? citation.page) 页")
                            .font(DS.font(12, weight: .bold)).foregroundStyle(DS.secondary)
                          Text(citation.quote).font(DS.font(12)).lineSpacing(4)
                            .foregroundStyle(DS.secondary).padding(.leading, 8)
                            .overlay(alignment: .leading) { Rectangle().fill(DS.primary.opacity(0.25)).frame(width: 2) }
                          Button("查看原文") {
                            onClose()
                            store.openCitation(citation)
                          }.font(DS.font(12, weight: .bold)).foregroundStyle(DS.primary)
                        }
                      }
                    }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
                      .background(DS.background, in: RoundedRectangle(cornerRadius: 12))
                      .overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.border))
                  } else if message.role != "user", message.text.contains("可靠") {
                    Text("当前教材未检索到可靠原文").font(DS.font(12, weight: .bold)).foregroundStyle(DS.muted)
                  }
                }.id(message.id)
              }
              if loading {
                bubble(isTextNote ? "正在结合批注和教材回答…" : "正在结合圈选内容回答…", user: false)
              }
              if let issue {
                Text(issue).font(DS.font(12)).foregroundStyle(DS.danger)
                if let failedQuestion {
                  Button("重试提问") { ask(failedQuestion) }
                    .font(DS.font(13, weight: .bold)).foregroundStyle(DS.primary)
                    .accessibilityIdentifier("reader.regionRetry")
                }
              }
              Color.clear.frame(height: 1).id("reader.regionTranscriptEnd")
            }.frame(maxWidth: .infinity, alignment: .leading)
          }.scrollDismissesKeyboard(.interactively)
            .onChange(of: messages.count) { _, _ in proxy.scrollTo("reader.regionTranscriptEnd", anchor: .bottom) }
            .onChange(of: loading) { _, _ in proxy.scrollTo("reader.regionTranscriptEnd", anchor: .bottom) }
        }.frame(maxHeight: .infinity)
        if !compact && (!inputFocused || viewport.size.height >= 430) {
          ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
              followup(isTextNote ? "检查我的理解" : "这一处讲了什么？",
                       question: isTextNote ? "我的这条批注理解正确吗？" : "这一处原文在讲什么？")
              followup("举个例子", question: isTextNote ? "用一个例子解释我批注中的内容" : "用一个例子解释圈选的内容")
            }
          }
        }
        VStack(alignment: .leading, spacing: 8) {
          if !compact {
            Text(isTextNote ? "针对这条批注提问" : "针对圈选区域提问")
              .font(DS.font(14, weight: .bold)).foregroundStyle(DS.secondary)
          }
          let layout = compact
            ? AnyLayout(HStackLayout(spacing: 8))
            : AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
          layout {
          TextField(isTextNote ? "例如：我的理解正确吗？" : "例如：这一处的箭头表示什么？", text: $question)
            .font(DS.font(compact ? 16 : 20)).padding(.horizontal, 12).frame(height: compact ? 44 : 48)
            .background(.white, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
            .focused($inputFocused).submitLabel(.send).onSubmit { ask(question) }
            .accessibilityIdentifier("reader.regionInput")
          BCButton(loading ? "正在提问…" : "提问") { ask(question) }
            .disabled(loading).accessibilityIdentifier("reader.regionSend")
            .frame(width: compact ? 72 : nil)
          }
        }.fixedSize(horizontal: false, vertical: true)
      }.padding(12).frame(width: viewport.size.width, height: viewport.size.height)
    }.task { messages = note?.conversation ?? [] }
      .onDisappear {
        request?.cancel()
        if let reservationID {
          self.reservationID = nil
          // Refund immediately even if native model inference is still finishing.
          Task { await store.refundCredits(reservationID) }
        }
      }
  }
  @ViewBuilder private var reference: some View {
    if let note {
      ScrollView {
        VStack(alignment: .leading, spacing: 6) {
          Text(referenceLabel).font(DS.font(12, weight: .bold)).foregroundStyle(DS.primaryPressed)
          Text(note.body).font(DS.font(13)).lineSpacing(7).foregroundStyle(DS.text)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 10).padding(.horizontal, 12)
      }.frame(height: 112)
        .background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 10))
    } else if let image = referenceImage {
      Image(uiImage: image).resizable().scaledToFit().frame(width: 72, height: 72)
        .background(.white, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(DS.border))
        .padding(6).background(DS.background, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.primary.opacity(0.25), style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
        .accessibilityLabel("圈选区域参照图")
    }
  }
  private func bubble(_ text: String, user: Bool) -> some View {
    Text(text).font(DS.font(20)).lineSpacing(8).textSelection(.enabled)
      .padding(12).frame(maxWidth: .infinity, alignment: .leading)
      .background(user ? DS.primarySoft : .white, in: RoundedRectangle(cornerRadius: 18))
      .overlay(RoundedRectangle(cornerRadius: 18).stroke(DS.border))
  }
  private func followup(_ title: String, question: String) -> some View {
    Button(title) { ask(question) }.font(DS.font(12, weight: .bold)).foregroundStyle(DS.primary)
      .padding(.horizontal, 12).frame(height: 40)
      .background(.white, in: Capsule()).overlay(Capsule().stroke(DS.border)).disabled(loading)
  }
  private func persistConversation() async {
    guard let note, var latest = store.notes.first(where: { $0.id == note.id }) else { return }
    latest.conversation = messages
    do { try await store.saveNote(latest) }
    catch { issue = "对话暂时未能保存，当前内容仍可查看。" }
  }
  private func ask(_ raw: String) {
    let input = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !input.isEmpty, !loading else { return }
    loading = true
    issue = nil
    failedQuestion = nil
    inputFocused = false
    let history = messages
    let courseID = store.selectedCourseID
    let selectedBookID = store.selectedBookID
    let sourceBookIDs = store.currentCourse?.resourceIDs ?? [bookID]
    let sources = store.sources
    request = Task { @MainActor in
      var reservation: String?
      do {
        if store.cloud == nil { reservation = try await store.reserveCredits(1) }
        reservationID = reservation
        try Task.checkCancellation()
        if question.trimmingCharacters(in: .whitespacesAndNewlines) == input { question = "" }
        messages.append(ChatMessage(role: "user", text: input))
        await persistConversation()
        let context = note.map { "文字批注：\($0.body)\n" } ?? ""
        let contextualQuestion = "\(input)\n\(context)（PDF 第 \(page) 页；当前页文字上下文：\(String(pageText.prefix(1200)))）"
        let result = try await store.answerAssistant(question: contextualQuestion, bookID: bookID,
          bookIDs: sourceBookIDs, chapterID: chapterID, sources: sources, history: history)
        try Task.checkCancellation()
        guard store.selectedCourseID == courseID, store.selectedBookID == selectedBookID else {
          throw CancellationError()
        }
        if let reservation { await store.completeCredits(reservation) }
        reservation = nil
        reservationID = nil
        messages.append(ChatMessage(role: "assistant", text: result.answer, citations: result.citations))
        await persistConversation()
      } catch {
        if let reservation { await store.refundCredits(reservation) }
        reservationID = nil
        if !Task.isCancelled {
          issue = error.localizedDescription
          failedQuestion = input
        }
      }
      loading = false
    }
  }
}
