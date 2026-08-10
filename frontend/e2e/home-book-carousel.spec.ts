import { expect, test, type Locator, type Page } from "playwright/test";

async function clickAfterMotionAndScrollSettle(page: Page, target: Locator, label: string) {
  const screenTransition = page.locator(".motion-screen-transition");
  const screenTransitionCount = await screenTransition.count();
  if (screenTransitionCount > 0) {
    expect(screenTransitionCount, `${label}: production renders one screen transition`).toBe(1);
    await expect(screenTransition, `${label}: screen transition is idle`).toHaveAttribute("data-motion-state", "idle");
  }
  await expect(target, `${label}: action is unique`).toHaveCount(1);
  await expect(target, `${label}: action is visible`).toBeVisible();
  await expect(target, `${label}: action is enabled`).toBeEnabled();
  await target.evaluate(async (element) => {
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
      if (firstFrame.some((value, index) => value !== secondFrame[index])) {
        throw new Error("Home action did not settle across two animation frames");
      }
    } finally {
      if (scroller) scroller.style.scrollBehavior = previousScrollBehavior;
    }
  });
  await target.click({ trial: true });
  await target.click();
}

test.describe("home book carousel", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", reducedMotion: "reduce", timezoneId: "Asia/Hong_Kong" });

  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/e2e/home-book-carousel-harness.html");
    await expect(page.locator("#home-book-carousel-harness")).toBeVisible();
  });

  test("switches ready books with blur, keyboard semantics, and an atomic loading workspace", async ({ page }) => {
    const listbox = page.getByRole("listbox", { name: "选择教材" });
    const options = listbox.getByRole("option");
    await expect(options).toHaveCount(10);
    const first = options.nth(0);
    const second = options.nth(1);
    await expect(first).toHaveAttribute("aria-selected", "true");

    const coverPresentation = await options.evaluateAll((elements) => elements.map((element) => {
      const optionStyle = getComputedStyle(element);
      const coverStyle = getComputedStyle(element.querySelector<HTMLElement>(".home-book-cover")!);
      return {
        selected: element.getAttribute("aria-selected"),
        filter: coverStyle.filter,
        transitionDurationMs: Number.parseFloat(optionStyle.transitionDuration) * 1_000
      };
    }));
    expect(coverPresentation[0].filter).toBe("none");
    expect(coverPresentation.slice(1).every((cover) => cover.filter.includes("blur(1.5px)"))).toBe(true);
    expect(
      coverPresentation.every((cover) => cover.transitionDurationMs <= .1),
      JSON.stringify(coverPresentation)
    ).toBe(true);

    await first.focus();
    await expect(first).toHaveCSS("outline-style", "solid");
    await expect(first).toHaveCSS("outline-width", "3px");
    await first.press("ArrowRight");
    await expect(second).toBeFocused();
    await expect(second).toHaveAttribute("aria-selected", "true");
    const loadingWorkspace = page.locator('.home-book-workspace[data-book-id="book-b"]');
    await expect(loadingWorkspace).toHaveAttribute("aria-busy", "true");
    await expect(loadingWorkspace).toHaveAttribute("data-loaded", "false");
    await expect(loadingWorkspace.locator(".home-workspace-loading-actions")).toBeVisible();
    await expect(loadingWorkspace.locator(".home-workspace-loading-tools > div > span")).toHaveCount(3);
    await expect(loadingWorkspace.locator("button, [data-tool]")).toHaveCount(0);
    await expect(loadingWorkspace.getByRole("status")).toContainText("学习操作暂不可用");
    await expect(loadingWorkspace).toHaveAttribute("data-loaded", "true");
    await expect(loadingWorkspace.getByRole("button", { name: "继续学习", exact: true })).toBeEnabled();

    const layout = await page.evaluate(() => {
      const longCover = document.querySelector<HTMLElement>('[data-book-id="book-b"] .home-book-cover');
      const longTitle = longCover?.querySelector<HTMLElement>("strong");
      return {
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: document.documentElement.clientWidth,
        titleInsideCover: Boolean(
          longCover
          && longTitle
          && longTitle.getBoundingClientRect().right <= longCover.getBoundingClientRect().right
          && longTitle.getBoundingClientRect().bottom <= longCover.getBoundingClientRect().bottom
        )
      };
    });
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
    expect(layout.titleInsideCover).toBe(true);
  });

  test("keeps non-ready books in summary mode without starting a full load", async ({ page }) => {
    const options = page.getByRole("listbox", { name: "选择教材" }).getByRole("option");
    await options.nth(2).click();
    await expect(options.nth(2)).toHaveAttribute("aria-selected", "true");
    await expect(page.locator('.home-book-workspace[data-book-id="book-c"]')).toHaveClass(/is-processing/);
    await expect(page.getByRole("button", { name: "查看整理详情", exact: true })).toBeVisible();
    expect(await page.evaluate(() => window.__homeBookCarouselHarness?.getLoadRequests())).toEqual([]);
  });

  test("renders mutually exclusive safe workspaces for every non-ready and unknown state", async ({ page }, testInfo) => {
    const listbox = page.getByRole("listbox", { name: "选择教材" });
    const cases = [
      { bookId: "book-c", state: "processing", heading: "正在整理教材", action: "查看整理详情" },
      { bookId: "book-e", state: "needs_review", heading: "课程目录等待确认", action: "确认课程目录" },
      { bookId: "book-f", state: "uploaded", heading: "教材已上传", action: "开始整理教材" },
      { bookId: "book-g", state: "error", heading: "教材整理没有完成", action: "查看处理详情" },
      { bookId: "book-d", state: "unknown", heading: "教材状态暂未同步", action: "查看教材详情" },
      { bookId: "book-h", state: "error", heading: "教材整理没有完成", action: "查看处理详情" },
      { bookId: "book-i", state: "error", heading: "教材整理没有完成", action: "查看处理详情" },
      { bookId: "book-j", state: "uploaded", heading: "教材已上传", action: "查看处理详情" }
    ] as const;

    for (const item of cases) {
      await listbox.locator(`[data-book-id="${item.bookId}"]`).click();
      const workspace = page.locator(`.home-book-workspace[data-book-id="${item.bookId}"]`);
      await expect(workspace).toHaveClass(new RegExp(`is-${item.state}`));
      await expect(workspace.locator("h2")).toHaveText(item.heading);
      await expect(workspace.getByRole("button", { name: item.action, exact: true })).toBeVisible();
      await expect(workspace).toHaveAttribute("data-loaded", "false");
      await expect(workspace.locator(".study-tool-grid, [data-tool]")).toHaveCount(0);
      await expect(workspace.getByRole("button", { name: "继续学习", exact: true })).toHaveCount(0);
      await workspace.getByRole("button", { name: item.action, exact: true }).click();
    }

    await listbox.locator('[data-book-id="book-c"]').click();
    await expect(page.getByRole("progressbar", { name: "教材整理进度" })).toHaveAttribute("aria-valuenow", "58");

    await listbox.locator('[data-book-id="book-e"]').click();
    await expect(page.getByRole("button", { name: "打开原书", exact: true })).toBeVisible();

    await listbox.locator('[data-book-id="book-f"]').click();
    await expect(page.getByRole("button", { name: "打开原书", exact: true })).toHaveCount(0);

    await listbox.locator('[data-book-id="book-g"]').click();
    await expect(page.getByRole("button", { name: "打开原书", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "重新上传", exact: true })).toBeVisible();
    if (testInfo.project.name === "iphone-17-pro") {
      await page.locator('.home-book-workspace[data-book-id="book-g"]').screenshot({
        path: "output/playwright/phase4-home-error-state-iphone.png"
      });
    }

    await listbox.locator('[data-book-id="book-h"]').click();
    const readableRemoteError = page.locator('.home-book-workspace[data-book-id="book-h"]');
    await expect(readableRemoteError.getByRole("button", { name: "打开原书", exact: true })).toBeVisible();
    await expect(readableRemoteError.getByRole("button", { name: "重新上传", exact: true })).toBeVisible();
    const mobileLayout = await readableRemoteError.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
      targets: Array.from(element.querySelectorAll<HTMLElement>("button")).map((button) => ({
        height: button.getBoundingClientRect().height,
        width: button.getBoundingClientRect().width
      }))
    }));
    expect(mobileLayout.scrollWidth).toBeLessThanOrEqual(mobileLayout.clientWidth);
    expect(mobileLayout.targets.every((target) => target.height >= 44 && target.width >= 44), JSON.stringify(mobileLayout.targets)).toBe(true);

    await listbox.locator('[data-book-id="book-i"]').click();
    const localError = page.locator('.home-book-workspace[data-book-id="book-i"]');
    await expect(localError.getByRole("button", { name: "打开原书", exact: true })).toBeVisible();
    await expect(localError.getByRole("button", { name: "重新整理", exact: true })).toBeVisible();

    await listbox.locator('[data-book-id="book-j"]').click();
    const remoteUpload = page.locator('.home-book-workspace[data-book-id="book-j"]');
    await expect(remoteUpload.getByRole("button", { name: "查看处理详情", exact: true })).toBeVisible();
    await expect(remoteUpload.getByRole("button", { name: "开始整理教材", exact: true })).toHaveCount(0);

    await listbox.locator('[data-book-id="book-d"]').click();
    await expect(page.getByRole("button", { name: "打开原书", exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => window.__homeBookCarouselHarness?.getLoadRequests())).toEqual([]);
    expect(await page.evaluate(() => window.__homeBookCarouselHarness?.getStatusActions())).toEqual([
      { bookId: "book-c", target: "processing" },
      { bookId: "book-e", target: "chapterConfirm" },
      { bookId: "book-f", target: "parseReady" },
      { bookId: "book-g", target: "library" },
      { bookId: "book-d", target: "library" },
      { bookId: "book-h", target: "library" },
      { bookId: "book-i", target: "processing" },
      { bookId: "book-j", target: "library" }
    ]);
  });

  test("restores the previously loaded book when a candidate fails", async ({ page }) => {
    await page.evaluate(() => window.__homeBookCarouselHarness?.failNextSelection("book-b"));
    const options = page.getByRole("listbox", { name: "选择教材" }).getByRole("option");
    await options.nth(1).click();
    await expect(page.locator('.home-book-workspace[data-book-id="book-b"]')).toHaveAttribute("aria-busy", "true");
    await expect(options.nth(0)).toHaveAttribute("aria-selected", "true");
    await expect(page.locator('.home-book-workspace[data-book-id="book-a"]')).toHaveAttribute("data-loaded", "true");
    const announcement = page.getByRole("status").filter({ hasText: /未能打开.*已回到/ });
    await expect(announcement).toBeVisible();
    await expect(announcement).toHaveAttribute("aria-live", "polite");
    await announcement.getByRole("button", { name: "重试切换", exact: true }).click();
    const retryWorkspace = page.locator('.home-book-workspace[data-book-id="book-b"]');
    await expect(retryWorkspace).toHaveAttribute("aria-busy", "true");
    await expect(retryWorkspace.getByRole("status")).toContainText("正在准备");
    await expect(retryWorkspace).toHaveAttribute("data-loaded", "true");
  });

  test("keeps a failed first ready book honest when there is no previous loaded book", async ({ page }) => {
    await page.evaluate(() => {
      window.__homeBookCarouselHarness?.clearLoadedBook();
      window.__homeBookCarouselHarness?.failNextSelection("book-b");
    });
    const options = page.getByRole("listbox", { name: "选择教材" }).getByRole("option");
    await options.nth(1).click();
    await expect(page.locator('.home-book-workspace[data-book-id="book-b"]')).toHaveAttribute("aria-busy", "true");
    await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText(/未能打开.*请检查连接后重试/)).toBeVisible();
    await expect(page.locator('.home-book-workspace[data-book-id="book-b"] h2')).toHaveText("暂时无法打开这本教材");
    await expect(page.getByRole("button", { name: "查看教材详情", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "继续学习", exact: true })).toHaveCount(0);
  });

  test("blocks stale A resources when the active processing session belongs to B", async ({ page }) => {
    const identityGate = page.getByRole("region", { name: "教材资源身份门禁" });
    await expect(identityGate.locator(".study-directory")).toHaveAttribute("data-book-id", "book-a");
    await expect(identityGate).toHaveAttribute("data-switcher-selected-book-id", "book-a");

    await page.evaluate(() => {
      window.__homeBookCarouselHarness?.setResourceIdentity("book-a", "book-b", "book-a");
    });

    await expect(identityGate).toHaveAttribute("data-loaded-book-id", "book-a");
    await expect(identityGate).toHaveAttribute("data-session-book-id", "book-b");
    await expect(identityGate.locator(".study-directory")).toHaveCount(0);
    await expect(identityGate.locator(".study-empty-state")).toHaveText("教材还在准备中");
    await expect(identityGate).toHaveAttribute("data-switcher-selected-book-id", "");
  });

  test("clears a removed remote loaded course after a successful summaries refresh", async ({ page }) => {
    const identityGate = page.getByRole("region", { name: "教材资源身份门禁" });
    await page.evaluate(() => window.__homeBookCarouselHarness?.applySuccessfulRefresh([]));

    await expect(identityGate).toHaveAttribute("data-loaded-book-id", "");
    await expect(identityGate).toHaveAttribute("data-session-book-id", "");
    await expect(identityGate).toHaveAttribute("data-resource-book-id", "");
    await expect(identityGate.locator(".study-directory")).toHaveCount(0);
    await expect(identityGate.locator(".study-empty-state")).toBeVisible();
  });

  test("deleting loaded A clears its payload even when the active session is B", async ({ page }) => {
    const identityGate = page.getByRole("region", { name: "教材资源身份门禁" });
    await page.evaluate(() => window.__homeBookCarouselHarness?.setResourceIdentity("book-a", "book-b", "book-a"));
    await expect(identityGate).toHaveAttribute("data-session-book-id", "book-b");
    await page.evaluate(() => window.__homeBookCarouselHarness?.deleteBook("book-a"));

    await expect(identityGate).toHaveAttribute("data-loaded-book-id", "");
    await expect(identityGate).toHaveAttribute("data-session-book-id", "book-b");
    await expect(identityGate).toHaveAttribute("data-resource-book-id", "");
    await expect(identityGate.locator(".study-directory")).toHaveCount(0);
  });

  test("embeds the shared chapter tools and keeps every action on the same book and chapter", async ({ page }) => {
    const workspace = page.locator('.home-book-workspace[data-book-id="book-a"]');
    await expect(workspace).toHaveAttribute("data-loaded", "true");
    await expect(workspace).toHaveAttribute("data-chapter-id", "chapter-book-a");

    const toolGrid = workspace.locator(".study-tool-grid");
    await expect(toolGrid).toBeVisible();
    await expect(toolGrid.locator('[data-tool="assignment"]')).toBeEnabled();
    await expect(toolGrid.locator('[data-tool="flashcards"]')).toBeEnabled();
    await expect(toolGrid.locator('[data-tool="future"]')).toBeDisabled();

    await workspace.getByRole("button", { name: "继续学习", exact: true }).click();
    await clickAfterMotionAndScrollSettle(
      page,
      workspace.getByRole("button", { name: "回到原书", exact: true }),
      "open current Home chapter source"
    );
    await toolGrid.locator('[data-tool="assignment"]').click();
    await toolGrid.locator('[data-tool="flashcards"]').click();

    expect(await page.evaluate(() => window.__homeBookCarouselHarness?.getWorkspaceActions())).toEqual([
      { destination: "lesson", bookId: "book-a", chapterId: "chapter-book-a" },
      { destination: "source", bookId: "book-a", chapterId: "chapter-book-a" },
      { destination: "assignment", bookId: "book-a", chapterId: "chapter-book-a" },
      { destination: "flashcards", bookId: "book-a", chapterId: "chapter-book-a" }
    ]);
  });

  test("hides chapter tools when a ready book does not have a complete loaded context", async ({ page }) => {
    await page.evaluate(() => window.__homeBookCarouselHarness?.setResourceIdentity("book-a", "book-a", null));
    const workspace = page.locator('.home-book-workspace[data-book-id="book-a"]');

    await expect(workspace).toHaveAttribute("data-loaded", "false");
    await expect(workspace.locator(".study-tool-grid")).toHaveCount(0);
    await expect(workspace.getByRole("button", { name: "回到原书", exact: true })).toHaveCount(0);
  });

  test("keeps the embedded tool workspace within the iPhone width and touch-safe", async ({ page }) => {
    await page.setViewportSize({ width: 402, height: 874 });
    const workspace = page.locator('.home-book-workspace[data-book-id="book-a"]');
    const layout = await workspace.evaluate((element) => {
      const workspaceBounds = element.getBoundingClientRect();
      const actions = element.querySelector<HTMLElement>(".home-workspace-actions")?.getBoundingClientRect();
      return {
        clientWidth: element.clientWidth,
        copy: element.querySelector<HTMLElement>(".home-workspace-copy")?.getBoundingClientRect().toJSON(),
        actions: actions?.toJSON(),
        actionRightInset: actions ? workspaceBounds.right - actions.right : null,
        height: workspaceBounds.height,
        scrollWidth: element.scrollWidth,
        toolCards: Array.from(element.querySelectorAll<HTMLElement>(".study-tool-card")).map((card) => ({
          height: card.getBoundingClientRect().height,
          width: card.getBoundingClientRect().width
        })),
        toolGrid: element.querySelector<HTMLElement>(".study-tool-grid")
          ? {
              clientWidth: element.querySelector<HTMLElement>(".study-tool-grid")!.clientWidth,
              scrollWidth: element.querySelector<HTMLElement>(".study-tool-grid")!.scrollWidth
            }
          : null,
        targets: Array.from(element.querySelectorAll<HTMLElement>("button")).map((button) => ({
          height: button.getBoundingClientRect().height,
          width: button.getBoundingClientRect().width
        }))
      };
    });

    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
    expect(layout.height, "the compact next-step workspace no longer keeps the old 610px minimum").toBeLessThan(390);
    expect(layout.copy?.right ?? 0, "chapter copy stays to the left of the action column").toBeLessThanOrEqual(layout.actions?.left ?? 0);
    expect(layout.actionRightInset, "the chapter action group stays against the workspace's 16px right padding").toBeGreaterThanOrEqual(15);
    expect(layout.actionRightInset, "the chapter action group stays against the workspace's 16px right padding").toBeLessThanOrEqual(17);
    expect(layout.toolCards.every((card) => Math.abs(card.height - 134) <= 1), JSON.stringify(layout.toolCards)).toBe(true);
    expect(layout.toolGrid?.scrollWidth ?? 0, "the compact chapter tools stay on one horizontal row").toBeGreaterThan(layout.toolGrid?.clientWidth ?? 0);
    expect(layout.targets.every((target) => target.height >= 44 && target.width >= 44), JSON.stringify(layout.targets)).toBe(true);
    await expect(workspace.getByText("从上次展开的小节继续", { exact: true })).toHaveCount(0);
    await expect(workspace.locator(".home-workspace-pages")).toHaveCount(0);
  });

  test("switches books with a mouse grab drag", async ({ page }) => {
    const listbox = page.getByRole("listbox", { name: "选择教材" });
    const options = listbox.getByRole("option");
    await listbox.evaluate((element) => {
      element.style.width = "320px";
      element.style.marginInline = "auto";
    });
    const dragDistance = await listbox.evaluate((element) => {
      const firstOption = element.querySelector<HTMLElement>('[role="option"]');
      const gap = Number.parseFloat(getComputedStyle(element).columnGap) || 0;
      return (firstOption?.getBoundingClientRect().width ?? 0) + gap;
    });
    await expect(options.first()).toHaveAttribute("aria-selected", "true");
    const selectedBounds = await options.first().boundingBox();
    if (!selectedBounds) throw new Error("Selected book bounds are unavailable for mouse drag");

    await page.mouse.move(
      selectedBounds.x + selectedBounds.width / 2,
      selectedBounds.y + selectedBounds.height / 2
    );
    await page.mouse.down();
    await page.mouse.move(
      selectedBounds.x + selectedBounds.width / 2 - dragDistance,
      selectedBounds.y + selectedBounds.height / 2,
      { steps: 8 }
    );
    await expect(listbox).toHaveClass(/is-dragging/);
    await page.mouse.up();

    await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
    await expect(listbox).not.toHaveClass(/is-dragging/);

    const secondBounds = await options.nth(1).boundingBox();
    if (!secondBounds) throw new Error("Second book bounds are unavailable for reverse mouse drag");
    await page.mouse.move(
      secondBounds.x + secondBounds.width / 2,
      secondBounds.y + secondBounds.height / 2
    );
    await page.mouse.down();
    await page.mouse.move(
      secondBounds.x + secondBounds.width / 2 + dragDistance,
      secondBounds.y + secondBounds.height / 2,
      { steps: 8 }
    );
    await page.mouse.up();

    await expect(options.first()).toHaveAttribute("aria-selected", "true");
  });

  test("commits the nearest book after real scrolling and clamps Home/End boundaries", async ({ page }) => {
    const listbox = page.getByRole("listbox", { name: "选择教材" });
    const options = listbox.getByRole("option");
    await expect(listbox).toHaveCSS("scroll-snap-type", "x mandatory");
    await expect(options.first()).toHaveCSS("scroll-snap-align", "center");
    const selectedDuringScroll = await listbox.evaluate((element) => {
      element.style.width = "320px";
      element.style.marginInline = "auto";
      element.scrollTo({ left: element.scrollWidth, behavior: "auto" });
      element.dispatchEvent(new Event("scroll"));
      return element.querySelector('[role="option"][aria-selected="true"]')?.getAttribute("data-book-id");
    });
    expect(selectedDuringScroll, "scroll events do not commit a book before the settle boundary").toBe("book-a");
    await expect(options.last()).toHaveAttribute("aria-selected", "true");

    await options.last().focus();
    await options.last().press("End");
    await expect(options.last()).toHaveAttribute("aria-selected", "true");
    await options.last().press("ArrowRight");
    await expect(options.last()).toHaveAttribute("aria-selected", "true");

    await options.last().press("Home");
    await expect(options.first()).toBeFocused();
    await expect(options.first()).toHaveAttribute("aria-selected", "true");
    await options.first().press("ArrowLeft");
    await expect(options.first()).toHaveAttribute("aria-selected", "true");
  });

  test("centers books only on the horizontal axis without moving the page", async ({ page }) => {
    await page.setViewportSize({ width: 874, height: 402 });
    const listbox = page.getByRole("listbox", { name: "选择教材" });
    const options = listbox.getByRole("option");
    const first = options.first();
    const second = options.nth(1);

    await first.focus();
    await page.evaluate(() => window.scrollTo({ top: 180, behavior: "auto" }));
    const initialWindowScroll = await page.evaluate(() => window.scrollY);
    expect(initialWindowScroll, "the regression probe starts with a meaningful vertical page offset").toBeGreaterThan(0);

    await page.keyboard.press("ArrowRight");
    await expect(second).toBeFocused();
    await expect(second).toHaveAttribute("aria-selected", "true");
    await expect.poll(
      () => page.evaluate(() => window.scrollY),
      "horizontal book centering does not change the page's vertical scroll position"
    ).toBe(initialWindowScroll);
    expect(await listbox.evaluate((element) => element.scrollLeft), "the carousel still centers the selected book horizontally").toBeGreaterThan(0);
  });

  test("handles zero, one, two, and many books without losing selection semantics", async ({ page }) => {
    await page.evaluate(() => window.__homeBookCarouselHarness?.setMode("empty"));
    await expect(page.getByText("还没有教材", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "上传第一本教材", exact: true })).toBeVisible();

    await page.evaluate(() => window.__homeBookCarouselHarness?.setMode("single"));
    let listbox = page.getByRole("listbox", { name: "选择教材" });
    await expect(listbox).toHaveClass(/is-single/);
    await expect(listbox.getByRole("option")).toHaveCount(1);
    await expect(listbox.getByRole("option")).toHaveAttribute("aria-selected", "true");

    await page.evaluate(() => window.__homeBookCarouselHarness?.setMode("two"));
    listbox = page.getByRole("listbox", { name: "选择教材" });
    await expect(listbox.getByRole("option")).toHaveCount(2);
    await expect(listbox.getByRole("option").nth(1)).toHaveAttribute("aria-selected", "false");

    await page.evaluate(() => window.__homeBookCarouselHarness?.setMode("many"));
    await expect(page.getByRole("listbox", { name: "选择教材" }).getByRole("option")).toHaveCount(10);
    await expect(page.getByText("1 / 10", { exact: true })).toBeVisible();
  });
});

test.describe("default homepage visual regression", () => {
  test.use({ colorScheme: "light", locale: "zh-CN", timezoneId: "Asia/Hong_Kong" });

  test("renders the real homepage without horizontal overflow", async ({ page }, testInfo) => {
    await page.goto("/?embedded=device-preview");

    const dashboard = page.locator(".home-dashboard");
    await expect(dashboard).toBeVisible();
    const listbox = page.getByRole("listbox", { name: "选择教材" });
    await expect(listbox).toBeVisible();
    const bookOptions = listbox.getByRole("option");
    await expect(bookOptions).toHaveCount(8);
    const selectedOption = listbox.getByRole("option", { selected: true });
    await expect(selectedOption).toHaveCount(1);
    await expect(selectedOption.locator(".home-book-cover")).toHaveCSS("filter", "none");
    await expect(selectedOption.locator(".home-book-cover img")).toHaveAttribute(
      "src",
      "/assets/book-covers/biology-required-2.webp"
    );
    await expect.poll(async () => listbox.evaluate((element) => {
      const selected = element.querySelector<HTMLElement>('[role="option"][aria-selected="true"]');
      if (!selected) return Number.NEGATIVE_INFINITY;
      const listboxBounds = element.getBoundingClientRect();
      const selectedBounds = selected.getBoundingClientRect();
      return (listboxBounds.left + listboxBounds.width / 2)
        - (selectedBounds.left + selectedBounds.width / 2);
    }), {
      message: "the initial smooth scroll settles with the selected cover left of center"
    }).toBeGreaterThanOrEqual(6);
    const carouselGeometry = await listbox.evaluate((element) => {
      const options = Array.from(element.querySelectorAll<HTMLElement>('[role="option"]'));
      const selectedIndex = options.findIndex((option) => option.getAttribute("aria-selected") === "true");
      const bounds = element.getBoundingClientRect();
      const selected = options[selectedIndex]?.getBoundingClientRect();
      const previous = options[selectedIndex - 1]?.getBoundingClientRect();
      const next = options[selectedIndex + 1]?.getBoundingClientRect();
      const secondNext = options[selectedIndex + 2]?.getBoundingClientRect();
      return {
        listbox: bounds.toJSON(),
        next: next?.toJSON() ?? null,
        previous: previous?.toJSON() ?? null,
        secondNext: secondNext?.toJSON() ?? null,
        selected: selected?.toJSON() ?? null,
        selectedIndex,
        visibleUnselected: options
          .filter((option) => option.getAttribute("aria-selected") !== "true")
          .map((option) => {
            const optionBounds = option.getBoundingClientRect();
            const intersection = Math.max(0, Math.min(bounds.right, optionBounds.right) - Math.max(bounds.left, optionBounds.left));
            return {
              bookId: option.dataset.bookId,
              fraction: intersection / optionBounds.width
            };
          })
          .filter((option) => option.fraction > .05)
      };
    });
    expect(carouselGeometry.selectedIndex, "the active biology course keeps a real neighbor on each side").toBe(1);
    expect(
      (carouselGeometry.listbox.x + carouselGeometry.listbox.width / 2)
        - ((carouselGeometry.selected?.x ?? 0) + (carouselGeometry.selected?.width ?? 0) / 2),
      "the selected cover sits slightly left of the carousel midpoint"
    ).toBeGreaterThanOrEqual(6);
    expect(carouselGeometry.previous?.right ?? 0, "the previous blurred cover remains visible").toBeGreaterThan(carouselGeometry.listbox.x);
    expect(carouselGeometry.next?.x ?? Number.POSITIVE_INFINITY, "the next blurred cover remains visible").toBeLessThan(carouselGeometry.listbox.right);
    if (testInfo.project.name === "iphone-17-pro") {
      expect(carouselGeometry.secondNext?.x ?? Number.POSITIVE_INFINITY, "a third distinct unselected book peeks in from the right edge")
        .toBeLessThan(carouselGeometry.listbox.right);
      expect(carouselGeometry.visibleUnselected.map((option) => option.bookId), "the visible unselected covers belong to three different books")
        .toEqual([
          "catalog_high_school_math_required_2",
          "catalog_physics_required_3",
          "catalog_chemistry_required_2"
        ]);
      expect(
        carouselGeometry.visibleUnselected.reduce((total, option) => total + option.fraction, 0),
        JSON.stringify(carouselGeometry.visibleUnselected)
      ).toBeGreaterThanOrEqual(2.35);
    }
    expect(await bookOptions.evaluateAll((options) => options
      .filter((option) => option.getAttribute("aria-selected") !== "true")
      .every((option) => {
      const cover = option.querySelector<HTMLElement>(".home-book-cover");
      return cover ? getComputedStyle(cover).filter.includes("blur(1.5px)") : false;
    }))).toBe(true);
    await expect(page.locator(".home-book-workspace")).toHaveAttribute("data-loaded", "true");

    const actionOrbGeometry = await page.evaluate(() => {
      const workspace = document.querySelector<HTMLElement>('.home-book-workspace[data-loaded="true"]');
      const actions = workspace?.querySelector<HTMLElement>(".home-workspace-actions") ?? null;
      const orb = document.querySelector<HTMLElement>(".ai-orb");
      if (!workspace || !actions || !orb) return null;
      const workspaceBounds = workspace.getBoundingClientRect();
      const actionBounds = actions.getBoundingClientRect();
      const orbBounds = orb.getBoundingClientRect();
      return {
        actionRightInset: workspaceBounds.right - actionBounds.right,
        actionBounds: actionBounds.toJSON(),
        orbBounds: orbBounds.toJSON(),
        overlaps: !(
          actionBounds.right <= orbBounds.left
          || orbBounds.right <= actionBounds.left
          || actionBounds.bottom <= orbBounds.top
          || orbBounds.bottom <= actionBounds.top
        )
      };
    });
    expect(actionOrbGeometry?.actionRightInset, "the real homepage actions align with the workspace right padding").toBeGreaterThanOrEqual(15);
    expect(actionOrbGeometry?.actionRightInset, "the real homepage actions align with the workspace right padding").toBeLessThanOrEqual(17);
    expect(actionOrbGeometry?.overlaps, JSON.stringify(actionOrbGeometry)).toBe(false);

    const layout = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      tapTargets: Array.from(document.querySelectorAll<HTMLElement>(".home-dashboard button"))
        .map((button) => ({
          label: button.getAttribute("aria-label") ?? button.textContent?.trim() ?? "",
          height: button.getBoundingClientRect().height
        }))
    }));
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
    expect(layout.tapTargets.every((target) => target.height >= 44), JSON.stringify(layout.tapTargets)).toBe(true);

    if (testInfo.project.name === "iphone-17-pro") {
      await page.screenshot({
        path: "output/playwright/phase3-home-next-step-iphone.png",
        fullPage: true
      });
      await page.locator(".primary-nav, .ai-orb").evaluateAll((elements) => {
        elements.forEach((element) => {
          (element as HTMLElement).style.visibility = "hidden";
        });
      });
      await page.locator('.home-book-workspace[data-loaded="true"]').screenshot({
        path: "output/playwright/phase3-home-next-step-workspace-iphone.png"
      });
    }
  });

  test("shows about two and a half distinct unselected books at the annotated 434px width", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "iphone-17-pro", "The browser annotation targets the 434px portrait layout.");
    await page.setViewportSize({ width: 434, height: 903 });
    await page.goto("/?embedded=device-preview");

    const listbox = page.getByRole("listbox", { name: "选择教材" });
    await expect(listbox).toBeVisible();
    await expect.poll(() => listbox.evaluate((element) => {
      const selected = element.querySelector<HTMLElement>('[role="option"][aria-selected="true"]');
      if (!selected) return Number.NEGATIVE_INFINITY;
      const transform = new DOMMatrixReadOnly(getComputedStyle(selected).transform);
      return transform.a;
    }), { message: "the selected cover finishes its 1.3x emphasis transition" }).toBeCloseTo(1.3, 2);
    expect(await listbox.evaluate((element) => Number.parseFloat(getComputedStyle(element).gap))).toBe(3);
    await expect(page.locator(".home-book-selection-summary small")).toHaveCount(0);
    await expect(page.locator(".home-book-selection-summary")).not.toContainText("可以学习");
    await expect(page.locator(".home-book-selection-summary")).not.toContainText("目录项");
    await expect.poll(async () => listbox.evaluate((element) => {
      const selected = element.querySelector<HTMLElement>('[role="option"][aria-selected="true"]');
      if (!selected) return Number.NEGATIVE_INFINITY;
      const listboxBounds = element.getBoundingClientRect();
      const selectedBounds = selected.getBoundingClientRect();
      return Math.abs(
        (listboxBounds.left + listboxBounds.width * .42)
          - (selectedBounds.left + selectedBounds.width / 2)
      );
    }), { message: "the selected cover settles on the 42% drag-selection anchor" }).toBeLessThanOrEqual(2);

    const visibleUnselected = await listbox.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return Array.from(element.querySelectorAll<HTMLElement>('[role="option"][aria-selected="false"]'))
        .map((option) => {
          const optionBounds = option.getBoundingClientRect();
          const intersection = Math.max(0, Math.min(bounds.right, optionBounds.right) - Math.max(bounds.left, optionBounds.left));
          return {
            bookId: option.dataset.bookId,
            fraction: intersection / optionBounds.width
          };
        })
        .filter((option) => option.fraction > .05);
    });

    expect(visibleUnselected.map((option) => option.bookId)).toEqual([
      "catalog_high_school_math_required_2",
      "catalog_physics_required_3",
      "catalog_chemistry_required_2"
    ]);
    expect(visibleUnselected.reduce((total, option) => total + option.fraction, 0), JSON.stringify(visibleUnselected))
      .toBeGreaterThanOrEqual(2.45);
    expect(visibleUnselected.reduce((total, option) => total + option.fraction, 0), JSON.stringify(visibleUnselected))
      .toBeLessThanOrEqual(2.7);
  });

  test("keeps the greeting bar pinned and opens import from its primary action", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "iphone-17-pro", "The browser annotation targets the iPhone portrait header.");
    await page.setViewportSize({ width: 434, height: 903 });
    await page.goto("/?embedded=device-preview");

    const header = page.locator(".home-topline");
    const screen = page.locator('.screen-content[data-screen="home"]');
    const importAction = header.getByRole("button", { name: "导入课程" });
    await expect(importAction).toBeVisible();
    await expect(importAction).toHaveCSS("background-color", "rgb(124, 58, 237)");

    const initialHeaderTop = await header.evaluate((element) => element.getBoundingClientRect().top);
    const stickyInsets = await page.evaluate(() => {
      const headerElement = document.querySelector<HTMLElement>(".home-topline");
      const screenElement = document.querySelector<HTMLElement>('.screen-content[data-screen="home"]');
      if (!headerElement || !screenElement) throw new Error("Home sticky elements are missing");
      const headerStyle = getComputedStyle(headerElement);
      const coverStyle = getComputedStyle(headerElement, "::before");
      const screenStyle = getComputedStyle(screenElement);
      return {
        coverBackground: coverStyle.backgroundColor,
        coverBottom: Number.parseFloat(coverStyle.bottom),
        coverContent: coverStyle.content,
        coverLeft: Number.parseFloat(coverStyle.left),
        coverOpacity: Number.parseFloat(coverStyle.opacity),
        coverPointerEvents: coverStyle.pointerEvents,
        coverRight: Number.parseFloat(coverStyle.right),
        coverTop: Number.parseFloat(coverStyle.top),
        headerBackground: headerStyle.backgroundColor,
        headerOpacity: Number.parseFloat(headerStyle.opacity),
        headerTop: Number.parseFloat(headerStyle.top),
        screenPaddingLeft: Number.parseFloat(screenStyle.paddingLeft),
        screenPaddingRight: Number.parseFloat(screenStyle.paddingRight),
        screenPaddingTop: Number.parseFloat(screenStyle.paddingTop)
      };
    });
    expect(stickyInsets.headerTop).toBe(0);
    expect(initialHeaderTop).toBeCloseTo(stickyInsets.screenPaddingTop, 0);
    expect(stickyInsets.headerOpacity).toBe(1);
    expect(stickyInsets.coverBackground).toBe(stickyInsets.headerBackground);
    expect(stickyInsets.coverBackground.startsWith("rgb(")).toBe(true);
    expect(stickyInsets.coverContent).not.toBe("none");
    expect(stickyInsets.coverOpacity).toBe(1);
    expect(stickyInsets.coverPointerEvents).toBe("none");
    expect(stickyInsets.coverTop).toBeLessThanOrEqual(-stickyInsets.screenPaddingTop + 1);
    expect(stickyInsets.coverLeft).toBeLessThanOrEqual(-stickyInsets.screenPaddingLeft + 1);
    expect(stickyInsets.coverRight).toBeLessThanOrEqual(-stickyInsets.screenPaddingRight + 1);
    expect(stickyInsets.coverBottom).toBe(0);
    await screen.evaluate((element) => { element.scrollTop = 720; });
    expect(await screen.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    await expect.poll(
      () => header.evaluate((element) => element.getBoundingClientRect().top),
      { message: "the greeting bar remains pinned below the iOS status bar" }
    ).toBeCloseTo(initialHeaderTop, 0);

    await importAction.click();
    await expect(page.locator('.screen-content[data-screen="upload"]')).toBeVisible();
    await expect(page.getByRole("heading", { name: "上传书籍" })).toBeVisible();
  });

  test("keeps chapter-tool shadows inside an unclipped workspace", async ({ page }, testInfo) => {
    await page.goto("/?embedded=device-preview");
    const workspace = page.locator('.home-book-workspace[data-loaded="true"]');
    const toolGrid = workspace.locator(".study-tool-grid");
    const assignment = toolGrid.locator('[data-tool="assignment"]');
    await expect(assignment).toBeVisible();

    const shadowGeometry = await workspace.evaluate((element) => {
      const grid = element.querySelector<HTMLElement>(".study-tool-grid");
      const card = grid?.querySelector<HTMLElement>('[data-tool="assignment"]');
      if (!grid || !card) return null;
      const gridStyle = getComputedStyle(grid);
      const gridBounds = grid.getBoundingClientRect();
      const cardBounds = card.getBoundingClientRect();
      return {
        cardLeftClearance: cardBounds.left - gridBounds.left,
        cardBottomClearance: gridBounds.bottom - cardBounds.bottom,
        cardShadow: getComputedStyle(card).boxShadow,
        gridOverflowX: gridStyle.overflowX,
        gridPaddingBottom: Number.parseFloat(gridStyle.paddingBottom),
        gridPaddingInline: Number.parseFloat(gridStyle.paddingInlineStart),
        workspaceOverflow: getComputedStyle(element).overflow
      };
    });

    expect(shadowGeometry?.workspaceOverflow).toBe("visible");
    expect(shadowGeometry?.gridOverflowX).toBe(
      testInfo.project.name.startsWith("ipad-pro-11") ? "visible" : "auto"
    );
    expect(shadowGeometry?.gridPaddingBottom).toBeGreaterThanOrEqual(12);
    expect(shadowGeometry?.cardBottomClearance).toBeGreaterThanOrEqual(11);
    expect(shadowGeometry?.gridPaddingInline).toBeGreaterThanOrEqual(12);
    expect(shadowGeometry?.cardLeftClearance).toBeGreaterThanOrEqual(11);
    expect(shadowGeometry?.cardShadow).not.toBe("none");
  });

  test("lays out the three global learning actions in one row", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    const actionList = page.locator(".home-global-action-list");
    const actions = actionList.locator(".home-global-action");
    await expect(actions).toHaveCount(3);

    const layout = await actionList.evaluate((element) => {
      const cards = Array.from(element.querySelectorAll<HTMLElement>(".home-global-action"));
      const bounds = cards.map((card) => card.getBoundingClientRect());
      return {
        cardHeights: bounds.map((item) => item.height),
        cardWidths: bounds.map((item) => item.width),
        columnCount: new Set(bounds.map((item) => Math.round(item.left))).size,
        documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        maxTopDelta: Math.max(...bounds.map((item) => item.top)) - Math.min(...bounds.map((item) => item.top))
      };
    });

    expect(layout.columnCount).toBe(3);
    expect(layout.maxTopDelta).toBeLessThanOrEqual(1);
    expect(Math.min(...layout.cardWidths)).toBeGreaterThanOrEqual(92);
    expect(Math.min(...layout.cardHeights)).toBeGreaterThanOrEqual(72);
    expect(layout.documentOverflow).toBeLessThanOrEqual(0);
  });

  test("keeps the bundled shelf books as cover-only previews until import", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    const listbox = page.getByRole("listbox", { name: "选择教材" });
    const previewBook = listbox.getByRole("option", { name: /高中数学 必修 第二册/ });

    await previewBook.click();
    await expect(previewBook).toHaveAttribute("aria-selected", "true");
    await expect(previewBook.locator("img")).toHaveAttribute(
      "src",
      "/assets/book-covers/high-school-math-required-2.webp"
    );

    const workspace = page.locator('.home-book-workspace[data-book-id="catalog_high_school_math_required_2"]');
    await expect(workspace).toHaveAttribute("data-loaded", "false");
    await expect(workspace.getByRole("heading", { name: "这本示范教材还未导入" })).toBeVisible();
    await expect(workspace.getByRole("button", { name: "导入这本教材" })).toBeVisible();
    await expect(workspace.locator(".study-tool-grid")).toHaveCount(0);
  });

  test("opens source and lesson as distinct destinations for the same resolved chapter", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    const workspace = page.locator('.home-book-workspace[data-loaded="true"]');
    await expect(workspace).toBeVisible();
    const bookId = await workspace.getAttribute("data-book-id");
    const chapterId = await workspace.getAttribute("data-chapter-id");
    const chapterTitle = (await workspace.locator("h2").textContent())?.trim();
    const lessonTitle = chapterTitle?.replace(/^第\s*\d+\s*节\s*/, "");
    expect(bookId).toBeTruthy();
    expect(chapterId).toBeTruthy();
    expect(chapterTitle).toBeTruthy();

    await workspace.getByRole("button", { name: "回到原书", exact: true }).click();
    await expect(page.locator(".source-reader-screen")).toBeVisible();
    await expect(page.locator(".source-reader-summary h2")).toHaveText(chapterTitle ?? "");
    await expect(page.locator(".source-reader-summary p")).toContainText("PDF 第");
    expect(await page.locator(".source-page-image").getAttribute("alt")).toContain(chapterTitle);

    await clickAfterMotionAndScrollSettle(page, page.getByRole("button", { name: "返回", exact: true }), "return from Home source");
    await expect(workspace).toBeVisible();
    await clickAfterMotionAndScrollSettle(
      page,
      workspace.getByRole("button", { name: "继续学习", exact: true }),
      "continue current Home chapter"
    );
    await expect(page.locator(".lesson-screen")).toBeVisible();
    await expect(page.locator("#lesson-article-title")).toHaveText(lessonTitle ?? "");
  });

  test("opens assignment and flashcards with the same homepage chapter context", async ({ page }) => {
    await page.goto("/?embedded=device-preview");
    const workspace = page.locator('.home-book-workspace[data-loaded="true"]');
    await expect(workspace).toBeVisible();
    const chapterId = await workspace.getAttribute("data-chapter-id");
    const chapterTitle = (await workspace.locator("h2").textContent())?.trim() ?? "";
    expect(chapterId).toBe("c2s1");
    expect(chapterTitle).toBe("第 1 节 减数分裂和受精作用");

    await clickAfterMotionAndScrollSettle(page, workspace.locator('[data-tool="assignment"]'), "open Home assignment tool");
    await expect(page.locator(".assignment-screen")).toBeVisible();
    await expect(page.locator(".assignment-progress-heading h2")).toHaveText(chapterTitle);

    await clickAfterMotionAndScrollSettle(page, page.getByRole("button", { name: "返回", exact: true }), "return from Home assignment");
    await expect(workspace).toHaveAttribute("data-chapter-id", chapterId ?? "");
    await clickAfterMotionAndScrollSettle(page, workspace.locator('[data-tool="flashcards"]'), "open Home flashcard tool");
    await expect(page.locator(".flashcard-screen")).toBeVisible();
    await expect(page.locator('.memory-card-answer-motion[data-motion-flash-card="fc_homologous"]')).toBeVisible();
    await expect(page.locator(".memory-card-answer-face-front h2")).toHaveText("什么是同源染色体？");
  });
});
