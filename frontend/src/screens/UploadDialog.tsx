import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, FileText, Plus, X } from "lucide-react";
import { captureStorage } from "../components/bookContext";
import { Button } from "../legacyControls/Primitives";
import { ParseReadyPanel, ProcessingPanel, UploadCompletePanel } from "../legacyControls/UploadFlowPanels";
import type { BookStatus } from "../types/api";
import type { StudyRepository } from "../services/contracts";

const pendingKey = "cloudpath.next.pending-upload";
type TaskStage = "awaiting_parse" | "uploaded" | "bound" | "processing" | "structuring" | "claiming" | "diagnostics";
type Task = { bookId: string; filename: string; stage: TaskStage; sizeBytes?: number; canonicalId?: string; canonicalTitle?: string };
type Phase = "idle" | "uploading" | TaskStage | "done" | "error";
type Storage = ReturnType<typeof captureStorage>;
const wait = (milliseconds: number) => new Promise<void>(resolve => window.setTimeout(resolve, milliseconds));

export function readPendingTask(storage: Pick<Storage, "safeGet">): Task | null {
  try {
    const raw = storage.safeGet(pendingKey);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<Task>;
    if (typeof value.bookId !== "string" || !value.bookId || typeof value.filename !== "string" || !value.filename) return null;
    const stage: TaskStage = value.stage && ["awaiting_parse", "uploaded", "bound", "processing", "structuring", "claiming", "diagnostics"].includes(value.stage) ? value.stage : "processing";
    return { bookId: value.bookId, filename: value.filename, stage,
      sizeBytes: typeof value.sizeBytes === "number" && Number.isFinite(value.sizeBytes) && value.sizeBytes >= 0 ? value.sizeBytes : undefined,
      canonicalId: value.canonicalId, canonicalTitle: value.canonicalTitle };
  } catch { return null; }
}

export function UploadDialog({ open, onClose, onClaimed, repository }: {
  open: boolean; onClose: () => void; onClaimed: (bookId: string) => Promise<void>; repository: StudyRepository;
}) {
  // AccountGate remounts this component on identity change. captureStorage holds
  // that identity, so even a late continuation cannot write another account.
  const storage = useMemo(captureStorage, []);
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [task, setTask] = useState<Task | null>(() => readPendingTask(storage));
  const [status, setStatus] = useState<BookStatus | null>(null);
  const [phase, setPhase] = useState<Phase>(() => readPendingTask(storage)?.stage || "idle");
  const [message, setMessage] = useState("");
  const [completedTitle, setCompletedTitle] = useState("");
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const mounted = useRef(false);
  const generation = useRef(0);
  const running = useRef(false);
  const taskRef = useRef(task);
  taskRef.current = task;
  const claimedRef = useRef(onClaimed);
  claimedRef.current = onClaimed;

  const live = (ticket: number) => mounted.current && generation.current === ticket;
  const persist = useCallback((next: Task, ticket: number) => {
    if (!live(ticket)) return;
    storage.safeSet(pendingKey, JSON.stringify(next));
    setTask(next); setPhase(next.stage);
  }, [storage]);

  const processTask = useCallback(async (initial: Task, ticket: number, retryOcr = false) => {
    let current = initial;
    const save = (stage: TaskStage, more: Partial<Task> = {}) => { current = { ...current, ...more, stage }; persist(current, ticket); };
    if (current.stage === "uploaded") {
      if (live(ticket)) setMessage("正在绑定到当前账号…");
      await repository.library.bind(current.bookId);
      if (!live(ticket)) return;
      save("bound");
    }

    if (!current.canonicalId) {
      let next = await repository.library.status(current.bookId);
      if (!live(ticket)) return;
      setStatus(next);
      if (next.status === "uploaded") {
        save("processing"); setMessage("已进入解析队列…");
        next = await repository.library.process(current.bookId);
        if (!live(ticket)) return;
        setStatus(next);
      } else if ((next.status === "failed" || next.status === "ocr_review_required") && retryOcr) {
        if (!next.retryable) throw new Error("当前 OCR 状态不可重试，请联系服务管理员。");
        save("processing"); setMessage("正在重新识别需要复核的页面…");
        next = await repository.library.process(current.bookId, true);
        if (!live(ticket)) return;
        setStatus(next);
      }
      while (live(ticket) && !["ocr_ready", "structured", "failed", "ocr_review_required"].includes(next.status)) {
        save("processing"); setMessage(next.current_step || "正在解析教材…");
        await wait(2200);
        if (!live(ticket)) return;
        next = await repository.library.status(current.bookId);
        if (!live(ticket)) return;
        setStatus(next);
      }
      if (!live(ticket)) return;
      if (next.status === "failed" || next.status === "ocr_review_required") {
        save("processing");
        throw new Error(next.status === "ocr_review_required" ? "部分页面需要重新识别。确认后可重试 OCR。" : "OCR 解析未完成。请查看状态并重试。");
      }
      if (next.status === "ocr_ready") {
        save("structuring"); setMessage("正在整理真实章节和全书摘要…");
        await repository.library.buildStructure(current.bookId);
        if (!live(ticket)) return;
      }
      save("claiming"); setMessage("正在加入你的个人书架…");
      const claimed = await repository.library.claim(current.bookId);
      if (!live(ticket)) return;
      save("claiming", { canonicalId: claimed.book_id, canonicalTitle: claimed.title });
    }

    // Claim can canonicalize a duplicate upload to another book id. All
    // following operations use the returned id, never the temporary upload id.
    const finalId = current.canonicalId;
    if (!finalId) throw new Error("书架没有返回最终教材 ID，请继续任务重试。");
    await claimedRef.current(finalId);
    if (!live(ticket)) return;
    save("diagnostics"); setMessage("正在准备学习前的小测…");
    const ready = await repository.library.diagnostics(finalId);
    if (!live(ticket)) return;
    if (!ready) throw new Error("教材已加入书架，诊断题仍在准备。稍后可继续任务。");
    // Claim precedes diagnostic generation, so refresh again to expose the
    // backend's new diagnostics_ready flag on the selected shelf item.
    await claimedRef.current(finalId);
    if (!live(ticket)) return;
    storage.safeSet(pendingKey, null); setCompletedTitle(current.canonicalTitle || "新教材"); setTask(null); setPhase("done");
    setStatus(null); setMessage(`《${current.canonicalTitle || "新教材"}》已准备好，可以开始学习。`);
    setWarning("");
  }, [persist, repository, storage]);

  const resume = useCallback((initial: Task, retryOcr = false) => {
    if (running.current) return;
    const ticket = ++generation.current;
    running.current = true; setError(""); setWarning("");
    void processTask(initial, ticket, retryOcr).catch(value => {
      if (!live(ticket)) return;
      setPhase("error"); setError((value as Error).message);
      if (initial.canonicalId || taskRef.current?.canonicalId) setWarning("教材已加入书架，当前可以查看目录；诊断题尚未确认就绪。");
    }).finally(() => { if (live(ticket)) running.current = false; });
  }, [processTask]);

  useEffect(() => {
    mounted.current = true;
    let active = true;
    const pending = readPendingTask(storage);
    if (pending) {
      setTask(pending); setMessage(`正在继续《${pending.filename}》的任务…`);
      // StrictMode replays mount effects. Defer the network start so its
      // first setup can be cancelled before it sends a duplicate mutation.
      if (pending.stage !== "awaiting_parse") void Promise.resolve().then(() => { if (active) resume(pending); });
    }
    // Always invalidate in-flight writes, including a task started after an
    // initially empty mount. StrictMode and account switch both use cleanup.
    return () => { active = false; mounted.current = false; ++generation.current; running.current = false; };
  }, [resume, storage]);

  async function start() {
    if (!file || running.current) return;
    const ticket = ++generation.current;
    running.current = true; setError(""); setWarning(""); setPhase("uploading"); setMessage("正在上传 PDF…");
    try {
      const uploaded = await repository.library.upload(file);
      if (!live(ticket)) return;
      const next: Task = { bookId: uploaded.book_id, filename: file.name, sizeBytes: file.size, stage: "awaiting_parse" };
      persist(next, ticket);
      setMessage("文件已上传。确认后开始后端解析，离开页面也能继续。");
    } catch (value) { if (live(ticket)) { setPhase("idle"); setError((value as Error).message); } }
    finally { if (live(ticket)) running.current = false; }
  }

  function beginParse() {
    if (!task || task.stage !== "awaiting_parse" || running.current) return;
    const next: Task = { ...task, stage: "uploaded" };
    storage.safeSet(pendingKey, JSON.stringify(next));
    setTask(next); setPhase("uploaded"); setMessage("正在绑定到当前账号…");
    resume(next);
  }

  function abandon() {
    ++generation.current; running.current = false;
    storage.safeSet(pendingKey, null); setTask(null); setStatus(null); setFile(null);
    setPhase("idle"); setError(""); setMessage(""); setWarning(""); setCompletedTitle("");
  }

  if (!open) return null;
  const busy = !["idle", "awaiting_parse", "done", "error"].includes(phase);
  const progress = phase === "done" ? 100 : status ? Math.max(0, Math.min(100, Math.round(status.progress * 100))) : null;
  const ocrFailed = status?.status === "failed" || status?.status === "ocr_review_required";
  const processing = phase === "error" || (busy && phase !== "uploading");
  const screen = phase === "awaiting_parse" ? "parseReady" : phase === "done" ? "courseReady" : processing ? "processing" : "upload";
  const selectFile = (next: File | null) => {
    if (next && !(next.type === "application/pdf" || next.name.toLowerCase().endsWith(".pdf"))) { setFile(null); setError("请选择 PDF 文件。"); }
    else { setFile(next); setError(""); }
  };
  return <div className="demo-port-upload-overlay" role="dialog" aria-modal="true" aria-label="上传书籍">
    <header className="header-bar"><div className="header-glass"><button className="icon-button" type="button" aria-label="关闭上传" onClick={onClose}><ArrowLeft size={21}/></button>
      <div className="header-title"><h1>上传书籍</h1></div><div className="header-right"><div className="header-spacer"/></div></div></header>
    <div className="screen-content with-header without-nav" data-screen={screen}>
      {phase === "awaiting_parse" && task ? <ParseReadyPanel filename={task.filename} sizeBytes={task.sizeBytes} onStart={beginParse} /> : null}
      {processing && <ProcessingPanel stage={task?.stage ?? "uploaded"} status={status} progress={progress} message={message}
        error={error} warning={warning} onClose={onClose} onRetry={() => { if (task) resume(task, ocrFailed); }}
        retryDisabled={!task || (ocrFailed && status?.retryable === false)} onChoose={() => { abandon(); requestAnimationFrame(() => fileInput.current?.click()); }} />}
      {phase === "done" && <UploadCompletePanel title={completedTitle} onEnter={onClose}
        onNew={() => { abandon(); requestAnimationFrame(() => fileInput.current?.click()); }} />}
      {(phase === "idle" || phase === "uploading") && <div className="screen-stack upload-sheet-screen upload-flow-screen"><section className="upload-sheet-card upload-flow-primary">
      <input ref={fileInput} className="hidden-file-input" type="file" accept="application/pdf,.pdf" aria-label="选择 PDF 文件"
        onChange={event => { selectFile(event.target.files?.[0] ?? null); event.target.value = ""; }}/>
      {file || task ? <div className={`upload-add-tile has-selection ${busy ? "is-loading" : ""}`} role="group" aria-label="已选择 PDF 教材">
        <div className="upload-selected-file-visual"><div className="upload-selected-file-item"><span className="upload-selected-file-icon"><FileText size={30}/>
          {!busy && !task && <button className="upload-remove-file" type="button" aria-label="移除文件" onClick={() => setFile(null)}><span className="upload-remove-file-mark"><X size={14}/></span></button>}
        </span><strong>{file?.name || task?.filename}</strong></div></div></div>
        : <button className="upload-add-tile" type="button" onClick={() => fileInput.current?.click()} aria-label="选择 PDF 文件"><span className="upload-add-icon"><Plus size={38}/></span></button>}
      <div className="upload-source-copy"><div className="upload-status-feedback" aria-live="polite"><h3>{phase === "uploading" ? "正在上传文件" : "选择学习资料"}</h3>
        <p>{message || "选择一份 PDF。解析后会生成章节目录，再准备学习诊断。"}</p><small>支持单个 PDF 文件</small></div></div>
      {warning && <p className="helper-text upload-status-feedback" role="status">{warning}</p>}
      {error && <p className="helper-text upload-error status-error-copy upload-status-feedback" role="alert">{error}</p>}
      {phase === "idle" && <Button disabled={!file} onClick={() => void start()}>上传并继续</Button>}
    </section></div>}
    </div>
  </div>;
}
