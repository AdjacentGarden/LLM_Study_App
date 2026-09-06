import { test, expect } from "@playwright/test";

test("two independent visitors: consent, chat, sharing, collection, blocking, phone layout", async ({
  browser,
  baseURL,
}, testInfo) => {
  test.skip(
    !process.env.SOCIAL_E2E_BASE_URL,
    "Explicitly choose a private preview; creates isolated test visitors.",
  );
  const a = await browser.newContext({
      viewport: { width: 900, height: 1020 },
    }),
    b = await browser.newContext({ viewport: { width: 900, height: 1020 } });
  const pa = await a.newPage(),
    pb = await b.newPage();
  const errors: string[] = [];
  pa.on("pageerror", (e) => errors.push(e.message));
  pb.on("pageerror", (e) => errors.push(e.message));
  const fa = pa.frameLocator('iframe[title="iPhone 16 App 模拟屏幕"]'),
    fb = pb.frameLocator('iframe[title="iPhone 16 App 模拟屏幕"]');
  let aid = "",
    bid = "";
  async function post(context: typeof a, path: string, body?: unknown) {
    const r = await context.request.post(baseURL + path, { data: body });
    expect(r.status(), await r.text()).toBe(200);
    return r.json();
  }
  try {
    await pa.goto(baseURL + "/?device=iphone-16");
    await pb.goto(baseURL + "/?device=iphone-16");
    await expect(
      fa.getByRole("button", { name: "社区", exact: true }),
    ).toBeEnabled();
    await expect(
      fb.getByRole("button", { name: "社区", exact: true }),
    ).toBeEnabled();
    aid = (await (await a.request.get(baseURL + "/api/social/me")).json())
      .user_id;
    bid = (await (await b.request.get(baseURL + "/api/social/me")).json())
      .user_id;
    await post(a, "/api/user/profile", {
      nickname: "界面验收甲",
      age: null,
      bio: "",
      revision: 0,
    });
    await post(b, "/api/user/profile", {
      nickname: "界面验收乙",
      age: null,
      bio: "",
      revision: 0,
    });
    for (const f of [fa, fb]) {
      await f.getByRole("button", { name: "社区", exact: true }).click();
      await f.getByRole("button", { name: /好友与消息/ }).click();
      await f
        .getByRole("button", { name: "开启好友查找", exact: true })
        .click();
      await expect(
        f.getByRole("button", { name: "关闭查找", exact: true }),
      ).toBeEnabled();
    }
    await fa.getByRole("button", { name: "添加", exact: true }).click();
    await fa
      .getByRole("textbox", { name: "搜索好友" })
      .fill("完全不存在的同学XYZ");
    await expect(fa.getByText(/没有找到匹配用户/)).toBeVisible();
    await fa.getByRole("textbox", { name: "搜索好友" }).fill(bid);
    await expect(fa.getByText("界面验收乙", { exact: true })).toBeVisible();
    await fa.getByRole("button", { name: "加好友", exact: true }).click();
    await fb
      .getByRole("group", { name: "好友页面切换" })
      .getByRole("button", { name: /^好友/ })
      .click();
    await fb.getByRole("button", { name: "接受", exact: true }).click();
    await fa
      .getByRole("group", { name: "好友页面切换" })
      .getByRole("button", { name: /^好友/ })
      .click();
    await fa.getByRole("button", { name: "聊天", exact: true }).click();
    await fb.getByRole("button", { name: "聊天", exact: true }).click();
    await expect(
      fa.getByRole("textbox", { name: "给好友的消息" }),
    ).toBeVisible();
    await fa
      .getByRole("textbox", { name: "给好友的消息" })
      .fill("你好！今天读到哪里了？📚");
    await fa.getByRole("button", { name: "发送", exact: true }).click();
    await expect(
      fb.getByText("你好！今天读到哪里了？📚", { exact: true }),
    ).toBeVisible();
    await fb
      .getByRole("textbox", { name: "给好友的消息" })
      .fill("我整理了一个阅读方法，发给你看看。");
    await fb.getByRole("button", { name: "发送", exact: true }).click();
    await expect(
      fa.getByText("我整理了一个阅读方法，发给你看看。", { exact: true }),
    ).toBeVisible();
    // Lose the response after the server commits: manual retry must be idempotent.
    let lost = false;
    await pa.route("**/api/social/chats/" + bid, async (route) => {
      if (route.request().method() === "POST" && !lost) {
        lost = true;
        await route.fetch();
        await route.abort("failed");
      } else await route.continue();
    });
    await fa
      .getByRole("textbox", { name: "给好友的消息" })
      .fill("断线重试也只能收到一次");
    await fa.getByRole("button", { name: "发送", exact: true }).click();
    await expect(fa.getByRole("alert")).toContainText("发送结果未确认");
    await fa.getByRole("button", { name: "重试", exact: true }).click();
    await expect(
      fb.getByText("断线重试也只能收到一次", { exact: true }),
    ).toHaveCount(1);
    await pa.unroute("**/api/social/chats/" + bid);
    const delivered = await (
      await a.request.get(baseURL + "/api/social/chats/" + bid)
    ).json();
    expect(
      delivered.messages.filter(
        (m: { text: string }) => m.text === "断线重试也只能收到一次",
      ),
    ).toHaveLength(1);
    const bounds = await fa.locator(".friend-chat").evaluate((el) => {
      const r = el.getBoundingClientRect();
      const input = el
        .querySelector(".friend-composer")!
        .getBoundingClientRect();
      const history = el
        .querySelector(".friend-chat-history")!
        .getBoundingClientRect();
      return {
        height: r.height,
        history: history.height,
        bottomGap: r.bottom - input.bottom,
        overflow: el.scrollWidth > el.clientWidth + 1,
      };
    });
    expect(bounds.history).toBeGreaterThan(280);
    expect(bounds.bottomGap).toBeLessThan(3);
    expect(bounds.overflow).toBe(false);
    await pa.screenshot({
      path: testInfo.outputPath("friend-chat-iphone16.png"),
    });
    const books = await (await a.request.get(baseURL + "/api/library")).json();
    const book = books[0];
    await post(b, `/api/library/books/${book.book_id}/remove`);
    await fa.getByRole("button", { name: "分享站内内容" }).click();
    await fa.getByRole("button", { name: "知识点清单", exact: true }).click();
    await fa.getByRole("button", { name: "书籍", exact: true }).click();
    await fa.getByLabel("好友分享教材").selectOption(book.book_id);
    await fa.getByRole("checkbox").check();
    await expect(
      fa.getByRole("button", { name: "添加到消息", exact: true }),
    ).toBeEnabled();
    await fa.getByRole("button", { name: "添加到消息", exact: true }).click();
    await fa.getByRole("button", { name: "发送", exact: true }).click();
    await fb.getByRole("button", { name: /书籍 · 点开查看/ }).click();
    await fb.getByRole("button", { name: "加入我的书架", exact: true }).click();
    await expect(
      fb.getByRole("status").filter({ hasText: "已收藏到书架。" }),
    ).toBeVisible();
    await fb.getByRole("button", { name: /书籍 · 点开查看/ }).click();
    await fb.getByRole("button", { name: "加入我的书架", exact: true }).click();
    await expect(
      fb
        .getByRole("status")
        .filter({ hasText: "已经收藏过啦，没有重复添加。" }),
    ).toBeVisible();
    expect(
      (await (await b.request.get(baseURL + "/api/library")).json()).length,
    ).toBe(5);
    const note = await post(b, "/api/library/notes", {
      book_id: book.book_id,
      title: "章节预习方法",
      body: "先读目录，再圈出一个自己想解答的问题。",
    });
    await fb.getByRole("button", { name: "分享站内内容" }).click();
    await fb.getByRole("button", { name: "笔记", exact: true }).click();
    await fb.getByLabel("好友分享教材").selectOption(book.book_id);
    await fb.getByLabel("好友分享内容").selectOption(`r:${note.id}`);
    await fb.getByRole("checkbox").check();
    await fb.getByRole("button", { name: "添加到消息", exact: true }).click();
    await fb.getByRole("button", { name: "发送", exact: true }).click();
    await fa.getByRole("button", { name: /笔记 · 点开查看/ }).click();
    await expect(
      fa.getByText("先读目录，再圈出一个自己想解答的问题。", { exact: true }),
    ).toBeVisible();
    await fa
      .getByRole("button", { name: "收藏到闪卡与笔记", exact: true })
      .click();
    await expect(
      fa.getByRole("status").filter({ hasText: "已收藏到书架。" }),
    ).toBeVisible();
    for(let index=0;index<51;index++)await post(a,`/api/social/chats/${bid}`,{client_id:`history-${Date.now()}-${index}`,text:`历史翻页验收 ${index+1}`});
    await pa.reload();
    await fa.getByRole("button",{name:"社区",exact:true}).click();
    await fa.getByRole("button",{name:/好友与消息/}).click();
    await fa.locator('.social-conversation').filter({hasText:'界面验收乙'}).click();
    await fa.getByRole('button',{name:'查看更早消息',exact:true}).click();
    await expect(fa.getByText('你好！今天读到哪里了？📚',{exact:true})).toBeAttached();
    expect((await (await a.request.get(baseURL+'/api/social/me')).json()).user_id).toBe(aid);
    await pa.setViewportSize({width:430,height:780});
    const screen=await pa.locator('iframe').boundingBox();
    expect(screen).not.toBeNull();
    expect(Math.abs(screen!.width/screen!.height-393/852)).toBeLessThan(.001);
    await pa.setViewportSize({width:900,height:1020});
    await fa.getByLabel("会话设置").click();
    await fa.getByRole("button", { name: "屏蔽此人", exact: true }).click();
    await fa.getByRole("button", { name: "确认屏蔽", exact: true }).click();
    await expect(
      fa.getByText("当前无法发送新消息，历史内容仍可查看。", { exact: true }),
    ).toBeVisible();
    await expect(
      fb.getByText("当前无法发送新消息，历史内容仍可查看。", { exact: true }),
    ).toBeVisible();
    await fa.getByRole("button", { name: "返回好友列表" }).click();
    await fa
      .getByRole("group", { name: "好友页面切换" })
      .getByRole("button", { name: /^好友/ })
      .click();
    await fa.getByText("已屏蔽用户（1）", { exact: true }).click();
    await fa.getByRole("button", { name: "解除屏蔽", exact: true }).click();
    await expect(fa.getByRole("button",{name:"解除屏蔽",exact:true})).toHaveCount(0);
    await pa.screenshot({ path: testInfo.outputPath("friends-iphone16.png") });
    expect(errors).toEqual([]);
  } finally {
    for (const c of [a, b])
      await c.request
        .post(baseURL + "/api/social/discoverability", {
          data: { enabled: false },
        })
        .catch(() => {});
    if (aid && bid) {
      await a.request.post(baseURL + `/api/social/friends/${bid}/unblock`);
      await a.request.post(baseURL + `/api/social/friends/${bid}/remove`);
    }
    await a.close();
    await b.close();
  }
});
