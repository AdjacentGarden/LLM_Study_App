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

@MainActor extension AppStore {
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

  var hasStudyContent: Bool {
    guard let course = currentCourse else { return false }
    return sources.contains { source in
      course.resourceIDs.contains(source.id)
        && source.lessons.contains { ["ready", "generated"].contains($0.status) && $0.lesson_kind != "module_intro" }
    }
  }

  var studyCoursePreparation: StudyCoursePreparation? {
    guard currentCourse != nil else { return nil }
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
    case .generation: return selectedCourseJob?.status == .failed && !cloudState.isWorking
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
    let confirmed = record?.setupCompleted == true || (record == nil && selectedCourseJob != nil)
    let contentReady = confirmed && hasStudyContent
    let planReady = contentReady && courseSources.contains { !$0.plan.tasks.isEmpty }
    let writing = selectedCourseJob?.status == .running && selectedCourseJob?.stage != "planning"
    return [
      CourseCreationStep(id: 0, title: "整理课程资料", completed: parsed, detail: parsed ? "已完成" : parsing ? "进行中" : "等待中", active: parsing && !parsed),
      CourseCreationStep(id: 1, title: "识别章节与重点", completed: directoryReady, detail: directoryReady ? "已完成" : parsing && record?.job.preparationStage == "structuring" ? "进行中" : "等待中",
        active: !directoryReady && parsing && record?.job.preparationStage == "structuring"),
      CourseCreationStep(id: 2, title: "结合学习偏好", completed: answered == CourseDiagnosisQuestion.all.count,
        detail: "已回答 \(answered)/\(CourseDiagnosisQuestion.all.count) 题"),
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
