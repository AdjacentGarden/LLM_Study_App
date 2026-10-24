import CryptoKit
import Foundation
import Observation
import SwiftUI

@MainActor @Observable final class AppStore {
  var route: Route = .onboarding
  var path: [Route] = []
  var sheet: SheetRoute?
  var toast: String?
  var isPad = false
  var bootError: String?
  var isLoading = true
  let courseState = CourseState()
  var chapterDrafts: [String: [Chapter]] {
    get { courseState.chapterDrafts }
    set { courseState.chapterDrafts = newValue }
  }
  var credits: Int {
    get { courseState.credits }
    set { courseState.credits = newValue }
  }
  var sources: [SourceContent] {
    get { courseState.sources }
    set { courseState.sources = newValue }
  }
  var community: [CommunityBook] {
    get { courseState.community }
    set { courseState.community = newValue }
  }
  var shelf: [ShelfBook] {
    get { courseState.shelf }
    set { courseState.shelf = newValue }
  }
  var courses: [Course] {
    get { courseState.courses }
    set { courseState.courses = newValue }
  }
  var preferences: LearnerPreferences? {
    get { courseState.preferences }
    set { courseState.preferences = newValue }
  }
  var draft: CourseDraft {
    get { courseState.draft }
    set { courseState.draft = newValue }
  }
  var selectedCourseID: String? {
    get { courseState.selectedCourseID }
    set { courseState.selectedCourseID = newValue }
  }
  var selectedBookID: String? {
    get { courseState.selectedBookID }
    set { courseState.selectedBookID = newValue }
  }
  var selectedCommunityID: String? {
    get { courseState.selectedCommunityID }
    set { courseState.selectedCommunityID = newValue }
  }
  var dismissedSourceIDs: Set<String> {
    get { courseState.dismissedSourceIDs }
    set { courseState.dismissedSourceIDs = newValue }
  }
  let learningState = LearningSessionState()
  var selectedChapterID: String? {
    get { learningState.selectedChapterID }
    set { learningState.selectedChapterID = newValue }
  }
  var selectedLessonID: String? {
    get { learningState.selectedLessonID }
    set { learningState.selectedLessonID = newValue }
  }
  var assignmentAnswer: String {
    get { learningState.assignmentAnswer }
    set { learningState.assignmentAnswer = newValue }
  }
  var diagnosisText: String {
    get { learningState.diagnosisText }
    set { learningState.diagnosisText = newValue }
  }
  var latestDiagnosis: Diagnosis? {
    get { learningState.latestDiagnosis }
    set { learningState.latestDiagnosis = newValue }
  }
  var mistakes: [MistakeRecord] {
    get { learningState.mistakes }
    set { learningState.mistakes = newValue }
  }
  var selectedQuizIndex: Int {
    get { learningState.selectedQuizIndex }
    set { learningState.selectedQuizIndex = newValue }
  }
  var locations: [String: StudyLocation] {
    get { learningState.locations }
    set { learningState.locations = newValue }
  }
  var mastered: Set<String> {
    get { learningState.mastered }
    set { learningState.mastered = newValue }
  }
  let readerState = ReaderState()
  var sourceTarget: SourcePageTarget? {
    get { readerState.sourceTarget }
    set { readerState.sourceTarget = newValue }
  }
  var selectedQuote: String {
    get { readerState.selectedQuote }
    set { readerState.selectedQuote = newValue }
  }
  var selectedAsset: BookAsset? {
    get { readerState.selectedAsset }
    set { readerState.selectedAsset = newValue }
  }
  let noteState = StudyNoteState()
  var noteKind: NoteKind {
    get { noteState.noteKind }
    set { noteState.noteKind = newValue }
  }
  var notes: [StudyNote] {
    get { noteState.notes }
    set { noteState.notes = newValue }
  }
  let importState = ImportTaskState()
  var importJob: ImportJob? {
    get { importState.job }
    set { importState.job = newValue }
  }
  var processingProgress: Double {
    get { importState.processingProgress }
    set { importState.processingProgress = newValue }
  }
  var processingMessage: String {
    get { importState.processingMessage }
    set { importState.processingMessage = newValue }
  }
  var importedFileURL: URL? {
    get { importState.importedFileURL }
    set { importState.importedFileURL = newValue }
  }
  var importError: String? {
    get { importState.importError }
    set { importState.importError = newValue }
  }
  let assistantState = AssistantConversationState()
  var globalAssistantOpen: Bool {
    get { assistantState.globalAssistantOpen }
    set { assistantState.globalAssistantOpen = newValue }
  }
  var chatMessages: [ChatMessage] {
    get { assistantState.chatMessages }
    set { assistantState.chatMessages = newValue }
  }
  var chatLoading: Bool {
    get { assistantState.chatLoading }
    set { assistantState.chatLoading = newValue }
  }
  @ObservationIgnored let repository: LocalBookCourseRepository
  @ObservationIgnored var filePaths: [String: String] = [:]
  @ObservationIgnored var persistTask: Task<Void, Never>?
  @ObservationIgnored private var processTask: Task<Void, Never>?
  @ObservationIgnored private var processGeneration = UUID()
  @ObservationIgnored private var toastTask: Task<Void, Never>?
  @ObservationIgnored private var creditReservations: [String: Int] = [:]
  @ObservationIgnored private var creditRevision = 0
  @ObservationIgnored var selectionGeneration = 0
  @ObservationIgnored let assistant: AssistantService
  @ObservationIgnored var cloud: CloudAPI?
  let cloudState = CloudState()
  init(repository: LocalBookCourseRepository, cloud: CloudAPI? = nil) {
    self.repository = repository
    self.cloud = cloud
    self.assistant = AssistantService()
  }
  var currentCourse: Course? { courses.first { $0.id == selectedCourseID } }
  var currentSource: SourceContent? { sources.first { $0.id == selectedBookID } }
  var chapters: [Chapter] {
    selectedBookID.flatMap { chapterDrafts[$0] } ?? currentSource?.chapters ?? []
  }
  var lessons: [Lesson] { currentSource?.lessons ?? [] }
  var activeLesson: Lesson? {
    lessons.first { $0.id == selectedLessonID } ?? lessons.first {
      $0.chapter_id == selectedChapterID
    } ?? (selectedChapterID == nil ? lessons.first : nil)
  }
  var flashcards: [Flashcard] {
    guard let s = currentSource else { return [] }
    let scoped = s.flashcards.filter { $0.chapter_id == selectedChapterID }
    return selectedChapterID == nil ? s.flashcards : scoped
  }
  var quizzes: [QuizQuestion] {
    guard let s = currentSource else { return [] }
    return selectedChapterID == nil
      ? s.quizzes : s.quizzes.filter { $0.chapter_id == selectedChapterID }
  }
  var plan: StudyPlan? { currentSource?.plan }
  /// Explicit reading completion is independent of a daily plan or knowledge mastery.
  var completedChapterIDs: Set<String> {
    if let resource = cloudResource {
      return Set((cloudState.pending[resource.id]?.value ?? resource.progress).completed_chapters)
    }
    return Set((plan?.tasks ?? []).filter { ["done", "completed"].contains($0.status) }.compactMap(\.chapter_id))
  }
  var currentTitle: String { currentCourse?.name ?? currentSource?.summary.title ?? "我的课程" }
  var chapterTitle: String {
    chapters.first { $0.id == selectedChapterID }?.title ?? activeLesson?.title ?? "章节学习"
  }
  var notesForCurrentSource: [StudyNote] {
    notes.filter {
      $0.anchor?.bookID == selectedBookID
        && ($0.anchor?.courseID == nil || $0.anchor?.courseID == selectedCourseID)
    }
  }
  var locationKey: String { "\(selectedCourseID ?? "")::\(selectedBookID ?? "")" }
  /// Reload the real server discovery catalog; only Debug tests inject a fixture.
  func refreshCommunity() async throws {
    if cloud != nil {
      try await refreshCloudCatalog()
      return
    }
    guard repository.includesFixtureContent else {
      community = []
      throw CloudError(status: 503, message: "社区课程需要连接服务；本机教材仍可使用。")
    }
    let refreshed = try ContentBundle.load([CommunityBook].self, name: "community")
    try Task.checkCancellation()
    community = refreshed
  }

  func boot() async {
    guard isLoading else { return }
    if cloud != nil {
      await bootCloud()
      return
    }
    let clock = ContinuousClock()
    let minimumSplashDeadline = clock.now.advanced(by: .milliseconds(500))
    var startupError: String?
    do {
      sources = try await repository.loadSources()
      community = repository.includesFixtureContent
        ? try ContentBundle.load([CommunityBook].self, name: "community") : []
      filePaths = try await repository.filePaths()
      shelf = sources.filter { sourceFileURL($0.id) != nil }.map {
        ShelfBook(bookId: $0.id, title: $0.summary.title,
                  filename: $0.summary.filename ?? $0.summary.title, coverUrl: $0.summary.cover_url ?? "")
      }
      if let snapshot = try await repository.loadSnapshot() {
        importJob = snapshot.importJob
        chapterDrafts = snapshot.chapterDrafts ?? [:]
        preferences = snapshot.preferences
        draft = snapshot.draft
        courses = snapshot.courses
        selectedCourseID = snapshot.selectedCourseID
        selectedBookID = snapshot.selectedBookID
        locations = snapshot.locations
        mastered = snapshot.mastery
        credits = snapshot.credits + snapshot.creditReservations.values.reduce(0, +)
        mistakes = snapshot.mistakes
        dismissedSourceIDs = snapshot.dismissedSourceIDs
        creditReservations = [:]
      }
      for source in sources
      where !source.id.hasPrefix("book_local_") && !dismissedSourceIDs.contains(source.id)
        && !courses.contains(where: { $0.resourceIDs.contains(source.id) })
      {
        courses.append(
          Course(
            id: "course:\(source.id)", name: source.summary.title, resourceIDs: [source.id],
            activeResourceID: source.id, createdAt: Date(timeIntervalSince1970: 1_785_638_400)))
      }
      if selectedCourseID == nil { selectedCourseID = courses.first?.id }
      if let id = selectedCourseID { selectCourse(id) }
      let ledger = try await repository.recoverCredits(startingBalance: credits)
      applyCredits(ledger)
      try await repository.seedDemoNotesIfNeeded()
      notes = try await repository.loadNotes()
      route = preferences?.hasCompletedOnboarding == true ? .home : .onboarding
      if importJob?.status == .running {
        importJob?.status = .interrupted
        importJob?.message = "上次整理中断，原文件已保留，请继续整理"
        importError = importJob?.message
        processingMessage = importJob?.message ?? "整理中断"
        if preferences?.hasCompletedOnboarding == true { route = .parseReady }
      }
      if repository.includesFixtureContent { seedMistakeIfNeeded() }
      let arguments = ProcessInfo.processInfo.arguments
      if repository.includesFixtureContent && ContentBundle.fixtureMode {
        preferences = LearnerPreferences(displayName: "小明同学", primaryGoal: nil, dailyTime: nil)
        if let i = arguments.firstIndex(of: "--book"), arguments.indices.contains(i + 1) {
          selectSource(arguments[i + 1])
        } else {
          selectSource("book_biology_2")
        }
        if let i = arguments.firstIndex(of: "--chapter"), arguments.indices.contains(i + 1) {
          selectedChapterID = arguments[i + 1]
          selectedLessonID = lessons.first(where: { $0.chapter_id == selectedChapterID })?.id
        }
        if let i = arguments.firstIndex(of: "--route"), arguments.indices.contains(i + 1),
          let target = Route(rawValue: arguments[i + 1])
        {
          route = target
        } else {
          route = .home
        }
        if [.courseSetup, .chapterConfirm, .courseReady, .courseImportReady].contains(route) {
          draft = CourseDraft(
            name: "生物复习", resourceIDs: ["book_biology_2"],
            diagnosis: [
              "urgency": ["steady"], "timePattern": ["block"], "goals": ["systematic"],
              "contentFoci": ["principles"], "aids": ["chat"], "reviews": ["periodic"],
            ], parseCompleted: true)
        }
        if route == .courseSetup,
          let i = arguments.firstIndex(of: "--fixture-step"), arguments.indices.contains(i + 1),
          let step = Int(arguments[i + 1])
        {
          draft.step = min(5, max(0, step))
        }
        if [.processing, .courseImportProcessing].contains(route) {
          processingProgress = 0.46
          processingMessage = "正在整理教材内容…"
        }
        if [.source, .voiceNote].contains(route) {
          sourceTarget = SourcePageTarget(
            bookID: selectedBookID ?? "book_biology_2", title: currentTitle, page: 11,
            printedPage: 16, from: .lesson)
        }
        if route == .diagnosis {
          makeSampleCourse()
          if let q = quizzes.last {
            submitAssignment(question: q, answer: "判断题：错误\n选择题：A\n简答题：我想再核对一下染色体的变化。")
          }
        }
        if route == .voiceNote {
          selectedChapterID = "c2s1"
          selectedLessonID = lessons.first(where: { $0.chapter_id == "c2s1" })?.id
          selectedQuote = "减数分裂过程中，染色体只复制一次，而细胞分裂两次。"
        }
        if route == .communityBook || route == .communityImport {
          selectedCommunityID = "community_genetics"
        }
        if let i = arguments.firstIndex(of: "--sheet"), arguments.indices.contains(i + 1) {
          sheet = SheetRoute(rawValue: arguments[i + 1])
          if sheet == .note {
            selectedQuote = "减数分裂\n理解一次复制与两次分裂的关系。"
            if let chunk = currentSource?.chunks.first(where: {
              $0.page_start <= 11 && $0.page_end >= 11
            }) {
              noteState.contextCitation = LessonCitation(
                chunk_id: chunk.id, page_start: 11, page_end: 11, printed_page_start: 16,
                quote: chunk.text)
            }
          }
          if sheet == .note || sheet == .noteType {
            sourceTarget = SourcePageTarget(
              bookID: selectedBookID ?? "book_biology_2", title: "减数分裂和受精作用", page: 11,
              printedPage: 16, from: route)
          }
        }
      }
      if repository.includesFixtureContent && arguments.contains("--visual-baseline"),
        !notes.contains(where: { $0.id == "baseline-note" })
      {
        let note = StudyNote(
          id: "baseline-note", kind: .text, title: "减数分裂学习笔记", body: "减数分裂过程中，染色体只复制一次，而细胞分裂两次。",
          anchor: NoteAnchor(
            bookID: "legacy-local", page: 11, printedPage: 16, quote: "染色体只复制一次，而细胞分裂两次。"),
          createdAt: Date(timeIntervalSince1970: 1_785_577_260),
          updatedAt: Date(timeIntervalSince1970: 1_785_577_260))
        try await repository.saveNote(note)
        notes = try await repository.loadNotes()
      }
      if arguments.contains("--assistant") { globalAssistantOpen = true }
      persist()
    } catch {
      startupError = error.localizedDescription
    }
    // Loading and the minimum display time overlap; slow launches incur no extra delay.
    if clock.now < minimumSplashDeadline {
      try? await clock.sleep(until: minimumSplashDeadline)
    }
    bootError = startupError
    isLoading = false
  }
  private func seedMistakeIfNeeded() {
    guard mistakes.isEmpty, let data = try? ContentBundle.data("Data/demo-state.json"),
      let seed = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let a = seed["assignment"] as? [String: Any], let d = seed["diagnosis"] as? [String: Any]
    else { return }
    mistakes = [
      MistakeRecord(
        mistake_id: "mistake_demo_01", user_id: "local_user", book_id: "book_biology_2",
        assignment_id: a["assignment_id"] as? String ?? "assignment_demo",
        question: a["question"] as? String ?? "", answer: a["initialAnswer"] as? String ?? "",
        stuck_point: d["stuck_point"] as? String ?? "",
        knowledge_points: d["knowledge_points"] as? [String] ?? [], citation_ids: ["chunk_c2s1_13"])
    ]
  }
  func go(_ next: Route) {
    guard route != next else { return }
    if next.isTab { path = [] } else { path.append(route) }
    route = next
    sheet = nil
    globalAssistantOpen = false
  }
  func replace(_ next: Route) {
    route = next
    sheet = nil
    globalAssistantOpen = false
  }
  func back() {
    if globalAssistantOpen {
      globalAssistantOpen = false
      return
    }
    if sheet != nil {
      sheet = nil
      return
    }
    if route == .processing {
      cancelImport()
      return
    }
    if route == .source {
      route = path.popLast() ?? sourceTarget?.from ?? .home
      if let origin = readerState.origin {
        readerState.origin = nil
        selectedCourseID = origin.courseID
        if let bookID = origin.bookID, sources.contains(where: { $0.id == bookID }) {
          if selectedBookID != bookID { selectSource(bookID) }
          selectedChapterID = origin.chapterID
          selectedLessonID = origin.lessonID
        } else if origin.bookID == nil {
          selectedBookID = nil
          selectedChapterID = nil
          selectedLessonID = nil
        }
        persist()
      }
    } else {
      route = path.popLast() ?? .home
    }
  }
  func showToast(_ message: String) {
    toastTask?.cancel()
    toast = message
    toastTask = Task {
      try? await Task.sleep(for: .seconds(2))
      if !Task.isCancelled { toast = nil }
    }
  }
  func openSheet(_ sheet: SheetRoute) {
    if sheet == .note, noteState.contextCitation == nil {
      noteState.contextCitation = activeLesson?.blocks.flatMap(\.citations).first
    }
    if sheet != .note { noteState.contextCitation = nil }
    self.sheet = sheet
  }
  func closeSheet() {
    sheet = nil
    noteState.contextCitation = nil
  }
  func selectSource(_ bookID: String) {
    guard sources.contains(where: { $0.id == bookID }) else {
      showToast("这份资料尚未导入")
      return
    }
    selectionGeneration += 1
    selectedBookID = bookID
    if sourceTarget?.bookID != bookID { sourceTarget = nil }
    if let i = courses.firstIndex(where: {
      $0.id == selectedCourseID && $0.resourceIDs.contains(bookID)
    }) {
      courses[i].activeResourceID = bookID
    } else if let course = courses.first(where: { $0.resourceIDs.contains(bookID) }) {
      selectedCourseID = course.id
    }
    let location = locations[locationKey]
    selectedChapterID =
      location?.chapterID ?? currentSource?.plan.tasks.first(where: {
        $0.status != "done" && $0.status != "completed"
      })?.chapter_id ?? chapters.first(where: { $0.level == 2 })?.id
    selectedLessonID =
      location?.lessonID ?? lessons.first(where: { $0.chapter_id == selectedChapterID })?.id
    chatMessages = []
    persist()
    if cloud != nil {
      loadSelectedCloudContent()
      cloudState.recordsTask?.cancel()
      cloudState.recordsTask = Task { await reloadCloudLearningRecords() }
    }
  }
  func selectCourse(_ courseID: String) {
    guard let course = courses.first(where: { $0.id == courseID }) else { return }
    selectedCourseID = courseID
    if cloud != nil { installCloudSources(workspaceID: courseID) }
    if let book = course.activeResourceID ?? course.resourceIDs.first {
      selectSource(book)
    } else {
      selectedBookID = nil
      selectedChapterID = nil
      selectedLessonID = nil
    }
    persist()
  }
  func openLesson(_ lesson: Lesson) {
    if cloud != nil, lesson.status == "pending" {
      Task { await beginCloudLearning(chapterID: lesson.chapter_id) }
      return
    }
    if selectedBookID != lesson.book_id { selectSource(lesson.book_id) }
    selectedChapterID = lesson.chapter_id
    selectedLessonID = lesson.id
    saveLocation()
    go(.lesson)
  }
  func openSource(_ target: SourcePageTarget) {
    if cloud != nil, sourceFileURL(target.bookID) == nil,
      sources.first(where: { $0.id == target.bookID })?.summary.content_mode != "catalog"
    {
      Task {
        do {
          try await ensureCloudPDF(bookID: target.bookID)
          openSource(target)
        } catch { handleCloudError(error) }
      }
      return
    }
    guard sources.contains(where: { $0.id == target.bookID }) else {
      showToast("这份资料尚未导入")
      return
    }
    if route != .source {
      readerState.origin = ReaderOrigin(
        courseID: selectedCourseID, bookID: selectedBookID,
        chapterID: selectedChapterID, lessonID: selectedLessonID)
    }
    if selectedBookID != target.bookID { selectSource(target.bookID) }
    var t = target
    if t.from == nil { t.from = route }
    sourceTarget = t
    go(.source)
  }
  func openCitation(_ citation: LessonCitation) {
    guard let bookID = selectedBookID else { showToast("请先选择教材"); return }
    guard citation.page_start > 0 else {
      showToast("此内容为 AI 补充，暂无原文页")
      return
    }
    openSource(
      SourcePageTarget(
        bookID: bookID,
        title: currentSource?.summary.title ?? currentTitle, page: citation.page_start,
        pageEnd: citation.page_end, printedPage: citation.printed_page_start,
        sourceText: citation.quote, from: route))
  }
  func openCitation(_ citation: Citation) {
    openSource(
      SourcePageTarget(
        bookID: citation.bookID, title: citation.chapterTitle, page: citation.page,
        printedPage: citation.printedPage, sourceText: citation.quote, from: route))
  }
  func completeOnboarding(
    name: String, goal: String?, dailyTime: String?, learningBarriers: [String] = [],
    guidanceStyles: [String] = [], planningSupport: String? = nil
  ) {
    guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    preferences = LearnerPreferences(
      displayName: name.trimmingCharacters(in: .whitespacesAndNewlines), primaryGoal: goal,
      dailyTime: dailyTime, learningBarriers: learningBarriers,
      guidanceStyles: guidanceStyles, planningSupport: planningSupport)
    persist()
    replace(.home)
  }
  func logout() {
    if cloud != nil {
      Task { await logoutCloud() }
      return
    }
    preferences = nil
    sheet = nil
    path = []
    persist()
    route = .onboarding
  }
  func createCourse(name: String, resourceIDs: [String], diagnosis: [String: [String]] = [:]) {
    let localOnly = resourceIDs.contains { $0.hasPrefix("book_local_") }
      || draft.editingCourseID?.hasPrefix("local-course:") == true
    if cloud != nil && !localOnly {
      Task {
        do {
          try await saveCloudCourse(name: name, resourceIDs: resourceIDs, diagnosis: diagnosis)
        } catch { handleCloudError(error) }
      }
      return
    }
    let validIDs = Array(NSOrderedSet(array: resourceIDs)) as? [String] ?? resourceIDs
    if let edit = draft.editingCourseID, let i = courses.firstIndex(where: { $0.id == edit }) {
      courses[i].name = name
      courses[i].resourceIDs = validIDs
      courses[i].diagnosis = diagnosis
      courses[i].updatedAt = .now
      selectedCourseID = edit
    } else {
      let course = Course(
        id: cloud != nil ? "local-course:" + UUID().uuidString : UUID().uuidString,
        name: name.isEmpty ? "我的课程" : name, resourceIDs: validIDs, activeResourceID: validIDs.first,
        diagnosis: diagnosis)
      courses.append(course)
      selectedCourseID = course.id
    }
    if let id = validIDs.first { selectSource(id) }
    draft = CourseDraft()
    persist()
  }
  func removeCourse(_ id: String) {
    if cloud != nil && !id.hasPrefix("local-course:") {
      Task { await removeCloudCourse(id) }
      return
    }
    guard let removed = courses.first(where: { $0.id == id }) else { return }
    dismissedSourceIDs.formUnion(removed.resourceIDs)
    courses.removeAll { $0.id == id }
    if selectedCourseID == id {
      selectedCourseID = courses.first?.id
      if let next = selectedCourseID { selectCourse(next) } else { selectedBookID = nil }
    }
    persist()
  }
  func addSource(_ bookID: String, to courseID: String?) async throws {
    let targetID = courseID ?? selectedCourseID
    if cloud != nil, targetID?.hasPrefix("local-course:") != true {
      guard !bookID.hasPrefix("book_local_") else {
        throw ContentError.invalid("原文件已保存在本机，请完成上传后再加入在线课程。")
      }
      try await addCloudResource(bookID: bookID, workspaceID: targetID)
      return
    }
    guard sources.contains(where: { $0.id == bookID }) else { throw ContentError.missing("教材资料") }
    if let id = targetID, let i = courses.firstIndex(where: { $0.id == id }) {
      if !courses[i].resourceIDs.contains(bookID) { courses[i].resourceIDs.append(bookID) }
      courses[i].updatedAt = .now
      persist()
      await persistTask?.value
      if let error = cloudState.cacheWriteError { throw ContentError.storage(error) }
    } else {
      createCourse(
        name: sources.first(where: { $0.id == bookID })?.summary.title ?? "新课程",
        resourceIDs: [bookID])
      await persistTask?.value
    }
  }

  func removeSource(_ bookID: String, from courseID: String) {
    if cloud != nil && !courseID.hasPrefix("local-course:") {
      Task { await removeCloudResource(bookID: bookID, workspaceID: courseID) }
      return
    }
    guard let i = courses.firstIndex(where: { $0.id == courseID }) else { return }
    courses[i].resourceIDs.removeAll { $0 == bookID }
    if courses[i].activeResourceID == bookID {
      courses[i].activeResourceID = courses[i].resourceIDs.first
    }
    if selectedCourseID == courseID { selectCourse(courseID) }
    persist()
  }
  func importCommunity(_ book: CommunityBook) {
    if cloud != nil {
      Task {
        do { try await importCloudCommunity(book) } catch { handleCloudError(error) }
      }
      return
    }
    guard repository.includesFixtureContent else {
      showToast("请连接社区服务后导入真实课程")
      return
    }
    let mapping = [
      "community_genetics": "book_biology_2",
      "community_high_school_mathematics_2": "catalog_high_school_math_required_2",
      "community_high_school_physics_3": "catalog_physics_required_3",
      "community_high_school_chemistry_2": "catalog_chemistry_required_2",
      "community_high_school_english_3": "catalog_english_required_3",
      "community_higher_mathematics": "catalog_advanced_mathematics_1",
    ]
    let source: SourceContent
    if let mapped = mapping[book.id], let existing = sources.first(where: { $0.id == mapped }) {
      source = existing
    } else if let existing = sources.first(where: { $0.id == book.id }) {
      source = existing
    } else {
      // Only the published community directory exists for these courses.
      // Keep that exact directory without inventing source pages or lesson bodies.
      let chapters = book.chapters.enumerated().map { i, title in
        Chapter(
          chapter_id: "\(book.id):chapter:\(i+1)", level: 1, source_title: title, ai_title: title,
          page_start: 0, page_end: 0, confidence: 100, status: "confirmed",
          source: "community-demo-directory")
      }
      let summary = SourceSummary(
        book_id: book.id, title: book.catalogTitle, cover_url: book.cover, status: "ready",
        page_count: 0, chapter_count: chapters.count, chunk_count: 0, asset_count: 0,
        average_confidence: 100, updated_at: Date().timeIntervalSince1970, content_mode: "directory"
      )
      source = SourceContent(
        summary: summary, chapters: chapters, lessons: [], chunks: [], assets: [], flashcards: [],
        quizzes: [],
        plan: StudyPlan(
          user_id: "local_user", book_id: book.id, days: max(1, chapters.count), daily_minutes: 25,
          tasks: []))
      sources.append(source)
    }
    let name = draft.name.isEmpty ? book.title : draft.name
    let diagnosis = draft.diagnosis
    createCourse(name: name, resourceIDs: [source.id], diagnosis: diagnosis)
    draft.catalogBookID = book.id
    draft.parseCompleted = true
    persist()
  }
  func importFile(
    _ url: URL, targetCourseID: String? = nil,
    onUploadProgress: @escaping @MainActor @Sendable (CloudUploadProgress) -> Void = { _ in }
  ) async {
    if cloud != nil {
      await importCloudPDF(url, targetCourseID: targetCourseID, onProgress: onUploadProgress)
      return
    }
    let requestGeneration = selectionGeneration
    importError = nil
    do {
      let result = try await LocalFileImporter.stage(url: url, repository: repository)
      guard requestGeneration == selectionGeneration, !Task.isCancelled else { return }
      if !sources.contains(where: { $0.id == result.source.id }) { sources.append(result.source) }
      filePaths[result.source.id] = result.relativePath
      if !draft.resourceIDs.contains(result.source.id) {
        draft.resourceIDs.append(result.source.id)
      }
      draft.parseCompleted = false
      if draft.name.isEmpty { draft.name = result.source.summary.title }
      selectedBookID = result.source.id
      selectedChapterID = nil
      selectedLessonID = nil
      importedFileURL = repository.attachmentURL(result.relativePath)
      try await repository.saveFilePaths(filePaths)
      try await repository.saveSources(sources)
      persist()
    } catch {
      guard !Task.isCancelled else { return }
      importError = error.localizedDescription
      showToast(error.localizedDescription)
    }
  }
  func processImport(retryPreparation: Bool = false) async {
    if cloud != nil {
      if let processTask { await processTask.value; return }
      let generation = UUID()
      processGeneration = generation
      let task = Task { @MainActor in await processCloudImport(retryPreparation: retryPreparation) }
      processTask = task
      if route == .parseReady { go(.processing) }
      await task.value
      if processGeneration == generation { processTask = nil }
      return
    }
    if ContentBundle.fixtureMode,
      !draft.resourceIDs.contains(where: { $0.hasPrefix("book_local_") })
    {
      return
    }
    if let processTask {
      await processTask.value
      return
    }
    let generation = UUID()
    processGeneration = generation
    let task = Task { @MainActor in
      processingProgress = 0
      importError = nil
      let ids = draft.resourceIDs
      let selectedGeneration = selectionGeneration
      importJob = ImportJob(bookIDs: ids, status: .running)
      persist()
      await flush()
      for (n, id) in ids.enumerated() {
        guard !Task.isCancelled else { return }
        guard let file = sourceFileURL(id), let index = sources.firstIndex(where: { $0.id == id }),
          !["ready", "source_ready"].contains(sources[index].summary.status)
        else { continue }
        processingMessage = "正在读取 \(sources[index].summary.filename ?? "资料")"
        do {
          let source = try await LocalFileImporter.extract(
            url: file, bookID: id,
            filename: sources[index].summary.filename ?? file.lastPathComponent)
          try Task.checkCancellation()
          if let i = sources.firstIndex(where: { $0.id == id }) { sources[i] = source }
          processingProgress = Double(n + 1) / Double(max(ids.count, 1))
          importJob?.progress = processingProgress
          importJob?.message = processingMessage
          try await repository.saveSources(sources)
          persist()
        } catch {
          if Task.isCancelled { return }
          importError = error.localizedDescription
          processingMessage = "整理中断，原文件已保留"
          importJob?.status = .failed
          importJob?.message = processingMessage
          persist()
          showToast(error.localizedDescription)
          return
        }
      }
      processingProgress = 1
      processingMessage = "资料整理完成"
      importJob?.status = .completed
      importJob?.progress = 1
      importJob?.message = processingMessage
      if draft.resourceIDs == ids { draft.parseCompleted = true }
      persist()
      if route == .processing, selectionGeneration == selectedGeneration { go(.courseSetup) }
    }
    processTask = task
    await task.value
    if processGeneration == generation { processTask = nil }
  }
  func cancelImport() {
    if let cloud {
      let books = importJob?.bookIDs ?? []
      Task {
        for bookID in books {
          do {
            let _: CloudOK = try await cloud.request(
              "/api/native/books/\(bookID)/cancel", method: "POST")
          } catch { self.handleCloudError(error) }
        }
      }
    }
    processTask?.cancel()
    processTask = nil
    processGeneration = UUID()
    replace(.parseReady)
    processingMessage = "整理已取消，原文件已保留"
    importError = "整理已取消，可以重新开始"
    importJob?.status = .cancelled
    importJob?.message = processingMessage
    persist()
  }
  func confirmChapters() {
    if cloud != nil {
      Task { await confirmCloudChapters() }
      return
    }
    if let book = selectedBookID, let chapters = chapterDrafts[book],
      let index = sources.firstIndex(where: { $0.id == book })
    {
      sources[index].chapters = chapters
      chapterDrafts.removeValue(forKey: book)
    }
    persist()
    if draft.editingCourseID != nil || !draft.resourceIDs.isEmpty {
      createCourse(name: draft.name, resourceIDs: draft.resourceIDs, diagnosis: draft.diagnosis)
    }
    go(.courseReady)
  }
  func updateChapter(_ chapter: Chapter) {
    guard let book = selectedBookID, var value = chapterDrafts[book] ?? currentSource?.chapters,
      let index = value.firstIndex(where: { $0.id == chapter.id })
    else { return }
    value[index] = chapter
    chapterDrafts[book] = value
    persist()
  }
  func deleteChapter(_ chapterID: String) {
    guard let book = selectedBookID, var value = chapterDrafts[book] ?? currentSource?.chapters
    else { return }
    var ids: Set<String> = [chapterID]
    var changed = true
    while changed {
      let before = ids.count
      for chapter in value where chapter.parent_id.map(ids.contains) ?? false {
        ids.insert(chapter.id)
      }
      changed = ids.count != before
    }
    value.removeAll { ids.contains($0.id) }
    chapterDrafts[book] = value
    persist()
  }
  func updateTask(_ taskID: String, status: String) {
    for i in sources.indices {
      if let j = sources[i].plan.tasks.firstIndex(where: { $0.id == taskID }) {
        sources[i].plan.tasks[j].status = status
      }
    }
    persist()
  }
  func completeLesson() {
    guard let lesson = activeLesson, lesson.status == "ready" else { return }
    for i in sources.indices where sources[i].id == lesson.book_id {
      for j in sources[i].plan.tasks.indices where sources[i].plan.tasks[j].lesson_id == lesson.id {
        sources[i].plan.tasks[j].status = "done"
      }
    }
    if let resource = cloudResource, resource.book_id == lesson.book_id {
      var pending = cloudState.pending[resource.id] ?? CloudPendingProgress(
        workspaceID: resource.workspace_id, resourceID: resource.id, value: resource.progress,
        baseMasteredIDs: resource.progress.mastered_ids)
      if !pending.value.completed_chapters.contains(lesson.chapter_id) {
        pending.value.completed_chapters.append(lesson.chapter_id)
        pending.value.completed_chapters.sort()
        // A completion made during an in-flight write must survive that write's acknowledgement.
        pending.mutationID = UUID().uuidString
        cloudState.pending[resource.id] = pending
      }
    }
    saveLocation()
    showToast("本课学习已完成")
  }
  func saveLocation(page: Int? = nil) {
    var location = locations[locationKey] ?? StudyLocation()
    location.chapterID = selectedChapterID
    location.lessonID = selectedLessonID
    if let page { location.page = page }
    locations[locationKey] = location
    persist()
  }
  func setMastery(_ questionID: String, mastered: Bool) {
    let key = masteryKey(questionID)
    if mastered { self.mastered.insert(key) } else { self.mastered.remove(key) }
    if cloud != nil, let resource = cloudResource {
      var pending =
        cloudState.pending[resource.id]
        ?? CloudPendingProgress(
          workspaceID: resource.workspace_id, resourceID: resource.id, value: resource.progress,
          baseMasteredIDs: resource.progress.mastered_ids)
      pending.recordMastery(resource.book_id + "::" + questionID, mastered: mastered)
      cloudState.pending[resource.id] = pending
    }
    persist()
  }
  func isMastered(_ questionID: String) -> Bool {
    mastered.contains(masteryKey(questionID))
  }
  func masteryKey(_ questionID: String) -> String {
    "\(cloud != nil ? cloudResource?.id ?? "unselected" : selectedBookID ?? "")::\(questionID)"
  }
  func submitAssignment(question: QuizQuestion, answer: String) {
    if cloud != nil {
      Task { await submitCloudAssignment(question: question, answer: answer) }
      return
    }
    assignmentAnswer = answer
    let section =
      currentSource?.quizzes.filter { $0.chapter_id == question.chapter_id } ?? [question]
    let grouped = answer.contains("判断题：") || answer.contains("选择题：")
    func submitted(_ prefix: String) -> String {
      answer.components(separatedBy: "\n").first(where: { $0.hasPrefix(prefix) }).map {
        String($0.dropFirst(prefix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
      } ?? "未作答"
    }
    var checks: [(QuizQuestion, Bool)] = []
    if grouped {
      if let judgment = section.first(where: { $0.question_type == "judgment" }) {
        checks.append((judgment, submitted("判断题：") == judgment.answer))
      }
      if let choice = section.first(where: { $0.question_type == "choice" }) {
        let key = submitted("选择题：").first
        let letters = Array("ABCD")
        let index = key.flatMap { letters.firstIndex(of: $0) }
        let selected = index.flatMap {
          choice.choices.indices.contains($0) ? choice.choices[$0] : nil
        }
        checks.append((choice, selected == choice.answer))
      }
    } else {
      checks = [
        (question, answer.trimmingCharacters(in: .whitespacesAndNewlines) == question.answer)
      ]
    }
    let wrong = checks.filter { !$0.1 }
    let correct = wrong.isEmpty
    let focus =
      wrong.first?.0 ?? section.first(where: { $0.question_type == "short-answer" }) ?? question
    let grounded = ([focus] + section).first {
      $0.source_kind != "ai_supplement" && $0.page_start > 0 && !$0.source_chunk_ids.isEmpty
    }
    let citations =
      grounded.map { q in
        [
          Citation(
            bookID: q.book_id, chapterID: q.chapter_id,
            chapterTitle: chapters.first(where: { $0.id == q.chapter_id })?.source_title
              ?? chapterTitle, page: q.page_start, printedPage: q.printed_page_start,
            chunkID: q.source_chunk_ids[0], quote: q.source_quote ?? q.explanation, score: 0.93)
        ]
      } ?? []
    let result =
      grouped
      ? "判断题和选择题共 \(checks.count) 题，已答对 \(checks.count-wrong.count) 题。"
        + (correct ? "简答题已按本节核心要点生成复习建议。" : "建议优先复习“\(focus.concept)”。")
      : correct ? "这道题已答对，继续巩固本节核心要点。" : "建议优先复习“\(focus.concept)”。"
    let hint =
      correct && grouped ? "简答题参考要点：" + focus.answer : focus.explanation + " 正确答案：" + focus.answer
    latestDiagnosis = Diagnosis(
      assignment_id: question.id, submission_id: UUID().uuidString, result: result,
      stuck_point: correct
        ? "客观题已通过，接下来需要用完整因果链表达“\(focus.concept)”。" : "客观题暴露出对“\(focus.concept)”的理解偏差。",
      knowledge_points: section.map(\.concept).reduce(into: [String]()) { values, value in
        if !values.contains(value) { values.append(value) }
      }, review_citations: citations, hint: hint, needs_followup: false, mistake_recorded: !correct)
    diagnosisText = result
    for (q, _) in wrong {
      let record = MistakeRecord(
        mistake_id: "mistake_" + q.id, user_id: "local_user", book_id: q.book_id,
        assignment_id: q.id, question: q.prompt, answer: answer, stuck_point: q.concept,
        knowledge_points: [q.concept], citation_ids: q.source_chunk_ids, correct_answer: q.answer,
        explanation: q.explanation, mastery: "due", error_count: 1)
      if let i = mistakes.firstIndex(where: { $0.id == record.id }) {
        var update = record
        update.error_count = (mistakes[i].error_count ?? 1) + 1
        mistakes[i] = update
      } else {
        mistakes.append(record)
      }
    }
    persist()
    go(.diagnosis)
  }
  func makeAnchor(page: Int? = nil) -> NoteAnchor {
    NoteAnchor(
      courseID: selectedCourseID, bookID: selectedBookID ?? "",
      chapterID: selectedChapterID,
      page: page ?? (sourceTarget?.bookID == selectedBookID ? sourceTarget?.page : nil)
        ?? activeLesson?.page_start ?? 1,
      printedPage: sourceTarget?.bookID == selectedBookID ? sourceTarget?.printedPage : nil,
      quote: selectedQuote.isEmpty ? nil : selectedQuote)
  }
  func saveNote(_ note: StudyNote) async throws {
    var updated = note
    if updated.anchor?.bookID.isEmpty == true { updated.anchor = nil }
    updated.updatedAt = .now
    if cloud != nil {
      if let index = notes.firstIndex(where: { $0.id == updated.id }) {
        notes[index] = updated
      } else {
        notes.append(updated)
      }
      guard let saved = persistCloud() else {
        throw CloudError(status: 409, message: "笔记尚未保存，请重新打开课程后重试")
      }
      try await saved.value.get()
      return
    }
    try await repository.saveNote(updated)
    await reloadNotes()
  }
  func deleteNote(_ id: String) async {
    if cloud != nil {
      notes.removeAll { $0.id == id }
      persistCloud()
      return
    }
    do {
      try await repository.deleteNote(id)
      await reloadNotes()
    } catch { showToast(error.localizedDescription) }
  }
  func reloadNotes() async {
    if cloud != nil { return }
    do { notes = try await repository.loadNotes() } catch { showToast(error.localizedDescription) }
  }
  func sourceFileURL(_ bookID: String) -> URL? {
    guard let relative = filePaths[bookID] else { return nil }
    let url = repository.attachmentURL(relative)
    return FileManager.default.fileExists(atPath: url.path) ? url : nil
  }
  func attachmentURL(_ relative: String) -> URL { repository.attachmentURL(relative) }
  func saveAttachment(_ data: Data, extension ext: String) async throws -> String {
    try await repository.saveAttachment(data, extension: ext)
  }
  private func applyCredits(_ ledger: CreditLedger) {
    guard ledger.revision >= creditRevision else { return }
    creditRevision = ledger.revision
    credits = ledger.balance
    creditReservations = ledger.reservations
  }
  func reserveCredits(_ amount: Int) async throws -> String {
    let (id, ledger) = try await repository.reserveCredits(amount)
    applyCredits(ledger)
    return id
  }
  func completeCredits(_ id: String) async {
    do { applyCredits(try await repository.finishCredits(id, refund: false)) } catch {
      showToast(error.localizedDescription)
    }
  }
  func refundCredits(_ id: String) async {
    do { applyCredits(try await repository.finishCredits(id, refund: true)) } catch {
      showToast(error.localizedDescription)
    }
  }
  func ask(_ question: String) async {
    if cloud != nil && !(selectedBookID?.hasPrefix("book_local_") ?? false) {
      await askCloud(question)
      return
    }
    guard !chatLoading, !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      return
    }
    chatLoading = true
    let generation = selectionGeneration
    let scope = currentCourse?.resourceIDs ?? [selectedBookID].compactMap { $0 }
    let book = selectedBookID ?? ""
    do {
      chatMessages.append(ChatMessage(role: "user", text: question))
      let result = try await assistant.ask(
        question: question, bookID: book, bookIDs: scope, chapterID: selectedChapterID,
        sources: sources, history: Array(chatMessages.suffix(8)))
      guard generation == selectionGeneration, !Task.isCancelled else {
        chatLoading = false
        return
      }
      chatMessages.append(
        ChatMessage(role: "assistant", text: result.answer, citations: result.citations))
    } catch {
      showToast(error.localizedDescription)
    }
    chatLoading = false
  }
  func snapshot() -> AppSnapshot {
    AppSnapshot(
      importJob: importJob, chapterDrafts: chapterDrafts, preferences: preferences, draft: draft,
      courses: courses,
      selectedCourseID: selectedCourseID, selectedBookID: selectedBookID, locations: locations,
      mastery: mastered, credits: credits, creditReservations: creditReservations,
      mistakes: mistakes, dismissedSourceIDs: dismissedSourceIDs)
  }
  func persist() {
    if cloud != nil {
      persistCloud()
      return
    }
    let snapshot = snapshot()
    let sourceSnapshot = sources
    let previous = persistTask
    persistTask = Task { [repository] in
      await previous?.value
      do {
        try await repository.saveSnapshot(snapshot)
        try await repository.saveSources(sourceSnapshot)
      } catch { self.showToast(error.localizedDescription) }
    }
  }
  func flush() async {
    await persistTask?.value
    if cloud != nil {
      await cloudState.recordsTask?.value
      while let task = cloudState.syncTask { await task.value }
      await persistTask?.value
    }
  }
  func makeSampleCourse() {
    guard repository.includesFixtureContent else { return }
    if let c = courses.first(where: { $0.resourceIDs.contains("book_biology_2") }) {
      selectCourse(c.id)
    }
    selectedChapterID = "c2s1"
    selectedLessonID = "lesson_meiosis"
  }
}
