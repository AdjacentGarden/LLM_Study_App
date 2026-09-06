import { describe, expect, it } from "vitest";
import {
  clampFlashcardDrag,
  isHorizontalFlashcardGesture,
  shouldAdvanceFlashcardSwipe
} from "./flashcardGestures";

describe("flashcard swipe gestures", () => {
  it("advances only for a deliberate left swipe when another card exists", () => {
    expect(shouldAdvanceFlashcardSwipe(-72, 5, 6)).toBe(true);
    expect(shouldAdvanceFlashcardSwipe(-72, 5, 1)).toBe(false);
  });

  it("does not treat right swipes or vertical scrolling as next-card gestures", () => {
    expect(shouldAdvanceFlashcardSwipe(72, 4, 6)).toBe(false);
    expect(shouldAdvanceFlashcardSwipe(-72, 68, 6)).toBe(false);
    expect(isHorizontalFlashcardGesture(12, 30)).toBe(false);
  });

  it("keeps left and right dragging within the visual range", () => {
    expect(clampFlashcardDrag(-48)).toBe(-48);
    expect(clampFlashcardDrag(-180)).toBe(-148);
    expect(clampFlashcardDrag(40)).toBe(40);
    expect(clampFlashcardDrag(180)).toBe(148);
  });
});
