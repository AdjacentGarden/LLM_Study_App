import { useEffect, useRef, useState, type PointerEvent } from "react";
import { studioApi, studioId, pendingStudio, type InkNote, type InkPoint, type InkStroke, type StudioCaps, type StudioJob } from "../api/learningStudio";
import { drawInk } from "../components/inkDrawing";
import { Button } from "../legacyControls/Primitives";

type Tool = "read" | "pen" | "erase";

export function SourcePageInk({ bookId, pageNumber, imageUrl, text, title, onImageError, registerSave }: {
  bookId: string; pageNumber: number; imageUrl: string; text: string | null; title: string;
  onImageError: () => void; registerSave: (save: (() => Promise<boolean>) | null) => void;
}) {
  const [note, setNote] = useState<InkNote | null>(null);
  const [caps, setCaps] = useState<StudioCaps | null>(null);
  const [jobs, setJobs] = useState<StudioJob[]>([]);
  const [tool, setTool] = useState<Tool>("read");
  const [finger, setFinger] = useState(true);
  const [color, setColor] = useState<InkStroke["color"]>("#243148");
  const [zoom, setZoom] = useState(1);
  const [size, setSize] = useState({ width: 1000, height: 1400 });
  const [imageReady, setImageReady] = useState(false);
  const [redo, setRedo] = useState<InkStroke[]>([]);
  const [message, setMessage] = useState("正在读取本页笔记…");
  const [error, setError] = useState("");
  const [transcript, setTranscript] = useState("");
  const [accepted, setAccepted] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef<InkNote | null>(null);
  const active = useRef<InkStroke | null>(null);
  const pointer = useRef<number | null>(null);
  const dirty = useRef(false);
  const savePromise = useRef<Promise<InkNote> | null>(null);
  const draftKey = useRef("");
  const requestId = useRef(studioId());
  const alive = useRef(true);

  useEffect(() => {
    let cancelled = false;
    alive.current = true;
    setNote(null); setError(""); setMessage("正在读取本页笔记…");
    void Promise.all([studioApi.sourcePage(bookId, pageNumber), studioApi.caps(), studioApi.jobs(bookId)])
      .then(([serverNote, capabilities, jobPage]) => {
        if (cancelled) return;
        const key = `zhiwo.studio.${capabilities.draft_scope}.${serverNote.id}`;
        draftKey.current = key;
        let current = serverNote.revision === 0 && text ? { ...serverNote, excerpt: text.slice(0, 3000) } : serverNote;
        let restored = false;
        try {
          const raw = sessionStorage.getItem(key);
          if (raw) {
            const draft = JSON.parse(raw) as InkNote;
            if (draft.id === serverNote.id && draft.book_id === bookId && draft.source_page_number === pageNumber && Array.isArray(draft.strokes)) {
              current = draft;
              dirty.current = true;
              restored = true;
            }
          }
        } catch { /* The server copy is still available. */ }
        if (!restored) dirty.current = false;
        latest.current = current;
        setNote(current); setCaps(capabilities); setJobs(jobPage.items);
        setMessage(restored ? "已恢复未同步草稿" : serverNote.revision ? "本页标注已加载" : "本页尚无笔迹");
      })
      .catch(value => { if (!cancelled) { setError((value as Error).message); setMessage("笔记加载失败，原文仍可阅读"); } });
    return () => { cancelled = true; alive.current = false; if (dirty.current) void save().catch(() => {}); };
  }, [bookId, pageNumber, reload]);

  const relevant = jobs.filter(job => note !== null && job.note_id === note.id && job.revision === note.revision);
  const working = relevant.some(pendingStudio);
  const recognition = relevant.find(job => job.kind === "complete" && job.status === "needs_confirmation");
  const improvement = relevant.find(job => (job.kind === "complete" || job.kind === "improve") && job.status === "succeeded");
  useEffect(() => {
    if (recognition?.result.transcript) setTranscript(recognition.result.transcript);
  }, [recognition?.id]);
  useEffect(() => {
    if (!working) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      void studioApi.jobs(bookId).then(page => { if (!cancelled) setJobs(page.items); }).catch(() => {});
    }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [working, bookId]);
  useEffect(() => { drawInk(canvas.current, note?.strokes ?? [], active.current); }, [note?.strokes, size, imageReady]);
  useEffect(() => {
    if (!note || !dirty.current) return;
    const timer = window.setTimeout(() => { void save().catch(() => {}); }, 1200);
    return () => window.clearTimeout(timer);
  }, [note]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    registerSave(async () => { if (!dirty.current) return true; try { await save(); return true; } catch { return false; } });
    return () => { window.removeEventListener("beforeunload", guard); registerSave(null); };
  }, [registerSave]);

  function change(next: InkNote) {
    latest.current = next; dirty.current = true; requestId.current = studioId(); setNote(next); setError("");
    try { if (draftKey.current) sessionStorage.setItem(draftKey.current, JSON.stringify(next)); setMessage("草稿已暂存"); }
    catch { setMessage("暂存失败，请先不要关闭页面"); }
  }
  async function save(): Promise<InkNote> {
    if (savePromise.current) {
      await savePromise.current;
      if (dirty.current) return save();
      if (!latest.current) throw new Error("笔记尚未加载");
      return latest.current;
    }
    const snapshot = latest.current;
    if (!snapshot) throw new Error("笔记尚未加载");
    if (!dirty.current) return snapshot;
    if (alive.current) setMessage("正在保存本页笔迹…");
    const task = studioApi.save(snapshot);
    savePromise.current = task;
    try {
      const saved = await task;
      const changedDuringSave = latest.current !== snapshot;
      const next = { ...latest.current!, revision: saved.revision };
      latest.current = next;
      dirty.current = changedDuringSave;
      if (alive.current) { setNote(next); setMessage(changedDuringSave ? "还有笔迹待保存" : "本页笔迹已保存"); setError(""); }
      try {
        if (draftKey.current) {
          if (changedDuringSave) sessionStorage.setItem(draftKey.current, JSON.stringify(next));
          else sessionStorage.removeItem(draftKey.current);
        }
      } catch { /* A successful server save does not depend on browser storage. */ }
      return next;
    } catch (value) {
      if (alive.current) { setError((value as Error).message); setMessage("尚未保存，草稿仍保留"); }
      throw value;
    } finally { savePromise.current = null; }
  }
  function point(event: PointerEvent<HTMLCanvasElement>): InkPoint {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1000, Math.max(0, (event.clientX - bounds.left) / bounds.width * 1000)),
      y: Math.min(1400, Math.max(0, (event.clientY - bounds.top) / bounds.height * 1400)),
      p: event.pressure || 0.5,
    };
  }
  function eraseAt(position: InkPoint) {
    const current = latest.current;
    if (!current) return;
    const targetX = position.x * size.width / 1000, targetY = position.y * size.height / 1400;
    const strokes = current.strokes.filter(stroke => !stroke.points.some((point, index) => {
      const previous = stroke.points[Math.max(0, index - 1)];
      const x1 = previous.x * size.width / 1000, y1 = previous.y * size.height / 1400;
      const x2 = point.x * size.width / 1000, y2 = point.y * size.height / 1400;
      const lengthSquared = (x2 - x1) ** 2 + (y2 - y1) ** 2;
      const t = lengthSquared ? Math.max(0, Math.min(1, ((targetX - x1) * (x2 - x1) + (targetY - y1) * (y2 - y1)) / lengthSquared)) : 0;
      return Math.hypot(targetX - x1 - t * (x2 - x1), targetY - y1 - t * (y2 - y1)) < 22;
    }));
    if (strokes.length !== current.strokes.length) { setRedo([]); change({ ...current, strokes }); }
  }
  function down(event: PointerEvent<HTMLCanvasElement>) {
    if (tool === "read" || !latest.current || busy || pointer.current !== null || (!finger && event.pointerType !== "pen")) return;
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); pointer.current = event.pointerId;
    const position = point(event);
    if (tool === "erase") { eraseAt(position); return; }
    const current = latest.current;
    if (current.strokes.length >= 1500 || current.strokes.reduce((sum, stroke) => sum + stroke.points.length, 0) >= 57000) {
      setError("这一页已写满，请新建一页普通笔记"); pointer.current = null; event.currentTarget.releasePointerCapture(event.pointerId); return;
    }
    active.current = { points: [position], color, width: 5 };
    drawInk(canvas.current, current.strokes, active.current);
  }
  function move(event: PointerEvent<HTMLCanvasElement>) {
    if (pointer.current !== event.pointerId) return;
    if (tool === "erase") { eraseAt(point(event)); return; }
    if (!active.current || active.current.points.length >= 3000) return;
    active.current.points.push(point(event));
    drawInk(canvas.current, latest.current?.strokes ?? [], active.current);
  }
  function end(event: PointerEvent<HTMLCanvasElement>) {
    if (pointer.current !== event.pointerId) return;
    if (active.current && latest.current) { change({ ...latest.current, strokes: [...latest.current.strokes, active.current] }); setRedo([]); }
    active.current = null; pointer.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  async function analyze(action: "complete" | "improve") {
    if (busy || working) return;
    setBusy(true); setError("");
    try {
      const saved = await save();
      const job = await studioApi.analyze(saved, action, transcript, requestId.current);
      setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)]);
      requestId.current = studioId();
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(false); }
  }
  async function accept(job: StudioJob, value: boolean) {
    if (!latest.current) return;
    setBusy(true);
    try { await studioApi.edition(latest.current, job.id, value); setAccepted(value ? job.id : ""); setMessage(value ? "整理版已保留，原页笔迹未改变" : "整理版已撤回"); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  async function preserveDraft() {
    if (!latest.current) return;
    setBusy(true);
    try {
      await studioApi.save({ ...latest.current, id: studioId(), revision: 0, surface: "blank", source_page_number: null, source_pdf_sha256: null, title: `${title.slice(0, 65)} · 标注草稿副本` });
      setMessage("草稿已另存为普通手写笔记");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(false); }
  }

  return <div className="next-source-annotation">
    <div className="next-source-ink-toolbar" aria-label="原文手写工具">
      <button aria-pressed={tool === "read"} onClick={() => setTool("read")}>阅读／移动</button>
      <button aria-pressed={tool === "pen"} disabled={!note} onClick={() => setTool("pen")}>手写</button>
      <button aria-pressed={tool === "erase"} disabled={!note} onClick={() => setTool("erase")}>擦除笔画</button>
      <button disabled={!note?.strokes.length || busy} onClick={() => { if (!latest.current) return; const strokes = latest.current.strokes; setRedo(previous => [...previous, strokes[strokes.length - 1]]); change({ ...latest.current, strokes: strokes.slice(0, -1) }); }}>撤销</button>
      <button disabled={!redo.length || busy} onClick={() => { if (!latest.current) return; change({ ...latest.current, strokes: [...latest.current.strokes, redo[redo.length - 1]] }); setRedo(previous => previous.slice(0, -1)); }}>重做</button>
      <label>笔色 <select aria-label="笔色" value={color} onChange={event => setColor(event.target.value as InkStroke["color"])}><option value="#243148">墨色</option><option value="#7655c9">紫色</option><option value="#23836e">绿色</option></select></label>
      <label><input type="checkbox" checked={finger} onChange={event => setFinger(event.target.checked)}/> 手指书写</label>
      <button disabled={zoom === 1} onClick={() => setZoom(1)}>100%</button><button disabled={zoom === 1.5} onClick={() => setZoom(1.5)}>150%</button>
    </div>
    <div className="next-source-ink-viewport">
      <figure className="next-source-image next-source-page-surface" style={{ width: `${zoom * 100}%` }}>
        <div className="next-source-page-layers">
          <img src={imageUrl} alt={`《${title}》PDF 第 ${pageNumber} 页`} onError={onImageError} onLoad={event => { const image = event.currentTarget; const scale = Math.min(1, 1400 / Math.max(image.naturalWidth, image.naturalHeight)); setSize({ width: Math.max(1, Math.round(image.naturalWidth * scale)), height: Math.max(1, Math.round(image.naturalHeight * scale)) }); setImageReady(true); }}/>
          {imageReady && note && <canvas ref={canvas} width={size.width} height={size.height} aria-label={`PDF 第 ${pageNumber} 页手写标注层`} style={{ touchAction: tool === "read" ? "auto" : "none", pointerEvents: tool === "read" ? "none" : "auto" }} onPointerDown={down} onPointerMove={move} onPointerUp={end} onPointerCancel={end}/>}
        </div>
        <figcaption>PDF 第 {pageNumber} 页 · 笔迹单独保存，原文不会改动</figcaption>
      </figure>
    </div>
    <div className="next-source-ink-status"><span role="status">{message}</span><Button disabled={!note || busy || !imageReady} onClick={() => void save().catch(() => {})}>保存本页笔记</Button></div>
    {error && <div className="next-source-ink-error" role="alert"><p>{error}</p><button onClick={() => setReload(value => value + 1)}>重新读取</button>{note && <button disabled={busy} onClick={() => void preserveDraft()}>另存草稿副本</button>}</div>}
    {note && <section className="next-source-ink-ai"><h2>整理本页手写笔记</h2><p>智能整理会对照原文；原始笔迹始终保留。</p><Button disabled={!caps?.notes_ai || !note.strokes.length || busy || working} onClick={() => void analyze("complete")}>{working ? "正在整理…" : "完成并整理"}</Button>{!caps?.notes_ai && <small>智能整理暂不可用，仍可保存手写笔记。</small>}
      {recognition && !improvement && <div><p>请核对字迹识别结果：</p><textarea aria-label="核对识别文字" value={transcript} onChange={event => { setTranscript(event.target.value); requestId.current = studioId(); }} rows={6}/><p>{recognition.result.uncertain?.join("；")}</p><Button disabled={busy || transcript.trim().length < 2 || transcript.includes("[待确认]")} onClick={() => void analyze("improve")}>确认并继续整理</Button></div>}
      {improvement && <div className="next-source-ink-result"><p>{improvement.result.summary}</p>{improvement.result.suggestions?.map((suggestion, index) => <article key={index}><strong>{suggestion.kind}</strong><p>{suggestion.suggestion}</p><small>教材第 {suggestion.page} 页：{suggestion.evidence}</small></article>)}<details open><summary>整理后的复习页</summary><p>{improvement.result.polished}</p></details><Button disabled={busy} onClick={() => void accept(improvement, accepted !== improvement.id)}>{accepted === improvement.id ? "撤回整理版" : "保留整理版（原笔迹不变）"}</Button></div>}
    </section>}
  </div>;
}
