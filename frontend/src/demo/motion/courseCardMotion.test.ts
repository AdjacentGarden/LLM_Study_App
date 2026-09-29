import { describe, expect, it } from "vitest";
import {
  clampCourseCardMotionIndex,
  courseCardStaggerStepMs,
  getCourseCardStaggerDelayMs,
  maxAnimatedCourseCards,
  maxCourseCardStaggerDelayMs,
  shouldAnimateCourseCard
} from "./courseCardMotion";

describe("course-card motion budget", () => {
  it("allows only the first five cards to animate", () => {
    expect(maxAnimatedCourseCards).toBe(5);
    expect([0, 1, 2, 3, 4].every(shouldAnimateCourseCard)).toBe(true);
    expect(shouldAnimateCourseCard(-1)).toBe(false);
    expect(shouldAnimateCourseCard(5)).toBe(false);
    expect(shouldAnimateCourseCard(99)).toBe(false);
  });

  it("clamps the 30ms stagger to a 120ms total budget", () => {
    expect(courseCardStaggerStepMs).toBe(30);
    expect(maxCourseCardStaggerDelayMs).toBe(120);
    expect(clampCourseCardMotionIndex(-1)).toBe(0);
    expect(clampCourseCardMotionIndex(4)).toBe(4);
    expect(clampCourseCardMotionIndex(5)).toBe(4);
    expect(getCourseCardStaggerDelayMs(0)).toBe(0);
    expect(getCourseCardStaggerDelayMs(4)).toBe(120);
    expect(getCourseCardStaggerDelayMs(99)).toBe(120);
  });
});
