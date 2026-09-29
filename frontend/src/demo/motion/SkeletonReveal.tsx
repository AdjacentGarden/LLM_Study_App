import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type TransitionEvent } from "react";
import { useReducedMotion } from "./useReducedMotion";

export type LoadState = "loading" | "ready" | "error";
export type ReadyContentKind = "content" | "empty";

type RevealPhase = LoadState | "revealing";

export type SkeletonRevealProps = Readonly<{
  state: LoadState;
  readyKind?: ReadyContentKind;
  skeleton: ReactNode;
  children: ReactNode;
  error?: ReactNode;
  className?: string;
  minBlockSize?: string;
  labelledBy?: string;
}>;

export function SkeletonReveal({
  state,
  readyKind = "content",
  skeleton,
  children,
  error,
  className = "",
  minBlockSize,
  labelledBy
}: SkeletonRevealProps) {
  const reducedMotion = useReducedMotion();
  const previousStateRef = useRef<LoadState | null>(null);
  const [phase, setPhase] = useState<RevealPhase>(() => state === "ready" ? "ready" : state);

  useLayoutEffect(() => {
    const previousState = previousStateRef.current;
    previousStateRef.current = state;

    if (reducedMotion || state !== "ready") {
      setPhase(state);
      return;
    }

    setPhase(previousState === "loading" ? "revealing" : "ready");
  }, [reducedMotion, state]);

  const style: CSSProperties | undefined = minBlockSize ? { minBlockSize } : undefined;
  const handleRevealEnd = (event: TransitionEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget || event.propertyName !== "opacity") return;
    if (phase === "revealing") setPhase("ready");
  };
  const handleRevealCancel = () => {
    if (phase === "revealing") setPhase("ready");
  };

  return (
    <div
      className={`motion-skeleton-reveal ${className}`.trim()}
      style={style}
      data-motion-load-state={phase}
      data-motion-ready-kind={state === "ready" ? readyKind : undefined}
      aria-busy={state === "loading" ? true : undefined}
      aria-labelledby={labelledBy}
    >
      <div className="motion-skeleton-layer motion-skeleton-placeholder" aria-hidden="true">
        {skeleton}
      </div>
      <div
        className="motion-skeleton-layer motion-skeleton-content"
        aria-hidden={state === "loading" || state === "error" ? true : undefined}
        onTransitionEnd={handleRevealEnd}
        onTransitionCancel={handleRevealCancel}
      >
        {children}
      </div>
      <div className="motion-skeleton-layer motion-skeleton-error" role={state === "error" ? "alert" : undefined}>
        {state === "error" ? error : null}
      </div>
    </div>
  );
}
