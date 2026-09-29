import type { Screen } from "../types/app";

export type ScreenDirection = "forward" | "back" | "replace";

export type NavigationSnapshot = {
  direction: ScreenDirection;
  history: Screen[];
  nonce: number;
  screen: Screen;
};

export type NavigationIntent =
  | { type: "go"; screen: Screen }
  | { type: "back" }
  | { type: "source" }
  | { type: "replace"; screen: Screen };

export function createInitialNavigation(): NavigationSnapshot {
  return {
    direction: "replace",
    history: [],
    nonce: 0,
    screen: "home"
  };
}

function withScreen(
  current: NavigationSnapshot,
  screen: Screen,
  direction: ScreenDirection,
  history: Screen[],
  forceEntry = false
): NavigationSnapshot {
  const screenChanged = forceEntry || screen !== current.screen;
  return {
    direction,
    history,
    nonce: screenChanged ? current.nonce + 1 : current.nonce,
    screen
  };
}

/**
 * Keeps page direction, screen and transition generation in one immutable
 * transaction while preserving the app's pre-motion history semantics.
 */
export function navigate(
  current: NavigationSnapshot,
  intent: NavigationIntent
): NavigationSnapshot {
  switch (intent.type) {
    case "go":
      return withScreen(
        current,
        intent.screen,
        intent.screen === current.screen ? "replace" : "forward",
        [...current.history, current.screen]
      );
    case "back": {
      if (current.history.length > 0) {
        return withScreen(
          current,
          current.history[current.history.length - 1],
          "back",
          current.history.slice(0, -1)
        );
      }
      return withScreen(
        current,
        "home",
        current.screen === "home" ? "replace" : "back",
        []
      );
    }
    case "source":
      return withScreen(current, "source", "forward", [...current.history, current.screen], true);
    case "replace":
      return withScreen(current, intent.screen, "replace", current.history);
  }
}
