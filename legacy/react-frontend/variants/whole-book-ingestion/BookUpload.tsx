import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../src/api/client";
import type { BookPreparation, BookStatus } from "../../src/types/api";
import { captureStorage } from "../../src/components/bookContext";
import { parsePendingUpload, prepareAndClaimBook, type PendingBookUpload } from "../../src/components/bookUploadFlow";
import { DetailSheet } from "../../src/components/DetailSheet";
import { Icon } from "../../src/components/Icon";

const TASK_KEY = "cloudpath.pending-book-upload";

export function BookUpload({open,onOpen,onClose,onAdded}:{open:boolean;onOpen:()=>void;onClose:()=>void;onAdded:()=>Promise<void>}) {
  const [file,setFile]=useState<File|null>(null),[status,setStatus]=useState<BookStatus|null>(null);
  const [preparation,setPreparation]=useState<BookPreparation|null>(null);
  const [phase,setPhase]=useState<"idle"|"uploading"|"processing"|"structuring"|"finishing"|"done"|"error">("idle");
  const [message,setMessage]=useState(""),[error,setError]=useState("");
  const storage=useMemo(captureStorage,[]);
  const pending=useRef<PendingBookUpload|null>(null);
  const run=useRef<AbortController|null>(null);
  const added=useRef(onAdded);added.current=onAdded;
  const working=!(["idle","done","error"] as string[]).includes(phase);
  const progress=phase==="uploading"?4:phase==="structuring"?(preparation?.stage==="structuring"?94:90):phase==="finishing"?97:phase==="done"?100:Math.max(7,Math.min(88,Math.round((status?.progress??0)*88)));
  const readingFailed=status?.status==="failed"||status?.status==="ocr_review_required";
  const canRetry=pending.current?.stage==="preparing"||Boolean(pending.current&&(!readingFailed||status?.retryable))||Boolean(!pending.current&&file);

  function saveTask(task:PendingBookUpload){pending.current=task;storage.safeSet(TASK_KEY,JSON.stringify(task));}
  function current(controller:AbortController){return run.current===controller&&!controller.signal.aborted;}
  function beginRun(){run.current?.abort();const controller=new AbortController();run.current=controller;return controller;}

  async function watch(task:PendingBookUpload,controller:AbortController,retry=false) {
    setPhase(task.stage==="preparing"?"structuring":"processing");
    setMessage(task.stage==="preparing"?"正在继续整理教材…":"正在读取教材…");
    const claimed=await prepareAndClaimBook(task,{
      api,signal:controller.signal,
      retryPreparation:retry&&task.stage==="preparing",
      retryReading:retry&&task.stage==="reading"&&Boolean(status?.retryable),
      onReading:next=>{setStatus(next);setMessage((next.progress??0)<.72?"正在读取教材…":"正在整理教材…");},
      onPreparing:()=>{saveTask({bookId:task.bookId,stage:"preparing"});setPhase("structuring");setMessage("正在整理教材内容…");},
      onPreparation:job=>{setPreparation(job);setPhase(job.stage==="diagnostics"||job.stage==="ready"?"finishing":"structuring");setMessage(job.stage==="diagnostics"?"正在准备练习…":job.stage==="ready"?"正在加入书架…":"正在整理教材内容…");},
    });
    if(!current(controller))return;
    await added.current();
    if(!current(controller))return;
    pending.current=null;storage.safeSet(TASK_KEY,null);
    setPhase("done");setMessage(`《${claimed.title}》已准备好。`);
  }

  function showError(failure:unknown,controller:AbortController){
    if(current(controller)){setPhase("error");setMessage(pending.current?"教材已保存，可以重试后继续准备。":"上传没有完成，请重试。");setError(failure instanceof Error?failure.message:"这次没有处理完成，请重试。");}
  }

  useEffect(()=>{
    const task=parsePendingUpload(storage.safeGet(TASK_KEY));
    if(task){pending.current=task;const controller=beginRun();void watch(task,controller).catch(failure=>showError(failure,controller));}
    return()=>{run.current?.abort();run.current=null;};
  },[]);

  async function start(retry=false) {
    if(!file&&!pending.current)return;
    const controller=beginRun();setError("");setPreparation(null);
    try {
      let task=retry?pending.current:null;
      if(!task){
        if(!file)return;
        setPhase("uploading");setMessage("正在上传…");
        const uploaded=await api.uploadBook(file,controller.signal);
        if(!current(controller))return;
        await api.bindUpload(uploaded.book_id,controller.signal);
        if(!current(controller))return;
        task={bookId:uploaded.book_id,stage:"reading"};saveTask(task);
      }
      await watch(task,controller,retry);
    } catch(failure) { showError(failure,controller); }
  }

  function reset(){run.current?.abort();run.current=null;pending.current=null;storage.safeSet(TASK_KEY,null);setFile(null);setStatus(null);setPreparation(null);setPhase("idle");setMessage("");setError("");}
  return <>
    <button className="library-upload-entry" onClick={onOpen}><span className="upload-entry-icon"><Icon name="upload" size={22}/></span><span><strong>上传教材</strong><small>{working?`${message} · ${progress}%`:phase==="done"?"教材已准备好":phase==="error"?"准备未完成，点击查看":"支持 PDF 与扫描版"}</small></span><Icon name="arrow" size={17}/></button>
    {open&&<DetailSheet title="上传一本新书" onClose={onClose}><div className="book-upload-flow">
      <div className="upload-orbit" style={{"--upload-progress":`${progress}%`} as React.CSSProperties}><span><Icon name={phase==="done"?"check":"upload"} size={28}/></span></div>
      <div className="upload-copy"><h3>{phase==="done"?"准备完成":phase==="error"?"准备未完成":working?"正在准备教材":"上传教材"}</h3><p>{message||"上传后即可按章节学习和答疑。"}</p></div>
      {phase==="idle"&&<label className="book-file-picker"><Icon name="book"/><span><strong>{file?.name||"选择 PDF 文件"}</strong><small>{file?`${(file.size/1024/1024).toFixed(1)} MB · 可以开始上传`:"扫描版和可复制文字的 PDF 都可以"}</small></span><input aria-label="选择要上传的 PDF" type="file" accept="application/pdf,.pdf" onChange={event=>{const picked=event.target.files?.[0]??null;setError("");if(picked&&!picked.name.toLowerCase().endsWith(".pdf")){setFile(null);setError("请选择 PDF 文件。");}else setFile(picked);}}/></label>}
      {working&&<div className="upload-progress" role="status" aria-live="polite"><span style={{width:`${progress}%`}}/><b>{progress}%</b><small>{status?.page_count?`共 ${status.page_count} 页 · `:""}可以稍后回来查看</small></div>}
      {error&&<p className="community-error" role="alert">{error}</p>}
      {phase==="idle"&&<button className="primary sheet-primary" disabled={!file} onClick={()=>void start()}>开始上传 <Icon name="arrow"/></button>}
      {phase==="error"&&<div className="upload-recovery"><button className="primary" disabled={!canRetry} onClick={()=>void start(true)}>重试</button><button className="secondary" onClick={reset}>重新选择</button></div>}
      {phase==="done"&&<div className="upload-recovery"><button className="primary" onClick={onClose}>打开书架</button><button className="secondary" onClick={reset}>继续上传</button></div>}
    </div></DetailSheet>}
  </>;
}
