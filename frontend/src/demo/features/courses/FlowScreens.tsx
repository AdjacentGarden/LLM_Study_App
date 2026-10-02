import { StickerIcon, type StickerIconName } from "../../components/icons/StickerIcon";
import { useEffect, useLayoutEffect, useRef, useState, type ChangeEvent } from "react";
import {
  AlarmClock, ArrowLeft, BookOpen, CalendarDays, Check, CircleAlert, Clock3, Coffee,
  Dumbbell, FilePlus2, Globe2, Heart, Lightbulb, ListChecks, MessageCircle,
  NotebookPen, PenLine, Plus, Puzzle, RotateCcw, Search, Shuffle, Sparkles,
  Target, TrendingUp, Video, type LucideIcon
} from "lucide-react";
import { useAppContext } from "../../context/AppContext";
import { communityCatalog as communityBooks } from "../../services/CommunityRepository";
import { useReducedMotion } from "../../motion";
import {
  sourceResourceId,
  dailyTimes,
  diagnosisQuestions,
  localResourceId,
  primaryGoals,
  toggleDiagnosisValue,
  type CourseDiagnosis,
  type PrimaryGoal
} from "./model";

function FlowError({ message }: { message: string | null }) {
  return message ? <p className="learning-flow-error" role="alert">{message}</p> : null;
}

const onboardingGoalIcons: Record<PrimaryGoal, StickerIconName> = {
  systematic: "LibraryBig",
  exam: "Target",
  growth: "TrendingUp",
  interest: "Heart"
};

const diagnosisQuestionIcons: Record<string, LucideIcon> = {
  urgency: AlarmClock,
  timePattern: Clock3,
  goals: Target,
  contentFoci: Lightbulb,
  aids: MessageCircle,
  reviews: RotateCcw
};

const diagnosisOptionIcons: Record<string, LucideIcon> = {
  "urgency:urgent": AlarmClock,
  "urgency:steady": CalendarDays,
  "urgency:relaxed": Coffee,
  "timePattern:block": CalendarDays,
  "timePattern:fragmented": Puzzle,
  "goals:systematic": BookOpen,
  "goals:exam": Target,
  "goals:professional": TrendingUp,
  "goals:interest": Heart,
  "goals:gaps": Search,
  "contentFoci:keyPoints": ListChecks,
  "contentFoci:principles": Lightbulb,
  "contentFoci:practice": Globe2,
  "contentFoci:exercises": Dumbbell,
  "contentFoci:notes": NotebookPen,
  "aids:chat": MessageCircle,
  "aids:video": Video,
  "aids:mistakes": RotateCcw,
  "aids:guidedNotes": NotebookPen,
  "reviews:alongside": PenLine,
  "reviews:notes": NotebookPen,
  "reviews:periodic": CalendarDays,
  "reviews:mistakes": CircleAlert,
  "reviews:free": Shuffle
};

export function OnboardingScreen() {
  const { courses, replaceScreen } = useAppContext();
  const { onboardingDraft } = courses.state;
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const step = Math.min(2, Math.max(0, onboardingDraft.step));

  function update(patch: Parameters<typeof courses.updateOnboardingDraft>[0]) {
    try {
      courses.updateOnboardingDraft(patch);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "偏好保存失败，请重试");
    }
  }

  async function next(skip = false) {
    try {
      if (step === 0 && !onboardingDraft.displayName.trim()) {
        setError("请先填写称呼");
        nameRef.current?.focus();
        return;
      }
      if (step === 1) {
        if (skip) courses.updateOnboardingDraft({ primaryGoal: null });
        courses.updateOnboardingDraft({ step: 2 });
      } else if (step === 2) {
        if (skip) courses.updateOnboardingDraft({ dailyTime: null });
        await courses.completeOnboarding();
        replaceScreen("home");
      } else {
        courses.updateOnboardingDraft({ step: 1 });
      }
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败，请重试");
    }
  }

  function previous() {
    if (step > 0) update({ step: step - 1 });
  }

  useEffect(() => {
    const onNativeBack = (event: Event) => {
      if (document.activeElement instanceof HTMLInputElement
        || document.activeElement instanceof HTMLTextAreaElement
        || document.activeElement instanceof HTMLSelectElement) return;
      event.preventDefault();
      previous();
    };
    window.addEventListener("bookcourse:native-back", onNativeBack);
    return () => window.removeEventListener("bookcourse:native-back", onNativeBack);
  });

  return (
    <div className="learning-flow onboarding-flow">
      <div className="learning-flow-top">
        {step > 0 ? <button className="learning-flow-back" type="button" onClick={previous} aria-label="上一步"><ArrowLeft size={21} /></button> : <span />}
        <span className="learning-flow-progress" aria-label={`第 ${step + 1} 步，共 3 步`}><strong>{step + 1}</strong><span>/3</span></span>
        <span />
      </div>

      <div className="learning-flow-center">
        {step === 0 ? (
          <div className="learning-flow-question">
            <span className="learning-flow-symbol"><StickerIcon name="Sparkles" size={28} aria-hidden="true" /></span>
            <h1>我们怎么称呼你</h1>
            <label className="learning-flow-label" htmlFor="learner-name">你的称呼</label>
            <input
              ref={nameRef}
              id="learner-name"
              className="learning-flow-name"
              autoComplete="nickname"
              maxLength={30}
              placeholder="输入你的姓名或昵称"
              value={onboardingDraft.displayName}
              onChange={(event) => update({ displayName: event.target.value })}
              onKeyDown={(event) => { if (event.key === "Enter") next(); }}
            />
          </div>
        ) : step === 1 ? (
          <div className="learning-flow-question">
            <span className="learning-flow-symbol"><StickerIcon name="BookOpen" size={28} aria-hidden="true" /></span>
            <h1>你使用云径的主要目标是</h1>
            <p>选择此刻最重要的一项，之后可以修改。</p>
            <div className="learning-goal-grid" role="group" aria-label="主要学习目标">
              {primaryGoals.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`learning-goal-option ${onboardingDraft.primaryGoal === option.value ? "selected" : ""}`}
                  data-goal={option.value}
                  aria-pressed={onboardingDraft.primaryGoal === option.value}
                  onClick={() => update({ primaryGoal: option.value })}
                >
                  <span className="learning-goal-illustration" aria-hidden="true">
                    <span className="learning-goal-icon"><StickerIcon name={onboardingGoalIcons[option.value]} size={40} /></span>
                  </span>
                  <strong>{option.label}</strong>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="learning-flow-question">
            <span className="learning-flow-symbol"><StickerIcon name="Clock3" size={28} aria-hidden="true" /></span>
            <h1>你每天能投入多少学习时间</h1>
            <p>只用于安排建议，不会限制你自由学习。</p>
            <div className="learning-time-grid" role="group" aria-label="每日学习时间">
              {dailyTimes.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`learning-choice-chip ${onboardingDraft.dailyTime === option.value ? "selected" : ""}`}
                  aria-pressed={onboardingDraft.dailyTime === option.value}
                  onClick={() => update({ dailyTime: option.value })}
                >{option.label}</button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="learning-flow-bottom">
        <FlowError message={error} />
        <button className="learning-flow-primary" type="button" onClick={() => next()}>
          {step === 0 ? "下一页" : step === 1 ? "下一页" : "开始专属学习之旅"}
        </button>
        {step > 0 ? <button className="learning-flow-skip" type="button" onClick={() => next(true)}>跳过</button> : null}
      </div>
    </div>
  );
}

export function CourseSetupScreen() {
  const { sourceSummaries, courses, replaceScreen, selectSource, importCourseFile, selectCourse } = useAppContext();
  const { draft, resources } = courses.state;
  const [error, setError] = useState<string | null>(null);
  const [savingFile, setSavingFile] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [pageMotion, setPageMotion] = useState<"forward" | "back" | null>(null);
  const reducedMotion = useReducedMotion();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const answerErrorTimerRef = useRef<number | null>(null);
  const step = Math.max(-1, Math.min(5, draft?.step ?? -1));
  const previousStepRef = useRef(step);

  useLayoutEffect(() => {
    const previousStep = previousStepRef.current;
    if (step === previousStep) return;
    previousStepRef.current = step;
    setPageMotion(reducedMotion ? null : step > previousStep ? "forward" : "back");
  }, [reducedMotion, step]);

  useEffect(() => () => {
    if (answerErrorTimerRef.current !== null) window.clearTimeout(answerErrorTimerRef.current);
  }, []);

  useEffect(() => {
    const onNativeBack = (event: Event) => {
      if (document.activeElement instanceof HTMLInputElement
        || document.activeElement instanceof HTMLTextAreaElement
        || document.activeElement instanceof HTMLSelectElement) return;
      event.preventDefault();
      if (!draft || draft.step < 0) {
        replaceScreen(draft?.editingCourseId ? "courseDetail" : "upload");
        return;
      }
      try {
        if (draft.step === 0 && draft.uploadedCourse && draft.parseCompleted) {
          replaceScreen("processing");
        } else {
          courses.updateDraft({ step: draft.step - 1 });
          if (draft.step === 0 && !draft.editingCourseId) replaceScreen("upload");
        }
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "草稿保存失败，请重试");
      }
    };
    window.addEventListener("bookcourse:native-back", onNativeBack);
    return () => window.removeEventListener("bookcourse:native-back", onNativeBack);
  }, [draft, courses, replaceScreen]);

  useEffect(() => {
    if (!draft || draft.editingCourseId) return;
    if (draft.uploadedCourse && !draft.parseCompleted) {
      replaceScreen(draft.parseJobId ? "processing" : "parseReady");
    }
  }, [draft, replaceScreen]);

  if (!draft) return <div className="learning-flow-loading">课程草稿暂时不可用。<button type="button" onClick={() => replaceScreen("home")}>返回首页</button></div>;
  const activeDraft = draft;
  const question = step >= 0 ? diagnosisQuestions[step] : null;
  const QuestionIcon = question ? diagnosisQuestionIcons[question.key] : BookOpen;

  function showAnswerError() {
    const message = "请至少选择一项再继续";
    if (answerErrorTimerRef.current !== null) window.clearTimeout(answerErrorTimerRef.current);
    setError(message);
    answerErrorTimerRef.current = window.setTimeout(() => {
      answerErrorTimerRef.current = null;
      setError((current) => current === message ? null : current);
    }, 1000);
  }

  function update(patch: Parameters<typeof courses.updateDraft>[0]) {
    try {
      courses.updateDraft(patch);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "草稿保存失败，请重试");
    }
  }

  function previous() {
    if (step < 0) replaceScreen(activeDraft.editingCourseId ? "courseDetail" : "upload");
    else if (step === 0 && activeDraft.uploadedCourse && activeDraft.parseCompleted) replaceScreen("processing");
    else {
      update({ step: step - 1 });
      if (step === 0 && !activeDraft.editingCourseId) replaceScreen("upload");
    }
  }

  async function next() {
    if (step < 0) {
      if (!activeDraft.name.trim()) {
        setError("请填写课程名称");
        nameRef.current?.focus();
        return;
      }
      if (activeDraft.resourceIds.length === 0) {
        setError("请至少选择或添加一份书籍资料");
        return;
      }
      update({ step: 0 });
      return;
    }
    const answer = activeDraft.diagnosis[question!.key];
    if (!answer || (Array.isArray(answer) && answer.length === 0)) {
      showAnswerError();
      return;
    }
    if (step < 5) {
      update({ step: step + 1 });
      return;
    }
    try {
      const confirmParsedCourse = !activeDraft.editingCourseId && Boolean(activeDraft.uploadedCourse);
      if (confirmParsedCourse) {
        if (!activeDraft.parseCompleted || !activeDraft.uploadedCourse) {
          throw new Error("请先完成教材解析");
        }
        setCompleting(true);
        const loaded = await selectSource(activeDraft.uploadedCourse.bookId);
        if (!loaded) throw new Error("解析结果暂时无法打开，请稍后重试");
      }
      const completedId = await courses.completeDraft();
      if (!confirmParsedCourse) await selectCourse(completedId);
      replaceScreen(confirmParsedCourse ? "chapterConfirm" : "courseDetail");
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "课程保存失败，请重试");
    } finally {
      setCompleting(false);
    }
  }

  function toggleResource(resourceId: string) {
    update({ resourceIds: activeDraft.resourceIds.includes(resourceId)
      ? activeDraft.resourceIds.filter((id) => id !== resourceId)
      : [...activeDraft.resourceIds, resourceId] });
  }

  function selectAnswer(value: string) {
    if (!question) return;
    const key = question.key;
    const current = activeDraft.diagnosis[key];
    const answer = question.multiple
      ? toggleDiagnosisValue(Array.isArray(current) ? current : [], value)
      : value;
    update({ diagnosis: { ...activeDraft.diagnosis, [key]: answer } as Partial<CourseDiagnosis> });
  }

  async function addFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    setSavingFile(true);
    setError(null);
    try {
      for (const file of files) await importCourseFile(file);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "资料保存失败，请重试");
    } finally {
      setSavingFile(false);
    }
  }

  return (
    <div className="learning-flow course-space-flow">
      <div className="learning-flow-top">
        <button className="learning-flow-back" type="button" onClick={previous} aria-label="上一步"><ArrowLeft size={21} /></button>
        <span className="learning-flow-progress" aria-label={step < 0 ? "课程基本信息" : `第 ${step + 1} 类，共 6 类`}>
          {step < 0 ? <strong>创建课程</strong> : <><strong>{step + 1}</strong><span>/6</span></>}
        </span>
        <span />
      </div>

      <div className="learning-flow-page" key={step} data-flow-motion={pageMotion ?? "idle"} onAnimationEnd={(event) => {
        if (event.target === event.currentTarget) setPageMotion(null);
      }}>
      {step < 0 ? (
        <div className="course-space-details">
          <span className="learning-flow-symbol"><StickerIcon name="BookOpen" size={24} aria-hidden="true" /></span>
          <h1>{draft.editingCourseId ? "调整课程" : "创建课程"}</h1>
          <p>把同一目标下的书籍和资料放在一起，再设定适合自己的学习方式。</p>
          <label className="learning-flow-label" htmlFor="course-space-name">课程名称</label>
          <input
            ref={nameRef}
            id="course-space-name"
            className="learning-flow-name"
            maxLength={40}
            placeholder="例如：期末生物复习"
            value={draft.name}
            onChange={(event) => update({ name: event.target.value })}
          />
          <div className="course-space-resource-heading"><h2>加入书籍资料</h2><span>至少一份</span></div>
          <div className="course-space-picker" role="group" aria-label="选择已有书籍资料">
            {sourceSummaries.filter((source) => !source.book_id.startsWith("book_local_")).map((course) => {
              const resourceId = sourceResourceId(course.book_id);
              const selected = draft.resourceIds.includes(resourceId);
              const uploadedNow = draft.uploadedCourse?.bookId === course.book_id;
              return (
                <button className={`learning-resource-option ${selected ? "selected" : ""}`} type="button" key={resourceId} aria-pressed={selected} disabled={uploadedNow} onClick={() => toggleResource(resourceId)}>
                  <BookOpen size={20} aria-hidden="true" />
                  <span><strong>{course.title}</strong><small>{uploadedNow ? "刚上传 · 完成问卷后继续生成课程" : course.status === "ready" ? "可开始学习" : "待整理"}</small></span>
                  {selected ? <Check size={18} aria-hidden="true" /> : <Plus size={18} aria-hidden="true" />}
                </button>
              );
            })}
            {draft.resourceIds.filter((resourceId) => resourceId.startsWith("source:") && !sourceSummaries.some((course) => sourceResourceId(course.book_id) === resourceId)).map((resourceId) => {
              const book = communityBooks.find((item) => sourceResourceId(item.id) === resourceId);
              const uploadedNow = draft.uploadedCourse && resourceId === sourceResourceId(draft.uploadedCourse.bookId);
              return <button className="learning-resource-option selected" type="button" key={resourceId} aria-pressed="true" disabled={Boolean(uploadedNow)} onClick={() => toggleResource(resourceId)}>
                <BookOpen size={20} aria-hidden="true" /><span><strong>{book?.title ?? draft.uploadedCourse?.name ?? "已选择课程"}</strong><small>{uploadedNow ? "刚上传 · 完成问卷后继续生成课程" : "待整理"}</small></span><Check size={18} aria-hidden="true" />
              </button>;
            })}
            {resources.map((resource) => {
              const resourceId = localResourceId(resource.id);
              const selected = draft.resourceIds.includes(resourceId);
              return (
                <button className={`learning-resource-option ${selected ? "selected" : ""}`} type="button" key={resourceId} aria-pressed={selected} onClick={() => toggleResource(resourceId)}>
                  <FilePlus2 size={20} aria-hidden="true" />
                  <span><strong>{resource.name}</strong><small>本地资料 · {resource.status === "ready" ? "可开始学习" : resource.status === "error" ? "整理失败，可重试" : "待整理"}</small></span>
                  {selected ? <Check size={18} aria-hidden="true" /> : <Plus size={18} aria-hidden="true" />}
                </button>
              );
            })}
          </div>
          <input ref={fileInputRef} type="file" multiple hidden onChange={(event) => void addFiles(event)} />
          <button className="learning-add-file" type="button" disabled={savingFile} onClick={() => fileInputRef.current?.click()}>
            <FilePlus2 size={19} aria-hidden="true" />{savingFile ? "正在保存资料…" : "添加本地资料"}
          </button>
        </div>
      ) : (
        <div className="learning-flow-center">
          <div className="learning-flow-question learning-diagnosis-question">
            <span className="learning-flow-symbol"><QuestionIcon size={24} aria-hidden="true" /></span>
            <h1>{question!.title}</h1>
            <p>{question!.helper}</p>
            <div className="learning-diagnosis-options" data-question={question!.key} data-option-count={question!.options.length} role="group" aria-label={question!.title}>
              {question!.options.map((option) => {
                const answer = draft.diagnosis[question!.key];
                const selected = Array.isArray(answer) ? answer.includes(option.value as never) : answer === option.value;
                const OptionIcon = diagnosisOptionIcons[`${question!.key}:${option.value}`] ?? Sparkles;
                return (
                  <button className={`learning-choice-chip ${selected ? "selected" : ""}`} key={option.value} type="button" aria-pressed={selected} onClick={() => selectAnswer(option.value)}>
                    <span className="learning-diagnosis-option-icon" aria-hidden="true"><OptionIcon size={20} strokeWidth={2.5} /></span>
                    <span className="learning-diagnosis-option-label">{option.label}</span>
                    <Check className="learning-diagnosis-option-check" size={16} aria-hidden="true" />
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}
      </div>

      <div className="learning-flow-bottom">
        <FlowError message={error} />
        <button className="learning-flow-primary" type="button" disabled={completing} onClick={() => void next()}>{completing ? "正在准备课程目录…" : step === 5 ? draft.uploadedCourse ? "完成问卷，查看课程目录" : "完成，进入课程" : "下一页"}</button>
        {step < 0 ? <button className="learning-flow-skip" type="button" onClick={() => replaceScreen("courseDetail")}>返回课程，草稿已保存</button> : null}
      </div>
    </div>
  );
}
