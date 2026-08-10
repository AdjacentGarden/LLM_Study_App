import { describe, expect, it } from "vitest";
import {
  globalMotionDurationMs,
  globalMotionFallbackMs,
  localMotionFallbackMs,
  localMotionMaxMs,
  localSlowMotionDurationSeconds,
  localStateGsapEase
} from "./timing";

describe("motion timing contract", () => {
  it("locks global surfaces to 350ms with a 450ms Presence fallback", () => {
    expect(globalMotionDurationMs).toBe(350);
    expect(globalMotionFallbackMs).toBe(450);
    expect(globalMotionFallbackMs).toBeGreaterThan(globalMotionDurationMs);
  });

  it("locks local motion to a 200ms maximum with a 300ms Presence fallback", () => {
    expect(localMotionMaxMs).toBe(200);
    expect(localMotionFallbackMs).toBe(300);
    expect(localMotionFallbackMs).toBeGreaterThan(localMotionMaxMs);
  });

  it("locks the PrimaryNav GSAP selection to the Slow/State contract", () => {
    expect(localSlowMotionDurationSeconds).toBe(0.2);
    expect(localStateGsapEase).toBe("power2.inOut");
  });
});
