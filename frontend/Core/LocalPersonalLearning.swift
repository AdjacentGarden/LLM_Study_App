import CryptoKit
import Foundation

struct LocalFirstCapabilities: Decodable, Sendable {
  var schema_version: Int
  var public_library: Bool
  var private_forwarding: Bool
  var private_export: Bool
  var legacy_writes_active: Bool
}
struct LocalPersonalState: Codable, Sendable {
  var snapshot: AppSnapshot
  var sources: [SourceContent]
  var workspaces: [CloudWorkspace]
  var filePaths: [String: String]
  var courseSources: [String: [SourceContent]]
  var dailyPlans: [String: CloudStudyPlan]
  var diagnostics: [String: LocalDiagnosticSession]
  var requiresPersonalization: Bool? = nil
  var initialized: Bool
  var deletedCourseIDs: Set<String>? = nil
}
struct LocalDiagnosticSession: Codable, Sendable {
  var id: String
  var courseID: String
  var bookID: String
  var chapterID: String
  var items: [LocalDiagnosticItem]
  var grades: [LocalDiagnosticGrade]
  var position: Int
  var results: [String: JSONValue]
}
struct LocalGenerationRecord: Codable, Sendable {
  var id: String
  var courseID: String
  var bookID: String
  var chapterID: String
  var operation: String
  var body: [String: JSONValue]
  var state: String
  var content: String?
  var error: String?
  /// Count of user-started retries after a definite failure; each uses a new request ID.
  var attempt: Int?
  var updatedAt: TimeInterval? = nil
  var statusKey: String { courseID + "::" + bookID }
}

struct LocalGenerationStatus: Sendable {
  var courseID: String
  var bookID: String
  var operation: String
  var state: String
  var error: String?
  init(_ record: LocalGenerationRecord) {
    courseID = record.courseID; bookID = record.bookID
    operation = record.operation; state = record.state; error = record.error
  }
}

extension LocalBookCourseRepository {
  func savePersonalState(_ state: LocalPersonalState, scope: String) throws {
    let filtered = try filterDeletedCourses(state, scope: scope)
    try db.put("private-local-authority", scope, encoder.encode(filtered))
  }
  func loadPersonalState(scope: String) throws -> LocalPersonalState? {
    try db.get("private-local-authority", scope).first.map { try decoder.decode(LocalPersonalState.self, from: $0) }
  }
  func saveLocalGeneration(_ record: LocalGenerationRecord, scope: String) throws {
    try requireLiveCourse(courseID: record.courseID, scope: scope)
    try db.put("private-generation:" + scope, record.id, encoder.encode(record))
  }
  func localGenerations(scope: String) throws -> [LocalGenerationRecord] {
    try db.get("private-generation:" + scope).map { try decoder.decode(LocalGenerationRecord.self, from: $0) }
  }
  func localOperation(_ id: String, scope: String) throws -> [String: JSONValue]? {
    try db.get("private-operation:" + scope, id).first.map { try decoder.decode([String: JSONValue].self, from: $0) }
  }
  func saveLocalOperation(_ value: [String: JSONValue], id: String, scope: String) throws {
    try db.put("private-operation:" + scope, id, encoder.encode(value))
  }
}

@MainActor extension AppStore {
  func verifyLocalFirstCapabilities() async throws {
    guard let cloud else { return }
    let epoch = cloudState.epoch
    do {
      let value: LocalFirstCapabilities = try await cloud.request("/api/native/local-first/capabilities")
      guard epoch == cloudState.epoch else { throw CancellationError() }
      var supported = value.public_library && value.private_forwarding && value.private_export
      if value.schema_version == 1, !value.public_library, !value.private_forwarding, !value.private_export {
        // Release c242719 inspected only top-level routes. Confirm its included routers
        // against the live contract without starting a generation or a migration.
        struct Contract: Decodable { let paths: [String: [String: Operation]] }
        struct Operation: Decodable {}
        struct Health: Decodable { let release_commit: String? }
        let health: Health = try await cloud.request("/api/health")
        if health.release_commit == "c242719e77b9c89779f1332bd9a659ef6bd5c11e" {
          let contract: Contract = try await cloud.request("/openapi.json")
          supported = contract.paths["/api/public/library/course-groups"]?["get"] != nil
            && contract.paths["/api/assistant/private-model"]?["post"] != nil
            && contract.paths["/api/private/export"]?["get"] != nil
        }
      }
      guard epoch == cloudState.epoch else { throw CancellationError() }
      guard value.schema_version == 1, supported else {
        throw ContentError.invalid("学习服务尚不支持本机个人数据，请更新服务后重试")
      }
      cloudState.privateLocalMode = true
      cloudState.localFirstChecked = true
      cloudState.localFirstError = nil
    } catch {
      guard epoch == cloudState.epoch else { throw CancellationError() }
      cloudState.localFirstChecked = false
      cloudState.localFirstError = "学习服务版本不支持本机个人数据；未启用旧版个人数据写入。"
      cloudState.syncError = cloudState.localFirstError
      throw error
    }
  }

  func captureLocalCourseContent() {
    guard cloudState.privateLocalMode, let id = selectedCourseID,
      let course = courses.first(where: { $0.id == id }) else { return }
    cloudState.localCourseSources[id] = sources.filter { course.resourceIDs.contains($0.id) }
  }
  func installLocalCourseContent(_ id: String) {
    guard cloudState.privateLocalMode else { return }
    for content in cloudState.localCourseSources[id] ?? [] {
      if let i = sources.firstIndex(where: { $0.id == content.id }) { sources[i] = content } else { sources.append(content) }
    }
  }
  func persistPrivateState() async throws {
    guard cloudState.privateLocalMode, let scope = noteScope else { throw ContentError.invalid("本机个人存储尚未就绪") }
    captureLocalCourseContent()
    let state = LocalPersonalState(snapshot: snapshot(), sources: sources, workspaces: cloudState.workspaces,
      filePaths: filePaths, courseSources: cloudState.localCourseSources, dailyPlans: dailyPlanState.plans,
      diagnostics: cloudState.localDiagnostics, requiresPersonalization: cloudState.requiresPersonalization, initialized: true, deletedCourseIDs: cloudState.localDeletedCourseIDs)
    try await repository.savePersonalState(state, scope: scope)
  }
  func restoreLocalGenerationStatuses(scope: String) async throws {
    let epoch = cloudState.epoch
    let records = try await repository.localGenerations(scope: scope)
    guard epoch == cloudState.epoch, cloudState.cacheKey == scope || scope == noteScope else { throw CancellationError() }
    var statuses: [String: LocalGenerationStatus] = [:]
    for record in records.sorted(by: {
      ($0.updatedAt ?? 0, $0.id) < ($1.updatedAt ?? 0, $1.id)
    }) where ["lecture", "plan"].contains(record.operation)
      && courses.contains(where: { $0.id == record.courseID })
      && !cloudState.localDeletedCourseIDs.contains(record.courseID) {
      statuses[record.statusKey] = LocalGenerationStatus(record)
    }
    cloudState.localGenerationStatuses = statuses
  }

  func restorePrivateState(_ state: LocalPersonalState) {
    courses = state.snapshot.courses; sources = state.sources; filePaths = state.filePaths
    for source in sources {
      for asset in source.assets {
        if let url = URL(string: asset.image_url), url.isFileURL,
          url.standardizedFileURL.path.hasPrefix(repository.directory.standardizedFileURL.path + "/materials/") {
          ContentBundle.registerMaterialURL(url)
        }
      }
    }
    preferences = state.snapshot.preferences; draft = state.snapshot.draft
    selectedCourseID = state.snapshot.selectedCourseID; selectedBookID = state.snapshot.selectedBookID
    locations = state.snapshot.locations; mastered = state.snapshot.mastery; mistakes = state.snapshot.mistakes
    chapterDrafts = state.snapshot.chapterDrafts ?? [:]; importState.courses = state.snapshot.courseImports ?? []
    importJob = state.snapshot.importJob; credits = state.snapshot.credits
    cloudState.workspaces = state.workspaces
    cloudState.localDeletedCourseIDs = state.deletedCourseIDs ?? []
    cloudState.localCourseSources = state.courseSources; cloudState.localDiagnostics = state.diagnostics
    dailyPlanState.plans = state.dailyPlans
    cloudState.requiresPersonalization = state.requiresPersonalization ?? false
    cloudState.onboardingComplete = !cloudState.requiresPersonalization && preferences?.hasCompletedOnboarding == true
    installLocalCourseContent(selectedCourseID ?? "")
  }

  func readLocalPersonalLearning(args: [String: JSONValue]) async throws -> [String: JSONValue] {
    guard let scope = noteScope else { throw ContentError.invalid("请登录") }
    let courseID = args["course_id"]?.stringValue
    if let courseID, !courses.contains(where: { $0.id == courseID }) { throw ContentError.invalid("课程不存在") }
    let requestedKind = args["kind"]?.stringValue ?? "all"
    let kind = ["note": "notes", "plan": "plans", "flashcard": "flashcards", "review": "reviews", "answers": "submitted_answers",
      "review_tasks": "reviews", "annotations": "notes", "attempts": "submitted_answers"][requestedKind] ?? requestedKind
    if kind == "preparation" {
      let selected = courses.filter { courseID == nil || $0.id == courseID }
      let records = try await repository.loadMaterialRecords(scope: scope)
      var tasks: [JSONValue] = []
      for course in selected {
        let pending = courseImport(for: course.id)
        for bookID in course.resourceIDs {
          var row: [String: JSONValue] = ["course_id": .string(course.id), "book_id": .string(bookID), "source": .string("local_snapshot")]
          if let book = pending?.books.first(where: { $0.id == bookID }) {
            row["status"] = .string(book.status.rawValue); row["message"] = .string(book.message)
            row["stage"] = book.stage.map(JSONValue.string) ?? .null
          } else if let material = records.first(where: { $0.bookID == bookID }) {
            row["status"] = .string("downloaded"); row["diagnostics_completed"] = .number(Double(material.diagnostics.count))
            row["diagnostics_finalized"] = .bool(!material.diagnostics.isEmpty)
            row["diagnostics_total"] = material.diagnostics.isEmpty ? .null : .number(Double(material.diagnostics.count))
          } else { row["status"] = .string("unknown") }
          tasks.append(.object(row))
        }
      }
      return ["tasks": .array(tasks), "source": .string("local_snapshot"), "complete": .bool(true),
        "note": .string("仅反映手机已保存的最近状态，不代表后台任务当前仍在运行。")]
    }
    let objectID = args["object_id"]?.stringValue
    let keyword = args["keyword"]?.stringValue
    let date = args["date"]?.stringValue
    let objects = try await repository.loadAgentLearningObjects(scope: scope).filter {
      (courseID == nil || $0.course_id == courseID) && (objectID == nil || $0.id == objectID)
        && (date == nil || $0.data["date"]?.stringValue == date)
        && (kind == "all" || ["flashcards": "flashcard", "plans": "plan", "reviews": "review_task", "submitted_answers": "submitted_answer", "diagnosis": "diagnostic"][kind] == $0.kind)
    }
    let encoder = JSONEncoder()
    func json<T: Encodable>(_ value: T) throws -> JSONValue { try JSONDecoder().decode(JSONValue.self, from: encoder.encode(value)) }
    var rows: [JSONValue] = []
    if kind == "all" || kind == "notes" {
      for var note in notes where (courseID == nil || note.anchor?.courseID == courseID) && (objectID == nil || note.id == objectID) {
        note.conversation = nil // Historical chats are never a learning-data source.
        rows.append(try json(note))
      }
    }
    if ["all", "media"].contains(kind), date == nil {
      let owned = Set(courses.map(\.id))
      for record in try await repository.localLessonMedia(scope: scope) {
        guard let id = record["course_id"]?.stringValue, owned.contains(id), courseID == nil || courseID == id,
          objectID == nil || record["id"]?.stringValue == objectID else { continue }
        rows.append(.object(record))
      }
    }
    if ["all", "lessons", "plans"].contains(kind), date == nil {
      for course in courses where courseID == nil || course.id == courseID {
        let materials = course.id == selectedCourseID ? sources : cloudState.localCourseSources[course.id] ?? []
        for source in materials where course.resourceIDs.contains(source.id) {
          if kind != "plans" {
            for lesson in source.lessons where objectID == nil || lesson.id == objectID {
              rows.append(.object(["kind": .string("lesson"), "id": .string(lesson.id),
                "course_id": .string(course.id), "book_id": .string(source.id),
                "chapter_id": .string(lesson.chapter_id), "data": try json(lesson)]))
            }
          }
          if kind != "lessons" {
            for task in source.plan.tasks where objectID == nil || task.id == objectID {
              rows.append(.object(["kind": .string("plan_task"), "id": .string(task.id),
                "course_id": .string(course.id), "book_id": .string(source.id), "data": try json(task)]))
            }
          }
        }
      }
    }
    if ["all", "flashcards", "plans", "reviews", "submitted_answers", "diagnosis"].contains(kind) { rows += try objects.map(json) }
    if kind == "all" || kind == "progress" {
      rows += try locations.filter { courseID == nil || $0.key.hasPrefix(courseID! + "::") }.map {
        .object(["id": .string($0.key), "kind": .string("progress"), "data": try json($0.value)])
      }
    }
    if kind == "all" || kind == "mistakes" { rows += try mistakes.filter { courseID == nil || $0.course_id == courseID }.map(json) }
    if let keyword, !keyword.isEmpty {
      rows = try rows.filter { String(decoding: try encoder.encode($0), as: UTF8.self).localizedCaseInsensitiveContains(keyword) }
    }
    let offset = max(0, Int(args["offset"]?.numberValue ?? 0)), limit = min(100, max(1, Int(args["limit"]?.numberValue ?? 30)))
    let page = Array(rows.dropFirst(offset).prefix(limit))
    return ["source": .string("local_private"), "items": .array(page), "total": .number(Double(rows.count)),
      "offset": .number(Double(offset)), "complete": .bool(offset + page.count >= rows.count),
      "next_offset": offset + page.count < rows.count ? .number(Double(offset + page.count)) : .null]
  }

  func performLocalPersonalOperation(name: String, args: [String: JSONValue], runID: String, callID: String) async throws -> [String: JSONValue] {
    guard cloudState.privateLocalMode, let scope = noteScope else { throw ContentError.invalid("本机个人数据尚未就绪") }
    let operationID = runID + "." + callID
    if let receipt = try await repository.localOperation(operationID, scope: scope) { return receipt }
    if ["complete_task", "review"].contains(name) {
      let id = args["object_id"]?.stringValue ?? args["task_id"]?.stringValue ?? args["card_id"]?.stringValue ?? ""
      var objects = try await repository.loadAgentLearningObjects(scope: scope)
      guard let index = objects.firstIndex(where: { $0.id == id }), courses.contains(where: { $0.id == objects[index].course_id }) else { throw ContentError.invalid("学习对象不存在") }
      if name == "complete_task" { objects[index].data["status"] = .string("done") }
      else {
        objects[index].data["last_rating"] = args["rating"] ?? .string("good")
        objects[index].data["reviewed_at"] = .string(ISO8601DateFormatter().string(from: .now))
        objects[index].data["repetitions"] = .number((objects[index].data["repetitions"]?.numberValue ?? 0) + 1)
      }
      objects[index].revision += 1
      try await repository.saveAgentLearningObjects(objects, scope: scope)
      assistantState.learningObjects = objects
      let result: [String: JSONValue] = ["status": .string("saved"), "id": .string(id), "revision": .number(Double(objects[index].revision))]
      try await repository.saveLocalOperation(result, id: operationID, scope: scope)
      return result
    }
    guard let courseID = args["course_id"]?.stringValue, let course = courses.first(where: { $0.id == courseID }) else {
      throw ContentError.invalid("必须指定本机课程")
    }
    let bookID = args["book_id"]?.stringValue ?? course.activeResourceID ?? course.resourceIDs.first ?? ""
    guard course.resourceIDs.contains(bookID) else { throw ContentError.invalid("教材不属于指定课程") }
    var objects = try await repository.loadAgentLearningObjects(scope: scope)
    var ids: [String] = []
    let rows: [[String: JSONValue]]
    if name == "create_flashcards", case .array(let cards)? = args["cards"] {
      rows = cards.compactMap { if case .object(let row) = $0 { return row }; return nil }
      guard rows.count == cards.count, !rows.isEmpty else { throw ContentError.invalid("闪卡内容无效") }
    } else { rows = [args] }
    let kind: String
    switch name { case "create_flashcards": kind = "flashcard"; case "create_review_task": kind = "review_task"; case "update_study_plan": kind = "plan"
    default: throw ContentError.invalid("不支持的本机学习操作") }
    for (index, row) in rows.enumerated() {
      let id = "local-object:" + String(AgentDigest.sha256(operationID + ":\(index)").prefix(32))
      let wire: [String: JSONValue] = ["id": .string(id), "revision": .number(1), "kind": .string(kind),
        "course_id": .string(courseID), "course_resource_id": .string(courseResourceID(courseID: courseID, bookID: bookID) ?? courseID + ":" + bookID),
        "data": .object(row.merging(["book_id": .string(bookID)]) { _, new in new })]
      let object = try JSONDecoder().decode(AgentLearningObject.self, from: JSONEncoder().encode(wire))
      if !objects.contains(where: { $0.id == id }) { objects.append(object) }
      ids.append(id)
    }
    try await repository.saveAgentLearningObjects(objects, scope: scope)
    assistantState.learningObjects = objects
    let result: [String: JSONValue] = ["status": .string("saved"), "ids": .array(ids.map(JSONValue.string)), "source": .string("local_private")]
    try await repository.saveLocalOperation(result, id: operationID, scope: scope)
    try await persistPrivateState()
    return result
  }

  func beginLocalDiagnosis(chapterID: String?, preview: Bool = false) async throws {
    guard let scope = noteScope, let courseID = selectedCourseID, let bookID = selectedBookID,
      let chapter = chapterID ?? currentSource?.chapters.first?.id else { throw ContentError.invalid("请先选择课程章节") }
    try await withLocalCourseLearning(courseID: courseID, scope: scope) {
      guard self.selectedCourseID == courseID, self.selectedBookID == bookID else { throw CancellationError() }
      try await self.beginOwnedLocalDiagnosis(courseID: courseID, bookID: bookID,
        chapter: chapter, scope: scope, preview: preview)
    }
  }

  private func beginOwnedLocalDiagnosis(courseID: String, bookID: String, chapter: String,
    scope: String, preview: Bool) async throws {
    let epoch = cloudState.epoch
    let jobs = try await repository.localGenerations(scope: scope)
    guard epoch == cloudState.epoch, canApplyCourseMutation(courseID: courseID, scope: scope) else { throw CancellationError() }
    if let existing = cloudState.localDiagnostics.values.first(where: { session in
      session.courseID == courseID && session.bookID == bookID && session.chapterID == chapter
        && ((!preview && session.position < session.items.count) || jobs.contains {
          $0.id == "lecture_" + AgentDigest.sha256(session.id) && ["submitted", "submitting", "uncertain", "retryable", "failed"].contains($0.state)
        })
    }) {
      cloudState.requestedChapterID = chapter
      cloudState.diagnosisWorkspaceID = courseID
      cloudState.diagnosisResourceID = courseResourceID(courseID: courseID, bookID: bookID)
      cloudState.interview = localInterview(existing)
      cloudState.showDiagnosis = true; cloudState.choosingLearningMode = false
      if existing.position >= existing.items.count { try await generateLocalPersonalChapter(sessionID: existing.id) }
      return
    }
    let records = try await repository.loadMaterialRecords(scope: scope)
    guard epoch == cloudState.epoch, scope == noteScope,
      canApplyCourseMutation(courseID: courseID, scope: scope) else { throw CancellationError() }
    let material = records.first { $0.bookID == bookID }
    let items = preview ? [] : material?.diagnostics.filter { $0.chapter_id == chapter } ?? []
    let grades = material?.grading ?? []
    if !preview && (items.isEmpty || !Set(items.map(\.item_id)).isSubset(of: Set(grades.map(\.item_id)))) {
      cloudState.requestedChapterID = chapter
      cloudState.diagnosisWorkspaceID = courseID
      cloudState.diagnosisResourceID = courseResourceID(courseID: courseID, bookID: bookID)
      cloudState.interview = nil
      cloudState.courseJob = nil
      cloudState.choosingLearningMode = true
      cloudState.showDiagnosis = true
      throw ContentError.invalid("本章诊断资料尚未完整，可选择“直接预览课程”先学习。")
    }
    // Direct learning uses verified local original text and does not require a public question bank.
    let session = LocalDiagnosticSession(id: "local-diagnosis:" + UUID().uuidString, courseID: courseID,
      bookID: bookID, chapterID: chapter, items: items, grades: grades, position: 0, results: [:])
    guard canApplyCourseMutation(courseID: courseID, scope: scope) else { throw CancellationError() }
    cloudState.localDiagnostics[session.id] = session
    cloudState.requestedChapterID = chapter
    cloudState.diagnosisWorkspaceID = courseID
    cloudState.diagnosisResourceID = courseResourceID(courseID: courseID, bookID: bookID)
    cloudState.showDiagnosis = true; cloudState.choosingLearningMode = false
    cloudState.interview = localInterview(session)
    try await persistPrivateState()
    guard epoch == cloudState.epoch, canApplyCourseMutation(courseID: courseID, scope: scope) else { throw CancellationError() }
    if items.isEmpty { try await generateLocalPersonalChapter(sessionID: session.id) }
  }
  func localInterview(_ session: LocalDiagnosticSession) -> CloudInterview {
    let item = session.items.indices.contains(session.position) ? session.items[session.position] : nil
    let phase = item == nil ? "complete" : "adaptive_diagnosis"
    let turn = CloudTurn(turn_id: session.id + ":\(session.position)", phase: phase,
      message: item == nil ? "学习诊断已完成，准备生成本机学习内容。" : "请根据当前理解作答。", options: [],
      item: item.map { CloudTurn.Item(item_id: $0.item_id, prompt: $0.prompt, response_type: $0.response_type, options: $0.options) },
      progress: session.items.isEmpty ? 1 : Double(session.position) / Double(session.items.count))
    return CloudInterview(session_id: session.id, phase: phase, turn: turn, profile: .object(session.results), purpose: "learning")
  }
  func answerLocalDiagnosis(selected: [String], confidence: Double, seconds: Double) async throws {
    guard let id = cloudState.interview?.session_id, let session = cloudState.localDiagnostics[id],
      let scope = noteScope else { throw CancellationError() }
    try await withLocalCourseLearning(courseID: session.courseID, scope: scope) {
      try await self.answerOwnedLocalDiagnosis(id: id, selected: selected, confidence: confidence, seconds: seconds)
    }
  }

  private func answerOwnedLocalDiagnosis(id: String, selected: [String], confidence: Double, seconds: Double) async throws {
    guard var session = cloudState.localDiagnostics[id],
      session.items.indices.contains(session.position) else { throw ContentError.invalid("本机诊断状态不可用") }
    guard let scope = noteScope,
      canApplyCourseMutation(courseID: session.courseID, scope: scope) else { throw CancellationError() }
    let epoch = cloudState.epoch
    let item = session.items[session.position]
    guard let grade = session.grades.first(where: { $0.item_id == item.item_id }) else { throw ContentError.invalid("评分数据缺失") }
    let result: [String: JSONValue]
    if !item.options.isEmpty {
      result = ["correct": .bool(Set(selected) == Set(grade.correct_option_ids)),
        "response": .array(selected.map(JSONValue.string)), "confidence": .number(confidence), "seconds": .number(seconds)]
    } else {
      result = try await gradeLocalShortAnswer(item: item, grade: grade, answer: selected.joined(separator: "\n"), sessionID: id)
    }
    guard epoch == cloudState.epoch, canApplyCourseMutation(courseID: session.courseID, scope: scope) else { throw CancellationError() }
    session.results[item.item_id] = .object(result)
    session.position += 1
    cloudState.localDiagnostics[id] = session
    cloudState.interview = localInterview(session)
    try await persistPrivateState()
    guard epoch == cloudState.epoch, canApplyCourseMutation(courseID: session.courseID, scope: scope) else { throw CancellationError() }
    if session.position == session.items.count { try await generateLocalPersonalChapter(sessionID: id) }
  }
}

@MainActor extension AppStore {
  func submitLocalPractice(question: QuizQuestion, answer: String) async throws {
    guard let courseID = selectedCourseID else { throw ContentError.invalid("请先选择课程") }
    let correct: Bool, feedback: String
    if !question.choices.isEmpty {
      let raw = answer.trimmingCharacters(in: .whitespacesAndNewlines)
      let ids: [String]
      if let letter = raw.uppercased().first, let index = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ").firstIndex(of: letter) { ids = [String(index)] }
      else { ids = raw.split(separator: ",").map(String.init) }
      guard !question.answer.isEmpty else { throw ContentError.invalid("本机缺少这道题的评分数据") }
      correct = Set(ids) == Set(question.answer.split(separator: ",").map(String.init))
      feedback = correct ? "回答正确。" : question.explanation
    } else {
      let item = LocalDiagnosticItem(item_id: question.id, chapter_id: question.chapter_id, prompt: question.prompt,
        response_type: "short_answer", options: [])
      let grade = LocalDiagnosticGrade(item_id: question.id, correct_option_ids: [], expected_answer: question.answer,
        rubric: question.explanation.isEmpty ? [] : [question.explanation])
      let result = try await gradeLocalShortAnswer(item: item, grade: grade, answer: answer,
        sessionID: courseID + ":practice:" + question.id)
      correct = result["correct"] == .bool(true)
      feedback = result["feedback"]?.stringValue ?? ""
    }
    assignmentAnswer = answer
    diagnosisText = feedback
    latestDiagnosis = Diagnosis(assignment_id: question.id, submission_id: UUID().uuidString, result: correct ? "correct" : "incorrect",
      stuck_point: correct ? "" : question.concept, knowledge_points: [question.concept], review_citations: [], hint: feedback,
      needs_followup: !correct, mistake_recorded: !correct)
    if !correct {
      mistakes.append(MistakeRecord(mistake_id: UUID().uuidString, user_id: cloudState.account?.user_id ?? "",
        book_id: question.book_id, assignment_id: question.id, question: question.prompt, answer: answer,
        stuck_point: question.concept, knowledge_points: [question.concept], citation_ids: question.source_chunk_ids,
        correct_answer: question.answer, explanation: question.explanation, course_id: courseID))
    }
    if let scope = noteScope {
      var objects = try await repository.loadAgentLearningObjects(scope: scope)
      let id = latestDiagnosis?.submission_id ?? UUID().uuidString
      let wire: [String: JSONValue] = ["id": .string(id), "revision": .number(1), "kind": .string("submitted_answer"),
        "course_id": .string(courseID), "course_resource_id": .string(courseResourceID(courseID: courseID, bookID: question.book_id) ?? ""),
        "data": .object(["title": .string(question.prompt), "answer": .string(answer), "score": .number(correct ? 1 : 0),
          "feedback": .string(feedback), "book_id": .string(question.book_id), "chapter_id": .string(question.chapter_id),
          "at": .string(ISO8601DateFormatter().string(from: .now))])]
      objects.append(try JSONDecoder().decode(AgentLearningObject.self, from: JSONEncoder().encode(wire)))
      try await repository.saveAgentLearningObjects(objects, scope: scope)
      assistantState.learningObjects = objects
    }
    try await persistPrivateState()
    go(.diagnosis)
  }
}
