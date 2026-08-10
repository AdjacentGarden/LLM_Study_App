import { useSyncExternalStore } from "react";

export const PAD_LAYOUT_MEDIA_QUERY = "(min-width: 768px) and (min-height: 600px)";

type MediaQueryListWithLegacyListeners = MediaQueryList & {
  addListener?: (listener: (event: MediaQueryListEvent) => void) => void;
  removeListener?: (listener: (event: MediaQueryListEvent) => void) => void;
};

function getMediaQueryList() {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(PAD_LAYOUT_MEDIA_QUERY)
    : null;
}

function getSnapshot() {
  return getMediaQueryList()?.matches ? "pad" : "phone";
}

function subscribe(callback: () => void) {
  const mediaQuery = getMediaQueryList();
  if (!mediaQuery) return () => undefined;
  const listener = () => callback();
  const legacyMediaQuery = mediaQuery as MediaQueryListWithLegacyListeners;
  if (typeof mediaQuery.addEventListener === "function") {
    mediaQuery.addEventListener("change", listener);
    return () => mediaQuery.removeEventListener("change", listener);
  }
  legacyMediaQuery.addListener?.(listener);
  return () => legacyMediaQuery.removeListener?.(listener);
}

export function useDeviceLayout(): "phone" | "pad" {
  return useSyncExternalStore(subscribe, getSnapshot, () => "phone");
}
