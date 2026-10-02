import { describe, expect, it } from "vitest";
import {
  getPublishedSourcePageImageUrl,
  isPublishedSourcePageImage
} from "./publishedSourcePages";

describe("published textbook source pages", () => {
  it.each([1, 11, 125])("resolves published biology PDF page %i", (page) => {
    const url = `/assets/textbook/pages/page_${String(page).padStart(3, "0")}.jpeg`;
    expect(getPublishedSourcePageImageUrl("book_biology_2", page)).toBe(url);
    expect(isPublishedSourcePageImage(url)).toBe(true);
  });

  it("never guesses an unpublished book or page URL", () => {
    expect(getPublishedSourcePageImageUrl("book_unknown", 11)).toBeUndefined();
    expect(getPublishedSourcePageImageUrl("book_biology_2", 0)).toBeUndefined();
    expect(getPublishedSourcePageImageUrl("book_biology_2", 126)).toBeUndefined();
    expect(isPublishedSourcePageImage("/assets/textbook/pages/page_126.jpeg")).toBe(false);
  });
});
