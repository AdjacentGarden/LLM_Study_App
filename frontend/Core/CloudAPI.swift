import CryptoKit
import Foundation
import Security

struct CloudError: LocalizedError {
  let status: Int
  let message: String
  var errorDescription: String? { message }
}

/// A separate credential jar per backend origin; credentials never enter SQLite snapshots.
actor CloudAPI {
  nonisolated let baseURL: URL
  nonisolated var prefersLocalLogin: Bool {
    ["127.0.0.1", "localhost", "::1", "[::1]"].contains(baseURL.host ?? "")
  }
  private let session: URLSession
  private var cookies: [String: String]
  private let credentialKey: String

  init(baseURL: URL, session: URLSession? = nil) {
    self.baseURL = baseURL
    credentialKey =
      "cloudpath.session."
      + SHA256.hash(data: Data(baseURL.absoluteString.utf8))
      .map { String(format: "%02x", $0) }.joined()
    if let data = Self.readCredential(credentialKey) {
      cookies = (try? JSONDecoder().decode([String: String].self, from: data)) ?? [:]
    } else {
      cookies = [:]
    }
    if let session {
      self.session = session
    } else {
      let config = URLSessionConfiguration.default
      config.httpCookieStorage = nil
      config.httpShouldSetCookies = false
      config.timeoutIntervalForRequest = 180
      config.timeoutIntervalForResource = 900
      self.session = URLSession(configuration: config)
    }
  }

  nonisolated static func configured() -> CloudAPI? {
    guard !ProcessInfo.processInfo.arguments.contains("--fixture") else { return nil }
    let value =
      ProcessInfo.processInfo.environment["BOOKCOURSE_API_BASE_URL"]
      ?? UserDefaults.standard.string(forKey: "cloudpath.server") ?? "http://127.0.0.1:8100"
    guard let url = validatedURL(value) else { return nil }
    return CloudAPI(baseURL: url)
  }

  nonisolated static func validatedURL(_ value: String) -> URL? {
    guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
      ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil,
      url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
      url.path.isEmpty || url.path == "/"
    else { return nil }
    return url
  }

  func request<T: Decodable & Sendable>(
    _ path: String, method: String = "GET", body: [String: JSONValue]? = nil,
    as type: T.Type = T.self
  ) async throws -> T {
    let data = try body.map { try JSONEncoder().encode($0) }
    let (bytes, _) = try await send(
      path, method: method, data: data, contentType: "application/json")
    do { return try JSONDecoder().decode(T.self, from: bytes) } catch {
      throw CloudError(status: 0, message: "服务返回的数据格式不兼容，请检查前后端版本。")
    }
  }

  func uploadPDF(_ url: URL, filename: String = "textbook.pdf") async throws -> CloudUpload {
    let boundary = "CloudPath-" + UUID().uuidString
    let safeName = filename.replacingOccurrences(of: "\"", with: "").replacingOccurrences(
      of: "\r", with: ""
    ).replacingOccurrences(of: "\n", with: "")
    var data = Data(
      "--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\nContent-Type: application/pdf\r\n\r\n"
        .utf8)
    data.append(try Data(contentsOf: url))
    data.append(Data("\r\n--\(boundary)--\r\n".utf8))
    let (bytes, _) = try await send(
      "/api/native/uploads", method: "POST", data: data,
      contentType: "multipart/form-data; boundary=\(boundary)")
    return try JSONDecoder().decode(CloudUpload.self, from: bytes)
  }

  func downloadPDF(bookID: String) async throws -> Data {
    let (bytes, response) = try await send(
      "/api/books/\(bookID)/source", method: "GET", data: nil, contentType: nil)
    guard response.mimeType == "application/pdf",
      bytes.prefix(1024).range(of: Data("%PDF-".utf8)) != nil
    else {
      throw CloudError(status: 0, message: "原 PDF 暂不可用")
    }
    return bytes
  }

  func clearCredentials() {
    cookies = [:]
    Self.writeCredential(credentialKey, data: nil)
  }

  private func send(_ path: String, method: String, data: Data?, contentType: String?) async throws
    -> (Data, HTTPURLResponse)
  {
    guard path.hasPrefix("/api/"), let url = URL(string: path, relativeTo: baseURL) else {
      throw CloudError(status: 0, message: "服务地址无效")
    }
    var request = URLRequest(url: url)
    request.httpMethod = method
    if path.hasPrefix("/api/auth/") || path == "/api/native/local-login" {
      request.timeoutInterval = 20
    } else if path.hasSuffix("/qa") {
      request.timeoutInterval = 60
    }
    request.httpBody = data
    if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
    if !cookies.isEmpty {
      request.setValue(
        cookies.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: "; "),
        forHTTPHeaderField: "Cookie")
    }
    let (bytes, raw): (Data, URLResponse)
    do { (bytes, raw) = try await session.data(for: request) } catch is CancellationError {
      throw CancellationError()
    } catch let error as URLError where error.code == .cancelled { throw CancellationError() } catch
    { throw CloudError(status: 0, message: "无法连接学习服务，请检查服务地址和网络；已保存的进度仍然保留。") }
    guard let response = raw as? HTTPURLResponse else {
      throw CloudError(status: 0, message: "服务响应无效")
    }
    let headers = response.allHeaderFields.reduce(into: [String: String]()) { result, pair in
      result[String(describing: pair.key)] = String(describing: pair.value)
    }
    for cookie in HTTPCookie.cookies(withResponseHeaderFields: headers, for: url)
    where ["zhiwo_account", "zhiwo_visitor"].contains(cookie.name) {
      if cookie.value.isEmpty || (cookie.expiresDate.map { $0 <= Date() } ?? false) {
        cookies.removeValue(forKey: cookie.name)
      } else {
        cookies[cookie.name] = cookie.value
      }
    }
    Self.writeCredential(credentialKey, data: try? JSONEncoder().encode(cookies))
    guard (200..<300).contains(response.statusCode) else {
      let json = try? JSONDecoder().decode([String: JSONValue].self, from: bytes)
      let message =
        json?["detail"]?.stringValue
        ?? (response.statusCode == 401 ? "登录已过期，请重新登录" : "服务暂时无法完成请求（\(response.statusCode)）")
      throw CloudError(status: response.statusCode, message: message)
    }
    return (bytes, response)
  }

  private nonisolated static func readCredential(_ key: String) -> Data? {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: "com.cloudpath.backend", kSecAttrAccount as String: key,
      kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var result: CFTypeRef?
    if SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess {
      return result as? Data
    }
    #if DEBUG && targetEnvironment(simulator)
      // Unsigned Simulator builds do not have a Keychain access group.
      return try? Data(contentsOf: simulatorCredentialURL(key))
    #else
      return nil
    #endif
  }

  private nonisolated static func writeCredential(_ key: String, data: Data?) {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: "com.cloudpath.backend", kSecAttrAccount as String: key,
    ]
    guard let data else {
      SecItemDelete(query as CFDictionary)
      #if DEBUG && targetEnvironment(simulator)
        try? FileManager.default.removeItem(at: simulatorCredentialURL(key))
      #endif
      return
    }
    let attributes: [String: Any] = [kSecValueData as String: data]
    var result = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
    if result == errSecItemNotFound {
      var insert = query
      insert[kSecValueData as String] = data
      insert[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
      result = SecItemAdd(insert as CFDictionary, nil)
    }
    #if DEBUG && targetEnvironment(simulator)
      if result != errSecSuccess {
        let url = simulatorCredentialURL(key)
        try? FileManager.default.createDirectory(
          at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(
          to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
      }
    #endif
  }

  #if DEBUG && targetEnvironment(simulator)
    private nonisolated static func simulatorCredentialURL(_ key: String) -> URL {
      FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("CloudPathSimulatorCredentials", isDirectory: true)
        .appendingPathComponent(key)
    }
  #endif
}

struct CloudOK: Codable, Sendable { var ok: Bool }
struct CloudUpload: Codable, Sendable {
  var book_id: String
  var filename: String
  var status: String
}
struct CloudAccountState: Codable, Sendable {
  struct Account: Codable, Sendable {
    var email: String
    var stage: String
    var interests: [String]
    var goal: String
    var is_demo: Bool
  }
  var account: Account?
  var user_id: String
  var email_available: Bool
  var demo_available: Bool
}
struct CloudChallenge: Codable, Sendable {
  var challenge_id: String
  var expires_in: Int
  var retry_after: Int
  var delivery: String
  var message: String
  var demo_code: String?
}
struct CloudBookStatus: Codable, Sendable {
  var book_id: String
  var status: String
  var progress: Double
  var current_step: String
  var retryable: Bool
  var needs_human_review: Bool
}
struct CloudHealth: Decodable, Sendable {
  var ocr_worker_enabled: Bool
  var ocr_worker_alive: Bool
}
struct CloudBootstrap: Codable, Sendable {
  struct Selection: Codable, Sendable, Equatable {
    var workspace_id: String
    var resource_id: String?
  }
  var workspaces: [CloudWorkspace]
  var selection: Selection?
  var contract_version: Int
  var library: [CloudLibraryBook]?
  var learner_preferences: LearnerPreferences?
  var catalog: [CommunityBook]?
}
struct CloudLibraryBook: Codable, Sendable {
  var book_id: String
  var title: String
  var status: String
  var cover_url: String?
  var structure: CloudStructure?
}
struct CloudWorkspace: Codable, Sendable, Identifiable {
  var id: String
  var name: String
  var preferences: [String: [String]]
  var created: Double
  var updated: Double
  var resources: [CloudResource]
}
struct CloudResource: Codable, Sendable, Identifiable {
  var id: String
  var book_id: String
  var workspace_id: String
  var revision: Int
  var session_id: String?
  var filename: String
  var status: String
  var structure: CloudStructure?
  var courses: [CloudCourse]
  var progress: CloudProgress
  var source_content: SourceContent? = nil
  var path: String { "/api/workspaces/\(workspace_id)/resources/\(id)" }
}
struct CloudStructure: Codable, Sendable {
  var title: String
  var summary: String
  var source_page_count: Int
  var chapters: [CloudChapter]
}
struct CloudChapter: Codable, Sendable {
  var chapter_id: String
  var order: Int
  var title: String
  var start_page: Int
  var end_page: Int
  var summary: String
  var knowledge_points: [String]
  var source_block_ids: [String]
}
struct CloudProgress: Codable, Sendable, Equatable {
  var revision = 0
  var page = 1
  var chapter_id: String?
  var lesson_id: String?
  var completed_chapters: [String] = []
  var task_status: [String: String] = [:]
  var mastered_ids: [String] = []
  func body(mutationID: String) -> [String: JSONValue] {
    [
      "revision": .number(Double(revision)), "mutation_id": .string(mutationID),
      "page": .number(Double(page)),
      "chapter_id": chapter_id.map(JSONValue.string) ?? .null,
      "lesson_id": lesson_id.map(JSONValue.string) ?? .null,
      "completed_chapters": .array(completed_chapters.map(JSONValue.string)),
      "task_status": .object(task_status.mapValues(JSONValue.string)),
      "mastered_ids": .array(mastered_ids.map(JSONValue.string)),
    ]
  }
}
struct CloudCitation: Codable, Sendable {
  var page_number: Int
  var block_id: String?
  var quote: String
}
struct CloudSection: Codable, Sendable {
  var title: String
  var content: String
  var purpose: String
  var citations: [CloudCitation]
}
struct CloudKnowledge: Codable, Sendable {
  var point_id: String
  var title: String
  var explanation: String
  var mastery: Double
  var citations: [CloudCitation]
}
struct CloudFlashcard: Codable, Sendable {
  var card_id: String
  var front: String
  var back: String
  var reason_for_user: String
  var point_id: String
  var citations: [CloudCitation]
}
struct CloudPractice: Codable, Sendable {
  var item_id: String
  var point_id: String
  var prompt: String
  var response_type: String
  var options: [String]
  var estimated_seconds: Int
}
struct CloudCourse: Codable, Sendable {
  var course_id: String
  var version: Int
  var chapter_id: String
  var chapter_title: String
  var opening: String
  var summary: String
  var original_reading: [CloudSection]
  var knowledge_points: [CloudKnowledge]
  var flashcards: [CloudFlashcard]
  var worked_examples: [CloudSection]
  var checkpoint_questions: [String]
  var practice_items: [CloudPractice]
  var estimated_minutes: Int
  var created_at: String
  var unresolved_source_warnings: [String]
}
struct CloudInterview: Codable, Sendable {
  var session_id: String
  var phase: String
  var turn: CloudTurn
  var profile: JSONValue
}
struct CloudTurn: Codable, Sendable {
  struct Option: Codable, Sendable, Identifiable {
    var id: String
    var label: String
  }
  struct Item: Codable, Sendable {
    var item_id: String
    var prompt: String
    var response_type: String
    var options: [String]
  }
  var turn_id: String
  var phase: String
  var message: String
  var question: String?
  var response_type: String?
  var options: [Option]
  var item: Item?
  var why_asked: String?
  var progress: Double
}
struct CloudDiagnosticResult: Codable, Sendable {
  var evidence: JSONValue
  var next_turn: CloudTurn
  var profile: JSONValue
}
struct CloudQA: Codable, Sendable {
  struct Claim: Codable, Sendable {
    var text: String
    var citations: [CloudCitation]
  }
  var book_id: String
  var status: String
  var answer: String
  var claims: [Claim]
  var confidence: Double
}
struct CloudPracticeAnswer: Sendable {
  var question: QuizQuestion
  var answer: String
  var eventID: String
  var seconds: Double
  var confidence: Double
}
struct CloudPracticeFeedback: Codable, Sendable {
  struct Evidence: Codable, Sendable {
    var score: Double
    var misconception_candidates: [String]
    var missing_rubric: [String]
    var needs_follow_up: Bool
  }
  var duplicate: Bool
  var evidence: Evidence
}
struct CloudLearningRecords: Decodable, Sendable {
  struct Evidence: Decodable, Sendable {
    var id: String
    var title: String
    var kind: String
    var chapter: String
    var score: Double
    var at: String
    var answer: String?
    var pages: [Int]
  }
  var book_id: String
  var evidence: [Evidence]
}
