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

test("Demo lesson, multiple-choice assignment and flashcards use the current course API", async ({ browser }, info) => {
  const context = await browser.newContext({ viewport: { width: 402, height: 874 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors: string[] = [];
  const practiceBodies: object[] = [];
  const reviewBodies: object[] = [];
  const questions: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const profile = {
    user_id: "course-user", book_id: "course-book", goal: "理解概念", declared_background: "入门",
    background_level: "beginner", focus_chapter_ids: ["sec1"], profile_confidence: .7,
    misconception_candidates: [], constraints: { minutes_per_day: 30 }, chapter_mastery: {}, knowledge_mastery: {},
    diagnostic_observations: [], flashcard_reviews: {},
  };
  const activity = { duplicate: false, evidence: { score: 1, scoring_confidence: 1, needs_follow_up: false,
    matched_rubric: ["选项 A", "选项 B"], missing_rubric: [] }, profile, course_stale: false, review_state: null };
  const course = {
    course_id: "course-1", version: 1, chapter_id: "sec1", chapter_title: "第 1 节 概念入门",
    decision: { depth: "standard", explanation: "依据诊断", emphasis: [], scaffolds: [] },
    opening: "从原文认识三个概念。", summary: "两项相互关联。",
    original_reading: [{ title: "原文梳理", content: "先阅读原文，再比较两个概念。", purpose: "建立理解",
      citations: [{ page_number: 2, quote: "原文证据" }], media: [] }],
    knowledge_points: [{ point_id: "point-1", title: "关键概念", explanation: "概念解释", importance: "high",
      mastery: .4, state: "learning", citations: [{ page_number: 2, quote: "原文证据" }] }],
    flashcards: [
      { card_id: "card-1", point_id: "point-1", front: "第一个概念是什么？", back: "第一个概念的解释。", reason_for_user: "巩固基础", citations: [{ page_number: 2, quote: "原文证据" }] },
      { card_id: "card-2", point_id: "point-1", front: "第二个概念是什么？", back: "第二个概念的解释。", reason_for_user: "巩固基础", citations: [{ page_number: 2, quote: "原文证据" }] },
    ],
    worked_examples: [], checkpoint_questions: [],
    practice_items: [{ item_id: "item-1", point_id: "point-1", prompt: "选出两项正确陈述", response_type: "multiple_choice",
      options: ["选项 A", "选项 B", "选项 C"], estimated_seconds: 60, citations: [{ page_number: 2, quote: "原文证据" }] }],
    estimated_minutes: 20, created_at: "2026-09-28T00:00:00Z", unresolved_source_warnings: [],
  };
  await page.route(url => url.pathname.startsWith("/api/"), async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { account: null, user_id: "course-user", legacy_profile: true,
      email_available: false, demo_available: false, demo_users: [], learning_sessions: { "course-book": "session-1" } } });
    if (path === "/api/library") return route.fulfill({ json: [{ book_id: "course-book", title: "课程测试教材", status: "ready",
      page_count: 20, chapter_count: 2, summary: "概念教材", diagnostics_ready: true }] });
    if (path === "/api/books/course-book/structure") return route.fulfill({ json: { title: "课程测试教材", summary: "概念教材", source_page_count: 20,
      chapters: [
        { chapter_id: "ch1", order: 0, title: "第 1 章 入门", start_page: 1, end_page: 5, summary: "入门", knowledge_points: [], evidence: [], level: 1 },
        { chapter_id: "sec1", parent_id: "ch1", order: 1, title: "第 1 节 概念入门", start_page: 2, end_page: 5,
          summary: "概念", knowledge_points: ["关键概念"], evidence: [{ page_number: 2, quote: "原文证据" }], level: 2 },
      ] } });
    if (path === "/api/user/profile") return route.fulfill({ json: { nickname: "测试学习者", age: null, bio: "", revision: 0, avatar_url: null } });
    if (path === "/api/interviews/session-1") return route.fulfill({ json: { session_id: "session-1", phase: "complete", profile,
      turn: { turn_id: "done", phase: "complete", message: "完成", question: null, response_type: null,
        options: [], item: null, why_asked: null, progress: 1 } } });
    if (path === "/api/interviews/session-1/study-workspace") return route.fulfill({ json: { session_id: "session-1", book_id: "course-book",
      plan: { minutes_per_day: 30, minutes_source: "profile", days: [], progress: { done: 0, total: 0, percent: 0 } }, mistakes: [] } });
    if (path === "/api/interviews/session-1/courses/sec1") return route.fulfill({ json: course });
    if (path === "/api/books/course-book/qa") {
      questions.push(JSON.parse(route.request().postData() || "{}").question);
      return route.fulfill({ json: { status: "supported", answer: "两个概念相互关联。", confidence: .9,
        evidence_pages: [2], insufficiency_reason: null, claims: [{ text: "概念相互关联", citations: [{ page_number: 2, quote: "原文证据" }] }] } });
    }
    if (path === "/api/interviews/session-1/courses/course-1/practice/item-1") {
      practiceBodies.push(JSON.parse(route.request().postData() || "{}"));
      return route.fulfill({ json: activity });
    }
    if (path === "/api/interviews/session-1/courses/course-1/flashcards/card-1") {
      reviewBodies.push(JSON.parse(route.request().postData() || "{}"));
      return route.fulfill({ json: { ...activity, review_state: { algorithm: "fsrs", interval_days: 1,
        due_at: "2026-09-29T00:00:00Z", last_rating: "good", stability: 1, difficulty: 5 } } });
    }
    return route.fulfill({ status: 404, json: { detail: "No fixture" } });
  });
  try {
    await page.goto(new URL("/", origin).href);
    await expect(page.locator(".app-shell")).toBeVisible();
    await page.getByRole("button", { name: "打开 AI 助手" }).click();
    const assistant = page.getByRole("dialog", { name: "AI 导学助手" });
    await assistant.getByRole("textbox", { name: "向 AI 助手提问" }).fill("两个概念有什么关系？");
    await assistant.getByRole("button", { name: "发送" }).click();
    await expect(assistant).toContainText("两个概念相互关联。");
    await expect(assistant).toContainText("教材原文依据");
    await page.screenshot({ path: info.outputPath("demo-assistant-answer.png"), fullPage: true });
    expect(questions).toEqual(["两个概念有什么关系？"]);
    await assistant.getByRole("button", { name: "收起 AI 助手" }).click();
    await page.getByRole("button", { name: "打开 AI 助手" }).click();
    await expect(page.getByRole("dialog", { name: "AI 导学助手" })).toContainText("两个概念有什么关系？");
    await assistant.getByRole("textbox", { name: "向 AI 助手提问" }).fill("再解释一次？");
    await assistant.getByRole("button", { name: "发送" }).click();
    await expect(assistant.locator(".ai-message-row.user")).toHaveCount(2);
    expect(questions).toEqual(["两个概念有什么关系？", "再解释一次？"]);
    await assistant.getByRole("button", { name: "收起 AI 助手" }).click();
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "学习" }).click();
    const tools = page.locator(".study-tool-grid").first();
    await expect(tools).toBeVisible();

    await page.locator(".study-enter-button").first().click();
    await expect(page.locator(".lesson-screen")).toContainText("从原文认识三个概念");
    await page.locator(".lesson-knowledge-pager").focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.locator(".lesson-screen")).toContainText("先阅读原文，再比较两个概念");
    await page.getByRole("button", { name: "返回", exact: true }).click();

    await tools.locator('[data-tool="assignment"]').click();
    await expect(page.locator(".assignment-question")).toContainText("选出两项正确陈述");
    const options = page.getByRole("group", { name: "请选择所有正确答案" });
    await options.getByRole("button", { name: /选项 A/ }).click();
    await options.getByRole("button", { name: /选项 B/ }).click();
    await expect(options.getByRole("button", { name: /选项 A/ })).toHaveAttribute("aria-pressed", "true");
    await expect(options.getByRole("button", { name: /选项 B/ })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "提交答案" }).click();
    await expect(page.locator(".demo-port-assignment-feedback")).toContainText("100%");
    expect(practiceBodies).toHaveLength(1);
    expect(practiceBodies[0]).toMatchObject({ selected_option_ids: ["0", "1"], answer: "选项 A、选项 B" });
    await page.getByRole("button", { name: "返回", exact: true }).click();

    await tools.locator('[data-tool="flashcards"]').click();
    await expect(page.locator(".memory-card")).toContainText("第一个概念是什么");
    await page.locator(".memory-card-trigger").click();
    await expect(page.locator(".memory-card-answer-face-back h2")).toContainText("第一个概念的解释");
    await page.getByRole("button", { name: /记住了/ }).click();
    await expect(page.locator(".memory-card")).toContainText("第二个概念是什么");
    expect(reviewBodies).toHaveLength(1);
    expect(reviewBodies[0]).toMatchObject({ rating: "good" });
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
