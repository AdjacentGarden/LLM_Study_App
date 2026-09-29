import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";

let server: ViteDevServer;
let origin: string;

test.beforeAll(async () => {
  server = await createServer({
    server: { host: "127.0.0.1", port: 0, watch: { ignored: ["**/test-results*/**"] } },
  });
  await server.listen();
  origin = server.resolvedUrls!.local[0];
});

test.afterAll(async () => { await server?.close(); });

for (const entry of ["/", "/?device=iphone-16", "/?ui=demo-full&device=iphone-16", "/?ui=next&device=iphone-16"]) {
  test(`${entry} opens the Demo frontend`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 900, height: 1020 } });
    const page = await context.newPage();
    const pageErrors: string[] = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    await page.route(url => url.pathname.startsWith("/api/"), route => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/auth/me") {
        return route.fulfill({ json: {
          account: null,
          user_id: "entry-smoke-visitor",
          legacy_profile: false,
          email_available: false,
          demo_available: false,
          demo_users: [],
          learning_sessions: {},
        } });
      }
      if (url.pathname === "/api/library") return route.fulfill({ json: [] });
      return route.fulfill({ status: 404, json: { detail: "No fixture for this endpoint" } });
    });

    try {
      await page.goto(new URL(entry, origin).href);
      const devicePreview = entry.includes("device=iphone-16");
      const app = devicePreview ? page.frameLocator('iframe[title="iPhone 16 应用预览"]') : page;
      await expect(page.locator("iframe")).toHaveCount(devicePreview ? 1 : 0);
      await expect(app.getByRole("button", { name: "暂不登录" })).toBeVisible();
      if (devicePreview) {
        const outerFrame = await page.locator(".next-device-preview-frame").boundingBox();
        expect(outerFrame?.width).toBe(407);
        expect(outerFrame?.height).toBe(866);
        expect(await app.locator("html").evaluate(() => innerWidth)).toBe(393);
      }

      await app.getByRole("button", { name: "暂不登录" }).click();
      await expect(app.locator(".app-shell[data-active-screen='home']")).toBeVisible();
      await expect(app.getByRole("navigation", { name: "主导航" })).toBeVisible();
      await expect(app.locator(".home-book-picker")).toContainText("还没有教材");
      if (devicePreview) {
        expect(await app.locator("html").evaluate(element => element.scrollWidth <= innerWidth + 1)).toBe(true);
      }
      expect(pageErrors).toEqual([]);
    } finally {
      await context.close();
    }
  });
}
