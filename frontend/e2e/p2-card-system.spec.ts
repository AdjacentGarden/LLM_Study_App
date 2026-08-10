import type { Locator } from "playwright/test";
import { expect, test, type Page } from "./fixtures";
import type { BookCourseApiFixture } from "./fixtures/bookcourse-api";

const preparedCourseSession = {
  uploadedFile: {
    bookId: "book_stage3",
    name: "stage3-biology.pdf",
    sizeBytes: 1024,
    contentType: "application/pdf",
    uploadedAt: 1
  },
  parseJobId: "job_stage3",
  parseJobStatus: {
    job_id: "job_stage3",
    book_id: "book_stage3",
    status: "pending",
    stage: "queued",
    progress: 1,
    message: "P2 fixture prepares a usable lesson",
    error: null
  }
};

type Rgb = readonly [number, number, number];

function contrastRatio(first: Rgb, second: Rgb) {
  const luminance = (color: Rgb) => {
    const [red, green, blue] = color.map((channel) => {
      const normalized = channel / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    }) as Rgb;
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  };
  const [light, dark] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

async function clickAfterMotionAndScrollSettle(page: Page, target: Locator, label: string) {
  await expect(
    page.locator(".motion-screen-transition"),
    `${label}: current screen transition is settled before interaction`
  ).toHaveAttribute("data-motion-state", "idle");
  await expect(target, `${label}: action is unique`).toHaveCount(1);
  await expect(target, `${label}: action is visible`).toBeVisible();
  await expect(target, `${label}: action is enabled`).toBeEnabled();
  await expect.poll(async () => target.evaluate((element) => {
    const action = element as HTMLElement;
    const motionStateSelector = [
      "[data-motion-state]",
      "[data-motion-item-state]",
      "[data-motion-course-card-state]",
      "[data-motion-selection-state]",
      "[data-motion-text-state]",
      "[data-motion-error-state]"
    ].join(",");
    const collapsibles: HTMLElement[] = [];
    const relatedStateElements = new Set<HTMLElement>();
    let ancestor: HTMLElement | null = action;
    while (ancestor) {
      if (ancestor.matches(".motion-collapsible")) collapsibles.push(ancestor);
      if (ancestor.matches(motionStateSelector)) relatedStateElements.add(ancestor);
      ancestor = ancestor.parentElement;
    }
    for (const collapsible of collapsibles) {
      collapsible.querySelectorAll<HTMLElement>(motionStateSelector).forEach((node) => relatedStateElements.add(node));
    }

    const stateAttributes = [
      "data-motion-state",
      "data-motion-item-state",
      "data-motion-course-card-state",
      "data-motion-selection-state",
      "data-motion-text-state",
      "data-motion-error-state"
    ];
    const nonIdleStates = Array.from(relatedStateElements).flatMap((node) => (
      stateAttributes
        .map((attribute) => node.getAttribute(attribute))
        .filter((state): state is string => Boolean(state && state !== "idle"))
    ));
    const nonExpandedCollapsibles = collapsibles
      .map((node) => node.getAttribute("data-motion-collapsible"))
      .filter((state) => state !== "expanded");
    const animations = new Set<Animation>();
    for (const root of collapsibles.length > 0 ? collapsibles : [action]) {
      root.getAnimations({ subtree: true }).forEach((animation) => animations.add(animation));
    }
    const activeFiniteAnimationCount = Array.from(animations)
      .filter((animation) => animation.effect?.getTiming().iterations !== Infinity)
      .filter((animation) => animation.playState !== "finished" && animation.playState !== "idle")
      .length;
    return { activeFiniteAnimationCount, nonExpandedCollapsibles, nonIdleStates };
  }), `${label}: related collapsible, layout, and presence motion is idle`).toEqual({
    activeFiniteAnimationCount: 0,
    nonExpandedCollapsibles: [],
    nonIdleStates: []
  });
  await expect.poll(async () => target.evaluate(async (element) => {
    const action = element as HTMLElement;
    const scroller = action.closest<HTMLElement>(".screen-content");
    const previousScrollBehavior = scroller?.style.scrollBehavior ?? "";
    if (scroller) scroller.style.scrollBehavior = "auto";
    try {
      action.scrollIntoView({ behavior: "auto", block: "center", inline: "nearest" });
      const readBounds = () => {
        const bounds = action.getBoundingClientRect();
        return [bounds.bottom, bounds.left, bounds.right, bounds.top];
      };
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
      const firstFrame = readBounds();
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
      const secondFrame = readBounds();
      return firstFrame.every((value, index) => value === secondFrame[index]);
    } finally {
      if (scroller) scroller.style.scrollBehavior = previousScrollBehavior;
    }
  }), `${label}: target settles across two exact animation frames`).toBe(true);
  await target.click({ trial: true });
  await target.click();
}

async function openLibrary(page: Page, bookCourseApi: BookCourseApiFixture) {
  bookCourseApi.useStageFiveFlow();
  await page.addInitScript((session) => {
    window.localStorage.setItem("bookcourse-active-parse-session", JSON.stringify(session));
  }, preparedCourseSession);
  await page.goto("/?embedded=device-preview");
  await clickAfterMotionAndScrollSettle(
    page,
    page.getByRole("button", { name: "全部教材", exact: true }),
    "open Library from Home"
  );
  await expect(page.locator(".library-course-grid")).toBeVisible();
}

async function openCourse(page: Page, bookCourseApi: BookCourseApiFixture) {
  await openLibrary(page, bookCourseApi);
  await clickAfterMotionAndScrollSettle(
    page,
    page.locator(".library-course-grid .course-space-card").first().getByRole("button", { name: "进入课程", exact: true }),
    "open first Library course"
  );
  await expect(page.locator(".book-course-screen")).toBeVisible();
}

async function openMeiosisSection(page: Page) {
  const secondChapter = page.locator(
    '.study-chapter-toggle[aria-label^="第 2 章 基因和染色体的关系 3 个小节 教材第 15-40 页 学习进度"]'
  );
  await expect(secondChapter, "meiosis chapter is unique").toHaveCount(1);
  if (await secondChapter.getAttribute("aria-expanded") !== "true") {
    await clickAfterMotionAndScrollSettle(page, secondChapter, "expand meiosis chapter");
  }
  await expect(secondChapter, "meiosis chapter remains expanded").toHaveAttribute("aria-expanded", "true");

  const sectionToggle = page.locator(
    '.study-chapter.is-expanded .study-section-toggle[aria-label^="第 1 节 减数分裂和受精作用 教材第 16-26 页"]'
  );
  await expect(sectionToggle, "meiosis section is unique inside the expanded chapter").toHaveCount(1);
  if (await sectionToggle.getAttribute("aria-expanded") !== "true") {
    await clickAfterMotionAndScrollSettle(page, sectionToggle, "expand meiosis section");
  }
  await expect(sectionToggle, "meiosis section remains expanded").toHaveAttribute("aria-expanded", "true");
  return sectionToggle.locator("..");
}

async function openLesson(page: Page, bookCourseApi: BookCourseApiFixture) {
  await openCourse(page, bookCourseApi);
  const section = await openMeiosisSection(page);
  await clickAfterMotionAndScrollSettle(
    page,
    section.getByRole("button", { name: "进入学习", exact: true }),
    "open section lesson"
  );
  await expect(page.locator(".lesson-screen")).toBeVisible();
}

async function readTouchViolations(page: Page) {
  return page.evaluate(() => {
    const selector = [
      "button:not([disabled])",
      "[role='button']:not([aria-disabled='true'])",
      "a.button",
      "input:not([type='checkbox']):not([type='radio']):not([type='hidden']):not([type='file']):not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])"
    ].join(",");
    return Array.from(document.querySelectorAll<HTMLElement>(selector))
      .filter((element) => {
        const style = getComputedStyle(element);
        const bounds = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && bounds.width > 0 && bounds.height > 0;
      })
      .map((element) => {
        const bounds = element.getBoundingClientRect();
        return {
          label: element.getAttribute("aria-label") || element.textContent?.trim() || element.tagName,
          className: element.className,
          height: Math.round(bounds.height * 10) / 10,
          width: Math.round(bounds.width * 10) / 10
        };
      })
      .filter((target) => target.width < 44 || target.height < 44);
  });
}

test.describe("P2 card-system acceptance", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "reduce", timezoneId: "Asia/Hong_Kong" });

  test("keeps real home and lesson interactions at least 44px, with 48px primary CTAs", async ({ page, bookCourseApi }) => {
    await page.goto("/?embedded=device-preview");
    await expect(page.locator(".home-primary-action")).toBeVisible();
    const tokenValues = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return {
        primary: root.getPropertyValue("--color-primary").trim(),
        surface: root.getPropertyValue("--color-surface").trim(),
        surfaceSoft: root.getPropertyValue("--color-surface-soft").trim(),
        text: root.getPropertyValue("--color-text").trim(),
        secondary: root.getPropertyValue("--color-text-secondary").trim(),
        muted: root.getPropertyValue("--color-text-muted").trim(),
        touch: root.getPropertyValue("--touch-target-min").trim(),
        primaryTouch: root.getPropertyValue("--touch-target-primary").trim()
      };
    });
    const white: Rgb = [255, 255, 255];
    const tokenColor = (value: string): Rgb => {
      const hex = value.replace("#", "");
      return [
        Number.parseInt(hex.slice(0, 2), 16),
        Number.parseInt(hex.slice(2, 4), 16),
        Number.parseInt(hex.slice(4, 6), 16)
      ];
    };
    expect(tokenValues.touch).toBe("44px");
    expect(tokenValues.primaryTouch).toBe("48px");
    expect(contrastRatio(tokenColor(tokenValues.primary), white), "primary token carries white text at AA").toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(tokenColor(tokenValues.text), tokenColor(tokenValues.surface)), "body text token is AA on surface").toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(tokenColor(tokenValues.secondary), tokenColor(tokenValues.surface)), "secondary text token is AA on surface").toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(tokenColor(tokenValues.muted), tokenColor(tokenValues.surfaceSoft)), "muted text token is AA on subdued surface").toBeGreaterThanOrEqual(4.5);

    const homeViolations = await readTouchViolations(page);
    expect(homeViolations, "home non-inline targets are all at least 44 x 44 CSS px").toEqual([]);

    const homePrimary = page.locator(".home-primary-action:visible");
    await expect(homePrimary).toHaveCSS("min-height", "48px");
    await expect
      .poll(() => homePrimary.evaluate((element) => element.getBoundingClientRect().height))
      .toBeGreaterThanOrEqual(48);

    await openLesson(page, bookCourseApi);
    const lessonViolations = await readTouchViolations(page);
    expect(lessonViolations, "lesson non-inline targets are all at least 44 x 44 CSS px").toEqual([]);

    const lessonPrimary = await page.locator(".lesson-floating-complete .button-primary").evaluate((element) => element.getBoundingClientRect().height);
    expect(lessonPrimary, "lesson completion CTA is at least 48px high").toBeGreaterThanOrEqual(48);
    await expect(page.getByRole("button", { name: "做练习", exact: true }), "exercise entry is intentionally absent from the reading surface").toHaveCount(0);
    await expect(page.getByRole("button", { name: "完成本节", exact: true }), "chapter completion remains the outcome CTA").toHaveClass(/button-primary/);
    await expect(page.locator(".lesson-screen .button-primary"), "the lesson exposes exactly one solid primary action").toHaveCount(1);
  });

  test("gives Library one primary course entry and groups the current chapter tools", async ({ page, bookCourseApi }) => {
    await openLibrary(page, bookCourseApi);
    const courseEntry = page.locator(".library-course-grid .course-space-card").first().getByRole("button", { name: "进入课程", exact: true });
    const libraryUpload = page.getByRole("button", { name: "上传新教材", exact: true }).last();
    const libraryActions = await Promise.all([courseEntry, libraryUpload].map((locator) => locator.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        background: style.backgroundColor,
        borderTopWidth: style.borderTopWidth,
        color: style.color
      };
    })));
    expect(
      ["rgb(124, 58, 237)", "rgb(91, 33, 182)"],
      "course entry remains the Library card's primary action in rest or hover state"
    ).toContain(libraryActions[0].background);
    expect(libraryActions[1], "upload is visually secondary when a course entry is available").toEqual({
      background: "rgb(255, 255, 255)",
      borderTopWidth: "1px",
      color: "rgb(32, 38, 58)"
    });

    await clickAfterMotionAndScrollSettle(
      page,
      courseEntry,
      "open the primary Library course entry for its chapter tools"
    );
    await expect(page.locator(".book-course-screen")).toBeVisible();
    const section = await openMeiosisSection(page);
    const group = section.locator(".study-tool-grid");
    await expect(group).toHaveAttribute("aria-label", "本节辅助工具");
    const groupedSurface = await group.evaluate((element) => {
      const groupStyle = getComputedStyle(element);
      const cells = Array.from(element.querySelectorAll<HTMLElement>(".study-tool-card")).map((cell) => {
        const style = getComputedStyle(cell);
        const bounds = cell.getBoundingClientRect();
        return {
          backdropFilter: style.backdropFilter,
          height: bounds.height,
          borderRadius: Number.parseFloat(style.borderTopLeftRadius),
          tool: cell.dataset.tool,
          width: bounds.width
        };
      });
      return {
        columnGap: groupStyle.columnGap,
        rowGap: groupStyle.rowGap,
        cells
      };
    });
    expect(groupedSurface, "chapter utilities keep the new two-column tool layout").toMatchObject({
      columnGap: "12px",
      rowGap: "12px"
    });
    expect(groupedSurface.cells.map((cell) => cell.tool)).toEqual(["assignment", "flashcards", "future"]);
    expect(groupedSurface.cells.every((cell) => cell.borderRadius >= 12 && cell.borderRadius <= 16 && cell.backdropFilter === "none"), "tool cards remain compact opaque surfaces").toBe(true);
    expect(groupedSurface.cells.every((cell) => cell.width >= 44 && cell.height >= 44), "chapter tool targets remain touch-safe").toBe(true);
  });

  test("keeps the white fully rounded Liquid Glass primary navigation", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    const navigation = page.locator(".primary-nav");
    await expect(navigation).toBeVisible();
    await expect(navigation).toHaveAttribute("data-lg-variant", "prominent");

    const navigationStyles = await navigation.evaluate((element) => {
      const style = getComputedStyle(element);
      const items = Array.from(element.querySelectorAll<HTMLElement>(".nav-item")).map((item) => {
        const bounds = item.getBoundingClientRect();
        const itemStyle = getComputedStyle(item);
        return {
          active: item.getAttribute("aria-current") === "page",
          background: itemStyle.backgroundColor,
          height: bounds.height,
          radius: itemStyle.borderTopLeftRadius,
          width: bounds.width
        };
      });
      return {
        background: style.backgroundColor,
        backdropFilter: style.backdropFilter,
        borderRadius: style.borderTopLeftRadius,
        items
      };
    });

    expect(navigationStyles.backdropFilter, "the restored navigation keeps its intentional glass treatment").toContain("blur(24px)");
    expect(navigationStyles.background, "the navigation surface is white rather than pale blue").toBe("rgb(255, 255, 255)");
    expect(navigationStyles.borderRadius, "the navigation uses the requested fully rounded silhouette").toBe("999px");
    expect(navigationStyles.items).toHaveLength(4);
    expect(navigationStyles.items.every((item) => item.width >= 44 && item.height >= 44), "tab targets remain at least 44 x 44 CSS px").toBe(true);
    expect(navigationStyles.items.filter((item) => !item.active).every((item) => item.radius === "999px"), "the press capsule uses the same fully rounded radius").toBe(true);
    expect(navigationStyles.items.find((item) => item.active)?.background, "the original active tab does not become an extra card").toBe("rgba(0, 0, 0, 0)");
  });

  test("uses a continuous reading surface and maintains AA contrast on rendered lesson text", async ({ page, bookCourseApi }) => {
    await openLesson(page, bookCourseApi);

    const surfaceViolations = await page.locator(".screen-content .card, .screen-content .quick-action, .screen-content .metric-card").evaluateAll((elements) => {
      return elements.map((element) => {
        const style = getComputedStyle(element);
        return {
          className: element.className,
          backdropFilter: style.backdropFilter,
          boxShadow: style.boxShadow,
          radius: Number.parseFloat(style.borderTopLeftRadius)
        };
      }).filter((surface) => surface.backdropFilter !== "none" || surface.boxShadow !== "none" || surface.radius < 12 || surface.radius > 16);
    });
    expect(surfaceViolations, "the lesson has no decorative elevated card regressions").toEqual([]);
    await expect(page.locator(".lesson-knowledge-section.card"), "knowledge sections are continuous article content rather than cards").toHaveCount(0);

    const renderedPairs = await page.locator(
      ".lesson-article-meta, .lesson-knowledge-section > p, .concept-card-grid strong, .lesson-floating-complete .button-primary"
    ).evaluateAll((elements) => elements.map((element) => {
      const parseRgb = (value: string) => {
        const channels = value.match(/\d+(?:\.\d+)?/g)?.slice(0, 3).map(Number);
        return channels && channels.length === 3 ? [channels[0], channels[1], channels[2]] as Rgb : null;
      };
      const foreground = parseRgb(getComputedStyle(element).color);
      let parent: HTMLElement | null = element as HTMLElement;
      let background: Rgb | null = null;
      while (parent && !background) {
        const color = getComputedStyle(parent).backgroundColor;
        const rgba = color.match(/\d+(?:\.\d+)?/g)?.map(Number) ?? [];
        const alpha = rgba[3] ?? 1;
        if (rgba.length >= 3 && alpha >= 0.99) background = [rgba[0], rgba[1], rgba[2]];
        parent = parent.parentElement;
      }
      return {
        label: element.textContent?.trim() || element.className,
        foreground,
        background
      };
    }));

    const contrastViolations = renderedPairs.map((pair) => {
      if (!pair.foreground || !pair.background) return { ...pair, ratio: 0 };
      return { ...pair, ratio: contrastRatio(pair.foreground, pair.background) };
    }).filter((pair) => pair.ratio < 4.5);
    expect(contrastViolations, "rendered ordinary text, labels and primary button text meet WCAG AA").toEqual([]);

    const lessonScroller = page.locator('.screen-content[data-screen="lesson"]');
    const completionBar = page.locator(".lesson-floating-complete");
    const fixedBeforeScroll = await completionBar.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return {
        position: getComputedStyle(element).position,
        top: bounds.top,
        left: bounds.left,
        width: bounds.width
      };
    });
    expect(fixedBeforeScroll.position, "completion control is anchored to the viewport").toBe("fixed");
    await lessonScroller.hover();
    await page.mouse.wheel(0, 700);
    await expect.poll(() => lessonScroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(200);
    const fixedAfterScroll = await completionBar.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return { top: bounds.top, left: bounds.left, width: bounds.width };
    });
    expect(Math.abs(fixedAfterScroll.top - fixedBeforeScroll.top), "completion control does not move vertically with the article").toBeLessThanOrEqual(1);
    expect(Math.abs(fixedAfterScroll.left - fixedBeforeScroll.left), "completion control keeps its horizontal anchor").toBeLessThanOrEqual(1);
    expect(Math.abs(fixedAfterScroll.width - fixedBeforeScroll.width), "completion control keeps its floating tab-bar width").toBeLessThanOrEqual(1);

    await clickAfterMotionAndScrollSettle(
      page,
      page.locator(".concept-card-grid button").first(),
      "open the concept detail sheet for the form audit"
    );
    const conceptSheet = page.locator(".sheet[data-sheet-type='note']");
    await expect(conceptSheet).toBeVisible();
    const noteInput = conceptSheet.locator("textarea");
    const noteField = await noteInput.evaluate((element) => {
      const background = getComputedStyle(element).backgroundColor.match(/\d+(?:\.\d+)?/g)?.slice(0, 3).map(Number);
      const bounds = element.getBoundingClientRect();
      return { background, height: bounds.height };
    });
    expect(noteField.height, "concept note input keeps a comfortable form-control target").toBeGreaterThanOrEqual(44);
    expect(noteField.background).toHaveLength(3);
    expect(await readTouchViolations(page), "sheet actions keep the same 44px target contract").toEqual([]);
  });

  test("keeps the P2 cascade active inside the device-preview iframe after studio CSS loads", async ({ page }) => {
    await page.goto("/?device=iphone-17-pro&orientation=portrait&quality=fit&chrome=1");
    const studioStyles = await page.locator(".device-preview-studio").evaluate((element) => {
      const toolbar = document.querySelector<HTMLElement>(".device-preview-toolbar");
      return {
        canvasShadow: getComputedStyle(document.querySelector<HTMLElement>(".device-preview-canvas")!).boxShadow,
        studioBackground: getComputedStyle(element).backgroundImage,
        toolbarBackdrop: toolbar ? getComputedStyle(toolbar).backdropFilter : null
      };
    });
    const preview = page.frameLocator(".device-preview-iframe");
    const primary = preview.locator(".home-primary-action");
    await expect(primary).toBeVisible();
    const finalStyles = await primary.evaluate((element) => {
      const button = getComputedStyle(element);
      const surface = getComputedStyle(document.querySelector<HTMLElement>(".home-focus-panel")!);
      return {
        background: button.backgroundColor,
        height: element.getBoundingClientRect().height,
        radius: Number.parseFloat(button.borderTopLeftRadius),
        surfaceShadow: surface.boxShadow,
        surfaceBackdrop: surface.backdropFilter
      };
    });
    expect(finalStyles.height, "iframe primary CTA keeps the 48px target after device-preview.css").toBeGreaterThanOrEqual(48);
    expect(finalStyles.radius, "iframe primary CTA uses the compact P2 corner radius").toBeGreaterThanOrEqual(12);
    expect(finalStyles.radius).toBeLessThanOrEqual(16);
    expect(finalStyles.background, "iframe primary CTA resolves to the shared navigation purple").toBe("rgb(124, 58, 237)");
    expect(finalStyles.surfaceShadow, "iframe task surface is not a floating ghost card").toBe("none");
    expect(finalStyles.surfaceBackdrop, "iframe task surface has no glass blur").toBe("none");
    expect(studioStyles.studioBackground, "studio background no longer uses a global decorative gradient").toBe("none");
    expect(studioStyles.toolbarBackdrop, "studio toolbar no longer uses glass blur").toBe("none");
    expect(studioStyles.canvasShadow, "preview canvas uses only the compact floating shadow").toMatch(/0px 4px 8px/);
  });
});
