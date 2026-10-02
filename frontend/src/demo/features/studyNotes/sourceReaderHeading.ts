import type { ApiChapter, ScanResult } from "../../types/api";
import type { SourcePageTarget } from "../../types/app";

export function resolveSourceReaderHeading(
  target: SourcePageTarget | null,
  page: number,
  chapters: ApiChapter[] | null,
  scan: ScanResult | null,
  fallbackTitle = "教材原文"
) {
  const matchingChapters = chapters
    ?.filter((item) => item.page_start <= page && page <= item.page_end);
  const chapterCandidates = matchingChapters?.some((item) => item.level <= 2)
    ? matchingChapters.filter((item) => item.level <= 2)
    : matchingChapters ?? [];
  const chapter = chapterCandidates
    .sort((left, right) => right.level - left.level || (left.page_end - left.page_start) - (right.page_end - right.page_start))[0];
  const location = scan?.source_locations?.find((item) => Number(item.index) === page);
  const printedPage = typeof location?.printed_page === "number" && Number.isFinite(location.printed_page)
    ? location.printed_page
    : page === target?.pageStart && typeof target.printedPageStart === "number"
      ? target.printedPageStart
      : typeof chapter?.printed_page_start === "number"
        ? chapter.printed_page_start + page - chapter.page_start
        : page;

  return {
    chapter,
    title: chapter?.source_title?.trim() || target?.title?.trim() || fallbackTitle,
    pageLabel: `第 ${printedPage} 页`,
    printedPage
  };
}
