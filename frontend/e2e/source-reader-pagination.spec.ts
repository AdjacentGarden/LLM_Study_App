import { expect, test } from "playwright/test";

test.describe("original PDF pagination", () => {
  test("supports buttons, swipe gestures, keyboard arrows, and page boundaries", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    await page.getByRole("button", { name: "回到原书", exact: true }).click();

    const previous = page.getByRole("button", { name: "上一页", exact: true });
    const next = page.getByRole("button", { name: "下一页", exact: true });
    const pageFrame = page.locator(".source-page-frame");

    await expect(page.getByRole("heading", { name: "原文文档" })).toBeVisible();
    const pageIndicator = page.locator(".source-reader-toolbar strong");
    const initialPage = Number((await pageIndicator.textContent())?.match(/\d+/)?.[0]);
    expect(initialPage).toBeGreaterThan(1);
    await expect(previous).toBeEnabled();

    await next.click();
    await expect(pageIndicator).toHaveText(`页 ${initialPage + 1}`);
    await expect(page.getByRole("img", { name: new RegExp(`页 ${initialPage + 1}$`) })).toBeVisible();

    await pageFrame.scrollIntoViewIfNeeded();
    const box = await pageFrame.boundingBox();
    expect(box).not.toBeNull();
    if (!box) return;
    const viewportHeight = page.viewportSize()?.height ?? 844;
    const swipeY = Math.max(60, Math.min(viewportHeight - 60, box.y + 80));
    await pageFrame.dispatchEvent("pointerdown", {
      pointerId: 7, isPrimary: true, clientX: box.x + box.width * 0.65, clientY: swipeY
    });
    await pageFrame.dispatchEvent("pointermove", {
      pointerId: 7, isPrimary: true, clientX: box.x + box.width * 0.35, clientY: swipeY
    });
    await pageFrame.dispatchEvent("pointerup", {
      pointerId: 7, isPrimary: true, clientX: box.x + box.width * 0.35, clientY: swipeY
    });
    await expect(pageIndicator).toHaveText(`页 ${initialPage + 2}`);

    await pageFrame.focus();
    await pageFrame.press("ArrowLeft");
    await expect(pageIndicator).toHaveText(`页 ${initialPage + 1}`);
    await pageFrame.press("ArrowRight");
    await expect(pageIndicator).toHaveText(`页 ${initialPage + 2}`);

    await pageFrame.scrollIntoViewIfNeeded();
    const nextBox = await pageFrame.boundingBox();
    expect(nextBox).not.toBeNull();
    if (!nextBox) return;
    const nextSwipeY = Math.max(60, Math.min((page.viewportSize()?.height ?? 844) - 60, nextBox.y + 80));
    await pageFrame.dispatchEvent("pointerdown", {
      pointerId: 8, isPrimary: true, clientX: nextBox.x + nextBox.width * 0.35, clientY: nextSwipeY
    });
    await pageFrame.dispatchEvent("pointermove", {
      pointerId: 8, isPrimary: true, clientX: nextBox.x + nextBox.width * 0.65, clientY: nextSwipeY
    });
    await pageFrame.dispatchEvent("pointerup", {
      pointerId: 8, isPrimary: true, clientX: nextBox.x + nextBox.width * 0.65, clientY: nextSwipeY
    });
    await expect(pageIndicator).toHaveText(`页 ${initialPage + 1}`);

    for (let pageNumber = initialPage + 1; pageNumber > 1; pageNumber -= 1) {
      await previous.click();
    }
    await expect(pageIndicator).toHaveText("页 1");
    await expect(previous).toBeDisabled();
  });
});
