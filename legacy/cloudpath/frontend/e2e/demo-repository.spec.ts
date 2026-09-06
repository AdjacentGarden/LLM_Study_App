import { expect, test, type Locator, type Page } from "playwright/test";

async function expectScreenReady(page: Page, selector: string, label: string) {
  const screen = page.locator(selector);
  await expect(screen, `${label}: exactly one target screen is mounted`).toHaveCount(1);
  await expect(screen, `${label}: target screen is visible`).toBeVisible();
  await expect(page.locator(".motion-screen-transition"), `${label}: screen transition is settled`).toHaveAttribute("data-motion-state", "idle");
  return screen;
}

async function clickUniqueAction(page: Page, action: Locator, label: string) {
  await expect(page.locator(".motion-screen-transition"), `${label}: current screen is settled before interaction`).toHaveAttribute("data-motion-state", "idle");
  await expect(action, `${label}: action is unique`).toHaveCount(1);
  await expect(action, `${label}: action is visible`).toBeVisible();
  await expect(action, `${label}: action is enabled`).toBeEnabled();
  await action.click();
}

async function advanceAssignmentToShortAnswer(page: import("playwright/test").Page) {
  await page.locator(".assignment-judgment-options button").first().click();
  await page.locator(".assignment-primary-action .button").click();
  await expect(page.locator('.assignment-exercise-card[data-assignment-type="choice"]')).toBeVisible();
  await page.locator(".assignment-choice-options button").nth(1).click();
  await page.locator(".assignment-primary-action .button").click();
  await expect(page.locator('.assignment-exercise-card[data-assignment-type="short-answer"]')).toBeVisible();
}

test.describe("local DemoRepository P0 flow", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "reduce", timezoneId: "Asia/Hong_Kong" });

  test("opens the meiosis chapter and blocks an empty diagnosis submission", async ({ page }) => {
    await page.goto("/?embedded=device-preview");

    await expectScreenReady(page, ".home-dashboard", "initial home");
    const continueLearning = page.getByRole("button", { name: "继续学习", exact: true });
    await clickUniqueAction(page, continueLearning, "continue the current chapter");

    const lesson = await expectScreenReady(page, ".lesson-screen", "current chapter lesson");
    await expect(lesson.locator(".lesson-title-card h2")).toHaveText("减数分裂和受精作用");
    await clickUniqueAction(
      page,
      lesson.getByRole("button", { name: "做练习", exact: true }),
      "open assignment diagnosis"
    );
    await expectScreenReady(page, ".assignment-screen", "assignment");
    await advanceAssignmentToShortAnswer(page);
    await clickUniqueAction(page, page.getByRole("button", { name: "提交作业", exact: true }), "submit the empty assignment");
    await expect(page.locator("#assignment-answer-error")).toHaveText("请先填写答案，再提交作业诊断。");
    await expect(page.locator(".assignment-card textarea")).toBeFocused();
  });

  test("replays upload through lesson completion with deterministic local data only", async ({ page }) => {
    test.setTimeout(75_000);

    const observedRequests: string[] = [];
    let appOrigin = "";
    page.on("request", (request) => {
      observedRequests.push(request.url());
    });

    await page.goto("/?embedded=device-preview");
    appOrigin = new URL(page.url()).origin;
    await expectScreenReady(page, ".home-dashboard", "upload replay home");
    const homeUploadAction = page.locator('[data-home-global-action="upload"]');
    await expect(homeUploadAction).toHaveAccessibleName("上传新书，添加另一份教材");
    await clickUniqueAction(page, homeUploadAction, "open upload");
    await expectScreenReady(page, ".upload-sheet-screen", "upload");

    await page.locator("input[type=file]").setInputFiles({
      name: "biology-demo.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 local demo fixture")
    });
    await expect(page.locator(".upload-add-tile.has-selection")).toContainText("文件一");
    await clickUniqueAction(page, page.getByRole("button", { name: "上传并继续", exact: true }), "confirm the selected upload");
    await expectScreenReady(page, ".parse-ready-screen", "parse ready");

    await clickUniqueAction(page, page.getByRole("button", { name: "开始解析", exact: true }), "start parsing");
    await expectScreenReady(page, ".processing-flow-screen", "processing");
    for (const progress of [18, 46, 74]) {
      await expect.poll(
        async () => page.locator(".processing-card .progress-wrap").getAttribute("aria-label"),
        { timeout: 8_000 }
      ).toBe(`解析进度 ${progress}%`);
    }

    await expectScreenReady(page, ".chapter-confirm-screen", "chapter confirmation");
    const confirmCourseButton = page.getByRole("button", { name: "确认生成课程", exact: true });
    await clickUniqueAction(page, confirmCourseButton, "confirm chapters and generate the course");

    await expectScreenReady(page, ".course-ready-screen", "course ready");
    await clickUniqueAction(page, page.getByRole("button", { name: "进入学习", exact: true }), "enter the generated course");
    await expectScreenReady(page, ".book-course-screen", "generated study directory");
    const generatedSecondChapter = page.getByRole("button", { name: "第 2 章 基因和染色体的关系 3 个小节 教材第 15-40 页 学习进度 17%", exact: true });
    if (await generatedSecondChapter.getAttribute("aria-expanded") !== "true") {
      await clickUniqueAction(page, generatedSecondChapter, "expand the generated chapter");
    }
    const generatedMeiosisToggle = page.locator('.study-section-toggle[aria-label="第 1 节 减数分裂和受精作用 教材第 16-26 页"]');
    await expect(generatedMeiosisToggle).toHaveCount(1);
    await expect(generatedMeiosisToggle).toBeVisible();
    if (await generatedMeiosisToggle.getAttribute("aria-expanded") !== "true") {
      await clickUniqueAction(page, generatedMeiosisToggle, "expand the generated meiosis section");
    }
    await clickUniqueAction(
      page,
      page.locator(".study-section", { has: generatedMeiosisToggle })
        .getByRole("button", { name: "作业诊断 提交解题过程，定位理解卡点", exact: true }),
      "open generated assignment diagnosis"
    );
    await expectScreenReady(page, ".assignment-screen", "generated assignment");
    await advanceAssignmentToShortAnswer(page);
    const assignmentAnswer = page.locator(".assignment-card textarea");
    await expect(assignmentAnswer, "assignment answer is unique").toHaveCount(1);
    await expect(assignmentAnswer, "assignment answer is visible").toBeVisible();
    await expect(assignmentAnswer, "assignment answer is enabled").toBeEnabled();
    await assignmentAnswer.fill("同源染色体在减数第一次分裂后期分离，姐妹染色单体在第二次分裂后期分离。");
    await clickUniqueAction(page, page.getByRole("button", { name: "提交作业", exact: true }), "submit the assignment for diagnosis");
    await expectScreenReady(page, ".diagnosis-screen", "diagnosis result");
    await expect(page.locator(".diagnosis-card")).toContainText("卡点");

    await clickUniqueAction(page, page.getByRole("button", { name: "查看错题本", exact: true }), "open the mistake book");
    await expectScreenReady(page, ".mistake-book-screen", "mistake book");
    await expect(page.locator(".mistake-list-item")).toHaveCount(1);
    await expect(page.locator(".mistake-detail-card")).toBeVisible();
    await expect(page.locator(".mistake-detail-card")).toContainText("1 条引用来源已记录");

    await clickUniqueAction(page, page.getByRole("button", { name: "用闪卡巩固", exact: true }), "open mistake flashcards");
    await expectScreenReady(page, ".flashcard-screen", "flashcards");
    await clickUniqueAction(page, page.getByRole("button", { name: /点击查看答案/ }), "reveal the flashcard answer");
    await expect(page.locator(".memory-reveal")).toHaveAttribute("aria-pressed", "true");
    await clickUniqueAction(page, page.getByRole("button", { name: "返回", exact: true }), "return to the mistake book");
    await expectScreenReady(page, ".mistake-book-screen", "returned mistake book");
    await clickUniqueAction(page, page.getByRole("button", { name: "查看原文", exact: true }), "return to the lesson");
    await expectScreenReady(page, ".lesson-screen", "lesson");

    await clickUniqueAction(page, page.getByRole("button", { name: "完成本节", exact: true }), "complete the chapter");
    await expectScreenReady(page, ".book-course-screen", "study directory");
    await expect(page.locator(".report-screen")).toHaveCount(0);

    await page.reload();
    await expect(page.locator(".home-dashboard")).toBeVisible();
    const forbiddenRequests = observedRequests.filter((requestUrl) => {
      try {
        const url = new URL(requestUrl);
        return url.origin !== appOrigin || url.pathname.startsWith("/api");
      } catch {
        return false;
      }
    });
    expect(forbiddenRequests).toEqual([]);
  });
});
