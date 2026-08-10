/* global document, window */

import { chromium } from "playwright";
import {
  access,
  mkdir,
  rename,
  rmdir,
  stat,
  writeFile
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, "..");
const moviesDirectory = path.join(projectRoot, "movies");
const rehearsalDirectory = path.join(projectRoot, "output", "recording-rehearsal");
const defaultTextbookPath = "C:\\Users\\asd25\\Desktop\\示范文件\\人教版高中生物必修2遗传与进化 (人民教育出版社, 课程教材研究所, 生物课程教材研究开发中心.pdf";
const defaultBaseUrl = "http://127.0.0.1:5173/?device=iphone-17-pro&orientation=portrait&quality=retina-3x&chrome=1";
const recordingViewport = { height: 2880, width: 1440 };
const routeLabels = [
  "上传教材",
  "开始解析",
  "原书目录被识别",
  "确认生成课程",
  "学习计划与课程目录",
  "进入减数分裂章节",
  "AI 提问",
  "回到教材第 16 页",
  "完成三步练习"
];

function optionValue(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function createStamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

const rehearsal = process.argv.includes("--rehearse");
const baseUrl = optionValue("--base-url", defaultBaseUrl);
const textbookPath = path.resolve(optionValue("--pdf", defaultTextbookPath));
const stamp = createStamp();
const outputDirectory = rehearsal ? rehearsalDirectory : moviesDirectory;
const outputStem = rehearsal
  ? "bookcourse-ai-ios-shell-clicks-rehearsal-" + stamp
  : "bookcourse-ai-operation-2k-ios-shell-clicks-" + stamp;
const outputVideoPath = path.join(outputDirectory, outputStem + ".webm");
const clickCueStillPath = path.join(outputDirectory, outputStem + "-click-cue.png");
const page16StillPath = path.join(outputDirectory, outputStem + "-page16.png");
const completedStillPath = path.join(outputDirectory, outputStem + "-complete.png");
const manifestPath = path.join(outputDirectory, outputStem + ".json");
const errorStillPath = path.join(outputDirectory, outputStem + "-error.png");
const videoStagingDirectory = path.join(moviesDirectory, ".playwright-video-" + stamp);
const finalPacing = rehearsal ? 0.08 : 1;

async function ensureReadableFile(filePath, label) {
  try {
    await access(filePath);
  } catch {
    throw new Error(label + "不存在或不可读取：" + filePath);
  }
}

async function wait(milliseconds) {
  const scaled = Math.max(rehearsal ? 40 : 0, Math.round(milliseconds * finalPacing));
  if (scaled > 0) await activePage.waitForTimeout(scaled);
}

async function waitForAttribute(locator, attribute, expected, timeout = 12_000) {
  const deadline = Date.now() + timeout;
  let actual = null;
  while (Date.now() < deadline) {
    actual = await locator.getAttribute(attribute).catch(() => null);
    if (actual === expected) return;
    await activePage.waitForTimeout(100);
  }
  throw new Error("等待属性失败：" + attribute + "，期望 " + expected + "，实际 " + actual);
}

async function waitForText(locator, expected, timeout = 12_000) {
  const deadline = Date.now() + timeout;
  let actual = "";
  while (Date.now() < deadline) {
    actual = (await locator.textContent().catch(() => "")) || "";
    const matches = expected instanceof RegExp ? expected.test(actual) : actual.includes(expected);
    if (matches) return;
    await activePage.waitForTimeout(100);
  }
  throw new Error("等待文本失败：" + String(expected) + "，实际 " + actual);
}

async function waitForCount(locator, expected, timeout = 12_000) {
  const deadline = Date.now() + timeout;
  let actual = -1;
  while (Date.now() < deadline) {
    actual = await locator.count().catch(() => -1);
    if (actual === expected) return;
    await activePage.waitForTimeout(100);
  }
  throw new Error("等待元素数量失败：期望 " + expected + "，实际 " + actual);
}

async function waitForScreen(selector, label, timeout = 18_000) {
  const screen = appFrame.locator(selector);
  await screen.waitFor({ state: "visible", timeout });
  await waitForAttribute(
    appFrame.locator(".motion-screen-transition"),
    "data-motion-state",
    "idle",
    timeout
  );
  console.log("[ready] " + label);
  return screen;
}

async function applyDeviceShellRecordingLayout() {
  const studio = activePage.locator(".device-preview-studio");
  await studio.waitFor({ state: "visible", timeout: 15_000 });
  const layoutCss = [
    "html, body, #root { width: 100%; height: 100%; margin: 0; overflow: hidden; }",
    "body { background: #e7edff; }",
    ".device-preview-studio {",
    "  width: 100vw !important;",
    "  height: 100vh !important;",
    "  display: grid !important;",
    "  grid-template-columns: minmax(0, 1fr) !important;",
    "  grid-template-rows: minmax(0, 1fr) !important;",
    "  overflow: hidden !important;",
    "  background:",
    "    radial-gradient(circle at 50% 9%, rgba(255,255,255,0.98), transparent 30%),",
    "    radial-gradient(circle at 15% 70%, rgba(210,226,255,0.88), transparent 34%),",
    "    linear-gradient(155deg, #eef3ff 0%, #f9fbff 48%, #e5edff 100%) !important;",
    "}",
    ".device-preview-toolbar,",
    ".device-preview-output-summary,",
    ".device-preview-status-announcer { display: none !important; }",
    ".device-preview-canvas-area,",
    ".device-preview-studio:not([data-preview-quality='fit']) .device-preview-canvas-area {",
    "  grid-column: 1 !important;",
    "  grid-row: 1 !important;",
    "  display: grid !important;",
    "  place-items: center !important;",
    "  padding: 0 !important;",
    "  overflow: visible !important;",
    "}",
    ".device-preview-canvas {",
    "  overflow: visible !important;",
    "  border-radius: 0 !important;",
    "  box-shadow: none !important;",
    "}",
    ".device-preview-studio .device-preview-frame--iphone-17-pro {",
    "  border-radius: var(--iphone-screen-radius) !important;",
    "}",
    ".device-preview-studio .device-preview-frame--iphone-17-pro > .device-preview-iframe {",
    "  border-radius: var(--iphone-screen-radius) !important;",
    "  clip-path: inset(0 round var(--iphone-screen-radius)) !important;",
    "}"
  ].join("\n");

  await activePage.evaluate((css) => {
    let style = document.querySelector("#promo-device-shell-layout");
    if (!style) {
      style = document.createElement("style");
      style.id = "promo-device-shell-layout";
      document.head.appendChild(style);
    }
    style.textContent = css;
  }, layoutCss);

  const bezel = activePage.locator('[data-testid="device-preview-bezel"]');
  const island = activePage.locator('[data-testid="device-preview-dynamic-island"]');
  await bezel.waitFor({ state: "visible", timeout: 12_000 });
  await island.waitFor({ state: "visible", timeout: 12_000 });
  await activePage.waitForTimeout(350);

  return await activePage.locator('[data-testid="device-preview-frame"]').evaluate((frame) => {
    const frameBounds = frame.getBoundingClientRect();
    const bezelElement = frame.querySelector(".device-preview-bezel");
    const islandElement = frame.querySelector(".device-preview-dynamic-island");
    const bezelBounds = bezelElement?.getBoundingClientRect();
    const islandBounds = islandElement?.getBoundingClientRect();
    return {
      bezel: bezelBounds ? {
        height: Math.round(bezelBounds.height),
        width: Math.round(bezelBounds.width),
        x: Math.round(bezelBounds.x),
        y: Math.round(bezelBounds.y)
      } : null,
      dynamicIsland: islandBounds ? {
        height: Math.round(islandBounds.height),
        width: Math.round(islandBounds.width),
        x: Math.round(islandBounds.x),
        y: Math.round(islandBounds.y)
      } : null,
      screen: {
        height: Math.round(frameBounds.height),
        width: Math.round(frameBounds.width),
        x: Math.round(frameBounds.x),
        y: Math.round(frameBounds.y)
      }
    };
  });
}

async function installInteractionOverlay() {
  const overlayCss = [
    "#promo-touch-pointer, #promo-touch-ripple, #promo-action-caption {",
    "  position: fixed;",
    "  z-index: 2147483647;",
    "  pointer-events: none;",
    "}",
    "#promo-touch-pointer {",
    "  left: -80px;",
    "  top: -80px;",
    "  width: 31px;",
    "  height: 31px;",
    "  border: 3px solid rgba(255,255,255,0.98);",
    "  border-radius: 999px;",
    "  background: rgba(109,71,255,0.76);",
    "  box-shadow: 0 4px 16px rgba(49,30,120,0.38), 0 0 0 4px rgba(109,71,255,0.2);",
    "  opacity: 0;",
    "  transform: translate(-50%, -50%) scale(0.86);",
    "  transition:",
    "    left 420ms cubic-bezier(0.22, 1, 0.36, 1),",
    "    top 420ms cubic-bezier(0.22, 1, 0.36, 1),",
    "    opacity 160ms ease,",
    "    transform 160ms ease,",
    "    background-color 160ms ease;",
    "}",
    "#promo-touch-pointer[data-state='moving'],",
    "#promo-touch-pointer[data-state='ready'] {",
    "  opacity: 1;",
    "  transform: translate(-50%, -50%) scale(1);",
    "}",
    "#promo-touch-pointer[data-state='pressing'] {",
    "  opacity: 1;",
    "  transform: translate(-50%, -50%) scale(0.68);",
    "  background: rgba(87,49,238,0.94);",
    "}",
    "#promo-touch-pointer[data-state='leaving'] {",
    "  opacity: 0;",
    "  transform: translate(-50%, -50%) scale(1.18);",
    "}",
    "#promo-touch-ripple {",
    "  left: -80px;",
    "  top: -80px;",
    "  width: 34px;",
    "  height: 34px;",
    "  border: 3px solid rgba(121,82,255,0.92);",
    "  border-radius: 999px;",
    "  opacity: 0;",
    "  transform: translate(-50%, -50%) scale(0.55);",
    "}",
    "#promo-touch-ripple[data-state='active'] {",
    "  animation: promo-touch-ripple 520ms cubic-bezier(0.16, 1, 0.3, 1) both;",
    "}",
    "#promo-action-caption {",
    "  left: -200px;",
    "  top: -200px;",
    "  max-width: 310px;",
    "  padding: 9px 14px;",
    "  border: 1px solid rgba(255,255,255,0.7);",
    "  border-radius: 999px;",
    "  color: #fff;",
    "  background: rgba(24,28,47,0.92);",
    "  box-shadow: 0 8px 22px rgba(28,31,63,0.28);",
    "  font-size: 13px;",
    "  font-weight: 800;",
    "  line-height: 1.25;",
    "  letter-spacing: 0.01em;",
    "  text-align: center;",
    "  white-space: nowrap;",
    "  opacity: 0;",
    "  transform: translate(-50%, -50%) translateY(5px) scale(0.96);",
    "  transition: opacity 180ms ease, transform 240ms cubic-bezier(0.22, 1, 0.36, 1);",
    "}",
    "#promo-action-caption[data-state='visible'] {",
    "  opacity: 1;",
    "  transform: translate(-50%, -50%) translateY(0) scale(1);",
    "}",
    "[data-promo-click-target='true'] {",
    "  outline: 3px solid rgba(255,255,255,0.98) !important;",
    "  outline-offset: 3px !important;",
    "  box-shadow:",
    "    0 0 0 7px rgba(119,77,255,0.56),",
    "    0 10px 26px rgba(67,43,164,0.34) !important;",
    "  filter: brightness(1.04) saturate(1.08);",
    "  animation: promo-target-focus 720ms cubic-bezier(0.22, 1, 0.36, 1) both;",
    "}",
    "@keyframes promo-touch-ripple {",
    "  0% { opacity: 0.95; transform: translate(-50%, -50%) scale(0.55); }",
    "  100% { opacity: 0; transform: translate(-50%, -50%) scale(2.75); }",
    "}",
    "@keyframes promo-target-focus {",
    "  0% { transform: scale(1); }",
    "  45% { transform: scale(1.035); }",
    "  100% { transform: scale(1.018); }",
    "}"
  ].join("\n");

  await appFrame.locator("body").evaluate((body, css) => {
    let style = document.querySelector("#promo-interaction-style");
    if (!style) {
      style = document.createElement("style");
      style.id = "promo-interaction-style";
      document.head.appendChild(style);
    }
    style.textContent = css;

    const ensureOverlayElement = (id) => {
      let element = document.querySelector("#" + id);
      if (!element) {
        element = document.createElement("div");
        element.id = id;
        element.setAttribute("aria-hidden", "true");
        body.appendChild(element);
      }
      return element;
    };
    ensureOverlayElement("promo-touch-pointer");
    ensureOverlayElement("promo-touch-ripple");
    ensureOverlayElement("promo-action-caption");
  }, overlayCss);
}

async function showInteractionCue(locator, label) {
  return await locator.evaluate((element, actionLabel) => {
    document.querySelectorAll("[data-promo-click-target='true']").forEach((target) => {
      target.removeAttribute("data-promo-click-target");
    });
    element.setAttribute("data-promo-click-target", "true");

    const bounds = element.getBoundingClientRect();
    const pointer = document.querySelector("#promo-touch-pointer");
    const ripple = document.querySelector("#promo-touch-ripple");
    const caption = document.querySelector("#promo-action-caption");
    if (!pointer || !ripple || !caption) {
      throw new Error("点击可视化层尚未初始化");
    }

    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const centerX = bounds.left + bounds.width / 2;
    const pointerX = bounds.width > 96
      ? Math.min(bounds.right - 22, viewportWidth - 18)
      : centerX;
    const pointerY = bounds.top + bounds.height / 2;
    const captionX = Math.min(Math.max(centerX, 88), viewportWidth - 88);
    const captionY = bounds.top > 105
      ? Math.max(72, bounds.top - 42)
      : Math.min(viewportHeight - 72, bounds.bottom + 42);

    pointer.style.left = pointerX + "px";
    pointer.style.top = pointerY + "px";
    pointer.dataset.state = "moving";
    ripple.style.left = pointerX + "px";
    ripple.style.top = pointerY + "px";
    ripple.dataset.state = "idle";
    caption.textContent = "点击 · " + actionLabel;
    caption.style.left = captionX + "px";
    caption.style.top = captionY + "px";
    caption.dataset.state = "visible";

    return {
      height: Math.round(bounds.height),
      pointerX: Math.round(pointerX),
      pointerY: Math.round(pointerY),
      width: Math.round(bounds.width),
      x: Math.round(bounds.x),
      y: Math.round(bounds.y)
    };
  }, label);
}

async function pressInteractionCue() {
  await appFrame.locator("body").evaluate((body) => {
    const pointer = body.querySelector("#promo-touch-pointer");
    const ripple = body.querySelector("#promo-touch-ripple");
    if (pointer) pointer.dataset.state = "pressing";
    if (ripple) {
      ripple.dataset.state = "idle";
      void ripple.getBoundingClientRect();
      ripple.dataset.state = "active";
    }
  });
}

async function clearInteractionCue() {
  await appFrame.locator("body").evaluate((body) => {
    body.querySelectorAll("[data-promo-click-target='true']").forEach((target) => {
      target.removeAttribute("data-promo-click-target");
    });
    const pointer = body.querySelector("#promo-touch-pointer");
    const caption = body.querySelector("#promo-action-caption");
    if (pointer) pointer.dataset.state = "leaving";
    if (caption) caption.dataset.state = "hidden";
  }).catch(() => {});
}

async function smoothIntoView(locator, label, duration = 1_100) {
  await locator.waitFor({ state: "attached", timeout: 12_000 });
  await locator.evaluate((element) => {
    element.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
  });
  await wait(duration);
  console.log("[view] " + label);
}

async function clickAction(locator, label, options = {}) {
  const timeout = options.timeout || 12_000;
  await locator.waitFor({ state: "visible", timeout });
  if (!options.skipScroll) {
    await locator.scrollIntoViewIfNeeded({ timeout });
  }
  await locator.hover({ timeout });
  await wait(options.hoverDelay || 220);
  const targetBounds = await showInteractionCue(locator, label);
  await activePage.waitForTimeout(rehearsal ? 260 : 480);
  if (!clickCueCaptured && label === "开始解析") {
    await captureStill(clickCueStillPath, "点击提示");
    clickCueCaptured = true;
  }
  await pressInteractionCue();
  await activePage.waitForTimeout(rehearsal ? 120 : 180);
  await locator.click({ timeout });
  interactionEvents.push({
    action: label,
    atSeconds: Number(((Date.now() - recordingStartedAt) / 1000).toFixed(3)),
    target: targetBounds
  });
  await activePage.waitForTimeout(rehearsal ? 140 : 340);
  await clearInteractionCue();
  console.log("[click] " + label);
}

async function typeNaturally(locator, value, delay = 28) {
  await locator.waitFor({ state: "visible", timeout: 12_000 });
  await locator.fill("");
  if (rehearsal) {
    await locator.fill(value);
  } else {
    await locator.pressSequentially(value, { delay });
  }
}

async function captureStill(filePath, label) {
  await activePage.screenshot({
    path: filePath,
    fullPage: false,
    type: "png"
  });
  console.log("[still] " + label + " -> " + filePath);
}

async function verifyVideoMetadata(browser, videoPath) {
  const verificationContext = await browser.newContext({
    locale: "zh-CN",
    viewport: recordingViewport
  });
  const verificationPage = await verificationContext.newPage();
  try {
    await verificationPage.goto(pathToFileURL(videoPath).href, {
      waitUntil: "load",
      timeout: 20_000
    });
    const video = verificationPage.locator("video");
    await video.waitFor({ state: "attached", timeout: 12_000 });
    return await video.evaluate(async (element) => {
      if (element.readyState < 1) {
        await new Promise((resolve, reject) => {
          const timeoutId = window.setTimeout(
            () => reject(new Error("视频元数据读取超时")),
            10_000
          );
          element.addEventListener("loadedmetadata", () => {
            window.clearTimeout(timeoutId);
            resolve();
          }, { once: true });
          element.addEventListener("error", () => {
            window.clearTimeout(timeoutId);
            reject(new Error("浏览器无法读取视频"));
          }, { once: true });
        });
      }
      return {
        durationSeconds: Number(element.duration.toFixed(3)),
        height: element.videoHeight,
        width: element.videoWidth
      };
    });
  } finally {
    await verificationContext.close();
  }
}

await ensureReadableFile(textbookPath, "教材 PDF");
await mkdir(outputDirectory, { recursive: true });
if (!rehearsal) await mkdir(videoStagingDirectory, { recursive: true });

const browser = await chromium.launch({
  headless: true
});
const contextOptions = {
  colorScheme: "light",
  deviceScaleFactor: 1,
  locale: "zh-CN",
  reducedMotion: "no-preference",
  timezoneId: "Asia/Hong_Kong",
  viewport: recordingViewport
};
if (!rehearsal) {
  contextOptions.recordVideo = {
    dir: videoStagingDirectory,
    size: recordingViewport
  };
}

const context = await browser.newContext(contextOptions);
const activePage = await context.newPage();
const recordedVideo = activePage.video();
const interactionEvents = [];
const recordingStartedAt = Date.now();
let appFrame;
let canvasMetadata;
let clickCueCaptured = false;
let deviceShellMetadata;
let routeCompleted;

activePage.on("pageerror", (error) => {
  console.warn("[pageerror] " + error.message);
});

try {
  await activePage.goto(baseUrl, {
    waitUntil: "domcontentloaded",
    timeout: 20_000
  });

  deviceShellMetadata = await applyDeviceShellRecordingLayout();
  const canvas = activePage.locator('[data-testid="device-preview-canvas"]');
  await canvas.waitFor({ state: "visible", timeout: 15_000 });
  await waitForAttribute(canvas, "data-canvas-width", "1206");
  await waitForAttribute(canvas, "data-canvas-height", "2622");
  canvasMetadata = await canvas.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return {
      canvasHeight: Number(element.getAttribute("data-canvas-height")),
      canvasWidth: Number(element.getAttribute("data-canvas-width")),
      outputHeight: window.innerHeight,
      outputWidth: window.innerWidth,
      renderedHeight: Math.round(bounds.height),
      renderedWidth: Math.round(bounds.width),
      x: Math.round(bounds.x),
      y: Math.round(bounds.y)
    };
  });

  appFrame = activePage.frameLocator('iframe[title="BookCourse AI 设备预览内层应用"]');
  await waitForScreen(".home-dashboard", "首页");
  await installInteractionOverlay();
  await wait(1_700);

  await clickAction(
    appFrame.locator('[data-home-global-action="upload"]'),
    "上传教材"
  );
  await waitForScreen(".upload-sheet-screen", "上传教材页");
  await wait(1_150);

  const fileChooserPromise = activePage.waitForEvent("filechooser", {
    timeout: 12_000
  });
  await clickAction(
    appFrame.getByRole("button", { name: "选择学习资料", exact: true }),
    "打开教材文件选择器"
  );
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles(textbookPath);
  await appFrame.locator(".upload-selection-summary").waitFor({
    state: "visible",
    timeout: 12_000
  });
  await wait(1_700);

  await clickAction(
    appFrame.getByRole("button", { name: "上传并继续", exact: true }),
    "上传并继续"
  );
  await waitForScreen(".parse-ready-screen", "解析确认页");
  await wait(1_500);

  await clickAction(
    appFrame.getByRole("button", { name: "开始后台解析", exact: true }),
    "开始解析"
  );
  await waitForScreen(".processing-flow-screen", "教材解析进度");
  const progress = appFrame.locator(".processing-card .progress-wrap");
  for (const value of [18, 46, 74]) {
    await waitForAttribute(progress, "aria-label", "解析进度 " + value + "%", 10_000);
    console.log("[progress] " + value + "%");
    await wait(700);
  }

  await waitForScreen(".chapter-confirm-screen", "原书目录识别结果", 20_000);
  await wait(1_800);
  const directory = appFrame.locator(".chapter-confirm-directory");
  await smoothIntoView(directory, "本次解析目录", 1_350);
  const expandAll = appFrame.locator(".toc-toggle-all");
  if (
    await expandAll.count() === 1 &&
    await expandAll.isVisible() &&
    (await expandAll.textContent() || "").includes("全部展开")
  ) {
    await clickAction(expandAll, "全部展开原书目录", { skipScroll: true });
  }
  await wait(2_200);

  await clickAction(
    appFrame.getByRole("button", { name: "确认生成课程", exact: true }),
    "确认生成课程"
  );
  await waitForScreen(".course-ready-screen", "课程生成完成", 25_000);
  await wait(1_800);

  await clickAction(
    appFrame.getByRole("button", { name: "进入学习", exact: true }),
    "进入学习"
  );
  await waitForScreen(".book-course-screen", "学习计划与课程目录");
  await waitForText(appFrame.locator(".book-course-screen"), "学习计划");
  await wait(2_000);

  const secondChapter = appFrame.getByRole("button", {
    name: /第 2 章 基因和染色体的关系.*3 个小节/
  });
  await smoothIntoView(secondChapter, "第 2 章 基因和染色体的关系", 1_250);
  if (await secondChapter.getAttribute("aria-expanded") !== "true") {
    await clickAction(secondChapter, "展开第 2 章", { skipScroll: true });
  }

  const meiosisToggle = appFrame.locator(
    '.study-section-toggle[aria-label="第 1 节 减数分裂和受精作用 教材第 16-26 页"]'
  );
  await smoothIntoView(meiosisToggle, "减数分裂和受精作用", 1_200);
  if (await meiosisToggle.getAttribute("aria-expanded") !== "true") {
    await clickAction(meiosisToggle, "展开减数分裂章节", { skipScroll: true });
  }

  const learningRegion = appFrame.getByRole("region", {
    name: "第 1 节 减数分裂和受精作用的学习方式",
    exact: true
  });
  await learningRegion.waitFor({ state: "visible", timeout: 12_000 });
  await wait(1_400);
  await clickAction(
    learningRegion.getByRole("button", { name: "进入学习", exact: true }),
    "进入减数分裂章节"
  );
  await waitForScreen(".lesson-screen", "减数分裂课程");
  await waitForText(appFrame.locator(".lesson-title-card"), "减数分裂和受精作用");
  await wait(1_800);

  const lessonTools = appFrame.locator(".lesson-learning-tools");
  await smoothIntoView(lessonTools, "学习工具栏", 1_750);
  await wait(700);
  await clickAction(
    lessonTools.getByRole("button", { name: "问 AI", exact: true }),
    "打开 AI 提问",
    { skipScroll: true }
  );

  const chatDialog = appFrame.getByRole("dialog", {
    name: "问 AI",
    exact: true
  });
  await chatDialog.waitFor({ state: "visible", timeout: 12_000 });
  await waitForAttribute(
    appFrame.locator('.sheet-overlay[data-sheet-type="chat"]'),
    "data-motion-state",
    "idle"
  );
  const questionInput = chatDialog.getByRole("textbox", {
    name: "继续提问",
    exact: true
  });
  await clickAction(questionInput, "输入 AI 问题", { skipScroll: true });
  await typeNaturally(
    questionInput,
    "同源染色体在减数分裂时如何分离？",
    48
  );
  await wait(650);
  await clickAction(
    chatDialog.getByRole("button", { name: "发送问题", exact: true }),
    "发送 AI 问题",
    { skipScroll: true }
  );

  const citation = chatDialog.locator(".citation-card");
  await citation.waitFor({ state: "visible", timeout: 15_000 });
  await waitForText(citation.locator(".citation-meta"), /教材第 16 页/);
  await smoothIntoView(citation, "AI 回答与教材引用", 1_100);
  await wait(2_300);
  await clickAction(
    citation.getByRole("button", { name: "查看原文", exact: true }),
    "回到教材第 16 页",
    { skipScroll: true }
  );

  const sourceDialog = appFrame.getByRole("dialog", {
    name: "查看原文",
    exact: true
  });
  await sourceDialog.waitFor({ state: "visible", timeout: 12_000 });
  await waitForText(sourceDialog, /教材第 16 页/);
  await waitForAttribute(
    appFrame.locator('.sheet-overlay[data-sheet-type="source"]'),
    "data-motion-state",
    "idle"
  );
  await wait(2_600);
  await captureStill(page16StillPath, "教材第 16 页");

  await clickAction(
    sourceDialog.getByRole("button", { name: "关闭", exact: true }),
    "关闭教材原文",
    { skipScroll: true }
  );
  await waitForCount(
    appFrame.getByRole("dialog", { name: "查看原文", exact: true }),
    0
  );
  await wait(700);

  await clickAction(
    appFrame.getByRole("button", { name: "做练习", exact: true }),
    "开始三步练习"
  );
  await waitForScreen(".assignment-screen", "练习第 1 题");
  const exerciseCard = appFrame.locator(".assignment-exercise-card");
  await waitForAttribute(exerciseCard, "data-assignment-type", "judgment");
  await wait(1_600);

  await clickAction(
    exerciseCard.getByRole("button", { name: "正确", exact: true }),
    "判断题选择正确"
  );
  await wait(850);
  await clickAction(
    exerciseCard.getByRole("button", {
      name: "提交判断题答案并进入下一题",
      exact: true
    }),
    "提交判断题"
  );
  await waitForAttribute(exerciseCard, "data-assignment-type", "choice");
  await wait(1_550);

  await clickAction(
    exerciseCard.getByRole("button", {
      name: /同源染色体分离/
    }),
    "选择题选择同源染色体分离"
  );
  await wait(850);
  await clickAction(
    exerciseCard.getByRole("button", {
      name: "提交选择题答案并进入下一题",
      exact: true
    }),
    "提交选择题"
  );
  await waitForAttribute(exerciseCard, "data-assignment-type", "short-answer");
  await wait(1_500);

  const shortAnswer = exerciseCard.locator("textarea");
  await clickAction(shortAnswer, "填写简答题答案");
  await typeNaturally(
    shortAnswer,
    "减数第二次分裂时，同源染色体分开，所以染色体数目减半。",
    32
  );
  await wait(900);
  await clickAction(
    exerciseCard.getByRole("button", { name: "提交作业", exact: true }),
    "提交简答题并完成三步练习"
  );
  await waitForScreen(".diagnosis-screen", "三题练习完成", 20_000);
  await waitForText(appFrame.locator(".diagnosis-screen"), "卡点");
  await wait(2_800);
  await captureStill(completedStillPath, "三题练习完成");
  routeCompleted = true;
} catch (error) {
  console.error("[recording-error] " + (error instanceof Error ? error.stack : String(error)));
  await captureStill(errorStillPath, "录制失败现场").catch(() => {});
  throw error;
} finally {
  await context.close();
}

let verification = null;
if (!rehearsal && routeCompleted && recordedVideo) {
  const rawVideoPath = await recordedVideo.path();
  await rename(rawVideoPath, outputVideoPath);
  await rmdir(videoStagingDirectory).catch(() => {});
  verification = await verifyVideoMetadata(browser, outputVideoPath);
  const fileStats = await stat(outputVideoPath);
  const manifest = {
    baseUrl,
    canvas: canvasMetadata,
    clickCueStillPath,
    completed: routeCompleted,
    container: "WebM",
    createdAt: new Date().toISOString(),
    deviceShell: deviceShellMetadata,
    interactionEvents,
    outputVideoPath,
    page16StillPath,
    completedStillPath,
    quality: "Retina 3x",
    recordingFeatures: [
      "iPhone 17 Pro 设备外壳",
      "动态岛与侧边按键",
      "紫色触控点",
      "点击扩散波纹",
      "按钮高亮与操作标签"
    ],
    route: routeLabels,
    sourcePdf: textbookPath,
    video: {
      ...verification,
      sizeBytes: fileStats.size
    }
  };
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n", "utf8");
}

await browser.close();

console.log(JSON.stringify({
  canvas: canvasMetadata,
  clickCueStillPath,
  completed: routeCompleted,
  completedStillPath,
  deviceShell: deviceShellMetadata,
  interactionCount: interactionEvents.length,
  manifestPath: rehearsal ? null : manifestPath,
  mode: rehearsal ? "rehearsal" : "recording",
  page16StillPath,
  route: routeLabels,
  verification,
  videoPath: rehearsal ? null : outputVideoPath
}, null, 2));
