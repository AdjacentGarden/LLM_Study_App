import Foundation

/// One persisted transition per call. Polling does not restart OCR or failed jobs.
public actor ImportWorkflow {
    private let api: APIClient; private let disk: DiskStore
    public init(api: APIClient, disk: DiskStore) { self.api = api; self.disk = disk }
    public func recover(_ task: ImportTask) async throws -> ImportTask {
        var saved = try await disk.load(ImportTask.self, from: "import-\(task.id).json") ?? task
        if saved.stage == "uploading" {
            saved.stage = "upload_uncertain"
            saved.error = "上次上传的回执未保存，PDF 仍在本机。请确认后重新上传；服务端会按文件内容检查重复教材。"
            try await persist(saved)
        }
        return saved
    }
    private func persist(_ task: ImportTask) async throws { try await disk.save(task, as: "import-\(task.id).json") }
    public func advance(_ original: ImportTask, retry: Bool = false) async -> ImportTask {
        var task = original
        task.error = nil
        do {
            switch task.stage {
            case "staged", "upload_uncertain":
                guard task.stage == "staged" || retry else { return original }
                task.stage = "uploading"; task.message = "正在上传 PDF"; try await persist(task)
                let file = try await disk.file(task.localFile)
                let uploaded = try await api.uploadPDF(file: file, filename: task.filename)
                task.bookID = uploaded.book_id; task.stage = "uploaded"; task.message = "PDF 已上传，正在关联书架"; task.progress = 0.1
            case "uploaded":
                guard let book = task.bookID else { throw ClientError.invalidResponse("缺少上传教材编号") }
                try await api.bind(book); task.stage = "bound"; task.message = "已关联教材，准备读取"
            case "bound":
                guard let book = task.bookID else { throw ClientError.invalidResponse("缺少上传教材编号") }
                _ = try await api.process(book); task.stage = "ocr"; task.message = "正在识别教材"; task.progress = 0
            case "ocr":
                guard let book = task.bookID else { throw ClientError.invalidResponse("缺少上传教材编号") }
                var status = try await api.bookStatus(book)
                if retry && status.retryable { status = try await api.process(book, retry: true) }
                task.progress = min(1, max(0, status.progress > 1 ? status.progress / 100 : status.progress))
                task.message = status.current_step; task.retryable = status.retryable
                if ["ocr_ready", "structured"].contains(status.status) {
                    _ = try await api.prepare(book); task.stage = "preparing"; task.message = "识别完成，正在整理章节与诊断题"
                } else if status.needs_human_review == true {
                    task.error = "教材识别需要核对：\(status.current_step)"; task.retryable = false
                } else if status.retryable || status.status == "failed" { task.error = "教材读取未完成：\(status.current_step)" }
            case "preparing", "canonical_preparing":
                guard let book = task.stage == "canonical_preparing" ? task.canonicalBookID : task.bookID else { throw ClientError.invalidResponse("缺少整理教材编号") }
                let job = retry ? try await api.prepare(book, retry: true) : try await api.preparation(book)
                task.message = "整理教材：\(job.stage)"; task.progress = 0.7
                if job.status == "failed" { task.error = job.error; task.retryable = true }
                if job.status == "succeeded" { task.stage = "claiming"; task.message = "正在加入书架"; task.retryable = false }
            case "claiming":
                guard let book = task.bookID else { throw ClientError.invalidResponse("缺少上传教材编号") }
                let canonical = try await api.claim(book); task.canonicalBookID = canonical.book_id
                if canonical.diagnostics_ready == true {
                    task.stage = "ready"; task.message = "教材已就绪"; task.progress = 1; task.retryable = false
                } else {
                    _ = try await api.prepare(canonical.book_id)
                    task.stage = "canonical_preparing"; task.message = "正在更新已有教材的诊断题"
                }
            default: return task
            }
        } catch {
            if task.stage == "uploading" { task.stage = "upload_uncertain" }
            task.error = error.localizedDescription
            task.retryable = true
        }
        do { try await persist(task) } catch { task.error = "本地进度无法保存：\(error.localizedDescription)" }
        return task
    }
}
