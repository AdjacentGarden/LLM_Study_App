import { expect, test, type Browser, type BrowserContext, type Page, type Route } from "@playwright/test";

// This suite must be opted into the isolated migration preview explicitly.
// All unmatched API reads are proxied to 8114 here, so Vite's normal proxy
// configuration can never send this test to the user's 8100 service.
const UI_URL = "http://127.0.0.1:5184";
const API_URL = "http://127.0.0.1:8114";
const PENDING = "cloudpath.next.pending-upload";

test.beforeEach(() => {
  test.skip(
    process.env.NEXT_UPLOAD_E2E !== "1" ||
      process.env.NEXT_UPLOAD_E2E_UI_URL !== UI_URL ||
      process.env.NEXT_UPLOAD_E2E_API_URL !== API_URL,
    "Opt in with NEXT_UPLOAD_E2E=1 and the isolated 5184 UI / 8114 API URLs.",
  );
});

type Stage = "uploaded" | "bound" | "processing" | "structuring" | "claiming" | "diagnostics";
type PendingTask = { bookId: string; filename: string; stage: Stage; canonicalId?: string };
type Fault = "bind" | "structure" | "claim" | "diagnostics";

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

function scenario(bookId: string, faults: Partial<Record<Fault, number>> = {}, holdBind = false) {
  const canonicalId = `${bookId}-canonical`;
  const calls = { upload: 0, bind: 0, status: 0, process: 0, ocrRetry: 0, structure: 0, claim: 0, diagnostics: 0 };
  const remaining = { ...faults };
  const bindEntered = deferred();
  const bindRelease = deferred();
  const bindFulfilled = deferred();
  let status = "uploaded";
  let claimed = false;
  const canonicalBook = { book_id: canonicalId, title: "恢复测试", status: "structured", page_count: 2, chapter_count: 0, summary: "", diagnostics_ready: false };
  const canonicalStructure = { title: "恢复测试", summary: "", source_page_count: 2, chapters: [] };

  const bookStatus = () => ({
    book_id: bookId,
    status,
    progress: status === "uploaded" ? 0 : status === "ocr_ready" ? 0.85 : 1,
    current_step: status === "uploaded" ? "等待处理" : "处理完成",
    quality_score: null,
    needs_human_review: false,
    attempts: 0,
    max_attempts: 3,
    page_count: status === "uploaded" ? null : 2,
    retryable: false,
  });
  const fail = async (route: Route, stage: Fault) => {
    if (!remaining[stage]) return false;
    remaining[stage]!--;
    await route.fulfill({ status: 503, json: { detail: `测试注入：${stage} 阶段失败` } });
    return true;
  };

  async function handle(route: Route, path: string, method: string): Promise<boolean> {
    if (path === "/api/books" && method === "POST") {
      calls.upload++;
      await route.fulfill({ json: { book_id: bookId, filename: "recovery.pdf", status: "uploaded", next: `POST /api/books/${bookId}/process` } });
      return true;
    }
    if (path === `/api/library/books/${bookId}/bind-upload` && method === "POST") {
      calls.bind++;
      if (holdBind) { bindEntered.release(); await bindRelease.promise; }
      if (!(await fail(route, "bind"))) await route.fulfill({ json: { ok: true } });
      bindFulfilled.release();
      return true;
    }
    if (path === `/api/books/${bookId}/status` && method === "GET") {
      calls.status++;
      await route.fulfill({ json: bookStatus() });
      return true;
    }
    if (path === `/api/books/${bookId}/process` && method === "POST") {
      calls.process++;
      status = "ocr_ready";
      await route.fulfill({ json: bookStatus() });
      return true;
    }
    if (path === `/api/books/${bookId}/process/retry` && method === "POST") {
      calls.ocrRetry++;
      await route.fulfill({ status: 409, json: { detail: "普通阶段重试不得调用 OCR retry" } });
      return true;
    }
    if (path === `/api/books/${bookId}/structure` && method === "POST") {
      calls.structure++;
      if (!(await fail(route, "structure"))) {
        status = "structured";
        await route.fulfill({ json: { title: "恢复测试", summary: "", source_page_count: 2, chapters: [] } });
      }
      return true;
    }
    if (path === `/api/library/books/${bookId}/claim` && method === "POST") {
      calls.claim++;
      if (!(await fail(route, "claim"))) {
        claimed = true;
        await route.fulfill({ json: canonicalBook });
      }
      return true;
    }
    if (claimed && path === "/api/library" && method === "GET") {
      await route.fulfill({ json: [canonicalBook] });
      return true;
    }
    if (claimed && path === `/api/books/${canonicalId}/structure` && method === "GET") {
      await route.fulfill({ json: canonicalStructure });
      return true;
    }
    if (path === `/api/books/${canonicalId}/diagnostics` && method === "POST") {
      calls.diagnostics++;
      if (!(await fail(route, "diagnostics"))) {
        await route.fulfill({ json: { book_id: canonicalId, item_count: 2, chapter_count: 1, choice_count: 2, explanation_count: 0, ready: true } });
      }
      return true;
    }
    return false;
  }

  return { bookId, canonicalId, calls, bindEntered, bindRelease, bindFulfilled, handle };
}

async function isolatedApp(browser: Browser, flow: ReturnType<typeof scenario>) {
  const context = await browser.newContext({ viewport: { width: 900, height: 1020 } });
  const initial = await context.request.get(`${API_URL}/api/auth/me`);
  expect(initial.status(), await initial.text()).toBe(200);
  const owner = (await initial.json() as { user_id: string }).user_id;
  const page = await context.newPage();
  await page.route(/^http:\/\/127\.0\.0\.1:5184\/api\//, async (route) => {
    const requested = new URL(route.request().url());
    if (requested.origin !== UI_URL) throw new Error(`Unexpected API origin: ${requested.origin}`);
    if (await flow.handle(route, requested.pathname, route.request().method())) return;
    // The scenario accounts for every write. Nothing here can create books,
    // claims, diagnostics or account changes on the isolated backend.
    if (!/^(GET|HEAD)$/.test(route.request().method())) {
      throw new Error(`Unexpected backend write: ${route.request().method()} ${requested.pathname}`);
    }
    const target = `${API_URL}${requested.pathname}${requested.search}`;
    const response = await route.fetch({ url: target });
    await route.fulfill({ response });
  });
  await page.goto(`${UI_URL}/`);
  await continueGuest(page);
  return { context, page, owner };
}

async function continueGuest(page: Page) {
  await page.getByRole("button", { name: "暂不登录" }).click();
  // A completed claim can restore the learning route after refresh.
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "首页" }).click();
  await expect(page.getByRole("button", { name: "导入课程" }).first()).toBeVisible();
}

async function beginUpload(page: Page) {
  await page.getByRole("button", { name: "导入课程" }).first().click();
  const dialog = page.getByRole("dialog", { name: "上传书籍" });
  await expect(dialog).toBeVisible();
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "recovery.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n% Route-injected upload; no backend file is written.\n"),
  });
  await dialog.getByRole("button", { name: "上传并继续" }).click();
  await expect(dialog.getByRole("button", { name: "开始解析" })).toBeVisible();
  await dialog.getByRole("button", { name: "开始解析" }).click();
  return dialog;
}

async function pending(page: Page, owner: string): Promise<PendingTask | null> {
  return page.evaluate((key) => {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) as PendingTask : null;
  }, `account:${owner}:${PENDING}`);
}

test("bind failure survives refresh and resumes bind plus the initial process", async ({ browser }) => {
  const flow = scenario("recovery-bind", { bind: 1 });
  const { context, page, owner } = await isolatedApp(browser, flow);
  try {
    const dialog = await beginUpload(page);
    await expect(dialog.getByRole("alert")).toContainText("bind 阶段失败");
    await expect.poll(() => pending(page, owner)).toMatchObject({ bookId: flow.bookId, stage: "uploaded" });
    expect(flow.calls).toMatchObject({ upload: 1, bind: 1, process: 0, ocrRetry: 0 });

    await page.reload();
    await continueGuest(page);
    await expect.poll(() => flow.calls.diagnostics).toBe(1);
    await expect.poll(() => pending(page, owner)).toBeNull();
    expect(flow.calls).toMatchObject({ upload: 1, bind: 2, process: 1, ocrRetry: 0, structure: 1, claim: 1, diagnostics: 1 });
    await page.getByRole("button", { name: "导入课程" }).first().click();
    await expect(page.getByRole("dialog", { name: "上传书籍" })).toContainText("教材已准备好");
  } finally { await context.close(); }
});

test("structure, claim and diagnostics resume their own stage after each refresh", async ({ browser }) => {
  const flow = scenario("recovery-stages", { structure: 1, claim: 1, diagnostics: 1 });
  const { context, page, owner } = await isolatedApp(browser, flow);
  try {
    let dialog = await beginUpload(page);
    await expect(dialog.getByRole("alert")).toContainText("structure 阶段失败");
    await expect.poll(() => pending(page, owner)).toMatchObject({ stage: "structuring" });

    await page.reload();
    await continueGuest(page);
    await expect.poll(() => flow.calls.claim).toBe(1);
    await page.getByRole("button", { name: "导入课程" }).first().click();
    dialog = page.getByRole("dialog", { name: "上传书籍" });

    await expect(dialog.getByRole("alert")).toContainText("claim 阶段失败");
    await expect.poll(() => pending(page, owner)).toMatchObject({ stage: "claiming" });

    await page.reload();
    await continueGuest(page);
    await expect.poll(() => flow.calls.diagnostics).toBe(1);
    await page.getByRole("button", { name: "导入课程" }).first().click();
    dialog = page.getByRole("dialog", { name: "上传书籍" });

    await expect(dialog.getByRole("alert")).toContainText("diagnostics 阶段失败");
    await expect.poll(() => pending(page, owner)).toMatchObject({ stage: "diagnostics", canonicalId: flow.canonicalId });
    await expect(dialog).not.toContainText("教材已准备好");
    expect(flow.calls).toMatchObject({ process: 1, ocrRetry: 0, structure: 2, claim: 2, diagnostics: 1 });

    await page.reload();
    await continueGuest(page);
    await page.getByRole("button", { name: "导入课程" }).first().click();
    dialog = page.getByRole("dialog", { name: "上传书籍" });
    await expect.poll(() => pending(page, owner)).toBeNull();
    await expect(dialog).toContainText("教材已准备好");
    expect(flow.calls).toMatchObject({ upload: 1, bind: 1, process: 1, ocrRetry: 0, structure: 2, claim: 2, diagnostics: 2 });
  } finally { await context.close(); }
});

test("late old-identity bind response cannot write the new identity's pending cache", async ({ browser }) => {
  const flow = scenario("recovery-switch", {}, true);
  const { context, page, owner: oldOwner } = await isolatedApp(browser, flow);
  try {
    await beginUpload(page);
    await flow.bindEntered.promise;
    await expect.poll(() => pending(page, oldOwner)).toMatchObject({ stage: "uploaded" });

    // Rotate the real isolated visitor credential while the old request waits.
    // AccountGate's BroadcastChannel reload exercises the same keyed remount
    // used by login/logout, without writing any account or book to 8114.
    await context.clearCookies();
    const identity = await context.request.get(`${API_URL}/api/auth/me`);
    expect(identity.status(), await identity.text()).toBe(200);
    const newOwner = (await identity.json() as { user_id: string }).user_id;
    expect(newOwner).not.toBe(oldOwner);
    await page.evaluate(() => {
      const channel = new BroadcastChannel("zhiwo-account");
      channel.postMessage("changed");
      channel.close();
    });
    await continueGuest(page);

    flow.bindRelease.release();
    await flow.bindFulfilled.promise;
    await page.waitForTimeout(250); // Allow the late fetch continuation to run before negative assertions.
    await expect.poll(() => pending(page, newOwner)).toBeNull();
    await expect.poll(() => flow.calls.process).toBe(0);
    expect(await pending(page, oldOwner)).toMatchObject({ stage: "uploaded" });
    expect(flow.calls).toMatchObject({ upload: 1, bind: 1, status: 0, process: 0, ocrRetry: 0 });
  } finally { flow.bindRelease.release(); await context.close(); }
});
