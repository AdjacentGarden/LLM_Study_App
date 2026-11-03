import CryptoKit
import Foundation

/// Durable state of one assistant tool run. The phone owns tool execution and
/// note writes; the server owns model requests and budgets for the same run ID.
enum AgentRunStatus: String, Codable, Sendable {
  case running, waitingForUser, completed, cancelled, failed
  /// A model request was submitted but its outcome is unknown. It is only
  /// reconciled against the original step and never resent automatically.
  case uncertain
  var isTerminal: Bool { [.completed, .cancelled, .failed, .uncertain].contains(self) }
}

/// Frozen at run creation. Later page turns or course switches never change it.
struct AgentFrozenContext: Codable, Hashable, Sendable {
  var scope: String
  var courseID: String?
  var bookID: String
  var bookTitle: String
  var sourceVersion: String
  var chapterID: String?
  var page: Int
  var printedPage: Int?
  var selection: String
  var currentNoteID: String?
  var capturedAt: Date
}

/// Locally registered evidence. Notes cite these records, never model text.
struct AgentEvidence: Codable, Hashable, Sendable {
  var id: String
  var bookID: String
  var courseID: String?
  var sourceVersion: String
  var chapterID: String?
  var page: Int
  var printedPage: Int?
  var quote: String
  var quoteSHA256: String
}

/// Executor-generated citation stored with a note. It keeps the source version
/// it was captured from so later re-indexing can map instead of rewrite it.
struct NoteReference: Codable, Hashable, Sendable {
  var evidenceID: String
  var bookID: String
  var courseID: String?
  var sourceVersion: String
  var chapterID: String?
  var page: Int
  var printedPage: Int?
  var quote: String
  var quoteSHA256: String
  init(_ evidence: AgentEvidence) {
    evidenceID = evidence.id
    bookID = evidence.bookID
    courseID = evidence.courseID
    sourceVersion = evidence.sourceVersion
    chapterID = evidence.chapterID
    page = evidence.page
    printedPage = evidence.printedPage
    quote = evidence.quote
    quoteSHA256 = evidence.quoteSHA256
  }
}

/// Who wrote a note and which knowledge it relies on.
struct NoteProvenance: Codable, Hashable, Sendable {
  var runID: String
  var toolCallID: String
  /// "textbook", "general" or "mixed"; general knowledge is never shown as a citation.
  var sourceLabel: String
}

struct AgentToolCall: Codable, Hashable, Sendable {
  var id: String
  var name: String
  var argumentsJSON: String
  var argumentsHash: String
}

struct AgentToolOutcome: Codable, Hashable, Sendable {
  var toolCallID: String
  /// "ok", "error" or "skipped".
  var status: String
  var resultJSON: String
  var noteID: String?
  var noteRevision: Int?
}

enum AgentStepState: String, Codable, Sendable {
  /// Created locally; the POST may not have left the phone yet.
  case pending
  /// The POST may have reached the server; query before any resend.
  case posted
  case completed
  case uncertain
  case failed
}

struct AgentStep: Codable, Hashable, Sendable {
  var index: Int
  var id: String
  var state: AgentStepState
  var content: String?
  var toolCalls: [AgentToolCall] = []
  var outcomes: [AgentToolOutcome] = []
  var failure: String?
  func outcome(_ id: String) -> AgentToolOutcome? { outcomes.first { $0.toolCallID == id } }
  var hasPendingTools: Bool { toolCalls.contains { outcome($0.id) == nil } }
}

struct AgentPendingChoice: Codable, Hashable, Sendable {
  var toolCallID: String
  var question: String
  var options: [String]
  var selected: String?
}

struct AgentRunRecord: Codable, Hashable, Identifiable, Sendable {
  static let maxSteps = 8
  var id: String
  /// Optimistic-concurrency counter maintained by the repository.
  var version = 0
  var scope: String
  var status: AgentRunStatus
  var request: String
  var context: AgentFrozenContext
  var evidence: [AgentEvidence]
  var steps: [AgentStep]
  var pendingChoice: AgentPendingChoice?
  var cancelRequested = false
  var finalText: String?
  var failure: String?
  var committedNoteIDs: [String] = []
  var createdAt: Date
  var updatedAt: Date

  func evidence(_ id: String) -> AgentEvidence? { evidence.first { $0.id == id } }
}

enum AgentDigest {
  static func sha256(_ text: String) -> String {
    SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
  }
  /// Key-sorted canonical JSON so equal arguments always hash equally.
  static func canonicalJSON(_ object: Any) throws -> String {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
    return String(decoding: data, as: UTF8.self)
  }
}
