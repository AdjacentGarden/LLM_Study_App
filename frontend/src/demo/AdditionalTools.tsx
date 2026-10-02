import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { ArrowLeft, MessageCircle, Sparkles, Users, UserRound, PenLine, RotateCcw } from "lucide-react";
import { api } from "../api/client";
import { request } from "./api/transport";
import { useAccount } from "../components/AccountGate";
import { captureStorage } from "../components/bookContext";
import { DoubtReturn, MemoryReturn } from "../components/LearningReturn";
import { StudioEntry, StudioMediaActions, StudioProvider } from "../components/StudioShell";
import type { BookCatalogItem, Course, InterviewResponse, UserProfile } from "../types/api";
import { useAppContext } from "./context/AppContext";
import { Button, Card } from "./components/ui";
import "../styles/next/social.css";
import "../styles/next/library-profile.css";
import "../styles/next/user-profile.css";
import "../styles/next/learning-return.css";
import "../styles/next/learning-studio.css";

const SocialPage = lazy(() => import("../components/SocialPage").then(module => ({ default: module.SocialPage })));
const UserProfilePage = lazy(() => import("../components/UserProfilePage").then(module => ({ default: module.UserProfilePage })));
const ProfileDashboard = lazy(() => import("../components/ProfileDashboard").then(module => ({ default: module.ProfileDashboard })));

function clampFontScale(value: number) {
  return Number.isFinite(value) ? Math.min(2, Math.max(1, value)) : 1;
}

// The active demo typography consumes these tokens on every descendant, including profile record dialogs.
function toolTypography(fontScale: number): CSSProperties {
  return {
    "--type-title": `${34 * fontScale}px`,
    "--type-subtitle": `${26 * fontScale}px`,
    "--type-body": `${20 * fontScale}px`,
    "--type-caption": `${14 * fontScale}px`,
  } as CSSProperties;
}

type Tool = "menu" | "social" | "account" | "profile" | "diagnosis" | "return" | "studio";

/** Supplemental existing business tools, opened from the original Profile page. */
export function AdditionalTools({ onClose }: { onClose: () => void }) {
  const { state: account } = useAccount();
  const storage = useMemo(captureStorage, [account.user_id]);
  const { loadedBookId, activeChapterId, parsedChapters, parsedChunks, sourceSummaries, openSourcePage, showToast } = useAppContext();
  const [tool, setTool] = useState<Tool>("menu");
  const [books, setBooks] = useState<BookCatalogItem[]>([]);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [session, setSession] = useState<InterviewResponse | null>(null);
  const [personalized, setPersonalized] = useState<Course | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [selected, setSelected] = useState<string[]>([]), [answer, setAnswer] = useState("");
  const [confidence, setConfidence] = useState(.65), [revision, setRevision] = useState(0);
  const [reminder, setReminder] = useState(() => storage.safeGet("zhiwo.reminder") || "19:00"), [fontScale, setFontScale] = useState(() => clampFontScale(Number(storage.safeGet("zhiwo.font-scale")) || 1));
  const epoch = useRef(0), started = useRef(Date.now());
  const bookId = loadedBookId ?? sourceSummaries[0]?.book_id ?? null;
  const source = sourceSummaries.find(item => item.book_id === bookId);
  const chapter = parsedChapters?.find(item => item.chapter_id === activeChapterId) ?? parsedChapters?.[0];
  const anchor = bookId ? { book_id: bookId, chapter_id: chapter?.chapter_id, chapter_title: chapter?.source_title,
    excerpt: parsedChunks?.filter(chunk => !chapter || chunk.chapter_id === chapter.chapter_id).map(chunk => chunk.text).join("\n").slice(0, 4000),
    pages: chapter ? [chapter.page_start] : [1] } : null;

  async function refreshLibrary() { setBooks(await api.books()); }
  useEffect(() => {
    const ticket = ++epoch.current;
    setSession(null); setError("");
    const sessionId=bookId ? account.learning_sessions[bookId] || storage.safeGet(`zhiwo.active-session:${bookId}`) : null;
    void Promise.allSettled([api.books(), api.userProfile(), sessionId
      ? api.resume(sessionId) : Promise.resolve(null)]).then(results => {
      if (epoch.current !== ticket) return;
      if (results[0].status === "fulfilled") setBooks(results[0].value);
      if (results[1].status === "fulfilled") setProfile(results[1].value);
      if (results[2].status === "fulfilled") setSession(results[2].value);
      const failure = results.find(result => result.status === "rejected");
      if (failure?.status === "rejected") setError(failure.reason instanceof Error ? failure.reason.message : "学习档案加载失败");
    });
    return () => { ++epoch.current; };
  }, [account.user_id, bookId]);
  useEffect(() => { setSelected([]); setAnswer(""); started.current = Date.now(); }, [session?.turn.turn_id]);

  async function startDiagnosis() {
    if (!bookId || busy) return;
    const ticket = epoch.current; setBusy(true); setError("");
    try {
      if (!session || session.phase === "complete") {
        const created=await request<{job_id:string;status:string}>("/api/demo/bookcourse/buildDiagnostics",{method:"POST",body:JSON.stringify({args:[bookId]})});
        let job=await request<{status:string;error?:string}>("/api/demo/bookcourse/getJob",{method:"POST",body:JSON.stringify({args:[created.job_id]})});
        while(job.status!=="done" && job.status!=="failed") {
          await new Promise(resolve=>setTimeout(resolve,1000));
          if(ticket!==epoch.current)return;
          job=await request("/api/demo/bookcourse/getJob",{method:"POST",body:JSON.stringify({args:[created.job_id]})});
        }
        if(job.status==="failed")throw new Error(job.error || "诊断题生成失败");
      }
      if(ticket!==epoch.current)return;
      const next = session && session.phase !== "complete" ? session : await api.start(bookId, account.user_id);
      if (ticket !== epoch.current) return;
      storage.safeSet(`zhiwo.active-session:${bookId}`, next.session_id);
      setSession(next); setTool("diagnosis");
    } catch (cause) { if (ticket === epoch.current) setError(cause instanceof Error ? cause.message : "诊断暂时不可用"); }
    finally { if (ticket === epoch.current) setBusy(false); }
  }
  async function submitDiagnosis() {
    if (!session || busy) return;
    const ticket = epoch.current; setBusy(true); setError("");
    try {
      let next: InterviewResponse;
      const labels=session.turn.options.filter(option=>selected.includes(option.id)).map(option=>option.label).join("、");
      if (["book_briefing", "goal_discovery", "background_discovery", "constraint_discovery"].includes(session.turn.phase)) next = await api.answerProfile(session.session_id, answer.trim() || labels, selected);
      else if (session.turn.phase === "profile_confirmation") next = await api.confirm(session.session_id, !selected.includes("edit"));
      else if (session.turn.item) {
        await api.respond(session.session_id, { item_id: session.turn.item.item_id, answer:answer.trim() || labels || "不确定", selected_option_ids: selected,
          confidence, response_seconds: Math.max(1, (Date.now() - started.current) / 1000), hints_used: 0, revisions: 0 });
        if(ticket!==epoch.current)return;
        next = await api.resume(session.session_id);
      } else next = await api.next(session.session_id);
      if (ticket !== epoch.current) return;
      setSession(next); if (next.phase === "complete") setTool("profile");
    } catch (cause) { if (ticket === epoch.current) setError(cause instanceof Error ? cause.message : "回答提交失败"); }
    finally { if (ticket === epoch.current) setBusy(false); }
  }
  async function compilePersonalized() {
    if(!session || !chapter || busy)return;
    const ticket=epoch.current;setBusy(true);setError("");
    try {const result=await api.compileCourse(session.session_id,chapter.chapter_id);if(ticket===epoch.current)setPersonalized(result);}
    catch(cause){if(ticket===epoch.current)setError(cause instanceof Error?cause.message:"个性化课程生成失败");}
    finally{if(ticket===epoch.current)setBusy(false);}
  }
  const shortcuts = [
    { tool: "social" as const, title: "好友与消息", description: "查找好友、私信与资料分享", icon: Users },
    { tool: "account" as const, title: "个人资料", description: "账号资料、头像与简介", icon: UserRound },
    { tool: "profile" as const, title: "学习画像与证据", description: "真实诊断、评分及复习记录", icon: Sparkles },
    { tool: "return" as const, title: "疑问与记忆回访", description: "待解问题与到期复习", icon: RotateCcw },
    { tool: "studio" as const, title: "Studio 创作空间", description: "原始手写、录音与模型任务", icon: PenLine },
  ];
  return <StudioProvider><section className="screen-stack additional-tools" aria-label="我的学习工具" style={toolTypography(fontScale)}>
    <div className="section-head"><Button variant="text" icon={<ArrowLeft size={18} />} onClick={tool === "menu" ? onClose : () => { setTool("menu"); setError(""); }}>返回</Button><h2>{shortcuts.find(item => item.tool === tool)?.title ?? "我的学习工具"}</h2></div>
    {error ? <Card><p role="alert">{error}</p></Card> : null}
    {tool === "menu" ? <div className="profile-course-list">{shortcuts.map(item => <Button key={item.tool} variant="secondary" icon={<item.icon size={20} />} onClick={() => setTool(item.tool)}>{item.title} · {item.description}</Button>)}</div> : null}
    <Suspense fallback={<Card><p role="status">正在打开学习工具…</p></Card>}>
    {tool === "social" ? <SocialPage books={books} onLibraryChanged={refreshLibrary} /> : null}
    {tool === "account" ? <UserProfilePage onBusy={setBusy} onSaved={value => { setProfile(value); setTool("menu"); showToast("个人资料已保存"); }} onBack={() => setTool("menu")} /> : null}
    {tool === "profile" ? <><ProfileDashboard profile={session?.profile} userProfile={profile} onAccount={() => setTool("account")}
      sessionId={session?.session_id} bookTitle={source?.title ?? "请先选择课程资料"} completed={session?.phase === "complete"}
      reminder={reminder} fontScale={fontScale} onReminder={value => { storage.safeSet("zhiwo.reminder", value); setReminder(value); }} onFont={value => { const scale = clampFontScale(value); storage.safeSet("zhiwo.font-scale", String(scale)); setFontScale(scale); }} onDiagnose={() => void startDiagnosis()} />
      {session?.phase === "complete" && chapter ? <Button disabled={busy} onClick={()=>void compilePersonalized()}>{busy?"正在生成…":"生成当前章节个性化课程"}</Button>:null}
      {personalized?<Card><h3>{personalized.chapter_title}</h3><p>{personalized.opening}</p><p>{personalized.summary}</p>{personalized.original_reading.map((section,index)=><section key={index}><h4>{section.title}</h4><p>{section.content}</p></section>)}</Card>:null}</> : null}
    {tool === "diagnosis" && session ? <Card className="assignment-card"><p className="eyebrow">学习画像诊断</p><h2>{session.turn.question ?? "继续建立学习画像"}</h2><p>{session.turn.message}</p>
      <div className="assignment-choice-options" role="group" aria-label="回答选项">{session.turn.options.map(option => <button key={option.id} type="button" disabled={busy} aria-pressed={selected.includes(option.id)} onClick={() => setSelected(current => session.turn.response_type === "multiple_choice" ? current.includes(option.id) ? current.filter(id => id !== option.id) : [...current, option.id] : [option.id])}>{option.label}</button>)}</div>
      {!session.turn.options.length && session.turn.item ? <textarea aria-label="诊断回答" value={answer} onChange={event => setAnswer(event.target.value)} /> : null}
      {session.turn.item ? <label>作答信心<input aria-label="作答信心" type="range" min="0" max="1" step=".05" value={confidence} onChange={event => setConfidence(Number(event.target.value))} /></label> : null}
      <Button disabled={busy || Boolean(session.turn.options.length && !selected.length)} onClick={() => void submitDiagnosis()}>{busy ? "提交中…" : "确认并继续"}</Button></Card> : null}
    {tool === "return" ? bookId ? <><DoubtReturn bookId={bookId} chapterId={chapter?.chapter_id} revision={revision} onAsk={doubt => {
      openSourcePage({ bookId: doubt.book_id, title: doubt.chapter_title ?? source?.title ?? "待解疑问", pageStart: doubt.pages?.[0] ?? 1, sourceText: doubt.excerpt, from: "profile" }); onClose();
    }} />{session ? <MemoryReturn sessionId={session.session_id} revision={revision} onReviewed={() => setRevision(value => value + 1)} /> : <Card><p>完成真实诊断和闪卡复习后显示记忆回访。</p></Card>}</> : <Card><p>请先选择一份课程资料。</p></Card> : null}
    {tool === "studio" ? anchor ? <Card><h3>{chapter?.source_title ?? source?.title}</h3><StudioEntry anchor={anchor} /><StudioMediaActions anchor={anchor} /><p>外部服务未配置时，任务会显示真实能力状态。</p></Card> : <Card><MessageCircle size={22} /><p>请先打开课程资料，再进入创作空间。</p></Card> : null}
    </Suspense>
  </section></StudioProvider>;
}
