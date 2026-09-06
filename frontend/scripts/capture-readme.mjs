// Capture real preview pages in an isolated visitor context, never a user's cookies.
// Run from frontend: README_PREVIEW_URL=http://127.0.0.1:18100 node scripts/capture-readme.mjs
import { webkit, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const base = process.env.README_PREVIEW_URL;
if (!base) throw new Error('Set README_PREVIEW_URL to an authorized private preview.');
const output = fileURLToPath(new URL('../../docs/images/', import.meta.url));
await mkdir(output, {recursive:true});
const browser = await webkit.launch();
try {
  const context = await browser.newContext({viewport:{width:900,height:1020},reducedMotion:'reduce'});
  const page = await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(new URL('/?device=iphone-16',base).href);
  const frame = page.frameLocator('iframe[title="iPhone 16 App 模拟屏幕"]');
  const capture = name => page.locator('.device-preview-frame').screenshot({path:`${output}/${name}.png`});
  await expect(frame.getByRole('button',{name:'暂时体验，稍后注册'})).toBeVisible();
  await capture('registration');
  await frame.getByRole('button',{name:'暂时体验，稍后注册'}).click();
  await frame.getByRole('button',{name:'书架',exact:true}).click();
  await expect(frame.locator('.shelf-cover img').first()).toBeVisible();
  await expect.poll(()=>frame.locator('.shelf-cover img').evaluateAll(es=>es.every(e=>e.complete&&e.naturalWidth>0))).toBe(true);
  await frame.locator('.app-content').evaluate(e=>e.scrollTop=180);
  await capture('bookshelf');
  await frame.getByRole('button',{name:'社区',exact:true}).click();
  await expect(frame.locator('.community-post.book').first()).toBeVisible();
  await expect.poll(()=>frame.locator('.community-post.book img').evaluateAll(es=>es.every(e=>e.complete&&e.naturalWidth>0))).toBe(true);
  await frame.locator('.app-content').evaluate(e=>e.scrollTop=365);
  await capture('community');
  await frame.getByRole('button',{name:'答疑',exact:true}).click();
  await frame.getByRole('combobox',{name:'答疑使用的书籍'}).selectOption('996d1581e1f6');
  await frame.getByRole('textbox',{name:'向教材小助手提问'}).fill('C++ 中构造函数和析构函数分别有什么作用？');
  const response=page.waitForResponse(r=>r.url().endsWith('/books/996d1581e1f6/qa'),{timeout:160000});
  await frame.getByRole('button',{name:'发送问题'}).click();
  const result=await response;expect(result.status()).toBe(200);
  expect((await result.json()).status).toBe('supported');
  await expect(frame.locator('.answer-paragraph').first()).toBeVisible();
  await expect.poll(()=>frame.locator('.tutor-response').evaluate(e=>getComputedStyle(e).opacity)).toBe('1');
  await capture('book-qa');
  expect(errors).toEqual([]);
  console.log('Captured registration, bookshelf, community, and real book QA.');
  await context.close();
}finally{await browser.close();}
