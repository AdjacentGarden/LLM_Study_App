import { useEffect, useRef } from "react";
import { useReducedMotion } from "../../motion/useReducedMotion";

/** Adds mouse dragging to a native scroll rail; touch and trackpad stay native. */
export function useHorizontalDragScroll(enabled: boolean) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!enabled || !scroller) return;
    let drag: {
      pointerId: number;
      startX: number;
      startY: number;
      startScroll: number;
      scale: number;
      moved: boolean;
    } | null = null;
    let frame: number | null = null;
    let focusFrame: number | null = null;
    let settleTimer: number | null = null;
    let settleTarget: number | null = null;
    let pendingScroll = scroller.scrollLeft;
    let suppressClick = false;

    const clearSettling = () => {
      if (settleTimer !== null) window.clearTimeout(settleTimer);
      settleTimer = null;
      settleTarget = null;
      scroller.classList.remove("is-settling");
    };
    const handleScrollEnd = () => {
      if (settleTarget !== null && Math.abs(scroller.scrollLeft - settleTarget) < 1) clearSettling();
    };
    const watchSettling = () => {
      if (!scroller.classList.contains("is-settling")) return;
      if (settleTimer !== null) window.clearTimeout(settleTimer);
      // scrollend is preferred; debounce is for engines without that event.
      settleTimer = window.setTimeout(clearSettling, 150);
    };
    const flushScroll = () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = null;
      if (focusFrame !== null) window.cancelAnimationFrame(focusFrame);
      focusFrame = null;
      scroller.scrollLeft = pendingScroll;
    };
    const settleScroll = () => {
      const bounds = scroller.getBoundingClientRect();
      const scale = bounds.width / Math.max(1, scroller.clientWidth);
      const padding = Number.parseFloat(getComputedStyle(scroller).scrollPaddingInlineStart) || 0;
      const max = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
      const stops = [0, max, ...Array.from(scroller.children, (card) => (
        Math.max(0, Math.min(max, scroller.scrollLeft + (card.getBoundingClientRect().left - bounds.left) / scale - padding))
      ))];
      const target = stops.reduce((nearest, stop) => (
        Math.abs(stop - scroller.scrollLeft) < Math.abs(nearest - scroller.scrollLeft) ? stop : nearest
      ));
      // Keep snapping disabled until the one native smooth scroll finishes, so
      // restoring CSS snap cannot jump back before the release animation.
      scroller.classList.add("is-settling");
      scroller.classList.remove("is-dragging");
      settleTarget = target;
      scroller.scrollTo({ left: target, behavior: reducedMotion ? "instant" : "smooth" });
      if (reducedMotion || Math.abs(target - scroller.scrollLeft) < .5) clearSettling();
      else watchSettling();
    };
    const finishDrag = (event: PointerEvent) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const finished = drag;
      drag = null;
      if (finished.moved) {
        flushScroll();
        suppressClick = true;
        settleScroll();
      }
      if (scroller.hasPointerCapture(finished.pointerId)) scroller.releasePointerCapture(finished.pointerId);
    };
    const resetInteraction = () => {
      const previous = drag;
      drag = null;
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = null;
      if (focusFrame !== null) window.cancelAnimationFrame(focusFrame);
      focusFrame = null;
      clearSettling();
      scroller.classList.remove("is-dragging");
      if (previous?.moved) suppressClick = true;
      if (previous && scroller.hasPointerCapture(previous.pointerId)) scroller.releasePointerCapture(previous.pointerId);
    };
    const handlePointerDown = (event: PointerEvent) => {
      if (scroller.classList.contains("is-settling")) {
        scroller.scrollTo({ left: scroller.scrollLeft, behavior: "instant" });
        clearSettling();
      }
      if (event.pointerType !== "mouse" || event.button !== 0 || !event.isPrimary
        || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
        || scroller.scrollWidth <= scroller.clientWidth + 1) return;
      clearSettling();
      scroller.scrollTo({ left: scroller.scrollLeft, behavior: "instant" });
      suppressClick = false;
      const bounds = scroller.getBoundingClientRect();
      drag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startScroll: scroller.scrollLeft,
        scale: bounds.width / Math.max(1, scroller.clientWidth),
        moved: false
      };
    };
    const handlePointerMove = (event: PointerEvent) => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      if (!drag.moved) {
        if (Math.abs(dy) > 6 && Math.abs(dy) > Math.abs(dx)) {
          drag = null;
          suppressClick = true;
          return;
        }
        if (Math.abs(dx) < 6) return;
        drag.moved = true;
        scroller.classList.add("is-dragging");
        scroller.setPointerCapture(event.pointerId);
      }
      event.preventDefault();
      pendingScroll = Math.max(0, Math.min(scroller.scrollWidth - scroller.clientWidth, drag.startScroll - dx / drag.scale));
      // No React state updates or per-card measurements on pointermove.
      if (frame === null) frame = window.requestAnimationFrame(flushScroll);
    };
    const handleClick = (event: MouseEvent) => {
      if (!suppressClick || event.detail === 0) return;
      suppressClick = false;
      event.preventDefault();
      event.stopPropagation();
    };
    const handleWheel = () => {
      if (!scroller.classList.contains("is-settling")) return;
      scroller.scrollTo({ left: scroller.scrollLeft, behavior: "instant" });
      clearSettling();
    };
    const handleFocus = (event: FocusEvent) => {
      const card = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>(".study-tool-card") : null;
      if (!card || !card.matches(":focus-visible")) return;
      if (focusFrame !== null) window.cancelAnimationFrame(focusFrame);
      focusFrame = window.requestAnimationFrame(() => {
        focusFrame = null;
        if (drag || !card.isConnected || document.activeElement !== card) return;
        const area = scroller.getBoundingClientRect();
        const box = card.getBoundingClientRect();
        const scale = area.width / Math.max(1, scroller.clientWidth);
        const padding = Number.parseFloat(getComputedStyle(scroller).scrollPaddingInlineStart) || 0;
        const delta = box.left < area.left + padding * scale ? box.left - area.left - padding * scale
          : box.right > area.right - padding * scale ? box.right - area.right + padding * scale : 0;
        if (Math.abs(delta) < 1) return;
        clearSettling();
        settleTarget = Math.max(0, Math.min(scroller.scrollWidth - scroller.clientWidth, scroller.scrollLeft + delta / scale));
        scroller.classList.add("is-settling");
        scroller.scrollTo({ left: settleTarget, behavior: reducedMotion ? "instant" : "smooth" });
        if (reducedMotion) clearSettling();
        else watchSettling();
      });
    };

    scroller.addEventListener("pointerdown", handlePointerDown);
    scroller.addEventListener("pointermove", handlePointerMove);
    // The app shell can capture vertical gestures before events reach the rail.
    // Observe release first so that gesture cannot leave a stale local drag.
    window.addEventListener("pointerup", finishDrag, true);
    window.addEventListener("pointercancel", finishDrag, true);
    scroller.addEventListener("lostpointercapture", finishDrag);
    scroller.addEventListener("click", handleClick, true);
    scroller.addEventListener("scroll", watchSettling, { passive: true });
    scroller.addEventListener("scrollend", handleScrollEnd);
    scroller.addEventListener("wheel", handleWheel, { passive: true });
    scroller.addEventListener("focusin", handleFocus);
    window.addEventListener("blur", resetInteraction);
    window.addEventListener("resize", resetInteraction);
    return () => {
      resetInteraction();
      scroller.removeEventListener("pointerdown", handlePointerDown);
      scroller.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", finishDrag, true);
      window.removeEventListener("pointercancel", finishDrag, true);
      scroller.removeEventListener("lostpointercapture", finishDrag);
      scroller.removeEventListener("click", handleClick, true);
      scroller.removeEventListener("scroll", watchSettling);
      scroller.removeEventListener("scrollend", handleScrollEnd);
      scroller.removeEventListener("wheel", handleWheel);
      scroller.removeEventListener("focusin", handleFocus);
      window.removeEventListener("blur", resetInteraction);
      window.removeEventListener("resize", resetInteraction);
    };
  }, [enabled, reducedMotion]);

  return scrollerRef;
}
