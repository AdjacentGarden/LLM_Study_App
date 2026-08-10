import { expect, test } from "playwright/test";

test.describe("device preview studio", () => {
  test("uses the workbench as the only public entry and reserves the embedded entry for the inner app", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".device-preview-studio")).toBeVisible();
    await expect(page.locator("iframe.device-preview-iframe")).toHaveCount(1);
    await expect(page.locator(".app-shell")).toHaveCount(0);
    await expect.poll(() => {
      const url = new URL(page.url());
      return {
        chrome: url.searchParams.get("chrome"),
        device: url.searchParams.get("device"),
        hasLegacyPreview: url.searchParams.has("preview"),
        orientation: url.searchParams.get("orientation"),
        quality: url.searchParams.get("quality")
      };
    }).toEqual({
      chrome: "1",
      device: "iphone-17-pro",
      hasLegacyPreview: false,
      orientation: "portrait",
      quality: "fit"
    });

    await page.goto("/?embedded=device-preview");
    await expect(page.locator(".device-preview-studio")).toHaveCount(0);
    await expect(page.locator("iframe")).toHaveCount(0);
    await expect(page.locator(".app-shell")).toBeVisible();
  });

  test("uses one iframe and preserves inner page, dialog, input, and main identity", async ({ page }) => {
    await page.goto("/?device=iphone-17-pro&orientation=portrait&quality=fit");
    const iframe = page.locator("iframe.device-preview-iframe");
    await expect(iframe).toHaveAttribute("src", "/?embedded=device-preview");
    await expect(iframe).toHaveAttribute("title", "BookCourse AI 设备预览内层应用");
    const iframeHandle = await iframe.elementHandle();
    if (!iframeHandle) throw new Error("The device preview iframe did not mount");
    const embeddedFrame = page.frameLocator("iframe.device-preview-iframe");

    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 874, width: 402 });

    await iframeHandle.evaluate((element) => {
      const iframeElement = element as HTMLIFrameElement;
      iframeElement.dataset.testLoadCount = "0";
      iframeElement.addEventListener("load", () => {
        iframeElement.dataset.testLoadCount = String(Number(iframeElement.dataset.testLoadCount ?? "0") + 1);
      });
    });
    await expect(iframe).toHaveAttribute("data-test-load-count", "0");
    await expect.poll(() => iframeHandle.evaluate((element) => (
      element === element.ownerDocument.querySelector("iframe.device-preview-iframe")
    ))).toBe(true);
    await expect(embeddedFrame.locator("[data-testid='ios-status-bar']")).toBeVisible();
    await expect(embeddedFrame.locator(".ios-status-bar__time")).toHaveText("9:41");
    await expect(embeddedFrame.locator(".ios-status-bar__cellular")).toBeVisible();
    await expect(embeddedFrame.locator(".ios-status-bar__wifi")).toBeVisible();
    await expect(embeddedFrame.locator(".ios-status-bar__battery")).toBeVisible();
    await expect(embeddedFrame.locator(".home-indicator")).toBeVisible();

    const statusBarGeometry = await embeddedFrame.locator("[data-testid='ios-status-bar']").evaluate((statusBar) => {
      const statusRect = statusBar.getBoundingClientRect();
      const geometry = (selector: string) => {
        const element = statusBar.querySelector<HTMLElement>(selector);
        if (!element) return null;
        const rect = element.getBoundingClientRect();
        return {
          height: rect.height,
          width: rect.width,
          x: rect.x - statusRect.x,
          y: rect.y - statusRect.y
        };
      };
      const time = statusBar.querySelector<HTMLElement>(".ios-status-bar__time");
      if (!time) return null;
      const timeStyles = getComputedStyle(time);
      return {
        height: statusRect.height,
        width: statusRect.width,
        timeGroup: geometry(".ios-status-bar__time-group"),
        time: geometry(".ios-status-bar__time"),
        indicators: geometry(".ios-status-bar__indicators"),
        cellular: geometry(".ios-status-bar__cellular"),
        wifi: geometry(".ios-status-bar__wifi"),
        battery: geometry(".ios-status-bar__battery"),
        batteryBorder: geometry(".ios-status-bar__battery-border"),
        batteryCap: geometry(".ios-status-bar__battery-cap"),
        batteryLevel: geometry(".ios-status-bar__battery-level"),
        typography: {
          fontFamily: timeStyles.fontFamily,
          fontSize: timeStyles.fontSize,
          fontWeight: timeStyles.fontWeight,
          lineHeight: timeStyles.lineHeight
        }
      };
    });
    expect(statusBarGeometry).not.toBeNull();
    if (!statusBarGeometry) throw new Error("The iPhone status bar geometry did not mount");
    expect(statusBarGeometry.width).toBeCloseTo(402, 1);
    expect(statusBarGeometry.height).toBeCloseTo(62, 1);
    expect(statusBarGeometry.timeGroup).toEqual({ x: 24, y: 21, width: 100, height: 22 });
    expect(statusBarGeometry.time).toEqual({ x: 55.5, y: 21.75, width: 37, height: 22 });
    expect(statusBarGeometry.indicators).toEqual({ x: 278, y: 21, width: 100, height: 22 });
    expect(statusBarGeometry.cellular?.x).toBeCloseTo(288.665, 1);
    expect(statusBarGeometry.cellular?.y).toBeCloseTo(26.387, 1);
    expect(statusBarGeometry.cellular?.width).toBeCloseTo(19.2, 1);
    expect(statusBarGeometry.cellular?.height).toBeCloseTo(12.226, 1);
    expect(statusBarGeometry.wifi?.x).toBeCloseTo(314.865, 1);
    expect(statusBarGeometry.wifi?.y).toBeCloseTo(26.336, 1);
    expect(statusBarGeometry.wifi?.width).toBeCloseTo(17.142, 1);
    expect(statusBarGeometry.wifi?.height).toBeCloseTo(12.328, 1);
    expect(statusBarGeometry.battery?.x).toBeCloseTo(339.007, 1);
    expect(statusBarGeometry.battery).toMatchObject({ y: 26, height: 13 });
    expect(statusBarGeometry.battery?.width).toBeCloseTo(27.328, 1);
    expect(statusBarGeometry.batteryBorder).toMatchObject({ y: 26, width: 25, height: 13 });
    expect(statusBarGeometry.batteryCap?.x).toBeCloseTo(365.007, 1);
    expect(statusBarGeometry.batteryCap?.y).toBeCloseTo(30.5, 1);
    expect(statusBarGeometry.batteryCap?.width).toBeCloseTo(1.328, 1);
    expect(statusBarGeometry.batteryCap?.height).toBeCloseTo(4.075, 1);
    expect(statusBarGeometry.batteryLevel).toMatchObject({ y: 28, width: 21, height: 9 });
    expect(statusBarGeometry.typography.fontFamily).toContain("SF Pro");
    expect(statusBarGeometry.typography).toMatchObject({
      fontSize: "17px",
      fontWeight: "590",
      lineHeight: "22px"
    });

    await page.getByTestId("device-preview-orientation-landscape").click();
    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 402, width: 874 });
    await page.getByTestId("device-preview-device-ipad-pro-11").click();
    await page.getByTestId("device-preview-orientation-portrait").click();
    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 1194, width: 834 });
    await page.getByTestId("device-preview-device-iphone-17-pro").click();
    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 874, width: 402 });

    await embeddedFrame.getByRole("button", { name: "继续学习", exact: true }).click();
    await expect(embeddedFrame.locator(".lesson-screen")).toBeVisible();
    await embeddedFrame.getByRole("button", { name: "返回", exact: true }).click();
    await expect(embeddedFrame.locator(".home-dashboard")).toBeVisible();
    await embeddedFrame.locator(".primary-nav .nav-item").last().click();
    await expect(embeddedFrame.locator(".profile-screen")).toBeVisible();
    const innerMain = await embeddedFrame.locator("main").elementHandle();
    if (!innerMain) throw new Error("The embedded app main landmark did not mount");

    await embeddedFrame.getByRole("button", { name: "打开 AI 助手" }).click();
    const assistant = embeddedFrame.getByRole("dialog", { name: "AI 导学助手" });
    await expect(assistant).toBeVisible();
    await assistant.getByRole("textbox", { name: "向 AI 助手提问" }).fill("唯一设备状态文本");
    const innerNavigation = await embeddedFrame.locator(".primary-nav").elementHandle();
    if (!innerNavigation) throw new Error("The embedded app primary navigation did not mount");

    await page.getByTestId("device-preview-device-ipad-pro-11").click();
    await page.getByTestId("device-preview-orientation-landscape").click();
    await page.getByTestId("device-preview-quality-retina-3x").click();

    await expect.poll(() => iframeHandle.evaluate((element) => element.isConnected)).toBe(true);
    await expect.poll(() => iframeHandle.evaluate((element) => (
      element === element.ownerDocument.querySelector("iframe.device-preview-iframe")
    ))).toBe(true);
    await expect(iframe).toHaveAttribute("data-test-load-count", "0");
    await expect(iframe).toHaveAttribute("src", "/?embedded=device-preview");
    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 834, width: 1194 });
    await expect(embeddedFrame.locator(".profile-screen")).toBeVisible();
    await expect(assistant).toBeVisible();
    await expect(assistant.getByRole("textbox", { name: "向 AI 助手提问" })).toHaveValue("唯一设备状态文本");
    await expect.poll(() => innerMain.evaluate((element) => element.isConnected)).toBe(true);
    await expect(embeddedFrame.locator(".app-shell")).toHaveAttribute("data-device-layout", "pad");
    await expect(embeddedFrame.locator("[data-testid='ios-status-bar']")).toBeHidden();
    await expect(embeddedFrame.locator(".home-indicator")).toBeHidden();
    await expect(embeddedFrame.locator(".primary-nav")).toBeVisible();
    await expect(embeddedFrame.locator(".primary-nav .nav-item")).toHaveCount(4);
    await expect.poll(() => innerNavigation.evaluate((element) => element.isConnected)).toBe(true);
    await expect.poll(() => innerNavigation.evaluate((element) => element === element.ownerDocument.querySelector(".primary-nav"))).toBe(true);
  });

  test("uses the Phone chrome for a short landscape viewport and keeps the app inside its width", async ({ page }) => {
    await page.goto("/?device=iphone-17-pro&orientation=landscape");
    const embeddedFrame = page.frameLocator("iframe.device-preview-iframe");

    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 402, width: 874 });
    await expect(embeddedFrame.locator(".app-shell")).toHaveAttribute("data-device-layout", "phone");
    await expect(embeddedFrame.locator("[data-testid='ios-status-bar']")).toBeHidden();
    await expect(embeddedFrame.locator(".home-indicator")).toBeHidden();
    await expect(embeddedFrame.locator(".primary-nav")).toBeVisible();
    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth) : false;
    }).toBe(true);
  });

  test("renders the iPhone 17 Pro bezel and Dynamic Island only in portrait", async ({ page }) => {
    await page.goto("/?device=iphone-17-pro&orientation=portrait&quality=fit");

    const frame = page.getByTestId("device-preview-frame");
    const bezel = page.getByTestId("device-preview-bezel");
    const controls = page.getByTestId("device-preview-hardware-controls");
    const island = page.getByTestId("device-preview-dynamic-island");
    await expect(frame).toHaveAttribute("data-device", "iphone-17-pro");
    await expect(frame).toHaveAttribute("data-orientation", "portrait");
    await expect(bezel).toBeVisible();
    await expect(controls).toBeVisible();
    await expect(island).toBeVisible();
    await expect(bezel).toHaveAttribute("aria-hidden", "true");
    await expect(controls).toHaveAttribute("aria-hidden", "true");
    await expect(island).toHaveAttribute("aria-hidden", "true");
    await expect(controls.locator("[data-hardware-control]")).toHaveCount(4);

    const portraitGeometry = await page.evaluate(() => {
      const frame = document.querySelector<HTMLElement>("[data-testid='device-preview-frame']");
      const bezel = document.querySelector<HTMLElement>("[data-testid='device-preview-bezel']");
      const controls = document.querySelector<HTMLElement>("[data-testid='device-preview-hardware-controls']");
      const island = document.querySelector<HTMLElement>("[data-testid='device-preview-dynamic-island']");
      if (!frame || !bezel || !controls || !island) return null;
      const frameRect = frame.getBoundingClientRect();
      const bezelRect = bezel.getBoundingClientRect();
      const islandRect = island.getBoundingClientRect();
      const iframe = frame.querySelector("iframe");
      if (!iframe) return null;
      const frameStyles = getComputedStyle(frame);
      const iframeStyles = getComputedStyle(iframe);
      const bezelStyles = getComputedStyle(bezel);
      const bezelBeforeStyles = getComputedStyle(bezel, "::before");
      const controlStyles = getComputedStyle(controls);
      const islandStyles = getComputedStyle(island);
      const islandSensorStyles = getComputedStyle(island, "::before");
      const islandCameraStyles = getComputedStyle(island, "::after");
      const hardwareControls = Object.fromEntries(Array.from(
        controls.querySelectorAll<HTMLElement>("[data-hardware-control]")
      ).map((control) => {
        const rect = control.getBoundingClientRect();
        const name = control.dataset.hardwareControl ?? "unknown";
        return [name, {
          height: rect.height,
          left: rect.left - frameRect.left,
          pointerEvents: getComputedStyle(control).pointerEvents,
          right: rect.right - frameRect.right,
          top: rect.top - frameRect.top,
          width: rect.width
        }];
      }));
      return {
        bezelHeight: bezelRect.height,
        bezelWidth: bezelRect.width,
        bezelBeforeInset: bezelBeforeStyles.top,
        bezelBeforeRadius: bezelBeforeStyles.borderRadius,
        bezelInsetLeft: bezelStyles.left,
        bezelInsetTop: bezelStyles.top,
        bezelRadius: bezelStyles.borderRadius,
        frameHeight: frameRect.height,
        frameWidth: frameRect.width,
        frameRadius: frameStyles.borderRadius,
        islandHeight: islandRect.height,
        islandCamera: {
          backgroundImage: islandCameraStyles.backgroundImage,
          boxShadow: islandCameraStyles.boxShadow,
          content: islandCameraStyles.content,
          height: islandCameraStyles.height,
          width: islandCameraStyles.width
        },
        islandDecoration: {
          borderWidth: islandStyles.borderWidth,
          boxShadow: islandStyles.boxShadow
        },
        islandRadius: islandStyles.borderRadius,
        islandSensor: {
          backgroundImage: islandSensorStyles.backgroundImage,
          boxShadow: islandSensorStyles.boxShadow,
          content: islandSensorStyles.content,
          height: islandSensorStyles.height,
          width: islandSensorStyles.width
        },
        islandTop: islandRect.top - frameRect.top,
        islandTopCss: islandStyles.top,
        islandWidth: islandRect.width,
        hardwareControls,
        pointerEvents: [bezelStyles.pointerEvents, controlStyles.pointerEvents, islandStyles.pointerEvents],
        screenRadius: iframeStyles.borderRadius,
        variables: {
          blackBezel: frameStyles.getPropertyValue("--iphone-black-bezel").trim(),
          islandHeight: frameStyles.getPropertyValue("--iphone-island-height").trim(),
          islandRadius: frameStyles.getPropertyValue("--iphone-island-radius").trim(),
          islandTop: frameStyles.getPropertyValue("--iphone-island-top").trim(),
          islandWidth: frameStyles.getPropertyValue("--iphone-island-width").trim(),
          metalRing: frameStyles.getPropertyValue("--iphone-metal-ring").trim(),
          screenRadius: frameStyles.getPropertyValue("--iphone-screen-radius").trim(),
          shellInset: frameStyles.getPropertyValue("--iphone-shell-visible-inset").trim(),
          shellRadius: frameStyles.getPropertyValue("--iphone-shell-radius").trim()
        }
      };
    });
    expect(portraitGeometry).not.toBeNull();
    if (!portraitGeometry) throw new Error("The iPhone 17 Pro portrait chrome did not mount");
    const scale = portraitGeometry.frameWidth / 402;
    expect(portraitGeometry.frameHeight).toBeCloseTo(874 * scale, 1);
    expect(portraitGeometry.bezelWidth).toBeCloseTo(portraitGeometry.frameWidth + 32 * scale, 1);
    expect(portraitGeometry.bezelHeight).toBeCloseTo(portraitGeometry.frameHeight + 32 * scale, 1);
    expect(portraitGeometry.islandWidth).toBeCloseTo(126 * scale, 1);
    expect(portraitGeometry.islandHeight).toBeCloseTo(37 * scale, 1);
    expect(portraitGeometry.islandTop).toBeCloseTo(14 * scale, 1);
    expect(portraitGeometry.frameRadius).toBe("61px");
    expect(portraitGeometry.screenRadius).toBe("61px");
    expect(portraitGeometry.bezelRadius).toBe("77px");
    expect(portraitGeometry.bezelInsetLeft).toBe("-16px");
    expect(portraitGeometry.bezelInsetTop).toBe("-16px");
    expect(portraitGeometry.bezelBeforeInset).toBe("6px");
    expect(portraitGeometry.bezelBeforeRadius).toBe("71px");
    expect(portraitGeometry.islandRadius).toBe("18.5px");
    expect(portraitGeometry.islandTopCss).toBe("14px");
    expect(portraitGeometry.variables).toEqual({
      blackBezel: "10px",
      islandHeight: "37px",
      islandRadius: "18.5px",
      islandTop: "14px",
      islandWidth: "126px",
      metalRing: "6px",
      screenRadius: "61px",
      shellInset: "16px",
      shellRadius: "77px"
    });
    expect(portraitGeometry.islandDecoration).toEqual({ borderWidth: "0px", boxShadow: "none" });
    expect(portraitGeometry.islandSensor).toEqual({
      backgroundImage: "none",
      boxShadow: "none",
      content: '""',
      height: "23px",
      width: "42px"
    });
    expect(portraitGeometry.islandCamera).toMatchObject({
      boxShadow: "none",
      content: '""',
      height: "17px",
      width: "17px"
    });
    expect(portraitGeometry.islandCamera.backgroundImage).toContain("radial-gradient");
    expect(portraitGeometry.pointerEvents).toEqual(["none", "none", "none"]);
    const expectedControls = {
      action: { side: "left", top: 170, height: 42 },
      "volume-up": { side: "left", top: 243, height: 68 },
      "volume-down": { side: "left", top: 329, height: 68 },
      side: { side: "right", top: 266, height: 107 }
    } as const;
    for (const [name, expected] of Object.entries(expectedControls)) {
      const control = portraitGeometry.hardwareControls[name];
      expect(control).toBeDefined();
      expect(control.width).toBeCloseTo(4 * scale, 1);
      expect(control.height).toBeCloseTo(expected.height * scale, 1);
      expect(control.top).toBeCloseTo(expected.top * scale, 1);
      expect(control.pointerEvents).toBe("none");
      if (expected.side === "left") {
        expect(control.left).toBeCloseTo(-19 * scale, 1);
      } else {
        expect(control.right).toBeCloseTo(19 * scale, 1);
      }
    }

    await page.getByTestId("device-preview-orientation-landscape").click();
    await expect(page.getByTestId("device-preview-bezel")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-hardware-controls")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-dynamic-island")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-frame")).toHaveAttribute("data-orientation", "landscape");

    await page.getByTestId("device-preview-device-ipad-pro-11").click();
    await page.getByTestId("device-preview-orientation-portrait").click();
    await expect(page.getByTestId("device-preview-bezel")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-hardware-controls")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-dynamic-island")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-frame")).toHaveAttribute("data-device", "ipad-pro-11");
  });

  test("keeps Fit inside the dynamic viewport and announces the final resized geometry", async ({ page }) => {
    await page.setViewportSize({ width: 420, height: 903 });
    await page.goto("/?quality=fit");

    await expect.poll(async () => page.evaluate(() => {
      const studio = document.querySelector<HTMLElement>(".device-preview-studio");
      const canvasArea = document.querySelector<HTMLElement>("[data-testid='device-preview-canvas-area']");
      const canvas = document.querySelector<HTMLElement>("[data-testid='device-preview-canvas']");
      if (!studio || !canvasArea || !canvas) return false;
      const studioRect = studio.getBoundingClientRect();
      const canvasAreaRect = canvasArea.getBoundingClientRect();
      const canvasRect = canvas.getBoundingClientRect();
      const scrollingElement = document.scrollingElement;
      return studioRect.height <= window.innerHeight + 1
        && (scrollingElement?.scrollHeight ?? 0) <= (scrollingElement?.clientHeight ?? window.innerHeight) + 1
        && canvasRect.top >= canvasAreaRect.top - 1
        && canvasRect.left >= canvasAreaRect.left - 1
        && canvasRect.bottom <= canvasAreaRect.bottom + 1
        && canvasRect.right <= canvasAreaRect.right + 1;
    })).toBe(true);

    await expect.poll(async () => page.evaluate(() => {
      const canvas = document.querySelector<HTMLElement>("[data-testid='device-preview-canvas']");
      const announcer = document.querySelector<HTMLElement>("[data-testid='device-preview-status-announcer']");
      if (!canvas || !announcer) return false;
      const width = canvas.dataset.canvasWidth;
      const height = canvas.dataset.canvasHeight;
      return Boolean(width && height && announcer.textContent?.includes(`输出 ${width} × ${height}`));
    })).toBe(true);

    await page.evaluate(() => {
      const announcer = document.querySelector<HTMLElement>("[data-testid='device-preview-status-announcer']");
      if (!announcer) throw new Error("The device preview live region did not mount");
      announcer.dataset.testMutationCount = "0";
      const observer = new MutationObserver(() => {
        announcer.dataset.testMutationCount = String(Number(announcer.dataset.testMutationCount ?? "0") + 1);
      });
      observer.observe(announcer, { characterData: true, childList: true, subtree: true });
    });

    for (const viewport of [
      { width: 430, height: 880 },
      { width: 440, height: 870 },
      { width: 410, height: 850 },
      { width: 420, height: 860 }
    ]) {
      await page.setViewportSize(viewport);
    }

    await expect.poll(async () => page.evaluate(() => {
      const canvas = document.querySelector<HTMLElement>("[data-testid='device-preview-canvas']");
      const announcer = document.querySelector<HTMLElement>("[data-testid='device-preview-status-announcer']");
      if (!canvas || !announcer) return null;
      const width = canvas.dataset.canvasWidth;
      const height = canvas.dataset.canvasHeight;
      return {
        matchesFinalGeometry: Boolean(width && height && announcer.textContent?.includes(`输出 ${width} × ${height}`)),
        mutationCount: Number(announcer.dataset.testMutationCount ?? "0")
      };
    }), { timeout: 2_000 }).toEqual({ matchesFinalGeometry: true, mutationCount: 1 });
  });

  test("uses a side control rail on wide workspaces and stacks controls on narrow screens", async ({ page }) => {
    await page.setViewportSize({ width: 603, height: 903 });
    await page.goto("/?quality=fit");

    await expect.poll(() => page.evaluate(() => {
      const toolbar = document.querySelector<HTMLElement>(".device-preview-toolbar");
      const summary = document.querySelector<HTMLElement>(".device-preview-output-summary");
      const canvasArea = document.querySelector<HTMLElement>(".device-preview-canvas-area");
      const studio = document.querySelector<HTMLElement>(".device-preview-studio");
      if (!toolbar || !summary || !canvasArea || !studio) return false;
      const toolbarRect = toolbar.getBoundingClientRect();
      const summaryRect = summary.getBoundingClientRect();
      const canvasRect = canvasArea.getBoundingClientRect();
      return toolbarRect.x === 0
        && toolbarRect.y === 0
        && toolbarRect.height === studio.getBoundingClientRect().height
        && summaryRect.x >= toolbarRect.right - 1
        && canvasRect.x >= toolbarRect.right - 1
        && canvasRect.y >= summaryRect.bottom - 1;
    })).toBe(true);

    await page.setViewportSize({ width: 420, height: 903 });
    await expect.poll(() => page.evaluate(() => {
      const toolbar = document.querySelector<HTMLElement>(".device-preview-toolbar");
      const summary = document.querySelector<HTMLElement>(".device-preview-output-summary");
      const canvasArea = document.querySelector<HTMLElement>(".device-preview-canvas-area");
      if (!toolbar || !summary || !canvasArea) return false;
      const toolbarRect = toolbar.getBoundingClientRect();
      const summaryRect = summary.getBoundingClientRect();
      const canvasRect = canvasArea.getBoundingClientRect();
      return toolbarRect.x === 0
        && toolbarRect.width === window.innerWidth
        && summaryRect.y >= toolbarRect.bottom - 1
        && canvasRect.y >= summaryRect.bottom - 1;
    })).toBe(true);
  });

  test("makes chrome-free HD output an exact viewport canvas and restores chrome with Escape", async ({ page }) => {
    const output = { width: 804, height: 1748 };
    await page.setViewportSize(output);
    await page.goto("/?device=iphone-17-pro&orientation=portrait&quality=hd-2x&chrome=0");
    const iframe = page.locator("iframe.device-preview-iframe");
    const iframeHandle = await iframe.elementHandle();
    if (!iframeHandle) throw new Error("The chrome-free device preview iframe did not mount");
    const embeddedFrame = page.frameLocator("iframe.device-preview-iframe");

    await expect(page.locator(".device-preview-toolbar")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-summary")).toHaveCount(0);
    await expect.poll(async () => page.evaluate(() => {
      const canvas = document.querySelector<HTMLElement>("[data-testid='device-preview-canvas']");
      const scrollingElement = document.scrollingElement;
      if (!canvas || !scrollingElement) return null;
      const rect = canvas.getBoundingClientRect();
      return {
        documentScrollHeight: scrollingElement.scrollHeight,
        documentScrollWidth: scrollingElement.scrollWidth,
        documentClientHeight: scrollingElement.clientHeight,
        documentClientWidth: scrollingElement.clientWidth,
        height: rect.height,
        width: rect.width,
        x: rect.x,
        y: rect.y
      };
    })).toEqual({
      documentScrollHeight: output.height,
      documentScrollWidth: output.width,
      documentClientHeight: output.height,
      documentClientWidth: output.width,
      height: output.height,
      width: output.width,
      x: 0,
      y: 0
    });

    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 874, width: 402 });

    await embeddedFrame.getByRole("button", { name: "继续学习", exact: true }).click();
    await expect(embeddedFrame.locator(".lesson-screen")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".device-preview-toolbar")).toBeVisible();
    await expect(page.getByTestId("device-preview-summary")).toBeVisible();
    await expect(page).toHaveURL(/chrome=1/);
    await expect(embeddedFrame.locator(".lesson-screen")).toBeVisible();
    await expect.poll(() => iframeHandle.evaluate((element) => (
      element === element.ownerDocument.querySelector("iframe.device-preview-iframe")
    ))).toBe(true);
  });

  test("updates output geometry and recording URL without changing the logical viewport", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-width", /\d+/);

    await page.getByTestId("device-preview-quality-hd-2x").click();
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-width", "804");
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-height", "1748");
    await expect(page).toHaveURL(/quality=hd-2x/);

    await page.getByTestId("device-preview-orientation-landscape").click();
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-width", "1748");
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-height", "804");
    await expect(page).toHaveURL(/orientation=landscape/);

    await page.getByTestId("device-preview-device-ipad-pro-11").click();
    await page.getByTestId("device-preview-orientation-portrait").click();
    await page.getByTestId("device-preview-quality-retina-3x").click();
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-width", "2502");
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-height", "3582");
    await expect.poll(() => page.evaluate(() => {
      const canvas = document.querySelector<HTMLElement>("[data-testid='device-preview-canvas']");
      const frame = document.querySelector<HTMLElement>("[data-testid='device-preview-frame']");
      if (!canvas || !frame) return null;
      const canvasRect = canvas.getBoundingClientRect();
      const frameRect = frame.getBoundingClientRect();
      return {
        canvasHeight: Math.round(canvasRect.height),
        canvasWidth: Math.round(canvasRect.width),
        frameHeight: Math.round(frameRect.height),
        frameWidth: Math.round(frameRect.width),
        offsetX: Math.round(frameRect.left - canvasRect.left),
        offsetY: Math.round(frameRect.top - canvasRect.top)
      };
    })).toEqual({
      canvasHeight: 3582,
      canvasWidth: 2502,
      frameHeight: 3582,
      frameWidth: 2502,
      offsetX: 0,
      offsetY: 0
    });
    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 1194, width: 834 });

    await page.getByTestId("device-preview-quality-4k").click();
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-width", "2160");
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-height", "3840");
    await expect.poll(() => page.evaluate(() => {
      const canvas = document.querySelector<HTMLElement>("[data-testid='device-preview-canvas']");
      const frame = document.querySelector<HTMLElement>("[data-testid='device-preview-frame']");
      if (!canvas || !frame) return null;
      const canvasRect = canvas.getBoundingClientRect();
      const frameRect = frame.getBoundingClientRect();
      return {
        bottomGap: Math.round(canvasRect.bottom - frameRect.bottom),
        canvasHeight: Math.round(canvasRect.height),
        canvasWidth: Math.round(canvasRect.width),
        frameHeight: Math.round(frameRect.height),
        frameWidth: Math.round(frameRect.width),
        leftGap: Math.round(frameRect.left - canvasRect.left),
        topGap: Math.round(frameRect.top - canvasRect.top)
      };
    })).toEqual({
      bottomGap: 374,
      canvasHeight: 3840,
      canvasWidth: 2160,
      frameHeight: 3092,
      frameWidth: 2160,
      leftGap: 0,
      topGap: 374
    });
    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 1194, width: 834 });

    await page.getByTestId("device-preview-orientation-landscape").click();
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-width", "3840");
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-height", "2160");
    await expect.poll(() => page.evaluate(() => {
      const canvas = document.querySelector<HTMLElement>("[data-testid='device-preview-canvas']");
      const frame = document.querySelector<HTMLElement>("[data-testid='device-preview-frame']");
      if (!canvas || !frame) return null;
      const canvasRect = canvas.getBoundingClientRect();
      const frameRect = frame.getBoundingClientRect();
      return {
        canvasHeight: Math.round(canvasRect.height),
        canvasWidth: Math.round(canvasRect.width),
        frameHeight: Math.round(frameRect.height),
        frameWidth: Math.round(frameRect.width),
        leftGap: Math.round(frameRect.left - canvasRect.left),
        rightGap: Math.round(canvasRect.right - frameRect.right),
        topGap: Math.round(frameRect.top - canvasRect.top)
      };
    })).toEqual({
      canvasHeight: 2160,
      canvasWidth: 3840,
      frameHeight: 2160,
      frameWidth: 3092,
      leftGap: 374,
      rightGap: 374,
      topGap: 0
    });
    await expect.poll(async () => {
      const frame = page.frames().find((candidate) => candidate.url().includes("embedded=device-preview"));
      return frame ? await frame.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })) : null;
    }).toEqual({ height: 834, width: 1194 });
  });

  test("supports parameterized chrome hiding and Escape recovery", async ({ page }) => {
    await page.goto("/?device=ipad-pro-11&orientation=landscape&quality=retina-3x&chrome=0");
    await expect(page.locator(".device-preview-toolbar")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-summary")).toHaveCount(0);
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-width", "3582");
    await expect(page.getByTestId("device-preview-canvas")).toHaveAttribute("data-canvas-height", "2502");

    await page.keyboard.press("Escape");
    await expect(page.locator(".device-preview-toolbar")).toBeVisible();
    await expect(page).toHaveURL(/chrome=1/);
    await expect(page.getByTestId("device-preview-device-ipad-pro-11")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("device-preview-orientation-landscape")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("device-preview-quality-retina-3x")).toHaveAttribute("aria-pressed", "true");
  });

  test("exposes labeled groups, pressed state, focus rings, and minimum hit targets", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("group", { name: "设备" })).toBeVisible();
    await expect(page.getByRole("group", { name: "方向" })).toBeVisible();
    await expect(page.getByRole("group", { name: "输出清晰度" })).toBeVisible();
    await expect(page.getByTestId("device-preview-device-iphone-17-pro")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("iframe.device-preview-iframe")).toHaveAttribute("title", /设备预览/);

    const iphoneToggle = page.getByTestId("device-preview-device-iphone-17-pro");
    const ipadToggle = page.getByTestId("device-preview-device-ipad-pro-11");
    await page.keyboard.press("Tab");
    await expect(iphoneToggle).toBeFocused();
    const focusOutline = await iphoneToggle.evaluate((element) => {
      const styles = getComputedStyle(element);
      return { style: styles.outlineStyle, width: Number.parseFloat(styles.outlineWidth) };
    });
    expect(focusOutline.style).not.toBe("none");
    expect(focusOutline.width).toBeGreaterThan(0);

    await page.keyboard.press("Tab");
    await expect(ipadToggle).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(ipadToggle).toHaveAttribute("aria-pressed", "true");

    const undersizedControls = await page.locator(".device-preview-toggle").evaluateAll((elements) => elements.filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width < 44 || rect.height < 44;
    }).length);
    expect(undersizedControls).toBe(0);
  });
});
