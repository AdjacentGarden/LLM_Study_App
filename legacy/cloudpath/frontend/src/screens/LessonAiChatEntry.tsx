import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent
} from "react";

type LessonAiEntryState = "idle" | "pressed" | "dragging";
type LessonAiEntrySide = "left" | "right";

type EntryBounds = {
  leftMax: number;
  leftMin: number;
  topMax: number;
  topMin: number;
};

const dragThreshold = 7;
const defaultTopRatio = 0.56;
const idleImageBySide: Record<LessonAiEntrySide, string> = {
  left: "/assets/brand/cloud-mascot-ai-chat-edge-left-ui.webp",
  right: "/assets/brand/cloud-mascot-ai-chat-edge-ui.webp"
};
const activeImageByState: Record<Exclude<LessonAiEntryState, "idle">, string> = {
  pressed: "/assets/brand/cloud-mascot-ai-chat-edge-pressed-ui.webp",
  dragging: "/assets/brand/cloud-mascot-ai-chat-airborne-ui.webp"
};

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function readCssNumber(style: CSSStyleDeclaration, property: string) {
  const values = style.getPropertyValue(property).match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? [];
  return values.length > 0 ? Math.max(...values) : 0;
}

function getEntryBounds(
  container: HTMLElement,
  entry: HTMLButtonElement,
  avoidCompletionAction: boolean
): EntryBounds {
  const containerBounds = container.getBoundingClientRect();
  const style = getComputedStyle(container);
  const visualViewport = window.visualViewport;
  const visibleLeft = clamp(
    (visualViewport?.offsetLeft ?? 0) - containerBounds.left,
    0,
    containerBounds.width
  );
  const visibleRight = clamp(
    visibleLeft + (visualViewport?.width ?? containerBounds.width),
    0,
    containerBounds.width
  );
  const visibleTop = clamp(
    (visualViewport?.offsetTop ?? 0) - containerBounds.top,
    0,
    containerBounds.height
  );
  const visibleBottom = clamp(
    visibleTop + (visualViewport?.height ?? containerBounds.height),
    0,
    containerBounds.height
  );
  const safeTop = readCssNumber(style, "--safe-area-top");
  const safeRight = readCssNumber(style, "--safe-area-right");
  const safeBottom = readCssNumber(style, "--safe-area-bottom");
  const safeLeft = readCssNumber(style, "--safe-area-left");
  const header = container.querySelector<HTMLElement>(".header-bar");
  const headerBottom = header && header.getClientRects().length > 0
    ? header.getBoundingClientRect().bottom - containerBounds.top
    : visibleTop + safeTop;
  const topMin = Math.max(visibleTop + safeTop + 12, headerBottom + 12);
  const completionAction = avoidCompletionAction
    ? container.querySelector<HTMLElement>(".lesson-floating-complete")
    : null;
  const completionTop = completionAction && completionAction.getClientRects().length > 0
    ? completionAction.getBoundingClientRect().top - containerBounds.top - 12
    : Number.POSITIVE_INFINITY;
  const availableBottom = Math.min(visibleBottom - safeBottom - 16, completionTop);
  const topMax = Math.max(topMin, availableBottom - entry.offsetHeight);
  const leftMin = Math.max(visibleLeft, visibleLeft + safeLeft - 1);
  const leftMax = Math.max(
    leftMin,
    visibleRight - safeRight - entry.offsetWidth + 1
  );
  return { leftMax, leftMin, topMax, topMin };
}

export function LessonAiChatEntry({
  avoidCompletionAction,
  containerElement,
  onOpen
}: {
  avoidCompletionAction: boolean;
  containerElement: HTMLElement;
  onOpen: (origin: HTMLButtonElement) => void;
}) {
  const instructionsId = useId();
  const entryRef = useRef<HTMLButtonElement | null>(null);
  const currentLeftRef = useRef(0);
  const currentTopRef = useRef(0);
  const sideRef = useRef<LessonAiEntrySide>("right");
  const positionedRef = useRef(false);
  const suppressClickRef = useRef(false);
  const dragFrameRef = useRef<number | null>(null);
  const settleFrameRef = useRef<number | null>(null);
  const settleTimerRef = useRef<number | null>(null);
  const pendingDragRef = useRef({ x: 0, y: 0 });
  const dragRef = useRef({
    baseLeft: 0,
    baseSide: "right" as LessonAiEntrySide,
    baseTop: 0,
    bounds: { leftMax: 0, leftMin: 0, topMax: 0, topMin: 0 },
    moved: false,
    pointerId: -1,
    startX: 0,
    startY: 0
  });
  const [interaction, setInteraction] = useState<LessonAiEntryState>("idle");
  const [positioned, setPositioned] = useState(false);
  const [side, setSideState] = useState<LessonAiEntrySide>("right");
  const [left, setLeft] = useState(0);
  const [top, setTop] = useState(0);

  const setSide = useCallback((next: LessonAiEntrySide) => {
    sideRef.current = next;
    setSideState(next);
  }, []);

  const clearSettleTransform = useCallback(() => {
    if (settleFrameRef.current !== null) {
      window.cancelAnimationFrame(settleFrameRef.current);
      settleFrameRef.current = null;
    }
    if (settleTimerRef.current !== null) {
      window.clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }
    const entry = entryRef.current;
    if (!entry) return;
    entry.removeAttribute("data-relocating");
    entry.removeAttribute("data-settling");
    entry.style.setProperty("--lesson-ai-entry-settle-x", "0px");
  }, []);

  const writeDragTransform = useCallback(() => {
    dragFrameRef.current = null;
    const entry = entryRef.current;
    if (!entry) return;
    entry.style.setProperty("--lesson-ai-entry-drag-x", `${pendingDragRef.current.x}px`);
    entry.style.setProperty("--lesson-ai-entry-drag-y", `${pendingDragRef.current.y}px`);
  }, []);

  const scheduleDragTransform = useCallback((nextX: number, nextY: number) => {
    pendingDragRef.current = { x: nextX, y: nextY };
    if (dragFrameRef.current !== null) return;
    dragFrameRef.current = window.requestAnimationFrame(writeDragTransform);
  }, [writeDragTransform]);

  const clearDragTransform = useCallback(() => {
    if (dragFrameRef.current !== null) {
      window.cancelAnimationFrame(dragFrameRef.current);
      dragFrameRef.current = null;
    }
    pendingDragRef.current = { x: 0, y: 0 };
    const entry = entryRef.current;
    if (!entry) return;
    entry.style.setProperty("--lesson-ai-entry-drag-x", "0px");
    entry.style.setProperty("--lesson-ai-entry-drag-y", "0px");
  }, []);

  const syncPosition = useCallback(() => {
    const entry = entryRef.current;
    if (!entry) return;
    clearSettleTransform();
    clearDragTransform();
    dragRef.current.pointerId = -1;
    dragRef.current.moved = false;
    setInteraction("idle");
    const bounds = getEntryBounds(containerElement, entry, avoidCompletionAction);
    const defaultTop = bounds.topMin + ((bounds.topMax - bounds.topMin) * defaultTopRatio);
    const nextTop = positionedRef.current
      ? clamp(currentTopRef.current, bounds.topMin, bounds.topMax)
      : defaultTop;
    const nextLeft = sideRef.current === "left" ? bounds.leftMin : bounds.leftMax;
    positionedRef.current = true;
    currentLeftRef.current = nextLeft;
    currentTopRef.current = nextTop;
    setLeft(nextLeft);
    setTop(nextTop);
    setPositioned(true);
  }, [avoidCompletionAction, clearDragTransform, clearSettleTransform, containerElement]);

  useEffect(() => {
    [
      ...Object.values(idleImageBySide),
      ...Object.values(activeImageByState)
    ].forEach((source) => {
      const image = new Image();
      image.decoding = "async";
      image.src = source;
    });
  }, []);

  useLayoutEffect(() => {
    syncPosition();
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(syncPosition);
    resizeObserver?.observe(containerElement);
    const header = containerElement.querySelector<HTMLElement>(".header-bar");
    const completionAction = containerElement.querySelector<HTMLElement>(".lesson-floating-complete");
    if (header) resizeObserver?.observe(header);
    if (completionAction) resizeObserver?.observe(completionAction);
    const visualViewport = window.visualViewport;
    visualViewport?.addEventListener("resize", syncPosition);
    visualViewport?.addEventListener("scroll", syncPosition);
    window.addEventListener("resize", syncPosition);
    window.addEventListener("orientationchange", syncPosition);

    return () => {
      resizeObserver?.disconnect();
      visualViewport?.removeEventListener("resize", syncPosition);
      visualViewport?.removeEventListener("scroll", syncPosition);
      window.removeEventListener("resize", syncPosition);
      window.removeEventListener("orientationchange", syncPosition);
    };
  }, [containerElement, syncPosition]);

  useEffect(() => () => {
    if (dragFrameRef.current !== null) window.cancelAnimationFrame(dragFrameRef.current);
    if (settleFrameRef.current !== null) window.cancelAnimationFrame(settleFrameRef.current);
    if (settleTimerRef.current !== null) window.clearTimeout(settleTimerRef.current);
  }, []);

  function handlePointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    clearSettleTransform();
    clearDragTransform();
    suppressClickRef.current = false;
    const bounds = getEntryBounds(containerElement, event.currentTarget, avoidCompletionAction);
    dragRef.current = {
      baseLeft: currentLeftRef.current,
      baseSide: sideRef.current,
      baseTop: currentTopRef.current,
      bounds,
      moved: false,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setInteraction("pressed");
  }

  function handlePointerMove(event: PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    if (drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) > dragThreshold) {
      drag.moved = true;
      setInteraction("dragging");
    }
    if (!drag.moved) return;
    const nextLeft = clamp(drag.baseLeft + dx, drag.bounds.leftMin, drag.bounds.leftMax);
    const nextTop = clamp(drag.baseTop + dy, drag.bounds.topMin, drag.bounds.topMax);
    const midpoint = (drag.bounds.leftMin + drag.bounds.leftMax + event.currentTarget.offsetWidth) / 2;
    const previewSide: LessonAiEntrySide = nextLeft + event.currentTarget.offsetWidth / 2 < midpoint
      ? "left"
      : "right";
    if (previewSide !== sideRef.current) setSide(previewSide);
    scheduleDragTransform(nextLeft - drag.baseLeft, nextTop - drag.baseTop);
  }

  function handlePointerUp(event: PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    if (drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const moved = drag.moved;
    const releasedLeft = clamp(
      drag.baseLeft + pendingDragRef.current.x,
      drag.bounds.leftMin,
      drag.bounds.leftMax
    );
    const releasedTop = clamp(
      drag.baseTop + pendingDragRef.current.y,
      drag.bounds.topMin,
      drag.bounds.topMax
    );
    dragRef.current.pointerId = -1;
    dragRef.current.moved = false;
    suppressClickRef.current = moved;
    if (!moved) {
      clearDragTransform();
      setSide(sideRef.current);
      setInteraction("idle");
      return;
    }

    const midpoint = (drag.bounds.leftMin + drag.bounds.leftMax + event.currentTarget.offsetWidth) / 2;
    const nextSide: LessonAiEntrySide = releasedLeft + event.currentTarget.offsetWidth / 2 < midpoint
      ? "left"
      : "right";
    const snappedLeft = nextSide === "left" ? drag.bounds.leftMin : drag.bounds.leftMax;
    currentLeftRef.current = snappedLeft;
    currentTopRef.current = releasedTop;
    event.currentTarget.setAttribute("data-relocating", "true");
    event.currentTarget.style.left = `${snappedLeft}px`;
    event.currentTarget.style.top = `${releasedTop}px`;
    event.currentTarget.style.setProperty("--lesson-ai-entry-drag-x", "0px");
    event.currentTarget.style.setProperty("--lesson-ai-entry-drag-y", "0px");
    event.currentTarget.style.setProperty(
      "--lesson-ai-entry-settle-x",
      `${releasedLeft - snappedLeft}px`
    );
    pendingDragRef.current = { x: 0, y: 0 };
    setLeft(snappedLeft);
    setTop(releasedTop);
    setSide(nextSide);
    setInteraction("idle");

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      event.currentTarget.style.setProperty("--lesson-ai-entry-settle-x", "0px");
      event.currentTarget.removeAttribute("data-relocating");
      return;
    }
    event.currentTarget.setAttribute("data-settling", "true");
    void event.currentTarget.offsetWidth;
    event.currentTarget.removeAttribute("data-relocating");
    settleFrameRef.current = window.requestAnimationFrame(() => {
      settleFrameRef.current = null;
      const entry = entryRef.current;
      if (!entry) return;
      entry.style.setProperty("--lesson-ai-entry-settle-x", "0px");
      settleTimerRef.current = window.setTimeout(() => {
        settleTimerRef.current = null;
        entryRef.current?.removeAttribute("data-settling");
      }, 220);
    });
  }

  function handlePointerCancel(event: PointerEvent<HTMLButtonElement>) {
    if (dragRef.current.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current.pointerId = -1;
    dragRef.current.moved = false;
    suppressClickRef.current = false;
    clearDragTransform();
    setSide(dragRef.current.baseSide);
    setInteraction("idle");
  }

  function handleClick(event: MouseEvent<HTMLButtonElement>) {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    setInteraction("idle");
    onOpen(event.currentTarget);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Enter" || event.key === " ") setInteraction("pressed");
  }

  function handleKeyUp(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Enter" || event.key === " ") setInteraction("idle");
  }

  const imageSource = interaction === "idle"
    ? idleImageBySide[side]
    : activeImageByState[interaction];

  return (
    <button
      ref={entryRef}
      className="lesson-ai-entry"
      type="button"
      aria-describedby={instructionsId}
      aria-haspopup="dialog"
      aria-label={interaction === "dragging" ? "正在拖动当前章节 AI 助手入口" : "打开当前章节 AI 助手"}
      data-interaction={interaction}
      data-mouse-drag-scroll="ignore"
      data-positioned={positioned ? "true" : "false"}
      data-side={side}
      style={{ left, top }}
      title="问 AI（按住可拖到屏幕任一侧）"
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      onKeyUp={handleKeyUp}
      onPointerCancel={handlePointerCancel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
    >
      <img
        src={imageSource}
        alt=""
        aria-hidden="true"
        decoding="async"
        draggable={false}
      />
      <span id={instructionsId} className="lesson-ai-entry-instructions">
        点击打开基于当前章节的 AI 对话，按住后可拖动并吸附到屏幕左侧或右侧。
      </span>
    </button>
  );
}
