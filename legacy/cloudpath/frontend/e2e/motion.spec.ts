import { expect, test as base, type Locator, type Page } from "playwright/test";

type RuntimeAudit = {
  consoleErrors: string[];
  forbiddenRequests: string[];
  pageErrors: string[];
};

const test = base.extend<{ audit: RuntimeAudit }>({
  audit: async ({ page, baseURL }, use) => {
    const audit: RuntimeAudit = { consoleErrors: [], forbiddenRequests: [], pageErrors: [] };
    const appOrigin = new URL(baseURL ?? "http://127.0.0.1:4173").origin;
    page.on("console", (message) => {
      if (message.type() === "error") audit.consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => audit.pageErrors.push(error.message));
    page.on("request", (request) => {
      const url = new URL(request.url());
      if ((url.protocol === "http:" || url.protocol === "https:") && (url.origin !== appOrigin || url.pathname.startsWith("/api/"))) {
        audit.forbiddenRequests.push(`${request.method()} ${request.url()}`);
      }
    });
    await use(audit);
    expect(audit.forbiddenRequests, "motion coverage must stay on the in-memory DemoRepository path").toEqual([]);
    expect(audit.consoleErrors, "motion coverage emits no unacknowledged console errors").toEqual([]);
    expect(audit.pageErrors, "motion coverage emits no page errors").toEqual([]);
  }
});

test.use({
  colorScheme: "light",
  locale: "zh-CN",
  reducedMotion: "no-preference",
  timezoneId: "Asia/Hong_Kong"
});

const curves = {
  globalEnter: "cubic-bezier(0.25,1,0.5,1)",
  globalExit: "cubic-bezier(0.5,0,0.75,0)",
  localEnter: "cubic-bezier(0.22,1,0.36,1)",
  localExit: "cubic-bezier(0.32,0,0.67,0)",
  localState: "cubic-bezier(0.65,0,0.35,1)",
  progress: "cubic-bezier(0.4,0,0.2,1)"
} as const;

function normalizeTimingFunction(value: string) {
  return value.replaceAll(/\s+/g, "");
}

async function readAnimation(locator: Locator) {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      delay: style.animationDelay,
      duration: style.animationDuration,
      fillMode: style.animationFillMode,
      name: style.animationName,
      opacity: style.opacity,
      playState: style.animationPlayState,
      timing: style.animationTimingFunction,
      visibility: style.visibility,
      willChange: style.willChange
    };
  });
}

async function readTransition(locator: Locator) {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      duration: style.transitionDuration,
      property: style.transitionProperty,
      timing: style.transitionTimingFunction
    };
  });
}

async function dispatchAnimation(locator: Locator, type: "animationend" | "animationcancel" = "animationend", name?: string) {
  await locator.evaluate((element, options) => {
    const animationName = options.name ?? getComputedStyle(element).animationName.split(",")[0].trim();
    element.dispatchEvent(new AnimationEvent(options.type, { animationName, bubbles: true }));
  }, { name, type });
}

async function settleScreen(page: Page) {
  const root = page.locator(".motion-screen-transition");
  if (await root.getAttribute("data-motion-state") === "transitioning") {
    await dispatchAnimation(root.locator(':scope > .motion-screen-surface[data-motion-surface="current"]'));
  }
  await expect(root).toHaveAttribute("data-motion-state", "idle");
  await expect(root.locator(':scope > [data-motion-surface="previous"]')).toHaveCount(0);
}

async function gotoApp(page: Page) {
  await page.goto("/?embedded=device-preview");
  await expect(page.locator(".home-dashboard")).toBeVisible();
  await settleScreen(page);
}

async function openLibrary(page: Page) {
  if (await page.locator(".home-dashboard").isVisible().catch(() => false)) {
    await page.locator(".home-book-picker-heading button").click();
  } else {
    await page.locator(".primary-nav .nav-item").nth(1).click();
  }
  await expect(page.locator(".library-screen")).toBeVisible();
  await settleScreen(page);
}

async function advanceAssignmentToShortAnswer(page: Page) {
  await page.locator(".assignment-judgment-options button").first().click();
  await page.locator(".assignment-primary-action .button").click();
  await expect(page.locator('.assignment-exercise-card[data-assignment-type="choice"]')).toBeVisible();
  await page.locator(".assignment-choice-options button").nth(1).click();
  await page.locator(".assignment-primary-action .button").click();
  await expect(page.locator('.assignment-exercise-card[data-assignment-type="short-answer"]')).toBeVisible();
}

async function openStudy(page: Page) {
  await gotoApp(page);
  await openLibrary(page);
  const course = page.locator(".library-course-grid .course-space-card").first();
  await expect(course).toBeVisible();
  await course.locator(".button-row .button").click();
  await expect(page.locator(".book-course-screen")).toBeVisible({ timeout: 15_000 });
  await settleScreen(page);
}

async function openSourceFromStudy(page: Page) {
  const entry = page.locator("#study-section-c2s1-content .study-enter-button");
  await expect(entry, "SourceReader entry is reachable from the expanded real Study section").toBeVisible();
  await entry.focus();
  await expect(entry, "SourceReader entry owns keyboard focus before activation").toBeFocused();
  await entry.press("Enter");
  await expect(page.locator(".lesson-screen")).toBeVisible({ timeout: 10_000 });
  await settleScreen(page);
  await page.locator(".lesson-source-link").first().click();
  await expect(page.locator(".source-reader-screen")).toBeVisible({ timeout: 10_000 });
  await settleScreen(page);
}

async function returnFromSourceToStudy(page: Page) {
  await page.locator(".header-bar .icon-button").click();
  await expect(page.locator(".lesson-screen")).toBeVisible();
  await settleScreen(page);
  await page.locator(".header-bar .icon-button").click();
  await expect(page.locator(".book-course-screen")).toBeVisible();
  await settleScreen(page);
}

async function openFlashcards(page: Page) {
  await openStudy(page);
  const chapterToggle = page.locator("#study-chapter-c2-toggle");
  if (await chapterToggle.getAttribute("aria-expanded") !== "true") await chapterToggle.click();
  const sectionToggle = page.locator("#study-section-c2s1-toggle");
  if (await sectionToggle.getAttribute("aria-expanded") !== "true") await sectionToggle.click();
  await page.locator('#study-section-c2s1-content .study-tool-card[data-tool="flashcards"]').click();
  await expect(page.locator(".flashcard-screen")).toBeVisible();
  await settleScreen(page);
  await expect(page.locator(".memory-card-answer-motion")).toBeVisible();
}

async function openLesson(page: Page) {
  await openStudy(page);
  const chapterToggle = page.locator("#study-chapter-c2-toggle");
  if (await chapterToggle.getAttribute("aria-expanded") !== "true") await chapterToggle.click();
  const sectionToggle = page.locator("#study-section-c2s1-toggle");
  if (await sectionToggle.getAttribute("aria-expanded") !== "true") await sectionToggle.click();
  const entry = page.locator("#study-section-c2s1-content .study-enter-button");
  await entry.focus();
  await expect(entry).toBeFocused();
  await entry.press("Enter");
  await expect(page.locator(".lesson-screen")).toBeVisible();
  await settleScreen(page);
}

async function openChapterConfirm(page: Page) {
  await gotoApp(page);
  await page.locator(".home-import-course-action").click();
  await expect(page.locator(".upload-flow-screen")).toBeVisible();
  await settleScreen(page);
  await page.locator('input[type="file"]').setInputFiles({
    name: "sheet-editor-motion.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 editor sheet motion")
  });
  await page.locator(".upload-flow-primary > .button").click();
  await expect(page.locator(".parse-ready-screen")).toBeVisible();
  await settleScreen(page);
  await page.locator(".parse-flow-actions .button").first().click();
  await expect(page.locator(".chapter-confirm-screen")).toBeVisible({ timeout: 12_000 });
  await settleScreen(page);
}

async function openNotes(page: Page) {
  await openStudy(page);
  await page.locator(".primary-nav .nav-item").nth(3).click();
  await expect(page.locator(".profile-screen")).toBeVisible();
  await settleScreen(page);
  await page.locator(".settings-row").nth(2).click();
  await expect(page.locator(".notes-screen")).toBeVisible();
  await settleScreen(page);
  await expect(page.locator(".notes-detail-panel")).toBeVisible();
}

async function openMistakes(page: Page) {
  await openStudy(page);
  await page.locator('.study-tool-card[data-tool="assignment"]').first().click();
  await expect(page.locator(".assignment-screen")).toBeVisible();
  await settleScreen(page);
  await advanceAssignmentToShortAnswer(page);
  await page.locator(".assignment-card textarea").fill("The homologous chromosomes separate in meiosis I.");
  await page.locator(".assignment-primary-action .button").click();
  await expect(page.locator(".diagnosis-screen")).toBeVisible();
  await settleScreen(page);
  const mistakesAction = page.locator(".diagnosis-actions .button").last();
  await expect(mistakesAction).toBeEnabled();
  await mistakesAction.focus();
  await expect(mistakesAction).toBeFocused();
  await mistakesAction.press("Enter");
  await expect(page.locator(".mistake-book-screen")).toBeVisible();
  await settleScreen(page);
  await expect(page.locator(".mistake-detail-card")).toBeVisible();
}

async function installPauseStyle(page: Page, selector: string) {
  return page.addStyleTag({ content: `${selector} { animation-play-state: paused !important; }` });
}

async function installInitialPauseStyle(page: Page, selector: string) {
  await page.addInitScript((rule) => {
    const install = () => {
      if (!document.documentElement || document.querySelector("style[data-motion-e2e-pause]")) return false;
      const style = document.createElement("style");
      style.dataset.motionE2ePause = "true";
      style.textContent = `${rule} { animation-play-state: paused !important; }`;
      document.documentElement.append(style);
      return true;
    };
    if (!install()) {
      const observer = new MutationObserver(() => {
        if (install()) observer.disconnect();
      });
      observer.observe(document, { childList: true });
    }
  }, selector);
}

async function expectGlobalPresenceMotion(locator: Locator, state: "closing" | "entering", label: string) {
  await expect(locator, `${label}: phase`).toHaveAttribute("data-motion-state", state);
  const motion = await readAnimation(locator);
  expect(motion.duration, `${label}: global duration`).toBe("0.35s");
  expect(motion.fillMode, `${label}: fill mode`).toBe("both");
  expect(motion.playState, `${label}: test pause owns the phase`).toBe("paused");
  expect(motion.name.endsWith(state === "entering" ? "-in" : "-out"), `${label}: direction-specific keyframe`).toBe(true);
  expect(normalizeTimingFunction(motion.timing), `${label}: named global curve`).toBe(
    state === "entering" ? curves.globalEnter : curves.globalExit
  );
  return motion.name.split(",")[0].trim();
}

async function expectFocusWithin(page: Page, locator: Locator, label: string) {
  await expect.poll(() => locator.evaluate((element) => element.contains(document.activeElement)), {
    message: `${label}: focus is owned by the dialog`,
    timeout: 2_000
  }).toBe(true);
}

async function expectLocalEntry(locator: Locator, label: string) {
  await expect(locator, `${label}: local state enters`).toHaveAttribute("data-motion-item-state", "entering");
  const motion = await readAnimation(locator);
  expect(motion.duration, `${label}: local base duration`).toBe("0.18s");
  expect(motion.playState, `${label}: pause probe owns the phase`).toBe("paused");
  expect(normalizeTimingFunction(motion.timing), `${label}: named local enter curve`).toBe(curves.localEnter);
  return motion.name.split(",")[0].trim();
}

test.describe("1. timing tokens, physical feedback, and Presence lifecycle", () => {
  test("locks global 350ms, local 150/180/200ms, named curves, and synchronous reduced motion", async ({ page }) => {
    await gotoApp(page);
    const tokens = await page.locator(".app-shell").evaluate((root) => {
      const style = getComputedStyle(root);
      const names = [
        "--motion-duration-global",
        "--motion-duration-local-fast",
        "--motion-duration-local-base",
        "--motion-duration-local-slow",
        "--motion-duration-loading",
        "--motion-ease-global-enter",
        "--motion-ease-global-exit",
        "--motion-ease-local-enter",
        "--motion-ease-local-exit",
        "--motion-ease-local-state",
        "--motion-ease-progress"
      ];
      return Object.fromEntries(names.map((name) => [name, style.getPropertyValue(name).trim()]));
    });
    expect(tokens).toEqual({
      "--motion-duration-global": "350ms",
      "--motion-duration-local-fast": "150ms",
      "--motion-duration-local-base": "180ms",
      "--motion-duration-local-slow": "200ms",
      "--motion-duration-loading": "1200ms",
      "--motion-ease-global-enter": "cubic-bezier(.25, 1, .5, 1)",
      "--motion-ease-global-exit": "cubic-bezier(.5, 0, .75, 0)",
      "--motion-ease-local-enter": "cubic-bezier(.22, 1, .36, 1)",
      "--motion-ease-local-exit": "cubic-bezier(.32, 0, .67, 0)",
      "--motion-ease-local-state": "cubic-bezier(.65, 0, .35, 1)",
      "--motion-ease-progress": "cubic-bezier(.4, 0, .2, 1)"
    });

    await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.id = "motion-control-probe";
      probe.style.cssText = "position:fixed;top:8px;left:8px;z-index:2147483647;display:flex;gap:8px;pointer-events:auto";
      probe.innerHTML = '<button class="button" type="button">button</button><button class="icon-button" type="button">icon</button><svg class="spin" viewBox="0 0 10 10"></svg>';
      document.body.append(probe);
    });
    const button = page.locator("#motion-control-probe .button");
    const icon = page.locator("#motion-control-probe .icon-button");
    for (const [control, scale] of [[button, 0.98], [icon, 0.96]] as const) {
      const before = await control.evaluate((element) => {
        const html = element as HTMLElement;
        return {
          clientHeight: html.clientHeight,
          clientWidth: html.clientWidth,
          offsetHeight: html.offsetHeight,
          offsetLeft: html.offsetLeft,
          offsetTop: html.offsetTop,
          offsetWidth: html.offsetWidth
        };
      });
      const box = await control.boundingBox();
      if (!box) throw new Error("motion control probe has no box");
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await expect.poll(() => control.evaluate((element) => getComputedStyle(element).transform)).toBe(`matrix(${scale}, 0, 0, ${scale}, 0, 0)`);
      const activeTransition = await readTransition(control);
      expect(activeTransition).toMatchObject({ duration: "0.15s", property: "transform" });
      expect(normalizeTimingFunction(activeTransition.timing)).toBe(curves.localState);
      expect(await control.evaluate((element) => {
        const html = element as HTMLElement;
        return {
          clientHeight: html.clientHeight,
          clientWidth: html.clientWidth,
          offsetHeight: html.offsetHeight,
          offsetLeft: html.offsetLeft,
          offsetTop: html.offsetTop,
          offsetWidth: html.offsetWidth
        };
      })).toEqual(before);
      await page.mouse.up();
    }
    expect(await readAnimation(page.locator("#motion-control-probe .spin"))).toMatchObject({ duration: "1.2s", name: "motion-spinner", timing: "linear" });

    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(page.locator(".app-shell")).toHaveAttribute("data-motion-reduced", "true");
    expect(await readTransition(button)).toMatchObject({ duration: "0s", property: "none" });
    expect(await readAnimation(page.locator("#motion-control-probe .spin"))).toMatchObject({ duration: "0s", name: "none" });
  });

  test("keeps local 299/300ms and global 449/450ms Presence fallbacks generation-bound with complete timer cleanup", async ({ page }) => {
    await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
    await page.clock.pauseAt(new Date("2026-01-01T00:00:01Z"));
    await page.goto("/e2e/motion-presence-harness.html");
    const local = page.locator("#motion-presence-state-local");
    const global = page.locator("#motion-presence-state-global");
    await expect(local).toHaveAttribute("data-state", "entering");
    await expect(global).toHaveAttribute("data-state", "entering");
    const initialLocalId = Number(await local.getAttribute("data-presence-id"));
    const initialGlobalId = Number(await global.getAttribute("data-presence-id"));
    await page.evaluate(() => {
      window.__motionPresenceHarness?.updateSameKey("local", 1);
      window.__motionPresenceHarness?.updateSameKey("global", 1);
    });
    await expect(local).toHaveAttribute("data-rendered-value", "1");
    await expect(global).toHaveAttribute("data-rendered-value", "1");
    await expect(local).toHaveAttribute("data-presence-id", String(initialLocalId));
    await expect(global).toHaveAttribute("data-presence-id", String(initialGlobalId));
    expect(await page.evaluate(() => ({
      active300: window.__motionTimerAudit?.active300,
      active450: window.__motionTimerAudit?.active450,
      scheduled300: window.__motionTimerAudit?.scheduled300,
      scheduled450: window.__motionTimerAudit?.scheduled450
    }))).toEqual({ active300: 1, active450: 1, scheduled300: 1, scheduled450: 1 });

    await page.clock.runFor(299);
    await expect(local, "local Presence remains active at 299ms").toHaveAttribute("data-state", "entering");
    await expect(global).toHaveAttribute("data-state", "entering");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active300)).toBe(1);
    await page.clock.runFor(1);
    await expect(local, "local Presence settles at exactly 300ms").toHaveAttribute("data-state", "idle");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active300)).toBe(0);

    await page.clock.runFor(149);
    await expect(global, "global Presence remains active at 449ms").toHaveAttribute("data-state", "entering");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active450)).toBe(1);
    await page.clock.runFor(1);
    await expect(global, "global Presence settles at exactly 450ms").toHaveAttribute("data-state", "idle");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active450)).toBe(0);

    await page.evaluate(() => window.__motionPresenceHarness?.clear("local"));
    await expect(local).toHaveAttribute("data-state", "closing");
    const closingLocalId = await local.getAttribute("data-presence-id");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active300)).toBe(1);
    await dispatchAnimation(local, "animationend", "wrong-animation");
    await expect(local).toHaveAttribute("data-state", "closing");
    await dispatchAnimation(local, "animationcancel", "motion-presence-harness");
    await expect(local).toHaveAttribute("data-rendered-key", "none");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active300)).toBe(0);

    await page.evaluate(() => window.__motionPresenceHarness?.replace("local", "beta", 2));
    await expect(local).toHaveAttribute("data-rendered-key", "beta");
    await expect(local).not.toHaveAttribute("data-presence-id", closingLocalId ?? "");
    await expect(local).toHaveAttribute("data-state", "entering");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active300)).toBe(1);
    await dispatchAnimation(local, "animationend", "motion-presence-harness");
    await expect(local).toHaveAttribute("data-state", "idle");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active300)).toBe(0);

    await page.evaluate(() => window.__motionPresenceHarness?.clear("global"));
    await expect(global).toHaveAttribute("data-state", "closing");
    const staleGlobalId = await global.getAttribute("data-presence-id");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active450)).toBe(1);
    await page.evaluate(() => window.__motionPresenceHarness?.replace("global", "beta", 2));
    await expect(global).toHaveAttribute("data-state", "entering");
    await expect(global).not.toHaveAttribute("data-presence-id", staleGlobalId ?? "");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active450)).toBe(1);
    await dispatchAnimation(global, "animationcancel", "motion-presence-harness");
    await expect(global).toHaveAttribute("data-state", "idle");
    expect(await page.evaluate(() => window.__motionTimerAudit?.active450)).toBe(0);

    await page.evaluate(() => {
      window.__motionPresenceHarness?.replace("local", "gamma", 3);
      window.__motionPresenceHarness?.replace("global", "gamma", 3);
    });
    await expect(local).toHaveAttribute("data-state", "entering");
    await expect(global).toHaveAttribute("data-state", "entering");
    expect(await page.evaluate(() => ({
      active300: window.__motionTimerAudit?.active300,
      active450: window.__motionTimerAudit?.active450
    }))).toEqual({ active300: 1, active450: 1 });
    await page.clock.runFor(50);
    const firedBeforeRuntimeReduce = await page.evaluate(() => ({
      fired300: window.__motionTimerAudit?.fired300,
      fired450: window.__motionTimerAudit?.fired450
    }));
    await page.evaluate(() => {
      window.__motionPresenceHarness?.setReducedMotion("local", true);
      window.__motionPresenceHarness?.setReducedMotion("global", true);
    });
    await expect(local).toHaveAttribute("data-reduced-motion", "true");
    await expect(global).toHaveAttribute("data-reduced-motion", "true");
    await expect(local).toHaveAttribute("data-rendered-key", "gamma");
    await expect(global).toHaveAttribute("data-rendered-key", "gamma");
    await expect(local, "runtime reduce settles the active local generation synchronously").toHaveAttribute("data-state", "idle");
    await expect(global, "runtime reduce settles the active global generation synchronously").toHaveAttribute("data-state", "idle");
    expect(await page.evaluate(() => ({
      active300: window.__motionTimerAudit?.active300,
      active450: window.__motionTimerAudit?.active450
    }))).toEqual({ active300: 0, active450: 0 });
    await page.clock.runFor(500);
    await expect(local, "the canceled local fallback cannot mutate final reduced state").toHaveAttribute("data-state", "idle");
    await expect(global, "the canceled global fallback cannot mutate final reduced state").toHaveAttribute("data-state", "idle");
    expect(await page.evaluate(() => ({
      fired300: window.__motionTimerAudit?.fired300,
      fired450: window.__motionTimerAudit?.fired450
    }))).toEqual(firedBeforeRuntimeReduce);

    await page.evaluate(() => {
      window.__motionPresenceHarness?.setReducedMotion("local", false);
      window.__motionPresenceHarness?.setReducedMotion("global", false);
    });
    await expect(local).toHaveAttribute("data-reduced-motion", "false");
    await expect(global).toHaveAttribute("data-reduced-motion", "false");
    await expect(local, "restoring normal motion does not replay the consumed local key").toHaveAttribute("data-state", "idle");
    await expect(global, "restoring normal motion does not replay the consumed global key").toHaveAttribute("data-state", "idle");

    await page.evaluate(() => {
      window.__motionPresenceHarness?.replace("local", "delta", 4);
      window.__motionPresenceHarness?.replace("global", "delta", 4);
    });
    await expect(local).toHaveAttribute("data-state", "entering");
    await expect(global).toHaveAttribute("data-state", "entering");
    const oldLocalReplacementId = await local.getAttribute("data-presence-id");
    const oldGlobalReplacementId = await global.getAttribute("data-presence-id");
    expect(await page.evaluate(() => ({
      active300: window.__motionTimerAudit?.active300,
      active450: window.__motionTimerAudit?.active450
    }))).toEqual({ active300: 1, active450: 1 });
    await page.clock.runFor(100);
    await page.evaluate(() => {
      window.__motionPresenceHarness?.replace("local", "epsilon", 5);
      window.__motionPresenceHarness?.replace("global", "epsilon", 5);
    });
    await expect(local).not.toHaveAttribute("data-presence-id", oldLocalReplacementId ?? "");
    await expect(global).not.toHaveAttribute("data-presence-id", oldGlobalReplacementId ?? "");
    await expect(local).toHaveAttribute("data-rendered-key", "epsilon");
    await expect(global).toHaveAttribute("data-rendered-key", "epsilon");
    expect(await page.evaluate(() => ({
      active300: window.__motionTimerAudit?.active300,
      active450: window.__motionTimerAudit?.active450
    }))).toEqual({ active300: 1, active450: 1 });
    await page.clock.runFor(200);
    await expect(local, "the old 300ms deadline cannot settle the newer local generation").toHaveAttribute("data-state", "entering");
    await expect(global).toHaveAttribute("data-state", "entering");
    await page.clock.runFor(100);
    await expect(local, "the new local generation owns its replacement-relative deadline").toHaveAttribute("data-state", "idle");
    await expect(global).toHaveAttribute("data-state", "entering");
    await page.clock.runFor(50);
    await expect(global, "the old 450ms deadline cannot settle the newer global generation").toHaveAttribute("data-state", "entering");
    await page.clock.runFor(100);
    await expect(global, "the new global generation owns its replacement-relative deadline").toHaveAttribute("data-state", "idle");
    expect(await page.evaluate(() => ({
      active300: window.__motionTimerAudit?.active300,
      active450: window.__motionTimerAudit?.active450
    }))).toEqual({ active300: 0, active450: 0 });

    await page.evaluate(() => {
      window.__motionPresenceHarness?.replace("local", "zeta", 6);
      window.__motionPresenceHarness?.replace("global", "zeta", 6);
    });
    await expect(local).toHaveAttribute("data-state", "entering");
    await expect(global).toHaveAttribute("data-state", "entering");
    expect(await page.evaluate(() => ({
      active300: window.__motionTimerAudit?.active300,
      active450: window.__motionTimerAudit?.active450
    }))).toEqual({ active300: 1, active450: 1 });
    const firedBeforeActiveUnmount = await page.evaluate(() => ({
      fired300: window.__motionTimerAudit?.fired300,
      fired450: window.__motionTimerAudit?.fired450
    }));
    await page.evaluate(() => window.__motionPresenceHarness?.unmount());
    await expect(local).toHaveCount(0);
    await expect(global).toHaveCount(0);
    expect(await page.evaluate(() => ({
      active300: window.__motionTimerAudit?.active300,
      active450: window.__motionTimerAudit?.active450
    }))).toEqual({ active300: 0, active450: 0 });
    await page.clock.runFor(500);
    expect(await page.evaluate(() => ({
      fired300: window.__motionTimerAudit?.fired300,
      fired450: window.__motionTimerAudit?.fired450
    }))).toEqual(firedBeforeActiveUnmount);
  });
});

test.describe("2. global navigation, sheets, AI, and Toast", () => {
  test("keeps exactly current+previous screen surfaces on a 350ms curved lifecycle and rejects stale completion", async ({ page }) => {
    await gotoApp(page);
    await installPauseStyle(page, ".motion-screen-surface");
    const root = page.locator(".motion-screen-transition");
    await page.locator(".home-book-picker-heading button").click();
    await expect(root).toHaveAttribute("data-motion-state", "transitioning");
    const current = root.locator(':scope > [data-motion-surface="current"]');
    const previous = root.locator(':scope > [data-motion-surface="previous"]');
    await expect(current).toHaveCount(1);
    await expect(previous).toHaveCount(1);
    const [entering, exiting] = await Promise.all([
      readAnimation(current),
      readAnimation(previous)
    ]);
    expect(entering).toMatchObject({ duration: "0.35s", fillMode: "both", playState: "paused" });
    expect(exiting).toMatchObject({ duration: "0.35s", fillMode: "both", playState: "paused" });
    expect(normalizeTimingFunction(entering.timing)).toBe(curves.globalEnter);
    expect(normalizeTimingFunction(exiting.timing)).toBe(curves.globalExit);
    await expect(previous).toHaveAttribute("aria-hidden", "true");
    await expect(previous).toHaveAttribute("inert", "");
    expect(exiting.visibility, "the retained outgoing tree never paints behind translucent destination content").toBe("hidden");
    expect(entering.opacity, "the destination owns every transition frame without cross-fading").toBe("1");
    await dispatchAnimation(previous, "animationend");
    await expect(root).toHaveAttribute("data-motion-state", "transitioning");
    await dispatchAnimation(current, "animationend", "motion-toast-in");
    await expect(root).toHaveAttribute("data-motion-state", "transitioning");
    await dispatchAnimation(current);
    await expect(root).toHaveAttribute("data-motion-state", "idle");
    await expect(previous).toHaveCount(0);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".primary-nav .nav-item").first().click();
    await expect(page.locator(".home-dashboard")).toBeVisible();
    await expect(root).toHaveAttribute("data-motion-state", "idle");
    await expect(root.locator(':scope > [data-motion-surface="previous"]')).toHaveCount(0);
  });

  test("settles real A→B→C navigation only from C and ignores stale completion/cancellation across generations", async ({ page }) => {
    await gotoApp(page);
    await installPauseStyle(page, ".motion-screen-surface");
    const root = page.locator(".motion-screen-transition");
    const homeSurface = root.locator(':scope > [data-motion-surface="current"]');
    const staleHome = await homeSurface.elementHandle();
    if (!staleHome) throw new Error("rapid navigation needs the initial Home surface");

    await page.locator(".home-book-picker-heading button").click();
    await expect(root).toHaveAttribute("data-screen", "library");
    await expect(root).toHaveAttribute("data-motion-state", "transitioning");
    const libraryCurrent = root.locator(':scope > [data-motion-surface="current"]');
    const libraryEntryName = (await readAnimation(libraryCurrent)).name.split(",")[0].trim();
    const staleLibrary = await libraryCurrent.elementHandle();
    if (!staleLibrary) throw new Error("rapid navigation needs the intermediate Library surface");

    await page.locator(".primary-nav .nav-item").nth(3).click();
    await expect(root).toHaveAttribute("data-screen", "profile");
    await expect(root).toHaveAttribute("data-motion-state", "transitioning");
    const profileCurrent = root.locator(':scope > [data-motion-surface="current"]');
    const retainedPrevious = root.locator(':scope > [data-motion-surface="previous"]');
    await expect(profileCurrent).toHaveAttribute("data-screen", "profile");
    await expect(retainedPrevious).toHaveAttribute("data-screen", "library");
    await expect(root.locator(":scope > .motion-screen-surface")).toHaveCount(2);
    expect(await staleHome.evaluate((element) => element.isConnected), "A is detached when C replaces the incomplete B transition").toBe(false);
    expect(await staleLibrary.evaluate((element) => element.isConnected), "B is retained only as C's inert previous surface").toBe(true);

    for (const [handle, type, animationName] of [
      [staleHome, "animationend", libraryEntryName],
      [staleHome, "animationcancel", libraryEntryName],
      [staleLibrary, "animationend", libraryEntryName],
      [staleLibrary, "animationcancel", libraryEntryName]
    ] as const) {
      await handle.evaluate((element, event) => {
        element.dispatchEvent(new AnimationEvent(event.type, { animationName: event.animationName, bubbles: true }));
      }, { animationName, type });
      await expect(root, `${type} from stale ${handle === staleHome ? "A" : "B"} cannot settle C`).toHaveAttribute("data-motion-state", "transitioning");
      await expect(profileCurrent).toHaveAttribute("data-screen", "profile");
      await expect(retainedPrevious).toHaveAttribute("data-screen", "library");
    }

    const profileEntryName = (await readAnimation(profileCurrent)).name.split(",")[0].trim();
    await dispatchAnimation(profileCurrent, "animationcancel", profileEntryName);
    await expect(root).toHaveAttribute("data-motion-state", "idle");
    await expect(root.locator(":scope > .motion-screen-surface")).toHaveCount(1);
    await expect(profileCurrent).toHaveAttribute("data-screen", "profile");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".primary-nav .nav-item").first().click();
    await page.locator(".primary-nav .nav-item").nth(1).click();
    await page.locator(".primary-nav .nav-item").nth(3).click();
    await expect(root, "reduced-motion rapid A→B→C exposes C synchronously").toHaveAttribute("data-screen", "profile");
    await expect(root).toHaveAttribute("data-motion-state", "idle");
    await expect(root.locator(":scope > .motion-screen-surface")).toHaveCount(1);
    await expect(root.locator(':scope > [data-motion-surface="previous"]')).toHaveCount(0);
    expect((await readAnimation(root.locator(':scope > [data-motion-surface="current"]'))).name).toBe("none");
  });

  test("uses 150ms Local State navigation curves and keeps selection aligned with the current screen", async ({ page }) => {
    await gotoApp(page);
    const navItem = page.locator(".primary-nav .nav-item").nth(3);
    const navIcon = navItem.locator(".nav-icon");
    const itemTransition = await readTransition(navItem);
    const iconTransition = await readTransition(navIcon);
    expect(itemTransition).toMatchObject({ duration: "0.15s", property: "color, background-color" });
    expect(iconTransition).toMatchObject({ duration: "0.15s", property: "transform" });
    expect(normalizeTimingFunction(itemTransition.timing)).toBe(curves.localState);
    expect(normalizeTimingFunction(iconTransition.timing)).toBe(curves.localState);
    await navItem.click();
    await expect(page.locator(".profile-screen")).toBeVisible();
    await settleScreen(page);
    await expect(navItem).toHaveAttribute("data-motion-active", "true");
    await expect(page.locator(".primary-nav [data-motion-active='true']")).toHaveCount(1);
  });

  test("maps ActionSheet and AI panel+scrim to the same 350ms enter/exit curves", async ({ page }) => {
    await openStudy(page);
    await installPauseStyle(page, ".sheet, .sheet-scrim");
    await page.locator(".study-book-switch").click();
    const sheet = page.locator(".sheet");
    const sheetScrim = page.locator(".sheet-scrim");
    await expect(sheet).toHaveAttribute("data-motion-state", "entering");
    const sheetMotion = await readAnimation(sheet);
    expect(sheetMotion, "sheet panel entry").toMatchObject({ duration: "0.35s", fillMode: "both", playState: "paused" });
    expect(normalizeTimingFunction(sheetMotion.timing)).toBe(curves.globalEnter);
    const sheetScrimMotion = await readAnimation(sheetScrim);
    expect(sheetScrimMotion, "sheet scrim entry").toMatchObject({ duration: "0.35s", fillMode: "both", playState: "paused" });
    expect(normalizeTimingFunction(sheetScrimMotion.timing)).toBe(curves.globalEnter);
    await dispatchAnimation(sheet);
    await expect(sheet).toHaveAttribute("data-motion-state", "idle");
    await sheet.locator(".sheet-close").click();
    await expect(sheet).toHaveAttribute("data-motion-state", "closing");
    expect(normalizeTimingFunction((await readAnimation(sheet)).timing)).toBe(curves.globalExit);
    await dispatchAnimation(sheet);
    await expect(sheet).toHaveCount(0);

    await page.locator(".primary-nav .nav-item").first().click();
    await settleScreen(page);
    await installPauseStyle(page, ".ai-overlay, .ai-overlay-scrim, .ai-shared-surface, .ai-shared-origin-icon");
    await page.locator(".ai-orb").click();
    const dialog = page.locator(".ai-overlay");
    const aiScrim = page.locator(".ai-overlay-scrim");
    await expect(dialog).toHaveAttribute("data-motion-state", "entering");
    for (const surface of [dialog, aiScrim]) {
      const motion = await readAnimation(surface);
      expect(motion).toMatchObject({ duration: "0.35s", fillMode: "both", playState: "paused" });
      expect(normalizeTimingFunction(motion.timing)).toBe(curves.globalEnter);
    }
    await dispatchAnimation(dialog);
    await expect(dialog).toHaveAttribute("data-motion-state", "idle");
    await dialog.locator(".ai-close").click();
    await expect(dialog).toHaveAttribute("data-motion-state", "closing");
    expect(normalizeTimingFunction((await readAnimation(dialog)).timing)).toBe(curves.globalExit);
    await dispatchAnimation(dialog);
    await expect(dialog).toHaveCount(0);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".ai-orb").click();
    await expect(page.locator(".ai-overlay")).toHaveAttribute("data-motion-state", "idle");
    expect((await readAnimation(page.locator(".ai-overlay"))).name).toBe("none");
  });

  test("covers bookSwitcher and concept detail sheets through focus, rapid reopen, and cleanup", async ({ page }) => {
    test.setTimeout(70_000);

    await openStudy(page);
    let pause = await installPauseStyle(page, ".sheet, .sheet-scrim");
    const bookTrigger = page.locator(".study-book-switch");
    await bookTrigger.focus();
    await bookTrigger.click();
    let sheet = page.locator(".sheet[data-sheet-type='bookSwitcher']");
    const scrim = page.locator(".sheet-scrim");
    const bookEntryName = await expectGlobalPresenceMotion(sheet, "entering", "bookSwitcher panel entry");
    await expectGlobalPresenceMotion(scrim, "entering", "bookSwitcher scrim entry");
    await expectFocusWithin(page, sheet, "bookSwitcher entry");
    await dispatchAnimation(sheet, "animationend", bookEntryName);
    await expect(sheet).toHaveAttribute("data-motion-state", "idle");
    await sheet.locator(".sheet-close").click();
    const bookExitName = await expectGlobalPresenceMotion(sheet, "closing", "bookSwitcher panel exit");
    await expectGlobalPresenceMotion(scrim, "closing", "bookSwitcher scrim exit");
    await dispatchAnimation(sheet, "animationend", bookExitName);
    await expect(sheet).toHaveCount(0);
    await expect(bookTrigger, "bookSwitcher cleanup restores its real trigger").toBeFocused();
    await pause.evaluate((element) => element.remove());

    await openLesson(page);
    await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
    await page.clock.pauseAt(new Date("2026-01-01T00:00:01Z"));
    pause = await installPauseStyle(page, ".sheet, .sheet-scrim");
    const noteTrigger = page.locator(".concept-card-grid button").first();
    await noteTrigger.focus();
    await noteTrigger.click();
    sheet = page.locator(".sheet[data-sheet-type='note']");
    const noteEntryName = await expectGlobalPresenceMotion(sheet, "entering", "Note panel entry");
    await page.clock.runFor(20);
    await expectFocusWithin(page, sheet, "Note entry");
    await dispatchAnimation(sheet, "animationend", noteEntryName);
    await expect(sheet).toHaveAttribute("data-motion-state", "idle");
    await sheet.focus();
    await page.keyboard.press("Escape");
    const noteExitName = await expectGlobalPresenceMotion(sheet, "closing", "Note panel exit");
    const closingPresence = Number(await sheet.getAttribute("data-motion-presence"));
    const staleNote = await sheet.elementHandle();
    if (!staleNote) throw new Error("rapid Note reopen needs a stale root");
    await noteTrigger.dispatchEvent("click");
    await expect(sheet).toHaveAttribute("data-motion-state", "entering");
    const reopenedPresence = Number(await sheet.getAttribute("data-motion-presence"));
    expect(reopenedPresence, "rapid Note reopen increments Presence generation").toBeGreaterThan(closingPresence);
    expect(await staleNote.evaluate((element) => element.isConnected), "rapid Note reopen detaches the closing root").toBe(false);
    await staleNote.evaluate((element, name) => {
      element.dispatchEvent(new AnimationEvent("animationcancel", { animationName: name, bubbles: true }));
    }, noteExitName);
    await expect(sheet, "stale Note cancel cannot settle reopened generation").toHaveAttribute("data-motion-state", "entering");
    await dispatchAnimation(sheet, "animationcancel", noteExitName);
    await expect(sheet, "wrong exit phase cannot settle Note entry").toHaveAttribute("data-motion-state", "entering");
    const reopenedEntryName = (await readAnimation(sheet)).name.split(",")[0].trim();
    await dispatchAnimation(sheet, "animationcancel", reopenedEntryName);
    await expect(sheet).toHaveAttribute("data-motion-state", "idle");
    await sheet.focus();
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveAttribute("data-motion-state", "closing");
    await page.clock.runFor(449);
    await expect(sheet, "real Note exit remains mounted at global fallback 449ms").toHaveAttribute("data-motion-state", "closing");
    await page.clock.runFor(1);
    await expect(sheet, "real Note exit cleans up at global fallback 450ms").toHaveCount(0);
    await expect(noteTrigger).toBeFocused();

    await page.emulateMedia({ reducedMotion: "reduce" });
    await noteTrigger.click();
    sheet = page.locator(".sheet[data-sheet-type='note']");
    await expect(sheet).toHaveAttribute("data-motion-state", "idle");
    expect((await readAnimation(sheet)).name).toBe("none");
    await sheet.focus();
    await page.keyboard.press("Escape");
    await expect(sheet, "reduced Note exit unmounts synchronously").toHaveCount(0);
    await expect(noteTrigger).toBeFocused();
    await pause.evaluate((element) => element.remove());
  });

  test("runs the real editChapter sheet through enter, focus, immutable exit, generation cleanup, and trigger restore", async ({ page }) => {
    test.setTimeout(55_000);
    await openChapterConfirm(page);
    const deviceLayout = await page.locator(".app-shell").getAttribute("data-device-layout");
    if (deviceLayout === "pad") {
      const pause = await installPauseStyle(page, ".chapter-save-feedback, .toc-directory-feedback");
      const detail = page.locator(".chapter-detail-panel");
      await expect(detail, "iPad keeps chapter editing in its real master-detail surface").toBeVisible();
      await expect(page.locator(".sheet[data-sheet-type='editChapter']"), "iPad does not invent the phone editChapter Sheet").toHaveCount(0);
      const titleInput = detail.locator("input").first();
      const originalTitle = await titleInput.inputValue();
      await titleInput.fill(`${originalTitle} motion`);
      await detail.locator("button[type='submit']").click();
      const saveFeedback = detail.locator(".chapter-save-feedback");
      const directoryFeedback = page.locator(".toc-directory-feedback");
      await expect(saveFeedback).toHaveAttribute("data-motion-chapter-save", "entering");
      await expect(directoryFeedback).toHaveAttribute("data-motion-chapter-feedback", "entering");
      expect(await readAnimation(saveFeedback)).toMatchObject({ duration: "0.18s", name: "motion-chapter-feedback-in", playState: "paused" });
      await dispatchAnimation(saveFeedback, "animationend", "motion-chapter-feedback-in");
      await dispatchAnimation(directoryFeedback, "animationend", "motion-chapter-feedback-in");
      await expect(saveFeedback).toHaveAttribute("data-motion-chapter-save", "idle");
      await expect(directoryFeedback).toHaveAttribute("data-motion-chapter-feedback", "idle");
      await expect(page.locator(".toc-entry-title").first()).toContainText("motion");

      await page.emulateMedia({ reducedMotion: "reduce" });
      const reducedTitleInput = detail.locator("input").first();
      await reducedTitleInput.fill(`${await reducedTitleInput.inputValue()} reduced`);
      await detail.locator("button[type='submit']").click();
      await expect(saveFeedback, "iPad reduced save settles directly").toHaveAttribute("data-motion-chapter-save", "idle");
      await expect(directoryFeedback).toHaveAttribute("data-motion-chapter-feedback", "idle");
      expect((await readAnimation(saveFeedback)).name).toBe("none");
      await expect(page.locator(".toc-entry-title").first()).toContainText("reduced");
      await pause.evaluate((element) => element.remove());
      return;
    }

    const pause = await installPauseStyle(page, ".sheet, .sheet-scrim");
    const editTrigger = page.locator(".toc-edit-button").first();
    await editTrigger.focus();
    await editTrigger.click();
    const editor = page.locator(".sheet[data-sheet-type='editChapter']");
    const scrim = page.locator(".sheet-scrim");
    const entryName = await expectGlobalPresenceMotion(editor, "entering", "editChapter panel entry");
    await expectGlobalPresenceMotion(scrim, "entering", "editChapter scrim entry");
    const enteringPresence = Number(await editor.getAttribute("data-motion-presence"));
    await expectFocusWithin(page, editor, "editChapter entry");
    await dispatchAnimation(editor, "animationend", entryName);
    await expect(editor).toHaveAttribute("data-motion-state", "idle");
    const originalTitle = await editor.locator("input").first().inputValue();
    await editor.locator(".sheet-close").click();
    const exitName = await expectGlobalPresenceMotion(editor, "closing", "editChapter panel exit");
    await expect(editor).toHaveAttribute("aria-modal", "true");
    await expect(editor).toHaveAttribute("aria-busy", "true");
    await expect(editor.locator("input").first(), "closing editor keeps its immutable view snapshot").toHaveValue(originalTitle);
    await dispatchAnimation(editor, "animationcancel", entryName);
    await expect(editor, "stale editChapter entry cancellation cannot settle exit").toHaveAttribute("data-motion-state", "closing");
    await dispatchAnimation(editor, "animationend", exitName);
    await expect(editor).toHaveCount(0);
    await expect(editTrigger, "editChapter cleanup restores its real trigger").toBeFocused();

    await editTrigger.click();
    const reopenedEditor = page.locator(".sheet[data-sheet-type='editChapter']");
    const reopenedPresence = Number(await reopenedEditor.getAttribute("data-motion-presence"));
    expect(reopenedPresence, "editChapter reopen owns a newer generation").toBeGreaterThan(enteringPresence);
    const reopenedEntryName = (await readAnimation(reopenedEditor)).name.split(",")[0].trim();
    await dispatchAnimation(reopenedEditor, "animationcancel", reopenedEntryName);
    await reopenedEditor.locator("input").first().fill(`${originalTitle} motion`);
    await reopenedEditor.locator("button[type='submit']").click();
    await expect(reopenedEditor, "real editChapter save freezes the current panel for exit").toHaveAttribute("data-motion-state", "closing");
    let reopenedExitName = (await readAnimation(reopenedEditor)).name.split(",")[0].trim();
    await dispatchAnimation(reopenedEditor, "animationcancel", reopenedExitName);
    await expect(reopenedEditor).toHaveCount(0);
    await expect(page.locator(".toc-entry-title").first(), "real sheet save updates the current directory row").toContainText("motion");

    const editCountBeforeDelete = await page.locator(".toc-edit-button").count();
    const deleteTrigger = page.locator(".toc-edit-button").first();
    await deleteTrigger.click();
    const deleteEditor = page.locator(".sheet[data-sheet-type='editChapter']");
    const deleteEntryName = (await readAnimation(deleteEditor)).name.split(",")[0].trim();
    await dispatchAnimation(deleteEditor, "animationend", deleteEntryName);
    await deleteEditor.locator(".button-danger").first().click();
    await expect(deleteEditor.locator(".chapter-delete-confirm")).toBeVisible();
    await deleteEditor.locator(".chapter-delete-confirm .button-danger").click();
    await expect(deleteEditor).toHaveAttribute("data-motion-state", "closing");
    reopenedExitName = (await readAnimation(deleteEditor)).name.split(",")[0].trim();
    await dispatchAnimation(deleteEditor, "animationend", reopenedExitName);
    await expect(deleteEditor).toHaveCount(0);
    await expect.poll(() => page.locator(".toc-edit-button").count(), { message: "real delete removes at least its selected directory row" }).toBeLessThan(editCountBeforeDelete);
    expect(await page.evaluate(() => document.activeElement?.tagName), "edit/delete cleanup never loses focus to body").not.toBe("BODY");

    await page.emulateMedia({ reducedMotion: "reduce" });
    const reducedTrigger = page.locator(".toc-edit-button").first();
    await reducedTrigger.click();
    const reducedEditor = page.locator(".sheet[data-sheet-type='editChapter']");
    await expect(reducedEditor, "reduced editChapter entry is direct").toHaveAttribute("data-motion-state", "idle");
    expect((await readAnimation(reducedEditor)).name).toBe("none");
    const reducedTitle = await reducedEditor.locator("input").first().inputValue();
    await reducedEditor.locator("input").first().fill(`${reducedTitle} reduced`);
    await reducedEditor.locator("button[type='submit']").click();
    await expect(reducedEditor, "reduced editChapter save unmounts synchronously").toHaveCount(0);
    await expect(page.locator(".toc-entry-title").first(), "reduced sheet save updates without an intermediate visual phase").toContainText("reduced");
    await pause.evaluate((element) => element.remove());
  });

  test("keeps AI panel and scrim synchronized through rapid close/reopen, stale generations, focus return, and reduced cleanup", async ({ page }) => {
    await gotoApp(page);
    await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
    await page.clock.pauseAt(new Date("2026-01-01T00:00:01Z"));
    const pause = await installPauseStyle(page, ".ai-overlay, .ai-overlay-scrim, .ai-shared-surface, .ai-shared-origin-icon");
    const orb = page.locator(".ai-orb");
    await orb.focus();
    await orb.click();
    const dialog = page.locator(".ai-overlay");
    const scrim = page.locator(".ai-overlay-scrim");
    const entryName = await expectGlobalPresenceMotion(dialog, "entering", "AI panel entry");
    await expectGlobalPresenceMotion(scrim, "entering", "AI scrim entry");
    const firstPresence = Number(await dialog.getAttribute("data-motion-presence"));
    await expect(orb).toHaveAttribute("aria-expanded", "true");
    await expect(orb).toHaveAttribute("data-ai-orb-hidden", "true");
    await page.clock.runFor(20);
    await expect(dialog.locator(".ai-compose input"), "AI entry autofocuses its real input").toBeFocused();
    await dispatchAnimation(dialog, "animationend", entryName);
    await expect(dialog).toHaveAttribute("data-motion-state", "idle");
    await expect(scrim).toHaveAttribute("data-motion-state", "idle");

    await dialog.locator(".ai-compose input").press("Escape");
    const exitName = await expectGlobalPresenceMotion(dialog, "closing", "AI panel exit");
    await expectGlobalPresenceMotion(scrim, "closing", "AI scrim exit");
    await expect(dialog).toHaveAttribute("aria-modal", "true");
    await expect(dialog).toHaveAttribute("aria-busy", "true");
    await dispatchAnimation(dialog, "animationcancel", entryName);
    await expect(dialog, "stale AI entry cancellation cannot settle exit").toHaveAttribute("data-motion-state", "closing");
    const staleDialog = await dialog.elementHandle();
    if (!staleDialog) throw new Error("rapid AI reopen needs its closing root");

    await orb.dispatchEvent("click");
    await expect(dialog).toHaveAttribute("data-motion-state", "entering");
    await expect(scrim).toHaveAttribute("data-motion-state", "entering");
    const reopenedPresence = Number(await dialog.getAttribute("data-motion-presence"));
    expect(reopenedPresence, "rapid AI reopen increments Presence generation").toBeGreaterThan(firstPresence);
    expect(await staleDialog.evaluate((element) => element.isConnected), "rapid AI reopen detaches the frozen exit root").toBe(false);
    await expect(page.locator(".ai-overlay-layer")).toHaveCount(1);
    await expect(page.locator(".ai-overlay-scrim")).toHaveCount(1);
    await expect(page.locator(".ai-shared-surface")).toHaveCount(1);
    await expect(page.locator(".ai-shared-origin-icon")).toHaveCount(1);
    for (const type of ["animationend", "animationcancel"] as const) {
      await staleDialog.evaluate((element, event) => {
        element.dispatchEvent(new AnimationEvent(event.type, { animationName: event.name, bubbles: true }));
      }, { name: exitName, type });
      await expect(dialog, `stale AI ${type} cannot settle reopened generation`).toHaveAttribute("data-motion-state", "entering");
      await expect(scrim).toHaveAttribute("data-motion-state", "entering");
    }
    const reopenedEntryName = (await readAnimation(dialog)).name.split(",")[0].trim();
    await dispatchAnimation(dialog, "animationcancel", exitName);
    await expect(dialog, "wrong AI exit phase cannot settle entry").toHaveAttribute("data-motion-state", "entering");
    await dispatchAnimation(dialog, "animationcancel", reopenedEntryName);
    await expect(dialog).toHaveAttribute("data-motion-state", "idle");
    await expect(scrim).toHaveAttribute("data-motion-state", "idle");
    await page.clock.runFor(20);
    await expect(dialog.locator(".ai-compose input")).toBeFocused();

    await dialog.locator(".ai-close").click();
    await expect(dialog).toHaveAttribute("data-motion-state", "closing");
    await expect(scrim).toHaveAttribute("data-motion-state", "closing");
    await page.clock.runFor(449);
    await expect(dialog, "real AI exit remains mounted at global fallback 449ms").toHaveAttribute("data-motion-state", "closing");
    await expect(scrim).toHaveAttribute("data-motion-state", "closing");
    await page.clock.runFor(1);
    await expect(dialog, "real AI exit cleans up at global fallback 450ms").toHaveCount(0);
    await expect(scrim).toHaveCount(0);
    await expect(page.locator(".ai-overlay-layer")).toHaveCount(0);
    await expect(page.locator(".ai-shared-surface")).toHaveCount(0);
    await expect(page.locator(".ai-shared-origin-icon")).toHaveCount(0);
    await expect(orb).toHaveAttribute("aria-expanded", "false");
    await expect(orb).toHaveAttribute("data-ai-orb-hidden", "false");
    await expect(orb, "AI fallback cleanup restores focus only after unmount").toBeFocused();

    await page.emulateMedia({ reducedMotion: "reduce" });
    await orb.click();
    await expect(dialog).toHaveAttribute("data-motion-state", "idle");
    await expect(scrim).toHaveAttribute("data-motion-state", "idle");
    expect((await readAnimation(dialog)).name).toBe("none");
    expect((await readAnimation(scrim)).name).toBe("none");
    await page.clock.runFor(20);
    await expect(dialog.locator(".ai-compose input")).toBeFocused();
    await dialog.locator(".ai-compose input").press("Escape");
    await expect(dialog, "reduced AI close unmounts synchronously").toHaveCount(0);
    await expect(scrim).toHaveCount(0);
    await expect(orb).toBeFocused();
    await pause.evaluate((element) => element.remove());
  });

  test("isolates consecutive Toast generations, stale events/timers, 3200ms dwell, and 180/150ms visual phases", async ({ page }) => {
    await gotoApp(page);
    await page.locator(".primary-nav .nav-item").nth(3).click();
    await expect(page.locator(".profile-screen")).toBeVisible();
    await settleScreen(page);
    await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
    await page.clock.pauseAt(new Date("2026-01-01T00:00:01Z"));
    await installPauseStyle(page, ".toast");
    const reminder = page.locator(".settings-row").nth(3);
    const preferences = page.locator(".settings-row").nth(4);
    const toast = page.locator(".toast");

    await reminder.focus();
    await reminder.press("Enter");
    await expect(toast).toHaveAttribute("data-motion-state", "entering");
    const firstMotion = await readAnimation(toast);
    expect(firstMotion).toMatchObject({ duration: "0.18s", name: "motion-toast-in", playState: "paused" });
    expect(normalizeTimingFunction(firstMotion.timing)).toBe(curves.localEnter);
    await expect(toast).toHaveAttribute("role", "status");
    await expect(toast).toHaveAttribute("aria-live", "polite");
    await expect(toast).toHaveAttribute("aria-atomic", "true");
    const firstPresence = Number(await toast.getAttribute("data-motion-presence"));
    const staleFirst = await toast.elementHandle();
    if (!staleFirst) throw new Error("first Toast generation root is missing");
    await dispatchAnimation(toast, "animationend", "motion-toast-in");
    await expect(toast).toHaveAttribute("data-motion-state", "idle");
    await page.clock.runFor(3199);
    await expect(toast, "Toast A remains idle through 3199ms of business dwell").toHaveAttribute("data-motion-state", "idle");

    await preferences.press("Enter");
    await expect(toast).toHaveAttribute("data-motion-state", "entering");
    const secondPresence = Number(await toast.getAttribute("data-motion-presence"));
    expect(secondPresence, "Toast B receives a monotonic replacement generation").toBeGreaterThan(firstPresence);
    expect(await staleFirst.evaluate((element) => element.isConnected), "Toast A root detaches for B").toBe(false);
    await page.clock.runFor(1);
    await expect(toast, "Toast A's old 3200ms deadline cannot close B").toHaveAttribute("data-motion-state", "entering");
    for (const [type, name] of [
      ["animationend", "motion-toast-in"],
      ["animationcancel", "motion-toast-in"],
      ["animationcancel", "motion-toast-out"]
    ] as const) {
      await staleFirst.evaluate((element, event) => {
        element.dispatchEvent(new AnimationEvent(event.type, { animationName: event.name, bubbles: true }));
      }, { name, type });
      await expect(toast, `Toast A stale ${type}/${name} cannot settle B`).toHaveAttribute("data-motion-state", "entering");
    }
    await dispatchAnimation(toast, "animationend", "motion-toast-out");
    await expect(toast, "wrong-phase exit cannot settle Toast B entry").toHaveAttribute("data-motion-state", "entering");
    await dispatchAnimation(toast, "animationcancel", "motion-toast-in");
    await expect(toast).toHaveAttribute("data-motion-state", "idle");

    await page.clock.runFor(3198);
    await expect(toast, "Toast B owns its full replacement dwell").toHaveAttribute("data-motion-state", "idle");
    await page.clock.runFor(1);
    await expect(toast, "Toast B closes at exactly its own 3200ms deadline").toHaveAttribute("data-motion-state", "closing");
    const secondExitMotion = await readAnimation(toast);
    expect(secondExitMotion).toMatchObject({ duration: "0.15s", name: "motion-toast-out", playState: "paused" });
    expect(normalizeTimingFunction(secondExitMotion.timing)).toBe(curves.localExit);
    const closingSecond = await toast.elementHandle();
    if (!closingSecond) throw new Error("closing Toast B root is missing");

    await reminder.press("Enter");
    await expect(toast, "Toast C replaces B's frozen exit with a fresh entry").toHaveAttribute("data-motion-state", "entering");
    const thirdPresence = Number(await toast.getAttribute("data-motion-presence"));
    expect(thirdPresence).toBeGreaterThan(secondPresence);
    expect(await closingSecond.evaluate((element) => element.isConnected), "Toast B exit root detaches for C").toBe(false);
    await closingSecond.evaluate((element) => {
      element.dispatchEvent(new AnimationEvent("animationcancel", { animationName: "motion-toast-out", bubbles: true }));
      element.dispatchEvent(new AnimationEvent("animationend", { animationName: "motion-toast-out", bubbles: true }));
    });
    await expect(toast, "Toast B stale exit events cannot unmount C").toHaveAttribute("data-motion-state", "entering");
    await dispatchAnimation(toast, "animationend", "motion-toast-in");
    await expect(toast).toHaveAttribute("data-motion-state", "idle");
    await page.clock.runFor(3199);
    await expect(toast).toHaveAttribute("data-motion-state", "idle");
    await page.clock.runFor(1);
    await expect(toast).toHaveAttribute("data-motion-state", "closing");
    expect(await readAnimation(toast)).toMatchObject({ duration: "0.15s", name: "motion-toast-out" });
    await page.clock.runFor(149);
    await expect(toast, "business expiry starts a distinct 150ms visual exit").toHaveAttribute("data-motion-state", "closing");
    await page.clock.runFor(1);
    await dispatchAnimation(toast, "animationend", "motion-toast-out");
    await expect(toast, "Toast unmounts only after its visual exit completion").toHaveCount(0);
  });
});

test.describe("3. DemoRepository upload, success, image, and card lifecycle", () => {
  test("uses real local validation, upload, parse status, and success feedback at 180/200ms", async ({ page }) => {
    test.setTimeout(45_000);
    await gotoApp(page);
    await page.locator(".home-import-course-action").click();
    await expect(page.locator(".upload-flow-screen")).toBeVisible();
    await settleScreen(page);
    await installPauseStyle(page, ".upload-status-feedback, .upload-success-mark, .parse-status-feedback, .stage-completion-check, .chapter-status-mark, .course-ready-success-mark, .course-ready-check-path, .success-hero-image");
    const input = page.locator('input[type="file"]');
    await input.setInputFiles({ name: "invalid.exe", mimeType: "application/octet-stream", buffer: Buffer.from("invalid") });
    const error = page.locator(".upload-error");
    await expect(error).toBeVisible();
    let motion = await readAnimation(error);
    expect(motion).toMatchObject({ duration: "0.18s", name: "motion-local-status-in", playState: "paused" });
    expect(normalizeTimingFunction(motion.timing)).toBe(curves.localEnter);

    await input.setInputFiles({ name: "biology-motion.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 motion fixture") });
    const selected = page.locator(".upload-status-feedback");
    await expect(selected).toBeVisible();
    expect(await readAnimation(selected)).toMatchObject({ duration: "0.18s", name: "motion-local-status-in" });
    await page.locator(".upload-flow-primary > .button").click();
    await expect(page.locator(".parse-ready-screen")).toBeVisible();
    await settleScreen(page);
    const success = page.locator(".upload-success-mark");
    await expect(success).toBeVisible();
    expect(await readAnimation(success)).toMatchObject({ duration: "0.18s", name: "motion-success-mark-in" });
    const parseStatus = page.locator(".parse-status-feedback");
    expect(await readAnimation(parseStatus)).toMatchObject({ duration: "0.18s", name: "motion-local-status-in" });
    await expect(parseStatus.locator('[role="progressbar"]')).toHaveAttribute("aria-label", /0%/);

    await page.locator(".parse-flow-actions .button").first().click();
    await expect(page.locator(".processing-flow-screen")).toBeVisible();
    await settleScreen(page);
    const processingSprite = page.locator(".processing-sprite-strip");
    const spriteAnimation = await readAnimation(processingSprite);
    expect(spriteAnimation).toMatchObject({
      duration: "1.2s",
      name: "processing-cloud-sprite-loading",
      playState: "running"
    });
    expect(spriteAnimation.timing).toContain("steps(8");
    const stageChecks = page.locator(".stage-completion-check");
    await expect(stageChecks).toHaveCount(2, { timeout: 8_000 });
    const firstStage = await stageChecks.nth(0).elementHandle();
    const secondStage = await stageChecks.nth(1).elementHandle();
    if (!firstStage || !secondStage) throw new Error("Processing 46% stage roots are missing");
    for (let index = 0; index < 2; index += 1) {
      const check = stageChecks.nth(index);
      await expect(check).toHaveAttribute("data-motion-stage-state", "entering");
      expect(await readAnimation(check)).toMatchObject({ duration: "0.18s", name: "motion-stage-check-in", playState: "paused" });
      await dispatchAnimation(check, "animationend", "motion-stage-check-in");
      await expect(check).toHaveAttribute("data-motion-stage-state", "idle");
    }
    await expect(stageChecks).toHaveCount(3, { timeout: 8_000 });
    expect(await firstStage.evaluate((element) => element === document.querySelectorAll(".stage-completion-check")[0]), "same processing status keeps stage 1 identity").toBe(true);
    expect(await secondStage.evaluate((element) => element === document.querySelectorAll(".stage-completion-check")[1]), "same processing status keeps stage 2 identity").toBe(true);
    await expect(stageChecks.nth(0), "same-status progress does not replay stage 1").toHaveAttribute("data-motion-stage-state", "idle");
    await expect(stageChecks.nth(1), "same-status progress does not replay stage 2").toHaveAttribute("data-motion-stage-state", "idle");
    await expect(stageChecks.nth(2), "only the newly completed stage enters").toHaveAttribute("data-motion-stage-state", "entering");
    await dispatchAnimation(stageChecks.nth(2), "animationcancel", "motion-stage-check-in");
    await expect(stageChecks.nth(2)).toHaveAttribute("data-motion-stage-state", "idle");
    await expect(page.locator(".chapter-confirm-screen")).toBeVisible({ timeout: 12_000 });
    await settleScreen(page);
    await expect(page.locator(".toc-entry").first()).toBeVisible();
    const chapterMarks = page.locator(".chapter-status-mark");
    await expect.poll(() => chapterMarks.count(), { message: "real reviewed ChapterConfirm rows expose first-play checks" }).toBeGreaterThan(0);
    motion = await readAnimation(chapterMarks.first());
    expect(motion.name).toBe("motion-chapter-check-in");
    expect(motion.duration).toBe("0.18s");
    expect(normalizeTimingFunction(motion.timing)).toBe(curves.localEnter);
    for (let index = (await chapterMarks.count()) - 1; index >= 0; index -= 1) {
      await dispatchAnimation(chapterMarks.nth(index), "animationend", "motion-chapter-check-in");
    }
    await expect(chapterMarks, "settled ChapterConfirm checks are removed instead of retaining animation residue").toHaveCount(0);
    await page.locator(".chapter-confirm-actions .button").click();
    await expect(page.locator(".course-ready-screen")).toBeVisible({ timeout: 10_000 });
    await settleScreen(page);
    const ready = page.locator(".course-ready-success-mark");
    await expect(ready).toHaveAttribute("data-motion-course-ready-state", "entering");
    expect(await readAnimation(ready)).toMatchObject({ duration: "0.18s", name: "motion-course-ready-success-in" });
    await dispatchAnimation(ready, "animationend", "motion-course-ready-success-in");
    await expect(ready).toHaveAttribute("data-motion-course-ready-state", "idle");
    await dispatchAnimation(ready, "animationend", "motion-course-ready-success-in");
    await expect(ready).toHaveAttribute("data-motion-course-ready-state", "idle");
  });

  test("keeps the real reduced upload, Processing, ChapterConfirm, and CourseReady path direct and consumes one-shot keys", async ({ page }) => {
    test.setTimeout(45_000);
    await gotoApp(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".home-import-course-action").click();
    await expect(page.locator(".upload-flow-screen")).toBeVisible();
    await settleScreen(page);
    const input = page.locator('input[type="file"]');
    await input.setInputFiles({
      name: "biology-reduced-motion.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 reduced motion fixture")
    });
    const selected = page.locator(".upload-status-feedback");
    await expect(selected).toBeVisible();
    expect((await readAnimation(selected)).name).toBe("none");
    await page.locator(".upload-flow-primary > .button").click();
    await expect(page.locator(".parse-ready-screen")).toBeVisible();
    await settleScreen(page);
    expect((await readAnimation(page.locator(".upload-success-mark"))).name).toBe("none");
    expect((await readAnimation(page.locator(".parse-status-feedback"))).name).toBe("none");

    await page.locator(".parse-flow-actions .button").first().click();
    await expect(page.locator(".processing-flow-screen")).toBeVisible();
    await settleScreen(page);
    expect((await readAnimation(page.locator(".processing-sprite-strip"))).name).toBe("none");
    let stageChecks = page.locator(".stage-completion-check");
    await expect(stageChecks).toHaveCount(2, { timeout: 8_000 });
    for (let index = 0; index < await stageChecks.count(); index += 1) {
      await expect(stageChecks.nth(index), `reduced Processing stage ${index + 1} is direct`).toHaveAttribute("data-motion-stage-state", "idle");
      expect((await readAnimation(stageChecks.nth(index))).name).toBe("none");
    }
    stageChecks = page.locator(".stage-completion-check");
    await expect(stageChecks).toHaveCount(3, { timeout: 8_000 });
    for (let index = 0; index < await stageChecks.count(); index += 1) {
      await expect(stageChecks.nth(index), `reduced Processing stage ${index + 1} remains direct`).toHaveAttribute("data-motion-stage-state", "idle");
      expect((await readAnimation(stageChecks.nth(index))).name).toBe("none");
    }
    const consumedStageKeys = await stageChecks.evaluateAll((elements) => elements.map((element) => element.getAttribute("data-motion-stage-key")));
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await expect(stageChecks).toHaveCount(3);
    expect(await stageChecks.evaluateAll((elements) => elements.map((element) => element.getAttribute("data-motion-stage-key")))).toEqual(consumedStageKeys);
    for (let index = 0; index < await stageChecks.count(); index += 1) {
      await expect(stageChecks.nth(index), `restoring normal motion does not replay consumed Processing stage ${index + 1}`).toHaveAttribute("data-motion-stage-state", "idle");
      expect((await readAnimation(stageChecks.nth(index))).name).toBe("none");
    }
    await page.emulateMedia({ reducedMotion: "reduce" });

    await expect(page.locator(".chapter-confirm-screen")).toBeVisible({ timeout: 12_000 });
    await settleScreen(page);
    await expect(page.locator(".toc-entry").first()).toBeVisible();
    await expect(page.locator(".chapter-status-mark"), "reviewed ChapterConfirm keys render no intermediate mark under reduced motion").toHaveCount(0);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await expect(page.locator(".chapter-status-mark"), "restoring normal motion does not replay consumed reviewed-status keys").toHaveCount(0);
    await page.emulateMedia({ reducedMotion: "reduce" });

    await page.locator(".chapter-confirm-actions .button").click();
    await expect(page.locator(".course-ready-screen")).toBeVisible({ timeout: 10_000 });
    await settleScreen(page);
    const ready = page.locator(".course-ready-success-mark");
    const checkPath = page.locator(".course-ready-check-path");
    await expect(ready, "CourseReady success is direct under reduced motion").toHaveAttribute("data-motion-course-ready-state", "idle");
    const readyKey = await ready.getAttribute("data-motion-course-ready-key");
    expect((await readAnimation(ready)).name).toBe("none");
    expect((await readAnimation(checkPath)).name).toBe("none");
    const hero = page.locator(".success-hero-image");
    await expect(hero, "successful CourseReady image settles directly under reduced motion").toHaveAttribute("data-motion-image-state", "idle");
    expect((await readAnimation(hero)).name).toBe("none");
    const readyNode = await ready.elementHandle();
    if (!readyNode) throw new Error("Reduced CourseReady success root is missing");

    await page.emulateMedia({ reducedMotion: "no-preference" });
    await expect(ready, "restoring normal motion does not replay the consumed CourseReady key").toHaveAttribute("data-motion-course-ready-state", "idle");
    await expect(ready).toHaveAttribute("data-motion-course-ready-key", readyKey ?? "");
    expect(await readyNode.evaluate((element) => element === document.querySelector(".course-ready-success-mark")), "normal restoration keeps CourseReady success DOM identity").toBe(true);
    expect((await readAnimation(ready)).name).toBe("none");
    expect((await readAnimation(checkPath)).name).toBe("none");
    await expect(hero).toHaveAttribute("data-motion-image-state", "idle");
    expect((await readAnimation(hero)).name).toBe("none");
  });

  test("plays the current real Library course card once across reconstruction without synthetic state probes", async ({ page }) => {
    await installInitialPauseStyle(page, "[data-motion-course-card-state='entering']");
    await gotoApp(page);
    await openLibrary(page);
    const libraryCard = page.locator(".library-course-grid [data-motion-course-card-state]").first();
    await expect(libraryCard).toHaveAttribute("data-motion-course-card-state", "entering");
    let motion = await readAnimation(libraryCard);
    expect(motion).toMatchObject({ delay: "0s", duration: "0.18s", name: "motion-course-card-in", playState: "paused" });
    expect(normalizeTimingFunction(motion.timing)).toBe(curves.localEnter);
    const motionKey = await libraryCard.getAttribute("data-motion-course-card-key");
    const firstCard = await libraryCard.elementHandle();
    if (!firstCard) throw new Error("Library course card is missing");
    await dispatchAnimation(libraryCard, "animationend", "motion-course-card-in");
    await expect(libraryCard).toHaveAttribute("data-motion-course-card-state", "idle");

    await page.locator(".primary-nav .nav-item").first().click();
    await expect(page.locator(".home-dashboard")).toBeVisible();
    await settleScreen(page);
    await openLibrary(page);
    await expect(libraryCard).toHaveAttribute("data-motion-course-card-key", motionKey ?? "");
    await expect(libraryCard, "the same semantic course does not replay when Library is reconstructed").toHaveAttribute("data-motion-course-card-state", "idle");
    expect(await firstCard.evaluate((element) => element.isConnected), "leaving Library detaches the first course card instance").toBe(false);
    motion = await readAnimation(libraryCard);
    expect(motion.name).toBe("none");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".primary-nav .nav-item").first().click();
    await expect(page.locator(".home-dashboard")).toBeVisible();
    await settleScreen(page);
    await openLibrary(page);
    await expect(libraryCard).toHaveAttribute("data-motion-course-card-state", "idle");
    expect((await readAnimation(libraryCard)).name).toBe("none");
  });

  test("plays the actual cover once per DOM node without replaying duplicate load", async ({ page }) => {
    await installInitialPauseStyle(page, ".course-cover-image[data-motion-image-state='entering']");
    await gotoApp(page);
    await openLibrary(page);
    const image = page.locator(".course-cover-image").first();
    await expect(image).toHaveAttribute("data-motion-image-state", "entering");
    expect(await readAnimation(image)).toMatchObject({ duration: "0.18s", name: "motion-stage3-image-in", playState: "paused" });
    await dispatchAnimation(image, "animationend", "motion-stage3-image-in");
    await expect(image).toHaveAttribute("data-motion-image-state", "idle");
    await image.dispatchEvent("load");
    await expect(image).toHaveAttribute("data-motion-image-state", "idle");
  });

  test("keeps a routed actual-cover failure as a stable non-animated fallback", async ({ page, audit }) => {
    await page.route("**/assets/textbook/biology-chapter-2-open.webp", (route) => route.fulfill({ status: 404, body: "missing" }));
    await gotoApp(page);
    await openLibrary(page);
    const fallback = page.locator(".course-cover-fallback").first();
    await expect(fallback).toBeVisible();
    await expect(fallback).toHaveAttribute("data-motion-image-state", "failed");
    expect((await readAnimation(fallback)).name).toBe("none");
    await expect.poll(() => audit.consoleErrors).toEqual(["Failed to load resource: the server responded with a status of 404 (Not Found)"]);
    audit.consoleErrors.splice(0, 1);
  });
});

test.describe("4. current SourceReader, Notes, Community, and StudyPlan local lifecycles", () => {
  test("replaces only SourceReader page content through rapid switches, same state, and reconstructed re-entry", async ({ page }) => {
    await openStudy(page);
    const chapterToggle = page.locator("#study-chapter-c2-toggle");
    if (await chapterToggle.getAttribute("aria-expanded") !== "true") await chapterToggle.click();
    const sectionToggle = page.locator("#study-section-c2s1-toggle");
    if (await sectionToggle.getAttribute("aria-expanded") !== "true") await sectionToggle.click();
    const pause = await installPauseStyle(page, ".source-page-frame, .source-page-image");
    await openSourceFromStudy(page);
    const frame = page.locator(".source-page-frame");
    const firstEntryName = await expectLocalEntry(frame, "SourceReader initial cited page");
    const firstFrame = await frame.elementHandle();
    if (!firstFrame) throw new Error("SourceReader initial page frame is missing");
    await dispatchAnimation(frame, "animationend", firstEntryName);
    await expect(frame).toHaveAttribute("data-motion-item-state", "idle");

    const next = page.locator(".source-reader-toolbar button").last();
    await next.click();
    const secondEntryName = await expectLocalEntry(frame, "SourceReader next page");
    const secondFrame = await frame.elementHandle();
    if (!secondFrame) throw new Error("SourceReader second page frame is missing");
    expect(await firstFrame.evaluate((element) => element.isConnected), "page replacement detaches only the old content root").toBe(false);
    await firstFrame.evaluate((element, name) => {
      element.dispatchEvent(new AnimationEvent("animationend", { animationName: name, bubbles: true }));
    }, firstEntryName);
    await expect(frame, "stale first-page completion cannot settle the second page").toHaveAttribute("data-motion-item-state", "entering");

    await next.click();
    await expect(frame, "rapid second page switch owns a fresh local generation").toHaveAttribute("data-motion-item-state", "entering");
    const thirdEntryName = (await readAnimation(frame)).name.split(",")[0].trim();
    expect(await secondFrame.evaluate((element) => element.isConnected), "rapid page switch detaches the superseded second page").toBe(false);
    await secondFrame.evaluate((element, name) => {
      element.dispatchEvent(new AnimationEvent("animationcancel", { animationName: name, bubbles: true }));
    }, secondEntryName);
    await expect(frame, "stale second-page cancellation cannot settle the third page").toHaveAttribute("data-motion-item-state", "entering");
    await dispatchAnimation(frame, "animationend", thirdEntryName);
    await expect(frame).toHaveAttribute("data-motion-item-state", "idle");

    const returnToCitation = page.locator(".source-reader-actions .button").first();
    await expect(returnToCitation).toBeEnabled();
    await returnToCitation.click();
    await expect(frame).toHaveAttribute("data-motion-item-state", "entering");
    const citationEntryName = (await readAnimation(frame)).name.split(",")[0].trim();
    await dispatchAnimation(frame, "animationend", citationEntryName);
    await expect(frame).toHaveAttribute("data-motion-item-state", "idle");
    await expect(returnToCitation).toBeDisabled();
    const citedFrame = await frame.elementHandle();
    if (!citedFrame) throw new Error("SourceReader cited page root is missing");
    await dispatchAnimation(frame, "animationend", citationEntryName);
    await expect(frame, "duplicate completion does not replay a settled cited page").toHaveAttribute("data-motion-item-state", "idle");
    expect(await citedFrame.evaluate((element) => element === document.querySelector(".source-page-frame")), "same-page state keeps its DOM identity").toBe(true);

    await returnFromSourceToStudy(page);
    await openSourceFromStudy(page);
    const reentryName = await expectLocalEntry(frame, "reconstructed SourceReader cited page");
    expect(await citedFrame.evaluate((element) => element.isConnected), "leaving SourceReader detached the previous cited-page root").toBe(false);
    await dispatchAnimation(frame, "animationend", reentryName);
    await expect(frame).toHaveAttribute("data-motion-item-state", "idle");
    await pause.evaluate((element) => element.remove());
  });

  test("keeps the real SourceReader cited page, switch, image, and reconstructed re-entry direct under reduced motion without normal replay", async ({ page }) => {
    await openStudy(page);
    const chapterToggle = page.locator("#study-chapter-c2-toggle");
    if (await chapterToggle.getAttribute("aria-expanded") !== "true") await chapterToggle.click();
    const sectionToggle = page.locator("#study-section-c2s1-toggle");
    if (await sectionToggle.getAttribute("aria-expanded") !== "true") await sectionToggle.click();
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openSourceFromStudy(page);

    const frame = page.locator(".source-page-frame");
    const image = page.locator(".source-page-image");
    await expect(frame, "initial cited page is direct under reduced motion").toHaveAttribute("data-motion-item-state", "idle");
    expect((await readAnimation(frame)).name).toBe("none");
    await expect(image, "the successful cited-page image settles directly under reduced motion").toHaveAttribute("data-motion-image-state", "idle");
    expect((await readAnimation(image)).name).toBe("none");
    const initialKey = await frame.getAttribute("data-motion-item-key");

    await page.locator(".source-reader-toolbar button").last().click();
    await expect(frame).not.toHaveAttribute("data-motion-item-key", initialKey ?? "");
    await expect(frame, "a real page switch is direct under reduced motion").toHaveAttribute("data-motion-item-state", "idle");
    expect((await readAnimation(frame)).name).toBe("none");
    await expect(image).toHaveAttribute("data-motion-image-state", "idle");
    expect((await readAnimation(image)).name).toBe("none");

    await returnFromSourceToStudy(page);
    await openSourceFromStudy(page);
    await expect(frame, "the reconstructed cited page is direct under reduced motion").toHaveAttribute("data-motion-item-state", "idle");
    await expect(frame).toHaveAttribute("data-motion-item-key", initialKey ?? "");
    expect((await readAnimation(frame)).name).toBe("none");
    await expect(image).toHaveAttribute("data-motion-image-state", "idle");
    expect((await readAnimation(image)).name).toBe("none");
    const reconstructedFrame = await frame.elementHandle();
    if (!reconstructedFrame) throw new Error("Reduced SourceReader reconstructed frame is missing");

    await page.emulateMedia({ reducedMotion: "no-preference" });
    await expect(frame, "restoring normal motion does not replay the current cited-page key").toHaveAttribute("data-motion-item-state", "idle");
    await expect(frame).toHaveAttribute("data-motion-item-key", initialKey ?? "");
    expect(await reconstructedFrame.evaluate((element) => element === document.querySelector(".source-page-frame")), "normal restoration keeps the reconstructed page DOM identity").toBe(true);
    expect((await readAnimation(frame)).name).toBe("none");
    await expect(image).toHaveAttribute("data-motion-image-state", "idle");
    expect((await readAnimation(image)).name).toBe("none");
  });

  test("keeps a failed SourceReader image fallback stable and retries on a new page DOM without geometry motion", async ({ page, audit }, testInfo) => {
    const failedResource = "Failed to load resource: the server responded with a status of 404 (Not Found)";
    const failedSourceAsset = "**/assets/textbook/biology-lesson-meiosis-1.webp";
    await page.route(failedSourceAsset, (route) => route.fulfill({ status: 404, body: "missing source page" }));
    await openStudy(page);
    const chapterToggle = page.locator("#study-chapter-c2-toggle");
    if (await chapterToggle.getAttribute("aria-expanded") !== "true") await chapterToggle.click();
    const sectionToggle = page.locator("#study-section-c2s1-toggle");
    if (await sectionToggle.getAttribute("aria-expanded") !== "true") await sectionToggle.click();
    await openSourceFromStudy(page);
    const fallback = page.locator(".source-page-fallback");
    await expect(fallback).toBeVisible();
    await expect.poll(() => audit.consoleErrors.length, { message: "SourceReader failure emits explicit 404 evidence" }).toBeGreaterThan(0);
    await expect(fallback).toHaveAttribute("data-motion-image-state", "failed");
    expect((await readAnimation(fallback)).name, "failed source page has no image animation residue").toBe("none");
    const before = await fallback.boundingBox();
    if (!before) throw new Error("failed SourceReader fallback has no geometry");

    await page.unroute(failedSourceAsset);
    const pause = await installPauseStyle(page, ".source-page-image");
    await page.locator(".source-reader-toolbar button").last().click();
    const image = page.locator(".source-page-image");
    await expect(image).toHaveAttribute("data-motion-image-state", "entering");
    expect(await readAnimation(image)).toMatchObject({ duration: "0.18s", name: "motion-stage3-image-in", playState: "paused" });
    const imageBox = await image.boundingBox();
    if (!imageBox) throw new Error("recovered SourceReader image has no geometry");
    expect(Math.abs(imageBox.width - before.width), "fallback and recovered media keep stable width").toBeLessThanOrEqual(1);
    await dispatchAnimation(image, "animationend", "motion-stage3-image-in");
    await expect(image).toHaveAttribute("data-motion-image-state", "idle");
    await image.dispatchEvent("load");
    await expect(image, "duplicate load does not replay the recovered image").toHaveAttribute("data-motion-image-state", "idle");
    await pause.evaluate((element) => element.remove());

    expect(audit.consoleErrors.every((message) => message === failedResource), "only explicitly routed image failures are acknowledged").toBe(true);
    testInfo.annotations.push({
      type: "acknowledged-source-reader-404",
      description: `${audit.consoleErrors.length} routed source/cover requests intentionally failed before SourceReader retry`
    });
    audit.consoleErrors.splice(0);
  });

  test("replaces Notes detail once, preserves master identity, and does not replay the selected same state", async ({ page }) => {
    await openNotes(page);
    const pause = await installPauseStyle(page, ".notes-detail-panel");
    const list = page.locator(".notes-list");
    const detail = page.locator(".notes-detail-panel");
    const master = await list.elementHandle();
    const firstDetail = await detail.elementHandle();
    if (!master || !firstDetail) throw new Error("Notes master/detail roots are missing");
    await expect(detail, "initial Notes detail is direct by contract").toHaveAttribute("data-motion-item-state", "idle");

    const second = list.locator("button").nth(1);
    await second.click();
    const entryName = await expectLocalEntry(detail, "selected Notes detail");
    expect(await master.evaluate((element) => element === document.querySelector(".notes-list")), "Notes master list keeps identity").toBe(true);
    expect(await firstDetail.evaluate((element) => element.isConnected), "only the previous Notes detail is replaced").toBe(false);
    await firstDetail.evaluate((element, name) => {
      element.dispatchEvent(new AnimationEvent("animationend", { animationName: name, bubbles: true }));
    }, entryName);
    await expect(detail, "stale detail completion cannot settle its replacement").toHaveAttribute("data-motion-item-state", "entering");
    await dispatchAnimation(detail, "animationend", entryName);
    await expect(detail).toHaveAttribute("data-motion-item-state", "idle");

    const selectedDetail = await detail.elementHandle();
    const selectedKey = await detail.getAttribute("data-motion-item-key");
    if (!selectedDetail || !selectedKey) throw new Error("selected Notes detail identity is missing");
    await second.click();
    await expect(second).toHaveAttribute("aria-pressed", "true");
    await expect(detail, "selecting the active note remains direct").toHaveAttribute("data-motion-item-state", "idle");
    await expect(detail).toHaveAttribute("data-motion-item-key", selectedKey);
    expect(await selectedDetail.evaluate((element) => element === document.querySelector(".notes-detail-panel")), "same note does not remount its detail").toBe(true);
    expect((await readAnimation(detail)).name).toBe("none");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await list.locator("button").nth(2).click();
    await expect(detail).toHaveAttribute("data-motion-item-state", "idle");
    expect((await readAnimation(detail)).name).toBe("none");
    await pause.evaluate((element) => element.remove());
  });

  test("keeps Community same-state direct while rebuilt local surfaces and covers receive one new DOM entry", async ({ page }) => {
    await gotoApp(page);
    const pause = await installPauseStyle(page, ".community-discovery-controls, .community-detail-overview, .community-import-success, .community-cover-image");
    await page.locator(".primary-nav .nav-item").nth(1).click();
    await expect(page.locator(".community-screen")).toBeVisible();
    await settleScreen(page);
    const discovery = page.locator(".community-discovery-controls");
    const discoveryEntryName = await expectLocalEntry(discovery, "Community discovery controls first play");
    await dispatchAnimation(discovery, "animationend", discoveryEntryName);
    await expect(discovery).toHaveAttribute("data-motion-item-state", "idle");
    const sameDiscovery = await discovery.elementHandle();
    if (!sameDiscovery) throw new Error("Community discovery controls root is missing");
    await page.locator(".primary-nav .nav-item").nth(1).click();
    expect(await sameDiscovery.evaluate((element) => element === document.querySelector(".community-discovery-controls")), "same Community navigation keeps the discovery root").toBe(true);
    await expect(discovery).toHaveAttribute("data-motion-item-state", "idle");

    await page.locator(".community-book-card").first().click();
    await expect(page.locator(".community-detail-screen")).toBeVisible();
    await settleScreen(page);
    const detail = page.locator(".community-detail-overview");
    const detailEntryName = await expectLocalEntry(detail, "Community book detail");
    await dispatchAnimation(detail, "animationend", detailEntryName);
    await expect(detail).toHaveAttribute("data-motion-item-state", "idle");
    await page.locator(".header-bar .icon-button").click();
    await expect(page.locator(".community-screen")).toBeVisible();
    await settleScreen(page);
    const rebuiltDiscoveryName = await expectLocalEntry(discovery, "rebuilt Community discovery controls");
    expect(await sameDiscovery.evaluate((element) => element.isConnected), "leaving Community detached its prior discovery root").toBe(false);
    await dispatchAnimation(discovery, "animationend", rebuiltDiscoveryName);
    await expect(discovery).toHaveAttribute("data-motion-item-state", "idle");
    const rebuiltCover = page.locator(".community-cover-image").first();
    await expect(rebuiltCover, "rebuilt successful Community image is allowed one per-DOM fade").toHaveAttribute("data-motion-image-state", "entering");
    expect(await readAnimation(rebuiltCover)).toMatchObject({ duration: "0.18s", name: "motion-stage3-image-in", playState: "paused" });
    await dispatchAnimation(rebuiltCover, "animationend", "motion-stage3-image-in");
    await expect(rebuiltCover).toHaveAttribute("data-motion-image-state", "idle");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".community-book-card").first().click();
    await expect(detail).toHaveAttribute("data-motion-item-state", "idle");
    expect((await readAnimation(detail)).name).toBe("none");
    await page.locator(".community-detail-actions .button").first().click();
    const imported = page.locator(".community-import-success");
    await expect(imported).toHaveAttribute("data-motion-item-state", "idle");
    expect((await readAnimation(imported)).name).toBe("none");
    await pause.evaluate((element) => element.remove());
  });

  test("keeps StudyPlan date and task feedback one-shot and direct for same-state or reduced updates", async ({ page }) => {
    await openStudy(page);
    const pause = await installPauseStyle(page, ".plan-date-selection-check, .timeline-task-completion, .study-plan-empty-state");
    await page.locator(".study-plan-summary button").click();
    await expect(page.locator(".study-plan-screen")).toBeVisible();
    await settleScreen(page);
    const days = page.locator(".plan-date-row button");
    const dayTwo = days.nth(1);
    await dayTwo.click();
    const selection = page.locator(".plan-date-selection-check");
    await expect(selection).toHaveAttribute("data-motion-plan-date-state", "entering");
    let motion = await readAnimation(selection);
    expect(motion).toMatchObject({ duration: "0.18s", name: "motion-plan-date-check-in", playState: "paused" });
    expect(normalizeTimingFunction(motion.timing)).toBe(curves.localEnter);
    await dispatchAnimation(selection, "animationend", "motion-plan-date-check-in");
    await expect(selection).toHaveAttribute("data-motion-plan-date-state", "idle");
    const sameSelection = await selection.elementHandle();
    if (!sameSelection) throw new Error("StudyPlan selected-date check is missing");
    await dayTwo.click();
    await expect(selection, "selecting the active plan day does not replay").toHaveAttribute("data-motion-plan-date-state", "idle");
    expect(await sameSelection.evaluate((element) => element === document.querySelector(".plan-date-selection-check")), "same plan day keeps its check node").toBe(true);
    expect((await readAnimation(selection)).name).toBe("none");

    await days.nth(4).click();
    await dispatchAnimation(selection, "animationend", "motion-plan-date-check-in");
    const task = page.locator(".timeline-item").first();
    await expect(task).toBeVisible();
    await expect(task).not.toHaveClass(/done/);
    await task.click();
    await expect(task).toHaveClass(/done/);
    const completion = task.locator(".timeline-task-completion");
    await expect(completion).toHaveAttribute("data-motion-plan-task-state", "entering");
    motion = await readAnimation(completion);
    expect(motion).toMatchObject({ duration: "0.18s", name: "motion-stage-check-in", playState: "paused" });
    await dispatchAnimation(completion, "animationend", "motion-stage-check-in");
    await expect(completion).toHaveAttribute("data-motion-plan-task-state", "idle");
    const completedNode = await completion.elementHandle();
    if (!completedNode) throw new Error("StudyPlan completion node is missing");
    await task.click();
    await expect(completion, "patching an already-done task does not replay completion").toHaveAttribute("data-motion-plan-task-state", "idle");
    expect(await completedNode.evaluate((element) => element === document.querySelector(".timeline-item.done .timeline-task-completion")), "same task keeps its completion node").toBe(true);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await days.nth(2).click();
    await expect(selection).toHaveAttribute("data-motion-plan-date-state", "idle");
    expect((await readAnimation(selection)).name).toBe("none");
    await pause.evaluate((element) => element.remove());
  });

  test("scopes Assignment feedback and Diagnosis motion to the real answer/submission identity without replay", async ({ page }) => {
    await openStudy(page);
    await page.locator('.study-tool-card[data-tool="assignment"]').first().click();
    await expect(page.locator(".assignment-screen")).toBeVisible();
    await settleScreen(page);
    await advanceAssignmentToShortAnswer(page);
    const pause = await installPauseStyle(page, ".assignment-answer-check, .diagnosis-card, .diagnosis-knowledge-progress-fill");
    const answer = page.locator(".assignment-card textarea");
    await answer.fill("Homologous chromosomes separate during the first meiotic division.");
    const answerCheck = page.locator(".assignment-answer-check");
    await expect(answerCheck).toHaveAttribute("data-motion-assignment-answer-state", "entering");
    let motion = await readAnimation(answerCheck);
    expect(motion).toMatchObject({ duration: "0.18s", name: "motion-stage-check-in", playState: "paused" });
    await dispatchAnimation(answerCheck, "animationend", "motion-stage-check-in");
    await expect(answerCheck).toHaveAttribute("data-motion-assignment-answer-state", "idle");
    const sameAnswerCheck = await answerCheck.elementHandle();
    if (!sameAnswerCheck) throw new Error("Assignment answer check is missing");
    await answer.fill("Homologous chromosomes separate during the first meiotic division, before sister chromatids.");
    await expect(answerCheck, "editing an already-present answer does not replay its presence check").toHaveAttribute("data-motion-assignment-answer-state", "idle");
    expect(await sameAnswerCheck.evaluate((element) => element === document.querySelector(".assignment-answer-check")), "same answer presence keeps its check node").toBe(true);

    await page.locator(".assignment-primary-action .button").click();
    await expect(page.locator(".diagnosis-screen")).toBeVisible();
    await settleScreen(page);
    const diagnosis = page.locator(".diagnosis-card");
    const progress = page.locator(".diagnosis-knowledge-progress-fill");
    await expect(diagnosis).toHaveAttribute("data-motion-diagnosis-state", "entering");
    await expect(progress).toHaveAttribute("data-motion-diagnosis-progress-state", "entering");
    motion = await readAnimation(diagnosis);
    expect(motion).toMatchObject({ duration: "0.18s", name: "motion-local-item-in", playState: "paused" });
    motion = await readAnimation(progress);
    expect(motion).toMatchObject({ duration: "0.2s", name: "motion-diagnosis-progress-in", playState: "paused" });
    expect(normalizeTimingFunction(motion.timing)).toBe(curves.progress);
    const submissionKey = await diagnosis.getAttribute("data-motion-diagnosis-key");
    await dispatchAnimation(progress, "animationend", "motion-diagnosis-progress-in");
    await expect(diagnosis).toHaveAttribute("data-motion-diagnosis-state", "idle");
    await expect(progress).toHaveAttribute("data-motion-diagnosis-progress-state", "idle");

    await page.locator(".diagnosis-actions .button").first().click();
    await expect(page.locator(".assignment-screen")).toBeVisible();
    await settleScreen(page);
    await advanceAssignmentToShortAnswer(page);
    await expect(answerCheck, "returning with the same non-empty answer remains direct").toHaveAttribute("data-motion-assignment-answer-state", "idle");
    await page.locator(".assignment-primary-action .button").click();
    await expect(page.locator(".diagnosis-screen")).toBeVisible();
    await settleScreen(page);
    await expect(diagnosis).toHaveAttribute("data-motion-diagnosis-key", submissionKey ?? "");
    await expect(diagnosis, "the same DemoRepository submission id is consumed and does not replay").toHaveAttribute("data-motion-diagnosis-state", "idle");
    await expect(progress).toHaveAttribute("data-motion-diagnosis-progress-state", "idle");
    expect((await readAnimation(diagnosis)).name).toBe("none");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".diagnosis-actions .button").first().click();
    await settleScreen(page);
    await advanceAssignmentToShortAnswer(page);
    await page.locator(".assignment-primary-action .button").click();
    await expect(page.locator(".diagnosis-screen")).toBeVisible();
    await settleScreen(page);
    await expect(diagnosis).toHaveAttribute("data-motion-diagnosis-state", "idle");
    expect((await readAnimation(diagnosis)).name).toBe("none");
    await pause.evaluate((element) => element.remove());
  });

  test("keeps Lesson and Profile local entries scoped to their current real screen instances", async ({ page }) => {
    test.setTimeout(50_000);
    await installInitialPauseStyle(page, ".lesson-article-header, .profile-card");
    await openLesson(page);

    const lessonTitle = page.locator(".lesson-article-header");
    await expectLocalEntry(lessonTitle, "lesson title entry");
    await dispatchAnimation(lessonTitle, "animationend", "motion-local-item-in");
    await expect(lessonTitle).toHaveAttribute("data-motion-item-state", "idle");
    const lessonNode = await lessonTitle.elementHandle();
    if (!lessonNode) throw new Error("Lesson title local-motion root is missing");

    await page.locator(".lesson-floating-complete .button").click();
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await expect(page.locator(".report-screen")).toHaveCount(0);
    await settleScreen(page);
    expect(await lessonNode.evaluate((element) => element.isConnected), "leaving Lesson detaches its old local root").toBe(false);
    await page.locator(".primary-nav .nav-item").nth(3).click();
    await expect(page.locator(".profile-screen")).toBeVisible();
    await settleScreen(page);
    const profile = page.locator(".profile-card");
    await expectLocalEntry(profile, "profile summary entry");
    await dispatchAnimation(profile, "animationend", "motion-local-item-in");
    await expect(profile).toHaveAttribute("data-motion-item-state", "idle");
    const profileNode = await profile.elementHandle();
    if (!profileNode) throw new Error("Profile local-motion root is missing");
    await page.locator(".primary-nav .nav-item").nth(3).click();
    await expect(profile, "same active Profile navigation does not replay the consumed key").toHaveAttribute("data-motion-item-state", "idle");
    expect(await profileNode.evaluate((element) => element === document.querySelector(".profile-card")), "same Profile instance keeps its local root").toBe(true);
    expect((await readAnimation(profile)).name).toBe("none");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".primary-nav .nav-item").first().click();
    await settleScreen(page);
    await page.locator(".primary-nav .nav-item").nth(3).click();
    await expect(page.locator(".profile-screen")).toBeVisible();
    await settleScreen(page);
    const reducedProfile = page.locator(".profile-card");
    await expect(reducedProfile, "reconstructed Profile is direct under reduced motion").toHaveAttribute("data-motion-item-state", "idle");
    expect((await readAnimation(reducedProfile)).name).toBe("none");
  });
});

test.describe("5. local collapse, filter, and flashcard state motion", () => {
  test("uses a 200ms Local State curve for collapse and settles directly under reduced motion", async ({ page }) => {
    await openStudy(page);
    const toggle = page.locator(".study-section-toggle").first();
    const region = page.locator(".motion-collapsible.study-section-region").first();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await toggle.click();
    await expect(region).toHaveAttribute("data-motion-collapsible", "collapsed");
    let transition = await readTransition(region);
    expect(transition.duration.split(",").every((value) => value.trim() === "0.2s")).toBe(true);
    expect(normalizeTimingFunction(transition.timing)).toBe(curves.localExit);
    await expect(region).toHaveAttribute("inert", "");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await toggle.click();
    await expect(region).toHaveAttribute("data-motion-collapsible", "expanded");
    transition = await readTransition(region);
    expect(transition.duration.split(",").every((value) => value.trim() === "0s")).toBe(true);
  });

  test("locks flashcard interaction during a 200ms 3D/crossfade response and removes geometry for reduced motion", async ({ page }) => {
    await openFlashcards(page);
    await installPauseStyle(page, ".memory-card-answer-3d, .memory-card-answer-face");
    const root = page.locator(".memory-card-answer-motion");
    const card = root.locator(".memory-card-answer-3d");
    const reveal = page.locator(".memory-reveal");
    await reveal.click();
    await expect(root).toHaveAttribute("data-motion-flash-state", "flipping");
    await expect(reveal).toBeDisabled();
    const viewport = page.viewportSize();
    const shortLandscape = Boolean(viewport && viewport.height < 600 && viewport.width > viewport.height);
    const animated = shortLandscape ? root.locator(".memory-card-answer-face-back") : card;
    const expectedName = shortLandscape ? "motion-flashcard-crossfade-in" : "motion-flashcard-flip-to-back";
    const motion = await readAnimation(animated);
    expect(motion).toMatchObject({ duration: "0.2s", name: expectedName, playState: "paused" });
    expect(normalizeTimingFunction(motion.timing)).toBe(curves.localState);
    await dispatchAnimation(animated, "animationend", expectedName);
    await expect(root).toHaveAttribute("data-motion-flash-state", "idle");
    await expect(reveal).toBeEnabled();

    await page.emulateMedia({ reducedMotion: "reduce" });
    await reveal.click();
    await expect(root).toHaveAttribute("data-motion-flash-state", "idle");
    expect((await readAnimation(card)).name).toBe("none");
    expect(await root.evaluate((element) => getComputedStyle(element).perspective)).toBe("none");
  });

  test("uses a 200ms Local State indicator for mistake filtering and reaches the real empty state", async ({ page }) => {
    await openMistakes(page);
    const group = page.locator(".mistake-filter-group");
    const indicator = group.locator(".motion-sliding-filter-indicator");
    const options = group.locator(".motion-sliding-filter-button");
    const initialTransform = await indicator.evaluate((element) => getComputedStyle(element).transform);
    const transition = await readTransition(indicator);
    expect(transition.duration.split(",").every((value) => value.trim() === "0.2s")).toBe(true);
    expect(normalizeTimingFunction(transition.timing)).toBe(`${curves.localState},${curves.localState}`);
    await options.last().click();
    await expect(group).toHaveAttribute("data-motion-selection-state", "idle");
    await expect(options.last()).toHaveAttribute("aria-pressed", "true");
    expect(await indicator.evaluate((element) => getComputedStyle(element).transform)).not.toBe(initialTransform);
    await expect(page.locator(".mistake-workspace")).toHaveAttribute("data-mistake-list-empty", "true");
    await expect(page.locator(".mistake-state-card")).toBeVisible();

    await page.emulateMedia({ reducedMotion: "reduce" });
    await options.first().click();
    await expect(group).toHaveAttribute("data-motion-selection-state", "idle");
    await expect(page.locator(".mistake-detail-card")).toBeVisible();
  });
});

test.describe("6. CSS and settled-runtime acceptance", () => {
  test("uses no transition-all, restricts keyframes, and leaves no permanent compositor residue", async ({ page }) => {
    await gotoApp(page);
    const audit = await page.evaluate(() => {
      const result = { keyframes: [] as Array<{ name: string; properties: string[] }>, transitionAll: [] as string[], willChange: [] as string[] };
      const scan = (rules: CSSRuleList, source: string) => {
        Array.from(rules).forEach((rule, index) => {
          const location = `${source}:${index}`;
          if (rule.type === CSSRule.KEYFRAMES_RULE) {
            const keyframes = rule as CSSKeyframesRule;
            const properties = new Set<string>();
            for (const frame of Array.from(keyframes.cssRules)) {
              for (let i = 0; i < frame.style.length; i += 1) properties.add(frame.style.item(i));
            }
            result.keyframes.push({ name: keyframes.name, properties: [...properties] });
          }
          if (rule.type === CSSRule.STYLE_RULE) {
            const styleRule = rule as CSSStyleRule;
            const transitionProperty = styleRule.style.transitionProperty;
            const transition = styleRule.style.transition;
            if (/(^|,)\s*all\s*(,|$)/.test(transitionProperty) || /(^|\s)all(\s|,|$)/.test(transition)) result.transitionAll.push(location);
            const willChange = styleRule.style.willChange.trim();
            const transient = /\[data-motion-state="(?:transitioning|entering|closing)"\]/.test(styleRule.selectorText);
            if (willChange && willChange !== "auto" && !transient) result.willChange.push(`${location}:${willChange}`);
          }
          const nested = (rule as CSSRule & { cssRules?: CSSRuleList }).cssRules;
          if (nested) scan(nested, location);
        });
      };
      Array.from(document.styleSheets).forEach((sheet, index) => {
        try { scan(sheet.cssRules, sheet.href ?? `inline-${index}`); } catch { /* local app sheets are readable */ }
      });
      return result;
    });
    const pathKeyframes = new Set(["motion-course-ready-check-path", "motion-checkbox-check-in"]);
    const sharedMorphs = new Set(["motion-dialog-ai-shared-in", "motion-dialog-ai-shared-out"]);
    const sharedProperties = new Set([
      "background-color", "border-bottom-color", "border-bottom-left-radius", "border-bottom-right-radius",
      "border-left-color", "border-right-color", "border-top-color", "border-top-left-radius", "border-top-right-radius"
    ]);
    const unsupported = audit.keyframes.filter((entry) => entry.properties.some((property) => (
      property !== "opacity" && property !== "transform"
      && !(property === "stroke-dashoffset" && pathKeyframes.has(entry.name))
      && !(sharedMorphs.has(entry.name) && sharedProperties.has(property))
    )));
    expect(unsupported).toEqual([]);
    expect(audit.transitionAll).toEqual([]);
    expect(audit.willChange).toEqual([]);

    const residue = await page.evaluate(() => Array.from(document.querySelectorAll<HTMLElement>(
      ".motion-screen-surface, .primary-nav, .primary-nav .nav-item, .ai-orb, [data-motion-item-state], [data-motion-image-state]"
    )).map((element) => ({
      animation: getComputedStyle(element).animationName,
      className: String(element.className),
      willChange: getComputedStyle(element).willChange
    })).filter((entry) => entry.willChange !== "auto" && entry.willChange !== ""));
    expect(residue).toEqual([]);
    expect(await page.locator(".motion-screen-surface").evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  });
});
