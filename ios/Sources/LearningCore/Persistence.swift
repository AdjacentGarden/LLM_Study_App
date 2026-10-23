import Foundation
import CryptoKit

public struct ServerConfiguration: Codable, Sendable, Equatable {
    public var baseURL: URL
    public var allowLocalHTTP: Bool
    public init(address: String, allowLocalHTTP: Bool = false) throws {
        guard let url = URL(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = url.scheme?.lowercased(), let host = url.host?.lowercased(),
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/" else { throw ClientError.configuration("请填写服务根地址，例如 https://study.example.com") }
        let parts = host.split(separator: ".").compactMap { Int($0) }
        let privateIP = host.split(separator: ".").count == 4 && parts.count == 4 && parts.allSatisfy { (0...255).contains($0) } &&
            (parts[0] == 10 || parts[0] == 127 || (parts[0] == 192 && parts[1] == 168) || (parts[0] == 172 && (16...31).contains(parts[1])))
        let local = privateIP || host == "localhost" || host == "::1" || host.hasSuffix(".local")
        guard scheme == "https" || (scheme == "http" && allowLocalHTTP && local) else {
            throw ClientError.configuration("请使用 HTTPS；局域网 HTTP 需要显式开启开发连接，且仅允许私网地址。")
        }
        baseURL = URL(string: "\(scheme)://\(url.host!.contains(":") ? "[\(host)]" : host)\(url.port.map { ":\($0)" } ?? "")")!
        self.allowLocalHTTP = allowLocalHTTP
    }
    public var namespace: String { SHA256.hash(data: Data(baseURL.absoluteString.utf8)).map { String(format: "%02x", $0) }.joined() }
}
public struct ImportTask: Codable, Identifiable, Sendable {
    public var id: String; public var filename: String; public var localFile: String
    public var stage: String; public var bookID: String?; public var canonicalBookID: String?
    public var progress: Double; public var message: String; public var error: String?
    public var retryable: Bool
    public var isFinished: Bool { stage == "ready" }
    public init(filename: String, localFile: String) {
        id = UUID().uuidString; self.filename = filename; self.localFile = localFile
        stage = "staged"; progress = 0; message = "PDF 已保存在本机，等待上传"; retryable = true
    }
}
public struct NoteDraft: Codable, Identifiable, Sendable {
    public var id: String; public var bookID: String; public var title: String; public var text: String
    public var recordingID: String?; public var remoteNote: Note?; public var analysisRequestID: String?
    public var remoteTextID: String?
    public var analysisJobID: String?
    public var analysisAction: String?
    public var transcriptReview: VoiceTranscriptReview?
    public var analysisSubmission: NoteAnalysisSubmission?
    public var uploadComplete: Bool
    public init(bookID: String) { id = UUID().uuidString; self.bookID = bookID; title = ""; text = ""; uploadComplete = false }
}
public struct StoredState: Codable, Sendable {
    public var schemaVersion = 1
    public var identity: String?
    public var sessions: [String: String] = [:]
    public var imports: [ImportTask] = []
    public var courseJobs: [String: CourseJob] = [:]
    public var courseRequestIDs: [String: String]? = [:]
    public var courseConflicts: [String: String]? = [:]
    public var courseValidation: [String: String]? = [:]
    public var courseSubmissionPending: [String: Bool]? = [:]
    public var bookPreparations: [String: PreparationJob]? = [:]
    public var cachedBooks: [Book]? = []
    public var bookStructures: [String: BookStructure]? = [:]
    public var pdfFiles: [String: String]? = [:]
    public var courses: [String: Course] = [:]
    public var notes: [NoteDraft] = []
    public var pendingAnswers: [String: Answer]? = [:]
    public var pendingReviews: [String: ReviewSubmission]? = [:]
    public init() {}
    /// Never combine visitor sessions from different cookie identities.
    public mutating func acceptAccount(_ account: AccountState) {
        if let identity, identity != account.user_id { sessions = [:]; courseJobs = [:]; courseRequestIDs = [:]; courseConflicts = [:]; courseValidation = [:]; courseSubmissionPending = [:]; courses = [:]; bookPreparations = [:]; cachedBooks = []; bookStructures = [:]; pendingAnswers = [:]; pendingReviews = [:] }
        identity = account.user_id
        sessions.merge(account.learning_sessions) { _, server in server }
    }
    public static func courseKey(session: String, chapter: String) -> String { session + ":" + chapter }
}
public actor DiskStore {
    public let root: URL
    private let manager = FileManager.default
    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
    }
    public func save<T: Encodable & Sendable>(_ value: T, as name: String) throws {
        let url = try safeURL(name)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        try encoder.encode(value).write(to: url, options: .atomic)
        try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        #if os(iOS)
        try manager.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: url.path)
        #endif
    }
    public func load<T: Decodable & Sendable>(_ type: T.Type, from name: String) throws -> T? {
        let url = try safeURL(name)
        guard manager.fileExists(atPath: url.path) else { return nil }
        // Corrupt state is surfaced. It must never silently create a different visitor identity.
        return try JSONDecoder().decode(type, from: Data(contentsOf: url))
    }
    public func stagePDF(from source: URL) throws -> ImportTask {
        let header = try FileHandle(forReadingFrom: source)
        defer { try? header.close() }
        guard (try header.read(upToCount: 5)) == Data("%PDF-".utf8) else { throw ClientError.configuration("请选择有效 PDF 文件") }
        let filename = UUID().uuidString + ".pdf"
        let destination = try safeURL(filename)
        try manager.copyItem(at: source, to: destination)
        #if os(iOS)
        try manager.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: destination.path)
        #endif
        return ImportTask(filename: source.lastPathComponent, localFile: filename)
    }
    public func file(_ name: String) throws -> URL { try safeURL(name) }
    private func safeURL(_ name: String) throws -> URL {
        guard !name.isEmpty, !name.contains("/"), name != ".", name != ".." else { throw ClientError.configuration("无效的本地文件名") }
        return root.appendingPathComponent(name)
    }
}
