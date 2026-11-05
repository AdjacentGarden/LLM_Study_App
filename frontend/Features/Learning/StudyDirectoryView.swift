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

private struct StudyHeaderMeasurement: Equatable {
  var compact = false
  var height: CGFloat = 0
}

private struct StudyRestorationLayout: Equatable {
  var generation: Int
  var key: String
  var target: String?
  var headerHeight: CGFloat
  var viewportHeight: CGFloat
  var titleHeight: CGFloat

  var isReady: Bool {
    target != nil && headerHeight > 0 && titleHeight > 0 && viewportHeight > titleHeight
  }
}

private func studyProgressScrollRegion(_ offset: CGFloat) -> Int {
  offset > 1 ? 1 : 0
}

private struct StudyProgressScrollObserver: ViewModifier {
  let changed: (Int, Bool) -> Void
  let legacyInteraction: () -> Void

  @ViewBuilder func body(content: Content) -> some View {
    if #available(iOS 18.0, *) {
      content
        .onScrollGeometryChange(for: Int.self) { geometry in
          studyProgressScrollRegion(geometry.contentOffset.y + geometry.contentInsets.top)
        } action: { _, region in changed(region, false) }
        .onScrollPhaseChange { _, phase, context in
          let region = studyProgressScrollRegion(
            context.geometry.contentOffset.y + context.geometry.contentInsets.top)
          if phase == .interacting {
            changed(region, true)
          } else if phase == .idle {
            changed(region, false)
          }
        }
    } else {
      content.simultaneousGesture(DragGesture(minimumDistance: 3).onChanged { _ in
        legacyInteraction()
      })
    }
  }
}

struct StudyDirectoryView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var expandedChapter: String?
  @State private var expandedSection: String?
  @State private var compactPlan = false
  @State private var headerMeasurement = StudyHeaderMeasurement()
  @State private var scrollViewportHeight: CGFloat = 0
  @State private var restorationTitleHeights: [String: CGFloat] = [:]
  @State private var automaticallyPositioning = true
  @State private var userHasScrolled = false
  @State private var scrollRegion = 0
  @State private var restorationGeneration = 0

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
  private var positionKey: String {
    "native.study.location.\(store.cloudState.cacheKey ?? "local").\(store.selectedCourseID ?? "none").\(store.selectedBookID ?? "none")"
  }
  private var restorationKey: String {
    positionKey + ":\(store.studyCoursePreparation == nil):"
      + directory.flatMap { $0.sections.map(\.id) }.joined(separator: "|")
  }

  private var restorationLayout: StudyRestorationLayout {
    let node = directory.first { $0.id == expandedChapter }
    let target = store.selectedCourseImport?.needsPreparation == true ? "study.materials.notice" : expandedSection.map { section in
      node?.sections.first?.id == section
        ? "study.chapter.title." + (node?.id ?? "")
        : "study.section.title." + section
    }
    let title = target.flatMap { restorationTitleHeights[$0] }
    return StudyRestorationLayout(generation: restorationGeneration, key: restorationKey, target: target,
      headerHeight: headerMeasurement.compact == compactPlan ? headerMeasurement.height : 0,
      viewportHeight: scrollViewportHeight,
      titleHeight: title ?? 0)
  }

  var body: some View {
    ScrollViewReader { proxy in
      VStack(spacing: 8) {
        studyHeader(compact: compactPlan)
          .frame(maxWidth: 720)
          .padding(.horizontal, store.isPad ? 32 : 16)
          .frame(maxWidth: .infinity, alignment: .top)
          .navigationBarSurface()
          .onGeometryChange(for: StudyHeaderMeasurement.self) {
            StudyHeaderMeasurement(compact: compactPlan, height: $0.size.height)
          } action: { headerMeasurement = $0 }
        ScrollView {
          VStack(alignment: .leading, spacing: 8) {
            // Chapter headings must have real frames before restoration. Lazy
            // estimates required a visible chapter jump followed by a second scroll.
            VStack(alignment: .leading, spacing: 8) {
              if let preparation = store.studyCoursePreparation, !store.hasStudyContent {
                StudyCoursePreparationView(preparation: preparation)
              } else if directory.isEmpty {
                EmptyStateView(
                  title: store.courses.isEmpty ? "开始你的第一门课程" : "课程资料还在准备中",
                  message: store.courses.isEmpty
                    ? "添加教材后，这里会按原书目录整理章节和每个小节的学习入口。"
                    : "你可以查看解析进度，或先选择另一门已经就绪的课程。",
                  asset: store.courses.isEmpty ? "/assets/brand/empty-states/no-courses-v1.png" : nil,
                  icon: store.courses.isEmpty ? nil : "LibraryBig")
                BCButton(store.courses.isEmpty ? "创建课程" : "全部课程", icon: "plus") {
                  if store.courses.isEmpty { store.courseFeatureStartDraft(); store.go(.upload) }
                  else { store.go(.library) }
                }
              } else {
                if let preparation = store.studyCoursePreparation, store.hasStudyContent {
                  StudyCoursePreparationView(preparation: preparation, compact: true)
                }
                if store.selectedCourseImport?.needsPreparation == true {
                  StudyCoursePreparationView(preparation: .materials, compact: true)
                    .id("study.materials.notice")
                    .onGeometryChange(for: CGFloat.self) { min(56, $0.size.height) } action: {
                      restorationTitleHeights["study.materials.notice"] = $0
                    }
                }
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
          // Cached chapters stay visible and usable while scroll restoration waits
          // for geometry. A missing measurement must never hide the whole course.
          .onGeometryChange(for: Int.self) { geometry in
            studyProgressScrollRegion(-geometry.frame(in: .scrollView).minY)
          } action: { region in
            if #unavailable(iOS 18.0) { updateProgressCard(region: region) }
          }
        }
        .accessibilityIdentifier("study.scroll")
        .modifier(StudyProgressScrollObserver(changed: { region, interacting in
          updateProgressCard(region: region, interacting: interacting)
        }, legacyInteraction: {
          updateProgressCard(region: scrollRegion, interacting: true)
        }))
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: {
          scrollViewportHeight = $0
        }
      }
      .onChange(of: restorationLayout, initial: true) { _, layout in
        guard automaticallyPositioning, layout.isReady, let target = layout.target else { return }
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
          // The header has its own layout space above the scroll viewport.
          // Restoration therefore needs no inset or hidden expanded-header copy.
          proxy.scrollTo(target, anchor: .top)
          automaticallyPositioning = false
        }
      }
      .task(id: restorationKey) {
        // Populate the selected branch and expanded header as one nonanimated
        // update. Geometry then triggers one direct scroll to the measured title.
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
          automaticallyPositioning = true
          userHasScrolled = false
          restorationGeneration += 1
          restorePosition()
          if store.studyCoursePreparation != nil || expandedSection == nil {
            automaticallyPositioning = false
          }
        }
      }
    }
  }

  private func updateProgressCard(region: Int, interacting: Bool = false) {
    scrollRegion = region
    if interacting {
      // A real drag takes precedence even if restoration never received all
      // its measurements. Scrolling back to zero must not require overscroll.
      automaticallyPositioning = false
      userHasScrolled = true
    }
    guard userHasScrolled else { return }
    // Entry restoration keeps the plan open at any saved chapter. Once the
    // user scrolls in either direction, only the actual directory top opens it.
    let next = region != 0
    guard next != compactPlan else { return }
    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.28)) { compactPlan = next }
  }

  private func studyHeader(compact: Bool) -> some View {
    VStack(alignment: .leading, spacing: compact ? 8 : 14) {
      HStack(spacing: 12) {
        Button {
          store.openSheet(.bookSwitcher)
        } label: {
          HStack(spacing: 10) {
            CourseCoverImage(path: store.currentCourse.flatMap { store.courseFeatureCover($0) },
              fallbackSeed: store.currentCourse?.id ?? "library")
              .frame(width: 42, height: 50).clipped().clipShape(RoundedRectangle(cornerRadius: 5))
            VStack(alignment: .leading, spacing: 3) {
              if !compact {
                Text("当前课程").font(DS.font(.supporting, weight: .medium)).foregroundStyle(DS.secondary)
                  .transition(.opacity)
              }
              Text(store.currentCourse?.name ?? store.currentTitle)
                .font(DS.font(compact ? .sectionTitle : .cardTitle, weight: .semibold))
                .lineLimit(compact ? nil : 1)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("study.course.title")
            }
            .frame(maxWidth: compact ? .infinity : nil, alignment: .leading)
            Image(systemName: "chevron.down").font(DS.font(.cardTitle))
          }
          .frame(maxWidth: compact ? .infinity : nil, alignment: .leading)
        }.buttonStyle(.plain).accessibilityIdentifier("study.course.switch")
        if !compact {
          Spacer(minLength: 4)
          Button {
            store.courseFeatureStartDraft()
            store.go(.upload)
          } label: {
            Label("创建课程", systemImage: "plus")
              .font(DS.font(.cardTitle)).foregroundStyle(.white)
              .padding(.horizontal, DS.ButtonSize.regular.horizontalPadding).buttonFrame()
          }.buttonStyle(StudyToolEntryStyle()).transition(.opacity)
            .accessibilityIdentifier("study.course.create")
        }
      }
      if store.studyCoursePreparation == nil, store.selectedCourseImport?.needsPreparation != true,
        let course = store.currentCourse, let label = store.courseCreationLabel(course) {
        Text(label).font(DS.font(.supporting, weight: .medium)).foregroundStyle(DS.secondary)
          .accessibilityIdentifier("study.course.status")
      }
      if store.studyCoursePreparation != nil {
        EmptyView()
      } else if store.usesCloudDailyPlan {
        DailyStudyPlanSummary(compact: compact)
      } else if directoryOnly {
        Text("教材目录 · 当前尚无生成的教学内容")
          .font(DS.font(.supporting)).foregroundStyle(DS.secondary)
      } else if !directory.isEmpty {
        StudyProgressCard(compact: compact, progress: store.courseLearningProgress,
          taskTitle: currentTask?.title ?? directory.first?.chapter.title ?? "从第一节开始",
          materialCount: store.currentCourse?.resourceIDs.count ?? 1) {
            store.go(.plan)
          }
      }
    }
    .padding(.top, store.isPad ? 8 : 12).padding(.bottom, 0)
  }

  private func chapterCard(_ node: LearningChapterNode, index: Int) -> some View {
    let expanded = expandedChapter == node.id
    let sectionIDs = Set(node.sections.map(\.chapter_id) + [node.id])
    let tasks = store.plan?.tasks.filter { sectionIDs.contains($0.chapter_id ?? "") } ?? []
    let readingIDs = Set(node.sections.map(\.id))
    let progress = learningReadingProgress(tasks, chapterIDs: readingIDs,
      completedChapterIDs: store.completedChapterIDs)
    return VStack(alignment: .leading, spacing: 0) {
      Button {
        automaticallyPositioning = false
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.22)) {
          expandedChapter = expanded ? nil : node.id
          if !node.sections.contains(where: { $0.id == expandedSection }) {
            expandedSection = node.sections.first?.id
          }
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
              Text("\(index + 1)").font(DS.font(.supporting, weight: .semibold))
            }
          }.iconButtonFrame()
          Text(node.chapter.source_title).font(DS.font(.cardTitle)).multilineTextAlignment(
            .leading)
          Spacer(minLength: 0)
          Image(systemName: expanded ? "chevron.down" : "chevron.right").font(
            DS.font(.supporting, weight: .semibold)
          ).foregroundStyle(DS.secondary)
        }.padding(18).contentShape(Rectangle())
      }.buttonStyle(.plain)
        .id("study.chapter.title." + node.id)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { restorationTitleHeights["study.chapter.title." + node.id] = $0 }
        .accessibilityIdentifier("study.chapter." + node.id)
        .accessibilityAddTraits(expanded ? .isSelected : [])
        .accessibilityValue(expanded ? "已展开" : "已收起")
      if expanded {
        Divider().overlay(DS.border).padding(.horizontal, 18)
        VStack(spacing: 0) {
          ForEach(node.sections) { chapter in
            sectionRow(chapter)
              .id("study.section." + chapter.id)
          }
        }.background(alignment: .leading) {
          Rectangle().fill(DS.primary.opacity(0.3)).frame(width: 1).padding(.leading, 8)
        }
        .padding(.horizontal, 18).padding(.bottom, 8)
      }
    }
    .background {
      RoundedRectangle(cornerRadius: 24).fill(DS.surface)
        .studyProgressShadow(enabled: expanded)
    }
    .overlay(
      RoundedRectangle(cornerRadius: 24).stroke(
        expanded ? DS.primary.opacity(0.24) : .clear, lineWidth: 1))
  }

  private func sectionRow(_ chapter: Chapter) -> some View {
    let expanded = expandedSection == chapter.id
    let sectionTasks = store.plan?.tasks.filter { $0.chapter_id == chapter.id } ?? []
    let complete = store.completedChapterIDs.contains(chapter.id)
      || (!sectionTasks.isEmpty && sectionTasks.allSatisfy { ["done", "completed"].contains($0.status) })
    return VStack(alignment: .leading, spacing: 0) {
      Button {
        automaticallyPositioning = false
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.22)) {
          expandedSection = expanded ? nil : chapter.id
        }
      } label: {
        HStack(spacing: 12) {
          ZStack {
            Circle().fill(DS.surface)
            Circle().stroke(expanded ? DS.primary : DS.primary.opacity(0.2), lineWidth: 2)
            if expanded { Circle().fill(DS.primary).frame(width: 7, height: 7) }
          }.frame(width: 17, height: 17)
          Text(chapter.source_title).font(DS.font(.supporting, weight: .semibold))
          Spacer(minLength: 0)
          if complete { Text("已完成").font(DS.font(.caption)).foregroundStyle(DS.secondary) }
          Image(systemName: expanded ? "chevron.down" : "chevron.right").font(DS.font(.supporting))
            .foregroundStyle(DS.secondary)
        }.buttonFrame(fillsWidth: true)
      }.buttonStyle(.plain)
        .id("study.section.title." + chapter.id)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { restorationTitleHeights["study.section.title." + chapter.id] = $0 }
        .accessibilityIdentifier("study.section." + chapter.id + ".title")
      if expanded && !directoryOnly {
        tools(for: chapter).padding(.leading, 38).padding(.bottom, 16)
      }
    }
  }

  private func tools(for chapter: Chapter) -> some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack {
        Text("选择下一步").font(DS.font(.cardTitle))
        Spacer()
        Button {
          open(.lesson, chapter: chapter)
        } label: {
          HStack(spacing: 4) {
            Text("进入学习")
            Image(systemName: "chevron.right")
          }
          .font(DS.font(.button)).foregroundStyle(.white).padding(.horizontal, DS.ButtonSize.primary.horizontalPadding)
          .buttonFrame(.primary)
        }.buttonStyle(StudyToolEntryStyle())
          .accessibilityIdentifier("study.section." + chapter.id + ".lesson")
      }
      .padding(.horizontal, 12).padding(.vertical, 9)
      .background {
        StudyToolGlassSurface(tint: .violet, cornerRadius: 20, depth: 3)
      }
      .padding(.top, 4).padding(.trailing, 3)
      LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible())], spacing: 14) {
        ForEach(learningPreferredTools(store.currentCourse?.diagnosis ?? [:])) { route in
          switch route {
          case .assignment:
            tool(
              "作业诊断", helper: "提交解题过程，定位理解卡点", icon: "ClipboardCheck", tone: "mint", route: route,
              chapter: chapter)
          case .flashcards:
            tool(
              "闪卡复习", helper: "用短时回忆巩固本节概念", icon: "Layers3", tone: "blue", route: route,
              chapter: chapter)
          case .mistakes:
            tool(
              "错题集", helper: "重做卡点题，追踪是否真正掌握", icon: "BookX", tone: "pink", route: route,
              chapter: chapter)
          default:
            tool(
              "学习笔记", helper: "查看文字、手写和语音记录", icon: "NotebookPen", tone: "violet", route: route,
              chapter: chapter)
          }
        }
      }
    }.padding(10).background(Color(hex: 0xf5f5f5), in: RoundedRectangle(cornerRadius: 24))
      .accessibilityElement(children: .contain)
      .accessibilityIdentifier("study.section." + chapter.id + ".tools")
  }

  private func tool(
    _ title: String, helper: String, icon: String, tone: String, route: Route, chapter: Chapter
  ) -> some View {
    Button {
      open(route, chapter: chapter)
    } label: {
      VStack(spacing: 0) {
        toolPreview(route, chapter: chapter)
          .aspectRatio(130.0 / 117.0, contentMode: .fit)
          .clipped()
        HStack(spacing: store.isPad ? 9 : 4) {
          StickerIcon(
            icon, tone: tone, size: store.isPad ? 28 : 17, glyphSize: store.isPad ? 17 : 10)
          VStack(alignment: .leading, spacing: 4) {
            Text(title).font(DS.font(.cardTitle)).lineLimit(1)
            Text(helper).font(DS.font(.caption)).foregroundStyle(DS.secondary)
              .lineLimit(2)
          }
          Spacer(minLength: 0)
          Image(systemName: "chevron.right").font(.system(size: store.isPad ? 12 : 8))
            .foregroundStyle(route == .assignment ? Color(hex: 0x1e765d) : DS.primary)
        }.padding(store.isPad ? 12 : 6).frame(
          maxWidth: .infinity, minHeight: store.isPad ? 94 : 66, alignment: .leading
        )
        .background { Rectangle().fill(Color.white) }
      }
      .foregroundStyle(DS.text)
    }.buttonStyle(StudyToolCardStyle(tint: StudyToolTint(route: route)))
      .accessibilityIdentifier("study.section." + chapter.id + "." + route.rawValue)
  }

  private func toolPreview(_ route: Route, chapter: Chapter) -> some View {
    GeometryReader { geometry in
      let small = geometry.size.width < 200
      let previewStyle: DS.Typography = small ? .caption : .supporting
      ZStack {
        Color.clear
        switch route {
        case .flashcards:
          Text(
            chapter.source_title.replacingOccurrences(
              of: "^第\\s*\\d+\\s*[章节]\\s*", with: "", options: .regularExpression)
          )
          .font(DS.font(previewStyle, weight: .semibold)).multilineTextAlignment(.center).lineLimit(2)
          .padding(small ? 5 : 12).frame(width: geometry.size.width * (82.0 / 130.0)).frame(
            minHeight: geometry.size.height * (30.0 / 117.0)
          )
          .background { StudyToolPreviewSurface() }
          .rotationEffect(.degrees(-16.88)).offset(y: geometry.size.height * 0.058)
        case .assignment:
          VStack(alignment: .leading, spacing: small ? 4 : 8) {
            Text("知识检测").font(DS.font(previewStyle, weight: .semibold)).foregroundStyle(DS.secondary)
            Text(store.quizzes.first { $0.chapter_id == chapter.id }?.prompt ?? "本节尚无小测").font(DS.font(previewStyle, weight: .semibold)).lineLimit(1)
            Text(store.quizzes.first { $0.chapter_id == chapter.id }?.choices.first ?? "完成教学内容生成后练习").font(DS.font(previewStyle)).foregroundStyle(DS.secondary)
              .padding(small ? 4 : 7).frame(maxWidth: .infinity, alignment: .leading).background(
                Color(hex: 0xeafaf4), in: RoundedRectangle(cornerRadius: 6))
          }.padding(small ? 7 : 11).frame(width: geometry.size.width * 0.88)
            .background { StudyToolPreviewSurface() }.offset(
              y: geometry.size.height * 0.058)
        case .mistakes:
          VStack(alignment: .leading, spacing: small ? 5 : 9) {
            HStack {
              Text("本书错题").foregroundStyle(DS.secondary)
              Spacer()
              Text("\(store.mistakes.filter { $0.book_id == store.selectedBookID && ($0.course_id == nil || $0.course_id == store.selectedCourseID) }.count) 道").foregroundStyle(DS.primary)
            }.font(DS.font(previewStyle, weight: .semibold))
            ForEach(store.mistakes.filter { $0.book_id == store.selectedBookID && ($0.course_id == nil || $0.course_id == store.selectedCourseID) }.prefix(2)) { mistake in
              HStack(spacing: 2) {
                Text("• ").foregroundStyle(DS.primary)
                Text(mistake.question).foregroundStyle(DS.secondary)
                Spacer(minLength: 0)
                Text("待复习").foregroundStyle(DS.primary).font(DS.font(small ? .caption : .supporting, weight: .semibold))
              }
              .font(DS.font(small ? .caption : .supporting)).padding(small ? 3 : 5).background(
                Color(hex: 0xf8f3ff), in: RoundedRectangle(cornerRadius: 5))
            }
          }.padding(small ? 7 : 11).frame(width: geometry.size.width * 0.9).background(
            StudyToolPreviewSurface()
          ).offset(y: geometry.size.height * 0.058)
        default:
          VStack(alignment: .leading, spacing: small ? 5 : 8) {
            ForEach(0..<3) { i in
              Capsule().fill(Color(hex: 0xdcd0fb)).frame(
                width: geometry.size.width * (i == 0 ? 0.62 : i == 1 ? 0.48 : 0.58),
                height: small ? 3 : 5)
            }
            Text("原始记录 · 整理版").font(DS.font(previewStyle, weight: .semibold)).foregroundStyle(DS.secondary)
          }.padding(small ? 9 : 15).frame(width: geometry.size.width * 0.8).background(
            StudyToolPreviewSurface()
          ).offset(y: geometry.size.height * 0.058)
        }
      }
    }
  }

  private func open(_ route: Route, chapter: Chapter) {
    if route == .lesson {
      store.openChapterLearning(chapterID: chapter.id)
      return
    }
    store.selectedChapterID = chapter.id
    let savedLessonID = store.locations[store.locationKey]?.lessonID
    let lesson = store.lessons.first { $0.chapter_id == chapter.id && $0.id == savedLessonID }
      ?? store.lessons.first { $0.chapter_id == chapter.id && $0.lesson_kind != "module_intro" }
    store.selectedLessonID = lesson?.id
    store.go(route)
  }

  private func restorePosition() {
    let chapterID = store.studyResumeChapter?.id
    let selected = directory.first { $0.sections.contains { $0.id == chapterID } } ?? directory.first
    expandedChapter = selected?.id
    expandedSection = selected?.sections.first(where: { $0.id == chapterID })?.id
      ?? selected?.sections.first?.id
    compactPlan = false
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
    if store.usesCloudDailyPlan {
      DailyStudyPlanView()
    } else if store.currentSource == nil {
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
                DS.font(.sectionTitle))
              Text((store.plan?.daily_minutes ?? 0) > 0 ? "\(days) 天 · 每天 \(store.plan?.daily_minutes ?? 0) 分钟" : "\(days) 天 · 每日时间尚未设定").font(DS.font(.body))
                .foregroundStyle(DS.secondary)
              BCProgress(value: learningProgress(tasks))
              Text("已完成 \(tasks.filter { $0.status == "done" }.count) / \(tasks.count) 项")
                .font(DS.font(.body)).foregroundStyle(DS.secondary)
            }
          }
        }
        ViewThatFits(in: .horizontal) {
          calendarGrid(columns: 7).frame(minWidth: 334, idealWidth: 334)
          calendarGrid(columns: 4)
        }.padding(9).background(DS.surface, in: RoundedRectangle(cornerRadius: 20))
          .overlay(RoundedRectangle(cornerRadius: 20).stroke(DS.border, lineWidth: 1))
          .padding(.bottom, store.isPad ? 4.68 : 0)

        Text("学习任务 · 第 \(selectedDay) 天").font(DS.font(.sectionTitle))
        if dailyTasks.isEmpty {
          BCCard {
            VStack(alignment: .leading, spacing: 12) {
              BCPill("暂无任务", color: DS.orange)
              Text("这一天暂时没有学习任务").font(DS.font(.cardTitle))
              Text("选择其他日期，或回到目录按自己的节奏学习。")
                .font(DS.font(.supporting)).foregroundStyle(DS.secondary)
            }
          }
        }
        ForEach(dailyTasks) { task in
          Button {
            store.updateTask(task.task_id, status: "done")
            store.showToast("学习任务已完成", kind: .success)
          } label: {
            HStack(alignment: .center, spacing: 12) {
              HStack(spacing: 3) {
                Text("D\(task.day)").font(DS.font(.caption, weight: .semibold))
                if task.status == "done" {
                  Image(systemName: "checkmark").font(.system(size: 12, weight: .medium))
                }
              }.foregroundStyle(DS.primary).frame(width: 70)
              VStack(alignment: .leading, spacing: 5) {
                Text(task.title).font(DS.font(.cardTitle)).multilineTextAlignment(.leading)
                  .frame(minHeight: 26, alignment: .leading).padding(.top, 7)
                Text(
                  "\(sourceName(for: task)) · \(task.task_type) · \(task.minutes) 分钟 · \(learningTaskStatus(task.status))"
                )
                .font(DS.font(.body)).foregroundStyle(DS.secondary).multilineTextAlignment(.leading)
                .lineSpacing(4).padding(.vertical, 2)
              }
              Spacer(minLength: 0)
            }.padding(13).contentShape(Rectangle())
          }.buttonStyle(.plain).accessibilityHint("标记任务为完成")
        }

        BCButton("开始今天学习", style: .secondary, size: .primary) { startToday() }
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
          Text("第\(day)天").font(DS.font(.smallButton)).lineLimit(1)
            .padding(.horizontal, 3).buttonFrame(fillsWidth: true)
          .foregroundStyle(selectedDay == day ? Color(hex: 0x456eff) : DS.text)
          .background(
            selectedDay == day ? DS.background : DS.surface, in: RoundedRectangle(cornerRadius: 12)
          )
          .overlay(
            RoundedRectangle(cornerRadius: 12).stroke(
              selectedDay == day ? Color(hex: 0xa9b7ff) : .clear, lineWidth: 1)
          )
        }.buttonStyle(.plain).accessibilityLabel("第 \(day) 天")
          .accessibilityAddTraits(selectedDay == day ? .isSelected : [])
      }
    }.padding(1).overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border, lineWidth: 1))
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
