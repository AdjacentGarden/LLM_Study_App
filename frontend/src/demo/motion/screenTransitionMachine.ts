import type { Screen } from "../types/app";

export type ScreenDirection = "forward" | "back" | "replace";
export type ScreenMotionState = "idle" | "transitioning";

export type ScreenSurface = {
  nonce: number;
  screen: Screen;
};

export type ScreenTransitionRequest = ScreenSurface & {
  direction: ScreenDirection;
  initial: boolean;
  reducedMotion: boolean;
};

export type ScreenTransitionSnapshot = {
  current: ScreenSurface;
  previous: ScreenSurface | null;
  direction: ScreenDirection;
  state: ScreenMotionState;
};

export function createScreenTransitionSnapshot(
  request: ScreenTransitionRequest
): ScreenTransitionSnapshot {
  return {
    current: { nonce: request.nonce, screen: request.screen },
    previous: null,
    direction: request.direction,
    state: "idle"
  };
}

export function reconcileScreenTransition(
  snapshot: ScreenTransitionSnapshot,
  request: ScreenTransitionRequest
): ScreenTransitionSnapshot {
  if (snapshot.current.nonce !== request.nonce) {
    const current = { nonce: request.nonce, screen: request.screen };
    if (request.initial || request.reducedMotion) {
      return {
        current,
        previous: null,
        direction: request.direction,
        state: "idle"
      };
    }

    return {
      current,
      // The surface that was current immediately before this request is the
      // only outgoing tree. Any older previous surface is intentionally
      // discarded when navigation advances again mid-transition.
      previous: snapshot.current,
      direction: request.direction,
      state: "transitioning"
    };
  }

  if (request.reducedMotion && snapshot.state === "transitioning") {
    return {
      ...snapshot,
      previous: null,
      state: "idle"
    };
  }

  return snapshot;
}

export function settleScreenTransition(
  snapshot: ScreenTransitionSnapshot,
  nonce: number
): ScreenTransitionSnapshot {
  if (snapshot.current.nonce !== nonce || snapshot.state !== "transitioning") {
    return snapshot;
  }

  return {
    ...snapshot,
    previous: null,
    state: "idle"
  };
}
