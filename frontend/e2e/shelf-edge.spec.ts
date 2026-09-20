import {test,expect} from '@playwright/test';

test('touch preview works without hover; empty library cannot submit QA',async({browser,baseURL})=>{
  test.skip(!baseURL,'Choose the private preview explicitly.');
  const c=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,reducedMotion:'reduce'});
  const p=await c.newPage();const f=p.frameLocator('iframe');
  try{
    await p.goto(baseURL+'/?device=iphone-16');await f.getByRole('button',{name:'暂时体验，稍后注册'}).tap();
    await f.getByRole('button',{name:/上传一本书/}).tap();
    await expect(f.getByRole('dialog',{name:'上传一本新书'})).toBeVisible();
    const submit=f.getByRole('button',{name:/上传并开始解析/});
    await expect(submit).toBeDisabled();
    await f.getByLabel('选择要上传的 PDF').setInputFiles({name:'入口测试.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7\nfixture')});
    await expect(submit).toBeEnabled();
    await f.locator('.sheet-close').tap();
    await f.getByRole('button',{name:'书架',exact:true}).tap();
    await expect(f.getByRole('button',{name:/上传一本书/})).toHaveCount(0);
    await f.locator('.shelf-cover').first().tap();
    await expect(f.getByRole('dialog')).toBeVisible();
    await f.locator('.sheet-close').tap();
    await expect(f.getByRole('dialog')).toHaveCount(0);
    await f.getByRole('button',{name:'社区',exact:true}).tap();
    await f.locator('.community-post.book').first().tap();
    await expect(f.getByRole('dialog')).toBeVisible();
    await f.locator('.sheet-close').tap();
    await p.route('**/api/library',r=>r.fulfill({json:[]}));
    await p.reload();await f.getByRole('button',{name:'暂时体验，稍后注册'}).tap();await f.getByRole('button',{name:'答疑',exact:true}).tap();
    await expect(f.getByRole('combobox',{name:'答疑使用的书籍'})).toBeDisabled();
    await f.getByRole('textbox',{name:'向教材小助手提问'}).fill('空书架不能发送');
    await expect(f.getByRole('button',{name:'发送问题'})).toBeDisabled();
    expect(await f.locator('.phone').evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBe(true);
  }finally{await c.close();}
});
