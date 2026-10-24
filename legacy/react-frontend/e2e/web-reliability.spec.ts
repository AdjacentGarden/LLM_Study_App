import { test, expect, type BrowserContext, type Page } from "@playwright/test";
if (process.env.RELIABILITY_WEBKIT_EXECUTABLE) test.use({ launchOptions: { executablePath: process.env.RELIABILITY_WEBKIT_EXECUTABLE } });

// Every API request is intercepted. These tests never generate model traffic.
const book = { book_id: "reliability-book", title: "可靠性测试教材", status: "structured", diagnostics_ready: true, page_count: 2, chapter_count: 1, summary: "用于断网恢复测试" };
const chapter = { chapter_id: "chapter-1", order: 1, title: "可靠性第一章", start_page: 1, end_page: 2, summary: "测试章节", knowledge_points: ["测试知识点"], evidence: [] };
const profile = { user_id: "reader-a", book_id: book.book_id, goal: "foundation", declared_background: "初学", background_level: "beginner", focus_chapter_ids: [], profile_confidence: .8, misconception_candidates: [], constraints: { minutes_per_day: 30 }, chapter_mastery: {}, knowledge_mastery: {}, diagnostic_observations: [], flashcard_reviews: {} };
const interview = { session_id: "session-1", phase: "complete", profile, turn: { turn_id: "complete", phase: "complete", message: "诊断完成", question: null, response_type: null, options: [], item: null, why_asked: null, progress: 1 } };
const course = { course_id: "course-1", version: 1, chapter_id: chapter.chapter_id, chapter_title: chapter.title,
  decision: { depth: "foundation", explanation: "测试", emphasis: [], scaffolds: [] }, opening: "可靠性课程已准备。", summary: "课程内容", original_reading: [], knowledge_points: [], flashcards: [], worked_examples: [], checkpoint_questions: [], practice_items: [], estimated_minutes: 5, created_at: new Date().toISOString(), unresolved_source_warnings: [] };
type State = { user: string; scope: string; statusFailures: number; prepared: boolean; courseReady: boolean; allowJob: boolean; posts: any[]; process: number; prepares: number; notePosts: number; unhandled: string[] };
async function fixture(context: BrowserContext) {
  const state: State = { user: "reader-a", scope: "scope-a", statusFailures: 0, prepared: false, courseReady: false, allowJob: false, posts: [], process: 0, prepares: 0, notePosts: 0, unhandled: [] };
  await context.addInitScript(({ b, sid }) => {
    const p = "account:reader-a:";
    localStorage.setItem(p + "zhiwo.active-book", b);
    localStorage.setItem(p + "zhiwo.active-session", sid);
    localStorage.setItem(p + "zhiwo.active-session:" + b, sid);
  }, { b: book.book_id, sid: interview.session_id });
  await context.route(url => url.pathname.startsWith("/api/"), async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value });
    if (path === "/api/auth/me") return json({ account: { email: "reliability@example.com", stage: "working", interests: [], goal: "foundation", is_demo: false }, user_id: state.user, legacy_profile: false, email_available: true, demo_available: false, demo_users: [], learning_sessions: state.user === "reader-a" ? { [book.book_id]: interview.session_id } : {} });
    if (path === "/api/library") return json([book]);
    if (path === `/api/books/${book.book_id}/structure`) return json({ title: book.title, summary: book.summary, source_page_count: 2, chapters: [chapter] });
    if (path === `/api/interviews/${interview.session_id}`) return json(interview);
    if (path.startsWith("/api/learning/memory/")) return json({ items: [], due_count: 0, reviewed_count: 0, delayed_count: 0, week_recalled: 0, week_checks: 0, history: [], now: new Date().toISOString() });
    if (path === "/api/learning/doubts") return json({ items: [], now: Date.now() / 1000 });
    if (path === "/api/library/resources") return json([]);
    if (path === "/api/user/profile") return json({ nickname: "测试读者", age: null, bio: "", revision: 0, avatar_url: null });
    if (path === "/api/studio/capabilities") return json({ image: false, video: false, notes_ai: false, voice_notes: true, draft_scope: state.scope });
    if (path === "/api/studio/jobs") return json({ items: [] });
    if (path === "/api/studio/notes" && method === "GET") return json({ items: [] });
    if (path === "/api/studio/notes" && method === "POST") { state.notePosts++; return json({ detail: "模拟离线：尚未同步" }, 503); }
    if (path.startsWith("/api/studio/notes/")) return json({ detail: "服务器尚无此本地草稿" }, 404);
    if (path === "/api/library/books/pending-scan/bind-upload") return json({ ok: true });
    if (path === "/api/books/pending-scan/status") {
      if (state.statusFailures-- > 0) return route.abort("failed");
      return json({ book_id: "pending-scan", status: "structured", progress: 1, current_step: "完成读取", quality_score: 1, needs_human_review: false, attempts: 1, max_attempts: 3, page_count: 2, retryable: false });
    }
    if (path === "/api/books/pending-scan/process" || path.endsWith("/process/retry")) { state.process++; return json({ detail: "不应重新OCR" }, 500); }
    if (path === "/api/books/pending-scan/prepare") {
      if (method === "POST") state.prepares++; else state.prepared = true;
      return json({ book_id: "pending-scan", status: state.prepared ? "succeeded" : "running", stage: state.prepared ? "ready" : "diagnostics", error: null, updated: 1 });
    }
    if (path === "/api/library/books/pending-scan/claim") return json({ ...book, diagnostics_ready: state.prepared });
    if (path === `/api/interviews/${interview.session_id}/courses/${chapter.chapter_id}`) return state.courseReady ? json(course) : json({ detail: "尚未生成" }, 404);
    const job = () => ({ job_id: "job-1", session_id: interview.session_id, chapter_id: chapter.chapter_id, status: state.allowJob ? "succeeded" : "running", stage: state.allowJob ? "ready" : "reviewing", retryable: false, error: null, course: state.allowJob ? course : null });
    if (path === `/api/interviews/${interview.session_id}/course-jobs` && method === "POST") {
      state.posts.push(request.postDataJSON());
      if (state.posts.length === 1) return route.abort("failed");
      return json(job(), 202);
    }
    if (path === `/api/interviews/${interview.session_id}/course-jobs/job-1`) { if (state.allowJob) state.courseReady = true; return json(job()); }
    state.unhandled.push(method + " " + path);
    return json({ detail: "API explicitly blocked by reliability test" }, 404);
  });
  return state;
}
async function home(page: Page) {
  await page.goto("/?embedded=1");
  await expect(page.getByRole("button", { name: /学习笔记/ })).toBeVisible();
}
async function rawDrafts(page: Page) {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => { const r = indexedDB.open("cloudpath-voice-drafts", 1); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
    const rows = await new Promise<any[]>((resolve, reject) => { const r = db.transaction("drafts").objectStore("drafts").getAll(); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
    db.close();
    return Promise.all(rows.map(async r => ({ scope: r.scope, id: r.note.id, title: r.note.title, recording: r.recording, bytes: await (r.blob ?? new Blob([r.audioBytes], { type: r.audioMime })).text() })));
  });
}
async function fakeRecorder(context: BrowserContext) {
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } });
    class FakeRecorder {
      static isTypeSupported() { return true; }
      state = "inactive"; mimeType = "audio/webm"; ondataavailable: ((e: { data: Blob }) => void) | null = null; onstop: (() => void) | null = null; onerror = null; timer: any;
      start() { this.state = "recording"; this.timer = setInterval(() => this.ondataavailable?.({ data: new Blob(["chunk-" + "x".repeat(64)], { type: this.mimeType }) }), 80); }
      stop() { this.state = "inactive"; clearInterval(this.timer); setTimeout(() => { this.ondataavailable?.({ data: new Blob(["FINAL-CHUNK"], { type: this.mimeType }) }); this.onstop?.(); }, 80); }
    }
    Object.defineProperty(window, "MediaRecorder", { configurable: true, value: FakeRecorder });
  });
}

test("structured upload survives status outage and reload without re-OCR", async ({ context, page }) => {
  const state = await fixture(context); state.statusFailures = 1;
  await context.addInitScript(() => { if (!localStorage.getItem("reliability.pending-seeded")) { localStorage.setItem("reliability.pending-seeded", "1"); localStorage.setItem("account:reader-a:cloudpath.pending-book-upload", JSON.stringify({ bookId: "pending-scan", filename: "existing.pdf" })); } });
  await home(page);
  await expect(page.getByRole("button", { name: /上传教材/ })).toContainText("处理已暂停");
  await page.reload();
  await page.getByRole("button", { name: /上传教材/ }).click();
  await expect(page.getByRole("heading", { name: "准备完成" })).toBeVisible();
  expect(state.process).toBe(0); expect(state.prepares).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem("account:reader-a:cloudpath.pending-book-upload"))).toBeNull();
  expect(state.unhandled).toEqual([]);
});

test("lost course POST retains request ID across reload and permits other navigation", async ({ context, page }) => {
  const state = await fixture(context); await home(page);
  await page.locator(".atlas-trigger").click();
  await page.locator(".atlas-actions").getByRole("button", { name: "学习", exact: true }).click();
  await expect(page.getByRole("button", { name: "继续查看", exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(1);
  await page.reload();
  await page.locator(".atlas-trigger").click();
  await page.locator(".atlas-actions").getByRole("button", { name: "学习", exact: true }).click();
  await expect.poll(() => state.posts.length).toBe(2);
  expect(state.posts[0].request_id).toBeTruthy(); expect(state.posts[1].request_id).toBe(state.posts[0].request_id);
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "书架", exact: true }).click();
  await expect(page.locator(".app-header h1")).toHaveText("书架");
  state.allowJob = true;
  await expect(page.getByRole("button", { name: "打开课程", exact: true })).toBeVisible();
  await expect(page.locator(".app-header h1")).toHaveText("书架");
  await page.getByRole("button", { name: "打开课程", exact: true }).click();
  await expect(page.getByText("可靠性课程已准备。", { exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(2); expect(state.unhandled).toEqual([]);
});

test("recording close waits for final chunk, then reload recovers raw audio and isolates account", async ({ context, page }) => {
  const state = await fixture(context); await fakeRecorder(context); await home(page);
  await page.getByRole("button", { name: /学习笔记/ }).click();
  await page.getByRole("button", { name: "新建语音笔记" }).click();
  await page.getByRole("textbox", { name: "笔记标题" }).fill("断网录音恢复测试");
  await page.getByRole("button", { name: "开始录音", exact: true }).click();
  await expect.poll(async () => (await rawDrafts(page))[0]?.bytes.length ?? 0).toBeGreaterThan(40);
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await expect(page.getByRole("dialog", { name: "学习创作空间" })).not.toBeVisible();
  let drafts = await rawDrafts(page); expect(drafts).toHaveLength(1); expect(drafts[0].recording).toBe(false); expect(drafts[0].bytes).toContain("FINAL-CHUNK");
  expect(state.notePosts).toBeGreaterThan(0);
  await page.reload();
  await page.getByRole("button", { name: /学习笔记/ }).click();
  await page.getByRole("button", { name: /断网录音恢复测试/ }).click();
  await expect(page.getByLabel("语音笔记录音")).toHaveAttribute("src", /^blob:/);
  await expect(page.locator(".ink-save")).toContainText("恢复");
  expect((await rawDrafts(page))[0].bytes).toBe(drafts[0].bytes);
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  state.scope = "scope-b"; state.user = "reader-b";
  await page.reload();
  await page.getByRole("button", { name: /学习笔记/ }).click();
  await expect(page.getByRole("button", { name: /断网录音恢复测试/ })).not.toBeVisible();
  expect(state.unhandled).toEqual([]);
});

test("unavailable IndexedDB never claims recording saved or allows unsafe close", async ({ context, page }) => {
  await fixture(context); await fakeRecorder(context);
  await context.addInitScript(() => Object.defineProperty(window, "indexedDB", { get() { throw new DOMException("disabled", "SecurityError"); } }));
  await home(page); await page.getByRole("button", { name: /学习笔记/ }).click();
  await page.getByRole("button", { name: "新建语音笔记" }).click();
  await page.getByRole("button", { name: "开始录音", exact: true }).click();
  await page.waitForTimeout(250);
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await expect(page.getByRole("dialog", { name: "学习创作空间" })).toBeVisible();
  await expect(page.getByRole("link", { name: /下载.*备份|下载录音/ })).toBeVisible();
  await expect(page.locator(".ink-save")).toContainText("仅在当前页面");
});
