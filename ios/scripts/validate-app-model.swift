import Foundation
import SwiftUI
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private struct ModelCheckFailure: Error, LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
private func ensure(_ condition: Bool, _ message: String) throws {
    if !condition { throw ModelCheckFailure(message: message) }
}
private let courseKey = "s1:ch1"
private let cachePath = "/api/interviews/s1/courses/ch1"
private let submitPath = "/api/interviews/s1/course-jobs"
private let jobPath = "/api/interviews/s1/course-jobs/j1"
private let queuedJSON = #"{"job_id":"j1","session_id":"s1","chapter_id":"ch1","status":"queued","stage":"queued","attempts":1,"error":null,"retryable":false,"created_at":"2026-10-22T01:00:00Z","updated_at":"2026-10-22T01:00:00Z","course":null}"#
private let courseJSON = #"{"course_id":"approved1","version":2,"chapter_id":"ch1","chapter_title":"Fixture chapter","opening":"Opening","summary":"Summary","original_reading":[],"knowledge_points":[],"flashcards":[],"worked_examples":[],"checkpoint_questions":[],"practice_items":[],"estimated_minutes":10,"created_at":"2026-10-22T01:00:00Z","unresolved_source_warnings":[]}"#
private let unreadyBookJSON = #"{"book_id":"b1","title":"Fixture owned book","status":"ready","page_count":2,"chapter_count":1,"summary":"Summary","diagnostics_ready":false}"#
private let preparationPath = "/api/books/b1/prepare"
private let preparingJSON = #"{"book_id":"b1","status":"running","stage":"diagnostics","error":"","updated":123.5}"#
private let preparedJSON = #"{"book_id":"b1","status":"succeeded","stage":"ready","error":"","updated":125.5}"#

private struct Step: Sendable {
    let method: String
    let path: String
    let status: Int
    let body: String
    var networkFailure = false
    var holdForRelease = false
    static func get(_ path: String, _ status: Int, _ body: String = #"{"detail":"fixture response"}"#) -> Step {
        Step(method: "GET", path: path, status: status, body: body)
    }
    static func post(_ status: Int, _ body: String = queuedJSON) -> Step {
        Step(method: "POST", path: submitPath, status: status, body: body)
    }
    static func disconnected(_ method: String, _ path: String) -> Step {
        Step(method: method, path: path, status: 0, body: "", networkFailure: true)
    }
}
private struct Observation: Sendable {
    let method: String
    let path: String
    let requestID: String?
    let persistedRequestID: String?
    let query: String?
}
private actor ModelTransport: HTTPTransport {
    private var steps: [Step]
    private var seen: [Observation] = []
    private var problems: [String] = []
    private var held: CheckedContinuation<Void, Never>?
    private let stateURL: URL
    init(_ steps: [Step], stateURL: URL) { self.steps = steps; self.stateURL = stateURL }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let method = request.httpMethod ?? "GET"
        let path = request.url?.path ?? ""
        let payload = request.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let requestID = payload?["request_id"] as? String
        let persisted = (try? Data(contentsOf: stateURL)).flatMap { try? JSONDecoder().decode(StoredState.self, from: $0) }
        seen.append(Observation(method: method, path: path, requestID: requestID, persistedRequestID: persisted?.courseRequestIDs?[courseKey], query: request.url?.query))
        guard !steps.isEmpty else {
            problems.append("Unexpected extra request: \(method) \(path)")
            throw URLError(.badServerResponse)
        }
        let next = steps.removeFirst()
        guard next.method == method && next.path == path else {
            problems.append("Expected \(next.method) \(next.path), received \(method) \(path)")
            throw URLError(.badServerResponse)
        }
        if next.networkFailure { throw URLError(.networkConnectionLost) }
        if next.holdForRelease { await withCheckedContinuation { held = $0 } }
        return (Data(next.body.utf8), HTTPURLResponse(url: request.url!, statusCode: next.status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!)
    }
    func observations() -> [Observation] { seen }
    func issues() -> [String] { problems }
    func remaining() -> Int { steps.count }
    func isHeld() -> Bool { held != nil }
    func releaseHeld() { held?.resume(); held = nil }
}

private struct CheckResult: Codable {
    var name: String
    var passed: Bool
    var detail: String
}
private struct CheckReport: Codable {
    var generatedAt: String
    var scope: String
    var fixturesOnly: Bool
    var realNetworkUsed: Bool
    var iOSBuildVerified: Bool
    var iOSUIVerified: Bool
    var passed: Bool
    var results: [CheckResult]
}

@main
private struct AppModelChecks {
    static let connectionSteps: [Step] = [
        .get("/api/health", 200, #"{"ok":true,"service":"fixture-only"}"#),
        .get("/api/auth/me", 200, #"{"account":null,"user_id":"visitor_fixture","email_available":false,"learning_sessions":{}}"#),
        .get("/api/library", 200, "[]"),
        .get("/api/readiness", 200, #"{"ok":true,"check_scope":"configuration_only","external_inference_verified":false,"features":{},"blocked_features":[]}"#)
    ]

    @MainActor
    static func main() async throws {
        let ios = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let suiteRoot = ios.appendingPathComponent(".build/app-model-state-checks/" + UUID().uuidString, isDirectory: true)
        try ensure(suiteRoot.standardizedFileURL.path.hasPrefix(ios.standardizedFileURL.path + "/.build/"), "Test root must remain inside ios/.build")
        try FileManager.default.createDirectory(at: suiteRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: suiteRoot) }
        var results: [CheckResult] = []
        let checks: [(String, @MainActor (URL) async throws -> Void)] = [
            ("cached_503_never_posts", cached503),
            ("cached_network_failure_never_posts", cachedNetworkFailure),
            ("cached_409_requires_explicit_new_request", cachedConflict),
            ("uncertain_post_relaunch_reuses_persisted_request_id", uncertainPost),
            ("poll_409_stops_without_new_post_or_request_id", pollConflict),
            ("poll_503_applies_immediate_backoff", pollingBackoff),
            ("entry_revalidation_409_invalidates_cached_course_without_post", revalidateConflict),
            ("entry_revalidation_network_failure_preserves_unverified_history", revalidateOffline),
            ("entry_revalidation_200_accepts_current_approved_cache", revalidateSuccess),
            ("reconnect_revalidates_restored_course_by_get_only", reconnectRevalidates),
            ("owned_book_prepare_relaunch_resumes_get_and_refreshes_shelf", existingPreparation),
            ("owned_book_prepare_missing_get_never_starts_ocr_or_prepare", existingPreparationMissing),
            ("owned_book_prepare_failed_retry_is_explicit", existingPreparationRetry),
            ("new_revision_post_unknown_relaunch_reuses_same_request", revisedSubmissionUnknown),
            ("foreground_health_failure_downgrades_course_to_history", foregroundOffline),
            ("cold_offline_relaunch_retains_books_directory_and_history", coldOffline),
            ("late_old_cache_get_cannot_revoke_newly_completed_job", lateCacheResponse),
            ("terminal_failed_status_checks_are_get_only", terminalFailureReadOnly),
            ("terminal_pending_status_check_is_get_only", terminalPendingReadOnly),
            ("terminal_failure_copy_truth_table", terminalCopyTruthTable),
            ("old_completed_job_cached409_does_not_restore_missing_course_or_verify", terminalCompletedRevokedNoHistory),
            ("old_completed_job_cached409_keeps_history_invalid_for_scoring", terminalCompletedRevokedHistory),
            ("completed_job_accepts_only_current_cache_get", terminalCompletedCurrentCache),
            ("completed_job_cached503_does_not_restore_or_verify", terminalCompletedCacheUnavailable),
            ("task_identity_mismatch_does_not_restore", terminalIdentityMismatch),
            ("late_task_response_cannot_overwrite_newer_request", terminalLateTask),
            ("late_current_cache_response_cannot_verify_newer_request", terminalLateCurrentCache),
            ("explicit_retry_preserves_same_durable_request_id", terminalExplicitRetrySameID)
        ]
        for (name, check) in checks {
            do {
                try await check(suiteRoot)
                results.append(CheckResult(name: name, passed: true, detail: "All transport sequence, model-state, and persistence assertions passed."))
                print("PASS: \(name)")
            } catch {
                results.append(CheckResult(name: name, passed: false, detail: error.localizedDescription))
                print("FAIL: \(name): \(error.localizedDescription)")
            }
        }
        let report = CheckReport(generatedAt: ISO8601DateFormatter().string(from: Date()), scope: "macOS Swift 6 AppModel state and on-disk persistence with deterministic HTTP fixtures; no microphone, iOS build, UI, model inference, or live service validation", fixturesOnly: true, realNetworkUsed: false, iOSBuildVerified: false, iOSUIVerified: false, passed: results.allSatisfy(\.passed), results: results)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(report).write(to: ios.appendingPathComponent("app-model-test-results.json"), options: .atomic)
        try ensure(report.passed, "One or more AppModel state regression checks failed")
    }

    @MainActor
    private static func setup(_ name: String, suiteRoot: URL, steps: [Step]) async throws -> (AppModel, ModelTransport, URL, ServerConfiguration) {
        let root = suiteRoot.appendingPathComponent(name, isDirectory: true)
        let config = try ServerConfiguration(address: "https://fixture.invalid")
        let transport = ModelTransport(connectionSteps + steps, stateURL: root.appendingPathComponent(config.namespace).appendingPathComponent("state.json"))
        let model = AppModel(root: root, transport: transport)
        await model.connect(config)
        try ensure(model.connected && model.error == nil, "Fixture connection did not complete: \(model.error ?? "unknown")")
        model.state.sessions["b1"] = "s1"
        return (model, transport, root, config)
    }
    private static func verifyTransport(_ transport: ModelTransport) async throws {
        let problems = await transport.issues()
        try ensure(problems.isEmpty, problems.joined(separator: "; "))
        try ensure(await transport.remaining() == 0, "Expected HTTP fixture steps were not consumed")
    }
    private static func verifyPersistedID(_ root: URL, _ config: ServerConfiguration, _ id: String?) async throws {
        let disk = try DiskStore(root: root.appendingPathComponent(config.namespace))
        let state = try await disk.load(StoredState.self, from: "state.json")
        try ensure(id != nil && state?.courseRequestIDs?[courseKey] == id, "Course request ID was not retained in state.json")
    }
    private static func verifyPostsCommitted(_ observations: [Observation]) throws {
        for request in observations where request.method == "POST" {
            try ensure(request.requestID != nil && request.requestID == request.persistedRequestID, "POST occurred before its request ID was committed to disk")
        }
    }

    @MainActor
    private static func cached503(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("cached503", suiteRoot: suiteRoot, steps: [.get(cachePath, 503)])
        await model.requestCourse(book: "b1", chapter: "ch1")
        let observations = await transport.observations()
        try ensure(observations.count == 5 && !observations.contains { $0.method == "POST" }, "Cached 503 caused an unexpected write")
        try ensure(model.error?.contains("503") == true, "Cached 503 did not surface as an error")
        try ensure(model.state.courseRequestIDs?[courseKey] == nil && model.state.courseJobs[courseKey] == nil, "Cached 503 allocated a generation request")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func cachedNetworkFailure(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("cachedNetwork", suiteRoot: suiteRoot, steps: [.disconnected("GET", cachePath)])
        await model.requestCourse(book: "b1", chapter: "ch1")
        let observations = await transport.observations()
        try ensure(observations.count == 5 && !observations.contains { $0.method == "POST" }, "Cached network failure caused an unexpected write")
        try ensure(model.error?.contains("连接中断") == true, "Cached network error did not surface")
        try ensure(model.state.courseRequestIDs?[courseKey] == nil, "Cached network failure allocated a request ID")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func cachedConflict(_ suiteRoot: URL) async throws {
        let (model, transport, root, config) = try await setup("cached409", suiteRoot: suiteRoot, steps: [.get(cachePath, 409, #"{"detail":"source revision changed"}"#), .post(202)])
        model.state.courseRequestIDs?[courseKey] = "previous-durable-request"
        await model.requestCourse(book: "b1", chapter: "ch1")
        try ensure(model.state.courseConflicts?[courseKey] == "source revision changed", "GET 409 did not persist a conflict")
        try ensure(model.state.courseRequestIDs?[courseKey] == "previous-durable-request", "GET 409 replaced the previous request ID")
        await model.requestCourse(book: "b1", chapter: "ch1")
        try ensure(await transport.observations().count == 5, "A known conflict was automatically resubmitted")
        await model.requestCourse(book: "b1", chapter: "ch1", newRequest: true)
        let id = model.state.courseRequestIDs?[courseKey]
        try ensure(id != nil && id != "previous-durable-request", "Explicit new request did not allocate a fresh ID")
        try ensure(model.state.courseConflicts?[courseKey] == nil && model.state.courseJobs[courseKey]?.status == "queued", "Explicit request did not clear the conflict and accept its job")
        let observations = await transport.observations()
        try ensure(observations.filter { $0.method == "POST" }.count == 1, "Conflict recovery submitted more than one job")
        try verifyPostsCommitted(observations)
        try await verifyPersistedID(root, config, id)
        try await verifyTransport(transport)
    }
    @MainActor
    private static func uncertainPost(_ suiteRoot: URL) async throws {
        let steps: [Step] = [.get(cachePath, 404), .disconnected("POST", submitPath)] + connectionSteps + [.post(202)]
        let (model, transport, root, config) = try await setup("uncertainPost", suiteRoot: suiteRoot, steps: steps)
        await model.requestCourse(book: "b1", chapter: "ch1")
        let firstID = model.state.courseRequestIDs?[courseKey]
        try ensure(firstID != nil && model.state.courseJobs[courseKey] == nil && model.error != nil, "Uncertain POST did not preserve an ID without inventing a job")
        try await verifyPersistedID(root, config, firstID)
        let relaunched = AppModel(root: root, transport: transport)
        await relaunched.connect(config)
        try ensure(relaunched.connected && relaunched.state.courseRequestIDs?[courseKey] == firstID, "Relaunched model did not recover the uncertain request")
        await relaunched.requestCourse(book: "b1", chapter: "ch1")
        let observations = await transport.observations()
        let posts = observations.filter { $0.method == "POST" }
        try ensure(posts.count == 2 && posts.allSatisfy { $0.requestID == firstID }, "Unknown POST retry allocated or sent a different request ID")
        try ensure(relaunched.state.courseJobs[courseKey]?.status == "queued", "Retry did not accept the eventual 202 job")
        try verifyPostsCommitted(observations)
        try await verifyPersistedID(root, config, firstID)
        try await verifyTransport(transport)
    }
    @MainActor
    private static func pollConflict(_ suiteRoot: URL) async throws {
        let (model, transport, root, config) = try await setup("poll409", suiteRoot: suiteRoot, steps: [.get(cachePath, 404), .post(202), .get(jobPath, 409, #"{"detail":"profile snapshot changed"}"#)])
        await model.requestCourse(book: "b1", chapter: "ch1")
        let id = model.state.courseRequestIDs?[courseKey]
        await model.tick()
        try ensure(model.state.courseConflicts?[courseKey] == "profile snapshot changed", "Polling 409 did not record the conflict")
        let count = await transport.observations().count
        await model.tick()
        await model.requestCourse(book: "b1", chapter: "ch1")
        let observations = await transport.observations()
        try ensure(observations.count == count, "Known conflicted job continued polling or automatically resubmitted")
        try ensure(observations.filter { $0.method == "POST" }.count == 1, "Polling conflict caused a second POST")
        try ensure(model.state.courseRequestIDs?[courseKey] == id, "Polling conflict replaced the request ID")
        try await verifyPersistedID(root, config, id)
        let disk = try DiskStore(root: root.appendingPathComponent(config.namespace))
        let state = try await disk.load(StoredState.self, from: "state.json")
        try ensure(state?.courseConflicts?[courseKey] == "profile snapshot changed", "Polling conflict was not saved to disk")
        try verifyPostsCommitted(observations)
        try await verifyTransport(transport)
    }
    @MainActor
    private static func pollingBackoff(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("pollBackoff", suiteRoot: suiteRoot, steps: [.get(cachePath, 404), .post(202), .get(jobPath, 503)])
        await model.requestCourse(book: "b1", chapter: "ch1")
        let id = model.state.courseRequestIDs?[courseKey]
        await model.tick()
        try ensure(model.error?.contains("503") == true, "Polling 503 did not surface its error")
        let count = await transport.observations().count
        await model.tick()
        try ensure(await transport.observations().count == count, "Immediate tick ignored retry backoff")
        try ensure(model.state.courseRequestIDs?[courseKey] == id, "Backoff replaced the generation request ID")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func seedCourse(_ model: AppModel) throws {
        model.state.courses[courseKey] = try JSONDecoder().decode(Course.self, from: Data(courseJSON.utf8))
        model.state.courseValidation?[courseKey] = "verified"
        model.state.courseRequestIDs?[courseKey] = "stable-existing-request"
    }
    @MainActor
    private static func revalidateConflict(_ suiteRoot: URL) async throws {
        let (model, transport, root, config) = try await setup("entryConflict", suiteRoot: suiteRoot, steps: [.get(cachePath, 409, #"{"detail":"teaching version revoked"}"#)])
        try seedCourse(model); await model.validateCachedCourse(book: "b1", chapter: "ch1")
        try ensure(model.state.courseValidation?[courseKey] == "invalid" && model.state.courseConflicts?[courseKey] != nil, "Revoked course remained current")
        try ensure(model.state.courses[courseKey] != nil, "Historical data was deleted")
        try await verifyPersistedID(root, config, "stable-existing-request")
        try ensure(!(await transport.observations()).contains { $0.method == "POST" }, "Validation posted generation")
        let saved = try await DiskStore(root: root.appendingPathComponent(config.namespace)).load(StoredState.self, from: "state.json")
        try ensure(saved?.courseValidation?[courseKey] == "invalid", "Invalidity did not survive disk persistence")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func revalidateOffline(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("entryOffline", suiteRoot: suiteRoot, steps: [.disconnected("GET", cachePath)])
        try seedCourse(model); await model.validateCachedCourse(book: "b1", chapter: "ch1")
        try ensure(model.state.courses[courseKey] != nil && model.state.courseValidation?[courseKey] == "unverified", "Network error lost history or kept it falsely verified")
        try ensure(model.courseValidationMessages[courseKey]?.contains("历史副本") == true, "Unverified history has no visible explanation")
        try ensure(model.state.courseConflicts?[courseKey] == nil, "A connection error was incorrectly treated as revocation")
        try ensure(!(await transport.observations()).contains { $0.method == "POST" }, "Offline validation posted a task")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func revalidateSuccess(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("entryCurrent", suiteRoot: suiteRoot, steps: [.get(cachePath, 200, courseJSON)])
        try seedCourse(model); model.state.courseValidation?[courseKey] = "invalid"; model.state.courseConflicts?[courseKey] = "previous rejection"
        await model.validateCachedCourse(book: "b1", chapter: "ch1")
        try ensure(model.state.courseValidation?[courseKey] == "verified" && model.state.courseConflicts?[courseKey] == nil, "Current approved GET was not accepted")
        try ensure(!(await transport.observations()).contains { $0.method == "POST" }, "Current cache lookup generated a course")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func reconnectRevalidates(_ suiteRoot: URL) async throws {
        let (model, transport, root, config) = try await setup("reconnectCache", suiteRoot: suiteRoot, steps: connectionSteps + [.get(cachePath, 409)])
        try seedCourse(model); try await model.persist()
        let restored = AppModel(root: root, transport: transport); await restored.connect(config)
        try ensure(restored.connected && restored.state.courseValidation?[courseKey] == "invalid", "Reconnect did not revalidate restored cache")
        try ensure(!(await transport.observations()).contains { $0.method == "POST" }, "Reconnect made a paid generation request")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func existingPreparation(_ suiteRoot: URL) async throws {
        var reconnect = connectionSteps
        reconnect[2] = .get("/api/library", 200, "[" + unreadyBookJSON + "]")
        let post = Step(method: "POST", path: preparationPath, status: 200, body: preparingJSON)
        let steps = [post] + reconnect + [.get(preparationPath, 200, preparingJSON), .get(preparationPath, 200, preparedJSON),
            .get("/api/library", 200, "[" + unreadyBookJSON.replacingOccurrences(of: "false", with: "true") + "]")]
        let (model, transport, root, config) = try await setup("ownedPrepare", suiteRoot: suiteRoot, steps: steps)
        await model.prepareExistingBook("b1")
        try ensure(model.state.bookPreparations?["b1"]?.status == "running", "Explicit owned preparation was not saved")
        let restored = AppModel(root: root, transport: transport); await restored.connect(config); await restored.tick()
        try ensure(restored.state.bookPreparations?["b1"]?.status == "succeeded" && restored.books.first?.diagnostics_ready == true, "Restored preparation did not unlock the refreshed shelf")
        let requests = await transport.observations()
        try ensure(requests.filter { $0.method == "POST" }.count == 1, "Reconnect duplicated preparation")
        try ensure(!requests.contains { $0.path.contains("/process") || $0.path == "/api/books" }, "Owned preparation reran OCR or upload")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func existingPreparationMissing(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("ownedMissing", suiteRoot: suiteRoot, steps: [.get(preparationPath, 404)])
        model.state.bookPreparations?["b1"] = try JSONDecoder().decode(PreparationJob.self, from: Data(preparingJSON.utf8))
        await model.refreshExistingPreparation("b1")
        try ensure(model.state.bookPreparations?["b1"] == nil, "Missing task lookup retained a stuck historical preparation")
        try ensure(!(await transport.observations()).contains { $0.method == "POST" }, "Read-only lookup started a job")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func existingPreparationRetry(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("ownedRetry", suiteRoot: suiteRoot, steps: [Step(method: "POST", path: preparationPath, status: 200, body: preparingJSON)])
        await model.prepareExistingBook("b1", retry: true)
        let last = await transport.observations().last
        try ensure(last?.query == "retry=true" && last?.path == preparationPath, "Explicit retry did not use the preparation retry contract")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func revisedSubmissionUnknown(_ suiteRoot: URL) async throws {
        let steps = [.get(cachePath, 409), .disconnected("POST", submitPath)] + connectionSteps + [.get(cachePath, 409), .post(202)]
        let (model, transport, root, config) = try await setup("revisionUnknown", suiteRoot: suiteRoot, steps: steps)
        try seedCourse(model)
        model.state.courseJobs[courseKey] = try JSONDecoder().decode(CourseJob.self, from: Data(queuedJSON.replacingOccurrences(of: "queued", with: "succeeded").utf8))
        await model.validateCachedCourse(book: "b1", chapter: "ch1")
        await model.requestCourse(book: "b1", chapter: "ch1", newRequest: true)
        let id = model.state.courseRequestIDs?[courseKey]
        try ensure(model.state.courseSubmissionPending?[courseKey] == true && model.state.courseJobs[courseKey] == nil, "Unknown new submission kept the previous terminal job in its place")
        let count = await transport.observations().count
        await model.requestCourse(book: "b1", chapter: "ch1", newRequest: true)
        let afterRejectedNewRequest = await transport.observations().count
        try ensure(model.state.courseRequestIDs?[courseKey] == id && afterRejectedNewRequest == count, "An unconfirmed request allocated or sent another new version")
        let restored = AppModel(root: root, transport: transport); await restored.connect(config)
        try ensure(restored.state.courseSubmissionPending?[courseKey] == true, "Unknown request marker was not restored")
        await restored.requestCourse(book: "b1", chapter: "ch1")
        try ensure(restored.state.courseJobs[courseKey]?.status == "queued" && restored.state.courseSubmissionPending?[courseKey] != true, "Explicit same-request retry could not recover its job")
        let posts = await transport.observations().filter { $0.method == "POST" }
        try ensure(posts.count == 2 && posts.allSatisfy { $0.requestID == id }, "New revision unknown retry changed request ID")
        try verifyPostsCommitted(posts); try await verifyTransport(transport)
    }
    @MainActor
    private static func foregroundOffline(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("foregroundOffline", suiteRoot: suiteRoot, steps: [.disconnected("GET", "/api/health")])
        try seedCourse(model); await model.refresh()
        try ensure(!model.connected && model.state.courseValidation?[courseKey] == "unverified", "Foreground health failure left course scoring enabled")
        try ensure(model.state.courses[courseKey] != nil && model.courseValidationMessages[courseKey] != nil, "Offline refresh lost history or its warning")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func coldOffline(_ suiteRoot: URL) async throws {
        let (model, transport, root, config) = try await setup("coldOffline", suiteRoot: suiteRoot, steps: [.disconnected("GET", "/api/health")])
        try seedCourse(model)
        model.state.cachedBooks = [try JSONDecoder().decode(Book.self, from: Data(unreadyBookJSON.utf8))]
        model.state.bookStructures?["b1"] = try JSONDecoder().decode(BookStructure.self, from: Data(#"{"title":"Book","summary":"Summary","source_page_count":2,"chapters":[]}"#.utf8))
        try await model.persist()
        let restored = AppModel(root: root, transport: transport); await restored.connect(config)
        try ensure(!restored.connected && restored.books.first?.book_id == "b1" && restored.state.bookStructures?["b1"] != nil, "Cold offline launch lost the historical reading navigation")
        try ensure(restored.state.courses[courseKey] != nil && restored.state.courseValidation?[courseKey] == "unverified", "Cold launch treated saved verification as current or lost its course")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func lateCacheResponse(_ suiteRoot: URL) async throws {
        let newer = courseJSON.replacingOccurrences(of: "approved1", with: "approved2").replacingOccurrences(of: "\"version\":2", with: "\"version\":3")
        let completed = queuedJSON.replacingOccurrences(of: "\"status\":\"queued\"", with: "\"status\":\"succeeded\"").replacingOccurrences(of: "\"course\":null", with: "\"course\":" + newer)
        let held = Step(method: "GET", path: cachePath, status: 409, body: #"{"detail":"old snapshot revoked"}"#, holdForRelease: true)
        let (model, transport, _, _) = try await setup("lateCache", suiteRoot: suiteRoot, steps: [held, .post(202), .get(jobPath, 200, completed)])
        try seedCourse(model)
        let lookup = Task { await model.validateCachedCourse(book: "b1", chapter: "ch1") }
        while !(await transport.isHeld()) { await Task.yield() }
        await model.requestCourse(book: "b1", chapter: "ch1", newRequest: true); await model.tick()
        await transport.releaseHeld(); await lookup.value
        try ensure(model.state.courses[courseKey]?.course_id == "approved2" && model.state.courseValidation?[courseKey] == "verified" && model.state.courseConflicts?[courseKey] == nil, "A delayed old GET revoked or overwrote a newly approved course")
        try await verifyTransport(transport)
    }
    private static func terminalJSON(_ status: String, retryable: Bool = false, embeddedCourse: String? = nil) throws -> String {
        var object = try JSONSerialization.jsonObject(with: Data(queuedJSON.utf8)) as! [String: Any]
        object["status"] = status; object["stage"] = status; object["attempts"] = status == "failed" ? 3 : 1; object["retryable"] = retryable
        if status == "failed" { object["error"] = "教学服务暂时不可用，请稍后重试" } else { object["error"] = NSNull() }
        object["course"] = try embeddedCourse.map { try JSONSerialization.jsonObject(with: Data($0.utf8)) } ?? NSNull()
        return String(data: try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]), encoding: .utf8)!
    }
    @MainActor
    private static func seedTask(_ model: AppModel, status: String = "failed", retryable: Bool = false) throws {
        model.state.courseJobs[courseKey] = try JSONDecoder().decode(CourseJob.self, from: Data(terminalJSON(status, retryable: retryable).utf8))
        model.state.courseRequestIDs?[courseKey] = "terminal-original-request"
    }
    private static func verifyOnlyGET(_ transport: ModelTransport) async throws {
        let observations = await transport.observations()
        try ensure(observations.allSatisfy { $0.method == "GET" }, "Task inspection submitted a write")
        try await verifyTransport(transport)
    }
    @MainActor
    private static func terminalFailureReadOnly(_ suiteRoot: URL) async throws {
        let (model, transport, root, config) = try await setup("terminalFailed", suiteRoot: suiteRoot,
            steps: [.get(jobPath, 200, try terminalJSON("failed")), .get(jobPath, 200, try terminalJSON("failed"))])
        try seedTask(model); try await model.persist()
        await model.checkCourseTask(book: "b1", chapter: "ch1"); await model.checkCourseTask(book: "b1", chapter: "ch1")
        try ensure(model.state.courseJobs[courseKey]?.isExhaustedFailure == true && model.state.courses[courseKey] == nil, "Exhausted job was restarted or promoted")
        try await verifyPersistedID(root, config, "terminal-original-request")
        try await verifyOnlyGET(transport)
    }
    @MainActor
    private static func terminalPendingReadOnly(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("terminalPending", suiteRoot: suiteRoot, steps: [.get(jobPath, 200, try terminalJSON("running"))])
        try seedTask(model, status: "queued"); await model.checkCourseTask(book: "b1", chapter: "ch1")
        try ensure(model.state.courseJobs[courseKey]?.isPending == true && model.state.courseRequestIDs?[courseKey] == "terminal-original-request", "Pending inspection changed request identity")
        try await verifyOnlyGET(transport)
    }
    @MainActor
    private static func terminalCopyTruthTable(_ suiteRoot: URL) async throws {
        for status in ["queued", "running", "succeeded"] {
            let task = try JSONDecoder().decode(CourseJob.self, from: Data(terminalJSON(status).utf8))
            try ensure(!task.isExhaustedFailure && task.clientFailureMessage == nil, "Non-failed task received exhausted failure copy")
        }
        let stopped = try JSONDecoder().decode(CourseJob.self, from: Data(terminalJSON("failed").utf8))
        let text = stopped.clientFailureMessage ?? ""
        try ensure(stopped.isExhaustedFailure && text.contains("已停止") && text.contains("任务已保留") && text.contains("检查服务") && text.contains("新版本"), "Exhausted copy omitted truthful next step")
        try ensure(!text.contains("稍后重试") && !text.contains("仍会继续"), "Exhausted copy exposed misleading retry/background wording")
        let retry = try JSONDecoder().decode(CourseJob.self, from: Data(terminalJSON("failed", retryable: true).utf8))
        try ensure(!retry.isExhaustedFailure && retry.clientFailureMessage == retry.error, "Retryable failed task lost its explicit retry context")
    }
    @MainActor
    private static func terminalCompletedRevokedNoHistory(_ suiteRoot: URL) async throws {
        let completed = try terminalJSON("succeeded", embeddedCourse: courseJSON)
        let (model, transport, root, config) = try await setup("terminalRevokedAbsent", suiteRoot: suiteRoot,
            steps: [.get(jobPath, 200, completed), .get(cachePath, 409, #"{"detail":"current publication revoked"}"#)])
        try seedTask(model); await model.checkCourseTask(book: "b1", chapter: "ch1")
        try ensure(model.state.courseJobs[courseKey]?.status == "succeeded", "Task state was not saved")
        try ensure(model.state.courses[courseKey] == nil && model.state.courseValidation?[courseKey] == "invalid" && model.state.courseConflicts?[courseKey] != nil, "Old completed job payload restored a revoked course or enabled scoring")
        let saved = try await DiskStore(root: root.appendingPathComponent(config.namespace)).load(StoredState.self, from: "state.json")
        try ensure(saved?.courses[courseKey] == nil && saved?.courseValidation?[courseKey] == "invalid", "Revoked missing course became verified on disk")
        try await verifyPersistedID(root, config, "terminal-original-request"); try await verifyOnlyGET(transport)
    }
    @MainActor
    private static func terminalCompletedRevokedHistory(_ suiteRoot: URL) async throws {
        let oldPayload = courseJSON.replacingOccurrences(of: "approved1", with: "untrusted-job-payload")
        let (model, transport, _, _) = try await setup("terminalRevokedHistory", suiteRoot: suiteRoot,
            steps: [.get(jobPath, 200, try terminalJSON("succeeded", embeddedCourse: oldPayload)), .get(cachePath, 409)])
        try seedCourse(model); try seedTask(model); await model.checkCourseTask(book: "b1", chapter: "ch1")
        try ensure(model.state.courses[courseKey]?.course_id == "approved1" && model.state.courseValidation?[courseKey] == "invalid", "Revoked payload replaced history or left scoring enabled")
        try await verifyOnlyGET(transport)
    }
    @MainActor
    private static func terminalCompletedCurrentCache(_ suiteRoot: URL) async throws {
        let current = courseJSON.replacingOccurrences(of: "approved1", with: "current-cache-only")
        let oldPayload = courseJSON.replacingOccurrences(of: "approved1", with: "untrusted-job-payload")
        let (model, transport, _, _) = try await setup("terminalCurrent", suiteRoot: suiteRoot,
            steps: [.get(jobPath, 200, try terminalJSON("succeeded", embeddedCourse: oldPayload)), .get(cachePath, 200, current)])
        try seedTask(model); await model.checkCourseTask(book: "b1", chapter: "ch1")
        try ensure(model.state.courses[courseKey]?.course_id == "current-cache-only" && model.state.courseValidation?[courseKey] == "verified", "Successful task did not use current cachedCourse GET")
        try await verifyOnlyGET(transport)
    }
    @MainActor
    private static func terminalCompletedCacheUnavailable(_ suiteRoot: URL) async throws {
        let (model, transport, _, _) = try await setup("terminalCacheUnavailable", suiteRoot: suiteRoot,
            steps: [.get(jobPath, 200, try terminalJSON("succeeded", embeddedCourse: courseJSON)), .get(cachePath, 503)])
        try seedTask(model); await model.checkCourseTask(book: "b1", chapter: "ch1")
        try ensure(model.state.courses[courseKey] == nil && model.state.courseValidation?[courseKey] != "verified", "Unavailable current cache was replaced by embedded job course")
        try ensure(model.courseValidationMessages[courseKey]?.contains("暂不展示或提交评分") == true, "Missing course cache failed without truthful validation notice")
        try await verifyOnlyGET(transport)
    }
    @MainActor
    private static func terminalIdentityMismatch(_ suiteRoot: URL) async throws {
        for (index, change) in [("job_id", "wrong-job"), ("session_id", "wrong-session"), ("chapter_id", "wrong-chapter")].enumerated() {
            var object = try JSONSerialization.jsonObject(with: Data(terminalJSON("succeeded", embeddedCourse: courseJSON).utf8)) as! [String: Any]
            object[change.0] = change.1
            let mismatch = String(data: try JSONSerialization.data(withJSONObject: object), encoding: .utf8)!
            let (model, transport, _, _) = try await setup("terminalMismatch\(index)", suiteRoot: suiteRoot, steps: [.get(jobPath, 200, mismatch)])
            try seedTask(model); await model.checkCourseTask(book: "b1", chapter: "ch1")
            try ensure(model.state.courseJobs[courseKey]?.status == "failed" && model.state.courses[courseKey] == nil && model.error?.contains("不匹配") == true, "Mismatched task was accepted or restored a course")
            try await verifyOnlyGET(transport)
        }
    }
    @MainActor
    private static func terminalLateTask(_ suiteRoot: URL) async throws {
        let held = Step(method: "GET", path: jobPath, status: 200, body: try terminalJSON("succeeded", embeddedCourse: courseJSON), holdForRelease: true)
        let (model, transport, _, _) = try await setup("terminalLateTask", suiteRoot: suiteRoot, steps: [held])
        try seedTask(model)
        let lookup = Task { await model.checkCourseTask(book: "b1", chapter: "ch1") }
        while !(await transport.isHeld()) { await Task.yield() }
        model.state.courseRequestIDs?[courseKey] = "newer-intent"
        await transport.releaseHeld(); await lookup.value
        try ensure(model.state.courseJobs[courseKey]?.status == "failed" && model.state.courses[courseKey] == nil, "Delayed task response overwrote newer request context")
        try await verifyOnlyGET(transport)
    }
    @MainActor
    private static func terminalLateCurrentCache(_ suiteRoot: URL) async throws {
        let held = Step(method: "GET", path: cachePath, status: 200, body: courseJSON, holdForRelease: true)
        let (model, transport, _, _) = try await setup("terminalLateCache", suiteRoot: suiteRoot,
            steps: [.get(jobPath, 200, try terminalJSON("succeeded", embeddedCourse: courseJSON)), held])
        try seedTask(model)
        let lookup = Task { await model.checkCourseTask(book: "b1", chapter: "ch1") }
        while !(await transport.isHeld()) { await Task.yield() }
        model.state.courseRequestIDs?[courseKey] = "newer-intent"
        await transport.releaseHeld(); await lookup.value
        try ensure(model.state.courses[courseKey] == nil && model.state.courseValidation?[courseKey] != "verified", "Delayed current-cache result enabled scoring in newer request context")
        try await verifyOnlyGET(transport)
    }
    @MainActor
    private static func terminalExplicitRetrySameID(_ suiteRoot: URL) async throws {
        let (model, transport, root, config) = try await setup("terminalExplicitRetry", suiteRoot: suiteRoot, steps: [.post(202)])
        try seedTask(model, retryable: true); await model.requestCourse(book: "b1", chapter: "ch1", retry: true)
        let posts = (await transport.observations()).filter { $0.method == "POST" }
        try ensure(posts.count == 1 && posts.first?.requestID == "terminal-original-request", "Explicit retry did not preserve original request identity")
        try verifyPostsCommitted(posts); try await verifyPersistedID(root, config, "terminal-original-request"); try await verifyTransport(transport)
    }
}
