import CryptoKit
import Foundation

/// Presentation state belongs to the selected course, independently of the open creation draft.
enum StudyCoursePreparation: Equatable {
  case materials, setup, localGeneration, diagnosis, generation
}

struct CourseCreationStep: Identifiable {
  var id: Int
  var title: String
  var completed: Bool
  var detail: String
  var active = false
}

/// Only empty, unfinished duplicate drafts are archived. Originals and all
/// learning records remain available; the complete draft can be recovered.
struct ArchivedDuplicateCourse: Codable, Sendable {
  var course: Course
  var record: CourseImportRecord
  var targetCourseID: String
  var locations: [String: StudyLocation]
}

extension LocalBookCourseRepository {
  func duplicateCourseArchives(scope: String) throws -> [ArchivedDuplicateCourse] {
    try db.get("duplicate-course-archive:" + scope).map {
      try decoder.decode(ArchivedDuplicateCourse.self, from: $0)
    }
  }

  func saveDuplicateCourseArchive(_ value: ArchivedDuplicateCourse, scope: String) throws {
    try db.put("duplicate-course-archive:" + scope, value.course.id, encoder.encode(value))
  }

  func discardDuplicateCourseArchive(_ id: String, scope: String) throws {
    try db.delete("duplicate-course-archive:" + scope, id)
  }
}

@MainActor extension AppStore {
  func preferredDuplicateCourse(matchingBookIDs: Set<String>, excluding: Set<String> = []) -> Course? {
    func score(_ course: Course) -> Int {
      var value = course.id.hasPrefix("local-course:") ? 0 : 100
      if courseImport(for: course.id)?.setupCompleted == true { value += 1_000 }
      if !course.diagnosis.isEmpty { value += 1_000 }
      if notes.contains(where: { $0.anchor?.courseID == course.id }) { value += 10_000 }
      if locations.contains(where: { $0.key.hasPrefix(course.id + "::") && $0.value.page > 1 }) { value += 10_000 }
      if dailyPlanState.plans.keys.contains(where: { $0.hasPrefix(course.id + ":") }) { value += 10_000 }
      let content = sources + (cloudState.localCourseSources[course.id] ?? [])
      if content.contains(where: { course.resourceIDs.contains($0.id) && $0.lessons.contains {
        ["ready", "generated"].contains($0.status) && $0.lesson_kind != "module_intro"
      } }) { value += 1_000 }
      return value
    }
    return courses.filter { !excluding.contains($0.id) && !matchingBookIDs.isDisjoint(with: $0.resourceIDs) }
      .sorted { lhs, rhs in
        let left = score(lhs), right = score(rhs)
        if left != right { return left > right }
        if lhs.createdAt != rhs.createdAt { return lhs.createdAt < rhs.createdAt }
        return lhs.id < rhs.id
      }.first
  }

  /// Warm historical metadata once after hydration, outside the import path.
  func startDuplicateImportMaintenance() {
    guard !isLoading, !cloudState.isHydrating, cloud == nil || cloudState.authenticated else {
      cloudState.duplicateMaintenanceTask?.cancel()
      cloudState.duplicateMaintenanceScope = nil
      return
    }
    let scope = courseUploadScope, epoch = cloudState.epoch
    let key = scope + ":" + epoch.uuidString
    guard cloudState.duplicateMaintenanceScope != key else { return }
    cloudState.duplicateMaintenanceTask?.cancel()
    cloudState.archivedDuplicateCourseAliases = [:]
    cloudState.duplicateMaintenanceScope = key
    let paths = filePaths
    cloudState.duplicateMaintenanceTask = Task { [weak self, repository] in
      do {
        try await repository.warmContentIdentityIndex(scope: scope, books: paths)
        guard let self, !Task.isCancelled, self.cloudState.epoch == epoch,
          self.courseUploadScope == scope else { return }
        try await self.archiveEmptyDuplicateCourses(scope: scope, epoch: epoch)
      } catch {
        // Import remains usable if optional historical cache warming fails.
        if let self, self.cloudState.epoch == epoch { self.cloudState.duplicateMaintenanceScope = nil }
      }
    }
  }

  private func canArchiveDuplicateDraft(_ course: Course, generatedCourseIDs: Set<String>,
    savedRecord: CourseImportRecord? = nil) -> Bool {
    guard course.id.hasPrefix("local-course:"), course.resourceIDs.count == 1,
      let record = courseImport(for: course.id) ?? savedRecord, record.setupCompleted != true,
      record.job.status != .running, record.job.status != .completed,
      record.draft.diagnosis.isEmpty, course.diagnosis.isEmpty,
      record.stageSave.map({ !$0.chapters.isEmpty || $0.workspace != nil || $0.fingerprint != nil
        || $0.attemptedFingerprint != nil || $0.serverDraftFingerprint != nil }) != true,
      importState.tasks[course.id] == nil, !generatedCourseIDs.contains(course.id),
      !uploadState.batches.contains(where: { $0.courseID == course.id && $0.isRunning }),
      !cloudState.localDiagnostics.values.contains(where: { $0.courseID == course.id }),
      !dailyPlanState.plans.keys.contains(where: { $0.hasPrefix(course.id + ":") }),
      !mistakes.contains(where: { $0.course_id == course.id || ($0.course_id == nil && course.resourceIDs.contains($0.book_id)) }),
      !assistantState.learningObjects.contains(where: { $0.course_id == course.id }) else { return false }
    let relationships = Set(course.resourceIDs.compactMap { courseResourceID(courseID: course.id, bookID: $0) })
    guard !notes.contains(where: { note in
      note.anchor?.courseID == course.id || note.anchor?.courseResourceID.map(relationships.contains) == true
        || (note.anchor?.courseID == nil && note.anchor?.courseResourceID == nil
          && note.anchor.map { course.resourceIDs.contains($0.bookID) } == true)
    }), !locations.contains(where: {
      $0.key.hasPrefix(course.id + "::") && ($0.value.page > 1 || $0.value.lessonID != nil
        || !($0.value.completedChapterIDs ?? []).isEmpty)
    }) else { return false }
    if draft.importCourseID == course.id, !draft.diagnosis.isEmpty { return false }
    let content = sources + (cloudState.localCourseSources[course.id] ?? [])
    return !content.contains { source in
      course.resourceIDs.contains(source.id) && (source.lessons.contains {
        ["ready", "generated"].contains($0.status) && $0.lesson_kind != "module_intro"
      } || !source.plan.tasks.isEmpty || !source.flashcards.isEmpty || !source.quizzes.isEmpty)
    }
  }

  func archiveEmptyDuplicateCourses(scope: String, epoch: UUID) async throws {
    let archived = try await repository.duplicateCourseArchives(scope: scope)
    let generated = Set(try await repository.localGenerations(scope: scope).map(\.courseID))
    guard !Task.isCancelled, courseUploadScope == scope, cloudState.epoch == epoch else { return }
    cloudState.archivedDuplicateCourseAliases = [:]
    for archive in archived {
      guard let keeper = courses.first(where: { $0.id == archive.targetCourseID }) else { continue }
      if let restored = courses.first(where: { $0.id == archive.course.id }),
        !canArchiveDuplicateDraft(restored, generatedCourseIDs: generated, savedRecord: archive.record) { continue }
      retireArchivedDuplicate(archive.course.id, record: archive.record, keeper: keeper)
    }
    var hashes: [String: Set<String>] = [:]
    for (bookID, path) in filePaths {
      if let hash = try await repository.cachedContentHash(relativePath: path) { hashes[hash, default: []].insert(bookID) }
    }
    guard !Task.isCancelled, courseUploadScope == scope, cloudState.epoch == epoch else { return }
    for matchingIDs in hashes.values {
      guard let keeper = preferredDuplicateCourse(matchingBookIDs: matchingIDs) else { continue }
      for course in courses where course.id != keeper.id && !matchingIDs.isDisjoint(with: course.resourceIDs) {
        guard canArchiveDuplicateDraft(course, generatedCourseIDs: generated),
          let record = courseImport(for: course.id) else { continue }
        let archive = ArchivedDuplicateCourse(course: course, record: record, targetCourseID: keeper.id,
          locations: locations.filter { $0.key.hasPrefix(course.id + "::") })
        try await repository.saveDuplicateCourseArchive(archive, scope: scope)
        guard !Task.isCancelled, courseUploadScope == scope, cloudState.epoch == epoch else { return }
        guard canArchiveDuplicateDraft(course, generatedCourseIDs: generated) else {
          try await repository.discardDuplicateCourseArchive(course.id, scope: scope)
          continue
        }
        retireArchivedDuplicate(course.id, record: record, keeper: keeper)
      }
    }
    applyArchivedCourseUploadAliases()
    persist()
    await persistTask?.value
  }

  private func retireArchivedDuplicate(_ id: String, record: CourseImportRecord, keeper: Course) {
    cloudState.archivedDuplicateCourseAliases[id] = keeper.id
    // Existing local tombstones also prevent old server exports from restoring
    // this empty draft. This never deletes a server workspace or any records.
    cloudState.localDeletedCourseIDs.insert(id)
    courses.removeAll { $0.id == id }
    importState.courses.removeAll { $0.id == id }
    if importJob?.id == record.job.id { importJob = nil }
    if draft.importCourseID == id { draft = CourseDraft() }
    if selectedCourseID == id { selectCourse(keeper.id) }
  }

  /// The course progress card measures reading, independently of today's tasks.
  var courseLearningProgress: Double {
    let bookIDs = Set(currentCourse?.resourceIDs ?? [selectedBookID ?? ""])
    let values = sources.filter { bookIDs.contains($0.id) }.flatMap { source in
      learningDirectory(source.id == selectedBookID ? chapters : source.chapters).map { node in
        learningReadingProgress(source.plan.tasks, chapterIDs: Set(node.sections.map(\.id)),
          completedChapterIDs: completedChapterIDs(for: source.id))
      }
    }
    return values.isEmpty ? 0 : values.reduce(0, +) / Double(values.count)
  }

  func workspaceID(for record: CourseImportRecord) -> String {
    if let id = record.draft.creationWorkspaceID { return id }
    guard record.id.hasPrefix("local-course:") else { return record.id }
    return "workspace_" + SHA256.hash(data: Data(record.id.utf8))
      .map { String(format: "%02x", $0) }.joined().prefix(32)
  }

  var selectedCourseImport: CourseImportRecord? {
    selectedCourseID.flatMap { courseImport(for: $0) }
  }

  var selectedCourseJob: CloudCourseJob? {
    guard cloudState.diagnosisWorkspaceID == selectedCourseID,
      cloudState.diagnosisResourceID == cloudResource?.id else { return nil }
    return cloudState.courseJob
  }

  var selectedLocalGenerationStatus: LocalGenerationStatus? {
    guard cloudState.privateLocalMode, let course = selectedCourseID, let book = selectedBookID else { return nil }
    return cloudState.localGenerationStatuses[course + "::" + book]
  }

  var selectedLocalGenerationIsRunning: Bool {
    guard let status = selectedLocalGenerationStatus,
      ["pending", "submitted", "submitting"].contains(status.state),
      cloudState.localLearningOwner?.courseID == selectedCourseID else { return false }
    return cloudState.isWorking
  }

  var hasStudyContent: Bool {
    guard let course = currentCourse else { return false }
    return sources.contains { source in
      course.resourceIDs.contains(source.id)
        && source.lessons.contains { ["ready", "generated"].contains($0.status) && $0.lesson_kind != "module_intro" }
    }
  }

  var studyCoursePreparation: StudyCoursePreparation? {
    guard currentCourse != nil else { return nil }
    if cloudState.privateLocalMode, selectedLocalGenerationStatus?.operation == "plan",
      hasStudyContent, !sources.contains(where: {
        currentCourse?.resourceIDs.contains($0.id) == true && !$0.plan.tasks.isEmpty
      }) { return .generation }
    // A background import must not hide the lessons already available in this course.
    if let record = selectedCourseImport {
      if record.setupCompleted == true && hasStudyContent { return nil }
      if record.needsPreparation {
        // The directory is useful before diagnostic or teaching generation
        // finishes. Its cards retain the compact preparation notice.
        return chapters.isEmpty ? .materials : nil
      }
      if record.setupCompleted != true { return .setup }
      if record.onDeviceOnly == true { return .localGeneration }
    }
    if hasStudyContent { return nil }
    if let job = selectedCourseJob, job.status != .succeeded { return .generation }
    if cloudResource != nil, !lessons.contains(where: { $0.status == "ready" }) {
      return .diagnosis
    }
    return nil
  }

  func courseCreationLabel(_ course: Course) -> String? {
    if let upload = courseUpload(for: course.id) {
      return upload.isRunning ? "上传中" : upload.isComplete ? "已上传 · 待整理" : "上传未完成"
    }
    if let record = courseImport(for: course.id) {
      if record.needsPreparation {
        if sources.contains(where: { course.resourceIDs.contains($0.id) && !$0.chapters.isEmpty }) {
          return "目录已就绪"
        }
        switch record.job.status {
        case .failed: return "解析失败"
        case .cancelled, .interrupted: return "待继续整理"
        case .queued: return record.started ? "等待解析" : "待解析"
        case .running: return "解析中"
        case .completed: break
        }
      }
      if record.setupCompleted != true { return "待完善" }
      if record.onDeviceOnly == true { return "待生成课程" }
    }
    let resources = cloudState.workspaces.first { $0.id == course.id }?.resources ?? []
    if cloudState.diagnosisWorkspaceID == course.id, let job = cloudState.courseJob {
      if job.isPending { return "生成中" }
      if job.status == .failed { return "生成未完成" }
    }
    if !resources.isEmpty, resources.allSatisfy({ $0.courses.isEmpty && $0.source_content == nil }) {
      return "待生成课程"
    }
    return nil
  }

  /// Register legacy/unbound drafts before replacing them with another course's draft.
  func retainPendingCourseDraft() {
    if draft.creationWorkspaceID == nil, !draft.resourceIDs.isEmpty {
      draft.creationWorkspaceID = cloudState.creationWorkspaceID
    }
    captureCourseImportDraft()
    guard !draft.resourceIDs.isEmpty, draft.editingCourseID == nil,
      draft.importCourseID == nil else { return }
    let originalCourseID = selectedCourseID
    defer { selectedCourseID = originalCourseID ?? courses.first?.id }
    if draft.catalogBookID == nil {
      let id = "local-course:" + UUID().uuidString
      courses.append(Course(id: id, name: draft.name.isEmpty ? "新课程" : draft.name,
        resourceIDs: draft.resourceIDs, activeResourceID: draft.resourceIDs.first))
      draft.importCourseID = id
      _ = registerCourseImport()
    } else {
      let id = "local-course:" + UUID().uuidString
      let job = ImportJob(bookIDs: draft.resourceIDs, status: .completed, progress: 1,
        message: "课程设置进度已保存")
      draft.importCourseID = id
      courses.append(Course(id: id, name: draft.name.isEmpty ? "新课程" : draft.name,
        resourceIDs: draft.resourceIDs, activeResourceID: draft.resourceIDs.first))
      importState.courses.append(CourseImportRecord(id: id, draft: draft,
        books: draft.resourceIDs.map { CourseImportBook(id: $0, status: .completed, progress: 1) },
        job: job, setupCompleted: false))
    }
    persist()
  }

  func openCoursePreparationDetails() {
    guard let record = selectedCourseImport else { return }
    importState.viewedCourseID = record.id
    go(.courseProgress)
  }

  func coursePreparationRequiresAction(_ preparation: StudyCoursePreparation) -> Bool {
    switch preparation {
    case .materials:
      guard let record = selectedCourseImport,
        importState.tasks[record.id] == nil else { return false }
      return [.failed, .cancelled, .interrupted].contains(record.job.status)
        || record.job.status == .queued && !record.started
    case .setup: return true
    case .localGeneration, .diagnosis: return !cloudState.isWorking && cloudState.courseTask == nil
    case .generation:
      if cloudState.privateLocalMode, selectedLocalGenerationStatus != nil {
        return !cloudState.isWorking && cloudState.courseTask == nil
      }
      return selectedCourseJob?.status == .failed && !cloudState.isWorking
    }
  }

  var courseCreationSteps: [CourseCreationStep] {
    let record = selectedCourseImport
    let saved = record?.draft ?? (draft.importCourseID == selectedCourseID ? draft
      : CourseDraft(name: currentCourse?.name ?? "", resourceIDs: currentCourse?.resourceIDs ?? [],
          diagnosis: currentCourse?.diagnosis ?? [:]))
    let parsing = record?.job.status == .running
    let parsed = record.map { !$0.needsPreparation } ?? (saved.parseCompleted || cloudResource?.structure != nil)
    let bookIDs = Set(saved.resourceIDs)
    let courseSources = sources.filter { bookIDs.contains($0.id) }
    let directoryReady = !courseSources.isEmpty && courseSources.allSatisfy { !$0.chapters.isEmpty }
    let answered = CourseDiagnosisQuestion.all.filter { !(saved.diagnosis[$0.id]?.isEmpty ?? true) }.count
    let common = [
      CourseCreationStep(id: 0, title: "整理课程资料", completed: parsed, detail: parsed ? "已完成" : parsing ? "进行中" : "等待中", active: parsing && !parsed),
      CourseCreationStep(id: 1, title: "识别章节与重点", completed: directoryReady, detail: directoryReady ? "已完成" : parsing && record?.job.preparationStage == "structuring" ? "进行中" : "等待中",
        active: !directoryReady && parsing && record?.job.preparationStage == "structuring"),
      CourseCreationStep(id: 2, title: "结合学习偏好", completed: answered == CourseDiagnosisQuestion.all.count,
        detail: "已回答 \(answered)/\(CourseDiagnosisQuestion.all.count) 题"),
    ]
    if cloudState.privateLocalMode {
      let contentReady = hasStudyContent
      let planReady = contentReady && courseSources.contains { !$0.plan.tasks.isEmpty }
      let status = selectedLocalGenerationStatus
      let writing = selectedLocalGenerationIsRunning && status?.operation == "lecture"
      let planning = selectedLocalGenerationIsRunning && status?.operation == "plan"
      let needsQuery = status.map { ["submitted", "submitting", "uncertain", "retryable"].contains($0.state) } ?? false
      let contentDetail = contentReady ? "已完成" : writing ? "进行中"
        : status?.operation == "lecture" && status?.state == "failed" ? "生成失败，可重试"
        : status?.operation == "lecture" && needsQuery ? "结果待确认" : "需要继续"
      let planDetail = planReady ? "已完成" : planning ? "进行中"
        : status?.operation == "plan" && status?.state == "failed" ? "生成失败，可重试"
        : !contentReady ? "等待学习内容"
        : status?.operation == "plan" && needsQuery ? "结果待确认" : "需要继续"
      return common + [
        CourseCreationStep(id: 3, title: "准备学习内容", completed: contentReady, detail: contentDetail, active: writing),
        CourseCreationStep(id: 4, title: "安排学习计划", completed: planReady, detail: planDetail, active: planning),
      ]
    }
    let confirmed = record?.setupCompleted == true || (record == nil && selectedCourseJob != nil)
    let contentReady = confirmed && hasStudyContent
    let planReady = contentReady && courseSources.contains { !$0.plan.tasks.isEmpty }
    let writing = selectedCourseJob?.status == .running && selectedCourseJob?.stage != "planning"
    return common + [
      CourseCreationStep(id: 3, title: "安排学习计划", completed: planReady, detail: planReady ? "已完成" : selectedCourseJob?.status == .running && selectedCourseJob?.stage == "planning" ? "进行中" : "等待中",
        active: !planReady && selectedCourseJob?.status == .running && selectedCourseJob?.stage == "planning"),
      CourseCreationStep(id: 4, title: "准备学习内容", completed: contentReady, detail: contentReady ? "已完成" : writing ? "进行中" : selectedCourseJob?.status == .queued ? "排队中" : "等待中",
        active: !contentReady && writing),
    ]
  }

  func pauseCourseSetup() {
    retainPendingCourseDraft()
    go(.study)
    persist()
  }

  func resumeSelectedCourseSetup() {
    guard let record = selectedCourseImport else { return }
    retainPendingCourseDraft()
    draft = record.draft
    for bookID in draft.resourceIDs {
      if let edits = record.stageSave?.chapters[bookID] { chapterDrafts[bookID] = edits }
      else if cloud != nil { chapterDrafts.removeValue(forKey: bookID) }
    }
    cloudState.creationWorkspaceID = draft.creationWorkspaceID
    draft.parseCompleted = record.job.status == .completed
    let questionsCompleted = CourseDiagnosisQuestion.all.allSatisfy {
      !(draft.diagnosis[$0.id]?.isEmpty ?? true)
    }
    go(questionsCompleted && draft.catalogBookID == nil ? .chapterConfirm : .courseSetup)
  }

  func resumeSelectedCourseGeneration() {
    if selectedCourseJob != nil || (cloudState.diagnosisWorkspaceID == selectedCourseID
      && cloudState.diagnosisResourceID == cloudResource?.id && cloudState.interview != nil) {
      cloudState.showDiagnosis = true
      return
    }
    cloudState.showCompletionOnCompile = true
    requestCloudLearning(chapterID: studyResumeChapter?.id)
  }

  var studyResumeChapter: Chapter? {
    let location = locations[locationKey]
    let savedChapter = lessons.first(where: { $0.id == location?.lessonID })?.chapter_id
      ?? location?.chapterID
    return CourseHomeNextStep.resolve(chapters: chapters, locationChapterID: savedChapter,
      plan: plan, lessons: lessons)
  }
}
