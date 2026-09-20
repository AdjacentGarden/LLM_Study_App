import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";

test("chapter, section and flashcard all carry usable visual learning context", async ({
  browser,
  baseURL,
}) => {
  test.skip(
    !process.env.STUDIO_ENTRY_LIVE_E2E,
    "Requires the isolated 4090 course fixture.",
  );
  test.setTimeout(120000);
  const c = await browser.newContext();
  const page = await c.newPage();
  const who = await (await c.request.get(baseURL + "/api/auth/me")).json();
  const books = await (await c.request.get(baseURL + "/api/library")).json();
  const book = books.find((b: any) => b.diagnostics_ready && /生物/.test(b.title));
  expect(book).toBeTruthy();
  const started = await c.request.post(baseURL + "/api/interviews/start", {
    data: { book_id: book.book_id, user_id: "return-e2e-studio-" + Date.now() },
  });
  expect(started.status(), await started.text()).toBe(200);
  const session = await started.json();
  const course = JSON.parse(
    execFileSync(
      "ssh",
      [
        "-o",
        "BatchMode=yes",
        "zhenghang@10.249.186.201",
        `cd /data1/zhenghang/adaptive-book-ocr/app && PYTHONPATH=backend/src:/data1/zhenghang/adaptive-book-ocr/runtime-patches/lib/python3.12/site-packages:/data1/zhenghang/adaptive-book-ocr/runtime/lib/python3.12/site-packages /home/zhenghang/download/enter/bin/python deployment/server/seed_learning_return_test.py ${session.session_id}`,
      ],
      { encoding: "utf8", timeout: 30000 },
    ),
  );
  await c.addInitScript(
    ({ id, book, sid }) => {
      const prefix = "account:" + id + ":";
      localStorage.setItem(prefix + "zhiwo.active-book", book);
      localStorage.setItem(prefix + "zhiwo.active-session", sid);
      localStorage.setItem(prefix + "zhiwo.active-session:" + book, sid);
    },
    { id: who.user_id, book: book.book_id, sid: session.session_id },
  );
  await page.route(
    `**/api/interviews/${session.session_id}/courses/${course.chapter_id}`,
    (route) => route.fulfill({ json: course }),
  );
  await page.goto(baseURL + "/?embedded=1");
  await page.getByRole("button", { name: "暂时体验，稍后注册" }).click();
  const chapter = page.locator(".atlas-row").filter({ hasText: course.chapter_title }).first();
  await chapter.locator(".atlas-trigger").click();
  await chapter.getByRole("button", { name: "进入学习", exact: true }).click();

  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "图解", exact: true }).click();
  await expect(page.getByLabel("选中的内容")).toHaveValue(
    new RegExp(course.chapter_title),
  );
  await expect(page.getByRole("button", { name: "生成一张图解" })).toBeEnabled();
  await page.getByRole("button", { name: "关闭学习工作台" }).click();

  await page.getByRole("tab", { name: "原文", exact: true }).click();
  await page.getByRole("button", { name: "▧ 看图理解", exact: true }).first().click();
  await expect(page.getByLabel("选中的内容")).not.toHaveValue("");
  await page.getByRole("button", { name: "关闭学习工作台" }).click();

  await page.getByRole("tab", { name: "闪卡", exact: true }).click();
  await expect(page.getByLabel("把这张闪卡变成")).toBeVisible();
  await page.getByRole("button", { name: "▧ 生成图解", exact: true }).click();
  await expect(page.getByLabel("选中的内容")).toHaveValue(/问题：.+\n答案：.+/s);
  await expect(page.getByRole("button", { name: "生成一张图解" })).toBeEnabled();
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await page.getByRole("button", { name: "▷ 生成短片", exact: true }).click();
  await expect(page.getByRole("button", { name: "生成 6 秒短片" })).toBeEnabled();
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await c.close();
});

test("server notebook works when browser session storage is disabled", async ({
  browser,
  baseURL,
}) => {
  const c = await browser.newContext({ viewport: { width: 393, height: 852 } }),
    page = await c.newPage();
  await c.addInitScript(() =>
    Object.defineProperty(window, "sessionStorage", {
      get() {
        throw new DOMException("disabled", "SecurityError");
      },
    }),
  );
  await page.goto(baseURL + "/?embedded=1");
  await page.getByRole("button", { name: "暂时体验，稍后注册" }).click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "新建手写笔记" }).click();
  await page.getByRole("textbox", { name: "笔记标题" }).fill("无本地存储测试");
  await page.getByRole("button", { name: "保存笔记", exact: true }).click();
  await expect(page.locator(".ink-save")).toContainText("已保存");
  await expect(page.getByRole("alert")).not.toBeVisible();
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await c.close();
});

test("private digital ink saves, undo redo, reload and narrow layout", async ({
  browser,
  baseURL,
}, info) => {
  const context = await browser.newContext({
    viewport: { width: 393, height: 852 },
    hasTouch: true,
  });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await context.request.get(baseURL + "/api/auth/me");
  const books = await (
    await context.request.get(baseURL + "/api/library")
  ).json();
  await page.goto(baseURL + "/?embedded=1");
  const skip = page.getByRole("button", { name: "暂时体验，稍后注册" });
  await skip.click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "新建手写笔记" }).click();
  await page.getByRole("textbox", { name: "笔记标题" }).fill("笔画保存测试");
  const canvas = page.getByLabel("手写笔记画布");
  await canvas.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  for (const stroke of [
    [
      [60, 80],
      [60, 200],
      [100, 200],
      [130, 170],
      [130, 110],
      [100, 80],
      [60, 80],
    ],
    [
      [160, 200],
      [160, 80],
      [220, 200],
      [220, 80],
    ],
    [
      [250, 200],
      [290, 80],
      [330, 200],
    ],
    [
      [268, 150],
      [312, 150],
    ],
  ]) {
    const [start, ...rest] = stroke;
    await page.mouse.move(
      box.x + (start[0] / 1000) * box.width,
      box.y + (start[1] / 1400) * box.height,
    );
    await page.mouse.down();
    for (const p of rest)
      await page.mouse.move(
        box.x + (p[0] / 1000) * box.width,
        box.y + (p[1] / 1400) * box.height,
        { steps: 8 },
      );
    await page.mouse.up();
  }
  await page.getByRole("button", { name: "保存笔记", exact: true }).click();
  await expect(page.locator(".ink-save")).toContainText("已保存");
  const all = await (
    await context.request.get(
      baseURL + "/api/studio/notes?book_id=" + books[0].book_id,
    )
  ).json();
  // Active book can differ from catalog order; find the newly saved note in each owned book.
  let saved: any = all.items.find((n: any) => n.title === "笔画保存测试");
  for (const book of books) {
    if (saved) break;
    const list = await (
      await context.request.get(
        baseURL + "/api/studio/notes?book_id=" + book.book_id,
      )
    ).json();
    saved = list.items.find((n: any) => n.title === "笔画保存测试");
  }
  expect(saved).toBeTruthy();
  let full = await (
    await context.request.get(baseURL + "/api/studio/notes/" + saved.id)
  ).json();
  expect(full.strokes.length).toBe(4);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await page.getByRole("button", { name: "保存笔记", exact: true }).click();
  await expect
    .poll(async () => {
      full = await (
        await context.request.get(baseURL + "/api/studio/notes/" + saved.id)
      ).json();
      return full.strokes.length;
    })
    .toBe(3);
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await page.getByRole("button", { name: "保存笔记", exact: true }).click();
  await expect
    .poll(async () => {
      full = await (
        await context.request.get(baseURL + "/api/studio/notes/" + saved.id)
      ).json();
      return full.strokes.length;
    })
    .toBe(4);
  await page.getByRole("button", { name: "放大画布" }).click();
  await page.getByRole("button", { name: "移动", exact: true }).click();
  expect(
    await page
      .locator("dialog")
      .evaluate((e) => e.scrollWidth <= e.clientWidth + 1),
  ).toBeTruthy();
  await page.screenshot({ path: info.outputPath("ink-mobile.png") });
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await page.reload();
  await skip.click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: /笔画保存测试/ }).click();
  await expect(page.getByRole("textbox", { name: "笔记标题" })).toHaveValue(
    "笔画保存测试",
  );
  const outsider = await browser.newContext();
  expect(
    (
      await outsider.request.get(baseURL + "/api/studio/notes/" + saved.id)
    ).status(),
  ).toBe(404);
  await outsider.close();
  expect(errors).toEqual([]);
  await context.close();
});

test("real handwriting recognition and grounded improvement on 4090", async ({
  browser,
  baseURL,
}, info) => {
  test.skip(
    !process.env.STUDIO_LIVE_E2E,
    "Requires explicit live AI test opt-in.",
  );
  test.setTimeout(480000);
  const c = await browser.newContext({
      viewport: { width: 1024, height: 900 },
    }),
    page = await c.newPage();
  const who = await (await c.request.get(baseURL + "/api/auth/me")).json();
  const books = await (await c.request.get(baseURL + "/api/library")).json();
  const book = books.find((b: any) => /生物/.test(b.title));
  expect(book).toBeTruthy();
  await c.addInitScript(
    ({ id, book }) =>
      localStorage.setItem("account:" + id + ":zhiwo.active-book", book),
    { id: who.user_id, book: book.book_id },
  );
  await page.goto(baseURL + "/?embedded=1");
  const skip = page.getByRole("button", { name: "暂时体验，稍后注册" });
  await skip.click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "新建手写笔记" }).click();
  await page
    .getByRole("textbox", { name: "笔记标题" })
    .fill("DNA · 真实手写识别测试");
  const canvas = page.getByLabel("手写笔记画布");
  await canvas.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  for (const stroke of [
    [
      [100, 180],
      [100, 380],
      [170, 380],
      [230, 330],
      [230, 230],
      [170, 180],
      [100, 180],
    ],
    [
      [300, 380],
      [300, 180],
      [420, 380],
      [420, 180],
    ],
    [
      [490, 380],
      [560, 180],
      [630, 380],
    ],
    [
      [520, 300],
      [602, 300],
    ],
  ]) {
    const [p, ...rest] = stroke;
    await page.mouse.move(
      box.x + (p[0] / 1000) * box.width,
      box.y + (p[1] / 1400) * box.height,
    );
    await page.mouse.down();
    for (const q of rest)
      await page.mouse.move(
        box.x + (q[0] / 1000) * box.width,
        box.y + (q[1] / 1400) * box.height,
        { steps: 12 },
      );
    await page.mouse.up();
  }
  const submittedAt = Date.now();
  await page.getByRole("button", { name: "完成并整理", exact: true }).click();
  const confirmation = page.getByLabel("核对识别文字");
  const provisional = page.getByText(
    "内容已生成，正在对照教材做最后复核；你可以先阅读。",
  );
  await expect(confirmation.or(provisional).first()).toBeVisible({
    timeout: 180000,
  });
  if (await confirmation.isVisible()) {
    await confirmation.fill("DNA");
    await page
      .getByRole("button", { name: "确认并继续整理", exact: true })
      .click();
  }
  await expect(
    provisional,
  ).toBeVisible({ timeout: 360000 });
  const previewSeconds = (Date.now() - submittedAt) / 1000;
  await expect(
    page.getByRole("button", { name: "保留整理版（原笔迹不变）" }),
  ).toBeVisible({ timeout: 360000 });
  const completedSeconds = (Date.now() - submittedAt) / 1000;
  await expect(page.getByLabel("核对识别文字")).toHaveCount(0);
  await page.getByRole("button", { name: "保留整理版（原笔迹不变）" }).click();
  await expect(
    page.getByRole("button", { name: "撤回这个整理版" }),
  ).toBeVisible();
  await page.screenshot({ path: info.outputPath("ink-reviewed-tablet.png") });
  const list = await (
    await c.request.get(baseURL + "/api/studio/notes?book_id=" + book.book_id)
  ).json();
  const full = await (
    await c.request.get(baseURL + "/api/studio/notes/" + list.items[0].id)
  ).json();
  expect(full.edition.polished.length).toBeGreaterThan(10);
  expect(full.edition.transcript.replace(/\s/g, "")).toMatch(/DNA/i);
  expect(full.strokes.length).toBe(4);
  await page.reload();
  const restored = await (await c.request.get(baseURL + "/api/studio/notes/" + full.id)).json();
  expect(restored.edition.polished).toBe(full.edition.polished);
  expect(restored.strokes).toEqual(full.strokes);
  await info.attach("live-note-result", {
    body: JSON.stringify(
      { note_id: full.id, recognition: "DNA", edition: full.edition },
      null,
      2,
    ),
    contentType: "application/json",
  });
  await info.attach("live-note-latency", {
    body: JSON.stringify({ preview_seconds: previewSeconds, completed_seconds: completedSeconds }),
    contentType: "application/json",
  });
  await c.close();
});

test("media controls submit once, keep context, and show a failed task without automatic retry", async ({
  browser,
  baseURL,
}) => {
  const c = await browser.newContext({
      viewport: { width: 360, height: 740 },
      reducedMotion: "reduce",
    }),
    page = await c.newPage();
  let submissions = 0;
  let jobs: any[] = [];
  await page.route("**/api/studio/capabilities", (r) =>
    r.fulfill({
      json: {
        image: true,
        video: true,
        notes_ai: true,
        draft_scope: "isolated-test",
      },
    }),
  );
  await page.route("**/api/studio/jobs?*", (r) =>
    r.fulfill({ json: { items: jobs } }),
  );
  await page.route("**/api/studio/media", (r) => {
    submissions++;
    const data = r.request().postDataJSON();
    expect(data.excerpt).toBe("选中的教材测试文本");
    expect(data.kind).toBe("image");
    jobs = [
      {
        id: "fake_test_job",
        book_id: data.book_id,
        kind: "image",
        status: "failed",
        error: "测试：媒体服务暂不可用",
        created: Date.now() / 1000,
        result: {},
        excerpt: data.excerpt,
        chapter_title: "",
      },
    ];
    return r.fulfill({ json: jobs[0] });
  });
  await page.goto(baseURL + "/?embedded=1");
  await page.getByRole("button", { name: "暂时体验，稍后注册" }).click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "图解", exact: true }).click();
  await page.getByLabel("选中的内容").fill("选中的教材测试文本");
  await page.getByRole("button", { name: "生成一张图解" }).click();
  await expect(page.getByText("测试：媒体服务暂不可用")).toBeVisible();
  await expect(page.getByLabel("选中的内容")).toHaveValue("选中的教材测试文本");
  expect(
    await page
      .locator("dialog")
      .evaluate((e) => e.scrollWidth <= e.clientWidth + 1),
  ).toBeTruthy();
  expect(
    await page
      .locator("dialog")
      .evaluate((e) => getComputedStyle(e).animationName),
  ).toBe("none");
  await page.getByRole("button", { name: "短片", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "生成 6 秒短片" }),
  ).toBeVisible();
  expect(submissions).toBe(1);
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await c.close();
});

test("media progress remains visible and completed work returns after visiting another book", async ({
  browser,
  baseURL,
}) => {
  const c = await browser.newContext({ viewport: { width: 402, height: 874 } });
  const page = await c.newPage();
  const books = await (await c.request.get(baseURL + "/api/library")).json();
  test.skip(books.length < 2, "Requires two shelf books to verify switching away and back.");
  let jobs: any[] = [];
  await page.route("**/api/studio/capabilities", (route) =>
    route.fulfill({
      json: {
        image: true,
        video: true,
        notes_ai: true,
        voice_notes: true,
        draft_scope: "media-resume-test",
      },
    }),
  );
  await page.route("**/api/studio/jobs?*", (route) => {
    const bookId = new URL(route.request().url()).searchParams.get("book_id");
    return route.fulfill({ json: { items: jobs.filter((job) => job.book_id === bookId) } });
  });
  await page.route("**/api/studio/media", (route) => {
    const data = route.request().postDataJSON();
    const job = {
      id: "persisted_media_job",
      book_id: data.book_id,
      kind: "image",
      status: "planning",
      error: "",
      created: Date.now() / 1000,
      result: { title: "跨页面生成测试" },
      excerpt: data.excerpt,
      chapter_title: "",
      asset_url: null,
    };
    jobs = [job];
    return route.fulfill({ json: job });
  });
  await page.goto(baseURL + "/?embedded=1");
  await page.getByRole("button", { name: "暂时体验，稍后注册" }).click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "图解", exact: true }).click();
  await page.getByLabel("选中的内容").fill("选中的教材测试文本");
  await page.getByRole("button", { name: "生成一张图解" }).click();
  await expect(page.getByRole("progressbar", { name: "图解生成进度" }).first()).toBeVisible();
  await expect(page.getByText("正在设计画面").first()).toBeVisible();
  await page.getByRole("button", { name: "关闭学习工作台" }).click();

  await page.getByRole("button", { name: "书架", exact: true }).click();
  await page.locator(".shelf-caption").filter({ hasText: books[1].title }).click();
  await page.getByRole("button", { name: "书架", exact: true }).click();
  await expect(
    page
      .locator(".shelf-caption")
      .filter({ hasText: books[1].title })
      .getByText("正在阅读"),
  ).toBeVisible();
  jobs = [
    {
      ...jobs[0],
      status: "succeeded",
      result: {
        title: "跨页面生成测试",
        visual_mode: "illustration",
        explanation: "离开页面后仍由服务器继续生成。",
      },
      asset_url:
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='220'%3E%3Crect width='320' height='220' rx='24' fill='%237655c9'/%3E%3Ccircle cx='160' cy='110' r='58' fill='%2355d6b3'/%3E%3C/svg%3E",
    },
  ];
  await page.locator(".shelf-caption").filter({ hasText: books[0].title }).click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "图解", exact: true }).click();
  await expect(page.getByRole("img", { name: "跨页面生成测试" })).toBeVisible();
  await expect(page.getByText("离开页面后仍由服务器继续生成。")).toBeVisible();
  await c.close();
});

test("saving failure keeps ink and blocks closing until retry succeeds", async ({
  browser,
  baseURL,
}) => {
  const c = await browser.newContext({ viewport: { width: 393, height: 852 } }),
    page = await c.newPage();
  let reject = true;
  await page.route("**/api/studio/notes", (r) =>
    r.request().method() === "POST" && reject
      ? r.fulfill({ status: 503, json: { detail: "测试保存中断" } })
      : r.continue(),
  );
  await page.goto(baseURL + "/?embedded=1");
  await page.getByRole("button", { name: "暂时体验，稍后注册" }).click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "新建手写笔记" }).click();
  await page.getByRole("textbox", { name: "笔记标题" }).fill("断网草稿");
  await page.getByRole("button", { name: "保存笔记", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("测试保存中断");
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "笔记标题" })).toHaveValue(
    "断网草稿",
  );
  reject = false;
  await page.getByRole("button", { name: "保存笔记", exact: true }).click();
  await expect(page.locator(".ink-save")).toContainText("已保存");
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await c.close();
});

test("voice note can import, preview and save private audio on a phone", async ({
  browser,
  baseURL,
}) => {
  const context = await browser.newContext({
    viewport: { width: 402, height: 874 },
    hasTouch: true,
  });
  const page = await context.newPage();
  await page.goto(baseURL + "/?embedded=1");
  await page.getByRole("button", { name: "暂时体验，稍后注册" }).click();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: "新建语音笔记" }).click();
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible();
  const title = `语音保存测试-${Date.now()}`;
  await page.getByRole("textbox", { name: "笔记标题" }).fill(title);

  const sampleRate = 8000;
  const pcm = Buffer.alloc(sampleRate * 2);
  const wav = Buffer.alloc(44 + pcm.length);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(36 + pcm.length, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24);
  wav.writeUInt32LE(sampleRate * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(pcm.length, 40);
  pcm.copy(wav, 44);
  await page.getByLabel("或选择已有录音").setInputFiles({
    name: "学习想法.wav",
    mimeType: "audio/wav",
    buffer: wav,
  });
  await expect(page.getByLabel("语音笔记录音")).toBeVisible();
  await page.getByRole("button", { name: "保存录音" }).click();
  await expect(page.locator(".ink-save")).toContainText("录音已保存");
  await page.getByRole("button", { name: "关闭学习工作台" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: /把理解，写下来/ }).click();
  await page.getByRole("button", { name: new RegExp(title) }).click();
  await expect(page.getByLabel("语音笔记录音")).toHaveAttribute(
    "src",
    /\/api\/studio\/notes\/.+\/audio\?v=/,
  );
  await context.close();
});
