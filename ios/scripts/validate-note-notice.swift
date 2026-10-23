import Foundation

private struct Failure: Error { let message: String }
private func check(_ value: @autoclosure () -> Bool, _ message: String) throws {
    if !value() { throw Failure(message: message) }
}
private func result(_ object: [String: Any]) throws -> StudioResult {
    try JSONDecoder().decode(StudioResult.self, from: JSONSerialization.data(withJSONObject: object))
}

@main private struct NoticeHostCheck {
    static func main() throws {
        let message = "已保留原笔记，部分补充因依据不足未加入。"
        let text = "用户原文通常如此。个人记录：我们同时记录响应时间。"
        var checks: [String] = []
        let legacy = try result(["transcript": text, "summary": "原有结果", "polished": text])
        try check(legacy.quality_notice == nil && legacy.excluded_optional_count == nil && legacy.provisional == nil,
                  "Old JSON must decode with absent optional fields")
        try check(legacy.qualityDisclosure(status: "succeeded") == nil, "Old success must not invent omitted supplements")
        checks.append("legacy_json_compatible")

        let completed = try result(["transcript": text, "polished": text, "quality_notice": message,
                                    "excluded_optional_count": 3, "review": ["passed": true]])
        try check(completed.qualityDisclosure(status: "succeeded") == message, "Use the fixed server notice verbatim")
        try check(completed.excluded_optional_count == 3 && completed.polished == text, "Count is server metadata, original text unchanged")
        let roundTrip = try JSONDecoder().decode(StudioResult.self, from: JSONEncoder().encode(completed))
        try check(roundTrip.quality_notice == message && roundTrip.excluded_optional_count == 3 && roundTrip.review?.passed == true,
                  "Round-trip must retain public notice/count/verdict")
        checks.append("accepted_notice_count_roundtrip")

        for status in ["queued", "running", "failed", "uncertain", "cancelled"] {
            let warning = completed.qualityDisclosure(status: status)
            try check(warning != nil && warning != message && warning?.contains("尚未通过") == true,
                      "Non-success must show an incomplete-review warning, never the final notice")
        }
        let provisional = try result(["polished": text, "quality_notice": message, "excluded_optional_count": 2, "provisional": true])
        try check(provisional.qualityDisclosure(status: "succeeded")?.contains("尚未通过") == true,
                  "Provisional flag wins over an inconsistent success status")
        let rejected = try result(["polished": text, "quality_notice": "已完整审核成功", "excluded_optional_count": 2,
                                  "review": ["passed": false]])
        try check(rejected.qualityDisclosure(status: "succeeded")?.contains("已完整审核成功") == false,
                  "A false review must not surface a success claim")
        try check(rejected.qualityDisclosure(status: "succeeded")?.contains("尚未通过") == true, "False verdict remains incomplete")
        checks.append("failed_running_provisional_false_gates")

        let countOnly = try result(["polished": text, "excluded_optional_count": 2])
        try check(countOnly.qualityDisclosure(status: "succeeded")?.contains("可选补充") == true,
                  "A positive exclusion count cannot silently lose its disclosure if notice is absent")
        let mediaLegacy = try result(["explanation": "示意图", "review": "AI 已复核；辅助示意不能替代教材。"])
        let mediaCopy = try JSONDecoder().decode(StudioResult.self, from: JSONEncoder().encode(mediaLegacy))
        try check(mediaCopy.review?.passed == nil && mediaCopy.qualityDisclosure(status: "succeeded") == nil,
                  "Existing string media review must remain compatible")
        checks.append("count_only_notice_and_legacy_media_review")

        let payload: [String: Any] = ["id": "job", "book_id": "book", "kind": "improve", "status": "succeeded",
                                     "result": ["transcript": text, "polished": text, "quality_notice": message, "excluded_optional_count": 2],
                                     "error": "", "note_id": "note", "revision": 2]
        let job = try JSONDecoder().decode(StudioJob.self, from: JSONSerialization.data(withJSONObject: payload))
        try check(job.result.qualityDisclosure(status: job.status) == message && !job.isPending, "GET job result must expose notice")
        let notePayload: [String: Any] = ["id": "note", "book_id": "book", "title": "已存笔记", "revision": 2,
                                         "input_mode": "voice", "edition": payload["result"]!]
        let note = try JSONDecoder().decode(Note.self, from: JSONSerialization.data(withJSONObject: notePayload))
        try check(note.edition?.qualityDisclosure(status: "succeeded") == message, "Saved edition route must expose the same notice")
        var draft = NoteDraft(bookID: "book"); draft.remoteNote = note; draft.text = "独立草稿不改写"
        let restored = try JSONDecoder().decode(NoteDraft.self, from: JSONEncoder().encode(draft))
        try check(restored.remoteNote?.edition?.quality_notice == message && restored.remoteNote?.edition?.excluded_optional_count == 2,
                  "Saved remote edition notice must survive local note persistence")
        try check(restored.text == draft.text, "Notice handling must not overwrite draft text")
        checks.append("get_job_saved_edition_and_draft_persistence")

        for invalid: [String: Any] in [["excluded_optional_count": true], ["excluded_optional_count": "2"], ["quality_notice": 3], ["review": ["passed": "false"]]] {
            var threw = false
            do { _ = try result(invalid) } catch { threw = true }
            try check(threw, "Malformed typed metadata must not decode as an accepted notice")
        }
        checks.append("typed_metadata_rejects_bool_or_string_count")
        let report: [String: Any] = ["passed": true, "checks": checks, "network_calls": 0, "model_calls": 0,
                                    "ios_build_verified": false, "iphone_ui_verified": false,
                                    "scope": "Patched real LearningCore models and presentation helper compiled on macOS; no AppModel/APIClient mutation"]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: URL(fileURLWithPath: CommandLine.arguments[1]), options: .atomic)
        print("Native notice host checks passed (offline macOS only).")
    }
}
