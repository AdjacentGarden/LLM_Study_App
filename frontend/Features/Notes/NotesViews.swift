import SwiftUI

@MainActor enum NotesCaptureContext {
  static var pendingKind: NoteKind?
  static var existingNoteID: String?
  static var position: UnitPointValue?
}

struct NotesRouterView: View {
  let route: Route
  var body: some View {
    switch route {
    case .source: NotesSourceReaderView()
    case .voiceNote: NotesVoiceNoteView()
    default: NotesHubView()
    }
  }
}

extension NoteKind {
  var notesLabel: String {
    switch self {
    case .text: "文字"
    case .ink: "手写"
    case .voice: "语音"
    }
  }
  var notesIcon: String {
    switch self {
    case .text: "FileText"
    case .ink: "PenLine"
    case .voice: "Mic2"
    }
  }
  var notesTone: String {
    switch self {
    case .text: "mint"
    case .ink: "yellow"
    case .voice: "blue"
    }
  }
}

struct NotesHubView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var guide = false
  @State private var filter: NoteKind?
  @State private var selectedID: String?
  @State private var selectedGuideID: String?
  @State private var editor: StudyNote?
  @State private var editorCloseRequest = 0
  @State private var deleting: StudyNote?
  @State private var hubWidth: CGFloat = 834
  private var filtered: [StudyNote] {
    store.notes.filter { note in
      let allowed =
        store.currentCourse.map { course in
          guard let anchor = note.anchor else { return true }
          return anchor.courseID.map { $0 == course.id }
            ?? course.resourceIDs.contains(anchor.bookID)
        } ?? true
      return allowed && (filter == nil || note.kind == filter)
    }.sorted { $0.updatedAt > $1.updatedAt }
  }
  private var selected: StudyNote? { filtered.first { $0.id == selectedID } ?? filtered.first }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 12) {
        if store.isPad {
          HStack(spacing: 12) {
            tabs.frame(width: 230)
            newButton
          }
        } else {
          VStack(spacing: 12) {
            tabs
            newButton
          }
        }
        if !guide { filters }
        if guide {
          guideContent
        } else if filtered.isEmpty {
          empty
        } else {
          if store.isPad {
            HStack(alignment: .top, spacing: 22) {
              detail.frame(width: max(250, (hubWidth - 86) * 0.36))
              noteList.frame(maxWidth: .infinity)
            }
          } else {
            detail
            noteList
          }
        }
        if store.isPad {
          HStack(spacing: 12) { noteActions }.padding(.top, 7)
        } else {
          VStack(spacing: 12) { noteActions }
        }
      }.frame(maxWidth: 1120).padding(.horizontal, store.isPad ? 32 : 16).padding(
        .top, store.isPad ? 21 : 12
      ).padding(.bottom, 20).frame(maxWidth: .infinity)
    }
    .background(
      GeometryReader { geometry in
        Color.clear.onAppear { hubWidth = geometry.size.width }.onChange(of: geometry.size.width) {
          _, value in hubWidth = value
        }
      }
    )
    .task { await store.reloadNotes() }
    .overlay {
      if let note = editor {
        NotesPopup(width: 560, height: 650, dim: true, onOutside: { editorCloseRequest += 1 }) {
          NotesTextEditor(note: note, closeRequest: editorCloseRequest, onClose: { editor = nil })
        }.id(note.id)
      }
    }
    .alert(
      "删除这条笔记？", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })
    ) {
      Button("取消", role: .cancel) { deleting = nil }
      Button("删除", role: .destructive) {
        if let note = deleting { Task { await store.deleteNote(note.id) } }
        deleting = nil
      }
    } message: {
      Text("删除后无法恢复原始记录与整理版。")
    }
  }
  private var tabs: some View {
    HStack(spacing: 4) {
      ForEach([false, true], id: \.self) { value in
        Button {
          withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { guide = value }
        } label: {
          Text(value ? "AI 导学" : "我的笔记").font(DS.font(20, weight: .bold))
            .foregroundStyle(guide == value ? .white : DS.secondary)
            .fixedSize(horizontal: true, vertical: false).padding(.horizontal, 18)
            .frame(maxWidth: .infinity, minHeight: 44).background(
              guide == value ? DS.primary : .clear, in: RoundedRectangle(cornerRadius: 10))
        }.buttonStyle(.plain).accessibilityIdentifier(value ? "notes.guide" : "notes.mine")
      }
    }.padding(5).background(.white.opacity(0.76), in: RoundedRectangle(cornerRadius: 14))
      .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
  }
  private var newButton: some View {
    NotesPrimaryAction(title: "新建笔记", symbol: "plus") { store.openSheet(.noteType) }
  }
  @ViewBuilder private var noteActions: some View {
    NotesPrimaryAction(title: "查看课程闪卡", symbol: "book") { store.go(.flashcards) }
      .accessibilityIdentifier("notes.flashcards")
    NotesPrimaryAction(title: "导出 PDF", symbol: "doc.badge.arrow.up", secondary: true) {
      store.go(.export)
    }.accessibilityIdentifier("notes.export")
    if store.currentSource == nil {
      NotesPrimaryAction(title: "上传教材", symbol: "Upload", secondary: true) { store.go(.upload) }
    }
  }
  private var filters: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        filterButton(nil)
        filterButton(.text)
        filterButton(.ink)
        filterButton(.voice)
      }
    }
  }
  private func filterButton(_ value: NoteKind?) -> some View {
    Button {
      filter = value
    } label: {
      HStack(spacing: 7) {
        Text(value?.notesLabel ?? "全部")
        Text("\(store.notes.filter { value == nil || $0.kind == value }.count)")
          .font(DS.font(20, weight: .bold)).padding(.horizontal, 6).padding(.vertical, 2)
          .background(DS.primary.opacity(0.1), in: Capsule())
      }.font(DS.font(20, weight: .bold)).foregroundStyle(
        filter == value ? DS.primary : DS.secondary
      )
      .padding(.horizontal, 13).frame(minHeight: 44)
      .background(filter == value ? DS.primarySoft : .white, in: RoundedRectangle(cornerRadius: 10))
      .overlay(RoundedRectangle(cornerRadius: 10).stroke(filter == value ? DS.primary : DS.border))
    }.buttonStyle(.plain)
  }
  private var empty: some View {
    BCCard {
      VStack(spacing: 12) {
        AssetImage("/assets/brand/empty-states/no-notes-v1.png").frame(width: 180, height: 140)
        Text("把课堂想法留在教材旁").font(DS.font(20, weight: .bold))
        Text("你可以输入文字、直接批注教材原页，或录下语音后整理。")
          .font(DS.font(14)).foregroundStyle(DS.muted).multilineTextAlignment(.center)
        BCButton("新建第一条笔记", icon: "Plus") { store.openSheet(.noteType) }
      }.frame(maxWidth: .infinity).padding(.vertical, 24)
    }
  }
  private var noteList: some View {
    LazyVStack(spacing: 8) {
      ForEach(filtered) { note in
        Button {
          withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { selectedID = note.id }
        } label: {
          HStack(alignment: .top, spacing: 12) {
            NotesSticker(kind: note.kind, glyphSize: 18, size: 44)
            VStack(alignment: .leading, spacing: 5) {
              (Text(note.kind.notesLabel).font(DS.font(13, weight: .bold)).foregroundColor(
                DS.primary)
                + Text(note.anchor.map { " · 第 \($0.printedPage ?? $0.page) 页" } ?? "").font(
                  DS.font(12)
                ).foregroundColor(DS.muted))
              Text(note.title).font(DS.font(16, weight: .bold)).foregroundStyle(DS.text).lineLimit(
                2)
              Text(preview(note)).accessibilityIdentifier("notes.originalBody").font(DS.font(20)).foregroundStyle(DS.secondary).lineLimit(2)
            }.frame(maxWidth: .infinity, alignment: .leading)
          }.padding(16).background(selected?.id == note.id ? DS.primarySoft : .white)
            .overlay(
              Rectangle().stroke(
                !store.isPad && selected?.id == note.id ? DS.primary : .clear, lineWidth: 3
              ).padding(-3))
        }.buttonStyle(.plain)
      }
    }
  }
  @ViewBuilder private var detail: some View {
    if let note = selected {
      BCCard {
        VStack(alignment: .leading, spacing: 0) {
          HStack(alignment: .top, spacing: 12) {
            NotesSticker(kind: note.kind, glyphSize: 18, size: 44)
            VStack(alignment: .leading, spacing: 6) {
              NotesBadge(title: note.kind.notesLabel + "笔记")
              Text(note.title).font(DS.font(20, weight: .bold)).lineSpacing(5).fixedSize(
                horizontal: false, vertical: true)
              Text(
                note.anchor.map { "第 \($0.printedPage ?? $0.page) 页" } ?? "本地学习笔记"
              ).font(DS.font(12)).foregroundStyle(DS.muted).frame(minHeight: 18, alignment: .top)
            }
          }
          if let quote = note.anchor?.quote, !quote.isEmpty {
            NotesQuote(quote: quote).padding(.top, 20)
          }
          Text(preview(note)).font(DS.font(20)).lineSpacing(6).foregroundStyle(DS.secondary)
            .textSelection(.enabled).padding(.top, 35).padding(.bottom, 36)
          if note.kind != .voice, let organized = note.organizedText {
            VStack(alignment: .leading, spacing: 7) {
              Text("独立整理版").font(DS.font(16, weight: .bold)).foregroundStyle(DS.primary)
              Text(organized).font(DS.font(20)).lineSpacing(5).textSelection(.enabled)
            }.padding(13).frame(maxWidth: .infinity, alignment: .leading).background(
              Color(hex: 0xf3f8ff), in: RoundedRectangle(cornerRadius: 14)
            ).padding(.bottom, 16)
          }
          NotesPrimaryAction(
            title: note.kind == .ink ? "回到教材批注" : note.kind == .voice ? "打开语音笔记" : "编辑笔记"
          ) { reopen(note) }
        }.padding(.bottom, 2).contextMenu {
          Button("编辑笔记") { reopen(note) }
          if let anchor = note.anchor {
            Button("查看原文") {
              store.openSource(
                SourcePageTarget(
                  bookID: anchor.bookID, title: note.title, page: anchor.page, from: .notes))
            }
          }
          Button("删除笔记", role: .destructive) { deleting = note }
        }
      }.id(note.id)
    }
  }
  private struct GuideItem: Identifiable {
    let id: String
    let title: String
    let body: String
    let label: String
  }
  private var guideItems: [GuideItem] {
    guard let source = store.currentSource else { return [] }
    return source.chunks.prefix(4).map {
      GuideItem(id: "chunk:" + $0.id, title: "RAG 片段：第 \($0.page_start)-\($0.page_end) 页",
                body: String($0.text.prefix(240)), label: "教材摘录")
    } + source.assets.prefix(2).map {
      GuideItem(id: "asset:" + $0.id,
                title: "课程插图：\($0.source_type == "extracted" ? "源文件抽取" : "AI 生成")",
                body: $0.caption, label: "课程插图")
    }
  }
  private var selectedGuide: GuideItem? {
    guideItems.first { $0.id == selectedGuideID } ?? guideItems.first
  }
  @ViewBuilder private var guideContent: some View {
    if store.isPad {
      HStack(alignment: .top, spacing: 22) {
        guideDetail.frame(width: max(250, (hubWidth - 86) * 0.36))
        guideList.frame(maxWidth: .infinity)
      }
    } else {
      guideDetail
      guideList
    }
  }
  private var guideDetail: some View {
    BCCard {
      if let item = selectedGuide {
        VStack(alignment: .leading, spacing: 14) {
          NotesBadge(title: item.label)
          Text(item.title).font(DS.font(20, weight: .bold)).accessibilityIdentifier("notes.guideDetail")
          Text(item.body).accessibilityIdentifier("notes.guideBody").font(DS.font(20)).lineSpacing(8).foregroundStyle(DS.secondary)
        }.frame(maxWidth: .infinity, alignment: .leading)
      } else {
        VStack(spacing: 10) {
          NotesGlyph(name: "Sparkles", size: 30).foregroundStyle(DS.primary)
          Text("暂无 AI 导学内容").font(DS.font(20, weight: .bold))
        }.frame(maxWidth: .infinity, minHeight: 260)
      }
    }
  }
  private var guideList: some View {
    LazyVStack(spacing: 8) {
      if guideItems.isEmpty {
        BCCard { Text("当前教材还没有可展示的 AI 导学内容。").font(DS.font(20)).foregroundStyle(DS.muted) }
      }
      ForEach(Array(guideItems.enumerated()), id: \.element.id) { index, item in
        Button {
          withAnimation(reduceMotion ? nil : DS.localAnimation) { selectedGuideID = item.id }
        } label: {
          HStack(alignment: .top, spacing: 12) {
            NotesGlyph(name: "Sparkles", size: 18).foregroundStyle(DS.primary)
            VStack(alignment: .leading, spacing: 5) {
              Text(item.title).font(DS.font(16, weight: .bold)).foregroundStyle(DS.text)
              Text(item.body).font(DS.font(20)).lineSpacing(6).foregroundStyle(DS.secondary).lineLimit(3)
            }.frame(maxWidth: .infinity, alignment: .leading)
          }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(selectedGuide?.id == item.id ? DS.primarySoft : .white)
        }.buttonStyle(.plain).accessibilityIdentifier("notes.guideItem.\(index)")
      }
    }
  }
  private func preview(_ note: StudyNote) -> String {
    if note.kind == .text { return note.body }
    if note.kind == .voice {
      return NotesAnnotationRecord.voicePreview(note)
    }
    return note.body.isEmpty ? "手写笔迹已保存，尚未整理。" : note.body
  }
  private func reopen(_ note: StudyNote) {
    if note.kind == .text && note.anchor == nil {
      editor = note
      return
    }
    NotesCaptureContext.existingNoteID = note.id
    NotesCaptureContext.pendingKind = note.kind
    if let anchor = note.anchor {
      let target = SourcePageTarget(
        bookID: anchor.bookID, title: note.title, page: anchor.page,
        printedPage: anchor.printedPage, from: .notes)
      if note.kind != .voice {
        store.openSource(target)
        return
      }
      if store.selectedBookID != anchor.bookID { store.selectSource(anchor.bookID) }
      store.sourceTarget = target
    }
    store.go(note.kind == .voice ? .voiceNote : .source)
  }
}

struct NotesQuote: View {
  let quote: String
  var fontSize: CGFloat = 20
  var rounded = false
  var body: some View {
    HStack(spacing: 0) {
      Rectangle().fill(DS.primary).frame(width: 3)
      Text(quote).font(DS.font(fontSize)).lineSpacing(4).foregroundStyle(DS.secondary).padding(12).frame(
        maxWidth: .infinity, alignment: .leading)
    }.fixedSize(horizontal: false, vertical: true).background(DS.primarySoft)
      .clipShape(UnevenRoundedRectangle(topLeadingRadius: 0, bottomLeadingRadius: 0,
                                      bottomTrailingRadius: rounded ? 10 : 0, topTrailingRadius: rounded ? 10 : 0))
  }
}

struct NotesBadge: View {
  let title: String
  var body: some View {
    Text(title).font(DS.font(20, weight: .bold)).foregroundStyle(Color(hex: 0x6426cb)).padding(
      .horizontal, 11
    ).padding(.vertical, 5).frame(minHeight: 40).background(DS.primarySoft, in: Capsule()).overlay(
      Capsule().stroke(DS.primary.opacity(0.16)))
  }
}
struct NotesPrimaryAction: View {
  let title: String
  var symbol: String? = nil
  var secondary = false
  let action: () -> Void
  private func glyphName(_ symbol: String) -> String {
    switch symbol {
    case "plus": "Plus"
    case "book": "BookOpen"
    case "doc.badge.arrow.up": "FileDown"
    case "square.and.arrow.down": "Save"
    default: symbol
    }
  }
  var body: some View {
    Button(action: action) {
      HStack(spacing: 8) {
        if let symbol {
          BCGlyph(glyphName(symbol), color: secondary ? DS.text : .white).frame(
            width: 18, height: 18)
        }
        Text(title).font(DS.font(20, weight: .bold))
      }
      .foregroundStyle(secondary ? DS.text : .white).frame(maxWidth: .infinity).frame(minHeight: 54)
      .background(secondary ? .white : DS.primary, in: Capsule()).overlay(
        Capsule().stroke(secondary ? Color(hex: 0xb4c3d9) : .clear))
    }.buttonStyle(BCPressStyle())
  }
}

struct NotesSticker: View {
  let kind: NoteKind
  var glyphSize: CGFloat = 18
  var size: CGFloat = 44
  private var surface: Color {
    switch kind {
    case .text: Color(hex: 0xb7efd8)
    case .ink: Color(hex: 0xffe883)
    case .voice: Color(hex: 0xbcdfff)
    }
  }
  private var artwork: String { kind == .text ? "NotebookPen" : kind.notesIcon }
  var body: some View {
    SVGAsset(path: "icons/sticker-\(artwork).svg").frame(width: glyphSize, height: glyphSize)
      .frame(width: size, height: size).background(surface, in: RoundedRectangle(cornerRadius: 9))
      .overlay(RoundedRectangle(cornerRadius: 9).stroke(Color(hex: 0x252436), lineWidth: 1.7))
      .shadow(color: Color(hex: 0x252436), radius: 0, x: 2, y: 2).rotationEffect(
        .degrees(kind == .ink ? 3 : -3)
      )
      .accessibilityHidden(true)
  }
}
