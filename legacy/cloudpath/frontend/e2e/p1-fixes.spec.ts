import { expect, test, type Page } from "playwright/test";

async function openLesson(page: Page) {
  await page.goto("/?embedded=device-preview");
  await page.getByRole("button", { name: "继续学习", exact: true }).click();
  await expect(page.locator(".lesson-screen")).toBeVisible();
}

async function readMainScrollTop(page: Page) {
  return page.locator(".screen-content").evaluate((element) => element.scrollTop);
}

async function expectNoHorizontalOverflow(page: Page) {
  const widths = await page.evaluate(() => {
    const shell = document.querySelector<HTMLElement>(".app-shell");
    const content = document.querySelector<HTMLElement>(".screen-content");
    if (!shell || !content) throw new Error("P1 layout test requires the app shell and main content");
    return {
      contentClientWidth: content.clientWidth,
      contentScrollWidth: content.scrollWidth,
      shellClientWidth: shell.clientWidth,
      shellScrollWidth: shell.scrollWidth
    };
  });
  expect(widths.contentScrollWidth).toBeLessThanOrEqual(widths.contentClientWidth + 1);
  expect(widths.shellScrollWidth).toBeLessThanOrEqual(widths.shellClientWidth + 1);
}

test.describe("P1 learning flow safeguards", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "reduce", timezoneId: "Asia/Hong_Kong" });

  test("starts new screens at the top, restores back destinations, and leaves ActionSheet scroll untouched", async ({ page }) => {
    await openLesson(page);
    await expectNoHorizontalOverflow(page);
    const main = page.locator(".screen-content");
    await main.evaluate((element) => {
      const inlineScrollBehavior = element.style.scrollBehavior;
      element.style.scrollBehavior = "auto";
      element.scrollTop = element.scrollHeight;
      element.style.scrollBehavior = inlineScrollBehavior;
    });
    const lessonScrollTop = await readMainScrollTop(page);
    expect(lessonScrollTop).toBeGreaterThan(100);

    await page.getByRole("button", { name: "完成本节", exact: true }).click();
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await expect(page.locator(".report-screen")).toHaveCount(0);
    await expect.poll(() => readMainScrollTop(page)).toBe(0);

    await page.getByRole("button", { name: "进入学习", exact: true }).click();
    await expect(page.locator(".lesson-screen")).toBeVisible();
    await expect.poll(() => readMainScrollTop(page)).toBe(0);

    const conceptButton = page.getByRole("button", { name: "查看核心概念：减数分裂", exact: true });
    await conceptButton.scrollIntoViewIfNeeded();
    const lessonScrollBeforeSheet = await readMainScrollTop(page);
    await conceptButton.click();
    const conceptSheet = page.locator(".sheet[data-sheet-type='note']");
    await expect(conceptSheet).toBeVisible();
    await expect.poll(() => readMainScrollTop(page)).toBe(lessonScrollBeforeSheet);
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(conceptSheet).toHaveCount(0);

    await page.getByRole("button", { name: "返回学习目录", exact: true }).click();
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await expect.poll(() => readMainScrollTop(page)).toBe(0);
    await page.getByRole("button", { name: "进入学习", exact: true }).click();
    await expect(page.locator(".lesson-screen")).toBeVisible();
    await expect.poll(() => readMainScrollTop(page)).toBe(0);
  });

  test("keeps choosing local, uploads only after confirmation, and starts parsing only from ParseReady", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    await page.locator('[data-home-global-action="upload"]').click();
    await expect(page.locator(".upload-flow-screen")).toBeVisible();

    const sourceCopy = page.locator(".upload-source-copy");
    const emptySourceCopy = await sourceCopy.innerText();

    await page.locator('input[type="file"]').setInputFiles({
      name: "biology-confirmation.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 confirmation fixture")
    });
    const selectedFileTile = page.locator(".upload-add-tile.has-selection");
    await expect(selectedFileTile).toContainText("文件一");
    await expect(selectedFileTile).not.toContainText("biology-confirmation.pdf");
    await expect(selectedFileTile).toHaveAccessibleName("已选择 1 份学习资料");
    await expect(selectedFileTile.locator(".upload-selected-file-icon")).toBeVisible();
    await expect(selectedFileTile.locator(".upload-add-icon")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "添加更多学习资料", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "删除文件一", exact: true })).toBeVisible();
    await expect(page.locator(".upload-selection-summary")).toHaveCount(0);
    await expect(page.locator(".parse-ready-screen")).toHaveCount(0);
    expect(await sourceCopy.innerText()).toBe(emptySourceCopy);
    await expectNoHorizontalOverflow(page);

    const replacementChooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "添加更多学习资料", exact: true }).click();
    await (await replacementChooser).setFiles({
      name: "biology-replacement (publisher).pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 replacement fixture")
    });
    await expect(selectedFileTile).toContainText("文件二");
    await expect(selectedFileTile).not.toContainText("biology-replacement (publisher).pdf");
    await page.getByRole("button", { name: "删除文件一", exact: true }).click();
    await expect(selectedFileTile).toHaveAccessibleName("已选择 1 份学习资料");
    await expect(selectedFileTile).toContainText("文件一");
    await expect(selectedFileTile).not.toContainText("文件二");

    await page.getByRole("button", { name: "上传并继续", exact: true }).click();
    await expect(page.locator(".parse-ready-screen")).toBeVisible();
    await expect(page.locator(".parse-info-grid")).toContainText("后台解析");
    await expect(page.locator(".parse-ready-summary h2")).toHaveText("biology-replacement");
    await expect(page.locator(".parse-ready-summary h2")).toHaveAttribute("title", "biology-replacement (publisher).pdf");
    await expect(page.locator(".parse-checklist")).toHaveCount(0);
    await expect(page.locator(".processing-flow-screen")).toHaveCount(0);

    await page.getByRole("button", { name: "开始解析", exact: true }).click();
    await expect(page.locator(".processing-flow-screen")).toBeVisible();
  });

  test("collapses deduplicated learner-facing evidence while keeping every source available", async ({ page }) => {
    await openLesson(page);

    const evidenceSummary = page.locator(".lesson-evidence-summary");
    const evidenceToggle = evidenceSummary.getByRole("button", { name: /本节 \d+ 个来源/ });
    await expect(evidenceToggle).toHaveAttribute("aria-expanded", "false");
    await expect(evidenceSummary).not.toContainText("chunk_c2s1_");
    await expect(page.getByRole("button", { name: "来自教材第 18 页", exact: true }).first()).toBeVisible();

    await evidenceToggle.click();
    await expect(evidenceToggle).toHaveAttribute("aria-expanded", "true");
    const sourceButton = evidenceSummary.locator(".lesson-evidence-source").filter({ hasText: "同源染色体先分离" }).first();
    await expect(sourceButton).toContainText("同源染色体先分离");
    await expect(sourceButton).toContainText("教材第 18 页");
    await expectNoHorizontalOverflow(page);
    await sourceButton.click();
    await expect(page.locator(".source-reader-screen")).toBeVisible();
  });
});
