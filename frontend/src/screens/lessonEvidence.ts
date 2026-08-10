import type { LessonBlock, LessonCitation } from "../types/api";
import { sourcePageLabel } from "./shared";

export type LessonEvidenceSource = {
  citation: LessonCitation;
  key: string;
  blockTitles: string[];
};

export function citationSourceKey(citation: LessonCitation) {
  const chunkId = citation.chunk_id.trim();
  if (chunkId) return `chunk:${chunkId}`;
  return [
    "page",
    citation.page_start,
    citation.page_end,
    citation.printed_page_start ?? "",
    citation.printed_page_end ?? ""
  ].join(":");
}

export function learnerCitationPageLabel(citation: LessonCitation) {
  const printedStart = citation.printed_page_start;
  if (typeof printedStart === "number") {
    return `教材${sourcePageLabel(printedStart, citation.printed_page_end ?? printedStart)}`;
  }
  return `教材${sourcePageLabel(citation.page_start, citation.page_end)}`;
}

export function detailedCitationPageLabel(citation: LessonCitation) {
  const learnerLabel = learnerCitationPageLabel(citation);
  const printedStart = citation.printed_page_start;
  const printedEnd = citation.printed_page_end ?? printedStart;
  const needsPdfLocator = typeof printedStart === "number"
    && (printedStart !== citation.page_start || printedEnd !== citation.page_end);
  return needsPdfLocator ? `${learnerLabel} · PDF ${sourcePageLabel(citation.page_start, citation.page_end)}` : learnerLabel;
}

export function collectLessonEvidenceSources(
  blocks: ReadonlyArray<Pick<LessonBlock, "citations" | "title">>
): LessonEvidenceSource[] {
  const sourcesByKey = new Map<string, LessonEvidenceSource>();

  for (const block of blocks) {
    for (const citation of block.citations) {
      const key = citationSourceKey(citation);
      const existing = sourcesByKey.get(key);
      if (existing) {
        if (!existing.blockTitles.includes(block.title)) existing.blockTitles.push(block.title);
        continue;
      }
      sourcesByKey.set(key, {
        citation,
        key,
        blockTitles: [block.title]
      });
    }
  }

  return [...sourcesByKey.values()];
}
