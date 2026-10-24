import Foundation
import CryptoKit

@main struct NativeUploadSmoke {
    private struct AuthorizedState: Decodable { var cookies: [String: String]; var book_id: String; var session_id: String }
    static func main() async {
        do { try await run() }
        catch { FileHandle.standardError.write(Data("Native upload check failed: \(error.localizedDescription)\n".utf8)); exit(1) }
    }
    static func run() async throws {
        let directory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let root = directory.appendingPathComponent(".runtime/native-upload-live", isDirectory: true)
        let source = directory.appendingPathComponent(".runtime/acceptance-native/source-validation.pdf")
        let reportURL = directory.appendingPathComponent("native-upload-live-results.json")
        let disk = try DiskStore(root: root)
        let state = try JSONDecoder().decode(AuthorizedState.self, from: Data(contentsOf: directory.appendingPathComponent(".runtime/acceptance-client-state.json")))
        let cookies: [[String: Any]] = state.cookies.map { ["name": $0.key, "value": $0.value, "domain": "127.0.0.1", "path": "/", "secure": false] }
        let cookieFile = root.appendingPathComponent("cookies.json")
        try JSONSerialization.data(withJSONObject: cookies).write(to: cookieFile, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: cookieFile.path)
        let config = try ServerConfiguration(address: "http://127.0.0.1:18126", allowLocalHTTP: true)
        let api = APIClient(configuration: config, store: disk); try await api.restoreCookies()
        let started = Date()
        var report: [String: Any] = ["created_at": ISO8601DateFormatter().string(from: started), "transport": "real native URLSession file upload on macOS", "scope": "authorized test visitor; new upload+bind only", "ocr_started": false, "claim_called": false, "model_inference_called": false, "iphone_runtime_test": false]
        do {
            let data = try Data(contentsOf: source, options: .mappedIfSafe)
            guard data.starts(with: Data("%PDF-".utf8)) else { throw ClientError.configuration("Missing actual source PDF") }
            let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            guard hash == "d35ca6844f22e87bef5cd3deb286c7965f386ba13924a8195eddaefa41db533a" else { throw ClientError.configuration("Unexpected PDF hash") }
            report["input_bytes"] = data.count; report["input_sha256"] = hash
            let account = try await api.account(); let before = try await api.books()
            let uploadStarted = Date()
            let uploaded = try await api.uploadPDF(file: source, filename: "原生multipart_125页联调.pdf")
            report["upload_duration_seconds"] = Date().timeIntervalSince(uploadStarted)
            report["book_id"] = uploaded.book_id; report["server_filename"] = uploaded.filename; report["server_status"] = uploaded.status
            let bindStarted = Date(); try await api.bind(uploaded.book_id)
            report["bind_duration_seconds"] = Date().timeIntervalSince(bindStarted)
            report["bind_succeeded"] = true
            let bookStatus = try await api.bookStatus(uploaded.book_id)
            report["status_after_bind"] = bookStatus.status
            guard bookStatus.status == "uploaded" else { throw ClientError.invalidResponse("Unexpected processing after upload-only check") }
            let after = try await api.books()
            report["shelf_count_before"] = before.count; report["shelf_count_after"] = after.count
            let relaunched = APIClient(configuration: config, store: disk); try await relaunched.restoreCookies()
            report["visitor_identity_preserved"] = try await relaunched.account().user_id == account.user_id
            report["result"] = "passed"
        } catch {
            report["result"] = "failed"; report["error"] = error.localizedDescription
            report["overall_seconds"] = Date().timeIntervalSince(started)
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted,.sortedKeys]).write(to: reportURL, options: .atomic)
            throw error
        }
        report["overall_seconds"] = Date().timeIntervalSince(started)
        let output = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted,.sortedKeys])
        try output.write(to: reportURL, options: .atomic); print(String(decoding: output, as: UTF8.self))
    }
}
