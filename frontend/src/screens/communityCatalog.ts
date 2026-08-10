import {
  communityBooks,
  type CommunityBook,
  type CommunitySubject
} from "../data/mockBook";

export type CommunityCategory = "推荐" | CommunitySubject;
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

function communityBookSearchText(book: CommunityBook) {
  return [
    book.title,
    book.catalogTitle,
    book.subject,
    book.grade,
    book.version,
    book.volume,
    book.owner,
    ...book.tags
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("zh-CN");
}

export function filterCommunityBooks(
  books: readonly CommunityBook[],
  category: CommunityFilter,
  query = ""
) {
  const normalizedQuery = normalizeQuery(query);

  return books.filter((book) => {
    if (category === "推荐" && !book.recommended) return false;
    if (category !== "推荐" && category !== "全部" && book.subject !== category) return false;
    return !normalizedQuery || communityBookSearchText(book).includes(normalizedQuery);
  });
}

export function resolveCommunityBook(
  bookId: string | null | undefined,
  books: readonly CommunityBook[] = communityBooks
): CommunityBook {
  const resolved = books.find((book) => book.id === bookId) ?? books[0];
  if (!resolved) throw new Error("Community catalog must contain at least one book");
  return resolved;
}
