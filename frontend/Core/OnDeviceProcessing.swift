import Foundation

extension AppStore {
  /// An on-device import never downloads a missing original or schedules a cloud
  /// preparation job. Only real page text is saved; courses are not fabricated.
  func processOnDeviceImport() async {
    let ids = draft.resourceIDs
    guard !ids.isEmpty else { importError = "请先选择一本教材。"; return }
    let epoch = cloudState.epoch, generation = selectionGeneration
    let job = ImportJob(bookIDs: ids, status: .running)
    importJob = job
    importError = nil
    processingProgress = 0
    processingMessage = "正在准备本机文字识别…"
    persist()
    for (index, id) in ids.enumerated() {
      do {
        try Task.checkCancellation()
        guard epoch == cloudState.epoch, importJob?.id == job.id else { return }
        guard let old = sources.first(where: { $0.id == id }), let file = sourceFileURL(id) else {
          throw CloudError(status: 409, message: "本机尚未保存教材原文件。请连接 Wi-Fi 打开原书、保存到本机后，再使用本机识别。")
        }
        let total = max(1, ids.count)
        let extracted = try await AppleDocumentOCR.extract(url: file, bookID: id,
          filename: old.summary.filename ?? file.lastPathComponent) { [weak self] progress in
            Task { @MainActor in
              guard let self, self.cloudState.epoch == epoch,
                self.importJob?.id == job.id, self.importJob?.status == .running else { return }
              self.processingProgress = (Double(index) + Double(progress.completedPages)
                / Double(max(1, progress.totalPages))) / Double(total)
              self.processingMessage = "本机识别 · 第 \(progress.currentPage)/\(progress.totalPages) 页"
              self.importJob?.progress = self.processingProgress
              self.importJob?.message = self.processingMessage
            }
          }
        try Task.checkCancellation()
        guard epoch == cloudState.epoch, importJob?.id == job.id else { return }
        var source = extracted
        if !old.chapters.isEmpty || !old.lessons.isEmpty {
          // Existing teaching content remains usable alongside refreshed local text.
          source = LocalBookCourseRepository.mergingLocalOCR(from: extracted, into: old)
        } else {
          source.summary.title = old.summary.title
          source.summary.cover_url = old.summary.cover_url
        }
        if let position = sources.firstIndex(where: { $0.id == id }) { sources[position] = source }
        processingProgress = Double(index + 1) / Double(total)
        persist()
        await persistTask?.value
        if let error = cloudState.cacheWriteError { throw ContentError.storage(error) }
      } catch is CancellationError { return }
      catch {
        guard epoch == cloudState.epoch, !Task.isCancelled, importJob?.id == job.id else { return }
        importError = error.localizedDescription
        processingMessage = "本机识别中断，原文件已保留"
        importJob?.status = .failed
        importJob?.message = processingMessage
        persist()
        showToast(error.localizedDescription)
        return
      }
    }
    guard epoch == cloudState.epoch, !Task.isCancelled, importJob?.id == job.id else { return }
    processingProgress = 1
    processingMessage = "本机文字识别完成，可阅读原文与检索"
    importJob?.status = .completed
    importJob?.progress = 1
    importJob?.message = processingMessage
    if draft.resourceIDs == ids { draft.parseCompleted = true }
    persist()
    if route == .processing, selectionGeneration == generation { replace(.courseDetail) }
  }

  func prepareLocalCourseOnWiFi(bookID: String) {
    guard processingPolicy.isStableWiFi else {
      showToast("请连接稳定 Wi-Fi 后，再继续生成课程。当前原文仍可在本机阅读和检索。")
      return
    }
    guard let source = sources.first(where: { $0.id == bookID }) else { return }
    let course = courses.first { $0.resourceIDs.contains(bookID) }
    selectSource(bookID)
    draft = CourseDraft(name: course?.name ?? source.summary.title, resourceIDs: course?.resourceIDs ?? [bookID],
      diagnosis: course?.diagnosis ?? [:], parseCompleted: false)
    draft.editingCourseID = course?.id
    importError = nil
    go(.parseReady)
    persist()
  }

  func answerFromOnDeviceTextbook(question: String, bookID: String, bookIDs: [String],
    sources: [SourceContent]) async throws -> AssistantAnswer {
    let scope = Set(bookIDs.isEmpty ? [bookID] : bookIDs)
    let result = try await appleTextbookSearch.search(query: question, sources: sources, bookIDs: scope)
    let capability = await appleTextbookSearch.capability()
    try Task.checkCancellation()
    guard !result.hits.isEmpty else {
      return AssistantAnswer(answer: (result.error ?? "没有找到相关教材原文。") + "\n\n" + capability.notice,
        citations: [])
    }
    let citations = result.hits.filter(\.reliable).map { hit in
      let chunk = hit.chunk
      let chapter = sources.first { $0.id == chunk.book_id }?.chapters.filter {
        $0.page_start <= chunk.page_start && $0.page_end >= chunk.page_start
      }.min { ($0.page_end - $0.page_start) < ($1.page_end - $1.page_start) }
      return Citation(bookID: chunk.book_id, chapterID: chapter?.id ?? "",
        chapterTitle: chapter?.title ?? "教材原文", page: chunk.page_start,
        chunkID: chunk.id, quote: chunk.text, score: hit.score)
    }
    let evidence = result.hits.prefix(3).enumerated().map { index, hit in
      let title = sources.first { $0.id == hit.chunk.book_id }?.summary.title ?? "教材"
      return "\(index + 1). \(title) · 第 \(hit.chunk.page_start) 页\n\(String(hit.chunk.text.prefix(600)))"
    }.joined(separator: "\n\n")
    return AssistantAnswer(answer: "本机教材检索结果：\n\n" + evidence + "\n\n"
      + capability.notice + " 可点击引用查看原文。", citations: citations)
  }
}
