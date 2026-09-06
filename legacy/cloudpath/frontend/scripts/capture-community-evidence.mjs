import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { env } from "node:process";
import { chromium } from "playwright";

/* global document, getComputedStyle, HTMLElement */

const baseUrl = env.COMMUNITY_EVIDENCE_URL ?? "http://127.0.0.1:5176";
const outputDirectory = join("docs", "design");
const viewports = [
  { name: "iphone-portrait", width: 402, height: 874 },
  { name: "iphone-landscape", width: 756, height: 352 },
  { name: "ipad-portrait", width: 834, height: 1194 },
  { name: "ipad-landscape", width: 1194, height: 834 }
];

await mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true });

try {
  for (const viewport of viewports) {
    const page = await browser.newPage({
      viewport: { width: viewport.width, height: viewport.height },
      deviceScaleFactor: 1
    });
    const errors = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`console: ${message.text()}`);
    });
    page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));

    await page.goto(`${baseUrl}/?embedded=device-preview`, {
      waitUntil: "domcontentloaded",
      timeout: 10_000
    });
    await page.getByRole("button", { name: "社区", exact: true }).click();
    await page.getByRole("heading", { name: "热门书籍", exact: true }).waitFor();
    await page.locator(".community-cover-image").last().waitFor();

    const geometry = await page.locator(".community-grid").evaluate((grid) => {
      const cards = Array.from(grid.querySelectorAll(".community-book-card"));
      const screenContent = grid.closest(".screen-content");
      if (!(screenContent instanceof HTMLElement)) {
        throw new Error("Community grid is not inside .screen-content");
      }
      const gridRect = grid.getBoundingClientRect();
      const screenRect = screenContent.getBoundingClientRect();
      const rects = cards.map((card) => {
        const rect = card.getBoundingClientRect();
        const style = getComputedStyle(card);
        const image = card.querySelector("img");
        return {
          left: Math.round(rect.left * 10) / 10,
          top: Math.round(rect.top * 10) / 10,
          width: Math.round(rect.width * 10) / 10,
          background: style.backgroundColor,
          backgroundImage: style.backgroundImage,
          backdropFilter: style.backdropFilter,
          imageSrc: image?.getAttribute("src") ?? null,
          imageLoaded: Boolean(image && image.complete && image.naturalWidth > 0)
        };
      });
      return {
        columns: getComputedStyle(grid).gridTemplateColumns,
        rowCount: new Set(rects.map((rect) => rect.top)).size,
        columnCount: new Set(rects.map((rect) => rect.left)).size,
        widthDelta: Math.max(...rects.map((rect) => rect.width)) - Math.min(...rects.map((rect) => rect.width)),
        documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        screenOverflow: screenContent.scrollWidth > screenContent.clientWidth,
        gridInsideScreen: gridRect.left >= screenRect.left - 1 && gridRect.right <= screenRect.right + 1,
        cardsInsideGrid: rects.every((rect) => rect.left >= gridRect.left - 1 && rect.left + rect.width <= gridRect.right + 1),
        screenWidth: {
          client: screenContent.clientWidth,
          scroll: screenContent.scrollWidth
        },
        boundaries: {
          screen: { left: screenRect.left, right: screenRect.right },
          grid: { left: gridRect.left, right: gridRect.right }
        },
        rects
      };
    });
    const selectedCategory = await page
      .locator('.community-category-button[aria-pressed="true"]')
      .evaluate((button) => {
        const style = getComputedStyle(button);
        return {
          background: style.backgroundColor,
          backgroundImage: style.backgroundImage,
          backdropFilter: style.backdropFilter,
          height: button.getBoundingClientRect().height
        };
      });

    await page.screenshot({
      path: join(outputDirectory, `community-after-refactor-${viewport.name}.png`),
      fullPage: true
    });
    console.log(JSON.stringify({
      viewport: `${viewport.width}x${viewport.height}`,
      ...geometry,
      selectedCategory,
      errors
    }));
    await page.close();
  }
} finally {
  await browser.close();
}
