import Foundation

/// Shipped course listings are independent of a learner's imported workspaces.
enum BuiltinCourseCatalog {
  static func merging(_ remote: [CommunityBook]) throws -> [CommunityBook] {
    var entries = try ContentBundle.load([CommunityBook].self, name: "builtin-catalog")
    for book in remote {
      if let index = entries.firstIndex(where: { $0.id == book.id }) {
        entries[index] = book
      } else {
        entries.append(book)
      }
    }
    return entries
  }
}

enum CloudMapping {
  static func citationID(page: Int, quote: String, blockID: String?) -> String {
    // A UI anchor identity, not a fabricated server retrieval chunk identifier.
    blockID.flatMap { $0.isEmpty ? nil : $0 } ?? "page:\(page):\(quote)"
  }
  static func citation(_ value: CloudCitation) -> LessonCitation {
    LessonCitation(
      chunk_id: citationID(page: value.page_number, quote: value.quote, blockID: value.block_id),
      page_start: value.page_number, page_end: value.page_number, quote: value.quote)
  }
  static func reviewedCard(_ card: Flashcard, resource: CloudResource) -> Flashcard {
    guard let state = resource.flashcard_reviews?[card.id] else { return card }
    var result = card
    // Keep the existing due-date pill; only replace its data.
    if let value = state.due_at {
      let formatter = ISO8601DateFormatter()
      formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
      let date = formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
      if let date {
        result.due =
          date <= .now || Calendar.current.isDateInToday(date)
          ? "today" : date.formatted(.dateTime.month().day())
      }
    }
    return result
  }

  static func source(_ resource: CloudResource) -> SourceContent {
    if var content = resource.source_content {
      for i in content.plan.tasks.indices {
        let task = content.plan.tasks[i]
        content.plan.tasks[i].status =
          resource.progress.task_status[task.id]
          ?? (task.chapter_id.map { resource.progress.completed_chapters.contains($0) } == true
            ? "done" : task.status)
      }
      content.flashcards = content.flashcards.map { reviewedCard($0, resource: resource) }
      return content
    }
    let bookID = resource.book_id
    let structure = resource.structure
    let chapters = (structure?.chapters ?? []).map { chapter in
      Chapter(
        chapter_id: chapter.chapter_id, level: 1, source_title: chapter.title,
        ai_title: chapter.title,
        page_start: chapter.start_page, page_end: chapter.end_page, confidence: 100,
        status: "confirmed", source: "server")
    }
    var lessons: [Lesson] = []
    var cards: [Flashcard] = []
    var quizzes: [QuizQuestion] = []
    var tasks: [StudyTask] = []
    for chapter in structure?.chapters ?? [] {
      let bundle = resource.courses.first { $0.chapter_id == chapter.chapter_id }
      let lessonID = bundle?.course_id ?? "pending:\(resource.id):\(chapter.chapter_id)"
      var blocks: [LessonBlock] = []
      if let bundle {
        if !bundle.opening.isEmpty {
          blocks.append(
            LessonBlock(
              block_id: lessonID + ":opening", block_type: "explanation", title: "学习导引",
              content: bundle.opening, citations: [], source_chunk_ids: [], asset_ids: [],
              ai_generated: true))
        }
        for (index, section) in (bundle.original_reading + bundle.worked_examples).enumerated() {
          let citations = section.citations.map(citation)
          blocks.append(
            LessonBlock(
              block_id: lessonID + ":section:\(index)", block_type: "explanation",
              title: section.title, content: section.content, citations: citations,
              source_chunk_ids: citations.map(\.chunk_id), asset_ids: [], ai_generated: true))
        }
        for (index, point) in bundle.knowledge_points.enumerated() {
          let citations = point.citations.map(citation)
          blocks.append(
            LessonBlock(
              block_id: lessonID + ":point:\(index)", block_type: "knowledge",
              title: point.title, content: point.explanation, citations: citations,
              source_chunk_ids: citations.map(\.chunk_id), asset_ids: [], ai_generated: true))
        }
        cards += bundle.flashcards.map { card in
          Flashcard(
            card_id: card.card_id, book_id: bookID, lesson_id: lessonID,
            chapter_id: chapter.chapter_id,
            front: card.front, back: card.back, concept: card.point_id,
            source_chunk_ids: card.citations.map {
              citationID(page: $0.page_number, quote: $0.quote, blockID: $0.block_id)
            },
            page_start: card.citations.first?.page_number ?? chapter.start_page,
            page_end: card.citations.last?.page_number ?? chapter.end_page, mastery: 0,
            reason: card.reason_for_user)
        }
        quizzes += bundle.practice_items.map { item in
          // Correct answers remain server-owned. Online submissions never use local grading.
          QuizQuestion(
            question_id: item.item_id, book_id: bookID, lesson_id: lessonID,
            chapter_id: chapter.chapter_id,
            prompt: item.prompt, choices: item.options, answer: "", explanation: "",
            concept: item.point_id,
            source_chunk_ids: [], page_start: 0, page_end: 0,
            question_type: item.options.isEmpty ? "short-answer" : "choice", source_kind: "server")
        }
      }
      lessons.append(
        Lesson(
          book_id: bookID, lesson_id: lessonID, chapter_id: chapter.chapter_id,
          title: bundle?.chapter_title ?? chapter.title, source_title: chapter.title,
          page_start: chapter.start_page, page_end: chapter.end_page, lesson_kind: "chapter",
          status: bundle == nil ? "pending" : "ready", confidence: 100,
          objectives: bundle?.knowledge_points.map(\.title) ?? chapter.knowledge_points,
          key_concepts: chapter.knowledge_points, summary: bundle?.summary ?? chapter.summary,
          blocks: blocks,
          source_chunk_ids: chapter.source_block_ids, asset_ids: [],
          warnings: bundle?.unresolved_source_warnings ?? []))
      let taskID = "task:\(resource.id):\(chapter.chapter_id)"
      tasks.append(
        StudyTask(
          task_id: taskID, user_id: "", day: tasks.count + 1, title: chapter.title,
          task_type: "lesson", minutes: bundle?.estimated_minutes ?? 25,
          chapter_id: chapter.chapter_id,
          lesson_id: lessonID,
          status: resource.progress.task_status[taskID]
            ?? (resource.progress.completed_chapters.contains(chapter.chapter_id)
              ? "done" : "pending"),
          weak_points: []))
    }
    return SourceContent(
      summary: SourceSummary(
        book_id: bookID, title: structure?.title ?? resource.filename,
        filename: resource.filename, status: structure == nil ? resource.status : "ready",
        page_count: structure?.source_page_count ?? 0, chapter_count: chapters.count,
        chunk_count: 0,
        asset_count: 0, average_confidence: 100, updated_at: Date().timeIntervalSince1970,
        content_mode: "remote"),
      chapters: chapters, lessons: lessons, chunks: [], assets: [],
      flashcards: cards.map { reviewedCard($0, resource: resource) },
      quizzes: quizzes,
      plan: StudyPlan(
        user_id: "", book_id: bookID, days: max(1, chapters.count), daily_minutes: 30, tasks: tasks)
    )
  }
}
