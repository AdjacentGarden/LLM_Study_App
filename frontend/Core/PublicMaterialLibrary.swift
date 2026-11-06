import CryptoKit
import Foundation

struct PublicCourseGroup: Codable, Sendable {
  var id: String
  var title: String
  var description: String
  var version: String?
  var book_ids: [String]
  var status: String
  var manifest_url: String
  var cover_url: String? = nil
}
struct PublicCourseGroups: Decodable, Sendable { var schema_version: Int; var items: [PublicCourseGroup]; var next_cursor: String? }
struct PublicMaterialFile: Codable, Sendable {
  var asset_id: String
  var kind: String
  var sha256: String
  var bytes: Int
  var media_type: String
  var download_url: String
}
struct PublicMaterialFigure: Codable, Sendable {
  var asset_id: String
  var page_number: Int
  var bbox: [Double]?
  var caption: String?
  var sha256: String
  var bytes: Int
  var media_type: String
  var download_url: String
}
struct PublicMaterialBook: Codable, Sendable {
  var book_id: String
  var title: String
  var content_version: String
  var structure: CloudStructure
  var files: [PublicMaterialFile]
  var figures: [PublicMaterialFigure]
  var cover_url: String? = nil
}
struct PublicMaterialManifest: Codable, Sendable {
  var schema_version: Int
  var group_id: String
  var title: String
  var description: String
  var version: String
  var books: [PublicMaterialBook]
}
struct LocalMaterialRecord: Codable, Sendable {
  var groupID: String
  var version: String
  var bookID: String
  var files: [String: String]
  var figurePaths: [String: String]
  var source: SourceContent
  var diagnostics: [LocalDiagnosticItem]
  var grading: [LocalDiagnosticGrade]
  var figureDescriptors: [PublicMaterialFigure]? = nil

  /// Restore shared textbook material without replacing this course's lessons or progress.
  func enriching(_ content: SourceContent) -> SourceContent {
    guard content.id == bookID else { return content }
    var enriched = content
    enriched.chunks = source.chunks
    enriched.assets = source.assets
    if enriched.chapters.isEmpty { enriched.chapters = source.chapters }
    enriched.summary.chunk_count = enriched.chunks.count
    enriched.summary.asset_count = enriched.assets.count
    enriched.summary.content_mode = "local_material"
    return enriched
  }
}
struct LocalDiagnosticItem: Codable, Sendable {
  var item_id: String
  var chapter_id: String
  var prompt: String
  var response_type: String
  var options: [String]
  var knowledge_point_ids: [String]?
  var source_pages: [Int]?
}
struct LocalDiagnosticGrade: Codable, Sendable {
  var item_id: String
  var correct_option_ids: [String]
  var expected_answer: String?
  var rubric: [String]
}
private struct DiagnosticDownload: Decodable { var items: [LocalDiagnosticItem] }
private struct GradingDownload: Decodable { var items: [LocalDiagnosticGrade] }
struct MaterialOCRPage: Decodable {
  struct Block: Decodable { var block_id: String?; var text: String? }
  var page_number: Int
  var cleaned_text: String?
  var raw_text: String?
  var text: String?
  var blocks: [Block]?
}

extension LocalBookCourseRepository {
  func loadMaterialRecords(scope: String) throws -> [LocalMaterialRecord] {
    try db.get("local-material:" + scope).map { try decoder.decode(LocalMaterialRecord.self, from: $0) }
  }
  func saveMaterialRecord(_ record: LocalMaterialRecord, scope: String) throws {
    try db.put("local-material:" + scope, record.bookID, encoder.encode(record))
  }
  func verifiedMaterialFile(_ file: PublicMaterialFile, scope: String, bytes: Data? = nil) throws -> String? {
    guard file.sha256.count == 64, file.sha256.allSatisfy({ $0.isHexDigit }), file.bytes >= 0 else {
      throw ContentError.invalid("教材文件清单无效")
    }
    let ext = file.kind == "pdf" ? "pdf" : ["image/png": "png", "image/jpeg": "jpg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "video/mp4": "mp4"][file.media_type] ?? "json"
    let relative = "materials/" + scope + "/" + file.sha256 + "." + ext
    let target = directory.appendingPathComponent(relative)
    let data = try bytes ?? (FileManager.default.fileExists(atPath: target.path) ? Data(contentsOf: target) : nil)
    guard let data else { return nil }
    guard data.count == file.bytes, SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == file.sha256.lowercased() else {
      if bytes == nil { try? FileManager.default.removeItem(at: target); return nil }
      throw ContentError.invalid("教材文件校验失败，请重新下载")
    }
    if bytes != nil {
      try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
      try data.write(to: target, options: .atomic)
    }
    return relative
  }
}

@MainActor extension AppStore {
  /// Upload/confirmed import already authorizes fetching this course's original.
  /// Join an active transfer, or recover only the core files; figures may follow.
  func ensureLocalCourseOriginal(courseID: String, bookID: String, chapterID: String? = nil) async throws {
    guard let scope = noteScope else { throw CancellationError() }
    let epoch = cloudState.epoch
    try await ensureCourseDeletionState(scope: scope)
    guard courses.first(where: { $0.id == courseID })?.resourceIDs.contains(bookID) == true,
      canApplyCourseMutation(courseID: courseID, scope: scope) else { throw CancellationError() }
    let deadline = ContinuousClock.now + .seconds(900)
    while true {
      try Task.checkCancellation()
      guard epoch == cloudState.epoch, scope == noteScope,
        canApplyCourseMutation(courseID: courseID, scope: scope) else { throw CancellationError() }
      let records = try await repository.loadMaterialRecords(scope: scope)
      guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
      var content = cloudState.localCourseSources[courseID]?.first { $0.id == bookID }
        ?? (selectedCourseID == courseID ? sources.first { $0.id == bookID } : nil)
      if let material = records.first(where: { $0.bookID == bookID }), let current = content {
        content = material.enriching(current)
        installGeneratedLocalSource(content!, courseID: courseID)
      }
      if let content {
        let chapter = chapterID.flatMap { id in content.chapters.first { $0.id == id } }
        if content.chunks.contains(where: { chunk in
          !chunk.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && (chapterID == nil || chapter.map { chunk.page_start <= $0.page_end && chunk.page_end >= $0.page_start } == true)
        }) { return }
      }
      if !cloudState.publicLibraryDownloading { break }
      guard ContinuousClock.now < deadline else { throw ContentError.invalid("教材原文仍在下载，原任务已保留，请稍后继续") }
      try await Task.sleep(for: .milliseconds(200))
    }
    let tasks = try await repository.publicLibraryTasks(scope: scope)
    guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
    guard let task = tasks.first(where: {
      ($0.kind == "publish" && $0.courseID == courseID && $0.bookIDs.contains(bookID))
        || ($0.kind == "download" && ($0.attachmentCourseID == courseID || $0.courseID == courseID))
    }) else { throw ContentError.invalid("本章教材原文尚未下载完成，请继续已确认的教材下载") }
    _ = try await acquirePublicCourseGroup(catalogID: task.groupID, name: task.title,
      attachToCourseID: courseID, navigateAfterDownload: false, originalsOnly: true)
    guard epoch == cloudState.epoch, scope == noteScope,
      canApplyCourseMutation(courseID: courseID, scope: scope) else { throw CancellationError() }
    resumeConfirmedLibraryTasksInBackground()
  }

  func refreshPublicCourseGroups() async throws {
    guard let cloud else { throw ContentError.invalid("请连接学习服务") }
    let response: PublicCourseGroups = try await cloud.request("/api/public/library/course-groups")
    guard response.schema_version == 1 else { throw ContentError.invalid("公开教材库版本不兼容，请更新应用") }
    community = response.items.map { group in
      CommunityBook(id: group.id, title: group.title, catalogTitle: group.title,
        catalog_origin: group.book_ids.first.map { CatalogOrigin(book_id: $0) }, owner: "公开教材库",
        cover: group.cover_url ?? group.book_ids.first.map { "/api/books/" + $0 + "/cover" } ?? "",
        subject: "", grade: "", version: group.version ?? "", volume: nil, learners: nil, progress: nil,
        flashcardCount: 0, recommended: false, tags: ["\(group.book_ids.count) 本教材"],
        description: group.description, chapters: [])
    }
  }

  private func downloadMaterialFigure(_ figure: PublicMaterialFigure, scope: String, epoch: UUID) async throws -> (PublicMaterialFigure, String?) {
    try Task.checkCancellation()
    guard let cloud, noteScope == scope, cloudState.epoch == epoch else { throw CancellationError() }
    let file = PublicMaterialFile(asset_id: figure.asset_id, kind: "figure", sha256: figure.sha256,
      bytes: figure.bytes, media_type: figure.media_type, download_url: figure.download_url)
    var path = try await repository.verifiedMaterialFile(file, scope: scope)
    if path == nil {
      do {
        let bytes = try await cloud.downloadPublicMaterial(file.download_url)
        path = try await repository.verifiedMaterialFile(file, scope: scope, bytes: bytes)
      } catch {
        try Task.checkCancellation()
        guard noteScope == scope, cloudState.epoch == epoch else { throw CancellationError() }
        return (figure, nil)
      }
    }
    try Task.checkCancellation()
    guard noteScope == scope, cloudState.epoch == epoch else { throw CancellationError() }
    return (figure, path)
  }

  /// A course is created only after every required file has a verified durable copy.
  /// On retry completed content hashes are reused; an interrupted file is downloaded afresh.
  func acquirePublicCourseGroup(catalogID: String, name: String, attachToCourseID: String? = nil,
    navigateAfterDownload: Bool = true, originalsOnly: Bool = false) async throws -> Course {
    guard cloudState.privateLocalMode, let cloud, let scope = noteScope else {
      throw ContentError.invalid("服务尚未启用本机学习模式，请更新服务后重试")
    }
    try await ensureCourseDeletionState(scope: scope)
    guard !originalsOnly || attachToCourseID != nil else { throw CancellationError() }
    if let targetID = attachToCourseID,
      !canApplyCourseMutation(courseID: targetID, scope: scope) { throw CancellationError() }
    guard !catalogID.isEmpty, catalogID.utf8.count <= 150,
      catalogID.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95 }) else {
      throw ContentError.invalid("公开课程标识无效")
    }
    guard !cloudState.publicLibraryDownloading else { throw ContentError.invalid("课程资料正在下载，请稍候") }
    captureLocalCourseContent()
    cloudState.publicLibraryDownloading = true
    defer { cloudState.publicLibraryDownloading = false }
    let priorTasks = try await repository.publicLibraryTasks(scope: scope)
    if let finished = priorTasks.first(where: { $0.kind == "download" && $0.groupID == catalogID && $0.state == "completed" }),
      let course = courses.first(where: { $0.id == finished.courseID }) {
      let epoch = cloudState.epoch
      let records = try await repository.loadMaterialRecords(scope: scope)
      let targetID = attachToCourseID ?? course.id
      guard epoch == cloudState.epoch, scope == noteScope,
        let target = courses.first(where: { $0.id == targetID }),
        canApplyCourseMutation(courseID: targetID, scope: scope) else { throw CancellationError() }
      let required = Set(finished.bookIDs).intersection(target.resourceIDs)
      let materials = records.filter { required.contains($0.bookID) }
      let filesPresent = materials.allSatisfy { record in
        (Array(record.files.values) + Array(record.figurePaths.values)).allSatisfy {
          FileManager.default.fileExists(atPath: repository.directory.appendingPathComponent($0).path)
        }
      }
      if !required.isEmpty, Set(materials.map(\.bookID)) == required, filesPresent {
        installDownloadedMaterials(materials, courseID: targetID)
        try await persistPrivateState()
        if targetID != finished.courseID {
          try await repository.savePublicLibraryTask(PublicLibraryTask(
            id: "download_" + UUID().uuidString, kind: "download", groupID: catalogID,
            title: name, bookIDs: materials.map(\.bookID), state: "completed", courseID: targetID), scope: scope)
        }
        return target
      }
    }
    var task = priorTasks.first(where: { $0.kind == "download" && $0.groupID == catalogID && $0.state != "completed" && $0.attachmentCourseID == attachToCourseID })
      ?? PublicLibraryTask(id: "download_" + UUID().uuidString, kind: "download", groupID: catalogID, title: name, bookIDs: [], state: "pending")
    task.attachmentCourseID = attachToCourseID
    try await repository.savePublicLibraryTask(task, scope: scope)
    let epoch = cloudState.epoch
    let manifest: PublicMaterialManifest = try await cloud.request("/api/public/library/course-groups/\(catalogID)/manifest")
    guard manifest.schema_version == 1, manifest.group_id == catalogID, !manifest.books.isEmpty,
      Set(manifest.books.map(\.book_id)).count == manifest.books.count else {
      throw ContentError.invalid("公开课程资料清单无效")
    }
    var records: [LocalMaterialRecord] = []
    for book in manifest.books {
      var paths: [String: String] = [:]
      for file in book.files {
        guard ["pdf", "ocr_pages", "structure", "figure_manifest", "diagnostics", "grading", "cover"].contains(file.kind) else { throw ContentError.invalid("资料清单包含不支持的文件类型") }
        if originalsOnly, !["pdf", "ocr_pages", "diagnostics", "grading"].contains(file.kind) { continue }
        try Task.checkCancellation()
        guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
        var path = try await repository.verifiedMaterialFile(file, scope: scope)
        if path == nil, file.kind == "pdf", let original = sourceFileURL(book.book_id),
          let bytes = try? Data(contentsOf: original, options: .mappedIfSafe),
          bytes.count == file.bytes,
          SHA256.hash(data: bytes).map({ String(format: "%02x", $0) }).joined() == file.sha256.lowercased() {
          path = try await repository.verifiedMaterialFile(file, scope: scope, bytes: bytes)
        }
        if path == nil {
          let bytes = try await cloud.downloadPublicMaterial(file.download_url)
          path = try await repository.verifiedMaterialFile(file, scope: scope, bytes: bytes)
        }
        guard let path else { throw ContentError.storage("教材文件未保存") }
        paths[file.kind] = path
      }
      guard paths["pdf"] != nil, let ocrPath = paths["ocr_pages"], let diagnosticPath = paths["diagnostics"], let gradePath = paths["grading"] else {
        throw ContentError.invalid("教材缺少 PDF、原文或公共诊断题，下载尚未完成")
      }
      let raw = try Data(contentsOf: repository.directory.appendingPathComponent(ocrPath))
      let decoder = JSONDecoder()
      var chunks: [SourceChunk] = []
      for line in String(decoding: raw, as: UTF8.self).split(separator: "\n") {
        let page = try decoder.decode(MaterialOCRPage.self, from: Data(line.utf8))
        guard page.page_number >= 1, page.page_number <= book.structure.source_page_count else { throw ContentError.invalid("教材页码无效") }
        let text = page.cleaned_text ?? page.raw_text ?? page.text ?? page.blocks?.compactMap(\.text).joined(separator: "\n") ?? ""
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
        let chapter = book.structure.chapters.first { $0.start_page <= page.page_number && $0.end_page >= page.page_number }
        chunks.append(SourceChunk(chunk_id: "public:\(book.book_id):\(page.page_number)", book_id: book.book_id,
          chapter_id: chapter?.chapter_id ?? "", page_start: page.page_number, page_end: page.page_number,
          content_type: "text", text: text, asset_ids: [],
          key_concepts: [], source_metadata: ["origin": .string("downloaded_public_ocr")]))
      }
      let items = try decoder.decode(DiagnosticDownload.self, from: Data(contentsOf: repository.directory.appendingPathComponent(diagnosticPath))).items
      let grades = try decoder.decode(GradingDownload.self, from: Data(contentsOf: repository.directory.appendingPathComponent(gradePath))).items
      guard Set(items.map(\.item_id)).isSubset(of: Set(grades.map(\.item_id))) else { throw ContentError.invalid("公共诊断评分数据不完整") }
      let resource = CloudResource(id: "material:" + book.book_id, book_id: book.book_id, workspace_id: "",
        revision: 0, filename: book.title, status: "ready", structure: book.structure, courses: [], progress: CloudProgress())
      var source = CloudMapping.source(resource)
      source.summary.status = "ready"
      source.summary.cover_url = book.cover_url ?? "/api/books/" + book.book_id + "/cover"
      source.summary.content_mode = "local_material"
      source.summary.chunk_count = chunks.count
      source.summary.asset_count = 0
      source.chunks = chunks
      source.assets = []
      // Public material never imports somebody else's lessons, answers or progress.
      source.lessons = []
      var material = LocalMaterialRecord(groupID: catalogID, version: book.content_version, bookID: book.book_id,
        files: paths, figurePaths: [:], source: source, diagnostics: items, grading: grades,
        figureDescriptors: book.figures)
      guard !chunks.isEmpty else { throw ContentError.invalid("教材原文为空，下载尚未完成") }
      guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
      try await repository.saveMaterialRecord(material, scope: scope)
      guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
      if let targetID = attachToCourseID {
        guard canApplyCourseMutation(courseID: targetID, scope: scope) else { throw CancellationError() }
        installDownloadedMaterials([material], courseID: targetID)
        try await persistPrivateState()
      }
      // OCR is durable and usable before optional figures are transferred.
      if originalsOnly { records.append(material); continue }
      var figurePaths: [String: String] = [:]
      var assets: [BookAsset] = []
      try await withThrowingTaskGroup(of: (PublicMaterialFigure, String?).self) { group in
        var next = 0
        for figure in book.figures.prefix(4) {
          group.addTask { try await self.downloadMaterialFigure(figure, scope: scope, epoch: epoch) }
          next += 1
        }
        while let (figure, path) = try await group.next() {
          guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
          if next < book.figures.count {
            let pending = book.figures[next]; next += 1
            group.addTask { try await self.downloadMaterialFigure(pending, scope: scope, epoch: epoch) }
          }
        guard let path else { continue }
        figurePaths[figure.asset_id] = path
        let localURL = repository.directory.appendingPathComponent(path)
        ContentBundle.registerMaterialURL(localURL)
        let url = localURL.absoluteString
        assets.append(BookAsset(asset_id: figure.asset_id, book_id: book.book_id, chapter_id: nil,
          source_type: "textbook", page: figure.page_number, type: "figure", caption: figure.caption ?? "",
          bbox: figure.bbox, image_url: url, thumbnail_url: url, source_page_image_url: nil,
          source_chunk_ids: [], concepts: [], metadata: ["verified_sha256": .string(figure.sha256)]))

          if assets.count % 8 == 0, let targetID = attachToCourseID {
            material.figurePaths = figurePaths
            material.source.assets = assets
            material.source.summary.asset_count = assets.count
            try await repository.saveMaterialRecord(material, scope: scope)
            guard epoch == cloudState.epoch, scope == noteScope,
              canApplyCourseMutation(courseID: targetID, scope: scope) else { throw CancellationError() }
            installDownloadedMaterials([material], courseID: targetID)
          }
        }
      }
      material.figurePaths = figurePaths
      material.source.assets = assets
      material.source.summary.asset_count = assets.count
      for index in material.source.chunks.indices {
        let page = material.source.chunks[index].page_start
        material.source.chunks[index].asset_ids = assets.filter { $0.page == page }.map(\.id)
      }
      records.append(material)
    }
    guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
    for record in records {
      try await repository.saveMaterialRecord(record, scope: scope)
      guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
      filePaths[record.bookID] = record.files["pdf"]
    }
    let ids = records.map(\.bookID)
    let courseID = attachToCourseID ?? "local-course:" + task.id
    if let attachToCourseID,
      !canApplyCourseMutation(courseID: attachToCourseID, scope: scope) { throw CancellationError() }
    guard !cloudState.localDeletedCourseIDs.contains(courseID) else { throw CancellationError() }
    let course = courses.first(where: { $0.id == courseID })
      ?? Course(id: courseID, name: name.isEmpty ? manifest.title : name, resourceIDs: ids,
        courseResourceIDs: Dictionary(uniqueKeysWithValues: ids.map { ($0, courseID + ":" + $0) }), activeResourceID: ids.first)
    if !courses.contains(where: { $0.id == course.id }) { courses.append(course) }
    installDownloadedMaterials(records, courseID: courseID)
    if attachToCourseID != nil {
      try await persistPrivateState()
      let imagesComplete = records.allSatisfy { $0.figurePaths.count == ($0.figureDescriptors?.count ?? 0) }
      task.state = originalsOnly ? "originals_ready" : imagesComplete ? "completed" : "partial"
      task.courseID = courseID; task.bookIDs = ids
      try await repository.savePublicLibraryTask(task, scope: scope)
      return course
    }
    var downloadedDraft = CourseDraft(name: course.name, resourceIDs: ids)
    downloadedDraft.importCourseID = course.id
    downloadedDraft.parseCompleted = true
    downloadedDraft.diagnosis = [:]
    if !importState.courses.contains(where: { $0.id == course.id }) {
      importState.courses.append(CourseImportRecord(id: course.id, draft: downloadedDraft,
        books: ids.map { CourseImportBook(id: $0, status: .completed, progress: 1, message: "教材已下载") },
        job: ImportJob(bookIDs: ids, status: .completed, progress: 1, message: "教材已下载"), setupCompleted: false))
    }
    if navigateAfterDownload {
      selectedCourseID = course.id
      selectedBookID = ids.first
      installLocalCourseContent(course.id)
      draft = downloadedDraft
      retainPendingCourseDraft()
    }
    try await persistPrivateState()
    task.state = records.allSatisfy { $0.figurePaths.count == ($0.figureDescriptors?.count ?? 0) } ? "completed" : "partial"
    task.courseID = course.id; task.bookIDs = ids
    try await repository.savePublicLibraryTask(task, scope: scope)
    if navigateAfterDownload { go(.courseSetup) }
    return course
  }

  private func installDownloadedMaterials(_ records: [LocalMaterialRecord], courseID: String) {
    for record in records {
      let content = cloudState.localCourseSources[courseID]?.first { $0.id == record.bookID }
        ?? (selectedCourseID == courseID ? sources.first { $0.id == record.bookID } : nil)
        ?? record.source
      installGeneratedLocalSource(record.enriching(content), courseID: courseID)
      filePaths[record.bookID] = record.files["pdf"]
    }
  }
}

@MainActor extension AppStore {
  func localFigureData(bookID: String, assetID: String) async throws -> Data {
    guard let scope = noteScope else { throw ContentError.invalid("请登录") }
    let records = try await repository.loadMaterialRecords(scope: scope)
    guard let record = records.first(where: { $0.bookID == bookID }), let path = record.figurePaths[assetID] else {
      throw ContentError.invalid("本机未保存这幅教材插图")
    }
    return try Data(contentsOf: repository.directory.appendingPathComponent(path))
  }
}

struct PublicLibraryTask: Codable, Sendable {
  var id: String
  var kind: String
  var groupID: String
  var title: String
  var bookIDs: [String]
  var state: String
  var courseID: String?
  var attachmentCourseID: String? = nil
}
extension LocalBookCourseRepository {
  func publicLibraryTasks(scope: String) throws -> [PublicLibraryTask] {
    try db.get("public-library-task:" + scope).map { try decoder.decode(PublicLibraryTask.self, from: $0) }
  }
  func savePublicLibraryTask(_ task: PublicLibraryTask, scope: String) throws {
    try db.put("public-library-task:" + scope, task.id, encoder.encode(task))
  }
}
@MainActor extension AppStore {
  func queuePublicCoursePublication(title: String, bookIDs: [String], courseID: String? = nil) async throws {
    guard cloudState.privateLocalMode, let cloud, let scope = noteScope, !bookIDs.isEmpty,
      !bookIDs.contains(where: { $0.hasPrefix("book_local_") }) else { return }
    let id = "group_" + AgentDigest.sha256(scope + "|" + bookIDs.joined(separator: "|"))
    let previous = try await repository.publicLibraryTasks(scope: scope)
    if var registered = previous.first(where: { $0.id == id && $0.kind == "publish" && ["registered", "completed"].contains($0.state) }) {
      if registered.courseID != courseID {
        registered.courseID = courseID
        try await repository.savePublicLibraryTask(registered, scope: scope)
      }
      return
    }
    var task = PublicLibraryTask(id: id, kind: "publish", groupID: id, title: title, bookIDs: bookIDs, state: "pending", courseID: courseID)
    try await repository.savePublicLibraryTask(task, scope: scope)
    let group: PublicCourseGroup = try await cloud.request("/api/public/library/course-groups", method: "POST",
      body: ["id": .string(id), "title": .string(title.isEmpty ? "新课程" : title), "book_ids": .array(bookIDs.map(JSONValue.string))])
    task.groupID = group.id
    task.state = "registered" // The server owns readiness/publication after this durable intent.
    try await repository.savePublicLibraryTask(task, scope: scope)
  }

  func resumeConfirmedLibraryTasksInBackground() {
    guard cloudState.privateLocalMode, let scope = noteScope else { return }
    let epoch = cloudState.epoch
    if cloudState.libraryResumeTask != nil, cloudState.libraryResumeScope == scope,
      cloudState.libraryResumeEpoch == epoch { return }
    cloudState.libraryResumeTask?.cancel()
    let token = UUID()
    cloudState.libraryResumeScope = scope
    cloudState.libraryResumeEpoch = epoch
    cloudState.libraryResumeToken = token
    cloudState.libraryResumeTask = Task { @MainActor [weak self] in
      guard let self else { return }
      defer {
        if self.cloudState.libraryResumeToken == token {
          self.cloudState.libraryResumeTask = nil
          self.cloudState.libraryResumeScope = nil
          self.cloudState.libraryResumeEpoch = nil
          self.cloudState.libraryResumeToken = nil
        }
      }
      guard self.cloudState.epoch == epoch, self.noteScope == scope, !Task.isCancelled else { return }
      await self.resumePublicLibraryTasks()
    }
  }

  func resumePublicLibraryTasks() async {
    guard cloudState.privateLocalMode, let scope = noteScope,
      let tasks = try? await repository.publicLibraryTasks(scope: scope) else { return }
    let epoch = cloudState.epoch
    for task in tasks where task.state != "completed" {
      guard epoch == cloudState.epoch, scope == noteScope, !Task.isCancelled else { return }
      do {
        if task.kind == "publish" {
          if task.state == "registered", let cloud {
            let group: PublicCourseGroup = try await cloud.request("/api/public/library/course-groups/" + task.groupID)
            if group.status == "ready", let id = task.courseID,
              courses.contains(where: { $0.id == id }) {
              _ = try await acquirePublicCourseGroup(catalogID: group.id, name: task.title,
                attachToCourseID: id, navigateAfterDownload: false)
            }
          } else { try await queuePublicCoursePublication(title: task.title, bookIDs: task.bookIDs, courseID: task.courseID) }
        }
        else if task.kind == "download" {
          if let id = task.attachmentCourseID, !courses.contains(where: { $0.id == id }) { continue }
          _ = try await acquirePublicCourseGroup(catalogID: task.groupID, name: task.title,
            attachToCourseID: task.attachmentCourseID, navigateAfterDownload: false)
        }
      } catch {
        guard epoch == cloudState.epoch, scope == noteScope, !Task.isCancelled else { return }
        cloudState.syncError = "资料下载或公开整理可重试：" + error.localizedDescription
      }
    }
    // A process may stop after upload receipts were accepted but before registration.
    for batch in uploadState.batches where batch.isComplete {
      guard epoch == cloudState.epoch, scope == noteScope, !Task.isCancelled else { return }
      let ids = batch.files.compactMap(\.bookID)
      do { try await queuePublicCoursePublication(title: batch.courseName, bookIDs: ids, courseID: batch.courseID) }
      catch { cloudState.syncError = error.localizedDescription }
    }
  }
}
