import { test, expect } from "@playwright/test";
import path from "node:path";
import { existsSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

test.use({ browserName: "chromium" });
const base = process.env.DIRECT_PORT_URL || "http://127.0.0.1:5187";

test("delta UI keeps real repositories and confirms settings logout", async ({ browser }, info) => {
  test.skip(process.env.DIRECT_PORT_E2E !== "1", "Requires the isolated direct-port backend.");
  const authFile = info.outputPath("isolated-auth.json");
  mkdirSync(path.dirname(authFile), { recursive: true });
  const context = await browser.newContext({ viewport: { width: 402, height: 874 }, reducedMotion: "reduce", storageState:existsSync(authFile)?authFile:undefined });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const request = context.request;
  let accountInfo = await (await request.get(`${base}/api/auth/me`)).json();
  if(!accountInfo.account) {await context.clearCookies();accountInfo=await (await request.get(`${base}/api/auth/me`)).json();}
  if(!accountInfo.account){
  const registered = accountInfo.demo_users?.find((user: { email: string; registered: boolean }) => user.email === "xiaolin@zhiwo.test")?.registered;
  const code = await request.post(`${base}/api/auth/code`, {
    headers: { Origin: base }, data: { email: "xiaolin@zhiwo.test", purpose: registered ? "login" : "register" },
  });
  expect(code.ok(), await code.text()).toBeTruthy();
  const challenge = await code.json();
  const verified = await request.post(`${base}/api/auth/verify`, {
    headers: { Origin: base }, data: {
      challenge_id: challenge.challenge_id, code: challenge.demo_code,
      registration: registered ? undefined : { nickname: "多资料验收", stage: "high", interests: ["science"], goal: "interest" },
    },
  });
  expect(verified.ok(), await verified.text()).toBeTruthy();
  await context.storageState({path:authFile});
  }
  await page.goto(base);
  await expect(page.locator(".app-shell, .onboarding-flow").first()).toBeVisible();
  if (await page.getByLabel("你的称呼", { exact: true }).isVisible()) {
    await page.getByLabel("你的称呼", { exact: true }).fill("多资料验收");
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await page.getByRole("button", { name: "系统学习", exact: true }).click();
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await page.getByRole("button", { name: "30分钟以内", exact: true }).click();
    await page.getByRole("button", { name: "开始专属学习之旅" }).click();
  }
  await expect(page.locator(".app-shell")).toBeVisible();
  await page.screenshot({ path: info.outputPath("original-mobile-home.png"), fullPage: true });
  await page.getByRole("button", { name: /导入课程|创建.*课程|添加课程/ }).first().click();
  await page.locator("input[type=file]").first().setInputFiles([
    fileURLToPath(new URL("./fixtures/materials/material-a.md", import.meta.url)),
    fileURLToPath(new URL("./fixtures/materials/material-b.csv", import.meta.url)),
  ]);
  if (await page.getByRole("button", { name: "保存资料并继续" }).isVisible()) {
    await page.getByRole("button", { name: "保存资料并继续" }).click();
  } else {
    await expect(page.getByRole("button", { name: "添加本地资料" })).toBeEnabled({ timeout: 30000 });
  }
  await expect(page.getByLabel("课程名称", { exact: true })).toBeVisible({ timeout: 30000 });
  await page.getByLabel("课程名称", { exact: true }).fill("真实双资料课程");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  for (let i = 0; i < 6; i++) {
    await page.locator(".learning-diagnosis-options button").first().click();
    await page.locator(".learning-flow-primary").click();
  }
  await expect(page.locator(".course-space-detail h1")).toHaveText("真实双资料课程");
  await expect(page.locator(".course-space-detail")).toContainText("2 份资料");
  await page.screenshot({ path: info.outputPath("original-mobile-course.png"), fullPage: true });
  await page.reload();
  await expect(page.locator(".app-shell")).toContainText("真实双资料课程");
  for (const width of [820, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.screenshot({ path: info.outputPath(`original-${width}-course.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  }
  await page.setViewportSize({width:402,height:874});
  await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"首页",exact:true}).click();
  await page.getByRole("button",{name:/^课程资料/}).first().click();
  await expect(page.locator(".course-space-detail")).toBeVisible();
  for(let i=0;i<2 && await page.getByRole("button",{name:"核对目录",exact:true}).count();i++){
    await page.getByRole("button",{name:"核对目录",exact:true}).first().click({timeout:12000});
    await expect(page.locator(".chapter-confirm-screen")).toBeVisible();
    await page.locator(".toc-directory .toc-entry-title").first().click();
    const editor=page.getByRole("dialog").last();
    await expect(editor.getByLabel("原书目录名称",{exact:true})).toBeVisible();
    await editor.getByLabel("原书目录名称",{exact:true}).fill(`人工核对资料${i+1}`);
    await editor.getByRole("button",{name:"保存本章修改",exact:true}).click();
    await expect(page.locator(".chapter-confirm-screen")).toContainText(`人工核对资料${i+1}`);
    await page.screenshot({path:info.outputPath(`original-directory-${i+1}.png`),fullPage:true});
    await page.getByRole("button",{name:"确认生成课程",exact:true}).click();
    await page.getByRole("button",{name:"进入学习",exact:true}).click({timeout:30000});
    await expect(page.locator(".study-screen")).toBeVisible();
    await page.screenshot({path:info.outputPath(`original-ready-study-${i+1}.png`),fullPage:true});
    await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"首页",exact:true}).click();
    await page.getByRole("button",{name:/^课程资料/}).first().click();
  }
  await page.getByRole("button",{name:"进入",exact:true}).first().click();
  await page.locator('.study-tool-grid [data-tool="notes"]').first().click();
  await expect(page.locator(".notes-screen, .notes-workspace").first()).toBeVisible();
  await page.getByRole("button",{name:"新建笔记",exact:true}).click();
  await page.getByRole("dialog").getByRole("button",{name:/文字笔记/}).click();
  await expect(page.locator(".source-reader-screen")).toBeVisible();
  await expect(page.locator(".source-page-text-document")).toContainText("叶绿体");
  await page.locator(".source-page-text-document").click({position:{x:70,y:90}});
  await page.locator(".source-text-note-popover").getByLabel("我的理解").fill("验收笔记：只有一个实验变量，比较光照与对照的氧气释放。");
  await page.locator(".source-text-note-popover").getByRole("button",{name:"完成",exact:true}).click();
  await page.screenshot({path:info.outputPath("original-source-annotation.png"),fullPage:true});
  await page.getByRole("button",{name:"返回",exact:true}).first().click();
  await expect(page.locator(".notes-screen, .notes-workspace").first()).toContainText("验收笔记");
  await page.reload();
  await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"学习",exact:true}).click();
  await page.locator('.study-tool-grid [data-tool="notes"]').first().click();
  await expect(page.locator(".notes-screen, .notes-workspace").first()).toContainText("验收笔记");
  await page.getByRole("button",{name:"章节学习报告",exact:true}).click();
  await expect(page.locator(".report-screen")).toContainText("暂无作答");
  await page.getByRole("button",{name:"返回",exact:true}).first().click();
  await page.getByRole("button",{name:"导出 PDF",exact:true}).click();
  const downloading=page.waitForEvent("download");
  await page.getByRole("button",{name:"确认导出",exact:true}).click();
  const download=await downloading;
  expect(await download.failure()).toBeNull();
  await download.saveAs(info.outputPath("learning-note.pdf"));
  await page.screenshot({path:info.outputPath("original-export-download.png"),fullPage:true});
  await page.getByRole("button",{name:"返回",exact:true}).first().click();
  await page.getByRole("button",{name:"返回",exact:true}).first().click();
  await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"首页",exact:true}).click();
  const beforeCredit=await (await request.get(`${base}/api/demo/credits`)).json();
  await page.getByRole("button",{name:"打开 AI 助手",exact:true}).click();
  await page.getByLabel("向 AI 助手提问").fill("叶绿体的光合作用实验应该如何控制变量？");
  await page.getByRole("dialog").getByRole("button",{name:"发送",exact:true}).click();
  await expect(page.locator(".ai-overlay")).toContainText(/未配置|不可用|没有可靠/);
  const afterCredit=await (await request.get(`${base}/api/demo/credits`)).json();
  expect(afterCredit.balance).toBe(beforeCredit.balance);
  await page.screenshot({path:info.outputPath("original-qa-capability-unavailable.png"),fullPage:true});
  await page.getByRole("button",{name:"收起 AI 助手"}).click();
  await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"我的",exact:true}).click();
  await page.getByRole("button",{name:"账号、好友与学习工具",exact:true}).click();
  await page.locator(".additional-tools").getByRole("button",{name:/^好友与消息/}).click();
  await expect(page.locator(".social-my-id")).toContainText("ZW-");
  await page.screenshot({path:info.outputPath("original-account-social-tools.png"),fullPage:true});
  const sessions=new Map([["xiaolin@zhiwo.test",await context.cookies()]]);
  async function switchAccount(email:string) {
    const saved=sessions.get(email);
    if(saved) {
      await context.clearCookies();await context.addCookies(saved);
      await page.evaluate(()=>{const c=new BroadcastChannel("zhiwo-account");c.postMessage("changed");c.close();});
      await expect(page.locator(".app-shell, .onboarding-flow").first()).toBeVisible();
      return;
    }
    await context.clearCookies();
    const me=await (await request.get(`${base}/api/auth/me`)).json();
    const registered=me.demo_users?.find((u:{email:string;registered:boolean})=>u.email===email)?.registered;
    const codeResponse=await request.post(`${base}/api/auth/code`,{headers:{Origin:base},data:{email,purpose:registered?"login":"register"}});
    expect(codeResponse.ok(),await codeResponse.text()).toBeTruthy();
    const challenge=await codeResponse.json();
    const verified=await request.post(`${base}/api/auth/verify`,{headers:{Origin:base},data:{challenge_id:challenge.challenge_id,code:challenge.demo_code,registration:registered?undefined:{nickname:"隔离账号",stage:"high",interests:["science"],goal:"interest"}}});
    expect(verified.ok(),await verified.text()).toBeTruthy();
    sessions.set(email,await context.cookies());
    await page.evaluate(()=>{const c=new BroadcastChannel("zhiwo-account");c.postMessage("changed");c.close();});
    await expect(page.locator(".app-shell, .onboarding-flow").first()).toBeVisible();
  }
  await switchAccount("momo@zhiwo.test");
  await expect(page.getByText("真实双资料课程",{exact:true})).toHaveCount(0);
  await page.screenshot({path:info.outputPath("account-b-isolated.png"),fullPage:true});
  await switchAccount("xiaolin@zhiwo.test");
  await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"首页",exact:true}).click();
  await expect(page.getByText("真实双资料课程",{exact:true}).first()).toBeVisible();
  await page.screenshot({path:info.outputPath("account-a-restored.png"),fullPage:true});
  await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"学习",exact:true}).click();
  await expect(page.locator(".sticker-icon").first()).toBeVisible();
  for(const width of [402,820,1440]) {
    await page.setViewportSize({width,height:874});
    await page.screenshot({path:info.outputPath(`delta-study-${width}.png`),fullPage:true});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  }
  await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"我的",exact:true}).click();
  await page.getByRole("button",{name:"设置",exact:true}).click();
  await expect(page.locator(".settings-screen")).toContainText("账号学习数据");
  await page.screenshot({path:info.outputPath("delta-settings.png"),fullPage:true});
  await page.getByRole("button",{name:"退出账号",exact:true}).click();
  await page.getByRole("dialog").getByRole("button",{name:"取消",exact:true}).click();
  await expect(page.locator(".settings-screen")).toBeVisible();
  const accountBefore=await (await request.get(`${base}/api/auth/me`)).json();
  await page.route("**/api/auth/logout",async route=>{await new Promise(resolve=>setTimeout(resolve,1200));await route.fulfill({status:503,contentType:"application/json",body:JSON.stringify({detail:"验收退出服务暂不可用"})});});
  await page.getByRole("button",{name:"退出账号",exact:true}).click();
  await page.getByRole("dialog").getByRole("button",{name:"确认退出",exact:true}).click();
  await expect(page.getByRole("dialog").getByRole("button",{name:"取消",exact:true})).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("验收退出服务暂不可用");
  expect((await (await request.get(`${base}/api/auth/me`)).json()).user_id).toBe(accountBefore.user_id);
  await expect(page.locator(".settings-screen")).toBeVisible();
  await page.screenshot({path:info.outputPath("delta-logout-failure.png"),fullPage:true});
  await page.unroute("**/api/auth/logout");
  await page.getByRole("dialog").getByRole("button",{name:"确认退出",exact:true}).click();
  await expect(page.getByLabel("注册登录邮箱")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("注册登录邮箱")).toBeVisible();
  await page.screenshot({path:info.outputPath("delta-logout-refresh.png"),fullPage:true});
  sessions.delete("xiaolin@zhiwo.test");
  await new Promise(resolve=>setTimeout(resolve,61000));
  await switchAccount("xiaolin@zhiwo.test");
  await expect(page.getByText("真实双资料课程",{exact:true}).first()).toBeVisible();
  await page.screenshot({path:info.outputPath("delta-relogin-preserved.png"),fullPage:true});
  expect(errors).toEqual([]);
  await context.storageState({path:authFile});
  await context.close();
});
