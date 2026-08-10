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
        themeColor: getComputedStyle(element.ownerDocument.querySelector<HTMLElement>(".home-primary-action")!).backgroundColor,
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
    expect(presentation.themeColor, "page actions share the selected tab purple").toBe(presentation.selectionColor);

    const geometry = await readSelectionGeometry(navigation);
    expect(Math.abs(geometry.selection.x - geometry.active.x)).toBeLessThan(1);
    expect(Math.abs(geometry.selection.y - geometry.active.y)).toBeLessThan(1);
    expect(Math.abs(geometry.selection.width - geometry.active.width)).toBeLessThan(1);
    expect(Math.abs(geometry.selection.height - geometry.active.height)).toBeLessThan(1);
    if (geometry.horizontal) {
      const bottomInset = geometry.navigation.height - geometry.selection.y - geometry.selection.height;
      expect(geometry.selection.y).toBeLessThanOrEqual(5);
      expect(bottomInset).toBeLessThanOrEqual(5);
    }
  });

  test("moves the capsule from 首页 to 社区 without overshooting the active item", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    const navigation = page.getByRole("navigation", { name: "主导航" });
    const before = await readSelectionGeometry(navigation);

    await page.getByRole("button", { name: "社区", exact: true }).click();
    const immediate = await readSelectionGeometry(navigation);
    await page.waitForTimeout(80);
    const midpoint = await readSelectionGeometry(navigation);
    await page.waitForTimeout(240);
    const settled = await readSelectionGeometry(navigation);

    const axis = before.horizontal ? "x" : "y";
    const start = before.selection[axis];
    const destination = immediate.active[axis];
    expect(Math.abs(immediate.selection[axis] - destination)).toBeGreaterThan(1);
    const direction = Math.sign(destination - start);
    expect((midpoint.selection[axis] - start) * direction).toBeGreaterThan(0);
    expect((destination - midpoint.selection[axis]) * direction).toBeGreaterThanOrEqual(0);
    expect(Math.abs(settled.selection[axis] - settled.active[axis])).toBeLessThan(1);
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
