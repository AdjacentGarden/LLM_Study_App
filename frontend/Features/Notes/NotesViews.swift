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

enum NotesChapterScope {
  static func notes(_ notes: [StudyNote], courseID: String?, bookID: String?,
    chapterID: String?, chapters: [Chapter]) -> [StudyNote] {
    guard let bookID, let chapterID else { return [] }
    var chapterIDs: Set<String> = [chapterID]
    var changed = true
    while changed {
      let before = chapterIDs.count
      for chapter in chapters where chapter.parent_id.map(chapterIDs.contains) == true {
        chapterIDs.insert(chapter.id)
      }
      changed = chapterIDs.count != before
    }
    let chapter = chapters.first { $0.id == chapterID }
    return notes.filter { note in
      guard let anchor = note.anchor, anchor.bookID == bookID,
        anchor.courseID == nil || anchor.courseID == courseID else { return false }
      if let savedChapterID = anchor.chapterID {
        if chapterIDs.contains(savedChapterID) { return true }
        if chapters.contains(where: { $0.id == savedChapterID }) { return false }
      }
      // Older page annotations may have a missing or retired chapter ID; their original PDF page
      // can still place them within the selected chapter without changing storage.
      guard let chapter, chapter.page_start > 0 else { return false }
      return (chapter.page_start...max(chapter.page_start, chapter.page_end)).contains(anchor.page)
    }
  }
}

struct NotesHubView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var filter: NoteKind?
  @State private var selectedID: String?
  @State private var editor: StudyNote?
  @State private var editorCloseRequest = 0
  @State private var deleting: StudyNote?
  @State private var hubWidth: CGFloat = 834
  private var hasChapter: Bool { store.selectedBookID != nil && store.selectedChapterID != nil }
  private var chapterNotes: [StudyNote] {
    NotesChapterScope.notes(store.notes, courseID: store.selectedCourseID,
      bookID: store.selectedBookID, chapterID: store.selectedChapterID, chapters: store.chapters)
  }
  private var filtered: [StudyNote] {
    chapterNotes.filter { filter == nil || $0.kind == filter }
      .sorted { $0.updatedAt > $1.updatedAt }
  }
  private var selected: StudyNote? { filtered.first { $0.id == selectedID } ?? filtered.first }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 12) {
        if hasChapter {
          Text(store.chapterTitle).font(DS.font(19.2, weight: .bold)).foregroundStyle(DS.secondary)
            .accessibilityIdentifier("notes.chapter.title")
        }
        if !chapterNotes.isEmpty {
          filters
        }
        if filtered.isEmpty {
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
  @ViewBuilder private var noteActions: some View {
    NotesPrimaryAction(title: "导出 PDF", symbol: "doc.badge.arrow.up", secondary: true) {
      store.go(.export)
    }.disabled(!hasChapter).accessibilityIdentifier("notes.export")
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
        Text("\(chapterNotes.filter { value == nil || $0.kind == value }.count)")
          .font(DS.font(16, weight: .bold)).padding(.horizontal, 6).padding(.vertical, 2)
          .background(DS.primary.opacity(0.1), in: Capsule())
      }.font(DS.font(16, weight: .bold)).foregroundStyle(
        filter == value ? DS.primary : DS.secondary
      )
      .padding(.horizontal, 13).frame(minHeight: 44)
      .background(filter == value ? DS.primarySoft : .clear, in: RoundedRectangle(cornerRadius: 10))
      .overlay(RoundedRectangle(cornerRadius: 10).stroke(filter == value ? DS.primary : .clear))
    }.buttonStyle(.plain)
  }
  private var empty: some View {
    BCCard {
      VStack(spacing: 12) {
        AssetImage("/assets/brand/empty-states/no-notes-v1.png").frame(width: 180, height: 140)
        Text(hasChapter ? "本章节还没有\(chapterNotes.isEmpty ? "" : filter?.notesLabel ?? "")笔记" : "请先选择学习章节")
          .font(DS.font(20, weight: .bold))
        Text(hasChapter ? "你可以输入文字、直接批注教材原页，或录下语音后整理。" : "在学习页选择章节后，可查看和记录对应的笔记。")
          .font(DS.font(14)).foregroundStyle(DS.muted).multilineTextAlignment(.center)
        BCButton(hasChapter ? "去记笔记" : "选择章节", icon: hasChapter ? "Plus" : "BookOpen") {
          if hasChapter { store.openSheet(.noteType) } else { store.go(.study) }
        }
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
