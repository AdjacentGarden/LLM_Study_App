import assert from "node:assert/strict";
import test from "node:test";
import { browserVoiceDraftStore, stopAndWaitForRecording, VoiceDraftWriter } from "../src/components/audioDrafts.ts";
import type { VoiceDraft, VoiceDraftStore } from "../src/components/audioDrafts.ts";
import type { InkNote } from "../src/api/learningStudio.ts";
const note:InkNote={id:"voice-test",revision:0,title:"录音草稿",input_mode:"voice",book_id:"book",strokes:[]};
const draft=(text:string,scope="owner-a",recording=false):VoiceDraft=>({scope,note,blob:new Blob([text],{type:"audio/webm"}),duration:3,recording,updated:1});
function memoryStore(){
 const rows=new Map<string,VoiceDraft>(),key=(s:string,n:string)=>JSON.stringify([s,n]);
 const store:VoiceDraftStore={put:async v=>{rows.set(key(v.scope,v.note.id),v);},get:async(s,n)=>rows.get(key(s,n))??null,list:async(s,b)=>[...rows.values()].filter(v=>v.scope===s&&v.note.book_id===b),remove:async(s,n)=>{rows.delete(key(s,n));}};
 return {store,rows};
}
test("raw audio survives a new writer/session and remains scoped to its account",async()=>{
 const {store}=memoryStore();await new VoiceDraftWriter(store).put(draft("raw bytes","owner-a",true));
 const restored=await store.get("owner-a",note.id);assert.equal(await restored!.blob.text(),"raw bytes");assert.equal(restored!.recording,true);
 assert.equal(await store.get("owner-b",note.id),null);assert.deepEqual(await store.list("owner-b","book"),[]);
 await new VoiceDraftWriter(store).put(draft("final bytes"));assert.equal(await (await store.get("owner-a",note.id))!.blob.text(),"final bytes");
});
test("chunk writes and deletion are serialized so old chunks cannot resurrect a synced draft",async()=>{
 const {store,rows}=memoryStore();let release:()=>void=()=>{};const gate=new Promise<void>(resolve=>{release=resolve;});const original=store.put;
 store.put=async d=>{await gate;await original(d);};const writer=new VoiceDraftWriter(store);
 const first=writer.put(draft("partial"));const final=writer.put(draft("partial+last"));const deleted=writer.remove("owner-a",note.id);release();await Promise.all([first,final,deleted]);assert.equal(rows.size,0);
});
test("quota failure is rejected and later retry can actually persist",async()=>{
 const {store}=memoryStore();const original=store.put;store.put=async()=>{throw new Error("quota full");};const writer=new VoiceDraftWriter(store);
 await assert.rejects(writer.put(draft("not saved")),/quota/);await assert.rejects(writer.flush(),/quota/);assert.equal(await store.get("owner-a",note.id),null);
 store.put=original;await writer.put(draft("saved after recovery"));assert.equal(await (await store.get("owner-a",note.id))!.blob.text(),"saved after recovery");
});
test("leaving while recording waits for final onstop bytes and durable write before server save",async()=>{
 const {store}=memoryStore(),writer=new VoiceDraftWriter(store);let done:()=>void=()=>{};
 const finished=new Promise<void>(resolve=>{done=resolve;});let state="recording";const events:string[]=[];
 const recorder={get state(){return state;},stop(){state="inactive";setTimeout(async()=>{events.push("last-data");await writer.put(draft("initial+last-data"));events.push("local-saved");done();},2);}};
 await stopAndWaitForRecording(recorder,finished);events.push("server-save");assert.deepEqual(events,["last-data","local-saved","server-save"]);assert.equal(await (await store.get("owner-a",note.id))!.blob.text(),"initial+last-data");
});
test("a recorder without onstop does not falsely allow closing",async()=>{
 await assert.rejects(stopAndWaitForRecording({state:"recording",stop(){}},new Promise<void>(()=>{}),3),/尚未停止/);
});
test("upload revision receipt is retained with the blob for idempotent audio PUT recovery",async()=>{
 const {store}=memoryStore();await new VoiceDraftWriter(store).put({...draft("voice"),uploadBase:{...note,revision:7}});
 const recovered=await store.get("owner-a",note.id);assert.equal(recovered!.uploadBase!.revision,7);assert.equal(await recovered!.blob.text(),"voice");
});
test("IndexedDB request success is not durable success until transaction commits",async()=>{
 let tx:any;let request:any;
 const database={onversionchange:null,transaction(){tx={objectStore(){return {put(){request={result:"id",onsuccess:null,onerror:null};queueMicrotask(()=>request.onsuccess?.());return request;}};},oncomplete:null,onabort:null,onerror:null,abort(){this.onabort?.();}};return tx;}};
 const factory={open(){const open:any={result:database,onsuccess:null,onerror:null,onblocked:null,onupgradeneeded:null};queueMicrotask(()=>open.onsuccess?.());return open;}} as unknown as IDBFactory;
 let resolved=false;const put=browserVoiceDraftStore(factory).put(draft("raw")).then(()=>{resolved=true;});
 await new Promise(resolve=>setTimeout(resolve,1));assert.equal(resolved,false);tx.onabort();await assert.rejects(put,/无法可靠地保存/);assert.equal(resolved,false);
});
test("WebKit-safe IndexedDB byte storage reconstructs the exact audio and MIME",async()=>{
 let stored:any;
 const database={onversionchange:null,transaction(){const tx:any={oncomplete:null,onabort:null,onerror:null,abort(){this.onabort?.();},objectStore(){return {put(value:unknown){stored=structuredClone(value);return response("key");},get(){return response(stored);}};}};function response(result:unknown){const request:any={result,onsuccess:null,onerror:null};queueMicrotask(()=>{request.onsuccess?.();tx.oncomplete?.();});return request;}return tx;}};
 const factory={open(){const request:any={result:database,onsuccess:null,onerror:null,onblocked:null,onupgradeneeded:null};queueMicrotask(()=>request.onsuccess?.());return request;}} as unknown as IDBFactory;
 const store=browserVoiceDraftStore(factory);await store.put(draft("exact\u0000raw\u00ff bytes"));assert.equal(stored.blob,undefined);assert.ok(stored.audioBytes instanceof ArrayBuffer);const recovered=await store.get("owner-a",note.id);assert.equal(await recovered!.blob.text(),"exact\u0000raw\u00ff bytes");assert.equal(recovered!.blob.type,"audio/webm");
});
test("missing IndexedDB reports unavailability instead of pretending to save",async()=>{
 const absent={open(){throw new Error("disabled");}} as unknown as IDBFactory;
 await assert.rejects(browserVoiceDraftStore(absent).put(draft("raw")),/无法可靠地保存/);
});
