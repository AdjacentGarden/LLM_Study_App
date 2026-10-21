import { expect, test, type Page } from "@playwright/test";

const taskKey = "cloudpath.pending-book-upload";
const book = "upload-test-book";
function job(status: string, stage = "diagnostics") {
  return { book_id: book, status, stage, error: status === "failed" ? "练习准备没有完成，请重试。" : "", updated: 1, next_step: 2 };
}

async function fakeApi(page: Page, prepare: (method: string, retry: boolean) => Promise<object> | object) {
  const calls: string[] = [];
  let processed = false;
  let identity = "upload-A";
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const method = route.request().method();
    calls.push(`${method} ${url.pathname}${url.search}`);
    let value: object;
    if (url.pathname === "/api/auth/me") value = {
      user_id: identity, account: { email: `${identity}@example.test`, stage: "university", interests: [], goal: "exam", is_demo: false },
      legacy_profile: false, email_available: true, demo_available: false, demo_users: [], learning_sessions: {},
    };
    else if (url.pathname === "/api/library") value = [];
    else if (url.pathname === "/api/user/profile") value = { nickname: "测试读者", age: null, bio: "", revision: 1, avatar_url: null };
    else if (url.pathname === "/api/books") value = { book_id: book, filename: "教材.pdf", status: "uploaded", next: "process" };
    else if (url.pathname.endsWith("/bind-upload")) value = { ok: true };
    else if (url.pathname.endsWith("/prepare")) value = await prepare(method, url.searchParams.get("retry") === "true");
    else if (url.pathname.endsWith("/claim")) value = { book_id: book, title: "测试教材", status: "structured", diagnostics_ready: true, chapter_count: 2, page_count: 12, summary: "概览" };
    else if (url.pathname.endsWith("/status") || url.pathname.endsWith("/process")) {
      if (url.pathname.endsWith("/process")) processed = true;
      value = { book_id: book, status: processed ? "ocr_ready" : "uploaded", progress: processed ? 1 : 0, current_step: "", quality_score: 1, needs_human_review: false, attempts: 1, max_attempts: 3, page_count: 12, retryable: false };
    } else throw new Error(`Unexpected API request: ${method} ${url.pathname}`);
    await route.fulfill({ json: value }).catch(() => {}); // A navigation can cancel an in-flight poll.
  });
  return { calls, switchAccount: (value: string) => { identity = value; } };
}

test("exercise preparation failure stays retryable and retry preserves the existing OCR", async ({ page, baseURL }, info) => {
  test.skip(!baseURL, "Choose a frontend preview with SOCIAL_E2E_BASE_URL.");
  const api = await fakeApi(page, (method, retry) => retry ? job("succeeded", "ready") : job(method === "POST" ? "running" : "failed"));
  await page.goto(`${baseURL}/?device=iphone-17`);
  const app = page.frameLocator("iframe");
  await app.getByRole("button", { name: /上传教材/ }).click();
  await app.getByLabel("选择要上传的 PDF").setInputFiles({ name: "教材.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7\nmock upload") });
  await app.getByRole("button", { name: /开始上传/ }).click();
  await expect(app.getByRole("alert")).toHaveText("练习准备没有完成，请重试。");
  await expect(app.getByRole("heading", { name: "准备完成", exact: true })).toHaveCount(0);
  expect(api.calls.filter(value => value.endsWith("/claim"))).toHaveLength(0);
  await app.locator(".book-upload-flow").screenshot({ path: info.outputPath("preparation-failed.png") });
  await app.getByRole("button", { name: "重试", exact: true }).click();
  await expect(app.getByRole("heading", { name: "准备完成", exact: true })).toBeVisible();
  expect(api.calls.filter(value => value === "POST /api/books")).toHaveLength(1);
  expect(api.calls.filter(value => value === `POST /api/books/${book}/process`)).toHaveLength(1);
  expect(api.calls.filter(value => value.endsWith("/process/retry"))).toHaveLength(0);
  expect(api.calls.filter(value => value.endsWith("/prepare?retry=true"))).toHaveLength(1);
  expect(api.calls.filter(value => value.endsWith("/claim"))).toHaveLength(1);
  await app.locator(".book-upload-flow").screenshot({ path: info.outputPath("preparation-ready.png") });
});

test("refresh resumes the preparation poll and never reuploads or reruns OCR", async ({ page, baseURL }) => {
  test.skip(!baseURL, "Choose a frontend preview with SOCIAL_E2E_BASE_URL.");
  let ready = false;
  const api = await fakeApi(page, () => job(ready ? "succeeded" : "running", ready ? "ready" : "diagnostics"));
  await page.addInitScript(({ key, id }) => localStorage.setItem(`account:upload-A:${key}`, JSON.stringify({ bookId: id, stage: "preparing" })), { key: taskKey, id: book });
  await page.goto(`${baseURL}/?device=iphone-17`);
  const app = page.frameLocator("iframe");
  await app.getByRole("button", { name: /上传教材/ }).click();
  await expect(app.getByText("正在准备练习…", { exact: true })).toBeVisible();
  await page.reload();
  await app.getByRole("button", { name: /上传教材/ }).click();
  await expect(app.getByText("正在准备练习…", { exact: true })).toBeVisible();
  ready = true;
  await expect(app.getByRole("heading", { name: "准备完成", exact: true })).toBeVisible();
  expect(api.calls.filter(value => value === "POST /api/books" || value.includes("/process") || value.startsWith("POST ") && value.includes("/prepare"))).toHaveLength(0);
  expect(api.calls.filter(value => value.endsWith("/claim"))).toHaveLength(1);
});

test("switching accounts leaves the original task scoped to its owner and ignores late success", async ({ page, baseURL }) => {
  test.skip(!baseURL, "Choose a frontend preview with SOCIAL_E2E_BASE_URL.");
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let polls = 0;
  const api = await fakeApi(page, async () => { polls++; await held; return job("succeeded", "ready"); });
  await page.addInitScript(({ key, id }) => localStorage.setItem(`account:upload-A:${key}`, JSON.stringify({ bookId: id, stage: "preparing" })), { key: taskKey, id: book });
  await page.goto(`${baseURL}/?device=iphone-17`);
  await expect.poll(() => polls).toBeGreaterThan(0);
  api.switchAccount("upload-B");
  await page.evaluate(() => { const channel = new BroadcastChannel("zhiwo-account"); channel.postMessage("changed"); channel.close(); });
  const app = page.frameLocator("iframe");
  await expect(app.getByRole("button", { name: /上传教材/ })).toContainText("支持 PDF 与扫描版");
  release();
  await expect(app.getByRole("button", { name: /上传教材/ })).toContainText("支持 PDF 与扫描版");
  expect(api.calls.filter(value => value.endsWith("/claim"))).toHaveLength(0);
  const stored = await page.evaluate(key => ({ a: localStorage.getItem(`account:upload-A:${key}`), b: localStorage.getItem(`account:upload-B:${key}`) }), taskKey);
  expect(stored.a).toContain(book);
  expect(stored.b).toBeNull();
});
