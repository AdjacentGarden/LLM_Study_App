import { test, expect } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";

// Isolated browser/component tests: no backend or paid model requests.
let server: ViteDevServer;
let url: string;
test.beforeAll(async () => {
  server = await createServer({ server: { host: "127.0.0.1", port: 0, watch: { ignored: ["**/test-results*/**"] } } });
  await server.listen();
  url = server.resolvedUrls!.local[0];
});
test.afterAll(async () => { await server?.close(); });

for (const scenario of ["success", "unclear", "save-error"] as const) {
  test(`one-button handwriting: ${scenario}`, async ({ browser }, info) => {
    const context = await browser.newContext({ viewport: { width: 393, height: 852 } });
    const page = await context.newPage();
    const actions: string[] = [];
    let saved: any = null;
    let jobs: any[] = [];
    await page.route("**/__studio-test", async r => r.fulfill({ contentType: "text/html", body: await server.transformIndexHtml("/__studio-test", '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/e2e/fixtures/studio.tsx"></script></body></html>') }));
    await page.route(u => u.pathname.startsWith("/api/"), async r => {
      const path = new URL(r.request().url()).pathname;
      if (path.endsWith("capabilities")) return r.fulfill({ json: { notes_ai: true, image: false, video: false, draft_scope: "complete-test" } });
      if (path.endsWith("/jobs")) return r.fulfill({ json: { items: jobs } });
      if (path.endsWith("/notes") && r.request().method() === "GET") return r.fulfill({ json: { items: saved ? [saved] : [] } });
      if (path.endsWith("/notes")) {
        if (scenario === "save-error") return r.fulfill({ status: 503, json: { detail: "测试保存失败" } });
        saved = { ...r.request().postDataJSON(), revision: 1 };
        return r.fulfill({ json: saved });
      }
      if (path.endsWith("/analyze")) {
        const input = r.request().postDataJSON();
        actions.push(input.action);
        expect(saved.strokes.length).toBeGreaterThan(0);
        expect(input.revision).toBe(saved.revision);
        jobs = [{ id: "mock_complete_111", note_id: saved.id, book_id: "test-book", revision: 1, kind: input.action, status: "planning", result: { phase: "verifying" }, error: "", created: Date.now() / 1000 }];
        return r.fulfill({ json: jobs[0] });
      }
      throw new Error(`Unexpected API ${path}`);
    });
    await page.goto(url + "__studio-test");
    await page.getByRole("button", { name: /把理解，写下来/ }).click();
    await page.getByRole("button", { name: "新建手写笔记" }).click();
    const complete = page.getByRole("button", { name: "完成并整理", exact: true });
    await expect(complete).toBeDisabled();
    const canvas = page.getByLabel("手写笔记画布");
    await canvas.scrollIntoViewIfNeeded();
    const box = (await canvas.boundingBox())!;
    await page.mouse.move(box.x + 25, box.y + 25);
    await page.mouse.down();
    await page.mouse.move(box.x + 55, box.y + 50, { steps: 5 });
    await page.mouse.up();
    await complete.click();
    if (scenario === "save-error") {
      await expect(page.getByRole("alert")).toContainText("测试保存失败");
      expect(actions).toEqual([]);
    } else {
      await expect(page.getByRole("button", { name: "正在核对识别文字…", exact: true })).toBeDisabled();
      expect(actions).toEqual(["complete"]);
      if (scenario === "success") {
        jobs[0] = {
          ...jobs[0],
          result: {
            phase: "checking",
            provisional: true,
            transcript: "函数",
            summary: "仅为关键词，不推断掌握情况。",
            polished: "函数需要终止条件。",
            suggestions: [],
          },
        };
        await expect(page.getByText("内容已生成，正在对照教材做最后复核；你可以先阅读。")).toBeVisible();
        await expect(page.getByRole("button", { name: "复核完成后可以保留" })).toBeDisabled();
      }
      jobs[0] = { ...jobs[0], status: scenario === "unclear" ? "needs_confirmation" : "succeeded", result: scenario === "unclear" ? { transcript: "[待确认]", uncertain: ["第二行"] } : { transcript: "函数", summary: "仅为关键词，不推断掌握情况。", polished: "函数需要终止条件。", suggestions: [] } };
      if (scenario === "unclear") {
        await expect(page.getByLabel("核对识别文字")).toHaveValue("[待确认]");
        const resume = page.getByRole("button", { name: "确认并继续整理" });
        await expect(resume).toBeDisabled();
        await page.getByLabel("核对识别文字").fill("函数");
        await resume.click();
        expect(actions).toEqual(["complete", "improve"]);
      } else {
        await expect(page.getByText("整理结果已保存，原笔迹保持不变。")).toBeVisible();
        await expect(page.getByLabel("核对识别文字")).toHaveCount(0);
        await expect(page.getByText("函数需要终止条件。")).toBeVisible();
        await page.screenshot({ path: info.outputPath("one-click-result.png") });
      }
    }
    await context.close();
  });
}
