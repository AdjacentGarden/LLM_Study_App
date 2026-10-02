import { describe, expect, it } from "vitest";
import {
  clampFlashcardDrag,
  isHorizontalFlashcardGesture,
  isLastFlashcard,
  nextFlashcardIndex,
  shouldAdvanceFlashcardSwipe
} from "./flashcardGestures";

describe("flashcard swipe gestures", () => {
  it("advances only for a deliberate left swipe when another card exists", () => {
    expect(shouldAdvanceFlashcardSwipe(-72, 5, 6)).toBe(true);
    expect(shouldAdvanceFlashcardSwipe(-72, 5, 1)).toBe(false);
    expect(shouldAdvanceFlashcardSwipe(-72, 5, 6, 5)).toBe(false);
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

  it("stops at the final card instead of wrapping to the beginning", () => {
    expect(isLastFlashcard(4, 5)).toBe(true);
    expect(isLastFlashcard(3, 5)).toBe(false);
    expect(nextFlashcardIndex(3, 5)).toBe(4);
    expect(nextFlashcardIndex(4, 5)).toBe(4);
  });
});
