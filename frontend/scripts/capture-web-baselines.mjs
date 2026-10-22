#!/usr/bin/env node
/** Capture the current React worktree without editing it. Only deterministic bundled fixtures are used. */
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const destination = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = process.env.BOOKCOURSE_SOURCE ?? process.env.SOURCE_REPO;
if (!source) throw new Error('Set BOOKCOURSE_SOURCE explicitly to the original frontend repository for this maintenance-only visual capture.');
const baseURL = process.env.WEB_BASE_URL ?? 'http://127.0.0.1:5173';
const require = createRequire(path.join(source, 'package.json'));
const { webkit } = require('playwright');
const output = path.join(destination, 'References', 'VisualBaselines');
const sources = JSON.parse(await readFile(path.join(destination, 'Resources/Data/sources.json'), 'utf8'));
const fixedDate = Date.UTC(2026, 7, 1, 9, 41);
const diagnosis = { urgency: 'steady', timePattern: 'block', goals: ['systematic'], contentFoci: ['principles'], aids: ['chat'], reviews: ['periodic'] };
const seed = {
  version: 2,
  preferences: { displayName: '小明同学', primaryGoal: null, dailyTime: null, completedAt: fixedDate },
  onboardingDraft: { displayName: '小明同学', primaryGoal: 'systematic', dailyTime: '30to60', step: 0 },
  courses: sources.map(item => ({ id: `fixture:${item.summary.book_id}`, name: item.summary.title, resourceIds: [`source:${item.summary.book_id}`], activeResourceId: `source:${item.summary.book_id}`, diagnosis, createdAt: fixedDate, updatedAt: fixedDate })),
  resources: [], draft: null, activeCourseId: 'fixture:book_biology_2', dismissedSourceIds: []
};
const routes = ['home','onboarding','courseSetup','courseDetail','upload','parseReady','processing','courseImportProcessing','chapterConfirm','courseReady','courseImportReady','library','community','communityBook','communityImport','study','plan','flashcards','lesson','assignment','diagnosis','mistakes','notes','voiceNote','source','export','profile','settings'];
const landscapeRoutes = ['home','onboarding','courseSetup','community','study','lesson','source','assignment','notes','profile','settings'];
const devices = [
  { name: 'iphone-402x874', width: 402, height: 874, routes },
  { name: 'ipad-834x1194', width: 834, height: 1194, routes },
  { name: 'iphone-landscape-874x402', width: 874, height: 402, routes: landscapeRoutes },
  { name: 'ipad-landscape-1194x834', width: 1194, height: 834, routes: landscapeRoutes },
  { name: 'ipad-834x1210', width: 834, height: 1210, routes },
  { name: 'ipad-landscape-1210x834', width: 1210, height: 834, routes: landscapeRoutes }
];
const selectedDevices = process.env.CAPTURE_DEVICES ? devices.filter(device => process.env.CAPTURE_DEVICES.split(",").includes(device.name)) : devices;
const previousManifest = process.env.CAPTURE_DEVICES ? JSON.parse(await readFile(path.join(output,"manifest.json"),"utf8").catch(() => "{}")) : {};
const selectedNames = new Set(selectedDevices.map(device => device.name));
const retain = key => (previousManifest[key] ?? []).filter(item => !selectedNames.has(item.device ?? item.file?.split("/")[0]));
const manifest = {
  source, sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: source, encoding: 'utf8' }).trim(),
  sourceWorkingTree: execFileSync('git', ['status', '--short'], { cwd: source, encoding: 'utf8' }),
  renderer: 'Playwright WebKit', colorScheme: 'light', locale: 'zh-CN', timezone: 'Asia/Hong_Kong', scale: 1,
  motion: 'reduce (stable appearance); animation behavior tested separately',
  systemChrome: 'Source simulated chrome retained. Crop the system status/home bars when comparing with native system chrome.',
  fixture: { bookID: 'book_biology_2', chapterID: 'c2s1', courseID: seed.activeCourseId, displayName: seed.preferences.displayName },
  scenes: retain("scenes"), failures: retain("failures"), unexpectedRequests: retain("unexpectedRequests"), consoleErrors: retain("consoleErrors")
};
await mkdir(output, { recursive: true });
const browser = await webkit.launch({ headless: true });

async function contextValue(page, operation, argument) {
  return await page.evaluate(async ({ operation, argument }) => {
    const root = document.querySelector('#root');
    const key = Object.keys(root).find(key => key.startsWith('__reactContainer$'));
    const anchor = root[key];
    const stack = [anchor.stateNode?.current ?? anchor];
    const seen = new Set(); let context;
    while (stack.length) {
      const node = stack.pop(); if (!node || seen.has(node)) continue; seen.add(node);
      if (node.type?.name === 'AppProvider' && node.memoizedProps?.value?.courses) { context = node.memoizedProps.value; break; }
      stack.push(node.child, node.sibling);
    }
    if (!context) throw new Error('Current AppProvider fixture context not found');
    const ctx = context;
    const source = { courseId: 'fixture:book_biology_2', bookId: 'book_biology_2', title: '生物 必修 2 遗传与进化', pageStart: 11, pageEnd: 11, printedPageStart: 16, from: 'lesson' };
    const anchorNote = { courseId: source.courseId, bookId: source.bookId, bookTitle: source.title, chapterId: 'c2s1', chapterTitle: '减数分裂和受精作用', pageStart: 11, pageEnd: 11, printedPageStart: 16, quote: '减数分裂过程中，染色体只复制一次，而细胞分裂两次。' };
    if (operation === 'select') { await ctx.selectCourse(argument); return; }
    if (operation === 'navigate') {
      const route = argument;
      ctx.closeSheet();
      if (route === 'onboarding') ctx.courses.updateOnboardingDraft({ step: 0, displayName: '小明同学', primaryGoal: 'systematic', dailyTime: '30to60' });
      if (route === 'upload') ctx.courses.clearDraft();
      if (route === 'courseSetup') {
        ctx.courses.startDraft('source:book_biology_2', { suggestedName: '生物复习', uploadedCourse: ctx.uploadedFile });
        ctx.courses.updateDraft({ step: 0, parseCompleted: true, diagnosis: { urgency: 'steady', timePattern: 'block', goals: ['systematic'], contentFoci: ['principles'], aids: ['chat'], reviews: ['periodic'] } });
      }
      if (route === 'diagnosis') {
        const { bookcourseApi } = await import('/src/api/bookcourseApi.ts');
        ctx.setLatestDiagnosis(await bookcourseApi.diagnoseAssignment('assignment_c2s1', 'fixture_submission'));
      }
      if (route === 'source') { ctx.openSourcePage(source); return; }
      if (route === 'voiceNote') { ctx.startNote({ kind: 'voice', anchor: anchorNote, source, from: 'source' }); return; }
      if (route === 'processing') {
        ctx.setParseJobStatus({ job_id: 'baseline_parse', book_id: source.bookId, status: 'processing', stage: 'chapters', progress: 46, message: '已识别目录，正在整理课程结构', error: null });
        ctx.setParseJobId('baseline_parse');
      } else if (route !== 'parseReady') ctx.setParseJobId(null);
      ctx.replaceScreen(route);
      return;
    }
    if (operation === 'sheet') {
      const type = argument;
      const sheet = type === 'note' ? { type, concept: '减数分裂', explanation: '理解一次复制与两次分裂的关系。', sourceLabel: '教材第 16 页', source }
        : type === 'noteType' ? { type, contextLabel: '教材第 16 页', inkAvailable: true, intent: { anchor: anchorNote, source, from: 'source' }, pageOptions: [{ page: 11, label: '教材第 16 页' }] }
        : type === 'editChapter' ? { type, chapterId: 'c2s1' }
        : type === 'addMaterials' ? { type, courseId: 'fixture:book_biology_2' }
        : { type };
      ctx.openSheet(sheet); return;
    }
    if (operation === 'onboardingStep') { ctx.courses.updateOnboardingDraft({ step: argument }); return; }
    if (operation === 'diagnosisStep') { ctx.courses.updateDraft({ step: argument }); return; }
    if (operation === 'closeSheet') { ctx.closeSheet(); return; }
    if (operation === 'inspect') return { loadedBookID: ctx.loadedBookId, chapterID: ctx.activeChapterId, selectedCommunityID: ctx.selectedCommunityBookId, courseID: ctx.courses.state.activeCourseId, draft: ctx.courses.state.draft };
    throw new Error(`Unknown fixture operation ${operation}`);
  }, { operation, argument });
}

async function capture(page, device, scene, expectedRoute, extra = {}) {
  await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].filter(image => !image.loading || image.loading !== 'lazy').map(image => image.decode().catch(() => {}))); });
  // Reduced motion settles transitions immediately; allow layout and repository promises to flush.
  await page.waitForTimeout(expectedRoute === 'processing' || expectedRoute === 'courseImportProcessing' ? 140 : 220);
  const state = await page.evaluate(() => {
    const shell = document.querySelector('.app-shell'); const main = document.querySelector('.screen-content');
    const boxes = {};
    for (const selector of ['.ios-status-bar','.home-indicator','.header-bar','.primary-nav','.home-book-carousel','.home-book-workspace','.sheet','.ai-overlay','.community-grid','.lesson-knowledge-pager','.source-page-frame']) {
      const node = document.querySelector(selector); if (node) { const r=node.getBoundingClientRect(); boxes[selector]={ x:r.x,y:r.y,width:r.width,height:r.height }; }
    }
    return { route: shell?.dataset.activeScreen, layout: shell?.dataset.deviceLayout, title: document.querySelector('.header-title h1')?.textContent, main: main ? { scrollHeight: main.scrollHeight, clientHeight: main.clientHeight } : null, boxes,
      typography: shell ? { family: getComputedStyle(shell).fontFamily, body: getComputedStyle(shell).fontSize, background: getComputedStyle(shell).backgroundColor, safeTop: getComputedStyle(shell).getPropertyValue('--safe-area-top').trim(), safeBottom: getComputedStyle(shell).getPropertyValue('--safe-area-bottom').trim(), titleTop: getComputedStyle(shell).getPropertyValue('--page-title-top').trim() } : null };
  });
  if (state.route !== expectedRoute) throw new Error(`Expected ${expectedRoute}, rendered ${state.route}`);
  const file = `${device.name}/${scene}.png`;
  await page.screenshot({ path: path.join(output,file), animations: 'disabled', fullPage: false });
  const fixtureState = await contextValue(page,'inspect');
  manifest.scenes.push({ file, scene, fixtureState, viewport: {width:device.width,height:device.height}, ...state, ...extra });
  console.log(`captured ${file}`);
}

async function runDevice(device) {
  await mkdir(path.join(output, device.name), { recursive: true });
  const context = await browser.newContext({ viewport: {width:device.width,height:device.height}, deviceScaleFactor: 1, colorScheme: 'light', reducedMotion: 'reduce', locale: 'zh-CN', timezoneId: 'Asia/Hong_Kong' });
  const page = await context.newPage();
  await page.clock.setFixedTime(new Date(fixedDate));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if ((url.protocol === 'http:' || url.protocol === 'https:') && (url.origin !== new URL(baseURL).origin || url.pathname.startsWith('/api/'))) {
      manifest.unexpectedRequests.push({ device:device.name,url:route.request().url() }); await route.abort();
    } else await route.continue();
  });
  await page.addInitScript(({ seed,fixedDate }) => {
    localStorage.clear(); localStorage.setItem('bookcourse.courses.v2',JSON.stringify(seed));
    localStorage.setItem('bookcourse.saved-study-notes.v1',JSON.stringify([{id:'baseline-note',title:'减数分裂学习笔记',body:'减数分裂过程中，染色体只复制一次，而细胞分裂两次。',createdAt:fixedDate,quote:'染色体只复制一次，而细胞分裂两次。',sourceLabel:'教材第 16 页'}]));
  }, {seed,fixedDate});
  page.on('pageerror',error => manifest.consoleErrors.push({device:device.name,error:error.message}));
  await page.goto(`${baseURL}/?embedded=device-preview`, { waitUntil:'networkidle' });
  await page.locator('.home-dashboard').waitFor();
  await page.waitForTimeout(1200);
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const book = page.locator('.home-book-option[data-book-id="fixture:book_biology_2"]');
    if (await book.getAttribute('aria-selected') !== 'true') await book.click();
    await contextValue(page,'select','fixture:book_biology_2');
    await page.waitForTimeout(350);
    if (await page.locator('.home-book-workspace').getAttribute('data-loaded') === 'true') break;
  }
  await page.waitForFunction(() => document.querySelector('.home-book-workspace')?.getAttribute('data-loaded') === 'true', {timeout:5000});
  for (const route of device.routes) {
    try {
      // Always restore a loaded source before the next screen; transient import polling cannot leak into screenshots.
      if (route !== 'home') await contextValue(page,'select','fixture:book_biology_2');
      await contextValue(page,'navigate',route);
      await capture(page,device,route,route);
    } catch (error) { manifest.failures.push({device:device.name,scene:route,error:error.message}); console.error(`${device.name}/${route}: ${error.message}`); }
  }
  if (!device.name.includes('landscape')) {
    for (const type of ['chat','note','noteType','editChapter','bookSwitcher','addMaterials']) {
      try {
        await contextValue(page,'navigate','study'); await page.waitForTimeout(100);
        await contextValue(page,'sheet',type);
        await page.locator(`.sheet[data-sheet-type="${type}"]`).waitFor({timeout:5000});
        await capture(page,device,`sheet-${type}`,'study',{sheet:type});
        await contextValue(page,'closeSheet');
      } catch(error) { manifest.failures.push({device:device.name,scene:`sheet-${type}`,error:error.message}); }
    }
    try {
      await contextValue(page,'navigate','onboarding');
      for (const step of [1,2]) { await contextValue(page,'onboardingStep',step); await capture(page,device,`onboarding-step-${step+1}`,'onboarding',{step}); }
      await contextValue(page,'navigate','courseSetup');
      for (const step of [1,2,3,4,5]) { await contextValue(page,'diagnosisStep',step); await capture(page,device,`courseSetup-step-${step+1}`,'courseSetup',{step}); }
      await contextValue(page,'navigate','home'); await page.locator('.ai-orb').click();
      await page.locator('.ai-overlay').waitFor({timeout:5000});
      await capture(page,device,'ai-assistant','home',{overlay:'AI assistant'});
    } catch(error) { manifest.failures.push({device:device.name,scene:'extra-states',error:error.message}); }
  }
  await context.close();
}
try { await Promise.all(selectedDevices.map(async device => { try { await runDevice(device); } catch(error) { manifest.failures.push({device:device.name,scene:'device-setup',error:error.message}); } })); }
finally {
  await browser.close();
  await writeFile(path.join(output,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
  await writeFile(path.join(output,'README.md'),`# Current-worktree WebKit baselines\n\nCaptured from the current React worktree, including its uncommitted changes. See manifest.json for exact source revision, dirty files, fixture scope, viewport, rendered routes and geometry.\n\nRun with the old project's Vite server available at ${baseURL}:\n\n\`node scripts/capture-web-baselines.mjs\`\n\nThe script reads the old project and drives its in-memory AppProvider only. It writes screenshots and the manifest to this new repository. External/API requests are blocked. Static screenshots use Reduced Motion; test animation timing separately. Portrait captures cover all 28 routes and six sheet types. Core flows also have landscape baselines. Source simulated system bars should be excluded when comparing native system chrome.\n\nScenes: ${manifest.scenes.length}; failures: ${manifest.failures.length}.\n`);
}
if (manifest.failures.length) process.exitCode=1;
