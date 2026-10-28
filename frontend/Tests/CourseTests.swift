import XCTest

@testable import BookCourse

final class CourseTests: XCTestCase {
  private func chapter(_ id: String, level: Int = 1, start: Int, end: Int, parent: String? = nil)
    -> Chapter
  {
    Chapter(
      chapter_id: id, level: level, source_title: id, ai_title: id, page_start: start,
      page_end: end, confidence: 90, status: "匹配良好", source: "目录", parent_id: parent)
  }
  func testChapterEditorRejectsOutOfDocumentAndSiblingOverlap() {
    let first = chapter("first", start: 1, end: 10)
    let second = chapter("second", start: 11, end: 20)
    var invalid = second
    invalid.page_start = 10
    XCTAssertTrue(
      CourseChapterValidation.validate(invalid, in: [first, second], pageCount: 30)?.contains("重叠")
        == true)
    invalid = second
    invalid.page_end = 31
    XCTAssertTrue(
      CourseChapterValidation.validate(invalid, in: [first, second], pageCount: 30)?.contains("总页数")
        == true)
    XCTAssertNil(CourseChapterValidation.validate(second, in: [first, second], pageCount: 30))
  }
  func testChapterEditorPreservesHierarchyAndRejectsCycle() {
    let root = chapter("root", start: 1, end: 20)
    let child = chapter("child", level: 2, start: 2, end: 10, parent: "root")
    let grandchild = chapter("grandchild", level: 3, start: 3, end: 5, parent: "child")
    let tree = [root, child, grandchild]
    XCTAssertEqual(
      CourseChapterValidation.descendants(of: "root", in: tree), ["child", "grandchild"])
    var invalid = root
    invalid.parent_id = "grandchild"
    XCTAssertTrue(
      CourseChapterValidation.validate(invalid, in: tree, pageCount: 20)?.contains("子章节") == true)
    invalid = child
    invalid.page_start = 4
    XCTAssertTrue(
      CourseChapterValidation.validate(invalid, in: tree, pageCount: 20)?.contains("包含子章节") == true)
    invalid = child
    invalid.page_end = 21
    XCTAssertTrue(
      CourseChapterValidation.validate(invalid, in: tree, pageCount: 30)?.contains("上级章节") == true)
  }
  func testFreeReviewIsExclusiveButCanBeReplacedOrDeselected() {
    let review = CourseDiagnosisQuestion.all.first { $0.id == "reviews" }!
    XCTAssertEqual(review.toggle("free", in: ["periodic", "mistakes"]), ["free"])
    XCTAssertEqual(review.toggle("notes", in: ["free"]), ["notes"])
    XCTAssertEqual(review.toggle("free", in: ["free"]), [])
    let urgency = CourseDiagnosisQuestion.all.first { $0.id == "urgency" }!
    XCTAssertEqual(urgency.toggle("steady", in: ["urgent"]), ["steady"])
  }

  func testCourseRecommendationsRankAcrossResourcesAndShareDailyBudget() throws {
    let base = try ContentBundle.load([SourceContent].self, name: "sources")[0]
    var first = base
    first.summary.book_id = "rank_a"
    var second = base
    second.summary.book_id = "rank_b"
    first.plan.tasks = [task("a", title: "阅读概念", minutes: 30)]
    second.plan.tasks = [
      task("b", title: "章节练习", minutes: 12), task("c", title: "错题复盘", minutes: 15),
    ]
    let course = Course(
      name: "跨资料课程", resourceIDs: [first.id, second.id],
      diagnosis: [
        "urgency": ["urgent"], "timePattern": ["fragmented"], "goals": ["exam"],
        "aids": ["mistakes"],
      ])
    let result = CourseRecommendations.build(
      course: course,
      preferences: LearnerPreferences(displayName: "测试", primaryGoal: "exam", dailyTime: "under30"),
      sources: [first, second])
    XCTAssertEqual(result.budget, 20)
    XCTAssertEqual(result.recommendations.map(\.bookID), ["rank_b", "rank_b"])
    XCTAssertEqual(result.recommendations.map(\.minutes), [12, 8])
    XCTAssertEqual(result.recommendations.map(\.segments), [[6, 6], [8]])
    XCTAssertEqual(result.recommendations.map(\.originalMinutes), [12, 15])
    XCTAssertEqual(result.reviewDays, 1)
    XCTAssertEqual(result.recommendations.first?.reason, "紧迫学习：先抓练习和薄弱点")
  }
  func testRecommendationExcludesCompletedTasksAndKeepsReviewPreferences() throws {
    var source = try ContentBundle.load([SourceContent].self, name: "sources")[0]
    source.plan.tasks = [
      task("done", title: "章节练习", minutes: 30, status: "done"),
      task("read", title: "阅读概念", minutes: 10),
    ]
    var course = Course(
      name: "课程", resourceIDs: [source.id],
      diagnosis: ["urgency": ["relaxed"], "reviews": ["free"]])
    var result = CourseRecommendations.build(course: course, preferences: nil, sources: [source])
    XCTAssertEqual(result.recommendations.map { $0.task.id }, ["read"])
    XCTAssertNil(result.reviewDays)
    course.diagnosis["reviews"] = ["periodic"]
    result = CourseRecommendations.build(course: course, preferences: nil, sources: [source])
    XCTAssertEqual(result.reviewDays, 7)
  }
  func testOnboardingDraftSurvivesReopeningAndIsClearedOnCompletion() {
    let suite = "BookCourseTests.Onboarding." + UUID().uuidString
    let defaults = UserDefaults(suiteName: suite)!
    defer { defaults.removePersistentDomain(forName: suite) }
    let draft = LearnerOnboardingDraft(
      step: 5, name: "学习者", goal: "exam", dailyTime: "30to60",
      learningBarriers: ["understanding", "memory"], guidanceStyles: ["examples", "stepByStep"],
      planningSupport: "collaborative", learningStage: "university")
    draft.save(defaults: defaults, scope: "account-a")
    let reopened = UserDefaults(suiteName: suite)!
    XCTAssertEqual(LearnerOnboardingDraft.load(defaults: reopened, scope: "account-a"), draft)
    XCTAssertEqual(
      LearnerOnboardingDraft.load(defaults: reopened, scope: "account-b"), LearnerOnboardingDraft())
    LearnerOnboardingDraft.clear(defaults: reopened, scope: "account-a")
    XCTAssertEqual(LearnerOnboardingDraft.load(defaults: defaults), LearnerOnboardingDraft())
  }
  func testLegacyPreferencesAndDraftResumeWithoutLosingOriginalAnswers() throws {
    let legacy = try JSONDecoder().decode(
      LearnerPreferences.self,
      from: Data(#"{"displayName":"已有学习者","primaryGoal":"exam","dailyTime":"under30"}"#.utf8))
    XCTAssertFalse(legacy.hasCompletedOnboarding)
    XCTAssertEqual(legacy.learningBarriers, [])
    let suite = "BookCourseTests.LegacyOnboarding." + UUID().uuidString
    let defaults = UserDefaults(suiteName: suite)!
    defer { defaults.removePersistentDomain(forName: suite) }
    let resumed = LearnerOnboardingDraft.load(defaults: defaults, preferences: legacy)
    XCTAssertEqual(resumed.step, 3)
    XCTAssertEqual(resumed.name, "已有学习者")
    XCTAssertEqual(resumed.goal, "exam")
    XCTAssertEqual(resumed.dailyTime, "under30")
    let originalDraft = Data(
      #"{"step":2,"name":"草稿学习者","goal":"interest","dailyTime":"30to60"}"#.utf8)
    defaults.set(originalDraft, forKey: LearnerOnboardingDraft.storageKey)
    let restoredDraft = LearnerOnboardingDraft.load(defaults: defaults, preferences: legacy)
    XCTAssertEqual(restoredDraft.step, 2)
    XCTAssertEqual(restoredDraft.name, "草稿学习者")
    XCTAssertEqual(restoredDraft.guidanceStyles, [])
  }
  func testHomeNextStepPrioritizesSavedSectionThenUnfinishedPlanAndSkipsContainers() throws {
    var source = try ContentBundle.load([SourceContent].self, name: "sources")[0]
    let root = chapter("root", start: 1, end: 20)
    let first = chapter("first", level: 2, start: 1, end: 10, parent: "root")
    let second = chapter("second", level: 2, start: 11, end: 20, parent: "root")
    var finished = task("done", title: "已完成", minutes: 10, status: "done")
    finished.chapter_id = "first"
    var pending = task("pending", title: "下一步", minutes: 10)
    pending.chapter_id = "second"
    source.plan.tasks = [finished, pending]
    XCTAssertEqual(
      CourseHomeNextStep.resolve(
        chapters: [root, first, second], locationChapterID: nil, plan: source.plan, lessons: [])?
        .id, "second")
    XCTAssertEqual(
      CourseHomeNextStep.resolve(
        chapters: [root, first, second], locationChapterID: "first", plan: source.plan, lessons: [])?
        .id, "first")
    XCTAssertEqual(
      CourseHomeNextStep.resolve(
        chapters: [root, first, second], locationChapterID: "root", plan: source.plan, lessons: [])?
        .id, "second")
  }
  func testRepositoryCourseProgressExcludesPlanPresentationMocks() {
    let tasks = [
      task("pending", title: "未完成", minutes: 10),
      task("real-done", title: "已完成", minutes: 10, status: "done"),
      task("front-end-mock-plan:completed", title: "演示已完成", minutes: 10, status: "done"),
    ]
    XCTAssertEqual(CourseRecommendations.repositoryProgress(tasks: tasks), 0.5)
    XCTAssertEqual(CourseRecommendations.repositoryProgress(tasks: [tasks[2]]), 0)
    XCTAssertEqual(CourseRecommendations.repositoryProgress(tasks: []), 0)
  }
  private func task(_ id: String, title: String, minutes: Int, status: String = "pending")
    -> StudyTask
  {
    StudyTask(
      task_id: id, user_id: "local_user", day: 1, title: title, task_type: "lesson",
      minutes: minutes, status: status, weak_points: [])
  }
}
