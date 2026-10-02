import { StickerIcon } from "./icons/StickerIcon";
import { communityCatalog } from "../services/CommunityRepository";
import { courseRagScope } from "../features/courses/selectors";
import { courseSourceBookIds } from "../features/courses/model";
import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ButtonHTMLAttributes, type ComponentPropsWithoutRef, type FormEvent, type KeyboardEvent, type MouseEvent, type PointerEvent, type ReactNode, type RefObject, type SyntheticEvent } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ArrowLeft, BookOpenCheck, CalendarDays, CheckCircle2, ChevronRight, ClipboardCheck, FileText, Home, Layers3, ListChecks, Loader2, MessageCircle, NotebookPen, PanelLeftClose, PanelLeftOpen, RefreshCw, SendHorizontal, Upload, User, X } from "lucide-react";
import type { Screen, SheetState, ToastMessage } from "../types/app";
import type { Citation, RagRetrievalSummary } from "../types/api";
import { globalMotionFallbackMs, localSlowMotionDurationSeconds, localStateGsapEase, StateSwapText, useImageMotion, useMotionPresence, useReducedMotion, type MotionAnimationEvent, type MotionState } from "../motion";
import { PadChrome } from "../layouts/PadChrome";
import { PhoneChrome } from "../layouts/PhoneChrome";
import { useDeviceLayout } from "../layouts/useDeviceLayout";
import { useMouseDragScroll } from "../hooks/useMouseDragScroll";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { IosStatusBar } from "./IosStatusBar";
import { getRuntimePlatform } from "../platform/nativeApp";
import { getCitationSourceText } from "../screens/sheets/citationSource";
import { communityBooks } from "../data/mockBook";
import { answerCommunityCourseQuery } from "../screens/communityAssistant";
import { creditCosts, useCredits } from "../features/credits/creditStore";

gsap.registerPlugin(useGSAP);

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

function DiscoveryCompassIcon({
  size = 24,
  ...props
}: ComponentPropsWithoutRef<"svg"> & { size?: number | string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <circle cx="12" cy="12" r="9" />
      <path d="m15.6 8.4-2.2 5-5 2.2 2.2-5 5-2.2Z" />
      <circle cx="12" cy="12" r="0.75" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function PrimaryNav({
  active,
  expanded,
  go,
  onToggleExpanded
}: {
  active: Screen;
  expanded: boolean;
  go: (screen: Screen) => void;
  onToggleExpanded: () => void;
}) {
  const navRef = useRef<HTMLElement>(null);
  const selectionRef = useRef<HTMLSpanElement>(null);
  const previousActiveIndexRef = useRef<number | null>(null);
  const previousLayoutVersionRef = useRef(0);
  const previousExpandedRef = useRef(expanded);
  const [layoutVersion, setLayoutVersion] = useState(0);
  const reducedMotion = useReducedMotion();
  const { loadedBookId, openSheet, uploadedFile, courses: courseStore } = useAppContext();
  const isStudyActive = active === "study" || active === "book";
  const currentCourse = courseStore.state.courses.find((course) => course.id === courseStore.state.activeCourseId);
  const currentCourseTitle = currentCourse?.name
    ?? (uploadedFile?.bookId === loadedBookId ? uploadedFile.name : null)
    ?? "尚未选择课程";
  const hasCurrentCourse = Boolean(loadedBookId && (currentCourse || uploadedFile?.bookId === loadedBookId));
  const items = [
    {
      screen: "home" as Screen,
      label: "首页",
      icon: Home,
      active: active === "home" || active === "library" || active === "courseDetail"
    },
    {
      screen: "study" as Screen,
      label: "学习",
      icon: BookOpenCheck,
      active: active === "study" || active === "book"
    },
    {
      screen: "community" as Screen,
      label: "发现",
      icon: DiscoveryCompassIcon,
      active: active === "community" || active === "communityBook" || active === "communityImport"
    },
    { screen: "profile" as Screen, label: "我的", icon: User, active: active === "profile" }
  ];
  const studyLinks = [
    { screen: "plan" as Screen, label: "学习计划", icon: CalendarDays },
    { screen: "flashcards" as Screen, label: "闪卡复习", icon: Layers3 },
    { screen: "assignment" as Screen, label: "作业练习", icon: ClipboardCheck },
    { screen: "mistakes" as Screen, label: "错题集", icon: ListChecks },
    { screen: "notes" as Screen, label: "导学笔记", icon: NotebookPen }
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

  useGSAP(() => {
    const navigation = navRef.current;
    const selection = selectionRef.current;
    const target = navigation?.querySelector<HTMLElement>(`[data-nav-index="${activeIndex}"]`);
    if (!navigation || !selection || !target) return;

    const layoutChanged = previousLayoutVersionRef.current !== layoutVersion
      || previousExpandedRef.current !== expanded;
    const canAnimate = previousActiveIndexRef.current !== null
      && previousActiveIndexRef.current !== activeIndex
      && !layoutChanged
      && !reducedMotion;
    const targetPosition = {
      x: target.offsetLeft,
      y: target.offsetTop,
      width: target.offsetWidth,
      height: target.offsetHeight
    };

    gsap.killTweensOf(selection);
    gsap.set(selection, {
      width: targetPosition.width,
      height: targetPosition.height
    });

    if (canAnimate) {
      gsap.to(selection, {
        x: targetPosition.x,
        y: targetPosition.y,
        width: targetPosition.width,
        height: targetPosition.height,
        duration: localSlowMotionDurationSeconds,
        ease: localStateGsapEase,
        overwrite: "auto"
      });
    } else {
      gsap.set(selection, {
        x: targetPosition.x,
        y: targetPosition.y,
        width: targetPosition.width,
        height: targetPosition.height
      });
    }

    previousActiveIndexRef.current = activeIndex;
    previousLayoutVersionRef.current = layoutVersion;
    previousExpandedRef.current = expanded;
  }, { dependencies: [activeIndex, expanded, layoutVersion, reducedMotion], scope: navRef });

  return (
    <nav
      ref={navRef}
      className="primary-nav glass-nav"
      data-active-index={activeIndex}
      data-expanded={expanded ? "true" : "false"}
      data-lg-variant="prominent"
      aria-label="主导航"
    >
      <span ref={selectionRef} className="nav-selection" aria-hidden="true" />
      <div className="nav-topbar">
        <span className="nav-brand">
          <span className="nav-brand-mark"><img src="/assets/brand/sidebar-cloud-icon.png" alt="" /></span>
          <span className="nav-brand-name">BookCourse AI</span>
        </span>
        <button
          className="nav-expand-toggle"
          type="button"
          aria-label={expanded ? "收起侧边栏" : "展开侧边栏"}
          aria-expanded={expanded}
          title={expanded ? "收起侧边栏" : "展开侧边栏"}
          onClick={onToggleExpanded}
        >
          {expanded ? <PanelLeftClose size={20} aria-hidden="true" /> : <PanelLeftOpen size={20} aria-hidden="true" />}
        </button>
      </div>
      {items.map((item, index) => {
        const Icon = item.icon;
        return (
          <Fragment key={item.screen}>
            <button
              className={`nav-item ${item.active ? "active" : ""} ${item.screen === "study" ? "nav-study" : ""}`}
              type="button"
              aria-label={item.label}
              title={item.label}
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
            {item.screen === "study" && expanded && isStudyActive ? (
              <section className="nav-study-context" aria-label="当前课程与学习工具">
                <div className="nav-course-row">
                  <span className="nav-course-mark" aria-hidden="true"><StickerIcon name="BookOpenCheck" size={19} /></span>
                  <span className="nav-course-title" title={currentCourseTitle}>{currentCourseTitle}</span>
                  <button
                    className="nav-course-switch"
                    type="button"
                    aria-label={currentCourse ? `切换课程，当前课程：${currentCourseTitle}` : "选择课程"}
                    title={currentCourse ? "切换课程" : "选择课程"}
                    onClick={() => {
                      if (courseStore.state.courses.length > 0) openSheet({ type: "bookSwitcher" });
                      else go("library");
                    }}
                  >
                    <RefreshCw size={17} aria-hidden="true" />
                  </button>
                </div>
                {hasCurrentCourse ? (
                  <div className="nav-study-links">
                    {studyLinks.map((link) => {
                      const LinkIcon = link.icon;
                      return (
                        <button className="nav-context-link" key={link.screen} type="button" onClick={() => go(link.screen)}>
                          <LinkIcon size={17} aria-hidden="true" />
                          <span>{link.label}</span>
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </section>
            ) : null}
          </Fragment>
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
  rightAction,
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
  rightAction?: ReactNode;
  showBack?: boolean;
  onBack: () => void;
  go: (screen: Screen) => void;
  children: ReactNode;
  hideNav?: boolean;
}) {
  const deviceLayout = useDeviceLayout();
  const [navExpanded, setNavExpanded] = useState(false);
  const isNavExpanded = deviceLayout === "pad" && navExpanded;
  const runtimePlatform = getRuntimePlatform();
  const isNativeAndroid = runtimePlatform === "android";
  const isEmbeddedPreview = new URLSearchParams(window.location.search).get("embedded") === "device-preview";
  const mouseDragScroll = useMouseDragScroll({
    enableVerticalMomentum: !motionReduced && (active === "study" || active === "book" || active === "community"),
    momentumScopeKey: active
  });
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
    <PadChrome>{isEmbeddedPreview && !isNativeAndroid ? <IosStatusBar /> : null}</PadChrome>
  ) : (
    <PhoneChrome>
      {isNativeAndroid ? null : (
        <>
          <IosStatusBar />
          <HomeIndicator />
        </>
      )}
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
        data-nav-expanded={isNavExpanded ? "true" : "false"}
        data-embedded-preview={isEmbeddedPreview ? "true" : "false"}
        data-runtime-platform={runtimePlatform}
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
        onWheelCapture={mouseDragScroll.onWheelCapture}
      >
        {deviceChrome}
        {title ? <HeaderBar title={title} subtitle={subtitle} showBack={showBack} onBack={onBack} rightAction={rightAction} /> : null}
        <main ref={setMainNode} tabIndex={-1} className={`screen-content ${title ? "with-header" : ""} ${hideNav ? "without-nav" : ""}`} data-screen={active}>{children}</main>
        {active !== "study" && active !== "book" && active !== "communityBook" && active !== "onboarding" && active !== "courseSetup" && active !== "settings" ? (
          <GlobalAIAssistant
            active={active}
            containerElement={appShellElement}
            containerRef={appShellRef}
            homeLayout={active === "home"}
            reducedMotion={motionReduced}
          />
        ) : null}
        {!hideNav ? (
          <PrimaryNav
            active={active}
            expanded={isNavExpanded}
            go={go}
            onToggleExpanded={() => setNavExpanded((value) => !value)}
          />
        ) : null}
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
  citations?: Citation[];
  courseIds?: string[];
  retrieval?: RagRetrievalSummary;
  role: "ai" | "user";
  text: string;
};

type AiAssistantContent = {
  contextLabel: string;
  contextMeta: string;
  contextTitle: string;
  contextVisible: boolean;
  inputPlaceholder: string;
  introDescription: string;
  introTitle: string;
  modes: string[];
  suggestionTitle: string;
  suggestions: string[];
  suggestionsVisible: boolean;
};

const discoveryAssistantContent: AiAssistantContent = {
  contextLabel: "",
  contextMeta: "",
  contextTitle: "",
  contextVisible: false,
  inputPlaceholder: "输入课程名、学科或学习目标...",
  introDescription: "输入课程名、学科、年级或学习目标，我会从发现页课程中推荐。",
  introTitle: "想学什么？我来帮你选课",
  modes: ["按学科推荐", "按目标推荐", "查看全部课程"],
  suggestionTitle: "",
  suggestions: [],
  suggestionsVisible: false
};
const learningAssistantPresentation: Pick<
  AiAssistantContent,
  "contextVisible" | "inputPlaceholder" | "introDescription" | "introTitle" | "suggestionsVisible"
> = {
  contextVisible: true,
  inputPlaceholder: "问教材、问错题、问计划...",
  introDescription: "学习相关的问题，都可以问我哦。",
  introTitle: "Hi，我是你的“AI学习助手”～",
  suggestionsVisible: true
};

function getCitationPrintedPage(citation: Citation) {
  const printedPages = citation.source_metadata.printed_pages;
  if (Array.isArray(printedPages)) {
    const firstPage = Number(printedPages[0]);
    if (Number.isFinite(firstPage) && firstPage > 0) return firstPage;
  }
  const labelMatch = citation.location_label?.match(/教材第\s*(\d+)\s*页/);
  const labelPage = Number(labelMatch?.[1]);
  return Number.isFinite(labelPage) && labelPage > 0 ? labelPage : null;
}

function getUniqueCitationPages(citations: Citation[]) {
  const seen = new Set<string>();
  return citations.filter((citation) => {
    const printedPage = getCitationPrintedPage(citation);
    const bookId = citation.book_id ?? String(citation.source_metadata.book_id ?? "");
    const key = `${bookId}:${printedPage ? `printed:${printedPage}` : `pdf:${citation.page}`}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

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
  const orb = shell.querySelector<HTMLElement>(".ai-orb");
  const orbWidth = orb?.offsetWidth || 62;
  const orbHeight = orb?.offsetHeight || 72;
  const verticalInset = 12;
  const baseTopMin = Math.min(bottom - orbHeight, visibleTop + safeTop + verticalInset);
  const topMax = Math.max(baseTopMin, bottom - safeBottom - navHeight - verticalInset - orbHeight);
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
  const credits = useCredits();
  const {
    activeChapterId,
    courses: courseStore,
    sourceSummaries,
    generatedLessons,
    go,
    loadedBookId,
    openSourcePage,
    parsedChapters,
    selectCommunityBook,
    uploadedFile
  } = useAppContext();
  const currentCourse = courseStore.state.courses.find((course) => course.id === courseStore.state.activeCourseId);
  const currentBookIds = courseSourceBookIds(currentCourse, courseStore.state.resources);
  const ragBookId = loadedBookId && currentBookIds.includes(loadedBookId) ? loadedBookId
    : currentBookIds.find((bookId) => sourceSummaries.some((source) => source.book_id === bookId && source.status === "ready")) ?? "";
  const activeCourse = sourceSummaries.find((course) => course.book_id === ragBookId)
    ?? null;
  const activeChapter = parsedChapters?.find((chapter) => chapter.chapter_id === activeChapterId)
    ?? parsedChapters?.[0]
    ?? null;
  const activeLesson = activeChapter
    ? generatedLessons?.find((lesson) => lesson.chapter_id === activeChapter.chapter_id) ?? null
    : generatedLessons?.[0] ?? null;
  const assistantContent = useMemo<AiAssistantContent>(() => {
    if (active === "community") return discoveryAssistantContent;
    if (!activeChapter && !activeLesson) {
      const courseTitle = currentCourse?.name ?? "尚未选择课程";
      return {
        ...learningAssistantPresentation,
        contextLabel: "当前课程",
        contextMeta: activeCourse ? `${currentBookIds.length} 份课程资料` : "添加资料后可依据课程原文回答",
        contextTitle: courseTitle,
        modes: ["知识点讲解", "原文问答", "复习计划"],
        suggestionTitle: "你可能感兴趣",
        suggestions: [
          `概括《${courseTitle}》的核心知识`,
          "请根据教材原文给我出一道复习题"
        ]
      };
    }
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
      ...learningAssistantPresentation,
      contextLabel: "当前课程",
      contextMeta: pageLabel,
      contextTitle: title,
      modes: ["本节讲解", "举例理解", "随堂测验"],
      suggestionTitle: "你可能感兴趣",
      suggestions: [
        `用一句话解释“${primaryConcept}”`,
        secondaryConcept
          ? `比较“${primaryConcept}”和“${secondaryConcept}”`
          : `围绕“${title}”给我出一道题`
      ]
    };
  }, [active, activeChapter, activeCourse, activeLesson, uploadedFile, currentCourse?.name, currentBookIds.length]);
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [creditError, setCreditError] = useState<string | null>(null);
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
  const conversationEpochRef = useRef(0);
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

  const openCitationSource = useCallback((citation: Citation) => {
    const printedPage = getCitationPrintedPage(citation);
    const title = citation.chapter_title || "教材原文";
    const sourceText = getCitationSourceText(citation);
    requestDialogClose();
    openSourcePage({
      courseId: currentCourse?.id,
      bookId: citation.book_id ?? String(citation.source_metadata.book_id ?? ragBookId),
      title,
      pageStart: citation.page,
      pageEnd: citation.page,
      printedPageStart: printedPage,
      printedPageEnd: printedPage,
      sourceText: sourceText || undefined,
      from: active
    });
  }, [active, openSourcePage, ragBookId, requestDialogClose, currentCourse?.id]);

  useEffect(() => {
    const closeForNativeBack: EventListener = (event) => {
      if (!open) return;
      event.preventDefault();
      requestDialogClose();
    };
    window.addEventListener("bookcourse:native-back", closeForNativeBack);
    return () => window.removeEventListener("bookcourse:native-back", closeForNativeBack);
  }, [open, requestDialogClose]);

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
    conversationEpochRef.current += 1;
    setInput("");
    setMessages([]);
    setLoading(false);
    setCreditError(null);
  }, [active, activeChapter?.chapter_id, activeLesson?.lesson_id, ragBookId, currentCourse?.id]);

  useEffect(() => {
    if (active === "community" || !dialogVisible || ragBookId !== "book_biology_2") return;
  }, [active, dialogVisible, ragBookId]);

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
    const useLeftDock = shell.clientHeight < 600 && shell.clientWidth > shell.clientHeight;

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
    if (active !== "community" && !ragBookId) {
      setCreditError("请先为当前课程添加可阅读的资料，再使用原文问答。");
      return;
    }
    if(active==="community") {
      const matches=communityCatalog.filter(book=>`${book.title} ${book.description}`.includes(text));
      setMessages(items=>[...items,{role:"user",text},{role:"ai",courseIds:matches.map(book=>book.id),text:matches.length?`当前共享资料中找到 ${matches.length} 项相关内容。`:"当前共享资料未找到匹配内容，请试试课程标题关键词。"}]);setInput("");return;
    }
    const conversationEpoch = conversationEpochRef.current;
    let reservationId: string;
    try {
      reservationId = await credits.reserve("chat");
      setCreditError(null);
    } catch (error) {
      setCreditError(error instanceof Error ? error.message : "积分不足，暂时无法提问。");
      return;
    }
    const history = messages.map((message) => ({
      content: message.text,
      role: message.role === "ai" ? "assistant" : "user"
    }));
    setMessages((items) => [...items, { role: "user", text }]);
    setInput("");
    setLoading(true);
    try {
      const result = await bookcourseRepository.queryRag({
        reservation_id: reservationId,
        ...courseRagScope(courseStore.state, ragBookId),
        book_id: ragBookId,
        chapter_id: activeChapter?.chapter_id ?? null,
        history,
        question: text,
        context: {
          book_title: activeCourse?.title ?? uploadedFile?.name ?? null,
          chapter_title: activeChapter?.source_title ?? null,
          section_title: activeLesson?.title ?? activeChapter?.ai_title ?? null,
          page_label: assistantContent.contextMeta,
          key_concepts: activeLesson?.key_concepts.filter(Boolean).slice(0, 8) ?? []
        }
      });
      if (conversationEpoch !== conversationEpochRef.current) {
        await credits.refund(reservationId);
        return;
      }
      if(result.retrieval?.status === "no_match")await credits.refund(reservationId);else await credits.complete(reservationId);
      setMessages((items) => [
        ...items,
        {
          citations: result.citations,
          retrieval: result.retrieval,
          role: "ai",
          text: result.answer
        }
      ]);
    } catch (error) {
      await credits.refund(reservationId);
      if (conversationEpoch !== conversationEpochRef.current) return;
      setMessages((items) => [
        ...items,
        {
          role: "ai",
          text: error instanceof Error
            ? `这次没有完成回答：${error.message}`
            : "这次没有完成回答，请稍后再试。"
        }
      ]);
    } finally {
      if (conversationEpoch === conversationEpochRef.current) setLoading(false);
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
          creditBalance={credits.balance}
          creditError={creditError}
          messages={messages}
          reducedMotion={reducedMotion}
          onOpenCitation={openCitationSource}
          onOpenCourse={(courseId) => {
            requestDialogClose();
            selectCommunityBook(courseId);
            go("communityBook");
          }}
          onClose={requestDialogClose}
          onAnimationEnd={dialogPresence.onAnimationEnd}
          onAnimationCancel={dialogPresence.onAnimationCancel}
          setInput={setInput}
          submitMessage={submitMessage}
      />
    </>
  );
}

function AiCommunityCourseLinks({
  courseIds,
  onOpenCourse
}: {
  courseIds: string[];
  onOpenCourse: (courseId: string) => void;
}) {
  const courses = courseIds
    .map((courseId) => communityBooks.find((book) => book.id === courseId))
    .filter((book) => book !== undefined);
  if (courses.length === 0) return null;

  return (
    <nav className="ai-message-course-links" aria-label="发现页课程链接">
      <span>{courses.length === 1 ? "相关课程" : `相关课程 · ${courses.length} 门`}</span>
      {courses.map((book) => (
        <button
          type="button"
          key={book.id}
          aria-label={`查看课程：${book.catalogTitle}`}
          onClick={() => onOpenCourse(book.id)}
        >
          <span>
            <strong>{book.catalogTitle}</strong>
            <small>{book.grade} · {book.subject} · {book.version}</small>
          </span>
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      ))}
    </nav>
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
  creditBalance,
  creditError,
  messages,
  reducedMotion,
  onOpenCitation,
  onOpenCourse,
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
  creditBalance: number;
  creditError: string | null;
  messages: AiAssistantMessage[];
  reducedMotion: boolean;
  onOpenCitation: (citation: Citation) => void;
  onOpenCourse: (courseId: string) => void;
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
  const latestAiMessageRef = useRef<HTMLDivElement | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const anchoredAiMessageRef = useRef<AiAssistantMessage | null>(null);
  const titleId = useId();
  const hasConversation = messages.length > 0 || loading;
  const showSuggestions = !hasConversation && input.trim().length === 0;
  const latestAiMessageIndex = messages.reduce(
    (latestIndex, message, index) => message.role === "ai" ? index : latestIndex,
    -1
  );
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

  useEffect(() => {
    if (!visible) return;

    const messageEnd = messageEndRef.current;
    if (!hasConversation) {
      messageEnd?.style.removeProperty("height");
      anchoredAiMessageRef.current = null;
      return;
    }

    const latestMessage = messages.at(-1);
    if (latestMessage?.role === "ai") {
      if (anchoredAiMessageRef.current === latestMessage) return;
      anchoredAiMessageRef.current = latestMessage;
      const latestAiMessage = latestAiMessageRef.current;
      const messageList = latestAiMessage?.closest<HTMLElement>(".ai-message-list");
      const dialogScroller = latestAiMessage?.closest<HTMLElement>(".ai-dialog-scroll");
      const messageListStyle = messageList ? getComputedStyle(messageList) : null;
      const messageListIsScrollViewport = Boolean(
        messageList
        && messageListStyle
        && messageListStyle.maxHeight !== "none"
        && /auto|scroll/.test(messageListStyle.overflowY)
      );
      const scroller = messageListIsScrollViewport ? messageList : dialogScroller;
      if (messageEnd && latestAiMessage && scroller) {
        const readingTailHeight = Math.max(scroller.clientHeight - latestAiMessage.offsetHeight, 1);
        messageEnd.style.height = `${readingTailHeight}px`;
      }
      latestAiMessage?.scrollIntoView({
        behavior: reducedMotion ? "auto" : "smooth",
        block: "start"
      });
      return;
    }

    messageEnd?.style.removeProperty("height");
    messageEnd?.scrollIntoView({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "end"
    });
  }, [hasConversation, loading, messages, reducedMotion, visible]);

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
              <StickerIcon name="Bot" size={18} aria-hidden="true" />
            </span>
            <h2 id={titleId}>AI 导学助手</h2>
          </div>
          <button className="icon-button ai-close" type="button" aria-label="收起 AI 助手" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <p className={`ai-credit-summary ${creditError ? "error" : ""}`} role={creditError ? "alert" : undefined} aria-live="polite">{creditError ?? `剩余 ${creditBalance} 积分 · 每次提问消耗 ${creditCosts.chat} 积分`}</p>
        <div className="ai-dialog-scroll">
          <div className="ai-intro">
            <p>下拉查看历史对话</p>
            <h3>{content.introTitle}</h3>
            <span>{content.introDescription}</span>
          </div>
          {content.contextVisible ? (
            <section className="ai-current-book">
              <div className="ai-current-book-head">
                <strong>{content.contextLabel}</strong>
                <span>{content.contextMeta}</span>
              </div>
              <div className="ai-current-book-body">
                <h3>{content.contextTitle}</h3>
              </div>
            </section>
          ) : null}
          {content.suggestionsVisible && showSuggestions ? (
            <section className="ai-suggestions" aria-labelledby="ai-suggest-title">
              <p className="ai-suggest-title" id="ai-suggest-title">{content.suggestionTitle}</p>
              <div className="ai-suggest-list">
                {content.suggestions.map((item) => (
                  <button disabled={loading} type="button" key={item} onClick={() => setInput(item)}>
                    <MessageCircle size={15} aria-hidden="true" />
                    {item}
                  </button>
                ))}
              </div>
            </section>
          ) : null}
          <div className="ai-message-list" aria-live="polite" aria-busy={loading}>
            {messages.map((message, index) => (
              <div
                ref={message.role === "ai" && index === latestAiMessageIndex ? latestAiMessageRef : undefined}
                className={`ai-message-row ${message.role}`}
                key={`${message.role}-${index}`}
              >
                <span className="ai-message-avatar" aria-hidden="true">
                  {message.role === "ai" ? <StickerIcon name="Bot" size={15} /> : <StickerIcon name="User" size={15} />}
                </span>
                <div className={`ai-message ${message.role}`}>
                  {message.role === "ai" ? <span className="ai-message-author">AI 导学助手</span> : null}
                  <p>{message.text}</p>
                  {message.citations?.length ? (
                    <div className="ai-message-citations" aria-label="教材来源">
                      <div className="ai-message-citations-head">
                        <StickerIcon name="BookOpenCheck" size={14} aria-hidden="true" />
                        <strong>教材原文依据</strong>
                        <span>{getUniqueCitationPages(message.citations).length} 处</span>
                      </div>
                      {getUniqueCitationPages(message.citations).map((citation) => {
                        const printedPage = getCitationPrintedPage(citation);
                        const filename = typeof citation.source_metadata.filename === "string" ? citation.source_metadata.filename : "";
                        const label = printedPage ? `教材第 ${printedPage} 页` : filename && !/\.pdf$/i.test(filename) ? `原文第 ${citation.page} 页` : `PDF 第 ${citation.page} 页`;
                        return (
                          <article
                            className="ai-message-citation-item"
                            key={citation.chunk_id || (printedPage ? `printed:${printedPage}` : `pdf:${citation.page}`)}
                          >
                            <strong>{filename ? `${filename} · ` : ""}{citation.chapter_title || "教材原文"}</strong>
                            <span>{label}</span>
                            {citation.quote ? <blockquote>{citation.quote}</blockquote> : null}
                            <button
                              type="button"
                              aria-label={`查看${label}教材原文`}
                              onClick={() => onOpenCitation(citation)}
                            >
                              <StickerIcon name="BookOpenCheck" size={14} aria-hidden="true" />
                              <span>查看教材原文</span>
                            </button>
                          </article>
                        );
                      })}
                    </div>
                  ) : message.retrieval?.attempted && message.retrieval.status !== "hit" ? (
                    <div className="ai-message-rag-status" role="status">
                      <StickerIcon name="BookOpenCheck" size={13} aria-hidden="true" />
                      <span>{message.retrieval.status === "unavailable"
                        ? "教材资料暂时不可用"
                        : "当前教材未检索到可靠原文"}</span>
                    </div>
                  ) : null}
                  {message.role === "ai" && message.courseIds?.length ? (
                    <AiCommunityCourseLinks courseIds={message.courseIds} onOpenCourse={onOpenCourse} />
                  ) : null}
                </div>
              </div>
            ))}
            {loading ? (
              <div className="ai-message-row ai ai-message-row-loading" role="status">
                <span className="ai-message-avatar" aria-hidden="true">
                  <StickerIcon name="Bot" size={15} />
                </span>
                <div className="ai-message ai">
                  <span className="ai-message-author">AI 导学助手</span>
                  <p>正在准备回答…</p>
                </div>
              </div>
            ) : null}
            <div ref={messageEndRef} className="ai-message-end" aria-hidden="true" />
          </div>
          {!hasConversation ? (
            <div className="ai-mode-row">
              {content.modes.map((item) => (
                <button disabled={loading} type="button" key={item} onClick={() => setInput(item)}>
                  {item}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <form className="ai-compose" onSubmit={submitMessage}>
          <input
            ref={inputRef}
            value={input}
            aria-label="向 AI 助手提问"
            disabled={loading}
            onChange={(event) => setInput(event.target.value)}
            placeholder={content.inputPlaceholder}
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
  openLabel = "查看该页",
  imageMotion = false
}: {
  title: string;
  page: string;
  quote: string;
  image?: string;
  onOpen: () => void;
  openLabel?: string;
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
        <StickerIcon name="BookOpenCheck" size={20} aria-hidden="true" />
      </div>
      <div>
        <p className="citation-meta">{title} · {page}</p>
        <p className="citation-quote">{quote}</p>
        <button className="inline-link" data-sheet-replacement="source" type="button" onClick={onOpen}>
          {openLabel}
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