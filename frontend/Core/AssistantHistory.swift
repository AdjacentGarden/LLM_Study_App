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
}

struct AssistantSession: Codable, Identifiable, Sendable {
  var id = UUID().uuidString
  var title: String
  var context: AssistantSessionContext
  var createdAt = Date()
  var updatedAt = Date()
  var messages: [ChatMessage] = []
  var draft = ""
  var courseLinks: [String: [String]] = [:]
  var pendingQuestion: String?
  var error: String?
}

struct AssistantHistoryArchive: Codable, Sendable {
  var sessions: [AssistantSession] = []
  var selectedID: String?
  var newDraft = ""
  var newContext: AssistantSessionContext?
}

struct AssistantHistoryAnswer: Sendable {
  var text: String
  var citations: [Citation] = []
  var courseIDs: [String] = []
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
  @ObservationIgnored private let repository: LocalBookCourseRepository
  @ObservationIgnored private var generation = UUID()
  @ObservationIgnored private var requests: [String: Task<Void, Never>] = [:]
  @ObservationIgnored private var saveTask: Task<Void, Never>?
  @ObservationIgnored private var draftSaveTask: Task<Void, Never>?

  init(repository: LocalBookCourseRepository) { self.repository = repository }

  var current: AssistantSession? { archive.sessions.first { $0.id == archive.selectedID } }
  var draft: String { current?.draft ?? archive.newDraft }
  var sessions: [AssistantSession] { archive.sessions.sorted { $0.updatedAt > $1.updatedAt } }
  var busy: Bool { archive.selectedID.map(runningIDs.contains) ?? false }
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
      if let id = archive.selectedID, !archive.sessions.contains(where: { $0.id == id }) {
        archive.selectedID = sessions.first?.id
      }
      for index in archive.sessions.indices where archive.sessions[index].pendingQuestion != nil {
        archive.sessions[index].error = "上次回答未完成，可以点击重试继续。"
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

  func startNew(context: AssistantSessionContext) {
    guard ready else { return }
    archive.selectedID = nil
    archive.newDraft = ""
    archive.newContext = context
    persist()
  }

  func select(_ id: String) {
    guard ready, archive.sessions.contains(where: { $0.id == id }) else { return }
    archive.selectedID = id
    persist()
  }

  func submit(_ question: String, context: AssistantSessionContext,
    answer: @escaping @MainActor (AssistantSessionContext, [ChatMessage], String) async throws -> AssistantHistoryAnswer) {
    guard ready, !busy, !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    if current == nil {
      let session = AssistantSession(title: AssistantSessionTitle.make(question),
        context: archive.newContext ?? context)
      archive.sessions.append(session)
      archive.selectedID = session.id
      archive.newDraft = ""
      archive.newContext = nil
    }
    guard let index = archive.sessions.firstIndex(where: { $0.id == archive.selectedID }) else { return }
    archive.sessions[index].messages.append(ChatMessage(role: "user", text: question))
    archive.sessions[index].draft = ""
    archive.sessions[index].pendingQuestion = question
    archive.sessions[index].updatedAt = Date()
    run(id: archive.sessions[index].id, answer: answer)
  }

  func retry(answer: @escaping @MainActor (AssistantSessionContext, [ChatMessage], String) async throws -> AssistantHistoryAnswer) {
    guard ready, !busy, let session = current, session.pendingQuestion != nil else { return }
    run(id: session.id, answer: answer)
  }

  private func run(id: String,
    answer: @escaping @MainActor (AssistantSessionContext, [ChatMessage], String) async throws -> AssistantHistoryAnswer) {
    guard let index = archive.sessions.firstIndex(where: { $0.id == id }),
      let question = archive.sessions[index].pendingQuestion else { return }
    archive.sessions[index].error = nil
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
        let response = try await answer(session.context, Array(session.messages.suffix(8)), question)
        try Task.checkCancellation()
        guard token == generation,
          let position = archive.sessions.firstIndex(where: { $0.id == id }) else { return }
        let message = ChatMessage(role: "assistant", text: response.text, citations: response.citations)
        archive.sessions[position].messages.append(message)
        if !response.courseIDs.isEmpty { archive.sessions[position].courseLinks[message.id] = response.courseIDs }
        archive.sessions[position].pendingQuestion = nil
        archive.sessions[position].error = nil
        archive.sessions[position].updatedAt = Date()
      } catch {
        guard token == generation,
          let position = archive.sessions.firstIndex(where: { $0.id == id }) else { return }
        archive.sessions[position].error = error is CancellationError
          ? "回答已中断，可以点击重试继续。" : error.localizedDescription
      }
      runningIDs.remove(id)
      requests[id] = nil
      await flush()
    }
  }

  private func persist() {
    guard ready, let scope else { return }
    let value = archive
    let previous = saveTask
    saveTask = Task { [repository, weak self] in
      await previous?.value
      do {
        try await repository.saveAssistantHistory(value, scope: scope)
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
    let concepts = activeLesson?.key_concepts ?? []
    let first = concepts.first ?? chapterTitle
    return AssistantSessionContext(courseID: selectedCourseID, bookID: selectedBookID ?? "",
      bookIDs: currentCourse?.resourceIDs ?? [selectedBookID].compactMap { $0 }, chapterID: selectedChapterID,
      courseTitle: discovery ? "课程发现" : (currentCourse?.name ?? currentSource?.summary.title ?? "当前课程"),
      chapterTitle: discovery ? "帮你发现适合的课程" : (activeLesson?.title ?? chapterTitle),
      prompts: discovery ? ["按学科推荐", "按目标推荐", "查看全部课程"] : ["用一句话解释“\(first)”",
        concepts.count > 1 ? "比较“\(first)”和“\(concepts[1])”" : "围绕“\(chapterTitle)”给我出一道题"],
      discovery: discovery)
  }

  func answerHistorySession(context: AssistantSessionContext, history: [ChatMessage],
    question: String) async throws -> AssistantHistoryAnswer {
    let epoch = cloudState.epoch
    let owner = assistantHistoryScope
    if context.discovery {
      let answer = answerCommunityCourseQuery(question, books: community)
      return AssistantHistoryAnswer(text: answer.text, courseIDs: answer.courseIds)
    }
    do {
      let response = try await answerAssistant(question: question, bookID: context.bookID,
        bookIDs: context.bookIDs, chapterID: context.chapterID, sources: sources, history: history,
        courseID: context.courseID, bindToCurrentSelection: false)
      guard epoch == cloudState.epoch, owner == assistantHistoryScope else { throw CancellationError() }
      return AssistantHistoryAnswer(text: response.answer, citations: response.citations)
    } catch {
      if let failure = error as? CloudError, failure.status == 401, epoch == cloudState.epoch {
        handleCloudError(error)
      }
      throw error
    }
  }
}
