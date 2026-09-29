import { useCallback, useEffect, useRef, useState } from "react";
import { useReducedMotion } from "./useReducedMotion";

export type OneShotFeedback = Readonly<{
  state: "idle" | "active";
  sequence: number;
  trigger: () => void;
  settle: () => void;
}>;

export function useOneShotFeedback(): OneShotFeedback {
  const reducedMotion = useReducedMotion();
  const frameRef = useRef<number | null>(null);
  const sequenceRef = useRef(0);
  const [state, setState] = useState<"idle" | "active">("idle");

  const cancelPendingFrame = useCallback(() => {
    if (frameRef.current === null) return;
    window.cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
  }, []);

  const settle = useCallback(() => {
    cancelPendingFrame();
    setState("idle");
  }, [cancelPendingFrame]);

  const trigger = useCallback(() => {
    sequenceRef.current += 1;
    cancelPendingFrame();

    if (reducedMotion) {
      setState("idle");
      return;
    }

    setState((current) => {
      if (current !== "active") return "active";
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        setState("active");
      });
      return "idle";
    });
  }, [cancelPendingFrame, reducedMotion]);

  useEffect(() => {
    if (reducedMotion) settle();
    return cancelPendingFrame;
  }, [cancelPendingFrame, reducedMotion, settle]);

  return {
    state,
    sequence: sequenceRef.current,
    trigger,
    settle
  };
}
