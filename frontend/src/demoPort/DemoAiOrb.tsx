import { useLayoutEffect, useRef, useState, type PointerEvent } from "react";

type Side = "left" | "right";
type Position = { side: Side; top: number | null };
type Interaction = "idle" | "pressed" | "dragging";

const idleImages: Record<Side, string> = {
  left: "/assets/brand/cloud-mascot-ai-chat-edge-left-ui.webp",
  right: "/assets/brand/cloud-mascot-ai-chat-edge-ui.webp",
};
const activeImages: Record<Exclude<Interaction, "idle">, string> = {
  pressed: "/assets/brand/cloud-mascot-ai-chat-edge-pressed-ui.webp",
  dragging: "/assets/brand/cloud-mascot-ai-chat-airborne-ui.webp",
};

function cssPixels(style: CSSStyleDeclaration, name: string) {
  return Number.parseFloat(style.getPropertyValue(name)) || 0;
}

function clampTop(shell: HTMLElement, orb: HTMLElement, requested: number) {
  const style = getComputedStyle(shell);
  const viewport = window.visualViewport;
  const bounds = shell.getBoundingClientRect();
  const visibleTop = Math.max(0, (viewport?.offsetTop ?? 0) - bounds.top);
  const visibleBottom = Math.min(bounds.height, visibleTop + (viewport?.height ?? bounds.height));
  const min = Math.min(visibleBottom - orb.offsetHeight, visibleTop + cssPixels(style, "--safe-area-top") + 12);
  const max = Math.max(min, visibleBottom - cssPixels(style, "--safe-area-bottom") - cssPixels(style, "--primary-nav-height") - orb.offsetHeight - 12);
  return Math.min(max, Math.max(min, requested));
}

export function DemoAiOrb({ shell, reducedMotion, open, onAsk }: { shell: HTMLElement | null; reducedMotion: boolean; open: boolean; onAsk: () => void }) {
  const orbRef = useRef<HTMLButtonElement>(null);
  const drag = useRef({ pointerId: -1, startX: 0, startY: 0, moved: false });
  const suppressClick = useRef(false);
  const settleFrame = useRef<number | null>(null);
  const [position, setPosition] = useState<Position>({ side: "right", top: null });
  const [interaction, setInteraction] = useState<Interaction>("idle");

  useLayoutEffect(() => {
    if (!shell) return;
    const constrain = () => {
      const orb = orbRef.current;
      if (!orb) return;
      setPosition(current => {
        const defaultTop = shell.clientHeight * .4;
        const top = clampTop(shell, orb, current.top ?? defaultTop);
        return current.top === top ? current : { ...current, top };
      });
    };
    constrain();
    const observer = new ResizeObserver(constrain);
    observer.observe(shell);
    window.visualViewport?.addEventListener("resize", constrain);
    window.addEventListener("orientationchange", constrain);
    return () => {
      observer.disconnect();
      window.visualViewport?.removeEventListener("resize", constrain);
      window.removeEventListener("orientationchange", constrain);
      if (settleFrame.current !== null) cancelAnimationFrame(settleFrame.current);
    };
  }, [shell]);

  function clearMotion(orb: HTMLButtonElement) {
    if (settleFrame.current !== null) cancelAnimationFrame(settleFrame.current);
    settleFrame.current = null;
    orb.removeAttribute("data-ai-orb-settling");
    orb.style.setProperty("--ai-orb-drag-x", "0px");
    orb.style.setProperty("--ai-orb-drag-y", "0px");
    orb.style.setProperty("--ai-orb-settle-x", "0px");
    orb.style.setProperty("--ai-orb-settle-y", "0px");
  }

  function onPointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    clearMotion(event.currentTarget);
    suppressClick.current = false;
    drag.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
    setInteraction("pressed");
  }

  function onPointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (drag.current.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.current.startX;
    const dy = event.clientY - drag.current.startY;
    if (Math.hypot(dx, dy) > 6) drag.current.moved = true;
    if (!drag.current.moved) return;
    setInteraction("dragging");
    event.currentTarget.style.setProperty("--ai-orb-drag-x", `${dx}px`);
    event.currentTarget.style.setProperty("--ai-orb-drag-y", `${dy}px`);
  }

  function onPointerUp(event: PointerEvent<HTMLButtonElement>) {
    if (drag.current.pointerId !== event.pointerId) return;
    const orb = event.currentTarget;
    if (orb.hasPointerCapture(event.pointerId)) orb.releasePointerCapture(event.pointerId);
    drag.current.pointerId = -1;
    setInteraction("idle");
    if (!drag.current.moved || !shell) {
      clearMotion(orb);
      return;
    }
    suppressClick.current = true;
    const before = orb.getBoundingClientRect();
    const bounds = shell.getBoundingClientRect();
    const side: Side = before.left + before.width / 2 < bounds.left + bounds.width / 2 ? "left" : "right";
    const top = clampTop(shell, orb, before.top - bounds.top);
    orb.style.top = `${top}px`;
    orb.style.left = side === "left" ? "var(--ai-orb-left-inset)" : "auto";
    orb.style.right = side === "right" ? "var(--ai-orb-right-inset)" : "auto";
    orb.style.setProperty("--ai-orb-drag-x", "0px");
    orb.style.setProperty("--ai-orb-drag-y", "0px");
    setPosition({ side, top });
    if (reducedMotion) return;
    const after = orb.getBoundingClientRect();
    orb.style.setProperty("--ai-orb-settle-x", `${before.left - after.left}px`);
    orb.style.setProperty("--ai-orb-settle-y", `${before.top - after.top}px`);
    orb.setAttribute("data-ai-orb-settling", "true");
    settleFrame.current = requestAnimationFrame(() => {
      settleFrame.current = null;
      orb.removeAttribute("data-ai-orb-settling");
      orb.style.setProperty("--ai-orb-settle-x", "0px");
      orb.style.setProperty("--ai-orb-settle-y", "0px");
    });
  }

  function onPointerCancel(event: PointerEvent<HTMLButtonElement>) {
    if (drag.current.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current.pointerId = -1;
    suppressClick.current = true;
    setInteraction("idle");
    clearMotion(event.currentTarget);
  }

  return <button ref={orbRef} className={`ai-orb ai-orb-mascot glass-button ${interaction === "dragging" ? "dragging" : ""}`}
    type="button" aria-label={interaction === "dragging" ? "正在拖动 AI 助手入口" : "打开 AI 助手"}
    aria-controls="ai-assistant-dialog" aria-expanded={open} aria-haspopup="dialog" tabIndex={open ? -1 : undefined}
    data-side={position.side} data-interaction={interaction} data-ai-orb-hidden={open ? "true" : "false"} data-mouse-drag-scroll="ignore"
    style={{ top: position.top === null ? "40%" : `${position.top}px`, left: position.side === "left" ? "var(--ai-orb-left-inset)" : "auto", right: position.side === "right" ? "var(--ai-orb-right-inset)" : "auto" }}
    onClick={() => { if (suppressClick.current) suppressClick.current = false; else onAsk(); }}
    onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerCancel}>
    <img src={interaction === "idle" ? idleImages[position.side] : activeImages[interaction]} alt="" aria-hidden="true" decoding="async" draggable={false} />
  </button>;
}
