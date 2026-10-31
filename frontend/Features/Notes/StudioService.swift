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
  var video_seconds: Int?
  var image_estimate: Double?
  var video_estimate: Double?
  var media_notice: String?
  var video_notice: String?
  var image_width: Int?
  var image_height: Int?
  var video_resolution: String?
  var video_requires_image: Bool?
  var voice_text_notes: Bool?
}

struct StudioNote: Decodable, Sendable {
  var id: String
  var book_id: String
  var revision: Int
  var audio_ready: Bool
  var audio_sha256: String
  var speech_ready: Bool? = nil
  var speech_sha256: String? = nil
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
  var created: Double?
  var excerpt: String?
  var chapter_title: String?

  var isFinished: Bool {
    ["succeeded", "needs_confirmation", "failed", "cancelled", "rejected", "uncertain"].contains(status)
  }
  var transcript: String { result["transcript"]?.stringValue ?? "" }
  var polished: String { result["polished"]?.stringValue ?? "" }
}

/// Uses the same authenticated backend as courses. Originals stay in the local note repository.
actor StudioService {
  private static let referenceLock = NSLock()
  private struct LogicalReference: Codable, Equatable {
    var accountID: String
    var scope: String
    var logicalID: String
    var bookID: String
  }
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

  private func logicalNoteID(_ note: StudyNote, scope: String) -> String {
    remoteNoteID(localID: note.id, scope: scope)
  }

  private func knownInputKey(_ note: StudyNote, scope: String) -> String {
    // Logical identity survives title/anchor edits. Legacy input links remain book-scoped.
    "native.studio.note.inputs." + digest(logicalNoteID(note, scope: scope) + "|" + (note.anchor?.bookID ?? ""))
  }

  private func referenceKey(_ note: StudyNote) -> String {
    "native.studio.note.references." + digest(api.baseURL.absoluteString + "|" + note.id + "|" + (note.anchor?.bookID ?? ""))
  }

  private func archiveKey(scope: String) -> String {
    "native.studio.note.archives." + digest(api.baseURL.absoluteString + "|" + scope)
  }

  private func references(for key: String) -> [LogicalReference] {
    guard let data = UserDefaults.standard.data(forKey: key) else { return [] }
    return (try? JSONDecoder().decode([LogicalReference].self, from: data)) ?? []
  }

  private func storeReferences(_ references: [LogicalReference], for key: String) {
    UserDefaults.standard.set(try? JSONEncoder().encode(references), forKey: key)
  }

  private func updateReferences(for key: String, _ update: ([LogicalReference]) -> [LogicalReference]) {
    Self.referenceLock.withLock { storeReferences(update(references(for: key)), for: key) }
  }

  private func rememberInput(_ remote: StudioNote, note: StudyNote, scope: String) async {
    let key = knownInputKey(note, scope: scope)
    let previous = UserDefaults.standard.stringArray(forKey: key) ?? []
    UserDefaults.standard.set(Array(([remote.id] + previous.filter { $0 != remote.id }).prefix(200)), forKey: key)
    guard let accountID = await api.verifiedAccount()?.user_id else { return }
    let reference = LogicalReference(accountID: accountID, scope: scope,
      logicalID: logicalNoteID(note, scope: scope), bookID: remote.book_id)
    let referenceKey = referenceKey(note)
    updateReferences(for: referenceKey) { [reference] + $0.filter { $0 != reference } }
  }

  /// Local deletion succeeds independently; failed metadata cleanup remains in its original account's outbox.
  func deleteLogicalNote(note: StudyNote) async {
    guard let accountID = await api.verifiedAccount()?.user_id else { return }
    for reference in references(for: referenceKey(note)) where reference.accountID == accountID {
      let key = archiveKey(scope: reference.scope)
      updateReferences(for: key) { [reference] + $0.filter { $0 != reference } }
    }
    do {
      let capability = try await capabilities()
      try await flushArchives(scope: capability.draft_scope)
    } catch { /* The next preparation for this account and server retries the metadata request. */ }
  }

  private func flushArchives(scope: String) async throws {
    guard let accountID = await api.verifiedAccount()?.user_id else { return }
    let key = archiveKey(scope: scope)
    for reference in references(for: key) where reference.scope == scope && reference.accountID == accountID {
      do {
        let _: [String: JSONValue] = try await api.request(
          "/api/studio/note-groups/\(reference.logicalID)/archive", method: "PUT",
          body: ["book_id": .string(reference.bookID)])
      } catch let error as CloudError where error.status == 404 {
        // No owned group remains to consume quota; retrying cannot change that.
      }
      updateReferences(for: key) { $0.filter { $0 != reference } }
    }
  }

  private func bindExistingInput(_ remote: StudioNote, note: StudyNote, scope: String) async throws -> StudioNote {
    let known = UserDefaults.standard.stringArray(forKey: knownInputKey(note, scope: scope)) ?? []
    let bound: StudioNote = try await api.request("/api/studio/notes/\(remote.id)/logical-group", method: "PUT", body: [
      "logical_note_id": .string(logicalNoteID(note, scope: scope)),
      "legacy_note_ids": .array(known.filter { $0 != remote.id }.prefix(199).map(JSONValue.string)),
    ])
    guard bound.id == remote.id, bound.book_id == remote.book_id, bound.book_id == note.anchor?.bookID,
      bound.revision == remote.revision else {
      throw CloudError(status: 409, message: "笔记关联信息已变化，请重新打开；原始笔记仍保留。")
    }
    await rememberInput(bound, note: note, scope: scope)
    return bound
  }

  private func anchorBody(_ note: StudyNote, remoteID: String, revision: Int, scope: String) throws -> [String: JSONValue] {
    guard let anchor = note.anchor, !anchor.bookID.isEmpty else {
      throw CloudError(status: 409, message: "请先将笔记关联到已上传的教材；原始笔记已保存在本机。")
    }
    return [
      "id": .string(remoteID), "revision": .number(Double(revision)),
      "logical_note_id": .string(logicalNoteID(note, scope: scope)),
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
    try await flushArchives(scope: capability.draft_scope)
    guard capability.voice_notes else {
      throw CloudError(status: 503, message: "语音转写服务尚未就绪，原始录音已保存在本机，可稍后重试。")
    }
    let id = remoteNoteID(localID: anchoredIdentity(note), scope: capability.draft_scope)
    var remote: StudioNote
    if let existing = try await existingNote(id: id) {
      remote = try await bindExistingInput(existing, note: note, scope: capability.draft_scope)
    }
    else {
      remote = try await api.request("/api/studio/notes", method: "POST",
        body: anchorBody(note, remoteID: id, revision: 0, scope: capability.draft_scope))
      await rememberInput(remote, note: note, scope: capability.draft_scope)
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
    try await flushArchives(scope: capability.draft_scope)
    guard capability.notes_ai else {
      throw CloudError(status: 503, message: "手写识别与整理服务尚未就绪，原始笔迹已保存在本机，可稍后重试。")
    }
    let strokes = try Self.inkStrokes(drawingData)
    // The immutable input digest keeps edits independent, and lets a lost response recover safely.
    let inputHash = SHA256.hash(data: drawingData).map { String(format: "%02x", $0) }.joined()
    let id = remoteNoteID(localID: anchoredIdentity(note) + "|" + inputHash, scope: capability.draft_scope)
    if let existing = try await existingNote(id: id) {
      return try await bindExistingInput(existing, note: note, scope: capability.draft_scope)
    }
    var body = try anchorBody(note, remoteID: id, revision: 0, scope: capability.draft_scope)
    body["strokes"] = .array(strokes)
    let remote: StudioNote = try await api.request("/api/studio/notes", method: "POST", body: body)
    await rememberInput(remote, note: note, scope: capability.draft_scope)
    return remote
  }

  func analyze(note: StudioNote, action: String, transcript: String = "", requestID: String? = nil,
    retryFailed: Bool = false) async throws -> StudioJob {
    guard ["recognize", "improve", "voice_note"].contains(action) else {
      throw CloudError(status: 422, message: "不支持的笔记操作")
    }
    guard transcript.count <= 12000 else { throw CloudError(status: 422, message: "笔记文字过长，请分段整理；原稿仍保留。") }
    let originalRequestID = requestID ?? ("native_" + digest(api.baseURL.absoluteString + "|" + note.id + "|\(note.revision)|" + action + "|" + transcript))
    let latestKey = "native.studio.note.latest." + digest(originalRequestID)
    var attempt = requestID == nil
      ? UserDefaults.standard.string(forKey: latestKey) ?? originalRequestID : originalRequestID
    guard (8...100).contains(attempt.utf8.count), attempt.utf8.allSatisfy({
      (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95
    }) else { throw CloudError(status: 422, message: "笔记生成请求标识无效。") }
    if let existing = try await storedAnalysis(note: note, action: action, requestID: attempt) {
      guard requestID == nil, retryFailed, ["failed", "cancelled", "rejected"].contains(existing.status) else {
        return existing
      }
      // Only an explicit retry of a confirmed terminal failure gets a new attempt.
      // Persist that link first so reopening follows it even if the POST reply is lost.
      attempt = "native_note_" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
      UserDefaults.standard.set(attempt, forKey: latestKey)
    }
    let receipt = "native.studio.note." + digest(attempt)
    // Persist intent before the paid command. A lost response only enables lookup, never a new POST.
    UserDefaults.standard.set(true, forKey: receipt + ".submitted")
    do {
      let submitted: StudioJob = try await api.request(
        "/api/studio/notes/\(note.id)/analyze", method: "POST",
        body: ["request_id": .string(attempt), "revision": .number(Double(note.revision)),
          "action": .string(action), "transcript": .string(transcript), "consent": .bool(true)])
      UserDefaults.standard.set(submitted.id, forKey: receipt + ".job")
      return submitted
    } catch let error as CloudError where [400, 401, 403, 404, 409, 413, 422, 429, 503].contains(error.status) {
      UserDefaults.standard.removeObject(forKey: receipt + ".submitted")
      throw error
    }
  }

  private func storedAnalysis(note: StudioNote, action: String, requestID: String) async throws -> StudioJob? {
    let receipt = "native.studio.note." + digest(requestID)
    let existing: StudioJob
    if let id = UserDefaults.standard.string(forKey: receipt + ".job") {
      existing = try await job(id: id)
    } else {
      guard UserDefaults.standard.bool(forKey: receipt + ".submitted") else { return nil }
      guard let recovered = try await job(requestID: requestID, bookID: note.book_id) else {
        throw CloudError(status: 409, message: "上次请求的接收状态尚未确认，原稿已保留；请稍后重新查询，不会重复发起生成。")
      }
      UserDefaults.standard.set(recovered.id, forKey: receipt + ".job")
      existing = recovered
    }
    guard existing.book_id == note.book_id, existing.kind == action else {
      throw CloudError(status: 422, message: "返回任务与当前笔记操作不一致，已停止提交。")
    }
    return existing
  }

  private func voiceRequestKey(_ note: StudioNote) -> String {
    "native.studio.voice.latest." + digest(api.baseURL.absoluteString + "|" + note.id + "|\(note.revision)")
  }

  private func submittedAnalysis(_ requestID: String) -> Bool {
    UserDefaults.standard.bool(forKey: "native.studio.note." + digest(requestID) + ".submitted")
  }

  private func existingVoiceJob(_ remote: StudioNote, requestID: String) async throws -> StudioJob? {
    let key = "native.studio.note." + digest(requestID)
    if let id = UserDefaults.standard.string(forKey: key + ".job") {
      do { return try await job(id: id) }
      catch let error as CloudError where error.status == 404 { }
    }
    return try await job(requestID: requestID, bookID: remote.book_id)
  }

  private func completedVoice(_ remote: StudioNote, initial: StudioJob,
    onProgress: (@MainActor @Sendable (String) -> Void)?) async throws -> (remoteNote: StudioNote, job: StudioJob) {
    try Task.checkCancellation()
    guard initial.kind == "voice_note", initial.book_id == remote.book_id else {
      throw CloudError(status: 422, message: "返回任务与当前语音笔记不一致。")
    }
    let result = try await waitForJob(initial, onProgress: onProgress)
    try Task.checkCancellation()
    guard result.kind == "voice_note", result.book_id == remote.book_id, result.status == "succeeded",
      !result.polished.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      throw CloudError(status: 422, message: "服务没有返回完整的生成笔记，原始录音仍保留。")
    }
    return (remote, result)
  }

  /// Called only after an explicit Generate action. A new attempt is allowed
  /// only after the previous job is confirmed failed; ambiguous writes are read back.
  func generateVoice(note: StudyNote, fileURL: URL, retryFailed: Bool = false,
    onProgress: (@MainActor @Sendable (String) -> Void)? = nil) async throws -> (remoteNote: StudioNote, job: StudioJob) {
    try Task.checkCancellation()
    let remote = try await prepareVoice(note: note, fileURL: fileURL)
    return try await generatePreparedVoice(remote, retryFailed: retryFailed, onProgress: onProgress)
  }

  private func generatePreparedVoice(_ remote: StudioNote, retryFailed: Bool,
    onProgress: (@MainActor @Sendable (String) -> Void)?) async throws -> (remoteNote: StudioNote, job: StudioJob) {
    let key = voiceRequestKey(remote)
    var requestID = UserDefaults.standard.string(forKey: key)
    if let existingID = requestID {
      if let existing = try await existingVoiceJob(remote, requestID: existingID) {
        if retryFailed && ["failed", "cancelled"].contains(existing.status) {
          requestID = nil
        } else {
          return try await completedVoice(remote, initial: existing, onProgress: onProgress)
        }
      } else if submittedAnalysis(existingID) {
        throw CloudError(status: 409, message: "上次生成请求尚未确认，请稍后查询原任务，不会重复提交。")
      }
    }
    let attempt = requestID ?? ("native_voice_" + UUID().uuidString.replacingOccurrences(of: "-", with: ""))
    UserDefaults.standard.set(attempt, forKey: key)
    let initial = try await analyze(note: remote, action: "voice_note", requestID: attempt)
    return try await completedVoice(remote, initial: initial, onProgress: onProgress)
  }

  private func speechText(_ text: String, source: String, language: String) throws -> String {
    let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty, text.count <= 12000, source == "apple-speech-on-device", language == "zh-CN" else {
      throw CloudError(status: 422, message: "请先完成本机中文语音识别，识别文字应在 12000 字以内。")
    }
    return text
  }

  private func speechRemoteID(_ note: StudyNote, scope: String) -> String {
    remoteNoteID(localID: anchoredIdentity(note) + "|on-device-speech", scope: scope)
  }

  func generateVoiceFromTranscript(note: StudyNote, transcript: String,
    source: String = "apple-speech-on-device", language: String = "zh-CN", retryFailed: Bool = false,
    onProgress: (@MainActor @Sendable (String) -> Void)? = nil) async throws -> (remoteNote: StudioNote, job: StudioJob) {
    try Task.checkCancellation()
    let text = try speechText(transcript, source: source, language: language)
    let capability = try await capabilities()
    try await flushArchives(scope: capability.draft_scope)
    guard capability.voice_text_notes == true else {
      throw CloudError(status: 503, message: "文字整理服务尚未就绪，本机录音和识别文字仍保留；不会改为上传录音。")
    }
    let id = speechRemoteID(note, scope: capability.draft_scope)
    var remote: StudioNote
    if let existing = try await existingNote(id: id) {
      remote = try await bindExistingInput(existing, note: note, scope: capability.draft_scope)
    }
    else {
      remote = try await api.request("/api/studio/notes", method: "POST",
        body: anchorBody(note, remoteID: id, revision: 0, scope: capability.draft_scope))
      await rememberInput(remote, note: note, scope: capability.draft_scope)
    }
    let hash = SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    if remote.speech_ready != true || remote.speech_sha256 != hash {
      remote = try await api.request("/api/studio/notes/\(id)/speech-text", method: "PUT", body: [
        "revision": .number(Double(remote.revision)), "text": .string(text),
        "source": .string(source), "language": .string(language),
      ])
    }
    return try await generatePreparedVoice(remote, retryFailed: retryFailed, onProgress: onProgress)
  }

  /// A text-path receipt is independent of an audio-upload receipt for the same note.
  func restoreVoiceFromTranscript(note: StudyNote, transcript: String,
    source: String = "apple-speech-on-device", language: String = "zh-CN",
    onProgress: (@MainActor @Sendable (String) -> Void)? = nil) async throws -> (remoteNote: StudioNote, job: StudioJob)? {
    try Task.checkCancellation()
    let text = try speechText(transcript, source: source, language: language)
    let capability = try await capabilities()
    let id = speechRemoteID(note, scope: capability.draft_scope)
    guard let remote = try await existingNote(id: id), remote.speech_ready == true else { return nil }
    let hash = SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    guard remote.speech_sha256 == hash else { return nil }
    return try await restorePreparedVoice(remote, onProgress: onProgress)
  }

  private func restorePreparedVoice(_ remote: StudioNote,
    onProgress: (@MainActor @Sendable (String) -> Void)?) async throws -> (remoteNote: StudioNote, job: StudioJob)? {
    guard let requestID = UserDefaults.standard.string(forKey: voiceRequestKey(remote)) else { return nil }
    guard let existing = try await existingVoiceJob(remote, requestID: requestID) else {
      if submittedAnalysis(requestID) {
        throw CloudError(status: 409, message: "上次生成请求尚未确认，请稍后查询原任务，不会重复提交。")
      }
      return nil
    }
    return try await completedVoice(remote, initial: existing, onProgress: onProgress)
  }

  /// Reopening a recording performs GETs only. It never uploads audio or submits
  /// a model request, including when no server note or receipt exists yet.
  func restoreVoice(note: StudyNote, fileURL: URL,
    onProgress: (@MainActor @Sendable (String) -> Void)? = nil) async throws -> (remoteNote: StudioNote, job: StudioJob)? {
    try Task.checkCancellation()
    let capability = try await capabilities()
    let id = remoteNoteID(localID: anchoredIdentity(note), scope: capability.draft_scope)
    guard let remote = try await existingNote(id: id), remote.audio_ready else { return nil }
    let values = try fileURL.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
    guard values.isRegularFile == true, let size = values.fileSize, (32...20 * 1024 * 1024).contains(size) else {
      throw CloudError(status: 413, message: "原始录音文件暂不可读取，请先确认本机录音仍保留。")
    }
    let bytes = try Data(contentsOf: fileURL, options: .mappedIfSafe)
    let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    guard remote.audio_sha256 == hash else { return nil }
    return try await restorePreparedVoice(remote, onProgress: onProgress)
  }

  func mediaJobs(bookID: String) async throws -> [StudioJob] {
    struct Jobs: Decodable, Sendable { var items: [StudioJob] }
    guard let query = bookID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) else {
      throw CloudError(status: 422, message: "教材标识无效")
    }
    let response: Jobs = try await api.request("/api/studio/jobs?book_id=\(query)")
    return response.items
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

  func waitForJob(_ initial: StudioJob,
    onProgress: (@MainActor @Sendable (String) -> Void)? = nil) async throws -> StudioJob {
    var current = initial
    let deadline = ContinuousClock.now + .seconds(600)
    if let onProgress { await onProgress(progressPhase(current)) }
    while !current.isFinished {
      try Task.checkCancellation()
      guard ContinuousClock.now < deadline else {
        throw CloudError(status: 408, message: "服务仍在处理，原稿已保留；稍后重试会查询同一任务，不会重复生成。")
      }
      try await Task.sleep(for: .seconds(2))
      current = try await job(id: current.id)
      if let onProgress { await onProgress(progressPhase(current)) }
    }
    if ["failed", "cancelled", "rejected", "uncertain"].contains(current.status) {
      throw CloudError(status: 409, message: current.error ?? "服务未完成处理，原稿仍保留；请稍后查看原任务。")
    }
    return current
  }

  private func progressPhase(_ job: StudioJob) -> String {
    let phase = job.result["phase"]?.stringValue ?? job.status
    return ["transcribing", "organizing", "queued", "planning", "submitting", "succeeded", "failed"].contains(phase)
      ? phase : "generating"
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
