import Foundation

/// The source course recommender is deterministic. It ranks existing tasks across
/// every resource in the course, then allocates the learner's daily time budget.
enum CourseRecommendationKind: String { case lesson, practice, mistakes, flashcards, notes, visual }
struct CourseRecommendation: Identifiable {
  let bookID: String
  let task: StudyTask
  let kind: CourseRecommendationKind
  let minutes: Int
  let originalMinutes: Int
  let segments: [Int]
  let reason: String
  var id: String { bookID + "::" + task.id }
}
struct CourseRecommendationPlan {
  let budget: Int
  let recommendations: [CourseRecommendation]
  let reviewDays: Int?
}
enum CourseRecommendations {
  /// Presentation-only mock tasks belong to the old plan screen, not repository course progress.
  static func repositoryProgress(tasks: [StudyTask]) -> Double {
    let persisted = tasks.filter { !$0.task_id.hasPrefix("front-end-mock-plan:") }
    guard !persisted.isEmpty else { return 0 }
    return Double(persisted.filter { $0.status == "done" }.count) / Double(persisted.count)
  }

  static let defaultDiagnosis: [String: [String]] = [
    "urgency": ["steady"], "timePattern": ["block"], "goals": ["systematic"],
    "contentFoci": ["principles"], "aids": ["chat"], "reviews": ["periodic"],
  ]
  static func diagnosis(_ course: Course) -> [String: [String]] {
    defaultDiagnosis.merging(course.diagnosis) { _, value in value }
  }
  static func build(course: Course, preferences: LearnerPreferences?, sources: [SourceContent])
    -> CourseRecommendationPlan
  {
    let answers = diagnosis(course)
    let budget: Int
    switch preferences?.dailyTime {
    case "under30": budget = 20
    case "1to2h": budget = 90
    case "over2h": budget = 120
    default: budget = 45
    }
    let urgency = answers["urgency"]?.first ?? "steady"
    let reviewDays: Int? =
      answers["reviews"]?.contains("free") == true
      ? nil : urgency == "urgent" ? 1 : urgency == "steady" ? 3 : 7
    struct Ranked {
      let bookID: String
      let task: StudyTask
      let bookOrder: Int
      let taskOrder: Int
      let score: Int
      let kind: CourseRecommendationKind
    }
    var ranked: [Ranked] = []
    for (bookOrder, bookID) in course.resourceIDs.enumerated() {
      guard let source = sources.first(where: { $0.id == bookID }) else { continue }
      for (taskOrder, task) in source.plan.tasks.enumerated()
      where task.status != "done" && task.status != "completed" {
        let kind = classify(task)
        ranked.append(
          Ranked(
            bookID: bookID, task: task, bookOrder: bookOrder, taskOrder: taskOrder,
            score: rank(
              kind, diagnosis: answers, globalGoal: preferences?.primaryGoal ?? "systematic"),
            kind: kind))
      }
    }
    ranked.sort {
      if $0.score != $1.score { return $0.score > $1.score }
      if $0.bookOrder != $1.bookOrder { return $0.bookOrder < $1.bookOrder }
      if $0.task.day != $1.task.day { return $0.task.day < $1.task.day }
      return $0.taskOrder < $1.taskOrder
    }
    var remaining = budget
    var recommendations: [CourseRecommendation] = []
    for candidate in ranked {
      if remaining < 5 { break }
      let minutes = min(max(5, candidate.task.minutes), remaining)
      remaining -= minutes
      let segments: [Int]
      if answers["timePattern"]?.first == "fragmented" {
        let count = Int(ceil(Double(minutes) / 10))
        let base = minutes / count
        segments = (0..<count).map { base + ($0 < minutes % count ? 1 : 0) }
      } else {
        segments = [minutes]
      }
      recommendations.append(
        CourseRecommendation(
          bookID: candidate.bookID, task: candidate.task, kind: candidate.kind, minutes: minutes,
          originalMinutes: candidate.task.minutes, segments: segments,
          reason: reason(candidate.kind, diagnosis: answers)))
    }
    return CourseRecommendationPlan(
      budget: budget, recommendations: recommendations, reviewDays: reviewDays)
  }
  static func classify(_ task: StudyTask) -> CourseRecommendationKind {
    let text = task.title + " " + task.task_type
    if text.range(of: "错题|卡点", options: .regularExpression) != nil { return .mistakes }
    if text.range(of: "练习|测验|诊断|考试|限时", options: .regularExpression) != nil { return .practice }
    if text.range(of: "闪卡|复习|回忆", options: .regularExpression) != nil { return .flashcards }
    if text.range(of: "笔记|总结", options: .regularExpression) != nil { return .notes }
    if text.range(of: "图示|动画|视频", options: .regularExpression) != nil { return .visual }
    return .lesson
  }
  private static func rank(
    _ kind: CourseRecommendationKind, diagnosis d: [String: [String]], globalGoal: String
  ) -> Int {
    let global: [String: [CourseRecommendationKind]] = [
      "systematic": [.lesson, .practice, .flashcards], "exam": [.practice, .mistakes, .flashcards],
      "growth": [.lesson, .practice, .notes], "interest": [.lesson, .visual, .notes],
    ]
    let goals: [String: [CourseRecommendationKind]] = [
      "systematic": [.lesson, .practice, .flashcards], "exam": [.practice, .mistakes, .flashcards],
      "professional": [.lesson, .visual, .practice], "interest": [.lesson, .visual, .notes],
      "gaps": [.mistakes, .practice, .flashcards],
    ]
    var score = 0
    if let position = (global[globalGoal] ?? global["systematic"]!).firstIndex(of: kind) {
      score += 3 - position
    }
    for (index, goal) in (d["goals"] ?? []).enumerated() {
      if let position = goals[goal]?.firstIndex(of: kind) {
        score += max(1, 12 - index * 2 - position * 3)
      }
    }
    func has(_ key: String, _ value: String) -> Bool { d[key]?.contains(value) == true }
    if has("urgency", "urgent") && (kind == .practice || kind == .mistakes) { score += 8 }
    if has("urgency", "relaxed") && (kind == .lesson || kind == .notes) { score += 6 }
    if has("contentFoci", "exercises") && kind == .practice { score += 4 }
    if has("contentFoci", "notes") && kind == .notes { score += 4 }
    if has("contentFoci", "principles") && kind == .lesson { score += 3 }
    if has("contentFoci", "practice") && kind == .visual { score += 2 }
    if has("contentFoci", "keyPoints") && kind == .flashcards { score += 3 }
    if has("aids", "mistakes") && kind == .mistakes { score += 3 }
    if has("aids", "guidedNotes") && kind == .notes { score += 3 }
    if has("aids", "video") && kind == .visual { score += 3 }
    if has("reviews", "alongside") && kind == .practice { score += 3 }
    if has("reviews", "periodic") && kind == .flashcards { score += 3 }
    if has("reviews", "mistakes") && kind == .mistakes { score += 3 }
    if has("reviews", "notes") && kind == .notes { score += 3 }
    return score
  }
  private static func reason(_ kind: CourseRecommendationKind, diagnosis d: [String: [String]])
    -> String
  {
    func has(_ key: String, _ value: String) -> Bool { d[key]?.contains(value) == true }
    if has("urgency", "urgent") && (kind == .practice || kind == .mistakes) {
      return "紧迫学习：先抓练习和薄弱点"
    }
    if has("goals", "exam") && kind == .practice { return "应试备考：优先练习" }
    if has("goals", "gaps") && kind == .mistakes { return "查漏补缺：先看卡点" }
    if has("contentFoci", "notes") && kind == .notes { return "内容侧重：整理笔记" }
    if has("reviews", "periodic") && kind == .flashcards { return "复习偏好：定期回忆" }
    if has("urgency", "relaxed") { return "按轻松节奏继续学习" }
    return "根据课程目标推荐"
  }
}

struct LearnerOnboardingDraft: Codable, Equatable {
  var step = 0
  var name = ""
  var learningStage: String?
  var goal: String?
  var dailyTime: String?
  var learningBarriers: [String] = []
  var guidanceStyles: [String] = []
  var planningSupport: String?
  static let stepCount = 6
  static let storageKey = "bookcourse.native-onboarding-draft.v1"

  init(
    step: Int = 0, name: String = "", goal: String? = nil, dailyTime: String? = nil,
    learningBarriers: [String] = [], guidanceStyles: [String] = [], planningSupport: String? = nil, learningStage: String? = nil
  ) {
    self.step = step
    self.name = name
    self.learningStage = learningStage
    self.goal = goal
    self.dailyTime = dailyTime
    self.learningBarriers = learningBarriers
    self.guidanceStyles = guidanceStyles
    self.planningSupport = planningSupport
  }
  private enum CodingKeys: String, CodingKey {
    case step, name, learningStage, goal, dailyTime, learningBarriers, guidanceStyles, planningSupport
  }
  init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    step = try values.decodeIfPresent(Int.self, forKey: .step) ?? 0
    name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
    learningStage = try values.decodeIfPresent(String.self, forKey: .learningStage)
    goal = try values.decodeIfPresent(String.self, forKey: .goal)
    dailyTime = try values.decodeIfPresent(String.self, forKey: .dailyTime)
    learningBarriers = try values.decodeIfPresent([String].self, forKey: .learningBarriers) ?? []
    guidanceStyles = try values.decodeIfPresent([String].self, forKey: .guidanceStyles) ?? []
    planningSupport = try values.decodeIfPresent(String.self, forKey: .planningSupport)
  }
  static func load(
    defaults: UserDefaults = .standard, scope: String? = nil, preferences: LearnerPreferences? = nil
  ) -> Self {
    guard let data = defaults.data(forKey: scope.map { storageKey + "." + $0 } ?? storageKey),
      var value = try? JSONDecoder().decode(Self.self, from: data)
    else {
      return Self(
        step: preferences.map { $0.hasCompletedOnboarding ? 0 : 3 } ?? 0,
        name: preferences?.displayName ?? "", goal: preferences?.primaryGoal,
        dailyTime: preferences?.dailyTime, learningBarriers: preferences?.learningBarriers ?? [],
        guidanceStyles: preferences?.guidanceStyles ?? [],
        planningSupport: preferences?.planningSupport, learningStage: preferences?.learningStage)
    }
    value.step = min(stepCount - 1, max(0, value.step))
    value.name = String(value.name.prefix(30))
    return value
  }
  func save(defaults: UserDefaults = .standard, scope: String? = nil) {
    if let data = try? JSONEncoder().encode(self) {
      defaults.set(data, forKey: scope.map { Self.storageKey + "." + $0 } ?? Self.storageKey)
    }
  }
  static func clear(defaults: UserDefaults = .standard, scope: String? = nil) {
    defaults.removeObject(forKey: scope.map { storageKey + "." + $0 } ?? storageKey)
  }
}

/// Home follows a saved section, then the first unfinished plan task, then lesson/directory order.
/// Merely selecting a chapter in another screen does not overwrite the saved reading location.
enum CourseHomeNextStep {
  static func resolve(
    chapters: [Chapter], locationChapterID: String?, plan: StudyPlan?, lessons: [Lesson]
  ) -> Chapter? {
    let byID = Dictionary(chapters.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
    func navigable(_ id: String?) -> Chapter? {
      guard let id, let chapter = byID[id] else { return nil }
      var current = chapter
      var visited = Set<String>()
      while true {
        guard visited.insert(current.id).inserted else { return nil }
        guard let parent = current.parent_id, let next = byID[parent] else { break }
        current = next
      }
      if current.id == chapter.id && chapters.contains(where: { $0.parent_id == chapter.id }) {
        return nil
      }
      return chapter
    }
    if let saved = navigable(locationChapterID) { return saved }
    for task in plan?.tasks ?? [] where task.status != "done" && task.status != "completed" {
      if let chapter = navigable(task.chapter_id)
        ?? navigable(lessons.first { $0.id == task.lesson_id }?.chapter_id)
      {
        return chapter
      }
    }
    for lesson in lessons where lesson.lesson_kind != "module_intro" {
      if let chapter = navigable(lesson.chapter_id) { return chapter }
    }
    return chapters.lazy.compactMap { navigable($0.id) }.first
  }
}
