import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "./transport";
import { runtimeConfig } from "../config/runtime";
import { bookcourseApi } from "./bookcourseApi";
import { getStudyNote, putStudyNote, processStudyNote, updateTextStudyNote } from "../features/studyNotes/repository";
afterEach(()=>{vi.unstubAllGlobals();runtimeConfig.defaultUserId="";});
describe("direct HTTP account fence",()=>{
 it("sends identity and rejects old account results after a switch",async()=>{
  runtimeConfig.defaultUserId="account-a";let resolve!: (value:Response)=>void;
  const fetch=vi.fn((_path,init)=>{expect(init.headers["X-Demo-Account-Id"]).toBe("account-a");return new Promise<Response>(done=>{resolve=done;});});vi.stubGlobal("fetch",fetch);
  const pending=request("/api/demo/course-state");runtimeConfig.defaultUserId="account-b";resolve(new Response("{}"));await expect(pending).rejects.toThrow("账号已切换");expect(fetch).toHaveBeenCalledTimes(1);
 });
 it("keeps 404 note creation separate from failed server reads",async()=>{
  vi.stubGlobal("fetch",vi.fn().mockResolvedValueOnce(new Response('{"detail":"笔记不存在"}',{status:404})).mockResolvedValueOnce(new Response('{"detail":"数据库不可用"}',{status:503})));
  expect(await getStudyNote("new-note")).toBeNull();await expect(getStudyNote("saved-note")).rejects.toThrow("数据库不可用");
 });
 it("uploads the actual file as multipart with the owning account",async()=>{
  runtimeConfig.defaultUserId="account-a";const file=new File(["real material"],"lesson.md",{type:"text/markdown"});
  vi.stubGlobal("fetch",vi.fn(async(path,init)=>{expect(path).toBe("/api/demo/bookcourse/uploadFile/book-a");expect(init.body.get("file").name).toBe("lesson.md");expect(await init.body.get("file").text()).toBe("real material");expect(init.headers["X-Demo-Account-Id"]).toBe("account-a");return new Response('{"book_id":"book-a","saved":true}');}));
  await bookcourseApi.uploadFile("book-a",file);
 });
 it("rejects deferred screen writes for their original owner before fetch",async()=>{
  runtimeConfig.defaultUserId="account-b";const fetch=vi.fn();vi.stubGlobal("fetch",fetch);
  await expect(getStudyNote("shared-note","account-a")).rejects.toThrow("账号已切换");
  await expect(putStudyNote({id:"shared-note"} as never,"account-a")).rejects.toThrow("账号已切换");
  await expect(processStudyNote("shared-note","transcribe",{},"account-a")).rejects.toThrow("账号已切换");
  expect(fetch).not.toHaveBeenCalled();
 });
 it("stops a queued text mutation when its account switches",async()=>{
  runtimeConfig.defaultUserId="account-a";const fetch=vi.fn();vi.stubGlobal("fetch",fetch);
  const pending=updateTextStudyNote("queued-note",()=>undefined);runtimeConfig.defaultUserId="account-b";
  await expect(pending).rejects.toThrow("账号已切换");expect(fetch).not.toHaveBeenCalled();
 });
});
