import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum ClientError: Error, LocalizedError, Sendable {
    case configuration(String), http(Int, String), invalidResponse(String), network(String)
    public var errorDescription: String? {
        switch self {
        case .configuration(let text), .invalidResponse(let text): return text
        case .http(let status, let text): return "\(text)（HTTP \(status)）"
        case .network(let text): return "连接中断：\(text)。本地进度已保留，可重新连接。"
        }
    }
}
public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
    func sendFile(_ request: URLRequest, file: URL) async throws -> (Data, HTTPURLResponse)
}
public extension HTTPTransport {
    func sendFile(_ request: URLRequest, file: URL) async throws -> (Data, HTTPURLResponse) {
        var request = request; request.httpBody = try Data(contentsOf: file); return try await send(request)
    }
}
private final class NoRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil) // Never forward the visitor cookie to a redirect target.
    }
}
public final class SessionTransport: HTTPTransport, @unchecked Sendable {
    private let session: URLSession
    public init(configuration: URLSessionConfiguration = .default) {
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        // API session is shared with authenticated audio and PDF downloads.
        session = URLSession(configuration: configuration, delegate: NoRedirectDelegate(), delegateQueue: nil)
    }
    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw ClientError.invalidResponse("服务没有返回 HTTP 响应") }
        return (data, response)
    }
    public func sendFile(_ request: URLRequest, file: URL) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.upload(for: request, fromFile: file)
        guard let response = response as? HTTPURLResponse else { throw ClientError.invalidResponse("服务没有返回 HTTP 响应") }
        return (data, response)
    }
}
public struct CookieRecord: Codable, Sendable {
    public var name: String; public var value: String; public var domain: String; public var path: String
    public var secure: Bool; public var expires: Date?
    init(_ cookie: HTTPCookie) { name = cookie.name; value = cookie.value; domain = cookie.domain; path = cookie.path; secure = cookie.isSecure; expires = cookie.expiresDate }
    func cookie() -> HTTPCookie? {
        // Foundation treats a present, nonempty secure property as true, including "FALSE".
        var properties: [HTTPCookiePropertyKey: Any] = [.name: name, .value: value, .domain: domain, .path: path]
        if secure { properties[.secure] = "TRUE" }
        if let expires { properties[.expires] = expires }
        return HTTPCookie(properties: properties)
    }
}
public actor APIClient {
    public let configuration: ServerConfiguration
    private let transport: any HTTPTransport
    private let store: DiskStore
    // Explicit cookie archive also preserves the visitor's session cookies across launches.
    private var cookies: [CookieRecord] = []
    public init(configuration: ServerConfiguration, store: DiskStore, transport: any HTTPTransport = SessionTransport()) {
        self.configuration = configuration; self.store = store; self.transport = transport
    }
    public func restoreCookies() async throws { cookies = try await store.load([CookieRecord].self, from: "cookies.json") ?? [] }
    public func url(_ path: String) throws -> URL {
        guard path.hasPrefix("/api/"), !path.contains(".."), let value = URL(string: path, relativeTo: configuration.baseURL)?.absoluteURL,
              value.host == configuration.baseURL.host, value.scheme == configuration.baseURL.scheme, value.port == configuration.baseURL.port else {
            throw ClientError.configuration("拒绝无效的服务地址")
        }
        return value
    }
    public func request<T: Decodable & Sendable>(_ type: T.Type, path: String, method: String = "GET", body: Data? = nil, headers: [String: String] = [:], timeout: Double = 45) async throws -> T {
        let data = try await raw(path: path, method: method, body: body, headers: headers, timeout: timeout)
        do { return try JSONDecoder().decode(type, from: data) }
        catch { throw ClientError.invalidResponse("服务数据格式不兼容：\(String(describing: type))。请检查服务版本。") }
    }
    public func raw(path: String, method: String = "GET", body: Data? = nil, headers: [String: String] = [:], timeout: Double = 45, bodyFile: URL? = nil) async throws -> Data {
        let target = try url(path)
        var request = URLRequest(url: target, timeoutInterval: timeout)
        request.httpMethod = method; request.httpBody = body; request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil || bodyFile != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        for (key, value) in headers { request.setValue(value, forHTTPHeaderField: key) }
        let now = Date()
        let active = cookies.compactMap { $0.cookie() }.filter { ($0.expiresDate ?? .distantFuture) > now && (!$0.isSecure || target.scheme == "https") }
            .filter { cookie in
                let domain = cookie.domain.hasPrefix(".") ? String(cookie.domain.dropFirst()) : cookie.domain
                return (target.host == domain || target.host?.hasSuffix("." + domain) == true) && target.path.hasPrefix(cookie.path)
            }
        if !active.isEmpty { request.setValue(HTTPCookie.requestHeaderFields(with: active)["Cookie"], forHTTPHeaderField: "Cookie") }
        let data: Data; let response: HTTPURLResponse
        do {
            if let bodyFile { (data, response) = try await transport.sendFile(request, file: bodyFile) }
            else { (data, response) = try await transport.send(request) }
        }
        catch is CancellationError { throw CancellationError() }
        catch { throw ClientError.network(error.localizedDescription) }
        var fields: [String: String] = [:]
        for (key, value) in response.allHeaderFields { fields[String(describing: key)] = String(describing: value) }
        let fresh = HTTPCookie.cookies(withResponseHeaderFields: fields, for: target)
        for cookie in fresh {
            cookies.removeAll { $0.name == cookie.name && $0.domain == cookie.domain && $0.path == cookie.path }
            if (cookie.expiresDate ?? .distantFuture) > now { cookies.append(CookieRecord(cookie)) }
        }
        if !fresh.isEmpty { try await store.save(cookies, as: "cookies.json") }
        guard (200..<300).contains(response.statusCode) else {
            let envelope = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            let message = envelope?["detail"] as? String ?? "服务暂时无法完成请求"
            throw ClientError.http(response.statusCode, message)
        }
        return data
    }
    private func encoded<T: Encodable>(_ value: T) throws -> Data { try JSONEncoder().encode(value) }
    private func segment(_ value: String) -> String { value.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-_"))) ?? "" }
    public func account() async throws -> AccountState { try await request(AccountState.self, path: "/api/auth/me", timeout: 20) }
    public func checkHealth() async throws -> HealthStatus { try await request(HealthStatus.self, path: "/api/health", timeout: 15) }
    public func readiness() async throws -> Readiness { try await request(Readiness.self, path: "/api/readiness", timeout: 15) }
    public func sourcePDF(_ book: String) async throws -> Data {
        let data = try await raw(path: "/api/library/books/\(segment(book))/source", timeout: 180)
        guard data.starts(with: Data("%PDF-".utf8)) else { throw ClientError.invalidResponse("服务器没有返回有效的原书 PDF") }
        return data
    }
    public func sourcePDFHeader(_ book: String) async throws -> Data {
        try await raw(path: "/api/library/books/\(segment(book))/source", headers: ["Range": "bytes=0-4"], timeout: 30)
    }
    public func books() async throws -> [Book] { try await request([Book].self, path: "/api/library", timeout: 30) }
    public func structure(_ book: String) async throws -> BookStructure { try await request(BookStructure.self, path: "/api/books/\(segment(book))/structure") }
    public func uploadPDF(file: URL, filename: String) async throws -> UploadedBook {
        // Foreground transfer is restartable from the staged file. It is not a background URLSession transfer.
        let boundary = "CloudPath-" + UUID().uuidString
        let safeName = filename.replacingOccurrences(of: "\"", with: "_").replacingOccurrences(of: "\r", with: "_").replacingOccurrences(of: "\n", with: "_")
        let multipart = try await store.file("multipart-" + UUID().uuidString + ".body")
        guard FileManager.default.createFile(atPath: multipart.path, contents: nil) else { throw ClientError.configuration("无法创建上传暂存文件") }
        defer { try? FileManager.default.removeItem(at: multipart) }
        let output = try FileHandle(forWritingTo: multipart); let input = try FileHandle(forReadingFrom: file)
        do {
            try output.write(contentsOf: Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\nContent-Type: application/pdf\r\n\r\n".utf8))
            while let chunk = try input.read(upToCount: 1_048_576), !chunk.isEmpty { try output.write(contentsOf: chunk) }
            try output.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8)); try output.close(); try input.close()
        } catch { try? output.close(); try? input.close(); throw error }
        let data = try await raw(path: "/api/books", method: "POST", headers: ["Content-Type": "multipart/form-data; boundary=\(boundary)"], timeout: 600, bodyFile: multipart)
        do { return try JSONDecoder().decode(UploadedBook.self, from: data) }
        catch { throw ClientError.invalidResponse("上传响应格式不兼容，本机 PDF 已保留。") }
    }
    public func bind(_ book: String) async throws { let _: OK = try await request(OK.self, path: "/api/library/books/\(segment(book))/bind-upload", method: "POST") }
    public func process(_ book: String, retry: Bool = false) async throws -> BookStatus { try await request(BookStatus.self, path: "/api/books/\(segment(book))/process\(retry ? "/retry" : "")", method: "POST") }
    public func bookStatus(_ book: String) async throws -> BookStatus { try await request(BookStatus.self, path: "/api/books/\(segment(book))/status", timeout: 30) }
    public func prepare(_ book: String, retry: Bool = false) async throws -> PreparationJob { try await request(PreparationJob.self, path: "/api/books/\(segment(book))/prepare\(retry ? "?retry=true" : "")", method: "POST") }
    public func preparation(_ book: String) async throws -> PreparationJob { try await request(PreparationJob.self, path: "/api/books/\(segment(book))/prepare") }
    public func claim(_ book: String) async throws -> Book { try await request(Book.self, path: "/api/library/books/\(segment(book))/claim", method: "POST") }
    public func start(book: String, user: String) async throws -> Interview { try await request(Interview.self, path: "/api/interviews/start", method: "POST", body: encoded(["book_id": book, "user_id": user])) }
    public func resume(_ session: String) async throws -> Interview { try await request(Interview.self, path: "/api/interviews/\(segment(session))") }
    public func profile(_ session: String, answer: String, selected: [String]) async throws -> Interview {
        struct Body: Encodable { var answer: String; var selected_option_ids: [String] }
        return try await request(Interview.self, path: "/api/interviews/\(segment(session))/profile", method: "POST", body: encoded(Body(answer: answer, selected_option_ids: selected)))
    }
    public func next(_ session: String) async throws -> Interview { try await request(Interview.self, path: "/api/interviews/\(segment(session))/next", method: "POST") }
    public func respond(_ session: String, answer: Answer) async throws -> DiagnosticReply { try await request(DiagnosticReply.self, path: "/api/interviews/\(segment(session))/respond", method: "POST", body: encoded(answer)) }
    public func confirm(_ session: String, confirmed: Bool) async throws -> Interview { try await request(Interview.self, path: "/api/interviews/\(segment(session))/confirm", method: "POST", body: encoded(["confirmed": confirmed])) }
    public func courseJob(_ session: String, chapter: String, retry: Bool = false, requestID: String? = nil) async throws -> CourseJob {
        struct Body: Encodable { var chapter_id: String; var retry: Bool; var request_id: String? }
        return try await request(CourseJob.self, path: "/api/interviews/\(segment(session))/course-jobs", method: "POST", body: encoded(Body(chapter_id: chapter, retry: retry, request_id: requestID)))
    }
    public func pollCourse(_ job: CourseJob) async throws -> CourseJob { try await request(CourseJob.self, path: "/api/interviews/\(segment(job.session_id))/course-jobs/\(segment(job.job_id))", timeout: 30) }
    public func cachedCourse(_ session: String, chapter: String) async throws -> Course { try await request(Course.self, path: "/api/interviews/\(segment(session))/courses/\(segment(chapter))") }
    public func practice(session: String, course: String, item: String, answer: Answer) async throws -> CourseActivity { try await request(CourseActivity.self, path: "/api/interviews/\(segment(session))/courses/\(segment(course))/practice/\(segment(item))", method: "POST", body: encoded(answer), timeout: 90) }
    public func review(session: String, course: String, card: String, rating: String, eventID: String, seconds: Double) async throws -> CourseActivity {
        struct Body: Encodable { var event_id: String; var rating: String; var response_seconds: Double }
        return try await request(CourseActivity.self, path: "/api/interviews/\(segment(session))/courses/\(segment(course))/flashcards/\(segment(card))", method: "POST", body: encoded(Body(event_id: eventID, rating: rating, response_seconds: max(1, min(3600, seconds)))))
    }
    public func ask(book: String, question: String) async throws -> QAResult { try await request(QAResult.self, path: "/api/books/\(segment(book))/qa", method: "POST", body: encoded(["question": question]), timeout: 55) }
    public func saveTextNote(book: String, title: String, text: String, resourceID: String?) async throws -> TextNoteResource {
        struct Body: Encodable { var book_id: String; var title: String; var body: String; var resource_id: String? }
        return try await request(TextNoteResource.self, path: "/api/library/notes", method: "POST", body: encoded(Body(book_id: book, title: title, body: text, resource_id: resourceID)))
    }
    public func notes(book: String) async throws -> [Note] { try await request(Items<Note>.self, path: "/api/studio/notes?book_id=\(segment(book))").items }
    public func note(_ id: String) async throws -> Note { try await request(Note.self, path: "/api/studio/notes/\(segment(id))") }
    public func saveNote(_ note: Note) async throws -> Note {
        struct Body: Encodable { var id: String; var book_id: String; var chapter_id: String; var chapter_title: String; var excerpt: String; var pages: [Int]; var revision: Int; var title: String; var input_mode: String; var strokes: [Note.Stroke] }
        return try await request(Note.self, path: "/api/studio/notes", method: "POST", body: encoded(Body(id: note.id, book_id: note.book_id, chapter_id: note.chapter_id ?? "", chapter_title: note.chapter_title ?? "", excerpt: note.excerpt ?? "", pages: note.pages ?? [], revision: note.revision, title: note.title, input_mode: note.input_mode ?? "voice", strokes: note.strokes ?? [])))
    }
    public func uploadAudio(note: Note, data: Data, seconds: Double) async throws -> Note { try await request(Note.self, path: "/api/studio/notes/\(segment(note.id))/audio", method: "PUT", body: data, headers: ["Content-Type": "audio/mp4", "X-Note-Revision": "\(note.revision)", "X-Audio-Duration": "\(seconds)"], timeout: 120) }
    public func downloadAudio(note: Note) async throws -> Data { try await raw(path: "/api/studio/notes/\(segment(note.id))/audio?v=\(note.revision)", timeout: 60) }
    public func analyze(note: Note, transcript: String, requestID: String, action: String = "complete", confirmedVoiceTranscript: String? = nil) async throws -> StudioJob {
        guard ["recognize", "improve", "complete"].contains(action) else { throw ClientError.configuration("无效的笔记处理方式") }
        struct Body: Encodable { var revision: Int; var action: String; var transcript: String; var request_id: String; var consent: Bool }
        // Only a separately confirmed voice transcript may enter improvement.
        // Recognize/complete still consume audio, never the independent draft.
        let submittedTranscript: String
        if note.input_mode == "voice" {
            if action == "improve" {
                guard let confirmedVoiceTranscript else { throw ClientError.configuration("请先核对并明确确认这段录音的转写") }
                try VoiceTranscriptReview.validate(confirmedVoiceTranscript)
                submittedTranscript = confirmedVoiceTranscript
            } else { submittedTranscript = "" }
        } else { submittedTranscript = transcript }
        return try await request(StudioJob.self, path: "/api/studio/notes/\(segment(note.id))/analyze", method: "POST", body: encoded(Body(revision: note.revision, action: action, transcript: submittedTranscript, request_id: requestID, consent: true)))
    }
    public func studioJobs(book: String) async throws -> [StudioJob] { try await request(Items<StudioJob>.self, path: "/api/studio/jobs?book_id=\(segment(book))").items }
    public func studioJob(_ id: String) async throws -> StudioJob { try await request(StudioJob.self, path: "/api/studio/jobs/\(segment(id))") }
}
