import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";

test("4090 memory return and durable private doubts close the loop", async ({
  browser,
  baseURL,
}, info) => {
  test.setTimeout(240000);
  test.skip(
    !process.env.RETURN_LIVE_E2E,
    "Opt in: creates an isolated test visitor and historical learning fixture on 4090.",
  );
  const c = await browser.newContext(),
    page = await c.newPage(),
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const who = await (await c.request.get(baseURL + "/api/auth/me")).json();
    const books = await (await c.request.get(baseURL + "/api/library")).json();
    const book =
      books.find((b: any) => b.diagnostics_ready && /生物/.test(b.title)) ??
      books.find((b: any) => b.diagnostics_ready);
    expect(book).toBeTruthy();
    const created = await c.request.post(baseURL + "/api/interviews/start", {
      data: { book_id: book.book_id, user_id: "return-e2e-" + Date.now() },
    });
    expect(created.status(), await created.text()).toBe(200);
    const session = await created.json();
    const bundle = JSON.parse(
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
    await page.goto(baseURL + "/?device=iphone-16&release=return-e2e");
    const f = page.frameLocator("iframe");
    const skip = f.getByRole("button", { name: "暂时体验，稍后注册" });
    if (await skip.isVisible()) await skip.click();
    const memory = f.getByRole("region", { name: "长期记忆回访" });
    await expect(memory).toContainText("1 张卡，今天再想一遍");
    await memory.getByRole("button", { name: "开始今天的回忆" }).click();
    await expect(
      memory.getByRole("button", { name: "闪卡题目，点击翻面查看答案" }),
    ).toBeVisible();
    await expect
      .poll(() =>
        memory
          .locator(".deck-card-wrap")
          .evaluate((e) => getComputedStyle(e).opacity),
      )
      .toBe("1");
    await page
      .locator(".device-preview-frame")
      .screenshot({ path: info.outputPath("memory-recall.png") });
    await memory
      .getByRole("button", { name: "闪卡题目，点击翻面查看答案" })
      .click();
    await memory.getByRole("button", { name: /基本记得/ }).click();
    await expect(memory.getByRole("status")).toContainText("已记录这次回忆");
    await expect(memory).toContainText("今天没有到期卡片");
    const after = await (
      await c.request.get(
        baseURL + "/api/learning/memory/" + session.session_id,
      )
    ).json();
    expect(after.due_count).toBe(0);
    expect(after.week_checks).toBe(2);
    expect(after.items[0].repetitions).toBe(3);
    await memory.getByRole("button", { name: "查看回忆记录与安排" }).click();
    await expect(memory).toContainText("已回忆 3 次");
    // Use the fixture's exact course to test original-reading placement without a new LLM generation.
    await page.route(
      `**/api/interviews/${session.session_id}/courses/${bundle.chapter_id}`,
      (r) => r.fulfill({ json: bundle }),
    );
    const chapter = f
      .locator(".atlas-row")
      .filter({ hasText: bundle.chapter_title })
      .first();
    await chapter.locator(".atlas-trigger").click();
    await chapter
      .getByRole("button", { name: "进入学习", exact: true })
      .click();
    await f.getByRole("tab", { name: "原文", exact: true }).click();
    const sourceParagraph = f.locator(".studio-selectable").first();
    const selectedText = await sourceParagraph.evaluate((element) => {
      const text = element.firstChild!;
      const range = document.createRange();
      range.setStart(text, 0);
      range.setEnd(text, Math.min(24, text.textContent!.length));
      const selection = window.getSelection()!;
      selection.removeAllRanges();
      selection.addRange(range);
      return selection.toString();
    });
    await expect(
      f.getByText(`已选 ${selectedText.length} 字`, { exact: true }),
    ).toBeVisible();
    await f
      .getByRole("button", { name: "▧ 看图理解", exact: true })
      .first()
      .click();
    await expect(f.getByLabel("选中的内容")).toHaveValue(selectedText);
    await f.getByRole("button", { name: "关闭学习工作台" }).click();
    await f
      .getByRole("button", { name: "这段还没懂，留待回访" })
      .first()
      .click();
    await expect(f.getByRole("region", { name: "阅读疑问回访" })).toContainText(
      "这里有你之前的疑问",
    );
    await expect
      .poll(() =>
        f.locator(".doubt-return").evaluate((e) => getComputedStyle(e).opacity),
      )
      .toBe("1");
    await page
      .locator(".device-preview-frame")
      .screenshot({ path: info.outputPath("chapter-doubt.png") });
    await f.getByRole("button", { name: "返回", exact: true }).click();
    // A direct QA question exercises real model routing and the save entry.
    await f.getByRole("button", { name: "答疑", exact: true }).click();
    const question = "请解释" + bundle.chapter_title + "的一个核心结论。";
    await f.getByRole("textbox", { name: "向教材小助手提问" }).fill(question);
    const answer = page.waitForResponse(
      (r) => r.url().endsWith("/qa") && r.request().method() === "POST",
    );
    await f.getByRole("button", { name: "发送问题" }).click();
    expect((await answer).status()).toBe(200);
    await f.getByRole("button", { name: "还没懂，记下来" }).click();
    await expect(f.getByText("已放入待解疑问")).toBeVisible();
    await f.getByRole("button", { name: "学习", exact: true }).click();
    const doubts = f.getByRole("region", { name: "阅读疑问回访" });
    await doubts.getByRole("button", { name: /查看疑问与理解记录/ }).click();
    await expect(doubts).toContainText(question);
    const row = doubts.locator(".doubt-row").filter({ hasText: question });
    await row.getByRole("button", { name: "7 天后再看" }).click();
    await expect(doubts.getByRole("status")).toContainText("已延后 7 天");
    let rows = (
      await (
        await c.request.get(
          baseURL + "/api/learning/doubts?book_id=" + book.book_id,
        )
      ).json()
    ).items;
    expect(rows).toHaveLength(2);
    const saved = rows.find((d: any) => d.question === question);
    expect(saved.due_at - saved.updated).toBe(7 * 86400);
    await row.getByText("我已经理解了", { exact: true }).click();
    await row
      .getByRole("textbox", { name: "我的理解（选填）" })
      .fill("独立测试：理解应以教材条件为准。");
    await row.getByRole("button", { name: "确认已理解" }).click();
    await expect(row).toContainText("已理解 · 自己确认");
    await page
      .locator(".device-preview-frame")
      .screenshot({ path: info.outputPath("doubt-resolved.png") });
    await page.reload();
    if (await skip.isVisible()) await skip.click();
    await doubts.getByRole("button", { name: /查看疑问与理解记录/ }).click();
    await expect(doubts).toContainText("独立测试：理解应以教材条件为准。");
    await row.getByRole("button", { name: "还想再想想" }).click();
    await row.getByRole("button", { name: "继续问", exact: true }).click();
    await expect(
      f.getByRole("textbox", { name: "向教材小助手提问" }),
    ).toHaveValue(question);
    const stranger = await browser.newContext();
    try {
      const other = await stranger.request.get(
        baseURL + "/api/learning/memory/" + session.session_id,
      );
      expect(other.status()).toBe(403);
      expect(
        (
          await (
            await stranger.request.get(
              baseURL + "/api/learning/doubts?book_id=" + book.book_id,
            )
          ).json()
        ).items,
      ).toEqual([]);
    } finally {
      await stranger.close();
    }
    expect(errors).toEqual([]);
  } finally {
    await c.close();
  }
});

test("doubt save failure is visible, retry persists once, and narrow layout remains usable", async ({
  browser,
  baseURL,
}) => {
  const c = await browser.newContext({
      viewport: { width: 360, height: 740 },
      isMobile: true,
      hasTouch: true,
      reducedMotion: "reduce",
    }),
    page = await c.newPage();
  try {
    await page.route("**/api/books/*/qa", (r) =>
      r.fulfill({
        json: {
          status: "supported",
          answer: "测试回答",
          claims: [],
          evidence_pages: [],
          confidence: 0.8,
          insufficiency_reason: null,
        },
      }),
    );
    let fail = true,
      saves = 0;
    await page.route("**/api/learning/doubts", (r) => {
      saves++;
      return fail
        ? r.fulfill({
            status: 503,
            json: { detail: "测试：暂时无法保存，请重试" },
          })
        : r.continue();
    });
    await page.goto(baseURL + "/?embedded=1");
    await page.getByRole("button", { name: "暂时体验，稍后注册" }).click();
    await page.getByRole("button", { name: "答疑", exact: true }).click();
    await page
      .getByRole("textbox", { name: "向教材小助手提问" })
      .fill("独立触屏测试：这个条件为什么成立？");
    await page.getByRole("button", { name: "发送问题" }).click();
    const save = page.getByRole("button", { name: "还没懂，记下来" });
    await save.click();
    await expect(page.getByRole("alert")).toContainText("暂时无法保存");
    await expect(page.getByText("已放入待解疑问", { exact: true })).toHaveCount(
      0,
    );
    fail = false;
    await save.evaluate((button: HTMLButtonElement) => {
      button.click();
      button.click();
    });
    await expect(
      page.getByText("已放入待解疑问", { exact: true }),
    ).toBeVisible();
    expect(saves).toBe(2);
    expect(
      await page
        .locator(".phone")
        .evaluate((e) => e.scrollWidth <= e.clientWidth + 1),
    ).toBe(true);
    const selection = await page
      .getByRole("combobox", { name: "答疑使用的书籍" })
      .inputValue();
    expect(
      (
        await (
          await c.request.get(
            baseURL + "/api/learning/doubts?book_id=" + selection,
          )
        ).json()
      ).items,
    ).toHaveLength(1);
    await page.getByRole("button", { name: "学习", exact: true }).click();
    const panel = page.getByRole("region", { name: "阅读疑问回访" });
    expect(await panel.evaluate((e) => getComputedStyle(e).animationName)).toBe(
      "none",
    );
    await panel.getByRole("button", { name: /查看疑问与理解记录/ }).click();
    await expect(
      panel.getByRole("button", { name: "继续问", exact: true }),
    ).toBeVisible();
  } finally {
    await c.close();
  }
});
