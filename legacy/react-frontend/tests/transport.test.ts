import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, errorMessage, request } from "../src/api/transport.ts";

test("structured validation errors never render object Object",()=>{
  assert.equal(errorMessage([{loc:["question"],msg:"empty"}],422),"输入内容不符合要求，请检查后重试。");
  assert.equal(errorMessage("具体错误",503),"具体错误");
});
test("proxy HTML failure becomes an actionable error", async()=>{
  const old=globalThis.fetch;
  globalThis.fetch=async()=>new Response("Bad Gateway",{status:502});
  try { await assert.rejects(request("/api/test"),error=>error instanceof ApiError && error.status===502); }
  finally { globalThis.fetch=old; }
});
test("timed out mutations are never automatically retried", async()=>{
  const old=globalThis.fetch; let calls=0;
  globalThis.fetch=async(_input,init)=>{calls++;return new Promise((_resolve,reject)=>init?.signal?.addEventListener("abort",()=>reject(new Error("abort"))));};
  try {await assert.rejects(request("/api/test",{method:"POST"},5),error=>error instanceof ApiError && error.status===408);assert.equal(calls,1);}
  finally {globalThis.fetch=old;}
});
test("network failure retains meaningful recovery guidance",async()=>{
  const old=globalThis.fetch;globalThis.fetch=async()=>{throw new TypeError("failed to fetch");};
  try {await assert.rejects(request("/api/test"),/学习进度仍然保留/);}finally{globalThis.fetch=old;}
});
