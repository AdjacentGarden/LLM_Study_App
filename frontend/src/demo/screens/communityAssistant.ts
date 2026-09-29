import { communityBooks, type CommunityBook } from "../data/mockBook";

export type CommunityAssistantAnswer = {
  courseIds: string[];
  text: string;
};

const goalRules = [
  { label: "实验", queries: ["实验"], terms: ["实验"] },
  { label: "闪卡", queries: ["闪卡", "记忆卡"], terms: ["闪卡"] },
  { label: "同步学习", queries: ["同步", "预习"], terms: ["同步", "教材"] },
  { label: "复习", queries: ["复习", "考前"], terms: ["复习", "诊断", "专项", "错题"] },
  { label: "专项提升", queries: ["提升", "专项", "强化"], terms: ["提升", "专项", "精讲", "诊断"] }
] as const;

function normalize(value: string) {
  return value
    .toLocaleLowerCase("zh-CN")
    .replace(/[\s“”‘’'"《》·•（）()，,。！？!?：:、—–-]/g, "");
}

function courseSearchText(book: CommunityBook) {
  return [
    book.catalogTitle,
    book.title,
    book.subject,
    book.grade,
    book.version,
    book.volume,
    book.description,
    ...book.tags,
    ...book.chapters
  ].filter(Boolean).join(" ");
}

function summarizeMatches(books: readonly CommunityBook[], label: string): CommunityAssistantAnswer {
  const names = books.map((book) => book.catalogTitle).join("、");
  return {
    courseIds: books.map((book) => book.id),
    text: `找到 ${books.length} 门${label}课程：${names}。点击下方课程即可查看详情。`
  };
}

function recommendFallback(books: readonly CommunityBook[]): CommunityAssistantAnswer {
  const recommended = books.filter((book) => book.recommended).slice(0, 4);
  const fallback = recommended.length > 0 ? recommended : books.slice(0, 4);
  return {
    courseIds: fallback.map((book) => book.id),
    text: "我只根据发现页的课程目录回答。你可以输入课程名，也可以告诉我学科、年级或学习目标；先为你列出几门热门课程。"
  };
}

export function answerCommunityCourseQuery(
  question: string,
  books: readonly CommunityBook[] = communityBooks
): CommunityAssistantAnswer {
  const normalizedQuestion = normalize(question);
  if (!normalizedQuestion || books.length === 0) {
    return { courseIds: [], text: "发现页暂时没有可推荐的课程。" };
  }

  const directMatches = books.filter((book) => {
    const catalogTitle = normalize(book.catalogTitle);
    const fullTitle = normalize(book.title);
    return normalizedQuestion.includes(catalogTitle) || normalizedQuestion.includes(fullTitle);
  });
  if (directMatches.length === 1) {
    const [book] = directMatches;
    const volume = book.volume ? ` · ${book.volume}` : "";
    return {
      courseIds: [book.id],
      text: `“${book.catalogTitle}”面向${book.grade}，版本为${book.version}${volume}。${book.description} 当前有 ${book.learners} 人学习。`
    };
  }
  if (directMatches.length > 1) return summarizeMatches(directMatches, "相关");

  const wantsAll = ["全部课程", "所有课程", "课程列表", "查看全部", "都有哪些课程"]
    .some((term) => normalizedQuestion.includes(normalize(term)));
  if (wantsAll) {
    return {
      courseIds: books.map((book) => book.id),
      text: `发现页共有 ${books.length} 门课程，已全部列在下方。点击课程即可查看详情。`
    };
  }

  const subjects = [...new Set(books.map((book) => book.subject))]
    .filter((subject) => normalizedQuestion.includes(normalize(subject)));
  const grades = [...new Set(books.map((book) => book.grade))]
    .filter((grade) => normalizedQuestion.includes(normalize(grade)));
  const versions = [...new Set(books.map((book) => book.version))]
    .filter((version) => normalizedQuestion.includes(normalize(version)));
  const goal = goalRules.find((rule) => rule.queries.some((query) => normalizedQuestion.includes(normalize(query))));
  const hasFilters = subjects.length > 0 || grades.length > 0 || versions.length > 0 || Boolean(goal);

  if (hasFilters) {
    const matches = books.filter((book) => {
      const searchText = normalize(courseSearchText(book));
      return (
        (subjects.length === 0 || subjects.includes(book.subject))
        && (grades.length === 0 || grades.includes(book.grade))
        && (versions.length === 0 || versions.includes(book.version))
        && (!goal || goal.terms.some((term) => searchText.includes(normalize(term))))
      );
    });
    if (matches.length > 0) {
      const label = [...grades, ...subjects, ...versions, ...(goal ? [goal.label] : [])].join(" · ");
      return summarizeMatches(matches, label);
    }
    return {
      ...recommendFallback(books),
      text: "发现页暂时没有完全符合这些条件的课程。你可以换一个学科、年级或学习目标；也可以先看看这些热门课程。"
    };
  }

  const titleQuery = normalize(question.replace(/请|帮我|推荐|介绍|一下|课程|想学|我要学|怎么样|如何|是什么|适合我吗|的/g, ""));
  if (titleQuery.length >= 2) {
    const fuzzyTitleMatches = books.filter((book) => (
      normalize(book.catalogTitle).includes(titleQuery)
      || normalize(book.title).includes(titleQuery)
    ));
    if (fuzzyTitleMatches.length === 1) {
      const [book] = fuzzyTitleMatches;
      return {
        courseIds: [book.id],
        text: `你说的应该是“${book.catalogTitle}”。${book.description} 点击下方课程可以查看完整介绍。`
      };
    }
    if (fuzzyTitleMatches.length > 1) return summarizeMatches(fuzzyTitleMatches, "相关");
  }

  return recommendFallback(books);
}
