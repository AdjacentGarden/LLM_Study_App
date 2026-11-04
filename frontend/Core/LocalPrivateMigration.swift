import CryptoKit
import Foundation
import PencilKit
import UIKit

struct PrivateExportRecord: Codable, Sendable {
  var kind: String; var id: String; var revision: String; var payload: [String: JSONValue]; var deleted: Bool
}
struct PrivateExportFile: Codable, Sendable {
  var id: String; var download_url: String; var sha256: String; var bytes: Int
  var kind: String?; var book_id: String?; var relative_path: String?
}
struct PrivateExportPage: Decodable, Sendable {
  var schema_version: Int; var cursor: String?; var next_cursor: String?
  var records: [PrivateExportRecord]; var files: [PrivateExportFile]; var snapshot_revision: String
  var legacy_writes_active: Bool
  var complete: Bool?
}
struct PrivateMigrationCheckpoint: Codable, Sendable {
  var snapshotRevision: String; var digest: String; var recordCount: Int; var fileCount: Int
  var completed: Bool; var files: [String: String]
  var missingRecordIDs: [String]? = nil
}

extension LocalBookCourseRepository {
  func archivePrivateExport(_ record: PrivateExportRecord, scope: String) throws {
    try db.put("legacy-archive:" + scope, record.id + ":" + record.revision, encoder.encode(record))
  }
  func privateMigrationCheckpoint(scope: String) throws -> PrivateMigrationCheckpoint? {
    try db.get("private-migration", scope).first.map { try decoder.decode(PrivateMigrationCheckpoint.self, from: $0) }
  }
  func savePrivateMigrationCheckpoint(_ checkpoint: PrivateMigrationCheckpoint, scope: String) throws {
    try db.put("private-migration", scope, encoder.encode(checkpoint))
  }
  func privateMigrationArchives(scope: String) throws -> [PrivateExportRecord] {
    try db.get("legacy-archive:" + scope).map { try decoder.decode(PrivateExportRecord.self, from: $0) }
  }
}

@MainActor extension AppStore {
  /// Every sweep is archived. Only the first verified import can fill absent local objects.
  /// Later legacy writes are never used to overwrite or resurrect phone state.
  func migrateLegacyPersonalData() async throws {
    guard let cloud, let scope = noteScope, cloudState.privateLocalMode else { return }
    let epoch = cloudState.epoch
    let previous = try await repository.privateMigrationCheckpoint(scope: scope)
    cloudState.migrationStatus = "正在校验旧版个人数据"
    var cursor: String?
    var revision: String?
    var records: [PrivateExportRecord] = [], files: [PrivateExportFile] = []
    repeat {
      let suffix = cursor.map { "?cursor=" + ($0.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "") } ?? ""
      let page: PrivateExportPage = try await cloud.request("/api/private/export" + suffix)
      guard page.schema_version == 1, revision == nil || revision == page.snapshot_revision else { throw ContentError.invalid("旧版数据正在变化，请重新校验") }
      revision = page.snapshot_revision
      records += page.records
      for file in page.files where !files.contains(where: { $0.id == file.id }) { files.append(file) }
      for record in page.records { try await repository.archivePrivateExport(record, scope: scope) }
      cursor = page.next_cursor
      guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
    } while cursor != nil
    var copied: [String: String] = previous?.files ?? [:]
    for file in files {
      let suffix = URL(fileURLWithPath: file.relative_path ?? "").pathExtension.lowercased()
      let media = ["png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "m4a": "audio/mp4", "mp3": "audio/mpeg", "mp4": "video/mp4"][suffix] ?? "application/octet-stream"
      let descriptor = PublicMaterialFile(asset_id: file.id, kind: file.kind == "original_pdf" ? "pdf" : "migration", sha256: file.sha256,
        bytes: file.bytes, media_type: media, download_url: file.download_url)
      var path = try await repository.verifiedMaterialFile(descriptor, scope: scope)
      if path == nil {
        let bytes = try await cloud.downloadPrivateMigrationFile(file.download_url)
        path = try await repository.verifiedMaterialFile(descriptor, scope: scope, bytes: bytes)
      }
      guard let path else { throw ContentError.storage("旧版附件未完成迁移") }
      copied[file.id] = path
    }
    if previous?.completed != true {
      try await hydrateLegacyPrivateObjects(records: records, files: copied, descriptors: files)
      try await persistPrivateState()
      // Read back the committed authority, not the still-live view state.
      guard let saved = try await repository.loadPersonalState(scope: scope), saved.initialized else {
        throw ContentError.storage("本机个人数据校验失败")
      }
    }
    let snapshot = revision ?? ""
    let digest = snapshot // Server revision is the canonical records/files digest.
    let unresolved = records.filter { record in
      guard record.kind == "migration_missing_file" else { return false }
      let book = record.payload["book_id"]?.stringValue ?? ""
      if record.payload["kind"]?.stringValue == "original_pdf", let url = sourceFileURL(book),
        let data = try? Data(contentsOf: url), data.prefix(1024).range(of: Data("%PDF-".utf8)) != nil { return false }
      return true
    }
    let checkpoint = PrivateMigrationCheckpoint(snapshotRevision: snapshot, digest: digest,
      recordCount: records.count, fileCount: files.count, completed: unresolved.isEmpty, files: copied, missingRecordIDs: unresolved.map(\.id))
    try await repository.savePrivateMigrationCheckpoint(checkpoint, scope: scope)
    let ack: [String: JSONValue] = try await cloud.request("/api/private/export/checkpoint", method: "POST",
      body: ["snapshot_revision": .string(snapshot), "record_count": .number(Double(records.count)),
        "file_count": .number(Double(files.count)), "digest": .string(digest)])
    guard ack["verified"] == .bool(true) else { throw ContentError.invalid("迁移校验回执不匹配") }
    cloudState.migrationStatus = unresolved.isEmpty ? "旧版数据已校验；后续旧版变化仅归档" : "旧版记录已迁回，仍有资料缺失，尚未完成全部校验。"
  }

  private func hydrateLegacyPrivateObjects(records: [PrivateExportRecord], files: [String: String], descriptors: [PrivateExportFile]) async throws {
    guard let cloud, let scope = noteScope else { return }
    let historyEpoch = cloudState.epoch
    let bootstrap: CloudBootstrap = try await cloud.request("/api/native/bootstrap?mode=metadata")
    let existingCourseIDs = Set(courses.map(\.id))
    for var workspace in bootstrap.workspaces where !cloudState.localDeletedCourseIDs.contains(workspace.id) {
      for i in workspace.resources.indices {
        if let path = workspace.resources[i].content_path {
          let content: CloudContent = try await cloud.request(path)
          workspace.resources[i] = content.applying(to: workspace.resources[i])
        }
        let resource = workspace.resources[i]
        if filePaths[resource.book_id] == nil {
          if let original = descriptors.first(where: { $0.book_id == resource.book_id && $0.kind == "original_pdf" }), let path = files[original.id] {
            filePaths[resource.book_id] = path
          }
        }
      }
      let course = Course(id: workspace.id, name: workspace.name, resourceIDs: workspace.resources.map(\.book_id),
        courseResourceIDs: Dictionary(uniqueKeysWithValues: workspace.resources.map { ($0.book_id, $0.id) }),
        activeResourceID: workspace.resources.first?.book_id, diagnosis: workspace.preferences,
        createdAt: Date(timeIntervalSince1970: workspace.created), updatedAt: Date(timeIntervalSince1970: workspace.updated))
      if !existingCourseIDs.contains(workspace.id) { courses.append(course) }
      if !cloudState.workspaces.contains(where: { $0.id == workspace.id }) { cloudState.workspaces.append(workspace) }
      let contents = workspace.resources.map(CloudMapping.source)
      if cloudState.localCourseSources[workspace.id] == nil { cloudState.localCourseSources[workspace.id] = contents }
      for resource in workspace.resources {
        let key = workspace.id + "::" + resource.book_id
        if locations[key] == nil { locations[key] = StudyLocation(chapterID: resource.progress.chapter_id,
          lessonID: resource.progress.lesson_id, page: resource.progress.page, completedChapterIDs: Set(resource.progress.completed_chapters)) }
        for id in resource.progress.mastered_ids where !existingCourseIDs.contains(workspace.id) {
          let raw = id.hasPrefix(resource.book_id + "::") ? String(id.dropFirst(resource.book_id.count + 2)) : id
          mastered.insert(workspace.id + "::" + resource.book_id + "::" + raw)
        }
      }
      for source in contents where !sources.contains(where: { $0.id == source.id }) { sources.append(source) }
    }
    // Reconnect verified original/OCR/independent figures to the usable local registry.
    for bookID in Set(descriptors.compactMap(\.book_id)) {
      guard let si = sources.firstIndex(where: { $0.id == bookID }) else { continue }
      let bookFiles = descriptors.filter { $0.book_id == bookID }
      var material = sources[si]
      var paths: [String: String] = [:], figures: [String: String] = [:]
      for file in bookFiles {
        guard let path = files[file.id] else { continue }
        if file.kind == "original_pdf" { paths["pdf"] = path; filePaths[bookID] = filePaths[bookID] ?? path }
        if file.relative_path?.hasSuffix("pages.jsonl") == true {
          paths["ocr_pages"] = path
          let text = try String(contentsOf: repository.directory.appendingPathComponent(path), encoding: .utf8)
          var chunks: [SourceChunk] = []
          for line in text.split(separator: "\n") {
            let page = try JSONDecoder().decode(MaterialOCRPage.self, from: Data(line.utf8))
            let content = page.cleaned_text ?? page.raw_text ?? page.text ?? page.blocks?.compactMap(\.text).joined(separator: "\n") ?? ""
            guard !content.isEmpty else { continue }
            let chapter = material.chapters.first { $0.page_start <= page.page_number && $0.page_end >= page.page_number }
            chunks.append(SourceChunk(chunk_id: "migrated:\(bookID):\(page.page_number)", book_id: bookID,
              chapter_id: chapter?.id ?? "", page_start: page.page_number, page_end: page.page_number,
              content_type: "text", text: content, asset_ids: [], key_concepts: [], source_metadata: ["origin": .string("verified_legacy_ocr")]))
          }
          if material.chunks.isEmpty { material.chunks = chunks }
        }
        if file.kind == "independent_figure" {
          let stem = URL(fileURLWithPath: file.relative_path ?? file.id).deletingPathExtension().lastPathComponent
          let localURL = repository.directory.appendingPathComponent(path)
          ContentBundle.registerMaterialURL(localURL)
          figures[stem] = path
          for index in material.assets.indices where material.assets[index].id == stem || URL(string: material.assets[index].image_url)?.deletingPathExtension().lastPathComponent == stem {
            figures[material.assets[index].id] = path
            material.assets[index].image_url = localURL.absoluteString
            material.assets[index].thumbnail_url = localURL.absoluteString
            material.assets[index].source_page_image_url = nil
          }
        }
      }
      material.assets = material.assets.filter { figures[$0.id] != nil }
      sources[si] = material
      for id in cloudState.localCourseSources.keys {
        if let index = cloudState.localCourseSources[id]?.firstIndex(where: { $0.id == bookID }) {
          cloudState.localCourseSources[id]?[index].chunks = material.chunks
          cloudState.localCourseSources[id]?[index].assets = material.assets
        }
      }
      var publicOnly = material
      publicOnly.lessons = []; publicOnly.flashcards = []; publicOnly.quizzes = []; publicOnly.plan.tasks = []
      if try await repository.loadMaterialRecords(scope: scope).contains(where: { $0.bookID == bookID }) == false {
        try await repository.saveMaterialRecord(LocalMaterialRecord(groupID: "legacy-private", version: "migration", bookID: bookID,
          files: paths, figurePaths: figures, source: publicOnly, diagnostics: [], grading: []), scope: scope)
      }
    }
    if preferences == nil { preferences = bootstrap.learner_preferences }
    if selectedCourseID == nil {
      selectedCourseID = courses.first(where: { $0.id == bootstrap.selection?.workspace_id })?.id ?? courses.first?.id
      if let resourceID = bootstrap.selection?.resource_id {
        selectedBookID = bootstrap.workspaces.flatMap(\.resources).first(where: { $0.id == resourceID })?.book_id
      }
      if selectedBookID == nil { selectedBookID = courses.first(where: { $0.id == selectedCourseID })?.activeResourceID }
    }
    cloudState.onboardingComplete = !cloudState.requiresPersonalization && preferences?.hasCompletedOnboarding == true
    var objects = try await repository.loadAgentLearningObjects(scope: scope)
    // Stable presentation records make old submissions and diagnosis available to local tools/UI.
    for workspace in bootstrap.workspaces {
      for resource in workspace.resources {
        let path = resource.session_id.map { "/api/interviews/" + $0 + "/learning-records" } ?? resource.path + "/catalog-records"
        let evidence: CloudLearningRecords
        do { evidence = try await cloud.request(path) }
        catch let error as CloudError where error.status == 404 { continue }
        for event in evidence.evidence where !objects.contains(where: { $0.id == event.id }) {
          let data: [String: JSONValue] = ["id": .string(event.id), "title": .string(event.title), "kind": .string(event.kind),
            "chapter": .string(event.chapter), "score": .number(event.score), "at": .string(event.at),
            "answer": event.answer.map(JSONValue.string) ?? .null, "pages": .array(event.pages.map { .number(Double($0)) })]
          let wire: [String: JSONValue] = ["id": .string(event.id), "revision": .number(1), "kind": .string("submitted_answer"),
            "course_id": .string(workspace.id), "course_resource_id": .string(resource.id), "data": .object(data)]
          objects.append(try JSONDecoder().decode(AgentLearningObject.self, from: JSONEncoder().encode(wire)))
          if event.score < 0.8, !mistakes.contains(where: { $0.id == event.id }) {
            mistakes.append(MistakeRecord(mistake_id: event.id, user_id: cloudState.account?.user_id ?? "",
              book_id: resource.book_id, assignment_id: event.id, question: event.title, answer: event.answer ?? "",
              stuck_point: event.chapter, knowledge_points: [event.chapter], citation_ids: [], course_id: workspace.id))
          }
        }
      }
    }
    func unpack(_ value: JSONValue?) -> [String: JSONValue]? {
      if case .object(let object)? = value { return object }
      if let raw = value?.stringValue, let data = raw.data(using: .utf8) { return try? JSONDecoder().decode([String: JSONValue].self, from: data) }
      return nil
    }
    var importedNoteIDs = Set<String>()
    for record in records where !record.deleted {
      let row = record.payload
      switch record.kind {
      case "account_learning_preferences":
        if preferences == nil {
          preferences = LearnerPreferences(displayName: "", primaryGoal: row["goal"]?.stringValue,
            dailyTime: nil, learningStage: row["stage"]?.stringValue, onboardingVersion: 0)
        }
      case "native_creation_drafts":
        guard let id = row["workspace_id"]?.stringValue, !cloudState.localDeletedCourseIDs.contains(id),
          !importState.courses.contains(where: { $0.id == id }), let payload = unpack(row["payload"]),
          case .array(let books)? = payload["book_ids"] else { continue }
        let bookIDs = books.compactMap(\.stringValue)
        var answers: [String: [String]] = [:]
        if case .object(let values)? = payload["preferences"] {
          for (key, value) in values { if case .array(let items) = value { answers[key] = items.compactMap(\.stringValue) } }
        }
        let complete = bookIDs.allSatisfy { bookID in sources.contains { $0.id == bookID && !$0.chapters.isEmpty } }
        let draft = CourseDraft(name: payload["name"]?.stringValue ?? "迁回课程", resourceIDs: bookIDs, diagnosis: answers,
          step: Int(payload["step"]?.numberValue ?? 0), importCourseID: id, parseCompleted: complete)
        let job = ImportJob(bookIDs: bookIDs, status: complete ? .completed : .interrupted,
          progress: complete ? 1 : 0, message: complete ? "旧版目录已迁回" : "旧版未完成整理已归档")
        importState.courses.append(CourseImportRecord(id: id, draft: draft,
          books: bookIDs.map { CourseImportBook(id: $0, status: complete ? .completed : .interrupted) },
          job: job, setupCompleted: payload["confirmed"] == .bool(true)))
      case "native_learning_objects":
        guard let id = row["id"]?.stringValue, !objects.contains(where: { $0.id == id }), let data = unpack(row["payload"]) else { continue }
        var object = row
        object["data"] = .object(data)
        if let decoded = try? JSONDecoder().decode(AgentLearningObject.self, from: JSONEncoder().encode(object)) { objects.append(decoded) }
      case "course_bundles":
        guard let data = unpack(row["bundle_json"]), let sessionID = row["session_id"]?.stringValue,
          let workspace = bootstrap.workspaces.first(where: { $0.resources.contains(where: { $0.session_id == sessionID }) }),
          let resource = workspace.resources.first(where: { $0.session_id == sessionID }),
          case .array(let practices)? = data["practice_items"] else { continue }
        func graded(_ source: SourceContent) -> SourceContent {
          var value = source
          for i in value.quizzes.indices where value.quizzes[i].answer.isEmpty {
            guard let item = practices.compactMap({ entry -> [String: JSONValue]? in
              if case .object(let object) = entry { return object }; return nil
            }).first(where: { $0["item_id"]?.stringValue == value.quizzes[i].id }) else { continue }
            if case .array(let ids)? = item["correct_option_ids"], !value.quizzes[i].choices.isEmpty {
              value.quizzes[i].answer = ids.compactMap(\.stringValue).joined(separator: ",")
            } else { value.quizzes[i].answer = item["expected_answer"]?.stringValue ?? "" }
            if case .array(let rubric)? = item["rubric"] { value.quizzes[i].explanation = rubric.compactMap(\.stringValue).joined(separator: "\n") }
            value.quizzes[i].source_kind = "local_personal"
          }
          return value
        }
        if let index = cloudState.localCourseSources[workspace.id]?.firstIndex(where: { $0.id == resource.book_id }),
          let source = cloudState.localCourseSources[workspace.id]?[index] { cloudState.localCourseSources[workspace.id]?[index] = graded(source) }
        if selectedCourseID == workspace.id, let index = sources.firstIndex(where: { $0.id == resource.book_id }) { sources[index] = graded(sources[index]) }
      case "interview_sessions":
        guard let data = unpack(row["session_json"]), let id = row["session_id"]?.stringValue,
          !objects.contains(where: { $0.id == id }), let workspace = bootstrap.workspaces.first(where: { $0.resources.contains(where: { $0.session_id == id }) }) else { continue }
        let resource = workspace.resources.first { $0.session_id == id }
        let wire: [String: JSONValue] = ["id": .string(id), "revision": row["revision"] ?? .number(0),
          "kind": .string("diagnostic"), "course_id": .string(workspace.id),
          "course_resource_id": resource.map { .string($0.id) } ?? .null, "data": .object(data)]
        objects.append(try JSONDecoder().decode(AgentLearningObject.self, from: JSONEncoder().encode(wire)))
      case "native_daily_plans":
        guard let wid = row["workspace_id"]?.stringValue, let day = row["day"]?.stringValue,
          dailyPlanState.plans[DailyStudyPlanState.key(wid, day)] == nil,
          let data = unpack(row["payload"]), let plan = try? JSONDecoder().decode(CloudStudyPlan.self, from: JSONEncoder().encode(data)) else { continue }
        dailyPlanState.plans[DailyStudyPlanState.key(wid, day)] = plan
      case "studio_notes":
        guard let data = unpack(row["data"]), let id = row["id"]?.stringValue, !notes.contains(where: { $0.id == id }) else { continue }
        let book = row["book_id"]?.stringValue ?? data["book_id"]?.stringValue ?? ""
        let courseID = data["course_id"]?.stringValue
        let drawing = try await importLegacyStrokes(data["strokes"])
        let media = descriptors.first { file in file.kind == "private_media" && (file.relative_path?.contains(id) == true)
          && ["m4a", "mp3", "wav"].contains(URL(fileURLWithPath: file.relative_path ?? "").pathExtension.lowercased()) }.flatMap { files[$0.id] }
        let page: Int
        if case .array(let pages)? = data["pages"] { page = Int(pages.first?.numberValue ?? 1) } else { page = 1 }
        let note = StudyNote(id: id, kind: drawing != nil ? .ink : media != nil ? .voice : .text, title: data["title"]?.stringValue ?? "迁回笔记",
          body: data["text"]?.stringValue ?? data["body"]?.stringValue ?? data["excerpt"]?.stringValue ?? "",
          anchor: NoteAnchor(courseID: courseID, courseResourceID: data["course_resource_id"]?.stringValue,
            bookID: book, chapterID: data["chapter_id"]?.stringValue, page: page),
          audioPath: media, duration: data["audio_duration_seconds"]?.numberValue ?? 0,
          transcript: data["transcript"]?.stringValue, drawingPath: drawing)
        try await repository.adoptNotes([note], scope: scope)
        if let stored = try await repository.loadStoredNote(id, scope: scope) { notes.append(stored.note); importedNoteIDs.insert(id) }
      default: break
      }
    }
    for record in records where record.kind == "studio_jobs" && record.payload["status"]?.stringValue == "succeeded" {
      guard let data = unpack(record.payload["data"]), let id = data["note_id"]?.stringValue,
        importedNoteIDs.contains(id), let result = unpack(record.payload["result"]),
        let index = notes.firstIndex(where: { $0.id == id }) else { continue }
      var note = notes[index]
      if note.transcript == nil { note.transcript = result["transcript"]?.stringValue }
      if note.organizedText == nil { note.organizedText = result["polished"]?.stringValue }
      let stored = try await repository.mutateNote(note, scope: scope)
      notes[index] = stored.note
    }
    try await repository.saveAgentLearningObjects(objects, scope: scope)
    assistantState.learningObjects = objects
    installLocalCourseContent(selectedCourseID ?? "")
    // Explicit migration reads the complete legacy chat; routine Agent history never syncs it back.
    guard historyEpoch == cloudState.epoch, scope == assistantHistoryScope else { throw CancellationError() }
    struct RemoteSession: Decodable { var session_id: String; var title: String; var state: String; var input_tokens: Int; var messages: [[String: JSONValue]]? }
    let list: [RemoteSession] = try await cloud.request("/api/assistant/conversations")
    guard historyEpoch == cloudState.epoch, scope == assistantHistoryScope else { throw CancellationError() }
    let archive = try await repository.loadAssistantHistory(scope: scope) ?? AssistantHistoryArchive()
    var imported: [AssistantSession] = []
    for item in list where !archive.sessions.contains(where: { $0.id == item.session_id }) && !archive.removedIDs.contains(item.session_id) {
      let detail: RemoteSession = try await cloud.request("/api/assistant/conversations/" + item.session_id)
      guard historyEpoch == cloudState.epoch, scope == assistantHistoryScope else { throw CancellationError() }
      var session = AssistantSession(id: detail.session_id, title: detail.title,
        context: AssistantSessionContext(courseID: nil, bookID: "", bookIDs: [], chapterID: nil,
          courseTitle: "迁回对话", chapterTitle: "", prompts: [], discovery: false))
      session.capacityState = detail.state == "archived" ? "read_only" : detail.state
      session.contextTokens = detail.input_tokens
      session.messages = (detail.messages ?? []).enumerated().compactMap { index, message in
        guard let role = message["role"]?.stringValue, ["user", "assistant"].contains(role) else { return nil }
        let text = message["display_text"]?.stringValue ?? message["content"]?.stringValue ?? ""
        guard !text.isEmpty else { return nil }
        return ChatMessage(id: detail.session_id + ":legacy:\(index)", role: role, text: text)
      }
      imported.append(session)
    }
    try await repository.importAssistantHistorySessions(imported, scope: scope)
    guard historyEpoch == cloudState.epoch, scope == assistantHistoryScope else { throw CancellationError() }
    await assistantHistory.load(scope: scope)
  }
  private func importLegacyStrokes(_ value: JSONValue?) async throws -> String? {
    guard case .array(let rows)? = value, !rows.isEmpty else { return nil }
    var strokes: [PKStroke] = []
    for row in rows {
      guard case .object(let stroke) = row, case .array(let points)? = stroke["points"], !points.isEmpty else { continue }
      let width = max(1, stroke["width"]?.numberValue ?? 4)
      let control: [PKStrokePoint] = points.enumerated().compactMap { index, item in
        guard case .object(let point) = item, let x = point["x"]?.numberValue, let y = point["y"]?.numberValue else { return nil }
        return PKStrokePoint(location: CGPoint(x: x, y: y), timeOffset: Double(index) * 0.01,
          size: CGSize(width: width, height: width), opacity: 1, force: point["p"]?.numberValue ?? 0.5, azimuth: 0, altitude: .pi / 2)
      }
      guard !control.isEmpty else { continue }
      let color: UIColor
      switch stroke["color"]?.stringValue { case "#7655c9": color = .purple; case "#23836e": color = .systemGreen; default: color = .darkGray }
      strokes.append(PKStroke(ink: PKInk(.pen, color: color), path: PKStrokePath(controlPoints: control, creationDate: .now), transform: .identity, mask: nil))
    }
    guard !strokes.isEmpty else { return nil }
    return try await repository.saveAttachment(PKDrawing(strokes: strokes).dataRepresentation(), extension: "drawing")
  }
}

private extension JSONEncoder {
  static var sortedLocal: JSONEncoder { let value = JSONEncoder(); value.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]; return value }
}
