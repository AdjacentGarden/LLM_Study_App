import { ApiError } from "../../../api/transport";
import { runtimeConfig } from "../../config/runtime";
import { request } from "../../api/transport";
import type { StudyNote, TextStudyNote } from "./types";
const queues = new Map<string,Promise<unknown>>();
function serialize<T>(id:string, mutate:(owner:string)=>Promise<T>):Promise<T> {
 const owner=runtimeConfig.defaultUserId;
 const result=(queues.get(id)??Promise.resolve()).catch(()=>undefined).then(()=>{if(owner!==runtimeConfig.defaultUserId)throw new Error("账号已切换，旧笔记操作已取消");return mutate(owner);});
 queues.set(id,result); const clear=()=>{if(queues.get(id)===result)queues.delete(id);}; void result.then(clear,clear); return result;
}
const owned=(owner:string)=>({"X-Demo-Account-Id":owner});
const path=(id:string)=>`/api/demo/study-notes/${encodeURIComponent(id)}`;
const notify=()=>window.dispatchEvent(new CustomEvent("bookcourse:study-notes-changed"));
export function listStudyNotes():Promise<StudyNote[]> {return request("/api/demo/study-notes");}
export async function getStudyNote(id:string,owner=runtimeConfig.defaultUserId):Promise<StudyNote|null>{try{return await request(path(id),{headers:owned(owner)});}catch(error){if(error instanceof ApiError && error.status===404)return null;throw error;}}
export async function putStudyNote(note:StudyNote,owner=runtimeConfig.defaultUserId){const saved=await request<StudyNote>(path(note.id),{method:"PUT",headers:owned(owner),body:JSON.stringify(note)});notify();return saved;}
export function updateTextStudyNote(id:string,update:(note:TextStudyNote|undefined)=>TextStudyNote|undefined){return serialize(id,async(owner)=>{const existing=await getStudyNote(id,owner);const next=update(existing?.kind==="text"?existing:undefined);if(next)await putStudyNote(next,owner);return next;});}
export function deleteStudyNote(id:string){return serialize(id,async(owner)=>{await request(path(id),{method:"DELETE",headers:owned(owner)});notify();});}
export async function saveAudioBlob(id:string,blob:Blob,owner=runtimeConfig.defaultUserId){const data=new FormData();data.append("file",blob,"recording.webm");await request(path(id)+"/audio",{method:"PUT",headers:owned(owner),body:data});return true;}
export async function getAudioBlob(id:string,owner=runtimeConfig.defaultUserId){if(owner!==runtimeConfig.defaultUserId)throw new Error("账号已切换");const response=await fetch(path(id)+"/audio",{headers:{"X-Demo-Account-Id":owner}});if(owner!==runtimeConfig.defaultUserId)throw new Error("账号已切换");if(response.status===404)return null;if(!response.ok)throw new Error("录音读取失败");return response.blob();}
export async function deleteAudioBlob(id:string,owner=runtimeConfig.defaultUserId){await request(path(id)+"/audio",{method:"DELETE",headers:owned(owner)});}
export function createStudyNoteId(kind:StudyNote["kind"]){return `${kind}-note-${crypto.randomUUID()}`;}
export function createSilentWavBlob(){throw new Error("请录制真实音频，演示录音不适用于真实账号");}
export async function processStudyNote(id:string,action:"transcribe"|"organize"|"recognize",payload:{transcript?:string;consent?:boolean}={},owner=runtimeConfig.defaultUserId,signal?:AbortSignal){
 if(signal?.aborted)throw new Error("笔记页面已关闭");
 let note=await request<StudyNote>(path(id)+"/"+action,{method:"POST",headers:owned(owner),signal,body:JSON.stringify({consent:true,...payload})});
 while(["recognizing","transcribing","checking","retrieving","organizing","reviewing"].includes(note.pipelinePhase)){
   await new Promise(resolve=>setTimeout(resolve,1500));if(signal?.aborted)throw new Error("笔记页面已关闭");const next=await getStudyNote(id,owner);if(!next)throw new Error("笔记任务不存在");note=next;
 }
 if(note.pipelinePhase==="error")throw new Error(note.error??"笔记处理失败");notify();return note;
}
