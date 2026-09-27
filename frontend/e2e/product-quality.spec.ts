import { expect, test } from "@playwright/test";

test("release baseline keeps type, focus and touch targets usable", async ({ browser, baseURL }, info) => {
  test.skip(!baseURL, "Choose the private preview explicitly.");
  const context = await browser.newContext({ viewport: { width: 900, height: 1020 } });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const frame = page.frameLocator('iframe[title="iPhone 17 App 模拟屏幕"]');
  try {
    await page.goto(`${baseURL}/?device=iphone-17&release=standards-v12-20260927`);
    await frame.getByRole("button", { name: "暂时体验，稍后注册" }).click();

    await expect(frame.locator('.bottom-nav [aria-current="page"]')).toHaveCount(1);
    const undersized = await frame.locator("button:visible").evaluateAll(buttons => buttons.flatMap(button => {
      const rect = button.getBoundingClientRect();
      return rect.width < 43.5 || rect.height < 43.5
        ? [{ label: button.getAttribute("aria-label") || button.textContent?.trim(), width: rect.width, height: rect.height }]
        : [];
    }));
    expect(undersized).toEqual([]);

    const activeNav = frame.locator('.bottom-nav [aria-current="page"]');
    await activeNav.focus();
    expect(await activeNav.evaluate(element => getComputedStyle(element).outlineWidth)).toBe("3px");
    expect(await activeNav.evaluate(element => getComputedStyle(element).outlineColor)).toBe("rgb(49, 91, 216)");

    for (const destination of ["学习", "书架", "社区", "答疑", "我的"]) {
      await frame.getByRole("button", { name: destination, exact: true }).click();
      const undersizedText = await frame.locator(".phone *").evaluateAll(elements => elements.flatMap(element => {
        const rect = element.getBoundingClientRect();
        const text = element.textContent?.trim();
        const size = Number.parseFloat(getComputedStyle(element).fontSize);
        return text && element.children.length === 0 && rect.width && rect.height && size < 11 && !element.closest("svg")
          ? [{ text: text.slice(0, 30), size }]
          : [];
      }));
      expect(undersizedText, `${destination} contains text below 11px`).toEqual([]);
    }

    const typeControl = frame.getByRole("slider", { name: "字体大小" });
    await typeControl.fill("2");
    await expect(typeControl).toHaveValue("2");
    await expect(frame.locator(".stage")).toHaveClass(/is-large-type/);
    expect(await frame.locator(".phone").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    expect(await frame.locator(".app-content").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath("type-200-percent.png") });
    expect(errors).toEqual([]);
  } finally {
    await context.close();
  }
});
