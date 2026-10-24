import SwiftUI

struct CourseDiscoveryView: View {
  @Environment(AppStore.self) private var store
  @State private var query = ""
  @State private var category = "推荐"
  @State private var allCategories = false
  @FocusState private var searchFocused: Bool
  private let categories = ["推荐", "生物", "数学", "物理", "化学", "历史", "地理", "语文", "英语"]
  private var books: [CommunityBook] {
    let search = query.trimmingCharacters(in: .whitespacesAndNewlines).localizedLowercase
    return store.community.filter { book in
      if search.isEmpty {
        return category == "推荐" ? book.recommended == true : book.subject == category
      }
      return
        ([
          book.title, book.catalogTitle, book.subject, book.grade, book.version, book.volume ?? "",
          book.owner,
        ] + book.tags).joined(separator: " ").localizedLowercase.contains(search)
    }
  }
  @State private var pullDistance: CGFloat = 0
  @State private var refreshPhase: DiscoveryRefreshPhase = .idle
  @State private var refreshSummary = "下拉刷新课程"
  @State private var refreshRequest = 0

  var body: some View {
    GeometryReader { geometry in
      let gutter = store.isPad ? 32.0 : max(16.0, geometry.size.width * 0.04)
      VStack(spacing: 0) {
        searchHeader
          .padding(.horizontal, gutter).padding(.top, 4).padding(.bottom, 12)
          .navigationBarSurface()
        ScrollView {
          LazyVStack(
            alignment: .leading, spacing: store.isPad ? 18 : 10, pinnedViews: [.sectionHeaders]
          ) {
            Section {
              VStack(alignment: .leading, spacing: 4) {
                if books.isEmpty {
                  EmptyStateView(
                    title: "没有找到相关课程", message: "换个关键词，或查看其他课程分类。",
                    asset: "/assets/brand/empty-states/no-results-v1.png")
                }
                LazyVGrid(
                  columns: [
                    GridItem(.flexible(), spacing: store.isPad ? 12 : 10),
                    GridItem(.flexible(), spacing: store.isPad ? 12 : 10),
                  ],
                  alignment: .leading, spacing: store.isPad ? 12 : 10
                ) {
                  ForEach(books) { book in
                    Button {
                      store.selectedCommunityID = book.id
                      store.go(.communityBook)
                    } label: {
                      CommunityCourseTile(book: book)
                    }.buttonStyle(.plain).accessibilityLabel("\(book.title)，\(book.subject)，查看课程")
                      .accessibilityIdentifier("community.course.\(book.id)")
                  }
                }
              }.frame(maxWidth: 820, alignment: .leading)
            } header: {
              categoryHeader(gutter: gutter)
                .padding(.horizontal, gutter)
                .background(DS.background)
                .overlay(alignment: .bottom) {
                  ProgressiveMaterial(top: true).frame(height: 24).offset(y: 24)
                    .allowsHitTesting(false)
                }
                .padding(.horizontal, -gutter)
            }
          }.padding(.horizontal, gutter).padding(.bottom, 28)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
              DiscoveryRefreshBridge(
                request: refreshRequest,
                onPull: { pullDistance = $0 },
                onPhase: { phase in
                  refreshPhase = phase
                  if phase == .loading { refreshSummary = "正在刷新课程" }
                  if phase == .launching { refreshSummary = "课程已更新" }
                  if phase == .failed { refreshSummary = "刷新失败，请重试" }
                },
                refresh: { try await store.refreshCommunity() })
            }
        }
        .accessibilityIdentifier("community.scroll")
        .accessibilityValue(refreshSummary)
        .accessibilityAction(named: Text("刷新课程")) { refreshRequest += 1 }
        .scrollDismissesKeyboard(.interactively)
        .overlay(alignment: .top) {
          DiscoveryRefreshScene(distance: pullDistance, phase: refreshPhase)
            .allowsHitTesting(false)
        }
      }
    }
  }

  private var searchHeader: some View {
    HStack(spacing: 10) {
      Image(systemName: "magnifyingglass").font(.system(size: 19))
      TextField("搜索课程", text: $query).font(DS.font(20)).submitLabel(.search)
        .focused($searchFocused).onSubmit { searchFocused = false }
        .autocorrectionDisabled().accessibilityIdentifier("community.search")
      if !query.isEmpty {
        Button {
          query = ""
        } label: {
          Image(systemName: "xmark").frame(width: 44, height: 44)
        }.buttonStyle(.plain).accessibilityLabel("清除搜索内容")
      }
    }.padding(.horizontal, 14).frame(height: 52).frame(maxWidth: 820)
      .background(DS.surface, in: Capsule()).overlay(Capsule().stroke(DS.border))
      .frame(maxWidth: .infinity, alignment: .leading)
  }

  private func categoryHeader(gutter: CGFloat) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack(spacing: 0) {
        ScrollView(.horizontal) {
          HStack(spacing: 0) { ForEach(categories, id: \.self) { categoryButton($0) } }
            .padding(.horizontal, gutter)
        }.scrollIndicators(.hidden)
        Button {
          withAnimation { allCategories.toggle() }
        } label: {
          Image(systemName: allCategories ? "chevron.up" : "chevron.down").frame(
            width: 44, height: 44
          ).background(DS.background.opacity(0.9))
        }.buttonStyle(.plain).accessibilityLabel(allCategories ? "收起全部分类" : "查看更多分类")
      }.padding(.horizontal, -gutter)
      if allCategories {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 74))], spacing: 8) {
          ForEach(categories, id: \.self) { categoryButton($0) }
        }.padding(12).background(DS.surface, in: RoundedRectangle(cornerRadius: 14))
          .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
      }
    }
  }
  private func categoryButton(_ value: String) -> some View {
    Button {
      category = value
      query = ""
      allCategories = false
    } label: {
      Text(value).font(DS.font(20, weight: .bold))
        .foregroundStyle(category == value && query.isEmpty ? DS.primary : DS.secondary)
        .frame(width: 64, height: 44)

    }.buttonStyle(.plain).accessibilityAddTraits(
      category == value && query.isEmpty ? .isSelected : [])
  }
}

private struct CommunityCourseTile: View {
  let book: CommunityBook
  var body: some View {
    VStack(spacing: 0) {
      visual
      copy.frame(maxWidth: .infinity, minHeight: 84, alignment: .topLeading)
    }.background(DS.surface, in: RoundedRectangle(cornerRadius: 14)).clipShape(
      RoundedRectangle(cornerRadius: 14)
    ).shadow(color: DS.text.opacity(0.10), radius: 4, x: 0, y: 3)
  }
  private var visual: some View {
    GeometryReader { geometry in
      let coverWidth = geometry.size.width * 0.80
      AssetImage(book.cover).frame(width: coverWidth, height: coverWidth * 1.355)
        .background(.white)
        .clipShape(RoundedRectangle(cornerRadius: 2))
        .shadow(color: DS.text.opacity(0.18), radius: 10, x: 6, y: 12)
        .rotationEffect(.degrees(-4))
        .position(x: geometry.size.width / 2, y: geometry.size.height / 2 - 3)
    }
    .aspectRatio(0.80, contentMode: .fit)
    .background(DS.background)
  }
  private var copy: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(book.catalogTitle).font(DS.font(16, weight: .bold)).foregroundStyle(DS.text).lineLimit(2)
        .frame(maxWidth: .infinity, minHeight: 20.8, alignment: .topLeading)
      HStack(spacing: 4) {
        Text("\(book.learners) 人学习").font(DS.font(14)).foregroundStyle(DS.secondary)
          .lineLimit(1).minimumScaleFactor(0.75)
        Spacer(minLength: 0)
        Text("进入").font(DS.font(14, weight: .bold)).foregroundStyle(.white).padding(.horizontal, 12)
          .frame(minWidth: 56, minHeight: 36).background(DS.primary, in: Capsule())
      }
    }.padding(12)
  }
}

struct CommunityBookDetailView: View {
  @Environment(AppStore.self) private var store
  @State private var selectedTab = "课程简介"
  private var book: CommunityBook? {
    store.community.first { $0.id == store.selectedCommunityID } ?? store.community.first
  }
  var body: some View {
    if let book {
      CourseCoverToContent(cover: book.cover) {
        VStack(spacing: 8) {
          Text(book.owner).font(DS.font(14)).foregroundStyle(DS.secondary)
          Text(book.title).font(DS.font(20.8, weight: .bold)).multilineTextAlignment(.center).frame(
            maxWidth: 220)
          Text(
            [book.subject, book.grade, book.version, book.volume].compactMap { $0 }.joined(
              separator: " · ")
          ).font(DS.font(14)).foregroundStyle(DS.secondary)
          HStack(spacing: 0) {
            stat("正在学习", "\(book.learners) 人")
            stat("课程章节", "\(book.chapters.count) 章")
            stat("概念闪卡", "\(book.flashcardCount) 张")
            stat("学习进度", "\(Int(book.progress))%")
          }.padding(.vertical, 14).frame(maxWidth: .infinity).background(
            DS.background, in: RoundedRectangle(cornerRadius: 12))
          HStack(spacing: 24) {
            ForEach(["课程简介", "评论"], id: \.self) { tab in
              Button {
                withAnimation { selectedTab = tab }
              } label: {
                Text(tab).font(DS.font(16, weight: .bold)).foregroundStyle(
                  selectedTab == tab ? DS.primary : DS.secondary
                ).padding(.vertical, 12).overlay(alignment: .bottom) {
                  if selectedTab == tab { Capsule().fill(DS.primary).frame(height: 3) }
                }
              }.buttonStyle(.plain)
            }
          }.frame(maxWidth: .infinity, alignment: .leading)
          if selectedTab == "课程简介" {
            Text(book.description).font(DS.font(20)).lineSpacing(7).frame(
              maxWidth: .infinity, alignment: .leading)
          } else {
            if store.cloud != nil {
              Text("暂无评论").font(DS.font(14)).foregroundStyle(DS.secondary)
            } else {
              comment("周", name: "周同学", text: "重点整理得很清楚，适合复习\(book.catalogTitle)。")
              comment("陈", name: "陈同学", text: "课程结构简洁，导入后继续学习很方便。")
            }
          }
        }
      } action: {
        BCButton("导入到我的课程", icon: "square.and.arrow.down") {
          store.courseFeatureStartDraft(catalogID: book.id)
          store.draft.step = 0
          store.go(.courseSetup)
        }.accessibilityIdentifier("community.import")
      }
    } else {
      EmptyStateView(
        title: "课程暂时无法打开", message: "返回发现页面，重新选择一门课程。",
        asset: "/assets/brand/empty-states/connection-error-v1.png")
    }
  }
  private func stat(_ title: String, _ value: String) -> some View {
    VStack(spacing: 3) {
      Text(value).font(DS.font(20, weight: .bold))
      Text(title).font(DS.font(14)).foregroundStyle(DS.secondary)
    }.frame(maxWidth: .infinity)
  }
  private func comment(_ initial: String, name: String, text: String) -> some View {
    HStack(alignment: .top, spacing: 12) {
      Text(initial).font(DS.font(16, weight: .bold)).frame(width: 40, height: 40).background(
        DS.primarySoft, in: Circle())
      VStack(alignment: .leading, spacing: 8) {
        Text(name).font(DS.font(16, weight: .bold))
        Text(text).font(DS.font(14)).foregroundStyle(DS.secondary)
      }
    }.padding(.vertical, 10)
  }
}

struct LearnerProfileView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var compactHeader = false
  private var tasks: [StudyTask] {
    store.sources.filter { store.currentCourse?.resourceIDs.contains($0.id) == true }.flatMap {
      $0.plan.tasks
    }
  }
  private var pendingTasks: [StudyTask] {
    tasks.filter { $0.status != "done" && $0.status != "completed" }
  }
  private var headerTopInset: CGFloat { store.isPad ? 42 : 4 }

  var body: some View {
    GeometryReader { geometry in
      profilePage
        .accessibilityIdentifier("profile.scroll")
        .overlay(alignment: .top) {
          profileHeader
            .frame(maxWidth: 1120)
            .padding(.horizontal, min(32, max(16, geometry.size.width * 0.04)))
            .frame(maxWidth: .infinity)
            .padding(.top, headerTopInset)
            .navigationBarSurface()
        }
    }
  }

  private var profileHeader: some View {
    ZStack(alignment: .trailing) {
      HStack(spacing: 0) {
        if compactHeader { Spacer(minLength: 0) }
        Text("我的").font(DS.font(27.2, weight: .bold))
          .foregroundStyle(DS.text)
          .frame(height: DS.navigationRowHeight, alignment: .top)
          .accessibilityAddTraits(.isHeader)
          .accessibilityIdentifier("profile.title")
        Spacer(minLength: 0)
      }
      if !compactHeader {
        Button {
          store.go(.settings)
        } label: {
          Image(systemName: "gearshape").frame(width: 44, height: 44).foregroundStyle(
            DS.primaryPressed
          ).background(Color(hex: 0xF7F3FF), in: RoundedRectangle(cornerRadius: 12)).overlay(
            RoundedRectangle(cornerRadius: 12).stroke(Color(hex: 0xDED4F7)))
        }.buttonStyle(.plain).offset(y: DS.navigationButtonOffset).accessibilityLabel("设置")
          .accessibilityIdentifier("profile.settings")
          .transition(.opacity)
      }
    }.frame(height: DS.navigationRowHeight)
  }

  private var profilePage: some View {
    CourseFeaturePage(topInset: headerTopInset, spacing: 12) {
      // Reserve a stable header slot in the document so switching alignment
      // never changes the scroll offset or moves the cards beneath the bar.
      Color.clear.frame(height: DS.navigationRowHeight)
        .accessibilityHidden(true)
        .onGeometryChange(for: Int.self) { geometry in
          let offset = headerTopInset - geometry.frame(in: .scrollView).minY
          return offset > 16 ? 2 : offset > 4 ? 1 : 0
        } action: { region in
          let next = region == 2 || (compactHeader && region == 1)
          guard next != compactHeader else { return }
          withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.25)) {
            compactHeader = next
          }
        }
      BCCard {
        VStack(spacing: 12) {
          HStack {
            Text("PROFILE").font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary)
            Spacer()
            Text(store.preferences == nil ? "待完善" : "已完善").font(DS.font(20, weight: .bold))
              .foregroundStyle(DS.primaryPressed).padding(.horizontal, 9).padding(.vertical, 5)
              .background(Color(hex: 0xF4EFFF), in: Capsule())
          }
          Button {
            store.go(.preferenceSetup)
          } label: {
            ZStack(alignment: .bottomTrailing) {
              ProfilePortraitArtwork().frame(height: store.isPad ? 190 : 132).frame(
                maxWidth: .infinity)
              Label("重新设置", systemImage: "arrow.counterclockwise").font(
                DS.font(14, weight: .bold)
              ).padding(
                .horizontal, 9
              ).padding(.vertical, 5).background(.white, in: Capsule()).overlay(
                Capsule().stroke(Color(hex: 0xD8C9F8))
              ).padding(10)
            }
          }.buttonStyle(.plain).accessibilityLabel("重新设置学习偏好")
            .accessibilityIdentifier("profile.resetPreferences")
          VStack(alignment: .leading, spacing: 8) {
            Text("当前课程").font(DS.font(20, weight: .bold)).foregroundStyle(DS.secondary)
            if store.courses.isEmpty {
              Button("选择课程") { store.go(.library) }.font(DS.font(16)).foregroundStyle(DS.primary)
            } else {
              Menu {
                ForEach(store.courses) { course in
                  Button(course.name) { store.selectCourse(course.id) }
                }
              } label: {
                HStack {
                  Text(store.currentCourse?.name ?? "选择课程").font(DS.font(20)).foregroundStyle(
                    DS.text
                  ).lineLimit(1)
                  Spacer()
                  Image(systemName: "chevron.up.chevron.down").font(.system(size: 12))
                    .foregroundStyle(DS.text)
                }.padding(.horizontal, 8).frame(height: 28).frame(
                  maxWidth: .infinity, alignment: .leading
                ).overlay(RoundedRectangle(cornerRadius: 6).stroke(DS.border))
              }.accessibilityLabel("切换课程")

            }
          }
        }
      }
      BCCard {
        VStack(alignment: .leading, spacing: 14) {
          HStack(spacing: 12) {
            CourseBadgeIcon(name: "Coins", index: 0, size: 46)
            VStack(alignment: .leading, spacing: 3) {
              Text("CREDITS").font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary)
              Text("我的积分").font(DS.font(20.8, weight: .bold))
            }
            Spacer()
            HStack(alignment: .firstTextBaseline, spacing: 4) {
              Text("\(store.credits)").font(DS.font(16, weight: .bold))
              Text("积分").font(DS.font(14))
            }.foregroundStyle(DS.primary)
          }
          Text("AI 对话每次 1 积分 · 视频生成每次 10 积分").font(DS.font(20)).foregroundStyle(DS.secondary)
        }
      }
      BCCard {
        VStack(alignment: .leading, spacing: 18) {
          HStack {
            VStack(alignment: .leading, spacing: 4) {
              Text("TODAY").font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary)
              Text("今日计划").font(DS.font(20.8, weight: .bold))
            }
            Spacer()
            ZStack {
              Circle().stroke(DS.primarySoft, lineWidth: 5)
              Circle().trim(
                from: 0,
                to: tasks.isEmpty
                  ? 0 : Double(tasks.count - pendingTasks.count) / Double(tasks.count)
              ).stroke(DS.primary, style: StrokeStyle(lineWidth: 5, lineCap: .round))
                .rotationEffect(.degrees(-90))
              VStack(spacing: 2) {
                Text(tasks.isEmpty ? "—" : "\(pendingTasks.count)").font(DS.font(16, weight: .bold))
                Text(!tasks.isEmpty && pendingTasks.isEmpty ? "已完成" : "项待完成").font(DS.font(14))
              }
            }.frame(width: 64, height: 64).accessibilityLabel("\(pendingTasks.count) 项待完成")
          }
          if tasks.isEmpty {
            HStack(spacing: 12) {
              StickerIcon("CalendarCheck2", size: 40)
              VStack(alignment: .leading, spacing: 7) {
                Text("今天还没有学习计划").font(DS.font(16, weight: .bold))
                Text("先选择一门课程，再安排今天要完成的内容。").font(DS.font(14)).foregroundStyle(DS.secondary)
              }
            }
          } else {
            ForEach(Array((pendingTasks.isEmpty ? tasks : pendingTasks).prefix(2))) { task in
              Button {
                store.go(.plan)
              } label: {
                HStack(spacing: 12) {
                  StickerIcon(
                    (task.status == "done" || task.status == "completed") ? "Check" : "BookOpen",
                    size: 36)
                  VStack(alignment: .leading, spacing: 6) {
                    Text(task.title).font(DS.font(16, weight: .bold))
                    Text("\(task.task_type) · \(task.minutes) 分钟").font(DS.font(14))
                      .foregroundStyle(DS.secondary)
                  }
                  Spacer()
                  Image(systemName: "arrow.right")
                }
              }.buttonStyle(.plain).padding(.vertical, 7).overlay(alignment: .top) {
                Rectangle().fill(DS.border).frame(height: 1)
              }
            }
          }
          BCButton(tasks.isEmpty ? "选择课程" : "查看今日计划", icon: "calendar") {
            store.go(tasks.isEmpty ? .library : .plan)
          }
        }
      }
    }
  }
}

private struct ProfilePortraitArtwork: View {
  var body: some View {
    Canvas { context, size in
      let scale = min(min(size.width, 280) / 280, size.height / 156)
      let offset = (size.width - 280 * scale) / 2
      context.translateBy(x: offset, y: (size.height - 156 * scale) / 2)
      context.scaleBy(x: scale, y: scale)
      var outline = Path()
      outline.move(to: CGPoint(x: 78, y: 149))
      outline.addCurve(
        to: CGPoint(x: 125, y: 91), control1: CGPoint(x: 83, y: 122),
        control2: CGPoint(x: 100, y: 102))
      outline.addCurve(
        to: CGPoint(x: 98, y: 49), control1: CGPoint(x: 109, y: 84), control2: CGPoint(x: 98, y: 68)
      )
      outline.addCurve(
        to: CGPoint(x: 140, y: 6), control1: CGPoint(x: 98, y: 22), control2: CGPoint(x: 116, y: 6))
      outline.addCurve(
        to: CGPoint(x: 182, y: 49), control1: CGPoint(x: 164, y: 6),
        control2: CGPoint(x: 182, y: 22))
      outline.addCurve(
        to: CGPoint(x: 155, y: 91), control1: CGPoint(x: 182, y: 68),
        control2: CGPoint(x: 171, y: 84))
      outline.addCurve(
        to: CGPoint(x: 202, y: 149), control1: CGPoint(x: 180, y: 102),
        control2: CGPoint(x: 197, y: 122))
      context.stroke(
        outline, with: .color(Color(hex: 0x8B5CF6)),
        style: StrokeStyle(lineWidth: 5, lineCap: .round, dash: [10, 10]))
      context.fill(
        Path(ellipseIn: CGRect(x: 106, y: 16, width: 68, height: 68)),
        with: .color(Color(hex: 0xC6B5F5)))
      var torso = Path()
      torso.move(to: CGPoint(x: 92, y: 149))
      torso.addCurve(
        to: CGPoint(x: 140, y: 98), control1: CGPoint(x: 97, y: 118),
        control2: CGPoint(x: 116, y: 98))
      torso.addCurve(
        to: CGPoint(x: 188, y: 149), control1: CGPoint(x: 164, y: 98),
        control2: CGPoint(x: 183, y: 118))
      torso.closeSubpath()
      context.fill(torso, with: .color(Color(hex: 0xC6B5F5)))
      var plus = Path()
      plus.move(to: CGPoint(x: 140, y: 39))
      plus.addLine(to: CGPoint(x: 140, y: 61))
      plus.move(to: CGPoint(x: 129, y: 50))
      plus.addLine(to: CGPoint(x: 151, y: 50))
      context.stroke(plus, with: .color(.white), style: StrokeStyle(lineWidth: 7, lineCap: .round))
    }.background(Color(hex: 0xF4EFFF), in: RoundedRectangle(cornerRadius: 12)).overlay(
      RoundedRectangle(cornerRadius: 12).stroke(Color(hex: 0xDED4F7)))
  }
}

struct LearnerSettingsView: View {
  @Environment(AppStore.self) private var store
  @State private var confirmingLogout = false
  var body: some View {
    CourseFeaturePage(width: 640, useSourceLayoutTop: true) {
      SettingsSourceCard {
        HStack(spacing: 14) {
          BCGlyph("UserRound", color: DS.primary).frame(width: 26, height: 26).frame(
            width: 50, height: 50
          ).background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 15))
          VStack(alignment: .leading, spacing: 4) {
            Text(store.preferences?.displayName ?? "我的账号").font(DS.font(20.8, weight: .bold)).frame(
              height: 26, alignment: .top)
            Text(store.cloudState.localLogin ? "本地默认账号" : "当前账号").font(DS.font(20)).foregroundStyle(
              DS.secondary
            ).frame(
              height: 28, alignment: .top)
          }
          Spacer()
        }
      }
      SettingsSourceCard {
        VStack(alignment: .leading, spacing: 12) {
          Text("学习偏好").font(DS.font(20.8, weight: .bold)).frame(height: 26, alignment: .top)
          VStack(spacing: 0) {
            preferenceRow("主要学习目标", value: CourseOptions.goalLabel(store.preferences?.primaryGoal))
            preferenceRow("每日学习时间", value: CourseOptions.timeLabel(store.preferences?.dailyTime))
            preferenceRow(
              "学习卡点",
              value: CourseOptions.selectionLabel(
                store.preferences?.learningBarriers ?? [], options: CourseOptions.barriers))
            preferenceRow(
              "帮助方式",
              value: CourseOptions.selectionLabel(
                store.preferences?.guidanceStyles ?? [], options: CourseOptions.guidance))
            preferenceRow(
              "计划支持", value: CourseOptions.planningLabel(store.preferences?.planningSupport))
          }
        }
      }
      VStack(spacing: 8) {
        Button {
          confirmingLogout = true
        } label: {
          HStack(spacing: 12) {
            BCGlyph("LogOut", color: DS.danger).frame(width: 20, height: 20)
            Text("退出账号").font(DS.font(20, weight: .bold))
            Spacer()
            BCGlyph("ArrowRight", color: DS.danger).frame(width: 18, height: 18)
          }.padding(.horizontal, 21).frame(maxWidth: .infinity).frame(height: 62).foregroundStyle(
            DS.danger
          )
          .background(DS.surface, in: RoundedRectangle(cornerRadius: 14))
          .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
        }.buttonStyle(.plain).accessibilityIdentifier("settings.logout")
        Text(
          store.cloud == nil
            ? "退出后，再次打开软件也需要重新完成引导。"
            : store.cloudState.localLogin ? "当前无需邮箱验证，退出后可直接重新进入本地默认账号。" : "退出后需要重新使用邮箱验证码登录。"
        )
        .font(DS.font(20)).lineSpacing(4).foregroundStyle(DS.secondary)
        .frame(maxWidth: .infinity, minHeight: 28, alignment: .top)
      }.padding(.top, 8)
      SettingsSourceCard {
        VStack(alignment: .leading, spacing: 8) {
          Text(store.cloud == nil ? "本机学习数据" : "账号学习数据").font(DS.font(20.8, weight: .bold)).frame(
            height: 26, alignment: .top)
          Text(
            store.cloud == nil
              ? "已保留 \(store.courses.count) 门课程。退出账号后，课程、资料和学习记录仍会保留，完成引导即可继续学习。"
              : "已保存 \(store.courses.count) 门课程。课程与已同步进度保留在账号中；本机笔记暂时只保存在这台设备。待同步进度会在下次登录后继续上传。"
          ).font(DS.font(20))
            .foregroundStyle(DS.secondary).lineSpacing(4).padding(.vertical, 2)
        }
      }
    }.overlay {
      if confirmingLogout {
        ZStack {
          DS.text.opacity(0.46).ignoresSafeArea().onTapGesture { confirmingLogout = false }
          VStack(alignment: .leading, spacing: 16) {
            Image(systemName: "rectangle.portrait.and.arrow.right").font(.system(size: 26))
              .foregroundStyle(DS.danger).frame(width: 50, height: 50).background(
                DS.danger.opacity(0.08), in: RoundedRectangle(cornerRadius: 15))
            Text("确认退出账号？").font(DS.font(20.8, weight: .bold))
            Text(
              store.cloud == nil
                ? "退出后需要重新完成个性化问题。已有课程、资料和学习记录会保留在本机。"
                : store.cloudState.localLogin
                  ? "已同步课程和学习进度会保留在本地默认账号中，可直接重新进入。" : "退出后需要重新使用邮箱验证码登录。已同步课程和学习进度会保留在账号中。"
            ).font(DS.font(20)).foregroundStyle(
              DS.secondary
            ).lineSpacing(6)
            HStack {
              BCButton("取消", style: .secondary) { confirmingLogout = false }
              BCButton("确认退出", style: .danger) {
                confirmingLogout = false
                store.logout()
              }
            }
          }.padding(24).frame(maxWidth: 400).background(
            DS.surface, in: RoundedRectangle(cornerRadius: 14)
          ).overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border)).padding(16)
        }.accessibilityAddTraits(.isModal)
      }
    }
  }
  private func preferenceRow(_ title: String, value: String) -> some View {
    VStack(spacing: 0) {
      Rectangle().fill(DS.border).frame(height: 1)
      HStack(alignment: .firstTextBaseline) {
        Text(title).foregroundStyle(DS.secondary)
        Spacer()
        Text(value).font(DS.font(20, weight: .bold)).multilineTextAlignment(.trailing)
      }.font(DS.font(20)).frame(minHeight: 28).padding(.vertical, 12)
    }
  }
}
private struct SettingsSourceCard<Content: View>: View {
  @ViewBuilder let content: () -> Content
  var body: some View {
    content().padding(21).frame(maxWidth: .infinity, alignment: .leading)
      .background(DS.surface, in: RoundedRectangle(cornerRadius: 14))
      .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
  }
}
