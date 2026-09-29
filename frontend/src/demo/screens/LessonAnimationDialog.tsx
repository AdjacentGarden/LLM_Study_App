import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  type AnimationEvent as ReactAnimationEvent,
  type RefObject
} from "react";
import { CirclePlay, PlayCircle, Sparkles, X } from "lucide-react";
import type {
  MotionAnimationEvent,
  MotionState
} from "../motion";
import type { LessonAnimation } from "./lessonAnimations";

export const lessonAnimationDialogMotionNames = [
  "motion-dialog-lesson-animation-in",
  "motion-dialog-lesson-animation-out"
] as const;

export function LessonAnimationDialog({
  animation,
  originRef,
  presenceId,
  state,
  onClose,
  onAnimationEnd,
  onAnimationCancel
}: {
  animation: LessonAnimation;
  originRef: RefObject<HTMLButtonElement | null>;
  presenceId: number;
  state: MotionState;
  onClose: () => void;
  onAnimationEnd: (event: MotionAnimationEvent) => void;
  onAnimationCancel: (event: MotionAnimationEvent) => void;
}) {
  const layerRef = useRef<HTMLDialogElement | null>(null);
  const surfaceRef = useRef<HTMLElement | null>(null);
  const sharedSurfaceRef = useRef<HTMLDivElement | null>(null);
  const sharedOriginRef = useRef<HTMLSpanElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  useLayoutEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    if (!layer.open) layer.showModal();
    return () => {
      if (layer.open) layer.close();
    };
  }, []);

  useLayoutEffect(() => {
    const layer = layerRef.current;
    const surface = surfaceRef.current;
    const sharedSurface = sharedSurfaceRef.current;
    const sharedOrigin = sharedOriginRef.current;
    const origin = originRef.current;
    if (!layer || !surface || !sharedSurface || !sharedOrigin || !origin) return;

    sharedSurface.removeAttribute("data-motion-ready");
    sharedOrigin.removeAttribute("data-motion-ready");
    if (state === "idle") return;

    const layerBounds = layer.getBoundingClientRect();
    const surfaceBounds = surface.getBoundingClientRect();
    const originBounds = origin.getBoundingClientRect();
    if (
      surfaceBounds.width <= 0
      || surfaceBounds.height <= 0
      || originBounds.width <= 0
      || originBounds.height <= 0
    ) return;

    const targetLeft = surfaceBounds.left - layerBounds.left;
    const targetTop = surfaceBounds.top - layerBounds.top;
    const sourceLeft = originBounds.left - layerBounds.left;
    const sourceTop = originBounds.top - layerBounds.top;
    const surfaceStyle = getComputedStyle(surface);
    const originStyle = getComputedStyle(origin);

    Object.assign(sharedSurface.style, {
      left: `${targetLeft}px`,
      top: `${targetTop}px`,
      width: `${surfaceBounds.width}px`,
      height: `${surfaceBounds.height}px`
    });
    sharedSurface.style.setProperty("--lesson-animation-shared-delta-x", `${sourceLeft - targetLeft}px`);
    sharedSurface.style.setProperty("--lesson-animation-shared-delta-y", `${sourceTop - targetTop}px`);
    sharedSurface.style.setProperty("--lesson-animation-shared-scale-x", String(originBounds.width / surfaceBounds.width));
    sharedSurface.style.setProperty("--lesson-animation-shared-scale-y", String(originBounds.height / surfaceBounds.height));
    sharedSurface.style.setProperty("--lesson-animation-target-radius", surfaceStyle.borderRadius || "24px");
    sharedSurface.style.setProperty("--lesson-animation-target-background", surfaceStyle.backgroundColor || "#ffffff");
    sharedSurface.style.setProperty("--lesson-animation-target-border", surfaceStyle.borderColor || "transparent");
    sharedSurface.style.setProperty("--lesson-animation-origin-radius", originStyle.borderRadius || "9px");

    Object.assign(sharedOrigin.style, {
      left: `${sourceLeft}px`,
      top: `${sourceTop}px`,
      width: `${originBounds.width}px`,
      height: `${originBounds.height}px`
    });

    void sharedSurface.offsetWidth;
    sharedSurface.setAttribute("data-motion-ready", "true");
    sharedOrigin.setAttribute("data-motion-ready", "true");
  }, [originRef, presenceId, state]);

  useEffect(() => {
    const focusFrame = window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus({ preventScroll: true });
      void videoRef.current?.play().catch(() => undefined);
    });
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";

    const closeFromNativeBack = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    window.addEventListener("bookcourse:native-back", closeFromNativeBack);

    return () => {
      window.cancelAnimationFrame(focusFrame);
      window.removeEventListener("bookcourse:native-back", closeFromNativeBack);
      document.documentElement.style.overflow = previousOverflow;
      videoRef.current?.pause();
      const origin = originRef.current;
      if (origin?.isConnected) {
        window.requestAnimationFrame(() => origin.focus({ preventScroll: true }));
      }
    };
  }, [animation.animationId, onClose, originRef]);

  useEffect(() => {
    if (state === "closing") videoRef.current?.pause();
  }, [state]);

  const settleDialogAnimation = (
    event: ReactAnimationEvent<HTMLElement> | AnimationEvent,
    settle: (event: MotionAnimationEvent) => void
  ) => {
    const expectedName = state === "entering"
      ? "motion-dialog-lesson-animation-in"
      : state === "closing"
        ? "motion-dialog-lesson-animation-out"
        : null;
    if (!expectedName || event.animationName !== expectedName) return;
    settle(event);
  };

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const handleAnimationCancel = (event: AnimationEvent) => {
      settleDialogAnimation(event, onAnimationCancel);
    };
    surface.addEventListener("animationcancel", handleAnimationCancel);
    return () => surface.removeEventListener("animationcancel", handleAnimationCancel);
  });

  return (
    <dialog
      ref={layerRef}
      id="lesson-animation-dialog"
      className="lesson-animation-layer"
      data-motion-state={state}
      data-motion-presence={presenceId}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      aria-busy={state === "closing" ? true : undefined}
      onCancel={(event) => {
        event.preventDefault();
        if (state !== "closing") onClose();
      }}
    >
      <button
        className="lesson-animation-scrim"
        data-motion-state={state}
        type="button"
        aria-label="关闭教学动画背景"
        onClick={() => {
          if (state !== "closing") onClose();
        }}
      />
      <div
        key={`lesson-animation-shared:${presenceId}`}
        ref={sharedSurfaceRef}
        className="lesson-animation-shared-surface"
        data-motion-state={state}
        aria-hidden="true"
      />
      <span
        key={`lesson-animation-origin:${presenceId}`}
        ref={sharedOriginRef}
        className="lesson-animation-shared-origin"
        data-motion-state={state}
        aria-hidden="true"
      >
        <CirclePlay size={17} aria-hidden="true" />
      </span>

      <div className="lesson-animation-positioner">
        <article
          key={`lesson-animation-panel:${presenceId}`}
          ref={surfaceRef}
          className="lesson-animation-surface"
          data-motion-state={state}
          onAnimationEnd={(event) => settleDialogAnimation(event, onAnimationEnd)}
        >
          <header className="lesson-animation-head">
            <div className="lesson-animation-heading">
              <span><Sparkles size={15} aria-hidden="true" />AI 教学动画 · {animation.durationLabel}</span>
              <h2 id={titleId}>{animation.title}</h2>
            </div>
            <button
              ref={closeButtonRef}
              className="lesson-animation-close"
              type="button"
              aria-label="关闭教学动画"
              onClick={onClose}
            >
              <X size={21} aria-hidden="true" />
            </button>
          </header>

          <div className="lesson-animation-player">
            <video
              ref={videoRef}
              controls
              autoPlay
              muted
              playsInline
              preload="metadata"
              poster={animation.posterUrl}
              aria-label={`${animation.title}视频`}
            >
              <source src={animation.videoUrl} type="video/mp4" />
              当前设备暂不支持视频播放。
            </video>
            <span className="lesson-animation-duration" aria-hidden="true">
              <PlayCircle size={14} />{animation.durationLabel}
            </span>
          </div>

          <div className="lesson-animation-copy">
            <h3>一次复制，两次分裂</h3>
            <p id={descriptionId}>{animation.description}</p>
          </div>
        </article>
      </div>
    </dialog>
  );
}
