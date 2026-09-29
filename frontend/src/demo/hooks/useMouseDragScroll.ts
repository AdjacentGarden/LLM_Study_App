import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent
} from "react";

const DRAG_THRESHOLD_PX = 6;
const MOMENTUM_DECAY_PER_FRAME = 0.9;
const MOMENTUM_FRAME_MS = 1000 / 60;
const MOMENTUM_MAX_FRAME_MS = 32;
const MOMENTUM_MAX_VELOCITY_PX_PER_MS = 2.4;
const MOMENTUM_MIN_VELOCITY_PX_PER_MS = 0.025;
const MOMENTUM_RELEASE_FRESHNESS_MS = 120;
const dragScrollIgnoreSelector = [
  "input",
  "textarea",
  "select",
  "video",
  "audio",
  "[contenteditable]:not([contenteditable='false'])",
  "[data-mouse-drag-scroll='ignore']"
].join(",");

type MouseDragState = {
  axis: "x" | "y" | null;
  dragging: boolean;
  horizontalScroller: HTMLElement | null;
  horizontalSelfManaged: boolean;
  lastClientY: number;
  lastMoveTime: number;
  pointerId: number;
  previousInlineScrollBehavior: string | null;
  startScrollLeft: number;
  startScrollTop: number;
  startX: number;
  startY: number;
  verticalScroller: HTMLElement | null;
  velocityY: number;
};

type MouseMomentumState = {
  frameId: number;
  lastTimestamp: number | null;
  previousInlineScrollBehavior: string;
  scroller: HTMLElement;
  velocity: number;
};

type MouseDragScrollOptions = {
  enableVerticalMomentum?: boolean;
  momentumScopeKey?: string;
};

function clampVelocity(value: number) {
  return Math.min(Math.max(value, -MOMENTUM_MAX_VELOCITY_PX_PER_MS), MOMENTUM_MAX_VELOCITY_PX_PER_MS);
}

function isScrollable(element: HTMLElement, axis: "x" | "y") {
  const styles = window.getComputedStyle(element);
  const overflow = axis === "x" ? styles.overflowX : styles.overflowY;
  const scrollSize = axis === "x" ? element.scrollWidth : element.scrollHeight;
  const clientSize = axis === "x" ? element.clientWidth : element.clientHeight;
  return (overflow === "auto" || overflow === "scroll" || overflow === "overlay")
    && scrollSize > clientSize + 1;
}

function findScroller(target: Element, root: HTMLElement, axis: "x" | "y") {
  let candidate: HTMLElement | null = target instanceof HTMLElement ? target : target.parentElement;

  while (candidate && root.contains(candidate)) {
    if (isScrollable(candidate, axis)) return candidate;
    if (candidate === root) break;
    candidate = candidate.parentElement;
  }

  return null;
}

/**
 * Adds touch-like mouse drag scrolling to every scroll container
 * below one application root. Touch, pen, wheel and trackpad input remain
 * native. Axis locking preserves horizontal gestures, while bespoke
 * two-dimensional gestures can opt out with data-mouse-drag-scroll="ignore".
 */
export function useMouseDragScroll({
  enableVerticalMomentum = false,
  momentumScopeKey = "default"
}: MouseDragScrollOptions = {}) {
  const dragRef = useRef<MouseDragState | null>(null);
  const momentumRef = useRef<MouseMomentumState | null>(null);
  const clickResetTimerRef = useRef<number | null>(null);
  const suppressClickRef = useRef(false);
  const [dragging, setDragging] = useState(false);

  const clearClickResetTimer = useCallback(() => {
    if (clickResetTimerRef.current === null) return;
    window.clearTimeout(clickResetTimerRef.current);
    clickResetTimerRef.current = null;
  }, []);

  const restoreScrollerBehavior = useCallback((drag: MouseDragState) => {
    const scroller = drag.axis === "x" ? drag.horizontalScroller : drag.axis === "y" ? drag.verticalScroller : null;
    if (scroller && drag.previousInlineScrollBehavior !== null) {
      scroller.style.scrollBehavior = drag.previousInlineScrollBehavior;
    }
  }, []);

  const stopMomentum = useCallback(() => {
    const momentum = momentumRef.current;
    if (!momentum) return;

    momentumRef.current = null;
    window.cancelAnimationFrame(momentum.frameId);
    momentum.scroller.style.scrollBehavior = momentum.previousInlineScrollBehavior;
  }, []);

  const startVerticalMomentum = useCallback((drag: MouseDragState, velocity: number) => {
    const scroller = drag.verticalScroller;
    if (!scroller) return;

    stopMomentum();
    const momentum: MouseMomentumState = {
      frameId: 0,
      lastTimestamp: null,
      previousInlineScrollBehavior: drag.previousInlineScrollBehavior ?? "",
      scroller,
      velocity: clampVelocity(velocity)
    };

    const finish = () => {
      if (momentumRef.current !== momentum) return;
      momentumRef.current = null;
      scroller.style.scrollBehavior = momentum.previousInlineScrollBehavior;
    };

    const step = (timestamp: number) => {
      if (momentumRef.current !== momentum) return;
      if (!scroller.isConnected) {
        finish();
        return;
      }

      const elapsed = momentum.lastTimestamp === null
        ? MOMENTUM_FRAME_MS
        : Math.min(Math.max(timestamp - momentum.lastTimestamp, 1), MOMENTUM_MAX_FRAME_MS);
      momentum.lastTimestamp = timestamp;

      const maxScrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      const nextScrollTop = Math.min(
        Math.max(scroller.scrollTop + (momentum.velocity * elapsed), 0),
        maxScrollTop
      );
      const reachedBoundary = (nextScrollTop <= 0 && momentum.velocity < 0)
        || (nextScrollTop >= maxScrollTop && momentum.velocity > 0);
      scroller.scrollTop = nextScrollTop;
      momentum.velocity *= Math.pow(MOMENTUM_DECAY_PER_FRAME, elapsed / MOMENTUM_FRAME_MS);

      if (reachedBoundary || Math.abs(momentum.velocity) < MOMENTUM_MIN_VELOCITY_PX_PER_MS) {
        finish();
        return;
      }
      momentum.frameId = window.requestAnimationFrame(step);
    };

    scroller.style.scrollBehavior = "auto";
    momentumRef.current = momentum;
    momentum.frameId = window.requestAnimationFrame(step);
  }, [stopMomentum]);

  const resetDrag = useCallback((
    event?: ReactPointerEvent<HTMLElement>,
    suppressClick = false,
    allowMomentum = false
  ) => {
    const drag = dragRef.current;
    if (!drag || (event && drag.pointerId !== event.pointerId)) return;

    dragRef.current = null;
    setDragging(false);

    if (event && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    const timeSinceLastMove = event
      ? Math.max(0, event.timeStamp - drag.lastMoveTime)
      : MOMENTUM_RELEASE_FRESHNESS_MS;
    const freshness = Math.max(0, 1 - (timeSinceLastMove / MOMENTUM_RELEASE_FRESHNESS_MS));
    const releaseVelocity = drag.velocityY * freshness;
    const shouldStartMomentum = allowMomentum
      && enableVerticalMomentum
      && drag.dragging
      && drag.axis === "y"
      && Boolean(drag.verticalScroller)
      && Math.abs(releaseVelocity) >= MOMENTUM_MIN_VELOCITY_PX_PER_MS;

    if (shouldStartMomentum) startVerticalMomentum(drag, releaseVelocity);
    else restoreScrollerBehavior(drag);

    if (!suppressClick || !drag.dragging) return;
    suppressClickRef.current = true;
    clearClickResetTimer();
    clickResetTimerRef.current = window.setTimeout(() => {
      suppressClickRef.current = false;
      clickResetTimerRef.current = null;
    }, 0);
  }, [clearClickResetTimer, enableVerticalMomentum, restoreScrollerBehavior, startVerticalMomentum]);

  useEffect(() => () => {
    clearClickResetTimer();
    stopMomentum();
    const drag = dragRef.current;
    if (drag) restoreScrollerBehavior(drag);
  }, [clearClickResetTimer, restoreScrollerBehavior, stopMomentum]);

  useLayoutEffect(() => {
    stopMomentum();
  }, [enableVerticalMomentum, momentumScopeKey, stopMomentum]);

  const onPointerDownCapture = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    stopMomentum();
    if (
      event.pointerType !== "mouse"
      || event.button !== 0
      || !event.isPrimary
      || event.altKey
      || event.ctrlKey
      || event.metaKey
      || event.shiftKey
    ) {
      return;
    }

    const target = event.target instanceof Element ? event.target : null;
    if (!target || target.closest(dragScrollIgnoreSelector)) return;

    const horizontalScroller = findScroller(target, event.currentTarget, "x");
    const verticalScroller = findScroller(target, event.currentTarget, "y");
    if (!horizontalScroller && !verticalScroller) return;

    clearClickResetTimer();
    suppressClickRef.current = false;
    dragRef.current = {
      axis: null,
      dragging: false,
      horizontalScroller,
      horizontalSelfManaged: Boolean(target.closest("[data-mouse-drag-scroll='self']")),
      lastClientY: event.clientY,
      lastMoveTime: event.timeStamp,
      pointerId: event.pointerId,
      previousInlineScrollBehavior: null,
      startScrollLeft: horizontalScroller?.scrollLeft ?? 0,
      startScrollTop: verticalScroller?.scrollTop ?? 0,
      startX: event.clientX,
      startY: event.clientY,
      verticalScroller,
      velocityY: 0
    };
  }, [clearClickResetTimer, stopMomentum]);

  const onPointerMoveCapture = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;

    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;

    if (!drag.dragging) {
      if (Math.hypot(deltaX, deltaY) < DRAG_THRESHOLD_PX) return;

      // Lock the gesture to its first meaningful axis. Components with a
      // semantic horizontal gesture can keep ownership of that axis while
      // their surrounding page still supports vertical mouse dragging.
      const horizontalWins = Math.abs(deltaX) >= Math.abs(deltaY);
      if (horizontalWins && drag.horizontalSelfManaged) {
        dragRef.current = null;
        return;
      }

      drag.axis = horizontalWins ? "x" : "y";
      const scroller = drag.axis === "x" ? drag.horizontalScroller : drag.verticalScroller;
      if (!scroller) {
        dragRef.current = null;
        return;
      }

      drag.dragging = true;
      drag.previousInlineScrollBehavior = scroller.style.scrollBehavior;
      scroller.style.scrollBehavior = "auto";
      event.currentTarget.setPointerCapture(event.pointerId);
      setDragging(true);
    }

    event.preventDefault();
    event.stopPropagation();
    const elapsed = Math.max(event.timeStamp - drag.lastMoveTime, 1);
    const sampledVelocityY = clampVelocity(-(event.clientY - drag.lastClientY) / elapsed);
    const sampleWeight = elapsed > 40 ? 1 : 0.72;
    drag.velocityY = (drag.velocityY * (1 - sampleWeight)) + (sampledVelocityY * sampleWeight);
    drag.lastClientY = event.clientY;
    drag.lastMoveTime = event.timeStamp;
    if (drag.axis === "x" && drag.horizontalScroller) {
      drag.horizontalScroller.scrollLeft = drag.startScrollLeft - deltaX;
    } else if (drag.axis === "y" && drag.verticalScroller) {
      drag.verticalScroller.scrollTop = drag.startScrollTop - deltaY;
    }
  }, []);

  const onPointerUpCapture = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (drag.dragging) {
      event.preventDefault();
      event.stopPropagation();
    }
    resetDrag(event, true, true);
  }, [resetDrag]);

  const onPointerCancelCapture = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    resetDrag(event, false);
  }, [resetDrag]);

  const onLostPointerCaptureCapture = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    resetDrag(event, false);
  }, [resetDrag]);

  const consumeClick = useCallback((event: ReactMouseEvent<HTMLElement>) => {
    if (!suppressClickRef.current) return false;
    suppressClickRef.current = false;
    clearClickResetTimer();
    event.preventDefault();
    event.stopPropagation();
    return true;
  }, [clearClickResetTimer]);

  const onWheelCapture = useCallback(() => {
    stopMomentum();
  }, [stopMomentum]);

  return {
    consumeClick,
    dragging,
    onLostPointerCaptureCapture,
    onPointerCancelCapture,
    onPointerDownCapture,
    onPointerMoveCapture,
    onPointerUpCapture,
    onWheelCapture
  };
}
