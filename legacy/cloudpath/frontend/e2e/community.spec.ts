import type { Page } from "playwright/test";
import { expect, test } from "./fixtures";
import { getResponsiveProject } from "./fixtures/viewports";

async function openCommunity(page: Page) {
  await page.goto("/?embedded=device-preview");
  await page.getByRole("button", { name: "社区", exact: true }).click();
  await expect(page.getByRole("region", { name: "社区课程", exact: true })).toBeVisible();
  await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle");
}

async function expectTwoColumnCommunityGrid(page: Page, label: string) {
  const cards = page.locator(".community-grid .community-book-card");
  await expect(cards, `${label}: all ten community books are visible`).toHaveCount(10);
  const geometry = await cards.evaluateAll((elements) => {
    const rects = elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: Math.round(rect.left * 10) / 10,
        top: Math.round(rect.top * 10) / 10,
        width: Math.round(rect.width * 10) / 10,
        height: Math.round(rect.height * 10) / 10
      };
    });
    const content = document.querySelector<HTMLElement>(".screen-content");
    const grid = document.querySelector<HTMLElement>(".community-grid");
    if (!content || !grid) throw new Error("Community layout containers are missing");
    const gridRect = grid.getBoundingClientRect();
    return {
      rects,
      columnCount: new Set(rects.map((rect) => rect.left)).size,
      rowCount: new Set(rects.map((rect) => rect.top)).size,
      contentOverflow: content.scrollWidth > content.clientWidth,
      cardsInsideGrid: rects.every((rect) => rect.left >= gridRect.left - 1 && rect.left + rect.width <= gridRect.right + 1)
    };
  });

  expect(geometry.columnCount, `${label}: grid has exactly two columns`).toBe(2);
  expect(geometry.rowCount, `${label}: ten books form exactly five rows`).toBe(5);
  expect(Math.max(...geometry.rects.map((rect) => rect.width)) - Math.min(...geometry.rects.map((rect) => rect.width)), `${label}: card widths match`).toBeLessThan(1);
  expect(Math.max(...geometry.rects.map((rect) => rect.height)) - Math.min(...geometry.rects.map((rect) => rect.height)), `${label}: card heights match`).toBeLessThan(1);
  expect(geometry.contentOverflow, `${label}: screen content has no horizontal overflow`).toBe(false);
  expect(geometry.cardsInsideGrid, `${label}: every card stays inside the grid`).toBe(true);
}

test.describe("community discovery", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "reduce", timezoneId: "Asia/Hong_Kong" });

  test("supports categories, search, empty-state recovery, and global controls", async ({ page, bookCourseApi }) => {
    await openCommunity(page);

    await expect(page.getByText("与你教材匹配", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "分类", exact: true })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "书籍分类", exact: true })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "热门书籍", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "显示全部热门书籍", exact: true })).toHaveCount(0);
    const search = page.getByLabel("搜索课程", { exact: true });
    await expect(search).toBeVisible();
    const categoryRail = page.getByRole("group", { name: "按学科筛选书籍" });
    const categories = categoryRail.getByRole("button");
    await expect(categories).toHaveCount(9);
    await expect(categories).toHaveText(["推荐", "生物", "数学", "物理", "化学", "历史", "地理", "语文", "英语"]);
    await expect(categoryRail.getByRole("button", { name: "全部", exact: true })).toHaveCount(0);
    await expect(categoryRail.getByRole("button", { name: "推荐", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".community-grid .community-book-card")).toHaveCount(8);
    await expect(page.locator(".community-subject-label")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "进入课程：遗传与进化", exact: true })).toBeVisible();
    await expect(page.locator('[data-community-book-id="community_genetics"]')).toBeVisible();
    await expect(page.locator('[data-community-book-id="community_functions"]')).toBeVisible();
    await expect(page.locator('[data-community-book-id="community_higher_mathematics"]')).toBeVisible();
    await expect(page.locator('[data-community-book-id="community_high_school_mathematics_2"]')).toBeVisible();
    await expect(page.locator('[data-community-book-id="community_theoretical_mechanics"]')).toBeVisible();
    await expect(page.locator('[data-community-book-id="community_high_school_physics_3"]')).toBeVisible();
    await expect(page.locator('[data-community-book-id="community_high_school_english_3"]')).toBeVisible();
    await expect(page.locator('[data-community-book-id="community_high_school_chemistry_2"]')).toBeVisible();
    const pdfBackedCovers = page.locator(
      '.community-book-card img[src*="/assets/community/"]:not([src$="functions-derivatives-cover-v1.webp"]):not([src$="force-motion-cover-v1.webp"])'
    );
    await expect(pdfBackedCovers).toHaveCount(6);
    expect(
      await pdfBackedCovers.evaluateAll((images) => images.every(
        (image) => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0
      )),
      "all six PDF-backed course covers load"
    ).toBe(true);

    const categoryScroller = page.locator(".community-category-list");
    const canDragCategories = await categoryScroller.evaluate((element) => element.scrollWidth > element.clientWidth);
    if (canDragCategories) {
      const scrollerBox = await categoryScroller.boundingBox();
      if (!scrollerBox) throw new Error("Category scroller geometry is unavailable");
      const startingScrollLeft = await categoryScroller.evaluate((element) => element.scrollLeft);
      await page.mouse.move(scrollerBox.x + scrollerBox.width - 12, scrollerBox.y + (scrollerBox.height / 2));
      await page.mouse.down();
      await page.mouse.move(scrollerBox.x + 12, scrollerBox.y + (scrollerBox.height / 2), { steps: 8 });
      await page.mouse.up();
      await expect.poll(
        () => categoryScroller.evaluate((element) => element.scrollLeft),
        { message: "dragging the category rail reveals categories on the right" }
      ).toBeGreaterThan(startingScrollLeft + 40);
      await expect(categoryRail.getByRole("button", { name: "推荐", exact: true })).toHaveAttribute("aria-pressed", "true");
      await categoryScroller.evaluate((element) => {
        element.scrollLeft = 0;
      });
    }

    const moreCategories = page.getByRole("button", { name: "查看更多分类", exact: true });
    await expect(moreCategories).toHaveAttribute("aria-expanded", "false");
    await moreCategories.click();
    const categoryMenu = page.getByRole("group", { name: "全部课程分类" });
    await expect(categoryMenu).toBeVisible();
    await expect(categoryMenu.getByRole("button")).toHaveCount(9);
    await expect(page.getByRole("button", { name: "收起全部分类", exact: true })).toHaveAttribute("aria-expanded", "true");
    await categoryMenu.getByRole("button", { name: "历史", exact: true }).click();
    await expect(categoryMenu).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("没有找到匹配书籍");
    await page.locator(".community-empty-state").getByRole("button", { name: "查看推荐", exact: true }).click();
    await expect(page.locator(".community-grid .community-book-card")).toHaveCount(8);

    await categoryRail.getByRole("button", { name: "数学", exact: true }).click();
    await expect(page.locator(".community-grid .community-book-card")).toHaveCount(3);
    await expect(page.locator('[data-community-book-id="community_functions"]')).toBeVisible();
    await expect(categoryRail.getByRole("button", { name: "数学", exact: true })).toHaveAttribute("aria-pressed", "true");

    await search.fill("北师大版");
    await expect(page.locator(".community-grid .community-book-card")).toHaveCount(1);
    await expect(page.locator('[data-community-book-id="community_functions"]')).toBeVisible();
    await expect(categoryRail.getByRole("button", { name: "数学", exact: true })).toHaveAttribute("aria-pressed", "false");
    await expect(categoryRail.locator('[aria-pressed="true"]')).toHaveCount(0);
    await page.getByRole("button", { name: "清除搜索内容", exact: true }).click();
    await expect(categoryRail.getByRole("button", { name: "数学", exact: true })).toHaveAttribute("aria-pressed", "true");

    await categoryRail.getByRole("button", { name: "化学", exact: true }).click();
    await expect(page.locator(".community-grid .community-book-card")).toHaveCount(1);
    await expect(page.locator('[data-community-book-id="community_high_school_chemistry_2"]')).toBeVisible();
    await categoryRail.getByRole("button", { name: "推荐", exact: true }).click();
    await expect(page.locator(".community-grid .community-book-card")).toHaveCount(8);
    await expect(categoryRail.getByRole("button", { name: "推荐", exact: true })).toHaveAttribute("aria-pressed", "true");

    const navigation = page.getByRole("navigation", { name: "主导航" });
    await expect(navigation.getByRole("button")).toHaveCount(4);
    await expect(page.getByRole("button", { name: "打开 AI 助手", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "打开 AI 助手", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("heading", { name: "AI 导学助手", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "收起 AI 助手", exact: true }).click();

    expect(bookCourseApi.consoleErrors, "community emits no console errors").toEqual([]);
    expect(bookCourseApi.pageErrors, "community emits no page errors").toEqual([]);
  });

  test("opens the Figma-style course keyboard from search and keeps its controls functional", async ({ page, bookCourseApi }) => {
    await openCommunity(page);

    const search = page.getByRole("searchbox", { name: "搜索课程", exact: true });
    await expect(search).toHaveAttribute("inputmode", "none");
    await expect(search).toHaveAttribute("aria-expanded", "false");
    await search.click();

    const keyboard = page.getByRole("region", { name: "课程搜索键盘", exact: true });
    await expect(keyboard).toBeVisible();
    await expect(search).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("navigation", { name: "主导航", exact: true })).toBeHidden();
    await expect(page.getByRole("button", { name: "打开 AI 助手", exact: true })).toBeHidden();
    await expect(keyboard.locator(".community-keyboard-row-top button")).toHaveCount(10);
    await expect(keyboard.locator(".community-keyboard-row-middle button")).toHaveCount(9);
    await expect(keyboard.locator(".community-keyboard-row-lower button")).toHaveCount(9);
    await expect(keyboard.locator(".community-keyboard-row-bottom button")).toHaveCount(3);
    await expect(page.getByRole("button", { name: "插入表情", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "语音输入暂不可用", exact: true })).toBeDisabled();

    const keyboardStyles = await keyboard.evaluate((element) => {
      const style = getComputedStyle(element);
      const submit = element.querySelector<HTMLElement>(".community-keyboard-submit-key");
      const buttons = Array.from(element.querySelectorAll<HTMLElement>("button"));
      const app = element.closest<HTMLElement>(".app-shell");
      if (!submit || !app) throw new Error("Community keyboard layout is incomplete");
      return {
        backgroundColor: style.backgroundColor,
        boxShadow: style.boxShadow,
        bottom: style.bottom,
        position: style.position,
        zIndex: style.zIndex,
        submitBackgroundColor: getComputedStyle(submit).backgroundColor,
        submitColor: getComputedStyle(submit).color,
        minButtonHeight: Math.min(...buttons.map((button) => button.getBoundingClientRect().height)),
        horizontalOverflow: app.scrollWidth > app.clientWidth
      };
    });
    expect(keyboardStyles).toMatchObject({
      backgroundColor: "rgb(215, 219, 225)",
      bottom: "0px",
      position: "absolute",
      zIndex: "44",
      submitBackgroundColor: "rgb(10, 132, 255)",
      submitColor: "rgb(255, 255, 255)",
      horizontalOverflow: false
    });
    expect(keyboardStyles.boxShadow).not.toBe("none");
    const minimumKeyboardButtonHeight = (page.viewportSize()?.height ?? 501) <= 500 ? 31.5 : 43.5;
    expect(keyboardStyles.minButtonHeight).toBeGreaterThanOrEqual(minimumKeyboardButtonHeight);

    await page.getByRole("button", { name: "字母 q", exact: true }).click();
    await expect(search).toHaveValue("q");
    await page.getByRole("button", { name: "开启大写", exact: true }).click();
    await page.getByRole("button", { name: "字母 w", exact: true }).click();
    await expect(search).toHaveValue("qW");
    await page.getByRole("button", { name: "退格", exact: true }).click();
    await expect(search).toHaveValue("q");
    await page.getByRole("button", { name: "空格", exact: true }).click();
    await expect(search).toHaveValue("q ");
    await page.getByRole("button", { name: "插入表情", exact: true }).click();
    await expect(search).toHaveValue("q 😊");
    await page.getByRole("button", { name: "建议：生物", exact: true }).click();
    await expect(search).toHaveValue("生物");
    const biologyResults = page.locator('.community-grid .community-book-card[data-community-subject="生物"]');
    await expect(biologyResults).toHaveCount(2);
    await expect(page.locator(".community-grid .community-book-card")).toHaveCount(2);

    await page.getByRole("button", { name: "完成搜索", exact: true }).click();
    await expect(keyboard).toHaveCount(0);
    await expect(search).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("navigation", { name: "主导航", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "打开 AI 助手", exact: true })).toBeVisible();

    await search.click();
    await expect(page.getByRole("region", { name: "课程搜索键盘", exact: true })).toBeVisible();
    await search.press("Escape");
    await expect(page.getByRole("region", { name: "课程搜索键盘", exact: true })).toHaveCount(0);

    expect(bookCourseApi.consoleErrors, "course keyboard emits no console errors").toEqual([]);
    expect(bookCourseApi.pageErrors, "course keyboard emits no page errors").toEqual([]);
  });

  test("keeps the two-column solid-surface system across paired viewports", async ({ page }, testInfo) => {
    const project = getResponsiveProject(testInfo.project.name);
    await openCommunity(page);

    const styles = await page.evaluate(() => {
      const app = document.querySelector<HTMLElement>(".app-shell");
      const screenContent = document.querySelector<HTMLElement>(".screen-content");
      const headerBar = document.querySelector<HTMLElement>(".header-bar");
      const discoveryControls = document.querySelector<HTMLElement>(".community-discovery-controls");
      const search = document.querySelector<HTMLElement>(".community-search-field");
      const card = document.querySelector<HTMLElement>(".community-book-card");
      const cover = document.querySelector<HTMLElement>(".community-book-cover, .community-book-cover-fallback");
      const enter = document.querySelector<HTMLElement>(".community-book-enter");
      const bookBottom = document.querySelector<HTMLElement>(".community-book-bottom");
      const bookMeta = document.querySelector<HTMLElement>(".community-book-meta");
      const categoryRail = document.querySelector<HTMLElement>(".community-category-rail");
      const categoryList = document.querySelector<HTMLElement>(".community-category-list");
      const categoryMore = document.querySelector<HTMLElement>(".community-category-more");
      const resultSummary = document.querySelector<HTMLElement>("#community-result-summary");
      const selected = document.querySelector<HTMLElement>('.community-category-button[aria-pressed="true"]');
      if (!app || !screenContent || !headerBar || !discoveryControls || !search || !card || !cover || !enter || !bookBottom || !bookMeta || !categoryRail || !categoryList || !categoryMore || !resultSummary || !selected) {
        throw new Error("Community visual surfaces are missing");
      }
      const read = (element: HTMLElement) => {
        const style = getComputedStyle(element);
        return {
          backgroundColor: style.backgroundColor,
          backgroundImage: style.backgroundImage,
          backdropFilter: style.backdropFilter,
          boxShadow: style.boxShadow
        };
      };
      const selectedStyle = getComputedStyle(selected);
      const selectedIndicator = getComputedStyle(selected, "::after");
      const discoveryControlsStyle = getComputedStyle(discoveryControls);
      const discoveryControlsBackdrop = getComputedStyle(discoveryControls, "::before");
      const coverStyle = getComputedStyle(cover);
      const cardStyle = getComputedStyle(card);
      const enterStyle = getComputedStyle(enter);
      const bookBottomRect = bookBottom.getBoundingClientRect();
      const bookMetaRect = bookMeta.getBoundingClientRect();
      const enterRect = enter.getBoundingClientRect();
      const categoryListStyle = getComputedStyle(categoryList);
      const categoryScrollbarStyle = getComputedStyle(categoryList, "::-webkit-scrollbar");
      const categoryMoreFade = getComputedStyle(categoryMore, "::before");
      const screenContentRect = screenContent.getBoundingClientRect();
      const headerBarRect = headerBar.getBoundingClientRect();
      const discoveryControlsRect = discoveryControls.getBoundingClientRect();
      const searchRect = search.getBoundingClientRect();
      const categoryRailRect = categoryRail.getBoundingClientRect();
      const categoryMoreRect = categoryMore.getBoundingClientRect();
      const resultSummaryRect = resultSummary.getBoundingClientRect();
      return {
        app: read(app),
        discoveryControls: {
          position: discoveryControlsStyle.position,
          top: Number.parseFloat(discoveryControlsStyle.top),
          renderedTop: discoveryControlsRect.top,
          zIndex: discoveryControlsStyle.zIndex,
          headerBottom: headerBarRect.bottom,
          backdropBackgroundColor: discoveryControlsBackdrop.backgroundColor,
          backdropBoxShadow: discoveryControlsBackdrop.boxShadow
        },
        search: read(search),
        card: {
          ...read(card),
          borderTopWidth: cardStyle.borderTopWidth,
          borderRadius: cardStyle.borderRadius
        },
        cover: {
          ...read(cover),
          borderTopColor: coverStyle.borderTopColor,
          borderTopWidth: coverStyle.borderTopWidth,
          boxSizing: coverStyle.boxSizing
        },
        enter: {
          backgroundColor: enterStyle.backgroundColor,
          color: enterStyle.color,
          borderRadius: enterStyle.borderRadius,
          minHeight: enterStyle.minHeight,
          text: enter.textContent,
          rightGap: bookBottomRect.right - enterRect.right,
          alignedWithMeta: Math.abs(bookMetaRect.bottom - enterRect.bottom) < 4
        },
        selected: {
          ...read(selected),
          height: selected.getBoundingClientRect().height,
          borderRadius: selectedStyle.borderRadius,
          borderTopWidth: selectedStyle.borderTopWidth,
          fontSize: selectedStyle.fontSize,
          indicatorColor: selectedIndicator.backgroundColor
        },
        categoryRail: {
          ...read(categoryRail),
          left: categoryRailRect.left,
          right: categoryRailRect.right
        },
        categoryList: {
          overflowX: categoryListStyle.overflowX,
          backgroundColor: categoryListStyle.backgroundColor,
          borderBottomWidth: categoryListStyle.borderBottomWidth,
          scrollbarWidth: categoryListStyle.scrollbarWidth,
          webkitScrollbarDisplay: categoryScrollbarStyle.display,
          clientWidth: categoryList.clientWidth,
          scrollWidth: categoryList.scrollWidth
        },
        categoryMore: {
          width: categoryMoreRect.width,
          height: categoryMoreRect.height,
          right: categoryMoreRect.right,
          fadeBackgroundImage: categoryMoreFade.backgroundImage,
          fadePointerEvents: categoryMoreFade.pointerEvents,
          fadeWidth: Number.parseFloat(categoryMoreFade.width)
        },
        screenContent: {
          left: screenContentRect.left,
          right: screenContentRect.right
        },
        spacing: {
          searchToRail: categoryRailRect.top - searchRect.bottom,
          railToResults: resultSummaryRect.top - categoryRailRect.bottom
        },
        touchViolations: Array.from(document.querySelectorAll<HTMLElement>(".community-screen button"))
          .filter((element) => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
          })
          .map((element) => {
            const rect = element.getBoundingClientRect();
            return { label: element.getAttribute("aria-label") ?? element.textContent?.trim(), width: rect.width, height: rect.height };
          })
          .filter((target) => target.width < 43.5 || target.height < 43.5)
      };
    });

    expect(styles.app.backgroundColor).toBe("rgb(246, 248, 251)");
    expect(styles.discoveryControls).toMatchObject({
      position: "sticky",
      zIndex: "10",
      backdropBackgroundColor: "rgb(246, 248, 251)"
    });
    expect(styles.discoveryControls.top).toBe(0);
    expect(styles.discoveryControls.renderedTop).toBeGreaterThanOrEqual(styles.discoveryControls.headerBottom - 1);
    expect(styles.discoveryControls.backdropBoxShadow).not.toBe("none");
    expect(styles.search).toMatchObject({
      backgroundColor: "rgb(255, 255, 255)",
      backgroundImage: "none",
      backdropFilter: "none",
      boxShadow: "none"
    });
    expect(styles.card).toMatchObject({
      backgroundColor: "rgb(255, 255, 255)",
      backgroundImage: "none",
      backdropFilter: "none",
      borderTopWidth: "0px",
      borderRadius: "14px"
    });
    expect(styles.card.boxShadow).not.toBe("none");
    expect(styles.cover.borderTopColor).toBe("rgb(255, 255, 255)");
    expect(styles.cover.borderTopWidth).toBe("3px");
    expect(styles.cover.boxSizing).toBe("border-box");
    expect(styles.cover.boxShadow).not.toBe("none");
    expect(styles.enter).toMatchObject({
      backgroundColor: "rgb(124, 58, 237)",
      color: "rgb(255, 255, 255)",
      borderRadius: "999px",
      minHeight: "36px",
      text: "进入"
    });
    expect(Math.abs(styles.enter.rightGap), "the entry action stays on the card footer's right edge").toBeLessThan(1);
    expect(styles.enter.alignedWithMeta, "metadata and the entry action share one footer row").toBe(true);
    expect(styles.selected.backgroundColor).toBe("rgba(0, 0, 0, 0)");
    expect(styles.selected.backgroundImage).toBe("none");
    expect(styles.selected.backdropFilter).toBe("none");
    expect(styles.selected.borderRadius).toBe("0px");
    expect(styles.selected.borderTopWidth).toBe("0px");
    expect(styles.selected.fontSize).toBe("16.9px");
    expect(styles.selected.indicatorColor).toBe("rgb(124, 58, 237)");
    expect(styles.selected.height).toBeGreaterThanOrEqual(43.5);
    expect(styles.categoryRail).toMatchObject({
      backgroundColor: "rgb(215, 222, 232)",
      backgroundImage: "none",
      boxShadow: "none"
    });
    expect(styles.categoryList).toMatchObject({
      overflowX: "auto",
      backgroundColor: "rgba(0, 0, 0, 0)",
      borderBottomWidth: "0px",
      scrollbarWidth: "none",
      webkitScrollbarDisplay: "none"
    });
    expect(styles.categoryMore.width).toBeGreaterThanOrEqual(43.5);
    expect(styles.categoryMore.height).toBeGreaterThanOrEqual(43.5);
    expect(styles.categoryMore.right).toBeCloseTo(styles.categoryRail.right, 1);
    expect(styles.categoryMore.fadeBackgroundImage).toContain("linear-gradient");
    expect(styles.categoryMore.fadePointerEvents).toBe("none");
    expect(styles.categoryMore.fadeWidth).toBeGreaterThanOrEqual(47.5);
    if (project.initialViewport.width < 480) {
      expect(styles.categoryList.scrollWidth, "phone category rail can scroll horizontally").toBeGreaterThan(styles.categoryList.clientWidth);
      expect(Math.abs(styles.categoryRail.left - styles.screenContent.left), "phone category background reaches the left screen-content edge").toBeLessThan(1);
      expect(Math.abs(styles.categoryRail.right - styles.screenContent.right), "phone category background reaches the right screen-content edge").toBeLessThan(1);
    }
    expect(styles.spacing.searchToRail, "search and category rail use the compact vertical rhythm").toBeLessThan(24);
    expect(styles.spacing.railToResults, "category rail and result summary use the compact vertical rhythm").toBeLessThan(32);
    expect(styles.touchViolations, "all visible community buttons meet the 44px target").toEqual([]);

    await page.getByLabel("搜索课程", { exact: true }).fill("课");
    await expectTwoColumnCommunityGrid(page, `${project.name}: initial viewport`);

    const screenScroller = page.locator(".screen-content");
    const stickyScrollTarget = await screenScroller.evaluate((element) => {
      const maxScrollTop = element.scrollHeight - element.clientHeight;
      const target = Math.min(600, maxScrollTop);
      element.scrollTo({ top: target, behavior: "instant" });
      return target;
    });
    expect(stickyScrollTarget, `${project.name}: community has enough content to exercise sticky controls`).toBeGreaterThan(0);
    await expect.poll(
      () => screenScroller.evaluate((element) => element.scrollTop),
      { message: `${project.name}: community content scrolls below the discovery controls` }
    ).toBeGreaterThan(0);
    const stickyLayout = await page.evaluate(() => {
      const headerBar = document.querySelector<HTMLElement>(".header-bar");
      const controls = document.querySelector<HTMLElement>(".community-discovery-controls");
      const search = document.querySelector<HTMLElement>(".community-search-field");
      const categoryRail = document.querySelector<HTMLElement>(".community-category-rail");
      const aiOrb = document.querySelector<HTMLElement>(".ai-orb");
      if (!headerBar || !controls || !search || !categoryRail || !aiOrb) throw new Error("Sticky community controls are missing");
      const controlsStyle = getComputedStyle(controls);
      const controlsBackdrop = getComputedStyle(controls, "::before");
      const headerBackdrop = getComputedStyle(headerBar, "::before");
      const controlsRect = controls.getBoundingClientRect();
      const headerRect = headerBar.getBoundingClientRect();
      const searchRect = search.getBoundingClientRect();
      const categoryRailRect = categoryRail.getBoundingClientRect();
      const aiOrbRect = aiOrb.getBoundingClientRect();
      const orbOverlapsControls = !(
        aiOrbRect.right <= controlsRect.left ||
        aiOrbRect.left >= controlsRect.right ||
        aiOrbRect.bottom <= controlsRect.top ||
        aiOrbRect.top >= controlsRect.bottom
      );
      return {
        cssTop: Number.parseFloat(controlsStyle.top),
        renderedTop: controlsRect.top,
        headerBottom: headerRect.bottom,
        headerTop: headerRect.top,
        headerBackdropColor: headerBackdrop.backgroundColor,
        headerBackdropTop: headerRect.top + Number.parseFloat(headerBackdrop.top),
        headerBackdropBottom: headerRect.bottom - Number.parseFloat(headerBackdrop.bottom),
        controlsBackdropTop: controlsRect.top + Number.parseFloat(controlsBackdrop.top),
        layoutSpace: Math.abs(Number.parseFloat(controlsBackdrop.top)),
        searchVisible: searchRect.bottom > 0 && searchRect.top < window.innerHeight,
        categoryVisible: categoryRailRect.bottom > 0 && categoryRailRect.top < window.innerHeight,
        orbOverlapsControls
      };
    });
    expect(stickyLayout.cssTop, `${project.name}: the scroll container owns the header inset`).toBe(0);
    const stickyHeaderGap = stickyLayout.renderedTop - stickyLayout.headerBottom;
    expect(stickyHeaderGap, `${project.name}: sticky controls never overlap the global header`).toBeGreaterThanOrEqual(stickyLayout.layoutSpace - 1);
    expect(stickyHeaderGap, `${project.name}: sticky controls stay within the intended header spacing band`).toBeLessThanOrEqual(stickyLayout.layoutSpace + 4);
    expect(stickyLayout.searchVisible, `${project.name}: search remains visible while the course grid scrolls`).toBe(true);
    expect(stickyLayout.categoryVisible, `${project.name}: categories remain visible while the course grid scrolls`).toBe(true);
    expect(stickyLayout.orbOverlapsControls, `${project.name}: AI assistant never blocks sticky discovery controls`).toBe(false);
    if (project.name === "iphone-17-pro") {
      expect(stickyLayout.headerTop, "the community title sits directly below the iOS status bar").toBeLessThanOrEqual(66);
      expect(stickyLayout.headerBackdropColor, "the community header backdrop is opaque").toBe("rgb(246, 248, 251)");
      expect(stickyLayout.headerBackdropTop, "the opaque community header covers the top of the screen").toBeLessThanOrEqual(0.5);
      expect(stickyLayout.headerBackdropBottom, "the opaque header meets the sticky controls without a transparent seam").toBeGreaterThanOrEqual(stickyLayout.controlsBackdropTop - 0.5);
    }
    await screenScroller.evaluate((element) => element.scrollTo({ top: 0, behavior: "instant" }));

    const category = page.getByRole("button", { name: "数学", exact: true });
    let categoryReceivedKeyboardFocus = false;
    for (let index = 0; index < 20; index += 1) {
      await page.keyboard.press("Tab");
      categoryReceivedKeyboardFocus = await category.evaluate((element) => document.activeElement === element);
      if (categoryReceivedKeyboardFocus) break;
    }
    expect(categoryReceivedKeyboardFocus, "math category is keyboard reachable").toBe(true);
    const focus = await category.evaluate((element) => {
      const style = getComputedStyle(element);
      return { outlineStyle: style.outlineStyle, outlineWidth: Number.parseFloat(style.outlineWidth) };
    });
    expect(focus.outlineStyle).not.toBe("none");
    expect(focus.outlineWidth).toBeGreaterThanOrEqual(3);

    await page.setViewportSize(project.pairedViewport);
    await expectTwoColumnCommunityGrid(page, `${project.name}: paired viewport`);
  });

  test("keeps the selected mathematics book through detail and import", async ({ page }) => {
    await openCommunity(page);
    await page.getByRole("button", { name: "数学", exact: true }).click();
    await page.locator('[data-community-book-id="community_functions"]').click();

    await expect(page.locator(".community-detail-screen").getByRole("heading", { name: "函数与导数系统提升课", exact: true })).toBeVisible();
    const detail = page.locator(".community-detail-screen");
    await expect(detail.locator(".community-detail-chapters")).toHaveCount(0);
    await expect(detail.getByRole("heading", { name: "课程章节", exact: true })).toHaveCount(0);
    await expect(detail.locator('[data-stat="flashcards"] dd')).toHaveText("16 张");
    await expect(page.getByRole("navigation", { name: "主导航" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "打开 AI 助手" })).toHaveCount(0);
    await expect(detail.getByRole("button", { name: "返回社区", exact: true })).toHaveCount(0);
    await expect(detail.locator(".community-detail-stats > div")).toHaveCount(4);
    await expect(detail.getByRole("button", { name: "导入到我的课程", exact: true })).toHaveCount(1);

    const overviewTab = detail.getByRole("tab", { name: "课程简介", exact: true });
    const commentsTab = detail.getByRole("tab", { name: "评论", exact: true });
    await expect(detail.getByRole("tablist", { name: "课程详情内容", exact: true }).getByRole("tab")).toHaveCount(2);
    await expect(overviewTab).toHaveAttribute("aria-selected", "true");
    await expect(commentsTab).toHaveAttribute("aria-selected", "false");
    await expect(detail.getByRole("tabpanel")).toContainText("从函数图像、单调性到导数应用分层整理");
    await commentsTab.click();
    await expect(commentsTab).toHaveAttribute("aria-selected", "true");
    await expect(overviewTab).toHaveAttribute("aria-selected", "false");
    await expect(detail.getByRole("tabpanel")).toContainText("重点整理得很清楚");
    await expect(detail.getByRole("tabpanel")).toContainText("导入后继续学习很方便");
    await overviewTab.click();
    await expect(overviewTab).toHaveAttribute("aria-selected", "true");

    const detailLayout = await detail.evaluate((element) => {
      const overview = element.querySelector<HTMLElement>(".community-detail-overview");
      const visual = element.querySelector<HTMLElement>(".community-detail-visual");
      const summary = element.querySelector<HTMLElement>(".community-detail-summary");
      const actions = element.querySelector<HTMLElement>(".community-detail-actions");
      const cover = element.querySelector<HTMLElement>(".community-detail-cover");
      const main = element.closest<HTMLElement>(".screen-content");
      const shell = main?.closest<HTMLElement>(".app-shell");
      const headerBar = shell?.querySelector<HTMLElement>(".header-bar");
      const headerGlass = shell?.querySelector<HTMLElement>(".header-glass");
      if (!overview || !visual || !summary || !actions || !cover || !main || !shell || !headerBar || !headerGlass) {
        throw new Error("Community detail layout is incomplete");
      }
      const overviewStyle = getComputedStyle(overview);
      const visualStyle = getComputedStyle(visual);
      const summaryStyle = getComputedStyle(summary);
      const actionStyle = getComputedStyle(actions);
      const headerBackdropStyle = getComputedStyle(headerBar, "::before");
      const coverRect = cover.getBoundingClientRect();
      const overviewRect = overview.getBoundingClientRect();
      const visualRect = visual.getBoundingClientRect();
      const summaryRect = summary.getBoundingClientRect();
      const mainRect = main.getBoundingClientRect();
      return {
        actionBottom: actionStyle.bottom,
        actionPosition: actionStyle.position,
        coverHeight: coverRect.height,
        coverRatio: coverRect.width / coverRect.height,
        coverToOverviewRatio: coverRect.width / overviewRect.width,
        coverWidth: coverRect.width,
        headerBackground: getComputedStyle(headerGlass).backgroundColor,
        headerBackdropBackground: headerBackdropStyle.backgroundColor,
        headerBackdropBottom: Number.parseFloat(headerBackdropStyle.bottom),
        horizontalOverflow: main.scrollWidth > main.clientWidth,
        overviewBackground: overviewStyle.backgroundColor,
        overviewBackgroundImage: overviewStyle.backgroundImage,
        overviewRadius: Number.parseFloat(overviewStyle.borderTopLeftRadius),
        overviewShadow: overviewStyle.boxShadow,
        summaryBackground: summaryStyle.backgroundColor,
        summaryBottomRadius: Number.parseFloat(summaryStyle.borderBottomLeftRadius),
        summaryFillRatio: summaryRect.width / mainRect.width,
        summaryOverlap: visualRect.bottom - summaryRect.top,
        summaryRadius: Number.parseFloat(summaryStyle.borderTopLeftRadius),
        shellBackground: getComputedStyle(shell).backgroundColor,
        visualBackground: visualStyle.backgroundColor,
        visualBackgroundImage: visualStyle.backgroundImage,
        visualFillRatio: visualRect.width / mainRect.width,
        visualLeftOffset: Math.abs(visualRect.left - mainRect.left)
      };
    });
    expect(detailLayout).toMatchObject({
      actionBottom: "0px",
      actionPosition: "fixed",
      headerBackground: "rgb(221, 214, 254)",
      headerBackdropBackground: "rgb(221, 214, 254)",
      horizontalOverflow: false,
      overviewBackground: "rgba(0, 0, 0, 0)",
      overviewBackgroundImage: "none",
      shellBackground: "rgb(221, 214, 254)",
      summaryBackground: "rgb(255, 255, 255)",
      visualBackground: "rgb(221, 214, 254)",
      visualBackgroundImage: "none"
    });
    expect(detailLayout.coverRatio).toBeCloseTo(0.75, 2);
    expect(detailLayout.coverWidth).toBeGreaterThanOrEqual(236.5);
    expect(detailLayout.coverHeight).toBeGreaterThanOrEqual(315.5);
    expect(detailLayout.coverToOverviewRatio).toBeGreaterThanOrEqual(0.25);
    expect(detailLayout.coverToOverviewRatio).toBeLessThanOrEqual(0.65);
    expect(detailLayout.headerBackdropBottom).toBeLessThanOrEqual(-16);
    expect(detailLayout.overviewRadius).toBe(0);
    expect(detailLayout.summaryRadius).toBeCloseTo(56, 1);
    expect(detailLayout.summaryBottomRadius).toBe(0);
    expect(detailLayout.summaryOverlap).toBeCloseTo(56, 1);
    expect(detailLayout.summaryFillRatio).toBeGreaterThanOrEqual(0.99);
    expect(detailLayout.visualFillRatio).toBeGreaterThanOrEqual(0.99);
    expect(detailLayout.visualLeftOffset).toBeLessThanOrEqual(1);
    expect(detailLayout.overviewShadow).toBe("none");
    const detailCover = page.locator('.community-detail-screen img[src="/assets/community/functions-derivatives-cover-v1.webp"]');
    await expect(detailCover).toBeVisible();
    expect(await detailCover.evaluate((image) => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0)).toBe(true);

    const detailScroller = page.locator('.screen-content[data-screen="communityBook"]');
    await detailScroller.evaluate((element) => {
      element.scrollTop = 0;
    });
    await expect(detail).toHaveAttribute("data-collapsed", "false");
    await expect(detail.locator(".community-detail-visual")).toHaveAttribute("aria-hidden", "false");
    await expect(detailCover).toHaveCSS("opacity", "1");
    const collapseGeometry = await detail.evaluate((element) => {
      const main = element.closest<HTMLElement>(".screen-content");
      const header = document.querySelector<HTMLElement>(".header-bar");
      const sentinel = element.querySelector<HTMLElement>(".community-detail-collapse-sentinel");
      if (!main || !header || !sentinel) throw new Error("Community detail collapse geometry is incomplete");
      const headerBackdrop = getComputedStyle(header, "::before");
      const headerBackdropEdge = header.getBoundingClientRect().bottom - Number.parseFloat(headerBackdrop.bottom);
      return {
        maxScrollTop: main.scrollHeight - main.clientHeight,
        targetScrollTop: main.scrollTop + sentinel.getBoundingClientRect().top - headerBackdropEdge + 2,
      };
    });
    if (collapseGeometry.maxScrollTop >= collapseGeometry.targetScrollTop) {
      await detailScroller.evaluate((element, scrollTop) => {
        element.scrollTop = scrollTop;
      }, collapseGeometry.targetScrollTop);
      await expect(detail).toHaveAttribute("data-collapsed", "true");
      await expect(detail.locator(".community-detail-visual")).toHaveAttribute("aria-hidden", "true");
      await expect(detailCover).toHaveCSS("opacity", "0");
      const collapsedLayout = await detail.evaluate((element) => {
        const header = document.querySelector<HTMLElement>(".header-bar");
        const edition = element.querySelector<HTMLElement>(".community-detail-edition");
        const owner = element.querySelector<HTMLElement>(".community-detail-owner");
        const stats = element.querySelector<HTMLElement>(".community-detail-stats");
        const summary = element.querySelector<HTMLElement>(".community-detail-summary");
        const tabs = element.querySelector<HTMLElement>(".community-detail-tabs");
        const title = element.querySelector<HTMLElement>(".community-detail-summary h2");
        const actions = element.querySelector<HTMLElement>(".community-detail-actions");
        if (!header || !edition || !owner || !stats || !summary || !tabs || !title || !actions) {
          throw new Error("Community detail collapsed state is incomplete");
        }
        const headerBackdrop = getComputedStyle(header, "::before");
        const headerBackdropEdge = header.getBoundingClientRect().bottom - Number.parseFloat(headerBackdrop.bottom);
        return {
          actionBottom: getComputedStyle(actions).bottom,
          actionPosition: getComputedStyle(actions).position,
          informationBlockHeight: edition.getBoundingClientRect().bottom - owner.getBoundingClientRect().top,
          ownerInset: owner.getBoundingClientRect().top - summary.getBoundingClientRect().top,
          summaryTopDelta: summary.getBoundingClientRect().top - headerBackdropEdge,
          tabsInset: tabs.getBoundingClientRect().top - summary.getBoundingClientRect().top,
          titleEditionGap: edition.getBoundingClientRect().top - title.getBoundingClientRect().bottom,
          ownerTitleGap: title.getBoundingClientRect().top - owner.getBoundingClientRect().bottom,
          statsGap: stats.getBoundingClientRect().top - edition.getBoundingClientRect().bottom,
        };
      });
      expect(collapsedLayout.actionBottom).toBe("0px");
      expect(collapsedLayout.actionPosition).toBe("fixed");
      expect(collapsedLayout.informationBlockHeight).toBeLessThanOrEqual(96);
      expect(collapsedLayout.ownerInset).toBeGreaterThanOrEqual(38);
      expect(collapsedLayout.ownerInset).toBeLessThanOrEqual(48);
      expect(collapsedLayout.ownerTitleGap).toBeLessThanOrEqual(12);
      expect(collapsedLayout.titleEditionGap).toBeLessThanOrEqual(12);
      expect(collapsedLayout.statsGap).toBeLessThanOrEqual(28);
      expect(collapsedLayout.tabsInset).toBeLessThanOrEqual(260);
      expect(Math.abs(collapsedLayout.summaryTopDelta)).toBeLessThanOrEqual(2);
      await detailScroller.evaluate((element) => {
        element.scrollTop = 0;
      });
      await expect(detail).toHaveAttribute("data-collapsed", "false");
      await expect(detailCover).toHaveCSS("opacity", "1");
    } else {
      await expect(detail).toHaveAttribute("data-collapsed", "false");
      await expect(detailCover).toHaveCSS("opacity", "1");
    }

    await page.getByRole("button", { name: "导入到我的课程", exact: true }).click();
    await expect(page.locator(".community-import-screen").getByRole("heading", { name: "导入成功", exact: true })).toBeVisible();
    await expect(page.getByText("已把「函数与导数系统提升课」加入你的课程空间，并生成章节进度、闪卡、练习和笔记模块。", { exact: true })).toBeVisible();
    await expect(page.locator(".community-import-screen .metric-card").filter({ hasText: "章节" }).locator("strong")).toHaveText("3");
    await expect(page.locator(".community-import-screen .metric-card").filter({ hasText: "闪卡" }).locator("strong")).toHaveText("16");
  });

  test("opens and imports a PDF-backed chemistry course", async ({ page }) => {
    await openCommunity(page);
    const categoryRail = page.getByRole("group", { name: "按学科筛选书籍" });
    await categoryRail.getByRole("button", { name: "化学", exact: true }).click();
    await page.locator('[data-community-book-id="community_high_school_chemistry_2"]').click();

    const detail = page.locator(".community-detail-screen");
    await expect(detail.getByRole("heading", { name: "化学必修第二册同步课", exact: true })).toBeVisible();
    await expect(detail.locator(".community-detail-chapters")).toHaveCount(0);
    await expect(detail.locator('.community-detail-cover[src="/assets/community/high-school-chemistry-required-2-cover.webp"]')).toBeVisible();
    await expect(detail.locator('[data-stat="flashcards"] dd')).toHaveText("26 张");

    await detail.getByRole("button", { name: "导入到我的课程", exact: true }).click();
    const imported = page.locator(".community-import-screen");
    await expect(imported.getByRole("heading", { name: "导入成功", exact: true })).toBeVisible();
    await expect(imported).toContainText("已把「化学必修第二册同步课」加入你的课程空间");
    await expect(imported.locator(".metric-card").filter({ hasText: "章节" }).locator("strong")).toHaveText("4");
    await expect(imported.locator(".metric-card").filter({ hasText: "闪卡" }).locator("strong")).toHaveText("26");
  });

  test("keeps card geometry stable when generated covers fail", async ({ page }) => {
    await page.route("**/assets/community/functions-derivatives-cover-v1.webp", (route) => route.abort("failed"));
    await page.route("**/assets/community/force-motion-cover-v1.webp", (route) => route.abort("failed"));
    await openCommunity(page);
    await page.getByLabel("搜索课程", { exact: true }).fill("课");

    await expect(page.getByRole("img", { name: "函数与导数系统提升课 封面不可用", exact: true })).toBeVisible();
    await expect(page.getByRole("img", { name: "力与运动实验精讲课 封面不可用", exact: true })).toBeVisible();
    await expectTwoColumnCommunityGrid(page, "failed community covers");

    const coverGeometry = await page.locator(".community-book-card").evaluateAll((cards) => cards.map((card) => {
      const cover = card.querySelector<HTMLElement>(".community-book-cover, .community-book-cover-fallback");
      if (!cover) throw new Error("Community cover is missing");
      const rect = cover.getBoundingClientRect();
      return { width: rect.width, height: rect.height };
    }));
    expect(coverGeometry.every((cover) => Math.abs(cover.width / cover.height - 0.75) < 0.01), "loaded and fallback covers keep the 3:4 ratio").toBe(true);
  });
});
