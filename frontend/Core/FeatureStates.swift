import Foundation
import Observation

// Each feature owns its observable state; AppStore coordinates repository operations
// and exposes forwarding properties to keep feature interfaces small and stable.
@MainActor @Observable final class CourseState {
  var credits = 0
  var chapterDrafts: [String: [Chapter]] = [:]
  var sources: [SourceContent] = []
  var community: [CommunityBook] = []
  var shelf: [ShelfBook] = []
  var courses: [Course] = []
  var preferences: LearnerPreferences? = nil
  var draft: CourseDraft = CourseDraft()
  var selectedCourseID: String? = nil
  var selectedBookID: String? = nil
  var selectedCommunityID: String? = nil
  var dismissedSourceIDs: Set<String> = []
}

@MainActor @Observable final class LearningSessionState {
  var readerCompletionVisible = false
  var readerOverlayVisible = false
  var selectedChapterID: String? = nil
  var selectedLessonID: String? = nil
  var assignmentAnswer: String = ""
  var diagnosisText: String = ""
  var latestDiagnosis: Diagnosis? = nil
  var mistakes: [MistakeRecord] = []
  var selectedQuizIndex: Int = 0
  var locations: [String: StudyLocation] = [:]
  var mastered: Set<String> = []
}

struct ReaderOrigin: Sendable {
  var courseID: String?
  var bookID: String?
  var chapterID: String?
  var lessonID: String?
}

@MainActor @Observable final class ReaderState {
  var origin: ReaderOrigin?

  var sourceTarget: SourcePageTarget? = nil
  var selectedQuote: String = ""
  var selectedAsset: BookAsset? = nil
}

@MainActor @Observable final class StudyNoteState {
  var contextCitation: LessonCitation?
  var noteKind: NoteKind = .text
  var notes: [StudyNote] = []
}

@MainActor @Observable final class ImportTaskState {
  var courses: [CourseImportRecord] = []
  var tasks: [String: Task<Void, Never>] = [:]
  var viewedCourseID: String?
  var legacyCourseID: String?
  var preparationServices: [String: BookPreparationService] = [:]
  var processingRoutes: [String: ProcessingRoute] = [:]
  var job: ImportJob?
  var processingProgress: Double = 0
  var processingMessage: String = "准备整理资料"
  var importedFileURL: URL? = nil
  var importError: String? = nil
}

@MainActor @Observable final class AssistantConversationState {
  var globalAssistantOpen: Bool = false
  var chatMessages: [ChatMessage] = []
  var chatLoading: Bool = false
}

@MainActor extension AppStore {
  func courseImport(for courseID: String) -> CourseImportRecord? {
    if let record = importState.courses.first(where: { $0.id == courseID }) { return record }
    guard let course = courses.first(where: { $0.id == courseID }) else { return nil }
    return importState.courses.first {
      !$0.draft.resourceIDs.isEmpty && Set($0.draft.resourceIDs).isSubset(of: Set(course.resourceIDs))
    }
  }

  @discardableResult func registerCourseImport() -> String? {
    guard !draft.resourceIDs.isEmpty, draft.catalogBookID == nil else { return nil }
    let ids = draft.resourceIDs
    let courseID: String
    if let edit = draft.importCourseID ?? draft.editingCourseID, courses.contains(where: { $0.id == edit }) {
      courseID = edit
    } else if let course = courses.first(where: { Set(ids).isSubset(of: Set($0.resourceIDs)) }) {
      courseID = course.id
    } else {
      courseID = "local-course:" + UUID().uuidString
      courses.append(Course(id: courseID, name: draft.name.isEmpty ? "新课程" : draft.name,
        resourceIDs: ids, activeResourceID: ids.first))
    }
    if let i = courses.firstIndex(where: { $0.id == courseID }) {
      for id in ids where !courses[i].resourceIDs.contains(id) { courses[i].resourceIDs.append(id) }
    }
    draft.importCourseID = courseID
    selectedCourseID = courseID
    if let index = importState.courses.firstIndex(where: { $0.id == courseID }) {
      for id in ids where !importState.courses[index].books.contains(where: { $0.id == id }) {
        importState.courses[index].books.append(CourseImportBook(id: id))
      }
      importState.courses[index].draft.resourceIDs = importState.courses[index].books.map(\.id)
      if importState.tasks[courseID] == nil {
        var retainedDraft = draft
        retainedDraft.resourceIDs = importState.courses[index].books.map(\.id)
        importState.courses[index].draft = retainedDraft
        importState.courses[index].job.bookIDs = retainedDraft.resourceIDs
        if importState.courses[index].books.contains(where: { $0.status != .completed }),
          importState.courses[index].job.status == .completed {
          importState.courses[index].job.status = .queued
          importState.courses[index].job.message = "新资料已保存，等待解析"
        }
      }
    } else {
      let legacy = importJob.flatMap { Set($0.bookIDs) == Set(ids) ? $0 : nil }
      let job = legacy ?? ImportJob(bookIDs: ids, status: .queued, message: "资料已保存，等待解析")
      importState.courses.append(CourseImportRecord(id: courseID, draft: draft,
        books: ids.map { CourseImportBook(id: $0) }, job: job, started: legacy != nil,
        setupCompleted: !courseID.hasPrefix("local-course:")))
    }
    return courseID
  }

  func rebindCourseImports(localID: String, bookID: String) {
    for i in importState.courses.indices {
      importState.courses[i].draft.resourceIDs = importState.courses[i].draft.resourceIDs.map { $0 == localID ? bookID : $0 }
      importState.courses[i].job.bookIDs = importState.courses[i].job.bookIDs.map { $0 == localID ? bookID : $0 }
      for b in importState.courses[i].books.indices where importState.courses[i].books[b].id == localID {
        importState.courses[i].books[b].id = bookID
      }
    }
  }

  func clearCourseImports() {
    for task in importState.tasks.values { task.cancel() }
    importState.tasks = [:]
    importState.preparationServices = [:]
    importState.processingRoutes = [:]
    processTask?.cancel()
    processTask = nil
    importState.courses = []
    importState.viewedCourseID = nil
    importState.legacyCourseID = nil
  }

  func openCourse(_ course: Course) {
    if let record = courseImport(for: course.id), record.onDeviceOnly == true, record.job.status == .completed {
      selectCourse(course.id)
      go(.courseDetail)
      return
    }
    if let record = courseImport(for: course.id), record.needsPreparation || record.setupCompleted != true {
      importState.viewedCourseID = record.id
      go(.courseProgress)
    } else {
      selectCourse(course.id)
      go(.study)
    }
  }

  func continueCourseImport(_ id: String) {
    guard let record = importState.courses.first(where: { $0.id == id }) else { return }
    draft = record.draft
    draft.parseCompleted = record.job.status == .completed
    if let course = courses.first(where: { $0.id == id }) { selectCourse(course.id) }
    else if let book = record.draft.resourceIDs.first { selectSource(book) }
    go(record.job.status == .completed ? .courseSetup : .courseProgress)
  }

  func updateCourseImport(_ id: String, _ update: (inout CourseImportRecord) -> Void) {
    guard let i = importState.courses.firstIndex(where: { $0.id == id }) else { return }
    update(&importState.courses[i])
    if importState.legacyCourseID == id {
      let record = importState.courses[i]
      importJob = record.job
      processingProgress = record.job.progress
      processingMessage = record.job.message
      importError = record.error
    }
    persist()
  }

  func startCourseImport(_ id: String, retryPreparation: Bool = false) async {
    if let existing = importState.tasks[id] { await existing.value; return }
    guard let record = importState.courses.first(where: { $0.id == id }),
      record.job.status != .completed else { return }
    let epoch = cloudState.epoch
    let selection = selectionGeneration
    let task = Task { @MainActor in
      await self.runCourseImport(id, retryPreparation: retryPreparation, epoch: epoch)
    }
    importState.tasks[id] = task
    if importState.legacyCourseID == id { processTask = task }
    await task.value
    guard epoch == cloudState.epoch else { return }
    importState.tasks[id] = nil
    if importState.legacyCourseID == id { processTask = nil }
    if epoch == cloudState.epoch, route == .processing, selection == selectionGeneration,
      let finished = importState.courses.first(where: { $0.id == id }), finished.job.status == .completed {
      draft.parseCompleted = true
      if finished.onDeviceOnly == true { replace(.courseDetail) }
      else { go(.courseSetup) }
    }
  }

  func pauseCourseImport(_ id: String) async {
    guard let record = importState.courses.first(where: { $0.id == id }) else { return }
    let epoch = cloudState.epoch
    do {
      if let cloud, record.onDeviceOnly != true,
        importState.processingRoutes[id] == .existing || record.job.preparationBookID != nil
          || importState.tasks[id] == nil && record.started {
        for book in record.books where book.status != .completed && !book.id.hasPrefix("book_local_") {
          do {
            let _: CloudBookPreparationJob = try await cloud.request("/api/books/\(book.id)/prepare/cancel", method: "POST")
          } catch let error as CloudError where error.status == 404 {
            let status: CloudBookStatus = try await cloud.request("/api/books/\(book.id)/status")
            guard !["ocr_ready", "structured"].contains(status.status) else {
              throw CloudError(status: 409, message: "当前后端尚不支持暂停教材准备，请先更新后端服务。")
            }
            let _: CloudOK = try await cloud.request("/api/native/books/\(book.id)/cancel", method: "POST")
          }
        }
      }
      guard epoch == cloudState.epoch else { return }
      updateCourseImport(id) {
        $0.job.status = .cancelled; $0.job.message = "解析已暂停，已完成的内容会保留。"; $0.error = nil
        for i in $0.books.indices where $0.books[i].status != .completed {
          $0.books[i].status = .cancelled; $0.books[i].message = $0.job.message
        }
      }
      importState.tasks[id]?.cancel()
    } catch {
      guard epoch == cloudState.epoch else { return }
      updateCourseImport(id) { $0.error = error.localizedDescription }
    }
  }

  private func runCourseImport(_ id: String, retryPreparation: Bool, epoch: UUID) async {
    guard let record = importState.courses.first(where: { $0.id == id }) else { return }
    let ids = record.books.map(\.id)
    let processingRoute = cloud == nil ? ProcessingRoute.onDevice : await processingPolicy.route(for: .ocr)
    guard epoch == cloudState.epoch, !Task.isCancelled else { return }
    guard processingRoute != .cancelled else {
      updateCourseImport(id) { $0.job.status = .cancelled; $0.job.message = "已取消本次处理，原文件已保留。" }
      if route == .processing { replace(.parseReady) }
      return
    }
    importState.processingRoutes[id] = processingRoute
    let onDeviceOnly = cloud != nil && processingRoute == .onDevice
    if importState.legacyCourseID == id { activeImportRoute = processingRoute }
    defer {
      importState.processingRoutes[id] = nil
      if importState.legacyCourseID == id { activeImportRoute = nil }
    }
    let retryBooks = retryPreparation ? Set(record.books.filter { $0.status != .completed }.map(\.id)) : []
    updateCourseImport(id) {
      $0.started = true; $0.error = nil; $0.onDeviceOnly = onDeviceOnly
      $0.job = ImportJob(bookIDs: ids, status: .running)
    }
    await persistTask?.value
    do {
      if cloud != nil, let error = cloudState.cacheWriteError { throw ContentError.storage(error) }
      for (offset, originalID) in ids.enumerated() {
        try Task.checkCancellation()
        guard epoch == cloudState.epoch else { return }
        var bookID = originalID
        updateCourseImport(id) { value in
          value.job.preparationBookID = nil; value.job.preparationStage = nil
          value.job.progress = Double(offset) / Double(max(1, ids.count))
          value.job.message = "正在读取资料"
          if let b = value.books.firstIndex(where: { $0.id == originalID }) {
            value.books[b].status = .running; value.books[b].message = "正在读取资料"
          }
        }
        if let cloud, processingRoute == .existing {
          guard let accountKey = cloudState.cacheKey else { throw CancellationError() }
          if bookID.hasPrefix("book_local_") {
            guard let file = sourceFileURL(bookID) else { throw ContentError.missing("本机原文件") }
            let uploaded = try await cloud.uploadPDF(file, filename: sources.first { $0.id == bookID }?.summary.filename ?? file.lastPathComponent)
            guard epoch == cloudState.epoch else { return }
            bindUploadedBook(localID: bookID, uploaded: uploaded)
            bookID = uploaded.book_id
            if !id.hasPrefix("local-course:"),
              cloudState.workspaces.contains(where: { $0.id == id }),
              cloudState.workspaces.first(where: { $0.id == id })?.resources.contains(where: { $0.book_id == bookID }) != true {
              let _: CloudResource = try await cloud.request("/api/workspaces/\(id)/resources", method: "POST", body: ["book_id": .string(bookID)])
            }
          }
          let currentBook = bookID
          var status: CloudBookStatus = try await cloud.request("/api/books/\(bookID)/status")
          if !["ocr_ready", "structured"].contains(status.status) {
            let health: CloudHealth = try await cloud.request("/api/health")
            guard health.ocr_worker_enabled, health.ocr_worker_alive else {
              throw CloudError(status: 503, message: "服务器的教材解析服务尚未启动，原文件已保留。")
            }
          }
          if status.status == "uploaded" {
            status = try await cloud.request("/api/books/\(bookID)/process", method: "POST")
          } else if status.retryable {
            status = try await cloud.request("/api/books/\(bookID)/process/retry", method: "POST")
          }
          while !["ocr_ready", "structured"].contains(status.status) {
            try Task.checkCancellation()
            guard epoch == cloudState.epoch else { return }
            if status.retryable || status.status == "cancelled" {
              throw CloudError(status: 409, message: "自动解析暂未完成，原文件和进度已保留。")
            }
            let fraction = status.progress, message = status.current_step
            updateCourseImport(id) { value in
              value.job.progress = (Double(offset) + fraction) / Double(max(1, ids.count))
              value.job.message = message
              if let b = value.books.firstIndex(where: { $0.id == currentBook }) {
                value.books[b].status = .running; value.books[b].progress = fraction
                value.books[b].message = message
              }
            }
            try await Task.sleep(for: .seconds(2))
            status = try await cloud.request("/api/books/\(bookID)/status")
          }
          updateCourseImport(id) {
            $0.job.preparationBookID = currentBook
            $0.job.preparationStage = "queued"
            $0.job.message = "正在准备检索索引、教材目录和诊断题"
          }
          let service = preparationService(api: cloud, accountKey: accountKey, bookID: currentBook)
          _ = try await service.waitForBook(bookID: currentBook, retry: retryBooks.contains(originalID) || retryBooks.contains(currentBook)) { [self] job in
            try await MainActor.run {
              guard epoch == self.cloudState.epoch else { throw CancellationError() }
              self.updateCourseImport(id) { value in
                value.job.message = job.message; value.job.preparationStage = job.stage
                value.job.preparationFailed = job.status == .failed
                if let b = value.books.firstIndex(where: { $0.id == currentBook }) {
                  value.books[b].status = .running; value.books[b].stage = job.stage
                  value.books[b].message = job.message
                }
              }
            }
          }
          let structure: CloudStructure = try await cloud.request("/api/books/\(currentBook)/structure")
          guard epoch == cloudState.epoch else { return }
          let resource = CloudResource(id: "staged:\(currentBook)", book_id: currentBook, workspace_id: "", revision: 0,
            filename: sources.first { $0.id == currentBook }?.summary.filename ?? "教材.pdf",
            status: "structured", structure: structure, courses: [], progress: CloudProgress())
          if let i = sources.firstIndex(where: { $0.id == currentBook }) { sources[i] = CloudMapping.source(resource) }
        } else if let source = sources.first(where: { $0.id == bookID }), onDeviceOnly || !["ready", "source_ready"].contains(source.summary.status) {
          guard let file = sourceFileURL(bookID) else { throw ContentError.missing("本机原文件") }
          let filename = source.summary.filename ?? file.lastPathComponent
          let extracted: SourceContent
          if AppleDocumentOCR.supportedExtensions.contains(file.pathExtension.lowercased()) {
            extracted = try await AppleDocumentOCR.extract(url: file, bookID: bookID, filename: filename) { [self] progress in
              Task { @MainActor in
                guard epoch == self.cloudState.epoch,
                  self.importState.courses.first(where: { $0.id == id })?.job.status == .running else { return }
                self.updateCourseImport(id) { value in
                  let fraction = Double(progress.completedPages) / Double(max(1, progress.totalPages))
                  value.job.progress = (Double(offset) + fraction) / Double(max(1, ids.count))
                  value.job.message = "本机识别 · 第 \(progress.currentPage)/\(progress.totalPages) 页"
                  if let b = value.books.firstIndex(where: { $0.id == bookID }) {
                    value.books[b].progress = fraction; value.books[b].message = value.job.message
                  }
                }
              }
            }
          } else {
            extracted = try await LocalFileImporter.extract(url: file, bookID: bookID, filename: filename)
          }
          try Task.checkCancellation()
          var result = extracted
          if !source.chapters.isEmpty || !source.lessons.isEmpty {
            result = LocalBookCourseRepository.mergingLocalOCR(from: extracted, into: source)
          } else {
            result.summary.title = source.summary.title; result.summary.cover_url = source.summary.cover_url
          }
          if let i = sources.firstIndex(where: { $0.id == bookID }) { sources[i] = result }
          if cloud == nil { try await repository.saveSources(sources) }
        }
        updateCourseImport(id) { value in
          if let b = value.books.firstIndex(where: { $0.id == bookID }) {
            value.books[b].status = .completed; value.books[b].progress = 1
            value.books[b].message = onDeviceOnly ? "本机文字识别完成，可阅读原文与检索" : "资料整理完成"
            if onDeviceOnly { value.books[b].stage = "local_ready" }
          }
        }
      }
      guard epoch == cloudState.epoch else { return }
      updateCourseImport(id) {
        let complete = $0.books.allSatisfy { $0.status == .completed }
        $0.job.status = complete ? .completed : .queued
        $0.job.progress = complete ? 1 : 0
        $0.job.message = complete ? (onDeviceOnly ? "本机文字识别完成，可阅读原文与检索" : "资料整理完成") : "新资料已保存，等待解析"
        if onDeviceOnly && complete { $0.setupCompleted = true }
        $0.draft.parseCompleted = complete
      }
      if Set(draft.resourceIDs) == Set(importState.courses.first(where: { $0.id == id })?.draft.resourceIDs ?? []) {
        draft.parseCompleted = importState.courses.first(where: { $0.id == id })?.job.status == .completed
      }
    } catch {
      guard epoch == cloudState.epoch else { return }
      updateCourseImport(id) {
        if $0.job.status != .cancelled {
          $0.job.status = error is CancellationError || ((error as? CloudError)?.transportFailure ?? false) ? .interrupted : .failed
          $0.error = error is CancellationError ? "查询已暂停，服务器任务和原文件仍保留" : error.localizedDescription
          $0.job.preparationFailed = $0.job.preparationBookID != nil && !((error as? CloudError)?.transportFailure ?? false)
          for b in $0.books.indices where $0.books[b].status == .running {
            $0.books[b].status = $0.job.status
            $0.books[b].message = $0.error ?? $0.job.message
          }
        }
      }
      if (error as? CloudError)?.status == 401 { handleCloudError(error) }
    }
    persist()
  }

  /// Opening a card only observes existing server jobs; it never submits work.
  func observeCourseImport(_ id: String, once: Bool = false) async {
    guard let cloud, let accountKey = cloudState.cacheKey else { return }
    let epoch = cloudState.epoch
    while !Task.isCancelled, epoch == cloudState.epoch,
      let record = importState.courses.first(where: { $0.id == id }), record.needsPreparation,
      record.onDeviceOnly != true {
      if importState.tasks[id] == nil, record.started {
        do {
          var complete = true
          for book in record.books {
            if book.id.hasPrefix("book_local_") { complete = false; continue }
            let status: CloudBookStatus = try await cloud.request("/api/books/\(book.id)/status")
            guard epoch == cloudState.epoch, !Task.isCancelled else { return }
            if !["ocr_ready", "structured"].contains(status.status) {
              complete = false
              updateCourseImport(id) {
                $0.error = nil
                $0.job.status = status.retryable || status.status == "cancelled" ? .failed
                  : status.status == "uploaded" ? .interrupted : .running
                $0.job.message = status.status == "uploaded" ? "资料已上传，点击继续解析" : status.current_step
                $0.job.progress = status.progress
                $0.job.preparationStage = nil
                if let b = $0.books.firstIndex(where: { $0.id == book.id }) {
                  $0.books[b].message = status.current_step; $0.books[b].progress = status.progress
                  $0.books[b].status = $0.job.status
                }
              }
            } else {
              let service = preparationService(api: cloud, accountKey: accountKey, bookID: book.id)
              let job = try await service.observe(bookID: book.id)
              guard epoch == cloudState.epoch, !Task.isCancelled else { return }
              complete = complete && job.status == .succeeded
              if job.status == .succeeded {
                let structure: CloudStructure = try await cloud.request("/api/books/\(book.id)/structure")
                guard epoch == cloudState.epoch, !Task.isCancelled else { return }
                let resource = CloudResource(id: "staged:\(book.id)", book_id: book.id, workspace_id: "", revision: 0,
                  filename: sources.first { $0.id == book.id }?.summary.filename ?? "教材.pdf", status: "structured",
                  structure: structure, courses: [], progress: CloudProgress())
                if let i = sources.firstIndex(where: { $0.id == book.id }) { sources[i] = CloudMapping.source(resource) }
              }
              updateCourseImport(id) {
                $0.error = job.status == .failed ? job.message : nil
                $0.job.preparationBookID = book.id; $0.job.preparationStage = job.stage
                $0.job.preparationFailed = job.status == .failed
                $0.job.status = job.status == .cancelled ? .cancelled : job.status == .failed ? .failed : .running
                $0.job.message = job.message
                if let b = $0.books.firstIndex(where: { $0.id == book.id }) {
                  $0.books[b].status = job.status == .succeeded ? .completed : job.status == .cancelled ? .cancelled : job.status == .failed ? .failed : .running
                  $0.books[b].stage = job.stage; $0.books[b].message = job.message
                }
              }
            }
          }
          if complete {
            updateCourseImport(id) { $0.job.status = .completed; $0.job.message = "资料整理完成"; $0.job.progress = 1; $0.error = nil; $0.draft.parseCompleted = true }
          }
        } catch is CancellationError { return }
        catch {
          guard epoch == cloudState.epoch else { return }
          updateCourseImport(id) {
            $0.job.status = .interrupted
            $0.error = (error as? CloudError)?.status == 404 ? "教材准备尚未提交，点击继续解析" : error.localizedDescription
          }
          if once || (error as? CloudError)?.status == 404 { return }
          do { try await Task.sleep(for: .seconds(10)) } catch { return }
        }
      }
      if once { return }
      do { try await Task.sleep(for: .seconds(2)) } catch { return }
    }
  }
}

@MainActor extension AppStore {
  var activeCourseImportKey: String {
    (cloudState.cacheKey ?? "local") + ":" + importState.courses.filter { $0.needsPreparation && $0.started && $0.onDeviceOnly != true && $0.job.status != .failed && $0.job.status != .cancelled }.map(\.id).sorted().joined(separator: "|")
  }
  func observeCourseImports() async {
    guard cloud != nil else { return }
    let ids = importState.courses.filter { $0.needsPreparation && $0.started && $0.onDeviceOnly != true && $0.job.status != .failed && $0.job.status != .cancelled }.map(\.id)
    await withTaskGroup(of: Void.self) { group in
      for id in ids { group.addTask { await self.observeCourseImport(id) } }
    }
  }
}

@MainActor extension AppStore {
  func preparationService(api: CloudAPI, accountKey: String, bookID: String) -> BookPreparationService {
    let key = accountKey + ":" + bookID
    if let existing = importState.preparationServices[key] { return existing }
    let service = BookPreparationService(api: api, repository: repository, accountKey: accountKey)
    importState.preparationServices[key] = service
    return service
  }
  func finishCourseImportSetup() {
    guard let id = selectedCourseID, let record = courseImport(for: id) else { return }
    updateCourseImport(record.id) { $0.setupCompleted = true }
  }
}

@MainActor extension AppStore {
  func captureCourseImportDraft() {
    guard let id = draft.importCourseID,
      let i = importState.courses.firstIndex(where: { $0.id == id }) else { return }
    var saved = draft
    if importState.courses[i].job.status != .completed {
      saved.resourceIDs = importState.courses[i].books.map(\.id)
    }
    importState.courses[i].draft = saved
    if let index = courses.firstIndex(where: { $0.id == id && $0.id.hasPrefix("local-course:") }),
      !draft.name.isEmpty { courses[index].name = draft.name }
  }
}
