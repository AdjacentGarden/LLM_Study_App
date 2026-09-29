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

test("Demo upload confirms a real PDF before processing and restores that stage after reload", async ({ browser }, info) => {
  const context = await browser.newContext({ viewport: { width: 393, height: 852 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const calls = { upload: 0, bind: 0, process: 0, structure: 0, claim: 0, diagnostics: 0 };
  let claimed = false;
  let ready = false;
  const book = { book_id: "final-book", title: "测试教材", status: "ready", page_count: 2, chapter_count: 1,
    summary: "两页测试教材", diagnostics_ready: true };
  const structure = { title: book.title, summary: book.summary, source_page_count: 2,
    chapters: [{ chapter_id: "chapter-1", order: 0, title: "第一章", start_page: 1, end_page: 2,
      summary: "测试章节", knowledge_points: ["概念"], evidence: [], level: 1 }] };
  const status = (state: string, progress: number) => ({ book_id: "upload-book", status: state, progress,
    current_step: state, quality_score: null, needs_human_review: false, attempts: 1, max_attempts: 2,
    page_count: 2, retryable: true });
  await page.route(url => url.pathname.startsWith("/api/"), async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path === "/api/auth/me") return route.fulfill({ json: { account: null, user_id: "upload-visitor", legacy_profile: false,
      email_available: false, demo_available: false, demo_users: [], learning_sessions: {} } });
    if (path === "/api/user/profile") return route.fulfill({ json: { nickname: "", age: null, bio: "", revision: 0, avatar_url: null } });
    if (path === "/api/library" && method === "GET") return route.fulfill({ json: claimed ? [{ ...book, diagnostics_ready: ready }] : [] });
    if (path === "/api/books" && method === "POST") { calls.upload++;
      if (calls.upload === 1) { await new Promise(resolve => setTimeout(resolve, 300));
        return route.fulfill({ status: 503, json: { detail: "上传服务暂时不可用" } }); }
      return route.fulfill({ json: { book_id: "upload-book", filename: "textbook.pdf", status: "uploaded" } }); }
    if (path === "/api/library/books/upload-book/bind-upload") { calls.bind++; return route.fulfill({ json: { ok: true } }); }
    if (path === "/api/books/upload-book/status") return route.fulfill({ json: status(calls.process ? "ocr_ready" : "uploaded", calls.process ? 1 : 0) });
    if (path === "/api/books/upload-book/process") { calls.process++; await new Promise(resolve => setTimeout(resolve, 500));
      return route.fulfill({ json: status("ocr_ready", 1) }); }
    if (path === "/api/books/upload-book/structure" && method === "POST") { calls.structure++; return route.fulfill({ json: structure }); }
    if (path === "/api/library/books/upload-book/claim") { calls.claim++; claimed = true; return route.fulfill({ json: book }); }
    if (path === "/api/books/final-book/structure") return route.fulfill({ json: structure });
    if (path === "/api/books/final-book/diagnostics") { calls.diagnostics++; ready = true;
      return route.fulfill({ json: { book_id: "final-book", ready: true, item_count: 2, chapter_count: 1, choice_count: 2, explanation_count: 0 } }); }
    return route.fulfill({ status: 404, json: { detail: `No fixture for ${method} ${path}` } });
  });
  try {
    await page.goto(new URL("/", origin).href);
    await page.getByRole("button", { name: "暂不登录" }).click();
    await page.getByRole("button", { name: "导入课程" }).first().click();
    let dialog = page.getByRole("dialog", { name: "上传书籍" });
    await dialog.locator('input[type="file"]').setInputFiles({ name: "textbook.pdf", mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n% Mocked API receives this upload.\n") });
    await dialog.getByRole("button", { name: "上传并继续" }).click();
    await expect(dialog).toContainText("正在上传文件");
    await expect(dialog).not.toContainText("解析页面与版面");
    await expect(dialog.getByRole("alert")).toBeVisible();
    await expect(dialog).toContainText("textbook.pdf");
    await dialog.getByRole("button", { name: "上传并继续" }).click();
    await expect(dialog).toContainText("已上传 · 待解析");
    expect(calls).toMatchObject({ upload: 2, bind: 0, process: 0 });
    await page.screenshot({ path: info.outputPath("demo-upload-confirm.png"), fullPage: true });
    await page.setViewportSize({ width: 320, height: 720 });
    await expect(dialog.getByRole("button", { name: "开始解析" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath("demo-upload-confirm-narrow.png"), fullPage: true });
    await page.setViewportSize({ width: 393, height: 852 });

    await page.reload();
    const guest = page.getByRole("button", { name: "暂不登录" });
    await expect(guest).toBeVisible();
    await guest.click();
    await page.getByRole("button", { name: "导入课程" }).first().click();
    dialog = page.getByRole("dialog", { name: "上传书籍" });
    await expect(dialog.getByRole("button", { name: "开始解析" })).toBeVisible();
    expect(calls).toMatchObject({ upload: 2, bind: 0, process: 0 });
    await dialog.getByRole("button", { name: "开始解析" }).click();
    await expect(dialog).toContainText("解析页面与版面");
    await page.screenshot({ path: info.outputPath("demo-upload-processing.png"), fullPage: true });
    await expect(dialog).toContainText("教材已准备好");
    await expect(dialog).toContainText("测试教材");
    await page.screenshot({ path: info.outputPath("demo-upload-complete.png"), fullPage: true });
    expect(calls).toMatchObject({ upload: 2, bind: 1, process: 1, structure: 1, claim: 1, diagnostics: 1 });
    await page.setViewportSize({ width: 320, height: 720 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await expect(dialog.getByRole("button", { name: "进入学习" })).toBeVisible();
    await page.setViewportSize({ width: 393, height: 852 });
    await dialog.getByRole("button", { name: "进入学习" }).click();
    await expect(page.locator(".study-screen")).toContainText("测试教材");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
