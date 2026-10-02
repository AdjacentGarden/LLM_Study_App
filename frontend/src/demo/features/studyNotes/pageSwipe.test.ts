import { describe, expect, it } from "vitest";
import { pageSwipeDirection } from "./pageSwipe";

describe("pageSwipeDirection", () => {
  it("turns left and right gestures into next and previous pages", () => {
    expect(pageSwipeDirection(-120, 12, 834)).toBe(1);
    expect(pageSwipeDirection(120, -12, 834)).toBe(-1);
  });

  it("ignores short and mostly vertical gestures", () => {
    expect(pageSwipeDirection(-40, 0, 834)).toBeNull();
    expect(pageSwipeDirection(-110, 120, 834)).toBeNull();
    expect(pageSwipeDirection(-55, 1, 320)).toBe(1);
  });
});
