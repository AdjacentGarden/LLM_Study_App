import { App as NativeApp } from "@capacitor/app";
import { Capacitor, SystemBars, SystemBarsStyle, type PluginListenerHandle } from "@capacitor/core";
import { Keyboard } from "@capacitor/keyboard";

export type RuntimePlatform = "android" | "ios" | "web";

export function getRuntimePlatform(): RuntimePlatform {
  const platform = Capacitor.getPlatform();
  return platform === "android" || platform === "ios" ? platform : "web";
}

export function isNativeAndroid() {
  return getRuntimePlatform() === "android";
}

/** Keep Android system bars readable while CSS handles the edge-to-edge safe areas. */
export async function configureNativeAppShell() {
  if (!isNativeAndroid()) return;

  // Avoid legacy overlay calls that resize the WebView away from the system
  // navigation area and expose a solid strip underneath the tablet sidebar.
  await SystemBars.setStyle({ style: SystemBarsStyle.Light }).catch(() => undefined);
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
