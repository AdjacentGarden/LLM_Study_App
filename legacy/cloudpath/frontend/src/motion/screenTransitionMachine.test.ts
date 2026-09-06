import { describe, expect, it } from "vitest";
import {
  createScreenTransitionSnapshot,
  reconcileScreenTransition,
  settleScreenTransition,
  type ScreenTransitionRequest
} from "./screenTransitionMachine";

const request = (
  nonce: number,
  screen: ScreenTransitionRequest["screen"],
  overrides: Partial<ScreenTransitionRequest> = {}
): ScreenTransitionRequest => ({
  nonce,
  screen,
  direction: "forward",
  initial: nonce === 0,
  reducedMotion: false,
  ...overrides
});

describe("screenTransitionMachine", () => {
  it("keeps the initial surface static", () => {
    expect(createScreenTransitionSnapshot(request(0, "home"))).toEqual({
      current: { nonce: 0, screen: "home" },
      previous: null,
      direction: "forward",
      state: "idle"
    });
  });

  it("keeps the latest current as previous and discards an older outgoing generation", () => {
    const initial = createScreenTransitionSnapshot(request(0, "home"));
    const upload = reconcileScreenTransition(initial, request(1, "upload"));
    expect(upload).toMatchObject({
      current: { nonce: 1, screen: "upload" },
      previous: { nonce: 0, screen: "home" },
      state: "transitioning"
    });

    const community = reconcileScreenTransition(upload, request(2, "community"));
    expect(community).toMatchObject({
      current: { nonce: 2, screen: "community" },
      previous: { nonce: 1, screen: "upload" },
      state: "transitioning"
    });
  });

  it("only settles the active current generation", () => {
    const active = reconcileScreenTransition(
      createScreenTransitionSnapshot(request(0, "home")),
      request(1, "upload")
    );
    expect(settleScreenTransition(active, 0)).toBe(active);
    expect(settleScreenTransition(active, 1)).toEqual({
      current: { nonce: 1, screen: "upload" },
      previous: null,
      direction: "forward",
      state: "idle"
    });
  });

  it("removes the outgoing surface immediately when reduced motion changes at runtime", () => {
    const active = reconcileScreenTransition(
      createScreenTransitionSnapshot(request(0, "home")),
      request(1, "upload")
    );
    expect(reconcileScreenTransition(active, request(1, "upload", { reducedMotion: true }))).toMatchObject({
      previous: null,
      state: "idle"
    });
  });
});
