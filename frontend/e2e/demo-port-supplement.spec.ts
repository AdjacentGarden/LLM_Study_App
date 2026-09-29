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

test("a supplementary chapter opens Demo reading, practice, cards and mistakes before diagnosis", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 402, height: 874 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors: string[] = [];
  const interviewRequests: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route(url => url.pathname.startsWith("/api/"), route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.startsWith("/api/interviews")) interviewRequests.push(path);
    if (path === "/api/auth/me") return route.fulfill({ json: {
      account: null, user_id: "supplement-visitor", legacy_profile: false,
      email_available: false, demo_available: false, demo_users: [], learning_sessions: {},
    } });
    if (path === "/api/library") return route.fulfill({ json: [{
      book_id: "biology", title: "生物学 · 遗传与进化", status: "ready", page_count: 180,
      chapter_count: 2, summary: "遗传规律", diagnostics_ready: false,
    }] });
    if (path === "/api/books/biology/structure") return route.fulfill({ json: {
      title: "生物学 · 遗传与进化", summary: "遗传规律", source_page_count: 180,
      chapters: [
        { chapter_id: "ch1", order: 0, title: "第 1 章 遗传因子的发现", start_page: 1, end_page: 20,
          summary: "遗传规律", knowledge_points: [], evidence: [], level: 1 },
        { chapter_id: "sec1", parent_id: "ch1", order: 1, title: "第 1 节 孟德尔的豌豆杂交实验（一）",
          start_page: 2, end_page: 8, summary: "补充学习", knowledge_points: [], evidence: [], level: 2,
          has_supplementary_content: true },
      ],
    } });
    if (path === "/api/user/profile") return route.fulfill({ json: { nickname: "", age: null, bio: "", revision: 0, avatar_url: null } });
    if (path === "/api/books/biology/supplementary-lessons/sec1") return route.fulfill({ json: {
      book_id: "biology", chapter_id: "sec1", title: "孟德尔的豌豆杂交实验（一）",
      summary: "用豌豆实验认识显性与隐性性状。",
      blocks: [{ title: "实验思路", content: "观察亲代和子代的性状。" }],
      cards: [{ id: "card1", front: "什么是显性性状？" }, { id: "card2", front: "什么是隐性性状？" }],
      questions: [{ id: "q1", prompt: "孟德尔研究了哪种植物？", choices: ["玉米", "豌豆"],
        instruction: "选择实验植物。", question_type: "choice" }],
    } });
    if (path === "/api/books/biology/supplementary-lessons/sec1/check") return route.fulfill({ json: {
      correct: JSON.parse(route.request().postData() || "{}").answer === "豌豆",
      answer: "豌豆", explanation: "孟德尔选用豌豆进行杂交实验。",
    } });
    if (path === "/api/books/biology/supplementary-lessons/sec1/cards/card1/reveal") return route.fulfill({ json: { back: "在杂种子一代中表现出来的性状。" } });
    if (path === "/api/books/biology/supplementary-lessons/sec1/cards/card2/reveal") return route.fulfill({ json: { back: "在杂种子一代中未表现出来的性状。" } });
    return route.fulfill({ status: 404, json: { detail: "No fixture" } });
  });
  try {
    await page.goto(new URL("/?ui=demo-full&embedded=1", origin).href);
    await page.getByRole("button", { name: "暂不登录" }).click();
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "学习" }).click();
    const tools = page.locator(".study-tool-grid").first();
    await expect(tools).toBeVisible();

    await page.locator(".study-enter-button").first().click();
    await page.locator(".lesson-knowledge-pager").focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.locator(".lesson-screen")).toContainText("观察亲代和子代的性状");
    await page.getByRole("button", { name: "返回", exact: true }).click();

    await tools.locator('[data-tool="assignment"]').click();
    await expect(page.locator(".assignment-question")).toContainText("孟德尔研究了哪种植物");
    await page.getByRole("group", { name: "题目选项" }).getByRole("button", { name: /玉米/ }).click();
    await page.getByRole("button", { name: "提交答案" }).click();
    await expect(page.locator(".demo-port-assignment-feedback")).toContainText("再巩固一下");
    await page.getByRole("button", { name: "返回", exact: true }).click();

    await tools.locator('[data-tool="mistakes"]').click();
    await expect(page.locator(".mistake-detail-card")).toContainText("孟德尔研究了哪种植物");
    await page.getByRole("button", { name: "重做此题" }).click();
    await page.getByRole("group", { name: "题目选项" }).getByRole("button", { name: /豌豆/ }).click();
    await page.getByRole("button", { name: "提交答案" }).click();
    await expect(page.locator(".demo-port-assignment-feedback")).toContainText("回答正确");
    await page.getByRole("button", { name: "返回", exact: true }).click();

    await tools.locator('[data-tool="flashcards"]').click();
    await expect(page.locator(".memory-card")).toContainText("什么是显性性状");
    await page.locator(".memory-card-trigger").click();
    await expect(page.locator(".memory-card")).toContainText("在杂种子一代中表现出来的性状");
    await page.getByRole("button", { name: /记住了/ }).click();
    await expect(page.locator(".memory-card")).toContainText("什么是隐性性状");
    await page.getByRole("button", { name: "返回", exact: true }).click();
    for (const width of [320, 402, 834, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    }

    expect(interviewRequests).toEqual([]);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
