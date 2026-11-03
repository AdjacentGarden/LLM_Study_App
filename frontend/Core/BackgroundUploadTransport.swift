import CryptoKit
import Foundation
import UIKit

struct BackgroundUploadIdentity: Sendable {
  var id: UUID
  var scope: String
}

/// The system owns file transfers while the app is suspended. Small response
/// receipts survive process termination until the course snapshot acknowledges them.
@MainActor final class BackgroundUploadTransport: NSObject, URLSessionDataDelegate {
  private struct Descriptor: Codable { var origin: URL; var scope: String }
  private struct Receipt: Codable {
    var body: Data
    var status: Int
    var headers: [String: String]
    var url: URL
    var errorCode: Int?

    func response() throws -> (Data, URLResponse) {
      if let errorCode { throw URLError(URLError.Code(rawValue: errorCode)) }
      guard let response = HTTPURLResponse(url: url, statusCode: status,
        httpVersion: "HTTP/1.1", headerFields: headers) else { throw URLError(.badServerResponse) }
      return (body, response)
    }
  }
  private struct Pending {
    var continuation: CheckedContinuation<(Data, URLResponse), Error>
    var progress: @MainActor @Sendable (Int64, Int64) -> Void
  }
  private static var clients: [String: BackgroundUploadTransport] = [:]
  private static let prefix = "com.cloudpath.background-upload."
  private let descriptor: Descriptor
  private let identifier: String
  private var pending: [String: Pending] = [:]
  private var bodies: [String: Data] = [:]
  private var completionHandler: (() -> Void)?
  private lazy var session: URLSession = {
    let config = URLSessionConfiguration.background(withIdentifier: identifier)
    config.isDiscretionary = false
    config.sessionSendsLaunchEvents = true
    config.waitsForConnectivity = true
    config.httpCookieStorage = nil
    config.httpShouldSetCookies = false
    config.timeoutIntervalForRequest = 900
    config.timeoutIntervalForResource = 24 * 60 * 60
    config.connectionProxyDictionary = CloudAPI.serviceSessionConfiguration(for: descriptor.origin)
      .connectionProxyDictionary
    let queue = OperationQueue()
    queue.maxConcurrentOperationCount = 1
    return URLSession(configuration: config, delegate: self, delegateQueue: queue)
  }()
  private static var directory: URL {
    FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
      .appendingPathComponent("CloudPathBackgroundTransfers", isDirectory: true)
  }
  private static func sessionID(origin: URL, scope: String) -> String {
    prefix + SHA256.hash(data: Data((origin.absoluteString + "|" + scope).utf8))
      .map { String(format: "%02x", $0) }.joined()
  }
  static func client(origin: URL, scope: String) -> BackgroundUploadTransport {
    let id = sessionID(origin: origin, scope: scope)
    if let client = clients[id] { return client }
    let client = BackgroundUploadTransport(identifier: id, descriptor: Descriptor(origin: origin, scope: scope))
    clients[id] = client
    return client
  }
  private init(identifier: String, descriptor: Descriptor) {
    self.identifier = identifier
    self.descriptor = descriptor
    super.init()
    try? FileManager.default.createDirectory(at: Self.directory, withIntermediateDirectories: true)
    if let data = try? JSONEncoder().encode(descriptor) {
      try? data.write(to: Self.directory.appendingPathComponent(identifier + ".json"),
        options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
  }
  static func handleEvents(identifier: String, completion: @escaping () -> Void) {
    guard identifier.hasPrefix(prefix),
      let data = try? Data(contentsOf: directory.appendingPathComponent(identifier + ".json")),
      let descriptor = try? JSONDecoder().decode(Descriptor.self, from: data) else {
      completion(); return
    }
    let client = client(origin: descriptor.origin, scope: descriptor.scope)
    client.completionHandler = completion
    _ = client.session
  }
  private func receiptURL(_ id: String) -> URL {
    Self.directory.appendingPathComponent(identifier + "-" + id + ".receipt")
  }
  private func receipt(_ id: String) -> Receipt? {
    (try? Data(contentsOf: receiptURL(id))).flatMap { try? JSONDecoder().decode(Receipt.self, from: $0) }
  }
  func acknowledge(_ id: UUID) { try? FileManager.default.removeItem(at: receiptURL(id.uuidString)) }

  func upload(_ request: URLRequest, file: URL, id: UUID,
    onProgress: @escaping @MainActor @Sendable (Int64, Int64) -> Void) async throws -> (Data, URLResponse) {
    let key = id.uuidString
    if let receipt = receipt(key) { return try receipt.response() }
    let tasks = await session.allTasks
    try Task.checkCancellation()
    // A delegate may have finished while getAllTasks was suspended.
    if let receipt = receipt(key) { return try receipt.response() }
    let task = tasks.first { $0.taskDescription == key }
      ?? session.uploadTask(with: request, fromFile: file)
    task.taskDescription = key
    return try await withTaskCancellationHandler {
      try await withCheckedThrowingContinuation { continuation in
        pending[key] = Pending(continuation: continuation, progress: onProgress)
        onProgress(task.countOfBytesSent, task.countOfBytesExpectedToSend)
        task.resume()
      }
    } onCancel: {
      task.cancel()
    }
  }
  func cancel(_ id: UUID) async {
    for task in await session.allTasks where task.taskDescription == id.uuidString { task.cancel() }
  }

  nonisolated func urlSession(_ session: URLSession, task: URLSessionTask,
    didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
    guard let key = task.taskDescription else { return }
    Task { @MainActor in self.pending[key]?.progress(totalBytesSent, totalBytesExpectedToSend) }
  }
  nonisolated func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
    guard let key = dataTask.taskDescription else { return }
    Task { @MainActor in self.bodies[key, default: Data()].append(data) }
  }
  nonisolated func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
    guard let key = task.taskDescription, let url = task.originalRequest?.url else { return }
    let response = task.response as? HTTPURLResponse
    Task { @MainActor in
      let headers = (response?.allHeaderFields ?? [:]).reduce(into: [String: String]()) { result, entry in
        let name = String(describing: entry.key)
        // Credentials stay in the Keychain, never in persisted transfer receipts.
        if name.lowercased() != "set-cookie" { result[name] = String(describing: entry.value) }
      }
      let receipt = Receipt(body: self.bodies.removeValue(forKey: key) ?? Data(),
        status: response?.statusCode ?? 0, headers: headers, url: url,
        errorCode: error.map { ($0 as NSError).code })
      do {
        let bytes = try JSONEncoder().encode(receipt)
        try bytes.write(to: self.receiptURL(key), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        if let context = self.pending.removeValue(forKey: key) {
          context.continuation.resume(with: Result { try receipt.response() })
        }
      } catch {
        self.pending.removeValue(forKey: key)?.continuation.resume(throwing: error)
      }
    }
  }
  nonisolated func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
    Task { @MainActor in
      let completion = self.completionHandler
      self.completionHandler = nil
      completion?()
    }
  }
}

final class CloudPathAppDelegate: NSObject, UIApplicationDelegate {
  func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String,
    completionHandler: @escaping () -> Void) {
    BackgroundUploadTransport.handleEvents(identifier: identifier, completion: completionHandler)
  }
}
