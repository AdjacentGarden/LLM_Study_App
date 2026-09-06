import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api/client";
import { ApiError } from "./api/transport";
import { captureStorage, selectBook, suggestedQuestions, hasAdditionalExplanation } from "./components/bookContext";
import { LearningHome } from "./components/LearningHome";
import { LibraryHub } from "./components/LibraryHub";
import { SocialPage } from "./components/SocialPage";
import { AccountControls } from "./components/AccountGate";
import { CommunityPage } from "./components/CommunityPage";
import { ProfileDashboard } from "./components/ProfileDashboard";
import { UserProfilePage, DEFAULT_AVATAR } from "./components/UserProfilePage";
import { DiagnosticJourney } from "./components/DiagnosticJourney";
import { FlashcardDeck } from "./components/FlashcardDeck";
import { PracticeFeedback } from "./components/PracticeFeedback";
import { Icon, type IconName } from "./components/Icon";
import { TutorChat } from "./components/TutorChat";
import { CourseTabs } from "./components/CourseTabs";
import { useAnimatedView } from "./components/useAnimatedView";
import type {
  BookCatalogItem,
  BookStructure,
  Course,
  CourseActivity,
  InterviewResponse,
  Profile,
  QAResult,
  Turn,
  UserProfile,
} from "./types/api";

type View = "social" | "home" | "library" | "community" | "interview" | "course" | "qa" | "profile" | "account";
type CourseTab = "guide" | "reading" | "points" | "cards" | "practice";

const SESSION_KEY = "zhiwo.active-session";
const ACTIVE_BOOK_KEY = "zhiwo.active-book";
const eventId = () => `event_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
const depthLabel = { foundation: "基础支架", standard: "结构理解", advanced: "迁移挑战" };

function App() {
  const {safeGet,safeSet}=useMemo(captureStorage,[]);
  const [books, setBooks] = useState<BookCatalogItem[]>([]);
  const [userProfile,setUserProfile]=useState<UserProfile|null>(null);
  const [initializing, setInitializing] = useState(true);
  const [book, setBook] = useState<BookCatalogItem | null>(null);
  const [structure, setStructure] = useState<BookStructure | null>(null);
  const [session, setSession] = useState<InterviewResponse | null>(null);
  const [view, setView] = useAnimatedView<View>("home");
  const [course, setCourse] = useState<Course | null>(null);
  const [courseTab, setCourseTab] = useState<CourseTab>("guide");
  const [selected, setSelected] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [confidence, setConfidence] = useState(0.65);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [qaQuestion, setQaQuestion] = useState("");
  const [askedQuestion, setAskedQuestion] = useState("");
  const [qaResult, setQaResult] = useState<QAResult | null>(null);
  const [qaBookId, setQaBookId] = useState("");
  const [qaStructure, setQaStructure] = useState<BookStructure | null>(null);
  const qaBook = books.find(item => item.book_id === qaBookId) ?? book;
  useEffect(() => {
    let cancelled = false;
    setQaResult(null); setAskedQuestion(""); setError("");
    setQaStructure(null);
    if (qaBook) void api.structure(qaBook.book_id).then(value => {
      if (!cancelled) setQaStructure(value);
    }).catch(() => { /* Generic suggestions remain available. */ });
    return () => { cancelled = true; };
  }, [qaBook?.book_id]);
  const [cardIndex, setCardIndex] = useState(0);
  const [cardFlipped, setCardFlipped] = useState(false);
  const [practiceIndex, setPracticeIndex] = useState(0);
  const [activity, setActivity] = useState<CourseActivity | null>(null);
  const [courseNeedsRefresh, setCourseNeedsRefresh] = useState(false);
  const [reminder, setReminder] = useState(safeGet("zhiwo.reminder") || "20:30");
  const [fontScale, setFontScale] = useState(Math.min(1.2, Math.max(.9, Number(safeGet("zhiwo.font-scale")) || 1)));
  const [online, setOnline] = useState(navigator.onLine);
  const boot = useRef(0);
  const startedAt = useRef(Date.now());
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    contentRef.current?.scrollTo({ top: 0, behavior: "instant" });
  }, [view, session?.turn.turn_id]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 2800);
    return () => window.clearTimeout(timer);
  }, [notice]);

  async function loadCatalog(preferred: string | null = null, showLoading = true) {
    const generation = ++boot.current;
    if(showLoading)setInitializing(true); setError("");
    try {
      const preferredBook = preferred ?? safeGet(ACTIVE_BOOK_KEY);
      const savedId = preferredBook ? safeGet(`${SESSION_KEY}:${preferredBook}`) : safeGet(SESSION_KEY);
      const [catalog, restored] = await Promise.all([api.books(), savedId ? api.resume(savedId).catch(error => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error; // Offline/5xx must never erase a valid learning session.
      }) : Promise.resolve(null)]);
      const active = selectBook(catalog, restored, preferredBook);
      const chapters = active ? await api.structure(active.book_id) : null;
      if (generation !== boot.current) return;
      const matching = restored?.profile.book_id === active?.book_id ? restored : null;
      setBooks(catalog); setBook(active); setStructure(chapters); setSession(matching);
      setCourse(null); setQaResult(null); setAskedQuestion(""); setQaQuestion("");
      safeSet(SESSION_KEY, matching?.session_id ?? null);
      safeSet(ACTIVE_BOOK_KEY, active?.book_id ?? null);
      if (matching) safeSet(`${SESSION_KEY}:${matching.profile.book_id}`, matching.session_id);
    } catch (value) { if (generation === boot.current) setError((value as Error).message); }
    finally { if (generation === boot.current && showLoading) setInitializing(false); }
  }

  useEffect(() => {
    void loadCatalog();
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update); window.addEventListener("offline", update);
    return () => { ++boot.current; window.removeEventListener("online", update); window.removeEventListener("offline", update); };
  }, []);

  useEffect(()=>{if(initializing)return;let cancelled=false;void api.userProfile().then(value=>{if(!cancelled)setUserProfile(value);}).catch(()=>{/* Profile page offers retry; keep the last saved avatar during network failures. */});return()=>{cancelled=true;};},[initializing]);

  useEffect(() => {
    if (!book || book.diagnostics_ready !== false) return;
    const id = book.book_id;
    const timer = window.setInterval(() => {
      void api.books().then(catalog => {
        setBooks(catalog);
        setBook(current => current?.book_id === id ? catalog.find(item => item.book_id === id) ?? current : current);
      }).catch(() => { /* Keep the existing catalog during a transient outage. */ });
    }, 15000);
    return () => window.clearInterval(timer);
  }, [book?.book_id, book?.diagnostics_ready]);

  const profile = session?.profile;
  const turn = session?.turn;
  const diagnosed = profile?.diagnostic_observations.length ?? 0;
  const completed = session?.phase === "complete";
  const canSubmit = useMemo(() => {
    if (!turn || busy) return false;
    if (turn.phase === "adaptive_diagnosis" && !turn.item) return true;
    if (turn.phase === "profile_confirmation") return selected.length > 0;
    return selected.length > 0;
  }, [turn, busy, selected]);

  function acceptInterview(next: InterviewResponse) {
    setSession(next);
    safeSet(SESSION_KEY, next.session_id);
    safeSet(`${SESSION_KEY}:${next.profile.book_id}`, next.session_id);
    setSelected([]);
    setConfidence(.65);
    setInput("");
    setActivity(null);
    startedAt.current = Date.now();
  }

  async function startInterview() {
    if (!book) return;
    if (book.diagnostics_ready === false) {
      setNotice("这本书的诊断题尚未就绪，可先查看全书主线或使用教材答疑。");
      return;
    }
    setBusy(true); setError("");
    try {
      const next = await api.start(book.book_id);
      acceptInterview(next);
      setView("interview");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(false); }
  }

  async function submitInterview() {
    if (!session || !turn || !canSubmit) return;
    setBusy(true); setError("");
    try {
      if (turn.phase === "profile_confirmation") {
        const wantsRevision = selected.includes("edit");
        const next = await api.confirm(session.session_id, !wantsRevision);
        acceptInterview(next);
        if (!wantsRevision) { setView("home"); setNotice("学习方向已保存，看看建议先学的章节"); }
        return;
      }
      if (turn.phase === "adaptive_diagnosis" && !turn.item) {
        acceptInterview(await api.next(session.session_id)); return;
      }
      const labels = turn.options.filter((option) => selected.includes(option.id)).map((option) => option.label);
      const answer = input.trim() || labels.join("、") || "不确定";
      if (["book_briefing", "goal_discovery", "background_discovery", "constraint_discovery"].includes(turn.phase)) {
        acceptInterview(await api.answerProfile(session.session_id, answer, selected)); return;
      }
      if (turn.item) {
        const next = await api.respond(session.session_id, {
          item_id: turn.item.item_id,
          answer,
          selected_option_ids: selected,
          confidence,
          response_seconds: Math.max(1, (Date.now() - startedAt.current) / 1000),
          hints_used: 0,
          revisions: 0,
        });
        setSession({ ...session, phase: next.next_turn.phase, turn: next.next_turn, profile: next.profile });
        setSelected([]); setInput(""); setConfidence(.65); startedAt.current = Date.now();
        setNotice(`第 ${next.profile.diagnostic_observations.length} 次回答已记录，学习方向持续更新`);
      }
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(false); }
  }

  async function openCourse(chapterId: string, initialTab: "guide" | "cards" = "guide") {
    if (!session || busy) return;
    setBusy(true); setError("");
    try {
      const next = await api.compileCourse(session.session_id, chapterId);
      setCourse(next); setCourseTab(initialTab); setCardIndex(0); setCardFlipped(false);
      setPracticeIndex(0); setActivity(null); setCourseNeedsRefresh(false); setView("course");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(false); }
  }

  async function reviewCard(rating: "again" | "hard" | "good" | "easy") {
    if (!session || !course || busy) return;
    const card = course.flashcards[cardIndex]; if (!card) return;
    setBusy(true); setError("");
    try {
      const result = await api.reviewFlashcard(session.session_id, course.course_id, card.card_id, {
        event_id: eventId(), rating, response_seconds: Math.max(1, (Date.now() - startedAt.current) / 1000),
      });
      setActivity(result); setCourseNeedsRefresh((current) => current || result.course_stale); setSession({ ...session, profile: result.profile });
      setCardIndex((current) => Math.min(course.flashcards.length - 1, current + 1));
      setCardFlipped(false); startedAt.current = Date.now();
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(false); }
  }

  async function submitPractice() {
    if (!session || !course || busy || !input.trim()) return;
    const item = course.practice_items[practiceIndex]; if (!item) return;
    setBusy(true); setError("");
    try {
      const result = await api.practice(session.session_id, course.course_id, item.item_id, {
        event_id: eventId(), answer: input.trim(), selected_option_ids: selected,
        confidence, response_seconds: Math.max(1, (Date.now() - startedAt.current) / 1000), hints_used: 0, revisions: 0,
      });
      setActivity(result); setCourseNeedsRefresh((current) => current || result.course_stale); setSession({ ...session, profile: result.profile }); setInput("");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(false); }
  }

  async function askQuestion(question = qaQuestion) {
    if (!qaBook || busy || !question.trim()) return;
    setQaQuestion(""); setAskedQuestion(question.trim()); setBusy(true); setError(""); setQaResult(null);
    try { setQaResult(await api.ask(qaBook.book_id, question.trim())); }
    catch (value) { setError((value as Error).message); setQaQuestion(current => current || question); }
    finally { setBusy(false); }
  }

  const title = view === "social" ? "好友与消息" : view === "course" ? course?.chapter_title ?? "个人课程" : view === "interview" ? "学习诊断" : view === "library" ? "我的书架" : view === "community" ? "学习社区" : view === "qa" ? "教材答疑" : view === "profile" ? "我的学习" : view === "account" ? "个人资料" : "知我";

  return (
    <main className="stage" style={{ "--font-scale": fontScale } as React.CSSProperties}>
      <section className="phone" aria-label="知我手机端模拟界面">
        <div className="statusbar"><span>9:41</span><span>●●● 100%</span></div>
        <header className={`app-header ${view === "interview" || view === "course" || view === "account" ? "context-header" : "root-header"}`}>
          {(view === "interview" || view === "course" || view === "account") && <button disabled={busy} className="icon-button" aria-label="返回" onClick={() => setView(view==="account"?"profile":"home")}><Icon name="back"/></button>}
          <div><p>知我 · 个性化读书课</p><h1>{title}</h1></div>
          {view!=="profile"&&view!=="account"&&<img src={userProfile?.avatar_url??DEFAULT_AVATAR} alt="用户头像" />}
        </header>

        <div ref={contentRef} key={view} inert={busy && !["qa", "interview", "course", "account"].includes(view)} className={`app-content page-transition ${view === "qa" ? "qa-shell" : view === "social" ? "social-shell" : ""} ${view === "interview" || view === "course" ? "focused-content" : ""}`}>
          {!online && <div className="connection-note" role="status">网络已断开，已有内容仍可查看；恢复连接后可继续保存。</div>}
          {initializing && <div className="home-skeleton" role="status" aria-label="正在恢复学习进度"><span className="skeleton-line"/><div/><span className="skeleton-line"/><span className="skeleton-line short"/><p>正在取回你的教材与学习进度…</p></div>}
          {view === "home" && !initializing && <LearningHome book={book} structure={structure} profile={profile} completed={completed} diagnosed={diagnosed} busy={busy || !online} onStart={startInterview} onContinue={() => setView("interview")} onCourse={openCourse} />}
          {view === "library" && !initializing && <LibraryHub books={books} activeBook={book} busy={busy} onOpen={async (id) => { if (id !== book?.book_id) await loadCatalog(id); setView("home"); }} onRemove={async id=>{await api.removeBook(id); const remaining=await api.books(); if(id===book?.book_id)await loadCatalog(remaining[0]?.book_id,false);else setBooks(remaining);}} onRestore={async id=>{await api.restoreBook(id);if(!book)await loadCatalog(id,false);else setBooks(await api.books());}} onCommunity={()=>setView("community")} />}
          {view === "community" && !initializing && <CommunityPage onSocial={()=>setView("social")} books={books} onLibraryChanged={async()=>{setBooks(await api.books());}} onOpenLibrary={()=>setView("library")} />}
          {view === "social" && !initializing && <SocialPage books={books} onLibraryChanged={async()=>{setBooks(await api.books());}}/>}
          {view === "interview" && <DiagnosticJourney turn={turn ?? null} profile={profile} selected={selected} confidence={confidence} busy={busy} canSubmit={canSubmit} onSelect={(id) => setSelected((current) => turn?.response_type === "multiple_choice" ? (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]) : [id])} onConfidence={setConfidence} onSubmit={submitInterview} />}
          {view === "course" && course && <CourseView course={course} tab={courseTab} cardIndex={cardIndex} flipped={cardFlipped} practiceIndex={practiceIndex} input={input} confidence={confidence} activity={activity} needsRefresh={courseNeedsRefresh} busy={busy} onTab={(next) => { setCourseTab(next); setActivity(null); }} onFlip={() => { setCardFlipped((value) => !value); startedAt.current = Date.now(); }} onRate={reviewCard} onCard={(index) => { setCardIndex(index); setCardFlipped(false); setActivity(null); }} onPractice={(index) => { setPracticeIndex(index); setActivity(null); setInput(""); }} onInput={setInput} onConfidence={setConfidence} onSubmitPractice={submitPractice} onRefresh={() => openCourse(course.chapter_id)} />}
          {view === "qa" && !initializing && <TutorChat question={qaQuestion} askedQuestion={askedQuestion} result={qaResult} busy={busy} error={error} bookTitle={qaBook?.title ?? "书架还没有教材"} books={books} selectedBookId={qaBook?.book_id ?? ""} onBookChange={setQaBookId} currentBookId={book?.book_id} suggestions={suggestedQuestions(qaStructure)} available={!!qaBook && online} onQuestion={setQaQuestion} onAsk={askQuestion} />}
          {view === "profile" && <ProfileDashboard key={session?.session_id??book?.book_id} userProfile={userProfile} onAccount={()=>setView("account")} profile={profile} sessionId={session?.session_id} bookTitle={book?.title??"未选择教材"} completed={completed} reminder={reminder} fontScale={fontScale} onReminder={(value) => { setReminder(value); safeSet("zhiwo.reminder", value); }} onFont={(value) => { setFontScale(value); safeSet("zhiwo.font-scale", String(value)); }} onDiagnose={() => setView(completed ? "home" : session ? "interview" : "home")} />}
          {view === "account" && <UserProfilePage onBusy={setBusy} onSaved={value=>{setUserProfile(value);setView("profile");setNotice("个人资料已保存");}} onBack={()=>setView("profile")}/>}
          {view === "profile" && <AccountControls/>}
          {view === "profile" && <details className="why-card restart-diagnosis"><summary>重新做一次选择题诊断</summary><p>之前的学习记录仍保留在服务器。开始后，新诊断将成为当前学习进度，重新调整你的章节重点。</p><button className="secondary" disabled={busy || !book} onClick={startInterview}>开始新的诊断</button></details>}
          {error && view !== "qa" && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => !book ? void loadCatalog() : setError("")}>{!book ? "重新连接" : "知道了"}</button></div>}
        </div>

        {notice && <div className="notice-toast" role="status" key={notice}><span aria-hidden="true">✓</span>{notice}</div>}
        {busy && view !== "interview" && view !== "course" && view !== "qa" && view !== "account" && <div className="preparing-overlay" role="status" aria-live="polite"><div><span className="preparing-orb" aria-hidden="true">✦</span><h2>{completed ? "正在编排适合你的内容" : "正在准备选择题诊断"}</h2><p>{completed ? "结合教材、作答和薄弱点，生成有重点的讲解与闪卡。" : "读取书籍章节，准备了解你的学习起点。"}</p><span className="loading-dots" aria-hidden="true"><i/><i/><i/></span><small>请稍候，完成后自动进入</small></div></div>}
        {view !== "interview" && view !== "course" && view !== "account" && <nav inert={busy || initializing} className="bottom-nav" aria-label="主导航" style={{ "--nav-index": ["home", "library", "community", "qa", "profile"].indexOf(view === "social" ? "community" : view) } as React.CSSProperties}>
          <NavButton active={view === "home"} icon="home" label="学习" onClick={() => setView("home")} />
          <NavButton active={view === "library"} icon="book" label="书架" onClick={() => setView("library")} />
          <NavButton active={view === "community" || view === "social"} icon="community" label="社区" onClick={() => setView("community")} />
          <NavButton active={view === "qa"} icon="spark" label="答疑" onClick={() => setView("qa")} />
          <NavButton active={view === "profile"} icon="user" label="我的" onClick={() => setView("profile")} />
        </nav>}
        <div className="home-indicator" />
      </section>
    </main>
  );
}

function CourseView({ course, tab, cardIndex, flipped, practiceIndex, input, confidence, activity, needsRefresh, busy, onTab, onFlip, onRate, onCard, onPractice, onInput, onConfidence, onSubmitPractice, onRefresh }: { course: Course; tab: CourseTab; cardIndex: number; flipped: boolean; practiceIndex: number; input: string; confidence: number; activity: CourseActivity | null; needsRefresh: boolean; busy: boolean; onTab:(tab:CourseTab)=>void; onFlip:()=>void; onRate:(rating:"again"|"hard"|"good"|"easy")=>void; onCard:(index:number)=>void; onPractice:(index:number)=>void; onInput:(value:string)=>void; onConfidence:(value:number)=>void; onSubmitPractice:()=>void; onRefresh:()=>void }) {
  const practice=course.practice_items[practiceIndex];
  return <div className="course-page" data-tab={tab}><section className="course-hero"><div><span className="depth-chip">{depthLabel[course.decision.depth]}</span><h2>{course.chapter_title}</h2><p>{course.opening}</p></div><div className="course-meta"><span>约 {course.estimated_minutes} 分钟</span><span>{course.knowledge_points.length} 个知识点</span></div></section>{needsRefresh && <button className="refresh-banner" disabled={busy} onClick={onRefresh}>学习结果已更新画像 · 点击刷新本章编排 ↻</button>}<CourseTabs value={tab} busy={busy} onChange={onTab}/><div id="course-panel" role="tabpanel" aria-labelledby={`course-tab-${tab}`} className="course-panel tab-transition" key={tab}>{tab==="guide" && <div className="content-stack"><ContentCard title="本章总结" content={course.summary}/><section className="strategy-card"><span className="kicker">为什么这样编排</span><p>{course.decision.explanation}</p><div>{course.decision.scaffolds.map((item)=><b key={item}>{item}</b>)}</div></section>{course.worked_examples.map((item)=><ReadingCard key={item.title} item={item}/>)}</div>}{tab==="reading" && <div className="content-stack">{course.original_reading.map((item)=><ReadingCard key={`${item.title}-${item.content.slice(0,10)}`} item={item} excerpt/>)}</div>}{tab==="points" && <div className="content-stack">{course.knowledge_points.map((point)=><section className="point-card" key={point.point_id}><div><span>{point.state}</span><b>{point.state === "尚未验证" ? "待验证" : `${Math.round(point.mastery*100)}%`}</b></div><h3>{point.title}</h3>{hasAdditionalExplanation(point.title,point.explanation) && <p>{point.explanation}</p>}<small>第 {formatPages(point.citations)} 页</small></section>)}</div>}{tab==="cards" && <FlashcardDeck cards={course.flashcards} index={cardIndex} flipped={flipped} busy={busy} activity={activity} onFlip={onFlip} onCard={onCard} onRate={onRate}/>}{tab==="practice" && practice && <div className="practice-zone tab-transition" key={practice.item_id}><div className="practice-head"><span>随堂练习 {practiceIndex+1}/{course.practice_items.length}</span><small>约 {practice.estimated_seconds} 秒</small></div><h2>{practice.prompt}</h2><textarea className="answer-box" value={input} onChange={(event)=>onInput(event.target.value)} placeholder="先独立作答，再查看反馈" rows={7}/><label className="confidence-control"><span>这次有多确定？</span><b>{Math.round(confidence*100)}%</b><input type="range" min="0" max="1" step="0.05" value={confidence} onInput={(event)=>onConfidence(Number(event.currentTarget.value))}/></label><button className="primary" disabled={busy||!input.trim()} onClick={onSubmitPractice}>{busy?'正在核验…':'提交并核验'}</button>{activity && <PracticeFeedback evidence={activity.evidence}/>}<div className="practice-nav"><button disabled={busy||practiceIndex===0} onClick={()=>onPractice(practiceIndex-1)}>上一题</button><button disabled={busy||practiceIndex===course.practice_items.length-1} onClick={()=>onPractice(practiceIndex+1)}>下一题</button></div></div>}</div></div>;
}




function ContentCard({ title, content }: { title:string; content:string }) { return <section className="content-card"><span className="kicker">个性化内容</span><h2>{title}</h2><p>{content}</p></section>; }
function formatPages(citations: Array<{page_number:number}>) { return [...new Set(citations.map((citation) => citation.page_number))].join("、"); }

function ReadingCard({ item, excerpt = false }: { item:{title:string;content:string;purpose:string;citations:Array<{page_number:number}>}; excerpt?:boolean }) { return <section className="reading-card"><div><strong>{excerpt ? "原文摘录" : item.title}</strong><span>{item.purpose}</span></div><p>{excerpt ? `…${item.content}…` : item.content}</p><small>{excerpt && "省略号表示摘录边界 · "}教材第 {formatPages(item.citations)} 页</small></section>; }
function EmptyState({ text }: { text:string }) { return <div className="empty-state"><img src="/assets/brand/cloud-mascot-parsing.png" alt=""/><p>{text}</p></div>; }
function NavButton({active,icon,label,onClick}:{active:boolean;icon:IconName;label:string;onClick:()=>void}) { return <button aria-current={active?"page":undefined} className={active?"active":""} onClick={onClick}><Icon name={icon} size={22}/><span>{label}</span></button>; }

export default App;
