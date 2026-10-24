import Foundation

/// Metadata-only answers for the discovery catalog. This follows the source
/// communityAssistant.ts rule order and never borrows textbook/online evidence.
struct CommunityAssistantAnswer: Equatable, Sendable {
  let courseIds: [String]
  let text: String
}

private enum CommunityAssistantRules {
  struct Goal {
    let label: String
    let queries: [String]
    let terms: [String]
  }
  static let goals: [Goal] = [
    Goal(label: "实验", queries: ["实验"], terms: ["实验"]),
    Goal(label: "闪卡", queries: ["闪卡", "记忆卡"], terms: ["闪卡"]),
    Goal(label: "同步学习", queries: ["同步", "预习"], terms: ["同步", "教材"]),
    Goal(label: "复习", queries: ["复习", "考前"], terms: ["复习", "诊断", "专项", "错题"]),
    Goal(label: "专项提升", queries: ["提升", "专项", "强化"], terms: ["提升", "专项", "精讲", "诊断"]),
  ]
  static func normalize(_ value: String) -> String {
    value.lowercased(with: Locale(identifier: "zh_CN"))
      .replacingOccurrences(
        of: #"[\s“”‘’'"《》·•（）()，,。！？!?：:、—–-]"#, with: "", options: .regularExpression)
  }
  static func unique(_ values: [String]) -> [String] {
    var seen = Set<String>()
    return values.filter { seen.insert($0).inserted }
  }
  static func searchText(_ book: CommunityBook) -> String {
    ([
      book.catalogTitle, book.title, book.subject, book.grade, book.version, book.volume ?? "",
      book.description,
    ] + book.tags + book.chapters)
    .filter { !$0.isEmpty }.joined(separator: " ")
  }
  static func summarize(_ books: [CommunityBook], label: String) -> CommunityAssistantAnswer {
    CommunityAssistantAnswer(
      courseIds: books.map(\.id),
      text:
        "找到 \(books.count) 门\(label)课程：\(books.map(\.catalogTitle).joined(separator: "、"))。点击下方课程即可查看详情。"
    )
  }
  static func fallback(_ books: [CommunityBook]) -> CommunityAssistantAnswer {
    let recommended = Array(books.filter { $0.recommended == true }.prefix(4))
    let fallback = recommended.isEmpty ? Array(books.prefix(4)) : recommended
    return CommunityAssistantAnswer(
      courseIds: fallback.map(\.id), text: "我只根据发现页的课程目录回答。你可以输入课程名，也可以告诉我学科、年级或学习目标；先为你列出几门目录中的课程。")
  }
}

func answerCommunityCourseQuery(_ question: String, books: [CommunityBook])
  -> CommunityAssistantAnswer
{
  typealias Rules = CommunityAssistantRules
  let normalized = Rules.normalize(question)
  guard !normalized.isEmpty, !books.isEmpty else {
    return CommunityAssistantAnswer(courseIds: [], text: "发现页暂时没有可推荐的课程。")
  }
  let direct = books.filter {
    normalized.contains(Rules.normalize($0.catalogTitle))
      || normalized.contains(Rules.normalize($0.title))
  }
  if direct.count == 1, let book = direct.first {
    let volume = (book.volume?.isEmpty == false) ? " · \(book.volume!)" : ""
    let details = [book.grade, book.version].filter { !$0.isEmpty }.joined(separator: " · ")
    let metadata = details.isEmpty ? "" : "课程信息：\(details)\(volume)。"
    return CommunityAssistantAnswer(
      courseIds: [book.id],
      text: "“\(book.catalogTitle)”。\(metadata)\(book.description)"
    )
  }
  if direct.count > 1 { return Rules.summarize(direct, label: "相关") }
  if ["全部课程", "所有课程", "课程列表", "查看全部", "都有哪些课程"].contains(where: {
    normalized.contains(Rules.normalize($0))
  }) {
    return CommunityAssistantAnswer(
      courseIds: books.map(\.id), text: "发现页共有 \(books.count) 门课程，已全部列在下方。点击课程即可查看详情。")
  }
  let subjects = Rules.unique(books.map(\.subject)).filter {
    normalized.contains(Rules.normalize($0))
  }
  let grades = Rules.unique(books.map(\.grade)).filter { normalized.contains(Rules.normalize($0)) }
  let versions = Rules.unique(books.map(\.version)).filter {
    normalized.contains(Rules.normalize($0))
  }
  let goal = Rules.goals.first { $0.queries.contains { normalized.contains(Rules.normalize($0)) } }
  if !subjects.isEmpty || !grades.isEmpty || !versions.isEmpty || goal != nil {
    let matches = books.filter { book in
      let search = Rules.normalize(Rules.searchText(book))
      return (subjects.isEmpty || subjects.contains(book.subject))
        && (grades.isEmpty || grades.contains(book.grade))
        && (versions.isEmpty || versions.contains(book.version))
        && (goal == nil || goal!.terms.contains { search.contains(Rules.normalize($0)) })
    }
    if !matches.isEmpty {
      let labels = grades + subjects + versions + (goal.map { [$0.label] } ?? [])
      return Rules.summarize(matches, label: labels.joined(separator: " · "))
    }
    return CommunityAssistantAnswer(
      courseIds: Rules.fallback(books).courseIds,
      text: "发现页暂时没有完全符合这些条件的课程。你可以换一个学科、年级或学习目标；也可以先看看这些已发布课程。")
  }
  let stripped = question.replacingOccurrences(
    of: "请|帮我|推荐|介绍|一下|课程|想学|我要学|怎么样|如何|是什么|适合我吗|的", with: "", options: .regularExpression)
  let titleQuery = Rules.normalize(stripped)
  if titleQuery.utf16.count >= 2 {
    let matches = books.filter {
      Rules.normalize($0.catalogTitle).contains(titleQuery)
        || Rules.normalize($0.title).contains(titleQuery)
    }
    if matches.count == 1, let book = matches.first {
      return CommunityAssistantAnswer(
        courseIds: [book.id],
        text: "你说的应该是“\(book.catalogTitle)”。\(book.description) 点击下方课程可以查看完整介绍。")
    }
    if matches.count > 1 { return Rules.summarize(matches, label: "相关") }
  }
  return Rules.fallback(books)
}
