import { expect, test } from "@playwright/test";

test.use({ browserName: "chromium" });
test("the default Demo frontend opens a live library and study directory", async ({ browser }, info) => {
  test.skip(process.env.DEMO_PORT_REAL_E2E !== "1", "Requires the local backend and Vite server.");
  const context = await browser.newContext({ viewport: { width: 402, height: 874 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    await page.goto(process.env.DEMO_PORT_REAL_URL || "http://127.0.0.1:5174/");
    const guest = page.getByRole("button", { name: "暂不登录" });
    await guest.waitFor({ state: "visible" });
    await guest.click();
    await expect(page.locator(".app-shell[data-active-screen='home']")).toBeVisible();
    await expect(page.locator(".home-book-picker")).toBeVisible();
    await page.screenshot({ path: info.outputPath("demo-live-home.png"), fullPage: true });
    await page.getByRole("button", { name: "导入课程" }).first().click();
    await expect(page.locator(".upload-flow-screen")).toBeVisible();
    await page.getByRole("button", { name: "关闭上传" }).click();
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "学习" }).click();
    await expect(page.locator(".study-screen")).toBeVisible();
    await expect(page.locator(".study-screen .study-directory")).toBeVisible();
    const cover = page.locator(".study-book-switch img");
    if (await cover.count()) {
      await expect.poll(() => cover.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true);
    } else {
      await expect(page.locator(".study-book-switch .course-cover-fallback")).toBeVisible();
    }
    await page.screenshot({ path: info.outputPath("demo-live-study.png"), fullPage: true });
    await page.locator(".study-book-switch").click();
    const biology = page.getByRole("dialog", { name: "切换教材" }).getByRole("button", { name: /生物/ }).first();
    await expect(biology).toBeVisible();
    await biology.click();
    await expect(page.locator(".study-screen")).toContainText("遗传与进化");
    await page.screenshot({ path: info.outputPath("demo-live-biology.png"), fullPage: true });
    await page.locator('.study-tool-grid [data-tool="assignment"]').first().click();
    await expect(page.locator(".assignment-screen")).toBeVisible();
    const options = page.locator(".assignment-choice-options button, .assignment-judgment-options button");
    if (await options.count()) await options.first().click();
    else await page.locator(".assignment-short-answer textarea").fill("我会结合孟德尔的实验过程解释这一现象。");
    await page.getByRole("button", { name: "提交答案" }).click();
    await expect(page.locator(".demo-port-assignment-feedback")).toBeVisible();
    await page.getByRole("button", { name: "返回", exact: true }).click();
    await page.locator('.study-tool-grid [data-tool="flashcards"]').first().click();
    await expect(page.locator(".flashcard-screen")).toBeVisible();
    await page.locator(".memory-card-trigger").click();
    await expect(page.locator(".memory-card-trigger")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".memory-card-answer-face-back h2")).not.toBeEmpty();
    await page.getByRole("button", { name: "返回", exact: true }).click();
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "首页" }).click();
    await page.getByRole("button", { name: "回到原书" }).click();
    await expect(page.locator(".source-reader-screen")).toBeVisible();
    await expect(page.locator(".next-source-page-layers img")).toBeVisible();
    const canvas = page.getByLabel(/PDF 第 \d+ 页手写标注层/);
    await expect(canvas).toBeVisible();
    await canvas.scrollIntoViewIfNeeded();
    const alignment = await page.locator(".next-source-page-layers").evaluate(element => {
      const image = element.querySelector("img")!.getBoundingClientRect();
      const ink = element.querySelector("canvas")!.getBoundingClientRect();
      return Math.max(Math.abs(image.x - ink.x), Math.abs(image.y - ink.y), Math.abs(image.width - ink.width), Math.abs(image.height - ink.height));
    });
    expect(alignment).toBeLessThan(1);
    await page.getByRole("button", { name: "手写", exact: true }).click();
    await expect(canvas).toHaveCSS("pointer-events", "auto");
    await canvas.scrollIntoViewIfNeeded();
    const bounds = await canvas.boundingBox();
    expect(bounds).not.toBeNull();
    await page.mouse.move(bounds!.x + bounds!.width * .22, bounds!.y + bounds!.height * .2);
    await page.mouse.down();
    await page.mouse.move(bounds!.x + bounds!.width * .35, bounds!.y + bounds!.height * .22, { steps: 5 });
    await page.mouse.up();
    await page.getByRole("button", { name: "保存本页笔记" }).click();
    await expect(page.locator(".next-source-ink-status [role='status']")).toContainText("本页笔迹已保存");
    await page.screenshot({ path: info.outputPath("demo-live-source.png"), fullPage: true });
    await page.getByRole("button", { name: "撤销" }).click();
    await page.getByRole("button", { name: "保存本页笔记" }).click();
    await expect(page.locator(".next-source-ink-status [role='status']")).toContainText("本页笔迹已保存");
    await page.getByRole("button", { name: "返回", exact: true }).click();
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "发现" }).click();
    await expect(page.locator(".community-screen")).toBeVisible();
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "我的" }).click();
    await expect(page.locator(".profile-screen")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
