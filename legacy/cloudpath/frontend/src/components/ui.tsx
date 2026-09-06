import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ButtonHTMLAttributes, type ComponentPropsWithoutRef, type FormEvent, type KeyboardEvent, type MouseEvent, type PointerEvent, type ReactNode, type RefObject, type SyntheticEvent } from "react";
import {
  ArrowLeft,
  BookOpenCheck,
  Bot,
  CheckCircle2,
  FileText,
  Home,
  Loader2,
  MessageCircle,
  SendHorizontal,
  Upload,
  User,
  UsersRound,
  X
} from "lucide-react";
import type { Screen, SheetState, ToastMessage } from "../types/app";
import type { AssistantHistoryMessage } from "../types/api";
import { globalMotionFallbackMs, localSlowMotionDurationSeconds, StateSwapText, useImageMotion, useMotionPresence, useReducedMotion, type MotionAnimationEvent, type MotionState } from "../motion";
import { PadChrome } from "../layouts/PadChrome";
import { PhoneChrome } from "../layouts/PhoneChrome";
import { useDeviceLayout } from "../layouts/useDeviceLayout";
import { useMouseDragScroll } from "../hooks/useMouseDragScroll";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { IosStatusBar } from "./IosStatusBar";

export const actionSheetAnimationNames = [
  "motion-sheet-phone-in",
  "motion-sheet-phone-out",
  "motion-sheet-short-in",
  "motion-sheet-short-out",
  "motion-panel-tablet-in",
  "motion-panel-tablet-out",
  "motion-dialog-source-in",
  "motion-dialog-source-out",
  "motion-dialog-center-in",
  "motion-dialog-center-out"
] as const;

const aiDialogAnimationNames = [
  "motion-dialog-ai-phone-in",
  "motion-dialog-ai-phone-out",
  "motion-dialog-ai-short-in",
  "motion-dialog-ai-short-out",
  "motion-dialog-ai-tablet-in",
  "motion-dialog-ai-tablet-out"
] as const;

const toastAnimationNames = ["motion-toast-in", "motion-toast-out"] as const;

function getToastKey(toast: ToastMessage) {
  return String(toast.id);
}

export type ActionSheetView = Readonly<{
  content: ReactNode;
  key: string;
  sheet: Readonly<Exclude<SheetState, null>>;
  title: string;
}>;

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "text" | "danger";
  icon?: ReactNode;
  loading?: boolean;
  motionStatus?: "idle" | "loading" | "success";
  statusText?: Readonly<{
    loading?: string;
    success?: string;
  }>;
};

export function Button({
  variant = "primary",
  icon,
  loading,
  motionStatus,
  statusText,
  className = "",
  children,
  ...props
}: ButtonProps) {
  const defaultText = typeof children === "string" ? children : null;
  const resolvedMotionStatus = motionStatus ?? (loading ? "loading" : "idle");
  const resolvedText = defaultText === null
    ? null
    : resolvedMotionStatus === "loading"
      ? statusText?.loading ?? defaultText
      : resolvedMotionStatus === "success"
        ? statusText?.success ?? defaultText
        : defaultText;
  const reserveValues = defaultText === null
    ? []
    : [
      defaultText,
      statusText?.loading ?? defaultText,
      statusText?.success ?? defaultText
    ];
  const shouldUseStateSwap = motionStatus !== undefined || loading === true;

  return (
    <button className={`button button-${variant} ${className}`} type={props.type ?? "button"} {...props}>
      {loading ? <Loader2 className="spin" size={18} aria-hidden="true" /> : icon}
      <span>
        {resolvedText === null || !shouldUseStateSwap ? children : (
          <StateSwapText
            value={resolvedText}
            reserveValues={reserveValues}
          />
        )}
      </span>
    </button>
  );
}

export function IconButton({
  label,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode }) {
  return (
    <button className={`icon-button ${className}`} type={props.type ?? "button"} aria-label={label} {...props}>
      {children}
    </button>
  );
}

export type CardSurface = "default" | "elevated" | "celebration";

export function Card({
  children,
  className = "",
  surface = "default",
  ...props
}: ComponentPropsWithoutRef<"section"> & { children: ReactNode; surface?: CardSurface }) {
  return (
    <section
      className={`card card-surface-${surface} ${className}`}
      data-surface={surface}
      {...props}
    >
      {children}
    </section>
  );
}

export function Section({
  title,
  action,
  children,
  className = ""
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`section ${className}`}>
      <div className="section-head">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Pill({ children, tone = "purple" }: { children: ReactNode; tone?: string }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const clampedValue = Math.max(0, Math.min(100, value));

  return (
    <div
      className="progress-wrap"
      role="progressbar"
      aria-label={label ?? `进度 ${clampedValue}%`}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clampedValue}
      aria-valuetext={label}
    >
      <div className="progress-track" aria-hidden="true">
        <div className="progress-fill" style={{ transform: `scaleX(${clampedValue / 100})` }} />
      </div>
      {label ? <span>{label}</span> : null}
    </div>
  );
}

export function HeaderBar({
  title,
  subtitle,
  showBack,
  onBack,
  rightAction
}: {
  title: string;
  subtitle?: string;
  showBack?: boolean;
  onBack: () => void;
  rightAction?: ReactNode;
}) {
  return (
    <header className="header-bar">
      <div className="header-glass">
        {showBack ? (
          <IconButton label="返回" onClick={onBack}>
            <ArrowLeft size={21} aria-hidden="true" />
          </IconButton>
        ) : (
          <div className="header-spacer" />
        )}
        <div className="header-title">
          <h1>{title}</h1>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        <div className="header-right">{rightAction ?? <div className="header-spacer" />}</div>
      </div>
    </header>
  );
}

export function PrimaryNav({ active, go }: { active: Screen; go: (screen: Screen) => void }) {
  const navRef = useRef<HTMLElement>(null);
  const selectionRef = useRef<HTMLSpanElement>(null);
  const previousActiveIndexRef = useRef<number | null>(null);
  const previousLayoutVersionRef = useRef(0);
  const [layoutVersion, setLayoutVersion] = useState(0);
  const reducedMotion = useReducedMotion();
  const items = [
    {
      screen: "home" as Screen,
      label: "首页",
      icon: Home,
      active: active === "home" || active === "library"
    },
    {
      screen: "community" as Screen,
      label: "社区",
      icon: UsersRound,
      active: active === "community" || active === "communityBook" || active === "communityImport"
    },
    {
      screen: "study" as Screen,
      label: "学习",
      icon: BookOpenCheck,
      active: active === "study" || active === "book"
    },
    { screen: "profile" as Screen, label: "我的", icon: User, active: active === "profile" }
  ];
  const activeIndex = Math.max(0, items.findIndex((item) => item.active));

  useLayoutEffect(() => {
    const navigation = navRef.current;
    if (!navigation || typeof ResizeObserver === "undefined") return;

    let hasMeasured = false;
    const resizeObserver = new ResizeObserver(() => {
      if (!hasMeasured) {
        hasMeasured = true;
        return;
      }
      setLayoutVersion((version) => version + 1);
    });
    resizeObserver.observe(navigation);
    return () => resizeObserver.disconnect();
  }, []);

  useLayoutEffect(() => {
    const navigation = navRef.current;
    const selection = selectionRef.current;
    const target = navigation?.querySelector<HTMLElement>(`[data-nav-index="${activeIndex}"]`);
    if (!navigation || !selection || !target) return;

    const layoutChanged = previousLayoutVersionRef.current !== layoutVersion;
    const canAnimate = previousActiveIndexRef.current !== null
      && previousActiveIndexRef.current !== activeIndex
      && !layoutChanged
      && !reducedMotion;
    const navigationRect = navigation.getBoundingClientRect();
    const selectionRect = selection.getBoundingClientRect();
    const targetPosition = {
      x: target.offsetLeft,
      y: target.offsetTop,
      width: target.offsetWidth,
      height: target.offsetHeight
    };

    selection.getAnimations().forEach((animation) => animation.cancel());
    const targetTransform = `translate(${targetPosition.x}px, ${targetPosition.y}px)`;

    if (canAnimate) {
      const startTransform = `translate(${selectionRect.left - navigationRect.left}px, ${selectionRect.top - navigationRect.top}px)`;
      const easing = getComputedStyle(navigation)
        .getPropertyValue("--motion-ease-local-state")
        .trim() || "cubic-bezier(.65, 0, .35, 1)";
      selection.animate([
        {
          transform: startTransform,
          width: `${selectionRect.width}px`,
          height: `${selectionRect.height}px`
        },
        {
          transform: targetTransform,
          width: `${targetPosition.width}px`,
          height: `${targetPosition.height}px`
        }
      ], {
        duration: localSlowMotionDurationSeconds * 1000,
        easing
      });
    }

    selection.style.transform = targetTransform;
    selection.style.width = `${targetPosition.width}px`;
    selection.style.height = `${targetPosition.height}px`;

    previousActiveIndexRef.current = activeIndex;
    previousLayoutVersionRef.current = layoutVersion;
  }, [activeIndex, layoutVersion, reducedMotion]);

  return (
    <nav
      ref={navRef}
      className="primary-nav glass-nav"
      data-active-index={activeIndex}
      data-lg-variant="prominent"
      aria-label="主导航"
    >
      <span ref={selectionRef} className="nav-selection" aria-hidden="true" />
      {items.map((item, index) => {
        const Icon = item.icon;
        return (
          <button
            key={item.screen}
            className={`nav-item ${item.active ? "active" : ""} ${item.screen === "study" ? "nav-study" : ""}`}
            type="button"
            aria-current={item.active ? "page" : undefined}
            data-nav-index={index}
            data-motion-active={item.active ? "true" : "false"}
            data-motion-nav-kind="standard"
            onClick={() => go(item.screen)}
          >
            <span className="nav-icon">
              <span className="nav-icon-motion">
                <Icon size={22} aria-hidden="true" />
              </span>
            </span>
            <span className="nav-label">{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

function HomeIndicator() {
  return (
    <div className="home-indicator" aria-hidden="true">
      <span />
    </div>
  );
}

export function AppShell({
  active,
  motionReduced,
  focusMainNonce,
  contentScrollTop,
  onMainElement,
  onClickCapture,
  overlays,
  title,
  subtitle,
  showBack,
  onBack,
  go,
  children,
  hideNav = false
}: {
  active: Screen;
  motionReduced: boolean;
  focusMainNonce: number;
  contentScrollTop: number;
  onMainElement?: (element: HTMLElement | null) => void;
  onClickCapture?: (event: MouseEvent<HTMLDivElement>) => void;
  overlays?: ReactNode;
  title?: string;
  subtitle?: string;
  showBack?: boolean;
  onBack: () => void;
  go: (screen: Screen) => void;
  children: ReactNode;
  hideNav?: boolean;
}) {
  const deviceLayout = useDeviceLayout();
  const mouseDragScroll = useMouseDragScroll();
  const appShellRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement | null>(null);
  const [appShellElement, setAppShellElement] = useState<HTMLDivElement | null>(null);
  const setAppShellNode = useCallback((node: HTMLDivElement | null) => {
    appShellRef.current = node;
    setAppShellElement(node);
  }, []);
  const syncOverlayViewport = useCallback(() => {
    const shell = appShellRef.current;
    if (!shell) return;
    const viewport = getOverlayViewportMetrics(shell);
    shell.style.setProperty("--overlay-visual-top", `${Math.round(viewport.top)}px`);
    shell.style.setProperty("--overlay-visual-height", `${Math.round(viewport.height)}px`);
    shell.style.setProperty("--overlay-visual-bottom", `${Math.round(viewport.bottomGap)}px`);
  }, []);

  useLayoutEffect(() => {
    const shell = appShellElement;
    if (!shell) return;

    syncOverlayViewport();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(syncOverlayViewport);
    resizeObserver?.observe(shell);
    const visualViewport = window.visualViewport;
    visualViewport?.addEventListener("resize", syncOverlayViewport);
    visualViewport?.addEventListener("scroll", syncOverlayViewport);
    window.addEventListener("resize", syncOverlayViewport);
    window.addEventListener("orientationchange", syncOverlayViewport);

    return () => {
      resizeObserver?.disconnect();
      visualViewport?.removeEventListener("resize", syncOverlayViewport);
      visualViewport?.removeEventListener("scroll", syncOverlayViewport);
      window.removeEventListener("resize", syncOverlayViewport);
      window.removeEventListener("orientationchange", syncOverlayViewport);
    };
  }, [appShellElement, syncOverlayViewport]);

  const setMainNode = useCallback((node: HTMLElement | null) => {
    mainRef.current = node;
    onMainElement?.(node);
  }, [onMainElement]);

  useLayoutEffect(() => {
    if (focusMainNonce === 0) return;
    const main = mainRef.current;
    if (!main) return;

    // The content element survives screen swaps. Set its destination position
    // in the layout phase, before moving focus, so a new screen never paints
    // at the previous screen's scroll position. Back navigation receives its
    // own recorded snapshot from App; every other entry starts at the top.
    const inlineScrollBehavior = main.style.scrollBehavior;
    main.style.scrollBehavior = "auto";
    main.scrollTop = contentScrollTop;
    main.scrollLeft = 0;
    main.style.scrollBehavior = inlineScrollBehavior;
    main.focus({ preventScroll: true });
  }, [contentScrollTop, focusMainNonce]);

  // Only the device-only chrome changes with the media query. PrimaryNav is a
  // stable sibling of that replaceable layer so rapid boundary resizes never
  // create a window where the navigation element has been replaced.
  const deviceChrome = deviceLayout === "pad" ? (
    <PadChrome />
  ) : (
    <PhoneChrome>
      <IosStatusBar />
      <HomeIndicator />
    </PhoneChrome>
  );

  return (
    <div className="stage">
      <div
        ref={setAppShellNode}
        className="app-shell"
        role="application"
        aria-label="BookCourse AI 应用"
        data-active-screen={active}
        data-device-layout={deviceLayout}
        data-motion-reduced={motionReduced ? "true" : "false"}
        data-mouse-dragging={mouseDragScroll.dragging ? "true" : "false"}
        onClickCapture={(event) => {
          if (mouseDragScroll.consumeClick(event)) return;
          onClickCapture?.(event);
        }}
        onLostPointerCaptureCapture={mouseDragScroll.onLostPointerCaptureCapture}
        onPointerCancelCapture={mouseDragScroll.onPointerCancelCapture}
        onPointerDownCapture={mouseDragScroll.onPointerDownCapture}
        onPointerMoveCapture={mouseDragScroll.onPointerMoveCapture}
        onPointerUpCapture={mouseDragScroll.onPointerUpCapture}
      >
        {deviceChrome}
        {title ? <HeaderBar title={title} subtitle={subtitle} showBack={showBack} onBack={onBack} /> : null}
        <main ref={setMainNode} tabIndex={-1} className={`screen-content ${title ? "with-header" : ""} ${hideNav ? "without-nav" : ""}`} data-screen={active}>{children}</main>
        {active !== "study" && active !== "book" && active !== "communityBook" ? (
          <GlobalAIAssistant
            active={active}
            containerElement={appShellElement}
            containerRef={appShellRef}
            homeLayout={active === "home"}
            reducedMotion={motionReduced}
          />
        ) : null}
        {!hideNav ? <PrimaryNav active={active} go={go} /> : null}
        {overlays}
      </div>
    </div>
  );
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])'
].join(",");

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    (element) => element.getAttribute("aria-hidden") !== "true" && element.getClientRects().length > 0
  );
}

function useDialogFocus(
  dialogRef: RefObject<HTMLElement | null>,
  close: () => void,
  {
    enabled,
    focusKey,
    initialFocusRef
  }: {
    enabled: boolean;
    focusKey: string;
    initialFocusRef?: RefObject<HTMLElement | null>;
  }
) {
  useEffect(() => {
    if (!enabled) return;
    const frame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      const target = initialFocusRef?.current ?? getFocusableElements(dialog)[0] ?? dialog;
      target.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [dialogRef, enabled, focusKey, initialFocusRef]);

  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = getFocusableElements(dialog);
    if (focusable.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  };
}

type OrbPosition = {
  side: "left" | "right";
  top: number;
  left: number | null;
};

type AiOrbInteraction = "idle" | "pressed" | "dragging";

const aiOrbIdleImageBySide: Record<OrbPosition["side"], string> = {
  left: "/assets/brand/cloud-mascot-ai-chat-edge-left-ui.webp",
  right: "/assets/brand/cloud-mascot-ai-chat-edge-ui.webp"
};

const aiOrbActiveImageByInteraction: Record<Exclude<AiOrbInteraction, "idle">, string> = {
  pressed: "/assets/brand/cloud-mascot-ai-chat-edge-pressed-ui.webp",
  dragging: "/assets/brand/cloud-mascot-ai-chat-airborne-ui.webp"
};

type AiDialogView = { key: "ai-assistant" };

export const openGlobalAiAssistantEvent = "bookcourse:open-global-ai-assistant";

type OpenGlobalAiAssistantDetail = {
  origin?: HTMLButtonElement;
};

type AiAssistantMessage = {
  role: "ai" | "user";
  source?: string;
  text: string;
};

type AiAssistantContent = {
  contextBody: string;
  contextLabel: string;
  contextMeta: string;
  contextTitle: string;
  modes: string[];
  suggestions: string[];
  topics: string[];
};

const defaultAiAssistantContent: AiAssistantContent = {
  contextBody: "学习相关的问题，都可以问我。",
  contextLabel: "当前书籍",
  contextMeta: "09 / 九月学习",
  contextTitle: "期末复习效率如何提升？",
  modes: ["知识点讲解", "作业解析", "错题复盘"],
  suggestions: ["长时间学习如何避免疲惫", "如何规划复习节奏？"],
  topics: ["科普", "学习方法"]
};

function getAiDialogKey(view: AiDialogView) {
  return view.key;
}

type OrbMetrics = {
  bottom: number;
  leftMax: number;
  leftMin: number;
  topMax: number;
  topMin: number;
  visibleHeight: number;
  visibleTop: number;
};

type OverlayViewportMetrics = {
  bottom: number;
  bottomGap: number;
  height: number;
  top: number;
};

function readCssNumber(style: CSSStyleDeclaration, property: string) {
  const values = style.getPropertyValue(property).match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? [];
  return values.length > 0 ? Math.max(...values) : 0;
}

function getOverlayViewportMetrics(shell: HTMLElement): OverlayViewportMetrics {
  const bounds = shell.getBoundingClientRect();
  const visualViewport = window.visualViewport;
  const viewportTop = visualViewport?.offsetTop ?? 0;
  const viewportHeight = visualViewport?.height ?? bounds.height;
  const top = clamp(viewportTop - bounds.top, 0, bounds.height);
  const bottom = clamp(top + viewportHeight, 0, bounds.height);
  return {
    bottom,
    bottomGap: Math.max(0, bounds.height - bottom),
    height: Math.max(0, bottom - top),
    top
  };
}

function getOrbMetrics(shell: HTMLElement, reservedTop = 0): OrbMetrics {
  const bounds = shell.getBoundingClientRect();
  const style = getComputedStyle(shell);
  const viewport = getOverlayViewportMetrics(shell);
  const visibleTop = viewport.top;
  const bottom = viewport.bottom;
  const visibleHeight = viewport.height;
  const safeTop = readCssNumber(style, "--safe-area-top");
  const safeRight = readCssNumber(style, "--safe-area-right");
  const safeBottom = readCssNumber(style, "--safe-area-bottom");
  const safeLeft = readCssNumber(style, "--safe-area-left");
  const navHeight = readCssNumber(style, "--primary-nav-height");
  const navigation = shell.querySelector<HTMLElement>(".primary-nav");
  const navigationBounds = navigation?.getBoundingClientRect();
  const orb = shell.querySelector<HTMLElement>(".ai-orb");
  const orbWidth = orb?.offsetWidth || 62;
  const orbHeight = orb?.offsetHeight || 72;
  const verticalInset = 12;
  const baseTopMin = Math.min(bottom - orbHeight, visibleTop + safeTop + verticalInset);
  const bottomNavigationTop = navigationBounds && navigationBounds.width > bounds.width * .5
    ? navigationBounds.top - bounds.top
    : bottom;
  const topMax = Math.max(baseTopMin, Math.min(
    bottom - safeBottom - navHeight - verticalInset - orbHeight,
    bottomNavigationTop - verticalInset - orbHeight
  ));
  const topMin = Math.min(topMax, Math.max(baseTopMin, reservedTop));
  const leftMin = safeLeft;
  const leftMax = Math.max(leftMin, bounds.width - safeRight - orbWidth);

  return { bottom, leftMax, leftMin, topMax, topMin, visibleHeight, visibleTop };
}

function GlobalAIAssistant({
  active,
  containerElement,
  containerRef,
  homeLayout,
  reducedMotion
}: {
  active: Screen;
  containerElement: HTMLDivElement | null;
  containerRef: RefObject<HTMLDivElement | null>;
  homeLayout: boolean;
  reducedMotion: boolean;
}) {
  const bookcourseRepository = useBookCourseRepository();
  const {
    activeChapterId,
    generatedLessons,
    parsedChapters,
    uploadedFile
  } = useAppContext();
  const activeChapter = parsedChapters?.find((chapter) => chapter.chapter_id === activeChapterId)
    ?? parsedChapters?.[0]
    ?? null;
  const activeLesson = activeChapter
    ? generatedLessons?.find((lesson) => lesson.chapter_id === activeChapter.chapter_id) ?? null
    : generatedLessons?.[0] ?? null;
  const assistantContent = useMemo<AiAssistantContent>(() => {
    if (active !== "lesson") return defaultAiAssistantContent;
    const title = activeLesson?.title ?? activeChapter?.ai_title ?? activeChapter?.source_title ?? "当前章节";
    const concepts = activeLesson?.key_concepts.filter(Boolean) ?? [];
    const primaryConcept = concepts[0] ?? title;
    const secondaryConcept = concepts[1] ?? null;
    const pageStart = activeChapter?.printed_page_start ?? activeLesson?.page_start ?? activeChapter?.page_start;
    const pageEnd = activeChapter?.printed_page_end ?? activeLesson?.page_end ?? activeChapter?.page_end;
    const pageLabel = pageStart
      ? `原书 ${pageStart}${pageEnd && pageEnd !== pageStart ? `–${pageEnd}` : ""} 页`
      : uploadedFile?.name ?? "当前课程";
    return {
      contextBody: "依据当前章节原文回答，并标注教材位置。",
      contextLabel: "当前课程",
      contextMeta: pageLabel,
      contextTitle: title,
      modes: ["本节讲解", "举例理解", "随堂测验"],
      suggestions: [
        `用一句话解释“${primaryConcept}”`,
        secondaryConcept
          ? `比较“${primaryConcept}”和“${secondaryConcept}”`
          : `围绕“${title}”给我出一道题`
      ],
      topics: concepts.length > 0 ? concepts.slice(0, 2) : ["本节重点", "教材原文"]
    };
  }, [active, activeChapter, activeLesson, uploadedFile]);
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [orbPosition, setOrbPosition] = useState<OrbPosition>({
    side: "right",
    top: 0,
    left: null
  });
  const [orbInteraction, setOrbInteraction] = useState<AiOrbInteraction>("idle");
  const orbRef = useRef<HTMLButtonElement | null>(null);
  const dialogOriginRef = useRef<HTMLButtonElement | null>(null);
  const suppressOrbClickRef = useRef(false);
  const hasDraggedOrbRef = useRef(false);
  const dialogEpochRef = useRef(0);
  const dialogCloseEpochRef = useRef<number | null>(null);
  const dragFrameRef = useRef<number | null>(null);
  const settleFrameRef = useRef<number | null>(null);
  const pendingDragRef = useRef({ x: 0, y: 0 });
  const dragState = useRef({
    baseLeft: 0,
    baseTop: 0,
    pointerId: -1,
    startX: 0,
    startY: 0,
    currentX: 0,
    currentY: 0,
    moved: false
  });
  const [messages, setMessages] = useState<AiAssistantMessage[]>([]);
  const requestedDialog = useMemo<AiDialogView | null>(() => (
    open ? { key: "ai-assistant" } : null
  ), [open]);
  const dialogPresence = useMotionPresence<AiDialogView>({
    requested: requestedDialog,
    getKey: getAiDialogKey,
    reducedMotion,
    motionNames: aiDialogAnimationNames,
    maxMotionMs: globalMotionFallbackMs
  });
  const previouslyRenderedDialogRef = useRef(false);
  const dialogVisible = dialogPresence.rendered !== null;
  const dragging = orbInteraction === "dragging";
  const orbImageSource = orbInteraction === "idle"
    ? aiOrbIdleImageBySide[orbPosition.side]
    : aiOrbActiveImageByInteraction[orbInteraction];
  const orbSuppressed = active === "lesson";

  const requestDialogOpen = useCallback(() => {
    dialogEpochRef.current += 1;
    dialogCloseEpochRef.current = null;
    setOpen(true);
  }, []);

  const requestDialogClose = useCallback(() => {
    dialogCloseEpochRef.current = dialogEpochRef.current;
    setOpen(false);
  }, []);

  useEffect(() => {
    const shell = containerElement;
    if (!shell) return;
    const openFromCustomEntry: EventListener = (event) => {
      const detail = (event as CustomEvent<OpenGlobalAiAssistantDetail>).detail;
      dialogOriginRef.current = detail?.origin?.isConnected ? detail.origin : orbRef.current;
      requestDialogOpen();
    };
    shell.addEventListener(openGlobalAiAssistantEvent, openFromCustomEntry);
    return () => shell.removeEventListener(openGlobalAiAssistantEvent, openFromCustomEntry);
  }, [containerElement, requestDialogOpen]);

  useEffect(() => {
    setInput("");
    setMessages([]);
    setLoading(false);
  }, [active, activeChapter?.chapter_id, activeLesson?.lesson_id, uploadedFile?.bookId]);

  useEffect(() => {
    [
      ...Object.values(aiOrbIdleImageBySide),
      ...Object.values(aiOrbActiveImageByInteraction)
    ].forEach((source) => {
      const image = new Image();
      image.decoding = "async";
      image.src = source;
    });
  }, []);

  const clearOrbMotion = useCallback(() => {
    if (dragFrameRef.current !== null) {
      window.cancelAnimationFrame(dragFrameRef.current);
      dragFrameRef.current = null;
    }
    if (settleFrameRef.current !== null) {
      window.cancelAnimationFrame(settleFrameRef.current);
      settleFrameRef.current = null;
    }
    pendingDragRef.current = { x: 0, y: 0 };
    const orb = orbRef.current;
    if (!orb) return;
    orb.removeAttribute("data-ai-orb-settling");
    orb.style.setProperty("--ai-orb-drag-x", "0px");
    orb.style.setProperty("--ai-orb-drag-y", "0px");
    orb.style.setProperty("--ai-orb-settle-x", "0px");
    orb.style.setProperty("--ai-orb-settle-y", "0px");
  }, []);

  const constrainOrb = useCallback(() => {
    const shell = containerRef.current;
    if (!shell) return;
    const shellBounds = shell.getBoundingClientRect();
    const discoveryControls = active === "community"
      ? shell.querySelector<HTMLElement>(".community-discovery-controls")
      : null;
    const reservedTop = discoveryControls
      ? discoveryControls.getBoundingClientRect().bottom - shellBounds.top + 8
      : 0;
    const metrics = getOrbMetrics(shell, reservedTop);
    // The short-landscape layout places primary navigation on the left rail.
    // Keep the default assistant dock on the opposite edge so it never hides
    // a navigation destination before the user has dragged it.
    const useLeftDock = false;

    setOrbPosition((current) => {
      const defaultTopRatio = homeLayout
        ? (metrics.visibleHeight < 760 ? .69 : .82)
        : .65;
      const defaultTop = metrics.topMin + ((metrics.topMax - metrics.topMin) * defaultTopRatio);
      const requestedTop = hasDraggedOrbRef.current ? (current.top || defaultTop) : defaultTop;
      const nextTop = clamp(requestedTop, metrics.topMin, metrics.topMax);
      const nextLeft = current.left === null ? null : clamp(current.left, metrics.leftMin, metrics.leftMax);
      const nextSide = current.left === null && !hasDraggedOrbRef.current
        ? (useLeftDock ? "left" : "right")
        : current.side;
      if (nextTop === current.top && nextLeft === current.left && nextSide === current.side) return current;
      return { ...current, side: nextSide, top: nextTop, left: nextLeft };
    });
  }, [active, containerRef, homeLayout]);

  const cancelOrbInteraction = useCallback(() => {
    clearOrbMotion();
    dragState.current.pointerId = -1;
    dragState.current.moved = false;
    setOrbInteraction("idle");
  }, [clearOrbMotion]);

  const handleViewportChange = useCallback(() => {
    cancelOrbInteraction();
    constrainOrb();
  }, [cancelOrbInteraction, constrainOrb]);

  useLayoutEffect(() => {
    const shell = containerElement;
    if (!shell) return;

    constrainOrb();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(handleViewportChange);
    resizeObserver?.observe(shell);
    const discoveryControls = active === "community"
      ? shell.querySelector<HTMLElement>(".community-discovery-controls")
      : null;
    if (discoveryControls) resizeObserver?.observe(discoveryControls);
    const visualViewport = window.visualViewport;
    visualViewport?.addEventListener("resize", handleViewportChange);
    visualViewport?.addEventListener("scroll", handleViewportChange);
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("orientationchange", handleViewportChange);

    return () => {
      resizeObserver?.disconnect();
      visualViewport?.removeEventListener("resize", handleViewportChange);
      visualViewport?.removeEventListener("scroll", handleViewportChange);
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("orientationchange", handleViewportChange);
    };
  }, [active, containerElement, constrainOrb, handleViewportChange]);

  useEffect(() => {
    if (!reducedMotion) return;
    handleViewportChange();
  }, [handleViewportChange, reducedMotion]);

  useLayoutEffect(() => {
    const wasRendered = previouslyRenderedDialogRef.current;
    const origin = dialogOriginRef.current;
    if (
      wasRendered &&
      !dialogVisible &&
      !open &&
      dialogCloseEpochRef.current === dialogEpochRef.current &&
      origin?.isConnected &&
      origin.getClientRects().length > 0 &&
      origin.dataset.aiOrbHidden !== "true"
    ) {
      origin.focus({ preventScroll: true });
    }
    previouslyRenderedDialogRef.current = dialogVisible;
  }, [dialogVisible, open]);

  async function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    const history: AssistantHistoryMessage[] = messages.map((message) => ({
      content: message.text,
      role: message.role === "ai" ? "assistant" : "user"
    }));
    setMessages((items) => [...items, { role: "user", text }]);
    setInput("");
    setLoading(true);
    try {
      if (active === "lesson" && uploadedFile) {
        const result = await bookcourseRepository.queryRag({
          book_id: uploadedFile.bookId,
          chapter_id: activeChapter?.chapter_id ?? null,
          history,
          question: text
        });
        const citation = result.citations[0];
        const source = citation
          ? [
              citation.chapter_title,
              citation.location_label || `第 ${citation.page} 页`
            ].filter(Boolean).join(" · ")
          : undefined;
        setMessages((items) => [
          ...items,
          { role: "ai", source, text: result.answer }
        ]);
      } else {
        const result = await bookcourseRepository.chatAssistant({
          message: text,
          history,
          context_title: activeLesson?.title ?? uploadedFile?.name ?? "学习首页",
          context_description: activeChapter?.source_title
            ? `学生当前正在查看：${activeChapter.source_title}`
            : `学生当前位于 App 的 ${active} 页面。`
        });
        setMessages((items) => [
          ...items,
          { role: "ai", source: `云端模型 · ${result.model}`, text: result.answer }
        ]);
      }
    } catch (error) {
      setMessages((items) => [
        ...items,
        {
          role: "ai",
          text: error instanceof Error
            ? `这次没有完成 AI 回答：${error.message}`
            : "这次没有完成 AI 回答，请稍后再试。"
        }
      ]);
    } finally {
      setLoading(false);
    }
  }

  const writePendingDragTransform = useCallback(() => {
    dragFrameRef.current = null;
    const orb = orbRef.current;
    if (!orb) return;
    orb.style.setProperty("--ai-orb-drag-x", `${pendingDragRef.current.x}px`);
    orb.style.setProperty("--ai-orb-drag-y", `${pendingDragRef.current.y}px`);
  }, []);

  const scheduleDragTransform = useCallback((x: number, y: number) => {
    pendingDragRef.current = { x, y };
    if (dragFrameRef.current !== null) return;
    dragFrameRef.current = window.requestAnimationFrame(writePendingDragTransform);
  }, [writePendingDragTransform]);

  const flushDragTransform = useCallback(() => {
    if (dragFrameRef.current !== null) {
      window.cancelAnimationFrame(dragFrameRef.current);
      dragFrameRef.current = null;
    }
    const orb = orbRef.current;
    if (!orb) return;
    orb.style.setProperty("--ai-orb-drag-x", `${pendingDragRef.current.x}px`);
    orb.style.setProperty("--ai-orb-drag-y", `${pendingDragRef.current.y}px`);
  }, []);

  function handleOrbPointerDown(event: PointerEvent<HTMLButtonElement>) {
    clearOrbMotion();
    suppressOrbClickRef.current = false;
    const baseRect = event.currentTarget.getBoundingClientRect();
    dragState.current = {
      baseLeft: baseRect.left,
      baseTop: baseRect.top,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      currentX: event.clientX,
      currentY: event.clientY,
      moved: false
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setOrbInteraction("pressed");
  }

  function handleOrbPointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (dragState.current.pointerId !== event.pointerId) return;
    const dx = event.clientX - dragState.current.startX;
    const dy = event.clientY - dragState.current.startY;
    if (Math.hypot(dx, dy) > 6) {
      if (!dragState.current.moved) {
        dragState.current.moved = true;
        setOrbInteraction("dragging");
      }
    }
    if (!dragState.current.moved) return;
    dragState.current.currentX = event.clientX;
    dragState.current.currentY = event.clientY;
    scheduleDragTransform(dx, dy);
  }

  function handleOrbPointerUp(event: PointerEvent<HTMLButtonElement>) {
    if (dragState.current.pointerId !== event.pointerId) return;
    const moved = dragState.current.moved;
    flushDragTransform();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragState.current.pointerId = -1;
    suppressOrbClickRef.current = moved;
    setOrbInteraction("idle");
    if (!moved) {
      clearOrbMotion();
      return;
    }

    hasDraggedOrbRef.current = true;
    const shell = containerRef.current;
    const orb = orbRef.current;
    if (!shell || !orb) {
      clearOrbMotion();
      constrainOrb();
      return;
    }

    const before = orb.getBoundingClientRect();
    const shellBounds = shell.getBoundingClientRect();
    const discoveryControls = active === "community"
      ? shell.querySelector<HTMLElement>(".community-discovery-controls")
      : null;
    const reservedTop = discoveryControls
      ? discoveryControls.getBoundingClientRect().bottom - shellBounds.top + 8
      : 0;
    const metrics = getOrbMetrics(shell, reservedTop);
    const releasedLeft = reducedMotion ? dragState.current.baseLeft + pendingDragRef.current.x : before.left;
    const releasedTop = reducedMotion ? dragState.current.baseTop + pendingDragRef.current.y : before.top;
    const top = clamp(releasedTop - shellBounds.top, metrics.topMin, metrics.topMax);
    const centerX = releasedLeft + before.width / 2 - shellBounds.left;
    const side: OrbPosition["side"] = centerX < shellBounds.width / 2 ? "left" : "right";

    if (!reducedMotion) orb.setAttribute("data-ai-orb-settling", "true");
    orb.style.top = `${top}px`;
    orb.style.left = side === "left" ? "var(--ai-orb-left-inset)" : "auto";
    orb.style.right = side === "right" ? "var(--ai-orb-right-inset)" : "auto";
    orb.style.setProperty("--ai-orb-drag-x", "0px");
    orb.style.setProperty("--ai-orb-drag-y", "0px");
    setOrbPosition({ side, top, left: null });
    if (reducedMotion) {
      clearOrbMotion();
      return;
    }

    const after = orb.getBoundingClientRect();
    orb.style.setProperty("--ai-orb-settle-x", `${before.left - after.left}px`);
    orb.style.setProperty("--ai-orb-settle-y", `${before.top - after.top}px`);
    settleFrameRef.current = window.requestAnimationFrame(() => {
      settleFrameRef.current = null;
      const activeOrb = orbRef.current;
      if (!activeOrb) return;
      activeOrb.removeAttribute("data-ai-orb-settling");
      activeOrb.style.setProperty("--ai-orb-settle-x", "0px");
      activeOrb.style.setProperty("--ai-orb-settle-y", "0px");
    });
  }

  function handleOrbPointerCancel(event: PointerEvent<HTMLButtonElement>) {
    if (dragState.current.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    cancelOrbInteraction();
    suppressOrbClickRef.current = false;
    constrainOrb();
  }

  const orbStyle = {
    top: `${orbPosition.top}px`,
    left: orbPosition.left !== null ? `${orbPosition.left}px` : orbPosition.side === "left" ? "var(--ai-orb-left-inset)" : "auto",
    right: orbPosition.left !== null ? "auto" : orbPosition.side === "right" ? "var(--ai-orb-right-inset)" : "auto"
  };

  return (
    <>
      <button
        ref={orbRef}
        className={`ai-orb ai-orb-mascot glass-button ${dragging ? "dragging" : ""}`}
        type="button"
        aria-label={dragging ? "正在拖动 AI 助手入口" : "打开 AI 助手"}
        aria-controls="ai-assistant-dialog"
        aria-expanded={dialogVisible}
        aria-haspopup="dialog"
        aria-hidden={orbSuppressed || dialogVisible ? true : undefined}
        data-interaction={orbInteraction}
        data-side={orbPosition.side}
        data-ai-orb-hidden={dialogVisible ? "true" : "false"}
        data-mouse-drag-scroll="ignore"
        hidden={orbSuppressed}
        tabIndex={orbSuppressed || dialogVisible ? -1 : undefined}
        style={orbStyle}
        onClick={(event) => {
          if (suppressOrbClickRef.current) {
            suppressOrbClickRef.current = false;
            return;
          }
          dialogOriginRef.current = event.currentTarget;
          requestDialogOpen();
        }}
        onPointerDown={handleOrbPointerDown}
        onPointerMove={handleOrbPointerMove}
        onPointerUp={handleOrbPointerUp}
        onPointerCancel={handleOrbPointerCancel}
      >
        <img src={orbImageSource} alt="" aria-hidden="true" decoding="async" draggable={false} />
      </button>
      <AIAssistantDialog
          content={assistantContent}
          visible={dialogVisible}
          state={dialogPresence.state}
          presenceId={dialogPresence.presenceId}
          originRef={dialogOriginRef}
          input={input}
          loading={loading}
          messages={messages}
          onClose={requestDialogClose}
          onAnimationEnd={dialogPresence.onAnimationEnd}
          onAnimationCancel={dialogPresence.onAnimationCancel}
          setInput={setInput}
          submitMessage={submitMessage}
      />
    </>
  );
}

function AIAssistantDialog({
  content,
  visible,
  state,
  presenceId,
  originRef,
  input,
  loading,
  messages,
  onClose,
  onAnimationEnd,
  onAnimationCancel,
  setInput,
  submitMessage
}: {
  content: AiAssistantContent;
  visible: boolean;
  state: MotionState;
  presenceId: number;
  originRef: RefObject<HTMLButtonElement | null>;
  input: string;
  loading: boolean;
  messages: AiAssistantMessage[];
  onClose: () => void;
  onAnimationEnd: (event: MotionAnimationEvent) => void;
  onAnimationCancel: (event: MotionAnimationEvent) => void;
  setInput: (value: string) => void;
  submitMessage: (event: FormEvent<HTMLFormElement>) => Promise<void>;
}) {
  const layerRef = useRef<HTMLDivElement | null>(null);
  const sharedSurfaceRef = useRef<HTMLDivElement | null>(null);
  const sharedIconRef = useRef<HTMLSpanElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const titleId = useId();
  const focusPresenceRef = useRef(presenceId);
  if (visible && state !== "closing") focusPresenceRef.current = presenceId;
  const panelKey = `ai-assistant:${focusPresenceRef.current}`;
  const handleDialogKeyDown = useDialogFocus(dialogRef, onClose, {
    enabled: visible && state !== "closing",
    focusKey: panelKey,
    initialFocusRef: inputRef
  });
  const blockClosingInteraction = (event: SyntheticEvent<HTMLElement>) => {
    if (state !== "closing") return;
    event.preventDefault();
    event.stopPropagation();
  };
  const blockClosingKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const isActivationKey = event.key === "Enter" || event.key === " " || event.key === "Spacebar" || event.key === "Space" || event.code === "Space";
    if (state !== "closing" || !isActivationKey) return;
    event.preventDefault();
    event.stopPropagation();
  };

  const closeFromScrim = () => {
    if (state !== "closing") onClose();
  };

  const settleDialogAnimation = (event: MotionAnimationEvent, settle: (event: MotionAnimationEvent) => void) => {
    const expectedDirection = state === "entering" ? "-in" : state === "closing" ? "-out" : null;
    // The exiting panel intentionally remains mounted. Ignore an old phase's
    // cancellation so it cannot settle a newer Presence generation.
    if (!expectedDirection || !event.animationName.endsWith(expectedDirection)) return;
    settle(event);
  };

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !visible) return;
    const handleAnimationCancel = (event: AnimationEvent) => settleDialogAnimation(event, onAnimationCancel);
    dialog.addEventListener("animationcancel", handleAnimationCancel);
    return () => dialog.removeEventListener("animationcancel", handleAnimationCancel);
  }, [onAnimationCancel, state, visible]);

  useLayoutEffect(() => {
    const layer = layerRef.current;
    const dialog = dialogRef.current;
    const origin = originRef.current;
    const sharedSurface = sharedSurfaceRef.current;
    const sharedIcon = sharedIconRef.current;
    if (!layer || !dialog || !origin || !sharedSurface || !sharedIcon || !visible) return;

    sharedSurface.removeAttribute("data-motion-ready");
    sharedIcon.removeAttribute("data-motion-ready");
    if (state === "idle") return;

    const layerBounds = layer.getBoundingClientRect();
    const dialogBounds = dialog.getBoundingClientRect();
    const originBounds = origin.getBoundingClientRect();
    if (
      dialogBounds.width <= 0 ||
      dialogBounds.height <= 0 ||
      originBounds.width <= 0 ||
      originBounds.height <= 0
    ) return;

    const targetLeft = dialogBounds.left - layerBounds.left;
    const targetTop = dialogBounds.top - layerBounds.top;
    const sourceLeft = originBounds.left - layerBounds.left;
    const sourceTop = originBounds.top - layerBounds.top;
    const deltaX = sourceLeft - targetLeft;
    const deltaY = sourceTop - targetTop;
    const scaleX = originBounds.width / dialogBounds.width;
    const scaleY = originBounds.height / dialogBounds.height;
    const dialogStyle = getComputedStyle(dialog);

    Object.assign(sharedSurface.style, {
      left: `${targetLeft}px`,
      top: `${targetTop}px`,
      width: `${dialogBounds.width}px`,
      height: `${dialogBounds.height}px`
    });
    sharedSurface.style.setProperty("--ai-shared-delta-x", `${deltaX}px`);
    sharedSurface.style.setProperty("--ai-shared-delta-y", `${deltaY}px`);
    sharedSurface.style.setProperty("--ai-shared-scale-x", String(scaleX));
    sharedSurface.style.setProperty("--ai-shared-scale-y", String(scaleY));
    sharedSurface.style.setProperty("--ai-shared-target-radius", dialogStyle.borderRadius || "16px");
    sharedSurface.style.setProperty("--ai-shared-target-background", dialogStyle.backgroundColor || "#ffffff");
    sharedSurface.style.setProperty("--ai-shared-target-border", dialogStyle.borderColor || "transparent");

    Object.assign(sharedIcon.style, {
      left: `${sourceLeft}px`,
      top: `${sourceTop}px`,
      width: `${originBounds.width}px`,
      height: `${originBounds.height}px`
    });

    // Restarting the proxy is intentional when a Presence generation changes.
    // Layout geometry is committed first; only transform, color, radius, and
    // opacity participate in the visible transition.
    void sharedSurface.offsetWidth;
    sharedSurface.setAttribute("data-motion-ready", "true");
    sharedIcon.setAttribute("data-motion-ready", "true");
  }, [originRef, presenceId, state, visible]);

  if (!visible) return null;

  return (
    <div ref={layerRef} className="ai-overlay-layer">
      <button className="ai-overlay-scrim" data-motion-state={state} type="button" aria-label="关闭 AI 助手背景" onClick={closeFromScrim} />
      <div
        key={`ai-shared-surface:${presenceId}`}
        ref={sharedSurfaceRef}
        className="ai-shared-surface"
        data-motion-state={state}
        aria-hidden="true"
      />
      <span
        key={`ai-shared-icon:${presenceId}`}
        ref={sharedIconRef}
        className="ai-shared-origin-icon"
        data-motion-state={state}
        aria-hidden="true"
      >
        <img
          src="/assets/brand/cloud-mascot-ai-chat-airborne-ui.webp"
          alt=""
          aria-hidden="true"
          decoding="async"
          draggable={false}
        />
      </span>
      <aside
        key={panelKey}
        ref={dialogRef}
        id="ai-assistant-dialog"
        className="ai-overlay glass-sheet"
        data-motion-state={state}
        data-motion-presence={presenceId}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy={state === "closing" ? true : undefined}
        tabIndex={-1}
        onAnimationEnd={(event) => settleDialogAnimation(event, onAnimationEnd)}
        onPointerDownCapture={blockClosingInteraction}
        onPointerUpCapture={blockClosingInteraction}
        onClickCapture={blockClosingInteraction}
        onSubmitCapture={blockClosingInteraction}
        onKeyDownCapture={blockClosingKeyDown}
        onKeyDown={handleDialogKeyDown}
      >
        <div className="ai-overlay-head">
          <div>
            <span className="ai-avatar">
              <Bot size={18} aria-hidden="true" />
            </span>
            <h2 id={titleId}>AI 导学助手</h2>
          </div>
          <button className="icon-button ai-close" type="button" aria-label="收起 AI 助手" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="ai-dialog-scroll">
          <div className="ai-intro">
            <p>下拉查看历史对话</p>
            <h3>Hi，我是你的“AI学习助手”～</h3>
            <span>学习相关的问题，都可以问我哦。</span>
          </div>
          <section className="ai-current-book">
            <div className="ai-current-book-head">
              <strong>{content.contextLabel}</strong>
              <span>{content.contextMeta}</span>
            </div>
            <div className="ai-current-book-body">
              <h3>{content.contextTitle}</h3>
              <p>{content.contextBody}</p>
              <div className="ai-topic-row">
                {content.topics.map((item) => (
                  <button type="button" key={item} onClick={() => setInput(`请讲解“${item}”`)}>
                    {item}
                  </button>
                ))}
              </div>
            </div>
          </section>
          <p className="ai-suggest-title">你可能感兴趣</p>
          <div className="ai-suggest-list">
            {content.suggestions.map((item) => (
              <button disabled={loading} type="button" key={item} onClick={() => setInput(item)}>
                <MessageCircle size={15} aria-hidden="true" />
                {item}
              </button>
            ))}
          </div>
          <div className="ai-message-list" aria-live="polite" aria-busy={loading}>
            {messages.map((message, index) => (
              <div className={`ai-message ${message.role}`} key={`${message.role}-${index}`}>
                <p>{message.text}</p>
                {message.source ? <small>{message.source}</small> : null}
              </div>
            ))}
            {loading ? <p className="ai-message ai">正在检索当前章节的教材片段…</p> : null}
          </div>
          <div className="ai-mode-row">
            {content.modes.map((item) => (
              <button disabled={loading} type="button" key={item} onClick={() => setInput(item)}>
                {item}
              </button>
            ))}
          </div>
        </div>
        <form className="ai-compose" onSubmit={submitMessage}>
          <input
            ref={inputRef}
            value={input}
            aria-label="向 AI 助手提问"
            disabled={loading}
            onChange={(event) => setInput(event.target.value)}
            placeholder="问教材、问错题、问计划..."
          />
          <button type="submit" aria-label="发送" disabled={loading || !input.trim()}>
            {loading
              ? <Loader2 className="spin" size={18} aria-hidden="true" />
              : <SendHorizontal size={18} aria-hidden="true" />}
          </button>
        </form>
      </aside>
    </div>
  );
}

export function Toast({ toast }: { toast: ToastMessage | null }) {
  const reducedMotion = useReducedMotion();
  const toastRef = useRef<HTMLDivElement | null>(null);
  const presence = useMotionPresence({
    requested: toast,
    getKey: getToastKey,
    reducedMotion,
    motionNames: toastAnimationNames
  });
  const settleToastAnimation = (event: MotionAnimationEvent, settle: (event: MotionAnimationEvent) => void) => {
    const expectedName = presence.state === "entering"
      ? "motion-toast-in"
      : presence.state === "closing"
        ? "motion-toast-out"
        : null;
    // A canceled animation from a superseded phase can arrive after React has
    // committed the next Presence generation. It must not settle that newer
    // phase just because both names are valid Toast animations.
    if (event.animationName !== expectedName) return;
    settle(event);
  };
  useEffect(() => {
    const toastElement = toastRef.current;
    if (!toastElement || !presence.rendered) return;
    const handleAnimationCancel = (event: AnimationEvent) => settleToastAnimation(event, presence.onAnimationCancel);
    toastElement.addEventListener("animationcancel", handleAnimationCancel);
    return () => toastElement.removeEventListener("animationcancel", handleAnimationCancel);
  }, [presence.onAnimationCancel, presence.rendered, presence.state]);
  if (!presence.rendered) return null;
  return (
    <div
      // A toast animation is owned by its Presence generation. Remount the
      // surface when that identity changes so stale DOM animation events
      // cannot be delivered to a replacement generation.
      key={presence.presenceId}
      ref={toastRef}
      className={`toast glass-surface toast-${presence.rendered.tone}`}
      data-motion-state={presence.state}
      data-motion-presence={presence.presenceId}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      onAnimationEnd={(event) => settleToastAnimation(event, presence.onAnimationEnd)}
    >
      <CheckCircle2 size={18} aria-hidden="true" />
      <span>{presence.rendered.text}</span>
    </div>
  );
}

export function ActionSheet({
  view,
  state,
  presenceId,
  close,
  onAnimationEnd,
  onAnimationCancel,
  onExited
}: {
  view: ActionSheetView | null;
  state: MotionState;
  presenceId: number;
  close: () => void;
  onAnimationEnd: (event: MotionAnimationEvent) => void;
  onAnimationCancel: (event: MotionAnimationEvent) => void;
  onExited: () => void;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const previousVisibilityRef = useRef(false);
  const focusPresenceRef = useRef(presenceId);
  if (view && state !== "closing") focusPresenceRef.current = presenceId;
  const panelKey = `${view?.key ?? "closed"}:${focusPresenceRef.current}`;
  const handleDialogKeyDown = useDialogFocus(dialogRef, close, {
    // The handler remains attached through closing for Tab/Escape, but its
    // entry autofocus must be canceled so it cannot race focus restoration
    // after the frozen panel unmounts.
    enabled: view !== null && state !== "closing",
    focusKey: `${view?.key ?? "closed"}:${focusPresenceRef.current}`
  });
  useLayoutEffect(() => {
    const wasVisible = previousVisibilityRef.current;
    const isVisible = view !== null;
    if (wasVisible && !isVisible) {
      onExited();
    }
    previousVisibilityRef.current = isVisible;
  }, [onExited, presenceId, state, view]);

  const permitsClosingSourceReplacement = (target: EventTarget | null) => (
    target instanceof Element && target.closest(".chat-sheet [data-sheet-replacement='source']") !== null
  );
  const blockClosingInteraction = (event: SyntheticEvent<HTMLElement>) => {
    // A source request has its own immutable view snapshot. Let it replace a
    // frozen Chat exit, while all other closing-panel work remains blocked.
    if (state !== "closing" || permitsClosingSourceReplacement(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
  };
  const blockClosingKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const isActivationKey = event.key === "Enter" || event.key === " " || event.key === "Spacebar" || event.key === "Space" || event.code === "Space";
    if (state !== "closing" || !isActivationKey || permitsClosingSourceReplacement(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
  };

  const closeFromScrim = () => {
    if (state !== "closing") close();
  };

  const settleSheetAnimation = (event: MotionAnimationEvent, settle: (event: MotionAnimationEvent) => void) => {
    const expectedDirection = state === "entering" ? "-in" : state === "closing" ? "-out" : null;
    // The panel stays mounted while closing, so an interrupted enter animation
    // can report after the closing generation starts. Only its own phase is
    // allowed to settle the Presence machine.
    if (!expectedDirection || !event.animationName.endsWith(expectedDirection)) return;
    settle(event);
  };

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !view) return;
    const handleAnimationCancel = (event: AnimationEvent) => settleSheetAnimation(event, onAnimationCancel);
    dialog.addEventListener("animationcancel", handleAnimationCancel);
    return () => dialog.removeEventListener("animationcancel", handleAnimationCancel);
  }, [onAnimationCancel, state, view]);

  if (!view) return null;

  return (
    <div className="sheet-overlay" data-sheet-type={view.sheet.type} data-motion-state={state}>
      <button className="sheet-scrim" data-motion-state={state} type="button" aria-label={`关闭${view.title}背景`} onClick={closeFromScrim} />
      <section
        key={panelKey}
        ref={dialogRef}
        className="sheet glass-sheet"
        data-sheet-type={view.sheet.type}
        data-motion-state={state}
        data-motion-presence={presenceId}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy={state === "closing" ? true : undefined}
        tabIndex={-1}
        onAnimationEnd={(event) => settleSheetAnimation(event, onAnimationEnd)}
        onPointerDownCapture={blockClosingInteraction}
        onPointerUpCapture={blockClosingInteraction}
        onClickCapture={blockClosingInteraction}
        onSubmitCapture={blockClosingInteraction}
        onKeyDownCapture={blockClosingKeyDown}
        onKeyDown={handleDialogKeyDown}
      >
        <div className="sheet-handle" aria-hidden="true" />
        <div className="sheet-header">
          <h2 id={titleId}>{view.title}</h2>
          <IconButton className="sheet-close" label="关闭" onClick={close}>
            <X size={20} aria-hidden="true" />
          </IconButton>
        </div>
        {view.content}
      </section>
    </div>
  );
}

export function CitationCard({
  title,
  page,
  quote,
  image,
  onOpen,
  imageMotion = false
}: {
  title: string;
  page: string;
  quote: string;
  image?: string;
  onOpen: () => void;
  imageMotion?: boolean;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const imageMotionState = useImageMotion(imageMotion ? image : undefined);

  useEffect(() => {
    setImageFailed(false);
  }, [image]);

  return (
    <article className="citation-card">
      <div className="citation-icon">
        <BookOpenCheck size={20} aria-hidden="true" />
      </div>
      <div>
        <p className="citation-meta">{title} · {page}</p>
        <p className="citation-quote">{quote}</p>
        <button className="inline-link" data-sheet-replacement="source" type="button" onClick={onOpen}>
          查看原文
        </button>
      </div>
      {image ? (
        <div className="citation-media">
          {imageFailed ? (
            <span
              className="citation-media-fallback"
              data-motion-image-source={imageMotion ? image : undefined}
              data-motion-image-state={imageMotion ? "failed" : undefined}
              role="img"
              aria-label={`${page} 教材缩略图不可用`}
            >
              <FileText size={20} aria-hidden="true" />
            </span>
          ) : (
            <img
              className={imageMotion ? "citation-media-image" : undefined}
              data-motion-image-state={imageMotion ? imageMotionState.state : undefined}
              ref={imageMotion ? imageMotionState.imageRef : undefined}
              src={image}
              alt={`${page} 教材缩略图`}
              onLoad={imageMotion ? imageMotionState.onLoad : undefined}
              onAnimationEnd={imageMotion ? (event) => {
                if (event.animationName === "motion-stage3-image-in") imageMotionState.settleAnimation();
              } : undefined}
              onError={() => {
                if (imageMotion) imageMotionState.onError();
                setImageFailed(true);
              }}
            />
          )}
        </div>
      ) : null}
    </article>
  );
}

export function KnowledgeChip({
  label,
  mastery,
  onClick
}: {
  label: string;
  mastery: number;
  onClick: () => void;
}) {
  return (
    <button className="knowledge-chip glass-pill" type="button" onClick={onClick}>
      <span>{label}</span>
      <small>{mastery}%</small>
    </button>
  );
}

export function Metric({ label, value, helper }: { label: string; value: string; helper?: string }) {
  return (
    <div className="metric-card">
      <strong>{value}</strong>
      <span>{label}</span>
      {helper ? <small>{helper}</small> : null}
    </div>
  );
}

export function TextbookPreview({ src, title }: { src: string; title: string }) {
  return (
    <figure className="textbook-preview">
      <img src={src} alt={title} />
      <figcaption>
        <FileText size={14} aria-hidden="true" />
        {title}
      </figcaption>
    </figure>
  );
}

export function UploadBadge() {
  return (
    <span className="upload-badge">
      <Upload size={15} aria-hidden="true" />
      PDF
    </span>
  );
}
