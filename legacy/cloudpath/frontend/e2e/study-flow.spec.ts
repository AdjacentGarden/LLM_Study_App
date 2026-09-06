import { expect, test } from "./fixtures";

test.describe("study directory flow", () => {
  test("keeps the book bar and learning plan pinned while the directory scrolls", async ({ page }) => {
    await page.setViewportSize({ width: 402, height: 874 });
    await page.goto("/?embedded=device-preview");
    await page.locator(".nav-study").click();

    const bookBar = page.locator(".study-book-bar");
    const stickyStack = page.locator(".study-sticky-stack");
    const plan = page.locator(".study-plan-summary");
    const scroller = page.locator('.screen-content[data-screen="study"]');
    await expect(bookBar).toBeVisible();
    await expect(plan).toBeVisible();
    await expect(plan).toHaveAttribute("data-plan-state", "expanded");
    await expect(page.getByRole("heading", { name: "学习计划", exact: true })).toBeVisible();
    await expect(page.getByText("沿着原书目录继续", { exact: true })).toHaveCount(0);
    await expect(page.getByText("从这里继续", { exact: true })).toHaveCount(0);
    await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle");

    const initialLayout = await scroller.evaluate((element) => {
      const bar = element.querySelector<HTMLElement>(".study-book-bar");
      const plan = element.querySelector<HTMLElement>(".study-plan-summary");
      const directory = element.querySelector<HTMLElement>(".study-directory");
      const statusBar = document.querySelector<HTMLElement>(".ios-status-bar");
      return {
        barTop: Math.round(bar?.getBoundingClientRect().top ?? -1),
        barBottom: Math.round(bar?.getBoundingClientRect().bottom ?? -1),
        planTop: Math.round(plan?.getBoundingClientRect().top ?? -1),
        planBottom: Math.round(plan?.getBoundingClientRect().bottom ?? -1),
        planHeight: Math.round(plan?.getBoundingClientRect().height ?? -1),
        directoryTop: Math.round(directory?.getBoundingClientRect().top ?? -1),
        directoryHeadingTop: Math.round(directory?.querySelector<HTMLElement>(".study-directory-heading")?.getBoundingClientRect().top ?? -1),
        stickyBackground: getComputedStyle(element.querySelector<HTMLElement>(".study-sticky-stack")!).backgroundColor,
        statusBarBottom: Math.round(statusBar?.getBoundingClientRect().bottom ?? -1)
      };
    });
    expect(initialLayout.barTop - initialLayout.statusBarBottom).toBeLessThanOrEqual(4);
    expect(initialLayout.barBottom).toBeLessThanOrEqual(initialLayout.planTop);
    expect(initialLayout.planTop - initialLayout.barBottom).toBeLessThanOrEqual(1);
    expect(initialLayout.planHeight).toBeLessThanOrEqual(132);
    expect(initialLayout.planBottom).toBeLessThanOrEqual(initialLayout.directoryTop);
    expect(initialLayout.directoryHeadingTop - initialLayout.planBottom).toBeGreaterThanOrEqual(8);
    expect(initialLayout.directoryHeadingTop - initialLayout.planBottom).toBeLessThanOrEqual(12);
    expect(initialLayout.stickyBackground).toBe("rgba(0, 0, 0, 0)");
    await scroller.evaluate((element) => {
      element.scrollTop = Math.min(600, element.scrollHeight - element.clientHeight);
    });

    await expect.poll(async () => scroller.evaluate((element) => Math.round(element.scrollTop))).toBeGreaterThan(200);
    await expect(stickyStack).toHaveClass(/is-plan-compact/);
    await expect(plan).toHaveAttribute("data-plan-state", "compact");
    await expect(page.getByRole("heading", { name: "学习计划", exact: true })).toHaveCount(0);
    await expect.poll(async () => bookBar.evaluate((element) => Math.round(element.getBoundingClientRect().top))).toBe(initialLayout.barTop);
    await expect.poll(async () => plan.evaluate((element) => Math.round(element.getBoundingClientRect().top))).toBe(initialLayout.planTop);
    await expect.poll(async () => plan.evaluate((element) => Math.round(element.getBoundingClientRect().height))).toBeLessThanOrEqual(28);
    const compactSurface = await plan.evaluate((element) => ({
      filter: getComputedStyle(element).filter,
      stackBackground: getComputedStyle(element.parentElement as HTMLElement).backgroundColor
    }));
    expect(compactSurface.filter).not.toBe("none");
    expect(compactSurface.stackBackground).toBe("rgba(0, 0, 0, 0)");

    await scroller.evaluate((element) => {
      element.style.scrollBehavior = "auto";
      element.scrollTop = 0;
    });
    await expect.poll(async () => scroller.evaluate((element) => Math.round(element.scrollTop))).toBeLessThanOrEqual(4);
    await expect(stickyStack).not.toHaveClass(/is-plan-compact/);
    await expect(plan).toHaveAttribute("data-plan-state", "expanded");
    await expect(page.getByRole("heading", { name: "学习计划", exact: true })).toBeVisible();
  });

  test("supports mouse drag scrolling without an opaque sticky parent", async ({ page }) => {
    await page.setViewportSize({ width: 420, height: 903 });
    await page.goto("/?embedded=device-preview");
    await page.locator(".nav-study").click();
    await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle");

    const scroller = page.locator('.screen-content[data-screen="study"]');
    const directory = page.locator(".study-directory");
    const firstChapterToggle = page.locator(".study-chapter-toggle").first();
    const startBox = await firstChapterToggle.boundingBox();
    expect(startBox).not.toBeNull();
    const startX = startBox!.x + startBox!.width / 2;
    const startY = startBox!.y + startBox!.height / 2;

    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX, startY - 180, { steps: 8 });

    await expect(directory).toHaveClass(/is-mouse-dragging/);
    await expect.poll(async () => scroller.evaluate((element) => Math.round(element.scrollTop))).toBeGreaterThan(100);
    await expect(page.locator(".study-plan-summary")).toHaveAttribute("data-plan-state", "compact");
    expect(await page.locator(".study-sticky-stack").evaluate((element) => getComputedStyle(element).backgroundColor))
      .toBe("rgba(0, 0, 0, 0)");

    await page.mouse.up();
    await expect(directory).not.toHaveClass(/is-mouse-dragging/);
    await expect(firstChapterToggle).toHaveAttribute("aria-expanded", "true");
    const draggedScrollTop = await scroller.evaluate((element) => element.scrollTop);

    await page.mouse.move(210, 470);
    await page.mouse.down();
    await page.mouse.move(210, 590, { steps: 6 });
    await page.mouse.up();
    await expect.poll(async () => scroller.evaluate((element) => Math.round(element.scrollTop)))
      .toBeLessThan(Math.round(draggedScrollTop));
  });

  test("positions a newly expanded chapter below the sticky study controls", async ({ page }) => {
    await page.setViewportSize({ width: 420, height: 903 });
    await page.goto("/?embedded=device-preview");
    await page.locator(".nav-study").click();
    await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle");

    const fifthChapter = page.locator(".study-chapter").nth(4);
    const fifthChapterToggle = fifthChapter.locator(".study-chapter-toggle");
    await fifthChapterToggle.click();
    await expect(fifthChapterToggle).toHaveAttribute("aria-expanded", "true");
    await expect(fifthChapter.locator(".study-tools-panel").first()).toBeVisible();

    await expect.poll(async () => fifthChapter.evaluate((article) => {
      const scroller = article.closest<HTMLElement>(".screen-content");
      if (!scroller) return Number.POSITIVE_INFINITY;
      const articleRect = article.getBoundingClientRect();
      const stickyBottom = Array.from(
        scroller.querySelectorAll<HTMLElement>(".study-sticky-stack, .study-book-bar, .study-plan-summary")
      ).reduce((bottom, element) => {
        const rect = element.getBoundingClientRect();
        const horizontallyOverlaps = rect.right > articleRect.left && rect.left < articleRect.right;
        return rect.height > 0 && horizontallyOverlaps ? Math.max(bottom, rect.bottom) : bottom;
      }, scroller.getBoundingClientRect().top);
      return Math.round(Math.abs(articleRect.top - stickyBottom - 8));
    })).toBeLessThanOrEqual(1);

    const visibleContent = await fifthChapter.evaluate((article) => {
      const toggleRect = article.querySelector<HTMLElement>(".study-chapter-toggle")?.getBoundingClientRect();
      const toolsRect = article.querySelector<HTMLElement>(".study-tools-panel")?.getBoundingClientRect();
      const navRect = document.querySelector<HTMLElement>(".primary-nav")?.getBoundingClientRect();
      return {
        chapterHeadingHeight: toggleRect?.height ?? 0,
        toolsTop: toolsRect?.top ?? Number.POSITIVE_INFINITY,
        toolsBottom: toolsRect?.bottom ?? Number.NEGATIVE_INFINITY,
        navTop: navRect?.top ?? window.innerHeight
      };
    });
    expect(visibleContent.chapterHeadingHeight).toBeGreaterThanOrEqual(78);
    expect(visibleContent.toolsTop).toBeLessThan(visibleContent.navTop);
    expect(visibleContent.toolsBottom).toBeGreaterThan(0);
  });

  test("opens a textbook section, reads the illustrated lesson, and returns to the pre-lesson tools", async ({ page }) => {
    await page.setViewportSize({ width: 402, height: 874 });
    await page.goto("/?embedded=device-preview");

    await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
    await expect(page.getByRole("button", { name: "学习", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "学习", exact: true }).click();

    await expect(page.getByRole("heading", { name: "学习计划", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "打开 AI 助手", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "当前教材 人教版高中生物必修二遗传与进化", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "添加", exact: true })).toBeVisible();
    const expandedChapterCandidate = page.locator(".study-chapter-toggle[aria-expanded='true']").first();
    await expect(expandedChapterCandidate).toBeVisible();
    const expandedChapterId = await expandedChapterCandidate.getAttribute("id");
    expect(expandedChapterId).toBeTruthy();
    const initiallyExpandedChapter = page.locator(`#${expandedChapterId}`);
    const expandedChapterSurfaces = await initiallyExpandedChapter.locator("..").evaluate((chapter) => {
      const chapterToggle = chapter.querySelector<HTMLElement>(".study-chapter-toggle")!;
      const sectionList = chapter.querySelector<HTMLElement>(".study-section-list")!;
      const screen = chapter.closest<HTMLElement>('.screen-content[data-screen="study"]')!;
      const lightness = (value: string) => {
        const channels = value.match(/\d*\.?\d+/g)?.slice(0, 3).map(Number) ?? [];
        if (channels.length !== 3) return 0;
        const normalized = channels.some((channel) => channel > 1)
          ? channels.map((channel) => channel / 255)
          : channels;
        return normalized.reduce((sum, channel) => sum + channel, 0) / 3;
      };
      const chapterBackground = getComputedStyle(chapterToggle).backgroundColor;
      const sectionBackground = getComputedStyle(sectionList).backgroundColor;
      const pageBackground = getComputedStyle(screen).backgroundColor;
      return {
        chapter: chapterBackground,
        chapterLightness: lightness(chapterBackground),
        pageLightness: lightness(pageBackground),
        sections: sectionBackground,
        sectionsLightness: lightness(sectionBackground)
      };
    });
    expect(expandedChapterSurfaces.sections).not.toBe("rgba(0, 0, 0, 0)");
    expect(expandedChapterSurfaces.sections).not.toBe(expandedChapterSurfaces.chapter);
    expect(expandedChapterSurfaces.sectionsLightness).toBeGreaterThan(expandedChapterSurfaces.pageLightness);
    expect(expandedChapterSurfaces.sectionsLightness).toBeLessThan(expandedChapterSurfaces.chapterLightness);
    await expect(initiallyExpandedChapter.locator("..").locator(".study-section-toggle").first()).toBeVisible();

    const secondChapter = page.getByRole("button", { name: "第 2 章 基因和染色体的关系 3 个小节 教材第 15-40 页 学习进度 17%", exact: true });
    await expect(secondChapter.locator(".study-chapter-progress")).toHaveAttribute("data-progress", "17");
    await secondChapter.click();
    await expect(secondChapter).toHaveAttribute("aria-expanded", "true");
    await expect(initiallyExpandedChapter).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("button", { name: "一 减数分裂 教材第 16-22 页", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "科学家的故事 染色体遗传理论的奠基人——摩尔根 教材第 32 页", exact: true })).toBeVisible();

    await expect(page.getByRole("button", { name: "更多功能 预留新学习工具", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "进入学习", exact: true }).click();
    await expect(page.locator(".lesson-screen")).toBeVisible();
    await expect(page.locator(".lesson-article-header h2")).toContainText("减数分裂和受精作用");
    await expect(page.getByText("已生成", { exact: true })).toHaveCount(0);
    await expect(page.getByText("全文依据状态", { exact: true })).toHaveCount(0);
    await expect(page.getByText("本节来源", { exact: true })).toHaveCount(0);
    await expect(page.getByText("学习工具", { exact: true })).toHaveCount(0);
    const lessonFigures = page.locator(".lesson-inline-figure img");
    await expect.poll(() => lessonFigures.count()).toBeGreaterThan(0);
    const lessonFigureSources = await lessonFigures.evaluateAll((images) => (
      images.map((image) => image.getAttribute("src") ?? "")
    ));
    expect(lessonFigureSources.every((source) => (
      source.startsWith("/assets/textbook/figures/")
      || source.startsWith("/assets/lesson/")
    ))).toBe(true);
    expect(lessonFigureSources.every((source) => !source.includes("/assets/textbook/pages/"))).toBe(true);
    if (lessonFigureSources.some((source) => source.startsWith("/assets/textbook/figures/"))) {
      await expect(page.getByText("教材原图", { exact: true }).first()).toBeVisible();
      await expect(page.getByRole("button", { name: /查看教材原图/ }).first()).toBeVisible();
    } else {
      await expect(page.getByText("AI 辅助示意", { exact: true }).first()).toBeVisible();
    }
    const compactReadingMetrics = await page.locator(".lesson-reading-column").evaluate((article) => {
      const body = article.querySelector<HTMLElement>(".lesson-knowledge-section > p")!;
      const heading = article.querySelector<HTMLElement>(".lesson-knowledge-section > h3")!;
      const section = article.querySelector<HTMLElement>(".lesson-knowledge-section")!;
      const figure = article.querySelector<HTMLElement>(".lesson-inline-figure")!;
      const bodyStyle = getComputedStyle(body);
      const headingStyle = getComputedStyle(heading);
      const sectionStyle = getComputedStyle(section);
      return {
        bodyFontSize: Number.parseFloat(bodyStyle.fontSize),
        bodyLineHeight: Number.parseFloat(bodyStyle.lineHeight),
        figureRatio: figure.getBoundingClientRect().width / article.getBoundingClientRect().width,
        headingFontSize: Number.parseFloat(headingStyle.fontSize),
        sectionPaddingTop: Number.parseFloat(sectionStyle.paddingTop)
      };
    });
    expect(compactReadingMetrics.bodyFontSize).toBe(15);
    expect(compactReadingMetrics.bodyLineHeight).toBeLessThanOrEqual(25);
    expect(compactReadingMetrics.headingFontSize).toBe(18);
    expect(compactReadingMetrics.sectionPaddingTop).toBe(22);
    expect(compactReadingMetrics.figureRatio).toBeLessThanOrEqual(0.93);

    const lessonScroller = page.locator('.screen-content[data-screen="lesson"]');
    const floatingCompletion = page.locator(".lesson-floating-complete");
    const completionBeforeScroll = await floatingCompletion.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return { position: getComputedStyle(element).position, top: bounds.top, left: bounds.left, width: bounds.width };
    });
    expect(completionBeforeScroll.position).toBe("fixed");
    await lessonScroller.hover();
    await page.mouse.wheel(0, 700);
    await expect.poll(() => lessonScroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(200);
    const completionAfterScroll = await floatingCompletion.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return { top: bounds.top, left: bounds.left, width: bounds.width };
    });
    expect(Math.abs(completionAfterScroll.top - completionBeforeScroll.top)).toBeLessThanOrEqual(1);
    expect(Math.abs(completionAfterScroll.left - completionBeforeScroll.left)).toBeLessThanOrEqual(1);
    expect(Math.abs(completionAfterScroll.width - completionBeforeScroll.width)).toBeLessThanOrEqual(1);

    await page.locator(".concept-card-grid button").first().click();
    const conceptSheet = page.locator(".sheet[data-sheet-type='note']");
    await expect(conceptSheet).toBeVisible();
    await expect(conceptSheet.locator(".concept-detail-explanation")).not.toBeEmpty();
    await expect(conceptSheet.locator(".concept-detail-figure img")).toBeVisible();
    await expect(conceptSheet.getByRole("button", { name: /查看教材第/ })).toBeVisible();
    await conceptSheet.locator(".sheet-close").click();
    await expect(conceptSheet).toHaveCount(0);

    await page.getByRole("button", { name: "查看教材第 16 页", exact: true }).click();
    await expect(page.getByRole("heading", { name: "原文文档", exact: true })).toBeVisible();
    await expect(page.getByText("教材第 16 页（PDF 第 11 页）", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "返回", exact: true }).click();
    await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle");
    await expect(page.locator(".lesson-screen")).toBeVisible();
    const pausedCompletionTransition = await page.addStyleTag({
      content: '.motion-screen-transition[data-motion-state="transitioning"] > .motion-screen-surface { animation-play-state: paused !important; }'
    });
    await page.getByRole("button", { name: "完成本节", exact: true }).click();
    await expect(page.locator(".app-shell")).toHaveAttribute("data-active-screen", "study");
    await expect(page.locator(".lesson-floating-complete")).toBeHidden();
    await pausedCompletionTransition.evaluate((style) => style.remove());
    await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle");
    await expect(page.locator(".book-course-screen")).toBeVisible();
    await expect(page.locator(".report-screen")).toHaveCount(0);
    await expect(secondChapter).toHaveAttribute("aria-expanded", "true");

    await page.getByRole("button", { name: "作业诊断 提交解题过程，定位理解卡点", exact: true }).click();
    await expect(page.getByRole("heading", { name: "作业练习", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "返回", exact: true }).click();
    await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle");

    await page.getByRole("button", { name: "闪卡复习 用短时回忆巩固本节概念", exact: true }).click();
    await expect(page.getByRole("heading", { name: "知识点闪卡", exact: true })).toBeVisible();
  });

  test("opens the book switcher and preserves semantic actions", async ({ page }) => {
    await page.setViewportSize({ width: 402, height: 874 });
    await page.goto("/?embedded=device-preview");
    await page.getByRole("button", { name: "学习", exact: true }).click();
    const switcher = page.getByRole("button", { name: "当前教材 人教版高中生物必修二遗传与进化", exact: true });
    await expect(switcher).toBeVisible();
    await switcher.click();

    await expect(page.getByRole("dialog", { name: "切换教材" })).toBeVisible();
    await expect(page.getByRole("button", { name: "添加新教材", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "管理全部教材", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await page.getByRole("button", { name: "添加", exact: true }).click();
    await expect(page.getByRole("heading", { name: "上传书籍", exact: true })).toBeVisible();
  });

  test("uses a master-detail directory layout on iPad without horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 834, height: 1194 });
    await page.goto("/?embedded=device-preview");
    await page.getByRole("button", { name: "学习", exact: true }).click();
    await expect(page.getByRole("heading", { name: "教材目录", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "第 7 章 现代生物进化理论 2 个小节 教材第 109-130 页 学习进度 0%", exact: true })).toBeVisible();

    const layout = await page.locator(".study-screen").evaluate((element) => {
      const plan = element.querySelector<HTMLElement>(".study-plan-summary");
      const directory = element.querySelector<HTMLElement>(".study-directory");
      return {
        display: getComputedStyle(element).display,
        planLeft: plan?.getBoundingClientRect().left ?? 0,
        directoryLeft: directory?.getBoundingClientRect().left ?? 0,
        viewportWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth
      };
    });

    expect(layout.display).toBe("grid");
    expect(layout.directoryLeft).toBeGreaterThan(layout.planLeft);
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  });
});
