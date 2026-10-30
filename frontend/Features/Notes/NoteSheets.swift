import Observation
import SwiftUI

struct NoteSheetView: View {
  let sheet: SheetRoute
  var body: some View {
    if sheet == .noteType { NotesTypePicker() } else { NotesConceptSheet() }
  }
}

struct NotesTypePicker: View {
  @Environment(AppStore.self) private var store
  @State private var page = 1
  private var printedPage: Int {
    let citation = store.activeLesson?.blocks.flatMap(\.citations).first
    if let target = store.sourceTarget, target.bookID == store.selectedBookID, target.page == page {
      return target.printedPage ?? page
    }
    return citation?.printed_page_start.map { $0 + page - (citation?.page_start ?? page) } ?? page
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      Text("将关联到：教材第 \(printedPage) 页").font(DS.font(20)).foregroundStyle(DS.secondary)
        .padding(12).frame(maxWidth: .infinity, alignment: .leading).background(
          DS.primarySoft, in: RoundedRectangle(cornerRadius: 12)
        ).overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.primary.opacity(0.25))).padding(
          .bottom, 16)
      if let source = store.currentSource {
        HStack(alignment: .top, spacing: 10) {
          Text("教材批注页").font(DS.font(20, weight: .bold)).foregroundStyle(DS.secondary).fixedSize()
            .padding(.top, 2)
          VStack(alignment: .leading, spacing: 4) {
            Menu {
              ForEach(1...max(1, source.summary.page_count), id: \.self) { value in
                Button("PDF 第 \(value) 页") { page = value }
              }
            } label: {
              HStack {
                Text("教材第 \(printedPage) 页")
                Spacer()
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 14))
              }.font(DS.font(20)).foregroundStyle(DS.text).padding(.horizontal, 8).frame(height: 30)
                .background(.white, in: RoundedRectangle(cornerRadius: 5)).overlay(
                  RoundedRectangle(cornerRadius: 5).stroke(DS.border))
            }
            Text("选择后会在对应教材原页上直接批注").font(DS.font(13)).foregroundStyle(DS.secondary)
          }
        }.padding(.bottom, 18)
      }
      VStack(spacing: 10) {
        option(.text, title: "文字笔记", copy: "点击原文任意位置，输入文字批注")
        option(.ink, title: "手写批注", copy: "打开教材原页，用触控笔圈画")
        option(.voice, title: "语音笔记", copy: "说出想法，直接生成整理笔记，原始录音保留")
      }
      Text("原始内容会一直保留，整理版不会覆盖你的记录。").font(DS.font(13)).foregroundStyle(DS.secondary).padding(
        .top, 30)
    }.padding(.horizontal, store.isPad ? 32 : 16).padding(.bottom, 36)
      .onAppear {
        let target = store.sourceTarget.flatMap { $0.bookID == store.selectedBookID ? $0 : nil }
        page = min(
          max(1, store.currentSource?.summary.page_count ?? 1),
          max(1, target?.page ?? store.activeLesson?.page_start ?? 1))
      }
  }
  private func option(_ kind: NoteKind, title: String, copy: String) -> some View {
    let bookID = store.currentSource?.id ?? ""
    let published = NotesPageManifest.assets.contains {
      $0.book_id == bookID && $0.pdf_page == page
    }
    let localExtension = store.sourceFileURL(bookID)?.pathExtension.lowercased() ?? ""
    let localPage = ["pdf", "png", "jpg", "jpeg", "webp", "gif", "heic", "heif"].contains(
      localExtension)
    let disabled = kind == .ink && !published && !localPage
    return Button {
      choose(kind)
    } label: {
      HStack(spacing: 12) {
        NotesSticker(kind: kind, glyphSize: 23, size: 44)
        VStack(alignment: .leading, spacing: 5) {
          Text(title).font(DS.font(16, weight: .bold)).foregroundStyle(DS.text)
          Text(disabled ? "当前页暂无可批注原图" : copy).font(DS.font(13)).foregroundStyle(DS.muted)
        }.frame(maxWidth: .infinity, alignment: .leading)
      }.padding(12).frame(minHeight: 76).background(.white, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border)).opacity(disabled ? 0.5 : 1)
    }.buttonStyle(.plain).disabled(disabled)
  }
  private func choose(_ kind: NoteKind) {
    store.noteKind = kind
    NotesCaptureContext.pendingKind = kind
    NotesCaptureContext.existingNoteID = nil
    NotesCaptureContext.position = nil
    store.closeSheet()
    if kind == .voice {
      if let source = store.currentSource {
        store.sourceTarget = SourcePageTarget(bookID: source.id, title: store.chapterTitle,
          page: page, printedPage: printedPage, from: .notes)
      }
      store.go(.voiceNote)
    } else if let source = store.currentSource {
      store.openSource(
        SourcePageTarget(bookID: source.id, title: store.chapterTitle, page: page, from: .notes))
    } else {
      store.openSheet(.note)
    }
  }
}

struct NotesConceptSheet: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var text = ""
  @State private var saving = false
  @State private var saved = false
  @State private var error: String?
  @State private var pending: StudyNote?
  private var parts: [String] { store.selectedQuote.components(separatedBy: "\n") }
  private var concept: String {
    parts.first.flatMap { $0.isEmpty ? nil : $0 }.map { String($0.prefix(32)) } ?? store
      .activeLesson?.key_concepts.first ?? "学习笔记"
  }
  private var explanation: String {
    if parts.count > 1 { return parts.dropFirst().joined(separator: "\n") }
    return citation?.quote ?? ""
  }
  private var citation: LessonCitation? {
    store.noteState.contextCitation ?? store.activeLesson?.blocks.flatMap(\.citations).first
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 0) {
        NotesBadge(title: concept).padding(.bottom, 14)
        Text(concept).font(DS.font(16, weight: .bold)).padding(.bottom, 14)
        Text(explanation).font(DS.font(20)).foregroundStyle(DS.secondary).lineSpacing(5).padding(
          .bottom, 12)
        if let image = store.selectedAsset {
          AssetImage(image.image_url).frame(maxHeight: 180).padding(.bottom, 12)
        }
        if let citation {
          Button {
            store.closeSheet()
            store.openCitation(citation)
          } label: {
            HStack(spacing: 8) {
              BCGlyph("BookOpenCheck", color: DS.primary).frame(width: 17, height: 17)
              Text("教材第 \(citation.printed_page_start ?? citation.page_start) 页").font(
                DS.font(20, weight: .bold))
            }.foregroundStyle(DS.primary).frame(minHeight: 44)
          }.padding(.bottom, 18)
        }
        Text("学习笔记").font(DS.font(20, weight: .bold)).foregroundStyle(DS.secondary).padding(
          .bottom, 12)
        TextEditor(text: $text).font(DS.font(20)).scrollContentBackground(.hidden).padding(8).frame(
          height: 82
        ).background(.white, in: RoundedRectangle(cornerRadius: 16)).overlay(
          RoundedRectangle(cornerRadius: 16).stroke(DS.border)
        ).padding(.bottom, 14).accessibilityIdentifier("notes.body")
        if let error {
          Text(error).font(DS.font(13)).foregroundStyle(DS.danger).padding(.bottom, 8)
        }
        NotesPrimaryAction(
          title: saving ? "正在保存…" : error == nil ? "保存到笔记" : "重试保存",
          symbol: "square.and.arrow.down", secondary: true
        ) { save() }.disabled(
          saving || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        ).accessibilityIdentifier("notes.save")
      }.padding(.horizontal, store.isPad ? 32 : 16).padding(.top, store.isPad ? 16 : 10).padding(
        .bottom, store.isPad ? 20 : 46)
    }.scrollBounceBehavior(.basedOnSize)
      .overlay { if saved { NotesSaveCelebration() } }
      .onAppear { text = "# \(concept)\n\(explanation)" }
  }
  private func save() {
    guard !saving else { return }
    saving = true
    error = nil
    var note = pending ?? StudyNote(kind: .text, title: concept, body: text)
    note.body = text.trimmingCharacters(in: .whitespacesAndNewlines)
    var anchor = store.makeAnchor(page: citation?.page_start)
    anchor.printedPage = citation?.printed_page_start
    note.anchor = anchor
    pending = note
    Task {
      do {
        try await store.saveNote(note)
        if !store.selectedQuote.isEmpty && !reduceMotion {
          withAnimation { saved = true }
          try? await Task.sleep(for: .milliseconds(1230))
        }
        store.closeSheet()
        store.showToast("已保存到学习笔记")
      } catch {
        self.error = error.localizedDescription
        saving = false
      }
    }
  }
}

struct NotesTextEditor: View {
  @Environment(AppStore.self) private var store
  @Environment(\.dismiss) private var dismiss
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  var note: StudyNote?
  var anchor: NoteAnchor?
  var position: UnitPointValue?
  var onSaved: (() -> Void)?
  var compact = false
  var closeRequest = 0
  var onClose: (() -> Void)?
  var onAskAI: ((StudyNote) -> Void)?
  @State private var voiceCloseRequest = 0
  @State private var title = ""
  @State private var bodyText = ""
  @State private var saving = false
  @State private var saved = false
  @State private var error: String?
  @State private var persistence: NotesTextDraftPersistence?
  @State private var editorReady = false
  @State private var voiceDraft: StudyNote?
  private var effectiveAnchor: NoteAnchor? {
    if let anchor = note?.anchor ?? anchor { return anchor }
    guard store.currentSource != nil else { return nil }
    let target = store.sourceTarget.flatMap { $0.bookID == store.selectedBookID ? $0 : nil }
    var result = store.makeAnchor(page: target?.page ?? store.activeLesson?.page_start ?? 1)
    result.printedPage = target?.printedPage
    if !store.selectedQuote.isEmpty { result.quote = store.selectedQuote }
    return result
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 14) {
        HStack {
          if compact {
            NotesGlyph(name: "MessageSquareText", size: 17).foregroundStyle(DS.primary)
            Text("文字批注").font(DS.font(14, weight: .bold))
            Spacer()
            Text(
              saving || persistence?.isSaving == true
                ? "保存中…"
                : persistence?.hasUnsavedContent == false && persistence?.lastSaved != nil
                  ? "已保存" : "自动保存"
            ).font(DS.font(11)).foregroundStyle(
              DS.secondary)
          } else {
            BCPill(effectiveAnchor.map { "PDF 第 \($0.page) 页 · 教材摘录" } ?? "学习笔记")
            Spacer()
          }
          Button {
            closeEditor()
          } label: {
            NotesGlyph(name: "X", size: 18).frame(width: 44, height: 36)
          }.foregroundStyle(DS.secondary).accessibilityLabel("关闭")
        }
        if compact {
          Text(effectiveAnchor.map { "PDF 第 \($0.page) 页 · 教材摘录" } ?? "学习笔记").font(DS.font(11))
            .foregroundStyle(DS.secondary)
        } else {
          Text(title.isEmpty ? "文字笔记" : title).font(DS.font(20, weight: .bold))
        }
        if let quote = effectiveAnchor?.quote, !quote.isEmpty {
          NotesQuote(quote: quote, fontSize: compact ? 12 : 20, rounded: compact)
            .frame(maxHeight: compact ? 68 : nil).clipped()
        }
        if let asset = store.selectedAsset {
          AssetImage(asset.image_url).frame(maxHeight: 230)
          Text(asset.caption).font(DS.font(12)).foregroundStyle(DS.muted)
        }
        if compact {
          TextField("笔记标题", text: $title).font(DS.font(13)).padding(.vertical, 4)
            .accessibilityIdentifier("notes.title")
          TextEditor(text: $bodyText).font(DS.font(16)).scrollContentBackground(.hidden).frame(
            minHeight: 112
          ).accessibilityIdentifier("notes.body")
        } else {
          BCTextField("笔记标题", text: $title).accessibilityIdentifier("notes.title")
          Text("学习笔记").font(DS.font(14, weight: .bold))
          BCTextEditor(text: $bodyText, placeholder: "记录你的理解…").frame(minHeight: 210)
            .accessibilityIdentifier("notes.body")
        }
        if let issue = error ?? persistence?.error {
          Text(issue).font(DS.font(13)).foregroundStyle(DS.danger).accessibilityLabel(
            "笔记保存失败：\(issue)")
        }
        if compact {
          HStack(spacing: 6) {
            Button {
              if let note {
                Task {
                  await persistence?.prepareForDeletion()
                  await store.deleteNote(note.id)
                  finish()
                }
              } else {
                finish()
              }
            } label: {
              NotesGlyph(name: "Trash2", size: 17).frame(width: 44, height: 44)
            }.foregroundStyle(DS.secondary).accessibilityLabel("删除批注")
            Spacer(minLength: 0)
            Button(saving ? "保存中…" : error == nil ? "完成" : "重试保存") { save() }
              .accessibilityIdentifier("notes.save").disabled(
                saving || bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
              )
              .font(DS.font(13, weight: .bold)).padding(.horizontal, 12).frame(height: 44)
              .background(DS.background, in: RoundedRectangle(cornerRadius: 8))
            Button("问 AI") { save(askingAI: true) }.disabled(
              saving || bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ).font(DS.font(13, weight: .bold)).foregroundStyle(DS.primary).padding(.horizontal, 12)
              .frame(height: 44).background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 8))
          }
        } else {
          BCButton(
            saving ? "正在保存…" : error == nil ? "保存到笔记" : "重试保存", icon: "Save", style: .secondary
          ) { save() }
          .disabled(saving || bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
          .accessibilityIdentifier("notes.save")
        }
        if effectiveAnchor != nil {
          Button {
            startVoice()
          } label: {
            HStack(spacing: 6) {
              NotesGlyph(name: "Mic2", size: 17)
              Text(compact ? "开始语音批注" : "添加语音批注")
            }.font(DS.font(compact ? 13 : 16, weight: .bold)).foregroundStyle(DS.primary).frame(
              maxWidth: .infinity, minHeight: 44)
          }.buttonStyle(.plain)
        }
        if saved {
          HStack {
            StickerIcon("Check", tone: "mint", size: 36)
            Text("摘录笔记保存成功").font(DS.font(15, weight: .bold)).foregroundStyle(DS.primary)
          }
          .transition(.scale.combined(with: .opacity))
        }
      }.padding(compact ? 12 : 20)
    }.scrollDismissesKeyboard(.interactively).task {
      guard persistence == nil else { return }
      title =
        note?.title ?? (store.selectedQuote.isEmpty ? store.activeLesson?.title ?? "学习笔记" : "教材摘录")
      bodyText =
        note?.body ?? (store.selectedQuote.isEmpty ? "" : "摘录：\(store.selectedQuote)\n\n我的理解：")
      var record = note ?? StudyNote(kind: .text, title: title, body: bodyText)
      record.title = title
      record.body = bodyText
      record.anchor = effectiveAnchor
      record.position = note?.position ?? position
      persistence = NotesTextDraftPersistence(
        note: record, initiallySaved: store.notes.contains { $0.id == record.id }
      ) { record in
        let updated = NotesAnnotationRecord.mergingConversation(
          draft: record, stored: store.notes.first { $0.id == record.id })
        try await store.saveNote(updated)
      }
      editorReady = true
    }
    .onChange(of: title) { _, _ in stageDraft() }
    .onChange(of: bodyText) { _, _ in stageDraft() }
    .onDisappear {
      let session = persistence
      Task { _ = await session?.flush() }
    }.overlay { if saved { NotesSaveCelebration().transition(.scale.combined(with: .opacity)) } }
    .interactiveDismissDisabled(
      saving || (!saved && !bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    )
    .onChange(of: closeRequest) { _, _ in
      if voiceDraft != nil { voiceCloseRequest += 1 } else { closeEditor() }
    }
    .overlay {
      if let record = voiceDraft {
        if compact {
          NotesVoiceNoteView(
            existingNote: record, embedded: true, closeRequest: voiceCloseRequest,
            onClose: {
              voiceDraft = nil
              finish()
            }
          )
          .frame(maxWidth: .infinity, maxHeight: .infinity).background(.white)
          .clipShape(RoundedRectangle(cornerRadius: 28)).id(record.id)
        } else {
          NotesPopup(width: 560, height: 650, dim: true, onOutside: { voiceCloseRequest += 1 }) {
            NotesVoiceNoteView(
              existingNote: record, embedded: true, closeRequest: voiceCloseRequest,
              onClose: {
                voiceDraft = nil
                finish()
              })
          }.id(record.id)
        }
      }
    }
  }
  private func stageDraft() {
    guard editorReady else { return }
    persistence?.stage(
      title: title, body: bodyText, anchor: effectiveAnchor, position: note?.position ?? position)
  }
  private func startVoice() {
    guard !saving else { return }
    saving = true
    error = nil
    stageDraft()
    Task {
      guard let voice = await persistence?.upgradeToVoice() else {
        error = persistence?.error ?? "文字批注尚未保存，请重试。"
        saving = false
        return
      }
      saving = false
      voiceDraft = voice
    }
  }
  private func save(askingAI: Bool = false) {
    guard !saving else { return }
    saving = true
    error = nil
    stageDraft()
    Task {
      guard let persistence, await persistence.flush() else {
        error = persistence?.error ?? "保存失败，内容仍保留在编辑区，请重试。"
        saving = false
        return
      }
      saving = false
      if askingAI, let onAskAI, let record = persistence.lastSaved {
        onAskAI(record)
        return
      }
      withAnimation(reduceMotion ? nil : .spring(duration: 0.35)) { saved = true }
      if !reduceMotion { try? await Task.sleep(for: .milliseconds(1230)) }
      store.showToast("已保存到学习笔记")
      finish()
      onSaved?()
      if askingAI {
        store.selectedQuote = bodyText
        store.openSheet(.chat)
      }
    }
  }
  private func closeEditor() {
    let hasChanges = bodyText != (note?.body ?? "") || title != (note?.title ?? "")
    if hasChanges && bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      && !(note?.body.isEmpty ?? true)
    {
      error = "批注内容不能为空。需要移除时，请点击删除。"
      return
    }
    if hasChanges && !bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      save()
    } else {
      finish()
    }
  }
  private func finish() {
    if let onClose {
      onClose()
    } else {
      if store.sheet == .note { store.closeSheet() }
      dismiss()
    }
  }
}

/// Serializes the source annotation's debounced writes. A queued write always
/// takes the latest draft, and a completion never replaces the editable text.
@MainActor @Observable final class NotesTextDraftPersistence {
  private(set) var latest: StudyNote
  private(set) var lastSaved: StudyNote?
  private(set) var isSaving = false
  private(set) var error: String?
  private(set) var transferred = false
  private let saveOperation: @MainActor (StudyNote) async throws -> Void
  private var debounce: Task<Void, Never>?
  private var queue: Task<Bool, Never>?

  init(
    note: StudyNote, initiallySaved: Bool = false,
    save: @escaping @MainActor (StudyNote) async throws -> Void
  ) {
    latest = note
    lastSaved = initiallySaved ? note : nil
    saveOperation = save
  }

  var hasUnsavedContent: Bool {
    guard let lastSaved else {
      return !latest.body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    return !Self.sameContent(latest, lastSaved)
  }

  func stage(title: String, body: String, anchor: NoteAnchor?, position: UnitPointValue?) {
    guard !transferred else { return }
    latest.title = title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "学习笔记" : title
    latest.body = body.trimmingCharacters(in: .whitespacesAndNewlines)
    latest.anchor = anchor
    latest.position = position
    error = nil
    debounce?.cancel()
    debounce = Task { [weak self] in
      do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
      guard !Task.isCancelled else { return }
      _ = await self?.flush()
    }
  }

  /// This can outlive its view. Closing/navigation waits for the newest content;
  /// a failed save leaves the draft intact for a subsequent explicit retry.
  @discardableResult func flush() async -> Bool {
    debounce?.cancel()
    debounce = nil
    while !transferred {
      if let lastSaved, Self.sameContent(latest, lastSaved) { return true }
      if latest.body.isEmpty {
        if lastSaved == nil { return true }
        error = "批注内容不能为空。需要移除时，请点击删除。"
        return false
      }
      let previous = queue
      let write = Task { @MainActor [self] in
        _ = await previous?.value
        guard !transferred else { return true }
        if let lastSaved, Self.sameContent(latest, lastSaved) { return true }
        var record = latest
        guard !record.body.isEmpty else {
          if lastSaved != nil { error = "批注内容不能为空。需要移除时，请点击删除。" }
          return lastSaved == nil
        }
        record.updatedAt = .now
        isSaving = true
        do {
          try await saveOperation(record)
          lastSaved = record
          isSaving = false
          if Self.sameContent(latest, record) { error = nil }
          return true
        } catch {
          isSaving = false
          // An older failure must not replace the status of a newer draft.
          if Self.sameContent(latest, record) {
            self.error = "保存失败，内容仍保留在编辑区，请重试：\(error.localizedDescription)"
          }
          return false
        }
      }
      queue = write
      let success = await write.value
      if !success, error != nil { return false }
    }
    return true
  }

  /// Text is durable before the recorder appears. Ownership is then handed to
  /// that recorder, which saves audio onto the same ID instead of a second note.
  func upgradeToVoice() async -> StudyNote? {
    guard await flush() else { return nil }
    transferred = true
    debounce?.cancel()
    var record = lastSaved ?? latest
    record.kind = .voice
    return record
  }

  func prepareForDeletion() async {
    transferred = true
    debounce?.cancel()
    _ = await queue?.value
  }

  private static func sameContent(_ lhs: StudyNote, _ rhs: StudyNote) -> Bool {
    var a = lhs
    var b = rhs
    a.updatedAt = Date(timeIntervalSince1970: 0)
    b.updatedAt = Date(timeIntervalSince1970: 0)
    return a == b
  }
}

/// Body edits and recorder upgrades preserve replies saved while their editor
/// was hidden. A record from another note can never supply that conversation.
enum NotesAnnotationRecord {
  static func mergingConversation(draft: StudyNote, stored: StudyNote?) -> StudyNote {
    var result = draft
    if let stored, stored.id == draft.id {
      result.conversation = stored.conversation ?? draft.conversation
    }
    return result
  }
  static func voicePreview(_ note: StudyNote) -> String {
    let content = voiceText(note)
    return content.isEmpty ? "录音已保存，点击生成笔记。" : content
  }
  static func voiceText(_ note: StudyNote) -> String {
    let organized = note.organizedText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    return organized.isEmpty ? note.body : organized
  }
}
