import { expect, test } from "playwright/test";

test.describe("production HomeScreen list states", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "reduce", timezoneId: "Asia/Hong_Kong" });

  test.beforeEach(async ({ page }) => {
    await page.goto("/e2e/home-screen-state-harness.html");
    await expect(page.locator(".home-dashboard")).toBeVisible();
  });

  test("keeps initial loading, list error, and confirmed empty states mutually exclusive", async ({ page }) => {
    const dashboard = page.locator(".home-dashboard");
    await expect(dashboard.locator(".home-book-carousel-skeleton")).toBeVisible();
    const loadingWorkspace = dashboard.locator(".home-book-workspace.is-loading");
    await expect(loadingWorkspace).toBeVisible();
    const loadingLayout = await loadingWorkspace.evaluate((element) => ({
      height: element.getBoundingClientRect().height,
      toolCards: Array.from(element.querySelectorAll<HTMLElement>(".home-workspace-loading-tools > div > span"))
        .map((card) => card.getBoundingClientRect().height)
    }));
    expect(loadingLayout.height, "loading and ready workspaces keep comparable compact heights").toBeLessThan(390);
    expect(loadingLayout.toolCards.every((height) => Math.abs(height - 134) <= 1), JSON.stringify(loadingLayout.toolCards)).toBe(true);
    await expect(dashboard.getByRole("status").filter({ hasText: "正在加载教材列表" })).toHaveCount(2);
    await expect(dashboard.locator("button")).toHaveCount(0);
    await expect(dashboard.getByText("还没有教材", { exact: true })).toHaveCount(0);

    await page.evaluate(() => window.__homeScreenStateHarness?.setMode("error"));
    const error = dashboard.getByRole("alert");
    await expect(error).toContainText("教材列表暂时无法更新");
    await expect(error.getByRole("button", { name: "重新加载", exact: true })).toBeVisible();
    await expect(dashboard.locator(".home-book-picker, .home-book-workspace, .home-global-section")).toHaveCount(0);
    await expect(dashboard.getByText("还没有教材", { exact: true })).toHaveCount(0);
    await expect(dashboard.getByRole("button")).toHaveCount(1);

    await error.getByRole("button", { name: "重新加载", exact: true }).click();
    await expect(dashboard.locator(".home-book-carousel-skeleton")).toBeVisible();
    await expect(dashboard.locator("button")).toHaveCount(0);
    await page.evaluate(() => window.__homeScreenStateHarness?.releaseRefresh());
    await expect(dashboard.getByText("还没有教材", { exact: true })).toBeVisible();
    await expect(dashboard.getByRole("button", { name: /上传/ })).toHaveCount(1);
    await expect(dashboard.getByRole("button", { name: "上传第一本教材", exact: true })).toBeVisible();
    await expect(dashboard.locator(".home-book-workspace.is-empty")).toBeVisible();
    await expect(dashboard.locator(".home-book-workspace.is-empty button")).toHaveCount(0);
    await expect(dashboard.locator(".home-global-section")).toHaveCount(0);
  });

  test("clears a failed selection announcement when that failed book disappears on refresh", async ({ page }) => {
    await page.evaluate(() => window.__homeScreenStateHarness?.setMode("selection-failure"));
    const listbox = page.getByRole("listbox", { name: "选择教材" });
    const initialOption = listbox.locator('[data-book-id="book-a"]');
    const failedOption = listbox.locator('[data-book-id="book-b"]');
    await expect(failedOption).toBeVisible();
    await expect(initialOption).toHaveAttribute("aria-selected", "true");
    await failedOption.click();
    const announcement = page.getByRole("status").filter({ hasText: /未能打开.*已回到/ });
    await expect(announcement).toBeVisible();
    await expect(announcement.getByRole("button", { name: "重试切换", exact: true })).toBeVisible();

    await page.evaluate(() => window.__homeScreenStateHarness?.removeFailedBook());
    await expect(failedOption).toHaveCount(0);
    await expect(announcement).toHaveCount(0);
    await expect(page.getByRole("button", { name: "重试切换", exact: true })).toHaveCount(0);
  });

  test("routes the confirmed empty upload action through the production HomeScreen callback", async ({ page }) => {
    await page.evaluate(() => window.__homeScreenStateHarness?.setMode("empty"));
    const uploadActions = page.getByRole("button", { name: /上传/ });
    await expect(uploadActions).toHaveCount(1);
    await expect(uploadActions).toHaveAccessibleName("上传第一本教材");
    await uploadActions.click();
    expect(await page.evaluate(() => window.__homeScreenStateHarness?.getRoutes())).toEqual(["upload"]);
  });

  test("only enters chapter confirmation after the production selectCourse action succeeds", async ({ page }) => {
    await page.evaluate(() => window.__homeScreenStateHarness?.setMode("review-failure"));
    await page.getByRole("button", { name: "确认课程目录", exact: true }).click();
    expect(await page.evaluate(() => window.__homeScreenStateHarness?.getRoutes())).toEqual([]);

    await page.evaluate(() => window.__homeScreenStateHarness?.setMode("review-success"));
    await page.getByRole("button", { name: "确认课程目录", exact: true }).click();
    expect(await page.evaluate(() => window.__homeScreenStateHarness?.getRoutes())).toEqual(["chapterConfirm"]);
  });

  test("shows only honest lower actions and routes them through production HomeScreen", async ({ page }, testInfo) => {
    await page.evaluate(() => window.__homeScreenStateHarness?.setMode("global-actions"));
    const section = page.getByRole("region", { name: "学习安排" });
    await expect(section).toBeVisible();
    await expect(section.getByText("1 项待完成", { exact: true })).toBeVisible();
    await expect(section.locator("[data-home-global-action]")).toHaveCount(3);
    await expect(section.locator('[data-home-global-action="plan"]')).toBeVisible();
    await expect(section.locator('[data-home-global-action="mistakes"]')).toBeVisible();
    await expect(section.locator('[data-home-global-action="upload"]')).toBeVisible();
    await expect(page.getByText("学习工具", { exact: true })).toHaveCount(0);
    await expect(page.getByText("解析教材", { exact: true })).toHaveCount(0);
    await expect(page.getByText("生成目录", { exact: true })).toHaveCount(0);
    await expect(page.getByText("建立知识库", { exact: true })).toHaveCount(0);
    await expect(section.locator('[data-home-global-action="library"], [data-home-global-action="study"], [data-home-global-action="home"]')).toHaveCount(0);

    await section.locator('[data-home-global-action="plan"]').click();
    await section.locator('[data-home-global-action="mistakes"]').click();
    await section.locator('[data-home-global-action="upload"]').click();
    expect(await page.evaluate(() => window.__homeScreenStateHarness?.getRoutes())).toEqual(["plan", "mistakes", "upload"]);

    const layout = await section.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
      targets: Array.from(element.querySelectorAll<HTMLElement>("button")).map((button) => ({
        height: button.getBoundingClientRect().height,
        width: button.getBoundingClientRect().width
      }))
    }));
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
    expect(layout.targets.every((target) => target.height >= 44 && target.width >= 44), JSON.stringify(layout.targets)).toBe(true);

    if (testInfo.project.name === "iphone-17-pro") {
      await page.screenshot({
        fullPage: true,
        path: "output/playwright/phase5-home-global-actions-iphone.png"
      });
    }
  });

  test("removes plan without real tasks and keeps non-ready books free of chapter-only tools", async ({ page }) => {
    await page.evaluate(() => window.__homeScreenStateHarness?.setMode("global-no-plan"));
    let section = page.getByRole("region", { name: "学习安排" });
    await expect(section.locator('[data-home-global-action="plan"]')).toHaveCount(0);
    await expect(section.locator('[data-home-global-action="mistakes"]')).toBeVisible();
    await expect(section.locator('[data-home-global-action="upload"]')).toBeVisible();

    await page.evaluate(() => window.__homeScreenStateHarness?.setMode("review-failure"));
    section = page.getByRole("region", { name: "学习安排" });
    await expect(section.locator("[data-home-global-action]")).toHaveCount(1);
    await expect(section.locator('[data-home-global-action="upload"]')).toBeVisible();
    await expect(page.locator(".study-tool-grid, [data-tool]")).toHaveCount(0);
  });
});
