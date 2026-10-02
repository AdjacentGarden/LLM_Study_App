import { describe, expect, it } from "vitest";
import type { LessonCitation } from "../types/api";
import {
  collectLessonEvidenceSources,
  detailedCitationPageLabel,
  learnerCitationPageLabel
} from "./lessonEvidence";

const textbookCitation: LessonCitation = {
  chunk_id: "chunk_c2s1_18",
  page_start: 13,
  page_end: 13,
  printed_page_start: 18,
  printed_page_end: 18,
  quote: "同源染色体在减数第一次分裂后期分离。"
};

describe("lesson evidence sources", () => {
  it("deduplicates repeated citations by a stable source key while retaining learner-facing titles", () => {
    const sources = collectLessonEvidenceSources([
      { title: "同源染色体先分离", citations: [textbookCitation] },
      { title: "为什么不是姐妹染色单体", citations: [{ ...textbookCitation, quote: "第二次分裂才分离。" }] },
      {
        title: "配子染色体数减半",
        citations: [{
          chunk_id: "chunk_c2s1_19",
          page_start: 14,
          page_end: 15
        }]
      }
    ]);

    expect(sources).toHaveLength(2);
    expect(sources[0]?.blockTitles).toEqual(["同源染色体先分离", "为什么不是姐妹染色单体"]);
    expect(sources[1]?.blockTitles).toEqual(["配子染色体数减半"]);
  });

  it("uses a concise textbook-page label and exposes the PDF locator only when pages differ", () => {
    expect(learnerCitationPageLabel(textbookCitation)).toBe("教材第 18 页");
    expect(detailedCitationPageLabel(textbookCitation)).toBe("教材第 18 页 · PDF 第 13 页");
    expect(learnerCitationPageLabel(textbookCitation)).not.toContain("chunk_c2s1_18");
  });
});
