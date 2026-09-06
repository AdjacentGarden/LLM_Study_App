import {test,expect} from '@playwright/test';

test('shelf direction, community zoom, two categories, keyboard and reduced motion',async({browser,baseURL},info)=>{
  test.skip(!baseURL,'Choose the private preview explicitly.');
  const context=await browser.newContext({viewport:{width:900,height:1020}});
  const page=await context.newPage(); const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  const frame=page.frameLocator('iframe[title="iPhone 16 App 模拟屏幕"]');
  try{
    await page.goto(baseURL+'/?device=iphone-16');
    await frame.getByRole('button',{name:'暂时体验，稍后注册'}).click();
    await frame.getByRole('button',{name:'书架',exact:true}).click();
    const covers=frame.locator('.shelf-cover');
    await expect(covers).toHaveCount(5);
    await expect.poll(()=>covers.locator('img').evaluateAll(es=>es.every(e=>(e as HTMLImageElement).complete&&(e as HTMLImageElement).naturalWidth>0))).toBe(true);
    await page.mouse.move(5,5);
    const angles=await covers.locator('img').evaluateAll(es=>es.map(e=>new DOMMatrix(getComputedStyle(e).transform).b));
    expect(angles.every(n=>n<0)).toBe(true);
    for(const cover of [covers.nth(0),covers.nth(1)]){
      await cover.hover();
      await expect.poll(()=>cover.locator('img').evaluate(e=>Math.abs(new DOMMatrix(getComputedStyle(e).transform).b))).toBeLessThan(.001);
      expect(await cover.evaluate(e=>getComputedStyle(e).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
    }
    await page.mouse.move(5,5);
    await expect.poll(()=>covers.nth(1).locator('img').evaluate(e=>new DOMMatrix(getComputedStyle(e).transform).b)).toBeLessThan(-.04);
    await page.screenshot({path:info.outputPath('shelf.png')});
    await covers.first().focus(); await page.keyboard.press('Enter');
    await expect(frame.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape'); await expect(frame.getByRole('dialog')).toHaveCount(0);
    await frame.getByRole('button',{name:'社区',exact:true}).click();
    const tabs=frame.getByRole('group',{name:'社区内容筛选'});
    await expect(tabs.getByRole('button')).toHaveText(['书籍','笔记']);
    await expect(frame.locator('.community-post.book').first()).toBeVisible();
    await tabs.getByRole('button',{name:'书籍',exact:true}).click();
    await expect(frame.locator('.community-post.book').first()).toBeVisible();
    const post=frame.locator('.community-post.book').first(), image=post.locator('img');
    await page.mouse.move(5,5);
    const resting=await image.evaluate(e=>{const m=new DOMMatrix(getComputedStyle(e).transform);return Math.hypot(m.a,m.b);});
    await post.hover();
    await expect.poll(()=>image.evaluate(e=>{const m=new DOMMatrix(getComputedStyle(e).transform);return Math.hypot(m.a,m.b);})).toBeGreaterThan(resting+.05);
    expect(await image.evaluate(e=>new DOMMatrix(getComputedStyle(e).transform).b)).toBeLessThan(0);
    await page.screenshot({path:info.outputPath('community.png')});
    await tabs.getByRole('button',{name:'笔记',exact:true}).click();
    await expect(frame.locator('.community-post.book')).toHaveCount(0);
    await tabs.getByRole('button',{name:'书籍',exact:true}).click();
    await expect(frame.locator('.community-post.book').first()).toBeVisible();
    await frame.getByRole('button',{name:'分享我的内容'}).click();
    await expect(frame.getByRole('group',{name:'分享类型'}).getByRole('button')).toHaveText(['书籍','笔记']);
    await page.keyboard.press('Escape');
    await page.emulateMedia({reducedMotion:'reduce'});
    await frame.getByRole('button',{name:'书架',exact:true}).click();
    expect(await covers.first().locator('img').evaluate(e=>getComputedStyle(e).transitionDuration)).toBe('0s');
    expect(await frame.locator('.phone').evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBe(true);
    expect(errors).toEqual([]);
  }finally{await context.close();}
});

test('QA book selection routes correctly without changing the learning book; errors preserve input',async({browser,baseURL},info)=>{
  test.skip(!baseURL,'Choose private preview explicitly.');
  const context=await browser.newContext();const page=await context.newPage();
  const frame=page.frameLocator('iframe');
  const paths:string[]=[];let fail=false;
  await page.route('**/api/books/*/qa',async route=>{
    paths.push(route.request().url());
    await route.fulfill({status:fail?502:200,contentType:'application/json',body:JSON.stringify(fail?{detail:'测试：回答服务暂时不可用'}:{status:'supported',answer:'测试答案',confidence:.9,evidence_pages:[10],insufficiency_reason:null,claims:[{text:'测试答案，来自所选书籍。',citations:[{page_number:10,quote:'测试原文',chunk_id:'test'}]}]})});
  });
  try{
    await page.goto(baseURL+'/?device=iphone-16');await frame.getByRole('button',{name:'暂时体验，稍后注册'}).click();
    await frame.getByRole('button',{name:'答疑',exact:true}).click();
    const select=frame.getByRole('combobox',{name:'答疑使用的书籍'});
    const first=await select.inputValue();
    const options=await select.locator('option').evaluateAll(es=>es.map(e=>(e as HTMLOptionElement).value));
    expect(options).toHaveLength(5);const other=options.find(x=>x!==first)!;
    await select.selectOption(other);
    await frame.getByRole('textbox',{name:'向教材小助手提问'}).fill('解释所选书的核心概念');
    await frame.getByRole('button',{name:'发送问题'}).click();
    await expect(frame.locator('.qa-answer')).toContainText('测试答案');
    expect(paths.at(-1)).toContain('/books/'+other+'/qa');
    await expect.poll(()=>frame.locator('.tutor-response').evaluate(e=>getComputedStyle(e).opacity)).toBe('1');
    await page.screenshot({path:info.outputPath('qa-selector.png')});
    await select.selectOption(first);await expect(frame.locator('.qa-answer')).toHaveCount(0);
    fail=true;
    await frame.getByRole('textbox',{name:'向教材小助手提问'}).fill('失败后保留我的问题');
    await frame.getByRole('button',{name:'发送问题'}).click();
    await expect(frame.locator('.qa-error')).toContainText('测试：回答服务暂时不可用');
    await expect(frame.getByRole('textbox',{name:'向教材小助手提问'})).toHaveValue('失败后保留我的问题');
    fail=false;await frame.getByRole('button',{name:/重新提问/}).click();
    await expect(frame.locator('.qa-answer')).toContainText('测试答案');expect(paths.at(-1)).toContain('/books/'+first+'/qa');
    await frame.getByRole('button',{name:'书架',exact:true}).click();
    await expect(frame.locator('.shelf-volume').filter({has:frame.locator('.reading-ribbon')})).toHaveCount(1);
    const library=await(await context.request.get(baseURL+'/api/library')).json();
    await expect(frame.locator('.shelf-volume').filter({has:frame.locator('.reading-ribbon')})).toContainText(library.find((b:{book_id:string})=>b.book_id===first).title);
  }finally{await context.close();}
});

test('live selected-book RAG returns supported citations for two subjects',async({browser,baseURL},info)=>{
  test.skip(!process.env.QA_LIVE_E2E,'Opt in to two real model calls.');test.setTimeout(360000);
  const context=await browser.newContext();const page=await context.newPage();const frame=page.frameLocator('iframe');
  const results:unknown[]=[];
  try{
    await page.goto(baseURL+'/?device=iphone-16');await frame.getByRole('button',{name:'暂时体验，稍后注册'}).click();
    await frame.getByRole('button',{name:'答疑',exact:true}).click();
    const select=frame.getByRole('combobox',{name:'答疑使用的书籍'});
    for(const [id,question] of [['biology-required-2','摩尔根的果蝇实验如何证明基因在染色体上？'],['996d1581e1f6','C++ 中构造函数和析构函数分别有什么作用？']]){
      await select.selectOption(id);await frame.getByRole('textbox',{name:'向教材小助手提问'}).fill(question);
      const pending=page.waitForResponse(r=>r.url().endsWith('/books/'+id+'/qa'),{timeout:160000});
      const started=Date.now();await frame.getByRole('button',{name:'发送问题'}).click();const response=await pending;
      expect(response.status(),await response.text()).toBe(200);const data=await response.json();
      expect(data.status).toBe('supported');expect(data.claims.length).toBeGreaterThan(0);expect(data.claims.every((c:{citations:unknown[]})=>c.citations.length>0)).toBe(true);
      await expect(frame.locator('.qa-answer')).toContainText(data.claims[0].text);
      results.push({book:id,question,elapsed_ms:Date.now()-started,result:data});
    }
    await info.attach('live-rag',{body:JSON.stringify(results,null,2),contentType:'application/json'});
    console.log('Live RAG results:',JSON.stringify(results));
    await expect.poll(()=>frame.locator('.tutor-response').evaluate(e=>getComputedStyle(e).opacity)).toBe('1');
    await page.screenshot({path:info.outputPath('qa-live.png')});
  }finally{await context.close();}
});
