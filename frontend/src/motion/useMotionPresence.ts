import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import {
  createPresenceSnapshot,
  reconcilePresence,
  settlePresence,
  type MotionState,
  type PresenceSnapshot
} from "./presenceMachine";
import { localMotionFallbackMs } from "./timing";

/** @deprecated Use a semantic fallback from `timing.ts` for new surfaces. */
export const motionPresenceMaxMs = localMotionFallbackMs;

export type UseMotionPresenceOptions<T> = {
  requested: T | null;
  getKey: (value: T) => string;
  reducedMotion: boolean;
  motionNames: readonly string[];
  maxMotionMs?: number;
};

export type MotionPresence<T> = {
  rendered: T | null;
  state: MotionState;
  presenceId: number;
  onAnimationEnd: (event: MotionAnimationEvent) => void;
  onAnimationCancel: (event: MotionAnimationEvent) => void;
};

export type MotionAnimationEvent = {
  target: EventTarget | null;
  currentTarget: EventTarget | null;
  animationName: string;
};

export function useMotionPresence<T>({
  requested,
  getKey,
  reducedMotion,
  motionNames,
  maxMotionMs = localMotionFallbackMs
}: UseMotionPresenceOptions<T>): MotionPresence<T> {
  const [snapshot, setSnapshot] = useState<PresenceSnapshot<T>>(() => (
    reconcilePresence(createPresenceSnapshot<T>(), { requested, getKey, reducedMotion })
  ));

  // Deriving the next snapshot during render makes a runtime reduced-motion
  // switch immediately expose the final surface state before the layout effect
  // commits the same transition to the machine.
  const renderedSnapshot = reconcilePresence(snapshot, { requested, getKey, reducedMotion });

  useLayoutEffect(() => {
    setSnapshot((current) => reconcilePresence(current, { requested, getKey, reducedMotion }));
  }, [getKey, reducedMotion, requested]);

  const settleForCurrentGeneration = useCallback((event: MotionAnimationEvent) => {
    if (event.target !== event.currentTarget || !motionNames.includes(event.animationName)) return;

    const presenceId = renderedSnapshot.presenceId;
    setSnapshot((current) => settlePresence(current, presenceId));
  }, [motionNames, renderedSnapshot.presenceId]);

  useEffect(() => {
    if (reducedMotion || renderedSnapshot.state === "idle") return;

    const presenceId = renderedSnapshot.presenceId;
    const fallback = window.setTimeout(() => {
      setSnapshot((current) => settlePresence(current, presenceId));
    }, maxMotionMs);

    return () => window.clearTimeout(fallback);
  }, [maxMotionMs, reducedMotion, renderedSnapshot.presenceId, renderedSnapshot.state]);

  return {
    rendered: renderedSnapshot.rendered,
    state: renderedSnapshot.state,
    presenceId: renderedSnapshot.presenceId,
    onAnimationEnd: settleForCurrentGeneration,
    onAnimationCancel: settleForCurrentGeneration
  };
}
