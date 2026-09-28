import { test, expect, type Page } from '@playwright/test';
async function enter(page: Page, baseURL?: string) {
  await page.goto(baseURL + '/?embedded=1');
  const entry = page.getByRole('button', { name: /暂不登录|返回学习空间/ });
  await entry.click();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
}
const answer = { status: 'supported', answer: '这是已核验的回答。', confidence: .9, evidence_pages: [1], claims: [{ text: '这是已核验的回答。', citations: [{ page_number: 1, quote: '教材依据' }] }] };
test('QA navigation, cancellation, late reply isolation and large type', async ({ page, baseURL }) => {
  test.skip(!baseURL, 'Select preview explicitly');
  let release: () => void = () => {}; let count = 0; let lateHandled = false;
  await page.route('**/api/books/*/qa', async route => {
    count++;
    if (count === 1) {
      await new Promise<void>(resolve => { release = resolve; });
      await route.fulfill({ json: { ...answer, claims: [{ text: '过期的回答', citations: [] }] } }).catch(() => {});
      lateHandled = true;
    } else await route.fulfill({ json: answer });
  });
  await enter(page, baseURL);
  await page.getByRole('button', { name: '答疑', exact: true }).click();
  const input = page.getByRole('textbox', { name: '向教材小助手提问' });
  await input.fill('解释这个知识点');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect.poll(() => count).toBe(1);
  await page.getByRole('button', { name: '书架', exact: true }).click();
  await expect(page.locator('.shelf-cover').first()).toBeVisible();
  await page.getByRole('button', { name: '答疑', exact: true }).click();
  await page.getByRole('button', { name: '停止等待' }).click();
  await expect(input).toHaveValue('解释这个知识点');
  await input.fill('换一个问题');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.locator('.qa-answer')).toContainText('这是已核验的回答');
  release();
  await expect.poll(() => lateHandled).toBe(true);
  await expect(page.locator('.qa-answer')).not.toContainText('过期的回答');
  expect(await page.locator('.answer-paragraph > p').first().evaluate(e => parseFloat(getComputedStyle(e).textIndent))).toBeGreaterThan(20);
  await page.getByRole('button', { name: '我的', exact: true }).click();
  await page.getByRole('slider', { name: '字体大小' }).fill('2');
  await page.setViewportSize({ width: 320, height: 740 });
  await page.getByRole('button', { name: '答疑', exact: true }).click();
  expect(await page.locator('.phone').evaluate(e => e.scrollWidth <= e.clientWidth + 1)).toBe(true);
  await expect(page.getByRole('button', { name: '发送问题' })).toBeVisible();
});
test('studio reload and existing image recovery never generate again', async ({ page, baseURL }) => {
  test.skip(!baseURL, 'Select preview explicitly');
  let failJobs = true, images = 0, posts = 0;
  await page.route('**/api/studio/capabilities', route => route.fulfill({ json: { image: true, video: true, notes_ai: true, voice_notes: true } }));
  await page.route('**/api/studio/notes?*', route => route.fulfill({ json: { items: [] } }));
  await page.route('**/api/studio/jobs?*', route => failJobs ? route.fulfill({ status: 503, json: { detail: '暂时无法加载' } }) : route.fulfill({ json: { items: [{ id: 'recovery-image', book_id: 'biology-required-2', kind: 'image', status: 'succeeded', result: { title: '教材示意' }, error: '', created: 1, excerpt: '教材内容', chapter_title: '', asset_url: '/api/studio/jobs/recovery-image/asset' }] } }));
  await page.route('**/api/studio/jobs/recovery-image/asset*', route => ++images === 1 ? route.fulfill({ status: 503, body: '' }) : route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240"><rect width="320" height="240" fill="#e8eff9"/></svg>' }));
  await page.route('**/api/studio/media', route => { posts++; return route.abort(); });
  await enter(page, baseURL);
  await page.getByRole('button', { name: /学习笔记/ }).first().click();
  await expect(page.getByRole('button', { name: '重新加载', exact: true })).toBeVisible();
  failJobs = false;
  await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await page.getByRole('button', { name: '图解', exact: true }).click();
  await page.locator('.studio-job').first().scrollIntoViewIfNeeded();
  await page.getByRole('button', { name: '重新加载图片' }).click();
  await expect.poll(() => page.locator('.studio-job img').first().evaluate((e: HTMLImageElement) => e.naturalWidth)).toBeGreaterThan(0);
  expect(posts).toBe(0);
});
