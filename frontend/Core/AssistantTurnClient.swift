import Foundation

/// Wire contract for POST /api/assistant/turn and its ledger lookups.
/// Snake_case property names mirror assistant_routes.py directly.
struct AssistantTurnContextDTO: Codable, Sendable, Equatable {
  var book_id: String
  var book_title: String
  var course_id: String?
  var source_version: String
  var chapter_id: String?
  var page: Int
  var printed_page: Int?
  var selection: String
  var current_note_id: String?
  var course_resource_id: String? = nil
  var page_type: String? = nil
  var practice_state: String? = nil
  var snapshot: [String: JSONValue]? = nil
}

struct AssistantTurnEvidenceDTO: Codable, Sendable, Equatable {
  var evidence_id: String
  var page: Int
  var printed_page: Int?
  var quote: String
  var source_type: String? = nil
  var object_id: String? = nil
  var source_version: String? = nil
  var book_id: String? = nil
  var course_id: String? = nil
  var course_resource_id: String? = nil
}

struct AssistantToolResultDTO: Codable, Sendable, Equatable {
  var step_index: Int
  var tool_call_id: String
  var content: String
  var complete: Bool? = nil
  var continuation: String? = nil
}

struct AssistantTurnRequestDTO: Codable, Sendable, Equatable {
  var contract_version = 1
  var run_id: String
  var step_id: String
  var step_index: Int
  var request: String
  var context: AssistantTurnContextDTO
  var evidence: [AssistantTurnEvidenceDTO]
  var tools: [String]
  var tool_results: [AssistantToolResultDTO]
  var session_id: String? = nil
  var message_id: String? = nil
  var ephemeral: Bool? = nil
  var history: [[String: JSONValue]]? = nil
  var attachments: [AssistantImageDTO]? = nil
  var client_messages: [[String: JSONValue]]? = nil
}

struct AssistantToolCallDTO: Decodable, Sendable {
  var id: String
  var name: String
  var arguments: [String: JSONValue]
}

struct AssistantMessageDTO: Decodable, Sendable {
  var content: String
  var tool_calls: [AssistantToolCallDTO]
}

struct AssistantBudgetDTO: Decodable, Sendable {
  var steps: Int
  var steps_max: Int?
  var posts: Int
  var posts_max: Int?
  var input_tokens: Int
  var input_tokens_max: Int?
  var output_tokens: Int
  var output_tokens_max: Int?
  var model_seconds: Double
  var model_seconds_max: Double?
  var cached_input_tokens: Int?
}

struct AssistantTurnResponseDTO: Decodable, Sendable {
  var contract_version: Int
  var run_id: String
  var step_id: String
  var step_index: Int
  /// submitting | completed | retryable | uncertain | failed | refused
  var state: String
  var attempt: Int
  var message: AssistantMessageDTO?
  var finish_reason: String?
  var error: String?
  var retry_after: Double?
  /// cancelled | steps | posts | input_tokens | output_tokens | model_seconds
  var refused: String?
  var budget: AssistantBudgetDTO?
  var context_tokens: Int? = nil
  var context_window: Int? = nil
  var capacity_state: String? = nil
  var context_estimated: Bool? = nil
}

struct AssistantCapabilitiesDTO: Decodable, Sendable {
  var contract_version: Int
  var available: Bool
  var tools: [String]
  var max_steps: Int?
}

extension CloudAPI {
  func assistantTurn(_ body: AssistantTurnRequestDTO) async throws -> AssistantTurnResponseDTO {
    let values = try JSONDecoder().decode([String: JSONValue].self, from: JSONEncoder().encode(body))
    return try await request("/api/assistant/turn", method: "POST", body: values)
  }

  func assistantStep(runID: String, stepID: String) async throws -> AssistantTurnResponseDTO {
    try await request("/api/assistant/runs/\(runID)/steps/\(stepID)")
  }

  func cancelAssistantRun(_ runID: String) async throws {
    // The cancel body is informational; only the durable ledger state matters.
    let _: [String: JSONValue] = try await request(
      "/api/assistant/runs/\(runID)/cancel", method: "POST")
  }

  func assistantCapabilities() async throws -> AssistantCapabilitiesDTO {
    try await request("/api/assistant/capabilities")
  }
}

/// An actual model image input. It is always selected from this run's authorized resources.
struct AssistantImageDTO: Codable, Hashable, Sendable, Equatable {
  var attachment_id: String
  var media_type: String
  var data_base64: String?
  var source_type: String
  var object_id: String
  var page: Int?
  var version: String?
}

extension CloudAPI {
  func closeTemporaryAssistantSession(_ id: String) async throws {
    let _: [String: JSONValue] = try await request("/api/assistant/conversations/\(id)/close", method: "POST")
  }
}


struct PrivateModelStepResponseDTO: Codable, Sendable {
  var request_id: String
  var state: String
  var content: String?
  var error: String?
  var retry_after: Double?
}

extension CloudAPI {
  func acknowledgeAssistantStep(runID: String, stepID: String) async throws {
    let _: [String: JSONValue] = try await request("/api/assistant/runs/\(runID)/steps/\(stepID)/ack", method: "POST")
  }
  func privateModelStep(_ body: [String: JSONValue]) async throws -> PrivateModelStepResponseDTO {
    try await request("/api/assistant/private-model", method: "POST", body: body)
  }
  func privateModelStepStatus(_ requestID: String) async throws -> PrivateModelStepResponseDTO {
    try await request("/api/assistant/private-model/\(requestID)")
  }
  func acknowledgePrivateModelStep(_ requestID: String) async throws {
    let _: [String: JSONValue] = try await request("/api/assistant/private-model/\(requestID)/ack", method: "POST")
  }
}
