import SwiftUI
import UIKit

struct LearningMistakesView: View {
  @Environment(AppStore.self) private var store
  @Environment(\.bookCourseReduceMotion) private var reduceMotion
  @State private var search = ""
  @State private var filter = "全部"
  @State private var selectedID: String?
  @State private var reviewing = false
  @State private var answer = ""
  @State private var revealed = false
  @State private var error: String?
  @State private var selectedReason: String?
  private let reasons = ["知识盲区", "审题疏忽", "概念混淆", "方法不熟"]

  private var mistakes: [MistakeRecord] {
    store.mistakes.filter { $0.book_id == store.selectedBookID && ($0.course_id == nil || $0.course_id == store.selectedCourseID) }
  }
  private var visible: [MistakeRecord] {
    mistakes.filter { mistake in
      let knowledge = mistake.knowledge_points.joined(separator: " ")
      let terms =
        filter == "减数分裂"
        ? ["meiosis", "减数分裂", "同源染色体", "姐妹染色单体"] : ["inheritance", "genetics", "遗传规律", "遗传"]
      let matchesFilter =
        filter == "全部" || terms.contains { knowledge.localizedCaseInsensitiveContains($0) }
      let full =
        ([mistake.question, mistake.answer, mistake.stuck_point] + mistake.knowledge_points).joined(
          separator: " ")
      return matchesFilter
        && (search.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
          || full.localizedCaseInsensitiveContains(
            search.trimmingCharacters(in: .whitespacesAndNewlines)))
    }
  }
  private var selected: MistakeRecord? { visible.first { $0.id == selectedID } ?? visible.first }
  private var mastered: Int { mistakes.filter { mastery($0) == "mastered" }.count }
  private var due: Int { mistakes.count - mastered }

  var body: some View {
    Group {
      if reviewing, let selected { review(selected) } else { overview }
    }
    .onChange(of: store.selectedBookID) { _, _ in
      selectedID = nil
      reviewing = false
      resetReview()
    }
  }

  private var overview: some View {
    LearningScroll(width: 1100) {
      if !mistakes.isEmpty {
        summary
        searchAndFilters
        if let selected {
          ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 20) {
              mistakeList.frame(width: 300)
              detail(selected).frame(width: 570)
            }
            VStack(spacing: 20) {
              mistakeList
              detail(selected)
            }
          }
        } else {
          BCCard {
            VStack(alignment: .leading, spacing: 16) {
              StickerIcon("Search", size: 38)
              Text("当前分类暂无错题").font(DS.font(21, weight: .bold))
              Text(search.isEmpty ? "可以切换分类查看其他错题。" : "没有找到与“\(search)”相关的错题，可以换个关键词。")
                .font(DS.font(14)).foregroundStyle(DS.secondary)
              BCButton("查看全部错题", style: .secondary) {
                search = ""
                filter = "全部"
              }
            }
          }
        }
      } else {
        EmptyStateView(title: "还没有可以复习的错题", message: "完成一次作业诊断，系统会把题目、真实卡点和对应原文整理到这里。")
        BCButton("去做一次诊断", icon: "checkmark.square") { store.go(.assignment) }
        BCButton("返回课程目录", style: .secondary) { store.go(.study) }
      }
    }
  }

  private var summary: some View {
    VStack(alignment: .leading, spacing: store.isPad ? 16 : 10) {
      HStack(alignment: .center, spacing: 10) {
        StickerIcon("CalendarClock", size: 42, glyphSize: 22)
        VStack(alignment: .leading, spacing: 4) {
          Text("今日待复习").font(DS.font(20)).foregroundStyle(DS.secondary)
          HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text("\(due)").font(DS.font(16, weight: .bold))
            Text("道").font(DS.font(14, weight: .bold)).foregroundStyle(DS.secondary)
          }
        }
        Spacer(minLength: 0)
        if !store.isPad { reviewStartButton }
      }
      if store.isPad { reviewStartButton }
      HStack(spacing: 1) {
        metric("\(mistakes.count)", "全部错题")
        metric(
          "\(mistakes.filter { ($0.error_count ?? 1) > 1 || $0.mastery == "repeated" }.count)",
          "反复出错")
        metric("\(mastered)", "已经掌握")
        metric("\(Int(Double(mastered) / Double(max(1, mistakes.count)) * 100))%", "掌握进度")
      }.background(DS.border).clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(DS.border, lineWidth: 1))
      BCProgress(value: Double(mastered) / Double(max(1, mistakes.count)))
    }.padding(store.isPad ? 16 : 12)
      .background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
      .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
  }

  private var reviewStartButton: some View {
    Button {
      startReview()
    } label: {
      Text(due > 0 ? "开始今日复习" : "再复习一轮").font(DS.font(20, weight: .bold))
        .foregroundStyle(.white).padding(.horizontal, 20).frame(minHeight: 54)
        .background(DS.primary, in: Capsule())
    }.buttonStyle(.plain)
  }

  private func metric(_ value: String, _ label: String) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(value).font(DS.font(16, weight: .bold))
      Text(label).font(DS.font(14, weight: .semibold)).foregroundStyle(DS.secondary)
    }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 10).padding(.vertical, 9)
      .background(Color(hex: 0xf3f6fb))
  }

  private var searchAndFilters: some View {
    ViewThatFits(in: .horizontal) {
      HStack(spacing: 10) {
        searchField.frame(minWidth: 240, maxWidth: .infinity)
        filterButtons.frame(width: 410)
      }
      VStack(alignment: .leading, spacing: 10) {
        searchField
        filterButtons
      }
    }
  }

  private var searchField: some View {
    HStack(spacing: 10) {
      Image(systemName: "magnifyingglass").foregroundStyle(DS.secondary)
      TextField("搜索题目或知识点", text: $search, prompt: Text("搜索题目或知识点").foregroundStyle(DS.secondary))
        .font(DS.font(20)).textInputAutocapitalization(.never)
      if !search.isEmpty {
        Button {
          search = ""
        } label: {
          Image(systemName: "xmark.circle.fill").foregroundStyle(DS.secondary)
        }.accessibilityLabel("清除错题搜索")
      }
    }.padding(.horizontal, 12).padding(.vertical, 10).background(
      DS.surface, in: RoundedRectangle(cornerRadius: 13)
    )
    .overlay(RoundedRectangle(cornerRadius: 13).stroke(DS.border, lineWidth: 1))
  }

  private var filterButtons: some View {
    HStack(spacing: 4) {
      ForEach(["全部", "减数分裂", "遗传规律"], id: \.self) { item in
        Button {
          withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { filter = item }
        } label: {
          Text(item).font(DS.font(20)).foregroundStyle(filter == item ? DS.primary : DS.text)
            .frame(maxWidth: .infinity, minHeight: 38)
            .background(
              filter == item ? DS.primarySoft : Color(hex: 0xf3f6fb),
              in: RoundedRectangle(cornerRadius: 12)
            )
            .overlay(
              RoundedRectangle(cornerRadius: 12).stroke(
                filter == item ? DS.primary : DS.border, lineWidth: 1))
        }.buttonStyle(.plain).accessibilityAddTraits(filter == item ? .isSelected : [])
      }
    }.padding(4).background(DS.surface, in: RoundedRectangle(cornerRadius: 14)).overlay(
      RoundedRectangle(cornerRadius: 14).stroke(DS.border, lineWidth: 1))
  }

  private var mistakeList: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack {
        Text("错题记录").font(DS.font(20.8, weight: .bold))
        Spacer()
        Text("\(visible.count) 道").font(DS.font(20)).foregroundStyle(DS.secondary)
      }
      ForEach(visible) { mistake in
        let isSelected = selected?.id == mistake.id
        Button {
          withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { selectedID = mistake.id }
        } label: {
          VStack(alignment: .leading, spacing: 13) {
            HStack {
              Text(subject(mistake)).font(DS.font(20, weight: .bold)).foregroundStyle(DS.secondary)
              Spacer()
              Text(masteryLabel(mastery(mistake))).font(DS.font(20, weight: .bold)).foregroundStyle(
                DS.secondary
              ).padding(.horizontal, 8).padding(.vertical, 4).background(
                Color(hex: 0xfff7e8), in: Capsule())
            }
            Text(mistake.question).font(DS.font(16, weight: .bold)).lineSpacing(4)
              .multilineTextAlignment(.leading).lineLimit(3)
            Text(mistake.knowledge_points.first ?? "待复习").font(DS.font(20, weight: .bold))
              .foregroundStyle(DS.secondary)
              .padding(.horizontal, 6).padding(.vertical, 2).background(
                Color(hex: 0xf3f6fb), in: Capsule())
            HStack {
              Label("最近记录", systemImage: "clock")
              Spacer()
              Text("错 \(max(1, mistake.error_count ?? 1)) 次")
            }.font(DS.font(20)).foregroundStyle(DS.secondary)
          }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
            .background(isSelected ? DS.primarySoft : DS.surface)
        }.buttonStyle(.plain).accessibilityIdentifier("mistake.select.\(mistake.id)")
          .accessibilityAddTraits(isSelected ? .isSelected : [])
      }
    }
  }

  private func detail(_ mistake: MistakeRecord) -> some View {
    VStack(alignment: .leading, spacing: 0) {
      VStack(alignment: .leading, spacing: 12) {
        HStack {
          Text(subject(mistake)).font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary)
            .padding(.horizontal, 9).padding(.vertical, 6).background(DS.primarySoft, in: Capsule())
          Spacer()
          Text("错 \(max(1, mistake.error_count ?? 1)) 次").font(DS.font(20, weight: .bold))
            .foregroundStyle(DS.danger).padding(.horizontal, 8).padding(.vertical, 6).background(
              Color(hex: 0xffeff2), in: Capsule())
        }
        Text(mistake.question).font(DS.font(20.8, weight: .bold)).lineSpacing(1)
        LearningFlowLayout {
          ForEach(mistake.knowledge_points, id: \.self) { point in
            Text(point).font(DS.font(20, weight: .bold)).foregroundStyle(DS.primary).padding(
              .horizontal, 10
            ).padding(.vertical, 6)
              .background(DS.primarySoft, in: Capsule()).overlay(
                Capsule().stroke(DS.primary.opacity(0.16), lineWidth: 1))
          }
        }
      }.padding(18)
      Divider().overlay(DS.border)
      VStack(alignment: .leading, spacing: 12) {
        detailBlock(
          "上次作答", text: mistake.answer.isEmpty ? "未记录答案" : mistake.answer, icon: "RotateCcw"
        )
        .padding(13).background(Color(hex: 0xf3f6fb), in: RoundedRectangle(cornerRadius: 12))
        detailBlock("纠错重点", text: mistake.stuck_point, icon: "Target")
          .padding(13).background(Color(hex: 0xfff0e2), in: RoundedRectangle(cornerRadius: 12))
        Button {
          openSource(mistake)
        } label: {
          HStack(alignment: .center, spacing: 10) {
            BCGlyph("BookOpenCheck", color: DS.primary).frame(width: 19, height: 19).frame(
              width: 28)
            VStack(alignment: .leading, spacing: 4) {
              Text("已连接教材原文").font(DS.font(16, weight: .bold))
              Text("\(mistake.citation_ids.count) 条引用来源已记录，可回到章节上下文核对。")
                .font(DS.font(20)).foregroundStyle(DS.secondary).multilineTextAlignment(.leading)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(DS.font(13)).foregroundStyle(DS.primary).frame(
              width: 36, height: 36
            ).background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 10))
          }.padding(.vertical, 12).padding(.horizontal, 13)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(DS.border, lineWidth: 1))
        }.buttonStyle(.plain)
        HStack(spacing: 10) {
          BCButton("重做此题", icon: "arrow.counterclockwise") { startReview(mistake.id) }
            .accessibilityIdentifier("mistake.redo.\(mistake.id)")
          BCButton("查看原文", style: .secondary) { openSource(mistake) }
        }
        BCButton("用闪卡巩固", style: .secondary) { store.go(.flashcards) }
      }.padding(.horizontal, 18).padding(.top, 16).padding(.bottom, 18)
    }.frame(maxWidth: .infinity, alignment: .leading)
      .background(DS.surface, in: RoundedRectangle(cornerRadius: 16))
      .overlay(RoundedRectangle(cornerRadius: 16).stroke(DS.border, lineWidth: 1))
  }

  private func detailBlock(_ title: String, text: String, icon: String) -> some View {
    HStack(alignment: .top, spacing: 10) {
      StickerIcon(icon, size: 34, glyphSize: 19)
      VStack(alignment: .leading, spacing: 4) {
        Text(title).font(DS.font(16, weight: .bold))
        Text(text).font(DS.font(20)).foregroundStyle(DS.secondary).lineSpacing(4).textSelection(
          .enabled)
      }
    }.frame(maxWidth: .infinity, alignment: .leading)
  }

  private func review(_ mistake: MistakeRecord) -> some View {
    LearningScroll(width: 880) {
      HStack(spacing: 12) {
        Button {
          reviewing = false
          resetReview()
        } label: {
          Image(systemName: "chevron.left").frame(width: 34, height: 44)
        }.buttonStyle(.plain).accessibilityLabel("返回错题记录")
        VStack(alignment: .leading, spacing: 4) {
          Text("错题复习").font(DS.font(18, weight: .bold))
          Text("先独立回忆，再对照纠错").font(DS.font(14)).foregroundStyle(DS.secondary)
        }
        Spacer()
        Text("\((visible.firstIndex { $0.id == mistake.id } ?? 0) + 1) / \(visible.count)").font(
          DS.font(12, weight: .semibold)
        ).foregroundStyle(DS.secondary)
      }
      BCProgress(
        value: Double((visible.firstIndex { $0.id == mistake.id } ?? 0) + 1)
          / Double(max(1, visible.count)))
      BCCard {
        VStack(alignment: .leading, spacing: 20) {
          HStack {
            BCPill("\(subject(mistake)) · \(mistake.knowledge_points.first ?? "待复习")")
            Spacer()
            Text("错 \(max(1, mistake.error_count ?? 1)) 次").font(DS.font(14)).foregroundStyle(
              DS.orange)
          }
          Text(mistake.question).font(DS.font(20.8, weight: .bold)).lineSpacing(6)
          HStack {
            Text("写下你的答案").font(DS.font(14, weight: .bold))
            Spacer()
            Button("完成输入") {
              UIApplication.shared.sendAction(
                #selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
            }.font(DS.font(14, weight: .bold)).foregroundStyle(DS.primary).buttonStyle(.plain)
              .accessibilityIdentifier("learning.keyboard.done")
            Button("填入上次答案") {
              answer = mistake.answer
              error = nil
            }.font(DS.font(11, weight: .semibold)).foregroundStyle(DS.primary).buttonStyle(.plain)
          }
          BCTextEditor(text: $answer, placeholder: "先独立回忆，不急着看解析").frame(minHeight: 180)
            .accessibilityIdentifier("mistake.answer")
          if let error { Text(error).font(DS.font(14)).foregroundStyle(DS.danger) }
          if !revealed {
            BCButton("提交并对照") {
              guard !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                error = "请先写下你的答案，再对照解析。"
                return
              }
              withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { revealed = true }
              error = nil
            }.accessibilityIdentifier("mistake.compare")
          }
        }
      }
      if revealed {
        BCCard {
          VStack(alignment: .leading, spacing: 24) {
            detailBlock("我的新答案", text: answer, icon: "PenLine")
            detailBlock(
              "上次答案", text: mistake.answer.isEmpty ? "未记录" : mistake.answer, icon: "RotateCcw")
            Divider().overlay(DS.border)
            Text("这次为什么会错？").font(DS.font(17, weight: .bold))
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
              ForEach(reasons, id: \.self) { reason in
                Button {
                  selectedReason = reason
                } label: {
                  HStack(spacing: 6) {
                    if selectedReason == reason { Image(systemName: "checkmark") }
                    Text(reason)
                  }
                  .font(DS.font(12, weight: .semibold)).frame(maxWidth: .infinity, minHeight: 42)
                  .foregroundStyle(selectedReason == reason ? DS.primary : DS.secondary)
                  .background(
                    selectedReason == reason ? DS.primarySoft : DS.background, in: Capsule()
                  )
                  .overlay(
                    Capsule().stroke(
                      selectedReason == reason ? DS.primary : DS.border, lineWidth: 1))
                }.buttonStyle(.plain).accessibilityAddTraits(
                  selectedReason == reason ? .isSelected : [])
              }
            }
            detailBlock(
              "核心纠错点", text: mistake.explanation ?? mistake.stuck_point, icon: "BrainCircuit")
            if let reference = mistake.correct_answer {
              Text("参考答案：\(reference)").font(DS.font(14)).lineSpacing(6)
            }
            BCButton("回到教材原文", icon: "book", style: .text) { openSource(mistake) }
            Text("现在掌握了吗？").font(DS.font(17, weight: .bold))
            VStack(spacing: 10) {
              masteryButton(
                "还不会", helper: "保留在今日复习", icon: "arrow.counterclockwise", value: "due",
                color: DS.danger, mistake: mistake)
              masteryButton(
                "有点模糊", helper: "稍后再巩固一次", icon: "clock", value: "learning", color: DS.orange,
                mistake: mistake)
              masteryButton(
                "已掌握", helper: "移出今日复习", icon: "checkmark.circle", value: "mastered",
                color: DS.mint, mistake: mistake)
            }
          }
        }
        HStack(spacing: 12) {
          BCButton("上一题", icon: "chevron.left", style: .secondary) { moveReview(-1) }
          BCButton("下一题", icon: "chevron.right", style: .secondary) { moveReview(1) }
        }
        BCButton("完成本轮复习", icon: "checkmark") {
          reviewing = false
          resetReview()
          store.showToast("已保存本轮复习状态")
        }.accessibilityIdentifier("mistake.finish")
      }
    }
  }

  private func masteryButton(
    _ title: String, helper: String, icon: String, value: String, color: Color,
    mistake: MistakeRecord
  ) -> some View {
    let active = mastery(mistake) == value
    return Button {
      markMastery(value, mistake: mistake)
    } label: {
      HStack(spacing: 14) {
        Image(systemName: icon).font(DS.font(20, weight: .medium))
        VStack(alignment: .leading, spacing: 4) {
          Text(title).font(DS.font(14, weight: .bold))
          Text(helper).font(DS.font(14)).foregroundStyle(DS.secondary)
        }
        Spacer()
        if active { Image(systemName: "checkmark").font(DS.font(15, weight: .bold)) }
      }.foregroundStyle(color).padding(15)
        .background(
          active ? color.opacity(0.08) : DS.surface, in: RoundedRectangle(cornerRadius: 13)
        )
        .overlay(
          RoundedRectangle(cornerRadius: 13).stroke(active ? color : DS.border, lineWidth: 1))
    }.buttonStyle(.plain).accessibilityIdentifier("mistake.mastery.\(value)")
      .accessibilityValue(active ? "selected" : "unselected")
      .accessibilityAddTraits(active ? .isSelected : [])
  }

  private func mastery(_ mistake: MistakeRecord) -> String {
    if store.isMastered(mistake.id) { return "mastered" }
    return mistake.mastery ?? ((mistake.error_count ?? 1) >= 3 ? "repeated" : "due")
  }
  private func masteryLabel(_ value: String) -> String {
    switch value {
    case "mastered": "已掌握"
    case "learning": "巩固中"
    case "repeated": "反复出错"
    default: "待复习"
    }
  }
  private func subject(_ mistake: MistakeRecord) -> String {
    let text = (mistake.question + mistake.knowledge_points.joined()).lowercased()
    if ["biology", "meiosis", "genetic", "染色体", "遗传", "细胞", "生物"].contains(where: text.contains) {
      return "生物"
    }
    if ["physics", "force", "受力", "物理"].contains(where: text.contains) { return "物理" }
    if ["math", "函数", "方程", "数学"].contains(where: text.contains) { return "数学" }
    if ["chem", "化学", "反应"].contains(where: text.contains) { return "化学" }
    if ["english", "grammar", "语法", "英语"].contains(where: text.contains) { return "英语" }
    return "本课程"
  }
  private func startReview(_ id: String? = nil) {
    selectedID =
      id ?? mistakes.first(where: { mastery($0) != "mastered" })?.id ?? mistakes.first?.id
    search = ""
    filter = "全部"
    resetReview()
    reviewing = true
  }
  private func resetReview() {
    answer = ""
    revealed = false
    error = nil
    selectedReason = nil
  }
  private func moveReview(_ delta: Int) {
    guard let selected, let index = visible.firstIndex(where: { $0.id == selected.id }) else {
      return
    }
    let next = min(max(0, index + delta), visible.count - 1)
    guard next != index else { return }
    selectedID = visible[next].id
    resetReview()
  }
  private func markMastery(_ value: String, mistake: MistakeRecord) {
    store.setMastery(mistake.id, mastered: value == "mastered")
    if let index = store.mistakes.firstIndex(where: {
      $0.id == mistake.id && $0.book_id == mistake.book_id
    }) {
      store.mistakes[index].mastery = value
      if let selectedReason {
        let map = [
          "知识盲区": "knowledge_gap", "审题疏忽": "misread", "概念混淆": "knowledge_gap",
          "方法不熟": "method_unfamiliar",
        ]
        store.mistakes[index].mistake_reason = map[selectedReason]
      }
      store.persist()
    }
    store.showToast(
      value == "mastered" ? "已移出今日复习" : value == "learning" ? "已记录，稍后再巩固一次" : "已保留在今日复习")
  }
  private func openSource(_ mistake: MistakeRecord) {
    if let chunk = store.currentSource?.chunks.first(where: { mistake.citation_ids.contains($0.id) }
    ) {
      store.openSource(
        SourcePageTarget(
          bookID: mistake.book_id, title: mistake.knowledge_points.first ?? "错题来源",
          page: chunk.page_start, pageEnd: chunk.page_end, printedPage: chunk.printed_page_start,
          sourceText: chunk.text, from: .mistakes))
    } else {
      store.go(.lesson)
    }
  }
}
