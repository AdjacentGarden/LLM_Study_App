import { describe, expect, it } from "vitest";
import { demoShelfBooks } from "./demoShelfBooks";
import {
  demoShelfStudyPreviewFor,
  demoShelfStudyPreviews
} from "./demoShelfStudyPreviews";

describe("demo shelf study previews", () => {
  it("fills every display-only shelf book with subject-specific next-step tools", () => {
    const realDemoCourseBookIds = new Set([
      "book_biology_2",
      "catalog_high_school_math_required_2"
    ]);
    const displayOnlyBooks = demoShelfBooks.filter((book) => !realDemoCourseBookIds.has(book.bookId));

    expect(demoShelfStudyPreviews).toHaveLength(displayOnlyBooks.length);
    expect(new Set(demoShelfStudyPreviews.map((preview) => preview.bookId)).size)
      .toBe(displayOnlyBooks.length);

    displayOnlyBooks.forEach((book) => {
      const preview = demoShelfStudyPreviewFor(book.bookId);
      expect(preview?.chapterTitle).toBeTruthy();
      expect(preview?.toolPreview.assignmentPrompt).toBeTruthy();
      expect(preview?.toolPreview.flashcardTitle).toBeTruthy();
      expect(preview?.toolPreview.mistakeItems).toHaveLength(2);
    });
  });

  it("does not replace the biology course's generated learning context", () => {
    expect(demoShelfStudyPreviewFor("book_biology_2")).toBeNull();
    expect(demoShelfStudyPreviewFor("catalog_high_school_math_required_2")).toBeNull();
    expect(demoShelfStudyPreviewFor("missing-book")).toBeNull();
  });
});
