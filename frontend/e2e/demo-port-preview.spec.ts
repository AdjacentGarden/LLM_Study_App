import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";

let server: ViteDevServer;
let origin: string;
test.use({ browserName: "chromium" });
test.beforeAll(async () => {
  server = await createServer({ server: { host: "127.0.0.1", port: 0, watch: { ignored: ["**/test-results*/**"] } } });
  await server.listen();
  origin = server.resolvedUrls!.local[0];
});
test.afterAll(async () => { await server?.close(); });

test("original Demo shell, carousel and directory render on real-service boundary", async ({ browser }, info) => {
  const context = await browser.newContext({ viewport: { width: 393, height: 852 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", value => errors.push(value.message));
  await page.route(url => url.pathname.startsWith("/api/"), route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: {
      account: null, user_id: "demo-port-visitor", legacy_profile: false, email_available: false,
      demo_available: false, demo_users: [], learning_sessions: {},
    } });
    if (path === "/api/library") return route.fulfill({ json: [] });
    if (path === "/api/user/profile") return route.fulfill({ json: { nickname: "", age: null, bio: "", revision: 0, avatar_url: null } });
    return route.fulfill({ status: 404, json: { detail: "No fixture for this endpoint" } });
  });
  try {
    await page.goto(new URL("/?ui=demo-full&embedded=1", origin).href);
    await page.getByRole("button", { name: "暂不登录" }).click();
    await expect(page.locator(".app-shell[data-active-screen='home']")).toBeVisible();
    await expect(page.locator(".home-book-picker")).toContainText("还没有教材");
    await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
    const orb = page.getByRole("button", { name: "打开 AI 助手" });
    const rect = await orb.boundingBox();
    expect(rect).not.toBeNull();
    await page.mouse.move(rect!.x + rect!.width / 2, rect!.y + rect!.height / 2);
    await page.mouse.down();
    await page.mouse.move(30, rect!.y + 45, { steps: 8 });
    await page.mouse.up();
    await expect(orb).toHaveAttribute("data-side", "left");
    await expect(page.locator(".app-shell[data-active-screen='home']")).toBeVisible();
    await orb.click();
    const assistant = page.getByRole("dialog", { name: "AI 导学助手" });
    await expect(assistant).toBeVisible();
    await expect(assistant.getByRole("textbox", { name: "向 AI 助手提问" })).toBeFocused();
    const dialogRect = await assistant.boundingBox();
    const shellRect = await page.locator(".app-shell").boundingBox();
    expect(dialogRect!.x).toBeGreaterThanOrEqual(shellRect!.x);
    expect(dialogRect!.y).toBeGreaterThanOrEqual(shellRect!.y);
    expect(dialogRect!.x + dialogRect!.width).toBeLessThanOrEqual(shellRect!.x + shellRect!.width);
    expect(dialogRect!.y + dialogRect!.height).toBeLessThanOrEqual(shellRect!.y + shellRect!.height);
    await page.screenshot({ path: info.outputPath("demo-assistant-dialog.png"), fullPage: true });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "AI 导学助手" })).toHaveCount(0);
    await expect(orb).toBeFocused();
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "学习" }).click();
    await expect(page.locator(".study-screen")).toContainText("开始你的第一门课程");
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "首页" }).click();
    for (const width of [320, 834, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.getByRole("button", { name: "打开 AI 助手" }).click();
      const overlay = await page.getByRole("dialog", { name: "AI 导学助手" }).boundingBox();
      const host = await page.locator(".app-shell").boundingBox();
      expect(overlay!.x).toBeGreaterThanOrEqual(host!.x - 1);
      expect(overlay!.x + overlay!.width).toBeLessThanOrEqual(host!.x + host!.width + 1);
      await page.keyboard.press("Escape");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
