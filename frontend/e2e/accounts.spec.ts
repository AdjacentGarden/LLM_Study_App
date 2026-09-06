import {test,expect,type BrowserContext,type Page,type FrameLocator} from '@playwright/test';

test('email signup/login, public demo friends, cross-device chat and session revocation',async({browser,baseURL},testInfo)=>{
 test.setTimeout(240000);
 test.skip(!process.env.ACCOUNT_E2E_SEED_DEMOS,'Opt-in: registers persistent, explicitly labelled demo accounts on the chosen private preview.');
 const contexts:BrowserContext[]=[],pages:Page[]=[],frames:FrameLocator[]=[],ids:string[]=[],errors:string[]=[];
 const emails=['xiaolin@zhiwo.test','momo@zhiwo.test','achen@zhiwo.test'];
 async function code(page:Page,frame:FrameLocator){
   for(let attempt=0;attempt<8;attempt++){
     const result=page.waitForResponse(r=>r.url().endsWith('/api/auth/code')&&r.request().method()==='POST');
     await frame.getByRole('button',{name:'获取验证码',exact:true}).click();const response=await result;
     if(response.status()===429){await page.waitForTimeout(10000);continue;}
     expect(response.status(),await response.text()).toBe(200);
     const value=await frame.locator('.auth-demo-code strong').innerText();expect(value).toMatch(/^\d{6}$/);
     await frame.getByRole('textbox',{name:'邮箱验证码',exact:true}).fill(value);return;
   }
   throw Error('Demo OTP cooldown did not clear');
 }
 try{
   for(let index=0;index<3;index++){
     const c=await browser.newContext({viewport:{width:900,height:1020}});contexts.push(c);
     const initial=await(await c.request.get(baseURL+'/api/auth/me')).json();
     const existing=initial.demo_users.find((u:{email:string})=>u.email===emails[index])?.registered;
     const page=await c.newPage();pages.push(page);page.on('pageerror',e=>errors.push(e.message));
     await page.goto(baseURL+'/?device=iphone-16&release=accounts-e2e');
     const f=page.frameLocator('iframe[title="iPhone 16 App 模拟屏幕"]');frames.push(f);
     await expect(f.getByRole('button',{name:'注册账号',exact:true})).toBeVisible();
     if(index===0)await page.screenshot({path:testInfo.outputPath('registration-iphone16.png')});
     if(existing)await f.getByRole('button',{name:'邮箱登录',exact:true}).click();
     await f.getByRole('textbox',{name:'注册登录邮箱'}).fill(emails[index]);
     await code(page,f);
     if(!existing){
       await f.getByRole('textbox',{name:'注册昵称'}).fill(['小林 · 演示','默默 · 演示','阿辰 · 演示'][index]);
       await f.getByLabel('学习阶段',{exact:true}).selectOption(['university','high','working'][index]);
       await f.getByRole('button',{name:['自然科学','语言学习','工程技术'][index],exact:true}).click();
       await f.getByLabel('学习目标',{exact:true}).selectOption(['interest','exam','work'][index]);
       await f.getByRole('checkbox').check();
     }
     await f.getByRole('button',{name:existing?'登录并继续学习':'创建我的学习空间',exact:true}).click();
     await expect(f.getByRole('button',{name:'社区',exact:true})).toBeEnabled();
     const state=await(await c.request.get(baseURL+'/api/auth/me')).json();expect(state.account.email).toBe(emails[index]);expect(state.account.is_demo).toBe(true);ids.push(state.user_id);
     await c.request.post(baseURL+'/api/social/discoverability',{data:{enabled:true}});
   }
   expect(new Set(ids).size).toBe(3);
   for(const f of frames.slice(0,2)){
     await f.getByRole('button',{name:'社区',exact:true}).click();await f.getByRole('button',{name:/好友与消息/}).click();
     await f.getByRole('button',{name:'添加演示好友',exact:true}).click();await expect(f.getByRole('status').filter({hasText:/已添加/})).toBeVisible();
     await f.getByRole('group',{name:'好友页面切换'}).getByRole('button',{name:/^好友/}).click();
   }
   await frames[0].locator('.social-person').filter({hasText:'默默 · 演示'}).getByRole('button',{name:'聊天',exact:true}).click();
   await frames[1].locator('.social-person').filter({hasText:'小林 · 演示'}).getByRole('button',{name:'聊天',exact:true}).click();
   const message='注册后的真实账号会话 '+Date.now();
   await frames[0].getByRole('textbox',{name:'给好友的消息'}).fill(message);await frames[0].getByRole('button',{name:'发送',exact:true}).click();
   await expect(frames[1].getByText(message,{exact:true})).toBeVisible();
   await frames[1].getByRole('textbox',{name:'给好友的消息'}).fill('收到了！可以换窗口继续测试。');await frames[1].getByRole('button',{name:'发送',exact:true}).click();
   await expect(frames[0].getByText('收到了！可以换窗口继续测试。',{exact:true}).last()).toBeVisible();
   await pages[0].screenshot({path:testInfo.outputPath('registered-friend-chat.png')});
   // Same browser, second tab: logout must clear the other tab's private app too.
   const second=await contexts[0].newPage();await second.goto(baseURL+'/?device=iphone-16');
   const sf=second.frameLocator('iframe[title="iPhone 16 App 模拟屏幕"]');await expect(sf.getByRole('button',{name:'我的',exact:true})).toBeEnabled();
   await frames[0].getByRole('button',{name:'我的',exact:true}).click();await frames[0].getByRole('button',{name:'退出登录 / 切换账号',exact:true}).click();
   await expect(frames[0].getByRole('button',{name:'邮箱登录',exact:true})).toBeVisible();
   await expect(sf.getByRole('button',{name:'邮箱登录',exact:true})).toBeVisible();
   expect((await(await contexts[0].request.get(baseURL+'/api/auth/me')).json()).account).toBeNull();
   // A fresh device can sign in with a new OTP and recover the exact same ID/history.
   const recovered=await browser.newContext({viewport:{width:900,height:1020}});contexts.push(recovered);
   const rp=await recovered.newPage();await rp.goto(baseURL+'/?device=iphone-16');const rf=rp.frameLocator('iframe[title="iPhone 16 App 模拟屏幕"]');
   await rf.getByRole('button',{name:'邮箱登录',exact:true}).click();await rf.getByRole('textbox',{name:'注册登录邮箱'}).fill(emails[0]);await code(rp,rf);
   await rf.getByRole('button',{name:'登录并继续学习',exact:true}).click();await expect(rf.getByRole('button',{name:'社区',exact:true})).toBeEnabled();
   expect((await(await recovered.request.get(baseURL+'/api/auth/me')).json()).user_id).toBe(ids[0]);
   const history=await(await recovered.request.get(baseURL+'/api/social/chats/'+ids[1])).json();expect(history.messages.some((m:{text:string})=>m.text===message)).toBe(true);
   expect(errors).toEqual([]);
 }finally{
   for(const c of contexts){await c.request.post(baseURL+'/api/auth/logout').catch(()=>{});await c.close();}
 }
});
