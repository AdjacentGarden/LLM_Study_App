import Foundation
import LearningCore
import Darwin
import CryptoKit
import PDFKit
import AVFoundation

/// Read-only native URLSession schema check. No uploads, OCR, interview or model calls.
@main struct APISmoke {
    private struct AuthorizedState: Decodable { var cookies: [String: String]; var book_id: String; var session_id: String }
    static func main() async {
        do { try await run() }
        catch { FileHandle.standardError.write(Data("API smoke failed: \(error.localizedDescription)\n".utf8)); exit(1) }
    }
    static func run() async throws {
        func argument(_ flag: String) -> String? {
            guard let index = CommandLine.arguments.firstIndex(of: flag), CommandLine.arguments.count > index + 1 else { return nil }
            return CommandLine.arguments[index + 1]
        }
        guard let address = argument("--base-url") else { throw ClientError.configuration("Usage: APISmoke --base-url URL [--allow-local-http] [--book-id ID] [--report PATH]") }
        let config = try ServerConfiguration(address: address, allowLocalHTTP: CommandLine.arguments.contains("--allow-local-http"))
        let statePath = argument("--state-dir")
        let root = statePath.map { URL(fileURLWithPath: $0) } ?? FileManager.default.temporaryDirectory.appendingPathComponent("cloudpath-api-smoke-" + UUID().uuidString)
        defer { if statePath == nil { try? FileManager.default.removeItem(at: root) } }
        let disk = try DiskStore(root: root); let api = APIClient(configuration: config, store: disk)
        var authorized: AuthorizedState?
        if let stateFile = argument("--client-state") {
            let state = try JSONDecoder().decode(AuthorizedState.self, from: Data(contentsOf: URL(fileURLWithPath: stateFile)))
            let cookies: [[String: Any]] = state.cookies.map { ["name": $0.key, "value": $0.value, "domain": config.baseURL.host!, "path": "/", "secure": config.baseURL.scheme == "https"] }
            let cookieFile = root.appendingPathComponent("cookies.json")
            try JSONSerialization.data(withJSONObject: cookies).write(to: cookieFile, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: cookieFile.path)
            authorized = state
        }
        try await api.restoreCookies()
        var checks: [[String: Any]] = []
        let started = Date()
        let health = try await api.checkHealth(); checks.append(["endpoint": "/api/health", "decoded": true, "ok": health.ok])
        let readiness = try await api.readiness(); checks.append(["endpoint": "/api/readiness", "decoded": true, "scope": readiness.check_scope, "external_inference_verified": readiness.external_inference_verified, "features": readiness.features])
        let account = try await api.account(); checks.append(["endpoint": "/api/auth/me", "decoded": true, "visitor": account.account == nil, "learning_sessions_count": account.learning_sessions.count])
        let books = try await api.books(); checks.append(["endpoint": "/api/library", "decoded": true, "count": books.count])
        print("PASS health/readiness/auth/library; owned book count: \(books.count)")
        if let book = argument("--book-id") ?? books.first(where: { $0.book_id == authorized?.book_id })?.book_id ?? books.first?.book_id ?? authorized?.book_id {
            let structure = try await api.structure(book)
            checks.append(["endpoint": "/api/books/{id}/structure", "decoded": true, "chapters": structure.chapters.count, "source_page_count": structure.source_page_count])
            print("PASS structure; chapters: \(structure.chapters.count), pages: \(structure.source_page_count)")
            if CommandLine.arguments.contains("--check-source") {
                let header = try await api.sourcePDFHeader(book)
                guard header == Data("%PDF-".utf8) else { throw ClientError.invalidResponse("Source Range did not return the exact PDF header") }
                checks.append(["endpoint": "/api/library/books/{id}/source", "range": "bytes=0-4", "pdf_header_valid": true, "received_bytes": header.count])
                print("PASS source PDF exact five-byte Range")
            }
            if CommandLine.arguments.contains("--download-pdf") {
                let startedPDF = Date(); let data = try await api.sourcePDF(book)
                let file = root.appendingPathComponent("source-validation.pdf")
                try data.write(to: file, options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
                guard let pdf = PDFDocument(url: file), pdf.pageCount > 0, pdf.page(at: 0) != nil, pdf.page(at: pdf.pageCount - 1) != nil else { throw ClientError.invalidResponse("Native PDFKit could not open the downloaded book") }
                checks.append(["check": "fullSourceDownloadAndNativeMacPDFKit", "received_bytes": data.count, "sha256": SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(), "page_count": pdf.pageCount, "first_and_last_page_valid": true, "duration_seconds": Date().timeIntervalSince(startedPDF), "iphone_runtime_test": false])
                print("PASS full source download + native Mac PDFKit, pages: \(pdf.pageCount), bytes: \(data.count)")
            }
            if authorized != nil {
                let notes = try await api.notes(book: book)
                checks.append(["endpoint": "/api/studio/notes", "decoded": true, "count": notes.count])
                print("PASS notes list schema")
                if CommandLine.arguments.contains("--check-audio") {
                    for note in notes.prefix(2) {
                        let detail = try await api.note(note.id)
                        checks.append(["endpoint": "/api/studio/notes/{id}", "decoded": true, "audio_ready": detail.audio_ready == true])
                        if detail.audio_ready == true {
                            let audio = try await api.downloadAudio(note: detail)
                            let player = try AVAudioPlayer(data: audio)
                            guard player.duration > 0 else { throw ClientError.invalidResponse("Downloaded audio is empty") }
                            checks.append(["check": "authenticatedAudioDownloadAndMacAVFoundationDecode", "received_bytes": audio.count, "duration_seconds": player.duration, "played": false, "iphone_runtime_test": false])
                        }
                    }
                }
            }
        }
        if let session = argument("--session-id") ?? authorized?.session_id {
            let interview = try await api.resume(session)
            checks.append(["endpoint": "/api/interviews/{id}", "decoded": true, "phase": interview.phase])
        }
        let relaunched = APIClient(configuration: config, store: disk); try await relaunched.restoreCookies()
        let restored = try await relaunched.account()
        guard restored.user_id == account.user_id else { throw ClientError.invalidResponse("Visitor cookie identity changed after reload") }
        checks.append(["check": "actualURLSessionVisitorIdentityAfterRelaunch", "passed": true])
        let report: [String: Any] = ["created_at": ISO8601DateFormatter().string(from: Date()), "base_url": config.baseURL.absoluteString, "transport": "real native URLSession on macOS", "scope": "read-only API/schema/cookie; no model generation, no iPhone runtime", "duration_seconds": Date().timeIntervalSince(started), "checks": checks]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        if let reportPath = argument("--report") { try data.write(to: URL(fileURLWithPath: reportPath), options: .atomic) }
        print(String(decoding: data, as: UTF8.self))
    }
}
