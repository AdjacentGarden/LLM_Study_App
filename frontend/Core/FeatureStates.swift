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
