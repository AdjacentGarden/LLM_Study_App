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

  /// A course is created only after every required file has a verified durable copy.
  /// On retry completed content hashes are reused; an interrupted file is downloaded afresh.
  func acquirePublicCourseGroup(catalogID: String, name: String, attachToCourseID: String? = nil) async throws -> Course {
    guard cloudState.privateLocalMode, let cloud, let scope = noteScope else {
      throw ContentError.invalid("服务尚未启用本机学习模式，请更新服务后重试")
    }
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
      let course = courses.first(where: { $0.id == finished.courseID }) { return course }
    var task = priorTasks.first(where: { $0.kind == "download" && $0.groupID == catalogID && $0.state != "completed" })
      ?? PublicLibraryTask(id: "download_" + UUID().uuidString, kind: "download", groupID: catalogID, title: name, bookIDs: [], state: "pending")
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
        try Task.checkCancellation()
        guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
        var path = try await repository.verifiedMaterialFile(file, scope: scope)
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
      var figurePaths: [String: String] = [:]
      var assets: [BookAsset] = []
      for figure in book.figures {
        let file = PublicMaterialFile(asset_id: figure.asset_id, kind: "figure", sha256: figure.sha256,
          bytes: figure.bytes, media_type: figure.media_type, download_url: figure.download_url)
        var path = try await repository.verifiedMaterialFile(file, scope: scope)
        if path == nil {
          // Figure failure is explicit partial coverage; never render/crop a whole page as fallback.
          if let bytes = try? await cloud.downloadPublicMaterial(file.download_url) {
            path = try? await repository.verifiedMaterialFile(file, scope: scope, bytes: bytes)
          }
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
          content_type: "text", text: text, asset_ids: assets.filter { $0.page == page.page_number }.map(\.id),
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
      source.summary.asset_count = assets.count
      source.chunks = chunks
      source.assets = assets
      // Public material never imports somebody else's lessons, answers or progress.
      source.lessons = []
      records.append(LocalMaterialRecord(groupID: catalogID, version: book.content_version, bookID: book.book_id,
        files: paths, figurePaths: figurePaths, source: source, diagnostics: items, grading: grades))
    }
    guard epoch == cloudState.epoch, scope == noteScope else { throw CancellationError() }
    for record in records {
      try await repository.saveMaterialRecord(record, scope: scope)
      if let i = sources.firstIndex(where: { $0.id == record.bookID }) {
        if attachToCourseID != nil {
          var enriched = sources[i]
          enriched.chunks = record.source.chunks; enriched.assets = record.source.assets
          if enriched.chapters.isEmpty { enriched.chapters = record.source.chapters }
          sources[i] = enriched
        } else { sources[i] = record.source }
      } else { sources.append(record.source) }
      filePaths[record.bookID] = record.files["pdf"]
    }
    let ids = records.map(\.bookID)
    let courseID = attachToCourseID ?? "local-course:" + task.id
    let course = Course(id: courseID, name: name.isEmpty ? manifest.title : name, resourceIDs: ids,
      courseResourceIDs: Dictionary(uniqueKeysWithValues: ids.map { ($0, courseID + ":" + $0) }), activeResourceID: ids.first)
    if !courses.contains(where: { $0.id == course.id }) { courses.append(course) }
    if let attachToCourseID {
      // Enrich uploaded local materials without resetting the owner's questionnaire or navigating.
      if cloudState.localCourseSources[attachToCourseID] == nil { cloudState.localCourseSources[attachToCourseID] = records.map(\.source) }
      try await persistPrivateState()
      task.state = "completed"; task.courseID = attachToCourseID; task.bookIDs = ids
      try await repository.savePublicLibraryTask(task, scope: scope)
      return courses.first(where: { $0.id == attachToCourseID }) ?? course
    }
    selectedCourseID = course.id
    selectedBookID = ids.first
    draft = CourseDraft(name: course.name, resourceIDs: ids)
    draft.importCourseID = course.id
    draft.parseCompleted = true
    draft.diagnosis = [:]
    if !importState.courses.contains(where: { $0.id == course.id }) {
      importState.courses.append(CourseImportRecord(id: course.id, draft: draft,
        books: ids.map { CourseImportBook(id: $0, status: .completed, progress: 1, message: "教材已下载") },
        job: ImportJob(bookIDs: ids, status: .completed, progress: 1, message: "教材已下载"), setupCompleted: false))
    }
    retainPendingCourseDraft()
    try await persistPrivateState()
    task.state = "completed"; task.courseID = course.id; task.bookIDs = ids
    try await repository.savePublicLibraryTask(task, scope: scope)
    go(.courseSetup)
    return course
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
    var task = PublicLibraryTask(id: id, kind: "publish", groupID: id, title: title, bookIDs: bookIDs, state: "pending", courseID: courseID)
    try await repository.savePublicLibraryTask(task, scope: scope)
    let group: PublicCourseGroup = try await cloud.request("/api/public/library/course-groups", method: "POST",
      body: ["id": .string(id), "title": .string(title.isEmpty ? "新课程" : title), "book_ids": .array(bookIDs.map(JSONValue.string))])
    task.groupID = group.id
    task.state = "registered" // The server owns readiness/publication after this durable intent.
    try await repository.savePublicLibraryTask(task, scope: scope)
  }
  func resumePublicLibraryTasks() async {
    guard cloudState.privateLocalMode, let scope = noteScope,
      let tasks = try? await repository.publicLibraryTasks(scope: scope) else { return }
    for task in tasks where task.state != "completed" {
      do {
        if task.kind == "publish" {
          if task.state == "registered", let cloud {
            let group: PublicCourseGroup = try await cloud.request("/api/public/library/course-groups/" + task.groupID)
            if group.status == "ready", let id = task.courseID { _ = try await acquirePublicCourseGroup(catalogID: group.id, name: task.title, attachToCourseID: id) }
          } else { try await queuePublicCoursePublication(title: task.title, bookIDs: task.bookIDs, courseID: task.courseID) }
        }
        else { _ = try await acquirePublicCourseGroup(catalogID: task.groupID, name: task.title) }
      } catch { cloudState.syncError = "资料下载或公开整理可重试：" + error.localizedDescription }
    }
    // A process may stop after upload receipts were accepted but before registration.
    for batch in uploadState.batches where batch.isComplete {
      let ids = batch.files.compactMap(\.bookID)
      do { try await queuePublicCoursePublication(title: batch.courseName, bookIDs: ids, courseID: batch.courseID) }
      catch { cloudState.syncError = error.localizedDescription }
    }
  }
}
