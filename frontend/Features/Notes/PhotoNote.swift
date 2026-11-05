import CryptoKit
import ImageIO
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers
import UIKit

struct NotePhoto: Codable, Hashable, Sendable, Identifiable {
  var id: String = UUID().uuidString
  var originalPath: String
  var preparedPath: String
  var imageSHA256: String
  var requestID: String = "photo_" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
  var recognizedText: String? = nil
  var organizedText: String? = nil
  var uncertain: [String]? = nil
}

extension StudyNote {
  var attachmentPaths: [String] {
    [audioPath, drawingPath].compactMap { $0 } + (photos ?? []).flatMap { [$0.originalPath, $0.preparedPath] }
  }
}

enum PhotoNoteImage {
  struct Prepared: Sendable { var jpeg: Data; var originalExtension: String }
  static func prepare(_ data: Data) throws -> Prepared {
    guard data.count <= 25 * 1024 * 1024,
      let source = CGImageSourceCreateWithData(data as CFData, nil), CGImageSourceGetCount(source) == 1,
      let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
      let width = properties[kCGImagePropertyPixelWidth] as? Int,
      let height = properties[kCGImagePropertyPixelHeight] as? Int,
      width >= 32, height >= 32, width <= 16000, height <= 16000, width * height <= 60000000,
      let typeID = CGImageSourceGetType(source), let type = UTType(typeID as String), type.conforms(to: .image),
      let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: 4096,
      ] as CFDictionary) else {
      throw ContentError.invalid("请选择清晰的单页照片，原图应小于 25 MB。")
    }
    let output = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(output, UTType.jpeg.identifier as CFString, 1, nil) else {
      throw ContentError.invalid("照片暂时无法处理，请重试。")
    }
    CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: 0.9] as CFDictionary)
    guard CGImageDestinationFinalize(destination), output.length <= 8 * 1024 * 1024 else {
      throw ContentError.invalid("照片过大，请裁剪到笔记区域后重试。")
    }
    return Prepared(jpeg: output as Data, originalExtension: type.preferredFilenameExtension ?? "jpg")
  }
}

extension LocalBookCourseRepository {
  /// The target append and removal of its temporary photo draft commit together.
  func commitPhotoNote(draftID: String, draftRevision: Int, targetID: String?, targetRevision: Int?,
    title: String, transcript: String, organized: String, scope: String) throws -> StoredNote {
    try db.transaction {
      guard let draft = try loadStoredNote(draftID, scope: scope), draft.revision == draftRevision,
        var photo = draft.note.photos?.first else { throw NoteStoreError.revisionConflict(noteID: draftID) }
      guard !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
        !transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
        !organized.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
        transcript.count <= 16000, organized.count <= 20000 else {
        throw ContentError.invalid("请先核对识别文字和整理笔记。")
      }
      photo.organizedText = organized
      if let targetID, targetID != draftID {
        guard let target = try loadStoredNote(targetID, scope: scope), target.revision == targetRevision,
          target.note.anchor?.courseResourceID == draft.note.anchor?.courseResourceID,
          target.note.anchor?.courseID == draft.note.anchor?.courseID,
          target.note.anchor?.bookID == draft.note.anchor?.bookID,
          !(target.note.photos ?? []).contains(where: { $0.id == photo.id }) else {
          throw NoteStoreError.revisionConflict(noteID: targetID)
        }
        var note = target.note
        note.photos = (note.photos ?? []) + [photo]
        note.body += (note.body.isEmpty ? "" : "\n\n") + "照片笔记：" + title + "\n" + transcript
        note.organizedText = (note.organizedText ?? "") + ((note.organizedText ?? "").isEmpty ? "" : "\n\n") + organized
        note.updatedAt = .now
        let saved = try writeNote(note, scope: scope, condition: .revision(target.revision))
        try tombstone(draftID, scope: scope)
        return saved
      }
      var note = draft.note
      note.title = title; note.body = transcript; note.organizedText = organized
      note.photos?[0] = photo; note.updatedAt = .now
      return try writeNote(note, scope: scope, condition: .revision(draftRevision))
    }
  }
}

extension StudioService {
  func generatePhoto(photo: NotePhoto, bookID: String, jpeg: Data, retryFailed: Bool = false,
    onProgress: (@MainActor @Sendable (String) -> Void)? = nil) async throws -> StudioJob {
    let capability = try await capabilities()
    guard capability.photo_notes == true else {
      throw CloudError(status: 503, message: "照片笔记服务尚未就绪，照片原稿已保存在本机。")
    }
    let hash = SHA256.hash(data: jpeg).map { String(format: "%02x", $0) }.joined()
    guard hash == photo.imageSHA256 else { throw ContentError.invalid("识别图片发生变化，请重新导入；照片原稿仍保留。") }
    let key = "native.studio.photo." + AgentDigest.sha256(api.baseURL.absoluteString + "|" + capability.draft_scope + "|" + photo.requestID)
    var requestID = UserDefaults.standard.string(forKey: key + ".latest") ?? photo.requestID
    var existing = try await job(requestID: requestID, bookID: bookID)
    if let previous = existing, retryFailed, ["failed", "cancelled", "rejected"].contains(previous.status) {
      requestID = "photo_" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
      UserDefaults.standard.set(requestID, forKey: key + ".latest")
      existing = nil
    }
    let intent = key + "." + requestID + ".submitted"
    let initial: StudioJob
    if let existing { initial = existing }
    else {
      guard !UserDefaults.standard.bool(forKey: intent) else {
        throw CloudError(status: 409, message: "上次请求尚未确认，请稍后查询原任务；不会重复提交。")
      }
      UserDefaults.standard.set(true, forKey: intent)
      do {
        initial = try await api.request("/api/studio/photo-notes", method: "POST", body: [
          "book_id": .string(bookID), "request_id": .string(requestID),
          "image_base64": .string(jpeg.base64EncodedString()), "image_sha256": .string(hash), "consent": .bool(true),
        ])
      } catch let error as CloudError where [400, 401, 403, 404, 409, 413, 422, 429, 503].contains(error.status) {
        UserDefaults.standard.removeObject(forKey: intent)
        throw error
      }
    }
    guard initial.kind == "photo_note", initial.book_id == bookID else { throw ContentError.invalid("返回任务不属于当前照片。") }
    let result = try await waitForJob(initial, onProgress: onProgress)
    guard result.kind == "photo_note", result.book_id == bookID, result.status == "succeeded",
      result.result["image_sha256"]?.stringValue == hash, !result.transcript.isEmpty, !result.polished.isEmpty else {
      throw ContentError.invalid("未返回完整的照片笔记，照片原稿仍保留。")
    }
    return result
  }
}

struct NotesPhotoNoteView: View {
  var noteID: String? = nil
  @Environment(AppStore.self) private var store
  @Environment(\.dismiss) private var dismiss
  @State private var picked: PhotosPickerItem?
  @State private var camera = false
  @State private var draft: StudyNote?
  @State private var revision: Int?
  @State private var scope: String?
  @State private var title = "照片笔记"
  @State private var transcript = ""
  @State private var organized = ""
  @State private var targetID = ""
  @State private var targetRevision: Int?
  @State private var error: String?
  @State private var busy = false
  @State private var phase = ""
  @State private var task: Task<Void, Never>?
  private var photoAPI: CloudAPI? {
    #if DEBUG
    let args = ProcessInfo.processInfo.arguments
    if ContentBundle.fixtureMode, store.repository.includesFixtureContent,
      let index = args.firstIndex(of: "--fixture-photo-server"), args.indices.contains(index + 1),
      let url = URL(string: args[index + 1]), url.host == "127.0.0.1" {
      return CloudAPI(baseURL: url)
    }
    #endif
    return store.cloud
  }
  private var photo: NotePhoto? { draft?.photos?.first }
  private var targets: [StudyNote] {
    guard let anchor = draft?.anchor else { return [] }
    return store.notes.filter { $0.id != draft?.id && $0.anchor?.courseResourceID == anchor.courseResourceID }
  }
  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: 18) {
          Text("拍下手写笔记，保留照片原稿，再提取文字并整理。").foregroundStyle(.secondary)
          if let photo, let image = UIImage(contentsOfFile: store.attachmentURL(photo.preparedPath).path) {
            Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 300)
              .accessibilityIdentifier("photo.preview")
            Text("照片原稿已保存在本机").font(.caption).foregroundStyle(.secondary)
          } else {
            HStack {
              Button { camera = true } label: { Label("拍照", systemImage: "camera") }
                .buttonStyle(.bordered).disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
              PhotosPicker(selection: $picked, matching: .images) { Label("从相册选择", systemImage: "photo") }
                .buttonStyle(.bordered).accessibilityIdentifier("photo.library")
            }
            if !UIImagePickerController.isSourceTypeAvailable(.camera) {
              Text("当前设备无法拍照，可以从相册选择。").font(.caption).foregroundStyle(.secondary)
            }
          }
          if photo != nil && (draft?.body.isEmpty == true || organized.isEmpty) {
            Button(busy ? phase : organized.isEmpty ? "提取并整理" : "重新查询／重试生成") { generate() }
              .buttonStyle(.borderedProminent).disabled(busy).accessibilityIdentifier("photo.generate")
            Text("生成时会将识别图片发送给 DeepSeek。请对照原图核对文字、数字和公式后保存。")
              .font(.caption).foregroundStyle(.secondary)
          }
          if !organized.isEmpty {
            TextField("笔记标题", text: $title).textFieldStyle(.roundedBorder).accessibilityIdentifier("photo.title")
            Text("识别文字（可编辑）").font(.headline)
            TextEditor(text: $transcript).frame(minHeight: 160).border(.secondary.opacity(0.2))
              .accessibilityIdentifier("photo.transcript")
            if let uncertain = photo?.uncertain, !uncertain.isEmpty {
              Text("待确认：" + uncertain.joined(separator: "；")).font(.caption).foregroundStyle(.orange)
            }
            Text("整理笔记（可编辑）").font(.headline)
            TextEditor(text: $organized).frame(minHeight: 180).border(.secondary.opacity(0.2))
              .accessibilityIdentifier("photo.organized")
            Picker("保存位置", selection: $targetID) {
              Text("新建照片笔记").tag("")
              ForEach(targets) { note in Text("追加到：" + note.title).tag(note.id) }
            }.accessibilityIdentifier("photo.destination")
            Button(targetID.isEmpty ? "保存到笔记" : "追加到笔记") { save() }
              .buttonStyle(.borderedProminent).disabled(busy || transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
              .accessibilityIdentifier("photo.save")
          }
          if let error { Text(error).foregroundStyle(.red).accessibilityIdentifier("photo.error") }
        }.padding()
      }.navigationTitle("照片笔记")
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("关闭") { close() }.disabled(busy && photo == nil) } }
    }
    .toolbar {
      ToolbarItemGroup(placement: .keyboard) {
        Spacer()
        Button("完成") { UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil) }
          .accessibilityIdentifier("photo.keyboard.done")
      }
    }
    .sheet(isPresented: $camera) { PhotoNoteCamera { data in importPhoto(data) } }
    .onChange(of: picked) { _, value in
      guard let value else { return }
      task = Task {
        do { guard let data = try await value.loadTransferable(type: Data.self) else { throw ContentError.invalid("照片无法读取。") }; importPhoto(data) }
        catch { self.error = error.localizedDescription }
      }
    }
    .onChange(of: targetID) { _, id in targetRevision = store.noteState.revisions[id] }
    .task { await restore() }
    .onDisappear { task?.cancel() }
  }
  private func close() {
    guard !busy, let saved = draft, let revision, let scope else { dismiss(); return }
    let originalText = saved.body.isEmpty ? saved.photos?.first?.recognizedText ?? "" : saved.body
    guard title != saved.title || transcript != originalText || organized != (saved.organizedText ?? "") else { dismiss(); return }
    guard store.noteScope == scope else { error = NoteStoreError.scopeMismatch.localizedDescription; return }
    busy = true
    task = Task {
      do {
        var updated = saved
        updated.title = title; updated.body = transcript; updated.organizedText = organized.isEmpty ? nil : organized
        _ = try await store.saveNote(updated, baseRevision: revision)
        dismiss()
      } catch { self.error = error.localizedDescription }
      busy = false
    }
  }
  private func restore() async {
    guard let id = noteID, let scope = store.noteScope else { return }
    do {
      guard let saved = try await store.repository.loadStoredNote(id, scope: scope) else { throw ContentError.invalid("笔记已删除。") }
      draft = saved.note; revision = saved.revision; self.scope = scope
      title = saved.note.title
      transcript = saved.note.body.isEmpty ? saved.note.photos?.first?.recognizedText ?? "" : saved.note.body
      organized = saved.note.organizedText ?? saved.note.photos?.first?.organizedText ?? ""
    } catch { self.error = error.localizedDescription }
  }
  private func importPhoto(_ data: Data) {
    guard !busy, draft == nil, let scope = store.noteScope, let book = store.selectedBookID,
      let course = store.selectedCourseID, let resource = store.courseResourceID(courseID: course, bookID: book) else {
      error = "请先选择学习课程和章节。"; return
    }
    let chapter = store.selectedChapterID
    let page = store.activeLesson?.page_start ?? 1
    busy = true; error = nil
    task = Task {
      do {
        let prepared = try PhotoNoteImage.prepare(data)
        let original = try await store.saveAttachment(data, extension: prepared.originalExtension)
        let path = try await store.saveAttachment(prepared.jpeg, extension: "jpg")
        try Task.checkCancellation()
        guard store.noteScope == scope else { throw NoteStoreError.scopeMismatch }
        let photo = NotePhoto(originalPath: original, preparedPath: path,
          imageSHA256: SHA256.hash(data: prepared.jpeg).map { String(format: "%02x", $0) }.joined())
        let anchor = NoteAnchor(courseID: course, courseResourceID: resource, bookID: book,
          chapterID: chapter, page: page)
        let note = StudyNote(kind: .photo, title: "照片笔记", anchor: anchor, photos: [photo])
        revision = try await store.saveNote(note)
        self.scope = scope; draft = note
      } catch { self.error = error.localizedDescription }
      busy = false
    }
  }
  private func generate() {
    guard !busy, let saved = draft, let photo, let scope, let base = revision else { return }
    guard store.noteScope == scope, let api = photoAPI else { error = "请登录并联网后生成；照片原稿仍保留。"; return }
    guard let bookID = saved.anchor?.bookID, !bookID.hasPrefix("book_local_") else {
      error = "请先完成教材上传，再提取整理；照片原稿仍保留。"; return
    }
    busy = true; error = nil; phase = "正在识别…"
    task = Task {
      do {
        let jpeg = try Data(contentsOf: store.attachmentURL(photo.preparedPath))
        let result = try await StudioService(api: api).generatePhoto(photo: photo, bookID: bookID, jpeg: jpeg, retryFailed: true) { phase in
          self.phase = phase == "organizing" ? "DeepSeek 正在整理…" : "正在提取手写文字…"
        }
        try Task.checkCancellation()
        guard store.noteScope == scope else { throw NoteStoreError.scopeMismatch }
        var updated = saved
        updated.photos?[0].recognizedText = result.transcript
        updated.photos?[0].organizedText = result.polished
        updated.photos?[0].uncertain = { if case .array(let values)? = result.result["uncertain"] { return values.compactMap(\.stringValue) }; return [] }()
        updated.organizedText = result.polished
        let proposedTitle = result.result["title"]?.stringValue ?? saved.title
        updated.title = proposedTitle
        revision = try await store.saveNote(updated, baseRevision: base)
        draft = updated; title = proposedTitle; transcript = result.transcript; organized = result.polished
      } catch { self.error = error.localizedDescription }
      busy = false
    }
  }
  private func save() {
    guard !busy, let draft, let revision, let scope else { return }
    guard store.noteScope == scope else { error = NoteStoreError.scopeMismatch.localizedDescription; return }
    busy = true; error = nil
    task = Task {
      do {
        let committed = try await store.repository.commitPhotoNote(draftID: draft.id, draftRevision: revision,
          targetID: targetID.isEmpty ? nil : targetID, targetRevision: targetRevision,
          title: title, transcript: transcript, organized: organized, scope: scope)
        guard store.noteScope == scope else { throw NoteStoreError.scopeMismatch }
        await store.reloadNotes()
        store.noteState.focusNoteID = committed.note.id
        store.showToast("照片笔记已保存")
        dismiss()
      } catch { self.error = error.localizedDescription }
      busy = false
    }
  }
}

private struct PhotoNoteCamera: UIViewControllerRepresentable {
  var onImage: (Data) -> Void
  @Environment(\.dismiss) private var dismiss
  func makeCoordinator() -> Coordinator { Coordinator(self) }
  func makeUIViewController(context: Context) -> UIImagePickerController {
    let picker = UIImagePickerController(); picker.sourceType = .camera
    picker.delegate = context.coordinator
    return picker
  }
  func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}
  final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
    let parent: PhotoNoteCamera
    init(_ parent: PhotoNoteCamera) { self.parent = parent }
    func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
      if let image = info[.originalImage] as? UIImage, let data = image.jpegData(compressionQuality: 1) { parent.onImage(data) }
      parent.dismiss()
    }
    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
  }
}
