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

  init(store: AppStore? = nil) { self.store = store }

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
    let digest = AgentDigest.sha256("\(sourceVersion)|\(selection.page)|\(quote)")
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
        results.append(AssistantToolResultDTO(
          step_index: step.index, tool_call_id: call.id,
          content: String(outcome.resultJSON.prefix(4000))))
      }
    }
    let context = AssistantTurnContextDTO(
      book_id: run.context.bookID, book_title: run.context.bookTitle,
      course_id: run.context.courseID, source_version: run.context.sourceVersion,
      chapter_id: run.context.chapterID, page: run.context.page,
      printed_page: run.context.printedPage, selection: run.context.selection,
      current_note_id: run.context.currentNoteID)
    let evidence = run.evidence.map {
      AssistantTurnEvidenceDTO(
        evidence_id: $0.id, page: $0.page, printed_page: $0.printedPage, quote: $0.quote)
    }
    return AssistantTurnRequestDTO(
      contract_version: 1, run_id: run.id, step_id: "\(run.id).s\(stepIndex)",
      step_index: stepIndex, request: run.request, context: context, evidence: evidence,
      tools: ["save_note", "open_note", "ask_user"], tool_results: results)
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

  private nonisolated static func strings(_ value: JSONValue?) -> [String]? {
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
    guard store.cloud != nil else {
      notice = "当前未连接学习服务，无法使用助手整理笔记"
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
    let context = AgentFrozenContext(
      scope: scope, courseID: selection.courseID, bookID: selection.bookID,
      bookTitle: source.summary.title, sourceVersion: version,
      chapterID: selection.chapterID, page: selection.page,
      printedPage: selection.printedPage, selection: text,
      currentNoteID: nil, capturedAt: .now)
    let now = Date.now
    let run = AgentRunRecord(
      id: "run-" + UUID().uuidString.lowercased(), version: 0, scope: scope,
      status: .running, request: requestText, context: context, evidence: [evidence],
      steps: [], pendingChoice: nil, cancelRequested: false, finalText: nil,
      failure: nil, committedNoteIDs: [], createdAt: now, updatedAt: now)
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
      guard let fresh = try? await store.repository.loadRun(runID, scope: scope) else { return }
      var run = fresh
      guard !Task.isCancelled, run.status == .waitingForUser,
        let pending = run.pendingChoice, pending.toolCallID == toolCallID,
        pending.options.contains(option) else { return }
      guard let index = run.steps.firstIndex(where: {
        $0.toolCalls.contains { $0.id == toolCallID }
      }) else { return }
      run.steps[index].outcomes.removeAll { $0.toolCallID == toolCallID }
      run.steps[index].outcomes.append(AgentToolOutcome(
        toolCallID: toolCallID, status: "ok", resultJSON: Self.answeredResult(option),
        noteID: nil, noteRevision: nil))
      run.pendingChoice = nil
      run.status = .running
      switch await self.checkpoint(run) {
      case .saved(let saved):
        self.active = saved
        await self.runLoop(runID: saved.id, scope: saved.scope)
      case .conflict, .failed:
        return
      }
    }
  }

  func cancel() {
    guard let run = active, !run.status.isTerminal, run.scope == store?.noteScope else {
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
          guard let reloaded = try? await store.repository.loadRun(run.id, scope: run.scope)
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
      if let cloud = store.cloud { try? await cloud.cancelAssistantRun(run.id) }
    }
  }

  /// Drop in-memory state when the account scope goes away. Persisted runs stay.
  func reset() {
    invalidateDrive()
    active = nil
    notice = nil
    cancelInFlight = false
  }

  func resume() {
    guard !isWorking, !cancelInFlight, let store, let scope = store.noteScope else { return }
    begin { [weak self] in
      guard let self, let store = self.store, store.noteScope == scope else { return }
      let runs = (try? await store.repository.loadRuns(scope: scope)) ?? []
      guard !Task.isCancelled else { return }
      if let run = runs.first(where: {
        $0.status == .running || $0.status == .waitingForUser
      }) {
        self.active = run
        self.notice = nil
        if run.status == .running {
          guard store.cloud != nil else {
            self.notice = "当前未连接学习服务，无法使用助手整理笔记"
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
    do {
      let saved = try await store.repository.saveRun(run)
      guard driveGeneration == generation, !Task.isCancelled, saved.scope == store.noteScope
      else { return .conflict }
      active = saved
      return .saved(saved)
    } catch let error as NoteStoreError {
      if case .runConflict = error {
        // Another owner advanced the row; reload and re-drive from that state.
        let reloaded = try? await store.repository.loadRun(run.id, scope: run.scope)
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
      guard let loaded = try? await store.repository.loadRun(runID, scope: scope) else { return }
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
          notice = "当前未连接学习服务，无法使用助手整理笔记"
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
          notice = "当前未连接学习服务，无法使用助手整理笔记"
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
          run.finalText = last.content ?? ""
          run.status = .completed
          switch await checkpoint(run) {
          case .saved(let saved): active = saved
          case .conflict: continue
          case .failed: return
          }
          return
        }
        if run.steps.count >= AgentRunRecord.maxSteps {
          run.status = .failed
          run.failure = "已达到助手步骤上限"
          switch await checkpoint(run) {
          case .conflict: continue
          case .saved, .failed: return
          }
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
    var waited: Duration = .zero
    while !Task.isCancelled {
      if run.cancelRequested { return .redo }
      // A refused budget ends the run whatever the wire state says, including
      // the response of a retryable re-POST.
      if response.refused != nil {
        if response.refused == "cancelled" {
          run.status = .cancelled
        } else {
          run.status = .failed
          run.failure = "已达到本次任务的模型预算（\(response.refused ?? "未知")）"
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
        case .saved(let saved): run = saved; return .next
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
          notice = "当前未连接学习服务，无法使用助手整理笔记"
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
          notice = "当前未连接学习服务，无法使用助手整理笔记"
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
          run.failure = "已达到本次任务的模型预算（\(response.refused ?? "未知")）"
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
      guard let fresh = try? await store.repository.loadRun(run.id, scope: run.scope) else {
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
      default:
        flow = await appendOutcome(
          Self.errorOutcome(call.id, "未知工具"), run: &run, stepIndex: stepIndex)
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
    switch Self.validateSaveNote(Self.arguments(call), run: run) {
    case .failure(let error):
      return await appendOutcome(
        Self.errorOutcome(call.id, error.message), run: &run, stepIndex: stepIndex)

    case .success(let args):
      let references = args.evidenceIDs.compactMap { id in
        run.evidence(id).map(NoteReference.init)
      }
      let note = StudyNote(
        id: "note-" + UUID().uuidString.lowercased(), kind: .text,
        title: args.title, body: args.body,
        anchor: NoteAnchor(
          courseID: run.context.courseID, bookID: run.context.bookID,
          chapterID: run.context.chapterID, page: run.context.page,
          printedPage: run.context.printedPage, quote: run.context.selection),
        references: references.isEmpty ? nil : references,
        provenance: NoteProvenance(
          runID: run.id, toolCallID: call.id, sourceLabel: args.sourceLabel))
      let result = Self.savedResult(noteID: note.id, revision: 1)
      do {
        let commit = try await store.repository.commitToolNote(
          run: run, toolCallID: call.id, argumentsHash: call.argumentsHash,
          note: note, result: result)
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
      run.committedNoteIDs.contains(noteID),
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
    let started = agent.start(selection, request: request)
    if !started, let notice = agent.notice {
      showToast(notice, kind: .warning)
    }
    // Show the run that is blocking the new request, or the run just started.
    if started || agent.active != nil { globalAssistantOpen = true }
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
