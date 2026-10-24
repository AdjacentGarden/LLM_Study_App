import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { BookStatus } from "../types/api";
import { captureStorage } from "./bookContext";
import { DetailSheet } from "./DetailSheet";
import { Icon } from "./Icon";
import { resumeUpload, UploadFlowCancelled, UploadStepError } from "./uploadFlow";

const delay = (milliseconds: number) => new Promise<void>(resolve => window.setTimeout(resolve, milliseconds));
const TASK_KEY = "cloudpath.pending-book-upload";
const stageLabels: Record<string, string> = {
  processing: "正在读取教材…", queued: "教材整理已排队…", indexing: "正在建立教材检索…",
  structuring: "正在整理章节与知识点…", diagnostics: "正在生成并审核诊断题…",
  ready: "正在核验学习内容…", claiming: "正在加入书架…",
};
type PendingUpload = { bookId: string; filename: string };
type Phase = "idle" | "uploading" | "processing" | "preparing" | "done" | "error";

export function BookUpload({ open, onOpen, onClose, onAdded }: {
  open: boolean; onOpen: () => void; onClose: () => void; onAdded: () => Promise<void>;
}) {
  const storage = useMemo(captureStorage, []);
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<BookStatus | null>(null);
  const [pending, setPending] = useState<PendingUpload | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [stage, setStage] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [canRetryOCR, setCanRetryOCR] = useState(false);
  const alive = useRef(true), running = useRef(false), generation = useRef(0);
  const phaseRef = useRef(phase), pendingRef = useRef(pending), onAddedRef = useRef(onAdded);
  phaseRef.current = phase; pendingRef.current = pending; onAddedRef.current = onAdded;
  const working = !["idle", "done", "error"].includes(phase);
  const progress = phase === "done" ? 100 : phase === "processing" && status && Number.isFinite(status.progress)
    ? Math.max(0, Math.min(100, Math.round(status.progress * 100))) : null;

  function persist(value: PendingUpload | null) {
    pendingRef.current = value;
    setPending(value);
    storage.safeSet(TASK_KEY, value ? JSON.stringify(value) : null);
  }

  async function run(existing: PendingUpload | null, retryFailedOCR = false, retryPreparation = false) {
    if (running.current || (!existing && !file)) return;
    running.current = true;
    const serial = ++generation.current;
    const active = () => alive.current && serial === generation.current;
    setError(""); setCanRetryOCR(false);
    if (existing) { setPhase("processing"); setMessage("正在恢复教材处理状态…"); }
    try {
      let task = existing;
      if (!task) {
        setPhase("uploading"); setMessage("正在上传…");
        const uploaded = await api.uploadBook(file!);
        task = { bookId: uploaded.book_id, filename: file!.name };
        // A tab/account switch may unmount this view while the server finishes
        // the upload. Retain the receipt in its captured account scope first.
        storage.safeSet(TASK_KEY, JSON.stringify(task));
        if (!active()) return;
        // Persist before bind/process: a lost response can be reconciled using this ID.
        persist(task);
      }
      const claimed = await resumeUpload(task.bookId, {
        api, active, wait: delay, retryFailedOCR, retryPreparation,
        onStatus: value => { if (active()) setStatus(value); },
        onStage: value => {
          if (!active()) return;
          setStage(value);
          setPhase(value === "processing" ? "processing" : "preparing");
          setMessage(stageLabels[value] ?? "正在整理教材…");
        },
      });
      if (!active()) return;
      await onAddedRef.current();
      if (!active()) return;
      persist(null);
      setPhase("done"); setMessage(`《${claimed.title}》已准备好，可以开始诊断与学习。`);
    } catch (value) {
      if (!active() || value instanceof UploadFlowCancelled) return;
      setPhase("error"); setError((value as Error).message);
      if (value instanceof UploadStepError) { setStage(value.stage); setCanRetryOCR(value.retryOCR); }
    } finally {
      if (serial === generation.current) running.current = false;
    }
  }

  useEffect(() => {
    alive.current = true;
    const saved = storage.safeGet(TASK_KEY);
    if (saved) {
      try {
        const value = JSON.parse(saved) as PendingUpload;
        if (typeof value.bookId !== "string" || !value.bookId) throw new Error("invalid pending upload");
        persist(value); void run(value);
      } catch { storage.safeSet(TASK_KEY, null); }
    }
    const online = () => {
      if (phaseRef.current === "error" && pendingRef.current && !running.current) {
        // Reconnect to saved state; failed model jobs need an explicit retry click.
        void run(pendingRef.current);
      }
    };
    window.addEventListener("online", online);
    return () => {
      alive.current = false; ++generation.current; running.current = false;
      window.removeEventListener("online", online);
    };
  }, [storage]);

  function reset() {
    if (running.current) return;
    persist(null); setFile(null); setStatus(null); setPhase("idle"); setStage("");
    setMessage(""); setError(""); setCanRetryOCR(false);
  }

  return <>
    <button className="library-upload-entry" onClick={onOpen}>
      <span className="upload-entry-icon"><Icon name="upload" size={22}/></span>
      <span><strong>上传教材</strong><small>{working ? `${message}${progress === null ? "" : ` · 读取阶段 ${progress}%`}` : phase === "done" ? "教材已准备好" : phase === "error" && pending ? "处理已暂停，点击继续" : "支持 PDF 与扫描版"}</small></span><Icon name="arrow" size={17}/>
    </button>
    {open && <DetailSheet title="上传一本新书" onClose={onClose}><div className="book-upload-flow">
      <div className="upload-orbit" style={{ "--upload-progress": `${progress ?? 0}%` } as React.CSSProperties}><span><Icon name={phase === "done" ? "check" : "upload"} size={28}/></span></div>
      <div className="upload-copy"><h3>{phase === "done" ? "准备完成" : working ? "正在准备教材" : phase === "error" ? "暂未完成" : "上传教材"}</h3><p>{phase === "error" ? pending ? "已保留当前教材与处理进度。恢复连接后可继续整理，无须重新上传。" : "上传尚未确认完成，请恢复连接后重新选择文件。" : message || "上传后整理章节、检索与诊断题，再开始学习。"}</p></div>
      {phase === "idle" && <label className="book-file-picker"><Icon name="book"/><span><strong>{file?.name || "选择 PDF 文件"}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · 可以开始上传` : "扫描版和可复制文字的 PDF 都可以"}</small></span><input aria-label="选择要上传的 PDF" type="file" accept="application/pdf,.pdf" onChange={event => {
        const picked = event.target.files?.[0] ?? null; setError("");
        if (picked && !picked.name.toLowerCase().endsWith(".pdf")) { setFile(null); setError("请选择 PDF 文件。"); } else setFile(picked);
      }}/></label>}
      {working && <div className="upload-progress" role="status" aria-live="polite">
        <progress max={100} value={progress ?? undefined} aria-label={progress === null ? "正在处理教材" : "教材读取阶段进度"} style={{ gridColumn: 1, gridRow: 1, width: "100%", height: 7 }}/>
        <b>{progress === null ? "处理中" : `${progress}%`}</b>
        <small>{status?.page_count ? `共 ${status.page_count} 页 · ` : ""}{progress === null ? "按当前处理阶段更新" : "仅表示教材读取阶段进度"}；可稍后回来查看</small>
      </div>}
      {error && <p className="community-error" role="alert">
        {phase === "error" && stage && <><small>处理阶段：{(stageLabels[stage] ?? "教材整理").replace(/^正在/, "").replace(/…$/, "")}</small><br/></>}
        {error}
      </p>}
      {phase === "idle" && <button className="primary sheet-primary" disabled={!file} onClick={() => void run(null)}>开始上传 <Icon name="arrow"/></button>}
      {phase === "error" && <div className="upload-recovery">
        {pending && <button className="primary" onClick={() => void run(pending, false, true)}>继续整理</button>}
        {pending && canRetryOCR && <button className="secondary" onClick={() => void run(pending, true, true)}>重新读取教材</button>}
        <button className="secondary" onClick={reset}>重新选择</button>
      </div>}
      {phase === "done" && <div className="upload-recovery"><button className="primary" onClick={onClose}>打开书架</button><button className="secondary" onClick={reset}>继续上传</button></div>}
    </div></DetailSheet>}
  </>;
}
