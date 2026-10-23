import Foundation

/// Wire names deliberately match the backend. Unknown fields are ignored by Codable.
public struct AccountState: Codable, Sendable {
    public struct Account: Codable, Sendable { public var email: String; public var is_demo: Bool? }
    public var account: Account?
    public var user_id: String
    public var email_available: Bool
    public var learning_sessions: [String: String]
}
public struct Book: Codable, Identifiable, Sendable {
    public var id: String { book_id }
    public var book_id: String
    public var title: String
    public var status: String
    public var page_count: Int?
    public var chapter_count: Int
    public var summary: String
    public var diagnostics_ready: Bool?
}
public struct UploadedBook: Codable, Sendable { public var book_id: String; public var filename: String; public var status: String }
public struct BookStatus: Codable, Sendable {
    public var book_id: String; public var status: String; public var progress: Double
    public var current_step: String; public var retryable: Bool
    public var needs_human_review: Bool?
}
public struct PreparationJob: Codable, Sendable {
    public var book_id: String; public var status: String; public var stage: String
    public var error: String; public var updated: Double
}
public struct Citation: Codable, Sendable { public var page_number: Int; public var quote: String; public var block_id: String? }
public struct Chapter: Codable, Identifiable, Sendable {
    public var id: String { chapter_id }; public var chapter_id: String
    public var order: Int; public var title: String; public var start_page: Int; public var end_page: Int
    public var summary: String; public var knowledge_points: [String]; public var evidence: [Citation]
}
public struct BookStructure: Codable, Sendable { public var title: String; public var summary: String; public var source_page_count: Int; public var chapters: [Chapter] }
public struct TurnOption: Codable, Identifiable, Sendable { public var id: String; public var label: String }
public struct DiagnosticItem: Codable, Sendable {
    public var item_id: String; public var prompt: String; public var options: [String]; public var response_type: String
}
public struct Turn: Codable, Sendable {
    public var turn_id: String; public var phase: String; public var message: String
    public var question: String?; public var response_type: String?
    public var options: [TurnOption]; public var item: DiagnosticItem?; public var why_asked: String?; public var progress: Double
}
public struct Interview: Codable, Sendable {
    public var session_id: String; public var phase: String; public var turn: Turn
    // Profile remains on the server; the native client only needs the current turn.
    public init(sessionID: String, turn: Turn) { session_id = sessionID; phase = turn.phase; self.turn = turn }
}
public struct DiagnosticReply: Codable, Sendable { public var next_turn: Turn }
public struct LessonSection: Codable, Sendable { public var title: String; public var content: String; public var purpose: String; public var citations: [Citation] }
public struct KnowledgePoint: Codable, Identifiable, Sendable {
    public var id: String { point_id }; public var point_id: String; public var title: String
    public var explanation: String; public var importance: String; public var mastery: Double; public var state: String; public var citations: [Citation]
}
public struct Flashcard: Codable, Identifiable, Sendable {
    public var id: String { card_id }; public var card_id: String; public var front: String; public var back: String
    public var reason_for_user: String; public var citations: [Citation]
}
public struct PracticeItem: Codable, Identifiable, Sendable {
    public var id: String { item_id }; public var item_id: String; public var prompt: String
    public var response_type: String; public var options: [String]; public var estimated_seconds: Int
}
public struct StudyUnit: Codable, Identifiable, Sendable {
    public var id: String { unit_id }; public var unit_id: String; public var title: String
    public var point_ids: [String]; public var estimated_minutes: Int
}
public struct Course: Codable, Sendable {
    public var course_id: String; public var version: Int; public var chapter_id: String; public var chapter_title: String
    public var opening: String; public var summary: String; public var original_reading: [LessonSection]
    public var knowledge_points: [KnowledgePoint]; public var flashcards: [Flashcard]
    public var worked_examples: [LessonSection]; public var checkpoint_questions: [String]; public var practice_items: [PracticeItem]
    public var estimated_minutes: Int; public var created_at: String; public var unresolved_source_warnings: [String]
    public var study_units: [StudyUnit]?
}
public struct CourseJob: Codable, Sendable {
    public var job_id: String; public var session_id: String; public var chapter_id: String
    public var status: String; public var stage: String; public var attempts: Int
    public var error: String?; public var retryable: Bool; public var created_at: String; public var updated_at: String
    public var course: Course?
    public var isPending: Bool { status == "queued" || status == "running" }
    public var isExhaustedFailure: Bool { status == "failed" && !retryable }
    public var clientFailureMessage: String? {
        guard status == "failed" else { return nil }
        return isExhaustedFailure ? "本次课程生成已停止，任务已保留。请检查服务状态；待明确有可用新版本后，再按当前状态准备。" : (error ?? "本次课程生成未完成，任务已保留，可明确重试。")
    }
}
public struct Evidence: Codable, Sendable {
    public var score: Double; public var scoring_confidence: Double; public var needs_follow_up: Bool
    public var matched_rubric: [String]; public var missing_rubric: [String]
}
public struct CourseActivity: Codable, Sendable { public var duplicate: Bool; public var evidence: Evidence; public var course_stale: Bool }
public struct QAResult: Codable, Sendable {
    public struct Claim: Codable, Sendable { public var text: String; public var citations: [Citation] }
    public var status: String; public var answer: String; public var confidence: Double
    public var evidence_pages: [Int]; public var insufficiency_reason: String?; public var claims: [Claim]
}
public struct Note: Codable, Identifiable, Sendable {
    public var id: String; public var book_id: String; public var chapter_id: String?; public var chapter_title: String?
    public var excerpt: String?; public var pages: [Int]?; public var revision: Int; public var title: String
    public var input_mode: String?; public var strokes: [Stroke]?; public var audio_ready: Bool?
    public var audio_duration_seconds: Double?; public var edition: StudioResult?
    public struct Stroke: Codable, Sendable {
        public struct Point: Codable, Sendable { public var x: Double; public var y: Double; public var p: Double }
        public var points: [Point]; public var color: String; public var width: Double
    }
    public init(id: String, bookID: String, title: String, revision: Int = 0, chapterID: String? = nil, chapterTitle: String? = nil) {
        self.id = id; book_id = bookID; self.title = title; self.revision = revision
        chapter_id = chapterID; chapter_title = chapterTitle; input_mode = "voice"; strokes = []
    }
}
public struct StudioResult: Codable, Sendable {
    public var transcript: String?; public var summary: String?; public var polished: String?
    public var explanation: String?; public var caution: String?; public var uncertain: [String]?
    public var quality_notice: String?; public var excluded_optional_count: Int?; public var provisional: Bool?
    public var review: ReviewMetadata?
    /// Existing media results use a string; note review metadata may expose a Boolean verdict.
    public struct ReviewMetadata: Codable, Sendable {
        public var passed: Bool?
        private var legacyText: String?
        private enum CodingKeys: String, CodingKey { case passed }
        public init(from decoder: Decoder) throws {
            let value = try decoder.singleValueContainer()
            if let text = try? value.decode(String.self) { passed = nil; legacyText = text; return }
            let fields = try decoder.container(keyedBy: CodingKeys.self)
            passed = try fields.decodeIfPresent(Bool.self, forKey: .passed); legacyText = nil
        }
        public func encode(to encoder: Encoder) throws {
            if let legacyText { var value = encoder.singleValueContainer(); try value.encode(legacyText) }
            else { var fields = encoder.container(keyedBy: CodingKeys.self); try fields.encodeIfPresent(passed, forKey: .passed) }
        }
    }

    /// Omission counts describe AI suggestions, never deleted user sentences or learner errors.
    public func qualityDisclosure(status: String) -> String? {
        let notice = quality_notice?.trimmingCharacters(in: .whitespacesAndNewlines)
        let excluded = max(0, excluded_optional_count ?? 0)
        guard provisional == true || review?.passed == false || excluded > 0 || !(notice?.isEmpty ?? true) else { return nil }
        guard status == "succeeded", provisional != true, review?.passed != false else {
            return "当前整理尚未通过完整审核；以下内容仅供核对。"
        }
        if let notice, !notice.isEmpty { return notice }
        return "已保留原笔记，部分 AI 可选补充未纳入本次结果。"
    }
}
public struct StudioJob: Codable, Identifiable, Sendable {
    public var id: String; public var book_id: String; public var kind: String; public var status: String
    public var result: StudioResult; public var error: String; public var note_id: String?; public var asset_url: String?
    public var revision: Int?
    public var isPending: Bool { !["succeeded", "failed", "needs_confirmation", "uncertain", "cancelled"].contains(status) }
}
public struct Items<T: Codable & Sendable>: Codable, Sendable { public var items: [T] }
public struct OK: Codable, Sendable { public var ok: Bool }
public struct TextNoteResource: Codable, Sendable { public var id: String }
public struct ReviewSubmission: Codable, Sendable {
    public var eventID: String; public var rating: String; public var seconds: Double
    public init(eventID: String, rating: String, seconds: Double) { self.eventID = eventID; self.rating = rating; self.seconds = max(1, min(3600, seconds)) }
}
public struct HealthStatus: Codable, Sendable { public var ok: Bool; public var service: String? }
public struct Readiness: Codable, Sendable {
    public var ok: Bool; public var check_scope: String; public var external_inference_verified: Bool
    public var features: [String: Bool]; public var blocked_features: [String]
}

/// Stable event_id makes uncertain practice/review responses safe to resubmit.
public struct Answer: Codable, Sendable {
    public var event_id: String?; public var item_id: String?
    public var answer: String; public var selected_option_ids: [String]; public var confidence: Double
    public var response_seconds: Double; public var hints_used: Int; public var revisions: Int
    public init(eventID: String? = nil, itemID: String? = nil, answer: String, selected: [String], confidence: Double, seconds: Double) {
        event_id = eventID; item_id = itemID; self.answer = answer; selected_option_ids = selected
        self.confidence = max(0, min(1, confidence)); response_seconds = max(1, min(3600, seconds)); hints_used = 0; revisions = 0
    }
}
