import { App as NativeApp } from "@capacitor/app";
import { Capacitor, type PluginListenerHandle } from "@capacitor/core";
import { Keyboard } from "@capacitor/keyboard";
import { StatusBar, Style } from "@capacitor/status-bar";

export type RuntimePlatform = "android" | "ios" | "web";

export function getRuntimePlatform(): RuntimePlatform {
  const platform = Capacitor.getPlatform();
  return platform === "android" || platform === "ios" ? platform : "web";
}

export function isNativeAndroid() {
  return getRuntimePlatform() === "android";
}

/** Configure the actual Android status bar instead of rendering iPhone chrome. */
export async function configureNativeAppShell() {
  if (!isNativeAndroid()) return;

  // Android 15+ may force edge-to-edge behaviour and reject some of these
  // methods. The CSS shell remains correct in that case, so failures are
  // intentionally non-fatal.
  await Promise.allSettled([
    StatusBar.setStyle({ style: Style.Light }),
    StatusBar.setBackgroundColor({ color: "#F6F8FB" }),
    StatusBar.setOverlaysWebView({ overlay: false })
  ]);
}

export function registerAndroidBackButton(handler: () => void) {
  if (!isNativeAndroid()) return () => undefined;

  let disposed = false;
  let listener: PluginListenerHandle | undefined;
  void NativeApp.addListener("backButton", handler).then((nextListener) => {
    if (disposed) {
      void nextListener.remove();
      return;
    }
    listener = nextListener;
  }).catch(() => {
    // A web preview can never reach this branch. Avoid making a missing native
    // bridge fatal if a device is mid-reload while Capacitor initializes.
  });

  return () => {
    disposed = true;
    void listener?.remove();
  };
}

export function dismissNativeKeyboardIfFocused() {
  if (!isNativeAndroid() || typeof document === "undefined") return false;
  const activeElement = document.activeElement;
  const isEditable = activeElement instanceof HTMLInputElement
    || activeElement instanceof HTMLTextAreaElement
    || (activeElement instanceof HTMLElement && activeElement.isContentEditable);
  if (!isEditable) return false;

  activeElement.blur();
  void Keyboard.hide().catch(() => undefined);
  return true;
}

export function minimizeNativeAndroidApp() {
  if (!isNativeAndroid()) return;
  void NativeApp.minimizeApp().catch(() => undefined);
}
