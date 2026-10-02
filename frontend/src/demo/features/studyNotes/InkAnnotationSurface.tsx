import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Ref,
  type ReactNode,
  type PointerEvent as ReactPointerEvent
} from "react";
import { Minus, Plus, Scan } from "lucide-react";
import { containImageRect, drawInkStrokes, normalizedInkPoint, strokeHitTest } from "./ink";
import { pageSwipeDirection, type PageSwipeDirection } from "./pageSwipe";
import { isRegionUsable, normalizeRegion, type PageRegion } from "./regionAsk";
import type { InkStroke, InkTool } from "./types";

type AnnotationTool = InkTool | "eraser";

export function InkAnnotationSurface({
  active,
  alt,
  color,
  fingerWrite,
  imageRef,
  imageState,
  imageUrl,
  onChange,
  onAnimationEnd,
  onError,
  onLoad,
  onPageSwipe,
  onRegionCommit,
  onSelectText,
  pageOverlay,
  region,
  regionMode = false,
  sourceText,
  strokes,
  tool,
  width
}: {
  active: boolean;
  alt: string;
  color: string;
  fingerWrite: boolean;
  imageRef?: Ref<HTMLImageElement>;
  imageState?: string;
  imageUrl: string;
  onChange: (strokes: InkStroke[]) => void;
  onAnimationEnd?: () => void;
  onError: () => void;
  onLoad: () => void;
  onPageSwipe?: (direction: PageSwipeDirection) => void;
  /** Called with the normalized rectangle once the pen lifts off a usable selection. */
  onRegionCommit?: (region: PageRegion) => void;
  onSelectText?: (text: string) => void;
  pageOverlay?: ReactNode;
  /** Selection kept on screen after the dialog opened. */
  region?: PageRegion | null;
  /** The pen draws a dashed rectangle instead of ink. */
  regionMode?: boolean;
  sourceText?: string;
  strokes: InkStroke[];
  tool: AnnotationTool;
  width: number;
}) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const draftStrokeRef = useRef<InkStroke | null>(null);
  const strokesRef = useRef(strokes);
  const pointersRef = useRef(new Map<number, { x: number; y: number; type: string }>());
  const gestureRef = useRef<{
    distance: number;
    scale: number;
    translateX: number;
    translateY: number;
    midpointX: number;
    midpointY: number;
  } | null>(null);
  const panRef = useRef<{ x: number; y: number; translateX: number; translateY: number } | null>(null);
  const swipeStartRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const regionStartRef = useRef<{ x: number; y: number } | null>(null);
  const draftRegionRef = useRef<PageRegion | null>(null);
  const [transform, setTransform] = useState({ scale: 1, translateX: 0, translateY: 0 });
  const [draftRegion, setDraftRegion] = useState<PageRegion | null>(null);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  strokesRef.current = strokes;
  draftRegionRef.current = draftRegion;

  const pageRect = containImageRect(viewportSize.width, viewportSize.height, imageSize.width, imageSize.height);
  const pageStyle = {
    left: `${pageRect.left}px`,
    top: `${pageRect.top}px`,
    width: pageRect.width ? `${pageRect.width}px` : "100%",
    height: pageRect.height ? `${pageRect.height}px` : "100%"
  };

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect();
    const scale = window.devicePixelRatio || 1;
    const cssWidth = Math.max(1, bounds.width / transform.scale);
    const cssHeight = Math.max(1, bounds.height / transform.scale);
    const pixelWidth = Math.round(cssWidth * scale);
    const pixelHeight = Math.round(cssHeight * scale);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(scale, 0, 0, scale, 0, 0);
    drawInkStrokes(
      context,
      draftStrokeRef.current ? [...strokesRef.current, draftStrokeRef.current] : strokesRef.current,
      cssWidth,
      cssHeight
    );
  }, [transform.scale]);

  useEffect(() => {
    redraw();
  }, [redraw, strokes]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      setViewportSize({ width: viewport.clientWidth, height: viewport.clientHeight });
      redraw();
    });
    observer.observe(viewport);
    setViewportSize({ width: viewport.clientWidth, height: viewport.clientHeight });
    return () => observer.disconnect();
  }, [redraw]);

  useEffect(() => {
    redraw();
  }, [imageSize, viewportSize, redraw]);

  function captureSelectedText() {
    if (active || !onSelectText) return;
    const selection = window.getSelection();
    const text = selection?.toString().trim().slice(0, 800) ?? "";
    if (text) onSelectText(text);
  }

  function canvasBounds() {
    return canvasRef.current?.getBoundingClientRect();
  }

  function shouldDraw(event: ReactPointerEvent<HTMLCanvasElement>) {
    return event.pointerType === "pen" || (event.pointerType !== "touch" && event.button === 0) || fingerWrite;
  }

  /**
   * Region selection is a pen (or mouse) gesture on purpose: a finger keeps
   * panning, zooming and page swipes in every mode.
   */
  function isRegionPointer(event: ReactPointerEvent<HTMLCanvasElement>) {
    return event.pointerType === "pen" || (event.pointerType === "mouse" && event.button === 0);
  }

  function beginGesture() {
    const touches = Array.from(pointersRef.current.values()).filter((pointer) => pointer.type === "touch");
    if (touches.length < 2) return;
    draftStrokeRef.current = null;
    swipeStartRef.current = null;
    const first = touches[0];
    const second = touches[1];
    gestureRef.current = {
      distance: Math.max(1, Math.hypot(second.x - first.x, second.y - first.y)),
      midpointX: (first.x + second.x) / 2,
      midpointY: (first.y + second.y) / 2,
      ...transform
    };
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (!active) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY, type: event.pointerType });
    const touchCount = Array.from(pointersRef.current.values()).filter((pointer) => pointer.type === "touch").length;
    if (touchCount >= 2) {
      beginGesture();
      redraw();
      return;
    }

    if (!shouldDraw(event)) {
      if (regionMode && event.pointerType !== "touch") return;
      if (transform.scale <= 1.01 && event.pointerType === "touch") {
        swipeStartRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
        panRef.current = null;
      } else {
        swipeStartRef.current = null;
        panRef.current = {
          x: event.clientX,
          y: event.clientY,
          translateX: transform.translateX,
          translateY: transform.translateY
        };
      }
      return;
    }

    const bounds = canvasBounds();
    if (!bounds) return;
    const point = normalizedInkPoint(event.clientX, event.clientY, bounds, event.pressure, event.timeStamp);
    if (regionMode) {
      if (!isRegionPointer(event)) return;
      regionStartRef.current = { x: point.x, y: point.y };
      setDraftRegion({ x: point.x, y: point.y, width: 0, height: 0 });
      return;
    }
    if (tool === "eraser") {
      const next = strokesRef.current.filter((stroke) => !strokeHitTest(stroke, point));
      if (next.length !== strokesRef.current.length) onChange(next);
      return;
    }
    draftStrokeRef.current = {
      id: `stroke-${Date.now()}-${event.pointerId}`,
      tool,
      color,
      width,
      opacity: tool === "highlighter" ? 0.28 : 0.92,
      points: [point]
    };
    redraw();
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (!active || !pointersRef.current.has(event.pointerId)) return;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY, type: event.pointerType });
    const touches = Array.from(pointersRef.current.values()).filter((pointer) => pointer.type === "touch");
    if (touches.length >= 2 && gestureRef.current) {
      const first = touches[0];
      const second = touches[1];
      const distance = Math.max(1, Math.hypot(second.x - first.x, second.y - first.y));
      const midpointX = (first.x + second.x) / 2;
      const midpointY = (first.y + second.y) / 2;
      const nextScale = Math.min(3, Math.max(1, gestureRef.current.scale * distance / gestureRef.current.distance));
      setTransform({
        scale: nextScale,
        translateX: gestureRef.current.translateX + midpointX - gestureRef.current.midpointX,
        translateY: gestureRef.current.translateY + midpointY - gestureRef.current.midpointY
      });
      return;
    }

    if (panRef.current && !draftStrokeRef.current) {
      setTransform((current) => ({
        ...current,
        translateX: panRef.current!.translateX + event.clientX - panRef.current!.x,
        translateY: panRef.current!.translateY + event.clientY - panRef.current!.y
      }));
      return;
    }

    const bounds = canvasBounds();
    if (!bounds) return;
    const point = normalizedInkPoint(event.clientX, event.clientY, bounds, event.pressure, event.timeStamp);
    if (regionMode) {
      const start = regionStartRef.current;
      if (!start) return;
      setDraftRegion(normalizeRegion(start, point));
      return;
    }
    if (tool === "eraser") {
      const next = strokesRef.current.filter((stroke) => !strokeHitTest(stroke, point));
      if (next.length !== strokesRef.current.length) onChange(next);
      return;
    }
    if (!draftStrokeRef.current) return;
    draftStrokeRef.current.points.push(point);
    redraw();
  }

  function endPointer(event: ReactPointerEvent<HTMLCanvasElement>) {
    const swipeStart = swipeStartRef.current;
    const swipe = event.type === "pointerup" && swipeStart?.pointerId === event.pointerId && transform.scale <= 1.01
      ? pageSwipeDirection(event.clientX - swipeStart.x, event.clientY - swipeStart.y, viewportSize.width)
      : null;
    pointersRef.current.delete(event.pointerId);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (pointersRef.current.size < 2) gestureRef.current = null;
    panRef.current = null;
    swipeStartRef.current = null;
    const regionStart = regionStartRef.current;
    regionStartRef.current = null;
    if (regionMode) {
      const selection = draftRegionRef.current;
      setDraftRegion(null);
      if (regionStart && selection && isRegionUsable(selection)) onRegionCommit?.(selection);
      return;
    }
    const draft = draftStrokeRef.current;
    draftStrokeRef.current = null;
    if (draft && draft.points.length > 0) onChange([...strokesRef.current, draft]);
    if (swipe) onPageSwipe?.(swipe);
    redraw();
  }

  function zoom(nextScale: number) {
    setTransform((current) => ({
      ...current,
      scale: Math.min(3, Math.max(1, nextScale)),
      translateX: nextScale <= 1 ? 0 : current.translateX,
      translateY: nextScale <= 1 ? 0 : current.translateY
    }));
  }

  const visibleRegion = draftRegion ?? region ?? null;

  return (
    <div
      ref={viewportRef}
      className="source-annotation-viewport"
      data-annotation-active={active ? "true" : "false"}
      data-region-mode={regionMode ? "true" : "false"}
    >
      <div
        className="source-annotation-transform"
        style={{ transform: `translate(${transform.translateX}px, ${transform.translateY}px) scale(${transform.scale})` }}
      >
        <div className="source-annotation-page" style={pageStyle}>
        <img
          ref={imageRef}
          className="source-page-image"
          data-motion-image-state={imageState}
          src={imageUrl}
          alt={alt}
          onLoad={(event) => {
            setImageSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight });
            onLoad();
          }}
          onError={onError}
          onAnimationEnd={(event) => {
            if (event.animationName === "motion-stage3-image-in") onAnimationEnd?.();
          }}
        />
        <canvas
          ref={canvasRef}
          className="source-annotation-canvas"
          aria-label="教材手写批注画布"
          data-active={active ? "true" : "false"}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endPointer}
          onPointerCancel={endPointer}
        />
        {!active && sourceText?.trim() ? (
          <div
            className="source-page-text-layer is-reader"
            role="region"
            aria-label="教材原文可选摘录"
            tabIndex={0}
            onPointerUp={captureSelectedText}
            onKeyUp={captureSelectedText}
          >
            {sourceText.split(/\n{2,}/u).map((paragraph, index) => <p key={`${index}:${paragraph.slice(0, 24)}`}>{paragraph.trim()}</p>)}
          </div>
        ) : null}
        {pageOverlay}
        {visibleRegion && visibleRegion.width > 0 && visibleRegion.height > 0 ? (
          <div
            className="source-region-selection"
            data-region-state={draftRegion ? "drawing" : "committed"}
            aria-hidden="true"
            style={{
              left: `${visibleRegion.x * 100}%`,
              top: `${visibleRegion.y * 100}%`,
              width: `${visibleRegion.width * 100}%`,
              height: `${visibleRegion.height * 100}%`
            }}
          />
        ) : null}
        </div>
      </div>
      {active ? (
        <div className="source-annotation-zoom" aria-label="批注画布缩放">
          <button type="button" aria-label="缩小画布" disabled={transform.scale <= 1} onClick={() => zoom(transform.scale - 0.25)}><Minus size={18} /></button>
          <span>{Math.round(transform.scale * 100)}%</span>
          <button type="button" aria-label="放大画布" disabled={transform.scale >= 3} onClick={() => zoom(transform.scale + 0.25)}><Plus size={18} /></button>
          <button type="button" aria-label="重置画布位置" onClick={() => setTransform({ scale: 1, translateX: 0, translateY: 0 })}><Scan size={18} /></button>
        </div>
      ) : null}
    </div>
  );
}
