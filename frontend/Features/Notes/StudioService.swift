import CryptoKit
import Foundation
import PencilKit
import UIKit

struct StudioCapabilities: Decodable, Sendable {
  var image: Bool
  var video: Bool
  var voice_notes: Bool
  var notes_ai: Bool
  var draft_scope: String
}

struct StudioNote: Decodable, Sendable {
  var id: String
  var book_id: String
  var revision: Int
  var audio_ready: Bool
  var audio_sha256: String
}

struct StudioJob: Codable, Sendable {
  var id: String
  var book_id: String
  var kind: String
  var status: String
  var result: [String: JSONValue]
  var error: String?
  var asset_url: String?
  var request_id: String?

  var isFinished: Bool {
    ["succeeded", "needs_confirmation", "failed", "cancelled", "uncertain"].contains(status)
  }
  var transcript: String { result["transcript"]?.stringValue ?? "" }
  var polished: String { result["polished"]?.stringValue ?? "" }
}

/// Uses the same authenticated backend as courses. Originals stay in the local note repository.
actor StudioService {
  let api: CloudAPI
  init(api: CloudAPI) { self.api = api }

  func capabilities() async throws -> StudioCapabilities {
    try await api.request("/api/studio/capabilities")
  }

  private func digest(_ value: String) -> String {
    SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
  }

  private func remoteNoteID(localID: String, scope: String) -> String {
    "native_" + digest(api.baseURL.absoluteString + "|" + scope + "|" + localID)
  }

  private func anchoredIdentity(_ note: StudyNote) -> String {
    let anchor = note.anchor
    return [note.id, anchor?.bookID ?? "", anchor?.chapterID ?? "", String(anchor?.page ?? 0),
      anchor?.quote ?? "", note.title].joined(separator: "|")
  }

  private func existingNote(id: String) async throws -> StudioNote? {
    do { return try await api.request("/api/studio/notes/\(id)") }
    catch let error as CloudError where error.status == 404 { return nil }
  }

  private func anchorBody(_ note: StudyNote, remoteID: String, revision: Int) throws -> [String: JSONValue] {
    guard let anchor = note.anchor, !anchor.bookID.isEmpty else {
      throw CloudError(status: 409, message: "请先将笔记关联到已上传的教材；原始笔记已保存在本机。")
    }
    return [
      "id": .string(remoteID), "revision": .number(Double(revision)),
      "book_id": .string(anchor.bookID), "chapter_id": .string(anchor.chapterID ?? ""),
      "chapter_title": .string(String(note.title.prefix(250))),
      "excerpt": .string(String((anchor.quote ?? "").prefix(3000))),
      "pages": .array([.number(Double(max(1, anchor.page)))]),
      "title": .string(String((note.title.isEmpty ? "学习笔记" : note.title).prefix(120))),
      "input_mode": .string(note.kind == .voice ? "voice" : "ink"), "strokes": .array([]),
    ]
  }

  func prepareVoice(note: StudyNote, fileURL: URL) async throws -> StudioNote {
    let capability = try await capabilities()
    guard capability.voice_notes else {
      throw CloudError(status: 503, message: "语音转写服务尚未就绪，原始录音已保存在本机，可稍后重试。")
    }
    let id = remoteNoteID(localID: anchoredIdentity(note), scope: capability.draft_scope)
    var remote: StudioNote
    if let existing = try await existingNote(id: id) { remote = existing }
    else {
      remote = try await api.request("/api/studio/notes", method: "POST", body: anchorBody(note, remoteID: id, revision: 0))
    }
    let values = try fileURL.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
    guard values.isRegularFile == true, let size = values.fileSize, size >= 32, size <= 20 * 1024 * 1024 else {
      throw CloudError(status: 413, message: "请选择有效且小于 20 MB 的录音，原始录音仍保留在本机。")
    }
    let bytes = try Data(contentsOf: fileURL, options: .mappedIfSafe)
    let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    if !remote.audio_ready || remote.audio_sha256 != hash {
      let data = try await api.uploadStudioAudio(noteID: id, revision: remote.revision, duration: note.duration, fileURL: fileURL)
      remote = try JSONDecoder().decode(StudioNote.self, from: data)
    }
    return remote
  }

  func prepareInk(note: StudyNote, drawingData: Data) async throws -> StudioNote {
    let capability = try await capabilities()
    guard capability.notes_ai else {
      throw CloudError(status: 503, message: "手写识别与整理服务尚未就绪，原始笔迹已保存在本机，可稍后重试。")
    }
    let strokes = try Self.inkStrokes(drawingData)
    // The immutable input digest keeps edits independent, and lets a lost response recover safely.
    let inputHash = SHA256.hash(data: drawingData).map { String(format: "%02x", $0) }.joined()
    let id = remoteNoteID(localID: anchoredIdentity(note) + "|" + inputHash, scope: capability.draft_scope)
    if let existing = try await existingNote(id: id) { return existing }
    var body = try anchorBody(note, remoteID: id, revision: 0)
    body["strokes"] = .array(strokes)
    return try await api.request("/api/studio/notes", method: "POST", body: body)
  }

  func analyze(note: StudioNote, action: String, transcript: String = "") async throws -> StudioJob {
    guard ["recognize", "improve"].contains(action) else {
      throw CloudError(status: 422, message: "不支持的笔记操作")
    }
    guard transcript.count <= 12000 else { throw CloudError(status: 422, message: "笔记文字过长，请分段整理；原稿仍保留。") }
    let requestID = "native_" + digest(api.baseURL.absoluteString + "|" + note.id + "|\(note.revision)|" + action + "|" + transcript)
    let receipt = "native.studio.note." + digest(requestID)
    if let id = UserDefaults.standard.string(forKey: receipt + ".job") { return try await job(id: id) }
    if UserDefaults.standard.bool(forKey: receipt + ".submitted") {
      guard let recovered = try await job(requestID: requestID, bookID: note.book_id) else {
        throw CloudError(status: 409, message: "上次请求的接收状态尚未确认，原稿已保留；请稍后重新查询，不会重复发起生成。")
      }
      UserDefaults.standard.set(recovered.id, forKey: receipt + ".job")
      return recovered
    }
    // Persist intent before the paid command. A lost response only enables lookup, never a new POST.
    UserDefaults.standard.set(true, forKey: receipt + ".submitted")
    do {
      let submitted: StudioJob = try await api.request(
        "/api/studio/notes/\(note.id)/analyze", method: "POST",
        body: ["request_id": .string(requestID), "revision": .number(Double(note.revision)),
          "action": .string(action), "transcript": .string(transcript), "consent": .bool(true)])
      UserDefaults.standard.set(submitted.id, forKey: receipt + ".job")
      return submitted
    } catch let error as CloudError where [400, 401, 403, 404, 409, 413, 422, 429, 503].contains(error.status) {
      UserDefaults.standard.removeObject(forKey: receipt + ".submitted")
      throw error
    }
  }

  func job(id: String) async throws -> StudioJob { try await api.request("/api/studio/jobs/\(id)") }

  func job(requestID: String, bookID: String) async throws -> StudioJob? {
    struct Jobs: Decodable, Sendable { var items: [StudioJob] }
    guard let query = bookID.addingPercentEncoding(withAllowedCharacters: .alphanumerics),
      let request = requestID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) else {
      throw CloudError(status: 422, message: "教材标识无效")
    }
    let response: Jobs = try await api.request("/api/studio/jobs?book_id=\(query)&request_id=\(request)")
    return response.items.first { $0.request_id == requestID && $0.book_id == bookID }
  }

  func waitForJob(_ initial: StudioJob) async throws -> StudioJob {
    var current = initial
    let deadline = ContinuousClock.now + .seconds(600)
    while !current.isFinished {
      try Task.checkCancellation()
      guard ContinuousClock.now < deadline else {
        throw CloudError(status: 408, message: "服务仍在处理，原稿已保留；稍后重试会查询同一任务，不会重复生成。")
      }
      try await Task.sleep(for: .seconds(2))
      current = try await job(id: current.id)
    }
    if ["failed", "cancelled", "uncertain"].contains(current.status) {
      throw CloudError(status: 409, message: current.error ?? "服务未完成处理，原稿仍保留；请稍后查看原任务。")
    }
    return current
  }

  func accept(note: StudioNote, job: StudioJob) async throws {
    guard job.status == "succeeded", !job.polished.isEmpty else {
      throw CloudError(status: 409, message: "整理结果尚未完成")
    }
    let _: CloudOK = try await api.request(
      "/api/studio/notes/\(note.id)/edition", method: "POST",
      body: ["job_id": .string(job.id), "revision": .number(Double(note.revision)), "accepted": .bool(true)])
  }

  func submitMedia(kind: String, bookID: String, chapterID: String, chapterTitle: String,
    excerpt: String, pages: [Int], requestID: String) async throws -> StudioJob {
    guard ["image", "video"].contains(kind) else { throw CloudError(status: 422, message: "不支持的媒体类型") }
    return try await api.request("/api/studio/media", method: "POST", body: [
      "kind": .string(kind), "book_id": .string(bookID), "chapter_id": .string(chapterID),
      "chapter_title": .string(String(chapterTitle.prefix(250))), "excerpt": .string(String(excerpt.prefix(3000))),
      "pages": .array(pages.prefix(30).map { .number(Double($0)) }),
      "request_id": .string(requestID), "consent": .bool(true), "goal": .string("意思"), "level": .string("标准"),
    ])
  }

  func downloadAsset(jobID: String) async throws -> Data { try await api.downloadStudioAsset(jobID: jobID) }

  static func inkStrokes(_ data: Data) throws -> [JSONValue] {
    let drawing = try PKDrawing(data: data)
    guard !drawing.strokes.isEmpty else { throw CloudError(status: 422, message: "请先写下一点内容，空白笔迹不会提交识别。") }
    guard drawing.strokes.count <= 1500, drawing.strokes.reduce(0, { $0 + $1.path.count }) <= 60000,
      drawing.strokes.allSatisfy({ $0.path.count <= 3000 }) else {
      throw CloudError(status: 422, message: "当前笔迹过多，请分成多页识别；完整原稿已保留。")
    }
    let bounds = drawing.bounds
    let offset = CGPoint(x: min(0, bounds.minX), y: min(0, bounds.minY))
    let scale = min(1, min(1000 / max(1, bounds.maxX - offset.x), 1400 / max(1, bounds.maxY - offset.y)))
    return drawing.strokes.map { stroke in
      let points: [JSONValue] = stroke.path.map { point in
        let location = point.location.applying(stroke.transform)
        return .object(["x": .number(Double(max(0, min(1000, (location.x - offset.x) * scale)))),
          "y": .number(Double(max(0, min(1400, (location.y - offset.y) * scale)))),
          "p": .number(Double(max(0, min(1, point.force))))])
      }
      let width = stroke.path.first.map { max(2, min(14, Int(($0.size.width * scale).rounded()))) } ?? 4
      return .object(["points": .array(points), "color": .string("#243148"), "width": .number(Double(width))])
    }
  }
}
