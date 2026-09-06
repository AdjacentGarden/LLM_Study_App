import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent
} from "react";

const DRAG_THRESHOLD_PX = 6;
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
  pointerId: number;
  previousInlineScrollBehavior: string | null;
  startScrollLeft: number;
  startScrollTop: number;
  startX: number;
  startY: number;
  verticalScroller: HTMLElement | null;
};

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
export function useMouseDragScroll() {
  const dragRef = useRef<MouseDragState | null>(null);
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

  const resetDrag = useCallback((event?: ReactPointerEvent<HTMLElement>, suppressClick = false) => {
    const drag = dragRef.current;
    if (!drag || (event && drag.pointerId !== event.pointerId)) return;

    dragRef.current = null;
    restoreScrollerBehavior(drag);
    setDragging(false);

    if (event && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    if (!suppressClick || !drag.dragging) return;
    suppressClickRef.current = true;
    clearClickResetTimer();
    clickResetTimerRef.current = window.setTimeout(() => {
      suppressClickRef.current = false;
      clickResetTimerRef.current = null;
    }, 0);
  }, [clearClickResetTimer, restoreScrollerBehavior]);

  useEffect(() => () => {
    clearClickResetTimer();
    const drag = dragRef.current;
    if (drag) restoreScrollerBehavior(drag);
  }, [clearClickResetTimer, restoreScrollerBehavior]);

  const onPointerDownCapture = useCallback((event: ReactPointerEvent<HTMLElement>) => {
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
      pointerId: event.pointerId,
      previousInlineScrollBehavior: null,
      startScrollLeft: horizontalScroller?.scrollLeft ?? 0,
      startScrollTop: verticalScroller?.scrollTop ?? 0,
      startX: event.clientX,
      startY: event.clientY,
      verticalScroller
    };
  }, [clearClickResetTimer]);

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
    resetDrag(event, true);
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

  return {
    consumeClick,
    dragging,
    onLostPointerCaptureCapture,
    onPointerCancelCapture,
    onPointerDownCapture,
    onPointerMoveCapture,
    onPointerUpCapture
  };
}
