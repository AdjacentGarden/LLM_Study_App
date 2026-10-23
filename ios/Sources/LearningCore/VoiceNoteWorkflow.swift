import Foundation

public struct VoiceNoteContext: Codable, Sendable, Equatable {
    public var noteID: String
    public var revision: Int
    public var recordingID: String?
}

/// Human review is deliberately separate from the independent prose draft.
public struct VoiceTranscriptReview: Codable, Sendable {
    public var context: VoiceNoteContext
    public var sourceJobID: String
    public var text: String
    public var confirmed: Bool

    public static func validate(_ text: String) throws {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.unicodeScalars.count >= 2, text.unicodeScalars.count <= 12000,
              !text.contains("[待确认]") else {
            throw ClientError.configuration("请逐字核对转写，补全待确认处；确认文字需为 2–12000 字符，普通笔记草稿不受此限制。")
        }
    }
}

/// This exact body is saved before POST and reused after a lost response.
public struct NoteAnalysisSubmission: Codable, Sendable {
    public var context: VoiceNoteContext
    public var requestID: String
    public var action: String
    public var confirmedTranscript: String?
    public var jobID: String?
    public var jobStatus: String?
    public var isUnacknowledged: Bool { jobID == nil }
    public var isPending: Bool {
        guard jobID != nil, let jobStatus else { return true }
        return !["succeeded", "failed", "needs_confirmation", "uncertain", "cancelled"].contains(jobStatus)
    }
}

public extension NoteDraft {
    var voiceContext: VoiceNoteContext? {
        guard uploadComplete, let note = remoteNote, note.input_mode == "voice", note.revision > 0 else { return nil }
        return VoiceNoteContext(noteID: note.id, revision: note.revision, recordingID: recordingID)
    }
    var voiceAnalysisLocked: Bool {
        if let submission = analysisSubmission, submission.context == voiceContext { return submission.isPending }
        return analysisSubmission == nil && analysisRequestID != nil && analysisJobID == nil
    }
    mutating func clearVoiceAnalysis() {
        transcriptReview = nil; analysisSubmission = nil
        analysisRequestID = nil; analysisJobID = nil
    }
    mutating func replaceVoiceRecording(with newID: String?) throws {
        guard recordingID != newID else { return }
        guard !voiceAnalysisLocked else { throw ClientError.configuration("当前任务仍在处理或回执未知；请先恢复任务，或另建笔记录音。") }
        uploadComplete = false; clearVoiceAnalysis(); recordingID = newID
    }
    /// r3 had only audio-driven actions, whose immutable transcript is empty.
    mutating func restoreLegacyVoiceSubmission() {
        guard analysisSubmission == nil, let context = voiceContext, let request = analysisRequestID,
              ["recognize", "complete"].contains(analysisAction ?? "recognize") else { return }
        analysisSubmission = NoteAnalysisSubmission(context: context, requestID: request,
            action: analysisAction ?? "recognize", confirmedTranscript: nil,
            jobID: analysisJobID, jobStatus: nil)
    }
    mutating func beginVoiceTranscriptReview(_ job: StudioJob) throws {
        guard !voiceAnalysisLocked, let context = voiceContext,
              job.note_id == context.noteID, job.revision == context.revision,
              ["recognize", "complete", "improve"].contains(job.kind),
              ["succeeded", "needs_confirmation"].contains(job.status),
              let text = job.result.transcript, !text.isEmpty else {
            throw ClientError.configuration("请先恢复正在处理的任务，并选择当前录音版本的转写。")
        }
        transcriptReview = VoiceTranscriptReview(context: context, sourceJobID: job.id, text: text, confirmed: false)
    }
    mutating func updateVoiceTranscriptReview(text: String, confirmed: Bool) throws {
        guard !voiceAnalysisLocked, var review = transcriptReview, review.context == voiceContext else {
            throw ClientError.configuration("这份转写不属于当前录音，或上次提交尚未确认；请先恢复任务。")
        }
        let changed = review.text != text
        review.text = text
        // Editing and confirmation are separate actions: an edit revokes consent.
        review.confirmed = changed ? false : confirmed
        if review.confirmed { try VoiceTranscriptReview.validate(text) }
        transcriptReview = review
    }
    mutating func prepareVoiceAnalysis(action: String, retry: Bool = false) throws -> NoteAnalysisSubmission {
        guard ["recognize", "complete", "improve"].contains(action), let context = voiceContext else {
            throw ClientError.configuration("请先确认当前录音已上传。")
        }
        restoreLegacyVoiceSubmission()
        let transcript: String?
        if action == "improve" {
            guard let review = transcriptReview, review.context == context, review.confirmed else {
                throw ClientError.configuration("请核对当前版本的转写，并勾选明确确认后再整理。")
            }
            try VoiceTranscriptReview.validate(review.text)
            transcript = review.text
        } else { transcript = nil }
        if let existing = analysisSubmission, existing.context == context {
            let sameBody = existing.action == action && existing.confirmedTranscript == transcript
            if existing.isPending {
                guard sameBody else { throw ClientError.configuration("上次提交仍在处理或回执未知；请先按原编号恢复，不能更换内容。") }
                return existing
            }
            if sameBody && !retry { return existing }
        }
        let submission = NoteAnalysisSubmission(context: context, requestID: UUID().uuidString,
            action: action, confirmedTranscript: transcript, jobID: nil, jobStatus: nil)
        analysisSubmission = submission; analysisRequestID = submission.requestID
        analysisJobID = nil; analysisAction = action
        return submission
    }
    mutating func acknowledgeVoiceAnalysis(_ job: StudioJob, requestID: String) throws {
        guard var submission = analysisSubmission, submission.requestID == requestID,
              submission.context == voiceContext, job.note_id == submission.context.noteID,
              job.revision == submission.context.revision, job.kind == submission.action else {
            throw ClientError.invalidResponse("任务回执不属于当前录音版本，已保留本机确认内容。")
        }
        submission.jobID = job.id; submission.jobStatus = job.status
        analysisSubmission = submission; analysisJobID = job.id
    }
}
