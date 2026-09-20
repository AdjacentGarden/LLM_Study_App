import { test, expect, type Page } from "@playwright/test";

const visitor = {
  account: null,
  user_id: "ZW-DESIGNTEST",
  legacy_profile: false,
  email_available: true,
  demo_available: false,
  demo_users: [],
  learning_sessions: {},
};
async function setup(page: Page, baseURL: string | undefined) {
  test.skip(!baseURL, "Choose the private preview explicitly.");
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ json: visitor }),
  );
  await page.goto(baseURL + "/?device=iphone-16");
  return page.frameLocator("iframe");
}

test("two-step registration preserves fields and verifies only on final submit", async ({
  page,
  baseURL,
}, info) => {
  let count = 0,
    fail = true;
  const payloads: any[] = [];
  await page.route("**/api/auth/code", (r) => {
    count++;
    return r.fulfill({
      json: {
        challenge_id: "design-challenge-123456",
        expires_in: 600,
        retry_after: 60,
        delivery: "email",
        message: "如果邮箱可用于此操作，验证码将发送到该邮箱",
      },
    });
  });
  await page.route("**/api/auth/verify", (r) => {
    payloads.push(r.request().postDataJSON());
    return r.fulfill({
      status: fail ? 400 : 200,
      json: fail
        ? { detail: "验证码不正确，请检查后重试" }
        : {
            ...visitor,
            account: {
              email: "reader@example.test",
              stage: "working",
              goal: "work",
              interests: ["engineering"],
              is_demo: false,
            },
          },
    });
  });
  const f = await setup(page, baseURL);
  await expect(f.getByRole("textbox", { name: "注册昵称" })).toHaveCount(0);
  const next = f.getByRole("button", { name: "下一步 · 学习偏好" });
  await expect(next).toBeDisabled();
  await page
    .locator(".device-preview-frame")
    .screenshot({ path: info.outputPath("register.png") });
  await f.getByRole("textbox", { name: "注册登录邮箱" }).fill("bad");
  await f.getByRole("textbox", { name: "邮箱验证码", exact: true }).click();
  await expect(f.getByText(/请填写完整邮箱/)).toBeVisible();
  await expect(f.getByRole("button", { name: "获取验证码" })).toBeDisabled();
  await f
    .getByRole("textbox", { name: "注册登录邮箱" })
    .fill("reader@example.test");
  await f.getByRole("button", { name: "获取验证码" }).click();
  await expect(f.getByRole("status")).toContainText("请查收邮箱验证码");
  await expect(
    f.getByRole("textbox", { name: "邮箱验证码", exact: true }),
  ).toBeFocused();
  await f
    .getByRole("textbox", { name: "邮箱验证码", exact: true })
    .fill("12a345");
  await expect(next).toBeDisabled();
  await f
    .getByRole("textbox", { name: "邮箱验证码", exact: true })
    .fill("123456");
  await next.click();
  expect(payloads).toHaveLength(0);
  await f.getByRole("textbox", { name: "注册昵称" }).fill("测试读者");
  await f.getByLabel("学习阶段", { exact: true }).selectOption("working");
  await f.getByLabel("学习目标", { exact: true }).selectOption("work");
  await f.getByRole("button", { name: "工程技术", exact: true }).click();
  await expect(
    f.getByRole("button", { name: "创建我的学习空间" }),
  ).toBeDisabled();
  await f.getByRole("checkbox").check();
  await page
    .locator(".device-preview-frame")
    .screenshot({ path: info.outputPath("preferences.png") });
  await f.getByRole("button", { name: "返回邮箱信息" }).click();
  await expect(
    f.getByRole("textbox", { name: "邮箱验证码", exact: true }),
  ).toHaveValue("123456");
  await next.click();
  await expect(f.getByRole("textbox", { name: "注册昵称" })).toHaveValue(
    "测试读者",
  );
  await f.getByRole("button", { name: "创建我的学习空间" }).click();
  await expect(f.getByRole("alert")).toContainText("验证码不正确");
  await expect(f.getByRole("textbox", { name: "注册登录邮箱" })).toBeVisible();
  expect(payloads[0].registration).toEqual({
    nickname: "测试读者",
    stage: "working",
    goal: "work",
    interests: ["engineering"],
  });
  fail = false;
  await f
    .getByRole("textbox", { name: "邮箱验证码", exact: true })
    .fill("654321");
  await next.click();
  await f.getByRole("button", { name: "创建我的学习空间" }).click();
  await expect(
    f.getByRole("button", { name: "书架", exact: true }),
  ).toBeEnabled();
  expect(payloads).toHaveLength(2);
  expect(count).toBe(1);
});

test("resend cooldown, expiry, changed email and purpose invalidate UI state", async ({
  page,
  baseURL,
}) => {
  await page.clock.install();
  let count = 0;
  await page.route("**/api/auth/code", (r) => {
    count++;
    return r.fulfill({
      json: {
        challenge_id: "expires-challenge-123456",
        expires_in: 5,
        retry_after: 10,
        delivery: "email",
        message: "如果邮箱可用于此操作，验证码将发送到该邮箱",
      },
    });
  });
  const f = await setup(page, baseURL);
  await f
    .getByRole("textbox", { name: "注册登录邮箱" })
    .fill("reader@example.test");
  await f.getByRole("button", { name: "获取验证码" }).click();
  await expect(f.getByRole("button", { name: /秒后重发/ })).toBeDisabled();
  await f
    .getByRole("textbox", { name: "邮箱验证码", exact: true })
    .fill("123456");
  await f.getByRole("button", { name: "下一步 · 学习偏好" }).click();
  await page.clock.fastForward(6000);
  await expect(f.getByRole("alert")).toContainText("验证码已过期");
  await f.getByRole("button", { name: "返回邮箱信息" }).click();
  await expect(
    f.getByRole("button", { name: "下一步 · 学习偏好" }),
  ).toBeDisabled();
  await page.clock.fastForward(5000);
  await f.getByRole("button", { name: "获取验证码" }).click();
  expect(count).toBe(2);
  await f
    .getByRole("textbox", { name: "邮箱验证码", exact: true })
    .fill("654321");
  await f.getByRole("button", { name: "注册账号", exact: true }).click();
  await expect(
    f.getByRole("textbox", { name: "邮箱验证码", exact: true }),
  ).toHaveValue("654321");
  await f
    .getByRole("textbox", { name: "注册登录邮箱" })
    .fill("changed@example.test");
  await expect(
    f.getByRole("textbox", { name: "邮箱验证码", exact: true }),
  ).toHaveValue("");
  await f.getByRole("button", { name: "邮箱登录", exact: true }).click();
  await expect(
    f.getByRole("button", { name: "登录并继续学习" }),
  ).toBeDisabled();
  await expect(f.getByRole("textbox", { name: "注册昵称" })).toHaveCount(0);
});

test("login errors, fixed footer, narrow touch viewport and reduced motion", async ({
  browser,
  baseURL,
}, info) => {
  const c = await browser.newContext({
    viewport: { width: 360, height: 650 },
    isMobile: true,
    hasTouch: true,
    reducedMotion: "reduce",
  });
  const page = await c.newPage();
  try {
    let sends = 0;
    await page.route("**/api/auth/code", (r) => {
      sends++;
      return r.fulfill({
        status: 503,
        json: { detail: "验证邮件发送失败，请稍后再试" },
      });
    });
    const f = await setup(page, baseURL);
    await f.getByRole("button", { name: "邮箱登录", exact: true }).tap();
    await f
      .getByRole("textbox", { name: "注册登录邮箱" })
      .fill("reader@example.test");
    await f.getByRole("button", { name: "获取验证码" }).tap();
    await expect(f.getByRole("alert")).toContainText("验证邮件发送失败");
    await expect(
      f.getByRole("button", { name: "登录并继续学习" }),
    ).toBeDisabled();
    await expect(f.getByRole("status")).toHaveCount(0);
    expect(sends).toBe(1);
    const bounds = await f.locator(".auth-phone").evaluate((e) => {
      const s = e.querySelector(".auth-scroll")!.getBoundingClientRect(),
        b = e.querySelector(".auth-bottom")!.getBoundingClientRect();
      return {
        overlap: s.bottom > b.top + 1,
        overflow: e.scrollWidth > e.clientWidth + 1,
        gap: e.getBoundingClientRect().bottom - b.bottom,
      };
    });
    expect(bounds).toEqual({ overlap: false, overflow: false, gap: 0 });
    expect(
      await f
        .locator(".auth-fields")
        .evaluate((e) => getComputedStyle(e).animationName),
    ).toBe("none");
    await page
      .locator(".device-preview-frame")
      .screenshot({ path: info.outputPath("login-error.png") });
  } finally {
    await c.close();
  }
});
