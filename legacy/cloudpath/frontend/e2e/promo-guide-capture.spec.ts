import fs from "node:fs/promises";
import path from "node:path";
import { expect, test, type Browser, type Locator, type Page } from "playwright/test";

const captureRoot = path.resolve("output/promo-guide-video/frames");
const baseURL = `http://127.0.0.1:${process.env.E2E_PORT ?? "4186"}`;
const question = "为什么减数分裂后，染色体数目会减半？";
const shortAnswer = "减数第一次分裂时，同源染色体分离，因此形成的子细胞中染色体数目减半。";

type ClickAudit = {
  clearance: number;
  height: number;
  label: string;
  target: string;
  shown: boolean;
  width: number;
  x: number;
  y: number;
};

async function waitForSettledScreen(page: Page, selector: string) {
  await expect(page.locator(selector)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".motion-screen-transition")).toHaveAttribute("data-motion-state", "idle", { timeout: 20_000 });
}

async function openFreshPage(browser: Browser) {
  const context = await browser.newContext({
    baseURL,
    colorScheme: "light",
    deviceScaleFactor: 3,
    locale: "zh-CN",
    reducedMotion: "no-preference",
    timezoneId: "Asia/Shanghai",
    viewport: { width: 402, height: 874 }
  });
  const page = await context.newPage();
  await page.goto("/?embedded=device-preview");
  await waitForSettledScreen(page, ".home-dashboard");
  await page.addStyleTag({ content: "html, body, #root { cursor: none !important; }" });
  return { context, page };
}

async function setupClick(locator: Locator) {
  await locator.waitFor({ state: "visible", timeout: 15_000 });
  await locator.click();
}

async function setupUploadSelection(page: Page) {
  await setupClick(page.locator('[data-home-global-action="upload"]'));
  await waitForSettledScreen(page, ".upload-sheet-screen");
  await page.locator('input[type="file"]').setInputFiles({
    name: "人教版高中生物必修2遗传与进化.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 BookCourse AI promo fixture")
  });
  await expect(page.locator(".upload-add-tile.has-selection")).toBeVisible();
}

async function setupParseReady(page: Page) {
  await setupUploadSelection(page);
  await setupClick(page.getByRole("button", { name: "上传并继续", exact: true }));
  await waitForSettledScreen(page, ".parse-ready-screen");
}

async function setupChapterConfirm(page: Page) {
  await setupParseReady(page);
  await setupClick(page.getByRole("button", { name: "开始解析", exact: true }));
  await waitForSettledScreen(page, ".processing-flow-screen");
  await waitForSettledScreen(page, ".chapter-confirm-screen");
}

async function setupCourseReady(page: Page) {
  await setupChapterConfirm(page);
  await setupClick(page.getByRole("button", { name: "确认生成课程", exact: true }));
  await waitForSettledScreen(page, ".course-ready-screen");
}

async function setupStudyDirectory(page: Page) {
  await setupClick(page.locator(".nav-study"));
  await waitForSettledScreen(page, ".book-course-screen");
}

async function expandMeiosis(page: Page) {
  const chapter = page.getByRole("button", { name: /第 2 章 基因和染色体的关系.*3 个小节/ });
  await chapter.scrollIntoViewIfNeeded();
  if (await chapter.getAttribute("aria-expanded") !== "true") await setupClick(chapter);
  const section = page.locator('.study-section-toggle[aria-label="第 1 节 减数分裂和受精作用 教材第 16-26 页"]');
  await section.scrollIntoViewIfNeeded();
  if (await section.getAttribute("aria-expanded") !== "true") await setupClick(section);
  await expect(page.getByRole("region", { name: "第 1 节 减数分裂和受精作用的学习方式", exact: true })).toBeVisible();
}

async function setupLesson(page: Page) {
  await setupClick(page.getByRole("button", { name: "继续学习", exact: true }));
  await waitForSettledScreen(page, ".lesson-screen");
  await expect(page.locator(".lesson-article-header h2")).toContainText("减数分裂和受精作用");
}

async function setupAssignment(page: Page) {
  await setupStudyDirectory(page);
  await expandMeiosis(page);
  const region = page.getByRole("region", { name: "第 1 节 减数分裂和受精作用的学习方式", exact: true });
  await setupClick(region.getByRole("button", { name: /作业诊断/ }));
  await waitForSettledScreen(page, ".assignment-screen");
}

async function advanceAssignment(page: Page, includeFinal = true) {
  const card = page.locator(".assignment-exercise-card");
  await expect(card).toHaveAttribute("data-assignment-type", "judgment");
  await setupClick(page.locator(".assignment-judgment-options button").first());
  await setupClick(page.locator(".assignment-primary-action .button"));
  await expect(card).toHaveAttribute("data-assignment-type", "choice");
  const correctChoice = page.locator(".assignment-choice-options button").filter({ hasText: "同源染色体分离" }).first();
  await setupClick(await correctChoice.count() ? correctChoice : page.locator(".assignment-choice-options button").nth(1));
  await setupClick(page.locator(".assignment-primary-action .button"));
  await expect(card).toHaveAttribute("data-assignment-type", "short-answer");
  if (!includeFinal) return;
  await page.locator(".assignment-card textarea").fill(shortAnswer);
  await setupClick(page.locator(".assignment-primary-action .button"));
  await waitForSettledScreen(page, ".diagnosis-screen");
}

async function setupDiagnosis(page: Page) {
  await setupAssignment(page);
  await advanceAssignment(page, true);
}

async function installClickDot(page: Page) {
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.id = "promo-safe-click-style";
    style.textContent = [
      "#promo-capture-safe-area{position:fixed;z-index:39;right:0;bottom:0;left:0;height:39px;background:var(--color-bg,#f5f7fb);pointer-events:none}",
      "#promo-safe-click-dot{position:fixed;left:-40px;top:-40px;width:8px;height:8px;border:1px solid rgba(255,255,255,.98);border-radius:50%;background:#6d47ff;box-shadow:0 1px 3px rgba(22,18,48,.28);pointer-events:none;z-index:2147483647;opacity:0;transform:translate(-50%,-50%) scale(.82);transition:opacity 70ms linear,transform 110ms ease-out}",
      "#promo-safe-click-dot[data-visible='true']{opacity:1;transform:translate(-50%,-50%) scale(1)}"
    ].join("");
    document.head.appendChild(style);
    const safeArea = document.createElement("span");
    safeArea.id = "promo-capture-safe-area";
    safeArea.setAttribute("aria-hidden", "true");
    document.querySelector(".app-shell")?.appendChild(safeArea);
    const dot = document.createElement("span");
    dot.id = "promo-safe-click-dot";
    dot.setAttribute("aria-hidden", "true");
    document.body.appendChild(dot);
  });
}

async function safePoint(locator: Locator) {
  return locator.evaluate((element) => {
    const target = element as HTMLElement;
    const rect = target.getBoundingClientRect();
    const forbidden: DOMRect[] = [];
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.textContent?.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const box of Array.from(range.getClientRects())) {
        if (box.width > 0 && box.height > 0) forbidden.push(box);
      }
    }
    for (const child of Array.from(target.querySelectorAll("svg,img,canvas,.icon,.lucide"))) {
      const box = child.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) forbidden.push(box);
    }
    const insetX = Math.min(18, Math.max(9, rect.width * 0.16));
    const insetY = Math.min(16, Math.max(9, rect.height * 0.22));
    const xs = [rect.left + insetX, rect.left + rect.width * 0.25, rect.left + rect.width * 0.75, rect.right - insetX];
    const ys = [rect.top + insetY, rect.top + rect.height * 0.3, rect.top + rect.height * 0.7, rect.bottom - insetY];
    const distanceToBox = (x: number, y: number, box: DOMRect) => {
      const dx = Math.max(box.left - x, 0, x - box.right);
      const dy = Math.max(box.top - y, 0, y - box.bottom);
      return Math.hypot(dx, dy);
    };
    let best = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, clearance: -1 };
    for (const x of xs) {
      for (const y of ys) {
        const edge = Math.min(x - rect.left, rect.right - x, y - rect.top, rect.bottom - y);
        const content = forbidden.length > 0 ? Math.min(...forbidden.map((box) => distanceToBox(x, y, box))) : edge;
        const clearance = Math.min(edge, content);
        if (clearance > best.clearance) best = { x, y, clearance };
      }
    }
    return {
      ...best,
      height: rect.height,
      target: target.getAttribute("aria-label") || target.textContent?.trim().replace(/\s+/g, " ").slice(0, 80) || target.tagName,
      width: rect.width
    };
  });
}

async function showSafeClick(page: Page, locator: Locator, label: string, audits: ClickAudit[], trigger = true) {
  await locator.waitFor({ state: "visible", timeout: 15_000 });
  await locator.scrollIntoViewIfNeeded();
  await page.waitForTimeout(450);
  const point = await safePoint(locator);
  const shown = point.clearance >= 5;
  audits.push({ ...point, label, shown });
  if (!shown) {
    if (trigger) await locator.click();
    await page.waitForTimeout(390);
    return;
  }
  await page.evaluate(({ x, y }) => {
    const dot = document.querySelector<HTMLElement>("#promo-safe-click-dot");
    if (!dot) return;
    dot.style.left = `${x}px`;
    dot.style.top = `${y}px`;
    dot.dataset.visible = "true";
  }, point);
  await page.waitForTimeout(150);
  if (trigger) {
    const box = await locator.boundingBox();
    if (!box) throw new Error(`No click bounds for ${label}`);
    await locator.click({ position: { x: point.x - box.x, y: point.y - box.y } });
  }
  await page.waitForTimeout(130);
  await page.evaluate(() => {
    const dot = document.querySelector<HTMLElement>("#promo-safe-click-dot");
    if (dot) dot.dataset.visible = "false";
  });
  await page.waitForTimeout(260);
}

async function smoothScroll(page: Page, selector: string, distance: number, duration = 1_500) {
  await page.locator(selector).evaluate(async (element, options) => {
    const scroller = element as HTMLElement;
    const start = scroller.scrollTop;
    const target = Math.max(0, Math.min(scroller.scrollHeight - scroller.clientHeight, start + options.distance));
    const started = performance.now();
    await new Promise<void>((resolve) => {
      const step = (now: number) => {
        const t = Math.min(1, (now - started) / options.duration);
        const eased = 1 - Math.pow(1 - t, 3);
        scroller.scrollTop = start + (target - start) * eased;
        if (t < 1) requestAnimationFrame(step); else resolve();
      };
      requestAnimationFrame(step);
    });
  }, { distance, duration });
  await page.waitForTimeout(650);
}

async function recordClip(
  browser: Browser,
  name: string,
  setup: (page: Page) => Promise<void>,
  actions: (page: Page, audits: ClickAudit[]) => Promise<void>
) {
  const clipRoot = path.join(captureRoot, name);
  await fs.rm(clipRoot, { recursive: true, force: true });
  await fs.mkdir(clipRoot, { recursive: true });
  const { context, page } = await openFreshPage(browser);
  await setup(page);
  await installClickDot(page);
  const cdp = await context.newCDPSession(page);
  const frames: Array<{ file: string; time: number }> = [];
  const clickAudits: ClickAudit[] = [];
  let frameIndex = 0;
  let writeChain: Promise<void> = Promise.resolve();
  const startedAt = performance.now();
  cdp.on("Page.screencastFrame", (payload) => {
    const file = `${String(frameIndex).padStart(6, "0")}.png`;
    frameIndex += 1;
    frames.push({ file, time: (performance.now() - startedAt) / 1_000 });
    const image = Buffer.from(payload.data, "base64");
    writeChain = writeChain.then(() => fs.writeFile(path.join(clipRoot, file), image));
    void cdp.send("Page.screencastFrameAck", { sessionId: payload.sessionId }).catch(() => undefined);
  });
  await cdp.send("Page.startScreencast", { format: "png", maxWidth: 1206, maxHeight: 2622, everyNthFrame: 1 });
  await page.waitForTimeout(1_800);
  await actions(page, clickAudits);
  await page.waitForTimeout(2_600);
  const totalDuration = (performance.now() - startedAt) / 1_000;
  await cdp.send("Page.stopScreencast");
  await context.close();
  await writeChain;
  if (frames.length < 2) throw new Error(`Screencast captured only ${frames.length} frames for ${name}`);
  const timeline = ["ffconcat version 1.0"];
  for (let index = 0; index < frames.length; index += 1) {
    const frame = frames[index];
    const nextTime = index + 1 < frames.length ? frames[index + 1].time : totalDuration;
    timeline.push(`file '${frame.file}'`);
    timeline.push(`duration ${Math.max(1 / 120, nextTime - frame.time).toFixed(6)}`);
  }
  timeline.push(`file '${frames.at(-1)!.file}'`);
  await fs.writeFile(path.join(clipRoot, "timeline.ffconcat"), `${timeline.join("\n")}\n`, "utf8");
  await fs.writeFile(path.join(clipRoot, "capture.json"), JSON.stringify({
    averageCapturedFps: frames.length / totalDuration,
    clickAudits,
    deviceScaleFactor: 3,
    durationSeconds: totalDuration,
    frameCount: frames.length,
    name,
    outputPixels: { width: 1206, height: 2622 },
    viewportCssPixels: { width: 402, height: 874 }
  }, null, 2), "utf8");
}

test.describe("4 minute promo guide raw phone operations", () => {
  test.describe.configure({ timeout: 180_000 });

  test("02 home overview", async ({ browser }) => {
    await recordClip(browser, "02-home-overview-take01", async () => {}, async (page) => {
      await smoothScroll(page, '.screen-content[data-screen="home"]', 430, 2_200);
    });
  });

  test("03 upload material", async ({ browser }) => {
    await recordClip(browser, "03-upload-material-take01", async () => {}, async (page, audits) => {
      await showSafeClick(page, page.locator('[data-home-global-action="upload"]'), "打开导入课程", audits);
      await waitForSettledScreen(page, ".upload-sheet-screen");
      await showSafeClick(page, page.locator(".upload-add-tile"), "选择学习资料", audits, false);
      await page.locator('input[type="file"]').setInputFiles({
        name: "人教版高中生物必修2遗传与进化.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.from("%PDF-1.4 BookCourse AI promo fixture")
      });
      await expect(page.locator(".upload-add-tile.has-selection")).toBeVisible();
      await page.waitForTimeout(1_200);
      await showSafeClick(page, page.getByRole("button", { name: "上传并继续", exact: true }), "上传并继续", audits);
      await waitForSettledScreen(page, ".parse-ready-screen");
    });
  });

  test("04 parse ready", async ({ browser }) => {
    await recordClip(browser, "04-parse-ready-take01", setupParseReady, async (page, audits) => {
      await page.waitForTimeout(900);
      await showSafeClick(page, page.getByRole("button", { name: "开始解析", exact: true }), "开始解析", audits);
      await waitForSettledScreen(page, ".processing-flow-screen");
    });
  });

  test("05 processing", async ({ browser }) => {
    await recordClip(browser, "05-processing-take01", setupParseReady, async (page, audits) => {
      await showSafeClick(page, page.getByRole("button", { name: "开始解析", exact: true }), "开始解析并观察进度", audits);
      await waitForSettledScreen(page, ".processing-flow-screen");
      await waitForSettledScreen(page, ".chapter-confirm-screen");
    });
  });

  test("06 chapter confirmation", async ({ browser }) => {
    await recordClip(browser, "06-chapter-confirm-take01", setupChapterConfirm, async (page, audits) => {
      const expandAll = page.getByRole("button", { name: "全部展开", exact: true });
      if (await expandAll.count()) await showSafeClick(page, expandAll, "全部展开", audits);
      await smoothScroll(page, '.screen-content[data-screen="chapterConfirm"]', 760, 2_200);
      await showSafeClick(page, page.getByRole("button", { name: "确认生成课程", exact: true }), "确认生成课程", audits);
      await waitForSettledScreen(page, ".course-ready-screen");
    });
  });

  test("07 course ready", async ({ browser }) => {
    await recordClip(browser, "07-course-ready-take01", setupCourseReady, async (page, audits) => {
      await page.waitForTimeout(1_100);
      await showSafeClick(page, page.getByRole("button", { name: "进入学习", exact: true }), "进入学习", audits);
      await waitForSettledScreen(page, ".book-course-screen");
    });
  });

  test("08 course directory", async ({ browser }) => {
    await recordClip(browser, "08-course-directory-take01", setupStudyDirectory, async (page, audits) => {
      await smoothScroll(page, '.screen-content[data-screen="study"]', 520, 1_900);
      const chapter = page.getByRole("button", { name: /第 2 章 基因和染色体的关系.*3 个小节/ });
      if (await chapter.getAttribute("aria-expanded") !== "true") await showSafeClick(page, chapter, "展开第 2 章", audits);
      const section = page.locator('.study-section-toggle[aria-label="第 1 节 减数分裂和受精作用 教材第 16-26 页"]');
      await section.scrollIntoViewIfNeeded();
      if (await section.getAttribute("aria-expanded") !== "true") await showSafeClick(page, section, "展开减数分裂和受精作用", audits);
      const region = page.getByRole("region", { name: "第 1 节 减数分裂和受精作用的学习方式", exact: true });
      await showSafeClick(page, region.getByRole("button", { name: "进入学习", exact: true }), "进入学习", audits);
      await waitForSettledScreen(page, ".lesson-screen");
    });
  });

  test("09 lesson learning", async ({ browser }) => {
    await recordClip(browser, "09-lesson-learning-take01", setupLesson, async (page) => {
      const scroller = '.screen-content[data-screen="lesson"]';
      await smoothScroll(page, scroller, 480, 2_100);
      await page.waitForTimeout(900);
      await smoothScroll(page, scroller, 540, 2_200);
    });
  });

  test("10 AI chat", async ({ browser }) => {
    await recordClip(browser, "10-ai-chat-take01", async () => {}, async (page, audits) => {
      await showSafeClick(page, page.getByRole("button", { name: "打开 AI 助手", exact: true }), "打开 AI 助手", audits);
      const dialog = page.getByRole("dialog", { name: "AI 导学助手", exact: true });
      await expect(dialog).toBeVisible();
      const input = dialog.getByRole("textbox", { name: "向 AI 助手提问", exact: true });
      await showSafeClick(page, input, "输入问题", audits);
      await input.fill("");
      await input.pressSequentially(question, { delay: 34 });
      await page.waitForTimeout(700);
      await showSafeClick(page, dialog.getByRole("button", { name: "发送", exact: true }), "发送问题", audits);
      await expect(dialog.locator(".ai-message.user")).toHaveCount(1, { timeout: 15_000 });
      await expect(dialog.locator(".ai-message.ai")).toHaveCount(1, { timeout: 15_000 });
      const answer = dialog.locator(".ai-message.ai").last();
      await expect(answer).not.toHaveText("", { timeout: 15_000 });
      await expect(answer).not.toContainText("回答失败", { timeout: 15_000 });
      await expect(dialog.locator(".ai-mode-row")).toHaveCSS("position", "static");
      await expect.poll(async () => dialog.evaluate((element) => {
        const messages = element.querySelector<HTMLElement>(".ai-message-list")?.getBoundingClientRect();
        const modes = element.querySelector<HTMLElement>(".ai-mode-row")?.getBoundingClientRect();
        return messages && modes ? modes.top - messages.bottom : Number.NEGATIVE_INFINITY;
      }), { timeout: 15_000 }).toBeGreaterThanOrEqual(8);
      const conversation = dialog.locator(".ai-dialog-scroll");
      await conversation.evaluate((element) => {
        const messages = element.querySelector<HTMLElement>(".ai-message-list");
        if (!messages) return;
        const viewport = element.getBoundingClientRect();
        const messageBox = messages.getBoundingClientRect();
        element.scrollTop += messageBox.top - viewport.top - 8;
      });
      await page.waitForTimeout(220);
      await conversation.evaluate((element) => {
        const messages = element.querySelector<HTMLElement>(".ai-message-list");
        if (!messages) return;
        const viewport = element.getBoundingClientRect();
        const messageBox = messages.getBoundingClientRect();
        element.scrollTop += messageBox.top - viewport.top - 8;
      });
      await expect.poll(async () => answer.evaluate((element) => {
        const answerBox = element.getBoundingClientRect();
        const viewport = element.closest(".ai-dialog-scroll")?.getBoundingClientRect();
        if (!viewport || answerBox.height <= 0) return 0;
        const visibleTop = Math.max(answerBox.top, viewport.top);
        const visibleBottom = Math.min(answerBox.bottom, viewport.bottom);
        return Math.max(0, visibleBottom - visibleTop) / answerBox.height;
      }), { timeout: 15_000 }).toBeGreaterThanOrEqual(0.5);
      await page.waitForTimeout(3_600);
    });
  });

  test("11 source verification and note", async ({ browser }) => {
    await recordClip(browser, "11-source-note-take01", async (page) => {
      await setupLesson(page);
      await page.getByRole("button", { name: "查看教材第 16 页", exact: true }).scrollIntoViewIfNeeded();
    }, async (page, audits) => {
      await showSafeClick(page, page.getByRole("button", { name: "查看教材第 16 页", exact: true }), "查看教材原文", audits);
      await waitForSettledScreen(page, ".source-reader-screen");
      await smoothScroll(page, '.screen-content[data-screen="source"]', 360, 1_600);
      await showSafeClick(page, page.getByRole("button", { name: "返回", exact: true }), "返回课程", audits);
      await waitForSettledScreen(page, ".lesson-screen");
      const concept = page.getByRole("button", { name: "查看核心概念：同源染色体", exact: true });
      await concept.scrollIntoViewIfNeeded();
      await showSafeClick(page, concept, "打开核心概念", audits);
      const noteDialog = page.getByRole("dialog", { name: "核心概念", exact: true });
      await expect(noteDialog).toBeVisible();
      await showSafeClick(page, noteDialog.getByRole("button", { name: "保存到笔记", exact: true }), "保存到笔记", audits);
      await expect(page.getByRole("status")).toContainText("已保存到导学笔记");
    });
  });

  test("12 flashcards", async ({ browser }) => {
    await recordClip(browser, "12-flashcards-take01", async (page) => {
      await setupStudyDirectory(page);
      await expandMeiosis(page);
      const region = page.getByRole("region", { name: "第 1 节 减数分裂和受精作用的学习方式", exact: true });
      await setupClick(region.getByRole("button", { name: /闪卡复习/ }));
      await waitForSettledScreen(page, ".flashcard-screen");
    }, async (page, audits) => {
      const card = page.getByRole("button", { name: /点击查看答案/ });
      await showSafeClick(page, card, "翻开第一张闪卡", audits);
      await expect(page.locator(".memory-reveal")).toHaveAttribute("aria-pressed", "true");
      await showSafeClick(page, page.getByRole("button", { name: /记住了/ }), "记住了", audits);
      await page.waitForTimeout(1_000);
      await showSafeClick(page, page.getByRole("button", { name: /点击查看答案/ }), "翻开第二张闪卡", audits);
      await showSafeClick(page, page.getByRole("button", { name: /还不熟/ }), "还不熟", audits);
    });
  });

  test("13 three step assignment", async ({ browser }) => {
    await recordClip(browser, "13-assignment-take01", setupAssignment, async (page, audits) => {
      const card = page.locator(".assignment-exercise-card");
      await showSafeClick(page, page.locator(".assignment-judgment-options button").first(), "判断题选择正确", audits);
      await showSafeClick(page, page.locator(".assignment-primary-action .button"), "提交判断题", audits);
      await expect(card).toHaveAttribute("data-assignment-type", "choice");
      const correctChoice = page.locator(".assignment-choice-options button").filter({ hasText: "同源染色体分离" }).first();
      await showSafeClick(page, await correctChoice.count() ? correctChoice : page.locator(".assignment-choice-options button").nth(1), "选择正确选项", audits);
      await showSafeClick(page, page.locator(".assignment-primary-action .button"), "提交选择题", audits);
      await expect(card).toHaveAttribute("data-assignment-type", "short-answer");
      const textarea = page.locator(".assignment-card textarea");
      await showSafeClick(page, textarea, "输入简答答案", audits);
      await textarea.pressSequentially(shortAnswer, { delay: 24 });
      await page.waitForTimeout(1_000);
      await showSafeClick(page, page.locator(".assignment-primary-action .button"), "提交并查看诊断", audits);
      await waitForSettledScreen(page, ".diagnosis-screen");
    });
  });

  test("14 diagnosis", async ({ browser }) => {
    await recordClip(browser, "14-diagnosis-take01", setupDiagnosis, async (page, audits) => {
      await smoothScroll(page, '.screen-content[data-screen="diagnosis"]', 520, 1_800);
      await showSafeClick(page, page.getByRole("button", { name: "查看错题本", exact: true }), "查看错题本", audits);
      await waitForSettledScreen(page, ".mistake-book-screen");
    });
  });

  test("15 mistake review", async ({ browser }) => {
    await recordClip(browser, "15-mistake-review-take01", async (page) => {
      await setupDiagnosis(page);
      await setupClick(page.getByRole("button", { name: "查看错题本", exact: true }));
      await waitForSettledScreen(page, ".mistake-book-screen");
    }, async (page, audits) => {
      await showSafeClick(page, page.getByRole("button", { name: "开始今日复习", exact: true }), "开始今日复习", audits);
      const answer = page.getByRole("textbox", { name: "写下你的答案", exact: true });
      await showSafeClick(page, answer, "填写错题答案", audits);
      await answer.fill("同源染色体在减数第一次分裂时分离。");
      await showSafeClick(page, page.getByRole("button", { name: "提交并对照", exact: true }), "提交并对照", audits);
      await expect(page.getByRole("heading", { name: "这次为什么会错？", exact: true })).toBeVisible();
      await showSafeClick(page, page.getByRole("button", { name: "概念混淆", exact: true }), "选择错因", audits);
      await showSafeClick(page, page.getByRole("button", { name: /有点模糊/ }), "标记稍后巩固", audits);
    });
  });

  test("16 study plan and community", async ({ browser }) => {
    await recordClip(browser, "16-plan-community-take01", async (page) => {
      await setupStudyDirectory(page);
      await setupClick(page.locator(".study-plan-heading button"));
      await waitForSettledScreen(page, ".study-plan-screen");
    }, async (page, audits) => {
      await showSafeClick(page, page.getByRole("button", { name: "第 2 天", exact: true }), "切换到第 2 天", audits);
      const task = page.locator(".timeline-item:not(.done)").first();
      if (await task.count()) await showSafeClick(page, task, "完成当天任务", audits);
      await showSafeClick(page, page.getByRole("button", { name: "返回", exact: true }), "返回课程", audits);
      await waitForSettledScreen(page, ".book-course-screen");
      await showSafeClick(page, page.getByRole("button", { name: "社区", exact: true }), "进入社区", audits);
      await waitForSettledScreen(page, ".community-screen");
      const book = page.getByRole("button", { name: "进入课程：遗传与进化", exact: true });
      await showSafeClick(page, book, "查看推荐教材", audits);
      await waitForSettledScreen(page, ".community-detail-screen");
    });
  });

  test("17 clean home outro", async ({ browser }) => {
    await recordClip(browser, "17-home-outro-take01", async () => {}, async (page) => {
      const scroller = '.screen-content[data-screen="home"]';
      await smoothScroll(page, scroller, 220, 1_200);
      await page.waitForTimeout(500);
      await smoothScroll(page, scroller, -220, 1_200);
      await expect(page.getByRole("heading", { name: /小明同学/ })).toBeVisible();
    });
  });
});
