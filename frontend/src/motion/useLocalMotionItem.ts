import { useCallback, useLayoutEffect, useRef, useState, type AnimationEvent } from "react";
import { useReducedMotion } from "./useReducedMotion";

export type LocalMotionItemKind = "content" | "source-page-content";
export type LocalMotionItemState = "entering" | "idle";

export type LocalMotionItemAttributes = {
  "data-motion-item": LocalMotionItemKind;
  "data-motion-item-key": string;
  "data-motion-item-state": LocalMotionItemState;
  onAnimationEnd: (event: AnimationEvent<HTMLElement>) => void;
};

export type LocalMotionItemOptions = {
  /**
   * Detail panes commonly begin as a stable companion to a list. Set this to
   * false when only an explicit user selection or state replacement should
   * enter; a changed key still receives the normal local motion.
   */
  animateInitial?: boolean;
};

const animationNameByKind: Record<LocalMotionItemKind, string> = {
  content: "motion-local-item-in",
  "source-page-content": "motion-source-page-in"
};

/**
 * Owns one explicit, local entry state. It deliberately has no history: these
 * items belong to the current detail surface, unlike course cards that must
 * remember their first appearance across Home and Library.
 */
export function useLocalMotionItem(
  motionKey: string,
  kind: LocalMotionItemKind = "content",
  { animateInitial = true }: LocalMotionItemOptions = {}
) {
  const reducedMotion = useReducedMotion();
  const activeKeyRef = useRef<string | null>(null);
  const [state, setState] = useState<LocalMotionItemState>(() => reducedMotion || !animateInitial ? "idle" : "entering");
  const animationName = animationNameByKind[kind];

  useLayoutEffect(() => {
    if (activeKeyRef.current !== motionKey) {
      const isInitialKey = activeKeyRef.current === null;
      activeKeyRef.current = motionKey;
      setState(reducedMotion || (isInitialKey && !animateInitial) ? "idle" : "entering");
      return;
    }

    if (reducedMotion) setState("idle");
  }, [animateInitial, motionKey, reducedMotion]);

  const onAnimationEnd = useCallback((event: AnimationEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.animationName !== animationName) return;
    if (event.currentTarget.getAttribute("data-motion-item-key") !== activeKeyRef.current) return;
    setState("idle");
  }, [animationName]);

  return {
    attributes: {
      "data-motion-item": kind,
      "data-motion-item-key": motionKey,
      "data-motion-item-state": state,
      onAnimationEnd
    } satisfies LocalMotionItemAttributes,
    motionKey,
    state
  };
}
