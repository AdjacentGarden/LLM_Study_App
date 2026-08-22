import type { CommunityBookSummary } from "../types/api";

export type CommunityCategory = "推荐" | "生物" | "数学" | "物理" | "化学" | "历史" | "地理" | "语文" | "英语";
export type CommunityFilter = "全部" | CommunityCategory;

export const communityCategories: readonly CommunityCategory[] = [
  "推荐",
  "生物",
  "数学",
  "物理",
  "化学",
  "历史",
  "地理",
  "语文",
  "英语"
];

function normalizeQuery(value: string) {
  return value.trim().toLocaleLowerCase("zh-CN");
}

function communityBookSearchText(book: CommunityBookSummary) {
  return [
    book.title,
    book.catalog_title,
    book.author,
    book.subject,
    book.level,
    book.edition,
    ...book.tags
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("zh-CN");
}

export function filterCommunityBooks(
  books: readonly CommunityBookSummary[],
  category: CommunityFilter,
  query = ""
) {
  const normalizedQuery = normalizeQuery(query);

  return books.filter((book) => {
    if (category !== "推荐" && category !== "全部" && book.subject !== category) return false;
    return !normalizedQuery || communityBookSearchText(book).includes(normalizedQuery);
  });
}
