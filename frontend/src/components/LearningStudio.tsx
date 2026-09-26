import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  studioApi,
  studioId,
  pendingStudio,
  type Anchor,
  type InkNote,
  type InkStroke,
  type InkPoint,
  type StudioJob,
  type StudioCaps,
} from "../api/learningStudio";

type Opening = { anchor: Anchor; mode: "image" | "video" | "notes" };
const StudioContext = createContext<(opening: Opening) => void>(() => {});
export function StudioProvider({ children }: { children: ReactNode }) {
  const [opening, setOpening] = useState<Opening | null>(null);
  return (
    <StudioContext.Provider value={setOpening}>
      {children}
      {opening && (
        <StudioDialog
          key={opening.anchor.book_id}
          opening={opening}
          onClose={() => setOpening(null)}
        />
      )}
    </StudioContext.Provider>
  );
}
export function StudioEntry({ anchor }: { anchor: Anchor }) {
  const open = useContext(StudioContext);
  return (
    <button
      className="learning-studio-entry"
      onClick={() => open({ anchor, mode: "notes" })}
    >
      <span className="studio-entry-icon" aria-hidden="true">
        ✎
      </span>
      <span>
        <strong>把理解，写下来</strong>
        <small>手写或语音笔记 · 图解与短片收藏</small>
      </span>
      <span aria-hidden="true">↗</span>
    </button>
  );
}
export function StudioMediaActions({
  anchor,
  label = "换个方式理解",
}: {
  anchor: Anchor;
  label?: string;
}) {
  const open = useContext(StudioContext);
  const ready = (anchor.excerpt ?? "").trim().length >= 4;
  return (
    <div className="studio-media-actions" aria-label={label}>
      <span>{label}</span>
      <div>
        <button
          type="button"
          disabled={!ready}
          onClick={() => open({ anchor, mode: "image" })}
        >
          ▧ 生成图解
        </button>
        <button
          type="button"
          disabled={!ready}
          onClick={() => open({ anchor, mode: "video" })}
        >
          ▷ 生成短片
        </button>
      </div>
    </div>
  );
}
export function StudyPassage({
  anchor,
  text,
}: {
  anchor: Anchor;
  text: string;
}) {
  const open = useContext(StudioContext),
    ref = useRef<HTMLParagraphElement>(null);
  const [selected, setSelected] = useState("");
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    function capture() {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const s = window.getSelection();
        if (
          s &&
          ref.current?.contains(s.anchorNode) &&
          ref.current.contains(s.focusNode)
        ) {
          const t = s.toString().trim();
          setSelected(t.length >= 4 ? t.slice(0, 3000) : "");
        }
      }, 120);
    }
    document.addEventListener("selectionchange", capture);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("selectionchange", capture);
    };
  }, []);
  const act = (mode: Opening["mode"]) =>
    open({
      anchor: { ...anchor, excerpt: selected || text.slice(0, 3000) },
      mode,
    });
  return (
    <>
      <p className="studio-selectable" ref={ref}>
        {text}
      </p>
      <div className="passage-tools" aria-label="选段学习工具">
        <small>
          {selected ? `已选 ${selected.length} 字` : "长按选段，换个方式理解"}
        </small>
        <div>
          <button onClick={() => act("image")}>▧ 看图理解</button>
          <button onClick={() => act("video")}>▷ 看短片</button>
          <button onClick={() => act("notes")}>✎ 记笔记</button>
        </div>
      </div>
    </>
  );
}
const statusText: Record<string, string> = {
  queued: "已排队",
  planning: "正在对照教材",
  submitting: "正在生成画面",
  polling: "短片制作中",
  reviewing: "正在检查内容",
  succeeded: "已完成",
  failed: "未完成",
  uncertain: "等待核对，未重复提交",
  needs_confirmation: "有字迹需要你确认",
};
const notePhase: Record<string, string> = {
  transcribing: "正在把语音转成文字…",
  recognizing: "正在识别笔迹…",
  verifying: "正在核对识别文字…",
  retrieving: "正在查找教材依据…",
  improving: "正在检查并补全…",
  repairing: "正在校正整理格式…",
  checking: "正在复核整理结果…",
};
const mediaProgress: Record<
  string,
  { value: number; step: string; detail: string }
> = {
  queued: { value: 8, step: "已加入队列", detail: "即将对照教材设计画面" },
  planning: { value: 28, step: "正在设计画面", detail: "核对教材事实，选择可靠的呈现方式" },
  submitting: { value: 52, step: "正在生成画面", detail: "正在处理生成请求，请勿重复提交" },
  polling: { value: 70, step: "正在制作短片", detail: "可以先去阅读，回来后会自动出现" },
  reviewing: { value: 90, step: "正在检查画面", detail: "核对知识关系与实际成品" },
};

function StudioDialog({
  opening,
  onClose,
}: {
  opening: Opening;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [mode, setMode] = useState<Opening["mode"]>(opening.mode),
    [caps, setCaps] = useState<StudioCaps | null>(null),
    [jobs, setJobs] = useState<StudioJob[]>([]),
    [notes, setNotes] = useState<InkNote[]>([]),
    [note, setNote] = useState<InkNote | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [goal, setGoal] = useState("意思"),
    [level, setLevel] = useState("标准");
  const [selection, setSelection] = useState(opening.anchor.excerpt ?? "");
  const lock = useRef(false),
    requestId = useRef(studioId()),
    mounted = useRef(true),
    editorClose = useRef<(() => Promise<boolean>) | null>(null);
  const refresh = async () => {
    const [j, n] = await Promise.all([
      studioApi.jobs(opening.anchor.book_id),
      studioApi.notes(opening.anchor.book_id),
    ]);
    if (mounted.current) {
      setJobs(j.items);
      setNotes(n.items);
    }
  };
  useEffect(() => {
    mounted.current = true;
    dialog.current?.showModal();
    void studioApi
      .caps()
      .then((c) => {
        if (mounted.current) setCaps(c);
      })
      .catch((e) => setError(e.message));
    void refresh().catch((e) => setError(e.message));
    const timer = setInterval(() => {
      void refresh().catch(() => {});
    }, 2000);
    return () => {
      mounted.current = false;
      clearInterval(timer);
    };
  }, []);
  async function close() {
    if (editorClose.current && !(await editorClose.current())) return;
    onClose();
  }
  async function generate() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const j = await studioApi.media(
        { ...opening.anchor, excerpt: selection },
        mode as "image" | "video",
        goal,
        level,
        requestId.current,
      );
      if (mounted.current) {
        setJobs((old) => [j, ...old.filter((x) => x.id !== j.id)]);
        requestId.current = studioId();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  function newNote(inputMode: "ink" | "voice") {
    setNote({
      id: studioId(),
      revision: 0,
      title: opening.anchor.chapter_title
        ? `${opening.anchor.chapter_title.slice(0, 70)} · 笔记`
        : inputMode === "voice" ? "我的语音笔记" : "我的手写笔记",
      input_mode: inputMode,
      strokes: [],
      ...opening.anchor,
    });
  }
  async function openNote(id: string) {
    setError("");
    try {
      setNote(await studioApi.note(id));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const unsynced: InkNote[] = [];
  if (caps) {
    try {
      const prefix = `zhiwo.studio.${caps.draft_scope}.`;
      for (let i = 0; i < sessionStorage.length; i++) {
        const k = sessionStorage.key(i);
        if (!k?.startsWith(prefix)) continue;
        const draft = JSON.parse(
          sessionStorage.getItem(k) || "null",
        ) as InkNote | null;
        if (
          draft?.book_id === opening.anchor.book_id &&
          Array.isArray(draft.strokes) &&
          !notes.some((n) => n.id === draft.id)
        )
          unsynced.push(draft);
      }
    } catch {
      /* Browsers that disable storage still support server notebooks. */
    }
  }
  return (
    <dialog
      ref={dialog}
      className={`learning-studio-dialog ${note ? "ink-open" : ""}`}
      onCancel={(e) => {
        e.preventDefault();
        void close();
      }}
      aria-label="学习创作空间"
    >
      <header className="studio-heading">
        <div>
          <small>云径 · 理解工作台</small>
          <h2>{note ? "留下你的思路" : "换个方式，读懂它"}</h2>
        </div>
        <button aria-label="关闭学习工作台" onClick={() => void close()}>
          ×
        </button>
      </header>
      <div className="studio-scroll">
        {!note && (
          <>
            <nav className="studio-segments" aria-label="学习方式">
              {(
                [
                  ["image", "图解"],
                  ["video", "短片"],
                  ["notes", "笔记"],
                ] as const
              ).map(([m, label]) => (
                <button
                  key={m}
                  aria-pressed={mode === m}
                  onClick={() => {
                    setMode(m);
                    requestId.current = studioId();
                  }}
                >
                  {label}
                </button>
              ))}
            </nav>
            {mode !== "notes" && (
              <section className="studio-compose">
                <span className="studio-eyebrow">只讲清楚一个问题</span>
                <label>
                  选中的内容
                  <textarea
                    value={selection}
                    maxLength={3000}
                    rows={4}
                    onChange={(e) => {
                      setSelection(e.target.value);
                      requestId.current = studioId();
                    }}
                    placeholder="从章节原文中长按选择一小段文字，也可以在这里填入你想理解的教材内容。"
                  />
                </label>
                <div className="studio-options">
                  <label>
                    讲解重点
                    <select
                      value={goal}
                      onChange={(e) => {
                        setGoal(e.target.value);
                        requestId.current = studioId();
                      }}
                    >
                      <option value="意思">它是什么意思</option>
                      <option value="原因">为什么会这样</option>
                      <option value="过程">具体如何发生</option>
                    </select>
                  </label>
                  <label>
                    讲解深度
                    <select
                      value={level}
                      onChange={(e) => {
                        setLevel(e.target.value);
                        requestId.current = studioId();
                      }}
                    >
                      <option>入门</option>
                      <option>标准</option>
                      <option>进阶</option>
                    </select>
                  </label>
                </div>
                <p className="studio-disclosure">
                  生成时会将选段与必要的教材上下文交给 AI 处理。
                  {mode === "video"
                    ? "短片约 6 秒，一次只讲一个变化。"
                    : "知识关系会整理成清晰的关系图，适合场景展示的内容会生成辅助插画。"}
                </p>
                <button
                  className="studio-primary"
                  disabled={
                    busy || selection.trim().length < 4 || !caps?.[mode]
                  }
                  onClick={() => void generate()}
                >
                  {busy
                    ? "正在提交…"
                    : mode === "image"
                      ? "生成一张图解"
                      : "生成 6 秒短片"}
                </button>
                {jobs
                  .filter(
                    (job) =>
                      job.kind === mode && pendingStudio(job),
                  )
                  .slice(0, 1)
                  .map((job) => (
                    <MediaJobProgress job={job} compact key={job.id} />
                  ))}
                {caps && !caps[mode] && (
                  <p role="status">
                    {caps.media_notice ||
                      "此方式暂未就绪，可以先记笔记或稍后再来。"}
                  </p>
                )}
                <small>按需生成 · 已生成的内容会保留在这里</small>
              </section>
            )}
            {mode === "notes" && (
              <section className="studio-notes-list">
                <div className="ink-invitation">
                  <span aria-hidden="true">✎</span>
                  <h3>你的思路，值得留下</h3>
                  <p>
                    写下来，或直接说出来。AI 会转成文字，再结合教材检查与补全。
                  </p>
                  <div className="note-create-choices">
                    <button aria-label="新建手写笔记" className="studio-primary" onClick={() => newNote("ink")}>
                      ✎ 手写笔记
                    </button>
                    <button aria-label="新建语音笔记" className="studio-secondary" onClick={() => newNote("voice")}>
                      ◉ 语音笔记
                    </button>
                  </div>
                </div>
                {unsynced.map((n) => (
                  <button
                    className="studio-note-row"
                    key={n.id}
                    onClick={() => setNote(n)}
                  >
                    <span>✎</span>
                    <span>
                      <strong>{n.title}</strong>
                      <small>未同步草稿 · 点击恢复并保存</small>
                    </span>
                    <span>›</span>
                  </button>
                ))}
                {notes.map((n) => (
                  <button
                    className="studio-note-row"
                    key={n.id}
                    onClick={() => void openNote(n.id)}
                  >
                    <span aria-hidden="true">{n.input_mode === "voice" ? "◉" : "▤"}</span>
                    <span>
                      <strong>{n.title}</strong>
                      <small>
                        {n.chapter_title || "本书笔记"} ·{" "}
                        {new Date((n.updated ?? 0) * 1000).toLocaleDateString()}
                      </small>
                    </span>
                    <span>›</span>
                  </button>
                ))}
              </section>
            )}
            <section className="studio-history">
              <h3>{mode === "notes" ? "笔记分析记录" : "我的视觉讲解"}</h3>
              {jobs.filter((j) =>
                mode === "notes"
                  ? ["recognize", "improve", "complete"].includes(j.kind)
                  : ["image", "video"].includes(j.kind),
              ).length === 0 && <p>完成后，会自动保存在这里。</p>}
              {jobs
                .filter((j) =>
                  mode === "notes"
                    ? ["recognize", "improve", "complete"].includes(j.kind)
                    : ["image", "video"].includes(j.kind),
                )
                .map((j) => (
                  <JobCard
                    key={j.id}
                    job={j}
                    onNote={
                      j.note_id ? () => void openNote(j.note_id!) : undefined
                    }
                  />
                ))}
            </section>
          </>
        )}
        {note && caps && (
          <InkEditor
            key={note.id}
            initial={note}
            scope={caps.draft_scope}
            enabled={caps.notes_ai}
            voiceEnabled={caps.voice_notes}
            jobs={jobs}
            onSaved={(n) => {
              setNote(n);
              void refresh().catch(e => { if(mounted.current) setError(e.message); });
            }}
            onJob={(j) =>
              setJobs((old) => [j, ...old.filter((x) => x.id !== j.id)])
            }
            closeRef={editorClose}
            onBack={() => {
              setNote(null);
              void refresh().catch(e => { if(mounted.current) setError(e.message); });
            }}
          />
        )}
        {error && (
          <p role="alert" className="studio-error">
            {error}
          </p>
        )}
      </div>
    </dialog>
  );
}
function JobCard({ job, onNote }: { job: StudioJob; onNote?: () => void }) {
  const r = job.result;
  return (
    <article className={`studio-job ${pendingStudio(job) ? "is-working" : ""}`}>
      <div className="studio-job-head">
        <strong>
          {r.title ??
            (job.kind === "recognize"
              ? "手写文字识别"
              : ["improve", "complete"].includes(job.kind)
                ? "笔记检查与补全"
                : job.kind === "video"
                  ? "短片讲解"
                  : "图解讲解")}
        </strong>
        <small role="status">{(pendingStudio(job) && notePhase[job.result.phase ?? ""]) || statusText[job.status] || job.status}</small>
      </div>
      {pendingStudio(job) && ["image", "video"].includes(job.kind) && (
        <MediaJobProgress job={job} />
      )}
      {pendingStudio(job) && !["image", "video"].includes(job.kind) && <p>你可以继续阅读，完成后回到这里查看。</p>}
      {pendingStudio(job) && r.transcript && (
        <details open>
          <summary>已转成文字，正在对照教材</summary>
          <p className="ink-polished">{r.transcript}</p>
        </details>
      )}
      {job.error && <p className="studio-error">{job.error}</p>}
      {r.visual_scope && <p className="studio-focus">这次看懂：{r.visual_scope}</p>}
      {job.asset_url && job.kind === "image" && <span className="studio-eyebrow">{r.visual_mode === "diagram" ? "教材彩色概念图" : "AI 辅助插画"}</span>}
      {job.asset_url &&
        (job.kind === "image" ? (
          <StudyImage src={job.asset_url} title={r.title ?? "AI 辅助图解"} />
        ) : (
          <video
            src={job.asset_url}
            controls
            playsInline
            preload="metadata"
            aria-label="AI 短片讲解"
          />
        ))}
      {r.explanation && <p>{r.explanation}</p>}
      {r.points && (
        <ol>
          {r.points.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ol>
      )}
      {r.caution && <p className="studio-caution">{r.caution}</p>}
      {job.asset_url && job.kind === "video" && <p className="studio-caution">动画中的速度、幅度与背景经过示意化处理；原文的动作含义、时长和事实以教材与文字讲解为准。</p>}
      {r.review && <small>{r.review}</small>}
      {r.evidence && (
        <details>
          <summary>对照教材出处</summary>
          {r.evidence.map((e, i) => (
            <blockquote key={i}>
              第 {e.page} 页 · {e.text}
            </blockquote>
          ))}
        </details>
      )}
      {onNote && <button onClick={onNote}>打开对应笔记 →</button>}
    </article>
  );
}
function MediaJobProgress({ job, compact = false }: { job: StudioJob; compact?: boolean }) {
  const phase = mediaProgress[job.status] ?? mediaProgress.queued;
  const step = job.status === "submitting" && job.kind === "video" ? "正在启动短片生成" : phase.step;
  return (
    <div className={`studio-job-progress ${compact ? "is-compact" : ""}`} role="status" aria-live="polite">
      <span className="studio-progress-spinner" aria-hidden="true" />
      <div>
        <strong>{step}</strong>
        <small>{phase.detail}</small>
        <div
          className="studio-progress-track"
          role="progressbar"
          aria-label={job.kind === "video" ? "短片生成进度" : "图解生成进度"}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={phase.value}
          aria-valuetext={`${step}，阶段进度，不代表预计剩余时间`}
        >
          <i style={{ width: `${phase.value}%` }} />
        </div>
        {!compact && <small className="studio-progress-footnote">阶段进度 · 离开后任务仍会在后台继续</small>}
      </div>
    </div>
  );
}
function StudyImage({ src, title }: { src: string; title: string }) {
  const [open, setOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  return <>
    <button className="studio-image-open" aria-label="查看图解大图" onClick={() => { setZoom(1); setOpen(true); }}>
      <img loading="lazy" src={src} alt={title} />
      <span>轻触放大，细看每个关系</span>
    </button>
    <dialog ref={dialog} className="studio-image-dialog" aria-label="图解大图"
      onCancel={e => { e.preventDefault(); e.stopPropagation(); setOpen(false); }} onClose={() => setOpen(false)}>
      {open && <>
        <header><strong>细看图解</strong><button autoFocus aria-label="关闭大图" onClick={() => setOpen(false)}>×</button></header>
        <div className="studio-image-tools">
          <button disabled={zoom <= 1} onClick={() => setZoom(z => Math.max(1, z - 0.5))}>缩小</button>
          <output aria-label="图解缩放比例">{zoom * 100}%</output>
          <button disabled={zoom >= 3} onClick={() => setZoom(z => Math.min(3, z + 0.5))}>放大</button>
        </div>
        <div className="studio-image-scroll" tabIndex={0} aria-label="可滚动的大图区域">
          <img src={src} alt={title} style={{ width: `${zoom * 100}%`, maxWidth: "none" }} />
        </div>
      </>}
    </dialog>
  </>;
}

function InkEditor({
  initial,
  scope,
  enabled,
  voiceEnabled,
  jobs,
  onSaved,
  onJob,
  closeRef,
  onBack,
}: {
  initial: InkNote;
  scope: string;
  enabled: boolean;
  voiceEnabled: boolean;
  jobs: StudioJob[];
  onSaved: (n: InkNote) => void;
  onJob: (j: StudioJob) => void;
  closeRef: React.MutableRefObject<(() => Promise<boolean>) | null>;
  onBack: () => void;
}) {
  const draftKey = `zhiwo.studio.${scope}.${initial.id}`;
  const [note, setNote] = useState<InkNote>(() => {
    try {
      const raw = sessionStorage.getItem(draftKey);
      if (raw) {
        const v = JSON.parse(raw) as InkNote;
        if (
          v.id === initial.id &&
          v.book_id === initial.book_id &&
          Array.isArray(v.strokes)
        )
          return v;
      }
    } catch {
      /* normal server version remains available */
    }
    return initial;
  });
  const latest = useRef(note),
    canvas = useRef<HTMLCanvasElement>(null),
    activeStroke = useRef<InkStroke | null>(null),
    pointer = useRef<number | null>(null),
    savePromise = useRef<Promise<InkNote> | null>(null),
    generationLock = useRef(false),
    dirty = useRef(note !== initial),
    alive = useRef(true),
    recorder = useRef<MediaRecorder | null>(null),
    recordingStream = useRef<MediaStream | null>(null),
    recordingStarted = useRef(0),
    recordingTimer = useRef<ReturnType<typeof setInterval> | null>(null),
    audioDurationRef = useRef(initial.audio_duration_seconds ?? 0),
    audioBlobRef = useRef<Blob | null>(null);
  const [tool, setTool] = useState<"pen" | "erase" | "move">("pen"),
    [finger, setFinger] = useState(true),
    [color, setColor] = useState<InkStroke["color"]>("#243148"),
    [redo, setRedo] = useState<InkStroke[]>([]),
    [message, setMessage] = useState(
      dirty.current
        ? "已恢复未保存草稿"
        : initial.revision > 0
          ? "已从云端恢复"
          : "尚未保存",
    ),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [transcript, setTranscript] = useState(""),
    [accepted, setAccepted] = useState(initial.edition?.job_id ?? ""),
    [zoom, setZoom] = useState(1),
    [recording, setRecording] = useState(false),
    [recordingSeconds, setRecordingSeconds] = useState(0),
    [audioPreview, setAudioPreview] = useState("");
  const voice = (note.input_mode ?? "ink") === "voice";
  const requestId = useRef(studioId());
  const relevant = jobs.filter(
    (j) => j.note_id === note.id && j.revision === note.revision,
  );
  const recognition = relevant.find(
      (j) => (j.kind === "recognize" && j.status === "succeeded") ||
        (j.kind === "complete" && j.status === "needs_confirmation"),
    ),
    improvement = relevant.find(
      (j) =>
        ["improve", "complete"].includes(j.kind) &&
        (j.status === "succeeded" ||
          (pendingStudio(j) &&
            j.result.phase === "checking" &&
            Boolean(j.result.polished))),
    );
  const working = relevant.some(pendingStudio);
  useEffect(() => {
    if (recognition?.result.transcript)
      setTranscript(recognition.result.transcript);
  }, [recognition?.id]);
  useEffect(
    () => () => {
      if (audioPreview) URL.revokeObjectURL(audioPreview);
    },
    [audioPreview],
  );
  function setVoiceBlob(blob: Blob, duration: number) {
    if (audioPreview) URL.revokeObjectURL(audioPreview);
    audioBlobRef.current = blob;
    audioDurationRef.current = Math.min(600, Math.max(0, duration));
    setAudioPreview(URL.createObjectURL(blob));
    setRecordingSeconds(audioDurationRef.current);
    setMessage("录音已准备好，点击“完成并整理”即可处理");
    setError("");
  }
  function stopRecording() {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }
  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("当前浏览器不能直接录音，你仍可选择已有音频文件。");
      return;
    }
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      recordingStream.current = stream;
      const preferred = [
        "audio/webm;codecs=opus",
        "audio/mp4",
        "audio/webm",
      ].find((type) => MediaRecorder.isTypeSupported(type));
      const current = new MediaRecorder(stream, preferred ? { mimeType: preferred } : undefined);
      const chunks: BlobPart[] = [];
      recorder.current = current;
      current.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      current.onstop = () => {
        const seconds = Math.min(600, (Date.now() - recordingStarted.current) / 1000);
        const blob = new Blob(chunks, { type: current.mimeType || preferred || "audio/webm" });
        recordingStream.current?.getTracks().forEach((track) => track.stop());
        recordingStream.current = null;
        recorder.current = null;
        if (recordingTimer.current) clearInterval(recordingTimer.current);
        recordingTimer.current = null;
        setRecording(false);
        if (blob.size >= 32) setVoiceBlob(blob, seconds);
        else setError("没有录到清晰声音，请重新录制。");
      };
      recordingStarted.current = Date.now();
      setRecordingSeconds(0);
      setRecording(true);
      current.start(500);
      recordingTimer.current = setInterval(() => {
        const seconds = (Date.now() - recordingStarted.current) / 1000;
        setRecordingSeconds(Math.min(600, seconds));
        if (seconds >= 600) stopRecording();
      }, 250);
    } catch {
      recordingStream.current?.getTracks().forEach((track) => track.stop());
      recordingStream.current = null;
      setError("没有获得麦克风权限。你可以允许访问后重试，或选择已有录音。");
    }
  }
  function chooseAudio(file?: File) {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
      setError("单条录音需小于 20 MB。");
      return;
    }
    const url = URL.createObjectURL(file);
    const probe = new Audio(url);
    probe.preload = "metadata";
    probe.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      if (Number.isFinite(probe.duration) && probe.duration > 600) {
        setError("单条语音笔记最长 10 分钟。");
        return;
      }
      setVoiceBlob(file, Number.isFinite(probe.duration) ? probe.duration : 0);
    };
    probe.onerror = () => {
      URL.revokeObjectURL(url);
      setError("无法读取这段录音，请换一个常见音频文件。");
    };
  }
  function draw(extra?: InkStroke) {
    const ctx = canvas.current?.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, 1000, 1400);
    for (const s of [...latest.current.strokes, ...(extra ? [extra] : [])]) {
      ctx.strokeStyle = s.color;
      ctx.fillStyle = s.color;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      s.points.forEach((p, i) => {
        const w = Math.max(2, s.width * (0.6 + p.p * 0.8));
        if (i) {
          const a = s.points[i - 1];
          ctx.lineWidth = w;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(p.x, p.y);
          ctx.stroke();
        } else {
          ctx.beginPath();
          ctx.arc(p.x, p.y, w / 2, 0, Math.PI * 2);
          ctx.fill();
        }
      });
    }
  }
  useEffect(() => {
    draw();
  }, [note.strokes]);
  function change(n: InkNote) {
    latest.current = n;
    setNote(n);
    dirty.current = true;
    requestId.current = studioId();
    try {
      sessionStorage.setItem(draftKey, JSON.stringify(n));
      setMessage("草稿已暂存");
    } catch {
      setMessage("草稿尚未同步，请勿关闭页面");
    }
    setError("");
  }
  async function saveDraftAsNew() {
    if (busy) return;
    setBusy(true);
    try {
      const saved = await studioApi.save({
        ...latest.current,
        id: studioId(),
        revision: 0,
        title:
          (latest.current.title || "手写笔记").slice(0, 100) + " · 草稿副本",
      });
      dirty.current = false;
      try {
        sessionStorage.removeItem(draftKey);
      } catch {
        /* server save succeeded */
      }
      onSaved(saved);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save(): Promise<InkNote> {
    if (savePromise.current) {
      await savePromise.current;
      if (dirty.current) return save();
      return latest.current;
    }
    if (!dirty.current && latest.current.revision > 0 && !audioBlobRef.current)
      return latest.current;
    const snapshot = latest.current;
    setMessage("正在保存…");
    const task = (async () => {
      let saved = await studioApi.save(snapshot);
      const audio = audioBlobRef.current;
      if ((snapshot.input_mode ?? "ink") === "voice" && audio) {
        setMessage("正在安全保存录音…");
        saved = await studioApi.audio(saved, audio, audioDurationRef.current);
        audioBlobRef.current = null;
      }
      return saved;
    })();
    savePromise.current = task;
    try {
      const saved = await task;
      const next = { ...latest.current, revision: saved.revision };
      dirty.current = latest.current !== snapshot;
      latest.current = next;
      if (alive.current) {
        setNote(next);
        onSaved(next);
        setMessage(dirty.current ? "还有修改待保存" : voice ? "录音已保存" : "已保存");
      }
      try {
        if (dirty.current)
          sessionStorage.setItem(draftKey, JSON.stringify(next));
        else sessionStorage.removeItem(draftKey);
      } catch {
        /* Browser storage must not turn a successful server save into a failure. */
      }
      return next;
    } catch (e) {
      if (alive.current) {
        setError((e as Error).message);
        setMessage("尚未保存，草稿仍保留");
      }
      throw e;
    } finally {
      savePromise.current = null;
    }
  }
  useEffect(() => {
    const t = setTimeout(() => {
      if (dirty.current) void save().catch(() => {});
    }, 1200);
    return () => clearTimeout(t);
  }, [note]);
  useEffect(() => {
    alive.current = true;
    const leave = (e: BeforeUnloadEvent) => {
      if (dirty.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", leave);
    closeRef.current = async () => {
      try {
        await save();
        return true;
      } catch {
        return false;
      }
    };
    return () => {
      alive.current = false;
      closeRef.current = null;
      window.removeEventListener("beforeunload", leave);
      if (recordingTimer.current) clearInterval(recordingTimer.current);
      if (recorder.current?.state === "recording") recorder.current.stop();
      recordingStream.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);
  function point(e: React.PointerEvent<HTMLCanvasElement>): InkPoint {
    const b = e.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1000, Math.max(0, ((e.clientX - b.left) / b.width) * 1000)),
      y: Math.min(1400, Math.max(0, ((e.clientY - b.top) / b.height) * 1400)),
      p: e.pressure || 0.5,
    };
  }
  function down(e: React.PointerEvent<HTMLCanvasElement>) {
    if (
      tool === "move" ||
      busy ||
      pointer.current !== null ||
      (!finger && e.pointerType !== "pen")
    )
      return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    pointer.current = e.pointerId;
    const p = point(e);
    if (tool === "erase") {
      const threshold = 25;
      const next = latest.current.strokes.filter(
        (s) =>
          !s.points.some((v) => Math.hypot(v.x - p.x, v.y - p.y) < threshold),
      );
      if (next.length !== latest.current.strokes.length) {
        setRedo([]);
        change({ ...latest.current, strokes: next });
      }
      return;
    }
    if (
      latest.current.strokes.length >= 1500 ||
      latest.current.strokes.reduce((a, s) => a + s.points.length, 0) >= 57000
    ) {
      setError("这一页已写满，请保存后新建一页");
      pointer.current = null;
      return;
    }
    activeStroke.current = { points: [p], color, width: 5 };
    draw(activeStroke.current);
  }
  function move(e: React.PointerEvent<HTMLCanvasElement>) {
    if (pointer.current !== e.pointerId || !activeStroke.current) return;
    if (activeStroke.current.points.length >= 3000) return;
    activeStroke.current.points.push(point(e));
    draw(activeStroke.current);
  }
  function end(e: React.PointerEvent<HTMLCanvasElement>) {
    if (pointer.current !== e.pointerId) return;
    if (activeStroke.current) {
      change({
        ...latest.current,
        strokes: [...latest.current.strokes, activeStroke.current],
      });
      setRedo([]);
    }
    activeStroke.current = null;
    pointer.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId))
      e.currentTarget.releasePointerCapture(e.pointerId);
  }
  async function analyze(action: "complete" | "improve") {
    if (generationLock.current) return;
    generationLock.current = true;
    setBusy(true);
    setError("");
    try {
      const n = await save();
      const j = await studioApi.analyze(
        n,
        action,
        transcript,
        requestId.current,
      );
      onJob(j);
      requestId.current = studioId();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      generationLock.current = false;
      setBusy(false);
    }
  }
  async function accept(value: boolean) {
    if (!improvement) return;
    setBusy(true);
    try {
      await studioApi.edition(latest.current, improvement.id, value);
      setAccepted(value ? improvement.id : "");
      setMessage(
        value ? "整理版已保存，原笔迹未改变" : "已撤回整理版，原笔迹仍保留",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="ink-editor">
      <button
        className="ink-back"
        onClick={async () => {
          try {
            await save();
            onBack();
          } catch {
            /* retain editor */
          }
        }}
      >
        ← 返回我的笔记
      </button>
      <input
        className="ink-title"
        aria-label="笔记标题"
        maxLength={120}
        value={note.title}
        onChange={(e) => change({ ...latest.current, title: e.target.value })}
      />
      {note.excerpt && (
        <details className="ink-source">
          <summary>对照选中的原文</summary>
          <p>{note.excerpt}</p>
          <small>
            {note.chapter_title}{" "}
            {note.pages?.length ? `· 第 ${note.pages.join("、")} 页` : ""}
          </small>
        </details>
      )}
      <div className="ink-layout">
        <section className="ink-paper-section">
          {voice ? (
            <div className={`voice-note ${recording ? "is-recording" : ""}`}>
              <div className="voice-orb" aria-hidden="true">
                <i /><i /><i /><i /><i />
              </div>
              <span className="studio-eyebrow">想到什么，就直接说</span>
              <h3>{recording ? "正在听你说…" : audioBlobRef.current || note.audio_ready ? "这段想法已经记下" : "录一段语音笔记"}</h3>
              <time aria-label="录音时长">
                {`${Math.floor(recordingSeconds / 60)}`.padStart(2, "0")}:
                {`${Math.floor(recordingSeconds % 60)}`.padStart(2, "0")}
              </time>
              {recording ? (
                <button className="voice-record stop" onClick={stopRecording}>
                  <span aria-hidden="true">■</span> 停止录音
                </button>
              ) : (
                <button className="voice-record" disabled={busy} onClick={() => void startRecording()}>
                  <span aria-hidden="true">●</span> {audioBlobRef.current || note.audio_ready ? "重新录制" : "开始录音"}
                </button>
              )}
              <label className="voice-file">
                或选择已有录音
                <input
                  type="file"
                  accept="audio/*,.aiff,.aif,.m4a,.mp3,.wav,.webm,.ogg"
                  disabled={busy || recording}
                  onChange={(event) => {
                    chooseAudio(event.target.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                />
              </label>
              {(audioPreview || note.audio_ready) && !recording && (
                <audio
                  controls
                  preload="metadata"
                  src={audioPreview || studioApi.audioUrl(note)}
                  aria-label="语音笔记录音"
                />
              )}
              <div className="ink-save">
                <small role="status">{message}</small>
                <button disabled={busy || recording || (!audioBlobRef.current && !note.audio_ready)} onClick={() => void save().catch(() => {})}>
                  保存录音
                </button>
              </div>
              <small className="ink-hint">安静环境下靠近麦克风自然说话即可；停顿会自动过滤，教材术语会在整理时再次核对。</small>
            </div>
          ) : <>
          <div className="ink-tools" aria-label="手写工具">
            <button
              aria-pressed={tool === "pen"}
              onClick={() => setTool("pen")}
            >
              笔
            </button>
            <button
              aria-pressed={tool === "erase"}
              onClick={() => setTool("erase")}
            >
              擦除笔画
            </button>
            <button
              aria-pressed={tool === "move"}
              onClick={() => setTool("move")}
            >
              移动
            </button>
            <button
              disabled={!note.strokes.length || busy}
              onClick={() => {
                const s = latest.current.strokes;
                setRedo((r) => [...r, s[s.length - 1]]);
                change({ ...latest.current, strokes: s.slice(0, -1) });
              }}
            >
              撤销
            </button>
            <button
              disabled={!redo.length || busy}
              onClick={() => {
                change({
                  ...latest.current,
                  strokes: [...latest.current.strokes, redo[redo.length - 1]],
                });
                setRedo((r) => r.slice(0, -1));
              }}
            >
              重做
            </button>
          </div>
          <div className="ink-options">
            <label>
              <input
                type="checkbox"
                checked={finger}
                onChange={(e) => setFinger(e.target.checked)}
              />
              手指书写
            </label>
            <label>
              笔色
              <select
                aria-label="笔色"
                value={color}
                onChange={(e) => setColor(e.target.value as InkStroke["color"])}
              >
                <option value="#243148">墨色</option>
                <option value="#7655c9">紫色</option>
                <option value="#23836e">绿色</option>
              </select>
            </label>
            <button
              aria-label="缩小画布"
              disabled={zoom === 1}
              onClick={() => setZoom(1)}
            >
              −
            </button>
            <span>{zoom * 100}%</span>
            <button
              aria-label="放大画布"
              disabled={zoom === 1.5}
              onClick={() => setZoom(1.5)}
            >
              ＋
            </button>
          </div>
          <div className="ink-viewport">
            <div className="ink-paper" style={{ width: `${zoom * 100}%` }}>
              <canvas
                ref={canvas}
                width={1000}
                height={1400}
                aria-label="手写笔记画布"
                style={{ touchAction: tool === "move" ? "auto" : "none" }}
                onPointerDown={down}
                onPointerMove={move}
                onPointerUp={end}
                onPointerCancel={end}
              />
            </div>
          </div>
          <div className="ink-save">
            <small role="status">{message}</small>
            <button onClick={() => void save().catch(() => {})}>
              保存笔记
            </button>
          </div>
          <small className="ink-hint">
            手写笔或手指都可以写；选择“移动”后可平移画布。擦除会移除点中的整条笔画。
          </small>
          </>}
        </section>
        <section className="ink-assistant">
          <span className="studio-eyebrow">与你一起完善，而不是替你重写</span>
          <h3>{voice ? "说完，剩下的交给我" : "写完，剩下的交给我"}</h3>
          <p>{voice ? "一键保存录音、转成文字，再结合教材检查与补全。" : "一键保存笔迹，自动识别、核对，再结合教材检查与补全。"}</p>
          <p className="studio-disclosure">
            {voice
              ? "点击完成会先在服务器内转写录音，再将逐字稿和必要教材片段交给 AI 整理。原录音和逐字稿都会保留；只有听不清时才请你确认。"
              : "点击完成会将笔记图像和必要教材片段交给 AI 处理。原笔迹不会被覆盖；仅在字迹仍不清楚时请你确认。"}
          </p>
          <button
            className="studio-primary"
            disabled={!(voice ? voiceEnabled : enabled) || busy || working || (voice ? (!audioBlobRef.current && !note.audio_ready) : !note.strokes.length)}
            onClick={() => void analyze("complete")}
          >
            {working ? (notePhase[relevant.find(pendingStudio)?.result.phase ?? ""] || "正在准备整理…") : busy ? "正在保存…" : "完成并整理"}
          </button>
          {!(voice ? voiceEnabled : enabled) && <small>AI 暂未就绪，仍可正常保存{voice ? "语音" : "手写"}笔记。</small>}
          {recognition && !improvement && !working && (
            <div className="ink-recognition">
              <label>
                {voice ? "这几处录音内容需要你确认" : "这几处字迹需要你确认"}
                <textarea
                  aria-label="核对识别文字"
                  rows={7}
                  value={transcript}
                  maxLength={12000}
                  onChange={(e) => {
                    setTranscript(e.target.value);
                    requestId.current = studioId();
                  }}
                />
              </label>
              {recognition.result.uncertain?.length !== 0 && (
                <p className="studio-caution">
                  请特别核对：{recognition.result.uncertain?.join("；")}
                </p>
              )}
              <button
                className="studio-primary"
                disabled={
                  busy ||
                  working ||
                  transcript.trim().length < 2 ||
                  transcript.includes("[待确认]")
                }
                onClick={() => void analyze("improve")}
              >
                确认并继续整理
              </button>
            </div>
          )}
          {improvement && (
            <div className="ink-feedback">
              <h3>{improvement.result.provisional ? "先看整理草稿" : "给你的修改建议"}</h3>
              <small role={improvement.result.provisional ? "status" : undefined}>
                {improvement.result.provisional
                  ? "内容已生成，正在对照教材做最后复核；你可以先阅读。"
                  : "整理结果已保存，原笔迹保持不变。"}
              </small>
              <details>
                <summary>查看识别文字</summary>
                <p className="ink-polished">{improvement.result.transcript}</p>
              </details>
              <p>{improvement.result.summary}</p>
              {improvement.result.suggestions?.map((s, i) => (
                <article key={i}>
                  <b>{s.kind}</b>
                  {s.original && <blockquote>{s.original}</blockquote>}
                  <p>{s.suggestion}</p>
                  <details>
                    <summary>教材第 {s.page} 页依据</summary>
                    <p>{s.evidence}</p>
                  </details>
                </article>
              ))}
              <details open>
                <summary>整理后的复习页</summary>
                <div className="ink-polished">
                  {improvement.result.polished}
                </div>
              </details>
              <button
                className="studio-primary"
                disabled={busy || improvement.result.provisional}
                onClick={() => void accept(accepted !== improvement.id)}
              >
                {improvement.result.provisional
                  ? "复核完成后可以保留"
                  : accepted === improvement.id
                  ? "撤回这个整理版"
                  : "保留整理版（原笔迹不变）"}
              </button>
            </div>
          )}
          {!improvement && initial.edition && (
            <details>
              <summary>已保存的整理版（历史版本）</summary>
              <p className="ink-polished">{initial.edition.polished}</p>
            </details>
          )}
          {relevant
            .filter((j) => pendingStudio(j) || j.error)
            .map((j) => (
              <JobCard job={j} key={j.id} />
            ))}
        </section>
      </div>
      {error && (
        <div role="alert" className="studio-error">
          <p>{error}</p>
          <button disabled={busy} onClick={() => void saveDraftAsNew()}>
            把当前草稿另存为新笔记
          </button>
        </div>
      )}
    </div>
  );
}
