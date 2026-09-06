import { expect, test, type Page } from "playwright/test";

async function expectNoHorizontalOverflow(page: Page, label: string) {
  const overflow = await page.evaluate(() => ({
    body: document.body.scrollWidth - document.body.clientWidth,
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth
  }));
  expect(overflow.body, `${label}: body`).toBeLessThanOrEqual(1);
  expect(overflow.document, `${label}: document`).toBeLessThanOrEqual(1);
}

async function clickUniqueButton(page: Page, name: string, label: string) {
  const button = page.getByRole("button", { name, exact: true });
  await expect(button, `${label}: unique`).toHaveCount(1);
  await expect(button, `${label}: visible`).toBeVisible();
  await expect(button, `${label}: enabled`).toBeEnabled();
  await button.click();
}

test.describe("production App parse state injection", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "reduce", timezoneId: "Asia/Hong_Kong" });

  test("holds 0%, failure/retry, pre-completion 100%, and done as distinct production states", async ({ page }, testInfo) => {
    test.setTimeout(40_000);
    const observedRequests: string[] = [];
    const uploadedTitle = "超长教材文件名用于验证窄屏错误状态与百分之百进度不会横向溢出";
    page.on("request", (request) => observedRequests.push(request.url()));

    await page.goto("/e2e/processing-state-harness.html");
    await expect(page.locator(".home-dashboard")).toBeVisible();
    await page.locator('[data-home-global-action="upload"]').click();
    await expect(page.locator(".upload-sheet-screen")).toBeVisible();
    await page.locator("input[type=file]").setInputFiles({
      name: `${uploadedTitle} (人民教育出版社, 课程教材研究所).pdf`,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 controlled parse fixture")
    });
    await expect(page.locator(".upload-add-tile.has-selection")).toContainText("文件一");
    await clickUniqueButton(page, "上传并继续", `${testInfo.project.name}: confirm upload`);
    await expect(page.locator(".parse-ready-screen")).toBeVisible();
    await clickUniqueButton(page, "开始解析", `${testInfo.project.name}: start parse`);

    await expect(page.locator(".processing-flow-screen")).toBeVisible();
    const spriteStrip = page.locator(".processing-sprite-strip");
    await expect(spriteStrip).toHaveAttribute("src", "/assets/brand/loading/cloud-course-loading-strip-v1.png");
    await expect(spriteStrip).toBeVisible();
    await expect(page.locator(".processing-card")).toHaveCount(0);
    await expect(page.locator(".processing-progress-summary")).toHaveCount(0);
    await expect(page.getByRole("progressbar")).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "正在准备解析，进度约 0%" })).toBeVisible();
    await expect(page.locator(".stage-row.done")).toHaveCount(0);
    await expect(page.locator('.stage-row[data-stage-status="waiting"]')).toHaveCount(5);
    const stageGeometry = await page.locator(".stage-row").evaluateAll((rows) => rows.map((row) => {
      const title = row.querySelector<HTMLElement>(":scope > strong");
      const status = row.querySelector<HTMLElement>(":scope > small");
      if (!title || !status) throw new Error("Processing stage row is incomplete");
      const rowBounds = row.getBoundingClientRect();
      const titleBounds = title.getBoundingClientRect();
      const statusBounds = status.getBoundingClientRect();
      return {
        height: rowBounds.height,
        rightInset: rowBounds.right - statusBounds.right,
        titleGap: statusBounds.left - titleBounds.right
      };
    }));
    for (const [index, geometry] of stageGeometry.entries()) {
      expect(geometry.height, `${testInfo.project.name}: stage ${index + 1} remains compact`).toBeLessThanOrEqual(56);
      expect(geometry.height, `${testInfo.project.name}: stage ${index + 1} remains reachable`).toBeGreaterThanOrEqual(44);
      expect(geometry.rightInset, `${testInfo.project.name}: stage ${index + 1} status is right-aligned`).toBeLessThanOrEqual(14);
      expect(geometry.titleGap, `${testInfo.project.name}: stage ${index + 1} title clears its status`).toBeGreaterThanOrEqual(8);
    }
    await expect(page.getByRole("button", { name: "查看目录", exact: true })).toHaveCount(0);
    await expectNoHorizontalOverflow(page, `${testInfo.project.name}: zero progress`);

    await page.evaluate(() => window.__processingStateHarness?.setPhase("failed"));
    await expect(page.locator(".processing-status-message")).toContainText("Retry after 30 seconds", { timeout: 6_000 });
    await expect(page.getByRole("status").filter({ hasText: "解析失败" })).toBeVisible();
    await expectNoHorizontalOverflow(page, `${testInfo.project.name}: long failure`);
    await clickUniqueButton(page, "返回重新解析", `${testInfo.project.name}: return for retry`);
    await expect(page.locator(".parse-ready-screen")).toBeVisible();
    await expect(page.locator(".parse-status-feedback")).toContainText("Retry after 30 seconds");
    await expect(page.getByRole("button", { name: "查看后台进度", exact: true })).toHaveCount(0);

    await page.evaluate(() => window.__processingStateHarness?.setPhase("hundred"));
    await clickUniqueButton(page, "重新解析", `${testInfo.project.name}: start a fresh retry job`);
    await expect(page.locator(".processing-flow-screen")).toBeVisible();
    await expect(page.getByRole("progressbar")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "查看目录", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "后台运行，先回首页", exact: true })).toBeVisible();
    await expect(page.locator(".stage-row").nth(4).locator("small")).toHaveText("处理中");
    await expect(page.locator('.stage-row[data-stage-status="processing"]')).toHaveCount(1);
    await expect(page.locator(".stage-row").filter({ has: page.getByText("已完成", { exact: true }) })).toHaveCount(4);
    await expectNoHorizontalOverflow(page, `${testInfo.project.name}: hundred percent but processing`);
    const startedJobIds = await page.evaluate(() => window.__processingStateHarness?.getStartedJobIds() ?? []);
    expect(startedJobIds).toHaveLength(2);
    expect(new Set(startedJobIds).size, "retry starts a fresh parse job id").toBe(2);

    await page.evaluate(() => {
      window.__processingStateHarness?.holdHydration();
      window.__processingStateHarness?.setPhase("done");
    });
    await expect(page.locator(".stage-row small")).toHaveText(Array.from({ length: 5 }, () => "已完成"));
    await page.evaluate(() => window.__processingStateHarness?.releaseHydration());
    await expect(page.locator(".chapter-confirm-screen"), `${testInfo.project.name}: done hydration replaces processing`).toBeVisible({ timeout: 6_000 });
    await expect(page.locator(".processing-flow-screen")).toHaveCount(0);
    await expect(page.locator(".toc-directory")).toBeVisible();
    await expect(page.locator(".book-summary h2")).toHaveText(uploadedTitle);
    await expect(page.getByText("扫描版目录识别证据", { exact: true })).toHaveCount(0);
    await expect(page.locator(".toc-directory-helper")).toHaveCount(0);
    const initialRootExpansionStates = await page.locator(".toc-directory > .toc-node > .toc-entry > .toc-expand-button").evaluateAll((toggles) => (
      toggles.map((toggle) => toggle.getAttribute("aria-expanded"))
    ));
    expect(initialRootExpansionStates.every((expanded) => expanded === "false"), "top-level chapters start collapsed").toBe(true);
    const metricHeights = await page.locator(".mapping-summary .metric-card").evaluateAll((metrics) => (
      metrics.map((metric) => metric.getBoundingClientRect().height)
    ));
    expect(metricHeights).toHaveLength(3);
    metricHeights.forEach((height, index) => {
      expect(height, `${testInfo.project.name}: summary metric ${index + 1} stays compact`).toBeLessThanOrEqual(64);
    });
    const rootRows = page.locator(".toc-directory > .toc-node > .toc-entry");
    const rootRowHeights = await rootRows.evaluateAll((rows) => rows.map((row) => row.getBoundingClientRect().height));
    expect(rootRowHeights.length).toBeGreaterThan(1);
    rootRowHeights.forEach((height, index) => {
      expect(height, `${testInfo.project.name}: collapsed chapter row ${index + 1} stays compact`).toBeLessThanOrEqual(68);
    });
    const directoryRowBackgrounds = await rootRows.evaluateAll((rows) => (
      rows.slice(0, 2).map((row) => getComputedStyle(row).backgroundColor)
    ));
    expect(directoryRowBackgrounds).toHaveLength(2);
    expect(directoryRowBackgrounds[0], "selected directory rows keep the same neutral surface as other rows").toBe(directoryRowBackgrounds[1]);
    const rootToggles = page.locator(".toc-directory > .toc-node > .toc-entry > .toc-expand-button");
    if (await rootToggles.count() > 1) {
      await rootToggles.nth(0).click();
      await expect(rootToggles.nth(0)).toHaveAttribute("aria-expanded", "true");
      await rootToggles.nth(1).click();
      await expect(rootToggles.nth(0), "opening another top-level chapter closes the previous chapter").toHaveAttribute("aria-expanded", "false");
      await expect(rootToggles.nth(1)).toHaveAttribute("aria-expanded", "true");
    }
    const chapterHeaderStyle = await page.locator(".header-glass").evaluate((header) => ({
      backdropFilter: getComputedStyle(header).backdropFilter,
      backgroundColor: getComputedStyle(header).backgroundColor
    }));
    expect(chapterHeaderStyle.backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
    expect(chapterHeaderStyle.backdropFilter).toBe("none");
    await expectNoHorizontalOverflow(page, `${testInfo.project.name}: hydrated chapter confirmation`);

    const appOrigin = new URL(page.url()).origin;
    const forbiddenRequests = observedRequests.filter((requestUrl) => {
      const url = new URL(requestUrl);
      return url.origin !== appOrigin || url.pathname.startsWith("/api/");
    });
    expect(forbiddenRequests, "controlled repository flow never restores the retired HTTP boundary").toEqual([]);
  });
});
