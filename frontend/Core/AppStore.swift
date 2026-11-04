import CryptoKit
import Foundation
import Observation
import SwiftUI
import UIKit

@MainActor @Observable final class AppStore {
  var route: Route = .onboarding
  var path: [Route] = []
  var sheet: SheetRoute?
  var sharingBookID: String?
  var toast: ToastNotice?
  var isPad = false
  var bootError: String?
  var isLoading = true
  let courseState = CourseState()
  let uploadState = CourseUploadState()
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
  let dailyPlanState = DailyStudyPlanState()
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
  let assistantHistory: AssistantHistoryStore
  @ObservationIgnored var filePaths: [String: String] = [:]
  @ObservationIgnored var persistTask: Task<Void, Never>?
  @ObservationIgnored var processTask: Task<Void, Never>?
  @ObservationIgnored private var processGeneration = UUID()
  @ObservationIgnored private var toastTask: Task<Void, Never>?
  @ObservationIgnored private var creditReservations: [String: Int] = [:]
  @ObservationIgnored private var creditRevision = 0
  @ObservationIgnored var selectionGeneration = 0
  @ObservationIgnored let assistant: AssistantService
  @ObservationIgnored let agent: AgentRuntime
  let processingPolicy: ProcessingNetworkPolicy
  @ObservationIgnored let appleTextbookSearch: AppleTextbookSearch
  var activeImportRoute: ProcessingRoute?
  @ObservationIgnored var cloud: CloudAPI?
  let cloudState = CloudState()
  init(repository: LocalBookCourseRepository, cloud: CloudAPI? = nil,
       processingPolicy: ProcessingNetworkPolicy? = nil,
       appleTextbookSearch: AppleTextbookSearch? = nil) {
    self.repository = repository
    self.assistantHistory = AssistantHistoryStore(repository: repository)
    self.cloud = cloud
    self.assistant = AssistantService()
    self.processingPolicy = processingPolicy ?? ProcessingNetworkPolicy()
    self.appleTextbookSearch = appleTextbookSearch ?? AppleTextbookSearch()
    self.agent = AgentRuntime()
    self.agent.store = self
    cloudState.legacyPrivateSyncEnabled = cloud?.legacyPrivateSyncEnabled ?? false
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
    let cards = s.flashcards + agentFlashcards
    let scoped = cards.filter { $0.chapter_id == selectedChapterID }
    return selectedChapterID == nil ? cards : scoped
  }
  var quizzes: [QuizQuestion] {
    guard let s = currentSource else { return [] }
    return selectedChapterID == nil
      ? s.quizzes : s.quizzes.filter { $0.chapter_id == selectedChapterID }
  }
  var plan: StudyPlan? { currentSource?.plan }
  /// Explicit reading completion is independent of a daily plan or knowledge mastery.
  var completedChapterIDs: Set<String> {
    completedChapterIDs(for: selectedBookID ?? "")
  }
  func completedChapterIDs(for bookID: String) -> Set<String> {
    if let resource = cloudState.workspaces.first(where: { $0.id == selectedCourseID })?
      .resources.first(where: { $0.book_id == bookID }) {
      return Set((cloudState.pending[resource.id]?.value ?? resource.progress).completed_chapters)
    }
    let key = "\(selectedCourseID ?? "")::\(bookID)"
    let tasks = sources.first { $0.id == bookID }?.plan.tasks ?? []
    return (locations[key]?.completedChapterIDs ?? []).union(
      tasks.filter { ["done", "completed"].contains($0.status) }.compactMap(\.chapter_id))
  }
  var currentTitle: String { currentCourse?.name ?? currentSource?.summary.title ?? "我的课程" }
  var chapterTitle: String {
    chapters.first { $0.id == selectedChapterID }?.title ?? activeLesson?.title ?? "章节学习"
  }
  var notesForCurrentSource: [StudyNote] {
    guard let resourceID = courseResourceID(courseID: selectedCourseID, bookID: selectedBookID ?? "") else { return [] }
    return notes.filter { $0.anchor?.courseResourceID == resourceID }
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
        importState.courses = snapshot.courseImports ?? []
        for i in importState.courses.indices { importState.courses[i].mergeDuplicateBooks() }
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
      _ = try await repository.loadNotes()
      await reloadNotes()
      if importState.courses.isEmpty, importJob != nil { _ = registerCourseImport() }
      retainPendingCourseDraft()
      for i in importState.courses.indices where importState.courses[i].job.status == .running {
        importState.courses[i].job.status = .interrupted
      }
      route = preferences?.hasCompletedOnboarding == true ? .study : .onboarding
      if importJob?.status == .running {
        importJob?.status = .interrupted
        importJob?.message = "上次整理中断，原文件已保留，请继续整理"
        importError = importJob?.message
        processingMessage = importJob?.message ?? "整理中断"
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
          var location = locations[locationKey] ?? StudyLocation()
          location.chapterID = selectedChapterID
          location.lessonID = selectedLessonID
          locations[locationKey] = location
        }
        if let i = arguments.firstIndex(of: "--route"), arguments.indices.contains(i + 1),
          let target = Route(rawValue: arguments[i + 1])
        {
          route = target == .home ? .study : target
        } else {
          route = .study
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
        #if DEBUG
        // Legacy display fixtures are confined to the isolated --fixture store.
        // Silence is a decodable attachment, never a microphone/ASR success claim.
        if let i = arguments.firstIndex(of: "--fixture-voice-note"), arguments.indices.contains(i + 1),
          ["raw_only", "organized"].contains(arguments[i + 1]) {
          let scenario = arguments[i + 1]
          let id = "fixture-legacy-voice-" + scenario
          if !notes.contains(where: { $0.id == id }) {
            let audio = NotesAudioController()
            audio.makeSample()
            guard let original = audio.audioURL else {
              throw ContentError.storage("语音显示测试附件未能创建")
            }
            defer {
              audio.shutdown()
              try? FileManager.default.removeItem(at: original)
            }
            let attachment = try await repository.saveAttachment(Data(contentsOf: original), extension: "wav")
            let note = StudyNote(
              id: id, kind: .voice, title: "兼容测试：旧语音笔记", body: "",
              anchor: NoteAnchor(courseID: selectedCourseID, bookID: selectedBookID ?? "book_biology_2",
                chapterID: "c2s1", page: 11, printedPage: 16),
              audioPath: attachment, duration: 8,
              transcript: "LEGACY_VOICE_RAW_PRIVATE_20261027",
              organizedText: scenario == "organized"
                ? "整理后的语音笔记：遗传物质复制一次，细胞连续分裂两次。" : nil)
            try await repository.saveNote(note)
            notes = try await repository.loadNotes()
          }
          if route == .voiceNote { NotesCaptureContext.existingNoteID = id }
        }
        #endif
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
  func go(_ destination: Route) {
    let next: Route = destination == .home ? .study : destination
    if next.isTab, [.upload, .parseReady, .processing, .courseSetup, .chapterConfirm].contains(route) {
      retainPendingCourseDraft()
      if next == .study, let id = draft.importCourseID { selectCourse(id) }
    }
    guard route != next else { return }
    if next.isTab { path = [] } else { path.append(route) }
    route = next
    sheet = nil
    globalAssistantOpen = false
  }
  func replace(_ destination: Route) {
    let next: Route = destination == .home ? .study : destination
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
    let leavingCreation = [.upload, .parseReady, .courseSetup, .chapterConfirm].contains(route)
    if leavingCreation {
      retainPendingCourseDraft()
    }
    if route == .source {
      route = path.popLast() ?? sourceTarget?.from ?? .study
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
      route = path.popLast() ?? .study
    }
    if route == .home { route = .study }
    if leavingCreation, route == .study, let id = draft.importCourseID,
      courseImport(for: id)?.setupCompleted != true { selectCourse(id) }
  }
  func showToast(
    _ message: String, kind: ToastNotice.Kind = .info, subtitle: String? = nil
  ) {
    toastTask?.cancel()
    toastTask = nil
    let notice = ToastNotice(title: message, subtitle: subtitle, kind: kind)
    withAnimation(.easeOut(duration: ToastNotice.Timing.appearance)) { toast = notice }
    UIAccessibility.post(notification: .announcement, argument: notice.announcement)
    guard kind != .loading else { return }
    toastTask = Task {
      do {
        try await Task.sleep(for: .seconds(ToastNotice.Timing.appearance + ToastNotice.Timing.hold))
      } catch { return }
      dismissToast(id: notice.id)
    }
  }
  func dismissToast(id: UUID? = nil) {
    guard let notice = toast, id == nil || id == notice.id else { return }
    toastTask?.cancel()
    toastTask = nil
    withAnimation(.easeIn(duration: ToastNotice.Timing.disappearance)) { toast = nil }
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
      showToast("这份资料尚未导入", kind: .warning)
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
    captureCourseImportDraft()
    captureLocalCourseContent()
    selectedCourseID = courseID
    if cloudState.privateLocalMode { installLocalCourseContent(courseID) }
    else if cloud != nil { installCloudSources(workspaceID: courseID) }
    if let book = course.activeResourceID ?? course.resourceIDs.first {
      if sources.contains(where: { $0.id == book }) { selectSource(book) }
      else {
        selectionGeneration += 1
        selectedBookID = book
        selectedChapterID = nil
        selectedLessonID = nil
      }
    } else {
      selectedBookID = nil
      selectedChapterID = nil
      selectedLessonID = nil
    }
    persist()
  }
  func openLesson(_ lesson: Lesson) {
    if cloud != nil, lesson.status == "pending" {
      requestCloudLearning(chapterID: lesson.chapter_id)
      return
    }
    if selectedBookID != lesson.book_id { selectSource(lesson.book_id) }
    selectedChapterID = lesson.chapter_id
    selectedLessonID = lesson.id
    saveLocation()
    go(.lesson)
  }
  func openSource(_ target: SourcePageTarget, allowDownload: Bool = true) {
    var target = target
    if target.courseID == nil {
      let courseRoutes: Set<Route> = [.study, .lesson, .courseDetail, .notes, .voiceNote, .flashcards, .plan, .assignment, .diagnosis, .mistakes]
      if route == .source, sourceTarget?.bookID == target.bookID {
        target.courseID = sourceTarget?.courseID
      } else if courseRoutes.contains(route), currentCourse?.resourceIDs.contains(target.bookID) == true {
        target.courseID = selectedCourseID
      }
    }
    target.courseResourceID = courseResourceID(courseID: target.courseID, bookID: target.bookID)
    guard let source = sources.first(where: { $0.id == target.bookID }) else {
      showToast("这份资料尚未导入", kind: .warning)
      return
    }
    let localOnly = target.bookID.hasPrefix("book_local_")
    if allowDownload, cloud != nil, !cloudState.privateLocalMode, !localOnly, sourceFileURL(target.bookID) == nil,
      source.summary.content_mode != "catalog"
    {
      let epoch = cloudState.epoch, generation = selectionGeneration
      let book = selectedBookID, course = selectedCourseID, originRoute = route
      Task {
        do {
          let choice = await processingPolicy.route(for: .retrieval)
          try Task.checkCancellation()
          guard epoch == cloudState.epoch, generation == selectionGeneration,
            book == selectedBookID, course == selectedCourseID, route == originRoute else { return }
          switch choice {
          case .cancelled: return
          case .onDevice: openSource(target, allowDownload: false)
          case .existing:
            try await ensureCloudPDF(bookID: target.bookID)
            try Task.checkCancellation()
            guard epoch == cloudState.epoch, generation == selectionGeneration,
              book == selectedBookID, course == selectedCourseID, route == originRoute else { return }
            openSource(target)
          }
        } catch is CancellationError {
          return
        } catch {
          guard epoch == cloudState.epoch, generation == selectionGeneration else { return }
          handleCloudError(error)
        }
      }
      return
    }
    if route != .source {
      readerState.origin = ReaderOrigin(
        courseID: selectedCourseID, bookID: selectedBookID,
        chapterID: selectedChapterID, lessonID: selectedLessonID)
    }
    if let courseID = target.courseID, selectedCourseID != courseID { selectCourse(courseID) }
    if selectedBookID != target.bookID {
      if allowDownload && !localOnly { selectSource(target.bookID) }
      else {
        // A cached-text selection must not hydrate cloud content, fetch learning
        // records, or enqueue cloud selection writes through selectSource().
        selectionGeneration += 1
        selectedBookID = target.bookID
        if sourceTarget?.bookID != target.bookID { sourceTarget = nil }
        if let index = courses.firstIndex(where: {
          $0.id == selectedCourseID && $0.resourceIDs.contains(target.bookID)
        }) {
          courses[index].activeResourceID = target.bookID
        } else if let course = courses.first(where: { $0.resourceIDs.contains(target.bookID) }) {
          selectedCourseID = course.id
        }
        let location = locations[locationKey]
        selectedChapterID = location?.chapterID ?? source.plan.tasks.first(where: {
          $0.status != "done" && $0.status != "completed"
        })?.chapter_id ?? source.chapters.first(where: { $0.level == 2 })?.id
        selectedLessonID = location?.lessonID
          ?? source.lessons.first(where: { $0.chapter_id == selectedChapterID })?.id
        chatMessages = []
        if cloud != nil { saveCloudCacheOnly() } else { persist() }
      }
    }
    var t = target
    if t.courseResourceID == nil { t.courseResourceID = courseResourceID(courseID: t.courseID, bookID: target.bookID) }
    if t.from == nil { t.from = route }
    sourceTarget = t
    go(.source)
  }
  func openCitation(_ citation: LessonCitation) {
    guard let bookID = selectedBookID else { showToast("请先选择教材", kind: .warning); return }
    guard citation.page_start > 0 else { return }
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
  /// An unfinished import is available on demand; it never overrides the startup route.
  var resumableCourseRoute: Route? {
    guard !draft.resourceIDs.isEmpty else { return nil }
    if let job = importJob, [.running, .interrupted, .failed].contains(job.status) {
      return .parseReady
    }
    if draft.catalogBookID != nil { return .courseSetup }
    if cloud != nil && cloudState.creationWorkspaceID != nil { return .chapterConfirm }
    return draft.parseCompleted ? .courseSetup : .parseReady
  }

  func completeOnboarding(
    name: String, goal: String?, dailyTime: String?, learningBarriers: [String] = [],
    guidanceStyles: [String] = [], planningSupport: String? = nil, learningStage: String? = nil
  ) {
    guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    preferences = LearnerPreferences(
      displayName: name.trimmingCharacters(in: .whitespacesAndNewlines), primaryGoal: goal,
      dailyTime: dailyTime, learningBarriers: learningBarriers,
      guidanceStyles: guidanceStyles, planningSupport: planningSupport, learningStage: learningStage)
    persist()
    replace(.study)
  }
  func logout() {
    if cloud != nil {
      Task { await logoutCloud() }
      return
    }
    preferences = nil
    LearnerOnboardingDraft.clear()
    sheet = nil
    path = []
    persist()
    route = .onboarding
  }
  func createCourse(name: String, resourceIDs: [String], diagnosis: [String: [String]] = [:]) {
    if cloud != nil && !cloudState.privateLocalMode { showToast("本机个人存储尚未就绪，请重试连接", kind: .warning); return }
    let localOnly = resourceIDs.contains { $0.hasPrefix("book_local_") }
      || draft.editingCourseID?.hasPrefix("local-course:") == true
    if cloud != nil && !localOnly && !cloudState.privateLocalMode {
      Task {
        do {
          try await saveCloudCourse(name: name, resourceIDs: resourceIDs, diagnosis: diagnosis)
        } catch { handleCloudError(error) }
      }
      return
    }
    let validIDs = Array(NSOrderedSet(array: resourceIDs)) as? [String] ?? resourceIDs
    if let edit = draft.editingCourseID ?? draft.importCourseID, let i = courses.firstIndex(where: { $0.id == edit }) {
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
    finishCourseImportSetup()
    draft = CourseDraft()
    persist()
  }
  func removeCourse(_ id: String) {
    if cloud != nil && !cloudState.privateLocalMode && !id.hasPrefix("local-course:") {
      Task { await removeCloudCourse(id) }
      return
    }
    guard let removed = courses.first(where: { $0.id == id }) else { return }
    if let record = courseImport(for: id), record.needsPreparation, record.job.status != .cancelled, cloud != nil {
      let epoch = cloudState.epoch
      Task {
        await pauseCourseImport(record.id)
        guard epoch == self.cloudState.epoch,
          self.importState.courses.first(where: { $0.id == record.id })?.job.status == .cancelled else { return }
        self.removeCourse(id)
      }
      return
    }
    if let record = courseImport(for: id) {
      importState.tasks[record.id]?.cancel()
      importState.tasks[record.id] = nil
      importState.courses.removeAll { $0.id == record.id }
    }
    let resourceIDs = Set(removed.resourceIDs.compactMap { courseResourceID(courseID: id, bookID: $0) })
    Task { @MainActor in
      do {
        try await clearLocalCourseData(id, resourceIDs: resourceIDs)
        dismissedSourceIDs.formUnion(removed.resourceIDs.filter { bookID in
          !courses.contains(where: { $0.id != id && $0.resourceIDs.contains(bookID) })
        })
        courses.removeAll { $0.id == id }
        if selectedCourseID == id {
          selectedCourseID = courses.first?.id
          if let next = selectedCourseID { selectCourse(next) } else { selectedBookID = nil }
        }
        persist()
        showToast("课程及关联学习数据已移除", kind: .success)
      } catch { showToast(error.localizedDescription, kind: .error) }
    }
  }

  func addSource(_ bookID: String, to courseID: String?) async throws {
    let targetID = courseID ?? selectedCourseID
    if cloud != nil, !cloudState.privateLocalMode, targetID?.hasPrefix("local-course:") != true {
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
    if cloud != nil && !cloudState.privateLocalMode && !courseID.hasPrefix("local-course:") {
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
      showToast("请连接社区服务后导入真实课程", kind: .warning)
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
      if let targetCourseID { draft.importCourseID = targetCourseID }
      _ = registerCourseImport()
      persist()
    } catch {
      guard !Task.isCancelled else { return }
      importError = error.localizedDescription
      showToast(error.localizedDescription, kind: .error)
    }
  }
  func processImport(retryPreparation: Bool = false) async {
    if cloud == nil, ContentBundle.fixtureMode,
      !draft.resourceIDs.contains(where: { $0.hasPrefix("book_local_") }) { return }
    guard let id = registerCourseImport() else { return }
    importState.legacyCourseID = id
    if route == .parseReady { go(.processing) }
    await startCourseImport(id, retryPreparation: retryPreparation)
  }
  func cancelImport() {
    if let id = importState.legacyCourseID {
      Task { await pauseCourseImport(id) }
    } else if let cloud {
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
    if let id = importState.legacyCourseID {
      updateCourseImport(id) { $0.job.status = .cancelled; $0.error = "整理已取消，原文件已保留" }
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
    if cloud != nil && !cloudState.privateLocalMode {
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
    go(.study)
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
    guard let lesson = activeLesson, ["ready", "generated"].contains(lesson.status) else { return }
    for i in sources.indices where sources[i].id == lesson.book_id {
      for j in sources[i].plan.tasks.indices where sources[i].plan.tasks[j].lesson_id == lesson.id {
        sources[i].plan.tasks[j].status = "done"
      }
    }
    if !cloudState.privateLocalMode, let resource = cloudResource, resource.book_id == lesson.book_id {
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
    } else {
      var location = locations[locationKey] ?? StudyLocation()
      var completed = location.completedChapterIDs ?? []
      completed.insert(lesson.chapter_id)
      location.completedChapterIDs = completed
      locations[locationKey] = location
    }
    saveLocation()
    let workspaceID = selectedCourseID, epoch = cloudState.epoch
    let planFocus = activeDailyStudyTask
    Task { await finishLessonDailyStudyTasks(lesson, workspaceID: workspaceID, epoch: epoch, focus: planFocus) }
    showToast("本课学习已完成", kind: .success)
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
    if cloud != nil, !cloudState.privateLocalMode, let resource = cloudResource {
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
    "\(cloudState.privateLocalMode ? locationKey : cloud != nil ? cloudResource?.id ?? "unselected" : selectedBookID ?? "")::\(questionID)"
  }
  func submitAssignment(question: QuizQuestion, answer: String) {
    if cloudState.privateLocalMode {
      Task { do { try await submitLocalPractice(question: question, answer: answer) } catch { handleCloudError(error) } }
      return
    }
    if cloud != nil && !cloudState.privateLocalMode {
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
    let courseID = [.source, .voiceNote].contains(route) && sourceTarget?.bookID == selectedBookID
      ? sourceTarget?.courseID : selectedCourseID
    return NoteAnchor(
      courseID: courseID, courseResourceID: courseResourceID(courseID: courseID, bookID: selectedBookID ?? ""), bookID: selectedBookID ?? "",
      chapterID: selectedChapterID,
      page: page ?? (sourceTarget?.bookID == selectedBookID ? sourceTarget?.page : nil)
        ?? activeLesson?.page_start ?? 1,
      printedPage: sourceTarget?.bookID == selectedBookID ? sourceTarget?.printedPage : nil,
      quote: selectedQuote.isEmpty ? nil : selectedQuote)
  }
  /// Signed-in notes belong to the account cache key; otherwise the local scope.
  var noteScope: String? {
    if cloud == nil { return LocalBookCourseRepository.localNoteScope }
    guard cloudState.authenticated else { return nil }
    return cloudState.cacheKey
  }
  /// Memory changes only after the note row commits, so a failed write is never shown as saved.
  /// `baseRevision` is the revision an editor opened; a newer commit elsewhere
  /// then fails with a conflict instead of overwriting the latest body.
  @discardableResult func saveNote(
    _ note: StudyNote, baseRevision: Int? = nil, opened: StudyNote? = nil
  ) async throws -> Int {
    guard let scope = noteScope else {
      throw CloudError(status: 409, message: "笔记尚未保存，请重新打开课程后重试")
    }
    var updated = note
    if updated.anchor?.bookID.isEmpty == true { updated.anchor = nil }
    if let anchor = updated.anchor {
      guard let courseID = anchor.courseID,
        let resourceID = courseResourceID(courseID: courseID, bookID: anchor.bookID) else {
        throw ContentError.invalid("无指定课程，无法进行笔记撰写")
      }
      if let saved = anchor.courseResourceID, saved != resourceID { throw NoteStoreError.scopeMismatch }
      updated.anchor?.courseResourceID = resourceID
    }
    updated.conversation = nil
    updated.updatedAt = .now
    let condition: NoteWriteCondition
    if let base = baseRevision {
      condition = .revision(base)
    } else if let opened {
      // Compare against the copy the editor opened, not the live revision map.
      let stored = try await repository.loadStoredNote(opened.id, scope: scope)
      if let stored {
        guard stored.note.title == opened.title, stored.note.body == opened.body else {
          throw NoteStoreError.revisionConflict(noteID: updated.id)
        }
        condition = .revision(stored.revision)
      } else {
        condition = .absent
      }
    } else if let base = noteState.revisions[updated.id] {
      condition = .revision(base)
    } else if let memory = notes.first(where: { $0.id == updated.id }) {
      // No known base: compare against the stored row the editor started from
      // instead of blindly overwriting a newer commit.
      let stored = try await repository.loadStoredNote(updated.id, scope: scope)
      if let stored {
        guard stored.note.title == memory.title, stored.note.body == memory.body else {
          throw NoteStoreError.revisionConflict(noteID: updated.id)
        }
        condition = .revision(stored.revision)
      } else {
        condition = .absent
      }
    } else {
      condition = .absent
    }
    let stored = try await repository.mutateNote(updated, scope: scope, condition: condition)
    applyCommitted([stored], scope: scope)
    // Keep the account cache shell present for offline restarts; notes are not part of it.
    if cloud != nil { persistCloud() }
    return stored.revision
  }
  /// Merge committed rows into memory unless the account changed meanwhile.
  func applyCommitted(_ stored: [StoredNote], scope: String) {
    guard noteScope == scope else { return }
    for item in stored {
      noteState.revisions[item.note.id] = item.revision
      if let index = notes.firstIndex(where: { $0.id == item.note.id }) {
        notes[index] = item.note
      } else {
        notes.insert(item.note, at: 0)
      }
    }
  }
  func deleteNote(_ id: String) async {
    guard let scope = noteScope else { return }
    let deleted = notes.first { $0.id == id }
    do {
      try await repository.deleteNote(id, scope: scope)
    } catch {
      showToast(error.localizedDescription, kind: .error)
      return
    }
    if noteScope == scope {
      notes.removeAll { $0.id == id }
      noteState.revisions[id] = nil
    }
    if let cloud, !cloudState.privateLocalMode, let deleted { await StudioService(api: cloud).deleteLogicalNote(note: deleted) }
  }
  func reloadNotes(resumeAgent: Bool = true) async {
    guard let scope = noteScope else { return }
    do {
      let relationships = courses.flatMap { course in course.resourceIDs.compactMap { bookID -> NoteCourseResource? in
        guard let resourceID = courseResourceID(courseID: course.id, bookID: bookID) else { return nil }
        return NoteCourseResource(courseID: course.id, bookID: bookID, resourceID: resourceID)
      } }
      let stored = try await repository.migrateNoteOwnership(scope: scope, relationships: relationships)
      guard noteScope == scope else { return }
      notes = stored.map(\.note)
      noteState.revisions = Dictionary(stored.map { ($0.note.id, $0.revision) }) { _, last in last }
      await reloadAgentLearningObjects()
      await assistantHistory.load(scope: assistantHistoryScope)
      guard noteScope == scope else { return }
      if resumeAgent { agent.resume() }
    } catch { showToast(error.localizedDescription, kind: .error) }
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
      showToast(error.localizedDescription, kind: .error)
    }
  }
  func refundCredits(_ id: String) async {
    do { applyCredits(try await repository.finishCredits(id, refund: true)) } catch {
      showToast(error.localizedDescription, kind: .error)
    }
  }
  func ask(_ question: String) async {
    guard !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    guard cloud != nil, processingPolicy.path.kind != .offline else {
      showToast("AI 助手需要联网", kind: .warning); return
    }
    let snapshot = assistantSessionContext
    await assistantHistory.load(scope: assistantHistoryScope)
    do { try await syncAssistantHistory() }
    catch { showToast(error.localizedDescription, kind: .error); return }
    closeSheet()
    globalAssistantOpen = true
    assistantHistory.submit(question, context: snapshot) { [weak self] context, history, question in
      guard let self else { throw CancellationError() }
      return try await self.answerHistorySession(context: context, history: history, question: question)
    }
  }
  func snapshot() -> AppSnapshot {
    captureCourseImportDraft()
    return AppSnapshot(
      courseImports: importState.courses, importJob: importJob, chapterDrafts: chapterDrafts, preferences: preferences, draft: draft,
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
      } catch { self.showToast(error.localizedDescription, kind: .error) }
    }
  }
  func flush() async {
    await assistantHistory.flush()
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

extension AppStore {
  /// The association remains stable across launches and never aliases a textbook file ID.
  func courseResourceID(courseID: String?, bookID: String) -> String? {
    guard let courseID, let course = courses.first(where: { $0.id == courseID }),
      course.resourceIDs.contains(bookID) else { return nil }
    if let remote = cloudState.workspaces.first(where: { $0.id == courseID })?
      .resources.first(where: { $0.book_id == bookID }) { return remote.id }
    return course.courseResourceIDs?[bookID] ?? "local-resource:" + AgentDigest.sha256(courseID + ":" + bookID)
  }

  var unassignedSourceNotes: [StudyNote] {
    notes.filter { $0.anchor != nil && $0.anchor?.courseResourceID == nil }
  }

  func claimSourceNote(_ id: String, courseID: String) async throws {
    guard var note = notes.first(where: { $0.id == id }), let anchor = note.anchor,
      anchor.courseResourceID == nil,
      let resourceID = courseResourceID(courseID: courseID, bookID: anchor.bookID) else {
      throw ContentError.invalid("请选择包含对应教材的课程。")
    }
    note.anchor?.courseID = courseID
    note.anchor?.courseResourceID = resourceID
    _ = try await saveNote(note)
  }

  /// Cloud deletion calls this with the relationship IDs frozen before removing the workspace.
  func clearLocalCourseData(_ id: String, resourceIDs: Set<String>) async throws {
    if cloudState.privateLocalMode { cloudState.localDeletedCourseIDs.insert(id); cloudState.localCourseSources.removeValue(forKey: id) }
    guard let scope = noteScope else { throw NoteStoreError.scopeMismatch }
    let deleted = try await repository.deleteCourseNotes(scope: scope, courseID: id, resourceIDs: resourceIDs)
    guard noteScope == scope else { throw CancellationError() }
    notes.removeAll { deleted.contains($0.id) }
    for noteID in deleted { noteState.revisions[noteID] = nil }
    locations = locations.filter { !$0.key.hasPrefix(id + "::") }
    mistakes.removeAll { $0.course_id == id }
    assistantState.learningObjects.removeAll { $0.course_id == id }
    try await repository.saveAgentLearningObjects(assistantState.learningObjects, scope: scope)
    guard noteScope == scope else { throw CancellationError() }
    dailyPlanState.plans = dailyPlanState.plans.filter { !$0.key.hasPrefix(id + ":") }
    dailyPlanState.errors = dailyPlanState.errors.filter { !$0.key.hasPrefix(id + ":") }
    dailyPlanState.requests = dailyPlanState.requests.filter { !$0.key.hasPrefix(id + ":") }
    dailyPlanState.confirming = dailyPlanState.confirming.filter { !$0.hasPrefix(id + ":") }
    dailyPlanState.pending.removeAll { $0.workspaceID == id }
    if dailyPlanState.focus?.workspaceID == id { dailyPlanState.focus = nil }
    cloudState.pending = cloudState.pending.filter { $0.value.workspaceID != id }
    cloudState.pendingReviews.removeAll { $0.workspaceID == id }
    if cloudState.pendingSelection?.workspace_id == id { cloudState.pendingSelection = nil }
    // Source payloads are shared by courses. Remove only exclusively owned local learning data.
    let books = courses.first(where: { $0.id == id })?.resourceIDs ?? []
    for bookID in books where !courses.contains(where: { $0.id != id && $0.resourceIDs.contains(bookID) }) {
      if let index = sources.firstIndex(where: { $0.id == bookID }) {
        let cardIDs = Set(sources[index].flashcards.map(\.id))
        mastered.subtract(cardIDs)
        sources[index].flashcards = []
        sources[index].plan.tasks = []
      }
    }
  }
}

extension AppStore {
  func courseRemovalSummary(_ id: String) -> String {
    guard let course = courses.first(where: { $0.id == id }) else { return "课程已不存在" }
    let resourceIDs = Set(course.resourceIDs.compactMap { courseResourceID(courseID: id, bookID: $0) })
    let noteCount = notes.filter { $0.anchor?.courseResourceID.map(resourceIDs.contains) == true }.count
    let ownedBooks = course.resourceIDs.filter { bookID in
      !courses.contains(where: { $0.id != id && $0.resourceIDs.contains(bookID) })
    }
    let ownedSources = sources.filter { ownedBooks.contains($0.id) }
    let savedObjects = assistantState.learningObjects.filter { $0.course_id == id }
    let cards = ownedSources.reduce(0) { $0 + $1.flashcards.count } + savedObjects.filter { $0.kind == "flashcard" }.count
    let reviews = ownedSources.reduce(0) { $0 + $1.plan.tasks.count } + savedObjects.filter { ["review_task", "plan"].contains($0.kind) }.count
    let records = mistakes.filter { $0.course_id == id }.count
    let sharedSources = sources.filter { course.resourceIDs.contains($0.id) && !ownedBooks.contains($0.id) }
    let uncertainCards = sharedSources.reduce(0) { $0 + $1.flashcards.count }
    let uncertainTasks = sharedSources.reduce(0) { $0 + $1.plan.tasks.count }
    let uncertainRecords = mistakes.filter { $0.course_id == nil && course.resourceIDs.contains($0.book_id) }.count
    let titles = sharedSources.map { "\($0.summary.title)（\($0.id)）" }.joined(separator: "、")
    let unresolved = uncertainCards + uncertainTasks + uncertainRecords
    return "笔记 \(noteCount) 条 · 闪卡 \(cards) 张 · 复习任务 \(reviews) 项 · 学习记录 \(records) 条"
      + (unresolved > 0 ? "。另有旧闪卡 \(uncertainCards) 张、任务 \(uncertainTasks) 项、学习记录 \(uncertainRecords) 条缺少课程归属，保留待核对。关联教材：\(titles.isEmpty ? course.resourceIDs.joined(separator: "、") : titles)。" : "")
  }
}

extension AppStore {
  /// Save the acknowledged cloud deletion before fetching again, including when the next request disconnects.
  func cacheAcknowledgedCourseRemoval(_ id: String) {
    cloudState.workspaces.removeAll { $0.id == id }
    courses.removeAll { $0.id == id }
    if selectedCourseID == id {
      selectedCourseID = courses.first?.id
      selectedBookID = courses.first?.activeResourceID ?? courses.first?.resourceIDs.first
      selectedChapterID = nil
      selectedLessonID = nil
      sourceTarget = nil
    }
    saveCloudCacheOnly()
  }
}

struct CourseRemovalConfirmation: Sendable {
  var courseID: String
  var operationID: String
  var scope: String
  var arguments: [String: JSONValue]
  var summary: String
}

extension AppStore {
  func prepareCourseRemoval(_ id: String) async throws -> CourseRemovalConfirmation {
    guard let scope = noteScope, let course = courses.first(where: { $0.id == id }) else {
      throw ContentError.invalid("课程已不存在或不属于当前账号。")
    }
    let operationID = "manual-course-removal-" + UUID().uuidString.lowercased()
    var arguments: [String: JSONValue] = ["course_id": .string(id),
      "local_version": .string(try UnifiedAgentTools.courseMutationVersion(id, store: self)),
      "frozen_resource_ids": .array(course.resourceIDs.compactMap { courseResourceID(courseID: id, bookID: $0) }.map(JSONValue.string))]
    var summary = "本机：" + courseRemovalSummary(id)
    if let cloud, !cloudState.privateLocalMode, !id.hasPrefix("local-course:") {
      let response: [String: JSONValue] = try await cloud.request("/api/assistant/tools/remove_course", method: "POST", body: [
        "operation_id": .string(operationID), "arguments": .object(["course_id": .string(id), "confirmed": .bool(false)])])
      guard noteScope == scope else { throw CancellationError() }
      guard case .object(let impact)? = response["impact"], case .object(let counts)? = impact["counts"], let version = impact["version"] else {
        throw ContentError.invalid("未能取得课程删除影响清单，请重试。")
      }
      arguments["impact_version"] = version
      func count(_ key: String) -> Int { if case .number(let value)? = counts[key] { return Int(value) }; return 0 }
      summary += "\n云端：笔记 \(count("notes")) 条 · 闪卡 \(count("flashcards")) 张 · 复习任务 \(count("review_tasks")) 项 · 学习记录 \(count("learning_records")) 条。"
      if case .number(let legacy)? = impact["unassigned_legacy_notes_preserved"], legacy > 0 {
        summary += "另有 \(Int(legacy)) 条未归属云端笔记，移除课程后仍会保留。"
      }
    }
    return CourseRemovalConfirmation(courseID: id, operationID: operationID, scope: scope, arguments: arguments, summary: summary)
  }

  func confirmCourseRemoval(_ confirmation: CourseRemovalConfirmation) async throws {
    guard confirmation.scope == noteScope else { throw CancellationError() }
    _ = try await UnifiedAgentTools.performConfirmedMutation("remove_course", args: confirmation.arguments,
      operationID: confirmation.operationID, scope: confirmation.scope, store: self)
  }
}
