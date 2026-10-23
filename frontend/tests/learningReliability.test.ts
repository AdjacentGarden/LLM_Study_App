import assert from "node:assert/strict";
import test from "node:test";
import { CourseTaskCancelled, CourseTaskFailure, loadChapterCourse, PendingOperations, reconcileInterviewFailure } from "../src/components/learningReliability.ts";
import type { CourseJob, CourseTaskAPI } from "../src/components/learningReliability.ts";
import type { Course, InterviewResponse } from "../src/types/api.ts";
function storage() {
 const values = new Map<string,string>();
 return { safeGet:(key:string)=>values.get(key)??null, safeSet:(key:string,value:string|null)=>{if(value===null)values.delete(key);else values.set(key,value);},values };
}
const unavailable=(status:number)=>Object.assign(new Error(`HTTP ${status}`),{status});
const course={course_id:"course",chapter_id:"chapter"} as Course;
const job=(status:CourseJob["status"]="queued"):CourseJob=>({job_id:"job-1",session_id:"session",chapter_id:"chapter",status,stage:status,error:status==="failed"?"章节未通过审核":null,retryable:status==="failed",course:status==="succeeded"?course:null});
function fixture(){
 const data=storage(),posts:Array<{id:string;retry:boolean}>=[],gets:string[]=[];let saved=false;
 const api:CourseTaskAPI={getCourse:async()=>{if(!saved)throw unavailable(404);return course;},startCourseJob:async(_s,_c,id,retry)=>{posts.push({id,retry});return job();},courseJob:async(_s,id)=>{gets.push(id);saved=true;return job("succeeded");}};
 return {data,posts,gets,api,ready:()=>{saved=true;},options:{api,storage:data,active:()=>true,onJob:()=>{},wait:async()=>{}}};
}
test("existing approved course opens without generating a job",async()=>{const f=fixture();f.ready();assert.equal(await loadChapterCourse("session","chapter",f.options),course);assert.deepEqual(f.posts,[]);});
test("read failure never converts an outage into paid generation",async()=>{const f=fixture();f.api.getCourse=async()=>{throw unavailable(503);};await assert.rejects(loadChapterCourse("session","chapter",f.options),/503/);assert.deepEqual(f.posts,[]);});
test("lost POST response preserves request ID across reload/profile change",async()=>{
 const f=fixture();let attempts=0;f.api.startCourseJob=async(_s,_c,id,retry)=>{f.posts.push({id,retry});if(++attempts===1)throw new Error("response lost");return job();};
 await assert.rejects(loadChapterCourse("session","chapter",f.options),/response lost/);await loadChapterCourse("session","chapter",f.options);assert.equal(f.posts.length,2);assert.equal(f.posts[0].id,f.posts[1].id);assert.equal(f.data.values.size,0);
});
test("lost GET resumes same job without posting another",async()=>{
 const f=fixture();let reads=0;const original=f.api.courseJob;f.api.courseJob=async(...args)=>{if(++reads===1)throw new Error("offline");return original(...args);};
 await assert.rejects(loadChapterCourse("session","chapter",f.options),/offline/);await loadChapterCourse("session","chapter",f.options);assert.equal(f.posts.length,1);assert.equal(reads,2);
});
test("disabled browser storage still keeps a lost POST receipt in this tab",async()=>{
 const f=fixture();let attempts=0;const disabled={safeGet:()=>null,safeSet:()=>{}};f.api.startCourseJob=async(_s,_c,id,retry)=>{f.posts.push({id,retry});if(++attempts===1)throw new Error("offline");return job();};const options={...f.options,storage:disabled};await assert.rejects(loadChapterCourse("session","chapter",options));await loadChapterCourse("session","chapter",options);assert.equal(f.posts[0].id,f.posts[1].id);
});
test("publication conflict clears obsolete receipt but does not generate in the failing call",async()=>{
 const f=fixture();f.api.courseJob=async()=>{throw unavailable(409);};await assert.rejects(loadChapterCourse("session","chapter",f.options),/409/);assert.equal(f.posts.length,1);assert.equal(f.data.values.size,0);f.api.courseJob=async()=>{f.ready();return job("succeeded");};await loadChapterCourse("session","chapter",f.options);assert.equal(f.posts.length,2);assert.notEqual(f.posts[0].id,f.posts[1].id);
});
test("leaving generation retains durable receipt",async()=>{
 const f=fixture();let active=true;await assert.rejects(loadChapterCourse("session","chapter",{...f.options,active:()=>active,wait:async()=>{active=false;}}),CourseTaskCancelled);assert.equal(f.posts.length,1);await loadChapterCourse("session","chapter",f.options);assert.equal(f.posts.length,1);
});
test("review rejection needs explicit retry with same request ID",async()=>{
 const f=fixture();f.api.startCourseJob=async(_s,_c,id,retry)=>{f.posts.push({id,retry});return retry?job():job("failed");};await assert.rejects(loadChapterCourse("session","chapter",f.options),CourseTaskFailure);const old=f.posts[0].id;await loadChapterCourse("session","chapter",{...f.options,retryFailed:true});assert.equal(f.posts[1].id,old);assert.equal(f.posts[1].retry,true);
});
test("lost write reply replays exact event/payload after reload",async()=>{
 const data=storage();let writes=0;const seen=new Set<string>(),payloads:unknown[]=[];const send=async(payload:Record<string,unknown>)=>{payloads.push(payload);if(!seen.has(String(payload.event_id))){seen.add(String(payload.event_id));writes++;}if(payloads.length===1)throw new Error("saved but reply lost");return "recovered";};
 await assert.rejects(new PendingOperations(data).run("practice",()=>({event_id:"fixed",answer:"original",confidence:.4}),send));assert.equal(await new PendingOperations(data).run("practice",()=>({event_id:"new",answer:"changed",confidence:.9}),send),"recovered");assert.deepEqual(payloads[0],payloads[1]);assert.equal(writes,1);assert.equal(data.values.size,0);
});
test("double click shares one request then next operation gets its own event",async()=>{
 const store=new PendingOperations(storage());let calls=0;const send=async()=>{calls++;await new Promise(resolve=>setTimeout(resolve,2));return true;};await Promise.all([store.run("review",()=>({event_id:"one",rating:"hard"}),send),store.run("review",()=>({event_id:"two",rating:"easy"}),send)]);assert.equal(calls,1);await store.run("review",()=>({event_id:"three",rating:"good"}),send);assert.equal(calls,2);
});
test("diagnostic reconciliation detects saved turn without resubmission",async()=>{const latest={turn:{turn_id:"next"}} as InterviewResponse;assert.deepEqual(await reconcileInterviewFailure(async()=>latest,"old"),{interview:latest,advanced:true});latest.turn.turn_id="old";assert.equal((await reconcileInterviewFailure(async()=>latest,"old")).advanced,false);});
