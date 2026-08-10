import { useCallback, useEffect, useRef, type AnimationEvent as ReactAnimationEvent, type ReactNode } from "react";

export type MotionErrorShakeProps = Readonly<{
  state: "idle" | "active";
  sequence: number;
  onSettle: () => void;
  children: ReactNode;
  className?: string;
}>;

export function MotionErrorShake({
  state,
  sequence,
  onSettle,
  children,
  className = ""
}: MotionErrorShakeProps) {
  const elementRef = useRef<HTMLSpanElement | null>(null);
  const settle = useCallback((event?: ReactAnimationEvent<HTMLSpanElement>) => {
    if (event && (event.target !== event.currentTarget || event.animationName !== "motion-error-shake")) return;
    onSettle();
  }, [onSettle]);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;
    const handleAnimationCancel = (event: globalThis.AnimationEvent) => {
      if (event.animationName === "motion-error-shake") onSettle();
    };
    element.addEventListener("animationcancel", handleAnimationCancel);
    return () => element.removeEventListener("animationcancel", handleAnimationCancel);
  }, [onSettle, sequence]);

  return (
    <span
      ref={elementRef}
      className={`motion-error-shake ${className}`.trim()}
      data-motion-error-state={state === "active" ? "shaking" : "idle"}
      data-motion-error-sequence={sequence}
      onAnimationEnd={settle}
    >
      {children}
    </span>
  );
}
