import SwiftUI

struct CourseSwitcherView: View {
  @Environment(AppStore.self) private var store
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Text("切换后会恢复这门课程上次打开的资料和小节。")
        .font(DS.font(20)).foregroundStyle(DS.secondary).frame(
          minHeight: 28, alignment: .topLeading)
      ScrollView {
        VStack(spacing: 0) {
          ForEach(Array(store.courses.enumerated()), id: \.element.id) { index, course in
            Button {
              store.openCourse(course)
            } label: {
              HStack(spacing: 11) {
                CourseCoverImage(path: store.courseFeatureCover(course))
                  .frame(width: 40, height: 50).clipped().clipShape(
                    RoundedRectangle(cornerRadius: 7)
                  )
                  .overlay(RoundedRectangle(cornerRadius: 7).stroke(DS.border))
                VStack(alignment: .leading, spacing: 4) {
                  Text(course.name).font(DS.font(16, weight: .bold)).lineLimit(1)
                    .frame(minHeight: 20.8, alignment: .topLeading)
                  Text("\(course.resourceIDs.count) 份资料").font(DS.font(14))
                    .foregroundStyle(DS.secondary).frame(minHeight: 18.9, alignment: .topLeading)
                  if let status = store.courseCreationLabel(course) {
                    Text(status).font(DS.font(12, weight: .semibold)).foregroundStyle(DS.primary)
                  }
                }.frame(maxWidth: .infinity, alignment: .leading)
                BCGlyph(
                  course.id == store.selectedCourseID ? "Check" : "ChevronRight", color: DS.primary
                )
                .frame(width: 19, height: 19).frame(width: 22)
              }.padding(.horizontal, 12).frame(minHeight: 82)
                .background(course.id == store.selectedCourseID ? DS.primarySoft : DS.surface)
                .overlay(alignment: .bottom) {
                  if index < store.courses.count - 1 {
                    Rectangle().fill(DS.border).frame(height: 1)
                  }
                }
            }.buttonStyle(.plain).accessibilityAddTraits(
              course.id == store.selectedCourseID ? .isSelected : [])
              .accessibilityIdentifier("study.course.\(course.id)")
          }
        }
      }.frame(height: min(390, CGFloat(store.courses.count) * 82))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
      if store.courses.isEmpty {
        Text("还没有课程，创建课程并添加资料后即可学习。")
          .font(DS.font(20)).foregroundStyle(DS.secondary)
      }
      HStack(spacing: 10) {
        BCButton("创建新课程", icon: "Plus") {
          store.courseFeatureStartDraft()
          store.closeSheet()
          store.go(.upload)
        }
        BCButton("全部课程", icon: "LibraryBig", style: .secondary) {
          store.closeSheet()
          store.go(.library)
        }
      }
    }.padding(.vertical, 12).frame(maxHeight: .infinity, alignment: .top)
  }
}

struct EditCourseChapterView: View {
  @Environment(AppStore.self) private var store
  @State private var sourceTitle = ""
  @State private var aiTitle = ""
  @State private var start = ""
  @State private var end = ""
  @State private var parentID = ""
  @State private var error: String?
  @State private var confirmingDelete = false
  @FocusState private var focusedField: String?
  private var chapter: Chapter? { store.chapters.first { $0.id == store.selectedChapterID } }
  private var excludedParents: Set<String> {
    CourseChapterValidation.descendants(of: store.selectedChapterID ?? "", in: store.chapters)
      .union([store.selectedChapterID ?? ""])
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 14) {
        if let chapter {
          HStack {
            let goodMatch = chapter.status == "匹配良好"
            Text(chapter.status).font(DS.font(20, weight: .bold))
              .foregroundStyle(goodMatch ? DS.mint : DS.orange)
              .padding(.horizontal, 10).frame(height: 40)
              .background(goodMatch ? Color(hex: 0xECFDF5) : Color(hex: 0xFFF3E6), in: Capsule())
              .overlay(Capsule().stroke(goodMatch ? Color(hex: 0xB8EBD8) : Color(hex: 0xF7CBA4)))
            Spacer()
            Text(chapter.confidence > 0 ? "\(Int(chapter.confidence))% 综合置信度" : "识别置信度未知，需核对原文").font(DS.font(16, weight: .bold))
              .foregroundStyle(Color(hex: 0x4D5C77))
          }.frame(height: 40)
          VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 10) {
              Text("识别详情").font(DS.font(16, weight: .bold)).fixedSize()
              Spacer(minLength: 0)
              Text("层级 \(chapter.level) · PDF \(chapter.page_start)–\(chapter.page_end) 页")
                .font(DS.font(20)).foregroundStyle(DS.secondary)
            }.frame(minHeight: 28)
            Text("暂无单项识别证据，可继续人工调整名称、页码与所属上级。")
              .font(DS.font(14)).lineSpacing(2.1).foregroundStyle(DS.secondary)
              .frame(minHeight: 18.9, alignment: .topLeading).padding(.vertical, 14)
            Text("识别来源：\(chapter.source.isEmpty ? "未知" : chapter.source)")
              .font(DS.font(20)).foregroundStyle(DS.secondary).frame(
                minHeight: 28, alignment: .topLeading)
          }.padding(12).frame(maxWidth: .infinity, alignment: .leading).background(
            DS.background, in: RoundedRectangle(cornerRadius: 14))
          field("原书目录名称", text: $sourceTitle)
          field("课程章节名称", text: $aiTitle)
          HStack(spacing: 12) {
            field("PDF 起始页", text: $start, numeric: true)
            field("PDF 结束页", text: $end, numeric: true)
          }
          VStack(alignment: .leading, spacing: 8) {
            Text("所属上级章节").font(DS.font(20, weight: .bold)).frame(height: 28, alignment: .top)
            Menu {
              Button("无（作为一级章节）") { parentID = "" }
              ForEach(
                store.chapters.filter {
                  !excludedParents.contains($0.id) && $0.level < chapter.level
                }
              ) { parent in
                Button("\(parent.source_title)（\(parent.page_start)–\(parent.page_end) 页）") {
                  parentID = parent.id
                }
              }
            } label: {
              HStack {
                Text(
                  store.chapters.first { $0.id == parentID }.map {
                    "\($0.source_title)（\($0.page_start)–\($0.page_end) 页）"
                  } ?? "无（作为一级章节）"
                ).font(DS.font(20)).lineLimit(2)
                Spacer()
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 12))
              }.foregroundStyle(DS.text).frame(maxWidth: .infinity, alignment: .leading).padding(
                .horizontal, 12
              ).frame(minHeight: 54)
                .background(DS.surface, in: RoundedRectangle(cornerRadius: 12)).overlay(
                  RoundedRectangle(cornerRadius: 12).stroke(DS.border))
            }.accessibilityLabel("所属上级章节")
          }

          if let error {
            Label(error, systemImage: "exclamationmark.circle").font(DS.font(14)).foregroundStyle(
              DS.danger
            ).accessibilityIdentifier("chapter.validationError")
          }
          Text("修改先保存在当前确认页，点击“确认生成课程”后再统一写入课程。").font(DS.font(14)).lineSpacing(2.1)
            .foregroundStyle(DS.secondary).padding(.vertical, 14)
          BCButton("保存本章修改", icon: "Save") { save(chapter) }
            .accessibilityIdentifier("chapter.save")
          if confirmingDelete {
            VStack(alignment: .leading, spacing: 12) {
              Text("确定移除“\(chapter.source_title)”吗？").font(DS.font(16, weight: .bold))
              let descendants = CourseChapterValidation.descendants(
                of: chapter.id, in: store.chapters)
              Text(descendants.isEmpty ? "该章节将不会用于生成课程。" : "它下面的 \(descendants.count) 个子章节也会一起移除。")
                .font(DS.font(14)).foregroundStyle(DS.secondary)
              HStack {
                BCButton("取消", style: .secondary) { confirmingDelete = false }
                BCButton("确认移除", style: .danger) {
                  store.deleteChapter(chapter.id)
                  store.closeSheet()
                }
              }
            }.padding(14).background(
              DS.danger.opacity(0.05), in: RoundedRectangle(cornerRadius: 14))
          } else {
            BCButton("移除此章节", icon: "trash", style: .danger) { confirmingDelete = true }
          }
          BCButton("返回目录", style: .text) { store.closeSheet() }
        } else {
          Text("章节已不存在，请返回目录重新选择。").font(DS.font(14)).foregroundStyle(DS.secondary)
          BCButton("返回目录", style: .secondary) { store.closeSheet() }
        }
      }.padding(.top, 15).padding(.bottom, 12)
    }.scrollDismissesKeyboard(.interactively).toolbar {
      ToolbarItemGroup(placement: .keyboard) {
        Spacer()
        Button("完成") { focusedField = nil }
          .accessibilityIdentifier("chapter.keyboardDone")
      }
    }.onAppear(perform: load).onChange(
      of: store.selectedChapterID
    ) { _, _ in load() }
  }
  private func field(_ label: String, text: Binding<String>, numeric: Bool = false) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(label).font(DS.font(20, weight: .bold)).frame(height: 28, alignment: .top)
      TextField(label, text: text).font(DS.font(20)).padding(.horizontal, 12)
        .frame(height: 54).background(.white, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(hex: 0xD7E3F8)))
        .keyboardType(numeric ? .numberPad : .default)
        .focused($focusedField, equals: label).submitLabel(.done)
        .onSubmit { focusedField = nil }.onChange(
          of: text.wrappedValue
        ) { _, _ in error = nil }
    }
  }
  private func load() {
    guard let chapter else { return }
    sourceTitle = chapter.source_title
    aiTitle = chapter.ai_title
    start = String(chapter.page_start)
    end = String(chapter.page_end)
    parentID = chapter.parent_id ?? ""
    confirmingDelete = false
    error = nil
  }
  private func save(_ chapter: Chapter) {
    guard let pageStart = Int(start), let pageEnd = Int(end) else {
      error = "页码必须是整数"
      return
    }
    var candidate = chapter
    candidate.source_title = sourceTitle.trimmingCharacters(in: .whitespacesAndNewlines)
    candidate.ai_title = aiTitle.trimmingCharacters(in: .whitespacesAndNewlines)
    candidate.page_start = pageStart
    candidate.page_end = pageEnd
    candidate.parent_id = parentID.isEmpty ? nil : parentID
    if candidate.parent_id != chapter.parent_id {
      candidate.level = (store.chapters.first { $0.id == parentID }?.level ?? 0) + 1
    }
    if let issue = CourseChapterValidation.validate(
      candidate, in: store.chapters, pageCount: store.currentSource?.summary.page_count)
    {
      error = issue
      return
    }
    candidate.status = "已人工校对"
    store.updateChapter(candidate)
    store.closeSheet()
  }
}

/// Mirrors source chapterStructure.ts and is shared by the editor and its tests.
enum CourseChapterValidation {
  static func descendants(of rootID: String, in chapters: [Chapter]) -> Set<String> {
    var result = Set<String>()
    var found = true
    while found {
      found = false
      for chapter in chapters where chapter.id != rootID && !result.contains(chapter.id) {
        if let parent = chapter.parent_id, parent == rootID || result.contains(parent) {
          result.insert(chapter.id)
          found = true
        }
      }
    }
    return result
  }
  static func validate(_ candidate: Chapter, in chapters: [Chapter], pageCount: Int?) -> String? {
    if candidate.source_title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      return "章节名称不能为空"
    }
    if candidate.ai_title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      return "课程章节名称不能为空"
    }
    if candidate.page_start < 1 { return "起始页必须大于等于 1" }
    if candidate.page_end < candidate.page_start { return "结束页不能小于起始页" }
    if let count = pageCount, count > 0, candidate.page_end > count {
      return "页码不能超过文档总页数 \(count)"
    }
    if let parentID = candidate.parent_id {
      if parentID == candidate.id || descendants(of: candidate.id, in: chapters).contains(parentID)
      {
        return "不能将自己或子章节设为上级章节"
      }
      guard let parent = chapters.first(where: { $0.id == parentID }) else { return "所选上级章节不存在" }
      if candidate.page_start < parent.page_start || candidate.page_end > parent.page_end {
        return "页码必须位于上级章节的 \(parent.page_start)–\(parent.page_end) 页内"
      }
    }
    if let child = chapters.first(where: {
      $0.parent_id == candidate.id
        && ($0.page_start < candidate.page_start || $0.page_end > candidate.page_end)
    }) {
      return "页码范围必须包含子章节“\(child.source_title)”"
    }
    if let sibling = chapters.first(where: {
      $0.id != candidate.id && $0.level == candidate.level && $0.parent_id == candidate.parent_id
        && $0.page_start > 0 && candidate.page_start <= $0.page_end
        && $0.page_start <= candidate.page_end
    }) {
      return "与同级章节“\(sibling.source_title)”的页码重叠"
    }
    return nil
  }
}
