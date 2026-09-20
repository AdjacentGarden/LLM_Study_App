import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Read-only verification of existing live assets. Never submits a generation request.
test("existing MiniMax results persist, rejected assets stay private and successful media plays", async ({ browser, baseURL }, info) => {
  const directory = process.env.STUDIO_MEDIA_RESULTS;
  test.skip(!directory, "Requires a private live-test result directory");
  const result = JSON.parse(readFileSync(resolve(directory!, "result.json"), "utf8"));
  const cookies = JSON.parse(readFileSync(resolve(directory!, "cookies.json"), "utf8"));
  const context = await browser.newContext({ viewport: { width: 393, height: 852 } });
  await context.addCookies(Object.entries(cookies).map(([name, value]) => ({ name, value: String(value), url: baseURL!, httpOnly: true })));
  const catalog = await (await context.request.get(baseURL + "/api/library")).json();
  const title = catalog.find((b: any) => b.book_id === result.book_id).title;
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/api/studio/media", route => {
    if (route.request().method() === "POST") throw new Error("Playback must never generate paid media");
    return route.continue();
  });
  const outsider = await browser.newContext();
  for (const job of result.jobs) {
    const current = await (await context.request.get(baseURL + "/api/studio/jobs/" + job.id)).json();
    if (current.status === "failed") {
      expect(current.asset_url).toBeNull();
      expect((await context.request.get(baseURL + `/api/studio/jobs/${job.id}/asset`)).status()).not.toBe(200);
      expect((await outsider.request.get(baseURL + `/api/studio/jobs/${job.id}/asset`)).status()).toBe(404);
      continue;
    }
    expect(current.status).toBe("succeeded");
    const asset = await context.request.get(baseURL + current.asset_url);
    expect(asset.status()).toBe(200);
    expect(asset.headers()["cache-control"]).toContain("private");
    expect((await outsider.request.get(baseURL + current.asset_url)).status()).toBe(404);
  }
  for (let pass = 0; pass < 2; pass++) {
    await page.goto(baseURL + "/?embedded=1");
    await page.getByRole("button", { name: "暂时体验，稍后注册" }).click();
    await page.getByRole("button", { name: "书架", exact: true }).click();
    await page.locator(".shelf-caption").filter({ hasText: title }).click();
    await page.getByRole("button", { name: /把理解，写下来/ }).click();
    await page.getByRole("button", { name: "图解", exact: true }).click();
    if (result.jobs.some((j: any) => j.kind === "image" && j.status === "succeeded")) {
    const image = page.locator(".studio-job img").first();
    await image.scrollIntoViewIfNeeded();
    await expect.poll(() => image.evaluate((e: HTMLImageElement) => e.naturalWidth)).toBeGreaterThan(0);
    await page.getByRole("button", { name: "查看图解大图" }).first().click();
    const viewer = page.getByRole("dialog", { name: "图解大图", exact: true });
    await expect(viewer).toBeVisible();
    for (let i = 0; i < 4; i++) await viewer.getByRole("button", { name: "放大", exact: true }).click();
    await expect(viewer.getByLabel("图解缩放比例")).toHaveText("300%");
    await expect(viewer.getByRole("button", { name: "放大", exact: true })).toBeDisabled();
    const scroll = viewer.getByLabel("可滚动的大图区域");
    expect(await scroll.evaluate(e => e.scrollWidth > e.clientWidth)).toBeTruthy();
    await scroll.hover();
    await page.mouse.wheel(200, 120);
    await expect.poll(() => scroll.evaluate(e => e.scrollLeft)).toBeGreaterThan(0);
    await page.screenshot({ path: info.outputPath(`zoom-${pass}.png`) });
    await viewer.getByRole("button", { name: "缩小", exact: true }).click();
    await expect(viewer.getByLabel("图解缩放比例")).toHaveText("250%");
    await page.keyboard.press("Escape");
    await expect(viewer).not.toBeVisible();
    await expect(page.getByRole("dialog", { name: "学习创作空间" })).toBeVisible();
    } else {
      await expect(page.locator(".studio-job .studio-error").first()).toContainText("画面未通过内容复核");
      await expect(page.locator(".studio-job img, .studio-job video")).toHaveCount(0);
      await page.locator(".studio-job").first().scrollIntoViewIfNeeded();
    }
    if (result.jobs.some((j: any) => j.kind === "video" && j.status === "succeeded")) {
      await page.getByRole("button", { name: "短片", exact: true }).click();
      const video = page.getByLabel("AI 短片讲解");
      await video.scrollIntoViewIfNeeded();
      await expect.poll(() => video.evaluate((e: HTMLVideoElement) => e.readyState)).toBeGreaterThan(0);
      const duration = await video.evaluate((e: HTMLVideoElement) => e.duration);
      expect(duration).toBeGreaterThan(0);
      expect(duration).toBeLessThanOrEqual(15);
      await video.evaluate(async (e: HTMLVideoElement) => { e.muted = true; await e.play(); });
      await expect.poll(() => video.evaluate((e: HTMLVideoElement) => e.currentTime)).toBeGreaterThan(0.25);
      await video.evaluate((e: HTMLVideoElement) => { e.pause(); e.currentTime = 3; });
      await expect.poll(() => video.evaluate((e: HTMLVideoElement) => e.currentTime)).toBeGreaterThanOrEqual(3);
    }
    await page.screenshot({ path: info.outputPath(`media-${pass}.png`) });
  }
  expect(errors).toEqual([]);
  await outsider.close();
  await context.close();
});
