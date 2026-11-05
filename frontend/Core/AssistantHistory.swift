import Foundation
import Observation

struct AssistantSessionContext: Codable, Equatable, Sendable {
  var courseID: String?
  var bookID: String
  var bookIDs: [String]
  var chapterID: String?
  var courseTitle: String
  var chapterTitle: String
  var prompts: [String]
  var discovery: Bool
  var sessionID: String? = nil
  var messageID: String? = nil
  var courseResourceID: String? = nil
  var page: Int? = nil
  var printedPage: Int? = nil
  var selection: String? = nil
  var currentNoteID: String? = nil
  var pageType: String? = nil
  var practiceState: String? = nil
  var sourceVersion: String? = nil
  var bookTitle: String? = nil
}

struct AssistantSession: Codable, Identifiable, Sendable {
  var id = UUID().uuidString
  var title: String
  var context: AssistantSessionContext
  var createdAt = Date()
  var updatedAt = Date()
  var messages: [ChatMessage] = []
  var draft = ""
  var draftAttachments: [AssistantAttachment]?
  var courseLinks: [String: [String]] = [:]
  var pendingQuestion: String?
  var error: String?
  var contextTokens: Int? = nil
  var contextEstimated: Bool? = nil
  /// Once locked, shrinking later inputs can never reopen this conversation.
  var capacityState: String? = nil
  var capacityTriggerTokens: Int? = nil
  var finishingTaskID: String? = nil
  var capacityReason: String? = nil
  var runs: [AgentRunRecord]? = nil
  var isReadOnly: Bool { capacityState == "read_only" || capacityState == "archived" }
  var acceptsNewMessages: Bool { capacityState == nil || capacityState == "open" }
}

struct AssistantHistoryArchive: Codable, Sendable {
  var sessions: [AssistantSession] = []
  var selectedID: String?
  var newDraft = ""
  var newAttachments: [AssistantAttachment]?
  var newContext: AssistantSessionContext?
  var deletedSessionIDs: [String]? = nil
  /// Deletion receipts may be acknowledged, but migration and late saves must
  /// always remember that the owner removed this conversation.
  var removedSessionIDs: [String]? = nil
  var removedIDs: Set<String> { Set((removedSessionIDs ?? []) + (deletedSessionIDs ?? [])) }
}

extension LocalBookCourseRepository {
  /// Serialize tombstones with the latest stored archive. An older migration
  /// or queued save cannot put a deleted conversation back into history.
  func saveAssistantHistoryPreservingDeletions(_ value: AssistantHistoryArchive, scope: String) throws {
    let previous = try loadAssistantHistory(scope: scope)
    var merged = value
    let removed = value.removedIDs.union(previous?.removedIDs ?? [])
    merged.removedSessionIDs = removed.isEmpty ? nil : removed.sorted()
    let present = Set(merged.sessions.map(\.id))
    merged.sessions += (previous?.sessions ?? []).filter { !present.contains($0.id) && !removed.contains($0.id) }
    merged.sessions.removeAll { removed.contains($0.id) }
    if let selected = merged.selectedID, removed.contains(selected) { merged.selectedID = nil }
    try saveAssistantHistory(merged, scope: scope)
  }

  func importAssistantHistorySessions(_ sessions: [AssistantSession], scope: String) throws {
    var latest = try loadAssistantHistory(scope: scope) ?? AssistantHistoryArchive()
    let existing = Set(latest.sessions.map(\.id)), removed = latest.removedIDs
    latest.sessions += sessions.filter { !existing.contains($0.id) && !removed.contains($0.id) }
    try saveAssistantHistoryPreservingDeletions(latest, scope: scope)
  }
}

struct AssistantHistoryAnswer: Sendable {
  var text: String
  var citations: [Citation] = []
  var courseIDs: [String] = []
  var contextTokens: Int? = nil
  var capacityState: String? = nil
  var sourceLabel: String? = nil
}

enum AssistantSessionTitle {
  static func make(_ question: String) -> String {
    let original = question.trimmingCharacters(in: .whitespacesAndNewlines)
    var title = original.components(separatedBy: .newlines).first ?? original
    let prefixes = ["请问", "请你", "请", "帮我", "帮忙", "能不能", "能否", "可以", "给我"]
    for _ in 0..<8 {
      title = title.trimmingCharacters(in: .whitespacesAndNewlines)
      guard let prefix = prefixes.first(where: title.hasPrefix) else { break }
      title.removeFirst(prefix.count)
    }
    let actions = ["我想了解", "我想知道", "告诉我", "解释一下", "解释", "介绍一下", "介绍",
      "说明一下", "说明", "讲解一下", "讲解", "讲讲", "分析一下", "分析", "比较一下", "比较", "什么是"]
    if let action = actions.first(where: title.hasPrefix) { title.removeFirst(action.count) }
    for suffix in ["是什么意思", "的关系", "有什么区别", "的区别", "怎么理解", "如何理解"] {
      title = title.trimmingCharacters(in: .punctuationCharacters.union(.whitespacesAndNewlines))
      if title.hasSuffix(suffix) { title.removeLast(suffix.count); break }
    }
    title = title.replacingOccurrences(of: "“", with: "").replacingOccurrences(of: "”", with: "")
      .trimmingCharacters(in: .punctuationCharacters.union(.whitespacesAndNewlines))
    if title.isEmpty { title = original }
    let hasChinese = title.unicodeScalars.contains { (0x3400...0x9fff).contains(Int($0.value)) }
    let limit = hasChinese ? 14 : 30
    if title.count > limit {
      title = String(title.prefix(limit))
      if !hasChinese, let boundary = title.lastIndex(of: " ") { title = String(title[..<boundary]) }
    }
    return title.isEmpty ? "新对话" : title
  }
}

/// Owns requests independently of the overlay. Each completion targets its original session.
@MainActor @Observable final class AssistantHistoryStore {
  private(set) var archive = AssistantHistoryArchive()
  private(set) var scope: String?
  private(set) var ready = false
  private(set) var loading = false
  private(set) var storageError: String?
  private(set) var runningIDs: Set<String> = []
  var showingSessionLimit = false
  var capacityAlertSessionID: String?
  static let maximumSessions = 6
  @ObservationIgnored private let repository: LocalBookCourseRepository
  @ObservationIgnored private var generation = UUID()
  @ObservationIgnored private var requests: [String: Task<Void, Never>] = [:]
  @ObservationIgnored private var saveTask: Task<Void, Never>?
  @ObservationIgnored private var draftSaveTask: Task<Void, Never>?

  init(repository: LocalBookCourseRepository) { self.repository = repository }

  var current: AssistantSession? { archive.sessions.first { $0.id == archive.selectedID } }
  var draft: String { current?.draft ?? archive.newDraft }
  var attachments: [AssistantAttachment] { current?.draftAttachments ?? (current == nil ? archive.newAttachments : nil) ?? [] }
  var sessions: [AssistantSession] { archive.sessions.sorted { $0.updatedAt > $1.updatedAt } }
  var busy: Bool { archive.selectedID.map(runningIDs.contains) ?? false }
  var acceptsNewMessages: Bool { current?.acceptsNewMessages ?? true }
  func isReady(for value: String?) -> Bool { value != nil && scope == value && ready }

  func load(scope value: String?) async {
    if scope == value, ready { return }
    reset()
    scope = value
    let token = generation
    loading = true
    await saveTask?.value
    guard token == generation else { return }
    guard let value else { loading = false; return }
    do {
      let saved = try await repository.loadAssistantHistory(scope: value) ?? AssistantHistoryArchive()
      guard token == generation else { return }
      archive = saved
      let removed = archive.removedIDs
      archive.removedSessionIDs = removed.isEmpty ? nil : removed.sorted()
      archive.sessions.removeAll { removed.contains($0.id) }
      if let id = archive.selectedID, !archive.sessions.contains(where: { $0.id == id }) {
        archive.selectedID = sessions.first?.id
      }
      for index in archive.sessions.indices {
        if archive.sessions[index].pendingQuestion != nil,
          let run = archive.sessions[index].runs?.last, run.status == .completed, let finalText = run.finalText {
          let message = ChatMessage(id: run.id + ".answer", role: "assistant", text: finalText,
            citations: run.finalCitations ?? [], sourceLabel: run.finalSourceLabel)
          archive.sessions[index].messages.append(message)
          if let ids = run.finalCourseIDs, !ids.isEmpty { archive.sessions[index].courseLinks[message.id] = ids }
          archive.sessions[index].pendingQuestion = nil
        }
        if archive.sessions[index].capacityState == "finishing", archive.sessions[index].runs?.last?.status.isTerminal == true {
          archive.sessions[index].capacityState = "read_only"
          archive.sessions[index].finishingTaskID = nil
        }
        if archive.sessions[index].pendingQuestion != nil {
          archive.sessions[index].error = archive.sessions[index].isReadOnly ? nil : "上次回答未完成，可以点击重试继续。"
        }
      }
      ready = true
      loading = false
    } catch {
      guard token == generation else { return }
      loading = false
      storageError = "无法读取历史对话：" + error.localizedDescription
    }
  }

  func reset() {
    draftSaveTask?.cancel()
    for request in requests.values { request.cancel() }
    for index in archive.sessions.indices where archive.sessions[index].pendingQuestion != nil {
      archive.sessions[index].error = "回答已中断，可以点击重试继续。"
    }
    if ready { persist() }
    generation = UUID()
    requests = [:]
    runningIDs = []
    archive = AssistantHistoryArchive()
    scope = nil
    ready = false
    loading = false
    storageError = nil
    showingSessionLimit = false
    capacityAlertSessionID = nil
  }

  func setDraft(_ text: String, context: AssistantSessionContext) {
    guard ready else { return }
    if let index = archive.sessions.firstIndex(where: { $0.id == archive.selectedID }) {
      guard archive.sessions[index].draft != text else { return }
      archive.sessions[index].draft = text
    } else {
      guard archive.newDraft != text else { return }
      archive.newDraft = text
      if archive.newContext == nil { archive.newContext = context }
    }
    draftSaveTask?.cancel()
    draftSaveTask = Task { [weak self] in
      do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
      self?.persist()
    }
  }

  @discardableResult func startNew(context: AssistantSessionContext) -> Bool {
    guard ready else { return false }
    guard archive.sessions.count < Self.maximumSessions else { showingSessionLimit = true; return false }
    showingSessionLimit = false
    draftSaveTask?.cancel()
    archive.selectedID = nil
    if archive.newContext == nil { archive.newContext = context }
    capacityAlertSessionID = nil
    persist()
    return true
  }

  func select(_ id: String) {
    guard ready, archive.sessions.contains(where: { $0.id == id }) else { return }
    archive.selectedID = id
    persist()
  }

  func setAttachments(_ value: [AssistantAttachment], context: AssistantSessionContext) {
    guard ready else { return }
    if let index = archive.sessions.firstIndex(where: { $0.id == archive.selectedID }) {
      archive.sessions[index].draftAttachments = value.isEmpty ? nil : value
    } else {
      archive.newAttachments = value.isEmpty ? nil : value
      if archive.newContext == nil { archive.newContext = context }
    }
    persist()
  }

  func submit(_ question: String, context: AssistantSessionContext,
    answer: @escaping @MainActor (AssistantSessionContext, [ChatMessage], String) async throws -> AssistantHistoryAnswer) {
    guard ready, !busy, !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    let files = attachments
    guard acceptsNewMessages else { capacityAlertSessionID = current?.id; return }
    if current == nil {
      // Main-actor synchronous check + insertion is atomic across simultaneous entry points.
      guard archive.sessions.count < Self.maximumSessions else { showingSessionLimit = true; return }
      let session = AssistantSession(title: AssistantSessionTitle.make(question),
        context: archive.newContext ?? context)
      archive.sessions.append(session)
      archive.selectedID = session.id
      archive.newDraft = ""
      archive.newAttachments = nil
      archive.newContext = nil
    }
    guard let index = archive.sessions.firstIndex(where: { $0.id == archive.selectedID }) else { return }
    let message = ChatMessage(role: "user", text: question, attachments: files.isEmpty ? nil : files)
    archive.sessions[index].context = context
    archive.sessions[index].context.sessionID = archive.sessions[index].id
    archive.sessions[index].context.messageID = message.id
    archive.sessions[index].messages.append(message)
    archive.sessions[index].contextTokens = (archive.sessions[index].messages.reduce(0) { $0 + $1.text.utf8.count }) / 3
    archive.sessions[index].contextEstimated = true
    archive.sessions[index].draft = ""
    archive.sessions[index].draftAttachments = nil
    archive.sessions[index].pendingQuestion = question
    archive.sessions[index].updatedAt = Date()
    run(id: archive.sessions[index].id, answer: answer)
  }

  func retry(answer: @escaping @MainActor (AssistantSessionContext, [ChatMessage], String) async throws -> AssistantHistoryAnswer) {
    guard ready, !busy, acceptsNewMessages, let session = current, session.pendingQuestion != nil else { return }
    run(id: session.id, answer: answer)
  }

  func freezeQueryPayload(_ proposed: [String: JSONValue], messageID: String) async throws -> [String: JSONValue] {
    guard let position = archive.sessions.firstIndex(where: { session in
      session.messages.contains { $0.id == messageID && $0.role == "user" }
    }), let message = archive.sessions[position].messages.firstIndex(where: { $0.id == messageID }) else {
      // Reader questions can use the same transport without belonging to the
      // global history archive. Their caller owns the request lifetime.
      return proposed
    }
    let token = generation
    let frozen = archive.sessions[position].messages[message].unifiedQueryPayload ?? proposed
    archive.sessions[position].messages[message].unifiedQueryPayload = frozen
    await flush()
    try Task.checkCancellation()
    guard token == generation else { throw CancellationError() }
    if let storageError { throw ContentError.storage(storageError) }
    return frozen
  }

  private func run(id: String,
    answer: @escaping @MainActor (AssistantSessionContext, [ChatMessage], String) async throws -> AssistantHistoryAnswer) {
    guard let index = archive.sessions.firstIndex(where: { $0.id == id }),
      let question = archive.sessions[index].pendingQuestion else { return }
    archive.sessions[index].error = nil
    archive.sessions[index].context.sessionID = id
    if archive.sessions[index].context.messageID == nil {
      archive.sessions[index].context.messageID = archive.sessions[index].messages.last(where: { $0.role == "user" })?.id
    }
    let session = archive.sessions[index]
    let token = generation
    runningIDs.insert(id)
    persist()
    requests[id] = Task { [weak self] in
      guard let self else { return }
      // Save the submitted question before making a potentially long request.
      await saveTask?.value
      guard token == generation, !Task.isCancelled else { return }
      do {
        let response = try await answer(session.context, session.messages, question)
        try Task.checkCancellation()
        guard token == generation,
          let position = archive.sessions.firstIndex(where: { $0.id == id }) else { return }
        let message = ChatMessage(role: "assistant", text: response.text, citations: response.citations,
          sourceLabel: response.sourceLabel)
        archive.sessions[position].messages.append(message)
        if let user = archive.sessions[position].messages.lastIndex(where: { $0.role == "user" }) {
          archive.sessions[position].messages[user].unifiedQueryPayload = nil
        }
        if !response.courseIDs.isEmpty { archive.sessions[position].courseLinks[message.id] = response.courseIDs }
        archive.sessions[position].pendingQuestion = nil
        archive.sessions[position].error = nil
        archive.sessions[position].updatedAt = Date()
        if let tokens = response.contextTokens {
          applyCapacity(sessionID: id, tokens: tokens, runningTaskID: nil, estimated: false)
        }
      } catch {
        // reset/delete/course removal already owns the visible cancellation
        // state. A retired waiter must not overwrite a deletion receipt or a
        // newer request in the same conversation.
        guard !Task.isCancelled, token == generation,
          let position = archive.sessions.firstIndex(where: { $0.id == id }) else { return }
        if error.localizedDescription.contains("已达会话数上限"), archive.sessions[position].messages.count == 1 {
          archive.newDraft = archive.sessions[position].messages.first?.text ?? question
          archive.newAttachments = archive.sessions[position].messages.first?.attachments
          archive.newContext = archive.sessions[position].context
          archive.sessions.remove(at: position)
          archive.removedSessionIDs = Array(archive.removedIDs.union([id])).sorted()
          if archive.selectedID == id { archive.selectedID = nil }
          showingSessionLimit = true
        } else {
          archive.sessions[position].error = error is CancellationError
            ? "回答已中断，可以点击重试继续。" : error.localizedDescription
        }
      }
      if let position = archive.sessions.firstIndex(where: { $0.id == id }),
        archive.sessions[position].capacityState == "finishing" {
        archive.sessions[position].capacityState = "read_only"
        archive.sessions[position].finishingTaskID = nil
        capacityAlertSessionID = id
      }
      runningIDs.remove(id)
      requests[id] = nil
      await flush()
    }
  }

  func delete(_ id: String) {
    guard ready, archive.sessions.contains(where: { $0.id == id }) else { return }
    requests[id]?.cancel()
    requests[id] = nil
    runningIDs.remove(id)
    archive.sessions.removeAll { $0.id == id }
    archive.deletedSessionIDs = Array(Set((archive.deletedSessionIDs ?? []) + [id]))
    archive.removedSessionIDs = Array(archive.removedIDs.union([id])).sorted()
    if archive.selectedID == id { archive.selectedID = nil }
    if capacityAlertSessionID == id { capacityAlertSessionID = nil }
    if archive.sessions.count < Self.maximumSessions { showingSessionLimit = false }
    // Attachments may be referenced by saved notes. History deletion never removes files.
    persist()
  }

  func acknowledgeDeletion(_ id: String) {
    guard ready else { return }
    archive.removedSessionIDs = Array(archive.removedIDs.union([id])).sorted()
    archive.deletedSessionIDs?.removeAll { $0 == id }
    persist()
  }

  func mergeRemoteSessions(_ values: [AssistantSession]) {
    guard ready else { return }
    let deleted = archive.removedIDs
    for remote in values where !deleted.contains(remote.id) {
      if let index = archive.sessions.firstIndex(where: { $0.id == remote.id }) {
        if remote.isReadOnly { archive.sessions[index].capacityState = "read_only" }
        if let tokens = remote.contextTokens { archive.sessions[index].contextTokens = tokens }
        if archive.sessions[index].messages.isEmpty { archive.sessions[index].messages = remote.messages }
      } else { archive.sessions.append(remote) }
    }
    persist()
  }

  func recordRun(_ run: AgentRunRecord) {
    guard ready, run.scope == scope, run.ephemeral != true, let sessionID = run.context.sessionID,
      !archive.removedIDs.contains(sessionID),
      let index = archive.sessions.firstIndex(where: { $0.id == sessionID }) else { return }
    var runs = archive.sessions[index].runs ?? []
    if let i = runs.firstIndex(where: { $0.id == run.id }) { runs[i] = run } else { runs.append(run) }
    archive.sessions[index].runs = runs
    if run.legacyArchived == true {
      archive.sessions[index].capacityState = "read_only"
      archive.sessions[index].capacityReason = "旧版任务已归档，请另开新版对话"
      archive.sessions[index].pendingQuestion = nil
      archive.sessions[index].error = run.failure
      persist()
      return
    }
    if run.capacityState == "archived" || run.capacityState == "read_only" {
      archive.sessions[index].capacityState = "read_only"
      archive.sessions[index].capacityReason = "上下文已封存"
      archive.sessions[index].finishingTaskID = nil
      capacityAlertSessionID = sessionID
    } else if run.capacityState == "finishing", !archive.sessions[index].isReadOnly {
      archive.sessions[index].capacityState = "finishing"
      archive.sessions[index].finishingTaskID = run.id
    }
    if run.status.isTerminal {
      if archive.sessions[index].capacityState == "finishing" {
        archive.sessions[index].capacityState = "read_only"
        archive.sessions[index].finishingTaskID = nil
        capacityAlertSessionID = sessionID
      }
      // A task may finish after a process restart, without the original waiter closure.
      if requests[sessionID] == nil {
        if run.status == .completed, let finalText = run.finalText {
          let messageID = run.id + ".answer"
          if !archive.sessions[index].messages.contains(where: { $0.id == messageID }) {
            archive.sessions[index].messages.append(ChatMessage(id: messageID, role: "assistant", text: finalText,
              citations: run.finalCitations ?? [], sourceLabel: run.finalSourceLabel))
            if let ids = run.finalCourseIDs, !ids.isEmpty { archive.sessions[index].courseLinks[messageID] = ids }
          }
          archive.sessions[index].pendingQuestion = nil
          archive.sessions[index].error = nil
        } else if let failure = run.failure { archive.sessions[index].error = failure }
      }
    }
    if let tokens = run.contextTokens {
      applyCapacity(sessionID: sessionID, tokens: tokens,
        runningTaskID: run.status.isTerminal ? nil : run.id, estimated: run.contextEstimated == true)
    }
    persist()
  }

  func applyCapacity(sessionID: String, tokens: Int, runningTaskID: String?, estimated: Bool = false) {
    guard let index = archive.sessions.firstIndex(where: { $0.id == sessionID }) else { return }
    archive.sessions[index].contextTokens = tokens
    archive.sessions[index].contextEstimated = estimated
    guard tokens >= 900_000 else { persist(); return }
    if archive.sessions[index].capacityTriggerTokens == nil { archive.sessions[index].capacityTriggerTokens = tokens }
    archive.sessions[index].capacityReason = tokens >= 1_000_000 ? "上下文已达到 1M" : "上下文已达到 900K"
    if !archive.sessions[index].isReadOnly {
      archive.sessions[index].capacityState = runningTaskID != nil && tokens < 1_000_000 ? "finishing" : "read_only"
      archive.sessions[index].finishingTaskID = runningTaskID
    }
    if archive.sessions[index].isReadOnly { capacityAlertSessionID = sessionID }
    persist()
  }

  /// Remove a deleted course's live references without deleting ordinary conversation text.
  func removeCourseReferences(tombstones: [CourseDeletionTombstone]) {
    guard ready, !tombstones.isEmpty else { return }
    let removed = CourseDeletionPruning.ids(tombstones)
    for index in archive.sessions.indices
      where archive.sessions[index].context.courseID.map(removed.contains) == true {
      let id = archive.sessions[index].id
      requests[id]?.cancel()
      requests[id] = nil
      runningIDs.remove(id)
      archive.sessions[index].pendingQuestion = nil
      archive.sessions[index].error = nil
    }
    archive = CourseDeletionPruning.history(archive, tombstones: tombstones)
    persist()
  }

  /// A course can delete the very run that confirmed its removal. Keep only a
  /// local completion message; never put that run, its evidence or course links
  /// back into the archive. Deleted conversations remain deleted.
  @discardableResult func recordCourseRemovalCompletion(operationID: String, sessionID: String?,
    messageID: String?, scope expectedScope: String) -> Bool {
    guard ready, scope == expectedScope, !operationID.isEmpty, let sessionID,
      !archive.removedIDs.contains(sessionID),
      let index = archive.sessions.firstIndex(where: { $0.id == sessionID }) else { return false }
    let receiptMessageID = "course-removal:" + operationID
    if !archive.sessions[index].messages.contains(where: { $0.id == receiptMessageID }) {
      archive.sessions[index].messages.append(ChatMessage(id: receiptMessageID, role: "assistant",
        text: "课程已从本机删除。"))
    }
    // A later question in this retained conversation owns its own pending state.
    if archive.sessions[index].context.messageID == messageID {
      archive.sessions[index].pendingQuestion = nil
      archive.sessions[index].error = nil
    }
    archive.sessions[index].updatedAt = .now
    persist()
    return true
  }

  private func persist() {
    guard ready, let scope else { return }
    let value = archive
    let previous = saveTask
    saveTask = Task { [repository, weak self] in
      await previous?.value
      do {
        try await repository.saveAssistantHistoryPreservingDeletions(value, scope: scope)
        if self?.scope == scope { self?.storageError = nil }
      } catch {
        if self?.scope == scope {
          self?.storageError = "对话暂未保存，请重试：" + error.localizedDescription
        }
      }
    }
  }

  func flush() async {
    draftSaveTask?.cancel()
    persist()
    await saveTask?.value
  }
}

extension AppStore {
  var assistantHistoryScope: String? {
    if repository.includesFixtureContent || cloud == nil { return "local" }
    guard cloudState.authenticated else { return nil }
    return cloudState.cacheKey
  }

  var assistantSessionContext: AssistantSessionContext {
    let discovery = route == .community
    let coursePages: Set<Route> = [.study, .lesson, .courseDetail, .notes, .voiceNote, .flashcards, .plan, .assignment, .diagnosis, .mistakes]
    let contextCourseID = route == .source ? sourceTarget?.courseID : (coursePages.contains(route) ? selectedCourseID : nil)
    let contextBookID = route == .source ? sourceTarget?.bookID : (contextCourseID == nil ? nil : selectedBookID)
    let readerPage = contextBookID.flatMap { locations["\(contextCourseID ?? "")::\($0)"]?.page } ?? sourceTarget?.page ?? 1
    let readerMatches = sources.first(where: { $0.id == contextBookID })?.chapters
      .filter { $0.page_start <= readerPage && $0.page_end >= readerPage } ?? []
    let readerSections = readerMatches.filter { $0.level <= 2 }
    let readerChapter = (readerSections.isEmpty ? readerMatches : readerSections).sorted {
      $0.level == $1.level ? $0.page_end - $0.page_start < $1.page_end - $1.page_start : $0.level > $1.level
    }.first
    let contextChapterID = route == .source ? readerChapter?.id : selectedChapterID
    let concepts = activeLesson?.key_concepts ?? []
    let first = concepts.first ?? chapterTitle
    return AssistantSessionContext(courseID: contextCourseID, bookID: contextBookID ?? "",
      bookIDs: courses.first(where: { $0.id == contextCourseID })?.resourceIDs ?? [contextBookID].compactMap { $0 }, chapterID: contextBookID == nil ? nil : contextChapterID,
      courseTitle: discovery ? "课程发现" : (contextCourseID == nil ? route.title : currentCourse?.name ?? "当前课程"),
      chapterTitle: discovery ? "帮你发现适合的课程" : (activeLesson?.title ?? chapterTitle),
      prompts: discovery ? ["按学科推荐", "按目标推荐", "查看全部课程"] : ["用一句话解释“\(first)”",
        concepts.count > 1 ? "比较“\(first)”和“\(concepts[1])”" : "围绕“\(chapterTitle)”给我出一道题"],
      discovery: discovery,
      courseResourceID: courseResourceID(courseID: contextCourseID, bookID: contextBookID ?? ""),
      page: max(1, route == .source ? readerPage : (sourceTarget?.page ?? activeLesson?.page_start ?? 1)),
      printedPage: route == .source ? readerChapter?.printed_page_start.map { $0 + readerPage - (readerChapter?.page_start ?? readerPage) } : sourceTarget?.printedPage,
      selection: contextBookID == nil ? "" : selectedQuote,
      currentNoteID: noteState.focusNoteID, pageType: route.rawValue,
      practiceState: route == .assignment ? "pending" : nil,
      sourceVersion: sources.first(where: { $0.id == contextBookID }).map { AgentRuntime.sourceVersion(for: $0.summary) },
      bookTitle: sources.first(where: { $0.id == contextBookID })?.summary.title)
  }

  func answerHistorySession(context: AssistantSessionContext, history: [ChatMessage],
    question: String) async throws -> AssistantHistoryAnswer {
    let epoch = cloudState.epoch
    let owner = assistantHistoryScope
    do {
      try await syncAssistantHistory()
      try Task.checkCancellation()
      guard epoch == cloudState.epoch, owner == assistantHistoryScope,
        context.sessionID.map({ !assistantHistory.archive.removedIDs.contains($0) }) != false else { throw CancellationError() }
      let response = try await agent.answerUnified(sessionID: context.sessionID ?? UUID().uuidString,
        messageID: context.messageID ?? history.last?.id ?? UUID().uuidString,
        request: question, context: agent.frozenContext(session: context), history: history,
        attachments: history.last?.attachments ?? [], ephemeral: false)
      guard epoch == cloudState.epoch, owner == assistantHistoryScope else { throw CancellationError() }
      return response
    } catch {
      if let failure = error as? CloudError, failure.status == 401, epoch == cloudState.epoch {
        handleCloudError(error)
      }
      throw error
    }
  }
}

private struct RemoteAssistantSession: Decodable, Sendable {
  var session_id: String
  var title: String
  var state: String
  var input_tokens: Int
  var estimated: JSONValue
  var created: Double?
  var updated: Double?
  var messages: [[String: JSONValue]]?

  var localSession: AssistantSession {
    var session = AssistantSession(id: session_id, title: title,
      context: AssistantSessionContext(courseID: nil, bookID: "", bookIDs: [], chapterID: nil,
        courseTitle: "已保存对话", chapterTitle: "", prompts: [], discovery: false))
    session.createdAt = created.map(Date.init(timeIntervalSince1970:)) ?? .now
    session.updatedAt = updated.map(Date.init(timeIntervalSince1970:)) ?? session.createdAt
    session.contextTokens = input_tokens
    session.contextEstimated = estimated == .bool(true) || estimated == .number(1)
    session.capacityState = state == "archived" ? "read_only" : state
    session.messages = (messages ?? []).enumerated().compactMap { index, message in
      guard let role = message["role"]?.stringValue, ["user", "assistant"].contains(role) else { return nil }
      if role == "user", let displayText = message["display_text"]?.stringValue {
        return ChatMessage(id: session_id + ".remote.\(index)", role: role, text: displayText)
      }
      let content = message["content"]
      if role == "user", case .array(let parts)? = content,
        !parts.contains(where: { if case .object(let part) = $0 { return part["type"]?.stringValue == "text" }; return false }) { return nil }
      let text: String
      if let value = content?.stringValue { text = value }
      else if case .array(let parts)? = content {
        text = parts.compactMap { value -> String? in
          if case .object(let part) = value { return part["text"]?.stringValue }
          return nil
        }.joined(separator: "\n")
      } else { return nil }
      guard !text.isEmpty else { return nil }
      return ChatMessage(id: session_id + ".remote.\(index)", role: role, text: text)
    }
    return session
  }
}

extension AppStore {
  /// Local tombstones survive offline deletion and release the server slot before another send.
  func syncAssistantHistory() async throws {
    // Normal chat never uploads or overwrites phone-owned history. Legacy
    // migration is a separate, explicit archive import with version checks.
    let scope = assistantHistoryScope, epoch = cloudState.epoch
    guard assistantHistory.isReady(for: scope) else { return }
    await assistantHistory.flush()
    guard scope == assistantHistoryScope, epoch == cloudState.epoch,
      assistantHistory.isReady(for: scope) else { throw CancellationError() }
    if let issue = assistantHistory.storageError { throw ContentError.storage(issue) }
    for id in assistantHistory.archive.deletedSessionIDs ?? [] {
      assistantHistory.acknowledgeDeletion(id)
    }
  }

  @discardableResult func deleteAssistantSession(_ id: String, expectedScope: String? = nil) async -> Bool {
    guard let scope = assistantHistoryScope, expectedScope == nil || expectedScope == scope,
      assistantHistory.isReady(for: scope), assistantHistory.sessions.contains(where: { $0.id == id }) else { return false }
    let epoch = cloudState.epoch
    assistantHistory.delete(id)
    do {
      try await agent.cancelSession(id, scope: scope)
      guard scope == assistantHistoryScope, epoch == cloudState.epoch else { return true }
      try await syncAssistantHistory()
    } catch {
      if scope == assistantHistoryScope, epoch == cloudState.epoch {
        showToast("对话已移除，保存或任务取消仍待重试。", kind: .warning)
      }
    }
    return true
  }
}
