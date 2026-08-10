import { useEffect, useLayoutEffect, useRef, useState, type AnimationEvent } from "react";
import { useReducedMotion } from "./useReducedMotion";
import {
  createStateSwapSnapshot,
  settleStateSwap,
  updateStateSwap,
  type StateSwapSnapshot
} from "./stateSwapMachine";

export type StateSwapTextProps = Readonly<{
  value: string;
  className?: string;
  announce?: "off" | "polite";
  motionKey?: string;
  reserveValues?: readonly string[];
}>;

export function StateSwapText({
  value,
  className = "",
  announce = "off",
  motionKey,
  reserveValues = []
}: StateSwapTextProps) {
  const reducedMotion = useReducedMotion();
  const [snapshot, setSnapshot] = useState<StateSwapSnapshot>(() => createStateSwapSnapshot(value));
  const latestValueRef = useRef(value);
  const currentLayerRef = useRef<HTMLSpanElement | null>(null);

  useLayoutEffect(() => {
    if (latestValueRef.current === value) {
      if (reducedMotion) {
        setSnapshot((current) => updateStateSwap(current, current.targetValue, true));
      }
      return;
    }

    latestValueRef.current = value;
    setSnapshot((current) => updateStateSwap(current, value, reducedMotion));
  }, [reducedMotion, value]);

  const settle = (event: AnimationEvent<HTMLSpanElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.animationName !== "motion-text-swap-enter") return;
    setSnapshot((current) => settleStateSwap(current));
  };

  useEffect(() => {
    const currentLayer = currentLayerRef.current;
    if (!currentLayer) return;

    const settleCancelledAnimation = () => {
      setSnapshot((current) => settleStateSwap(current));
    };
    currentLayer.addEventListener("animationcancel", settleCancelledAnimation);
    return () => currentLayer.removeEventListener("animationcancel", settleCancelledAnimation);
  }, []);

  const valuesToMeasure = Array.from(new Set([
    snapshot.visibleValue,
    snapshot.previousValue,
    ...reserveValues
  ]));

  return (
    <span
      className={`motion-state-swap ${className}`.trim()}
      data-motion-text-state={snapshot.state}
      data-motion-text-key={motionKey}
      aria-live={announce === "polite" ? "polite" : undefined}
      aria-atomic={announce === "polite" ? true : undefined}
    >
      <span className="motion-state-swap-visual">
        <span
          className="motion-state-swap-layer motion-state-swap-previous"
          data-motion-value={snapshot.previousValue}
          aria-hidden="true"
        />
        <span
          ref={currentLayerRef}
          className="motion-state-swap-layer motion-state-swap-current"
          onAnimationEnd={settle}
        >
          {snapshot.visibleValue}
        </span>
        {valuesToMeasure.map((candidate) => (
          <span className="motion-state-swap-measure" data-motion-value={candidate} aria-hidden="true" key={candidate} />
        ))}
      </span>
    </span>
  );
}
