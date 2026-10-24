import SwiftUI

struct LearningChapterNode: Identifiable {
  let chapter: Chapter
  let sections: [Chapter]
  var id: String { chapter.chapter_id }
}

func learningDirectory(_ chapters: [Chapter]) -> [LearningChapterNode] {
  let ids = Set(chapters.map(\.chapter_id))
  let roots = chapters.filter {
    $0.chapter_id != "frontmatter" && ($0.parent_id == nil || !ids.contains($0.parent_id ?? ""))
  }
  return roots.map { root in
    let children = chapters.filter { chapter in
      guard chapter.parent_id == root.chapter_id else { return false }
      return chapter.source == "local-original" || chapter.source == "demo-directory"
        || chapter.source_title.range(
          of: "^(?:第\\s*\\d+\\s*节|\\d+\\.\\d+\\*?\\s*)", options: .regularExpression) != nil
    }
    return LearningChapterNode(chapter: root, sections: children.isEmpty ? [root] : children)
  }
}

func learningPreferredTools(_ diagnosis: [String: [String]]) -> [Route] {
  if diagnosis.isEmpty {
    return learningPreferredTools(["goals": ["systematic"], "reviews": ["periodic"]])
  }
  var order: [Route] = []
  func add(_ route: Route) { if !order.contains(route) { order.append(route) } }
  for goal in diagnosis["goals"] ?? [] {
    if goal == "exam" || goal == "gaps" {
      add(.assignment)
      add(.mistakes)
    }
    if goal == "systematic" { add(.flashcards) }
  }
  for focus in diagnosis["contentFoci"] ?? [] {
    if focus == "exercises" { add(.assignment) }
    if focus == "notes" { add(.notes) }
    if focus == "keyPoints" { add(.flashcards) }
  }
  for aid in diagnosis["aids"] ?? [] {
    if aid == "mistakes" { add(.mistakes) }
    if aid == "guidedNotes" { add(.notes) }
  }
  for review in diagnosis["reviews"] ?? [] {
    if review == "alongside" { add(.assignment) }
    if review == "periodic" { add(.flashcards) }
    if review == "mistakes" { add(.mistakes) }
    if review == "notes" { add(.notes) }
  }
  [.assignment, .flashcards, .mistakes, .notes].forEach(add)
  return order
}

struct StudyDirectoryView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var expandedChapter: String?
  @State private var expandedSection: String?
  @State private var compactPlan = false

  private var directory: [LearningChapterNode] { learningDirectory(store.chapters) }
  private var directoryOnly: Bool { store.currentSource?.summary.content_mode == "directory" }
  private var courseTasks: [StudyTask] {
    let bookIDs = store.currentCourse?.resourceIDs ?? [store.selectedBookID ?? ""]
    return store.sources.filter { bookIDs.contains($0.id) }.flatMap { $0.plan.tasks }
  }
  private var currentTask: StudyTask? {
    courseTasks.first { ["in_progress", "processing"].contains($0.status) } ?? courseTasks.first {
      !["done", "completed"].contains($0.status)
    } ?? courseTasks.first
  }
  private var studyProgress: Double {
    if courseTasks.isEmpty {
      let ids = Set(directory.flatMap { $0.sections.map(\.id) })
      return ids.isEmpty ? 0 : Double(ids.intersection(store.completedChapterIDs).count) / Double(ids.count)
    }
    if store.selectedBookID == "book_biology_2", (store.currentCourse?.resourceIDs.count ?? 1) <= 1,
      !directory.isEmpty
    {
      let values = directory.map { node in
        learningChapterProgress(
          store.plan?.tasks ?? [], chapterIDs: Set(node.sections.map(\.id) + [node.id]))
      }
      return (values.reduce(0, +) / Double(values.count) * 100).rounded() / 100
    }
    return learningProgress(courseTasks)
  }
  private var positionKey: String {
    "native.study.location.\(store.selectedCourseID ?? "none").\(store.selectedBookID ?? "none")"
  }

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 8) {
        // Only this invisible copy participates in the scrolling layout. Its
        // expanded height keeps the directory and scroll bounds stable while
        // the visible controls above the scroll view collapse.
        studyHeader(compact: false)
          .hidden().allowsHitTesting(false).accessibilityHidden(true)
        LazyVStack(alignment: .leading, spacing: 8) {
          if directory.isEmpty {
            EmptyStateView(
              title: store.courses.isEmpty ? "开始你的第一门课程" : "课程资料还在准备中",
              message: store.courses.isEmpty
                ? "添加教材后，这里会按原书目录整理章节和每个小节的学习入口。"
                : "你可以查看解析进度，或先选择另一门已经就绪的课程。",
              asset: store.courses.isEmpty ? "/assets/brand/empty-states/no-courses-v1.png" : nil,
              icon: store.courses.isEmpty ? nil : "LibraryBig")
            if let source = store.currentSource, store.sourceFileURL(source.id) != nil {
              BCButton("阅读原文件", icon: "book") {
                store.openSource(SourcePageTarget(bookID: source.id, title: source.summary.title, page: 1,
                  sourceText: source.chunks.map(\.text).joined(separator: "\n\n"), from: .study))
              }
            }
            BCButton(store.courses.isEmpty ? "创建课程" : "查看课程资料", icon: "plus") {
              store.go(store.courses.isEmpty ? .upload : .library)
            }
          } else {
            ForEach(Array(directory.enumerated()), id: \.element.id) { index, node in
              chapterCard(node, index: index)
                .id(node.id)
            }
          }
        }
      }
      .frame(maxWidth: 720)
      .padding(.horizontal, store.isPad ? 32 : 16)
      .padding(.bottom, 32)
      .frame(maxWidth: .infinity)
      .onGeometryChange(for: Int.self) { geometry in
        let scrollTop = -geometry.frame(in: .scrollView).minY
        // Report only threshold crossings, rather than every scrolling frame.
        return scrollTop > 48 ? 2 : scrollTop > 16 ? 1 : 0
      } action: { region in
        let next = region == 2 || (compactPlan && region == 1)
        guard next != compactPlan else { return }
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.28)) { compactPlan = next }
      }
    }
    .accessibilityIdentifier("study.scroll")
    .overlay(alignment: .top) {
      // The overlay occupies only the visible controls' height. Chapters can
      // scroll and receive taps through the space released by the compact plan.
      studyHeader(compact: compactPlan)
        .frame(maxWidth: 720)
        .padding(.horizontal, store.isPad ? 32 : 16)
        .frame(maxWidth: .infinity, alignment: .top)
        .navigationBarSurface()
    }
    .onChange(of: expandedChapter) { _, _ in savePosition() }
    .onChange(of: expandedSection) { _, _ in savePosition() }
    .task(id: positionKey) { restorePosition() }
  }

  private func studyHeader(compact: Bool) -> some View {
    VStack(alignment: .leading, spacing: compact ? 8 : store.isPad ? 36 : 14) {
      HStack(spacing: 12) {
        Button {
          store.openSheet(.bookSwitcher)
        } label: {
          HStack(spacing: 10) {
            if let cover = store.currentSource?.summary.cover_url {
              AssetImage(cover, contentMode: .fill)
                .frame(width: 42, height: 50).clipped().clipShape(RoundedRectangle(cornerRadius: 5))
            } else {
              StickerIcon("LibraryBig", size: 36)
            }
            VStack(alignment: .leading, spacing: 3) {
              if !compact {
                Text("当前课程").font(DS.font(14, weight: .medium)).foregroundStyle(DS.secondary)
                  .transition(.opacity)
              }
              Text(store.currentCourse?.name ?? store.currentTitle)
                .font(DS.font(compact ? 22 : 16, weight: .bold))
                .lineLimit(compact ? nil : 1)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("study.course.title")
            }
            .frame(maxWidth: compact ? .infinity : nil, alignment: .leading)
            Image(systemName: "chevron.down").font(DS.font(16, weight: .semibold))
          }
          .frame(maxWidth: compact ? .infinity : nil, alignment: .leading)
        }.buttonStyle(.plain).accessibilityIdentifier("study.course.switch")
        if !compact {
          Spacer(minLength: 4)
          Button {
            if store.currentCourse == nil {
              store.go(.upload)
            } else {
              store.openSheet(.addMaterials)
            }
          } label: {
            Label(store.currentCourse == nil ? "创建课程" : "添加资料", systemImage: "plus")
              .font(DS.font(20, weight: .bold)).foregroundStyle(DS.text)
              .padding(.horizontal, 12).padding(.vertical, 10).background(DS.surface, in: Capsule())
              .shadow(color: DS.text.opacity(0.08), radius: 3, y: 2)
          }.buttonStyle(.plain).transition(.opacity)
        }
      }
      if directoryOnly {
        Text("教材目录 · 当前尚无生成的教学内容")
          .font(DS.font(14)).foregroundStyle(DS.secondary)
      } else if !directory.isEmpty {
        VStack(alignment: .leading, spacing: compact ? 0 : 4) {
          if !compact {
            VStack(alignment: .leading, spacing: 4) {
              HStack {
                Text(courseTasks.isEmpty ? "阅读进度" : "学习计划").font(DS.font(20.8, weight: .bold))
                Spacer()
                Button {
                  store.go(.plan)
                } label: {
                  HStack(spacing: 4) {
                    Text("计划详情")
                    Image(systemName: "chevron.right")
                  }
                  .font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary)
                }.buttonStyle(.plain)
              }
              HStack(spacing: 12) {
                StickerIcon("Check", size: 30, glyphSize: 16)
                VStack(alignment: .leading, spacing: 4) {
                  Text((currentTask?.minutes ?? store.plan?.daily_minutes ?? 0) > 0 ? "今日建议 · \(currentTask?.minutes ?? store.plan?.daily_minutes ?? 0) 分钟" : "尚无每日学习安排")
                    .font(DS.font(14)).foregroundStyle(DS.secondary)
                  Text(currentTask?.title ?? directory.first?.chapter.title ?? "从第一节开始")
                    .font(DS.font(16, weight: .bold)).lineLimit(2)
                }
              }.padding(.top, 12)
            }
            .transition(.opacity.combined(with: .move(edge: .top)))
          }
          BCProgress(value: studyProgress)
            .accessibilityIdentifier("study.plan.progress")
          if !compact {
            Text("\(courseTasks.isEmpty ? "本书已读" : "计划完成") \(Int((studyProgress * 100).rounded()))%")
              .font(DS.font(20)).foregroundStyle(DS.secondary)
              .transition(.opacity)
          }
        }
        .padding(.horizontal, 16).padding(.vertical, compact ? 10 : 16)
        .clipped()
        .background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
        .shadow(color: DS.text.opacity(compact ? 0.08 : 0), radius: 4, y: 2)
      }
    }
    .padding(.top, store.isPad ? 8 : 12).padding(.bottom, 0)
  }

  private func chapterCard(_ node: LearningChapterNode, index: Int) -> some View {
    let expanded = expandedChapter == node.id
    let sectionIDs = Set(node.sections.map(\.chapter_id) + [node.id])
    let tasks = store.plan?.tasks.filter { sectionIDs.contains($0.chapter_id ?? "") } ?? []
    let readingIDs = Set(node.sections.map(\.id))
    let progress = tasks.isEmpty
      ? (readingIDs.isEmpty ? 0 : Double(readingIDs.intersection(store.completedChapterIDs).count) / Double(readingIDs.count))
      : learningChapterProgress(tasks, chapterIDs: sectionIDs)
    return VStack(alignment: .leading, spacing: 0) {
      Button {
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.22)) {
          expandedChapter = expanded ? nil : node.id
          if !node.sections.contains(where: { $0.id == expandedSection }) {
            expandedSection = node.sections.first?.id
          }
          if let expandedSection { store.selectedChapterID = expandedSection }
        }
      } label: {
        HStack(spacing: 13) {
          ZStack {
            Circle().inset(by: 3).stroke(DS.border, lineWidth: 5)
            Circle().inset(by: 3).trim(from: 0, to: progress).stroke(
              DS.primary, style: StrokeStyle(lineWidth: 5, lineCap: .round)
            ).rotationEffect(.degrees(-90))
            if progress >= 1 {
              Image(systemName: "checkmark").font(.system(size: 19, weight: .bold)).foregroundStyle(
                DS.primary)
            } else {
              Text("\(index + 1)").font(DS.font(15, weight: .bold))
            }
          }.frame(width: 44, height: 44)
          VStack(alignment: .leading, spacing: 6) {
            Text(node.chapter.source_title).font(DS.font(16, weight: .bold)).multilineTextAlignment(
              .leading)
            Text(
              "\(node.sections.count) 个小节 · \(learningPageLabel(node.chapter.page_start, end: node.chapter.page_end, printed: node.chapter.printed_page_start, printedEnd: node.chapter.printed_page_end))"
            )
            .font(DS.font(14)).foregroundStyle(DS.secondary)
          }
          Spacer(minLength: 0)
          Image(systemName: expanded ? "chevron.down" : "chevron.right").font(
            DS.font(14, weight: .semibold)
          ).foregroundStyle(DS.secondary)
        }.padding(18).contentShape(Rectangle())
      }.buttonStyle(.plain).accessibilityIdentifier("study.chapter." + node.id)
        .accessibilityAddTraits(expanded ? .isSelected : [])
        .accessibilityValue(expanded ? "已展开" : "已收起")
      if expanded {
        Divider().overlay(DS.border).padding(.horizontal, 18)
        VStack(spacing: 0) {
          ForEach(node.sections) { chapter in
            sectionRow(chapter)
          }
        }.background(alignment: .leading) {
          Rectangle().fill(DS.primary.opacity(0.3)).frame(width: 1).padding(.leading, 8)
        }
        .padding(.horizontal, 18).padding(.bottom, 8)
      }
    }
    .background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
    .overlay(
      RoundedRectangle(cornerRadius: 16).stroke(
        expanded ? DS.primary.opacity(0.24) : DS.border, lineWidth: 1))
  }

  private func sectionRow(_ chapter: Chapter) -> some View {
    let expanded = expandedSection == chapter.id
    let sectionTasks = store.plan?.tasks.filter { $0.chapter_id == chapter.id } ?? []
    let complete = sectionTasks.isEmpty ? store.completedChapterIDs.contains(chapter.id)
      : sectionTasks.allSatisfy { ["done", "completed"].contains($0.status) }
    return VStack(alignment: .leading, spacing: 0) {
      Button {
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.22)) {
          expandedSection = expanded ? nil : chapter.id
          if !expanded {
            store.selectedChapterID = chapter.id
            store.saveLocation()
          }
        }
      } label: {
        HStack(spacing: 12) {
          ZStack {
            Circle().fill(DS.surface)
            Circle().stroke(expanded ? DS.primary : DS.primary.opacity(0.2), lineWidth: 2)
            if expanded { Circle().fill(DS.primary).frame(width: 7, height: 7) }
          }.frame(width: 17, height: 17)
          VStack(alignment: .leading, spacing: 5) {
            Text(chapter.source_title).font(DS.font(14, weight: .semibold))
            Text(
              learningPageLabel(
                chapter.page_start, end: chapter.page_end, printed: chapter.printed_page_start,
                printedEnd: chapter.printed_page_end)
            ).font(DS.font(14)).foregroundStyle(DS.secondary)
          }
          Spacer(minLength: 0)
          if complete { Text("已完成").font(DS.font(20, weight: .bold)).foregroundStyle(DS.secondary) }
          Image(systemName: expanded ? "chevron.down" : "chevron.right").font(DS.font(14))
            .foregroundStyle(DS.secondary)
        }.padding(.vertical, 17).contentShape(Rectangle())
      }.buttonStyle(.plain)
      if expanded {
        if directoryOnly {
          Text(
            "目录 · \(learningPageLabel(chapter.page_start, end: chapter.page_end, printed: chapter.printed_page_start, printedEnd: chapter.printed_page_end))"
          )
          .font(DS.font(14)).foregroundStyle(DS.secondary).padding(.bottom, 16)
        } else {
          tools(for: chapter).padding(.leading, 38).padding(.bottom, 16)
        }
      }
    }
  }

  private func tools(for chapter: Chapter) -> some View {
    VStack(alignment: .leading, spacing: 13) {
      HStack {
        VStack(alignment: .leading, spacing: 4) {
          Text("选择下一步").font(DS.font(16, weight: .bold))
          Text(
            learningPageLabel(
              chapter.page_start, printed: chapter.printed_page_start,
              printedEnd: chapter.printed_page_end)
          ).font(DS.font(14)).foregroundStyle(DS.secondary)
        }
        Spacer()
        Button {
          open(.lesson, chapter: chapter)
        } label: {
          HStack(spacing: 4) {
            Text("进入学习")
            Image(systemName: "chevron.right")
          }
          .font(DS.font(16, weight: .bold)).foregroundStyle(.white).padding(.horizontal, 13)
          .padding(.vertical, 11).background(DS.primary, in: Capsule())
        }.buttonStyle(.plain)
      }
      if store.currentCourse?.diagnosis["aids"]?.contains("video") == true {
        Button("查看已有图示与动画 · 视频待提供") { open(.lesson, chapter: chapter) }
          .font(DS.font(14, weight: .semibold)).foregroundStyle(DS.primary).buttonStyle(.plain)
      }
      LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
        ForEach(learningPreferredTools(store.currentCourse?.diagnosis ?? [:])) { route in
          switch route {
          case .assignment:
            tool(
              "作业诊断", helper: "提交解题过程，定位理解卡点", icon: "ClipboardCheck", tone: "pink", route: route,
              chapter: chapter)
          case .flashcards:
            tool(
              "闪卡复习", helper: "用短时回忆巩固本节概念", icon: "Layers3", tone: "blue", route: route,
              chapter: chapter)
          case .mistakes:
            tool(
              "错题集", helper: "重做卡点题，追踪是否真正掌握", icon: "BookX", tone: "peach", route: route,
              chapter: chapter)
          default:
            tool(
              "学习笔记", helper: "查看文字、手写和语音记录", icon: "NotebookPen", tone: "mint", route: route,
              chapter: chapter)
          }
        }
      }
    }.padding(14).background(Color(hex: 0xf3f6fb), in: RoundedRectangle(cornerRadius: 18))
  }

  private func tool(
    _ title: String, helper: String, icon: String, tone: String, route: Route, chapter: Chapter
  ) -> some View {
    Button {
      open(route, chapter: chapter)
    } label: {
      VStack(spacing: 0) {
        toolPreview(route, chapter: chapter)
          .aspectRatio(store.isPad ? 1.95 : 1.5, contentMode: .fit)
          .clipped()
        HStack(spacing: store.isPad ? 9 : 4) {
          StickerIcon(
            icon, tone: tone, size: store.isPad ? 28 : 17, glyphSize: store.isPad ? 17 : 10)
          VStack(alignment: .leading, spacing: 4) {
            Text(title).font(DS.font(store.isPad ? 16 : 14, weight: .bold)).lineLimit(1)
            Text(helper).font(DS.font(store.isPad ? 14 : 9)).foregroundStyle(DS.secondary)
              .lineLimit(2)
          }
          Spacer(minLength: 0)
          Image(systemName: "chevron.right").font(.system(size: store.isPad ? 12 : 8))
            .foregroundStyle(route == .assignment ? Color(hex: 0x1e765d) : DS.primary)
        }.padding(store.isPad ? 12 : 6).frame(
          maxWidth: .infinity, minHeight: store.isPad ? 94 : 62, alignment: .leading
        )
        .background(DS.surface)
      }
      .background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
      .clipShape(RoundedRectangle(cornerRadius: 16))
      .shadow(color: DS.text.opacity(0.12), radius: 5, y: 4)
    }.buttonStyle(.plain)
  }

  private func toolPreview(_ route: Route, chapter: Chapter) -> some View {
    GeometryReader { geometry in
      let small = geometry.size.width < 200
      let unit: CGFloat = small ? 9 : 14
      ZStack {
        (route == .flashcards
          ? Color(hex: 0xdce5ff)
          : route == .assignment
            ? Color(hex: 0xd9f6ec)
            : route == .mistakes ? Color(hex: 0xf2e9ff) : Color(hex: 0xece1ff))
        switch route {
        case .flashcards:
          Text(
            chapter.source_title.replacingOccurrences(
              of: "^第\\s*\\d+\\s*[章节]\\s*", with: "", options: .regularExpression)
          )
          .font(DS.font(unit, weight: .bold)).multilineTextAlignment(.center).lineLimit(2)
          .padding(small ? 10 : 22).frame(width: geometry.size.width * 0.78).frame(
            minHeight: geometry.size.height * 0.46
          )
          .background(
            RoundedRectangle(cornerRadius: 10).fill(DS.surface).shadow(
              color: Color(hex: 0xaabcf0).opacity(0.6), radius: 0, x: 5, y: 6)
          )
          .rotationEffect(.degrees(-5)).offset(y: geometry.size.height * 0.18)
        case .assignment:
          VStack(alignment: .leading, spacing: small ? 4 : 8) {
            Text("知识检测").font(DS.font(unit, weight: .bold)).foregroundStyle(DS.secondary)
            Text(store.quizzes.first { $0.chapter_id == chapter.id }?.prompt ?? "本节尚无小测").font(DS.font(unit, weight: .bold)).lineLimit(1)
            Text(store.quizzes.first { $0.chapter_id == chapter.id }?.choices.first ?? "完成教学内容生成后练习").font(DS.font(unit)).foregroundStyle(DS.secondary)
              .padding(small ? 4 : 7).frame(maxWidth: .infinity, alignment: .leading).background(
                Color(hex: 0xeafaf4), in: RoundedRectangle(cornerRadius: 6))
          }.padding(small ? 7 : 11).frame(width: geometry.size.width * 0.88)
            .background(DS.surface, in: RoundedRectangle(cornerRadius: 10)).offset(
              y: geometry.size.height * 0.12)
        case .mistakes:
          VStack(alignment: .leading, spacing: small ? 5 : 9) {
            HStack {
              Text("本书错题").foregroundStyle(DS.secondary)
              Spacer()
              Text("\(store.mistakes.filter { $0.book_id == store.selectedBookID && ($0.course_id == nil || $0.course_id == store.selectedCourseID) }.count) 道").foregroundStyle(DS.primary)
            }.font(DS.font(unit, weight: .bold))
            ForEach(store.mistakes.filter { $0.book_id == store.selectedBookID && ($0.course_id == nil || $0.course_id == store.selectedCourseID) }.prefix(2)) { mistake in
              HStack(spacing: 2) {
                Text("• ").foregroundStyle(DS.primary)
                Text(mistake.question).foregroundStyle(DS.secondary)
                Spacer(minLength: 0)
                Text("待复习").foregroundStyle(DS.primary).fontWeight(.bold)
              }
              .font(DS.font(small ? 9 : 20)).padding(small ? 3 : 5).background(
                Color(hex: 0xf8f3ff), in: RoundedRectangle(cornerRadius: 5))
            }
          }.padding(small ? 7 : 11).frame(width: geometry.size.width * 0.9).background(
            DS.surface, in: RoundedRectangle(cornerRadius: 10)
          ).offset(y: small ? 6 : 18)
        default:
          VStack(alignment: .leading, spacing: small ? 5 : 8) {
            ForEach(0..<3) { i in
              Capsule().fill(Color(hex: 0xdcd0fb)).frame(
                width: geometry.size.width * (i == 0 ? 0.62 : i == 1 ? 0.48 : 0.58),
                height: small ? 3 : 5)
            }
            Text("原始记录 · 整理版").font(DS.font(unit, weight: .bold)).foregroundStyle(DS.secondary)
          }.padding(small ? 9 : 15).frame(width: geometry.size.width * 0.8).background(
            DS.surface, in: RoundedRectangle(cornerRadius: 10)
          ).offset(y: geometry.size.height * 0.15)
        }
      }
    }
  }

  private func open(_ route: Route, chapter: Chapter) {
    store.selectedChapterID = chapter.id
    if route == .lesson, let lesson = store.lessons.first(where: { $0.chapter_id == chapter.id }) {
      store.openLesson(lesson)
    } else {
      store.go(route)
    }
  }

  private func savePosition() {
    let value = ["chapter": expandedChapter ?? "", "section": expandedSection ?? ""]
    UserDefaults.standard.set(value, forKey: positionKey)
  }

  private func restorePosition() {
    if !ContentBundle.fixtureMode,
      let saved = UserDefaults.standard.dictionary(forKey: positionKey) as? [String: String]
    {
      expandedChapter = saved["chapter"].flatMap { $0.isEmpty ? nil : $0 }
      expandedSection = saved["section"].flatMap { $0.isEmpty ? nil : $0 }
    } else {
      let selected =
        directory.first { $0.sections.contains { $0.id == store.selectedChapterID } }
        ?? directory.first
      expandedChapter = selected?.id
      expandedSection =
        selected?.sections.first(where: { $0.id == store.selectedChapterID })?.id
        ?? selected?.sections.first?.id
    }
  }
}

struct LearningPlanView: View {
  @Environment(AppStore.self) private var store
  @State private var selectedDay = 1
  private var sources: [SourceContent] {
    let ids = store.currentCourse?.resourceIDs ?? [store.selectedBookID ?? ""]
    return store.sources.filter { ids.contains($0.id) }
  }
  private var tasks: [StudyTask] { sources.flatMap { $0.plan.tasks } }
  private var days: Int { min(90, max(1, sources.map { $0.plan.days }.max() ?? 14)) }
  private var dailyTasks: [StudyTask] { tasks.filter { $0.day == min(selectedDay, days) } }

  var body: some View {
    if store.currentSource == nil {
      LearningNoSource(title: "暂无学习计划", message: "完成教材解析、章节确认和课程生成后，就能开始每日学习。", icon: "CalendarDays")
    } else {
      LearningScroll(spacing: 12) {
        BCCard {
          HStack(alignment: .center, spacing: 20) {
            SVGAsset(path: "icons/sticker-CalendarDays.svg")
              .frame(width: 24, height: 24).frame(width: 74, height: 98)
              .background(
                Color(hex: 0xddd0ff),
                in: UnevenRoundedRectangle(
                  topLeadingRadius: 9, bottomLeadingRadius: 11, bottomTrailingRadius: 8,
                  topTrailingRadius: 12)
              )
              .overlay(
                UnevenRoundedRectangle(
                  topLeadingRadius: 9, bottomLeadingRadius: 11, bottomTrailingRadius: 8,
                  topTrailingRadius: 12
                ).stroke(Color(hex: 0x252436), lineWidth: 1.7)
              )
              .shadow(color: Color(hex: 0x252436), radius: 0, x: 2, y: 2).rotationEffect(
                .degrees(-3)
              )
              .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 7) {
              Text(store.currentCourse?.name ?? store.currentTitle).font(
                DS.font(20.8, weight: .bold))
              Text((store.plan?.daily_minutes ?? 0) > 0 ? "\(days) 天 · 每天 \(store.plan?.daily_minutes ?? 0) 分钟" : "\(days) 天 · 每日时间尚未设定").font(DS.font(20))
                .foregroundStyle(DS.secondary)
              Text("按课程资料汇总学习任务，完成状态会保存到对应资料。")
                .font(DS.font(20)).foregroundStyle(DS.secondary).lineSpacing(4)
                .padding(.bottom, store.isPad ? 2 : 4)
              BCProgress(value: learningProgress(tasks))
              Text("已完成 \(tasks.filter { $0.status == "done" }.count) / \(tasks.count) 项")
                .font(DS.font(20)).foregroundStyle(DS.secondary)
            }
          }
        }
        ViewThatFits(in: .horizontal) {
          calendarGrid(columns: 7).frame(minWidth: 334, idealWidth: 334)
          calendarGrid(columns: 4)
        }.padding(9).background(DS.surface, in: RoundedRectangle(cornerRadius: 20))
          .overlay(RoundedRectangle(cornerRadius: 20).stroke(DS.border, lineWidth: 1))
          .padding(.bottom, store.isPad ? 4.68 : 0)

        Text("学习任务 · 第 \(selectedDay) 天").font(DS.font(20.8, weight: .bold))
        if dailyTasks.isEmpty {
          BCCard {
            VStack(alignment: .leading, spacing: 12) {
              BCPill("暂无任务", color: DS.orange)
              Text("这一天暂时没有学习任务").font(DS.font(16, weight: .bold))
              Text("选择其他日期，或回到目录按自己的节奏学习。")
                .font(DS.font(13)).foregroundStyle(DS.secondary)
            }
          }
        }
        ForEach(dailyTasks) { task in
          Button {
            store.updateTask(task.task_id, status: "done")
            store.showToast("学习任务已完成")
          } label: {
            HStack(alignment: .center, spacing: 12) {
              HStack(spacing: 3) {
                Text("D\(task.day)").font(DS.font(20, weight: .bold))
                if task.status == "done" {
                  Image(systemName: "checkmark").font(.system(size: 12, weight: .medium))
                }
              }.foregroundStyle(DS.primary).frame(width: 70)
              VStack(alignment: .leading, spacing: 5) {
                Text(task.title).font(DS.font(16, weight: .bold)).multilineTextAlignment(.leading)
                  .frame(height: 20.8, alignment: .leading).padding(.top, 7)
                Text(
                  "\(sourceName(for: task)) · \(task.task_type) · \(task.minutes) 分钟 · \(learningTaskStatus(task.status))"
                )
                .font(DS.font(20)).foregroundStyle(DS.secondary).multilineTextAlignment(.leading)
                .lineSpacing(4).padding(.vertical, 2)
              }
              Spacer(minLength: 0)
            }.padding(13).contentShape(Rectangle())
          }.buttonStyle(.plain).accessibilityHint("标记任务为完成")
        }

        BCButton("开始今天学习", style: .secondary) { startToday() }
      }
    }
  }

  private func calendarGrid(columns: Int) -> some View {
    LazyVGrid(
      columns: Array(repeating: GridItem(.flexible(minimum: 44), spacing: 4), count: columns),
      spacing: 4
    ) {
      ForEach(1...days, id: \.self) { day in
        Button {
          withAnimation(.easeInOut(duration: 0.2)) { selectedDay = day }
        } label: {
          // CSS overflow-wrap keeps the numeral together. SwiftUI's default word
          // breaker instead groups "1天", so choose the same fitting line boxes explicitly.
          ViewThatFits(in: .horizontal) {
            calendarLine("第\(day)天")
            VStack(spacing: 0) {
              calendarLine("第\(day)")
              calendarLine("天")
            }.fixedSize()
            VStack(spacing: 0) {
              calendarLine("第")
              calendarLine("\(day)")
              calendarLine("天")
            }.fixedSize()
          }
          .padding(.horizontal, 3).padding(.vertical, 5)
          .frame(maxWidth: .infinity, minHeight: store.isPad ? 54 : 44)
          .foregroundStyle(selectedDay == day ? Color(hex: 0x456eff) : DS.text)
          .background(
            selectedDay == day ? DS.background : DS.surface, in: RoundedRectangle(cornerRadius: 12)
          )
          .overlay(
            RoundedRectangle(cornerRadius: 12).stroke(
              selectedDay == day ? Color(hex: 0xa9b7ff) : .clear, lineWidth: 1)
          )
          .overlay(alignment: .topTrailing) {
            if selectedDay == day {
              Image(systemName: "checkmark").font(.system(size: 10, weight: .bold))
                .foregroundStyle(Color(hex: 0x456eff)).frame(width: 16, height: 16)
                .background(.white, in: Circle()).padding(5)
            }
          }
        }.buttonStyle(.plain).accessibilityLabel("第 \(day) 天")
          .accessibilityAddTraits(selectedDay == day ? .isSelected : [])
      }
    }.padding(1).overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border, lineWidth: 1))
  }

  private func calendarLine(_ title: String) -> some View {
    Text(title).font(DS.font(20, weight: .bold)).fixedSize(horizontal: true, vertical: true).frame(
      height: 28)
  }

  private func sourceName(for task: StudyTask) -> String {
    guard let source = sources.first(where: { $0.plan.tasks.contains { $0.id == task.id } }) else {
      return store.currentTitle
    }
    if source.id == "book_biology_2", let filename = LearningSourceProvenance.biologyFilename {
      return filename
    }
    return source.summary.filename ?? source.summary.title
  }
  private func startToday() {
    guard let task = dailyTasks.first(where: { $0.status != "done" }) ?? dailyTasks.first else {
      store.go(.study)
      return
    }
    if let source = sources.first(where: { $0.plan.tasks.contains { $0.id == task.id } }) {
      store.selectSource(source.id)
    }
    store.selectedChapterID = task.chapter_id
    if let lesson = store.lessons.first(where: {
      $0.id == task.lesson_id || $0.chapter_id == task.chapter_id
    }) {
      store.openLesson(lesson)
    } else {
      store.go(.lesson)
    }
  }
}

private enum LearningSourceProvenance {
  struct Manifest: Decodable {
    struct Original: Decodable { let filename: String }
    let source: Original
  }
  static let biologyFilename =
    (try? ContentBundle.data("rag/biology-required-2-rag-v1/manifest.json"))
    .flatMap { try? JSONDecoder().decode(Manifest.self, from: $0).source.filename }
}
