import { expect, test as base, type Locator, type Page } from "playwright/test";
import { getResponsiveProject, type CssViewport } from "./fixtures/viewports";
import { installVisualViewportShim, setVisualViewport } from "./fixtures/visual-viewport";

type RuntimeAudit = {
  consoleErrors: string[];
  forbiddenRequests: string[];
  pageErrors: string[];
};

const test = base.extend<{ audit: RuntimeAudit }>({
  audit: async ({ page, baseURL }, use) => {
    const audit: RuntimeAudit = { consoleErrors: [], forbiddenRequests: [], pageErrors: [] };
    const origin = new URL(baseURL ?? "http://127.0.0.1:4173").origin;
    page.on("console", (message) => {
      if (message.type() === "error") audit.consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => audit.pageErrors.push(error.message));
    page.on("request", (request) => {
      const url = new URL(request.url());
      if ((url.protocol === "http:" || url.protocol === "https:") && (url.origin !== origin || url.pathname.startsWith("/api/"))) {
        audit.forbiddenRequests.push(`${request.method()} ${request.url()}`);
      }
    });
    await use(audit);
    expect(audit.forbiddenRequests, "responsive flows stay on DemoRepository without HTTP APIs").toEqual([]);
    expect(audit.consoleErrors, "responsive flows emit no console errors").toEqual([]);
    expect(audit.pageErrors, "responsive flows emit no page errors").toEqual([]);
  }
});

test.use({
  colorScheme: "light",
  locale: "zh-CN",
  reducedMotion: "no-preference",
  timezoneId: "Asia/Hong_Kong"
});

function expectedDeviceLayout(viewport: CssViewport) {
  return viewport.width >= 768 && viewport.height >= 600 ? "pad" : "phone";
}

async function dispatchCurrentAnimation(page: Page) {
  const current = page.locator('.motion-screen-surface[data-motion-surface="current"]');
  await current.evaluate((element) => {
    const name = getComputedStyle(element).animationName.split(",")[0].trim();
    element.dispatchEvent(new AnimationEvent("animationend", { animationName: name, bubbles: true }));
  });
}

async function settleScreen(page: Page) {
  const root = page.locator(".motion-screen-transition");
  if (await root.getAttribute("data-motion-state") === "transitioning") await dispatchCurrentAnimation(page);
  await expect(root).toHaveAttribute("data-motion-state", "idle");
  await expect(root.locator(':scope > [data-motion-surface="previous"]')).toHaveCount(0);
}

async function gotoApp(page: Page) {
  await page.goto("/?embedded=device-preview");
  await expect(page.locator(".home-dashboard")).toBeVisible();
  await settleScreen(page);
}

async function openLibrary(page: Page) {
  await page.locator(".home-book-picker-heading button").click();
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

async function expectNoShellOverflow(page: Page, label: string) {
  const widths = await page.evaluate(() => {
    const shell = document.querySelector<HTMLElement>(".app-shell");
    if (!shell) throw new Error("app shell is missing");
    return {
      appClient: shell.clientWidth,
      appScroll: shell.scrollWidth,
      documentClient: document.documentElement.clientWidth,
      documentScroll: document.documentElement.scrollWidth
    };
  });
  expect(widths.documentScroll, `${label}: document has no horizontal overflow`).toBeLessThanOrEqual(widths.documentClient + 1);
  expect(widths.appScroll, `${label}: app shell has no horizontal overflow`).toBeLessThanOrEqual(widths.appClient + 1);
}

async function expectShellMode(page: Page, viewport: CssViewport, label: string) {
  const shell = page.locator(".app-shell");
  await expect(shell, `${label}: app shell is visible`).toBeVisible();
  await expect(shell, `${label}: layout media query matches viewport`).toHaveAttribute("data-device-layout", expectedDeviceLayout(viewport));
  const nav = page.locator(".primary-nav");
  await expect(nav, `${label}: primary navigation remains visible`).toBeVisible();
  await expect(nav.locator(".nav-item"), `${label}: four navigation destinations remain present`).toHaveCount(4);
  const bounds = await nav.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { height: rect.height, width: rect.width };
  });
  expect(bounds.height > bounds.width ? "pad" : "phone", `${label}: navigation orientation follows the layout mode`).toBe(expectedDeviceLayout(viewport));
  await expectNoShellOverflow(page, label);
}

async function expectInsideShell(page: Page, locator: Locator, label: string) {
  const result = await locator.evaluate((element) => {
    const shell = document.querySelector<HTMLElement>(".app-shell");
    if (!shell) return null;
    const outer = shell.getBoundingClientRect();
    const inner = element.getBoundingClientRect();
    return {
      bottom: inner.bottom <= outer.bottom + 1,
      left: inner.left >= outer.left - 1,
      right: inner.right <= outer.right + 1,
      top: inner.top >= outer.top - 1
    };
  });
  expect(result, `${label}: surface is inside app shell`).toEqual({ bottom: true, left: true, right: true, top: true });
}

async function expectHorizontallyInsideShell(locator: Locator, label: string) {
  const result = await locator.evaluate((element) => {
    const shell = document.querySelector<HTMLElement>(".app-shell");
    if (!shell) return null;
    const outer = shell.getBoundingClientRect();
    const inner = element.getBoundingClientRect();
    return {
      left: inner.left >= outer.left - 1,
      right: inner.right <= outer.right + 1
    };
  });
  expect(result, `${label}: content stays inside the shell horizontally`).toEqual({ left: true, right: true });
}

async function expectStrictHorizontalBounds(page: Page, selectors: string[], label: string) {
  const shell = page.locator(".app-shell");
  await expect(shell, `${label}: app shell exists`).toBeVisible();
  const shellRect = await shell.boundingBox();
  expect(shellRect, `${label}: app shell has measurable geometry`).not.toBeNull();
  if (!shellRect) return;

  for (const selector of selectors) {
    const matching = page.locator(selector);
    await expect(matching.first(), `${label}: ${selector} has a first attached element`).toBeAttached();
    const elements = await matching.all();
    expect(elements.length, `${label}: ${selector} has at least one element`).toBeGreaterThan(0);
    for (const [index, element] of elements.entries()) {
      await expect(element, `${label}: ${selector}[${index}] is visible`).toBeVisible();
      const rect = await element.boundingBox();
      expect(rect, `${label}: ${selector}[${index}] has measurable geometry`).not.toBeNull();
      if (!rect) continue;
      expect(rect.x, `${label}: ${selector}[${index}] does not overflow left`).toBeGreaterThanOrEqual(shellRect.x - 1);
      expect(rect.x + rect.width, `${label}: ${selector}[${index}] does not overflow right`).toBeLessThanOrEqual(shellRect.x + shellRect.width + 1);
    }
  }
}

async function expectReachable(locator: Locator, label: string) {
  await expect(locator, `${label}: control is visible`).toBeVisible();
  await locator.scrollIntoViewIfNeeded();
  await locator.click({ trial: true });
  const result = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      height: rect.height,
      width: rect.width
    };
  });
  expect(result.height + 0.001, `${label}: touch target height`).toBeGreaterThanOrEqual(44);
  expect(result.width + 0.001, `${label}: touch target width`).toBeGreaterThanOrEqual(44);
}

async function readStudyBookBarGeometry(page: Page) {
  return page.locator(".study-book-bar").evaluate((bar) => {
    const shell = document.querySelector<HTMLElement>(".app-shell");
    const add = bar.querySelector<HTMLElement>(".study-add-button");
    if (!shell || !add) return null;
    const shellRect = shell.getBoundingClientRect();
    const barRect = bar.getBoundingClientRect();
    const addRect = add.getBoundingClientRect();
    const hit = document.elementFromPoint(addRect.left + addRect.width / 2, addRect.top + addRect.height / 2);
    return {
      addCenterReachable: Boolean(hit && (hit === add || add.contains(hit))),
      addLeft: addRect.left,
      addOverhang: Math.max(0, addRect.right - shellRect.right),
      addRight: addRect.right,
      barLeft: barRect.left,
      barRight: barRect.right,
      overhang: Math.max(0, barRect.right - shellRect.right),
      shellLeft: shellRect.left,
      shellRight: shellRect.right,
      viewportWidth: window.innerWidth
    };
  });
}

async function expandCurrentStudySection(page: Page) {
  const chapter = page.locator("#study-chapter-c2-toggle");
  if (await chapter.getAttribute("aria-expanded") !== "true") {
    await chapter.focus();
    await chapter.press("Enter");
    await expect(chapter).toHaveAttribute("aria-expanded", "true");
  }
  await expect(page.locator("#study-chapter-c2-content")).toBeVisible();
  const section = page.locator("#study-section-c2s1-toggle");
  if (await section.getAttribute("aria-expanded") !== "true") {
    await section.focus();
    await section.press("Enter");
    await expect(section).toHaveAttribute("aria-expanded", "true");
  }
  await expect(page.locator("#study-section-c2s1-content")).toBeVisible();
}

async function openSourceReader(page: Page) {
  await expandCurrentStudySection(page);
  const entry = page.locator("#study-section-c2s1-content .study-enter-button");
  await entry.focus();
  await expect(entry).toBeFocused();
  await entry.press("Enter");
  await expect(page.locator(".lesson-screen")).toBeVisible({ timeout: 10_000 });
  await settleScreen(page);
  await page.locator(".lesson-source-link").first().click();
  await expect(page.locator(".source-reader-screen")).toBeVisible({ timeout: 10_000 });
  await settleScreen(page);
}

async function openLesson(page: Page) {
  await expandCurrentStudySection(page);
  const entry = page.locator("#study-section-c2s1-content .study-enter-button");
  await entry.focus();
  await expect(entry).toBeFocused();
  await entry.press("Enter");
  await expect(page.locator(".lesson-screen")).toBeVisible();
  await settleScreen(page);
}

async function expectCurrentScreenGeometry(page: Page, screenSelector: string, keySelectors: string[], label: string) {
  await expectStrictHorizontalBounds(page, [
    ".screen-content",
    ".motion-screen-transition",
    ".motion-screen-surface[data-motion-surface='current']",
    screenSelector,
    ...keySelectors
  ], label);
  await expectNoShellOverflow(page, label);
}

async function activateUnobscuredWithKeyboard(control: Locator, label: string) {
  await expect(control, `${label}: control is visible`).toBeVisible();
  await control.evaluate((element) => {
    const stickySummary = element.closest<HTMLElement>(".study-plan-summary");
    const screen = element.closest<HTMLElement>(".screen-content");
    if (stickySummary && screen && getComputedStyle(stickySummary).position === "sticky") {
      screen.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    element.scrollIntoView({ block: "center", inline: "nearest" });
  });
  await expect.poll(async () => control.evaluate(async (element) => {
    const before = element.getBoundingClientRect();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    const after = element.getBoundingClientRect();
    const centerX = after.left + after.width / 2;
    const centerY = after.top + after.height / 2;
    const hit = document.elementFromPoint(centerX, centerY);
    return {
      centerReachable: Boolean(hit && (hit === element || element.contains(hit))),
      scrollStable: Math.abs(after.left - before.left) < 0.01 && Math.abs(after.top - before.top) < 0.01
    };
  }), {
    message: `${label}: scroll settles without sticky or sibling obstruction`
  }).toEqual({ centerReachable: true, scrollStable: true });
  await control.focus();
  await expect(control, `${label}: keyboard focus succeeds`).toBeFocused();
  await control.press("Enter");
}

async function expectAllControlsReachableInVisualViewport(page: Page, selector: string, label: string) {
  const matching = page.locator(selector);
  await expect(matching.first(), `${label}: first real control is attached`).toBeAttached();
  const controls = await matching.all();
  expect(controls.length, `${label}: at least one real control exists`).toBeGreaterThan(0);
  for (const [index, control] of controls.entries()) {
    await control.evaluate((element) => element.scrollIntoView({ block: "center", inline: "nearest" }));
    await expect(control, `${label}[${index}] remains visible`).toBeVisible();
    await expect.poll(async () => control.evaluate(async (element) => {
      const before = element.getBoundingClientRect();
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const rect = element.getBoundingClientRect();
      const visualTop = window.visualViewport?.offsetTop ?? 0;
      const visualBottom = visualTop + (window.visualViewport?.height ?? window.innerHeight);
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const hit = document.elementFromPoint(centerX, centerY);
      return {
        bottomInsideVisual: rect.bottom <= visualBottom,
        centerInsideVisual: centerY >= visualTop - 1 && centerY <= visualBottom + 1,
        centerReachable: Boolean(hit && (hit === element || element.contains(hit))),
        scrollStable: Math.abs(rect.left - before.left) < 0.01 && Math.abs(rect.top - before.top) < 0.01,
        topInsideVisual: rect.top >= visualTop
      };
    }), {
      message: `${label}[${index}] smooth scroll settles with its full rectangle reachable in the visual viewport`
    }).toEqual({
      bottomInsideVisual: true,
      centerInsideVisual: true,
      centerReachable: true,
      scrollStable: true,
      topInsideVisual: true
    });
    const result = await control.evaluate((element) => {
      const shell = document.querySelector<HTMLElement>(".app-shell");
      if (!shell) return null;
      const rect = element.getBoundingClientRect();
      const shellRect = shell.getBoundingClientRect();
      const visualTop = window.visualViewport?.offsetTop ?? 0;
      const visualBottom = visualTop + (window.visualViewport?.height ?? window.innerHeight);
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const hit = document.elementFromPoint(centerX, centerY);
      return {
        bottomInsideVisual: rect.bottom <= visualBottom,
        centerInsideVisual: centerY >= visualTop - 1 && centerY <= visualBottom + 1,
        centerReachable: Boolean(hit && (hit === element || element.contains(hit))),
        height: rect.height,
        leftInsideShell: rect.left >= shellRect.left - 1,
        rightInsideShell: rect.right <= shellRect.right + 1,
        topInsideVisual: rect.top >= visualTop,
        width: rect.width
      };
    });
    expect(result, `${label}[${index}] has geometry`).not.toBeNull();
    if (!result) continue;
    expect(result.leftInsideShell, `${label}[${index}] stays inside shell left`).toBe(true);
    expect(result.rightInsideShell, `${label}[${index}] stays inside shell right`).toBe(true);
    expect(result.topInsideVisual, `${label}[${index}] full rectangle stays below the visual viewport top`).toBe(true);
    expect(result.bottomInsideVisual, `${label}[${index}] full rectangle stays above the visual viewport bottom`).toBe(true);
    expect(result.centerInsideVisual, `${label}[${index}] actionable center stays inside the visual viewport`).toBe(true);
    expect(result.centerReachable, `${label}[${index}] center is not obscured`).toBe(true);
    expect(result.height, `${label}[${index}] touch target height`).toBeGreaterThanOrEqual(44);
    expect(result.width, `${label}[${index}] touch target width`).toBeGreaterThanOrEqual(44);
  }
}

test.describe("current DemoRepository responsive matrix", () => {
  test("keeps home, navigation, safe chrome, and primary actions usable in both mapped viewports", async ({ page }, testInfo) => {
    const project = getResponsiveProject(testInfo.project.name);
    await gotoApp(page);
    await expectShellMode(page, project.initialViewport, `${project.name} initial`);
    await expectReachable(page.locator(".home-primary-action"), `${project.name} initial primary action`);
    await expectReachable(page.locator(".primary-nav .nav-item").first(), `${project.name} initial navigation`);

    await page.setViewportSize(project.pairedViewport);
    await expectShellMode(page, project.pairedViewport, `${project.name} paired`);
    await expectReachable(page.locator(".home-import-course-action"), `${project.name} paired upload tool`);
    const phoneChrome = page.locator("[data-testid='ios-status-bar']");
    if (expectedDeviceLayout(project.pairedViewport) === "pad" || project.pairedViewport.height < 600) {
      await expect(phoneChrome).toBeHidden();
    } else {
      await expect(phoneChrome).toBeVisible();
    }
  });

  test("keeps Library cards usable and records the bounded current Study book-bar geometry", async ({ page }, testInfo) => {
    const project = getResponsiveProject(testInfo.project.name);
    await gotoApp(page);
    await openLibrary(page);
    const card = page.locator(".library-course-grid .course-space-card").first();
    await expect(card).toBeVisible();
    await expectHorizontallyInsideShell(card, `${project.name} library card`);
    await expectReachable(card.locator(".button-row .button"), `${project.name} library entry`);
    await card.locator(".button-row .button").click();
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await settleScreen(page);

    const initialGeometry = await readStudyBookBarGeometry(page);
    if (!initialGeometry) throw new Error("initial Study book-bar geometry is missing");
    await page.setViewportSize(project.pairedViewport);
    const pairedGeometry = await readStudyBookBarGeometry(page);
    if (!pairedGeometry) throw new Error("paired Study book-bar geometry is missing");
    await testInfo.attach("study-book-bar-geometry.json", {
      body: Buffer.from(JSON.stringify({
        project: project.name,
        initial: { viewport: project.initialViewport, ...initialGeometry },
        paired: { viewport: project.pairedViewport, ...pairedGeometry }
      }, null, 2)),
      contentType: "application/json"
    });
    for (const [mapping, viewport, geometry] of [
      ["initial", project.initialViewport, initialGeometry],
      ["paired", project.pairedViewport, pairedGeometry]
    ] as const) {
      testInfo.annotations.push({
        type: "study-book-bar-geometry",
        description: `${mapping} shell=${geometry.shellRight.toFixed(2)} bar=${geometry.barRight.toFixed(2)} add=${geometry.addRight.toFixed(2)} overhang=${geometry.overhang.toFixed(2)} at ${viewport.width}x${viewport.height}`
      });
      if (geometry.overhang > 1) {
        const isKnownIpadStudyBookBar = project.name === "ipad-pro-11"
          && viewport.width === 834
          && (viewport.height === 1194 || viewport.height === 1210);
        expect(isKnownIpadStudyBookBar, `${project.name} ${mapping}: no generic overflow allowance outside the exact iPad Study exception`).toBe(true);
        testInfo.annotations.push({
          type: "known-layout-overhang",
          description: `${mapping} exact iPad-only .study-book-bar/.study-add-button exception: shell=${geometry.shellRight.toFixed(2)} bar=${geometry.barRight.toFixed(2)} add=${geometry.addRight.toFixed(2)} overhang=${geometry.overhang.toFixed(2)} at ${viewport.width}x${viewport.height}`
        });
        expect(geometry.overhang, `${project.name} ${mapping}: known .study-book-bar overhang remains near the audited 9.05px`).toBeGreaterThanOrEqual(8);
        expect(geometry.overhang, `${project.name} ${mapping}: known .study-book-bar overhang remains tightly bounded`).toBeLessThanOrEqual(10);
        expect(geometry.addOverhang, `${project.name} ${mapping}: only the bar's add control shares the exact exception`).toBeGreaterThanOrEqual(8);
        expect(geometry.addOverhang, `${project.name} ${mapping}: add control exception remains tightly bounded`).toBeLessThanOrEqual(10);
      } else {
        expect(geometry.overhang, `${project.name} ${mapping}: Study book bar has strict no-overflow outside the named exception`).toBeLessThanOrEqual(1);
        expect(geometry.addOverhang, `${project.name} ${mapping}: Study add control has strict no-overflow outside the named exception`).toBeLessThanOrEqual(1);
      }
      expect(geometry.barLeft, `${project.name} ${mapping}: Study starts inside the shell`).toBeGreaterThanOrEqual(geometry.shellLeft - 1);
      expect(geometry.addCenterReachable, `${project.name} ${mapping}: edged add control remains center-reachable`).toBe(true);
    }
    await expectStrictHorizontalBounds(page, [
      ".screen-content",
      ".motion-screen-transition",
      ".motion-screen-surface[data-motion-surface='current']",
      ".book-course-screen",
      ".study-book-switch",
      ".study-plan-summary button",
      ".study-section-toggle",
      ".study-tool-card"
    ], `${project.name} paired strict Study surfaces and controls`);
    await expectNoShellOverflow(page, `${project.name} paired Study`);
  });

  test("keeps collapse, c2s1 flashcards, and their touch controls usable after viewport pairing", async ({ page }, testInfo) => {
    const project = getResponsiveProject(testInfo.project.name);
    await openStudy(page);
    const chapter = page.locator("#study-chapter-c2-toggle");
    if (await chapter.getAttribute("aria-expanded") !== "true") {
      await chapter.focus();
      await expect(chapter).toBeFocused();
      await chapter.press("Enter");
      await expect(chapter).toHaveAttribute("aria-expanded", "true");
    }
    const section = page.locator("#study-section-c2s1-toggle");
    if (await section.getAttribute("aria-expanded") !== "true") {
      await section.focus();
      await expect(section).toBeFocused();
      await section.press("Enter");
      await expect(section).toHaveAttribute("aria-expanded", "true");
    }
    await expectReachable(section, `${project.name} chapter section toggle`);
    await page.setViewportSize(project.pairedViewport);
    await expectShellMode(page, project.pairedViewport, `${project.name} paired Study tools`);
    const flashcardTool = page.locator('#study-section-c2s1-content .study-tool-card[data-tool="flashcards"]');
    await expectReachable(flashcardTool, `${project.name} flashcard tool`);
    await flashcardTool.focus();
    await expect(flashcardTool).toBeFocused();
    await flashcardTool.press("Enter");
    await expect(page.locator(".flashcard-screen")).toBeVisible();
    await settleScreen(page);
    await expectHorizontallyInsideShell(page.locator(".memory-card"), `${project.name} flashcard`);
    await expectReachable(page.locator(".memory-reveal"), `${project.name} flashcard reveal`);
    await page.locator(".memory-reveal").click();
    await expect(page.locator(".memory-reveal")).toHaveAttribute("aria-pressed", "true");
    await expectNoShellOverflow(page, `${project.name} paired flashcard`);
  });

  test("keeps AI and ActionSheet surfaces inside the visual app viewport in both orientations", async ({ page }, testInfo) => {
    const project = getResponsiveProject(testInfo.project.name);
    await gotoApp(page);
    await page.locator(".ai-orb").click();
    const ai = page.locator(".ai-overlay");
    await expect(ai).toHaveAttribute("data-motion-state", "idle");
    await expectInsideShell(page, ai, `${project.name} initial AI`);
    await ai.locator(".ai-close").click();
    await expect(ai).toHaveCount(0);

    await openStudy(page);
    await page.setViewportSize(project.pairedViewport);
    await page.locator(".study-book-switch").click();
    const sheet = page.locator(".sheet");
    await expect(sheet).toHaveAttribute("data-motion-state", "idle");
    await expectInsideShell(page, sheet, `${project.name} paired ActionSheet`);
    await expectReachable(sheet.locator(".sheet-close"), `${project.name} paired sheet close`);
    await sheet.locator(".sheet-close").click();
    await expect(sheet).toHaveCount(0);
    await expectNoShellOverflow(page, `${project.name} overlay cleanup`);
  });

  test("keeps every current learning destination reachable with strict document, shell, screen, content, and control bounds", async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    const project = getResponsiveProject(testInfo.project.name);
    await openStudy(page);
    await page.setViewportSize(project.pairedViewport);
    await expectShellMode(page, project.pairedViewport, `${project.name} paired current destinations`);

    await openSourceReader(page);
    await expectCurrentScreenGeometry(page, ".source-reader-screen", [
      ".source-reader-toolbar button",
      ".source-page-frame",
      ".source-reader-actions .button"
    ], `${project.name} SourceReader`);
    await page.locator(".header-bar .icon-button").click();
    await expect(page.locator(".lesson-screen")).toBeVisible();
    await settleScreen(page);
    await page.locator(".header-bar .icon-button").click();
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await settleScreen(page);

    await page.locator(".screen-content").evaluate((element) => element.scrollTo({ top: 0, behavior: "instant" }));
    await expect(page.locator(".study-plan-summary")).toHaveAttribute("data-plan-state", "expanded");
    const studyPlanEntry = page.locator(".study-plan-summary button");
    await activateUnobscuredWithKeyboard(studyPlanEntry, `${project.name} StudyPlan entry`);
    await expect(page.locator(".study-plan-screen")).toBeVisible();
    await settleScreen(page);
    await expectCurrentScreenGeometry(page, ".study-plan-screen", [
      ".plan-date-row button",
      ".study-plan-tasks",
      ".timeline-item"
    ], `${project.name} StudyPlan`);
    await page.locator(".header-bar .icon-button").click();
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await settleScreen(page);

    await expandCurrentStudySection(page);
    const assignmentTool = page.locator('#study-section-c2s1-content .study-tool-card[data-tool="assignment"]');
    await assignmentTool.focus();
    await expect(assignmentTool).toBeFocused();
    await assignmentTool.press("Enter");
    await expect(page.locator(".assignment-screen")).toBeVisible();
    await settleScreen(page);
    await advanceAssignmentToShortAnswer(page);
    await expectCurrentScreenGeometry(page, ".assignment-screen", [
      ".assignment-workspace",
      ".assignment-card",
      ".assignment-card textarea",
      ".assignment-primary-action .button"
    ], `${project.name} Assignment`);
    await page.locator(".assignment-card textarea").fill("Homologous chromosomes separate during meiosis I.");
    await page.locator(".assignment-primary-action .button").click();
    await expect(page.locator(".diagnosis-screen")).toBeVisible();
    await settleScreen(page);
    await expectCurrentScreenGeometry(page, ".diagnosis-screen", [
      ".diagnosis-workspace",
      ".diagnosis-card",
      ".diagnosis-knowledge-progress",
      ".diagnosis-actions .button"
    ], `${project.name} Diagnosis`);
    await page.locator(".header-bar .icon-button").click();
    await expect(page.locator(".assignment-screen")).toBeVisible();
    await settleScreen(page);
    await page.locator(".header-bar .icon-button").click();
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await settleScreen(page);

    await openLesson(page);
    await expectCurrentScreenGeometry(page, ".lesson-screen", [
      ".lesson-layout",
      ".lesson-reading-column",
      ".lesson-knowledge-section",
      ".lesson-inline-figure",
      ".lesson-source-link",
      ".concept-card-grid button",
      ".lesson-floating-complete .button"
    ], `${project.name} Lesson article, concepts, and fixed completion action`);
    await page.locator(".lesson-floating-complete .button").click();
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await expect(page.locator(".report-screen")).toHaveCount(0);
    await settleScreen(page);
    await page.locator(".primary-nav .nav-item").nth(3).click();
    await expect(page.locator(".profile-screen")).toBeVisible();
    await settleScreen(page);
    await expectCurrentScreenGeometry(page, ".profile-screen", [
      ".profile-workspace",
      ".profile-card",
      ".profile-settings-list .settings-row"
    ], `${project.name} Profile`);
    await page.locator(".primary-nav .nav-item").nth(1).click();
    await expect(page.locator(".community-screen")).toBeVisible();
    await settleScreen(page);
    await expectCurrentScreenGeometry(page, ".community-screen", [
      ".community-discovery-controls",
      ".community-grid",
      ".community-book-card"
    ], `${project.name} Community`);
  });

  test("keeps every Lesson article control and fixed completion action reachable in paired and shrunken visual viewports", async ({ page }, testInfo) => {
    test.setTimeout(45_000);
    const project = getResponsiveProject(testInfo.project.name);
    await installVisualViewportShim(page);
    await openStudy(page);
    await page.setViewportSize(project.pairedViewport);
    await setVisualViewport(page, { height: project.pairedViewport.height, offsetTop: 0 });
    await openLesson(page);
    const repeatedLessonSelectors = [
      ".lesson-source-link",
      ".concept-card-grid button",
      ".lesson-inline-figure",
      ".lesson-floating-complete .button"
    ];
    await expectCurrentScreenGeometry(page, ".lesson-screen", [
      ".lesson-layout",
      ".lesson-reading-column",
      ...repeatedLessonSelectors
    ], `${project.name} Lesson paired viewport full-element geometry`);
    await expectAllControlsReachableInVisualViewport(
      page,
      ".lesson-source-link, .concept-card-grid button, .lesson-floating-complete .button",
      `${project.name} Lesson paired visual viewport controls`
    );

    const shortLandscape = project.name === "small-phone-short-landscape";
    const offsetTop = Math.min(shortLandscape ? 32 : 24, Math.max(0, project.pairedViewport.height - 1));
    const shrunkenHeight = project.pairedViewport.height - offsetTop;
    await setVisualViewport(page, { height: shrunkenHeight, offsetTop });
    testInfo.annotations.push({
      type: "lesson-visual-viewport",
      description: `${project.name} physical=${project.pairedViewport.width}x${project.pairedViewport.height} visual=${shrunkenHeight}+${offsetTop}; shortLandscape=${shortLandscape}`
    });
    await expectCurrentScreenGeometry(page, ".lesson-screen", [
      ".lesson-layout",
      ".lesson-reading-column",
      ...repeatedLessonSelectors
    ], `${project.name} Lesson shrunken visual viewport full-element geometry`);
    await expectAllControlsReachableInVisualViewport(
      page,
      ".lesson-source-link, .concept-card-grid button, .lesson-floating-complete .button",
      `${project.name} Lesson shrunken visual viewport controls`
    );
    await expectNoShellOverflow(page, `${project.name} Lesson visual viewport cleanup`);
  });

  test("keeps upload error/selection status readable and direct under runtime reduced motion", async ({ page }, testInfo) => {
    const project = getResponsiveProject(testInfo.project.name);
    await gotoApp(page);
    await page.locator(".home-import-course-action").click();
    await expect(page.locator(".upload-flow-screen")).toBeVisible();
    await settleScreen(page);
    await page.setViewportSize(project.pairedViewport);
    const input = page.locator('input[type="file"]');
    await input.setInputFiles({ name: "responsive.exe", mimeType: "application/octet-stream", buffer: Buffer.from("invalid") });
    await expect(page.locator(".upload-error")).toBeVisible();
    await expectHorizontallyInsideShell(page.locator(".upload-error"), `${project.name} upload validation`);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(page.locator(".app-shell")).toHaveAttribute("data-motion-reduced", "true");
    await input.setInputFiles({ name: "responsive.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 responsive") });
    const status = page.locator(".upload-status-feedback");
    await expect(status).toBeVisible();
    await expect(status).toHaveCSS("animation-name", "none");
    await expect(page.locator(".upload-add-tile.has-selection")).toContainText("文件一");
    await expect(page.locator(".upload-selection-summary")).toHaveCount(0);
    await expectReachable(page.locator(".upload-flow-primary > .button"), `${project.name} reduced upload action`);
    await expectNoShellOverflow(page, `${project.name} reduced upload`);
  });
});
