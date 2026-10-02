import type { ReactNode } from "react";

export type MotionIconSwapProps = Readonly<{
  state: string;
  firstState: string;
  secondState: string;
  firstIcon: ReactNode;
  secondIcon: ReactNode;
  className?: string;
}>;

export function MotionIconSwap({
  state,
  firstState,
  secondState,
  firstIcon,
  secondIcon,
  className = ""
}: MotionIconSwapProps) {
  const validState = state === firstState || state === secondState ? state : firstState;

  return (
    <span
      className={`motion-icon-swap ${className}`.trim()}
      data-motion-icon-state={validState}
      data-motion-icon-invalid={validState === state ? undefined : "true"}
    >
      <span className={`motion-icon-swap-layer ${validState === firstState ? "is-active" : ""}`} aria-hidden="true">
        {firstIcon}
      </span>
      <span className={`motion-icon-swap-layer ${validState === secondState ? "is-active" : ""}`} aria-hidden="true">
        {secondIcon}
      </span>
    </span>
  );
}
