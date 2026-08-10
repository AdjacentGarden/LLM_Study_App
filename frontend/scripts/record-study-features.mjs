/* global document, window */

import { chromium } from "playwright";
import {
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
// chrome=1 keeps the iPhone bezel + dynamic island visible; the injected promo
// layout CSS hides the workbench toolbar so the frame stays clean on camera.
const defaultBaseUrl = "http://127.0.0.1:5173/?device=iphone-17-pro&orientation=portrait&quality=retina-3x&chrome=1";
const recordingViewport = { height: 2880, width: 1440 };
const deviceLogicalWidth = 402;
const deviceLogicalHeight = 874;
const routeLabels = [
  "首页 · 课程工作区就绪",
  "继续学习，进入本章课程",
  "学习目标",
  "核心概念 → 进入闪卡复习",
  "翻卡看答案 ×3",
  "标记掌握 / 稍后再复习",
  "回到教材原文",
  "返回学习页",
  "做练习 · 三步作业诊断",
  "判断题 · 选择题 · 简答题",
  "作业诊断完成"
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
const stamp = createStamp();
const outputDirectory = rehearsal ? rehearsalDirectory : moviesDirectory;
const outputStem = rehearsal
  ? "bookcourse-ai-study-features-ios-shell-clicks-rehearsal-" + stamp
  : "bookcourse-ai-study-features-2k-ios-shell-clicks-" + stamp;
const outputVideoPath = path.join(outputDirectory, outputStem + ".webm");
const homeStillPath = path.join(outputDirectory, outputStem + "-home.png");
const lessonStillPath = path.join(outputDirectory, outputStem + "-lesson.png");
const flashcardsStillPath = path.join(outputDirectory, outputStem + "-flashcards.png");
const sourceStillPath = path.join(outputDirectory, outputStem + "-source.png");
const assignmentStillPath = path.join(outputDirectory, outputStem + "-assignment.png");
const completedStillPath = path.join(outputDirectory, outputStem + "-complete.png");
const errorStillPath = path.join(outputDirectory, outputStem + "-error.png");
const manifestPath = path.join(outputDirectory, outputStem + ".json");
const videoStagingDirectory = path.join(moviesDirectory, ".playwright-video-" + stamp);
const finalPacing = rehearsal ? 0.08 : 1;
// CSS transition real-time budget for the promo choreography. During rehearsal the
// transitions snap so the dry-run only validates routing, not the final visuals.
const effectMs = (realMs) => (rehearsal ? 1 : realMs);

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

// ---------------------------------------------------------------------------
// Device-shell promo layout (iPhone 17 Pro bezel + gradient backdrop)
// ---------------------------------------------------------------------------
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

  const canvasBox = await activePage.locator('[data-testid="device-preview-canvas"]').boundingBox();
  const frameBox = await activePage.locator('[data-testid="device-preview-frame"]').boundingBox();
  const deviceShellMetadata = await activePage.locator('[data-testid="device-preview-frame"]').evaluate((frame) => {
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
  return { canvasBox, deviceShellMetadata, frameBox };
}

// ---------------------------------------------------------------------------
// Promo interaction overlay: touch dot, ripple, caption, dim + target outline
// ---------------------------------------------------------------------------
async function installInteractionOverlay() {
  const overlayCss = [
    "#promo-touch-pointer, #promo-touch-ripple, #promo-action-caption, #promo-dim-overlay {",
    "  position: fixed;",
    "  pointer-events: none;",
    "}",
    "#promo-touch-pointer, #promo-touch-ripple, #promo-action-caption {",
    "  z-index: 2147483647;",
    "}",
    "#promo-dim-overlay {",
    "  z-index: 2147483646;",
    "  inset: 0;",
    "  opacity: 0;",
    "  transition: opacity 220ms ease;",
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
    "    left 440ms cubic-bezier(0.22, 1, 0.36, 1),",
    "    top 440ms cubic-bezier(0.22, 1, 0.36, 1),",
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
    ensureOverlayElement("promo-dim-overlay");
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
    caption.textContent = "点击「" + actionLabel + "」";
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

async function setTargetDim(localTarget) {
  const cx = localTarget.x;
  const cy = localTarget.y;
  const radius = Math.max(localTarget.width, localTarget.height) * 0.72 + 30;
  await appFrame.locator("body").evaluate((body, { cx, cy, radius }) => {
    const dim = body.querySelector("#promo-dim-overlay");
    if (!dim) return;
    dim.style.cssText = [
      "position: fixed",
      "inset: 0",
      "z-index: 2147483646",
      "pointer-events: none",
      `background: radial-gradient(circle at ${cx.toFixed(1)}px ${cy.toFixed(1)}px,`,
      "  rgba(12,9,36,0) 0%,",
      `  rgba(12,9,36,0) ${radius.toFixed(1)}px,`,
      `  rgba(12,9,36,0.30) ${(radius * 1.35).toFixed(1)}px,`,
      "  rgba(12,9,36,0.42) 100%)",
      "opacity: 0",
      "transition: opacity 220ms ease"
    ].join(";");
    void dim.getBoundingClientRect();
    dim.style.opacity = "1";
  }, { cx, cy, radius });
}

async function clearTargetDim() {
  await appFrame.locator("body").evaluate((body) => {
    const dim = body.querySelector("#promo-dim-overlay");
    if (dim) dim.style.opacity = "0";
  }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Local camera move: scale the whole device canvas around the tapped button.
// The overlay elements live in the frame's coordinate space, so they stay glued
// to the content while the canvas transform zooms everything uniformly.
// ---------------------------------------------------------------------------
async function zoomCanvas(canvasPoint, zoom) {
  const duration = effectMs(520);
  await activePage.evaluate(({ x, y, zoom, duration }) => {
    const canvas = document.querySelector('[data-testid="device-preview-canvas"]');
    if (!canvas) return;
    canvas.style.transition = `transform ${duration}ms cubic-bezier(0.22, 1, 0.36, 1)`;
    canvas.style.transformOrigin = `${x}px ${y}px`;
    canvas.style.transform = `scale(${zoom})`;
  }, { x: canvasPoint.x, y: canvasPoint.y, zoom, duration });
  await activePage.waitForTimeout(Math.max(30, Math.round(duration * 0.12)));
}

async function resetZoomCanvas() {
  const duration = effectMs(620);
  await activePage.evaluate(({ duration }) => {
    const canvas = document.querySelector('[data-testid="device-preview-canvas"]');
    if (!canvas) return;
    canvas.style.transition = `transform ${duration}ms cubic-bezier(0.22, 1, 0.36, 1)`;
    canvas.style.transformOrigin = "50% 50%";
    canvas.style.transform = "scale(1)";
  }, { duration });
  await activePage.waitForTimeout(Math.max(30, Math.round(duration * 0.12)));
}

function captureTargetPoint(locator) {
  return locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      height: rect.height,
      width: rect.width
    };
  });
}

function mapToCanvas(localX, localY) {
  return {
    x: Math.round((shellLayout.frameBox.x - shellLayout.canvasBox.x) + localX * shellLayout.scaleX),
    y: Math.round((shellLayout.frameBox.y - shellLayout.canvasBox.y) + localY * shellLayout.scaleY)
  };
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
  const zoom = options.noZoom ? null : (options.zoom ?? 1.3);
  await locator.waitFor({ state: "visible", timeout });
  if (!options.skipScroll) {
    await locator.scrollIntoViewIfNeeded({ timeout });
  }
  await locator.hover({ timeout });
  await wait(options.hoverDelay || 240);
  const localTarget = await captureTargetPoint(locator);
  const targetBounds = await showInteractionCue(locator, label);
  await setTargetDim(localTarget);
  await activePage.waitForTimeout(rehearsal ? 220 : 520);
  await pressInteractionCue();
  await activePage.waitForTimeout(rehearsal ? 80 : 150);
  await locator.click({ timeout });
  interactionEvents.push({
    action: label,
    atSeconds: Number(((Date.now() - recordingStartedAt) / 1000).toFixed(3)),
    target: targetBounds,
    zoom
  });
  if (zoom) {
    await zoomCanvas(mapToCanvas(localTarget.x, localTarget.y), zoom);
    await clearTargetDim();
    await activePage.waitForTimeout(rehearsal ? 100 : 640);
    await clearInteractionCue();
    await resetZoomCanvas();
    await activePage.waitForTimeout(rehearsal ? 100 : 320);
  } else {
    await clearInteractionCue();
    await clearTargetDim();
    await activePage.waitForTimeout(rehearsal ? 140 : 340);
  }
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
let deviceShellMetadata;
let shellLayout = null;
let routeCompleted;

activePage.on("pageerror", (error) => {
  console.warn("[pageerror] " + error.message);
});

try {
  await activePage.goto(baseUrl, {
    waitUntil: "domcontentloaded",
    timeout: 20_000
  });

  const shell = await applyDeviceShellRecordingLayout();
  shellLayout = {
    canvasBox: shell.canvasBox,
    frameBox: shell.frameBox,
    scaleX: shell.frameBox.width / deviceLogicalWidth,
    scaleY: shell.frameBox.height / deviceLogicalHeight
  };
  deviceShellMetadata = shell.deviceShellMetadata;
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
  await wait(1_200);

  // --- 首页课程工作区（自动加载 book_biology_2） ---
  const workspace = appFrame.locator('.home-book-workspace[data-loaded="true"]');
  await workspace.waitFor({ state: "visible", timeout: 25_000 });
  await wait(1_600);
  await captureStill(homeStillPath, "首页课程工作区");

  // --- 继续学习 → 学习页 ---
  await clickAction(workspace.locator(".home-primary-action"), "继续学习", { zoom: 1.32 });
  await waitForScreen(".lesson-screen", "学习页");
  await waitForText(appFrame.locator(".lesson-title-card"), "减数分裂和受精作用");
  await wait(1_600);

  // --- 学习目标 ---
  const objectives = appFrame.locator(".lesson-objectives-card");
  await smoothIntoView(objectives, "学习目标", 1_400);
  await wait(2_000);
  await captureStill(lessonStillPath, "学习页学习目标");

  // --- 核心概念 → 练本节闪卡 ---
  const conceptCard = appFrame.locator(".concept-flash-card");
  await smoothIntoView(conceptCard, "核心概念", 1_400);
  await wait(1_000);
  await clickAction(
    conceptCard.getByRole("button", { name: "练本节闪卡", exact: true }),
    "进入闪卡复习",
    { zoom: 1.3 }
  );
  await waitForScreen(".flashcard-screen", "闪卡复习");
  await appFrame.locator(".memory-card-trigger").waitFor({ state: "visible", timeout: 12_000 });
  await wait(1_600);

  async function waitForFlashcardIdle() {
    const motion = appFrame.locator(".memory-card-answer-motion");
    await waitForAttribute(motion, "data-motion-flash-state", "idle", 8_000).catch(() => {});
    await waitForAttribute(motion, "data-motion-flash-next-state", "idle", 8_000).catch(() => {});
  }

  const cardTrigger = appFrame.locator(".memory-card-trigger");
  const flashcardRatings = [
    { label: "记住了", toast: "已记录为掌握" },
    { label: "还不熟", toast: "已加入复习队列" },
    { label: "记住了", toast: "已记录为掌握" }
  ];
  for (let i = 0; i < flashcardRatings.length; i += 1) {
    await waitForFlashcardIdle();
    await clickAction(cardTrigger, `翻开闪卡答案 ${i + 1}`, { zoom: 1.32 });
    const rating = appFrame.locator(".flashcard-actions").getByRole("button", {
      name: new RegExp(flashcardRatings[i].label)
    });
    await rating.waitFor({ state: "visible", timeout: 10_000 });
    await waitForFlashcardIdle();
    await wait(600);
    if (i === 1) {
      await captureStill(flashcardsStillPath, "闪卡复习");
    }
    await clickAction(rating, flashcardRatings[i].label, { zoom: 1.3 });
    await wait(1_300);
  }

  // --- 闪卡 → 回到教材原文 ---
  try {
    await waitForFlashcardIdle();
    await clickAction(
      appFrame.locator(".memory-card-source-row").locator(".inline-link"),
      "查看教材原文",
      { zoom: 1.32 }
    );
    await waitForScreen(".source-reader-screen", "教材原文", 15_000);
    await wait(2_200);
    await captureStill(sourceStillPath, "教材原文页");
    await clickAction(
      appFrame.locator(".source-reader-actions").getByRole("button", { name: "返回上一页", exact: true }),
      "返回闪卡复习",
      { zoom: 1.28 }
    );
    await waitForScreen(".flashcard-screen", "闪卡复习");
    await wait(900);
  } catch (sourceError) {
    console.warn("[source-detour-fallback] " + (sourceError instanceof Error ? sourceError.message : String(sourceError)));
    await appFrame.locator(".header-bar").getByRole("button", { name: "返回", exact: true }).click().catch(() => {});
    await waitForScreen(".lesson-screen", "学习页（源回退）").catch(() => {});
  }

  // --- 返回学习页 ---
  await clickAction(
    appFrame.locator(".header-bar").getByRole("button", { name: "返回", exact: true }),
    "返回学习页",
    { zoom: 1.28 }
  );
  await waitForScreen(".lesson-screen", "学习页");
  await wait(1_100);

  // --- 学习工具栏 → 做练习 ---
  const lessonTools = appFrame.locator(".lesson-learning-tools");
  await smoothIntoView(lessonTools, "学习工具栏", 1_500);
  await wait(900);
  await clickAction(
    lessonTools.getByRole("button", { name: "做练习", exact: true }),
    "开始作业练习",
    { zoom: 1.3 }
  );
  await waitForScreen(".assignment-screen", "作业练习");
  const exerciseCard = appFrame.locator(".assignment-exercise-card");
  await waitForAttribute(exerciseCard, "data-assignment-type", "judgment");
  await wait(1_500);
  await captureStill(assignmentStillPath, "作业练习第 1 题");

  // 判断题
  await clickAction(
    exerciseCard.getByRole("button", { name: "正确", exact: true }),
    "判断题选择正确",
    { zoom: 1.3 }
  );
  await wait(800);
  await clickAction(
    exerciseCard.getByRole("button", {
      name: "提交判断题答案并进入下一题",
      exact: true
    }),
    "提交判断题",
    { zoom: 1.28 }
  );
  await waitForAttribute(exerciseCard, "data-assignment-type", "choice");
  await wait(1_400);

  // 选择题
  await clickAction(
    exerciseCard.getByRole("button", {
      name: /同源染色体分离/
    }),
    "选择题选择同源染色体分离",
    { zoom: 1.3 }
  );
  await wait(800);
  await clickAction(
    exerciseCard.getByRole("button", {
      name: "提交选择题答案并进入下一题",
      exact: true
    }),
    "提交选择题",
    { zoom: 1.28 }
  );
  await waitForAttribute(exerciseCard, "data-assignment-type", "short-answer");
  await wait(1_300);

  // 简答题
  const shortAnswer = exerciseCard.locator("textarea");
  await clickAction(shortAnswer, "填写简答答案", { zoom: 1.34, noZoom: false });
  await typeNaturally(
    shortAnswer,
    "减数第二次分裂时，同源染色体分开，所以染色体数目减半。",
    32
  );
  await wait(900);
  await clickAction(
    exerciseCard.getByRole("button", { name: "提交作业", exact: true }),
    "提交作业",
    { zoom: 1.3 }
  );
  await waitForScreen(".diagnosis-screen", "作业诊断", 20_000);
  await waitForText(appFrame.locator(".diagnosis-screen"), "卡点");
  await wait(2_600);
  await captureStill(completedStillPath, "作业诊断完成");
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
    completed: routeCompleted,
    container: "WebM",
    createdAt: new Date().toISOString(),
    device: {
      id: "iphone-17-pro",
      logicalViewport: `${deviceLogicalWidth} × ${deviceLogicalHeight}`,
      orientation: "portrait",
      quality: "Retina 3x",
      rawOutput: "1206 × 2622"
    },
    deviceShell: deviceShellMetadata,
    interactionEvents,
    outputVideoPath,
    stills: {
      assignmentStillPath,
      completedStillPath,
      flashcardsStillPath,
      homeStillPath,
      lessonStillPath,
      sourceStillPath
    },
    recordingFeatures: [
      "iPhone 17 Pro 设备外壳",
      "动态岛与侧边按键",
      "紫色触控点平滑移动",
      "目标锁定高亮与背景压暗",
      "点击扩散波纹",
      "操作说明胶囊",
      "局部运镜 125%–140%"
    ],
    route: routeLabels,
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
  completed: routeCompleted,
  completedStillPath,
  deviceShell: deviceShellMetadata,
  interactionCount: interactionEvents.length,
  manifestPath: rehearsal ? null : manifestPath,
  mode: rehearsal ? "rehearsal" : "recording",
  route: routeLabels,
  verification,
  videoPath: rehearsal ? null : outputVideoPath
}, null, 2));
