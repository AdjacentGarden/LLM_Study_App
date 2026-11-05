import Foundation
import PDFKit
import UIKit
import UniformTypeIdentifiers
import ZIPFoundation

/// Every entry point receives this same registry. IDs are checked against account-owned
/// business objects before reads or navigation; model arguments never become filesystem paths.
@MainActor enum UnifiedAgentTools {
  nonisolated static let names = ["list_courses", "read_course_catalog", "search_discovery", "acquire_course",
    "read_material", "search_learning_materials", "read_attachment", "read_learning_data", "navigate", "capture_screen",
    "save_note", "edit_note", "delete_note", "open_note", "create_flashcards", "create_review_task",
    "update_study_plan", "remove_course", "ask_user"]

  static func label(_ name: String) -> String {
    ["save_note": "保存笔记", "open_note": "打开笔记", "ask_user": "询问你的选择", "list_courses": "查看已保存课程", "read_course_catalog": "读取课程目录", "search_discovery": "检索发现页课程",
     "acquire_course": "获取课程", "read_material": "读取课程资料", "search_learning_materials": "跨教材检索原文", "read_attachment": "读取本会话附件",
     "read_learning_data": "读取学习记录", "navigate": "打开目标页面", "capture_screen": "查看当前界面",
     "edit_note": "更新笔记", "delete_note": "删除笔记", "create_flashcards": "创建闪卡",
     "create_review_task": "创建复习任务", "update_study_plan": "更新学习计划", "remove_course": "移除课程"][name] ?? "执行操作"
  }

  static func json<T: Encodable>(_ value: T) throws -> Any {
    try JSONSerialization.jsonObject(with: JSONEncoder().encode(value))
  }
  static func encoded(_ value: [String: Any]) throws -> String { try AgentDigest.canonicalJSON(value) }
  static func integer(_ args: [String: JSONValue], _ name: String, default fallback: Int = 0) -> Int {
    if case .number(let number)? = args[name], number.isFinite { return Int(number) }
    return fallback
  }
  /// JSONValue stores numbers as Double; accept only exact, positive page integers.
  static func materialPage(_ value: Any?) -> Int? {
    guard let number = value as? Double, number.isFinite,
      let page = Int(exactly: number), page > 0 else { return nil }
    return page
  }
  static func required(_ args: [String: JSONValue], _ name: String) throws -> String {
    guard let text = args[name]?.stringValue, !text.isEmpty else { throw AgentValidationError(message: "缺少参数：" + name) }
    return text
  }
  static func course(_ args: [String: JSONValue], store: AppStore) throws -> Course {
    let id = try required(args, "course_id")
    guard let course = store.courses.first(where: { $0.id == id }) else { throw AgentValidationError(message: "课程不存在或不属于当前账号") }
    return course
  }
  static func source(_ args: [String: JSONValue], store: AppStore) throws -> SourceContent {
    let course = try course(args, store: store)
    let id = args["book_id"]?.stringValue ?? course.activeResourceID ?? course.resourceIDs.first ?? ""
    guard course.resourceIDs.contains(id), let source = store.sources.first(where: { $0.id == id }) else {
      throw AgentValidationError(message: "课程资料尚未就绪或无权读取")
    }
    return source
  }
  static func filtered<T>(_ values: [T], args: [String: JSONValue], matches: (T, String) -> Bool) -> [T] {
    guard let keyword = args["keyword"]?.stringValue, !keyword.isEmpty else { return values }
    return values.filter { matches($0, keyword) }
  }
  static func page(_ values: [Any], args: [String: JSONValue]) -> [String: Any] {
    let offset = max(0, integer(args, "offset")), limit = min(500, max(1, integer(args, "limit", default: 100)))
    let items = Array(values.dropFirst(offset).prefix(limit)), end = offset + items.count
    return ["items": items, "total": values.count, "offset": offset, "complete": end >= values.count,
      "next_offset": end < values.count ? end as Any : NSNull()]
  }

  static func execute(_ call: AgentToolCall, run: inout AgentRunRecord, store: AppStore) async throws -> String {
    guard run.scope == store.noteScope, !run.cancelRequested, run.capacityState != "archived" else { throw CancellationError() }
    let args = AgentRuntime.arguments(call), epoch = store.cloudState.epoch
    switch call.name {
    case "list_courses":
      // AppStore's course registry already merges cloud workspaces with pending local imports by stable ID.
      let values = store.courses.map { course -> [String: Any] in
        ["course_id": course.id, "name": course.name, "status": store.courseCreationLabel(course) ?? "ready",
         "resource_ids": course.resourceIDs, "resources": course.resourceIDs.map { bookID -> [String: Any] in
           ["book_id": bookID, "course_resource_id": store.courseResourceID(courseID: course.id, bookID: bookID) ?? "",
            "status": store.sources.first { $0.id == bookID }?.summary.status ?? "not_ready"]
         }]
      }
      var result = page(values, args: args)
      let accountIDs = Set(store.cloudState.workspaces.map(\.id))
      let accountCount = store.courses.filter { course in
        accountIDs.contains(course.id) || store.courseImport(for: course.id).map { accountIDs.contains(store.workspaceID(for: $0)) } == true
      }.count
      result["total_courses"] = values.count
      result["account_course_count"] = accountCount
      result["device_pending_course_count"] = values.count - accountCount
      result["creation_in_progress_count"] = store.courses.filter { store.courseImport(for: $0.id)?.setupCompleted == false ||
        store.courseImport(for: $0.id).map { $0.setupCompleted == nil } == true }.count
      result["count_note"] = "总数按本机课程列表去重，包含创建中的课程；同步到账号不代表已经完成创建或开始学习。"
      return try encoded(result)

    case "read_course_catalog":
      let target = try course(args, store: store)
      if run.privateTransport != true, store.cloudState.workspaces.contains(where: { $0.id == target.id }) { try await store.hydrateCloudContent(workspaceID: target.id) }
      guard epoch == store.cloudState.epoch else { throw CancellationError() }
      let ids = args["book_id"]?.stringValue.map { [$0] } ?? target.resourceIDs
      guard Set(ids).isSubset(of: Set(target.resourceIDs)) else { throw AgentValidationError(message: "资料不属于指定课程") }
      let resources: [[String: Any]] = try ids.map { id in
        guard let source = store.sources.first(where: { $0.id == id }) else {
          return ["book_id": id, "status": "not_ready", "complete": false]
        }
        return ["book_id": id, "title": source.summary.title,
          "course_resource_id": store.courseResourceID(courseID: target.id, bookID: id) ?? "",
          "status": source.chapters.isEmpty ? (source.summary.status == "ready" ? "no_chapters" : "not_generated") : "complete",
          "chapters": try json(source.chapters), "complete": true]
      }
      return try encoded(["course_id": target.id, "show_outline": args["kind"]?.stringValue == "outline", "resources": resources, "complete": resources.allSatisfy { $0["complete"] as? Bool == true }])

    case "search_discovery":
      try await store.refreshCommunity()
      guard epoch == store.cloudState.epoch else { throw CancellationError() }
      var books = store.community
      for key in ["keyword", "title", "subject", "grade", "version"] {
        if let query = args[key]?.stringValue, !query.isEmpty {
          books = books.filter { book in
            let text: String
            switch key { case "subject": text = book.subject; case "grade": text = book.grade; case "version": text = book.version
            default: text = [book.title, book.catalogTitle, book.subject, book.grade, book.version, book.description].joined(separator: " ") }
            return text.localizedCaseInsensitiveContains(query)
          }
        }
      }
      let values = try books.map { book -> [String: Any] in
        var object = try json(book) as? [String: Any] ?? [:]
        object["catalog_id"] = book.id
        object["source"] = book.owner
        object["saved"] = book.catalog_origin.map { origin in store.courses.contains { $0.resourceIDs.contains(origin.book_id) } } ?? false
        object["open_target"] = "communityBook"
        return object
      }
      return try encoded(page(values, args: args))

    case "acquire_course":
      let id = try required(args, "catalog_id")
      if run.privateTransport == true {
        let result = try await store.acquirePublicCourseGroup(catalogID: id, name: args["title"]?.stringValue ?? store.community.first(where: { $0.id == id })?.title ?? "我的课程")
        return try encoded(["status": "downloaded", "course_id": result.id, "source": "local_library"])
      }
      // Re-read visibility before acquisition, so a withdrawn search result cannot be imported.
      try await store.refreshCommunity()
      guard let book = store.community.first(where: { $0.id == id }), let cloud = store.cloud else { throw AgentValidationError(message: "该课程已撤回或当前无法获取") }
      guard let bookID = book.catalog_origin?.book_id else { throw AgentValidationError(message: "课程目录缺少教材来源，请刷新后重试") }
      let existing = store.courses.first { $0.resourceIDs.contains(bookID) }
      let courseID: String
      if let existing { courseID = existing.id }
      else {
        let operationID = "workspace_" + String(AgentDigest.sha256(run.id + call.id).prefix(32))
        let workspace: CloudWorkspace = try await cloud.request("/api/native/catalog/\(id)/import", method: "POST",
          body: ["id": .string(operationID), "name": .string(book.title)])
        guard epoch == store.cloudState.epoch else { throw CancellationError() }
        courseID = workspace.id
        try await store.reloadCloud()
      }
      guard epoch == store.cloudState.epoch else { throw CancellationError() }
      do {
        try await store.ensureCloudPDF(bookID: bookID)
        guard epoch == store.cloudState.epoch else { throw CancellationError() }
        guard store.sourceFileURL(bookID) != nil else { throw AgentValidationError(message: "原文件未保存到设备") }
        await store.persistTask?.value
        if let error = store.cloudState.cacheWriteError { throw ContentError.storage(error) }
      } catch {
        guard epoch == store.cloudState.epoch, !Task.isCancelled else { throw CancellationError() }
        return try encoded(["status": "partial", "course_id": courseID, "added": true,
          "original_file": "download_failed", "error": error.localizedDescription])
      }
      return try encoded(["status": existing == nil ? "added" : "already_added", "course_id": courseID, "original_file": "downloaded"])

    case "search_learning_materials":
      return try await searchMaterials(args, run: &run, store: store)

    case "read_material":
      let targetCourse = try course(args, store: store)
      if run.privateTransport != true, store.cloudState.workspaces.contains(where: { $0.id == targetCourse.id }) { try await store.hydrateCloudContent(workspaceID: targetCourse.id) }
      guard epoch == store.cloudState.epoch else { throw CancellationError() }
      let content = try source(args, store: store), courseID = targetCourse.id
      let pageNumber = max(1, integer(args, "page", default: 1)), chapterID = args["chapter_id"]?.stringValue
      if args["mode"]?.stringValue == "image" {
        if run.privateTransport == true {
          guard let id = args["asset_id"]?.stringValue, let asset = content.assets.first(where: { $0.id == id }) else {
            throw AgentValidationError(message: "请选择已下载的独立教材插图；不生成整页图片")
          }
          let data = try await store.localFigureData(bookID: content.id, assetID: id)
          return try addImage(data, id: "local-figure-" + String(AgentDigest.sha256(content.id + id).prefix(24)),
            objectID: id, sourceType: "textbook", page: asset.page, version: AgentRuntime.sourceVersion(for: content.summary),
            courseID: courseID, run: &run, bookID: content.id, courseResourceID: store.courseResourceID(courseID: courseID, bookID: content.id))
        }
        let data: Data
        let objectID: String
        var imagePage = pageNumber
        var renderedPage = false
        if let assetID = args["asset_id"]?.stringValue {
          guard let asset = content.assets.first(where: { $0.id == assetID }) else { throw AgentValidationError(message: "未找到指定插图") }
          imagePage = asset.page ?? pageNumber
          if let url = ContentBundle.url(asset.image_url) { data = try Data(contentsOf: url) }
          else if let cloud = store.cloud {
            do { data = try await cloud.downloadAssistantSourceAsset(asset.image_url, bookID: content.id) }
            catch { renderedPage = true; data = try await originalImage(bookID: content.id, page: imagePage, store: store) }
          } else { renderedPage = true; data = try await originalImage(bookID: content.id, page: imagePage, store: store) }
          objectID = renderedPage ? content.id : asset.id
        } else {
          data = try await originalImage(bookID: content.id, page: pageNumber, store: store)
          objectID = content.id
        }
        return try addImage(data, id: "material-" + String(AgentDigest.sha256(courseID + "|" + objectID + "|\(imagePage)|" + AgentRuntime.sourceVersion(for: content.summary)).prefix(24)), objectID: objectID, sourceType: "textbook", page: imagePage,
          version: AgentRuntime.sourceVersion(for: content.summary), courseID: courseID, run: &run, bookID: content.id, courseResourceID: store.courseResourceID(courseID: courseID, bookID: content.id))
      }
      // Some indexes store subsection text under its parent. Use the directory's
      // PDF bounds to distinguish siblings sharing that parent.
      let chapters = Dictionary(content.chapters.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
      func lineage(_ id: String?) -> Set<String> {
        var chain = Set<String>()
        var next = id
        while let current = next, chain.insert(current).inserted { next = chapters[current]?.parent_id }
        return chain
      }
      let ancestors = lineage(chapterID)
      func matchesChapter(_ indexedID: String?, pages: ClosedRange<Int>?) -> Bool {
        guard let chapterID else { return true }
        if lineage(indexedID).contains(chapterID) { return true }
        guard let chapter = chapters[chapterID], let pages,
          chapter.page_start > 0, chapter.page_end >= chapter.page_start,
          indexedID?.isEmpty != false || indexedID.map(ancestors.contains) == true else { return false }
        return pages.overlaps(chapter.page_start...chapter.page_end)
      }
      let requestedPage = args["page"] == nil ? nil : pageNumber
      let chunks = filtered(content.chunks.filter { chunk in
        let pages = chunk.page_start...max(chunk.page_start, chunk.page_end)
        return matchesChapter(chunk.chapter_id, pages: pages) && (requestedPage.map(pages.contains) ?? true)
      }, args: args) { $0.text.localizedCaseInsensitiveContains($1) }
      // Register each returned original passage as independently verifiable evidence.
      let returnedChunks = Array(chunks.dropFirst(max(0, integer(args, "offset"))).prefix(min(500, max(1, integer(args, "limit", default: 100)))))
      var passages: [[String: Any]] = []
      for chunk in returnedChunks {
        var evidence = AgentRuntime.evidence(for: AgentSelection(bookID: content.id, courseID: courseID, chapterID: chunk.chapter_id,
          page: chunk.page_start, printedPage: chunk.printed_page_start, text: chunk.text), sourceVersion: AgentRuntime.sourceVersion(for: content.summary))
        evidence.courseResourceID = store.courseResourceID(courseID: courseID, bookID: content.id)
        evidence.sourceType = "textbook"
        evidence.objectID = chunk.id
        var passage = try json(chunk) as? [String: Any] ?? [:]
        passage["evidence_id"] = evidence.id
        passages.append(passage)
        if !run.evidence.contains(where: { $0.id == evidence.id }) { run.evidence.append(evidence) }
      }
      var result = page(chunks, args: args)
      result["items"] = passages
      result["book_id"] = content.id
      result["page_count"] = content.summary.page_count
      let passageRanges = returnedChunks.map { $0.page_start...max($0.page_start, $0.page_end) }
      let linkedAssets = Set(returnedChunks.flatMap(\.asset_ids))
      let assets = content.assets.filter { asset in
        if let requestedPage { return asset.page == requestedPage && matchesChapter(asset.chapter_id, pages: requestedPage...requestedPage) }
        if linkedAssets.contains(asset.id) { return true }
        guard matchesChapter(asset.chapter_id, pages: asset.page.map { $0...$0 }) else { return false }
        if let chapterID, chapters[chapterID] != nil, args["keyword"] == nil { return true }
        return asset.page.map { page in passageRanges.contains { $0.contains(page) } } ?? false
      }
      result["assets"] = assets.map { asset -> [String: Any] in
        var descriptor: [String: Any] = ["asset_id": asset.id, "type": asset.type, "caption": String(asset.caption.prefix(240))]
        descriptor["page"] = asset.page
        return descriptor
      }
      result["source_version"] = AgentRuntime.sourceVersion(for: content.summary)
      return try encoded(result)

    case "read_attachment":
      let id = try required(args, "attachment_id")
      guard let file = run.attachments?.first(where: { $0.id == id }) else { throw AgentValidationError(message: "附件不属于当前会话") }
      let pageNumber = max(1, integer(args, "page", default: 1))
      if args["mode"]?.stringValue == "image" {
        let data = try fileImage(url: store.repository.attachmentURL(file.path), page: pageNumber)
        return try addImage(data, id: id + "-p\(pageNumber)", objectID: id, sourceType: "attachment", page: pageNumber,
          version: AgentDigest.sha256(file.path), courseID: nil, run: &run, attachmentPath: file.path)
      }
      let text: String
      let fileURL = store.repository.attachmentURL(file.path)
      if args["page"] != nil, fileURL.pathExtension.lowercased() == "pdf" {
        guard let document = PDFDocument(url: fileURL), let page = document.page(at: pageNumber - 1) else { throw AgentValidationError(message: "附件页码超出范围") }
        text = page.string ?? ""
      } else if args["page"] != nil, ["docx", "pptx", "xlsx"].contains(fileURL.pathExtension.lowercased()) {
        let extracted = try await LocalFileImporter.extract(url: fileURL, bookID: id, filename: file.filename)
        text = extracted.chunks.filter { $0.page_start <= pageNumber && $0.page_end >= pageNumber }.map(\.text).joined(separator: "\n")
      } else if let path = file.textPath { text = try String(contentsOf: store.repository.attachmentURL(path), encoding: .utf8) }
      else { text = file.text }
      let offset = max(0, integer(args, "offset")), limit = min(100_000, max(1, integer(args, "limit", default: 12_000)))
      let part = String(text.dropFirst(offset).prefix(limit)), end = offset + part.count
      let evidenceID = "ev-" + String(AgentDigest.sha256(id + "|\(pageNumber)|\(offset)|" + part).prefix(24))
      if !part.isEmpty, !run.evidence.contains(where: { $0.id == evidenceID }) {
        run.evidence.append(AgentEvidence(id: evidenceID, bookID: "", courseID: nil, sourceVersion: AgentDigest.sha256(file.path),
          chapterID: nil, page: pageNumber, printedPage: nil, quote: part, quoteSHA256: AgentDigest.sha256(part),
          attachmentPath: file.path, sourceType: "attachment", objectID: id))
      }
      return try encoded(["attachment_id": id, "filename": file.filename, "text": part, "evidence_id": part.isEmpty ? NSNull() : evidenceID as Any,
        "status": part.isEmpty ? "no_text_use_image" : "ok", "total_characters": text.count, "offset": offset, "complete": end >= text.count,
        "next_offset": end < text.count ? end as Any : NSNull(), "page_count": file.pageCount ?? 1])

    case "read_learning_data":
      if run.privateTransport == true {
        let result = try await store.readLocalPersonalLearning(args: args)
        return try encoded(AgentRuntime.foundationObject(.object(result)) as? [String: Any] ?? [:])
      }
      if args["kind"]?.stringValue == "preparation" {
        return try await readPreparation(args, store: store)
      }
      return try await readLearning(args, run: &run, store: store)

    case "navigate":
      return try await navigate(args, store: store)

    case "capture_screen":
      return try await capture(args, run: &run, store: store)

    case "edit_note":
      let id = try required(args, "note_id")
      guard let scope = store.noteScope else { throw NoteStoreError.scopeMismatch }
      let row = try await store.repository.commitAgentNoteEdit(scope: scope, operationID: run.id + "." + call.id,
        noteID: id, revision: integer(args, "revision", default: -1), title: args["title"]?.stringValue, body: args["body"]?.stringValue)
      store.applyCommitted([row.stored], scope: scope)
      return try encoded(["status": "saved", "note_id": id, "revision": row.committedRevision])

    case "delete_note", "remove_course":
      throw AgentValidationError(message: "删除需要在对话中展示名称、影响范围并由用户明确确认。请先使用 ask_user，确认后通过对应业务操作执行。")

    case "create_flashcards", "create_review_task", "update_study_plan":
      let target = try course(args, store: store)
      var arguments = args
      let bookID = args["book_id"]?.stringValue ?? target.activeResourceID ?? target.resourceIDs.first ?? ""
      guard target.resourceIDs.contains(bookID), let resourceID = store.courseResourceID(courseID: target.id, bookID: bookID),
        args["course_resource_id"]?.stringValue == nil || args["course_resource_id"]?.stringValue == resourceID else { throw AgentValidationError(message: "写入资料不属于指定课程") }
      arguments["book_id"] = .string(bookID); arguments["course_resource_id"] = .string(resourceID)
      let selectedEvidence: [AgentEvidence]
      if case .array(let ids)? = args["evidence_ids"] {
        let requested = ids.compactMap(\.stringValue)
        selectedEvidence = requested.compactMap { run.evidence($0) }
        guard selectedEvidence.count == requested.count else { throw AgentValidationError(message: "学习产出引用不是当前任务已读取的资料") }
      } else { selectedEvidence = [] }
      let references = try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(selectedEvidence.map(NoteReference.init)))
      arguments["source_references"] = references
      if case .array(let cards)? = arguments["cards"] {
        arguments["cards"] = .array(cards.map { value in
          guard case .object(var card) = value else { return value }
          card["book_id"] = .string(bookID); card["source_references"] = references
          if card["chapter_id"] == nil { card["chapter_id"] = arguments["chapter_id"] }
          return .object(card)
        })
      }
      if run.privateTransport == true {
        let result = try await store.performLocalPersonalOperation(name: call.name, args: arguments, runID: run.id, callID: call.id)
        return try encoded(AgentRuntime.foundationObject(.object(result)) as? [String: Any] ?? [:])
      }
      guard let cloud = store.cloud else { throw AgentValidationError(message: "AI 助手需要联网") }
      let operationID = run.id + "." + call.id
      let result: [String: JSONValue] = try await cloud.request("/api/assistant/tools/\(call.name)", method: "POST", body: [
        "operation_id": .string(operationID), "arguments": .object(arguments)])
      guard epoch == store.cloudState.epoch else { throw CancellationError() }
      try await store.reloadCloud()
      await store.reloadAgentLearningObjects()
      return try encoded(AgentRuntime.foundationObject(.object(result)) as? [String: Any] ?? [:])

    default: throw AgentValidationError(message: "未知工具：" + call.name)
    }
  }

  private static let localBM25 = AppleTextbookSearch(useSystemEmbeddings: false)

  /// Search only the phone's account-scoped downloaded text. No cloud fallback.
  private static func searchMaterials(_ args: [String: JSONValue], run: inout AgentRunRecord, store: AppStore) async throws -> String {
    let query = try required(args, "query"), scope = store.noteScope
    let courseIDs = AgentRuntime.strings(args["course_ids"])
    let books = AgentRuntime.strings(args["book_ids"])
    if let courseIDs, !Set(courseIDs).isSubset(of: Set(store.courses.map(\.id))) {
      throw AgentValidationError(message: "检索课程不存在或不属于本机账号")
    }
    let courses = store.courses.filter { courseIDs == nil || courseIDs!.contains($0.id) }
    let available = Set(courses.flatMap(\.resourceIDs))
    if let books, !Set(books).isSubset(of: available) { throw AgentValidationError(message: "检索教材不属于指定课程或本机资料库") }
    let selected = books.map(Set.init) ?? available
    var sources = store.sources.filter { selected.contains($0.id) }
    if let chapterID = args["chapter_id"]?.stringValue {
      sources = sources.map { original in
        var source = original
        guard let chapter = source.chapters.first(where: { $0.id == chapterID }) else { source.chunks = []; return source }
        source.chunks = source.chunks.filter { $0.page_start <= chapter.page_end && $0.page_end >= chapter.page_start }
        return source
      }
    }
    let missing = selected.subtracting(Set(sources.filter { !$0.chunks.isEmpty }.map(\.id))).sorted()
    let matches = try await localBM25.search(query: query, sources: sources, bookIDs: selected,
      limit: min(12, max(1, integer(args, "limit", default: 8))))
    try Task.checkCancellation()
    guard scope == store.noteScope else { throw CancellationError() }
    var evidenceRows: [[String: Any]] = [], imageRows: [[String: Any]] = []
    var loadedAssets = Set<String>()
    for hit in matches.hits where hit.reliable {
      let chunk = hit.chunk
      guard let source = sources.first(where: { $0.id == chunk.book_id }), let course = courses.first(where: { $0.resourceIDs.contains(source.id) }) else { continue }
      var evidence = AgentRuntime.evidence(for: AgentSelection(bookID: source.id, courseID: course.id,
        chapterID: chunk.chapter_id, page: chunk.page_start, printedPage: chunk.printed_page_start, text: chunk.text),
        sourceVersion: AgentRuntime.sourceVersion(for: source.summary))
      evidence.sourceType = "textbook"; evidence.objectID = chunk.id
      evidence.courseResourceID = store.courseResourceID(courseID: course.id, bookID: source.id)
      if !run.evidence.contains(where: { $0.id == evidence.id }) { run.evidence.append(evidence) }
      var row: [String: Any] = ["evidence_id": evidence.id, "book_id": source.id, "course_id": course.id,
        "course_ids": courses.filter { $0.resourceIDs.contains(source.id) }.map(\.id), "page_number": chunk.page_start,
        "quote": chunk.text, "source_version": evidence.sourceVersion, "score": hit.score]
      let figures = source.assets.filter { chunk.asset_ids.contains($0.id) || $0.page.map { chunk.page_start <= $0 && $0 <= chunk.page_end } == true }
      row["figures"] = figures.map { ["asset_id": $0.id, "caption": $0.caption, "page": $0.page as Any? ?? NSNull(), "status": "available_to_read"] as [String: Any] }
      evidenceRows.append(row)
      if args["include_images"] == .bool(true) {
        for asset in figures where imageRows.count < 2 && loadedAssets.insert(source.id + asset.id).inserted {
          do {
            let bytes = try await store.localFigureData(bookID: source.id, assetID: asset.id)
            let payload = try addImage(bytes, id: "local-figure-" + String(AgentDigest.sha256(source.id + asset.id).prefix(24)),
              objectID: asset.id, sourceType: "textbook", page: asset.page, version: evidence.sourceVersion, courseID: course.id,
              run: &run, bookID: source.id, courseResourceID: evidence.courseResourceID)
            var image = (try JSONSerialization.jsonObject(with: Data(payload.utf8)) as? [String: Any]) ?? [:]
            image["status"] = "loaded"; image["kind"] = "independent_figure"; imageRows.append(image)
          } catch is CancellationError { throw CancellationError() }
          catch { imageRows.append(["asset_id": asset.id, "status": "unavailable", "error": error.localizedDescription]) }
        }
      }
    }
    return try encoded(["ok": true, "method": matches.method, "scope": "local_material_library",
      "searched_book_ids": sources.filter { !$0.chunks.isEmpty }.map(\.id), "unavailable_book_ids": missing,
      "scope_truncated": false, "evidence": evidenceRows, "images": imageRows,
      "images_loaded": imageRows.filter { $0["status"] as? String == "loaded" }.count,
      "scope_note": "只检索本机已保存原文；未缓存和缺失内容不代表教材没有相关知识。图片仅为独立插图，不读取整页图片。",
      "complete": missing.isEmpty])
  }

  private static func readPreparation(_ args: [String: JSONValue], store: AppStore) async throws -> String {
    let epoch = store.cloudState.epoch
    let target = try course(args, store: store)
    let record = store.courseImport(for: target.id)
    let ids = args["book_id"]?.stringValue.map { [$0] } ?? target.resourceIDs
    guard Set(ids).isSubset(of: Set(target.resourceIDs)) else { throw AgentValidationError(message: "资料不属于当前课程") }
    var tasks: [[String: Any]] = []
    for id in ids {
      let cached = record?.books.first { $0.id == id }
      var item: [String: Any] = ["book_id": id, "title": store.sources.first { $0.id == id }?.summary.title ?? target.name, "source": "unknown"]
      if let cached {
        item["source"] = "last_known"
        item["status"] = cached.status.rawValue
        item["stage"] = cached.stage
        item["message"] = cached.message
      }
      if let cloud = store.cloud, record?.onDeviceOnly != true, !id.hasPrefix("book_local_") {
        do {
          let job: CloudBookPreparationJob = try await cloud.request("/api/books/\(id)/prepare")
          item["source"] = "live"
          item["status"] = job.status.rawValue
          item["stage"] = job.stage
          item["message"] = job.message
          item["diagnostics_completed"] = job.diagnostics_completed
          item["diagnostics_total"] = job.diagnostics_total.map { $0 as Any } ?? NSNull()
          item["diagnostics_finalized"] = job.diagnostics_finalized ?? false
          item["diagnostics_skipped"] = job.diagnostics_skipped
        } catch let error as CloudError where error.status == 404 {
          if let ocr: CloudBookStatus = try? await cloud.request("/api/books/\(id)/status") {
            item["source"] = "live"
            item["status"] = ocr.status
            item["stage"] = "ocr"
            item["message"] = ocr.current_step
          }
        } catch { /* Only the local snapshot, if present, is available. */ }
      }
      try Task.checkCancellation()
      guard epoch == store.cloudState.epoch else { throw CancellationError() }
      tasks.append(item)
    }
    return try encoded(["course_id": target.id, "tasks": tasks])
  }

  static func readLearning(_ args: [String: JSONValue], run: inout AgentRunRecord, store: AppStore) async throws -> String {
    let courseID = args["course_id"]?.stringValue, objectID = args["object_id"]?.stringValue
    let kind = args["kind"]?.stringValue ?? "all", date = args["date"]?.stringValue
    if let courseID, !store.courses.contains(where: { $0.id == courseID }) { throw AgentValidationError(message: "课程不属于当前账号") }
    let courseIDs = Set(courseID.map { [$0] } ?? store.courses.map(\.id))
    let bookIDs = Set(store.courses.filter { courseIDs.contains($0.id) }.flatMap(\.resourceIDs))
    var records: [[String: Any]] = []
    var submittedWrong: [[String: Any]] = []
    var coveredResources = Set<String>()
    var failedSources = false
    if ["all", "notes", "annotations"].contains(kind) {
      for var note in store.notes where (courseID == nil || note.anchor?.courseID == courseID) && (objectID == nil || note.id == objectID) {
        note.conversation = nil // Old embedded conversations are never an agent-readable data source.
        var value = try json(note) as? [String: Any] ?? [:]
        value["record_type"] = "note"
        records.append(value)
      }
    }
    var matchingMistakes: [MistakeRecord] = []
    if ["all", "mistakes", "attempts"].contains(kind) {
      for record in store.mistakes where bookIDs.contains(record.book_id) && (courseID == nil || record.course_id == courseID)
        && (objectID == nil || record.id == objectID || record.assignment_id == objectID)
        && (date == nil || record.last_wrong_at?.hasPrefix(date!) == true) {
        matchingMistakes.append(record)
        var value = try json(record) as? [String: Any] ?? [:]
        value["record_type"] = "mistake"
        records.append(value)
      }
      if let cloud = store.cloud {
        for resource in store.cloudState.workspaces.filter({ courseIDs.contains($0.id) }).flatMap(\.resources) {
          let path: String
          if resource.source_content != nil { path = resource.path + "/catalog-records" }
          else if let session = resource.session_id { path = "/api/interviews/\(session)/learning-records" }
          else {
            failedSources = true
            records.append(["record_type": "attempts", "course_id": resource.workspace_id, "book_id": resource.book_id, "status": "not_ready"])
            continue
          }
          do {
            let result: [String: JSONValue] = try await cloud.request(path)
            let payload = AgentRuntime.foundationObject(.object(result)) as? [String: Any] ?? [:]
            if payload["complete"] as? Bool == false { failedSources = true }
            coveredResources.insert(resource.workspace_id + "::" + resource.book_id)
            for original in payload["evidence"] as? [[String: Any]] ?? [] {
              if let objectID, original["id"] as? String != objectID, original["question_id"] as? String != objectID { continue }
              if let date, (original["at"] as? String)?.hasPrefix(date) != true { continue }
              var value = original
              value["record_type"] = "submitted_attempt"
              value["course_id"] = resource.workspace_id; value["book_id"] = resource.book_id
              value["course_resource_id"] = resource.id
              records.append(value)
              if let score = value["score"] as? Double, score < 0.8 { submittedWrong.append(value) }
            }
          } catch {
            failedSources = true
            records.append(["record_type": "attempts", "course_id": resource.workspace_id, "status": "error", "error": error.localizedDescription])
          }
        }
      }
    }
    for content in store.sources where bookIDs.contains(content.id) {
      if ["all", "flashcards"].contains(kind) {
        for card in content.flashcards where objectID == nil || card.id == objectID {
          var value = try json(card) as? [String: Any] ?? [:]; value["record_type"] = "flashcard"; records.append(value)
        }
      }
      if ["all", "plan", "review_tasks"].contains(kind) {
        for task in content.plan.tasks where objectID == nil || task.id == objectID {
          var value = try json(task) as? [String: Any] ?? [:]; value["record_type"] = "study_task"; records.append(value)
        }
      }
      if ["all", "progress"].contains(kind) {
        for id in courseIDs where store.courses.first(where: { $0.id == id })?.resourceIDs.contains(content.id) == true {
          let key = "\(id)::\(content.id)"
          if let location = store.locations[key] { records.append(["record_type": "progress", "course_id": id, "book_id": content.id, "location": try json(location)]) }
        }
      }
    }
    if ["all", "plan", "review_tasks"].contains(kind), let date, let id = courseID {
      await store.loadDailyStudyPlan(day: date, workspaceID: id)
      if let plan = store.dailyPlanState.plans[DailyStudyPlanState.key(id, date)] {
        records.append(["record_type": "daily_plan", "course_id": id, "plan": try json(plan)])
      }
    }
    for object in store.assistantState.learningObjects where courseIDs.contains(object.course_id)
      && (objectID == nil || object.id == objectID) && (date == nil || object.data["date"]?.stringValue == date)
      && (kind == "all" || (kind == "flashcards" && object.kind == "flashcard") || (kind == "plan" && object.kind == "plan") || (kind == "review_tasks" && object.kind == "review_task")) {
      var record = try json(object) as? [String: Any] ?? [:]
      record["record_type"] = object.kind
      records.append(record)
    }
    if let keyword = args["keyword"]?.stringValue, !keyword.isEmpty {
      records = records.filter { ((try? encoded($0)) ?? "").localizedCaseInsensitiveContains(keyword) }
    }
    let offset = max(0, integer(args, "offset")), limit = min(500, max(1, integer(args, "limit", default: 100)))
    for record in records.dropFirst(offset).prefix(limit) {
      let object = record["attempt_id"] as? String ?? record["id"] as? String ?? record["mistake_id"] as? String ?? record["task_id"] as? String
      guard let object else { continue }
      let quote = try encoded(record)
      let id = "ev-" + String(AgentDigest.sha256((record["course_id"] as? String ?? "") + "|" + object + "|" + quote).prefix(24))
      if !run.evidence.contains(where: { $0.id == id }) {
        run.evidence.append(AgentEvidence(id: id, bookID: record["book_id"] as? String ?? "", courseID: record["course_id"] as? String,
          sourceVersion: record["source_version"] as? String ?? (record["source_version"] as? Int).map(String.init) ?? (record["revision"] as? Int).map(String.init) ?? AgentDigest.sha256(quote), chapterID: record["chapter_id"] as? String,
          page: (record["pages"] as? [Int])?.first ?? 1, printedPage: nil, quote: quote, quoteSHA256: AgentDigest.sha256(quote),
          sourceType: "learning_record", objectID: object, courseResourceID: record["course_resource_id"] as? String))
      }
    }
    var result = page(records, args: args)
    let wrong = matchingMistakes.filter { !coveredResources.contains(($0.course_id ?? "") + "::" + $0.book_id) }
    let minimumAttempts = submittedWrong.count + wrong.reduce(0) { $0 + (date == nil ? ($1.error_count ?? 1) : 1) }
    let exactAttempts = !failedSources && wrong.allSatisfy { date == nil ? $0.error_count != nil : $0.error_count == 1 }
    result["wrong_attempt_count"] = exactAttempts ? minimumAttempts as Any : NSNull()
    result["wrong_attempt_count_lower_bound"] = minimumAttempts
    let remoteQuestionIDs = submittedWrong.compactMap { value -> String? in
      guard let id = value["question_id"] as? String else { return nil }
      return (value["course_id"] as? String ?? "") + "::" + (value["book_id"] as? String ?? "") + "::" + id
    }
    let localQuestionIDs = wrong.map { ($0.course_id ?? "") + "::" + $0.book_id + "::" + $0.assignment_id }
    result["distinct_wrong_question_count"] = !failedSources && remoteQuestionIDs.count == submittedWrong.count ? Set(remoteQuestionIDs + localQuestionIDs).count as Any : NSNull()
    result["statistics_complete"] = exactAttempts && remoteQuestionIDs.count == submittedWrong.count
    result["sources_complete"] = !failedSources
    result["status"] = records.isEmpty ? "no_matching_records" : "ok"
    return try encoded(result)
  }

  static func navigate(_ args: [String: JSONValue], store: AppStore) async throws -> String {
    let aliases = ["discovery": "community", "acquisition": "communityBook", "reader": "source", "review": "flashcards", "practice": "assignment", "chapter": "lesson"]
    let requested = args["page_type"]?.stringValue ?? args["target"]?.stringValue ?? ""
    guard let route = Route(rawValue: aliases[requested] ?? requested) else { throw AgentValidationError(message: "页面目标不存在") }
    let objectID = args["object_id"]?.stringValue
    if let id = args["course_id"]?.stringValue {
      guard store.courses.contains(where: { $0.id == id }) else { throw AgentValidationError(message: "课程不存在或无权限") }
      try await store.hydrateCloudContent(workspaceID: id)
      store.selectCourse(id)
    }
    if let id = args["book_id"]?.stringValue {
      guard store.currentCourse?.resourceIDs.contains(id) == true, store.sources.contains(where: { $0.id == id }) else { throw AgentValidationError(message: "课程资料尚未就绪") }
      store.selectSource(id)
    }
    if let chapter = args["chapter_id"]?.stringValue {
      guard store.chapters.contains(where: { $0.id == chapter }) else { throw AgentValidationError(message: "章节不存在或尚未生成") }
      store.selectedChapterID = chapter
      store.selectedLessonID = store.lessons.first(where: { $0.chapter_id == chapter })?.id
    }
    let coursePages: Set<Route> = [.study, .courseDetail, .lesson, .source, .flashcards, .assignment, .mistakes, .plan]
    if coursePages.contains(route), store.currentCourse == nil { throw AgentValidationError(message: "请指定可访问的课程") }
    if route == .assignment, objectID == nil, store.quizzes.isEmpty { throw AgentValidationError(message: "该章节的练习尚未生成") }
    if route == .flashcards, objectID == nil, store.flashcards.isEmpty { throw AgentValidationError(message: "该课程章节尚无闪卡") }
    if route == .diagnosis, objectID == nil, store.latestDiagnosis == nil { throw AgentValidationError(message: "尚无已提交的作答诊断") }
    let objectRoutes: Set<Route> = [.assignment, .lesson, .notes, .flashcards, .mistakes, .diagnosis, .plan, .communityBook, .communityImport]
    if objectID != nil && !objectRoutes.contains(route) {
      throw AgentValidationError(message: "该页面不支持按此对象定位")
    }
    var requestedPlanDay: String?
    switch route {
    case .assignment:
      if let objectID {
        guard let quiz = store.currentSource?.quizzes.first(where: { $0.id == objectID }),
          args["chapter_id"]?.stringValue.map({ quiz.chapter_id == $0 }) ?? true
        else { throw AgentValidationError(message: "指定练习题不存在或不属于该章节") }
        store.selectedChapterID = quiz.chapter_id
        store.learningState.agentFocusQuizID = objectID
      }
      store.go(.assignment)
    case .lesson:
      if let objectID, store.activeLesson?.id != objectID { throw AgentValidationError(message: "指定学习内容与章节不符") }
      guard let lesson = store.activeLesson, ["ready", "generated"].contains(lesson.status) else { throw AgentValidationError(message: "章节教学尚未生成") }
      store.openLesson(lesson)
    case .source:
      guard let source = store.currentSource else { throw AgentValidationError(message: "请指定课程与资料") }
      store.openSource(SourcePageTarget(bookID: source.id, title: source.summary.title, page: max(1, integer(args, "page", default: 1)),
        courseID: store.selectedCourseID, courseResourceID: store.selectedCourseID.flatMap { store.courseResourceID(courseID: $0, bookID: source.id) }))
    case .communityBook, .communityImport:
      let id = args["catalog_id"]?.stringValue ?? objectID ?? ""
      try await store.refreshCommunity()
      guard store.community.contains(where: { $0.id == id }) else { throw AgentValidationError(message: "发现页课程不存在或已撤回") }
      store.selectedCommunityID = id
      store.go(.communityBook)
    case .notes:
      if let objectID {
        guard store.notes.contains(where: { $0.id == objectID }) else { throw AgentValidationError(message: "笔记不存在") }
        store.openAgentNote(objectID)
      } else { store.go(route) }
    case .flashcards:
      if let objectID {
        store.dailyPlanState.focus = nil
        guard let card = ((store.currentSource?.flashcards ?? []) + store.agentFlashcards).first(where: { $0.id == objectID }) else { throw AgentValidationError(message: "闪卡不存在或不属于指定课程") }
        store.selectedChapterID = card.chapter_id.isEmpty ? nil : card.chapter_id
        store.learningState.agentFocusCardID = objectID
      }
      store.go(route)
    case .mistakes, .diagnosis:
      if let objectID {
        guard store.mistakes.contains(where: { $0.id == objectID && $0.course_id == store.selectedCourseID }) else { throw AgentValidationError(message: "作答记录不存在或不属于指定课程") }
        store.learningState.agentFocusMistakeID = objectID
      }
      store.go(route == .diagnosis && objectID != nil ? .mistakes : route)
    case .plan:
      let object = objectID.flatMap { id in store.assistantState.learningObjects.first { $0.id == id && $0.course_id == store.selectedCourseID } }
      if let objectID, object == nil { throw AgentValidationError(message: "计划记录不存在或不属于指定课程：" + objectID) }
      if let date = args["date"]?.stringValue ?? object?.data["date"]?.stringValue {
        await store.loadDailyStudyPlan(day: date)
        requestedPlanDay = date
        store.learningState.agentFocusPlanDate = date
      }
      store.go(route)
    default: store.go(route)
    }
    await Task.yield()
    store.globalAssistantOpen = false
    guard store.route == (route == .home ? .study : route) || (route == .communityImport && store.route == .communityBook) || (route == .diagnosis && objectID != nil && store.route == .mistakes) else {
      throw AgentValidationError(message: "页面尚未到达，请核对课程状态后重试")
    }
    func reachedTarget() -> Bool {
      switch route {
      case .assignment: return objectID == nil || store.learningState.visibleQuizID == objectID
      case .notes: return objectID == nil || store.learningState.visibleNoteID == objectID
      case .flashcards: return objectID == nil || store.learningState.visibleCardID == objectID
      case .mistakes, .diagnosis: return objectID == nil || store.learningState.visibleMistakeID == objectID
      case .plan:
        guard let date = requestedPlanDay else { return objectID == nil }
        guard store.learningState.visiblePlanDay == date else { return false }
        guard let objectID else { return true }
        return store.dailyStudyPlan(date)?.tasks.contains { task in
          task.id == objectID || store.agentTaskObject(taskID: task.id, courseID: store.selectedCourseID ?? "")?.id == objectID
        } == true
      default: return true
      }
    }
    let arrivalRoute = store.route
    for _ in 0..<50 {
      if reachedTarget() { break }
      try await Task.sleep(for: .milliseconds(50))
    }
    guard store.route == arrivalRoute, reachedTarget() else { throw AgentValidationError(message: "已切换页面，但未确认指定内容已显示") }
    return try encoded(["status": "arrived", "page_type": store.route.rawValue,
      "course_id": store.selectedCourseID ?? "", "book_id": store.selectedBookID ?? "", "chapter_id": store.selectedChapterID ?? "", "object_id": objectID ?? ""])
  }

  static func capture(_ args: [String: JSONValue], run: inout AgentRunRecord, store: AppStore) async throws -> String {
    let full = args["target"]?.stringValue == "full"
    store.assistantState.screenshotExcludesAssistant = !full
    defer { store.assistantState.screenshotExcludesAssistant = false }
    try await Task.sleep(for: .milliseconds(250))
    guard let window = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene })
      .flatMap(\.windows).first(where: { $0.isKeyWindow }) else { throw AgentValidationError(message: "当前界面无法截图") }
    let capturedRoute = store.route, capturedAt = Date()
    let capturedBookID = capturedRoute == .source ? store.sourceTarget?.bookID : store.selectedBookID
    let capturedCourseID = capturedRoute == .source ? store.sourceTarget?.courseID : store.selectedCourseID
    let capturedPage = capturedRoute == .source ? store.locations[NotesReadingPosition.key(courseID: capturedCourseID, bookID: capturedBookID ?? "")]?.page ?? store.sourceTarget?.page : nil
    let capturedChapterID = store.selectedChapterID, capturedNoteID = store.noteState.focusNoteID
    let format = UIGraphicsImageRendererFormat(); format.scale = 1
    let image = UIGraphicsImageRenderer(bounds: window.bounds, format: format).image { _ in window.drawHierarchy(in: window.bounds, afterScreenUpdates: true) }
    guard let data = image.jpegData(compressionQuality: 0.85) else { throw AgentValidationError(message: "截图生成失败") }
    let id = "screen-" + UUID().uuidString.lowercased()
    let path = try await store.repository.saveAttachment(data, extension: "jpg")
    run.events = (run.events ?? []) + [AgentRunEvent(kind: "screenshot", message: "实际截图：" + capturedRoute.title, attachmentPath: path)]
    let imageResult = try addImage(data, id: id, objectID: capturedBookID ?? capturedRoute.rawValue, sourceType: "screenshot", page: capturedPage, version: nil, courseID: capturedCourseID, run: &run, attachmentPath: path)
    var result = try JSONSerialization.jsonObject(with: Data(imageResult.utf8)) as? [String: Any] ?? [:]
    result["page_type"] = capturedRoute.rawValue; result["captured_at"] = ISO8601DateFormatter().string(from: capturedAt)
    result["chapter_id"] = capturedChapterID ?? ""
    result["note_id"] = capturedNoteID ?? ""
    result["practice_state"] = capturedRoute == .assignment ? "pending" : "not_on_practice_page"
    result["target"] = full ? "full" : "business"; result["visible_region_only"] = true
    return try encoded(result)
  }

  static func addImage(_ data: Data, id: String, objectID: String, sourceType: String, page: Int?, version: String?, courseID: String?, run: inout AgentRunRecord, bookID: String? = nil, courseResourceID: String? = nil, attachmentPath: String? = nil) throws -> String {
    guard let source = UIImage(data: data) else { throw AgentValidationError(message: "原图格式无法解码") }
    let scale = min(1, 1800 / max(source.size.width, source.size.height))
    let format = UIGraphicsImageRendererFormat(); format.scale = 1
    let image = UIGraphicsImageRenderer(size: CGSize(width: source.size.width * scale, height: source.size.height * scale), format: format)
      .image { _ in source.draw(in: CGRect(origin: .zero, size: CGSize(width: source.size.width * scale, height: source.size.height * scale))) }
    guard let bytes = image.jpegData(compressionQuality: 0.88) else { throw AgentValidationError(message: "原图转换失败") }
    var images = run.images ?? []; images.removeAll { $0.attachment_id == id }
    images.append(AssistantImageDTO(attachment_id: id, media_type: "image/jpeg", data_base64: bytes.base64EncodedString(), source_type: sourceType, object_id: objectID, page: page, version: version))
    run.images = images
    let evidenceID = "ev-" + String(AgentDigest.sha256(id + (version ?? "")).prefix(24))
    if !run.evidence.contains(where: { $0.id == evidenceID }) {
      run.evidence.append(AgentEvidence(id: evidenceID, bookID: bookID ?? "",
        courseID: courseID, sourceVersion: version ?? ISO8601DateFormatter().string(from: .now), chapterID: nil,
        page: page ?? 1, printedPage: nil, quote: "", quoteSHA256: AgentDigest.sha256(""), attachmentPath: attachmentPath, sourceType: sourceType, objectID: objectID, courseResourceID: courseResourceID))
    }
    return try encoded(["status": "image_ready", "attachment_id": id, "evidence_id": evidenceID, "source_type": sourceType,
      "object_id": objectID, "page": page as Any? ?? NSNull(), "source_version": version ?? "", "course_id": courseID ?? "",
      "complete": true, "image_input": true, "pixel_width": Int(image.size.width), "pixel_height": Int(image.size.height)])
  }

  static func originalImage(bookID: String, page: Int, store: AppStore) async throws -> Data {
    if let url = store.sourceFileURL(bookID) { return try fileImage(url: url, page: page) }
    guard let cloud = store.cloud else { throw AgentValidationError(message: "原资料尚未下载") }
    let data = try await cloud.downloadPDF(bookID: bookID)
    guard let document = PDFDocument(data: data) else { throw AgentValidationError(message: "原资料无法打开") }
    return try pdfImage(document, page: page)
  }
  static func fileImage(url: URL, page: Int) throws -> Data {
    let ext = url.pathExtension.lowercased()
    if ext == "pdf" {
      guard let document = PDFDocument(url: url) else { throw AgentValidationError(message: "PDF 无法读取") }
      return try pdfImage(document, page: page)
    }
    if ["docx", "pptx", "xlsx"].contains(ext) {
      let archive = try Archive(url: url, accessMode: .read)
      let prefix = ext == "docx" ? "word/media/" : ext == "pptx" ? "ppt/media/" : "xl/media/"
      let images = archive.filter { $0.path.hasPrefix(prefix) && UTType(filenameExtension: ($0.path as NSString).pathExtension)?.conforms(to: .image) == true }.sorted { $0.path.localizedStandardCompare($1.path) == .orderedAscending }
      guard images.indices.contains(page - 1) else { throw AgentValidationError(message: "未找到该序号的 Office 原图；page 表示文档内原图顺序") }
      let entry = images[page - 1]
      guard entry.uncompressedSize <= 30 * 1024 * 1024 else { throw AgentValidationError(message: "原图超过可读取容量") }
      var data = Data(); _ = try archive.extract(entry) { data.append($0) }; return data
    }
    guard page == 1 else { throw AgentValidationError(message: "这类图片仅包含一页") }
    return try Data(contentsOf: url)
  }
  static func pdfImage(_ document: PDFDocument, page: Int) throws -> Data {
    guard let original = document.page(at: page - 1), page > 0 else { throw AgentValidationError(message: "页码超出原文范围") }
    let bounds = original.bounds(for: .mediaBox), scale = min(2, 1800 / max(bounds.width, bounds.height))
    let image = original.thumbnail(of: CGSize(width: bounds.width * scale, height: bounds.height * scale), for: .mediaBox)
    guard let data = image.jpegData(compressionQuality: 0.9) else { throw AgentValidationError(message: "原文页面渲染失败") }; return data
  }
}

/// Learning outputs live independently of chat archives, including temporary conversations.
struct AgentLearningObject: Codable, Hashable, Sendable {
  var id: String
  var revision: Int
  var kind: String
  var course_id: String
  var course_resource_id: String?
  var data: [String: JSONValue]
  enum CodingKeys: String, CodingKey { case id, revision, kind, course_id, workspace_id, course_resource_id, resource_id, data }
  init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    id = try values.decode(String.self, forKey: .id)
    revision = try values.decode(Int.self, forKey: .revision)
    kind = try values.decode(String.self, forKey: .kind)
    course_id = try values.decodeIfPresent(String.self, forKey: .course_id) ?? values.decode(String.self, forKey: .workspace_id)
    course_resource_id = try values.decodeIfPresent(String.self, forKey: .course_resource_id) ?? values.decodeIfPresent(String.self, forKey: .resource_id)
    data = try values.decode([String: JSONValue].self, forKey: .data)
  }
  func encode(to encoder: Encoder) throws {
    var values = encoder.container(keyedBy: CodingKeys.self)
    try values.encode(id, forKey: .id); try values.encode(revision, forKey: .revision); try values.encode(kind, forKey: .kind)
    try values.encode(course_id, forKey: .course_id); try values.encodeIfPresent(course_resource_id, forKey: .course_resource_id)
    try values.encode(data, forKey: .data)
  }
}

extension LocalBookCourseRepository {
  func saveAgentLearningObjects(_ values: [AgentLearningObject], scope: String) throws {
    let filtered = try filterDeletedLearningObjects(values, scope: scope)
    try db.put("agent-learning-objects", scope, encoder.encode(filtered))
  }
  func loadAgentLearningObjects(scope: String) throws -> [AgentLearningObject] {
    let values = try db.get("agent-learning-objects", scope).first.map { try decoder.decode([AgentLearningObject].self, from: $0) } ?? []
    return try filterDeletedLearningObjects(values, scope: scope)
  }
}

extension AppStore {
  func reloadAgentLearningObjects() async {
    guard let scope = noteScope else { return }
    if let cached = try? await repository.loadAgentLearningObjects(scope: scope), scope == noteScope { assistantState.learningObjects = cached }
    guard !cloudState.privateLocalMode, let cloud, processingPolicy.path.kind != .offline else { return }
    struct Response: Decodable, Sendable { var objects: [AgentLearningObject] }
    if let response: Response = try? await cloud.request("/api/assistant/learning-data"), scope == noteScope {
      guard let filtered = try? await repository.filterDeletedLearningObjects(response.objects, scope: scope), scope == noteScope else { return }
      assistantState.learningObjects = filtered
      try? await repository.saveAgentLearningObjects(filtered, scope: scope)
    }
  }

  var agentFlashcards: [Flashcard] {
    assistantState.learningObjects.filter { $0.kind == "flashcard" && $0.course_id == selectedCourseID }.compactMap { object in
      let resource = cloudState.workspaces.first { $0.id == object.course_id }?.resources.first { $0.id == object.course_resource_id }
      let bookID = object.data["book_id"]?.stringValue ?? resource?.book_id ?? currentCourse?.resourceIDs.first ?? ""
      guard bookID == selectedBookID else { return nil }
      return Flashcard(card_id: object.id, book_id: bookID, lesson_id: object.data["lesson_id"]?.stringValue ?? "",
        chapter_id: object.data["chapter_id"]?.stringValue ?? "", front: object.data["front"]?.stringValue ?? "",
        back: object.data["back"]?.stringValue ?? "", concept: object.data["concept"]?.stringValue ?? "", source_chunk_ids: [],
        page_start: 1, page_end: 1, mastery: { if case .number(let value)? = object.data["mastery"] { return value }; return 0 }(), reason: "助手创建", due: object.data["date"]?.stringValue, source_kind: "agent")
    }
  }

  func agentPlanTasks(courseID: String, date: String) -> [CloudStudyPlanTask] {
    assistantState.learningObjects.filter { $0.course_id == courseID && ["review_task", "plan"].contains($0.kind) && $0.data["date"]?.stringValue == date }.flatMap { object -> [CloudStudyPlanTask] in
      let values: [[String: JSONValue]]
      if case .array(let tasks)? = object.data["tasks"] { values = tasks.compactMap { if case .object(let value) = $0 { return value }; return nil } }
      else { values = [object.data] }
      return values.enumerated().map { index, data in
        let resource = cloudState.workspaces.first { $0.id == courseID }?.resources.first { $0.id == object.course_resource_id }
        return CloudStudyPlanTask(task_id: data["task_id"]?.stringValue ?? data["id"]?.stringValue ?? (values.count == 1 ? object.id : object.id + ".\(index)"), resource_id: object.course_resource_id ?? resource?.id ?? "",
          book_id: data["book_id"]?.stringValue ?? resource?.book_id ?? "", chapter_id: data["chapter_id"]?.stringValue,
          lesson_id: data["lesson_id"]?.stringValue, card_id: data["card_id"]?.stringValue,
          title: data["title"]?.stringValue ?? "复习任务", kind: data["kind"]?.stringValue ?? "recall",
          minutes: UnifiedAgentTools.integer(data, "minutes", default: 10), status: data["status"]?.stringValue ?? "pending", requires: [])
      }
    }
  }
}

extension UnifiedAgentTools {
  static func courseMutationVersion(_ id: String, store: AppStore) throws -> String {
    let course = store.courses.first { $0.id == id }
    let resources = Set((course?.resourceIDs ?? []).compactMap {
      store.courseResourceID(courseID: id, bookID: $0)
    })
    let notes = store.notes.filter { note in
      if let owner = note.anchor?.courseID { return owner == id }
      return note.anchor?.courseResourceID.map(resources.contains) == true
    }.map { "\($0.id):\(store.noteState.revisions[$0.id] ?? 0)" }.sorted()
    return AgentDigest.sha256(try encoded(["course": course.map { try json($0) } ?? NSNull(), "notes": notes,
      "learning_objects": try json(store.assistantState.learningObjects.filter { $0.course_id == id }),
      "mistakes": try json(store.mistakes.filter { $0.course_id == id })]))
  }

  static func prepareMutation(_ call: AgentToolCall, run: AgentRunRecord, store: AppStore) async throws -> AgentPendingChoice {
    var args = AgentRuntime.arguments(call)
    let question: String, confirm: String
    if call.name == "delete_note" {
      let id = try required(args, "note_id")
      guard let row = try await store.repository.loadStoredNote(id, scope: run.scope) else { throw AgentValidationError(message: "笔记不存在") }
      args["expected_revision"] = .number(Double(row.revision))
      question = "删除笔记“\(row.note.title)”？此操作将移除这条笔记。"
      confirm = "删除笔记"
    } else {
      guard run.scope == store.noteScope else { throw NoteStoreError.scopeMismatch }
      let target = try course(args, store: store)
      let confirmation = try await store.prepareCourseRemoval(target.id)
      guard run.scope == store.noteScope else { throw CancellationError() }
      args = confirmation.arguments
      question = "从本机删除课程“\(target.name)”？\n" + confirmation.summary
      confirm = "删除本机课程"
    }
    return AgentPendingChoice(toolCallID: call.id, question: question, options: [confirm, "取消"], selected: nil,
      actionName: call.name, actionArgumentsJSON: try encoded(AgentRuntime.foundationObject(.object(args)) as? [String: Any] ?? [:]))
  }

  static func performConfirmedMutation(_ name: String, args: [String: JSONValue], operationID: String, scope: String, store: AppStore, localOnly: Bool = false) async throws -> String {
    guard scope == store.noteScope else { throw NoteStoreError.scopeMismatch }
    if name == "delete_note" {
      let id = try required(args, "note_id"), revision = integer(args, "expected_revision", default: -1)
      let result = try await store.repository.commitAgentNoteDeletion(scope: scope, operationID: operationID, noteID: id, revision: revision)
      guard scope == store.noteScope else { throw CancellationError() }
      store.notes.removeAll { $0.id == id }; store.noteState.revisions[id] = nil
      return result
    }
    guard name == "remove_course" else { throw AgentValidationError(message: "不支持的删除操作") }
    let id = try required(args, "course_id")
    let argumentsHash = AgentDigest.sha256(try AgentDigest.canonicalJSON(
      AgentRuntime.foundationObject(.object(args)) as? [String: Any] ?? [:]))
    if let receipt = try await store.repository.localCourseDeletionResult(operationID, scope: scope) {
      guard receipt["course_id"]?.stringValue == id,
        receipt["arguments_hash"]?.stringValue == argumentsHash else {
        throw AgentValidationError(message: "删除操作的确认内容已变化，请重新确认")
      }
      return try encoded(AgentRuntime.foundationObject(.object(receipt)) as? [String: Any] ?? [:])
    }
    if let target = store.courses.first(where: { $0.id == id }) {
      guard args["local_version"]?.stringValue == (try courseMutationVersion(id, store: store)),
        case .array(let frozen)? = args["frozen_book_ids"],
        Set(frozen.compactMap(\.stringValue)) == Set(target.resourceIDs),
        case .array(let resources)? = args["frozen_resource_ids"],
        Set(resources.compactMap(\.stringValue)) == Set(target.resourceIDs.compactMap {
          store.courseResourceID(courseID: id, bookID: $0)
        }) else {
        throw AgentValidationError(message: "课程资料或学习记录已变化，请重新确认删除")
      }
    }
    try await store.performLocalCourseDeletion(courseID: id, scope: scope)
    guard scope == store.noteScope else { throw CancellationError() }
    let receipt: [String: JSONValue] = ["status": .string("deleted"), "course_id": .string(id),
      "operation_id": .string(operationID), "arguments_hash": .string(argumentsHash)]
    try await store.repository.saveLocalCourseDeletionResult(receipt, id: operationID, scope: scope)
    return try encoded(AgentRuntime.foundationObject(.object(receipt)) as? [String: Any] ?? [:])
  }
}

extension LocalBookCourseRepository {
  func commitAgentNoteDeletion(scope: String, operationID: String, noteID: String, revision: Int) throws -> String {
    try db.transaction {
      if let data = try db.get("agent-deletion-receipt", scope + ":" + operationID).first { return String(decoding: data, as: UTF8.self) }
      guard let row = try loadStoredNote(noteID, scope: scope), row.revision == revision else { throw NoteStoreError.revisionConflict(noteID: noteID) }
      try tombstone(noteID, scope: scope)
      let result = try AgentDigest.canonicalJSON(["status": "deleted", "note_id": noteID, "operation_id": operationID])
      try db.put("agent-deletion-receipt", scope + ":" + operationID, Data(result.utf8))
      return result
    }
  }
}

struct AgentNoteEditCommit: Sendable { var stored: StoredNote; var committedRevision: Int }

extension LocalBookCourseRepository {
  func commitAgentNoteEdit(scope: String, operationID: String, noteID: String, revision: Int, title: String?, body: String?) throws -> AgentNoteEditCommit {
    try db.transaction {
      let key = scope + ":" + operationID
      let digest = AgentDigest.sha256(try AgentDigest.canonicalJSON(["note_id": noteID, "revision": revision, "title": title as Any? ?? NSNull(), "body": body as Any? ?? NSNull()]))
      if let data = try db.get("agent-edit-receipt", key).first {
        let receipt = try JSONDecoder().decode([String: String].self, from: data)
        guard receipt["arguments_hash"] == digest, receipt["note_id"] == noteID,
          let committed = receipt["revision"].flatMap(Int.init), let row = try loadStoredNote(noteID, scope: scope) else {
          throw NoteStoreError.receiptConflict(toolCallID: operationID)
        }
        return AgentNoteEditCommit(stored: row, committedRevision: committed)
      }
      guard var note = try loadStoredNote(noteID, scope: scope)?.note else { throw NoteStoreError.revisionConflict(noteID: noteID) }
      if let title { guard (1...80).contains(title.count) else { throw ContentError.invalid("笔记标题需为1到80字") }; note.title = title }
      if let body { guard (1...100_000).contains(body.count) else { throw ContentError.invalid("笔记正文超出可保存容量") }; note.body = body }
      note.conversation = nil
      let saved = try writeNote(note, scope: scope, condition: .revision(revision))
      try db.put("agent-edit-receipt", key, try JSONEncoder().encode(["note_id": noteID, "revision": String(saved.revision), "arguments_hash": digest]))
      return AgentNoteEditCommit(stored: saved, committedRevision: saved.revision)
    }
  }
}

extension AppStore {
  /// Agent outputs use their own business IDs and completion receipts, independently of generated lesson plans.
  func completeAgentStudyTask(_ task: CloudStudyPlanTask, day: String, courseID: String) async -> Bool {
    let objects = assistantState.learningObjects.filter { $0.course_id == courseID && ["plan", "review_task"].contains($0.kind) }
    guard let object = objects.first(where: { object in
      if task.id == object.id || task.id.hasPrefix(object.id + ".") { return true }
      if case .array(let tasks)? = object.data["tasks"] { return tasks.contains { if case .object(let value) = $0 { return (value["task_id"]?.stringValue ?? value["id"]?.stringValue) == task.id }; return false } }
      return false
    }) else { return false }
    let key = DailyStudyPlanState.key(courseID, day), scope = noteScope
    if cloudState.privateLocalMode {
      do {
        let _: [String: JSONValue] = try await performLocalPersonalOperation(name: "complete_task",
          args: ["object_id": .string(object.id), "task_id": .string(task.id), "course_id": .string(courseID)],
          runID: "complete", callID: String(AgentDigest.sha256(object.id + "|" + task.id).prefix(32)))
        dailyPlanState.errors[key] = nil
      } catch { dailyPlanState.errors[key] = error.localizedDescription }
      return true
    }
    guard let cloud else { dailyPlanState.errors[key] = "AI 助手需要联网"; return true }
    do {
      let operationID = "complete-" + String(AgentDigest.sha256(object.id + "|" + task.id).prefix(32))
      let _: [String: JSONValue] = try await cloud.request("/api/assistant/learning-data/\(object.id)/complete", method: "POST",
        body: ["operation_id": .string(operationID), "task_id": .string(task.id)])
      guard noteScope == scope else { return true }
      await reloadAgentLearningObjects()
      dailyPlanState.errors[key] = nil
    } catch { if noteScope == scope { dailyPlanState.errors[key] = error.localizedDescription } }
    return true
  }
}

extension AppStore {
  func agentTaskObject(taskID: String, courseID: String) -> AgentLearningObject? {
    assistantState.learningObjects.first { object in
      guard object.course_id == courseID, ["plan", "review_task"].contains(object.kind) else { return false }
      if taskID == object.id || taskID.hasPrefix(object.id + ".") { return true }
      if case .array(let tasks)? = object.data["tasks"] { return tasks.contains { if case .object(let value) = $0 { return (value["task_id"]?.stringValue ?? value["id"]?.stringValue) == taskID }; return false } }
      return false
    }
  }
}
