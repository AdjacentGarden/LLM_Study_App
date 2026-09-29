import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ArrowLeft, BookOpenCheck, Compass, Home, Plus, User, X, Bot } from "lucide-react";
import { IosStatusBar } from "../demo/components/IosStatusBar";
import { PhoneChrome } from "../demo/layouts/PhoneChrome";
import { PadChrome } from "../demo/layouts/PadChrome";
import { useDeviceLayout } from "../demo/layouts/useDeviceLayout";
import { useReducedMotion } from "../demo/motion/useReducedMotion";
import { useMouseDragScroll } from "../demo/hooks/useMouseDragScroll";
import { DemoAiOrb } from "./DemoAiOrb";

export type DemoTab = "home" | "study" | "community" | "profile";
const navItems: { tab: DemoTab; label: string; Icon: typeof Home }[] = [
  { tab: "home", label: "首页", Icon: Home },
  { tab: "study", label: "学习", Icon: BookOpenCheck },
  { tab: "community", label: "发现", Icon: Compass },
  { tab: "profile", label: "我的", Icon: User },
];

function DemoNav({ active, go }: { active: DemoTab; go: (tab: DemoTab) => void }) {
  const navRef = useRef<HTMLElement>(null);
  const selectionRef = useRef<HTMLSpanElement>(null);
  const previousIndex = useRef<number | null>(null);
  const reducedMotion = useReducedMotion();
  const activeIndex = navItems.findIndex(item => item.tab === active);
  useGSAP(() => {
    const selection = selectionRef.current;
    const target = navRef.current?.querySelector<HTMLElement>(`[data-nav-index="${activeIndex}"]`);
    if (!selection || !target) return;
    gsap.killTweensOf(selection);
    const position = { x: target.offsetLeft, y: target.offsetTop, width: target.offsetWidth, height: target.offsetHeight };
    if (previousIndex.current !== null && previousIndex.current !== activeIndex && !reducedMotion) {
      gsap.to(selection, { ...position, duration: .34, ease: "power2.out", overwrite: "auto" });
    } else gsap.set(selection, position);
    previousIndex.current = activeIndex;
  }, { dependencies: [activeIndex, reducedMotion], scope: navRef });
  return <nav ref={navRef} className="primary-nav glass-nav" data-active-index={activeIndex} data-lg-variant="prominent" aria-label="主导航">
    <span ref={selectionRef} className="nav-selection" aria-hidden="true" />
    {navItems.map(({ tab, label, Icon }, index) => <button key={tab} className={`nav-item ${active === tab ? "active" : ""} ${tab === "study" ? "nav-study" : ""}`}
      type="button" aria-current={active === tab ? "page" : undefined} data-nav-index={index} data-motion-active={active === tab ? "true" : "false"} data-motion-nav-kind="standard" onClick={() => go(tab)}>
      <span className="nav-icon"><span className="nav-icon-motion"><Icon size={22} aria-hidden="true" /></span></span><span className="nav-label">{label}</span>
    </button>)}
  </nav>;
}

export function DemoShell({ active, title, subtitle, onBack, onTab, onUpload, assistant, children, notice, scrollKey }: {
  active: DemoTab | null;
  title?: string;
  subtitle?: string;
  onBack: () => void;
  onTab: (tab: DemoTab) => void;
  onUpload: () => void;
  assistant: (close: () => void) => ReactNode;
  children: ReactNode;
  notice?: string;
  scrollKey: string;
}) {
  const layout = useDeviceLayout();
  const mainRef = useRef<HTMLElement>(null);
  const [shell, setShell] = useState<HTMLDivElement | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const assistantRef = useRef<HTMLElement>(null);
  const setShellNode = useCallback((node: HTMLDivElement | null) => setShell(node), []);
  const reducedMotion = useReducedMotion();
  const dragScroll = useMouseDragScroll({ enableVerticalMomentum: !reducedMotion && (active === "study" || active === "community"), momentumScopeKey: active ?? title ?? "detail" });
  useLayoutEffect(() => {
    if (!shell) return;
    const sync = () => {
      const bounds = shell.getBoundingClientRect();
      const top = Math.max(0, Math.min(bounds.height, (window.visualViewport?.offsetTop ?? 0) - bounds.top));
      const bottom = Math.max(top, Math.min(bounds.height, top + (window.visualViewport?.height ?? bounds.height)));
      shell.style.setProperty("--overlay-visual-top", `${Math.round(top)}px`);
      shell.style.setProperty("--overlay-visual-height", `${Math.round(bottom - top)}px`);
      shell.style.setProperty("--overlay-visual-bottom", `${Math.round(bounds.height - bottom)}px`);
    };
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(shell);
    window.visualViewport?.addEventListener("resize", sync);
    return () => { observer.disconnect(); window.visualViewport?.removeEventListener("resize", sync); };
  }, [shell]);
  useEffect(() => { mainRef.current?.scrollTo({ top: 0, behavior: "instant" }); }, [scrollKey]);
  useEffect(() => {
    if (!assistantOpen) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = requestAnimationFrame(() => assistantRef.current?.querySelector<HTMLInputElement>(".ai-compose input")?.focus());
    return () => {
      cancelAnimationFrame(frame);
      if (previousFocus?.isConnected && previousFocus.getClientRects().length > 0) previousFocus.focus();
    };
  }, [assistantOpen]);
  const closeAssistant = () => setAssistantOpen(false);
  function handleAssistantKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { event.preventDefault(); closeAssistant(); return; }
    if (event.key !== "Tab") return;
    const items = Array.from(assistantRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])") ?? [])
      .filter(item => item.getClientRects().length > 0);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  const chrome = layout === "pad" ? <PadChrome /> : <PhoneChrome><IosStatusBar /><div className="home-indicator" aria-hidden="true"><span /></div></PhoneChrome>;
  return <div className="stage"><div ref={setShellNode} className="app-shell" role="application" aria-label="云径学习应用"
    data-active-screen={active ?? "detail"} data-device-layout={layout} data-runtime-platform="web" data-motion-reduced={reducedMotion ? "true" : "false"}
    data-mouse-dragging={dragScroll.dragging ? "true" : "false"}
    onClickCapture={event => { dragScroll.consumeClick(event); }}
    onLostPointerCaptureCapture={dragScroll.onLostPointerCaptureCapture} onPointerCancelCapture={dragScroll.onPointerCancelCapture}
    onPointerDownCapture={dragScroll.onPointerDownCapture} onPointerMoveCapture={dragScroll.onPointerMoveCapture}
    onPointerUpCapture={dragScroll.onPointerUpCapture} onWheelCapture={dragScroll.onWheelCapture}>
    {chrome}
    {title ? <header className="header-bar"><div className="header-glass"><button className="icon-button" type="button" aria-label="返回" onClick={onBack}><ArrowLeft size={21} aria-hidden="true" /></button>
      <div className="header-title"><h1>{title}</h1>{subtitle ? <p>{subtitle}</p> : null}</div><div className="header-right"><div className="header-spacer" /></div></div></header> : null}
    <main ref={mainRef} tabIndex={-1} className={`screen-content ${title ? "with-header" : ""} ${active ? "" : "without-nav"}`} data-screen={active ?? "detail"}>{children}</main>
    {active !== "study" && <DemoAiOrb shell={shell} reducedMotion={reducedMotion} open={assistantOpen} onAsk={() => setAssistantOpen(true)} />}
    {active ? <DemoNav active={active} go={onTab} /> : null}
    {active === "home" && <button className="demo-port-upload-fab" type="button" aria-label="上传教材" onClick={onUpload}><Plus size={18} /></button>}
    {notice ? <div className="demo-port-toast" role="status">{notice}</div> : null}
    {assistantOpen && <div className="ai-overlay-layer"><button className="ai-overlay-scrim" type="button" aria-label="关闭 AI 助手背景" onClick={closeAssistant} />
      <aside ref={assistantRef} id="ai-assistant-dialog" className="ai-overlay glass-sheet demo-port-ai-overlay" role="dialog" aria-modal="true" aria-labelledby="demo-port-ai-title" onKeyDown={handleAssistantKeyDown}>
        <div className="ai-overlay-head"><div><span className="ai-avatar"><Bot size={18} aria-hidden="true" /></span><h2 id="demo-port-ai-title">AI 导学助手</h2></div>
          <button className="icon-button ai-close" type="button" aria-label="收起 AI 助手" onClick={closeAssistant}><X size={18} aria-hidden="true" /></button></div>
        {assistant(closeAssistant)}
      </aside></div>}
  </div></div>;
}
