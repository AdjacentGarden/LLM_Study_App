import CryptoKit
import Foundation
import ImageIO
import Security

struct CloudError: LocalizedError {
  let status: Int
  let message: String
  var transportFailure = false
  var code: String? = nil
  static func learningMessage(_ message: String) -> String {
    let internalDetails = ["章节边界", "待核验", "OCR 页面", "OCR页面", "引用无法核验", "原文超过",
      "原文上下文不完整", "该章证据不足", "检索或诊断内容尚未全部就绪", "原文审核", "完整性与原文审核"]
    if internalDetails.contains(where: { message.contains($0) }) {
      return "课程内容暂未准备完成，已保留进度，请稍后继续。"
    }
    return message
  }
  var errorDescription: String? { Self.learningMessage(message) }
}

struct CloudUploadProgress: Sendable {
  var sentBytes: Int64 = 0
  var totalBytes: Int64 = 0
  var fraction: Double {
    totalBytes > 0 ? min(1, max(0, Double(sentBytes) / Double(totalBytes))) : 0
  }
}

/// URLSession reports bytes sent, not server acceptance. Completion still requires a response.
private final class CloudUploadObserver: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
  private let lock = NSLock()
  private var lastActivity = ContinuousClock.now
  private var lastSent: Int64 = 0
  private weak var task: URLSessionTask?
  private var stalled = false
  private let headerBytes: Int64
  private let fileBytes: Int64
  private let onProgress: @MainActor @Sendable (CloudUploadProgress) -> Void

  init(
    headerBytes: Int64, fileBytes: Int64,
    onProgress: @escaping @MainActor @Sendable (CloudUploadProgress) -> Void
  ) {
    self.headerBytes = headerBytes
    self.fileBytes = fileBytes
    self.onProgress = onProgress
  }

  func urlSession(
    _ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
    totalBytesSent: Int64, totalBytesExpectedToSend: Int64
  ) {
    lock.lock()
    self.task = task
    if totalBytesSent > lastSent {
      lastSent = totalBytesSent
      lastActivity = .now
    }
    lock.unlock()
    let progress = CloudUploadProgress(
      sentBytes: min(fileBytes, max(0, totalBytesSent - headerBytes)), totalBytes: fileBytes)
    Task { @MainActor in onProgress(progress) }
  }

  func cancelIfStalled() {
    lock.lock()
    let overdue = ContinuousClock.now - lastActivity >= .seconds(90)
    let pending = overdue ? task : nil
    if pending != nil { stalled = true }
    lock.unlock()
    pending?.cancel()
  }

  var didStall: Bool {
    lock.lock()
    defer { lock.unlock() }
    return stalled
  }
}

private final class CoverRedirectPolicy: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
  func urlSession(_ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping (URLRequest?) -> Void) {
    completionHandler(nil)
  }
}

/// A separate credential jar per backend origin; credentials never enter SQLite snapshots.
actor CloudAPI {
  nonisolated static let defaultServer = "http://10.249.44.137:8100"
  nonisolated let baseURL: URL
  nonisolated var prefersLocalLogin: Bool {
    ["127.0.0.1", "localhost", "::1", "[::1]"].contains(baseURL.host ?? "")
  }
  private let session: URLSession
  private var cookies: [String: String]
  nonisolated let cacheOrigins: [String]
  private let credentialKeys: [String]
  private let credentialKey: String
  private let coverCache = NSCache<NSString, NSData>()

  init(baseURL: URL, session: URLSession? = nil) {
    let normalized = Self.normalizedURL(baseURL)
    self.baseURL = normalized
    cacheOrigins = Self.compatibleOrigins(baseURL)
    credentialKeys = cacheOrigins.map(Self.credentialKeyForOrigin)
    credentialKey = Self.credentialKeyForOrigin(normalized.absoluteString)
    if let data = credentialKeys.compactMap({ Self.readCredential($0) }).first {
      cookies = (try? JSONDecoder().decode([String: String].self, from: data)) ?? [:]
    } else { cookies = [:] }
    if let session {
      self.session = session
    } else {
      self.session = URLSession(configuration: Self.serviceSessionConfiguration(for: normalized))
    }
    coverCache.countLimit = 64
    coverCache.totalCostLimit = 32 * 1024 * 1024
  }

  nonisolated static func serviceSessionConfiguration(for url: URL) -> URLSessionConfiguration {
    let config = URLSessionConfiguration.default
    config.httpCookieStorage = nil
    config.httpShouldSetCookies = false
    config.timeoutIntervalForRequest = 180
    config.timeoutIntervalForResource = 900
    let host = url.host?.lowercased() ?? ""
    let octets = host.split(separator: ".", omittingEmptySubsequences: false)
    let numbers = octets.compactMap { UInt8($0) }
    let tailnetAddress = octets.count == 4 && numbers.count == 4
      && numbers[0] == 100 && (64...127).contains(numbers[1])
    // Tailnet IPv4 addresses and MagicDNS names must follow the device's VPN
    // route, including when its system HTTP proxy cannot reach that network.
    if tailnetAddress || host.hasSuffix(".ts.net") { config.connectionProxyDictionary = [:] }
    return config
  }

  private nonisolated static func credentialKeyForOrigin(_ origin: String) -> String {
    "cloudpath.session." + SHA256.hash(data: Data(origin.utf8)).map { String(format: "%02x", $0) }.joined()
  }

  nonisolated static func compatibleOrigins(_ url: URL) -> [String] {
    let normalized = normalizedURL(url)
    var candidates = [normalized.absoluteString, url.absoluteString, normalized.absoluteString + "/"]
    if normalized.port == nil {
      var parts = URLComponents(url: normalized, resolvingAgainstBaseURL: false)!
      parts.port = parts.scheme == "https" ? 443 : 80
      let defaultPort = parts.url!.absoluteString
      candidates += [defaultPort, defaultPort + "/"]
    }
    var values: [String] = []
    for value in candidates where !values.contains(value) { values.append(value) }
    return values
  }

  #if DEBUG
  nonisolated static func seedLegacyIdentityForTesting(url: URL, account: CloudAccountState) {
    let key = credentialKeyForOrigin(url.absoluteString)
    writeCredential(key, data: try? JSONEncoder().encode(["zhiwo_account": "test-credential"]))
    writeCredential(key + ".identity", data: try? JSONEncoder().encode(account))
  }
  #endif

  nonisolated static func configured() -> CloudAPI? {
    guard !ContentBundle.fixtureMode else { return nil }
    let value =
      ProcessInfo.processInfo.environment["BOOKCOURSE_API_BASE_URL"]
      ?? UserDefaults.standard.string(forKey: "cloudpath.server") ?? defaultServer
    guard let url = validatedURL(value) else { return nil }
    return CloudAPI(baseURL: url)
  }

  nonisolated static func validatedURL(_ value: String) -> URL? {
    guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
      ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil,
      url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
      url.path.isEmpty || url.path == "/"
    else { return nil }
    return normalizedURL(url)
  }

  nonisolated static func normalizedURL(_ url: URL) -> URL {
    var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
    parts.scheme = parts.scheme?.lowercased()
    parts.host = parts.host?.lowercased()
    if (parts.scheme == "http" && parts.port == 80) || (parts.scheme == "https" && parts.port == 443) { parts.port = nil }
    parts.path = ""
    return parts.url!
  }

  func verifiedAccount() -> CloudAccountState? {
    guard cookies["zhiwo_account"] != nil, let data = credentialKeys.compactMap({ Self.readCredential($0 + ".identity") }).first else { return nil }
    return try? JSONDecoder().decode(CloudAccountState.self, from: data)
  }

  func rememberAccount(_ value: CloudAccountState) {
    Self.writeCredential(credentialKey + ".identity", data: try? JSONEncoder().encode(value))
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

  func uploadPDF(
    _ url: URL, filename: String = "textbook.pdf",
    onProgress: @escaping @MainActor @Sendable (CloudUploadProgress) -> Void = { _ in }
  ) async throws -> CloudUpload {
    let boundary = "CloudPath-" + UUID().uuidString
    let safeName = filename.replacingOccurrences(of: "\"", with: "").replacingOccurrences(
      of: "\r", with: ""
    ).replacingOccurrences(of: "\n", with: "")
    let header = Data(
      "--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\nContent-Type: application/pdf\r\n\r\n"
        .utf8)
    let bodyURL = FileManager.default.temporaryDirectory
      .appendingPathComponent("cloudpath-upload-\(UUID().uuidString).multipart")
    defer { try? FileManager.default.removeItem(at: bodyURL) }
    guard FileManager.default.createFile(atPath: bodyURL.path, contents: nil) else {
      throw CloudError(status: 0, message: "无法准备上传文件，请检查本机剩余空间。")
    }
    let reader = try FileHandle(forReadingFrom: url)
    defer { try? reader.close() }
    let writer = try FileHandle(forWritingTo: bodyURL)
    defer { try? writer.close() }
    try writer.write(contentsOf: header)
    var fileBytes: Int64 = 0
    while let chunk = try reader.read(upToCount: 1024 * 1024), !chunk.isEmpty {
      try Task.checkCancellation()
      try writer.write(contentsOf: chunk)
      fileBytes += Int64(chunk.count)
    }
    try writer.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
    try writer.close()
    try Task.checkCancellation()
    await onProgress(CloudUploadProgress(totalBytes: fileBytes))
    let observer = CloudUploadObserver(
      headerBytes: Int64(header.count), fileBytes: fileBytes, onProgress: onProgress)
    let watchdog = Task {
      while !Task.isCancelled {
        do { try await Task.sleep(for: .seconds(5)) } catch { return }
        observer.cancelIfStalled()
      }
    }
    defer { watchdog.cancel() }
    let (bytes, _) = try await send(
      "/api/native/uploads", method: "POST", data: nil,
      contentType: "multipart/form-data; boundary=\(boundary)",
      uploadFile: bodyURL, uploadObserver: observer)
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

  nonisolated static func isBookCoverPath(_ path: String) -> Bool {
    guard let parts = URLComponents(string: path), parts.scheme == nil, parts.host == nil,
      parts.fragment == nil,
      parts.percentEncodedPath.range(of: #"^/api/books/[A-Za-z0-9_-]{1,160}/cover$"#,
        options: .regularExpression) != nil else { return false }
    let query = parts.queryItems ?? []
    return query.isEmpty || (query.count == 1 && query[0].name == "v"
      && query[0].value?.range(of: #"^[A-Za-z0-9_-]{1,160}$"#, options: .regularExpression) != nil)
  }

  func downloadBookCover(_ path: String, accountID: String, epoch: UUID) async throws -> Data {
    guard Self.isBookCoverPath(path) else { throw CloudError(status: 422, message: "教材封面地址无效") }
    guard let identity = verifiedAccount(), identity.account != nil, identity.user_id == accountID else {
      throw CloudError(status: 401, message: "请登录后查看教材封面")
    }
    // The API instance belongs to one origin; both account and UI identity epoch
    // must match before cached bytes may be reused.
    let key = (accountID + "|" + epoch.uuidString + "|" + path) as NSString
    if let cached = coverCache.object(forKey: key) { return cached as Data }
    let (bytes, response) = try await send(path, method: "GET", data: nil, contentType: nil,
      privateAsset: true)
    let jpeg = response.mimeType == "image/jpeg" && bytes.starts(with: [0xff, 0xd8, 0xff])
    let png = response.mimeType == "image/png" && bytes.starts(with: [137, 80, 78, 71, 13, 10, 26, 10])
    guard bytes.count <= 5 * 1024 * 1024, jpeg || png,
      let source = CGImageSourceCreateWithData(bytes as CFData, nil), CGImageSourceGetCount(source) == 1,
      let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
      let width = properties[kCGImagePropertyPixelWidth] as? Int,
      let height = properties[kCGImagePropertyPixelHeight] as? Int,
      width > 0, height > 0, width <= 8192, height <= 8192, width * height <= 16_000_000 else {
      throw CloudError(status: 422, message: "教材封面暂不可用")
    }
    try Task.checkCancellation()
    guard verifiedAccount()?.user_id == accountID else { throw CancellationError() }
    coverCache.setObject(bytes as NSData, forKey: key, cost: bytes.count)
    return bytes
  }

  func uploadStudioAudio(noteID: String, revision: Int, duration: Double, fileURL: URL) async throws -> Data {
    guard Self.validStudioID(noteID), revision >= 1, duration.isFinite, (0...602).contains(duration) else {
      throw CloudError(status: 422, message: "录音信息不完整，原始文件仍保留在本机。")
    }
    let values = try fileURL.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
    guard fileURL.isFileURL, values.isRegularFile == true, let size = values.fileSize,
      size >= 32, size <= 20 * 1024 * 1024 else {
      throw CloudError(status: 413, message: "录音必须为有效且小于 20 MB 的本机文件。")
    }
    let mime = fileURL.pathExtension.lowercased() == "wav" ? "audio/wav" : "audio/mp4"
    let (bytes, _) = try await send(
      "/api/studio/notes/\(noteID)/audio", method: "PUT", data: nil, contentType: mime,
      headers: ["X-Note-Revision": String(revision), "X-Audio-Duration": String(duration)],
      uploadFile: fileURL)
    return bytes
  }

  func downloadStudioAsset(jobID: String, kind: String? = nil) async throws -> Data {
    guard Self.validStudioID(jobID) else { throw CloudError(status: 422, message: "媒体任务标识无效") }
    let (bytes, response) = try await send(
      "/api/studio/jobs/\(jobID)/asset", method: "GET", data: nil, contentType: nil)
    let jpeg = response.mimeType == "image/jpeg" && bytes.starts(with: [0xff, 0xd8, 0xff])
    let png = response.mimeType == "image/png" && bytes.starts(with: [137, 80, 78, 71, 13, 10, 26, 10])
    let video = response.mimeType == "video/mp4" && bytes.count > 12
      && bytes.subdata(in: 4..<8) == Data("ftyp".utf8)
    let valid = kind == "image" ? jpeg || png : kind == "video" ? video : jpeg || png || video
    guard valid, bytes.count <= 60_000_000 else {
      throw CloudError(status: 422, message: "服务未返回有效媒体文件，请稍后查询原任务。")
    }
    return bytes
  }

  private nonisolated static func validStudioID(_ value: String) -> Bool {
    !value.isEmpty && value.count <= 100 && value.unicodeScalars.allSatisfy {
      CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-").contains($0)
    }
  }

  func clearCredentials() {
    cookies = [:]
    coverCache.removeAllObjects()
    for key in credentialKeys {
      Self.writeCredential(key, data: nil)
      Self.writeCredential(key + ".identity", data: nil)
    }
  }

  private func send(
    _ path: String, method: String, data: Data?, contentType: String?,
    headers: [String: String] = [:], uploadFile: URL? = nil,
    uploadObserver: CloudUploadObserver? = nil, privateAsset: Bool = false
  ) async throws
    -> (Data, HTTPURLResponse)
  {
    guard path.hasPrefix("/api/"), let url = URL(string: path, relativeTo: baseURL) else {
      throw CloudError(status: 0, message: "服务地址无效")
    }
    var request = URLRequest(url: url)
    if privateAsset { request.cachePolicy = .reloadIgnoringLocalCacheData }
    request.httpMethod = method
    if uploadFile != nil {
      request.timeoutInterval = 60
    } else if path.hasPrefix("/api/auth/") || path == "/api/native/local-login" {
      // A relayed tailnet connection can spend most of the normal auth budget
      // establishing TCP and TLS before the server receives the request.
      request.timeoutInterval = baseURL.host?.lowercased().hasSuffix(".ts.net") == true ? 60 : 20
    } else if path.contains("/course-jobs")
      || path.hasSuffix("/prepare") || path.contains("/prepare?")
      || path.hasSuffix("/readiness") || path.hasSuffix("/status")
      || (method == "GET" && path.hasSuffix("/diagnostics")) {
      request.timeoutInterval = 20
    } else if path.hasSuffix("/qa") {
      request.timeoutInterval = 60
    }
    if uploadFile == nil { request.httpBody = data }
    if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
    for (key, value) in headers { request.setValue(value, forHTTPHeaderField: key) }
    if !cookies.isEmpty {
      request.setValue(
        cookies.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: "; "),
        forHTTPHeaderField: "Cookie")
    }
    let (bytes, raw): (Data, URLResponse)
    // Catalog imports reuse a caller-supplied workspace ID, so a transport retry
    // cannot create a second course. Other writes must never be replayed here.
    let canRetry = baseURL.host?.lowercased().hasSuffix(".ts.net") == true
      && (method == "GET" || (method == "POST" && path.hasPrefix("/api/native/catalog/") && path.hasSuffix("/import")))
    do {
      if let uploadFile {
        (bytes, raw) = try await session.upload(
          for: request, fromFile: uploadFile, delegate: uploadObserver)
      } else {
        (bytes, raw) = try await load(request, canRetry: canRetry, allowRedirects: !privateAsset)
      }
    } catch where uploadObserver?.didStall == true {
      throw CloudError(
        status: 0, message: "上传中断：网络或服务器连续 90 秒没有新的进展，请检查连接后重试。",
        transportFailure: true)
    } catch let error as URLError where uploadFile != nil && error.code == .timedOut {
      throw CloudError(
        status: 0, message: "文件上传超时，请检查网络后重试。原文件仍保留在本机。",
        transportFailure: true)
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as URLError where error.code == .cancelled { throw CancellationError()
    } catch let error as URLError where error.code == .appTransportSecurityRequiresSecureConnection {
      throw CloudError(status: 0, message: "系统阻止了此地址的 HTTP 连接，请使用已配置的局域网或 VPN 服务地址，或改用 HTTPS。", transportFailure: true)
    } catch
    { throw CloudError(status: 0, message: "无法连接学习服务，请检查服务地址和网络；已保存的进度仍然保留。", transportFailure: true) }
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
      throw CloudError(status: response.statusCode, message: CloudError.learningMessage(message),
        code: response.value(forHTTPHeaderField: "X-CloudPath-Error-Code"))
    }
    return (bytes, response)
  }

  private func load(_ request: URLRequest, canRetry: Bool, allowRedirects: Bool) async throws -> (Data, URLResponse) {
    let delegate: URLSessionTaskDelegate? = allowRedirects ? nil : CoverRedirectPolicy()
    do { return try await session.data(for: request, delegate: delegate) }
    catch let error as URLError where canRetry
      && [.timedOut, .networkConnectionLost, .cannotConnectToHost].contains(error.code) {
      try Task.checkCancellation()
      return try await session.data(for: request, delegate: delegate)
    }
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
  var flashcard_reviews: [String: CloudReviewState]? = nil
  var content_key: String? = nil
  var content_path: String? = nil
  var session_purpose: String? = nil
  var cover_url: String? = nil
  var isPreview: Bool { session_purpose == "preview" }
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
  var purpose: String? = nil
  var isPreview: Bool { purpose == "preview" }
  var canGenerateCourse: Bool {
    ((purpose == nil || purpose == "learning") && phase == "complete")
      || (isPreview && phase == "preview_ready")
  }
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
struct CloudReviewState: Codable, Sendable {
  var algorithm: String
  var repetitions: Int
  var due_at: String?
  var last_rating: String?
}
struct CloudReviewResult: Decodable, Sendable {
  var duplicate: Bool
  var review_state: CloudReviewState
}

struct CloudQA: Codable, Sendable {
  struct Claim: Codable, Sendable {
    var book_id: String? = nil
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
