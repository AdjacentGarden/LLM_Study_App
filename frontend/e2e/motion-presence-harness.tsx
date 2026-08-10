import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { globalMotionFallbackMs } from "../src/motion/timing";
import { useMotionPresence } from "../src/motion/useMotionPresence";

type Surface = {
  key: string;
  value: number;
};

type PresenceScope = "global" | "local";

type MotionPresenceHarness = {
  clear: (scope: PresenceScope) => void;
  replace: (scope: PresenceScope, key: string, value: number) => void;
  setReducedMotion: (scope: PresenceScope, reducedMotion: boolean) => void;
  unmount: () => void;
  updateSameKey: (scope: PresenceScope, value: number) => void;
};

declare global {
  interface Window {
    __motionPresenceHarness?: MotionPresenceHarness;
  }
}

const rootElement = document.getElementById("motion-presence-root");

if (!rootElement) {
  throw new Error("Motion presence harness root is missing.");
}

const root = createRoot(rootElement);
const getSurfaceKey = (surface: Surface) => surface.key;
const motionNames = ["motion-presence-harness"] as const;

function MotionPresenceHarnessView() {
  const [localRequested, setLocalRequested] = useState<Surface | null>({ key: "alpha", value: 0 });
  const [globalRequested, setGlobalRequested] = useState<Surface | null>({ key: "alpha", value: 0 });
  const [localReducedMotion, setLocalReducedMotion] = useState(false);
  const [globalReducedMotion, setGlobalReducedMotion] = useState(false);
  const localOutputRef = useRef<HTMLOutputElement | null>(null);
  const globalOutputRef = useRef<HTMLOutputElement | null>(null);
  const localPresence = useMotionPresence({
    requested: localRequested,
    getKey: getSurfaceKey,
    reducedMotion: localReducedMotion,
    motionNames
  });
  const globalPresence = useMotionPresence({
    requested: globalRequested,
    getKey: getSurfaceKey,
    reducedMotion: globalReducedMotion,
    motionNames,
    maxMotionMs: globalMotionFallbackMs
  });

  useEffect(() => {
    const output = localOutputRef.current;
    if (!output) return;
    const handleCancel = (event: AnimationEvent) => localPresence.onAnimationCancel(event);
    output.addEventListener("animationcancel", handleCancel);
    return () => output.removeEventListener("animationcancel", handleCancel);
  }, [localPresence.onAnimationCancel]);

  useEffect(() => {
    const output = globalOutputRef.current;
    if (!output) return;
    const handleCancel = (event: AnimationEvent) => globalPresence.onAnimationCancel(event);
    output.addEventListener("animationcancel", handleCancel);
    return () => output.removeEventListener("animationcancel", handleCancel);
  }, [globalPresence.onAnimationCancel]);

  useEffect(() => {
    window.__motionPresenceHarness = {
      clear: (scope) => (scope === "local" ? setLocalRequested(null) : setGlobalRequested(null)),
      replace: (scope, key, value) => (scope === "local" ? setLocalRequested({ key, value }) : setGlobalRequested({ key, value })),
      setReducedMotion: (scope, reducedMotion) => (
        scope === "local" ? setLocalReducedMotion(reducedMotion) : setGlobalReducedMotion(reducedMotion)
      ),
      unmount: () => root.unmount(),
      updateSameKey: (scope, value) => (
        scope === "local" ? setLocalRequested({ key: "alpha", value }) : setGlobalRequested({ key: "alpha", value })
      )
    };

    return () => {
      delete window.__motionPresenceHarness;
    };
  }, []);

  return (
    <>
      <output
        ref={localOutputRef}
        id="motion-presence-state-local"
        data-presence-id={localPresence.presenceId}
        data-rendered-key={localPresence.rendered?.key ?? "none"}
        data-rendered-value={localPresence.rendered?.value ?? "none"}
        data-requested-key={localRequested?.key ?? "none"}
        data-requested-value={localRequested?.value ?? "none"}
        data-reduced-motion={localReducedMotion ? "true" : "false"}
        data-state={localPresence.state}
        onAnimationEnd={localPresence.onAnimationEnd}
      />
      <output
        ref={globalOutputRef}
        id="motion-presence-state-global"
        data-presence-id={globalPresence.presenceId}
        data-rendered-key={globalPresence.rendered?.key ?? "none"}
        data-rendered-value={globalPresence.rendered?.value ?? "none"}
        data-requested-key={globalRequested?.key ?? "none"}
        data-requested-value={globalRequested?.value ?? "none"}
        data-reduced-motion={globalReducedMotion ? "true" : "false"}
        data-state={globalPresence.state}
        onAnimationEnd={globalPresence.onAnimationEnd}
      />
    </>
  );
}

root.render(<MotionPresenceHarnessView />);
