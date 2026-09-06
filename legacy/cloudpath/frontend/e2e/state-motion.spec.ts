import type { Locator, Page } from "playwright/test";
import { expect, test } from "./fixtures";
import type { BookCourseApiFixture } from "./fixtures/bookcourse-api";

async function advanceAssignmentToShortAnswer(page: Page) {
  await page.locator(".assignment-judgment-options button").first().click();
  await page.locator(".assignment-primary-action .button").click();
  await expect(page.locator('.assignment-exercise-card[data-assignment-type="choice"]')).toBeVisible();
  await page.locator(".assignment-choice-options button").nth(1).click();
  await page.locator(".assignment-primary-action .button").click();
  await expect(page.locator('.assignment-exercise-card[data-assignment-type="short-answer"]')).toBeVisible();
}

async function waitForNaturalScreenTransition(page: Page, label: string) {
  await expect(page.locator(".motion-screen-transition"), label).toHaveAttribute("data-motion-state", "idle", { timeout: 2_000 });
}

async function openStageSixCourse(page: Page, bookCourseApi: BookCourseApiFixture) {
  bookCourseApi.useStageSixFlow();
  await page.addInitScript(() => {
    window.localStorage.removeItem("bookcourse-active-parse-session");
  });
  await page.goto("/?embedded=device-preview");
  await expect(page.locator(".home-book-option").first()).toBeVisible();
  await page.locator(".home-book-picker-heading button").click();
  await expect(page.locator(".library-course-grid")).toBeVisible();
  await waitForNaturalScreenTransition(page, "Stage 6 Library transition settles");
  await page.locator(".library-course-grid .course-space-card .button").first().click();
  await expect(page.locator(".book-course-screen")).toBeVisible();
  await waitForNaturalScreenTransition(page, "Stage 6 course transition settles");
}

function cssList(value: string) {
  return value.split(",").map((item) => item.trim());
}

function normalizedTimingList(value: string) {
  return value.split(/\),\s*/).map((item, index, items) => {
    const timing = index < items.length - 1 ? `${item})` : item;
    return timing.replace(/\s+/g, "");
  });
}

async function readFiniteMotionStyle(locator: Locator) {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      animationDuration: style.animationDuration,
      animationName: style.animationName,
      animationTimingFunction: style.animationTimingFunction,
      transitionDuration: style.transitionDuration,
      transitionProperty: style.transitionProperty,
      transitionTimingFunction: style.transitionTimingFunction
    };
  });
}

test.describe("state feedback primitives", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "no-preference", timezoneId: "Asia/Hong_Kong" });

  test.afterEach(({ bookCourseApi }, testInfo) => {
    expect(bookCourseApi.unhandledRequests, `${testInfo.title}: every API request has a local fixture`).toEqual([]);
    expect(bookCourseApi.externalRequests, `${testInfo.title}: no external network request is permitted`).toEqual([]);
    expect(bookCourseApi.consoleErrors, `${testInfo.title}: no console errors are emitted`).toEqual([]);
    expect(bookCourseApi.pageErrors, `${testInfo.title}: no page errors are emitted`).toEqual([]);
  });

  test("settles text, accordion, skeleton, tabs, icon, and error states from real browser events", async ({ page, bookCourseApi }) => {
    void bookCourseApi;
    await page.goto("/e2e/state-motion-harness.html");
    const state = page.locator("#state-motion-harness");
    await expect(state).toBeVisible();

    const harness = async (method: string, ...args: unknown[]) => {
      await page.evaluate(({ method: methodName, args: methodArgs }) => {
        const api = window.__stateMotionHarness;
        if (!api) throw new Error("State motion harness is not ready.");
        (api[methodName as keyof typeof api] as (...values: unknown[]) => void)(...methodArgs);
      }, { method, args });
    };

    await expect(state.locator(".motion-state-swap")).toHaveAttribute("data-motion-text-state", "idle");
    await harness("setText", "处理中");
    await expect(state.locator(".motion-state-swap")).toHaveAttribute("data-motion-text-state", "swapping");
    await expect.poll(() => page.evaluate(() => window.__stateMotionEvents?.animationEnd ?? 0)).toBeGreaterThan(0);
    await expect(state.locator(".motion-state-swap")).toHaveAttribute("data-motion-text-state", "idle");

    await harness("setExpanded", true);
    await expect(state.locator("#accordion-region")).toHaveAttribute("data-motion-collapsible", "expanded");
    await expect(state.locator("#accordion-region")).not.toHaveAttribute("inert", "true");
    await state.locator("#accordion-focus-target").focus();
    await harness("setExpanded", false);
    await expect(state.locator("#accordion-region")).toHaveAttribute("aria-hidden", "true");
    await expect(state.locator("#accordion-toggle")).toBeFocused();

    await harness("setLoadState", "ready", "content");
    await expect(state.locator(".motion-skeleton-reveal")).toHaveAttribute("data-motion-load-state", "revealing");
    await expect.poll(() => page.evaluate(() => window.__stateMotionEvents?.transitionEnd ?? 0)).toBeGreaterThan(0);
    await expect(state.locator(".motion-skeleton-reveal")).toHaveAttribute("data-motion-load-state", "ready");
    await expect(state.locator(".motion-skeleton-reveal")).toHaveAttribute("data-motion-ready-kind", "content");

    await harness("setFilter", "chapter");
    await expect(state.locator(".motion-sliding-filter-button[aria-pressed='true']")).toHaveText("章节");
    await expect(state.locator(".motion-sliding-filter")).toHaveAttribute("data-motion-selection-state", "idle");

    await harness("triggerError");
    const errorShake = state.locator(".motion-error-shake");
    await expect(errorShake).toHaveAttribute("data-motion-error-state", "shaking");
    await expect.poll(() => page.evaluate(() => window.__stateMotionEvents?.animationEnd ?? 0)).toBeGreaterThan(1);
    await expect(errorShake).toHaveAttribute("data-motion-error-state", "idle");

    const beforeUnmount = await page.evaluate(() => ({
      created: window.__motionObserverAudit?.created ?? 0,
      disconnected: window.__motionObserverAudit?.disconnected ?? 0
    }));
    await harness("unmount");
    await expect.poll(() => page.evaluate(() => ({
      created: window.__motionObserverAudit?.created ?? 0,
      disconnected: window.__motionObserverAudit?.disconnected ?? 0
    }))).toEqual({ created: beforeUnmount.created, disconnected: beforeUnmount.created });
  });

  test("uses the 150/180/200ms semantic timelines and curves for every state primitive", async ({ page, bookCourseApi }) => {
    void bookCourseApi;
    await page.goto("/e2e/state-motion-harness.html");
    const state = page.locator("#state-motion-harness");
    await expect(state).toBeVisible();
    await page.addStyleTag({
      content: ".motion-state-swap-layer, .motion-error-shake { animation-play-state: paused !important; }"
    });

    const harness = async (method: string, ...args: unknown[]) => {
      await page.evaluate(({ method: methodName, args: methodArgs }) => {
        const api = window.__stateMotionHarness;
        if (!api) throw new Error("State motion harness is not ready.");
        (api[methodName as keyof typeof api] as (...values: unknown[]) => void)(...methodArgs);
      }, { method, args });
    };

    const collapsed = await readFiniteMotionStyle(state.locator("#accordion-region"));
    expect(cssList(collapsed.transitionDuration)).toEqual(["0.2s"]);
    expect(normalizedTimingList(collapsed.transitionTimingFunction)).toEqual(["cubic-bezier(0.32,0,0.67,0)"]);

    const skeletonLayer = await readFiniteMotionStyle(state.locator(".motion-skeleton-placeholder"));
    expect(cssList(skeletonLayer.transitionDuration)).toEqual(["0.2s"]);
    expect(normalizedTimingList(skeletonLayer.transitionTimingFunction)).toEqual(["cubic-bezier(0.22,1,0.36,1)"]);
    const skeletonPulse = await readFiniteMotionStyle(state.getByTestId("skeleton-placeholder"));
    expect(skeletonPulse.animationDuration).toBe("1.2s");
    expect(normalizedTimingList(skeletonPulse.animationTimingFunction)).toEqual(["cubic-bezier(0.65,0,0.35,1)"]);

    const filterButton = await readFiniteMotionStyle(state.locator(".motion-sliding-filter-button").first());
    expect(cssList(filterButton.transitionDuration)).toEqual(["0.15s"]);
    expect(normalizedTimingList(filterButton.transitionTimingFunction)).toEqual(["cubic-bezier(0.65,0,0.35,1)"]);
    const filterIndicator = await readFiniteMotionStyle(state.locator(".motion-sliding-filter-indicator"));
    expect(cssList(filterIndicator.transitionDuration)).toEqual(["0.2s", "0.2s"]);
    expect(normalizedTimingList(filterIndicator.transitionTimingFunction)).toEqual([
      "cubic-bezier(0.65,0,0.35,1)",
      "cubic-bezier(0.65,0,0.35,1)"
    ]);

    const iconLayer = await readFiniteMotionStyle(state.locator(".motion-icon-swap-layer").first());
    expect(cssList(iconLayer.transitionDuration)).toEqual(["0.15s", "0.15s"]);
    expect(normalizedTimingList(iconLayer.transitionTimingFunction)).toEqual([
      "cubic-bezier(0.65,0,0.35,1)",
      "cubic-bezier(0.65,0,0.35,1)"
    ]);

    await harness("setText", "处理中");
    await expect(state.locator(".motion-state-swap")).toHaveAttribute("data-motion-text-state", "swapping");
    const exitingText = await readFiniteMotionStyle(state.locator(".motion-state-swap-previous"));
    expect(exitingText.animationDuration).toBe("0.15s");
    expect(normalizedTimingList(exitingText.animationTimingFunction)).toEqual(["cubic-bezier(0.32,0,0.67,0)"]);
    const enteringText = await readFiniteMotionStyle(state.locator(".motion-state-swap-current"));
    expect(enteringText.animationDuration).toBe("0.18s");
    expect(normalizedTimingList(enteringText.animationTimingFunction)).toEqual(["cubic-bezier(0.22,1,0.36,1)"]);

    await harness("setExpanded", true);
    const expanded = await readFiniteMotionStyle(state.locator("#accordion-region"));
    expect(cssList(expanded.transitionDuration)).toEqual(["0.2s"]);
    expect(normalizedTimingList(expanded.transitionTimingFunction)).toEqual(["cubic-bezier(0.22,1,0.36,1)"]);

    await harness("triggerError");
    await expect(state.locator(".motion-error-shake")).toHaveAttribute("data-motion-error-state", "shaking");
    const error = await readFiniteMotionStyle(state.locator(".motion-error-shake"));
    expect(error.animationDuration).toBe("0.18s");
    expect(normalizedTimingList(error.animationTimingFunction)).toEqual(["cubic-bezier(0.65,0,0.35,1)"]);
  });

  test("reduced motion keeps state changes direct and preserves semantics", async ({ page, bookCourseApi }) => {
    void bookCourseApi;
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/e2e/state-motion-harness.html");
    const state = page.locator("#state-motion-harness");
    const harness = async (method: string, ...args: unknown[]) => {
      await page.evaluate(({ method: methodName, args: methodArgs }) => {
        const api = window.__stateMotionHarness;
        if (!api) throw new Error("State motion harness is not ready.");
        (api[methodName as keyof typeof api] as (...values: unknown[]) => void)(...methodArgs);
      }, { method, args });
    };
    await harness("setText", "完成");
    await expect(state.locator(".motion-state-swap")).toHaveAttribute("data-motion-text-state", "idle");
    await harness("setLoadState", "ready", "empty");
    await expect(state.locator(".motion-skeleton-reveal")).toHaveAttribute("data-motion-ready-kind", "empty");
    await expect.poll(() => state.locator(".motion-sliding-filter-indicator").evaluate((element) => getComputedStyle(element).transitionDuration)).toBe("0s");
  });

  test("real assignment path blocks an empty answer before either backend call", async ({ page, bookCourseApi }) => {
    await openStageSixCourse(page, bookCourseApi);
    await page.locator('.study-section.is-expanded [data-tool="assignment"]').click();
    await expect(page.locator(".assignment-screen")).toBeVisible();
    await waitForNaturalScreenTransition(page, "Pre-lesson tool entry opens the exercise");
    await advanceAssignmentToShortAnswer(page);
    const beforeRequests = bookCourseApi.requests.length;
    await page.getByRole("button", { name: "提交作业", exact: true }).click();
    await expect(page.locator("#assignment-answer-error")).toHaveText("请先填写答案，再提交作业诊断。");
    await expect(page.locator(".assignment-card textarea")).toBeFocused();
    expect(bookCourseApi.requests.slice(beforeRequests).filter(({ path }) => path.includes("/assignments/") || path.includes("/diagnose")).length).toBe(0);
  });

  test("real mistake filters use knowledge_points and expose a filtered empty state", async ({ page, bookCourseApi }) => {
    await openStageSixCourse(page, bookCourseApi);
    await page.locator(".primary-nav .nav-item").first().click();
    await expect(page.locator(".home-dashboard")).toBeVisible();
    await waitForNaturalScreenTransition(page, "Mistake path returns Home");
    await page.locator('[data-home-global-action="mistakes"]').click();
    await expect(page.locator(".mistake-book-screen")).toBeVisible();
    await waitForNaturalScreenTransition(page, "Mistake path opens the mistake book");
    await expect(page.locator(".mistake-detail-card")).toBeVisible();

    await page.locator(".filter-pill").filter({ hasText: "遗传规律" }).click();
    await expect(page.locator(".filter-pill").filter({ hasText: "遗传规律" })).toHaveClass(/active/);
    await expect(page.locator(".mistake-list-item")).toHaveCount(0);
    await expect(page.locator(".mistake-workspace")).toHaveAttribute("data-mistake-list-empty", "true");
    await expect(page.locator(".mistake-state-card h3")).toHaveText("当前分类暂无错题");

    await page.getByRole("button", { name: "查看全部错题", exact: true }).click();
    await expect(page.locator(".mistake-list-item")).toHaveCount(1);
    await expect(page.locator(".mistake-detail-card")).toBeVisible();
  });
});
