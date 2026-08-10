import { expect, test } from "playwright/test";

test.describe("assignment exercise sequence", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "reduce", timezoneId: "Asia/Hong_Kong" });

  test("runs judgment, choice, and short answer in order without a type selector", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    await page.getByRole("button", { name: "作业诊断，带原文引用", exact: true }).click();

    await page.getByRole("button", { name: /第 2 章 基因和染色体的关系.*3 个小节/ }).click();

    const learningTools = page.getByRole("region", { name: "第 1 节 减数分裂和受精作用的学习方式", exact: true });
    await expect(learningTools).toBeVisible();
    await learningTools.getByRole("button", { name: /作业诊断.*提交解题过程/ }).click();

    const exerciseCard = page.locator(".assignment-exercise-card");
    const action = page.locator(".assignment-primary-action .button");
    const source = page.locator(".assignment-source-button");
    await expect(page.locator('[role="tablist"]')).toHaveCount(0);
    await expect(page.locator(".assignment-workspace > .card")).toHaveCount(2);
    await expect(exerciseCard.locator(".assignment-primary-action")).toHaveCount(1);
    await expect(exerciseCard.locator(".assignment-source-button")).toHaveCount(1);
    await expect(source.locator("svg")).toHaveCount(0);
    await expect(exerciseCard).toHaveAttribute("data-assignment-type", "judgment");
    await expect(page.locator(".assignment-progress-heading > strong")).toHaveText("1 / 3");
    await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle");

    const judgmentComposition = await page.evaluate(() => {
      const shell = document.querySelector(".app-shell");
      const progressCard = document.querySelector(".assignment-progress-card");
      const progressTitle = document.querySelector(".assignment-progress-heading h2");
      const card = document.querySelector('.assignment-exercise-card[data-assignment-type="judgment"]');
      const question = card?.querySelector(".assignment-question");
      const instruction = card?.querySelector(".assignment-exercise-instruction");
      if (!shell || !progressCard || !progressTitle || !card || !question || !instruction) throw new Error("Judgment composition is missing.");
      const shellBounds = shell.getBoundingClientRect();
      const cardBounds = card.getBoundingClientRect();
      const questionBounds = question.getBoundingClientRect();
      const instructionBounds = instruction.getBoundingClientRect();
      return {
        bottomReachesEdge: cardBounds.bottom >= shellBounds.bottom - 1,
        instructionGap: instructionBounds.top - questionBounds.bottom,
        leftReachesEdge: cardBounds.left <= shellBounds.left + 1,
        progressRadius: Number.parseFloat(getComputedStyle(progressCard).borderTopLeftRadius),
        progressTitleFits: progressTitle.scrollWidth <= progressTitle.clientWidth && progressTitle.scrollHeight <= progressTitle.clientHeight,
        rightReachesEdge: cardBounds.right >= shellBounds.right - 1,
        topLeftRadius: Number.parseFloat(getComputedStyle(card).borderTopLeftRadius)
      };
    });
    expect(judgmentComposition.leftReachesEdge).toBe(true);
    expect(judgmentComposition.rightReachesEdge).toBe(true);
    expect(judgmentComposition.bottomReachesEdge).toBe(true);
    expect(judgmentComposition.progressRadius).toBe(999);
    expect(judgmentComposition.progressTitleFits).toBe(true);
    expect(judgmentComposition.topLeftRadius).toBeGreaterThanOrEqual(44);
    expect(judgmentComposition.instructionGap).toBeGreaterThanOrEqual(70);

    await page.locator(".assignment-judgment-options button").first().click();
    await action.click();
    await expect(exerciseCard).toHaveAttribute("data-assignment-type", "choice");
    await expect(page.locator(".assignment-progress-heading > strong")).toHaveText("2 / 3");

    await page.locator(".assignment-choice-options button").nth(1).click();
    await action.click();
    await expect(exerciseCard).toHaveAttribute("data-assignment-type", "short-answer");
    await expect(page.locator(".assignment-progress-heading > strong")).toHaveText("3 / 3");

    await page.locator(".assignment-card textarea").fill("染色体只复制一次，细胞连续分裂两次；第一次分裂时同源染色体分离，使染色体数目减半。");
    await expect(action).toBeEnabled();

    const layout = await page.evaluate(() => {
      const card = document.querySelector(".assignment-exercise-card");
      const source = document.querySelector(".assignment-source-button");
      const submit = document.querySelector(".assignment-primary-action .button");
      const appShell = document.querySelector(".app-shell");
      if (!card || !source || !submit || !appShell) throw new Error("Assignment layout controls are missing.");
      const cardBounds = card.getBoundingClientRect();
      const sourceBounds = source.getBoundingClientRect();
      const submitBounds = submit.getBoundingClientRect();
      const shellStyle = getComputedStyle(appShell);
      return {
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        shellBackgroundColor: shellStyle.backgroundColor,
        shellBackgroundImage: shellStyle.backgroundImage,
        sourceInsideCard: sourceBounds.left >= cardBounds.left && sourceBounds.right <= cardBounds.right && sourceBounds.bottom <= cardBounds.bottom,
        submitInsideCard: submitBounds.left >= cardBounds.left && submitBounds.right <= cardBounds.right && submitBounds.bottom <= cardBounds.bottom,
        sourceToSubmitGap: submitBounds.top - sourceBounds.bottom,
        submitHeight: submitBounds.height
      };
    });
    expect(layout.horizontalOverflow).toBe(0);
    expect(layout.shellBackgroundColor).toBe("rgb(217, 210, 255)");
    expect(layout.shellBackgroundImage).toBe("none");
    expect(layout.sourceInsideCard).toBe(true);
    expect(layout.submitInsideCard).toBe(true);
    expect(layout.sourceToSubmitGap).toBeGreaterThanOrEqual(8);
    expect(layout.submitHeight).toBeGreaterThanOrEqual(44);

    await expect(source).toHaveText(/查看原文/);

    await action.click();
    await expect(page.locator(".diagnosis-screen")).toBeVisible();
  });
});
