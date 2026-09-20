import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { BookStatus } from "../types/api";
import { DetailSheet } from "./DetailSheet";
import { Icon } from "./Icon";

const delay=(milliseconds:number)=>new Promise(resolve=>window.setTimeout(resolve,milliseconds));
const TASK_KEY="cloudpath.pending-book-upload";

export function BookUpload({open,onOpen,onClose,onAdded}:{open:boolean;onOpen:()=>void;onClose:()=>void;onAdded:()=>Promise<void>}) {
  const [file,setFile]=useState<File|null>(null),[status,setStatus]=useState<BookStatus|null>(null);
  const [phase,setPhase]=useState<"idle"|"uploading"|"processing"|"structuring"|"finishing"|"done"|"error">("idle");
  const [message,setMessage]=useState(""),[error,setError]=useState("");
  const alive=useRef(true);
  const working=!(["idle","done","error"] as string[]).includes(phase);
  const progress=phase==="uploading"?4:phase==="structuring"?92:phase==="finishing"?97:phase==="done"?100:Math.max(7,Math.min(88,Math.round((status?.progress??0)*88)));

  useEffect(()=>{
    alive.current=true;
    const pending=window.localStorage.getItem(TASK_KEY);
    if(pending){
      try{
        const {bookId}=JSON.parse(pending) as {bookId:string};
        if(bookId){
          setPhase("processing");setMessage("正在继续上次的解析任务…");
          void watch(bookId).catch(e=>{if(alive.current){setPhase("error");setError((e as Error).message);}});
        }
      }catch{window.localStorage.removeItem(TASK_KEY);}
    }
    return()=>{alive.current=false;};
  },[]);

  async function finish(bookId:string) {
    setPhase("structuring");setMessage("正在整理章节和全书摘要…");
    await api.buildStructure(bookId);
    const claimed=await api.claimBook(bookId);
    await onAdded();
    setPhase("finishing");setMessage("正在准备个性化诊断题…");
    let diagnosticWarning=false;
    try { await api.buildDiagnostics(bookId); } catch { diagnosticWarning=true; }
    if(!alive.current)return;
    window.localStorage.removeItem(TASK_KEY);
    setPhase("done");setMessage(diagnosticWarning?`《${claimed.title}》已加入书架；诊断题会稍后继续准备。`:`《${claimed.title}》已准备好，可以开始学习了。`);
  }

  async function watch(bookId:string) {
    while(alive.current) {
      const next=await api.bookStatus(bookId);setStatus(next);setMessage(next.current_step||"正在解析教材…");
      if(next.status==="ocr_ready"){await finish(bookId);return;}
      if(next.status==="structured"){const claimed=await api.claimBook(bookId);await onAdded();window.localStorage.removeItem(TASK_KEY);setPhase("done");setMessage(`《${claimed.title}》已在你的书架中。`);return;}
      if(next.status==="failed"||next.status==="ocr_review_required")throw new Error(next.status==="ocr_review_required"?"部分页面需要重新识别，请点击重试。":"解析没有完成，请点击重试。");
      await delay(2000);
    }
  }

  async function start(retry=false) {
    if(!file&&!retry)return;
    setError("");
    try {
      let bookId=status?.book_id;
      if(!retry){setPhase("uploading");setMessage("正在安全上传到云端…");const uploaded=await api.uploadBook(file!);bookId=uploaded.book_id;await api.bindUpload(bookId);window.localStorage.setItem(TASK_KEY,JSON.stringify({bookId,filename:file!.name}));setPhase("processing");setMessage("已进入解析队列…");setStatus(await api.processBook(bookId));}
      else if(bookId){setPhase("processing");setMessage("正在重新解析需要复核的页面…");setStatus(await api.retryBook(bookId));}
      if(!bookId)throw new Error("上传任务没有正确创建，请重新选择文件。");
      await watch(bookId);
    } catch(e) { if(alive.current){setPhase("error");setError((e as Error).message);} }
  }

  function reset(){window.localStorage.removeItem(TASK_KEY);setFile(null);setStatus(null);setPhase("idle");setMessage("");setError("");}
  return <>
    <button className="library-upload-entry" onClick={onOpen}><span className="upload-entry-icon"><Icon name="upload" size={22}/></span><span><strong>上传一本书</strong><small>{working?`${message} · ${progress}%`:phase==="done"?"刚刚的教材已经准备好":"PDF 会在云端解析，不占手机空间"}</small></span><Icon name="arrow" size={17}/></button>
    {open&&<DetailSheet title="上传一本新书" onClose={onClose}><div className="book-upload-flow">
      <div className="upload-orbit" style={{"--upload-progress":`${progress}%`} as React.CSSProperties}><span><Icon name={phase==="done"?"check":"upload"} size={28}/></span></div>
      <div className="upload-copy"><h3>{phase==="done"?"新书已经准备好":working?"正在把书变成课程":"从 PDF 开始"}</h3><p>{message||"选择一本 PDF，我们会自动识别文字、整理章节并加入你的书架。"}</p></div>
      {phase==="idle"&&<label className="book-file-picker"><Icon name="book"/><span><strong>{file?.name||"选择 PDF 文件"}</strong><small>{file?`${(file.size/1024/1024).toFixed(1)} MB · 可以开始上传`:"扫描版和可复制文字的 PDF 都可以"}</small></span><input aria-label="选择要上传的 PDF" type="file" accept="application/pdf,.pdf" onChange={event=>{const picked=event.target.files?.[0]??null;setError("");if(picked&&!picked.name.toLowerCase().endsWith(".pdf")){setFile(null);setError("请选择 PDF 文件。");}else setFile(picked);}}/></label>}
      {working&&<div className="upload-progress" role="status" aria-live="polite"><span style={{width:`${progress}%`}}/><b>{progress}%</b><small>{status?.page_count?`共 ${status.page_count} 页 · `:""}你可以关闭窗口，解析会继续进行</small></div>}
      {error&&<p className="community-error" role="alert">{error}</p>}
      {phase==="idle"&&<button className="primary sheet-primary" disabled={!file} onClick={()=>void start()}>上传并开始解析 <Icon name="arrow"/></button>}
      {phase==="error"&&<div className="upload-recovery"><button className="primary" disabled={!status?.retryable} onClick={()=>void start(true)}>重新解析</button><button className="secondary" onClick={reset}>重新选择文件</button></div>}
      {phase==="done"&&<div className="upload-recovery"><button className="primary" onClick={onClose}>去书架看看</button><button className="secondary" onClick={reset}>继续上传</button></div>}
    </div></DetailSheet>}
  </>;
}
