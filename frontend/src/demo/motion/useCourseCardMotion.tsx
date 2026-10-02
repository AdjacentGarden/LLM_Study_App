import { useCallback, useLayoutEffect, useRef, useState, type AnimationEvent, type CSSProperties, type ReactNode } from "react";
import { clampCourseCardMotionIndex, shouldAnimateCourseCard } from "./courseCardMotion";
import { useMotionHistory } from "./MotionHistoryContext";
import { useReducedMotion } from "./useReducedMotion";

export type CourseCardMotionState = "entering" | "idle";

export type CourseCardMotionAttributes = {
  "data-motion-course-card-key": string;
  "data-motion-course-card-state": CourseCardMotionState;
  onAnimationEnd: (event: AnimationEvent<HTMLElement>) => void;
  style: CSSProperties;
};

/**
 * A course card is allowed one finite first-appearance entry per session. The
 * key intentionally follows the book rather than a screen, so Home and
 * Library share the same history and a remount cannot replay it.
 */
export function useCourseCardMotion(bookId: string, index: number) {
  const history = useMotionHistory();
  const reducedMotion = useReducedMotion();
  const motionKey = `course-card:${bookId}`;
  const activeKeyRef = useRef<string | null>(null);
  const [state, setState] = useState<CourseCardMotionState>("idle");

  useLayoutEffect(() => {
    if (activeKeyRef.current === motionKey) {
      if (reducedMotion) setState("idle");
      return;
    }

    activeKeyRef.current = motionKey;
    const firstAppearance = history.consume(motionKey);
    setState(!reducedMotion && firstAppearance && shouldAnimateCourseCard(index) ? "entering" : "idle");
  }, [history, index, motionKey, reducedMotion]);

  const onAnimationEnd = useCallback((event: AnimationEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.animationName !== "motion-course-card-in") return;
    if (event.currentTarget.getAttribute("data-motion-course-card-key") !== activeKeyRef.current) return;
    setState("idle");
  }, []);

  return {
    "data-motion-course-card-key": motionKey,
    "data-motion-course-card-state": state,
    onAnimationEnd,
    style: {
      "--motion-course-card-index": clampCourseCardMotionIndex(index)
    } as CSSProperties
  } satisfies CourseCardMotionAttributes;
}

export function CourseCardMotion({
  bookId,
  children,
  index
}: {
  bookId: string;
  children: (attributes: CourseCardMotionAttributes) => ReactNode;
  index: number;
}) {
  return <>{children(useCourseCardMotion(bookId, index))}</>;
}
