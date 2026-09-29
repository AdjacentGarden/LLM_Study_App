import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { useMotionHistory } from "./MotionHistoryContext";
import { useReducedMotion } from "./useReducedMotion";

export type DiagnosisMotionState = "entering" | "idle";

/**
 * A diagnosis response is a session-scoped result: it may enter once for its
 * submission id, but ordinary rerenders and returning from SourceReader must
 * keep its final state. A later submission intentionally receives a new key.
 */
export function useDiagnosisMotion(submissionId: string | null) {
  const { consume } = useMotionHistory();
  const reducedMotion = useReducedMotion();
  const motionKey = submissionId ? `diagnosis:${submissionId}` : null;
  const appliedKeyRef = useRef<string | null>(null);
  const [state, setState] = useState<DiagnosisMotionState>("idle");

  useLayoutEffect(() => {
    if (!motionKey) {
      appliedKeyRef.current = null;
      setState("idle");
      return;
    }
    if (appliedKeyRef.current === motionKey) {
      if (reducedMotion) setState("idle");
      return;
    }

    appliedKeyRef.current = motionKey;
    setState(consume(motionKey) && !reducedMotion ? "entering" : "idle");
  }, [consume, motionKey, reducedMotion]);

  const settle = useCallback((key: string) => {
    if (key !== appliedKeyRef.current) return;
    setState("idle");
  }, []);

  return { motionKey, state, settle };
}
