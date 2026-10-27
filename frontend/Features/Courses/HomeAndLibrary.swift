import SwiftUI

struct CourseHomeView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.verticalSizeClass) private var verticalSizeClass
  private var homeCourses: [Course] {
    var result = store.courses
    if let biology = result.firstIndex(where: { $0.resourceIDs.contains("book_biology_2") }),
      let math = result.firstIndex(where: {
        $0.resourceIDs.contains("catalog_high_school_math_required_2")
      }), math > biology
    {
      result.swapAt(biology, math)
    }
    return result
  }
  private var selected: Course? {
    store.currentCourse ?? store.courses.first
  }
  private var nextChapter: Chapter? {
    CourseHomeNextStep.resolve(
      chapters: store.chapters, locationChapterID: store.locations[store.locationKey]?.chapterID,
      plan: store.plan, lessons: store.lessons)
  }
  private var nextLesson: Lesson? {
    store.lessons.first { $0.chapter_id == nextChapter?.id && $0.lesson_kind != "module_intro" }
  }
  var body: some View {
    CourseFeaturePage(width: 920, topInset: store.isPad ? 42 : 4) {
      Text("Hi，\(store.preferences?.displayName ?? "同学")").font(DS.font(27.2, weight: .bold)).frame(
        maxWidth: .infinity, alignment: .leading
      ).frame(height: 44, alignment: .top)
      VStack(alignment: .leading, spacing: 0) {
        CourseSectionHeading(
          title: "我的课程", detail: store.courses.isEmpty ? "从原书开始学习" : "共 \(store.courses.count) 门",
          detailSize: 20.8, detailWeight: .bold)
        if !store.courses.isEmpty { showcase }
      }.padding(.top, -2)
      if store.courses.isEmpty {
        emptyShelf
      } else {
        workspace
        CourseSectionHeading(title: "学习安排", detail: "计划、复习与课程资料")
        HStack(spacing: store.isPad ? 10 : 8) {
          if !(store.plan?.tasks.isEmpty ?? true) {
            globalAction("学习计划", helper: "查看课程安排", icon: "CalendarDays", route: .plan)
          }
          globalAction("错题复习", helper: "回顾当前资料的卡点", icon: "CircleAlert", route: .mistakes)
          globalAction("课程资料", helper: "管理文件与教材", icon: "Upload", route: .courseDetail)
        }.padding(.top, -8)
      }
    }
  }
  private var emptyShelf: some View {
    VStack(spacing: 8) {
      EmptyStateView(
        title: "开始你的第一门课程",
        message: "添加课程资料后，可以从原书位置继续学习。",
        asset: "/assets/brand/empty-states/home-no-courses-v1.png")
      BCButton("创建课程", icon: "plus") { create() }
        .accessibilityIdentifier("home.empty.create")
    }.frame(maxWidth: 720).frame(maxWidth: .infinity)
  }
  private var showcase: some View {
    GeometryReader { geometry in
      let short = verticalSizeClass == .compact
      let coverWidth = short ? 112 : min(180.0, max(128.0, geometry.size.width * 0.26))
      let coverHeight = short ? 144 : coverWidth * 1.43
      let gutter = min(32.0, max(16.0, geometry.size.width * 0.04))
      let controlsWidth = min(252.0, max(128.0, geometry.size.width * 0.30))
      let railWidth = geometry.size.width - controlsWidth - 16 + gutter
      HStack(alignment: .top, spacing: 16) {
        HomeCourseShelf(
          courses: homeCourses, selectedCourseID: selected?.id,
          coverWidth: coverWidth, coverHeight: coverHeight, railWidth: railWidth, short: short
        ).frame(width: railWidth).padding(.leading, -gutter)
        VStack(alignment: .trailing, spacing: 12) {
          Button {
            store.go(.library)
          } label: {
            HStack(spacing: 3) {
              Text("全部课程")
              Image(systemName: "arrow.right").font(.system(size: 15))
            }.font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary).frame(minHeight: 44)
          }.buttonStyle(.plain)
          Button(action: create) {
            HStack(spacing: 6) {
              BCGlyph("Upload", color: .white).frame(width: 16, height: 16)
              Text("创建课程").font(DS.font(20, weight: .bold)).fixedSize(
                horizontal: true, vertical: false)
            }.padding(.horizontal, 12).frame(height: 44).foregroundStyle(.white).background(
              DS.primary, in: Capsule())
          }.buttonStyle(.plain)
          Spacer(minLength: 12)
          if let selected {
            HStack(alignment: .lastTextBaseline, spacing: 8) {
              Text(selected.name).font(DS.font(16, weight: .bold)).lineLimit(3)
                .multilineTextAlignment(.trailing)
              Text(
                "\((homeCourses.firstIndex { $0.id == selected.id } ?? 0) + 1) / \(homeCourses.count)"
              ).font(DS.font(14, weight: .bold)).foregroundStyle(DS.secondary).fixedSize()
            }
          }
        }.frame(width: controlsWidth, alignment: .trailing).padding(.top, 8).padding(.bottom, 16)
      }
    }.frame(height: verticalSizeClass == .compact ? 176 : store.isPad ? 305.4 : 231)
  }
  private var workspace: some View {
    VStack(alignment: .leading, spacing: 12) {
      if let selected {
        VStack(alignment: .leading, spacing: 4) {
          HStack(spacing: 6) {
            BCGlyph("LibraryBig", color: DS.primary).frame(width: 16, height: 16)
            Text("这门课程的下一步").font(DS.font(14, weight: .bold))
          }.foregroundStyle(DS.primary)
          Text(store.currentSource?.summary.title ?? selected.name).font(DS.font(14))
            .foregroundStyle(DS.secondary)
          Text(nextChapter?.source_title ?? nextLesson?.title ?? selected.name).font(
            DS.font(20.8, weight: .bold))
        }.frame(height: 72, alignment: .top)
        HStack(spacing: 8) {
          homeAction("继续学习", icon: "Play", primary: true) {
            if let lesson = nextLesson { store.openLesson(lesson) } else { store.go(.study) }
          }
          homeAction("回到原书", icon: "BookOpenText", primary: false) {
            guard let source = store.currentSource else { return }
            store.openSource(
              SourcePageTarget(bookID: source.id, title: source.summary.title, page: 1, from: .home)
            )
          }
        }
        Divider().overlay(DS.primary.opacity(0.18))
        HStack {
          Text("本章工具").font(DS.font(16, weight: .bold))
          Spacer()
          Text("围绕当前章节继续练习与复习").font(DS.font(14)).foregroundStyle(DS.secondary).lineLimit(1)
        }.frame(height: 21, alignment: .top)
        ScrollView(.horizontal) {
          HStack(spacing: 8) {
            toolCard("作业诊断", subtitle: "提交解题过程，定位理解卡点", icon: "ClipboardCheck", route: .assignment)
            toolCard("闪卡复习", subtitle: "用短时回忆巩固本节概念", icon: "Layers3", route: .flashcards)
            toolCard("错题集", subtitle: "重做卡点题，追踪是否真正掌握", icon: "BookX", route: .mistakes)
            toolCard("学习笔记", subtitle: "查看文字、手写和语音记录", icon: "NotebookPen", route: .notes)
          }.padding(.horizontal, 12).padding(.top, 0).padding(.bottom, 12)
        }.scrollIndicators(.hidden)
      }
    }
    .padding(16).background(Color(hex: 0xEEF3FF), in: RoundedRectangle(cornerRadius: 16))
  }
  private func homeAction(
    _ title: String, icon: String, primary: Bool, action: @escaping () -> Void
  ) -> some View {
    Button(action: action) {
      HStack(spacing: 7) {
        BCGlyph(icon, color: primary ? .white : DS.primary).frame(width: 18, height: 18)
        Text(title).font(DS.font(20, weight: .bold)).lineLimit(1).minimumScaleFactor(0.8)
        if primary { BCGlyph("ArrowRight", color: .white).frame(width: 16, height: 16) }
      }.frame(maxWidth: .infinity).frame(height: 46)
        .foregroundStyle(primary ? .white : DS.primary)
        .background(primary ? DS.primary : DS.surface, in: Capsule())
        .overlay(Capsule().stroke(primary ? DS.primary : Color(hex: 0xC7B6FA), lineWidth: 1))
    }.buttonStyle(BCPressStyle())
  }
  private func toolCard(_ title: String, subtitle: String, icon: String, route: Route) -> some View
  {
    let width: CGFloat = store.isPad ? 210 : 174
    let captionSize = min(14, max(9, width * 0.061))
    let coverColor =
      route == .flashcards
      ? Color(hex: 0xDCE6FF)
      : route == .mistakes
        ? Color(hex: 0xF1EAFF) : route == .notes ? Color(hex: 0xEADFFF) : Color(hex: 0xD8F5E9)
    return Button {
      if let chapter = nextChapter {
        store.selectedChapterID = chapter.id
        store.selectedLessonID = nextLesson?.id
      }
      store.go(route)
    } label: {
      VStack(alignment: .leading, spacing: 0) {
        ZStack(alignment: .bottom) {
          coverColor
          if route == .assignment {
            VStack(alignment: .leading, spacing: width * 0.03) {
              Text("知识检测").font(DS.font(captionSize)).foregroundStyle(DS.secondary)
              Text("这一节的核心概念是？").font(DS.font(captionSize, weight: .bold)).lineLimit(2)
              HStack(spacing: 5) {
                Text("A").font(DS.font(captionSize, weight: .bold)).foregroundStyle(DS.mint)
                Text("选择你的答案").font(DS.font(captionSize)).foregroundStyle(DS.secondary)
              }.padding(5).frame(maxWidth: .infinity, alignment: .leading).background(
                Color(hex: 0xEFFAF5), in: RoundedRectangle(cornerRadius: 7))
              Spacer(minLength: 0)
            }.padding(width * 0.043).frame(height: 106)
              .frame(maxWidth: .infinity, alignment: .leading)
              .background(
                .white, in: UnevenRoundedRectangle(topLeadingRadius: 9, topTrailingRadius: 9)
              )
              .padding(.horizontal, width * 0.043)
          } else if route == .flashcards {
            Text(nextLesson?.title ?? nextChapter?.title ?? store.chapterTitle)
              .font(DS.font(captionSize, weight: .bold)).lineLimit(2).multilineTextAlignment(
                .center
              )
              .frame(width: width * 0.7866, height: 92)
              .background(.white, in: RoundedRectangle(cornerRadius: 10))
              .shadow(color: Color(hex: 0x315FBE).opacity(0.14), radius: 0, x: 7, y: 7)
              .rotationEffect(.degrees(-4)).padding(.bottom, 12)
          } else if route == .mistakes {
            VStack(alignment: .leading, spacing: 7) {
              HStack {
                Text("今日待复习").font(DS.font(14, weight: .bold))
                Spacer()
                Text("\(store.mistakes.filter { $0.book_id == store.selectedBookID }.count) 道").font(DS.font(16, weight: .bold)).foregroundStyle(DS.primary)
              }
              ForEach(Array(store.mistakes.filter { $0.book_id == store.selectedBookID }.prefix(2)), id: \.mistake_id) { mistake in
                HStack(spacing: 5) {
                  Circle().fill(DS.primary).frame(width: 4, height: 4)
                  Text(mistake.question).lineLimit(1)
                  Spacer(minLength: 0)
                  Text("待复习").fontWeight(.bold).foregroundStyle(DS.primary).fixedSize()
                }.font(DS.font(min(20, max(9, width * 0.087)))).padding(.horizontal, 5).padding(
                  .vertical, 4
                ).background(Color(hex: 0xF9F5FF), in: RoundedRectangle(cornerRadius: 6))
              }
            }.padding(9).frame(height: 106, alignment: .top).background(
              .white, in: UnevenRoundedRectangle(topLeadingRadius: 9, topTrailingRadius: 9)
            ).padding(.horizontal, 8)
          } else {
            VStack(spacing: 8) {
              ForEach(0..<3) { index in
                Capsule().fill(DS.border).frame(
                  width: width * (index == 1 ? 0.55 : 0.68), height: 4)
              }
              Text("原始记录 · 整理版").font(DS.font(captionSize)).foregroundStyle(DS.secondary)
            }.frame(width: width * 0.85, height: 100).background(
              .white, in: RoundedRectangle(cornerRadius: 9)
            ).padding(.bottom, 8)
          }
        }.frame(height: 118)
        HStack(spacing: 6) {
          StickerIcon(icon, size: 26, glyphSize: 16.9)
          VStack(alignment: .leading, spacing: 4) {
            Text(title).font(DS.font(16, weight: .bold)).lineLimit(1)
            Text(subtitle).font(DS.font(captionSize)).foregroundStyle(DS.secondary).lineLimit(1)
          }
          Spacer(minLength: 0)
          BCGlyph("ChevronRight").frame(width: 17, height: 17)
        }.padding(9).frame(height: 76)
      }.frame(width: width).background(DS.surface, in: RoundedRectangle(cornerRadius: 14))
        .clipShape(RoundedRectangle(cornerRadius: 14)).shadow(
          color: DS.text.opacity(0.12), radius: 4, x: 0, y: 4)
    }.buttonStyle(.plain)
  }
  private func globalAction(_ title: String, helper: String, icon: String, route: Route)
    -> some View
  {
    Button {
      store.go(route)
    } label: {
      VStack(alignment: .leading, spacing: 9) {
        StickerIcon(icon, size: 40)
        Text(title).font(DS.font(16, weight: .bold)).lineLimit(2)
      }.frame(maxWidth: .infinity, minHeight: 76, alignment: .leading).padding(
        .horizontal, store.isPad ? 14 : 10
      ).padding(.vertical, 12)
        .background(
          route == .courseDetail ? Color(hex: 0xF6FBFF) : DS.surface,
          in: RoundedRectangle(cornerRadius: 16)
        )
        .overlay(
          RoundedRectangle(cornerRadius: 16).stroke(
            route == .courseDetail ? Color(hex: 0xC9DEF5) : DS.border.opacity(0.72),
            style: StrokeStyle(lineWidth: 1, dash: route == .courseDetail ? [3, 3] : [])))
    }.buttonStyle(.plain).accessibilityHint(helper)
  }
  private func create() {
    store.courseFeatureStartDraft()
    store.go(.upload)
  }
}

private struct HomeCourseShelf: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  let courses: [Course]
  let selectedCourseID: String?
  let coverWidth: CGFloat
  let coverHeight: CGFloat
  let railWidth: CGFloat
  let short: Bool
  @State private var scrollCourseID: String?
  @State private var interactedWithShelf = false

  init(
    courses: [Course], selectedCourseID: String?, coverWidth: CGFloat,
    coverHeight: CGFloat, railWidth: CGFloat, short: Bool
  ) {
    self.courses = courses
    self.selectedCourseID = selectedCourseID
    self.coverWidth = coverWidth
    self.coverHeight = coverHeight
    self.railWidth = railWidth
    self.short = short
    // ScrollView needs its restored ID on its first layout, before it publishes
    // the leading visible item back through the scroll-position binding.
    _scrollCourseID = State(initialValue: selectedCourseID)
  }

  var body: some View {
    ScrollViewReader { proxy in
      ScrollView(.horizontal) {
        HStack(alignment: .center, spacing: -coverWidth * 0.32) {
          ForEach(courses) { course in
            Button {
              interactedWithShelf = true
              if selectedCourseID == course.id {
                store.selectCourse(course.id)
                store.go(.study)
              } else {
                choose(course.id)
                scrollCourseID = course.id
              }
            } label: {
              CourseCoverImage(path: store.courseFeatureCover(course))
                .frame(width: coverWidth, height: coverHeight).clipped()
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .blur(radius: selectedCourseID == course.id ? 0 : 1.5)
                .shadow(color: DS.text.opacity(0.13), radius: 4, x: 0, y: 5)
                .scaleEffect(selectedCourseID == course.id ? 1.16 : 0.9)
                .padding(.vertical, short ? 16 : 24)
            }
            .buttonStyle(.plain).id(course.id).zIndex(selectedCourseID == course.id ? 2 : 1)
            .accessibilityIdentifier("home.course.\(course.id)")
            .accessibilityLabel(
              "\(course.name)，\(selectedCourseID == course.id ? "当前选中，点击进入课程详情" : "点击切换到这门课程")"
            )
            .accessibilityAddTraits(selectedCourseID == course.id ? .isSelected : [])
          }
        }.scrollTargetLayout()
      }
      .contentMargins(.horizontal, max(0, (railWidth - coverWidth) / 2), for: .scrollContent)
      .scrollIndicators(.hidden).scrollTargetBehavior(.viewAligned)
      .scrollPosition(id: $scrollCourseID, anchor: .center)
      .onChange(of: scrollCourseID) { _, id in if interactedWithShelf, let id { choose(id) } }
      .simultaneousGesture(
        DragGesture(minimumDistance: 4).onChanged { _ in interactedWithShelf = true }
      )
      .mask(
        LinearGradient(
          stops: [
            .init(color: .white, location: 0), .init(color: .white, location: 0.88),
            .init(color: .clear, location: 1),
          ], startPoint: .leading, endPoint: .trailing)
      )
      .accessibilityIdentifier("home.course.shelf")
      .onAppear {
        interactedWithShelf = false
        restorePosition()
      }
      .onChange(of: selectedCourseID) { _, _ in restorePosition() }
      .task {
        await Task.yield()
        guard !Task.isCancelled, let selectedCourseID else { return }
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) { proxy.scrollTo(selectedCourseID, anchor: .center) }
      }
    }
  }
  private func choose(_ id: String) {
    guard selectedCourseID != id else { return }
    withAnimation(reduceMotion ? nil : DS.localAnimation) { store.selectCourse(id) }
  }
  private func restorePosition() {
    guard scrollCourseID != selectedCourseID else { return }
    var transaction = Transaction()
    transaction.disablesAnimations = true
    withTransaction(transaction) { scrollCourseID = selectedCourseID }
  }
}

struct CourseLibraryView: View {
  @Environment(AppStore.self) private var store
  @State private var removingID: String?
  var body: some View {
    CourseFeaturePage(useSourceLayoutTop: true) {
      HStack {
        Text("\(store.courses.count) 门课程").font(DS.font(20)).foregroundStyle(DS.text)
        Spacer()
        Button {
          store.courseFeatureStartDraft()
          store.go(.upload)
        } label: {
          HStack(spacing: 8) {
            BCGlyph("Plus", color: .white).frame(width: 18, height: 18)
            Text("创建课程").font(DS.font(20, weight: .bold))
          }.padding(.horizontal, 20).frame(height: 54).foregroundStyle(.white).background(
            DS.primary, in: Capsule())
        }.buttonStyle(.plain)
      }
      if store.courses.isEmpty {
        EmptyStateView(title: "还没有课程", message: "创建课程，再添加教材或其他学习文件。", icon: "BookOpen")
        BCButton("创建第一门课程") {
          store.courseFeatureStartDraft()
          store.go(.upload)
        }
      }
      ForEach(store.courses) { course in
        let source = store.sources.first {
          $0.id == (course.activeResourceID ?? course.resourceIDs.first)
        }
        let directory = source?.summary.content_mode == "directory"
        let ready = store.sources.filter {
          course.resourceIDs.contains($0.id) && $0.summary.status == "ready"
            && $0.summary.content_mode != "directory"
        }.count
        VStack(alignment: .leading, spacing: 16) {
          Button {
            store.selectCourse(course.id)
            store.go(.study)
          } label: {
            HStack(spacing: 16) {
              CourseCoverImage(path: store.courseFeatureCover(course)).frame(
                width: 64, height: 86
              ).clipped().clipShape(RoundedRectangle(cornerRadius: 10))
              VStack(alignment: .leading, spacing: 6) {
                Text(course.name).font(DS.font(16, weight: .bold))
                Text(directory ? "教材目录 · 尚未生成教学内容" : "\(course.resourceIDs.count) 份资料 · \(ready) 份可学习")
                  .font(DS.font(14)).foregroundStyle(DS.secondary)
                Text(source?.summary.title ?? "添加资料后开始学习").font(DS.font(14)).foregroundStyle(
                  DS.secondary)
              }.frame(maxWidth: .infinity, alignment: .leading)
            }.frame(height: 86)
          }.buttonStyle(.plain)
          if !directory {
            VStack(alignment: .leading, spacing: 6) {
              BCProgress(value: store.courseFeatureProgress(course))
              Text("课程完成 \(Int((store.courseFeatureProgress(course) * 100).rounded()))%").font(
                DS.font(20)
              ).foregroundStyle(DS.secondary).frame(height: 28, alignment: .top)
            }.padding(.top, 10).frame(height: 50)
          }
          HStack {
            Button {
              store.selectCourse(course.id)
              store.go(.courseDetail)
            } label: {
              HStack(spacing: 6) {
                BCGlyph("Settings", color: DS.primary).frame(width: 16, height: 16)
                Text("课程资料与偏好").font(DS.font(20))
              }
            }.buttonStyle(.plain).foregroundStyle(DS.primary)
            Spacer()
            Button {
              withAnimation { removingID = removingID == course.id ? nil : course.id }
            } label: {
              BCGlyph("Trash2", color: DS.primary).frame(width: 16, height: 16).frame(
                width: 44, height: 44)
            }.buttonStyle(.plain).accessibilityLabel("删除课程 \(course.name)")
          }.frame(height: 44)
          if removingID == course.id {
            VStack(alignment: .leading, spacing: 10) {
              Text("移除这门课程？资料文件和已有笔记会保留。").font(DS.font(20))
              HStack {
                BCButton("确认移除课程", style: .danger) {
                  withAnimation {
                    store.removeCourse(course.id)
                    removingID = nil
                  }
                  store.showToast("课程已移除，原资料和笔记仍保留")
                }
                BCButton("取消", style: .secondary) { removingID = nil }
              }
            }.padding(14).background(Color(hex: 0xFFF1F2), in: RoundedRectangle(cornerRadius: 14))
          } else {
            Color.clear.frame(height: 0)
          }
        }.padding(19).background(DS.surface, in: RoundedRectangle(cornerRadius: 14)).overlay(
          RoundedRectangle(cornerRadius: 14).stroke(DS.border))
      }
    }
  }
}

struct CourseDetailView: View {
  @Environment(AppStore.self) private var store
  @State private var removingResourceID: String?
  private var preferredLabels: [String] {
    guard let course = store.currentCourse else { return [] }
    let answers = CourseRecommendations.diagnosis(course)
    var result: [String] = []
    func add(_ label: String) { if !result.contains(label) { result.append(label) } }
    for goal in answers["goals"] ?? [] {
      if goal == "exam" || goal == "gaps" {
        add("章节练习")
        add("错题复盘")
      }
      if goal == "systematic" { add("闪卡回忆") }
    }
    for focus in answers["contentFoci"] ?? [] {
      if focus == "exercises" { add("章节练习") }
      if focus == "notes" { add("学习笔记") }
      if focus == "keyPoints" { add("闪卡回忆") }
    }
    for aid in answers["aids"] ?? [] {
      if aid == "mistakes" { add("错题复盘") }
      if aid == "guidedNotes" { add("学习笔记") }
    }
    for review in answers["reviews"] ?? [] {
      if review == "alongside" { add("章节练习") }
      if review == "periodic" { add("闪卡回忆") }
      if review == "mistakes" { add("错题复盘") }
      if review == "notes" { add("学习笔记") }
    }
    ["章节练习", "闪卡回忆", "错题复盘", "学习笔记"].forEach { add($0) }
    result = Array(result.prefix(3))
    if answers["aids"]?.contains("chat") == true { result.append("AI 问答") }
    if answers["aids"]?.contains("video") == true { result.append("优先图示与动画 · 视频素材待提供") }
    return result
  }
  var body: some View {
    CourseFeaturePage(width: 920, spacing: 20, useSourceLayoutTop: true) {
      if let course = store.currentCourse {
        let plan = CourseRecommendations.build(
          course: course, preferences: store.preferences, sources: store.sources)
        VStack(alignment: .leading, spacing: 22) {
          HStack(spacing: 6) {
            BCGlyph("Sparkles", color: DS.primary).frame(width: 16, height: 16)
            Text("当前课程").font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary).frame(
              minHeight: 28, alignment: .top)
          }
          ViewThatFits(in: .horizontal) {
            HStack(spacing: 9) {
              detailAction("调整画像与名称", icon: "Pencil") { edit(course) }
              detailAction("添加资料", icon: "Plus") { store.openSheet(.addMaterials) }
            }
            VStack(alignment: .leading, spacing: 9) {
              detailAction("调整画像与名称", icon: "Pencil") { edit(course) }
              detailAction("添加资料", icon: "Plus") { store.openSheet(.addMaterials) }
            }
          }
        }.padding(store.isPad ? 34.36 : 23).frame(maxWidth: .infinity, alignment: .leading)
          .background(DS.surface, in: RoundedRectangle(cornerRadius: 24)).overlay(
            RoundedRectangle(cornerRadius: 24).stroke(DS.border))
        CourseDetailGroup {
          sectionHeading("今日建议", detail: "\(plan.budget) 分钟预算")
          Text(plan.reviewDays.map { "建议每 \($0) 天回看一次重点与错题。" } ?? "按你的自由复习偏好，不安排固定复盘提醒。").font(
            DS.font(20)
          ).foregroundStyle(DS.secondary).frame(minHeight: 28, alignment: .topLeading).padding(
            .top, 9
          ).padding(.bottom, 16)
          if plan.recommendations.isEmpty {
            Text("当前没有可推荐的待做任务。选择已就绪的教材继续学习，或添加更多资料。").font(DS.font(20)).foregroundStyle(
              DS.secondary)
          }
          VStack(spacing: 9) {
            ForEach(plan.recommendations) { recommendation in
              Button {
                open(recommendation)
              } label: {
                HStack(spacing: 13) {
                  StickerIcon("CalendarDays", size: 42, glyphSize: 19)
                  VStack(alignment: .leading, spacing: 3) {
                    Text(recommendation.task.title).font(DS.font(16, weight: .bold)).frame(
                      minHeight: 20.8, alignment: .topLeading)
                    Text(
                      "\(store.sources.first { $0.id == recommendation.bookID }?.summary.title ?? "教材") · \(recommendation.task.task_type) · 建议 \(recommendation.minutes) 分钟"
                        + (recommendation.originalMinutes > recommendation.minutes
                          ? "（完整任务预计 \(recommendation.originalMinutes) 分钟）" : "")
                    ).font(DS.font(14)).foregroundStyle(DS.secondary).frame(
                      minHeight: 18.9, alignment: .topLeading)
                    Text(recommendation.reason).font(DS.font(20)).foregroundStyle(DS.primaryPressed)
                      .frame(minHeight: 28, alignment: .topLeading)
                    if recommendation.segments.count > 1 {
                      HStack(spacing: 5) {
                        ForEach(Array(recommendation.segments.enumerated()), id: \.offset) {
                          _, minutes in
                          Text("\(minutes) 分钟").font(DS.font(14)).padding(.horizontal, 8).padding(
                            .vertical, 5
                          ).background(DS.primarySoft, in: Capsule())
                        }
                      }.padding(.top, 5)
                    }
                  }.frame(maxWidth: .infinity, alignment: .leading)
                  BCGlyph("ArrowRight").frame(width: 18, height: 18)
                }.padding(13).frame(minHeight: 99.7).background(
                  DS.surface, in: RoundedRectangle(cornerRadius: 14)
                ).overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
              }.buttonStyle(.plain)
            }
          }
        }
        CourseDetailGroup {
          sectionHeading("课程资料", detail: "\(course.resourceIDs.count) 份")
          VStack(spacing: 9) {
            ForEach(course.resourceIDs, id: \.self) { id in
              let source = store.sources.first { $0.id == id }
              resourceRow(source, id: id)
            }
            if course.resourceIDs.isEmpty {
              Text("这门课程暂时没有资料，点击“添加资料”继续。").font(DS.font(20)).foregroundStyle(DS.secondary)
            }
          }.padding(.top, 14)
        }
        CourseDetailGroup {
          HStack {
            sectionHeading("学习画像")
            Button("修改") { edit(course) }.font(DS.font(20, weight: .bold)).foregroundStyle(
              DS.primaryPressed
            ).frame(height: 44)
          }
          LazyVGrid(
            columns: Array(
              repeating: GridItem(.flexible(), spacing: 9, alignment: .top),
              count: store.isPad ? 2 : 1), spacing: 9
          ) {
            ForEach(CourseDiagnosisQuestion.all) { question in
              VStack(alignment: .leading, spacing: 6) {
                Text(question.title).font(DS.font(16, weight: .bold)).frame(
                  minHeight: 20.8, alignment: .topLeading)
                Text(
                  (CourseRecommendations.diagnosis(course)[question.id] ?? []).map { value in
                    question.options.first { $0.0 == value }?.1 ?? value
                  }.joined(separator: "、")
                ).font(DS.font(20)).foregroundStyle(DS.secondary).frame(
                  minHeight: 28, alignment: .topLeading)
              }.padding(12).frame(maxWidth: .infinity, alignment: .topLeading).background(
                DS.background, in: RoundedRectangle(cornerRadius: 12))
            }
          }.padding(.top, 14)
        }
        CourseDetailGroup {
          sectionHeading("为你优先展示")
          CoursePreferredChips(labels: preferredLabels).padding(.top, 13)
        }
      } else {
        EmptyStateView(title: "暂无课程", message: "创建课程并添加资料后开始学习。")
        BCButton("返回首页") { store.replace(.home) }
      }
    }.confirmationDialog(
      "从课程移出这份资料？原资料和已有笔记会保留。",
      isPresented: Binding(
        get: { removingResourceID != nil }, set: { if !$0 { removingResourceID = nil } }),
      titleVisibility: .visible
    ) {
      Button("确认移出", role: .destructive) {
        if let id = removingResourceID, let courseID = store.currentCourse?.id {
          store.removeSource(id, from: courseID)
          store.showToast("已从课程移出，原资料仍保留")
        }
        removingResourceID = nil
      }
    }
  }
  @ViewBuilder private func resourceRow(_ source: SourceContent?, id: String) -> some View {
    Group {
      if store.isPad {
        HStack(spacing: 10) {
          resourceCopy(source)
          resourceSharingAction(source, id: id)
          resourceControls(source, id: id)
        }
      } else {
        VStack(alignment: .leading, spacing: 10) {
          HStack(spacing: 10) {
            resourceCopy(source)
            resourceSharingAction(source, id: id)
          }
          HStack(spacing: 10) { resourceControls(source, id: id) }
        }
      }
    }.padding(11).background(DS.surface, in: RoundedRectangle(cornerRadius: 14)).overlay(
      RoundedRectangle(cornerRadius: 14).stroke(DS.border))
  }
  private func resourceCopy(_ source: SourceContent?) -> some View {
    HStack(spacing: 10) {
      StickerIcon("BookOpen", size: 42, glyphSize: 21)
      VStack(alignment: .leading, spacing: 4) {
        Text(source?.summary.title ?? "资料暂时不可用").font(DS.font(16, weight: .bold))
        Text(
          source?.summary.content_mode == "directory"
            ? "教材目录 · 尚未生成教学内容" : source?.summary.status == "ready" ? "可开始学习" : "待整理 · 原文件已保留"
        ).font(DS.font(14)).foregroundStyle(DS.secondary)
      }.frame(maxWidth: .infinity, alignment: .leading)
    }
  }
  @ViewBuilder private func resourceSharingAction(_ source: SourceContent?, id: String) -> some View {
    if store.cloud != nil && id.hasPrefix("book_") && source?.summary.content_mode != "catalog" {
      detailAction("分享", icon: "Upload") { store.openCommunitySharing(bookID: id) }
        .accessibilityLabel("分享到发现：\(source?.summary.title ?? "教材")")
        .accessibilityIdentifier("course.share.open.\(id)")
    }
  }
  @ViewBuilder private func resourceControls(_ source: SourceContent?, id: String) -> some View {
    if store.sourceFileURL(id) != nil {
      detailAction("读原文", icon: "BookOpen") {
        store.selectSource(id)
        store.openSource(SourcePageTarget(bookID: id, title: source?.summary.title ?? "原文", page: 1,
          sourceText: source?.chunks.map(\.text).joined(separator: "\n\n"), from: .courseDetail))
      }
    }
    if source?.summary.status == "ready" {
      detailAction("进入", icon: "ArrowRight", trailing: true, fill: !store.isPad) {
        store.selectSource(id)
        store.go(.study)
      }
    }
    Button {
      removingResourceID = id
    } label: {
      BCGlyph("X", color: DS.danger).frame(width: 18, height: 18).frame(
        maxWidth: store.isPad ? nil : .infinity
      ).frame(minWidth: 46, minHeight: 46).background(
        DS.surface, in: RoundedRectangle(cornerRadius: 12)
      ).overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.border))
    }.buttonStyle(.plain).accessibilityLabel("从课程移出资料")
  }
  private func sectionHeading(_ title: String, detail: String? = nil) -> some View {
    HStack {
      Text(title).font(DS.font(20.8, weight: .bold))
      Spacer()
      if let detail { Text(detail).font(DS.font(20)).foregroundStyle(DS.secondary) }
    }.frame(minHeight: 28)
  }
  private func detailAction(
    _ title: String, icon: String, trailing: Bool = false, fill: Bool = false,
    action: @escaping () -> Void
  ) -> some View {
    Button(action: action) {
      HStack(spacing: 7) {
        if !trailing { BCGlyph(icon).frame(width: 16, height: 16) }
        Text(title).font(DS.font(20, weight: .bold)).fixedSize()
        if trailing { BCGlyph(icon).frame(width: 16, height: 16) }
      }.frame(maxWidth: fill ? .infinity : nil).padding(.horizontal, 13).frame(height: 46)
        .background(
          DS.surface, in: RoundedRectangle(cornerRadius: 12)
        ).overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.border))
    }.buttonStyle(.plain)
  }
  private func open(_ recommendation: CourseRecommendation) {
    store.selectSource(recommendation.bookID)
    store.selectedChapterID = recommendation.task.chapter_id
    store.selectedLessonID = recommendation.task.lesson_id
    switch recommendation.kind {
    case .practice: store.go(.assignment)
    case .mistakes: store.go(.mistakes)
    case .flashcards: store.go(.flashcards)
    case .notes: store.go(.notes)
    case .lesson, .visual:
      if let lesson = store.lessons.first(where: { $0.id == recommendation.task.lesson_id })
        ?? store.lessons.first(where: { $0.chapter_id == recommendation.task.chapter_id })
      {
        store.openLesson(lesson)
      } else {
        store.go(.study)
      }
    }
  }
  private func edit(_ course: Course) {
    store.draft.name = course.name
    store.draft.resourceIDs = course.resourceIDs
    store.draft.diagnosis = CourseRecommendations.diagnosis(course)
    store.draft.editingCourseID = course.id
    store.draft.catalogBookID = nil
    store.draft.step = -1
    store.go(.courseSetup)
  }
}

private struct CourseDetailGroup<Content: View>: View {
  @ViewBuilder let content: () -> Content
  var body: some View {
    VStack(alignment: .leading, spacing: 0, content: content).padding(21).frame(
      maxWidth: .infinity, alignment: .leading
    ).background(DS.surface, in: RoundedRectangle(cornerRadius: 18)).overlay(
      RoundedRectangle(cornerRadius: 18).stroke(DS.border))
  }
}
private struct CoursePreferredChips: View {
  let labels: [String]
  var body: some View {
    ViewThatFits(in: .horizontal) {
      HStack(spacing: 7) { chips }
      VStack(alignment: .leading, spacing: 7) { chips }
    }
  }
  @ViewBuilder private var chips: some View {
    ForEach(labels, id: \.self) {
      Text($0).font(DS.font(20)).foregroundStyle(DS.primaryPressed).padding(.horizontal, 8).padding(
        .vertical, 5
      ).background(DS.primarySoft, in: Capsule())
    }
  }
}
