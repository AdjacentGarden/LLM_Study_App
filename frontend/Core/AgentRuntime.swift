import Foundation
import Observation

/// What the reader hands over. It is frozen into the run before any network work.
struct AgentSelection: Sendable, Equatable {
  var bookID: String
  var courseID: String?
  var chapterID: String?
  var page: Int
  var printedPage: Int?
  var text: String
}

/// Validated save_note arguments; evidence IDs are already checked against the run.
struct ValidatedSaveNote: Sendable, Equatable {
  var title: String
  var body: String
  var evidenceIDs: [String]
  var sourceLabel: String
}

struct AgentValidationError: Error, Equatable, Sendable, LocalizedError {
  var message: String
  var errorDescription: String? { message }
}

/// Owns the phone-side tool loop for one selection. Views observe it but never
/// start or hold the drive task, so page turns and overlay dismissal cannot
/// strand a run.
@MainActor @Observable final class AgentRuntime {
  private(set) var active: AgentRunRecord?
  private(set) var isWorking = false
  private(set) var notice: String?

  // Test seams for the two server-side waits.
  var pollInterval: Duration = .seconds(2)
  var submittingWindow: Duration = .seconds(90)
  // Test seam: hold a drive after the save_note commit returns, before publish.
  @ObservationIgnored var afterCommitToolNote: (@MainActor () async -> Void)?

  @ObservationIgnored weak var store: AppStore?
  @ObservationIgnored private var driveTask: Task<Void, Never>?
  @ObservationIgnored private var driveGeneration = 0
  @ObservationIgnored private var driveGenerationAtStart = 0
  @ObservationIgnored private var cancelInFlight = false
  @ObservationIgnored private var registeredSessionIDs: Set<String> = []

  init(store: AppStore? = nil) { self.store = store }

  /// One gateway and one executor for every entry point, without requiring a selected textbook.
  func answerUnified(sessionID: String, messageID: String, request: String,
    context: AgentFrozenContext, history: [ChatMessage], attachments: [AssistantAttachment],
    ephemeral: Bool) async throws -> AssistantHistoryAnswer {
    try Task.checkCancellation()
    guard let store, let scope = store.noteScope, store.cloud != nil,
      store.processingPolicy.path.kind != .offline else { throw AgentValidationError(message: "AI 助手需要联网") }
    guard ephemeral || !store.assistantHistory.archive.removedIDs.contains(sessionID) else { throw CancellationError() }
    guard !isWorking, !cancelInFlight, active?.status.isTerminal != false else { throw AgentValidationError(message: "请先完成或取消当前助手任务") }
    if let legacy = store.assistantHistory.sessions.first(where: { $0.id == sessionID }),
      (legacy.runs ?? []).contains(where: { $0.privateTransport != true && $0.status != .completed }) {
      throw AgentValidationError(message: "旧版未完成任务已归档，请另开新版对话")
    }
    var context = context
    context.scope = scope
    context.sessionID = sessionID
    context.messageID = messageID
    let previousMessages = history.filter { $0.id != messageID && ["user", "assistant"].contains($0.role) }
    let now = Date()
    var run = AgentRunRecord(id: "run-" + UUID().uuidString.lowercased(), scope: scope,
      status: .running, request: request, context: context, evidence: [], steps: [], createdAt: now, updatedAt: now)
    run.ephemeral = ephemeral
    var seenAttachments = Set<String>()
    run.attachments = (attachments + history.flatMap { $0.attachments ?? [] }).filter { seenAttachments.insert($0.id).inserted }
    run.privateTransport = true
    run.history = previousMessages
    let previousRuns = ephemeral ? [active].compactMap { $0 }.filter { $0.context.sessionID == sessionID }
      : store.assistantHistory.sessions.first(where: { $0.id == sessionID })?.runs ?? []
    let prior = previousRuns.filter { $0.status == .completed && $0.privateTransport == true }
    if !prior.isEmpty {
      run.clientHistory = prior.flatMap { Self.privateTranscript(for: $0, stepIndex: $0.steps.count, includeHistory: false) }
    }
    // Prior image inputs already live in the frozen clientHistory transcript.
    // New tool images belong only to this turn, avoiding quadratic duplication.
    run.images = nil

    run.events = [AgentRunEvent(kind: "started", message: "正在处理请求")]
    run.contextTokens = (request.utf8.count + history.reduce(0) { $0 + $1.text.utf8.count }) / 3
    run.contextEstimated = true
    if !context.selection.isEmpty {
      run.evidence = [Self.evidence(for: AgentSelection(bookID: context.bookID, courseID: context.courseID,
        chapterID: context.chapterID, page: context.page, printedPage: context.printedPage, text: context.selection), sourceVersion: context.sourceVersion)]
    }
    if ephemeral, let prior = active, prior.ephemeral == true, prior.scope == scope, prior.context.sessionID == sessionID {
      let fresh = Set(run.evidence.map(\.id))
      run.evidence += prior.evidence.filter { !fresh.contains($0.id) }
    }
    active = run
    begin { [weak self] in
      guard let self else { return }
      if case .saved(let saved) = await self.checkpoint(run) { await self.runLoop(runID: saved.id, scope: scope) }
    }
    while let current = active, current.id == run.id, current.scope == scope {
      try Task.checkCancellation()
      if current.status.isTerminal {
        if current.status == .completed {
          return AssistantHistoryAnswer(text: current.finalText ?? "", citations: current.finalCitations ?? [],
            courseIDs: current.finalCourseIDs ?? [], contextTokens: current.contextTokens,
            capacityState: current.capacityState, sourceLabel: current.finalSourceLabel)
        }
        throw AgentValidationError(message: current.failure ?? notice ?? "任务已取消")
      }
      if !isWorking, current.status != .waitingForUser {
        throw AgentValidationError(message: notice ?? "任务已暂停，联网后可继续核对")
      }
      try await Task.sleep(for: .milliseconds(100))
    }
    throw CancellationError()
  }

  /// Build reply metadata from executor receipts and registered evidence, including recovered runs.
  nonisolated static func completeAnswer(_ text: String, run: inout AgentRunRecord) {
    var answer = text
    let cited = run.evidence.filter { text.contains("[" + $0.id + "]") }
      .sorted { (text.range(of: "[" + $0.id + "]")?.lowerBound ?? text.endIndex)
        < (text.range(of: "[" + $1.id + "]")?.lowerBound ?? text.endIndex) }
    let textbook = cited.filter { ($0.sourceType == "textbook" || $0.sourceType == nil) && !$0.bookID.isEmpty && !$0.quote.isEmpty }
    run.finalCitations = textbook.enumerated().map { index, evidence in
      answer = answer.replacingOccurrences(of: "[" + evidence.id + "]", with: "[\(index + 1)]")
      return Citation(bookID: evidence.bookID, chapterID: evidence.chapterID ?? "", chapterTitle: "教材原文",
        page: evidence.page, printedPage: evidence.printedPage, chunkID: evidence.id, quote: evidence.quote, score: 1)
    }
    // Raw and invented evidence handles must never masquerade as clickable citations.
    if let pattern = try? NSRegularExpression(pattern: "\\[(?:ev[-_]|evidence_|src_)[A-Za-z0-9_:.-]+\\]") {
      answer = pattern.stringByReplacingMatches(in: answer, range: NSRange(answer.startIndex..., in: answer), withTemplate: "")
    }
    var kinds = Set<String>()
    if run.evidence.contains(where: { $0.sourceType == "textbook" || ($0.sourceType == nil && !$0.bookID.isEmpty) }) {
      kinds.insert(textbook.isEmpty ? "textbook_uncited" : "textbook")
    }
    for evidence in run.evidence {
      if evidence.sourceType == "attachment" { kinds.insert("attachments") }
      if evidence.sourceType == "screenshot" { kinds.insert("screen") }
    }
    var links: [(Int, String)] = []
    for step in run.steps {
      for call in step.toolCalls {
        guard let receipt = step.outcome(call.id), receipt.status == "ok",
          let object = try? JSONSerialization.jsonObject(with: Data(receipt.resultJSON.utf8)) as? [String: Any],
          object["ok"] as? Bool != false else { continue }
        if ["list_courses", "read_course_catalog", "read_learning_data"].contains(call.name) { kinds.insert("learning_records") }
        if call.name == "search_discovery" {
          kinds.insert("public_catalog")
          for item in object["items"] as? [[String: Any]] ?? [] {
            guard let id = item["catalog_id"] as? String ?? item["id"] as? String,
              let title = item["title"] as? String, !title.isEmpty, let range = text.range(of: title) else { continue }
            links.append((text.distance(from: text.startIndex, to: range.lowerBound), id))
          }
        }
      }
    }
    var seen = Set<String>()
    run.finalCourseIDs = Array(links.sorted { $0.0 < $1.0 }.map(\.1).filter { seen.insert($0).inserted }.prefix(6))
    run.finalSourceLabel = kinds.count > 1 ? "mixed" : kinds.first ?? "general"
    run.finalText = answer.trimmingCharacters(in: .whitespacesAndNewlines)
  }

  func frozenContext(session: AssistantSessionContext) -> AgentFrozenContext {
    let store = self.store
    let hasSnapshot = session.pageType != nil
    let source = store?.sources.first { $0.id == session.bookID }
    return AgentFrozenContext(scope: store?.noteScope ?? "", courseID: session.courseID,
      bookID: session.bookID, bookTitle: session.bookTitle ?? source?.summary.title ?? "", sourceVersion: session.sourceVersion ?? source.map { Self.sourceVersion(for: $0.summary) } ?? "",
      chapterID: session.chapterID, page: max(1, session.page ?? store?.sourceTarget?.page ?? store?.activeLesson?.page_start ?? 1),
      printedPage: hasSnapshot ? session.printedPage : store?.sourceTarget?.printedPage, selection: session.selection ?? store?.selectedQuote ?? "",
      currentNoteID: hasSnapshot ? session.currentNoteID : store?.noteState.focusNoteID, capturedAt: .now,
      courseResourceID: session.courseResourceID ?? session.courseID.flatMap { store?.courseResourceID(courseID: $0, bookID: session.bookID) },
      pageType: session.pageType ?? store?.route.rawValue, practiceState: hasSnapshot ? session.practiceState : (store?.route == .assignment ? "pending" : nil))
  }

  private func loadRun(_ id: String, scope: String) async throws -> AgentRunRecord? {
    if let active, active.id == id, active.scope == scope, active.ephemeral == true { return active }
    return try await store?.repository.loadRun(id, scope: scope)
  }

  /// Closing a temporary annotation discards conversation state and never creates history.
  func closeEphemeral() {
    guard let run = active, run.ephemeral == true else { return }
    invalidateDrive()
    if let store {
      let preserved = Set(store.notes.flatMap { $0.references ?? [] }.compactMap(\.attachmentPath))
      let paths = (run.events ?? []).compactMap(\.attachmentPath) + (run.attachments ?? []).flatMap { [$0.path, $0.thumbnailPath, $0.textPath].compactMap { $0 } }
      for path in paths where !preserved.contains(path) { try? FileManager.default.removeItem(at: store.repository.attachmentURL(path)) }
    }
    active = nil
    if run.privateTransport != true, let cloud = store?.cloud, let sessionID = run.context.sessionID {
      Task { try? await cloud.closeTemporaryAssistantSession(sessionID) }
    }
  }

  // MARK: Pure helpers

  nonisolated static func sourceVersion(for summary: SourceSummary) -> String {
    let digest = AgentDigest.sha256(
      "\(summary.book_id)|\(summary.page_count)|\(summary.chunk_count)|\(summary.updated_at)")
    return "src:" + String(digest.prefix(32))
  }

  nonisolated static func evidence(
    for selection: AgentSelection, sourceVersion: String
  ) -> AgentEvidence {
    let quote = selection.text.trimmingCharacters(in: .whitespacesAndNewlines)
    let digest = AgentDigest.sha256("\(selection.courseID ?? "")|\(selection.bookID)|\(sourceVersion)|\(selection.page)|\(quote)")
    return AgentEvidence(
      id: "ev-" + String(digest.prefix(16)), bookID: selection.bookID,
      courseID: selection.courseID, sourceVersion: sourceVersion,
      chapterID: selection.chapterID, page: selection.page,
      printedPage: selection.printedPage, quote: quote,
      quoteSHA256: AgentDigest.sha256(quote))
  }

  nonisolated static func turnRequest(
    for run: AgentRunRecord, stepIndex: Int
  ) -> AssistantTurnRequestDTO {
    var results: [AssistantToolResultDTO] = []
    for step in run.steps where step.index < stepIndex {
      for call in step.toolCalls {
        guard let outcome = step.outcome(call.id) else { continue }
        let object = (try? JSONSerialization.jsonObject(with: Data(outcome.resultJSON.utf8))) as? [String: Any]
        results.append(AssistantToolResultDTO(
          step_index: step.index, tool_call_id: call.id,
          content: outcome.resultJSON, complete: object?["complete"] as? Bool ?? true,
          continuation: (object?["next_offset"] as? Int).map(String.init)))
      }
    }
    var context = AssistantTurnContextDTO(
      book_id: run.context.bookID, book_title: run.context.bookTitle,
      course_id: run.context.courseID, source_version: run.context.sourceVersion,
      chapter_id: run.context.chapterID, page: max(1, run.context.page),
      printed_page: run.context.printedPage, selection: run.context.selection,
      current_note_id: run.context.currentNoteID)
    context.course_resource_id = run.context.courseResourceID
    context.page_type = run.context.pageType
    context.practice_state = run.context.practiceState
    let attachmentMetadata: [[String: JSONValue]] = (run.attachments ?? []).map {
      ["attachment_id": .string($0.id), "filename": .string($0.filename),
       "kind": .string($0.kind.rawValue), "page_count": .number(Double($0.pageCount ?? 1))]
    }
    context.snapshot = ["attachments": .array(attachmentMetadata.map(JSONValue.object))]
    let evidence = run.evidence.map {
      AssistantTurnEvidenceDTO(
        evidence_id: $0.id, page: $0.page, printed_page: $0.printedPage, quote: $0.quote,
        source_type: $0.sourceType, object_id: $0.objectID, source_version: $0.sourceVersion, book_id: $0.bookID,
        course_id: $0.courseID, course_resource_id: $0.courseResourceID)
    }
    var body = AssistantTurnRequestDTO(
      contract_version: run.context.sessionID == nil ? 1 : 2, run_id: run.id, step_id: "\(run.id).s\(stepIndex)",
      step_index: stepIndex, request: run.request, context: context, evidence: evidence,
      tools: run.context.sessionID == nil ? ["save_note", "open_note", "ask_user"] : UnifiedAgentTools.names, tool_results: results)
    body.session_id = run.context.sessionID
    body.message_id = run.context.messageID
    body.ephemeral = run.ephemeral
    body.attachments = run.images?.map { original in
      var image = original
      let introducedNow = run.steps.first(where: { $0.index == stepIndex - 1 })?.outcomes.contains { $0.resultJSON.contains(original.attachment_id) } == true
      if stepIndex > 0, !introducedNow { image.data_base64 = nil }
      return image
    }
    // Only the current session is supplied. The gateway persists ordinary turns incrementally.
    body.history = stepIndex == 0 && run.ephemeral != true ? run.history?.map { ["role": .string($0.role), "text": .string($0.text)] } : nil
    if run.privateTransport == true {
      body.contract_version = 3
      body.client_messages = privateTranscript(for: run, stepIndex: stepIndex)
      body.history = nil
      body.attachments = nil // Actual bytes are already in the complete client transcript.
    }
    return body
  }

  /// Full current-run receipts and images are rebuilt from phone-owned durable state.
  nonisolated static func privateTranscript(for run: AgentRunRecord, stepIndex: Int, includeHistory: Bool = true) -> [[String: JSONValue]] {
    var messages: [[String: JSONValue]] = []
    if includeHistory {
      if let saved = run.clientHistory { messages += saved }
      else { messages += (run.history ?? []).map { ["role": .string($0.role), "content": .string($0.text)] } }
    }
    let context = (try? JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(run.context))) ?? .null
    let evidence = (try? JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(run.evidence))) ?? .array([])
    let snapshot = (try? AgentDigest.canonicalJSON(["context": foundationObject(context), "evidence": foundationObject(evidence)])) ?? ""
    var parts: [JSONValue] = [.object(["type": .string("text"), "text": .string("发送时页面与可用证据：" + snapshot + "\n用户请求：" + run.request)])]
    for image in run.images ?? [] {
      guard let bytes = image.data_base64 else { continue }
      parts.append(.object(["type": .string("text"), "text": .string("图片标识：" + image.attachment_id)]))
      parts.append(.object(["type": .string("image_url"), "image_url": .object(["url": .string("data:" + image.media_type + ";base64," + bytes)])]))
    }
    messages.append(["role": .string("user"), "content": .array(parts)])
    for step in run.steps where step.index < stepIndex && step.state == .completed {
      var message: [String: JSONValue] = ["role": .string("assistant"), "content": .string(step.content ?? "")]
      if !step.toolCalls.isEmpty {
        message["tool_calls"] = .array(step.toolCalls.map { call in .object(["id": .string(call.id), "type": .string("function"),
          "function": .object(["name": .string(call.name), "arguments": .string(call.argumentsJSON)])]) })
      }
      messages.append(message)
      for call in step.toolCalls {
        guard let result = step.outcome(call.id) else { continue }
        messages.append(["role": .string("tool"), "tool_call_id": .string(call.id), "content": .string(result.resultJSON)])
      }
    }
    return messages
  }

  nonisolated static func validateSaveNote(
    _ args: [String: JSONValue], run: AgentRunRecord
  ) -> Result<ValidatedSaveNote, AgentValidationError> {
    guard let title = args["title"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
      (1...80).contains(title.count) else {
      return .failure(AgentValidationError(message: "笔记标题需为 1 到 80 字"))
    }
    guard let body = args["body"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
      (1...8000).contains(body.count) else {
      return .failure(AgentValidationError(message: "笔记正文需为 1 到 8000 字"))
    }
    guard let evidenceIDs = Self.strings(args["evidence_ids"]) else {
      return .failure(AgentValidationError(message: "引用编号格式无效"))
    }
    guard evidenceIDs.count <= 8, Set(evidenceIDs).count == evidenceIDs.count else {
      return .failure(AgentValidationError(message: "引用编号最多 8 个且不能重复"))
    }
    let known = Set(run.evidence.map(\.id))
    guard evidenceIDs.allSatisfy({ known.contains($0) }) else {
      return .failure(AgentValidationError(message: "引用不是本机登记的原文证据"))
    }
    guard var label = args["source_label"]?.stringValue,
      ["textbook", "general", "mixed"].contains(label) else {
      return .failure(AgentValidationError(message: "来源类型无效"))
    }
    // The note does cite the textbook, so the label must not claim otherwise.
    if label == "general", !evidenceIDs.isEmpty { label = "mixed" }
    if label != "general", evidenceIDs.isEmpty {
      return .failure(AgentValidationError(message: "教材来源需要至少一条原文引用"))
    }
    return .success(ValidatedSaveNote(
      title: title, body: body, evidenceIDs: evidenceIDs, sourceLabel: label))
  }

  /// AgentToolCall stores canonical JSON, so tool execution decodes it back.
  nonisolated static func arguments(_ call: AgentToolCall) -> [String: JSONValue] {
    (try? JSONDecoder().decode(
      [String: JSONValue].self, from: Data(call.argumentsJSON.utf8))) ?? [:]
  }

  /// Foundation representation for AgentDigest.canonicalJSON.
  nonisolated static func foundationObject(_ value: JSONValue) -> Any {
    switch value {
    case .string(let text): return text
    case .number(let number): return number
    case .bool(let flag): return flag
    case .array(let items): return items.map { foundationObject($0) }
    case .object(let object): return object.mapValues { foundationObject($0) }
    case .null: return NSNull()
    }
  }

  nonisolated static func strings(_ value: JSONValue?) -> [String]? {
    guard case .array(let values)? = value else { return nil }
    var result: [String] = []
    for item in values {
      guard let text = item.stringValue else { return nil }
      result.append(text)
    }
    return result
  }

  private nonisolated static func errorResult(_ message: String) -> String {
    (try? AgentDigest.canonicalJSON(["status": "error", "message": message]))
      ?? "{\"message\":\"error\",\"status\":\"error\"}"
  }

  private nonisolated static func openedResult(_ noteID: String) -> String {
    (try? AgentDigest.canonicalJSON(["status": "opened", "note_id": noteID]))
      ?? "{\"note_id\":\"\(noteID)\",\"status\":\"opened\"}"
  }

  private nonisolated static func answeredResult(_ option: String) -> String {
    (try? AgentDigest.canonicalJSON(["status": "answered", "selected": option]))
      ?? "{\"selected\":\"\(option)\",\"status\":\"answered\"}"
  }

  private nonisolated static func savedResult(noteID: String, revision: Int) -> String {
    let object: [String: Any] = ["status": "saved", "note_id": noteID, "revision": revision]
    return (try? AgentDigest.canonicalJSON(object))
      ?? "{\"note_id\":\"\(noteID)\",\"revision\":\(revision),\"status\":\"saved\"}"
  }

  private nonisolated static func errorOutcome(
    _ callID: String, _ message: String
  ) -> AgentToolOutcome {
    AgentToolOutcome(
      toolCallID: callID, status: "error", resultJSON: errorResult(message),
      noteID: nil, noteRevision: nil)
  }

  private static let networkNotice = "网络中断，恢复后会先核对原请求，不会重复发送"

  // MARK: Commands

  @discardableResult
  func start(_ selection: AgentSelection, request: String) -> Bool {
    guard !isWorking else {
      notice = "已有助手任务正在进行，请先完成或取消"
      return false
    }
    guard let store else {
      notice = "请先登录后再保存笔记"
      return false
    }
    if let current = active, !current.status.isTerminal {
      if current.scope == store.noteScope {
        notice = "请先继续或取消当前助手任务"
        return false
      }
      active = nil
    }
    guard store.cloud != nil, store.processingPolicy.path.kind != .offline else {
      notice = "AI 助手需要联网"
      return false
    }
    guard let scope = store.noteScope else {
      notice = "请先登录后再保存笔记"
      return false
    }
    guard let source = store.sources.first(where: { $0.id == selection.bookID }) else {
      notice = "教材尚未导入"
      return false
    }
    let text = selection.text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard (1...4000).contains(text.unicodeScalars.count) else {
      notice = "请选择 1 到 4000 字的教材原文"
      return false
    }
    let requestText = request.trimmingCharacters(in: .whitespacesAndNewlines)
    guard (1...2000).contains(requestText.unicodeScalars.count) else {
      notice = "请输入 1 到 2000 字的整理要求"
      return false
    }
    let version = Self.sourceVersion(for: source.summary)
    let evidence = Self.evidence(for: selection, sourceVersion: version)
    var context = AgentFrozenContext(
      scope: scope, courseID: selection.courseID, bookID: selection.bookID,
      bookTitle: source.summary.title, sourceVersion: version,
      chapterID: selection.chapterID, page: selection.page,
      printedPage: selection.printedPage, selection: text,
      currentNoteID: nil, capturedAt: .now)
    context.courseResourceID = selection.courseID.flatMap { store.courseResourceID(courseID: $0, bookID: selection.bookID) }
    context.pageType = store.route.rawValue
    let now = Date.now
    var run = AgentRunRecord(
      id: "run-" + UUID().uuidString.lowercased(), version: 0, scope: scope,
      status: .running, request: requestText, context: context, evidence: [evidence],
      steps: [], pendingChoice: nil, cancelRequested: false, finalText: nil,
      failure: nil, committedNoteIDs: [], createdAt: now, updatedAt: now)
    run.privateTransport = true
    notice = nil
    active = run
    begin { [weak self] in
      guard let self else { return }
      switch await self.checkpoint(run) {
      case .saved(let saved):
        self.active = saved
        await self.runLoop(runID: saved.id, scope: saved.scope)
      case .conflict:
        return
      case .failed:
        self.active = nil
        return
      }
    }
    return true
  }

  func choose(_ option: String) {
    guard !isWorking, let store, let current = active, current.scope == store.noteScope,
      let choice = current.pendingChoice,
      choice.options.contains(option) else { return }
    notice = nil
    let runID = current.id
    let scope = current.scope
    let toolCallID = choice.toolCallID
    begin { [weak self] in
      guard let self else { return }
      guard let fresh = try? await self.loadRun(runID, scope: scope) else { return }
      var run = fresh
      guard run.privateTransport == true else { self.notice = "旧版任务已归档，请另开新版对话"; return }
      guard !Task.isCancelled, run.status == .waitingForUser,
        let pending = run.pendingChoice, pending.toolCallID == toolCallID,
        pending.options.contains(option) else { return }
      guard let index = run.steps.firstIndex(where: {
        $0.toolCalls.contains { $0.id == toolCallID }
      }) else { return }
      let choiceResult: String
      let operationID = run.id + "." + toolCallID
      let confirmsCourseRemoval = pending.actionName == "remove_course" && option == pending.options.first
      if let action = pending.actionName, action != "repeated_failure", let json = pending.actionArgumentsJSON, option == pending.options.first {
        do {
          let arguments = try JSONDecoder().decode([String: JSONValue].self, from: Data(json.utf8))
          choiceResult = try await UnifiedAgentTools.performConfirmedMutation(action, args: arguments,
            operationID: operationID, scope: scope, store: store, localOnly: run.privateTransport == true)
        } catch {
          guard scope == store.noteScope else { return }
          if confirmsCourseRemoval, self.courseAnchorWasRemoved(run, store: store) {
            // The durable deletion barrier already closed this run. Cleanup may
            // still need retrying, so neither resurrect the run nor claim success.
            if self.active?.id == run.id, self.active?.scope == scope { self.active = nil }
            self.notice = nil
            store.showToast("课程已移除，课程清理尚未完成，请稍后重试清理。", kind: .warning)
          } else {
            self.notice = error.localizedDescription
          }
          return // Keep an undeleted run's original operation ID available for reconciliation.
        }
      } else { choiceResult = Self.answeredResult(option) }
      guard scope == store.noteScope else { return }
      if confirmsCourseRemoval, self.courseAnchorWasRemoved(run, store: store) {
        // performConfirmedMutation has committed its independent minimal receipt.
        // This course's run/receipts were intentionally retired: no checkpoint,
        // tool transcript or additional model request may recreate them.
        let receipt = try? JSONDecoder().decode([String: JSONValue].self, from: Data(choiceResult.utf8))
        guard let receipt, receipt["status"]?.stringValue == "deleted",
          receipt["operation_id"]?.stringValue == operationID,
          let removedCourseID = receipt["course_id"]?.stringValue,
          store.courseDeletionState.deletedIDs.contains(removedCourseID) else {
          if self.active?.id == run.id, self.active?.scope == scope { self.active = nil }
          store.showToast("课程已移除，删除完成回执暂不可用，请稍后检查清理状态。", kind: .warning)
          return
        }
        if run.ephemeral != true {
          store.assistantHistory.recordCourseRemovalCompletion(operationID: operationID,
            sessionID: run.context.sessionID, messageID: run.context.messageID, scope: scope)
        }
        if self.active?.id == run.id, self.active?.scope == scope { self.active = nil }
        self.notice = nil
        store.showToast("课程已从本机删除", kind: .success)
        return
      }
      if confirmsCourseRemoval {
        // Deleting another course can prune this surviving run and advance its
        // version. Reconcile the real receipt against that fresh row; never
        // replay the mutation or ask the model to describe a local deletion.
        await self.completeSurvivingCourseRemoval(runID: run.id, scope: scope,
          pending: pending, operationID: operationID, result: choiceResult)
        return
      }
      run.steps[index].outcomes.removeAll { $0.toolCallID == toolCallID }
      run.steps[index].outcomes.append(AgentToolOutcome(
        toolCallID: toolCallID, status: "ok", resultJSON: choiceResult,
        noteID: nil, noteRevision: nil))
      run.pendingChoice = nil
      run.status = pending.actionName == "repeated_failure" && option == pending.options.first ? .cancelled : .running
      switch await self.checkpoint(run) {
      case .saved(let saved):
        self.active = saved
        await self.runLoop(runID: saved.id, scope: saved.scope)
      case .conflict, .failed:
        return
      }
    }
  }

  private func completeSurvivingCourseRemoval(runID: String, scope: String,
    pending: AgentPendingChoice, operationID: String, result: String) async {
    guard let store, scope == store.noteScope,
      let receipt = try? JSONDecoder().decode([String: JSONValue].self, from: Data(result.utf8)),
      receipt["status"]?.stringValue == "deleted",
      receipt["operation_id"]?.stringValue == operationID,
      let courseID = receipt["course_id"]?.stringValue,
      store.courseDeletionState.deletedIDs.contains(courseID),
      let tombstones = try? await store.repository.courseDeletionTombstones(scope: scope) else { return }
    for _ in 0..<3 {
      guard !Task.isCancelled, scope == store.noteScope,
        var fresh = try? await loadRun(runID, scope: scope),
        let index = fresh.steps.firstIndex(where: {
          $0.toolCalls.contains { $0.id == pending.toolCallID }
        }) else { return }
      fresh = CourseDeletionPruning.runReferences(fresh, tombstones: tombstones)
      if fresh.status == .completed,
        fresh.steps[index].outcome(pending.toolCallID)?.resultJSON == result {
        active = fresh
        return
      }
      guard fresh.status == .waitingForUser,
        fresh.pendingChoice?.toolCallID == pending.toolCallID,
        fresh.pendingChoice?.actionArgumentsJSON == pending.actionArgumentsJSON else { return }
      fresh.steps[index].outcomes.removeAll { $0.toolCallID == pending.toolCallID }
      fresh.steps[index].outcomes.append(AgentToolOutcome(toolCallID: pending.toolCallID,
        status: "ok", resultJSON: result, noteID: nil, noteRevision: nil))
      fresh.pendingChoice = nil
      fresh.status = .completed
      fresh.finalText = "课程已从本机删除。"
      fresh.finalCitations = []
      fresh.finalSourceLabel = "general"
      fresh.finalCourseIDs = []
      fresh.failure = nil
      switch await checkpoint(fresh) {
      case .saved:
        notice = nil
        store.showToast("课程已从本机删除", kind: .success)
        return
      case .conflict: continue
      case .failed: return
      }
    }
    notice = "课程已删除，完成状态暂未保存；重新打开对话后会按原回执核对。"
  }

  private func courseAnchorWasRemoved(_ run: AgentRunRecord, store: AppStore) -> Bool {
    guard run.scope == store.noteScope, store.courseDeletionState.scope == run.scope,
      let courseID = run.context.courseID else { return false }
    return store.courseDeletionState.deletedIDs.contains(courseID)
  }

  /// Retire every durable turn owned by this conversation, including a turn
  /// that was paused before the app restarted. Other conversations keep running.
  func cancelSession(_ sessionID: String, scope: String) async throws {
    guard let store else { return }
    let current = active.flatMap { $0.scope == scope && $0.context.sessionID == sessionID ? $0 : nil }
    if current?.status.isTerminal == false { cancel() }
    var runs = try await store.repository.loadRuns(scope: scope)
    if let current, !runs.contains(where: { $0.id == current.id }) { runs.append(current) }
    for candidate in runs where candidate.context.sessionID == sessionID && candidate.ephemeral != true {
      var run = candidate
      for attempt in 0..<3 {
        if run.status.isTerminal { break }
        run.cancelRequested = true
        run.status = .cancelled
        do {
          run = try await store.repository.saveRun(run)
          break
        } catch let error as NoteStoreError {
          guard case .runConflict = error, attempt < 2,
            let refreshed = try await store.repository.loadRun(run.id, scope: scope) else { throw error }
          run = refreshed
        }
      }
      if active?.id == run.id, active?.scope == scope, store.noteScope == scope { active = run }
      if run.cancelRequested, run.cancellationReconciled != true { await reconcileCancellation(run) }
    }
  }

  func cancel() {
    guard let run = active, !run.status.isTerminal, run.scope == store?.noteScope else {
      return
    }
    if run.ephemeral == true {
      invalidateDrive()
      var cancelled = run; cancelled.cancelRequested = true; cancelled.status = .cancelled
      active = cancelled
      Task { await self.reconcileCancellation(cancelled) }
      return
    }
    cancelInFlight = true
    invalidateDrive()
    Task { @MainActor [weak self] in
      guard let self else { return }
      defer { self.cancelInFlight = false }
      guard let store = self.store else { return }
      var current = run
      var saved: AgentRunRecord?
      for attempt in 0..<3 {
        current.cancelRequested = true
        current.status = .cancelled
        do {
          saved = try await store.repository.saveRun(current)
          break
        } catch let error as NoteStoreError {
          guard case .runConflict = error, attempt < 2 else {
            self.notice = error.localizedDescription
            break
          }
          guard let reloaded = try? await self.loadRun(run.id, scope: run.scope)
          else { break }
          // A concurrent terminal transition wins; never turn a completed run into cancelled.
          if reloaded.status.isTerminal, !reloaded.cancelRequested {
            if self.active?.id == run.id, reloaded.scope == self.store?.noteScope {
              self.active = reloaded
            }
            return
          }
          current = reloaded
        } catch {
          self.notice = error.localizedDescription
          break
        }
      }
      if let saved, self.active?.id == run.id, saved.scope == self.store?.noteScope {
        self.active = saved
        self.notice = nil
      }
      if let saved { await self.reconcileCancellation(saved) }
    }
  }

  /// A cancelled write may already have committed remotely. Only read its receipt.
  private func reconcileCancellation(_ original: AgentRunRecord) async {
    guard let store, original.scope == store.noteScope, let cloud = store.cloud else { return }
    var run = original
    guard run.privateTransport == true else { return }
    do {
      try await cloud.cancelAssistantRun(run.id)
      if run.privateTransport == true {
        run.cancellationReconciled = true
        _ = try? await store.repository.saveRun(run)
        return
      }
      guard let session = run.context.sessionID else { return }
      var missing = false
      var receipts: [JSONValue] = []
      for index in run.steps.indices {
        for call in run.steps[index].toolCalls {
          var outcome = run.steps[index].outcome(call.id)
          if outcome == nil, ["create_flashcards", "create_review_task", "update_study_plan", "remove_course"].contains(call.name) {
            let receipt: [String: JSONValue] = try await cloud.request("/api/assistant/operations/\(run.id).\(call.id)")
            guard run.scope == store.noteScope else { return }
            if receipt["state"]?.stringValue == "completed" {
              let data = try JSONEncoder().encode(receipt)
              let result = AgentToolOutcome(toolCallID: call.id, status: "ok", resultJSON: String(decoding: data, as: UTF8.self))
              outcome = result
              run.steps[index].outcomes.append(result)
              run.events = (run.events ?? []) + [AgentRunEvent(kind: "completed", message: UnifiedAgentTools.label(call.name) + "已核对完成", toolCallID: call.id)]
              if call.name == "remove_course", let choice = run.pendingChoice,
                let json = choice.actionArgumentsJSON,
                let args = try? JSONDecoder().decode([String: JSONValue].self, from: Data(json.utf8)),
                let course = args["course_id"]?.stringValue, case .array(let ids)? = args["frozen_resource_ids"] {
                try await store.clearLocalCourseData(course, resourceIDs: Set(ids.compactMap(\.stringValue)))
                store.cacheAcknowledgedCourseRemoval(course)
              }
              await store.reloadAgentLearningObjects()
            } else { missing = true }
          }
          if let outcome {
            let object = try? JSONDecoder().decode([String: JSONValue].self, from: Data(outcome.resultJSON.utf8))
            var receipt: [String: JSONValue] = ["step_index": .number(Double(run.steps[index].index)),
              "tool_call_id": .string(call.id), "content": .string(outcome.resultJSON),
              "complete": object?["complete"] ?? .bool(true)]
            if case .number(let offset)? = object?["next_offset"] { receipt["continuation"] = .string(String(Int(offset))) }
            receipts.append(.object(receipt))
          }
        }
      }
      if !receipts.isEmpty {
        let _: [String: JSONValue] = try await cloud.request("/api/assistant/conversations/\(session)/receipts", method: "POST",
          body: ["run_id": .string(run.id), "ephemeral": .bool(run.ephemeral == true), "tool_results": .array(receipts)])
      }
      run.cancellationReconciled = !missing
      if missing { run.failure = "任务已取消；未收到回执的操作仍待核对" }
      guard store.noteScope == run.scope else { return }
      if run.ephemeral != true {
        run = try await store.repository.saveRun(run)
        store.assistantHistory.recordRun(run)
      }
      if active?.id == run.id { active = run }
    } catch {
      if active?.id == run.id { notice = "任务已取消，联网后将核对操作结果" }
    }
  }

  /// Drop in-memory state when the account scope goes away. Persisted runs stay.
  func reset() {
    if let run = active, run.ephemeral == true, let repository = store?.repository {
      let paths = (run.events ?? []).compactMap(\.attachmentPath) + (run.attachments ?? []).flatMap { [$0.path, $0.thumbnailPath, $0.textPath].compactMap { $0 } }
      Task {
        guard let notes = try? await repository.loadStoredNotes(scope: run.scope) else { return }
        let preserved = Set(notes.flatMap { $0.note.references ?? [] }.compactMap(\.attachmentPath))
        for path in paths where !preserved.contains(path) { try? FileManager.default.removeItem(at: repository.attachmentURL(path)) }
      }
    }
    store?.assistantState.learningObjects = []
    store?.learningState.agentFocusCardID = nil
    store?.learningState.agentFocusMistakeID = nil
    store?.learningState.agentFocusPlanDate = nil
    invalidateDrive()
    active = nil
    notice = nil
    cancelInFlight = false
    registeredSessionIDs = []
  }

  func resume() {
    guard !isWorking, !cancelInFlight, let store, let scope = store.noteScope else { return }
    begin { [weak self] in
      guard let self, let store = self.store, store.noteScope == scope else { return }
      var runs = (try? await store.repository.loadRuns(scope: scope)) ?? []
      guard !Task.isCancelled else { return }
      var removed = (try? await store.repository.loadAssistantHistory(scope: scope))?.removedIDs ?? []
      if store.assistantHistory.scope == scope { removed.formUnion(store.assistantHistory.archive.removedIDs) }
      for sessionID in Set(runs.compactMap { run in
        run.context.sessionID.flatMap { removed.contains($0) && !run.status.isTerminal ? $0 : nil }
      }) {
        do { try await self.cancelSession(sessionID, scope: scope) }
        catch { self.notice = "已删除对话的任务尚未取消，不继续执行"; return }
      }
      if !removed.isEmpty { runs = (try? await store.repository.loadRuns(scope: scope)) ?? [] }
      guard !Task.isCancelled, store.noteScope == scope else { return }
      for index in runs.indices where runs[index].privateTransport != true && runs[index].status != .completed && runs[index].legacyArchived != true {
        var archived = runs[index]
        archived.legacyArchived = true
        archived.status = .cancelled
        archived.cancellationReconciled = true
        archived.failure = "旧版未完成任务已归档，请另开新版对话。原记录保留，不再查询或执行。"
        archived.events = (archived.events ?? []) + [AgentRunEvent(kind: "archived", message: archived.failure!)]
        do {
          archived = try await store.repository.saveRun(archived)
          runs[index] = archived
          store.assistantHistory.recordRun(archived)
          self.notice = archived.failure
          if self.active?.id == archived.id { self.active = archived }
        } catch { self.notice = "旧任务归档尚未保存，不执行后续操作"; return }
      }
      for cancelled in runs where cancelled.privateTransport == true && cancelled.context.sessionID != nil && cancelled.cancelRequested && cancelled.cancellationReconciled != true {
        await self.reconcileCancellation(cancelled)
      }
      if let run = runs.first(where: {
        $0.privateTransport == true && ($0.status == .running || $0.status == .waitingForUser)
      }) {
        self.active = run
        self.notice = nil
        if run.status == .running {
          guard store.cloud != nil else {
            self.notice = "AI 助手需要联网"
            return
          }
          await self.runLoop(runID: run.id, scope: scope)
        }
        return
      }
      if let current = self.active, current.scope == scope, !current.status.isTerminal {
        self.active = runs.first { $0.id == current.id }
      } else if self.active?.scope != scope {
        self.active = nil
      }
    }
  }

  func suspend() {
    // The durable checkpoint is already on disk; dropping the task is enough.
    // Clear isWorking too so a quick foreground resume is not ignored.
    invalidateDrive()
  }

  func dismiss() {
    guard let run = active, run.status.isTerminal else { return }
    active = nil
  }

  // MARK: Drive

  private enum Flow { case next, redo, stop }
  private enum Checkpoint { case saved(AgentRunRecord), conflict, failed }

  private func begin(_ body: @escaping @MainActor @Sendable () async -> Void) {
    guard !isWorking else { return }
    isWorking = true
    driveGeneration += 1
    let generation = driveGeneration
    driveGenerationAtStart = generation
    driveTask = Task { @MainActor [weak self] in
      await body()
      guard let self, self.driveGeneration == generation else { return }
      self.isWorking = false
      self.driveTask = nil
    }
  }

  private func invalidateDrive() {
    driveGeneration += 1
    driveTask?.cancel()
    driveTask = nil
    isWorking = false
  }

  /// Publish a tool result only while this drive still owns the current scope.
  private func publish(_ run: AgentRunRecord) {
    guard run.scope == store?.noteScope, driveGeneration == driveGenerationAtStart,
      !Task.isCancelled
    else { return }
    active = run
  }

  private func checkpoint(_ run: AgentRunRecord) async -> Checkpoint {
    guard let store else { return .failed }
    let generation = driveGenerationAtStart
    if run.ephemeral == true {
      guard run.scope == store.noteScope, !Task.isCancelled else { return .conflict }
      active = run
      return .saved(run)
    }
    do {
      let saved = try await store.repository.saveRun(run)
      guard driveGeneration == generation, !Task.isCancelled, saved.scope == store.noteScope
      else { return .conflict }
      active = saved
      store.assistantHistory.recordRun(saved)
      return .saved(saved)
    } catch let error as NoteStoreError {
      if case .runConflict = error {
        // Another owner advanced the row; reload and re-drive from that state.
        let reloaded = try? await loadRun(run.id, scope: run.scope)
        if driveGeneration == generation, !Task.isCancelled,
          let reloaded, reloaded.scope == store.noteScope {
          active = reloaded
        }
        return .conflict
      }
      notice = error.localizedDescription
      return .failed
    } catch {
      notice = error.localizedDescription
      return .failed
    }
  }

  private func runLoop(runID: String, scope: String) async {
    guard let store else { return }
    while !Task.isCancelled {
      guard store.noteScope == scope else { return }
      guard let loaded = try? await loadRun(runID, scope: scope) else { return }
      guard !Task.isCancelled else { return }
      var run = loaded
      active = run

      if run.cancelRequested {
        run.status = .cancelled
        switch await checkpoint(run) {
        case .saved(let saved):
          active = saved
          if let cloud = store.cloud { try? await cloud.cancelAssistantRun(saved.id) }
        case .conflict:
          continue
        case .failed:
          return
        }
        return
      }
      if run.status.isTerminal || run.status == .waitingForUser { return }

      guard let last = run.steps.last else {
        run.steps.append(AgentStep(index: 0, id: "\(run.id).s0", state: .pending))
        switch await checkpoint(run) {
        case .saved(let saved): run = saved; active = saved
        case .conflict: continue
        case .failed: return
        }
        continue
      }
      let stepIndex = last.index
      let arrayIndex = run.steps.count - 1

      switch last.state {
      case .pending:
        guard let cloud = store.cloud else {
          notice = "AI 助手需要联网"
          return
        }
        run.steps[arrayIndex].state = .posted
        switch await checkpoint(run) {
        case .saved(let saved): run = saved
        case .conflict: continue
        case .failed: return
        }
        guard !Task.isCancelled, !run.cancelRequested else { return }
        let body = Self.turnRequest(for: run, stepIndex: stepIndex)
        do {
          let response = try await cloud.assistantTurn(body)
          guard !Task.isCancelled, store.noteScope == scope else { return }
          switch await handle(response, run: &run, stepIndex: stepIndex) {
          case .next, .redo: continue
          case .stop: return
          }
        } catch is CancellationError {
          return
        } catch let error as CloudError where error.transportFailure {
          notice = Self.networkNotice
          return
        } catch let error as CloudError {
          run.steps[arrayIndex].state = .failed
          run.steps[arrayIndex].failure = error.localizedDescription
          run.status = .failed
          run.failure = error.localizedDescription
          switch await checkpoint(run) {
          case .conflict: continue
          case .saved, .failed: return
          }
        } catch {
          notice = error.localizedDescription
          return
        }

      case .posted:
        guard let cloud = store.cloud else {
          notice = "AI 助手需要联网"
          return
        }
        do {
          let response = try await cloud.assistantStep(runID: run.id, stepID: last.id)
          guard !Task.isCancelled, store.noteScope == scope else { return }
          switch await handle(response, run: &run, stepIndex: stepIndex) {
          case .next, .redo: continue
          case .stop: return
          }
        } catch let error as CloudError where error.status == 404 {
          // The server never claimed the step, so the POST never reached the model.
          run.steps[arrayIndex].state = .pending
          switch await checkpoint(run) {
          case .saved(let saved): run = saved
          case .conflict: continue
          case .failed: return
          }
          continue
        } catch is CancellationError {
          return
        } catch let error as CloudError where error.transportFailure {
          notice = Self.networkNotice
          return
        } catch let error as CloudError {
          run.steps[arrayIndex].state = .failed
          run.steps[arrayIndex].failure = error.localizedDescription
          run.status = .failed
          run.failure = error.localizedDescription
          switch await checkpoint(run) {
          case .conflict: continue
          case .saved, .failed: return
          }
        } catch {
          notice = error.localizedDescription
          return
        }

      case .completed:
        if last.hasPendingTools {
          switch await executeTools(run: &run, stepIndex: stepIndex) {
          case .next, .redo: continue
          case .stop: return
          }
        }
        if last.toolCalls.isEmpty {
          Self.completeAnswer(last.content ?? "", run: &run)
          run.status = .completed
          switch await checkpoint(run) {
          case .saved(let saved): active = saved
          case .conflict: continue
          case .failed: return
          }
          return
        }
        let next = run.steps.count
        run.steps.append(AgentStep(index: next, id: "\(run.id).s\(next)", state: .pending))
        switch await checkpoint(run) {
        case .saved(let saved): run = saved; active = saved
        case .conflict: continue
        case .failed: return
        }
        continue

      case .uncertain, .failed:
        return
      }
    }
  }

  private func handle(
    _ first: AssistantTurnResponseDTO, run: inout AgentRunRecord, stepIndex: Int
  ) async -> Flow {
    guard let store else { return .stop }
    guard let index = run.steps.firstIndex(where: { $0.index == stepIndex }) else {
      return .redo
    }
    var response = first
    if let sessionID = run.context.sessionID, run.ephemeral != true, first.state != "refused" { registeredSessionIDs.insert(sessionID) }
    var waited: Duration = .zero
    while !Task.isCancelled {
      if run.cancelRequested { return .redo }
      if let tokens = response.context_tokens {
        run.contextTokens = tokens
        run.contextEstimated = response.context_estimated
        if tokens >= 1_000_000 { run.capacityState = "archived" }
        else if tokens >= 900_000, run.capacityState != "archived" { run.capacityState = "finishing" }
      }
      if let state = response.capacity_state, state != "open" { run.capacityState = state }
      if run.capacityState == "archived" {
        let calls = response.message?.tool_calls ?? []
        run.steps[index].content = response.message?.content
        run.steps[index].state = response.state == "completed" ? .completed : .failed
        run.steps[index].toolCalls = calls.map { call in
          let json = (try? AgentDigest.canonicalJSON(Self.foundationObject(.object(call.arguments)))) ?? "{}"
          return AgentToolCall(id: call.id, name: call.name, argumentsJSON: json, argumentsHash: AgentDigest.sha256(call.name + "\n" + json))
        }
        run.steps[index].outcomes = calls.map { call in
          AgentToolOutcome(toolCallID: call.id, status: "skipped", resultJSON: Self.errorResult("未执行：上下文已达到上限"))
        }
        if response.state == "completed", calls.isEmpty {
          Self.completeAnswer(response.message?.content ?? "", run: &run)
          run.status = .completed
        } else {
          run.status = .failed
          run.failure = "此对话已达到上下文上限，无法继续对话。请新建对话。"
        }
        switch await checkpoint(run) {
        case .saved(let saved): run = saved; return .stop
        case .conflict: return .redo
        case .failed: return .stop
        }
      }
      // A refused budget ends the run whatever the wire state says, including
      // the response of a retryable re-POST.
      if response.refused != nil {
        if response.refused == "cancelled" {
          run.status = .cancelled
        } else {
          run.status = .failed
          run.failure = response.error ?? "API 错误：\(response.refused ?? "未知")"
        }
        switch await checkpoint(run) {
        case .conflict: return .redo
        case .saved, .failed: return .stop
        }
      }
      switch response.state {
      case "completed":
        let calls = (response.message?.tool_calls ?? []).map { call -> AgentToolCall in
          let json = (try? AgentDigest.canonicalJSON(
            Self.foundationObject(.object(call.arguments)))) ?? "{}"
          return AgentToolCall(
            id: call.id, name: call.name, argumentsJSON: json,
            argumentsHash: AgentDigest.sha256(call.name + "\n" + json))
        }
        run.steps[index].state = .completed
        run.steps[index].content = response.message?.content ?? ""
        run.steps[index].toolCalls = calls
        switch await checkpoint(run) {
        case .saved(let saved):
          run = saved
          if run.privateTransport == true { try? await store.cloud?.acknowledgeAssistantStep(runID: run.id, stepID: run.steps[index].id) }
          return .next
        case .conflict: return .redo
        case .failed: return .stop
        }

      case "submitting":
        guard waited < submittingWindow else {
          notice = "模型仍在处理，请稍后继续"
          return .stop
        }
        do {
          try await Task.sleep(for: pollInterval > .zero ? pollInterval : .milliseconds(1))
        } catch { return .stop }
        waited += pollInterval > .zero ? pollInterval : .milliseconds(1)
        guard !Task.isCancelled else { return .stop }
        guard let cloud = store.cloud else {
          notice = "AI 助手需要联网"
          return .stop
        }
        do {
          response = try await cloud.assistantStep(
            runID: run.id, stepID: run.steps[index].id)
        } catch let error as CloudError where error.status == 404 {
          run.steps[index].state = .pending
          switch await checkpoint(run) {
          case .saved, .conflict: return .redo
          case .failed: return .stop
          }
        } catch is CancellationError {
          return .stop
        } catch let error as CloudError where error.transportFailure {
          notice = Self.networkNotice
          return .stop
        } catch let error as CloudError {
          notice = error.localizedDescription
          return .stop
        } catch {
          notice = error.localizedDescription
          return .stop
        }

      case "retryable":
        let delay = min(max(response.retry_after ?? 2, 0.1), 30)
        do {
          try await Task.sleep(for: .seconds(delay))
        } catch { return .stop }
        guard !Task.isCancelled, !run.cancelRequested else { return .stop }
        guard let cloud = store.cloud else {
          notice = "AI 助手需要联网"
          return .stop
        }
        do {
          response = try await cloud.assistantTurn(
            Self.turnRequest(for: run, stepIndex: stepIndex))
        } catch is CancellationError {
          return .stop
        } catch let error as CloudError where error.transportFailure {
          notice = Self.networkNotice
          return .stop
        } catch let error as CloudError {
          run.steps[index].state = .failed
          run.steps[index].failure = error.localizedDescription
          run.status = .failed
          run.failure = error.localizedDescription
          switch await checkpoint(run) {
          case .conflict: return .redo
          case .saved, .failed: return .stop
          }
        } catch {
          notice = error.localizedDescription
          return .stop
        }

      case "uncertain":
        run.steps[index].state = .uncertain
        run.status = .uncertain
        run.failure = "模型请求结果未知，已停止，不会自动重发"
        switch await checkpoint(run) {
        case .conflict: return .redo
        case .saved, .failed: return .stop
        }

      case "failed":
        run.steps[index].state = .failed
        run.steps[index].failure = response.error
        run.status = .failed
        run.failure = response.error ?? "模型请求失败"
        switch await checkpoint(run) {
        case .conflict: return .redo
        case .saved, .failed: return .stop
        }

      case "refused":
        if response.refused == "cancelled" {
          run.status = .cancelled
        } else {
          run.status = .failed
          run.failure = response.error ?? "API 错误：\(response.refused ?? "未知")"
        }
        switch await checkpoint(run) {
        case .conflict: return .redo
        case .saved, .failed: return .stop
        }

      default:
        run.steps[index].state = .failed
        run.status = .failed
        run.failure = "服务返回了未知的助手状态"
        switch await checkpoint(run) {
        case .conflict: return .redo
        case .saved, .failed: return .stop
        }
      }
    }
    return .stop
  }

  private func executeTools(
    run: inout AgentRunRecord, stepIndex: Int
  ) async -> Flow {
    guard let store else { return .stop }
    guard let initial = run.steps.first(where: { $0.index == stepIndex }) else {
      return .redo
    }
    for callID in initial.toolCalls.map(\.id) {
      guard !Task.isCancelled else { return .stop }
      guard let fresh = try? await loadRun(run.id, scope: run.scope) else {
        return .stop
      }
      guard !Task.isCancelled else { return .stop }
      run = fresh
      publish(run)
      guard run.scope == store.noteScope else { return .stop }
      if run.cancelRequested { return .redo }
      if run.status.isTerminal { return .stop }
      guard let index = run.steps.firstIndex(where: { $0.index == stepIndex }),
        let call = run.steps[index].toolCalls.first(where: { $0.id == callID }) else { continue }
      if run.steps[index].outcome(call.id) != nil { continue }
      let flow: Flow
      switch call.name {
      case "save_note":
        flow = await saveNoteTool(call: call, run: &run, stepIndex: stepIndex)
      case "open_note":
        flow = await openNoteTool(call: call, run: &run, stepIndex: stepIndex)
      case "ask_user":
        flow = await askUserTool(call: call, run: &run, stepIndex: stepIndex)
      case "delete_note", "remove_course":
        do {
          run.pendingChoice = try await UnifiedAgentTools.prepareMutation(call, run: run, store: store)
          run.status = .waitingForUser
          switch await checkpoint(run) {
          case .saved(let saved): run = saved; flow = .stop
          case .conflict: flow = .redo
          case .failed: flow = .stop
          }
        } catch {
          flow = await appendOutcome(Self.errorOutcome(call.id, error.localizedDescription), run: &run, stepIndex: stepIndex)
        }
      default:
        if UnifiedAgentTools.names.contains(call.name) {
          run.events = (run.events ?? []) + [AgentRunEvent(kind: "reading", message: UnifiedAgentTools.label(call.name), toolCallID: call.id)]
          switch await checkpoint(run) {
          case .saved(let saved): run = saved
          case .conflict: return .redo
          case .failed: return .stop
          }
          do {
            let result = try await UnifiedAgentTools.execute(call, run: &run, store: store)
            run.events = (run.events ?? []) + [AgentRunEvent(kind: "completed", message: "已完成：" + UnifiedAgentTools.label(call.name), toolCallID: call.id)]
            flow = await appendOutcome(AgentToolOutcome(toolCallID: call.id, status: "ok", resultJSON: result, noteID: nil, noteRevision: nil), run: &run, stepIndex: stepIndex)
          } catch let error as CloudError where error.transportFailure {
            notice = Self.networkNotice
            return .stop
          } catch is CancellationError {
            return .stop
          } catch {
            flow = await appendOutcome(Self.errorOutcome(call.id, error.localizedDescription), run: &run, stepIndex: stepIndex)
          }
        } else {
          flow = await appendOutcome(Self.errorOutcome(call.id, "未知工具"), run: &run, stepIndex: stepIndex)
        }
      }
      switch flow {
      case .stop: return .stop
      case .redo: return .redo
      case .next: break
      }
    }
    return .next
  }

  private func appendOutcome(
    _ outcome: AgentToolOutcome, run: inout AgentRunRecord, stepIndex: Int
  ) async -> Flow {
    guard let index = run.steps.firstIndex(where: { $0.index == stepIndex }) else {
      return .redo
    }
    run.steps[index].outcomes.removeAll { $0.toolCallID == outcome.toolCallID }
    run.steps[index].outcomes.append(outcome)
    if outcome.status == "error", let call = run.steps[index].toolCalls.first(where: { $0.id == outcome.toolCallID }) {
      let repeated = run.steps.flatMap { step in step.toolCalls.filter { prior in
        prior.name == call.name && prior.argumentsHash == call.argumentsHash && step.outcome(prior.id)?.status == "error"
      } }.count
      if repeated >= 3 {
        run.status = .waitingForUser
        run.failure = "相同操作连续失败，未取得新信息。" + outcome.resultJSON
        run.pendingChoice = AgentPendingChoice(toolCallID: call.id, question: "相同操作已连续失败：" + outcome.resultJSON,
          options: ["停止任务", "继续尝试"], selected: nil, actionName: "repeated_failure")
        switch await checkpoint(run) {
        case .saved(let saved): run = saved; return .stop
        case .conflict: return .redo
        case .failed: return .stop
        }
      }
    }
    switch await checkpoint(run) {
    case .saved(let saved): run = saved; return .next
    case .conflict: return .redo
    case .failed: return .stop
    }
  }

  private func saveNoteTool(
    call: AgentToolCall, run: inout AgentRunRecord, stepIndex: Int
  ) async -> Flow {
    guard let store else { return .stop }
    if !run.context.bookID.isEmpty, run.context.courseID == nil {
      return await appendOutcome(Self.errorOutcome(call.id, "无指定课程，无法进行笔记撰写"), run: &run, stepIndex: stepIndex)
    }
    // A follow-up may cite a passage verified during a previous message in this chat.
    if let sessionID = run.context.sessionID, run.ephemeral != true,
      let requested = Self.strings(Self.arguments(call)["evidence_ids"]),
      let previous = store.assistantHistory.sessions.first(where: { $0.id == sessionID })?.runs {
      let scope = run.scope
      for id in requested where run.evidence(id) == nil {
        if let verified = previous.reversed().lazy.filter({ $0.scope == scope }).compactMap({ $0.evidence(id) }).first {
          run.evidence.append(verified)
        }
      }
    }
    switch Self.validateSaveNote(Self.arguments(call), run: run) {
    case .failure(let error):
      return await appendOutcome(
        Self.errorOutcome(call.id, error.message), run: &run, stepIndex: stepIndex)

    case .success(let args):
      let references = args.evidenceIDs.compactMap { id in
        run.evidence(id).map(NoteReference.init)
      }
      let raw = Self.arguments(call)
      let targetCourseID = raw["course_id"]?.stringValue ?? run.context.courseID
      let targetBookID = raw["book_id"]?.stringValue ?? run.context.bookID
      var anchor: NoteAnchor?
      if !targetBookID.isEmpty {
        guard let targetCourseID, let course = store.courses.first(where: { $0.id == targetCourseID }),
          course.resourceIDs.contains(targetBookID), let resourceID = store.courseResourceID(courseID: targetCourseID, bookID: targetBookID),
          raw["course_resource_id"]?.stringValue == nil || raw["course_resource_id"]?.stringValue == resourceID else {
          return await appendOutcome(Self.errorOutcome(call.id, "无指定课程，无法进行笔记撰写"), run: &run, stepIndex: stepIndex)
        }
        anchor = NoteAnchor(courseID: targetCourseID, courseResourceID: resourceID, bookID: targetBookID,
          chapterID: raw["chapter_id"]?.stringValue ?? run.context.chapterID,
          page: UnifiedAgentTools.integer(raw, "page", default: run.context.page), printedPage: run.context.printedPage,
          quote: run.context.selection.isEmpty ? nil : run.context.selection)
      }
      let note = StudyNote(
        id: "note-" + String(AgentDigest.sha256(run.id + call.id).prefix(32)), kind: .text,
        title: args.title, body: args.body,
        anchor: anchor,
        references: references.isEmpty ? nil : references,
        provenance: NoteProvenance(
          runID: run.id, toolCallID: call.id, sourceLabel: args.sourceLabel))
      let result = Self.savedResult(noteID: note.id, revision: 1)
      do {
        if run.ephemeral == true {
          let committed = try await store.repository.commitEphemeralToolNote(scope: run.scope, runID: run.id,
            toolCallID: call.id, argumentsHash: call.argumentsHash, note: note)
          if let row = try await store.repository.loadStoredNote(committed.noteID, scope: run.scope) { store.applyCommitted([row], scope: run.scope) }
          if !run.committedNoteIDs.contains(committed.noteID) { run.committedNoteIDs.append(committed.noteID) }
          return await appendOutcome(AgentToolOutcome(toolCallID: call.id, status: "ok", resultJSON: Self.savedResult(noteID: committed.noteID, revision: committed.revision), noteID: committed.noteID, noteRevision: committed.revision), run: &run, stepIndex: stepIndex)
        }
        let commit = try await store.repository.commitToolNote(
          run: run, toolCallID: call.id, argumentsHash: call.argumentsHash,
          note: note, result: result, verifiedCourseResourceID: anchor?.courseResourceID)
        if let afterCommitToolNote { await afterCommitToolNote() }
        if let saved = commit.run {
          run = saved
          publish(saved)
        } else {
          // A replay can return only the receipt; record the same outcome locally.
          let outcome = AgentToolOutcome(
            toolCallID: call.id, status: "ok",
            resultJSON: Self.savedResult(noteID: commit.noteID, revision: commit.revision),
            noteID: commit.noteID, noteRevision: commit.revision)
          switch await appendOutcome(outcome, run: &run, stepIndex: stepIndex) {
          case .stop: return .stop
          case .redo: return .redo
          case .next: break
          }
        }
        do {
          if let stored = try await store.repository.loadStoredNote(
            commit.noteID, scope: run.scope) {
            store.applyCommitted([stored], scope: run.scope)
          }
        } catch {
          // The row is already durable; the next reloadNotes corrects memory.
        }
        return Task.isCancelled ? .stop : .next
      } catch let error as NoteStoreError {
        switch error {
        case .runClosed:
          return .stop
        case .runConflict:
          return .redo
        case .receiptConflict, .untrustedReference, .revisionConflict, .scopeMismatch:
          return await appendOutcome(
            Self.errorOutcome(call.id, error.localizedDescription),
            run: &run, stepIndex: stepIndex)
        }
      } catch {
        notice = error.localizedDescription
        return .stop
      }
    }
  }

  private func openNoteTool(
    call: AgentToolCall, run: inout AgentRunRecord, stepIndex: Int
  ) async -> Flow {
    guard let store else { return .stop }
    let args = Self.arguments(call)
    guard let noteID = args["note_id"]?.stringValue,
      (try? await store.repository.loadStoredNote(noteID, scope: run.scope)) != nil else {
      return await appendOutcome(
        Self.errorOutcome(call.id, "笔记尚未保存或不属于当前任务"),
        run: &run, stepIndex: stepIndex)
    }
    guard !Task.isCancelled else { return .stop }
    let outcome = AgentToolOutcome(
      toolCallID: call.id, status: "ok", resultJSON: Self.openedResult(noteID),
      noteID: noteID, noteRevision: nil)
    switch await appendOutcome(outcome, run: &run, stepIndex: stepIndex) {
    case .stop: return .stop
    case .redo: return .redo
    case .next: break
    }
    // Persist the fact first; opening is only a navigation side effect.
    if run.scope == store.noteScope { store.openAgentNote(noteID) }
    return .next
  }

  private func askUserTool(
    call: AgentToolCall, run: inout AgentRunRecord, stepIndex: Int
  ) async -> Flow {
    let args = Self.arguments(call)
    guard let rawQuestion = args["question"]?.stringValue,
      let rawOptions = Self.strings(args["options"]) else {
      return await appendOutcome(
        Self.errorOutcome(call.id, "提问内容无效"), run: &run, stepIndex: stepIndex)
    }
    let question = rawQuestion.trimmingCharacters(in: .whitespacesAndNewlines)
    guard (1...200).contains(question.count) else {
      return await appendOutcome(
        Self.errorOutcome(call.id, "提问内容无效"), run: &run, stepIndex: stepIndex)
    }
    let options = rawOptions.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
    guard (2...4).contains(options.count), Set(options).count == options.count,
      options.allSatisfy({ (1...60).contains($0.count) }) else {
      return await appendOutcome(
        Self.errorOutcome(call.id, "提问选项需为 2 到 4 个且不能重复"),
        run: &run, stepIndex: stepIndex)
    }
    run.pendingChoice = AgentPendingChoice(
      toolCallID: call.id, question: question, options: options, selected: nil)
    run.status = .waitingForUser
    switch await checkpoint(run) {
    case .saved(let saved): run = saved; active = saved; return .stop
    case .conflict: return .redo
    case .failed: return .stop
    }
  }
}

extension AppStore {
  func startSelectionNote(_ selection: AgentSelection, request: String) {
    guard cloud != nil, processingPolicy.path.kind != .offline else { showToast("AI 助手需要联网", kind: .warning); return }
    guard selection.courseID != nil else {
      showToast("无指定课程，无法进行笔记撰写", kind: .warning)
      return
    }
    var context = assistantSessionContext
    context.courseID = selection.courseID
    context.bookID = selection.bookID
    context.bookIDs = [selection.bookID]
    context.chapterID = selection.chapterID
    context.page = selection.page
    context.printedPage = selection.printedPage
    context.selection = selection.text
    context.courseResourceID = selection.courseID.flatMap { courseResourceID(courseID: $0, bookID: selection.bookID) }
    if let source = sources.first(where: { $0.id == selection.bookID }) {
      context.bookTitle = source.summary.title
      context.sourceVersion = AgentRuntime.sourceVersion(for: source.summary)
    }
    let epoch = cloudState.epoch
    globalAssistantOpen = true
    Task { @MainActor in
      await assistantHistory.load(scope: assistantHistoryScope)
      do { try await syncAssistantHistory() } catch { showToast(error.localizedDescription, kind: .warning); return }
      guard epoch == cloudState.epoch else { return }
      assistantHistory.submit(request, context: context) { [weak self] snapshot, history, question in
        guard let self else { throw CancellationError() }
        return try await self.answerHistorySession(context: snapshot, history: history, question: question)
      }
    }
  }

  func openAgentNote(_ noteID: String) {
    guard let note = notes.first(where: { $0.id == noteID }) else { return }
    if let courseID = note.anchor?.courseID, courses.contains(where: { $0.id == courseID }) {
      selectCourse(courseID)
    }
    if let bookID = note.anchor?.bookID, sources.contains(where: { $0.id == bookID }),
      selectedBookID != bookID {
      selectSource(bookID)
    }
    if let chapterID = note.anchor?.chapterID {
      selectedChapterID = chapterID
      selectedLessonID = lessons.first(where: { $0.chapter_id == chapterID })?.id
    }
    noteState.focusNoteID = noteID
    go(.notes)
  }
}
