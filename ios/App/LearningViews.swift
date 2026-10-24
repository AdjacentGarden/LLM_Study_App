import SwiftUI
#if canImport(LearningCore)
import LearningCore
#endif

struct CitationsView: View {
    let citations: [Citation]
    var bookID: String? = nil
    var body: some View {
        ForEach(Array(citations.enumerated()), id: \.offset) { _, citation in
            VStack(alignment: .leading, spacing: 4) {
                if let bookID {
                    NavigationLink("打开原书第 \(citation.page_number) 页") { PDFReaderView(bookID: bookID, title: "教材依据", page: citation.page_number) }.font(.caption)
                } else { Text("原书第 \(citation.page_number) 页").font(.caption).foregroundStyle(.indigo) }
                Text(citation.quote).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
            }.padding(10).frame(maxWidth: .infinity, alignment: .leading).background(.secondary.opacity(0.05), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

struct ChapterView: View {
    @EnvironmentObject var model: AppModel
    let book: Book
    let chapter: Chapter
    @State private var tab = "lesson"
    @State private var selectedUnit = "all"
    private var key: String? { model.key(book: book.book_id, chapter: chapter.chapter_id) }
    private var conflict: String? { key.flatMap { model.state.courseConflicts?[$0] } }
    private var course: Course? { conflict == nil && key.flatMap({ model.state.courseValidation?[$0] }) != "invalid" ? key.flatMap { model.state.courses[$0] } : nil }
    private var verified: Bool { key.flatMap { model.state.courseValidation?[$0] } == "verified" }
    private var submissionPending: Bool { key.flatMap { model.state.courseSubmissionPending?[$0] } == true }
    private var job: CourseJob? { key.flatMap { model.state.courseJobs[$0] } }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text(chapter.summary).textSelection(.enabled)
                NavigationLink("打开原书第 \(chapter.start_page) 页") { PDFReaderView(bookID: book.book_id, title: chapter.title, page: chapter.start_page) }
                if let course {
                    if !verified {
                        Text(key.flatMap { model.courseValidationMessages[$0] } ?? "此为本机历史课程，尚未完成联网核验。可阅读，复习评级和提交练习暂不可用。").font(.callout).foregroundStyle(.orange)
                        Button("仅核验服务端当前课程") { Task { await model.validateCachedCourse(book: book.book_id, chapter: chapter.chapter_id) } }.disabled(model.busy)
                    }
                    Text("约 \(course.estimated_minutes) 分钟 · 版本 \(course.version)").font(.caption).foregroundStyle(.secondary)
                    if submissionPending {
                        Text("上次更新请求回执未确认，历史课程仍可阅读。")
                        Button("重试同一更新请求") { Task { await model.requestCourse(book: book.book_id, chapter: chapter.chapter_id) } }.disabled(model.busy)
                    } else if job?.isPending == true {
                        ProgressView("正在按最新学习记录更新，已保存课程仍可阅读")
                    } else {
                        Button("按最新学习记录更新课程") { Task { await model.requestCourse(book: book.book_id, chapter: chapter.chapter_id, newRequest: true) } }.font(.caption).disabled(model.busy)
                    }
                    if job?.status == "failed" {
                        Text(job?.clientFailureMessage ?? "课程更新未完成").font(.caption).foregroundStyle(.red)
                        if job?.retryable == true { Button("重试这次更新") { Task { await model.requestCourse(book: book.book_id, chapter: chapter.chapter_id, retry: true) } }.disabled(model.busy) }
                        else { Button("查看任务状态") { Task { await model.checkCourseTask(book: book.book_id, chapter: chapter.chapter_id) } }.disabled(model.busy || !model.connected) }
                    }
                    ForEach(course.unresolved_source_warnings, id: \.self) { Text($0).font(.callout).foregroundStyle(.orange) }
                    Picker("学习方式", selection: $tab) {
                        Text("课程").tag("lesson"); Text("原文").tag("reading"); Text("闪卡").tag("cards"); Text("练习").tag("practice")
                    }.pickerStyle(.segmented)
                    switch tab {
                    case "reading":
                        ForEach(Array(course.original_reading.enumerated()), id: \.offset) { _, section in LessonSectionView(section: section, bookID: book.book_id) }
                        if course.original_reading.isEmpty { Text("当前课程没有原文选读段落，请以章节页码和下方已核验引用为准。") }
                        CitationsView(citations: chapter.evidence, bookID: book.book_id)
                    case "cards": FlashcardsView(book: book, course: course, canSubmit: verified)
                    case "practice": PracticeView(book: book, course: course, canSubmit: verified)
                    default:
                        Text(course.opening).font(.title3).textSelection(.enabled)
                        if let units = course.study_units, !units.isEmpty {
                            Picker("学习单元", selection: $selectedUnit) {
                                Text("全部知识点").tag("all")
                                ForEach(units) { unit in Text("\(unit.title) · \(unit.estimated_minutes) 分钟").tag(unit.unit_id) }
                            }
                        }
                        let scope = course.study_units?.first { $0.unit_id == selectedUnit }?.point_ids
                        ForEach(course.knowledge_points.filter { scope == nil || scope!.contains($0.point_id) }) { point in
                            VStack(alignment: .leading, spacing: 10) {
                                Text(point.title).font(.headline)
                                Text(point.explanation).textSelection(.enabled)
                                Text(point.importance).font(.caption).foregroundStyle(.secondary)
                                CitationsView(citations: point.citations, bookID: book.book_id)
                            }.padding().background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
                        }
                        ForEach(Array(course.worked_examples.enumerated()), id: \.offset) { _, section in LessonSectionView(section: section, bookID: book.book_id) }
                        if !course.checkpoint_questions.isEmpty {
                            Text("自查").font(.headline)
                            ForEach(course.checkpoint_questions, id: \.self) { Text("• " + $0) }
                        }
                        Text(course.summary).textSelection(.enabled)
                    }
                } else {
                    CitationsView(citations: chapter.evidence, bookID: book.book_id)
                    if submissionPending {
                        Text("准备请求的回执尚未确认，已保存请求编号；不会自动重新提交或生成新编号。")
                        Button("查询或重试同一准备请求") { Task { await model.requestCourse(book: book.book_id, chapter: chapter.chapter_id) } }.buttonStyle(.borderedProminent).disabled(model.busy || !model.connected)
                    } else if let conflict {
                        Text(conflict).foregroundStyle(.red)
                        Text("教材、诊断或课程版本的状态需要核对；不会自动重复提交。确认后可重新准备。")
                            .font(.caption).foregroundStyle(.secondary)
                        Button("按当前状态重新准备") { Task { await model.requestCourse(book: book.book_id, chapter: chapter.chapter_id, newRequest: true) } }.buttonStyle(.borderedProminent).disabled(model.busy || !model.available("courses"))
                        NavigationLink("核对或恢复学习诊断") { InterviewView(book: book) }
                    } else if let job {
                        VStack(alignment: .leading, spacing: 10) {
                            if job.isPending { ProgressView(); Text(job.stage == "queued" ? "课程已排队" : "正在生成并审核章节课程") }
                            if let failure = job.clientFailureMessage { Text(failure).foregroundStyle(.red) }
                            else if let error = job.error { Text(error).foregroundStyle(.red) }
                            if job.isPending { Text("任务已保存，离开页面后服务器仍会继续。").font(.caption).foregroundStyle(.secondary) }
                            if job.status == "failed", job.retryable {
                                Button("重试课程生成") { Task { await model.requestCourse(book: book.book_id, chapter: chapter.chapter_id, retry: true) } }.disabled(model.busy)
                            } else if job.isExhaustedFailure {
                                Button("查看任务状态") { Task { await model.checkCourseTask(book: book.book_id, chapter: chapter.chapter_id) } }.disabled(model.busy || !model.connected)
                            }
                        }
                    } else if model.state.sessions[book.book_id] == nil || model.interviews[book.book_id]?.phase != "complete" {
                        NavigationLink("先完成或恢复学习诊断") { InterviewView(book: book) }.buttonStyle(.borderedProminent)
                    } else {
                        Button("生成这一章的课程") { Task { await model.requestCourse(book: book.book_id, chapter: chapter.chapter_id) } }.buttonStyle(.borderedProminent).disabled(model.busy || !model.available("courses"))
                    }
                }
            }.padding()
        }.navigationTitle(chapter.title)
            .task { await model.validateCachedCourse(book: book.book_id, chapter: chapter.chapter_id) }
    }
}
struct LessonSectionView: View {
    let section: LessonSection
    var bookID: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(section.title).font(.headline); Text(section.content).textSelection(.enabled)
            if !section.purpose.isEmpty { Text(section.purpose).font(.caption).foregroundStyle(.secondary) }
            CitationsView(citations: section.citations, bookID: bookID)
        }
    }
}

struct FlashcardsView: View {
    @EnvironmentObject var model: AppModel
    let book: Book; let course: Course
    var canSubmit = true
    @State private var index = 0
    @State private var flipped = false
    @State private var eventID = UUID().uuidString
    @State private var pendingRating: String?
    @State private var started = Date()
    @State private var feedback: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if course.flashcards.indices.contains(index) {
                let card = course.flashcards[index]
                Text("\(index + 1) / \(course.flashcards.count)").font(.caption)
                Text(card.front).font(.title3).textSelection(.enabled)
                if flipped { Text(card.back).textSelection(.enabled); CitationsView(citations: card.citations, bookID: book.book_id) }
                Button(flipped ? "收起答案" : "翻开答案") { flipped.toggle() }.buttonStyle(.bordered)
                if flipped {
                    Text("这次记忆的感觉如何？").font(.headline)
                    ForEach([( "again", "需要再学"), ("hard", "有些困难"), ("good", "基本理解"), ("easy", "很熟悉")], id: \.0) { rating, label in
                        Button(label) { Task { await rate(card: card, rating: rating) } }.disabled(!canSubmit || model.busy || feedback != nil || (pendingRating != nil && pendingRating != rating))
                    }
                    if pendingRating != nil && feedback == nil { Text("回执未确认时，请重试同一反馈；事件编号保持不变。").font(.caption).foregroundStyle(.secondary) }
                }
                if let feedback { Text(feedback).foregroundStyle(.green) }
                HStack {
                    Button("上一张") { move(-1) }.disabled(index == 0 || model.busy || (pendingRating != nil && feedback == nil))
                    Spacer(); Button("下一张") { move(1) }.disabled(index + 1 >= course.flashcards.count || model.busy || (pendingRating != nil && feedback == nil))
                }
            } else { ContentUnavailableView("当前章节暂无闪卡", systemImage: "rectangle.on.rectangle") }
        }.task(id: index) { restorePending() }
    }
    private func move(_ delta: Int) { index += delta; flipped = false; eventID = UUID().uuidString; pendingRating = nil; feedback = nil; started = Date() }
    private func rate(card: Flashcard, rating: String) async {
        pendingRating = rating
        await model.perform {
            guard let api = model.api, let session = model.state.sessions[book.book_id] else { throw ClientError.configuration("请先恢复学习会话") }
            let key = session + ":" + course.course_id + ":" + card.card_id
            let submission = model.state.pendingReviews?[key] ?? ReviewSubmission(eventID: eventID, rating: rating, seconds: Date().timeIntervalSince(started))
            if model.state.pendingReviews == nil { model.state.pendingReviews = [:] }
            model.state.pendingReviews?[key] = submission; try await model.persist()
            _ = try await api.review(session: session, course: course.course_id, card: card.card_id, rating: submission.rating, eventID: submission.eventID, seconds: submission.seconds)
            model.state.pendingReviews?.removeValue(forKey: key); try await model.persist()
            feedback = "复习反馈已保存"
        }
    }
    private func restorePending() {
        guard course.flashcards.indices.contains(index), let session = model.state.sessions[book.book_id],
              let pending = model.state.pendingReviews?[session + ":" + course.course_id + ":" + course.flashcards[index].card_id] else { return }
        eventID = pending.eventID; pendingRating = pending.rating; flipped = true
    }
}

struct PracticeView: View {
    @EnvironmentObject var model: AppModel
    let book: Book; let course: Course
    var canSubmit = true
    @State private var index = 0
    @State private var input = ""
    @State private var selected: [String] = []
    @State private var confidence = 0.65
    @State private var started = Date()
    @State private var eventID = UUID().uuidString
    @State private var pendingAnswer: Answer?
    @State private var activity: CourseActivity?
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if course.practice_items.indices.contains(index) {
                let item = course.practice_items[index]
                Text("练习 \(index + 1) / \(course.practice_items.count)").font(.caption)
                Text(item.prompt).font(.title3).textSelection(.enabled)
                ForEach(Array(item.options.enumerated()), id: \.offset) { offset, option in
                    let id = String(offset)
                    Button {
                        if item.response_type == "multiple_choice" {
                            if selected.contains(id) { selected.removeAll { $0 == id } } else { selected.append(id) }
                        } else { selected = [id] }
                    } label: { Label(option, systemImage: selected.contains(id) ? "checkmark.circle.fill" : "circle") }
                        .disabled(pendingAnswer != nil || activity != nil)
                }
                if item.options.isEmpty { TextField("写下你的解答", text: $input, axis: .vertical).textFieldStyle(.roundedBorder).lineLimit(4...12).disabled(pendingAnswer != nil || activity != nil) }
                Text("把握程度：\(Int(confidence * 100))% ").font(.caption)
                Slider(value: $confidence, in: 0...1).disabled(pendingAnswer != nil || activity != nil)
                if let activity {
                    if activity.evidence.scoring_confidence <= 0 {
                        Text("本次未能完成评分").font(.headline)
                        Text("学习掌握记录未更新，这个结果不代表答错。")
                    } else {
                        let tentative = activity.evidence.needs_follow_up || activity.evidence.scoring_confidence < 0.7
                        Text("\(tentative ? "初步判断得分" : "本次得分") \(Int(activity.evidence.score * 100))% · 评估置信度 \(Int(activity.evidence.scoring_confidence * 100))%").font(.headline)
                        ForEach(activity.evidence.matched_rubric, id: \.self) { Text("已覆盖：" + $0) }
                        ForEach(activity.evidence.missing_rubric, id: \.self) { Text("再想一想：" + $0) }
                        if tentative { Text("需要后续证据确认，这次评分不代表最终掌握。") }
                    }
                    if activity.course_stale { Text("当前学习记录与课程版本可能不同；本页为已保存的课程。").font(.caption).foregroundStyle(.secondary) }
                } else {
                    Button(pendingAnswer == nil ? "提交解答" : "重试同一份解答") { Task { await submit(item) } }.buttonStyle(.borderedProminent).disabled(!canSubmit || model.busy || (input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && selected.isEmpty))
                }
                if index + 1 < course.practice_items.count { Button("下一题") { index += 1; reset() }.disabled(model.busy || (pendingAnswer != nil && activity == nil)) }
            } else { ContentUnavailableView("当前章节暂无练习", systemImage: "pencil.and.list.clipboard") }
        }.task(id: index) { restorePending() }
    }
    private func reset() { input = ""; selected = []; confidence = 0.65; activity = nil; pendingAnswer = nil; eventID = UUID().uuidString; started = Date() }
    private func submit(_ item: PracticeItem) async {
        if pendingAnswer == nil {
            let labels = selected.compactMap { id -> String? in guard let i = Int(id) else { return nil }; return item.options.indices.contains(i) ? item.options[i] : nil }
            pendingAnswer = Answer(eventID: eventID, answer: input.isEmpty ? labels.joined(separator: "、") : input, selected: selected, confidence: confidence, seconds: Date().timeIntervalSince(started))
        }
        await model.perform {
            guard let api = model.api, let session = model.state.sessions[book.book_id], let answer = pendingAnswer else { throw ClientError.configuration("请先恢复学习会话") }
            let key = session + ":" + course.course_id + ":" + item.item_id
            if model.state.pendingAnswers == nil { model.state.pendingAnswers = [:] }
            model.state.pendingAnswers?[key] = answer; try await model.persist()
            activity = try await api.practice(session: session, course: course.course_id, item: item.item_id, answer: answer)
            model.state.pendingAnswers?.removeValue(forKey: key); try await model.persist()
        }
    }
    private func restorePending() {
        guard course.practice_items.indices.contains(index), let session = model.state.sessions[book.book_id],
              let pending = model.state.pendingAnswers?[session + ":" + course.course_id + ":" + course.practice_items[index].item_id] else { return }
        pendingAnswer = pending; eventID = pending.event_id ?? eventID; selected = pending.selected_option_ids; input = pending.answer; confidence = pending.confidence
    }
}

struct QuestionView: View {
    @EnvironmentObject var model: AppModel
    let book: Book
    @State private var question = ""
    @State private var result: QAResult?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                TextField("围绕这本教材提问", text: $question, axis: .vertical).textFieldStyle(.roundedBorder).lineLimit(3...8).disabled(model.busy)
                Button(model.busy ? "正在查找教材依据…" : "提问") {
                    Task {
                        await model.perform {
                            guard let api = model.api else { throw ClientError.configuration("请先连接学习服务") }
                            result = try await api.ask(book: book.book_id, question: question)
                        }
                    }
                }.buttonStyle(.borderedProminent).disabled(model.busy || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || question.count > 2000)
                if let result {
                    Text(result.status == "supported" ? "根据教材回答" : "教材依据不足").font(.headline)
                    Text(result.answer).textSelection(.enabled)
                    if let reason = result.insufficiency_reason { Text(reason).foregroundStyle(.secondary) }
                    ForEach(Array(result.claims.enumerated()), id: \.offset) { _, claim in
                        Text(claim.text); CitationsView(citations: claim.citations, bookID: book.book_id)
                    }
                }
            }.padding()
        }.navigationTitle("教材答疑")
    }
}
