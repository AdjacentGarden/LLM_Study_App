import Foundation
import XCTest

@testable import BookCourse

// MARK: - Scripted URLProtocol

private struct AgentRecordedRequest {
  var host: String
  var method: String
  var path: String
  var body: Data
}

private enum AgentStubReply {
  case json(status: Int, body: String)
  case failure(URLError)
  case gated(AgentGate)
  indirect case handler((AgentRecordedRequest) -> AgentStubReply)
}

/// A one-shot release valve: the URLProtocol parks until the test decides what
/// to answer, so tests can inspect durable state between request and response.
private final class AgentGate: @unchecked Sendable {
  private let lock = NSLock()
  private var deliver: ((AgentStubReply) -> Void)?
  private var released: AgentStubReply?

  var isWaiting: Bool { lock.withLock { deliver != nil && released == nil } }

  func wait(_ body: @escaping (AgentStubReply) -> Void) {
    var immediate: AgentStubReply?
    lock.lock()
    if let released {
      immediate = released
      self.released = nil
    } else {
      deliver = body
    }
    lock.unlock()
    if let immediate { body(immediate) }
  }

  func release(_ reply: AgentStubReply) {
    var body: ((AgentStubReply) -> Void)?
    lock.lock()
    if let deliver {
      body = deliver
      self.deliver = nil
    } else {
      released = reply
    }
    lock.unlock()
    body?(reply)
  }
}

/// Lock-protected per-(host, method, path) queues plus a log of every request.
private final class AgentScriptedRouter: @unchecked Sendable {
  static let shared = AgentScriptedRouter()

  private struct Key: Hashable {
    var host: String
    var method: String
    var path: String
  }

  private let lock = NSLock()
  private var queues: [Key: [AgentStubReply]] = [:]
  private var requests: [AgentRecordedRequest] = []

  func enqueue(host: String, method: String, path: String, reply: AgentStubReply) {
    let key = Key(host: host, method: method, path: path)
    lock.withLock { queues[key, default: []].append(reply) }
  }

  func enqueue(
    host: String, method: String, path: String,
    handler: @escaping (AgentRecordedRequest) -> AgentStubReply
  ) {
    enqueue(host: host, method: method, path: path, reply: .handler(handler))
  }

  func take(host: String, method: String, path: String, body: Data) -> AgentStubReply {
    let key = Key(host: host, method: method, path: path)
    let request = AgentRecordedRequest(host: host, method: method, path: path, body: body)
    var reply: AgentStubReply?
    lock.lock()
    requests.append(request)
    if var queue = queues[key], !queue.isEmpty {
      reply = queue.removeFirst()
      queues[key] = queue
    }
    lock.unlock()
    guard let reply else {
      return .json(
        status: 500,
        body: "{\"detail\":\"unexpected \(method) \(path)\"}")
    }
    if case .handler(let handler) = reply { return handler(request) }
    return reply
  }

  func snapshot() -> [AgentRecordedRequest] {
    lock.withLock { requests }
  }
}

private final class AgentURLProtocol: URLProtocol, @unchecked Sendable {
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

  override func startLoading() {
    let reply = AgentScriptedRouter.shared.take(
      host: request.url?.host ?? "", method: request.httpMethod ?? "GET",
      path: request.url?.path ?? "", body: Self.bodyData(request))
    respond(reply)
  }

  override func stopLoading() {}

  private func respond(_ reply: AgentStubReply) {
    switch reply {
    case .json(let status, let body):
      deliver(status: status, data: Data(body.utf8))
    case .failure(let error):
      client?.urlProtocol(self, didFailWithError: error)
    case .gated(let gate):
      gate.wait { [self] reply in
        DispatchQueue.global().async { self.respond(reply) }
      }
    case .handler:
      client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
    }
  }

  private func deliver(status: Int, data: Data) {
    guard let url = request.url,
      let response = HTTPURLResponse(
        url: url, statusCode: status, httpVersion: "HTTP/1.1",
        headerFields: ["Content-Type": "application/json"])
    else {
      client?.urlProtocol(self, didFailWithError: URLError(.badURL))
      return
    }
    client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: data)
    client?.urlProtocolDidFinishLoading(self)
  }

  private static func bodyData(_ request: URLRequest) -> Data {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { return Data() }
    if stream.streamStatus == .notOpen { stream.open() }
    defer { stream.close() }
    var data = Data()
    var buffer = [UInt8](repeating: 0, count: 4096)
    while true {
      let read = stream.read(&buffer, maxLength: buffer.count)
      if read <= 0 { break }
      data.append(buffer, count: read)
    }
    return data
  }
}

// MARK: - Fixtures and helpers

private struct AgentTestTimeout: Error {}

private func agentTempDirectory() -> URL {
  FileManager.default.temporaryDirectory
    .appendingPathComponent("agent-runtime-\(UUID().uuidString)")
}

private func agentUniqueHost() -> String {
  "agent-\(UUID().uuidString.lowercased()).example"
}

private func agentSource(
  bookID: String, title: String, pageCount: Int = 20,
  chunkCount: Int = 1, updatedAt: Double = 0
) -> SourceContent {
  SourceContent(
    summary: SourceSummary(
      book_id: bookID, title: title, status: "ready", page_count: pageCount,
      chapter_count: 0, chunk_count: chunkCount, asset_count: 0,
      average_confidence: 0, updated_at: updatedAt),
    chapters: [], lessons: [], chunks: [], assets: [], flashcards: [], quizzes: [],
    plan: StudyPlan(
      user_id: "local", book_id: bookID, days: 0, daily_minutes: 0, tasks: []))
}

@MainActor
private func agentMakeStore(
  directory: URL, host: String, userID: String = "owner-a"
) throws -> (AppStore, URLSession) {
  let configuration = URLSessionConfiguration.ephemeral
  configuration.protocolClasses = [AgentURLProtocol.self]
  let session = URLSession(configuration: configuration)
  let api = CloudAPI(baseURL: URL(string: "https://\(host)")!, session: session)
  let account = CloudAccountState(
    account: CloudAccountState.Account(
      email: "\(userID)@example.com", stage: "university", interests: [],
      goal: "interest", is_demo: false),
    user_id: userID, email_available: true, demo_available: false)
  let repository = try LocalBookCourseRepository(directory: directory)
  let store = AppStore(repository: repository, cloud: api)
  store.cloudState.authenticated = true
  store.cloudState.account = account
  store.cloudState.cacheKey = store.cloudCacheKey(account)
  store.agent.pollInterval = .milliseconds(5)
  store.agent.submittingWindow = .milliseconds(100)
  return (store, session)
}

@MainActor
private func agentRegisterCourse(_ store: AppStore) {
  store.courses = [Course(id: "course-agent", name: "测试课程", resourceIDs: store.sources.map(\.id),
    courseResourceIDs: Dictionary(uniqueKeysWithValues: store.sources.map { ($0.id, "resource:" + $0.id) }), activeResourceID: store.sources.first?.id)]
}

@MainActor
private func agentWaitUntil(
  timeout: TimeInterval = 5,
  _ predicate: () async -> Bool
) async throws {
  let deadline = Date().addingTimeInterval(timeout)
  while true {
    if await predicate() { return }
    if Date() >= deadline { throw AgentTestTimeout() }
    try await Task.sleep(for: .milliseconds(10))
  }
}

private func agentJSON(_ data: Data) -> [String: Any] {
  (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
}

/// One-shot async gate for the runtime's commit seam. The first waiter parks
/// until released; later waiters pass straight through.
@MainActor
private final class AgentAsyncGate {
  private var continuation: CheckedContinuation<Void, Never>?
  private var released = false

  var isWaiting: Bool { continuation != nil }

  func wait() async {
    if released { return }
    await withCheckedContinuation { continuation = $0 }
  }

  func release() {
    released = true
    continuation?.resume()
    continuation = nil
  }
}

private func agentJSON(_ request: AgentRecordedRequest) -> [String: Any] {
  agentJSON(request.body)
}

private func agentToolCall(
  id: String, name: String, arguments: [String: Any]
) -> [String: Any] {
  ["id": id, "name": name, "arguments": arguments]
}

private func agentTurnResponse(
  runID: String, stepIndex: Int, state: String,
  content: String = "", toolCalls: [[String: Any]] = [],
  error: String? = nil, refused: String? = nil, retryAfter: Double? = nil
) -> String {
  var object: [String: Any] = [
    "contract_version": 1,
    "run_id": runID,
    "step_id": "\(runID).s\(stepIndex)",
    "step_index": stepIndex,
    "state": state,
    "attempt": 1,
    "budget": [
      "steps": stepIndex + 1, "steps_max": 8,
      "posts": stepIndex + 1, "posts_max": 24,
      "input_tokens": 0, "input_tokens_max": 100_000,
      "output_tokens": 0, "output_tokens_max": 10_000,
      "model_seconds": 0.0, "model_seconds_max": 120.0,
      "cached_input_tokens": 0,
    ],
  ]
  if state == "completed" {
    object["message"] = ["content": content, "tool_calls": toolCalls] as [String: Any]
  }
  if let error { object["error"] = error }
  if let refused { object["refused"] = refused }
  if let retryAfter { object["retry_after"] = retryAfter }
  let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
  return String(decoding: data, as: UTF8.self)
}

/// Builds the response for the run/step carried by the request body, so a
/// scripted POST does not need to know the generated run ID in advance.
private func agentTurnReply(
  _ request: AgentRecordedRequest,
  content: String = "", toolCalls: [[String: Any]] = [],
  state: String = "completed", error: String? = nil,
  refused: String? = nil, retryAfter: Double? = nil
) -> AgentStubReply {
  let json = agentJSON(request)
  let runID = json["run_id"] as? String ?? "run-unknown"
  let stepIndex = json["step_index"] as? Int ?? 0
  return .json(
    status: 200,
    body: agentTurnResponse(
      runID: runID, stepIndex: stepIndex, state: state, content: content,
      toolCalls: toolCalls, error: error, refused: refused, retryAfter: retryAfter))
}

private func agentRepositoryEvidence(
  quote: String = "染色体只复制一次，而细胞分裂两次。"
) -> AgentEvidence {
  AgentEvidence(
    id: "ev-1", bookID: "book-agent", courseID: "course-agent",
    sourceVersion: "src:test", chapterID: "chapter-2", page: 11, printedPage: 16,
    quote: quote, quoteSHA256: AgentDigest.sha256(quote))
}

private func agentRepositoryRun(
  scope: String, id: String = "run-replay", evidence: AgentEvidence,
  toolCallID: String = "call-save"
) -> AgentRunRecord {
  let context = AgentFrozenContext(
    scope: scope, courseID: evidence.courseID, bookID: evidence.bookID,
    bookTitle: "生物学", sourceVersion: evidence.sourceVersion,
    chapterID: evidence.chapterID, page: evidence.page,
    printedPage: evidence.printedPage, selection: evidence.quote,
    currentNoteID: nil, capturedAt: .now, courseResourceID: "resource-agent")
  let call = AgentToolCall(
    id: toolCallID, name: "save_note", argumentsJSON: "{}", argumentsHash: "hash-a")
  var run = AgentRunRecord(
    id: id, scope: scope, status: .running, request: "整理",
    context: context, evidence: [evidence],
    steps: [AgentStep(
      index: 0, id: "\(id).s0", state: .completed, toolCalls: [call])],
    createdAt: .now, updatedAt: .now)
  run.privateTransport = true
  return run
}

private func agentToolNote(
  run: AgentRunRecord, id: String, toolCallID: String = "call-save",
  title: String = "减数分裂", body: String = "整理后的解释"
) -> StudyNote {
  StudyNote(
    id: id, kind: .text, title: title, body: body,
    anchor: NoteAnchor(
      courseID: run.context.courseID, courseResourceID: run.context.courseResourceID, bookID: run.context.bookID,
      chapterID: run.context.chapterID, page: run.context.page,
      printedPage: run.context.printedPage, quote: run.context.selection),
    references: run.evidence.map(NoteReference.init),
    provenance: NoteProvenance(
      runID: run.id, toolCallID: toolCallID, sourceLabel: "textbook"))
}

// MARK: - Tests

final class AgentRuntimeTests: XCTestCase {

  /// Contract: save_note stores executor-made citations from the frozen
  /// selection, open_note opens the real committed ID, and the run completes.
  @MainActor func testSelectionNoteSavesTrustedCitationAndOpensRealNoteID() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-citation"
    let source = agentSource(bookID: bookID, title: "生物学")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let text = "减数分裂时，染色体只复制一次。"
    let selection = AgentSelection(
      bookID: bookID, courseID: "course-agent", chapterID: "chapter-2",
      page: 11, printedPage: 16, text: text)
    let version = AgentRuntime.sourceVersion(for: source.summary)
    let evidence = AgentRuntime.evidence(for: selection, sourceVersion: version)

    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-save", name: "save_note",
            arguments: [
              "title": "减数分裂要点", "body": "染色体只复制一次。",
              "evidence_ids": [evidence.id], "source_label": "textbook",
            ])
        ])
    }
    let gate = AgentGate()
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn", reply: .gated(gate))
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(request, content: "笔记已保存并打开。")
    }

    store.agent.start(
      selection, request: "请整理并解释这段教材原文，保存为笔记后打开。")
    try await agentWaitUntil { gate.isWaiting }
    let noteID = try XCTUnwrap(store.notes.first?.id)
    let runID = try XCTUnwrap(store.agent.active?.id)
    gate.release(
      .json(
        status: 200,
        body: agentTurnResponse(
          runID: runID, stepIndex: 1, state: "completed",
          toolCalls: [
            agentToolCall(
              id: "call-open", name: "open_note",
              arguments: ["note_id": noteID])
          ])))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .completed
    }

    let run = try XCTUnwrap(store.agent.active)
    XCTAssertEqual(run.status, .completed)
    XCTAssertEqual(run.committedNoteIDs, [noteID])
    XCTAssertEqual(store.notes.count, 1)
    XCTAssertEqual(store.noteState.focusNoteID, noteID)
    let persistedRun = try await store.repository.loadRun(run.id, scope: run.scope)
    XCTAssertEqual(persistedRun?.status, .completed)

    let storedNotes = try await store.repository.loadStoredNotes(scope: run.scope)
    XCTAssertEqual(storedNotes.count, 1)
    let stored = try XCTUnwrap(storedNotes.first)
    let reference = try XCTUnwrap(stored.note.references?.first)
    XCTAssertEqual(reference.evidenceID, evidence.id)
    XCTAssertEqual(reference.quote, text)
    XCTAssertEqual(reference.page, 11)
    XCTAssertEqual(reference.printedPage, 16)
    XCTAssertEqual(reference.bookID, bookID)
    let provenance = try XCTUnwrap(stored.note.provenance)
    XCTAssertEqual(provenance.runID, runID)
    XCTAssertEqual(provenance.toolCallID, "call-save")
    XCTAssertEqual(provenance.sourceLabel, "textbook")
    let anchor = try XCTUnwrap(stored.note.anchor)
    XCTAssertEqual(anchor.page, 11)
    XCTAssertEqual(anchor.printedPage, 16)
    XCTAssertEqual(anchor.bookID, bookID)
    XCTAssertEqual(anchor.quote, text)
  }

  /// Contract: the (scope, run, tool call) receipt wins over a fresh note ID,
  /// conflicting arguments are rejected, and a crash after commit replays the
  /// durable outcome instead of saving again.
  @MainActor func testReplayedToolCallReturnsOriginalNoteWithoutDuplicate() async throws {
    let root = agentTempDirectory()
    let repository = try LocalBookCourseRepository(directory: root)
    let evidence = agentRepositoryEvidence()
    let run = agentRepositoryRun(scope: "owner-a", evidence: evidence)
    let saved = try await repository.saveRun(run)
    let first = try await repository.commitToolNote(
      run: saved, toolCallID: "call-save", argumentsHash: "hash-a",
      note: agentToolNote(run: saved, id: "note-first"), result: "{}")
    XCTAssertFalse(first.replayed)
    XCTAssertEqual(first.revision, 1)

    let replay = try await repository.commitToolNote(
      run: saved, toolCallID: "call-save", argumentsHash: "hash-a",
      note: agentToolNote(run: saved, id: "note-second"), result: "{}")
    XCTAssertTrue(replay.replayed)
    XCTAssertEqual(replay.noteID, first.noteID)
    XCTAssertEqual(replay.revision, first.revision)
    let notesAfterReplay = try await repository.loadStoredNotes(scope: "owner-a")
    XCTAssertEqual(notesAfterReplay.map(\.note.id), ["note-first"])

    do {
      _ = try await repository.commitToolNote(
        run: saved, toolCallID: "call-save", argumentsHash: "hash-b",
        note: agentToolNote(run: saved, id: "note-third"), result: "{}")
      XCTFail("Same tool call with different arguments must conflict")
    } catch {
      XCTAssertEqual(
        error as? NoteStoreError,
        NoteStoreError.receiptConflict(toolCallID: "call-save"))
    }

    // Simulate termination right after save_note committed: a new repository
    // and store on the same directory see both the note and the outcome.
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-resume"
    let source = agentSource(bookID: bookID, title: "生物学")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: "course-agent", chapterID: "chapter-2",
      page: 11, printedPage: 16, text: "染色体只复制一次。")
    let version = AgentRuntime.sourceVersion(for: source.summary)
    let liveEvidence = AgentRuntime.evidence(for: selection, sourceVersion: version)
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-save", name: "save_note",
            arguments: [
              "title": "减数分裂", "body": "整理后的解释",
              "evidence_ids": [liveEvidence.id], "source_label": "textbook",
            ])
        ])
    }
    let gate = AgentGate()
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn", reply: .gated(gate))
    store.agent.start(selection, request: "整理")
    try await agentWaitUntil { store.notes.count == 1 && gate.isWaiting }
    let noteID = try XCTUnwrap(store.notes.first?.id)
    let runID = try XCTUnwrap(store.agent.active?.id)
    let scope = try XCTUnwrap(store.noteScope)
    store.agent.suspend()
    try await agentWaitUntil { !store.agent.isWorking }

    let reopened = try LocalBookCourseRepository(directory: root)
    let reopenedStore = AppStore(
      repository: reopened,
      cloud: CloudAPI(baseURL: URL(string: "https://\(host)")!, session: session))
    reopenedStore.sources = [source]
    reopenedStore.selectedBookID = bookID
    reopenedStore.cloudState.authenticated = true
    reopenedStore.cloudState.account = store.cloudState.account
    reopenedStore.cloudState.cacheKey = scope
    reopenedStore.agent.pollInterval = .milliseconds(5)
    reopenedStore.agent.submittingWindow = .milliseconds(100)

    let loadedPosted = try await reopened.loadRun(runID, scope: scope)
    let posted = try XCTUnwrap(loadedPosted)
    XCTAssertEqual(posted.steps.last?.state, .posted)
    XCTAssertEqual(posted.steps.first?.outcome("call-save")?.noteID, noteID)
    let stepID = try XCTUnwrap(posted.steps.last?.id)
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "GET",
      path: "/api/assistant/runs/\(runID)/steps/\(stepID)"
    ) { _ in
      .json(
        status: 200,
        body: agentTurnResponse(
          runID: runID, stepIndex: 1, state: "completed", content: "恢复完成。"))
    }
    let before = AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count
    reopenedStore.agent.resume()
    try await agentWaitUntil {
      !reopenedStore.agent.isWorking && reopenedStore.agent.active?.status == .completed
    }
    let resumed = AgentScriptedRouter.shared.snapshot().filter { $0.host == host }
      .dropFirst(before)
    XCTAssertEqual(resumed.first?.method, "GET")
    XCTAssertFalse(
      resumed.contains { $0.method == "POST" && $0.path == "/api/assistant/turn" })
    let loadedFinalReopened = try await reopened.loadRun(runID, scope: scope)
    let finalRun = try XCTUnwrap(loadedFinalReopened)
    XCTAssertEqual(finalRun.committedNoteIDs, [noteID])
    let storedNotes = try await reopened.loadStoredNotes(scope: scope)
    XCTAssertEqual(storedNotes.map(\.note.id), [noteID])
  }

  /// Contract: a persisted .posted step is reconciled with GET before any
  /// resend, and an uncertain outcome stops the run without another POST.
  @MainActor func testPostedStepIsCheckedBeforeAnyResend() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-posted"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 7,
      printedPage: nil, text: "光合作用发生在叶绿体。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn",
      reply: .failure(URLError(.networkConnectionLost)))
    store.agent.start(selection, request: "整理")
    try await agentWaitUntil { !store.agent.isWorking }

    let runID = try XCTUnwrap(store.agent.active?.id)
    let scope = try XCTUnwrap(store.noteScope)
    let loadedPersisted = try await store.repository.loadRun(runID, scope: scope)
    let persisted = try XCTUnwrap(loadedPersisted)
    XCTAssertEqual(persisted.status, .running)
    XCTAssertEqual(persisted.steps.last?.state, .posted)
    XCTAssertEqual(store.agent.notice, "网络中断，恢复后会先核对原请求，不会重复发送")
    let stepID = try XCTUnwrap(persisted.steps.last?.id)

    AgentScriptedRouter.shared.enqueue(
      host: host, method: "GET",
      path: "/api/assistant/runs/\(runID)/steps/\(stepID)"
    ) { _ in
      .json(
        status: 200,
        body: agentTurnResponse(
          runID: runID, stepIndex: 0, state: "completed", content: "已核对并完成。"))
    }
    let before = AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count
    store.agent.resume()
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .completed
    }
    let resumed = AgentScriptedRouter.shared.snapshot().filter { $0.host == host }
      .dropFirst(before)
    XCTAssertEqual(resumed.first?.method, "GET")
    XCTAssertEqual(resumed.first?.path, "/api/assistant/runs/\(runID)/steps/\(stepID)")
    XCTAssertFalse(
      resumed.contains { $0.method == "POST" && $0.path == "/api/assistant/turn" })

    // Variant: the server reports an unknown outcome; the run stops uncertain
    // and never resends the POST.
    let root2 = agentTempDirectory()
    let host2 = agentUniqueHost()
    let (store2, session2) = try agentMakeStore(directory: root2, host: host2)
    defer { session2.invalidateAndCancel() }
    let source2 = agentSource(bookID: "book-agent-uncertain", title: "教材")
    store2.sources = [source2]
    store2.selectedBookID = source2.id
    let selection2 = AgentSelection(
      bookID: source2.id, courseID: nil, chapterID: nil, page: 3,
      printedPage: nil, text: "细胞膜控制物质进出。")
    AgentScriptedRouter.shared.enqueue(
      host: host2, method: "POST", path: "/api/assistant/turn",
      reply: .failure(URLError(.networkConnectionLost)))
    store2.agent.start(selection2, request: "整理")
    try await agentWaitUntil { !store2.agent.isWorking }
    let runID2 = try XCTUnwrap(store2.agent.active?.id)
    let scope2 = try XCTUnwrap(store2.noteScope)
    let loadedPersisted2 = try await store2.repository.loadRun(runID2, scope: scope2)
    let persisted2 = try XCTUnwrap(loadedPersisted2)
    let stepID2 = try XCTUnwrap(persisted2.steps.last?.id)
    AgentScriptedRouter.shared.enqueue(
      host: host2, method: "GET",
      path: "/api/assistant/runs/\(runID2)/steps/\(stepID2)"
    ) { _ in
      .json(
        status: 200,
        body: agentTurnResponse(runID: runID2, stepIndex: 0, state: "uncertain"))
    }
    let before2 = AgentScriptedRouter.shared.snapshot().filter { $0.host == host2 }.count
    store2.agent.resume()
    try await agentWaitUntil {
      if store2.agent.isWorking { return false }
      let loaded = try? await store2.repository.loadRun(runID2, scope: scope2)
      return loaded?.status == .uncertain
    }
    let resumed2 = AgentScriptedRouter.shared.snapshot().filter { $0.host == host2 }
      .dropFirst(before2)
    XCTAssertFalse(
      resumed2.contains { $0.method == "POST" && $0.path == "/api/assistant/turn" })
    let loadedFinal2 = try await store2.repository.loadRun(runID2, scope: scope2)
    let final2 = try XCTUnwrap(loadedFinal2)
    XCTAssertEqual(final2.status, .uncertain)
    XCTAssertEqual(final2.steps.last?.state, .uncertain)
  }

  /// Contract: cancel is sticky, keeps already committed notes, and no later
  /// tool in the same step runs after the cancel checkpoint.
  @MainActor func testCancelStopsLaterToolsAndKeepsCommittedNote() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-cancel"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: "course-agent", chapterID: nil, page: 5,
      printedPage: nil, text: "细胞分裂使细胞数目增多。")
    let version = AgentRuntime.sourceVersion(for: source.summary)
    let evidence = AgentRuntime.evidence(for: selection, sourceVersion: version)
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-first", name: "save_note",
            arguments: [
              "title": "细胞分裂", "body": "细胞数目增多。",
              "evidence_ids": [evidence.id], "source_label": "textbook",
            ]),
          agentToolCall(
            id: "call-ask", name: "ask_user",
            arguments: [
              "question": "还需要继续整理吗？", "options": ["继续", "停止"],
            ]),
          agentToolCall(
            id: "call-late", name: "save_note",
            arguments: [
              "title": "不该保存", "body": "取消之后不应执行。",
              "evidence_ids": [evidence.id], "source_label": "textbook",
            ]),
        ])
    }

    store.agent.start(selection, request: "整理")
    // ask_user pauses the step after the first commit, making the cancel
    // boundary deterministic: the third tool has not run yet.
    try await agentWaitUntil {
      store.notes.count == 1 && store.agent.active?.status == .waitingForUser
    }
    let noteID = try XCTUnwrap(store.notes.first?.id)
    let runID = try XCTUnwrap(store.agent.active?.id)
    let scope = try XCTUnwrap(store.noteScope)
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST",
      path: "/api/assistant/runs/\(runID)/cancel",
      reply: .json(status: 200, body: "{}"))
    store.agent.cancel()
    try await agentWaitUntil { store.agent.active?.status == .cancelled }
    try await agentWaitUntil {
      AgentScriptedRouter.shared.snapshot().contains {
        $0.host == host && $0.method == "POST"
          && $0.path == "/api/assistant/runs/\(runID)/cancel"
      }
    }

    let loadedFinalRun = try await store.repository.loadRun(runID, scope: scope)
    let finalRun = try XCTUnwrap(loadedFinalRun)
    XCTAssertEqual(finalRun.status, .cancelled)
    XCTAssertEqual(finalRun.committedNoteIDs, [noteID])
    XCTAssertNil(finalRun.steps[0].outcome("call-late"))
    XCTAssertEqual(store.notes.map(\.id), [noteID])
    let storedNotes = try await store.repository.loadStoredNotes(scope: scope)
    XCTAssertEqual(storedNotes.map(\.note.id), [noteID])
  }

  /// Contract: the run context and citations are frozen at start; later page or
  /// book switches never leak into the wire request or the saved anchor.
  @MainActor func testPageSwitchDoesNotChangeFrozenContext() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-frozen"
    let source = agentSource(bookID: bookID, title: "原始教材")
    store.sources = [source, agentSource(bookID: "book-other", title: "另一本教材")]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    store.selectedChapterID = "chapter-2"
    let text = "有丝分裂间期完成DNA复制。"
    let selection = AgentSelection(
      bookID: bookID, courseID: "course-agent", chapterID: "chapter-2",
      page: 11, printedPage: 16, text: text)
    let version = AgentRuntime.sourceVersion(for: source.summary)
    let evidence = AgentRuntime.evidence(for: selection, sourceVersion: version)

    let gate = AgentGate()
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn", reply: .gated(gate))
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(request, content: "已整理。")
    }

    store.agent.start(selection, request: "整理")
    try await agentWaitUntil { gate.isWaiting }

    store.selectedBookID = "book-other"
    store.selectedChapterID = "chapter-9"
    store.selectedLessonID = "lesson-9"
    store.selectedQuote = "另一个页面上的选中文字"
    store.sourceTarget = SourcePageTarget(
      bookID: "book-other", title: "另一本教材", page: 99, printedPage: 88)

    let runID = try XCTUnwrap(store.agent.active?.id)
    gate.release(
      .json(
        status: 200,
        body: agentTurnResponse(
          runID: runID, stepIndex: 0, state: "completed",
          toolCalls: [
            agentToolCall(
              id: "call-save", name: "save_note",
              arguments: [
                "title": "有丝分裂", "body": "间期完成DNA复制。",
                "evidence_ids": [evidence.id], "source_label": "textbook",
              ])
          ])))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .completed
    }

    let requests = AgentScriptedRouter.shared.snapshot().filter {
      $0.host == host && $0.method == "POST" && $0.path == "/api/assistant/turn"
    }
    XCTAssertEqual(requests.count, 2)
    for request in requests {
      let json = agentJSON(request)
      let context = try XCTUnwrap(json["context"] as? [String: Any])
      XCTAssertEqual(context["book_id"] as? String, bookID)
      XCTAssertEqual(context["page"] as? Int, 11)
      XCTAssertEqual(context["printed_page"] as? Int, 16)
      XCTAssertEqual(context["selection"] as? String, text)
    }

    let run = try XCTUnwrap(store.agent.active)
    let storedNotes = try await store.repository.loadStoredNotes(scope: run.scope)
    let stored = try XCTUnwrap(storedNotes.first)
    let anchor = try XCTUnwrap(stored.note.anchor)
    XCTAssertEqual(anchor.bookID, bookID)
    XCTAssertEqual(anchor.page, 11)
    XCTAssertEqual(anchor.printedPage, 16)
    XCTAssertEqual(anchor.quote, text)
    let reference = try XCTUnwrap(stored.note.references?.first)
    XCTAssertEqual(reference.page, 11)
    XCTAssertEqual(reference.quote, text)
  }

  /// Contract: runs are stored under the account cache key; switching accounts
  /// hides A's run, finds no B runs, and issues no request for A's run.
  @MainActor func testRunsAreScopedToAccount() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host, userID: "owner-a")
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-account"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 4,
      printedPage: nil, text: "细胞分化形成不同组织。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn",
      reply: .failure(URLError(.networkConnectionLost)))
    store.agent.start(selection, request: "整理")
    try await agentWaitUntil { !store.agent.isWorking }

    let runID = try XCTUnwrap(store.agent.active?.id)
    let scopeA = try XCTUnwrap(store.noteScope)
    let loadedA = try await store.repository.loadRun(runID, scope: scopeA)
    let persisted = try XCTUnwrap(loadedA)
    XCTAssertEqual(persisted.steps.last?.state, .posted)

    var accountB = try XCTUnwrap(store.cloudState.account)
    accountB.user_id = "owner-b"
    store.cloudState.account = accountB
    store.cloudState.cacheKey = store.cloudCacheKey(accountB)
    let scopeB = try XCTUnwrap(store.noteScope)
    XCTAssertNotEqual(scopeA, scopeB)

    let before = AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count
    store.agent.resume()
    try await agentWaitUntil { !store.agent.isWorking }
    XCTAssertNil(store.agent.active)
    let runsB = try await store.repository.loadRuns(scope: scopeB)
    XCTAssertTrue(runsB.isEmpty)
    XCTAssertEqual(
      AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count, before)
    let runsA = try await store.repository.loadRuns(scope: scopeA)
    XCTAssertEqual(runsA.map(\.id), [runID])
  }

  /// Revision conflicts are covered at the repository/store level already
  /// (NoteMutationStoreTests); this covers the runtime ask_user pause, choose,
  /// and the selected option flowing back in the next request's tool_results.
  @MainActor func testAskUserChoiceIsPersistedInNextToolResults() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-ask"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 6,
      printedPage: nil, text: "酶能降低化学反应的活化能。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-ask", name: "ask_user",
            arguments: [
              "question": "需要多详细？", "options": ["简短", "详细"],
            ])
        ])
    }
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(request, content: "已按详细程度整理。")
    }

    store.agent.start(selection, request: "整理")
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .waitingForUser
    }
    let waiting = try XCTUnwrap(store.agent.active)
    XCTAssertEqual(waiting.pendingChoice?.options, ["简短", "详细"])
    XCTAssertEqual(waiting.pendingChoice?.question, "需要多详细？")

    store.agent.choose("详细")
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .completed
    }

    let requests = AgentScriptedRouter.shared.snapshot().filter {
      $0.host == host && $0.method == "POST" && $0.path == "/api/assistant/turn"
    }
    XCTAssertEqual(requests.count, 2)
    let second = agentJSON(requests[1])
    let results = try XCTUnwrap(second["tool_results"] as? [[String: Any]])
    let result = try XCTUnwrap(
      results.first { $0["tool_call_id"] as? String == "call-ask" })
    let content = try XCTUnwrap(result["content"] as? String)
    let payload = agentJSON(Data(content.utf8))
    XCTAssertEqual(payload["status"] as? String, "answered")
    XCTAssertEqual(payload["selected"] as? String, "详细")

    let run = try XCTUnwrap(store.agent.active)
    XCTAssertEqual(run.steps[0].outcome("call-ask")?.status, "ok")
    XCTAssertNil(run.pendingChoice)
  }

  /// Contract: unregistered evidence is an error outcome, no note is written,
  /// the error is sent back in the next turn's tool_results, and the run goes on.
  @MainActor func testInvalidSaveNoteArgumentsBecomeErrorOutcome() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-invalid"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: "course-agent", chapterID: nil, page: 8,
      printedPage: nil, text: "DNA聚合酶沿着模板链合成新链。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-bad", name: "save_note",
            arguments: [
              "title": "伪造引用", "body": "这条引用没有登记。",
              "evidence_ids": ["ev-forged"], "source_label": "textbook",
            ])
        ])
    }
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(request, content: "引用无效，已停止保存。")
    }

    store.agent.start(selection, request: "整理")
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .completed
    }

    let run = try XCTUnwrap(store.agent.active)
    let outcome = try XCTUnwrap(run.steps[0].outcome("call-bad"))
    XCTAssertEqual(outcome.status, "error")
    XCTAssertNil(outcome.noteID)
    let storedNotes = try await store.repository.loadStoredNotes(scope: run.scope)
    XCTAssertTrue(storedNotes.isEmpty)
    XCTAssertTrue(store.notes.isEmpty)

    let requests = AgentScriptedRouter.shared.snapshot().filter {
      $0.host == host && $0.method == "POST" && $0.path == "/api/assistant/turn"
    }
    XCTAssertEqual(requests.count, 2)
    let second = agentJSON(requests[1])
    let results = try XCTUnwrap(second["tool_results"] as? [[String: Any]])
    let result = try XCTUnwrap(
      results.first { $0["tool_call_id"] as? String == "call-bad" })
    let content = try XCTUnwrap(result["content"] as? String)
    let payload = agentJSON(Data(content.utf8))
    XCTAssertEqual(payload["status"] as? String, "error")
    XCTAssertTrue(
      (payload["message"] as? String)?.contains("引用不是本机登记的原文证据") == true)
  }

  /// Contract: a retryable response carrying a refusal ends the run immediately
  /// and never sleeps or re-POSTs.
  @MainActor func testRefusedRetryableFailsWithoutResend() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-refused"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 9,
      printedPage: nil, text: "细胞呼吸释放能量。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(request, state: "retryable", refused: "posts")
    }

    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .failed
    }
    let run = try XCTUnwrap(store.agent.active)
    XCTAssertEqual(run.status, .failed)
    XCTAssertEqual(run.failure, "API 错误：posts")
    let posts = AgentScriptedRouter.shared.snapshot().filter {
      $0.host == host && $0.method == "POST" && $0.path == "/api/assistant/turn"
    }
    XCTAssertEqual(posts.count, 1)
  }

  /// Contract: start refuses while a drive is working or while a non-terminal
  /// run is waiting, without persisting a second run.
  @MainActor func testStartRefusesExistingNonTerminalRun() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-blocked"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 10,
      printedPage: nil, text: "基因表达包括转录和翻译。")
    let gate = AgentGate()
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn", reply: .gated(gate))

    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil { gate.isWaiting }
    XCTAssertFalse(store.agent.start(selection, request: "整理"))
    XCTAssertEqual(store.agent.notice, "已有助手任务正在进行，请先完成或取消")
    let runID = try XCTUnwrap(store.agent.active?.id)
    let scope = try XCTUnwrap(store.noteScope)

    gate.release(
      .json(
        status: 200,
        body: agentTurnResponse(
          runID: runID, stepIndex: 0, state: "completed",
          toolCalls: [
            agentToolCall(
              id: "call-ask", name: "ask_user",
              arguments: ["question": "需要多详细？", "options": ["简短", "详细"]])
          ])))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .waitingForUser
    }
    let runsBefore = try await store.repository.loadRuns(scope: scope)
    XCTAssertFalse(store.agent.start(selection, request: "整理"))
    XCTAssertEqual(store.agent.notice, "请先继续或取消当前助手任务")
    XCTAssertEqual(store.agent.active?.id, runID)
    let runsAfter = try await store.repository.loadRuns(scope: scope)
    XCTAssertEqual(runsAfter.map(\.id), runsBefore.map(\.id))
  }

  /// Contract: reset drops in-memory state, and cancel/choose never write a run
  /// that belongs to another account scope.
  @MainActor func testResetAndScopeSwitchBlockStaleWrites() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host, userID: "owner-a")
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-reset"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 12,
      printedPage: nil, text: "生态系统的能量流动是单向的。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-ask", name: "ask_user",
            arguments: ["question": "继续吗？", "options": ["继续", "停止"]])
        ])
    }
    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .waitingForUser
    }
    let runID = try XCTUnwrap(store.agent.active?.id)
    let scopeA = try XCTUnwrap(store.noteScope)
    let beforeRequests = AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count

    // Switch the account without reset: only the scope guard can stop a write.
    var accountB = try XCTUnwrap(store.cloudState.account)
    accountB.user_id = "owner-b"
    store.cloudState.account = accountB
    store.cloudState.cacheKey = store.cloudCacheKey(accountB)
    store.agent.cancel()
    store.agent.choose("继续")
    try await agentWaitUntil { !store.agent.isWorking }
    let loadedUnchanged = try await store.repository.loadRun(runID, scope: scopeA)
    let unchanged = try XCTUnwrap(loadedUnchanged)
    XCTAssertEqual(unchanged.status, .waitingForUser)
    XCTAssertFalse(unchanged.cancelRequested)
    XCTAssertNil(unchanged.steps[0].outcome("call-ask"))
    XCTAssertEqual(
      AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count,
      beforeRequests)

    store.agent.reset()
    XCTAssertNil(store.agent.active)
    XCTAssertNil(store.agent.notice)
    store.agent.cancel()
    store.agent.choose("继续")
    try await agentWaitUntil { !store.agent.isWorking }
    let loadedStillUnchanged = try await store.repository.loadRun(runID, scope: scopeA)
    let stillUnchanged = try XCTUnwrap(loadedStillUnchanged)
    XCTAssertEqual(stillUnchanged.status, .waitingForUser)
    XCTAssertFalse(stillUnchanged.cancelRequested)
    XCTAssertEqual(
      AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count,
      beforeRequests)
  }

  /// Contract: a reset that lands while the first checkpoint is still in flight
  /// must not resurrect the run in the overlay.
  @MainActor func testResetDuringInFlightCheckpointLeavesNoActiveRun() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-reset-checkpoint"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 17,
      printedPage: nil, text: "遗传信息从DNA流向RNA。")
    let scope = try XCTUnwrap(store.noteScope)

    // A gated POST keeps the drive in flight while the reset lands.
    let gate = AgentGate()
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn", reply: .gated(gate))
    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil { gate.isWaiting }
    let firstRunID = try XCTUnwrap(store.agent.active?.id)
    store.agent.reset()
    gate.release(
      .json(
        status: 200,
        body: agentTurnResponse(
          runID: firstRunID, stepIndex: 0, state: "completed", content: "不应写回。")))
    try await Task.sleep(for: .milliseconds(50))
    XCTAssertNil(store.agent.active)

    // Reset before the scheduled checkpoint returns: the stale save must not
    // publish the run after the reset.
    let runsBefore = try await store.repository.loadRuns(scope: scope)
    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    store.agent.reset()
    try await agentWaitUntil {
      let runs = (try? await store.repository.loadRuns(scope: scope)) ?? []
      return runs.count > runsBefore.count
    }
    try await Task.sleep(for: .milliseconds(50))
    XCTAssertNil(store.agent.active)
    XCTAssertNil(store.agent.notice)
    XCTAssertFalse(store.agent.isWorking)
  }

  /// Contract: cancelling after another owner completed the run leaves the
  /// completed record untouched.
  @MainActor func testCancelDoesNotOverwriteConcurrentTerminalRun() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-cancel-race"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 13,
      printedPage: nil, text: "光合作用把光能转变成化学能。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-ask", name: "ask_user",
            arguments: ["question": "继续吗？", "options": ["继续", "停止"]])
        ])
    }
    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .waitingForUser
    }
    let runID = try XCTUnwrap(store.agent.active?.id)
    let scope = try XCTUnwrap(store.noteScope)
    let loadedCompleted = try await store.repository.loadRun(runID, scope: scope)
    var completed = try XCTUnwrap(loadedCompleted)
    completed.status = .completed
    completed.pendingChoice = nil
    let terminal = try await store.repository.saveRun(completed)
    let beforeRequests = AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count

    store.agent.cancel()
    try await agentWaitUntil { store.agent.active?.status == .completed }
    let loadedReloaded = try await store.repository.loadRun(runID, scope: scope)
    let reloaded = try XCTUnwrap(loadedReloaded)
    XCTAssertEqual(reloaded.status, .completed)
    XCTAssertFalse(reloaded.cancelRequested)
    XCTAssertEqual(reloaded.version, terminal.version)
    XCTAssertEqual(
      AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.count,
      beforeRequests)
  }

  /// Contract: a cancel saved while the drive is suspended is still published
  /// after resume and does not leave a non-terminal run blocking a new start.
  @MainActor func testCancelDuringInFlightSaveSurvivesSuspendAndResume() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-cancel-suspend"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 15,
      printedPage: nil, text: "细胞分化形成不同组织。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-ask", name: "ask_user",
            arguments: ["question": "继续吗？", "options": ["继续", "停止"]])
        ])
    }
    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .waitingForUser
    }
    let runID = try XCTUnwrap(store.agent.active?.id)
    let scope = try XCTUnwrap(store.noteScope)
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST",
      path: "/api/assistant/runs/\(runID)/cancel",
      reply: .json(status: 200, body: "{}"))

    store.agent.cancel()
    store.agent.suspend()
    try await agentWaitUntil { store.agent.active?.status == .cancelled }
    try await agentWaitUntil {
      AgentScriptedRouter.shared.snapshot().contains {
        $0.host == host && $0.method == "POST"
          && $0.path == "/api/assistant/runs/\(runID)/cancel"
      }
    }
    try await Task.sleep(for: .milliseconds(30))
    store.agent.resume()
    try await agentWaitUntil { !store.agent.isWorking }

    let loaded = try await store.repository.loadRun(runID, scope: scope)
    let stored = try XCTUnwrap(loaded)
    XCTAssertEqual(stored.status, .cancelled)
    XCTAssertEqual(store.agent.active?.id, runID)
    XCTAssertEqual(store.agent.active?.status, stored.status)
    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    store.agent.reset()
  }

  /// Contract: a save_note commit that returns after suspend()/resume() started
  /// a newer drive must not publish its stale record over the newer run.
  @MainActor func testCancelledDriveDoesNotPublishAfterSuspendAndResume() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-stale-publish"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let text = "减数分裂时，染色体只复制一次。"
    let selection = AgentSelection(
      bookID: bookID, courseID: "course-agent", chapterID: "chapter-2",
      page: 11, printedPage: 16, text: text)
    let version = AgentRuntime.sourceVersion(for: source.summary)
    let evidence = AgentRuntime.evidence(for: selection, sourceVersion: version)

    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-save", name: "save_note",
            arguments: [
              "title": "减数分裂", "body": "染色体只复制一次。",
              "evidence_ids": [evidence.id], "source_label": "textbook",
            ])
        ])
    }
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(request, content: "已完成。")
    }

    // Park the old drive after its durable commit but before it publishes.
    let commitGate = AgentAsyncGate()
    store.agent.afterCommitToolNote = { await commitGate.wait() }

    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil { commitGate.isWaiting }
    let runID = try XCTUnwrap(store.agent.active?.id)

    // suspend() cancels the parked drive; resume() starts a new drive that
    // finishes the same run. Releasing the old drive afterwards must not
    // overwrite the completed record with its stale in-flight snapshot.
    store.agent.suspend()
    try await agentWaitUntil { !store.agent.isWorking }
    store.agent.resume()
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .completed
    }
    let completed = try XCTUnwrap(store.agent.active)

    commitGate.release()
    try await Task.sleep(for: .milliseconds(50))

    let active = try XCTUnwrap(store.agent.active)
    XCTAssertEqual(active.id, runID)
    XCTAssertEqual(active.status, .completed)
    XCTAssertEqual(active.version, completed.version)
    XCTAssertFalse(active.steps.last?.hasPendingTools ?? true)
  }

  /// Contract: resume shows the stored terminal row instead of a stale
  /// non-terminal active run that another owner already finished.
  @MainActor func testResumeShowsRunCancelledByAnotherOwner() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-cancel-elsewhere"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 16,
      printedPage: nil, text: "组织由形态相似的细胞构成。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-ask", name: "ask_user",
            arguments: ["question": "继续吗？", "options": ["继续", "停止"]])
        ])
    }
    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .waitingForUser
    }
    let runID = try XCTUnwrap(store.agent.active?.id)
    let scope = try XCTUnwrap(store.noteScope)
    let loaded = try await store.repository.loadRun(runID, scope: scope)
    var cancelled = try XCTUnwrap(loaded)
    cancelled.status = .cancelled
    cancelled.cancelRequested = true
    _ = try await store.repository.saveRun(cancelled)

    store.agent.resume()
    try await agentWaitUntil { store.agent.active?.status == .cancelled }
    try await agentWaitUntil { !store.agent.isWorking }
    XCTAssertEqual(store.agent.active?.id, runID)
    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    store.agent.reset()
  }

  /// Contract: a general label that cites the textbook is normalized to mixed.
  @MainActor func testGeneralLabelWithEvidenceBecomesMixed() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-mixed"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: "course-agent", chapterID: nil, page: 14,
      printedPage: nil, text: "染色体由DNA和蛋白质组成。")
    let version = AgentRuntime.sourceVersion(for: source.summary)
    let evidence = AgentRuntime.evidence(for: selection, sourceVersion: version)
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-save", name: "save_note",
            arguments: [
              "title": "染色体", "body": "由DNA和蛋白质组成。",
              "evidence_ids": [evidence.id], "source_label": "general",
            ])
        ])
    }
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(request, content: "已保存。")
    }

    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .completed
    }
    let scope = try XCTUnwrap(store.noteScope)
    let storedNotes = try await store.repository.loadStoredNotes(scope: scope)
    let stored = try XCTUnwrap(storedNotes.first)
    XCTAssertEqual(stored.note.provenance?.sourceLabel, "mixed")
    XCTAssertEqual(stored.note.references?.map(\.evidenceID) ?? [], [evidence.id])
  }

  /// Contract: a general label without evidence saves a note with no references.
  @MainActor func testGeneralLabelWithoutEvidenceSavesNoReferences() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-general"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    let selection = AgentSelection(
      bookID: bookID, courseID: "course-agent", chapterID: nil, page: 15,
      printedPage: nil, text: "细胞学说认为细胞是生命的基本单位。")
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(
        request,
        toolCalls: [
          agentToolCall(
            id: "call-save", name: "save_note",
            arguments: [
              "title": "细胞学说", "body": "细胞是生命的基本单位。",
              "evidence_ids": [], "source_label": "general",
            ])
        ])
    }
    AgentScriptedRouter.shared.enqueue(
      host: host, method: "POST", path: "/api/assistant/turn"
    ) { request in
      agentTurnReply(request, content: "已保存。")
    }

    XCTAssertTrue(store.agent.start(selection, request: "整理"))
    try await agentWaitUntil {
      !store.agent.isWorking && store.agent.active?.status == .completed
    }
    let scope = try XCTUnwrap(store.noteScope)
    let storedNotes = try await store.repository.loadStoredNotes(scope: scope)
    let stored = try XCTUnwrap(storedNotes.first)
    XCTAssertEqual(stored.note.provenance?.sourceLabel, "general")
    XCTAssertNil(stored.note.references)
  }

  /// Contract: selection and request limits count Unicode scalars, not graphemes.
  @MainActor func testLengthLimitsUseUnicodeScalars() async throws {
    let root = agentTempDirectory()
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    let bookID = "book-agent-limits"
    let source = agentSource(bookID: bookID, title: "教材")
    store.sources = [source]
    agentRegisterCourse(store)
    store.selectedBookID = bookID
    // 2001 grapheme clusters but 4002 Unicode scalars.
    let longSelectionText = String(repeating: "e\u{0301}", count: 2001)
    XCTAssertEqual(longSelectionText.count, 2001)
    XCTAssertEqual(longSelectionText.unicodeScalars.count, 4002)
    let selection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 16,
      printedPage: nil, text: longSelectionText)
    XCTAssertFalse(store.agent.start(selection, request: "整理"))
    XCTAssertEqual(store.agent.notice, "请选择 1 到 4000 字的教材原文")
    XCTAssertNil(store.agent.active)

    let validSelection = AgentSelection(
      bookID: bookID, courseID: nil, chapterID: nil, page: 16,
      printedPage: nil, text: "教材原文")
    // 1001 grapheme clusters but 2002 Unicode scalars.
    let longRequest = String(repeating: "e\u{0301}", count: 1001)
    XCTAssertEqual(longRequest.count, 1001)
    XCTAssertEqual(longRequest.unicodeScalars.count, 2002)
    XCTAssertFalse(store.agent.start(validSelection, request: longRequest))
    XCTAssertEqual(store.agent.notice, "请输入 1 到 2000 字的整理要求")
    XCTAssertNil(store.agent.active)
  }
}

extension AgentRuntimeTests {
  @MainActor func testUnifiedHomeRunCanExecuteBeyondOldTwelveSubmissionLimit() async throws {
    let root = agentTempDirectory(), host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    for index in 0..<14 {
      AgentScriptedRouter.shared.enqueue(host: host, method: "POST", path: "/api/assistant/turn") { request in
        agentTurnReply(request, toolCalls: [agentToolCall(id: "list-\(index)", name: "list_courses", arguments: [:])])
      }
    }
    AgentScriptedRouter.shared.enqueue(host: host, method: "POST", path: "/api/assistant/turn") { request in
      let body = agentJSON(request)
      XCTAssertEqual(body["contract_version"] as? Int, 3)
      XCTAssertEqual(body["session_id"] as? String, "session-long")
      XCTAssertEqual(body["message_id"] as? String, "message-long")
      return agentTurnReply(request, content: "已读取全部课程。")
    }
    let context = AgentFrozenContext(scope: store.noteScope!, courseID: nil, bookID: "", bookTitle: "", sourceVersion: "",
      chapterID: nil, page: 1, printedPage: nil, selection: "", currentNoteID: nil, capturedAt: .now)
    let answer = try await store.agent.answerUnified(sessionID: "session-long", messageID: "message-long",
      request: "查看全部课程", context: context, history: [], attachments: [], ephemeral: false)
    XCTAssertEqual(answer.text, "已读取全部课程。")
    XCTAssertEqual(store.agent.active?.steps.count, 15)
  }

  func testUnifiedWireKeepsCompleteToolResultsAndCurrentSessionIdentity() throws {
    var run = agentRepositoryRun(scope: "owner", evidence: agentRepositoryEvidence())
    run.context.sessionID = "current-session"; run.context.messageID = "current-message"
    let fullText = String(repeating: "完整资料", count: 3000)
    run.steps[0].outcomes = [AgentToolOutcome(toolCallID: "call-save", status: "ok", resultJSON: fullText)]
    let body = AgentRuntime.turnRequest(for: run, stepIndex: 1)
    XCTAssertEqual(body.tool_results.first?.content, fullText)
    XCTAssertEqual(body.contract_version, 3)
    XCTAssertEqual(body.session_id, "current-session")
    XCTAssertTrue(body.tools.contains("capture_screen"))
    XCTAssertNil(body.history)
  }

  @MainActor func testUnifiedCatalogPaginationDoesNotDropSameNameCourses() async throws {
    let repository = try LocalBookCourseRepository(directory: agentTempDirectory())
    let store = AppStore(repository: repository)
    store.courses = (0..<107).map { Course(id: "course-\($0)", name: "同名课程", resourceIDs: []) }
    var run = agentRepositoryRun(scope: store.noteScope!, evidence: agentRepositoryEvidence())
    let call = AgentToolCall(id: "list", name: "list_courses", argumentsJSON: "{\"offset\":100,\"limit\":100}", argumentsHash: "hash")
    let text = try await UnifiedAgentTools.execute(call, run: &run, store: store)
    let result = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])
    XCTAssertEqual(result["total"] as? Int, 107)
    XCTAssertEqual((result["items"] as? [[String: Any]])?.count, 7)
    XCTAssertEqual(result["complete"] as? Bool, true)
  }
}

extension AgentRuntimeTests {
  @MainActor func testUnifiedOneMillionContextSkipsNewActions() async throws {
    let root = agentTempDirectory(), host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    AgentScriptedRouter.shared.enqueue(host: host, method: "POST", path: "/api/assistant/turn") { request in
      let requestBody = agentJSON(request)
      let response = agentTurnResponse(runID: requestBody["run_id"] as! String, stepIndex: 0, state: "completed", toolCalls: [
        agentToolCall(id: "forbidden-write", name: "save_note", arguments: ["title": "不能执行", "body": "不能写入", "evidence_ids": [], "source_label": "general"])
      ])
      var object = agentJSON(Data(response.utf8)); object["context_tokens"] = 1_000_000; object["capacity_state"] = "archived"
      return .json(status: 200, body: (try? AgentDigest.canonicalJSON(object)) ?? "{}")
    }
    let context = AgentFrozenContext(scope: store.noteScope!, courseID: nil, bookID: "", bookTitle: "", sourceVersion: "",
      chapterID: nil, page: 1, printedPage: nil, selection: "", currentNoteID: nil, capturedAt: .now)
    do {
      _ = try await store.agent.answerUnified(sessionID: "session-full", messageID: "message-full", request: "保存笔记",
        context: context, history: [], attachments: [], ephemeral: false)
      XCTFail("A full context must stop before tool execution")
    } catch { }
    XCTAssertTrue(store.notes.isEmpty)
    XCTAssertEqual(store.agent.active?.steps[0].outcomes.first?.status, "skipped")
    XCTAssertEqual(store.agent.active?.capacityState, "archived")
  }

  @MainActor func testUnifiedFinishingTaskKeepsItsFinalAnswerWhenArchived() async throws {
    let root = agentTempDirectory(), host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: root, host: host)
    defer { session.invalidateAndCancel() }
    AgentScriptedRouter.shared.enqueue(host: host, method: "POST", path: "/api/assistant/turn") { request in
      let requestBody = agentJSON(request)
      let response = agentTurnResponse(runID: requestBody["run_id"] as! String, stepIndex: 0, state: "completed", content: "收尾结果保留。")
      var object = agentJSON(Data(response.utf8)); object["context_tokens"] = 900_000; object["capacity_state"] = "archived"
      return .json(status: 200, body: (try? AgentDigest.canonicalJSON(object)) ?? "{}")
    }
    let context = AgentFrozenContext(scope: store.noteScope!, courseID: nil, bookID: "", bookTitle: "", sourceVersion: "",
      chapterID: nil, page: 1, printedPage: nil, selection: "", currentNoteID: nil, capturedAt: .now)
    let answer = try await store.agent.answerUnified(sessionID: "session-finish", messageID: "message-finish", request: "完成",
      context: context, history: [], attachments: [], ephemeral: false)
    XCTAssertEqual(answer.text, "收尾结果保留。")
    XCTAssertEqual(answer.capacityState, "archived")
    XCTAssertEqual(store.agent.active?.status, .completed)
  }
}

extension AgentRuntimeTests {
  @MainActor func testOrdinaryChatReadsSubsectionFromParentIndexAndScopesPageAssets() async throws {
    let store = AppStore(repository: try LocalBookCourseRepository(directory: agentTempDirectory()))
    let book = try XCTUnwrap(ContentBundle.load([SourceContent].self, name: "sources").first { $0.id == "book_biology_2" })
    store.sources = [book]
    agentRegisterCourse(store)
    store.selectedBookID = book.id
    store.selectedCourseID = "course-agent"
    store.selectedLessonID = book.lessons.first?.id
    XCTAssertEqual(store.activeLesson?.page_start, 0)
    XCTAssertEqual(store.assistantSessionContext.page, 1)
    var oldSnapshot = store.assistantSessionContext
    oldSnapshot.page = 0
    XCTAssertEqual(store.agent.frozenContext(session: oldSnapshot).page, 1)

    var run = agentRepositoryRun(scope: store.noteScope!, evidence: agentRepositoryEvidence())
    func read(_ filter: [String: Any]) async throws -> [String: Any] {
      let args = filter.merging(["course_id": "course-agent", "book_id": book.id]) { existing, _ in existing }
      let call = AgentToolCall(id: "read", name: "read_material", argumentsJSON: try AgentDigest.canonicalJSON(args), argumentsHash: "")
      let result = try await UnifiedAgentTools.execute(call, run: &run, store: store)
      return agentJSON(Data(result.utf8))
    }
    let subsection = try await read(["chapter_id": "c2s1a"])
    let passages = try XCTUnwrap(subsection["items"] as? [[String: Any]])
    XCTAssertEqual(passages.compactMap { $0["page_start"] as? Int }, [11, 13])
    let singlePage = try await read(["page": 13])
    let assets = try XCTUnwrap(singlePage["assets"] as? [[String: Any]])
    XCTAssertFalse(assets.isEmpty)
    XCTAssertEqual(assets.compactMap { $0["asset_id"] as? String }, book.assets.filter { $0.page == 13 }.map(\.id))
    XCTAssertTrue(assets.allSatisfy { $0["page"] as? Int == 13 && $0["metadata"] == nil })
  }
}

extension AgentRuntimeTests {
  @MainActor func testPreparationProgressUsesTaskEvenWhenCatalogIsEmpty() async throws {
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: agentTempDirectory(), host: host)
    defer { session.invalidateAndCancel() }
    store.sources = [agentSource(bookID: "book-test", title: "测试教材")]
    agentRegisterCourse(store)
    let path = "/api/books/book-test/prepare"
    AgentScriptedRouter.shared.enqueue(host: host, method: "GET", path: path) { _ in
      .json(status: 200, body: #"{"book_id":"book-test","status":"running","stage":"diagnostics","updated":1,"next_step":2,"diagnostics_completed":5,"diagnostics_total":12}"#)
    }
    AgentScriptedRouter.shared.enqueue(host: host, method: "GET", path: path) { _ in
      .json(status: 403, body: "{}")
    }
    var run = agentRepositoryRun(scope: store.noteScope!, evidence: agentRepositoryEvidence())
    run.privateTransport = nil // Legacy tool remains compatible; new Agent reads local state.
    let call = AgentToolCall(id: "progress", name: "read_learning_data",
      argumentsJSON: #"{"kind":"preparation","course_id":"course-agent"}"#, argumentsHash: "")
    func task() async throws -> [String: Any] {
      let result = try await UnifiedAgentTools.execute(call, run: &run, store: store)
      return try XCTUnwrap((agentJSON(Data(result.utf8))["tasks"] as? [[String: Any]])?.first)
    }
    let live = try await task()
    XCTAssertEqual(live["source"] as? String, "live")
    XCTAssertEqual(live["stage"] as? String, "diagnostics")
    XCTAssertEqual(live["diagnostics_completed"] as? Int, 5)
    XCTAssertEqual(live["diagnostics_total"] as? Int, 12)
    let unavailable = try await task()
    XCTAssertEqual(unavailable["source"] as? String, "unknown")
    XCTAssertNil(unavailable["status"])
  }
}

extension AgentRuntimeTests {
  @MainActor func testMaterialPageRequiresAnExactPositiveInteger() {
    XCTAssertEqual(UnifiedAgentTools.materialPage(Double(2)), 2)
    for invalid in [0.0, -1.0, 1.5, Double.infinity, Double.nan, Double(Int.max)] {
      XCTAssertNil(UnifiedAgentTools.materialPage(invalid))
    }
    XCTAssertNil(UnifiedAgentTools.materialPage("2"))
    XCTAssertNil(UnifiedAgentTools.materialPage(nil))
  }

  func testFinalAnswerUsesRegisteredCitationsAndPersistsSourceMetadata() throws {
    var evidence = agentRepositoryEvidence()
    evidence.sourceType = "textbook"
    var run = agentRepositoryRun(scope: "owner", evidence: evidence)
    AgentRuntime.completeAnswer("原文说明 [ev-1]。伪造引用 [ev_missing]。", run: &run)
    XCTAssertEqual(run.finalCitations?.map(\.quote), [evidence.quote])
    XCTAssertEqual(run.finalCitations?.first?.page, 11)
    XCTAssertEqual(run.finalText, "原文说明 [1]。伪造引用 。")
    XCTAssertEqual(run.finalSourceLabel, "textbook")
    let restored = try JSONDecoder().decode(AgentRunRecord.self, from: JSONEncoder().encode(run))
    XCTAssertEqual(restored.finalCitations, run.finalCitations)
    XCTAssertEqual(restored.finalSourceLabel, "textbook")
  }

  func testFinalAnswerDoesNotTurnUncitedSearchResultsIntoCitations() {
    var evidence = agentRepositoryEvidence()
    evidence.sourceType = "textbook"
    var run = agentRepositoryRun(scope: "owner", evidence: evidence)
    AgentRuntime.completeAnswer("没有引用具体原文。", run: &run)
    XCTAssertEqual(run.finalCitations, [])
    XCTAssertEqual(run.finalSourceLabel, "textbook_uncited")
  }

  @MainActor func testCrossBookSearchReadsOnlyLocalChunksAndReportsMissingBooks() async throws {
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: agentTempDirectory(), host: host)
    defer { session.invalidateAndCancel() }
    var source = agentSource(bookID: "book-test", title: "物理")
    source.chunks = [SourceChunk(chunk_id: "momentum", book_id: "book-test", chapter_id: "chapter", page_start: 2, page_end: 2,
      content_type: "text", text: "系统动量守恒，总动量保持不变。", asset_ids: [], key_concepts: [])]
    store.sources = [source]
    store.courses = [Course(id: "course-agent", name: "物理", resourceIDs: ["book-test", "book_local_pending"])]
    var run = agentRepositoryRun(scope: store.noteScope!, evidence: agentRepositoryEvidence())
    run.privateTransport = true
    let call = AgentToolCall(id: "search", name: "search_learning_materials", argumentsJSON: #"{"query":"动量守恒","include_images":false}"#, argumentsHash: "")
    let result = agentJSON(Data(try await UnifiedAgentTools.execute(call, run: &run, store: store).utf8))
    XCTAssertEqual(result["searched_book_ids"] as? [String], ["book-test"])
    XCTAssertEqual(result["unavailable_book_ids"] as? [String], ["book_local_pending"])
    XCTAssertEqual(result["scope"] as? String, "local_material_library")
    XCTAssertEqual((result["evidence"] as? [[String: Any]])?.first?["quote"] as? String, source.chunks[0].text)
    XCTAssertTrue(AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.isEmpty)
    XCTAssertNil(run.images)
  }

  @MainActor func testSearchNeverRendersWholePageWhenIndependentFigureIsMissing() async throws {
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: agentTempDirectory(), host: host)
    defer { session.invalidateAndCancel() }
    var source = agentSource(bookID: "book-test", title: "物理")
    source.chunks = [SourceChunk(chunk_id: "momentum", book_id: "book-test", chapter_id: "chapter", page_start: 2, page_end: 2,
      content_type: "text", text: "系统动量守恒，总动量保持不变。", asset_ids: [], key_concepts: [])]
    store.sources = [source]; agentRegisterCourse(store)
    var run = agentRepositoryRun(scope: store.noteScope!, evidence: agentRepositoryEvidence())
    run.privateTransport = true
    let call = AgentToolCall(id: "search", name: "search_learning_materials", argumentsJSON: #"{"query":"动量守恒","include_images":true}"#, argumentsHash: "")
    let result = agentJSON(Data(try await UnifiedAgentTools.execute(call, run: &run, store: store).utf8))
    XCTAssertEqual(result["images_loaded"] as? Int, 0)
    XCTAssertNil(run.images)
    let page = AgentToolCall(id: "image", name: "read_material", argumentsJSON: #"{"course_id":"course-agent","book_id":"book-test","mode":"image","page":2}"#, argumentsHash: "")
    do { _ = try await UnifiedAgentTools.execute(page, run: &run, store: store); XCTFail("No full-page fallback is allowed") }
    catch { XCTAssertTrue(error.localizedDescription.contains("独立教材插图")) }
    XCTAssertTrue(AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.isEmpty)
  }

}

extension AgentRuntimeTests {
  @MainActor func testSearchSuppliesIndependentFigureBytesInPrivateTranscript() async throws {
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: agentTempDirectory(), host: host)
    defer { session.invalidateAndCancel() }
    var source = agentSource(bookID: "book-image", title: "图片教材", pageCount: 1)
    source.chunks = [SourceChunk(chunk_id: "figure-text", book_id: "book-image", chapter_id: "chapter", page_start: 1, page_end: 1,
      content_type: "text", text: "动量守恒示意图", asset_ids: ["figure-1"], key_concepts: [])]
    source.assets = [BookAsset(asset_id: "figure-1", book_id: "book-image", source_type: "textbook", page: 1,
      type: "figure", caption: "独立示意图", image_url: "figure.png", thumbnail_url: "figure.png", source_chunk_ids: ["figure-text"], concepts: [])]
    store.sources = [source]; agentRegisterCourse(store)
    let png = try XCTUnwrap(Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
    let figurePath = try await store.repository.saveAttachment(png, extension: "png")
    let record = LocalMaterialRecord(groupID: "group", version: "v1", bookID: source.id, files: [:], figurePaths: ["figure-1": figurePath],
      source: source, diagnostics: [], grading: [])
    try await store.repository.saveMaterialRecord(record, scope: store.noteScope!)
    var run = agentRepositoryRun(scope: store.noteScope!, evidence: agentRepositoryEvidence())
    run.privateTransport = true; run.context.sessionID = "local-chat"
    let call = AgentToolCall(id: "image-search", name: "search_learning_materials", argumentsJSON: #"{"query":"动量守恒","include_images":true}"#, argumentsHash: "")
    let payload = try await UnifiedAgentTools.execute(call, run: &run, store: store)
    XCTAssertEqual(agentJSON(Data(payload.utf8))["images_loaded"] as? Int, 1)
    let image = try XCTUnwrap(run.images?.first)
    XCTAssertFalse(image.data_base64?.isEmpty ?? true)
    run.steps[0].toolCalls = [call]
    run.steps[0].outcomes = [AgentToolOutcome(toolCallID: call.id, status: "ok", resultJSON: payload)]
    let next = AgentRuntime.turnRequest(for: run, stepIndex: 1)
    XCTAssertEqual(next.contract_version, 3)
    XCTAssertNil(next.attachments)
    let transcript = try AgentDigest.canonicalJSON(AgentRuntime.foundationObject(.array((next.client_messages ?? []).map(JSONValue.object))))
    XCTAssertTrue(transcript.contains("data:image/jpeg;base64,"))
    XCTAssertTrue(transcript.contains("image-search"))
    XCTAssertTrue(AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.isEmpty)
  }

}


extension AgentRuntimeTests {
  @MainActor func testLegacyUnfinishedRunIsArchivedWithoutAnyNetworkOrToolExecution() async throws {
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: agentTempDirectory(), host: host)
    defer { session.invalidateAndCancel() }
    var legacy = agentRepositoryRun(scope: store.noteScope!, evidence: agentRepositoryEvidence())
    legacy.privateTransport = nil
    legacy.context.sessionID = "legacy-session"
    legacy.steps[0].state = .posted
    legacy = try await store.repository.saveRun(legacy)
    store.agent.resume()
    try await agentWaitUntil { !store.agent.isWorking }
    let stored = try await store.repository.loadRun(legacy.id, scope: legacy.scope)
    let archived = try XCTUnwrap(stored)
    XCTAssertEqual(archived.legacyArchived, true)
    XCTAssertEqual(archived.status, .cancelled)
    XCTAssertEqual(archived.steps[0].state, .posted, "Keep original request history without executing it")
    XCTAssertTrue(AgentScriptedRouter.shared.snapshot().filter { $0.host == host }.isEmpty)
  }
}


extension AgentRuntimeTests {
  @MainActor func testConfirmedRemovalOfOwnCourseEndsLocallyWithoutRevivingRunOrCallingModel() async throws {
    let host = agentUniqueHost()
    let (store, session) = try agentMakeStore(directory: agentTempDirectory(), host: host)
    defer { session.invalidateAndCancel() }
    store.isLoading = false
    store.sources = [agentSource(bookID: "book-agent", title: "测试教材")]
    agentRegisterCourse(store)
    let scope = try XCTUnwrap(store.noteScope)
    try await store.restoreCourseDeletionState()
    var run = agentRepositoryRun(scope: scope, evidence: agentRepositoryEvidence())
    run.context.sessionID = "removal-session"
    run.context.messageID = "removal-question"
    let call = AgentToolCall(id: "remove-own-course", name: "remove_course",
      argumentsJSON: #"{"course_id":"course-agent"}"#, argumentsHash: "")
    run.steps[0].toolCalls = [call]
    run.pendingChoice = try await UnifiedAgentTools.prepareMutation(call, run: run, store: store)
    run.status = .waitingForUser
    let confirmation = try XCTUnwrap(run.pendingChoice)
    let argumentsJSON = try XCTUnwrap(confirmation.actionArgumentsJSON)
    let arguments = try JSONDecoder().decode([String: JSONValue].self, from: Data(argumentsJSON.utf8))
    run = try await store.repository.saveRun(run)
    var context = AssistantSessionContext(courseID: "course-agent", bookID: "book-agent", bookIDs: ["book-agent"],
      chapterID: nil, courseTitle: "测试课程", chapterTitle: "", prompts: [], discovery: false)
    context.sessionID = run.context.sessionID
    context.messageID = run.context.messageID
    var conversation = AssistantSession(id: "removal-session", title: "保留普通对话", context: context)
    conversation.messages = [ChatMessage(id: "removal-question", role: "user", text: "删除这门课程")]
    conversation.pendingQuestion = "删除这门课程"
    conversation.runs = [run]
    try await store.repository.saveAssistantHistory(
      AssistantHistoryArchive(sessions: [conversation], selectedID: conversation.id), scope: scope)
    await store.assistantHistory.load(scope: scope)
    store.agent.resume()
    try await agentWaitUntil { store.agent.active?.id == run.id && !store.agent.isWorking }
    store.agent.choose(try XCTUnwrap(confirmation.options.first))
    try await agentWaitUntil { !store.agent.isWorking && store.agent.active == nil }
    await store.assistantHistory.flush()

    let operationID = run.id + "." + call.id
    let storedRun = try await store.repository.loadRun(run.id, scope: scope)
    let receipt = try await store.repository.localCourseDeletionResult(operationID, scope: scope)
    XCTAssertNil(storedRun)
    XCTAssertEqual(receipt?["status"], .string("deleted"))
    XCTAssertFalse(store.courses.contains { $0.id == "course-agent" })
    let retained = try XCTUnwrap(store.assistantHistory.current)
    XCTAssertNil(retained.context.courseID)
    XCTAssertTrue(retained.runs?.isEmpty ?? true)
    XCTAssertNil(retained.pendingQuestion)
    XCTAssertNil(retained.error)
    XCTAssertEqual(retained.messages.map(\.text), ["删除这门课程", "课程已从本机删除。"])
    XCTAssertTrue(retained.courseLinks.isEmpty)
    XCTAssertNil(store.agent.notice)
    do {
      _ = try await store.repository.saveRun(run)
      XCTFail("The completed deletion must never resurrect its original run")
    } catch {}
    let replay = try await UnifiedAgentTools.performConfirmedMutation("remove_course", args: arguments,
      operationID: operationID, scope: scope, store: store, localOnly: true)
    XCTAssertEqual(agentJSON(Data(replay.utf8))["status"] as? String, "deleted")
    store.agent.choose(confirmation.options[0]) // A repeated tap has no live confirmation left.
    XCTAssertEqual(store.assistantHistory.current?.messages.count, 2)
    XCTAssertTrue(AgentScriptedRouter.shared.snapshot().filter {
      $0.host == host && $0.method == "POST" && $0.path == "/api/assistant/turn"
    }.isEmpty, "A local removal receipt must terminate without another model turn")
  }
}


extension AgentRuntimeTests {
  @MainActor func testConfirmedRemovalOfReferencedCourseCompletesSurvivingRunsOnceWithoutModel() async throws {
    for variant in ["other-course", "global", "ephemeral"] {
      let host = agentUniqueHost()
      let (store, session) = try agentMakeStore(directory: agentTempDirectory(), host: host)
      defer { session.invalidateAndCancel() }
      store.isLoading = false
      store.sources = [agentSource(bookID: "book-agent", title: "删除教材"),
        agentSource(bookID: "book-other", title: "保留教材")]
      store.courses = [Course(id: "course-agent", name: "删除课程", resourceIDs: ["book-agent"]),
        Course(id: "course-other", name: "保留课程", resourceIDs: ["book-other"])]
      let scope = try XCTUnwrap(store.noteScope)
      try await store.ensureCourseDeletionState(scope: scope)
      var run = agentRepositoryRun(scope: scope, id: "remove-reference-" + variant,
        evidence: agentRepositoryEvidence())
      run.context.courseID = variant == "global" ? nil : "course-other"
      run.context.bookID = variant == "global" ? "" : "book-other"
      run.context.courseResourceID = nil
      run.context.sessionID = "session-" + variant
      run.ephemeral = variant == "ephemeral"
      run.finalCourseIDs = ["course-agent", "course-other"]
      let call = AgentToolCall(id: "remove-reference", name: "remove_course",
        argumentsJSON: #"{"course_id":"course-agent"}"#, argumentsHash: "")
      run.steps[0].toolCalls = [call]
      run.pendingChoice = try await UnifiedAgentTools.prepareMutation(call, run: run, store: store)
      run.status = .waitingForUser
      let confirm = try XCTUnwrap(run.pendingChoice?.options.first)
      // Seed a paused run; ephemeral then exercises loadRun's in-memory path.
      run = try await store.repository.saveRun(run)
      store.agent.resume()
      try await agentWaitUntil { store.agent.active?.id == run.id && !store.agent.isWorking }
      store.agent.choose(confirm)
      try await agentWaitUntil { !store.agent.isWorking && store.agent.active?.status == .completed }
      let result = try XCTUnwrap(store.agent.active)
      XCTAssertNil(result.pendingChoice, variant)
      XCTAssertEqual(result.context.courseID, run.context.courseID, variant)
      XCTAssertFalse(result.evidence.contains { $0.courseID == "course-agent" }, variant)
      XCTAssertFalse(result.finalCourseIDs?.contains("course-agent") ?? false, variant)
      XCTAssertEqual(result.steps[0].outcomes.filter { $0.toolCallID == call.id }.count, 1, variant)
      XCTAssertEqual(result.finalText, "课程已从本机删除。", variant)
      XCTAssertEqual(store.courses.map(\.id), ["course-other"], variant)
      let receipt = try await store.repository.localCourseDeletionResult(run.id + "." + call.id, scope: scope)
      XCTAssertEqual(receipt?["status"], .string("deleted"), variant)
      if variant != "ephemeral" {
        let durable = try await store.repository.loadRun(run.id, scope: scope)
        XCTAssertEqual(durable?.status, .completed, variant)
        XCTAssertNil(durable?.pendingChoice, variant)
        XCTAssertGreaterThan(durable?.version ?? 0, run.version, variant)
      }
      store.agent.choose(confirm)
      XCTAssertEqual(store.agent.active?.steps[0].outcomes.count, 1, variant)
      XCTAssertNil(store.agent.notice, variant)
      XCTAssertTrue(AgentScriptedRouter.shared.snapshot().filter {
        $0.host == host && $0.method == "POST"
      }.isEmpty, "Finishing a local deletion must not invoke another model turn")
    }
  }
}
