export type StateSwapMotionState = "idle" | "swapping";

export type StateSwapSnapshot = Readonly<{
  state: StateSwapMotionState;
  visibleValue: string;
  previousValue: string;
  targetValue: string;
}>;

export function createStateSwapSnapshot(value: string): StateSwapSnapshot {
  return {
    state: "idle",
    visibleValue: value,
    previousValue: value,
    targetValue: value
  };
}

export function updateStateSwap(
  snapshot: StateSwapSnapshot,
  nextValue: string,
  reducedMotion: boolean
): StateSwapSnapshot {
  if (snapshot.targetValue === nextValue && snapshot.state === "swapping") return snapshot;
  if (snapshot.state === "idle" && snapshot.visibleValue === nextValue) return snapshot;

  if (reducedMotion) {
    return {
      state: "idle",
      visibleValue: nextValue,
      previousValue: nextValue,
      targetValue: nextValue
    };
  }

  return {
    state: "swapping",
    visibleValue: nextValue,
    previousValue: snapshot.visibleValue,
    targetValue: nextValue
  };
}

export function settleStateSwap(snapshot: StateSwapSnapshot): StateSwapSnapshot {
  if (snapshot.state === "idle") return snapshot;
  return {
    state: "idle",
    visibleValue: snapshot.visibleValue,
    previousValue: snapshot.visibleValue,
    targetValue: snapshot.visibleValue
  };
}
