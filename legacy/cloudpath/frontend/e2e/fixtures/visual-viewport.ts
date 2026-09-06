import type { Page } from "playwright/test";

export type VisualViewportMetrics = {
  height: number;
  offsetTop: number;
};

const visualViewportShimSetter = "__bookcourseSetVisualViewport";

/**
 * Installs the deterministic test-only visualViewport shim used by responsive
 * and motion coverage. It models viewport geometry and resize/scroll events;
 * it does not emulate a physical software keyboard.
 */
export async function installVisualViewportShim(page: Page) {
  await page.addInitScript((setterName) => {
    let height = 1194;
    let offsetTop = 0;
    const events = new EventTarget();
    const shim = {
      get width() {
        return window.innerWidth;
      },
      get height() {
        return height;
      },
      get offsetTop() {
        return offsetTop;
      },
      get offsetLeft() {
        return 0;
      },
      get pageTop() {
        return offsetTop;
      },
      get pageLeft() {
        return 0;
      },
      get scale() {
        return 1;
      },
      addEventListener: events.addEventListener.bind(events),
      removeEventListener: events.removeEventListener.bind(events),
      dispatchEvent: events.dispatchEvent.bind(events)
    };
    Object.defineProperty(window, "visualViewport", { configurable: true, value: shim });
    Object.defineProperty(window, setterName, {
      configurable: true,
      value: (nextViewport: VisualViewportMetrics) => {
        height = nextViewport.height;
        offsetTop = nextViewport.offsetTop;
        events.dispatchEvent(new Event("resize"));
        events.dispatchEvent(new Event("scroll"));
      }
    });
  }, visualViewportShimSetter);
}

export async function setVisualViewport(page: Page, viewport: VisualViewportMetrics) {
  await page.evaluate(({ nextViewport, setterName }) => {
    const setter = (window as typeof window & {
      [key: string]: ((value: VisualViewportMetrics) => void) | undefined;
    })[setterName];
    if (!setter) throw new Error("visualViewport shim is not installed");
    setter(nextViewport);
  }, { nextViewport: viewport, setterName: visualViewportShimSetter });
}

export async function setVisualViewportHeight(page: Page, height: number) {
  await setVisualViewport(page, { height, offsetTop: 0 });
}
