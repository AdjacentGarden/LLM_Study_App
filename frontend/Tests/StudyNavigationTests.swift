import XCTest

@testable import BookCourse

@MainActor final class StudyNavigationTests: XCTestCase {
  private func store() throws -> AppStore {
    AppStore(repository: try LocalBookCourseRepository(directory:
      FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)))
  }

  func testEmptyHistoricalDuplicatesArchiveWithoutRemovingOriginalOrLearningRecords() async throws {
    let store = try store()
    store.isLoading = false
    let original = Course(id: "workspace_original", name: "化学", resourceIDs: ["server-old"])
    let duplicate = Course(id: "local-course:duplicate", name: "chemistry", resourceIDs: ["server-new"])
    store.courses = [duplicate, original]
    store.importState.courses = [CourseImportRecord(id: duplicate.id,
      draft: CourseDraft(resourceIDs: duplicate.resourceIDs), books: [],
      job: ImportJob(bookIDs: duplicate.resourceIDs, status: .failed), stageSave: CourseStageSave())]
    store.importJob = store.importState.courses.first?.job
    store.notes = [StudyNote(kind: .text, title: "原课笔记",
      anchor: NoteAnchor(courseID: original.id, bookID: "server-old", page: 8))]
    store.locations[original.id + "::server-old"] = StudyLocation(page: 8)
    store.selectedCourseID = duplicate.id
    let path = try await store.repository.saveOriginalAttachment(Data("%PDF-1.4\nsame chemistry".utf8), extension: "pdf")
    store.filePaths = ["server-old": path, "server-new": path]
    try await store.repository.warmContentIdentityIndex(scope: "local", books: store.filePaths)
    try await store.archiveEmptyDuplicateCourses(scope: "local", epoch: store.cloudState.epoch)
    XCTAssertEqual(store.courses.map(\.id), [original.id])
    XCTAssertEqual(store.selectedCourseID, original.id)
    XCTAssertEqual(store.notes.count, 1)
    XCTAssertEqual(store.locations[original.id + "::server-old"]?.page, 8)
    XCTAssertEqual(store.filePaths.count, 2)
    let archives = try await store.repository.duplicateCourseArchives(scope: "local")
    XCTAssertEqual(archives.first?.course, duplicate)
    XCTAssertEqual(archives.first?.record.id, duplicate.id)
    XCTAssertTrue(store.cloudState.localDeletedCourseIDs.contains(duplicate.id))
    XCTAssertNil(store.importJob)
    // A legacy export may carry a course after its import record was retired.
    // Reapplying the durable archive must hide it without needing a new parse.
    store.courses.append(duplicate)
    try await store.archiveEmptyDuplicateCourses(scope: "local", epoch: store.cloudState.epoch)
    XCTAssertEqual(store.courses.map(\.id), [original.id])
    let otherAccount = try await store.repository.duplicateCourseArchives(scope: "another-account")
    XCTAssertTrue(otherAccount.isEmpty)
    await store.flush()
  }

  func testDuplicateCourseWithPersonalNotesOrPreferencesIsPreserved() async throws {
    let store = try store()
    let original = Course(id: "workspace_original", name: "化学", resourceIDs: ["a"])
    let notesCourse = Course(id: "local-course:notes", name: "笔记课", resourceIDs: ["b"])
    let settingsCourse = Course(id: "local-course:settings", name: "个性化课", resourceIDs: ["c"])
    store.courses = [original, notesCourse, settingsCourse]
    store.importState.courses = [notesCourse, settingsCourse].map { course in
      CourseImportRecord(id: course.id,
        draft: CourseDraft(resourceIDs: course.resourceIDs, diagnosis: course.id == settingsCourse.id ? ["goal": ["exam"]] : [:]),
        books: [], job: ImportJob(bookIDs: course.resourceIDs, status: .failed))
    }
    store.notes = [StudyNote(kind: .text, title: "不要删除",
      anchor: NoteAnchor(courseID: notesCourse.id, bookID: "b", page: 1))]
    let path = try await store.repository.saveOriginalAttachment(Data("%PDF-1.4\nsame".utf8), extension: "pdf")
    store.filePaths = ["a": path, "b": path, "c": path]
    try await store.repository.warmContentIdentityIndex(scope: "local", books: store.filePaths)
    try await store.archiveEmptyDuplicateCourses(scope: "local", epoch: store.cloudState.epoch)
    XCTAssertEqual(store.courses.count, 3)
    let archives = try await store.repository.duplicateCourseArchives(scope: "local")
    XCTAssertTrue(archives.isEmpty)
    await store.flush()
  }

  func testDuplicateCourseWithUnselectedSavedTeachingContentIsPreserved() async throws {
    let store = try store()
    var content = try XCTUnwrap(ContentBundle.load([SourceContent].self, name: "sources").first)
    content.summary.book_id = "b"
    let original = Course(id: "workspace_original", name: "已有课", resourceIDs: ["a"])
    let saved = Course(id: "local-course:saved", name: "另一学习课", resourceIDs: ["b"])
    store.courses = [original, saved]
    store.sources = []
    store.cloudState.localCourseSources[saved.id] = [content]
    store.importState.courses = [CourseImportRecord(id: saved.id,
      draft: CourseDraft(resourceIDs: ["b"]), books: [], job: ImportJob(bookIDs: ["b"], status: .failed))]
    let path = try await store.repository.saveOriginalAttachment(Data("%PDF-1.4\nsame".utf8), extension: "pdf")
    store.filePaths = ["a": path, "b": path]
    try await store.repository.warmContentIdentityIndex(scope: "local", books: store.filePaths)
    try await store.archiveEmptyDuplicateCourses(scope: "local", epoch: store.cloudState.epoch)
    XCTAssertEqual(store.courses.count, 2)
    XCTAssertEqual(store.cloudState.localCourseSources[saved.id]?.count, 1)
    await store.flush()
  }

  func testPrimaryNavigationAndLegacyHomeLinksResolveToStudy() async throws {
    let store = try store()
    XCTAssertEqual(Route.allCases.filter(\.isTab), [.community, .study, .profile])
    store.completeOnboarding(name: "学习者", goal: nil, dailyTime: nil)
    XCTAssertEqual(store.route, .study)
    store.go(.home)
    XCTAssertEqual(store.route, .study)
    store.go(.library)
    store.back()
    XCTAssertEqual(store.route, .study)
    store.path = []
    store.route = .settings
    store.back()
    XCTAssertEqual(store.route, .study)
    await store.flush()
  }

  func testUnfinishedCourseOpensInStudyAndKeepsTheOtherCreationDraft() async throws {
    let store = try store()
    store.draft = CourseDraft(name: "第一门", resourceIDs: ["book-a"])
    let id = try XCTUnwrap(store.registerCourseImport())
    store.courseFeatureStartDraft()
    store.draft.name = "另一门课程的问卷"
    let otherDraft = store.draft
    store.openCourse(try XCTUnwrap(store.courses.first))
    XCTAssertEqual(store.route, .study)
    XCTAssertEqual(store.selectedCourseID, id)
    XCTAssertEqual(store.selectedBookID, "book-a")
    XCTAssertNil(store.currentSource)
    XCTAssertEqual(store.studyCoursePreparation, .materials)
    XCTAssertEqual(store.draft, otherDraft)
    store.updateCourseImport(id) { $0.job.status = .completed }
    XCTAssertEqual(store.studyCoursePreparation, .setup)
    store.resumeSelectedCourseSetup()
    XCTAssertEqual(store.route, .courseSetup)
    XCTAssertEqual(store.draft.name, "第一门")
    XCTAssertTrue(store.draft.parseCompleted)
    await store.flush()
  }

  func testAddingMaterialsDoesNotHideAnExistingReadyCourse() async throws {
    let store = try store()
    let source = try XCTUnwrap(ContentBundle.load([SourceContent].self, name: "sources")
      .first { $0.id == "book_biology_2" })
    store.sources = [source]
    store.draft = CourseDraft(name: "已有课程", resourceIDs: [source.id])
    let id = try XCTUnwrap(store.registerCourseImport())
    store.selectCourse(id)
    store.updateCourseImport(id) { $0.setupCompleted = true; $0.job.status = .running }
    XCTAssertTrue(store.hasStudyContent)
    XCTAssertNil(store.studyCoursePreparation)
    XCTAssertTrue(store.selectedCourseImport?.needsPreparation == true)
    store.updateCourseImport(id) { $0.setupCompleted = false }
    XCTAssertNil(store.studyCoursePreparation,
      "Unfinished setup must not hide an already available source directory or lesson")
    await store.flush()
  }

  func testVerifiedDirectoryRemainsVisibleWhenOnlyDiagnosticsFailed() async throws {
    let store = try store()
    let chapter = CloudChapter(chapter_id: "source-chapter", order: 1, title: "真实章节",
      start_page: 1, end_page: 3, summary: "原文摘要", knowledge_points: ["原文支持的知识点"], source_block_ids: [])
    let resource = CloudResource(id: "resource", book_id: "book-directory", workspace_id: "workspace",
      revision: 0, filename: "教材.pdf", status: "structured",
      structure: CloudStructure(title: "教材", summary: "原文摘要", source_page_count: 3, chapters: [chapter]),
      courses: [], progress: CloudProgress())
    store.sources = [CloudMapping.source(resource)]
    store.draft = CourseDraft(name: "目录已生成", resourceIDs: [resource.book_id])
    let id = try XCTUnwrap(store.registerCourseImport())
    store.selectCourse(id)
    store.updateCourseImport(id) {
      $0.job.status = .failed; $0.job.preparationStage = "diagnostics"; $0.job.preparationFailed = true
    }
    XCTAssertNil(store.studyCoursePreparation)
    XCTAssertEqual(store.courseCreationLabel(try XCTUnwrap(store.currentCourse)), "目录已就绪")
    XCTAssertEqual(store.currentSource?.summary.status, "source_ready")
    XCTAssertEqual(store.lessons.map(\.status), ["pending"])
    XCTAssertFalse(store.hasStudyContent)
    XCTAssertTrue(store.selectedCourseImport?.needsPreparation == true)
    await store.flush()
  }

  func testLegacyUnboundDraftIsRetainedBeforeStartingAnotherCourseAndAfterRelaunch() async throws {
    let store = try store()
    store.draft = CourseDraft(name: "保留的草稿", resourceIDs: ["draft-book"],
      diagnosis: ["goals": ["exam"]], step: 3, parseCompleted: true,
      creationWorkspaceID: "existing-workspace")
    store.courseFeatureStartDraft()
    let course = try XCTUnwrap(store.courses.first)
    let record = try XCTUnwrap(store.courseImport(for: course.id))
    XCTAssertEqual(record.draft.step, 3)
    XCTAssertEqual(record.draft.creationWorkspaceID, "existing-workspace")
    XCTAssertEqual(record.job.status, .completed)
    XCTAssertEqual(record.setupCompleted, false)
    XCTAssertTrue(store.draft.resourceIDs.isEmpty)
    await store.flush()
    let reopened = AppStore(repository: store.repository)
    await reopened.boot()
    reopened.openCourse(try XCTUnwrap(reopened.courses.first))
    XCTAssertEqual(reopened.studyCoursePreparation, .setup)
    reopened.resumeSelectedCourseSetup()
    XCTAssertEqual(reopened.draft.name, "保留的草稿")
    XCTAssertEqual(reopened.draft.step, 3)
    XCTAssertEqual(reopened.cloudState.creationWorkspaceID, "existing-workspace")
    await reopened.flush()
  }

  func testCatalogDraftRemainsSelectableAndResumesItsQuestionnaire() async throws {
    let store = try store()
    store.draft = CourseDraft(name: "共享课程草稿", resourceIDs: ["catalog-book"],
      diagnosis: ["urgency": ["steady"]], step: 2, catalogBookID: "catalog-book")
    store.courseFeatureStartDraft()
    let course = try XCTUnwrap(store.courses.first)
    store.openCourse(course)
    XCTAssertEqual(store.courseCreationLabel(course), "待完善")
    XCTAssertEqual(store.studyCoursePreparation, .setup)
    store.resumeSelectedCourseSetup()
    XCTAssertEqual(store.route, .courseSetup)
    XCTAssertEqual(store.draft.catalogBookID, "catalog-book")
    XCTAssertEqual(store.draft.step, 2)
    await store.flush()
  }

  func testResumeUsesSavedLessonInsteadOfDirectoryBrowsingAndRejectsDeletedLocations() async throws {
    let store = try store()
    let source = try XCTUnwrap(ContentBundle.load([SourceContent].self, name: "sources")
      .first { $0.id == "book_biology_2" })
    store.sources = [source]
    let course = Course(name: "生物", resourceIDs: [source.id], activeResourceID: source.id)
    store.courses = [course]
    store.selectCourse(course.id)
    let lesson = try XCTUnwrap(source.lessons.first { $0.chapter_id == "c2s1" })
    store.openLesson(lesson)
    store.selectedChapterID = "c1s1"
    store.go(.study)
    XCTAssertEqual(store.studyResumeChapter?.id, "c2s1")
    XCTAssertEqual(store.locations[store.locationKey]?.lessonID, lesson.id)
    store.locations[store.locationKey] = StudyLocation(chapterID: "deleted", lessonID: "deleted")
    XCTAssertNotNil(store.studyResumeChapter)
    XCTAssertNotEqual(store.studyResumeChapter?.id, "deleted")
    await store.flush()
  }

  func testCourseGenerationStateIsScopedToTheSelectedWorkspaceAndResource() async throws {
    let store = try store()
    let resources = ["a", "b"].map { id in
      CloudResource(id: "resource-" + id, book_id: "book-" + id,
        workspace_id: "workspace-" + id, revision: 0, filename: "教材.pdf",
        status: "structured", courses: [], progress: CloudProgress())
    }
    store.cloudState.workspaces = resources.map { resource in
      CloudWorkspace(id: resource.workspace_id, name: resource.filename,
        preferences: [:], created: 0, updated: 0, resources: [resource])
    }
    store.rebuildCloudCourses()
    store.cloudState.diagnosisWorkspaceID = "workspace-a"
    store.cloudState.diagnosisResourceID = "resource-a"
    store.cloudState.courseJob = CloudCourseJob(job_id: "job-a", session_id: "session-a",
      chapter_id: "chapter", status: .running, stage: "writing", attempts: 1, retryable: false)
    store.selectCourse("workspace-a")
    XCTAssertEqual(store.studyCoursePreparation, .generation)
    store.selectCourse("workspace-b")
    XCTAssertEqual(store.studyCoursePreparation, .diagnosis)
    XCTAssertNil(store.selectedCourseJob)
    store.selectCourse("workspace-a")
    XCTAssertEqual(store.selectedCourseJob?.job_id, "job-a")
    store.cloudState.showDiagnosis = true
    store.pauseCloudCourseGeneration()
    XCTAssertFalse(store.cloudState.showDiagnosis)
    XCTAssertEqual(store.selectedCourseJob?.status, .running)
    await store.flush()
  }

  func testFinishedQuestionnaireResumesDirectoryConfirmation() async throws {
    let store = try store()
    store.draft = CourseDraft(name: "待确认", resourceIDs: ["book"],
      diagnosis: Dictionary(uniqueKeysWithValues: CourseDiagnosisQuestion.all.map { ($0.id, ["answer"]) }),
      parseCompleted: true)
    store.retainPendingCourseDraft()
    store.openCourse(try XCTUnwrap(store.courses.first))
    store.resumeSelectedCourseSetup()
    XCTAssertEqual(store.route, .chapterConfirm)
    await store.flush()
  }
  func testOpeningParsedCourseResumesSavedQuestionsAndBackShowsProgress() async throws {
    let store = try store()
    store.draft = CourseDraft(name: "待回答", resourceIDs: ["book"],
      diagnosis: ["urgency": ["steady"], "timePattern": ["block"]], step: 2, parseCompleted: true)
    store.retainPendingCourseDraft()
    let course = try XCTUnwrap(store.courses.first)
    store.go(.study)
    store.openCourse(course)
    XCTAssertEqual(store.route, .courseSetup)
    XCTAssertEqual(store.draft.step, 2)
    XCTAssertEqual(store.courseCreationSteps[2].detail, "已回答 2/6 题")
    XCTAssertTrue(store.courseCreationSteps[0].completed)
    XCTAssertFalse(store.courseCreationSteps[3].completed)
    XCTAssertFalse(store.courseCreationSteps[4].completed)
    store.pauseCourseSetup()
    XCTAssertEqual(store.route, .study)
    XCTAssertEqual(store.studyCoursePreparation, .setup)
    store.openCourse(course)
    XCTAssertEqual(store.route, .courseSetup)
    XCTAssertEqual(store.draft.step, 2)
    await store.flush()
  }

  func testCreationProgressDoesNotInventCompletedStepsFromQuestionPosition() async throws {
    let store = try store()
    store.draft = CourseDraft(name: "未回答", resourceIDs: ["book"], step: 5)
    store.retainPendingCourseDraft()
    store.selectCourse(try XCTUnwrap(store.courses.first).id)
    XCTAssertEqual(store.courseCreationSteps[2].detail, "已回答 0/6 题")
    XCTAssertFalse(store.courseCreationSteps[2].completed)
    XCTAssertFalse(store.courseCreationSteps[3].completed)
    XCTAssertFalse(store.courseCreationSteps[4].completed)
    await store.flush()
  }

  func testAutomaticPreparationHidesContinueUntilUserActionIsRequired() async throws {
    let store = try store()
    store.draft = CourseDraft(name: "后台课程", resourceIDs: ["book"])
    let id = try XCTUnwrap(store.registerCourseImport())
    store.selectCourse(id)
    XCTAssertTrue(store.coursePreparationRequiresAction(.materials))
    store.updateCourseImport(id) { $0.started = true; $0.job.status = .running }
    XCTAssertFalse(store.coursePreparationRequiresAction(.materials))
    XCTAssertTrue(store.courseCreationSteps[0].active)
    store.updateCourseImport(id) { $0.job.status = .queued }
    XCTAssertFalse(store.coursePreparationRequiresAction(.materials))
    store.updateCourseImport(id) { $0.job.status = .failed }
    XCTAssertTrue(store.coursePreparationRequiresAction(.materials))
    XCTAssertTrue(store.coursePreparationRequiresAction(.setup))
    store.cloudState.diagnosisWorkspaceID = id
    store.cloudState.diagnosisResourceID = store.cloudResource?.id
    store.cloudState.courseJob = CloudCourseJob(job_id: "job", session_id: "session",
      chapter_id: "chapter", status: .running, stage: "writing", attempts: 1, retryable: false)
    XCTAssertFalse(store.coursePreparationRequiresAction(.generation))
    XCTAssertTrue(store.courseCreationSteps[4].active)
    store.cloudState.courseJob?.status = .queued
    XCTAssertFalse(store.coursePreparationRequiresAction(.generation))
    store.cloudState.courseJob?.status = .failed
    XCTAssertTrue(store.coursePreparationRequiresAction(.generation))
    await store.flush()
  }

}
