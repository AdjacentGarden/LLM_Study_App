import type { Locator } from "playwright/test";
import { expect, test } from "./fixtures";

async function readSelectionGeometry(navigation: Locator) {
  return navigation.evaluate((element) => {
    const navigationRect = element.getBoundingClientRect();
    const selection = element.querySelector<HTMLElement>(".nav-selection");
    const activeItem = element.querySelector<HTMLElement>(".nav-item.active");
    if (!selection || !activeItem) throw new Error("Primary navigation selection is missing.");

    const selectionRect = selection.getBoundingClientRect();
    const activeRect = activeItem.getBoundingClientRect();
    return {
      horizontal: navigationRect.width > navigationRect.height,
      navigation: {
        width: navigationRect.width,
        height: navigationRect.height
      },
      selection: {
        x: selectionRect.left - navigationRect.left,
        y: selectionRect.top - navigationRect.top,
        width: selectionRect.width,
        height: selectionRect.height
      },
      active: {
        x: activeRect.left - navigationRect.left,
        y: activeRect.top - navigationRect.top,
        width: activeRect.width,
        height: activeRect.height
      }
    };
  });
}

test.describe("four-item sliding tab bar", () => {
  test.use({ reducedMotion: "no-preference" });

  test("keeps four equal items and applies the selected capsule colors", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    const navigation = page.getByRole("navigation", { name: "主导航" });
    await expect(navigation).toBeVisible();

    const presentation = await navigation.evaluate((element) => {
      const items = Array.from(element.querySelectorAll<HTMLElement>(".nav-item"));
      const selection = element.querySelector<HTMLElement>(".nav-selection");
      return {
        labels: items.map((item) => item.textContent?.trim()),
        widths: items.map((item) => item.getBoundingClientRect().width),
        activeColor: getComputedStyle(items.find((item) => item.classList.contains("active"))!).color,
        inactiveColors: items.filter((item) => !item.classList.contains("active")).map((item) => getComputedStyle(item).color),
        selectionColor: selection ? getComputedStyle(selection).backgroundColor : null
      };
    });

    expect(presentation.labels).toEqual(["首页", "社区", "学习", "我的"]);
    expect(Math.max(...presentation.widths) - Math.min(...presentation.widths)).toBeLessThan(1);
    expect(presentation.activeColor).toBe("rgb(255, 255, 255)");
    expect(presentation.inactiveColors).toEqual([
      "rgb(52, 54, 61)",
      "rgb(52, 54, 61)",
      "rgb(52, 54, 61)"
    ]);
    expect(presentation.selectionColor).toBe("rgb(124, 58, 237)");

    const geometry = await readSelectionGeometry(navigation);
    expect(Math.abs(geometry.selection.x - geometry.active.x)).toBeLessThan(1);
    expect(Math.abs(geometry.selection.y - geometry.active.y)).toBeLessThan(1);
    expect(Math.abs(geometry.selection.width - geometry.active.width)).toBeLessThan(1);
    expect(Math.abs(geometry.selection.height - geometry.active.height)).toBeLessThan(1);
    if (geometry.horizontal) {
      const bottomInset = geometry.navigation.height - geometry.selection.y - geometry.selection.height;
      expect(Math.abs(geometry.selection.y - bottomInset)).toBeLessThan(1);
      expect(geometry.selection.y).toBeLessThanOrEqual(8);
      expect(bottomInset).toBeLessThanOrEqual(8);
    }
  });

  test("moves the capsule from 首页 to 社区 without overshooting the active item", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    const navigation = page.getByRole("navigation", { name: "主导航" });
    const trajectory = await navigation.evaluate(async (element) => {
      const selection = element.querySelector<HTMLElement>(".nav-selection");
      const destinationItem = element.querySelector<HTMLButtonElement>('[data-nav-index="1"]');
      if (!selection || !destinationItem) throw new Error("Primary navigation controls are missing.");
      const navigationBounds = element.getBoundingClientRect();
      const horizontal = navigationBounds.width > navigationBounds.height;
      const axisValue = (target: HTMLElement) => {
        const bounds = target.getBoundingClientRect();
        return horizontal
          ? bounds.left - navigationBounds.left
          : bounds.top - navigationBounds.top;
      };
      const start = axisValue(selection);
      const destination = axisValue(destinationItem);
      destinationItem.click();
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const animation = selection.getAnimations()[0];
      if (!animation) throw new Error("Primary navigation did not start its WAAPI transition.");
      const timing = animation.effect?.getComputedTiming();
      animation.pause();
      animation.currentTime = 0;
      const sampledStart = axisValue(selection);
      animation.currentTime = 100;
      const midpoint = axisValue(selection);
      animation.currentTime = 200;
      const sampledEnd = axisValue(selection);
      animation.finish();
      return {
        start,
        destination,
        sampledStart,
        midpoint,
        sampledEnd,
        duration: timing?.duration,
        activeDuration: timing?.activeDuration
      };
    });

    const direction = Math.sign(trajectory.destination - trajectory.start);
    expect(Math.abs(trajectory.destination - trajectory.start)).toBeGreaterThan(1);
    expect(trajectory.duration).toBe(200);
    expect(trajectory.activeDuration).toBe(200);
    expect(Math.abs(trajectory.sampledStart - trajectory.start)).toBeLessThanOrEqual(1);
    expect((trajectory.midpoint - trajectory.start) * direction).toBeGreaterThan(0);
    expect((trajectory.destination - trajectory.midpoint) * direction).toBeGreaterThan(0);
    expect(Math.abs(trajectory.sampledEnd - trajectory.destination)).toBeLessThan(1);
    const settled = await readSelectionGeometry(navigation);
    const settledAxis = settled.horizontal ? "x" : "y";
    expect(Math.abs(settled.selection[settledAxis] - settled.active[settledAxis])).toBeLessThan(1);
    await expect(page.getByRole("button", { name: "社区", exact: true })).toHaveAttribute("aria-current", "page");
  });
});

test.describe("four-item tab bar with reduced motion", () => {
  test("places the capsule immediately on the selected item", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/?embedded=device-preview");
    expect(await page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    const navigation = page.getByRole("navigation", { name: "主导航" });
    await page.getByRole("button", { name: "社区", exact: true }).click();
    await page.waitForTimeout(30);
    const geometry = await readSelectionGeometry(navigation);

    expect(Math.abs(geometry.selection.x - geometry.active.x)).toBeLessThan(1);
    expect(Math.abs(geometry.selection.y - geometry.active.y)).toBeLessThan(1);
  });
});
