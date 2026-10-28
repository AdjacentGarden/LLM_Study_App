import Foundation
import SwiftUI

// Wire models intentionally retain source JSON keys, including snake_case IDs.
enum JSONValue: Codable, Hashable, Sendable {
  case string(String)
  case number(Double)
  case bool(Bool)
  case array([JSONValue])
  case object([String: JSONValue])
  case null
  init(from decoder: Decoder) throws {
    let c = try decoder.singleValueContainer()
    if c.decodeNil() {
      self = .null
    } else if let v = try? c.decode(Bool.self) {
      self = .bool(v)
    } else if let v = try? c.decode(Double.self) {
      self = .number(v)
    } else if let v = try? c.decode(String.self) {
      self = .string(v)
    } else if let v = try? c.decode([JSONValue].self) {
      self = .array(v)
    } else {
      self = .object(try c.decode([String: JSONValue].self))
    }
  }
  func encode(to encoder: Encoder) throws {
    var c = encoder.singleValueContainer()
    switch self {
    case .string(let v): try c.encode(v)
    case .number(let v): try c.encode(v)
    case .bool(let v): try c.encode(v)
    case .array(let v): try c.encode(v)
    case .object(let v): try c.encode(v)
    case .null: try c.encodeNil()
    }
  }
  var stringValue: String? {
    if case .string(let s) = self { return s }
    return nil
  }
  var numberValue: Double? {
    if case .number(let n) = self { return n }
    return nil
  }
}
struct SourceSummary: Codable, Identifiable, Hashable, Sendable {
  var book_id: String
  var title: String
  var cover_url: String?
  var filename: String?
  var status: String
  var page_count: Int
  var chapter_count: Int
  var chunk_count: Int
  var asset_count: Int
  var average_confidence: Double
  var updated_at: Double
  var content_mode: String?
  var directory_unit_label: String?
  var next_title: String?
  var id: String { book_id }
}
struct Chapter: Codable, Identifiable, Hashable, Sendable {
  var chapter_id: String
  var level: Int
  var source_title: String
  var ai_title: String
  var page_start: Int
  var page_end: Int
  var printed_page_start: Int?
  var printed_page_end: Int?
  var confidence: Double
  var status: String
  var source: String
  var parent_id: String?
  var id: String { chapter_id }
  var title: String { ai_title.isEmpty ? source_title : ai_title }
  var pageLabel: String {
    page_start == 0
      ? "AI 补充 · 待原文核验" : "第 \(printed_page_start ?? page_start)–\(printed_page_end ?? page_end) 页"
  }
}
struct SourceChunk: Codable, Identifiable, Hashable, Sendable {
  var chunk_id: String
  var book_id: String
  var chapter_id: String
  var page_start: Int
  var page_end: Int
  var printed_page_start: Int?
  var printed_page_end: Int?
  var content_type: String
  var text: String
  var asset_ids: [String]
  var key_concepts: [String]
  var source_metadata: [String: JSONValue]?
  var section_id: String?
  var title_path: [String]?
  var vector_position: Int?
  var id: String { chunk_id }
}
struct LessonCitation: Codable, Identifiable, Hashable, Sendable {
  var chunk_id: String
  var page_start: Int
  var page_end: Int
  var printed_page_start: Int?
  var printed_page_end: Int?
  var quote: String?
  var source_metadata: [String: JSONValue]?
  var id: String { chunk_id }
}
struct LessonBlock: Codable, Identifiable, Hashable, Sendable {
  var block_id: String
  var block_type: String
  var title: String
  var content: String
  var citations: [LessonCitation]
  var source_chunk_ids: [String]
  var asset_ids: [String]
  var ai_generated: Bool
  var id: String { block_id }
}
struct Lesson: Codable, Identifiable, Hashable, Sendable {
  var book_id: String
  var lesson_id: String
  var chapter_id: String
  var title: String
  var source_title: String
  var page_start: Int
  var page_end: Int
  var lesson_kind: String
  var status: String
  var confidence: Double
  var objectives: [String]
  var key_concepts: [String]
  var summary: String
  var blocks: [LessonBlock]
  var source_chunk_ids: [String]
  var asset_ids: [String]
  var warnings: [String]
  var printed_page_start: Int?
  var printed_page_end: Int?
  var id: String { lesson_id }
}
struct BookAsset: Codable, Identifiable, Hashable, Sendable {
  var asset_id: String
  var book_id: String
  var chapter_id: String?
  var source_type: String
  var page: Int?
  var type: String
  var caption: String
  var bbox: [Double]?
  var image_url: String
  var thumbnail_url: String
  var source_page_image_url: String?
  var source_chunk_ids: [String]
  var concepts: [String]
  var metadata: [String: JSONValue]?
  var id: String { asset_id }
}
struct Flashcard: Codable, Identifiable, Hashable, Sendable {
  var card_id: String
  var book_id: String
  var lesson_id: String
  var chapter_id: String
  var front: String
  var back: String
  var concept: String
  var source_chunk_ids: [String]
  var page_start: Int
  var page_end: Int
  var printed_page_start: Int?
  var printed_page_end: Int?
  var mastery: Double
  var reason: String
  var due: String?
  var source_kind: String?
  var source_quote: String?
  var id: String { card_id }
}
struct QuizQuestion: Codable, Identifiable, Hashable, Sendable {
  var question_id: String
  var book_id: String
  var lesson_id: String
  var chapter_id: String
  var prompt: String
  var choices: [String]
  var answer: String
  var explanation: String
  var concept: String
  var source_chunk_ids: [String]
  var page_start: Int
  var page_end: Int
  var printed_page_start: Int?
  var printed_page_end: Int?
  var question_type: String?
  var instruction: String?
  var source_kind: String?
  var source_quote: String?
  var id: String { question_id }
}
struct StudyTask: Codable, Identifiable, Hashable, Sendable {
  var task_id: String
  var user_id: String
  var day: Int
  var title: String
  var task_type: String
  var minutes: Int
  var chapter_id: String?
  var lesson_id: String?
  var status: String
  var score: Double?
  var weak_points: [String]
  var review_target: String?
  var adjustment_reason: String?
  var id: String { task_id }
}
struct StudyPlan: Codable, Hashable, Sendable {
  var user_id: String
  var book_id: String
  var days: Int
  var daily_minutes: Int
  var tasks: [StudyTask]
}
struct SourceContent: Codable, Identifiable, Hashable, Sendable {
  var summary: SourceSummary
  var chapters: [Chapter]
  var lessons: [Lesson]
  var chunks: [SourceChunk]
  var assets: [BookAsset]
  var flashcards: [Flashcard]
  var quizzes: [QuizQuestion]
  var plan: StudyPlan
  var id: String { summary.book_id }
}
struct CommunityBook: Codable, Identifiable, Hashable, Sendable {
  var id: String
  var title: String
  var catalogTitle: String
  var owner: String
  var cover: String
  var subject: String
  var grade: String
  var version: String
  var volume: String?
  var learners: Int?
  var progress: Double?
  var flashcardCount: Int
  var recommended: Bool?
  var tags: [String]
  var description: String
  var chapters: [String]
}
struct ShelfBook: Codable, Identifiable, Hashable, Sendable {
  var bookId: String
  var title: String
  var filename: String
  var coverUrl: String
  var id: String { bookId }
}
struct Course: Codable, Identifiable, Hashable, Sendable {
  var id: String = UUID().uuidString
  var name: String
  var resourceIDs: [String]
  var activeResourceID: String?
  var diagnosis: [String: [String]] = [:]
  var createdAt: Date = .now
  var updatedAt: Date = .now
}
struct LearnerPreferences: Codable, Hashable, Sendable {
  static let currentOnboardingVersion = 2
  var displayName: String
  var primaryGoal: String?
  var dailyTime: String?
  var learningBarriers: [String]
  var guidanceStyles: [String]
  var planningSupport: String?
  var onboardingVersion: Int
  var hasCompletedOnboarding: Bool { onboardingVersion >= Self.currentOnboardingVersion }

  init(
    displayName: String, primaryGoal: String?, dailyTime: String?,
    learningBarriers: [String] = [], guidanceStyles: [String] = [],
    planningSupport: String? = nil, onboardingVersion: Int = currentOnboardingVersion
  ) {
    self.displayName = displayName
    self.primaryGoal = primaryGoal
    self.dailyTime = dailyTime
    self.learningBarriers = learningBarriers
    self.guidanceStyles = guidanceStyles
    self.planningSupport = planningSupport
    self.onboardingVersion = onboardingVersion
  }

  private enum CodingKeys: String, CodingKey {
    case displayName, primaryGoal, dailyTime, learningBarriers, guidanceStyles, planningSupport,
      onboardingVersion
  }
  init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    displayName = try values.decode(String.self, forKey: .displayName)
    primaryGoal = try values.decodeIfPresent(String.self, forKey: .primaryGoal)
    dailyTime = try values.decodeIfPresent(String.self, forKey: .dailyTime)
    learningBarriers = try values.decodeIfPresent([String].self, forKey: .learningBarriers) ?? []
    guidanceStyles = try values.decodeIfPresent([String].self, forKey: .guidanceStyles) ?? []
    planningSupport = try values.decodeIfPresent(String.self, forKey: .planningSupport)
    // An existing three-question profile resumes at the newly added questions.
    onboardingVersion = try values.decodeIfPresent(Int.self, forKey: .onboardingVersion) ?? 1
  }
}
struct CourseDraft: Codable, Hashable, Sendable {
  var name = ""
  var resourceIDs: [String] = []
  var diagnosis: [String: [String]] = [:]
  var step = 0
  var editingCourseID: String?
  var importCourseID: String? = nil
  var catalogBookID: String?
  var parseCompleted = false
  var creationWorkspaceID: String? = nil
}
struct StudyLocation: Codable, Hashable, Sendable {
  var chapterID: String?
  var lessonID: String?
  var page: Int = 1
}
enum NoteKind: String, Codable, CaseIterable, Identifiable, Sendable {
  case text, ink, voice
  var id: String { rawValue }
  var title: String {
    switch self {
    case .text: "文字"
    case .ink: "手写"
    case .voice: "语音"
    }
  }
}
struct UnitPointValue: Codable, Hashable, Sendable {
  var x: CGFloat
  var y: CGFloat
}
struct NoteAnchor: Codable, Hashable, Sendable {
  var courseID: String?
  var bookID: String
  var chapterID: String?
  var page: Int
  var printedPage: Int?
  var quote: String?
  init(
    courseID: String? = nil, bookID: String, chapterID: String? = nil, page: Int,
    printedPage: Int? = nil, quote: String? = nil
  ) {
    self.courseID = courseID
    self.bookID = bookID
    self.chapterID = chapterID
    self.page = page
    self.printedPage = printedPage
    self.quote = quote
  }
}
struct StudyNote: Codable, Identifiable, Hashable, Sendable {
  var id: String
  var kind: NoteKind
  var title: String
  var body: String
  var anchor: NoteAnchor?
  var createdAt: Date
  var updatedAt: Date
  var position: UnitPointValue?
  var audioPath: String?
  var duration: Double
  var transcript: String?
  var voiceRecognitionSource: String?
  var voiceRecognitionAudioSHA256: String?
  var organizedText: String?
  var drawingPath: String?
  var conversation: [ChatMessage]?
  init(
    id: String = UUID().uuidString, kind: NoteKind, title: String, body: String = "",
    anchor: NoteAnchor? = nil, createdAt: Date = .now, updatedAt: Date = .now,
    position: UnitPointValue? = nil, audioPath: String? = nil, duration: Double = 0,
    transcript: String? = nil, organizedText: String? = nil, drawingPath: String? = nil,
    conversation: [ChatMessage]? = nil, voiceRecognitionSource: String? = nil,
    voiceRecognitionAudioSHA256: String? = nil
  ) {
    self.id = id
    self.kind = kind
    self.title = title
    self.body = body
    self.anchor = anchor
    self.createdAt = createdAt
    self.updatedAt = updatedAt
    self.position = position
    self.audioPath = audioPath
    self.duration = duration
    self.transcript = transcript
    self.voiceRecognitionSource = voiceRecognitionSource
    self.voiceRecognitionAudioSHA256 = voiceRecognitionAudioSHA256
    self.organizedText = organizedText
    self.drawingPath = drawingPath
    self.conversation = conversation
  }
  /// Legacy transcripts have no provenance and must never be treated as a new
  /// on-device recognition. Tie a reusable result to the exact saved recording.
  func localSpeechTranscript(audioSHA256: String) -> String? {
    guard voiceRecognitionSource == "apple-speech-on-device", !audioSHA256.isEmpty,
      voiceRecognitionAudioSHA256 == audioSHA256,
      let text = transcript?.trimmingCharacters(in: .whitespacesAndNewlines),
      !text.isEmpty, text.count <= 12000 else { return nil }
    return text
  }
}
struct SourcePageTarget: Codable, Hashable, Sendable {
  var bookID: String
  var title: String
  var page: Int
  var pageEnd: Int?
  var printedPage: Int?
  var sourceText: String?
  var from: Route?
  init(
    bookID: String, title: String, page: Int, pageEnd: Int? = nil, printedPage: Int? = nil,
    sourceText: String? = nil, from: Route? = nil
  ) {
    self.bookID = bookID
    self.title = title
    self.page = page
    self.pageEnd = pageEnd
    self.printedPage = printedPage
    self.sourceText = sourceText
    self.from = from
  }
}
struct Citation: Codable, Identifiable, Hashable, Sendable {
  var bookID: String
  var chapterID: String
  var chapterTitle: String
  var page: Int
  var printedPage: Int?
  var chunkID: String
  var quote: String
  var score: Double
  var id: String { bookID + ":" + chunkID }
}
struct ChatMessage: Codable, Identifiable, Hashable, Sendable {
  var id = UUID().uuidString
  var role: String
  var text: String
  var citations: [Citation] = []
}
struct Diagnosis: Codable, Hashable, Sendable {
  var assignment_id: String
  var submission_id: String
  var result: String
  var stuck_point: String
  var knowledge_points: [String]
  var review_citations: [Citation]
  var hint: String
  var needs_followup: Bool
  var mistake_recorded: Bool
}
struct MistakeRecord: Codable, Identifiable, Hashable, Sendable {
  var mistake_id: String
  var user_id: String
  var book_id: String
  var assignment_id: String
  var question: String
  var answer: String
  var stuck_point: String
  var knowledge_points: [String]
  var citation_ids: [String]
  var correct_answer: String?
  var explanation: String?
  var mastery: String?
  var error_count: Int?
  var next_review_at: String?
  var last_wrong_at: String?
  var mistake_reason: String?
  var course_id: String?
  var id: String { mistake_id }
}
enum Route: String, Codable, CaseIterable, Identifiable, Sendable {
  case home, onboarding, preferenceSetup, courseSetup, courseDetail, upload, parseReady, processing, courseProgress,
    courseImportProcessing, chapterConfirm, courseReady, courseImportReady, library, community,
    communityBook, communityImport, study, plan, flashcards, lesson, assignment, diagnosis,
    mistakes, notes, voiceNote, source, export, profile, settings
  var id: String { rawValue }
  var title: String {
    switch self {
    case .home: "首页"
    case .onboarding: "我们怎么称呼你"
    case .preferenceSetup: "重新设置学习偏好"
    case .courseSetup: "设置课程"
    case .courseDetail: "课程"
    case .upload: "创建课程"
    case .parseReady, .processing: "解析教材"
    case .courseProgress: "解析进度"
    case .courseImportProcessing: "正在解析资料"
    case .chapterConfirm: "确认目录"
    case .courseReady: "生成成功"
    case .courseImportReady: "课程导入完成"
    case .library: "全部课程"
    case .community: "社区"
    case .communityBook: "共享课程"
    case .communityImport: "导入成功"
    case .study: "学习"
    case .plan: "学习计划"
    case .flashcards: "知识点闪卡"
    case .lesson: "章节学习"
    case .assignment: "作业练习"
    case .diagnosis: "作业诊断"
    case .mistakes: "错题集"
    case .notes: "学习笔记"
    case .voiceNote: "语音笔记"
    case .source: "原文"
    case .export: "导出预览"
    case .profile: "我的"
    case .settings: "设置"
    }
  }
  var showsPrimaryNavigation: Bool { isTab }
  var isTab: Bool { [.study, .community, .profile].contains(self) }
  var hidesHeader: Bool {
    [
      .home, .onboarding, .preferenceSetup, .courseSetup, .community, .study, .profile, .lesson,
      .source, .voiceNote,
    ].contains(self)
  }
}
enum SheetRoute: String, Codable, Identifiable, CaseIterable, Sendable {
  case chat, note, noteType, editChapter, bookSwitcher, addMaterials, shareBook
  var id: String { rawValue }
}
struct AppSnapshot: Codable, Sendable {
  var courseImports: [CourseImportRecord]? = nil
  var importJob: ImportJob?
  var chapterDrafts: [String: [Chapter]]?
  var version = 1
  var preferences: LearnerPreferences?
  var draft = CourseDraft()
  var courses: [Course] = []
  var selectedCourseID: String?
  var selectedBookID: String?
  var locations: [String: StudyLocation] = [:]
  var mastery: Set<String> = []
  var credits = 0
  var creditReservations: [String: Int] = [:]
  var mistakes: [MistakeRecord] = []
  var dismissedSourceIDs: Set<String> = []
}

struct CreditLedger: Codable, Sendable {
  var balance = 0
  var reservations: [String: Int] = [:]
  var revision = 0
}

enum GenerationTaskStatus: String, Codable, Sendable {
  case queued, running, completed, failed, cancelled, interrupted
}
struct ImportJob: Codable, Sendable, Identifiable {
  var id = UUID().uuidString
  var bookIDs: [String]
  var status: GenerationTaskStatus
  var progress: Double = 0
  var message: String = "准备整理资料"
  var preparationBookID: String? = nil
  var preparationFailed: Bool? = nil
  var preparationStage: String? = nil
}

struct CourseImportBook: Codable, Sendable, Identifiable {
  var id: String
  var status: GenerationTaskStatus = .queued
  var progress: Double = 0
  var stage: String? = nil
  var message = "等待解析"
}

/// Account-scoped course work survives replacement of the creation questionnaire.
struct CourseImportRecord: Codable, Sendable, Identifiable {
  var id: String
  var draft: CourseDraft
  var books: [CourseImportBook]
  var job: ImportJob
  var error: String? = nil
  var started = false
  var setupCompleted: Bool? = nil
  var onDeviceOnly: Bool? = nil
  var message: String { error ?? job.message }
  var needsPreparation: Bool { job.status != .completed }
  var ringProgress: Double? {
    guard job.status == .running, job.preparationStage == nil else { return nil }
    return min(1, max(0, job.progress))
  }
}
