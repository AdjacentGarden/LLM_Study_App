import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { Screen } from "../types/app";
import {
  createScreenTransitionSnapshot,
  reconcileScreenTransition,
  settleScreenTransition,
  type ScreenDirection,
  type ScreenSurface
} from "./screenTransitionMachine";
import { globalMotionFallbackMs } from "./timing";

export type ScreenTransitionProps = {
  screenKey: Screen;
  direction: ScreenDirection;
  nonce: number;
  initial: boolean;
  reducedMotion: boolean;
  children: ReactNode;
};

export const screenTransitionEnterAnimationNames = [
  "motion-screen-phone-forward-in",
  "motion-screen-phone-back-in",
  "motion-screen-replace-in",
  "motion-screen-tablet-in",
  "motion-screen-short-forward-in",
  "motion-screen-short-back-in"
] as const;

export const screenTransitionExitAnimationNames = [
  "motion-screen-phone-forward-out",
  "motion-screen-phone-back-out",
  "motion-screen-replace-out",
  "motion-screen-tablet-out",
  "motion-screen-short-forward-out",
  "motion-screen-short-back-out"
] as const;

/** @deprecated Prefer the explicit enter/exit animation-name sets. */
export const screenTransitionAnimationNames = screenTransitionEnterAnimationNames;

export function ScreenTransition({
  screenKey,
  direction,
  nonce,
  initial,
  reducedMotion,
  children
}: ScreenTransitionProps) {
  const request = { nonce, screen: screenKey, direction, initial, reducedMotion };
  const [snapshot, setSnapshot] = useState(() => createScreenTransitionSnapshot(request));
  const renderedSnapshot = reconcileScreenTransition(snapshot, request);
  const currentSurfaceRef = useRef<HTMLDivElement | null>(null);
  const contentByNonceRef = useRef(new Map<number, ReactNode>());

  // Screen content is retained by generation rather than by a keyed wrapper.
  // Updating the current entry during render keeps same-screen data fresh; the
  // entry becomes a frozen outgoing tree only when a later nonce arrives.
  contentByNonceRef.current.set(nonce, children);

  useLayoutEffect(() => {
    setSnapshot((current) => reconcileScreenTransition(current, request));
  }, [direction, initial, nonce, reducedMotion, screenKey]);

  useEffect(() => {
    const retainedNonces = new Set([
      renderedSnapshot.current.nonce,
      renderedSnapshot.previous?.nonce
    ]);
    for (const retainedNonce of contentByNonceRef.current.keys()) {
      if (!retainedNonces.has(retainedNonce)) contentByNonceRef.current.delete(retainedNonce);
    }
  }, [renderedSnapshot.current.nonce, renderedSnapshot.previous?.nonce]);

  useEffect(() => {
    if (reducedMotion || renderedSnapshot.state !== "transitioning") return;

    const currentSurface = currentSurfaceRef.current;
    if (!currentSurface) return;
    const activeNonce = renderedSnapshot.current.nonce;
    const settleCurrentSurface = (event: AnimationEvent) => {
      if (
        event.target !== currentSurface
        || currentSurfaceRef.current !== currentSurface
        || !screenTransitionEnterAnimationNames.includes(
          event.animationName as typeof screenTransitionEnterAnimationNames[number]
        )
      ) {
        return;
      }
      setSnapshot((current) => settleScreenTransition(current, activeNonce));
    };

    currentSurface.addEventListener("animationend", settleCurrentSurface);
    currentSurface.addEventListener("animationcancel", settleCurrentSurface);
    return () => {
      currentSurface.removeEventListener("animationend", settleCurrentSurface);
      currentSurface.removeEventListener("animationcancel", settleCurrentSurface);
    };
  }, [reducedMotion, renderedSnapshot.current.nonce, renderedSnapshot.state]);

  useEffect(() => {
    if (reducedMotion || renderedSnapshot.state !== "transitioning") return;

    const activeNonce = renderedSnapshot.current.nonce;
    const fallback = window.setTimeout(() => {
      setSnapshot((current) => settleScreenTransition(current, activeNonce));
    }, globalMotionFallbackMs);

    return () => window.clearTimeout(fallback);
  }, [reducedMotion, renderedSnapshot.current.nonce, renderedSnapshot.state]);

  const renderSurface = (surface: ScreenSurface, role: "current" | "previous") => {
    const isPrevious = role === "previous";
    return (
      <div
        key={surface.nonce}
        ref={isPrevious ? undefined : currentSurfaceRef}
        className="motion-screen-surface"
        data-motion-direction={renderedSnapshot.direction}
        data-motion-nonce={surface.nonce}
        data-motion-state={renderedSnapshot.state}
        data-motion-surface={role}
        data-screen={surface.screen}
        aria-hidden={isPrevious ? true : undefined}
        inert={isPrevious ? true : undefined}
      >
        {contentByNonceRef.current.get(surface.nonce)}
      </div>
    );
  };

  return (
    <div
      className="motion-screen-transition"
      data-motion-direction={renderedSnapshot.direction}
      data-motion-nonce={renderedSnapshot.current.nonce}
      data-motion-state={renderedSnapshot.state}
      data-screen={renderedSnapshot.current.screen}
    >
      {renderedSnapshot.previous ? renderSurface(renderedSnapshot.previous, "previous") : null}
      {renderSurface(renderedSnapshot.current, "current")}
    </div>
  );
}
