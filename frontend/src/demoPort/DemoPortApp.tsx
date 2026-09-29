import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStudyState } from "../app/useStudyState";
import { useStudyWorkspace } from "../app/useStudyWorkspace";
import { captureStorage, safeGet, safeSet } from "../components/bookContext";
import { DemoShell, type DemoTab } from "./DemoShell";
import { DemoHome } from "./DemoHome";
import { DemoStudy } from "./DemoStudy";
import { DemoLesson } from "./DemoLesson";
import { DemoFlashcards } from "./DemoFlashcards";
import { DemoAssignment } from "./DemoAssignment";
import { DemoMistakes } from "./DemoMistakes";
import { DemoSourceReader } from "./DemoSourceReader";
import { DemoLibrary } from "./DemoLibrary";
import { DemoProfile } from "./DemoProfile";
import { DemoPlan } from "./DemoPlan";
import { DemoCommunity } from "./DemoCommunity";
import { DemoQa } from "./DemoQa";
import { DemoAssistant, type AssistantMessage } from "./DemoAssistant";
import { DemoDiagnosis } from "./DemoDiagnosis";
import { DemoMaterials } from "./DemoMaterials";
import { DemoSupplementTools } from "./DemoSupplementTools";
import type { ChapterMode } from "./types";
import { UploadDialog } from "../screens/UploadDialog";
import { SocialPage } from "../components/SocialPage";
import { ProfileDashboard } from "../components/ProfileDashboard";
import { UserProfilePage } from "../components/UserProfilePage";
import { StudioProvider } from "../components/LearningStudio";
import type { StudyTask } from "../types/studyWorkspace";
import { cleanChapterTitle } from "../services/mappers/chapterTitles";

type View = "home" | "study" | "discover" | "profile" | "library" | "materials" | "diagnosis" | "course" | "qa" | "source" | "social" | "report" | "account" | "plan" | "mistakes" | "supplement";
const mainTabs: View[] = ["home", "study", "discover", "profile"];

export function DemoPortApp() {
  const study = useStudyState();
  const workspaceRevision = useMemo(() => ({ profile: study.session?.profile, review: study.returnRevision }), [study.session?.profile, study.returnRevision]);
  const workspace = useStudyWorkspace(study.repository, study.session?.session_id ?? null, study.book?.book_id ?? null, workspaceRevision);
  const rootStorage = useMemo(captureStorage, [study.account.user_id]);
  const [view, setView] = useState<View>(() => {
    const saved = safeGet("cloudpath.demo-port.main-tab");
    return mainTabs.includes(saved as View) ? saved as View : "home";
  });
  const [stack, setStack] = useState<View[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [sourcePage, setSourcePage] = useState(1);
  const sourceSave = useRef<(() => Promise<boolean>) | null>(null);
  const registerSourceSave = useCallback((save: (() => Promise<boolean>) | null) => { sourceSave.current = save; }, []);
  const [courseMode, setCourseMode] = useState<ChapterMode>("reading");
  const [supplementChapterId, setSupplementChapterId] = useState("");
  const [mistakesChapterId, setMistakesChapterId] = useState<string>();
  const [qaDraft, setQaDraft] = useState("");
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([]);
  useEffect(() => setAssistantMessages([]), [study.book?.book_id]);
  const [reminder, setReminder] = useState(() => safeGet("zhiwo.reminder") || "20:30");
  const [fontScale, setFontScale] = useState(() => Math.min(1.2, Math.max(.9, Number(safeGet("zhiwo.font-scale")) || 1)));
  const go = useCallback((next: View) => { setStack(old => [...old, view]); setView(next); }, [view]);
  const tab = useCallback((next: View) => { setView(next); setStack([]); safeSet("cloudpath.demo-port.main-tab", next); }, []);
  const back = useCallback(() => {
    const navigate = () => setStack(old => { const copy = [...old]; setView(copy.pop() ?? "study"); return copy; });
    if (view === "source" && sourceSave.current) void sourceSave.current().then(saved => { if (saved) navigate(); });
    else navigate();
  }, [view]);
  const openSource = useCallback((page: number) => { if (Number.isInteger(page) && page > 0) { setSourcePage(page); go("source"); } }, [go]);
  async function openDiagnosis() {
    if (study.session && study.session.phase !== "complete") { go("diagnosis"); return; }
    if (await study.startDiagnosis()) go("diagnosis");
  }
  async function openChapter(chapterId: string, mode: ChapterMode = "reading") {
    if (study.structure?.chapters.find(item => item.chapter_id === chapterId)?.has_supplementary_content) {
      setSupplementChapterId(chapterId); setCourseMode(mode); go("supplement"); return;
    }
    if (!study.session || study.session.phase !== "complete") { await openDiagnosis(); return; }
    if (await study.openCourse(chapterId)) { setCourseMode(mode); go("course"); }
  }
  function openMistakes(chapterId?: string) {
    if (chapterId && study.structure?.chapters.find(item => item.chapter_id === chapterId)?.has_supplementary_content) {
      setSupplementChapterId(chapterId); setCourseMode("mistakes"); go("supplement"); return;
    }
    setMistakesChapterId(chapterId); go("mistakes");
  }
  function openTask(task: StudyTask) {
    const chapterId = task.section_id ?? task.chapter_id;
    if (task.kind === "reading" && task.source_start_page && !study.structure?.chapters.find(item => item.chapter_id === chapterId)?.knowledge_points.length) {
      openSource(task.source_start_page); return;
    }
    void openChapter(chapterId, task.kind === "flashcards" ? "cards" : task.kind === "practice" ? "practice" : "reading");
  }
  async function completeReading(chapterId: string) {
    const task = workspace.workspace?.plan.days.flatMap(day => day.tasks).find(item => item.kind === "reading" && (item.section_id ?? item.chapter_id) === chapterId);
    if (task && task.status !== "done" && !await workspace.setTask(task.task_id, "done")) return;
    tab("study");
  }
  const currentTab: DemoTab | null = view === "discover" ? "community" : mainTabs.includes(view) ? view as DemoTab : null;
  const title: Record<View, string | undefined> = {
    home: undefined, study: undefined, discover: "发现", profile: "我的", library: "我的课程", materials: "闪卡与笔记",
    diagnosis: "学习诊断", course: courseMode === "practice" ? "作业诊断" : courseMode === "cards" ? "知识点闪卡" : courseMode === "mistakes" ? "错题集" : "章节学习", qa: "教材答疑", source: "原文文档", social: "好友与消息",
    report: "学习报告", account: "个人资料", plan: "学习计划", mistakes: "错题集",
    supplement: courseMode === "practice" ? "作业诊断" : courseMode === "cards" ? "知识点闪卡" : courseMode === "mistakes" ? "错题集" : "章节学习",
  };
  return <StudioProvider><div className="demo-port-root next-root" style={{ "--next-font-scale": fontScale } as React.CSSProperties}>
    <DemoShell active={currentTab} title={title[view]} subtitle={view === "course" && study.course ? cleanChapterTitle(study.course.chapter_title) : undefined}
      onBack={back} onTab={next => tab(next === "community" ? "discover" : next)} onUpload={() => setUploadOpen(true)}
      assistant={close => <DemoAssistant key={study.book?.book_id ?? "no-book"} book={study.book} structure={study.structure}
        busy={study.busy} error={study.error} messages={assistantMessages} onMessages={setAssistantMessages}
        onAsk={study.ask} onSource={page => { close(); openSource(page); }}
        onRemembered={() => study.setReturnRevision(v => v + 1)} />} notice={study.notice}
      scrollKey={`${view}:${study.book?.book_id ?? ""}:${study.course?.course_id ?? ""}:${sourcePage}`}>
      {study.initializing ? <div className="next-loading" role="status"><span /><p>正在打开你的学习空间…</p></div> : <>
        {study.error && view !== "qa" && <div className="next-global-error" role="alert"><p>{study.error}</p><button onClick={() => study.setError("")}>关闭</button><button onClick={() => void study.load()}>重试</button></div>}
        {view === "home" && <DemoHome books={study.books} activeBook={study.book} structure={study.structure} session={study.session}
          profile={study.userProfile} loading={study.initializing} busy={study.busy} error={study.error} onReload={() => void study.load()}
          onSelectBook={id => void study.selectBook(id)} onOpenBook={id => { void study.selectBook(id).then(ok => { if (ok) tab("study"); }); }}
          onLibrary={() => go("library")} onUpload={() => setUploadOpen(true)} onSource={openSource}
          onChapter={(id, tool) => { if (tool === "mistakes") openMistakes(id); else void openChapter(id, tool === "assignment" ? "practice" : tool === "flashcards" ? "cards" : "reading"); }}
          onPlan={() => go("plan")} onMistakes={() => openMistakes()} />}
        {view === "study" && <DemoStudy books={study.books} book={study.book} structure={study.structure} session={study.session}
          workspace={workspace.workspace} loading={study.initializing || study.busy} userId={study.account.user_id}
          onSelectBook={study.selectBook} onChapter={(id, mode) => void openChapter(id, mode)} onPlan={() => go("plan")}
          onMistakes={openMistakes} onUpload={() => setUploadOpen(true)} onLibrary={() => go("library")} onSource={openSource}
          onAsk={() => go("qa")} onDoubt={doubt => { setQaDraft(doubt.question); go("qa"); }}
          onReviewed={() => study.setReturnRevision(value => value + 1)} returnRevision={study.returnRevision} />}
        {view === "library" && <DemoLibrary books={study.books} activeId={study.book?.book_id ?? null} workspace={workspace.workspace} busy={study.busy}
          onSelect={id => { void study.selectBook(id).then(ok => { if (ok) tab("study"); }); }} onRemove={study.removeBook}
          onUpload={() => setUploadOpen(true)} onDiscover={() => tab("discover")} onMaterials={() => go("materials")} />}
        {view === "diagnosis" && <DemoDiagnosis session={study.session} busy={study.busy}
          onSubmit={async (selected, answer, confidence) => { const next = await study.submitDiagnosis(selected, answer, confidence); if (next?.phase === "complete") tab("study"); }} />}
        {view === "course" && courseMode === "reading" && study.course && study.book && <DemoLesson book={study.book} structure={study.structure}
          chapterId={study.course.chapter_id} course={study.course} onSource={openSource} onAsk={() => go("qa")}
          onUpdate={study.updateCourse} onComplete={() => void completeReading(study.course!.chapter_id)} onUpload={() => setUploadOpen(true)}
          onLibrary={() => go("library")} onNotice={study.setNotice} />}
        {view === "course" && courseMode === "cards" && study.course && study.book && <DemoFlashcards book={study.book} course={study.course}
          onSource={openSource} onReview={study.review} onUpdate={study.updateCourse} onBack={back} onNotice={study.setNotice} />}
        {view === "course" && courseMode === "practice" && study.course && <DemoAssignment course={study.course} onPractice={study.submitPractice}
          onSource={openSource} onMistakes={() => openMistakes(study.course?.chapter_id)} onCards={() => setCourseMode("cards")}
          onReading={() => setCourseMode("reading")} onBack={back} />}
        {view === "supplement" && courseMode === "reading" && study.book && <DemoLesson book={study.book} structure={study.structure}
          chapterId={supplementChapterId} course={null} onSource={openSource} onAsk={() => go("qa")}
          onUpdate={() => Promise.resolve(false)} onComplete={() => { rootStorage.safeSet(`cloudpath.chapter-read:${study.book?.book_id}:${supplementChapterId}`, "done"); tab("study"); }}
          onUpload={() => setUploadOpen(true)} onLibrary={() => go("library")} onNotice={study.setNotice} />}
        {view === "supplement" && courseMode !== "reading" && study.book && <DemoSupplementTools key={`${study.account.user_id}:${study.book.book_id}:${supplementChapterId}`}
          book={study.book} chapterId={supplementChapterId} mode={courseMode} onSource={openSource} onBack={back} onNotice={study.setNotice}
          onMode={setCourseMode} />}
        {view === "plan" && <DemoPlan book={study.book} workspace={workspace.workspace} loading={workspace.loading} error={workspace.error}
          busy={study.busy || workspace.busy} onReload={() => void workspace.reload()} onSetTask={workspace.setTask} onOpenTask={openTask}
          onUpload={() => setUploadOpen(true)} />}
        {view === "mistakes" && <DemoMistakes book={study.book} workspace={workspace.workspace} loading={workspace.loading} error={workspace.error}
          busy={study.busy || workspace.busy} initialChapterId={mistakesChapterId} onReload={() => void workspace.reload()}
          onRetry={study.retryMistake} onStatus={workspace.setMistakeStatus} onCourse={id => void openChapter(id)} onSource={openSource}
          onUpload={() => setUploadOpen(true)} />}
        {view === "qa" && <DemoQa book={study.book} structure={study.structure} question={study.qaQuestion} result={study.qaResult}
          busy={study.busy} error={study.error} onAsk={study.ask} onSource={openSource} onRemembered={() => study.setReturnRevision(v => v + 1)} initialDraft={qaDraft} />}
        {view === "source" && study.book && <DemoSourceReader bookId={study.book.book_id} pageNumber={sourcePage} title={study.book.title}
          repository={study.repository} onPage={setSourcePage} onBack={back} onRegisterSave={registerSourceSave} />}
        {view === "discover" && <DemoCommunity books={study.books} onLibraryChanged={async finalId => { if (!(await study.refreshLibrary(finalId))) throw new Error("书架暂时无法刷新，请重试。"); }}
          onOpenLibrary={() => go("library")} onSocial={() => go("social")} />}
        {view === "profile" && <DemoProfile profile={study.userProfile} accountEmail={study.account.account?.email || null}
          userId={study.account.user_id} book={study.book} books={study.books} session={study.session} workspace={workspace.workspace}
          repository={study.repository} onAccount={() => go("account")} onSocial={() => go("social")} onReport={() => go("report")}
          onMaterials={() => go("materials")} onDiagnosis={() => void openDiagnosis()} onPlan={() => go("plan")}
          onLibrary={() => go("library")} onTask={() => go("plan")} onQa={() => go("qa")} />}
        {view === "materials" && <DemoMaterials books={study.books} activeBook={study.book} onDiscover={() => tab("discover")} />}
        {view === "social" && <SocialPage books={study.books} onLibraryChanged={async () => { await study.refreshLibrary(); }} />}
        {view === "report" && <ProfileDashboard userProfile={study.userProfile} onAccount={() => go("account")} profile={study.session?.profile}
          sessionId={study.session?.session_id} bookTitle={study.book?.title || "未选择教材"} completed={study.session?.phase === "complete"}
          reminder={reminder} fontScale={fontScale} onReminder={value => { setReminder(value); safeSet("zhiwo.reminder", value); }}
          onFont={value => { setFontScale(value); safeSet("zhiwo.font-scale", String(value)); }} onDiagnose={() => void openDiagnosis()} />}
        {view === "account" && <UserProfilePage onBusy={() => {}} onSaved={value => { study.setUserProfile(value); back(); study.setNotice("个人资料已保存"); }} onBack={back} />}
      </>}
    </DemoShell>
    <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} onClaimed={async id => {
      if (!(await study.refreshLibrary(id))) throw new Error("教材已加入书架，但暂时无法刷新，请重试。");
      tab("study");
    }} repository={study.repository} />
  </div></StudioProvider>;
}
