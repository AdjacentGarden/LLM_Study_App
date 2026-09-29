import { useCallback, useEffect, useLayoutEffect, useRef, useState, type TransitionEvent } from "react";
import { useReducedMotion } from "./useReducedMotion";

export type SlidingFilterOption<T extends string> = Readonly<{
  value: T;
  label: string;
}>;

export type SlidingFilterGroupProps<T extends string> = Readonly<{
  value: T;
  options: readonly SlidingFilterOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
}>;

type SelectionMotionState = "unmeasured" | "idle" | "moving";

export function SlidingFilterGroup<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  className = ""
}: SlidingFilterGroupProps<T>) {
  const reducedMotion = useReducedMotion();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const indicatorRef = useRef<HTMLSpanElement | null>(null);
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const measuredRef = useRef(false);
  const previousValueRef = useRef(value);
  const [motionState, setMotionState] = useState<SelectionMotionState>("unmeasured");

  const measure = useCallback((animate: boolean) => {
    const activeButton = buttonRefs.current[options.findIndex((option) => option.value === value)];
    const indicator = indicatorRef.current;
    if (!activeButton || !indicator) return;

    const left = activeButton.offsetLeft;
    const top = activeButton.offsetTop;
    const width = activeButton.offsetWidth;
    const height = activeButton.offsetHeight;
    const shouldAnimate = animate && measuredRef.current && !reducedMotion;

    if (!shouldAnimate) {
      const previousTransition = indicator.style.transition;
      indicator.style.transition = "none";
      indicator.style.transform = `translate3d(${left}px, 0, 0)`;
      indicator.style.width = `${width}px`;
      indicator.style.top = `${top}px`;
      indicator.style.height = `${height}px`;
      void indicator.offsetWidth;
      indicator.style.transition = previousTransition;
      measuredRef.current = true;
      setMotionState("idle");
      return;
    }

    indicator.style.transform = `translate3d(${left}px, 0, 0)`;
    indicator.style.width = `${width}px`;
    indicator.style.top = `${top}px`;
    indicator.style.height = `${height}px`;
    setMotionState("moving");
  }, [options, reducedMotion, value]);

  useLayoutEffect(() => {
    const shouldAnimate = previousValueRef.current !== value;
    previousValueRef.current = value;
    measure(shouldAnimate);
  }, [measure, value]);

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    let active = true;
    const resnap = () => {
      if (active) measure(false);
    };
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resnap);
    resizeObserver?.observe(root);
    buttonRefs.current.forEach((button) => {
      if (button) resizeObserver?.observe(button);
    });
    window.addEventListener("resize", resnap);
    window.addEventListener("orientationchange", resnap);

    const fontsReady = document.fonts?.ready;
    fontsReady?.then(resnap).catch(() => undefined);

    return () => {
      active = false;
      resizeObserver?.disconnect();
      window.removeEventListener("resize", resnap);
      window.removeEventListener("orientationchange", resnap);
    };
  }, [measure, options.length]);

  useEffect(() => {
    const indicator = indicatorRef.current;
    if (!indicator) return;
    const settleCancelledTransition = () => setMotionState("idle");
    indicator.addEventListener("transitioncancel", settleCancelledTransition);
    return () => indicator.removeEventListener("transitioncancel", settleCancelledTransition);
  }, []);

  const settle = (event: TransitionEvent<HTMLSpanElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.propertyName !== "transform" && event.propertyName !== "width") return;
    setMotionState("idle");
  };

  return (
    <div
      ref={rootRef}
      className={`motion-sliding-filter ${className}`.trim()}
      role="group"
      aria-label={ariaLabel}
      data-motion-selection-state={motionState}
    >
      <span ref={indicatorRef} className="motion-sliding-filter-indicator" aria-hidden="true" onTransitionEnd={settle} />
      {options.map((option, index) => (
        <button
          key={option.value}
          ref={(button) => { buttonRefs.current[index] = button; }}
          className={`motion-sliding-filter-button filter-pill ${value === option.value ? "active" : ""}`.trim()}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
