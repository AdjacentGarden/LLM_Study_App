import QuickLook
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
    case .photo: "照片"
    }
  }
  var notesIcon: String {
    switch self {
    case .text: "FileText"
    case .ink: "PenLine"
    case .voice: "Mic2"
    case .photo: "Camera"
    }
  }
  var notesTone: String {
    switch self {
    case .text: "mint"
    case .ink: "yellow"
    case .voice: "blue"
    case .photo: "blue"
    }
  }
}

extension NoteProvenance {
  var sourceLabelText: String {
    switch sourceLabel {
    case "textbook": "来源：教材原文"
    case "general": "来源：一般知识（非教材引用）"
    case "mixed": "来源：教材原文 + 一般知识"
    default: "来源：\(sourceLabel)"
    }
  }
}

enum NotesChapterScope {
  /// Notes opened from any subsection belong to its enclosing chapter.
  static func chapterID(containing selectedID: String?, chapters: [Chapter]) -> String? {
    guard var id = selectedID else { return nil }
    var visited: Set<String> = [id]
    while let parent = chapters.first(where: { $0.id == id })?.parent_id,
      chapters.contains(where: { $0.id == parent }), visited.insert(parent).inserted {
      id = parent
    }
    return id
  }

  static func notes(_ notes: [StudyNote], courseID: String?, bookID: String?,
    chapterID: String?, chapters: [Chapter], courseResourceID: String? = nil) -> [StudyNote] {
    guard let bookID, let chapterID = self.chapterID(containing: chapterID, chapters: chapters) else { return [] }
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
        anchor.courseID == courseID, anchor.courseResourceID != nil,
        courseResourceID == nil || anchor.courseResourceID == courseResourceID else { return false }
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

enum NotesSortOrder: String, CaseIterable, Identifiable {
  case recordedAt, chapterOrder
  var id: String { rawValue }
  var title: String { self == .recordedAt ? "按记录时间" : "按章节顺序" }

  func sorted(_ notes: [StudyNote], chapters: [Chapter]) -> [StudyNote] {
    // Walk the directory, preserving sibling order even when children are not
    // adjacent to their parents in the source array.
    var ranks: [String: Int] = [:]
    func visit(_ chapter: Chapter) {
      guard ranks[chapter.id] == nil else { return }
      ranks[chapter.id] = ranks.count
      for child in chapters where child.parent_id == chapter.id { visit(child) }
    }
    for chapter in chapters where chapter.parent_id == nil
      || !chapters.contains(where: { $0.id == chapter.parent_id }) { visit(chapter) }
    for chapter in chapters { visit(chapter) }
    func rank(_ note: StudyNote) -> Int {
      if let id = note.anchor?.chapterID, let rank = ranks[id] { return rank }
      // Place older annotations with retired chapter IDs by their PDF page.
      guard let page = note.anchor?.page else { return .max }
      let chapter = chapters.filter {
        $0.page_start > 0 && ($0.page_start...max($0.page_start, $0.page_end)).contains(page)
      }.max { $0.level < $1.level }
      return chapter.flatMap { ranks[$0.id] } ?? .max
    }
    return notes.sorted { lhs, rhs in
      if self == .chapterOrder {
        let left = rank(lhs), right = rank(rhs)
        if left != right { return left < right }
        let leftPage = lhs.anchor?.page ?? .max, rightPage = rhs.anchor?.page ?? .max
        if leftPage != rightPage { return leftPage < rightPage }
      }
      if lhs.createdAt != rhs.createdAt {
        return self == .recordedAt ? lhs.createdAt > rhs.createdAt : lhs.createdAt < rhs.createdAt
      }
      return lhs.id < rhs.id
    }
  }
}

struct NotesHubView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var photoCapture = false
  @State private var photoNoteID: String?
  @AppStorage("notes.sortOrder") private var sortOrder: NotesSortOrder = .recordedAt
  @State private var filter: NoteKind?
  @State private var selectedID: String?
  @State private var focusedNoteID: String?
  @State private var editor: StudyNote?
  @State private var editorCloseRequest = 0
  @State private var referencePreviewURL: URL?
  @State private var deleting: StudyNote?
  private var hasChapter: Bool { store.selectedBookID != nil && store.selectedChapterID != nil }
  private var chapterTitle: String {
    let id = NotesChapterScope.chapterID(containing: store.selectedChapterID, chapters: store.chapters)
    return store.chapters.first { $0.id == id }?.title ?? store.chapterTitle
  }
  private var chapterNotes: [StudyNote] {
    var values = NotesChapterScope.notes(store.notes, courseID: store.selectedCourseID,
      bookID: store.selectedBookID, chapterID: store.selectedChapterID, chapters: store.chapters,
      courseResourceID: store.courseResourceID(courseID: store.selectedCourseID, bookID: store.selectedBookID ?? ""))
    if let id = focusedNoteID ?? store.noteState.focusNoteID, let note = store.notes.first(where: { $0.id == id }),
      note.anchor == nil {
      if !values.contains(where: { $0.id == id }) { values.insert(note, at: 0) }
    }
    return values
  }
  private var filtered: [StudyNote] {
    sortOrder.sorted(chapterNotes.filter { filter == nil || $0.kind == filter }, chapters: store.chapters)
  }
  private var selected: StudyNote? { filtered.first { $0.id == selectedID } ?? filtered.first }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 12) {
        if !store.unassignedSourceNotes.isEmpty {
          DisclosureGroup("未归属笔记（\(store.unassignedSourceNotes.count)）") {
            ForEach(store.unassignedSourceNotes) { note in
              HStack {
                Text(note.title).font(DS.font(.supporting))
                Spacer()
                Menu("选择课程") {
                  ForEach(store.courses.filter { $0.resourceIDs.contains(note.anchor?.bookID ?? "") }) { course in
                    Button(course.name) {
                      Task {
                        do { try await store.claimSourceNote(note.id, courseID: course.id) }
                        catch { store.showToast(error.localizedDescription, kind: .error) }
                      }
                    }
                  }
                }
              }.padding(.vertical, 8)
            }
          }.padding(12).background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 12))
        }
        if hasChapter {
          Text(chapterTitle).font(DS.font(.cardTitle)).foregroundStyle(DS.secondary)
            .accessibilityIdentifier("notes.chapter.title")
        }
        if !chapterNotes.isEmpty {
          ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
              filterOptions
              Spacer(minLength: 0)
              sortingMenu
            }
            VStack(alignment: .leading, spacing: 8) {
              filters
              sortingMenu.frame(maxWidth: .infinity, alignment: .trailing)
            }
          }
        }
        if filtered.isEmpty {
          empty
        } else {
          noteList
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
    .sheet(isPresented: $photoCapture) { NotesPhotoNoteView(noteID: photoNoteID) }
    .quickLookPreview($referencePreviewURL)
    .onAppear { applyFocusNote() }
    .onChange(of: selected?.id, initial: true) { _, id in store.learningState.visibleNoteID = id }
    .onDisappear { store.learningState.visibleNoteID = nil }
    .onChange(of: store.noteState.focusNoteID) { _, _ in applyFocusNote() }
    .task {
      await store.reloadNotes()
      applyFocusNote()
    }
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
    NotesPrimaryAction(title: "拍照导入", symbol: "camera", secondary: true) {
      photoNoteID = nil; photoCapture = true
    }.disabled(!hasChapter).accessibilityIdentifier("notes.photo.import")
    NotesPrimaryAction(title: "导出 PDF", symbol: "doc.badge.arrow.up", secondary: true) {
      store.go(.export)
    }.disabled(!hasChapter).accessibilityIdentifier("notes.export")
    if store.currentSource == nil {
      NotesPrimaryAction(title: "上传教材", symbol: "Upload", secondary: true) { store.go(.upload) }
    }
  }
  private var filters: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      filterOptions
    }
  }
  private var filterOptions: some View {
    HStack(spacing: 8) {
      filterButton(nil)
      filterButton(.text)
      filterButton(.ink)
      filterButton(.voice)
      filterButton(.photo)
    }
  }
  private var sortingMenu: some View {
    Menu {
      Picker("笔记排序", selection: $sortOrder) {
        ForEach(NotesSortOrder.allCases) { order in
          Text(order.title).tag(order)
        }
      }
    } label: {
      Label(sortOrder.title, systemImage: "arrow.up.arrow.down")
        .font(DS.font(.supporting, weight: .semibold)).foregroundStyle(DS.primary)
        .padding(.horizontal, 8).buttonFrame()
    }.fixedSize().accessibilityIdentifier("notes.sort")
      .accessibilityLabel("笔记排序").accessibilityValue(sortOrder.title)
  }
  private func filterButton(_ value: NoteKind?) -> some View {
    Button {
      filter = value
    } label: {
      HStack(spacing: 7) {
        Text(value?.notesLabel ?? "全部")
        Text("\(chapterNotes.filter { value == nil || $0.kind == value }.count)")
          .font(DS.font(.cardTitle)).padding(.horizontal, 6).padding(.vertical, 2)
          .background(DS.primary.opacity(0.1), in: Capsule())
      }.font(DS.font(.cardTitle)).foregroundStyle(
        filter == value ? DS.primary : DS.secondary
      )
      .padding(.horizontal, DS.ButtonSize.regular.horizontalPadding).buttonFrame()
      .background(filter == value ? DS.primarySoft : .clear, in: RoundedRectangle(cornerRadius: 10))
      .overlay(RoundedRectangle(cornerRadius: 10).stroke(filter == value ? DS.primary : .clear))
    }.buttonStyle(.plain)
  }
  private func applyFocusNote() {
    guard let id = store.noteState.focusNoteID else { return }
    focusedNoteID = id
    if filter != nil, !filtered.contains(where: { $0.id == id }) { filter = nil }
    guard filtered.contains(where: { $0.id == id }) else { return }
    selectedID = id
    store.noteState.focusNoteID = nil
  }
  private var empty: some View {
    BCCard {
      VStack(spacing: 12) {
        AssetImage("/assets/brand/empty-states/no-notes-v1.png").frame(width: 180, height: 140)
        Text(hasChapter ? "本章节还没有\(chapterNotes.isEmpty ? "" : filter?.notesLabel ?? "")笔记" : "请先选择学习章节")
          .font(DS.font(.cardTitle))
        Text(hasChapter ? "你可以输入文字、批注教材、录下语音，或拍照导入手写笔记。" : "在学习页选择章节后，可查看和记录对应的笔记。")
          .font(DS.font(.supporting)).foregroundStyle(DS.muted).multilineTextAlignment(.center)
        BCButton(hasChapter ? "去记笔记" : "选择章节", icon: hasChapter ? "Plus" : "BookOpen") {
          if hasChapter { store.openSheet(.noteType) } else { store.go(.study) }
        }
      }.frame(maxWidth: .infinity).padding(.vertical, 24)
    }
  }
  private var noteList: some View {
    let notes = filtered
    let highlightedID = notes.first { $0.id == selectedID }?.id ?? notes.first?.id
    return LazyVStack(spacing: 8) {
      ForEach(notes) { note in
        Button {
          selectedID = note.id
          reopen(note)
        } label: {
          HStack(alignment: .top, spacing: 12) {
            NotesSticker(kind: note.kind, glyphSize: 18, size: 44)
            VStack(alignment: .leading, spacing: 5) {
              (Text(note.kind.notesLabel).font(DS.font(.supporting, weight: .semibold)).foregroundColor(
                DS.primary)
                + Text(note.anchor.map { " · 第 \($0.printedPage ?? $0.page) 页" } ?? "").font(
                  DS.font(.caption)
                ).foregroundColor(DS.muted))
              Text(note.title).font(DS.font(.cardTitle)).foregroundStyle(DS.text).lineLimit(
                2)
              Text(preview(note)).accessibilityIdentifier("notes.originalBody").font(DS.font(.body)).foregroundStyle(DS.secondary).lineLimit(2)
              HStack {
                if let id = note.anchor?.chapterID, let chapter = store.chapters.first(where: { $0.id == id }) {
                  Text(chapter.title).lineLimit(1)
                }
                Spacer(minLength: 8)
                Text(note.createdAt, format: .dateTime.year().month().day().hour().minute())
              }.font(DS.font(.caption)).foregroundStyle(DS.muted)
            }.frame(maxWidth: .infinity, alignment: .leading)
          }.padding(16).background(highlightedID == note.id ? DS.primarySoft : .white)
            .overlay(
              Rectangle().stroke(
                !store.isPad && highlightedID == note.id ? DS.primary : .clear, lineWidth: 3
              ).padding(-3))
        }.buttonStyle(.plain).accessibilityIdentifier("notes.row.\(note.id)")
          .contextMenu { noteMenu(note) }
      }
    }
  }
  @ViewBuilder private func noteMenu(_ note: StudyNote) -> some View {
    Button("编辑笔记") { reopen(note) }
    if let anchor = note.anchor {
      Button("查看原文") {
        store.openSource(
          SourcePageTarget(
            bookID: anchor.bookID, title: note.title, page: anchor.page,
            courseID: anchor.courseID, courseResourceID: anchor.courseResourceID, from: .notes))
      }
    }
    if let references = note.references, !references.isEmpty {
      Menu("引用来源") {
        ForEach(Array(references.enumerated()), id: \.element.evidenceID) { index, reference in
          Button("引用 \(index + 1) · \(reference.quote.prefix(40))") {
            openReference(reference, note: note)
          }.accessibilityIdentifier("notes.reference")
        }
      }
    }
    if let photos = note.photos {
      ForEach(photos) { photo in
        Button("查看照片原稿") { referencePreviewURL = store.attachmentURL(photo.originalPath) }
          .accessibilityIdentifier("notes.photo.original")
      }
    }
    Button("删除笔记", role: .destructive) { deleting = note }
  }
  private func openReference(_ reference: NoteReference, note: StudyNote) {
    if ["attachment", "screenshot"].contains(reference.sourceType ?? ""), let path = reference.attachmentPath {
      referencePreviewURL = store.attachmentURL(path)
      return
    }
    let bookID = store.sources.contains(where: { $0.id == reference.bookID })
      ? reference.bookID : (note.anchor?.bookID ?? reference.bookID)
    let courseID = store.courses.contains(where: { $0.id == reference.courseID && $0.resourceIDs.contains(bookID) })
      ? reference.courseID : note.anchor?.courseID
    store.openSource(
      SourcePageTarget(
        bookID: bookID, title: note.title, page: reference.page,
        courseID: courseID, courseResourceID: store.courseResourceID(courseID: courseID, bookID: bookID),
        printedPage: reference.printedPage, from: .notes))
  }
  private func preview(_ note: StudyNote) -> String {
    if note.kind == .photo { return note.body.isEmpty ? "照片原稿已保存，点击打开后提取整理。" : note.body }
    if note.kind == .text { return note.body }
    if note.kind == .voice {
      return NotesAnnotationRecord.voicePreview(note)
    }
    return note.body.isEmpty ? "手写笔迹已保存，尚未整理。" : note.body
  }
  private func reopen(_ note: StudyNote) {
    if note.kind == .photo { photoNoteID = note.id; photoCapture = true; return }
    if note.kind == .text && note.anchor == nil {
      editor = note
      return
    }
    NotesCaptureContext.existingNoteID = note.id
    NotesCaptureContext.pendingKind = note.kind
    if let anchor = note.anchor {
      let target = SourcePageTarget(
        bookID: anchor.bookID, title: note.title, page: anchor.page,
        courseID: anchor.courseID, courseResourceID: anchor.courseResourceID,
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
  var textStyle: DS.Typography = .reading
  var rounded = false
  var body: some View {
    HStack(spacing: 0) {
      Rectangle().fill(DS.primary).frame(width: 3)
      Text(quote).font(DS.font(textStyle)).lineSpacing(4).foregroundStyle(DS.secondary).padding(12).frame(
        maxWidth: .infinity, alignment: .leading)
    }.fixedSize(horizontal: false, vertical: true).background(DS.primarySoft)
      .clipShape(UnevenRoundedRectangle(topLeadingRadius: 0, bottomLeadingRadius: 0,
                                      bottomTrailingRadius: rounded ? 10 : 0, topTrailingRadius: rounded ? 10 : 0))
  }
}

struct NotesBadge: View {
  let title: String
  var body: some View {
    Text(title).font(DS.font(.cardTitle)).foregroundStyle(Color(hex: 0x6426cb)).padding(
      .horizontal, 11
    ).padding(.vertical, 5).frame(minHeight: 40).background(DS.primarySoft, in: Capsule()).overlay(
      Capsule().stroke(DS.primary.opacity(0.16)))
  }
}
struct NotesPrimaryAction: View {
  let title: String
  var symbol: String? = nil
  var secondary = false
  var size: DS.ButtonSize? = nil
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
        Text(title).font(DS.font(.button))
      }
      .foregroundStyle(secondary ? DS.text : .white)
      .buttonFrame(size ?? (secondary ? .regular : .primary), fillsWidth: true)
      .background(secondary ? Color.white : .clear, in: Capsule()).overlay(
        Capsule().stroke(secondary ? Color(hex: 0xb4c3d9) : .clear))
      .primaryCapsuleSurface(active: !secondary)
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
    case .photo: Color(hex: 0xd8d0ff)
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
