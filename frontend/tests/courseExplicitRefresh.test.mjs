import assert from 'node:assert/strict';
import test from 'node:test';
import { loadChapterCourse, CourseTaskFailure, CourseTaskCancelled } from '../src/components/learningReliability.ts';
const key = 'zhiwo.course-task:session:chapter';
const seed = { requestId: 'original-request', jobId: 'old-job' };
const bookCourse = { course_id: 'new-profile-course', chapter_id: 'chapter', version: 2 };
const job = (status, id = 'old-job') => ({ job_id:id, session_id:'session', chapter_id:'chapter', status, stage:status, error:status === 'failed' ? '本次已停止；任务保留，请检查服务状态。' : null, retryable:false, attempts:status === 'failed' ? 3 : 1, course:status === 'succeeded' ? bookCourse : null });
const httpError = status => Object.assign(new Error(`fixture GET ${status}`), {status});
function fixture(receipt = seed) {
  const values = new Map(receipt ? [[key, JSON.stringify(receipt)]] : []), events = [];
  const f = { values, events, active:true, blockStorage:false, reads:[job('failed')], postReply:job('succeeded','new-job'), afterRead:null, readGate:null };
  f.storage = {
    safeGet:k => values.get(k) ?? null,
    safeSet:(k,v) => { events.push({kind:'storage', key:k, value:v ? JSON.parse(v) : null, writeBlocked:f.blockStorage}); if (!f.blockStorage) { if (v === null) values.delete(k); else values.set(k,v); } },
  };
  f.api = {
    getCourse:async (session,chapter) => { events.push({kind:'mock-api', method:'GET', target:'course-cache',session,chapter}); return bookCourse; },
    courseJob:async (session,id) => {
      events.push({kind:'mock-api',method:'GET',target:id,session});
      if (f.readGate) await f.readGate;
      const response = f.reads.length > 1 ? f.reads.shift() : f.reads[0];
      if (response instanceof Error) throw response;
      if (f.afterRead) f.afterRead();
      return structuredClone(response);
    },
    startCourseJob:async (session,chapter,id,retry) => {
      const saved = JSON.parse(values.get(key) ?? 'null');
      events.push({kind:'mock-api',method:'POST',target:'current-context-course-job',session,chapter,requestId:id,retry,persistedBeforeCall:saved?.requestId === id,persistedReceipt:saved});
      assert.equal(saved?.requestId,id,'intent must be readable from storage before a mock submission');
      if (f.postReply instanceof Error) throw f.postReply;
      return structuredClone(f.postReply);
    },
  };
  f.options = {api:f.api,storage:f.storage,active:()=>f.active,onJob:j=>events.push({kind:'onJob',id:j.job_id,status:j.status,retryable:j.retryable}),wait:async()=>events.push({kind:'wait'})};
  f.run = (options={}, loader=loadChapterCourse) => loader('session','chapter',{...f.options,...options});
  f.calls = () => events.filter(e=>e.kind === 'mock-api');
  f.posts = () => f.calls().filter(e=>e.method === 'POST');
  f.saved = () => JSON.parse(values.get(key) ?? 'null');
  return f;
}
async function check(name, fn) { await test(name, async () => { await fn(); }); }
await check('newer durable intent appearing during terminal preflight is never replaced', async () => {
  const f=fixture();
  const newer={requestId:'newer-cross-tab-intent',jobId:null};
  f.afterRead=()=>f.values.set(key,JSON.stringify(newer));
  await assert.rejects(f.run({refresh:true}), /已有另一次准备请求/);
  assert.equal(f.posts().length,0);assert.deepEqual(f.saved(),newer);return[f];
});
await check('explicit changed-profile refresh confirms old terminal then durable fresh ID and exactly one current-context POST',async()=>{
  const f=fixture();assert.equal(await f.run({refresh:true}),bookCourse);
  assert.deepEqual(f.calls().map(x=>[x.method,x.target]),[['GET','old-job'],['POST','current-context-course-job'],['GET','course-cache']]);
  const post=f.posts()[0];assert.notEqual(post.requestId,seed.requestId);assert.equal(post.retry,false);assert.equal(post.persistedBeforeCall,true);assert.equal(post.persistedReceipt.jobId,null);assert.equal(f.saved(),null);return[f];
});
await check('same-profile exhausted result remains same failed job with attempts3 and no retry-limit bypass',async()=>{
  const f=fixture();f.postReply=job('failed');let error;try{await f.run({refresh:true});}catch(e){error=e;}
  assert(error instanceof CourseTaskFailure);assert.equal(error.job.job_id,'old-job');assert.equal(error.job.attempts,3);assert.equal(error.job.retryable,false);
  assert.equal(f.posts().length,1);assert.equal(f.posts()[0].retry,false);assert.notEqual(f.saved().requestId,seed.requestId);assert.equal(f.saved().jobId,'old-job');return[f];
});
await check('known failed normal entry/status read is same GET only and retains ID',async()=>{
  const f=fixture();await assert.rejects(f.run(),CourseTaskFailure);assert.equal(f.posts().length,0);assert.deepEqual(f.saved(),seed);return[f];
});
await check('explicit refresh of unknown receipt does not retire intent or POST',async()=>{
  const unknown={requestId:'unknown-original',jobId:null};const f=fixture(unknown);await assert.rejects(f.run({refresh:true}),/回执尚未确认/);assert.equal(f.calls().length,0);assert.deepEqual(f.saved(),unknown);return[f];
});
await check('pending preflight followed by failure never becomes automatic new-version generation',async()=>{
  const f=fixture();f.reads=[job('running'),job('failed')];await assert.rejects(f.run({refresh:true}),CourseTaskFailure);assert.equal(f.posts().length,0);assert.deepEqual(f.calls().map(x=>x.target),['old-job','old-job']);assert.deepEqual(f.saved(),seed);return[f];
});
await check('pending preflight followed by success opens its result without starting another job',async()=>{
  const f=fixture();f.reads=[job('queued'),job('succeeded')];assert.equal(await f.run({refresh:true}),bookCourse);assert.equal(f.posts().length,0);assert.deepEqual(f.calls().map(x=>x.target),['old-job','old-job','course-cache']);return[f];
});
await check('confirmed succeeded old job is eligible for explicit current-version refresh',async()=>{
  const f=fixture();f.reads=[job('succeeded')];assert.equal(await f.run({refresh:true}),bookCourse);assert.equal(f.posts().length,1);assert.notEqual(f.posts()[0].requestId,seed.requestId);return[f];
});
await check('preflight404409503 preserve receipt and issue no submission',async()=>{
  const fixtures=[];for(const status of[404,409,503]){const f=fixture();f.reads=[httpError(status)];await assert.rejects(f.run({refresh:true}),new RegExp(String(status)));assert.equal(f.posts().length,0);assert.deepEqual(f.saved(),seed);fixtures.push(f);}return fixtures;
});
await check('mismatched job session chapter or unknown status cannot prove terminal',async()=>{
  const fixtures=[];for(const patch of[{job_id:'wrong-job'},{session_id:'wrong-session'},{chapter_id:'wrong-chapter'},{status:'unknown'}]){const f=fixture();f.reads=[{...job('failed'),...patch}];await assert.rejects(f.run({refresh:true}),/未能确认原课程任务/);assert.equal(f.posts().length,0);assert.deepEqual(f.saved(),seed);fixtures.push(f);}return fixtures;
});
await check('navigation cancellation after preflight cannot retire receipt or POST',async()=>{
  const f=fixture();f.afterRead=()=>{f.active=false;};await assert.rejects(f.run({refresh:true}),CourseTaskCancelled);assert.equal(f.posts().length,0);assert.deepEqual(f.saved(),seed);return[f];
});
await check('blocked durable storage prevents fresh submission and retains original saved receipt',async()=>{
  const f=fixture();f.blockStorage=true;await assert.rejects(f.run({refresh:true}),/无法保存/);assert.equal(f.posts().length,0);assert.deepEqual(f.saved(),seed);return[f];
});
await check('fresh POST lost reply preserves ID; another refresh blocks; explicit old recovery reuses that ID',async()=>{
  const f=fixture();f.postReply=new Error('fixture lost response');await assert.rejects(f.run({refresh:true}),/lost response/);
  const pending=f.saved();assert.notEqual(pending.requestId,seed.requestId);assert.equal(pending.jobId,null);const calls=f.calls().length;
  await assert.rejects(f.run({refresh:true}),/回执尚未确认/);assert.equal(f.calls().length,calls);assert.deepEqual(f.saved(),pending);
  f.postReply=job('succeeded','new-job');assert.equal(await f.run(),bookCourse);assert.equal(f.posts().length,2);assert.equal(f.posts()[0].requestId,f.posts()[1].requestId);assert.equal(f.posts()[1].retry,false);return[f];
});
await check('existing explicit retryFailed keeps same original ID and retrytrue without new refresh branch',async()=>{
  const f=fixture();f.postReply=job('succeeded');assert.equal(await f.run({retryFailed:true}),bookCourse);assert.equal(f.posts().length,1);assert.equal(f.posts()[0].requestId,seed.requestId);assert.equal(f.posts()[0].retry,true);assert.equal(f.calls()[0].method,'POST');return[f];
});
await check('concurrent explicit refresh calls from one scoped storage submit current context once',async()=>{
  const f=fixture();let release;f.readGate=new Promise(r=>{release=r;});f.postReply=job('failed');
  const calls=[f.run({refresh:true}),f.run({refresh:true})];await Promise.resolve();release();const outcomes=await Promise.allSettled(calls);
  assert(outcomes.every(x=>x.status==='rejected'));assert.equal(f.posts().length,1);assert(outcomes.some(x=>x.status==='rejected' && /另一次准备请求/.test(x.reason.message)));return[f];
});
