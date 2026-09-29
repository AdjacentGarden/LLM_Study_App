import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { useReducedMotion } from "./useReducedMotion";

export type ImageMotionState = "loading" | "entering" | "idle" | "failed";
export type StageThreeImageMotionState = ImageMotionState;

/**
 * Keeps image feedback local to one image element. A successful load may fade
 * once per DOM/source pair; it is deliberately not part of MotionHistory.
 */
export function useImageMotion(source?: string | null) {
  const reducedMotion = useReducedMotion();
  const imageRef = useRef<HTMLImageElement | null>(null);
  const activeSourceRef = useRef<string | null>(null);
  const loadedSourceRef = useRef<string | null>(null);
  const [state, setState] = useState<ImageMotionState>(source ? "loading" : "failed");

  const settleLoaded = useCallback(() => {
    if (!source || loadedSourceRef.current === source) return;
    loadedSourceRef.current = source;
    setState(reducedMotion ? "idle" : "entering");
  }, [reducedMotion, source]);

  const settleFailed = useCallback(() => {
    if (!source) return;
    setState("failed");
  }, [source]);

  useLayoutEffect(() => {
    if (!source) {
      activeSourceRef.current = null;
      loadedSourceRef.current = null;
      setState("failed");
      return;
    }

    if (activeSourceRef.current !== source) {
      activeSourceRef.current = source;
      loadedSourceRef.current = null;
      setState("loading");
    }

    const image = imageRef.current;
    if (!image?.complete) return;
    if (image.naturalWidth > 0) settleLoaded();
    else settleFailed();
  }, [settleFailed, settleLoaded, source]);

  useLayoutEffect(() => {
    if (reducedMotion) {
      setState((current) => current === "entering" ? "idle" : current);
    }
  }, [reducedMotion]);

  const settleAnimation = useCallback(() => {
    setState((current) => current === "entering" ? "idle" : current);
  }, []);

  return {
    imageRef,
    onError: settleFailed,
    onLoad: settleLoaded,
    settleAnimation,
    state
  };
}

// Keep the Stage 3 public name for its existing CourseReady/CourseCover users.
export const useStageThreeImageMotion = useImageMotion;
