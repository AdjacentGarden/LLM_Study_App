import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpenCheck,
  Check,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  Lightbulb,
  ListChecks,
  Upload,
  X
} from "lucide-react";
import { runtimeConfig } from "../config/runtime";
import {
  Button,
  Card
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { localMotionFallbackMs, MotionErrorShake, useOneShotFeedback, useReducedMotion } from "../motion";
import {
  buildAssignmentExercises,
  getNextAssignmentExerciseIndex
} from "./assignmentExercises";

type JudgmentAnswer = "correct" | "incorrect";
type AssignmentCardSwitchState = "idle" | "exiting" | "entering";

const assignmentCardExitAnimationName = "motion-assignment-card-postcard-out";
const assignmentCardEnterAnimationName = "motion-assignment-card-postcard-in";

const exerciseIcons = {
  judgment: CheckCircle2,
  choice: ListChecks,
  "short-answer": FileText
} as const;

export function AssignmentScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const { activeChapterId, answer, generatedLessons, generatedQuizzes, go, openSourcePage, parsedChapters, setAnswer, setLatestDiagnosis, showToast, uploadedFile } = useAppContext();
  const reducedMotion = useReducedMotion();
  const [exerciseIndex, setExerciseIndex] = useState(0);
  const [judgmentAnswer, setJudgmentAnswer] = useState<JudgmentAnswer | null>(null);
  const [choiceAnswer, setChoiceAnswer] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [answerFieldActive, setAnswerFieldActive] = useState(false);
  const [answerCheckState, setAnswerCheckState] = useState<"entering" | "idle">("idle");
  const [answerError, setAnswerError] = useState<string | null>(null);
  const [cardSwitchState, setCardSwitchState] = useState<AssignmentCardSwitchState>("idle");
  const [pendingExerciseIndex, setPendingExerciseIndex] = useState<number | null>(null);
  const answerFeedback = useOneShotFeedback();
  const answerRef = useRef<HTMLTextAreaElement | null>(null);
  const answerPresenceRef = useRef<boolean | null>(null);
  const questionHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const hasMountedExerciseRef = useRef(false);
  const liveChapter = parsedChapters?.find((chapter) => chapter.chapter_id === activeChapterId) ?? parsedChapters?.[0] ?? null;
  const assignmentExercises = useMemo(() => buildAssignmentExercises(
    (generatedQuizzes ?? []).filter((quiz) => quiz.chapter_id === liveChapter?.chapter_id)
  ), [generatedQuizzes, liveChapter?.chapter_id]);
  const currentExercise = assignmentExercises[exerciseIndex] ?? assignmentExercises[0];
  const ExerciseIcon = currentExercise ? exerciseIcons[currentExercise.id] : FileText;
  const displayedPageStart = currentExercise?.printedPageStart ?? currentExercise?.pageStart;
  const displayedPageEnd = currentExercise?.printedPageEnd ?? currentExercise?.pageEnd;
  const canOpenSource = Boolean(uploadedFile && currentExercise?.sourceKind === "textbook" && currentExercise.pageStart > 0);
  const sourcePageLabel = canOpenSource && displayedPageStart
    ? displayedPageStart === displayedPageEnd
      ? `教材第 ${displayedPageStart} 页`
      : `教材第 ${displayedPageStart}-${displayedPageEnd} 页`
    : "AI 补充 · 待原文核验";
  const hasAnswer = answer.trim().length > 0;
  const canContinue = currentExercise?.id === "judgment"
    ? judgmentAnswer !== null
    : currentExercise.id === "choice"
      ? choiceAnswer !== null
      : hasAnswer;

  useEffect(() => {
    const previous = answerPresenceRef.current;
    answerPresenceRef.current = hasAnswer;
    if (previous === null || !hasAnswer || reducedMotion) {
      setAnswerCheckState("idle");
      return;
    }
    if (!previous) setAnswerCheckState("entering");
  }, [hasAnswer, reducedMotion]);

  useEffect(() => {
    if (!hasMountedExerciseRef.current) {
      hasMountedExerciseRef.current = true;
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      questionHeadingRef.current?.closest<HTMLElement>(".screen-content")?.scrollTo({ top: 0, behavior: "auto" });
      questionHeadingRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [exerciseIndex]);

  useEffect(() => {
    if (cardSwitchState === "idle") return;

    const fallback = window.setTimeout(() => {
      if (cardSwitchState === "exiting") {
        if (pendingExerciseIndex !== null) setExerciseIndex(pendingExerciseIndex);
        setCardSwitchState(pendingExerciseIndex === null ? "idle" : "entering");
        return;
      }
      setCardSwitchState("idle");
      setPendingExerciseIndex(null);
    }, localMotionFallbackMs);

    return () => window.clearTimeout(fallback);
  }, [cardSwitchState, pendingExerciseIndex]);

  useEffect(() => {
    if (!reducedMotion || cardSwitchState === "idle") return;
    if (pendingExerciseIndex !== null) setExerciseIndex(pendingExerciseIndex);
    setCardSwitchState("idle");
    setPendingExerciseIndex(null);
  }, [cardSwitchState, pendingExerciseIndex, reducedMotion]);

  useEffect(() => {
    setExerciseIndex(0);
    setJudgmentAnswer(null);
    setChoiceAnswer(null);
    setAnswer("");
    setCardSwitchState("idle");
    setPendingExerciseIndex(null);
  }, [liveChapter?.chapter_id, setAnswer]);

  function openAssignmentSource() {
    if (uploadedFile && liveChapter && currentExercise && canOpenSource) {
      openSourcePage({
        bookId: uploadedFile.bookId,
        title: liveChapter.source_title,
        pageStart: currentExercise.pageStart,
        pageEnd: currentExercise.pageEnd
      });
    }
  }

  function continueToNextExercise() {
    if (!canContinue || cardSwitchState !== "idle") return;
    const nextExerciseIndex = getNextAssignmentExerciseIndex(exerciseIndex, assignmentExercises.length);
    if (nextExerciseIndex === exerciseIndex) return;

    if (reducedMotion) {
      setExerciseIndex(nextExerciseIndex);
      return;
    }

    setPendingExerciseIndex(nextExerciseIndex);
    setCardSwitchState("exiting");
  }

  function settleCardSwitch(animationName: string) {
    if (cardSwitchState === "exiting" && animationName === assignmentCardExitAnimationName) {
      if (pendingExerciseIndex !== null) setExerciseIndex(pendingExerciseIndex);
      setCardSwitchState(pendingExerciseIndex === null ? "idle" : "entering");
      return;
    }

    if (cardSwitchState === "entering" && animationName === assignmentCardEnterAnimationName) {
      setCardSwitchState("idle");
      setPendingExerciseIndex(null);
    }
  }

  async function submit() {
    if (!uploadedFile || !liveChapter) {
      showToast("请先选择已解析课程章节，再提交作业诊断", "warning");
      return;
    }
    if (!answer.trim()) {
      setAnswerError("请先填写答案，再提交作业诊断。");
      answerFeedback.trigger();
      answerRef.current?.focus({ preventScroll: true });
      answerRef.current?.scrollIntoView({ block: "center", inline: "nearest" });
      return;
    }
    setAnswerError(null);
    answerFeedback.settle();
    setLoading(true);
    try {
      const assignmentId = `assignment_${liveChapter.chapter_id}`;
      const question = assignmentExercises
        .map((exercise, index) => `${index + 1}. ${exercise.label}：${exercise.prompt}`)
        .join("\n");
      const combinedAnswer = [
        `判断题：${judgmentAnswer === "correct" ? "正确" : "错误"}`,
        `选择题：${choiceAnswer ?? "未作答"}`,
        `简答题：${answer.trim()}`
      ].join("\n");
      const submission = await bookcourseRepository.submitAssignment(assignmentId, {
        user_id: runtimeConfig.defaultUserId,
        book_id: uploadedFile.bookId,
        lesson_id: generatedLessons?.find((lesson) => lesson.chapter_id === liveChapter.chapter_id)?.lesson_id ?? `lesson_${liveChapter.chapter_id}`,
        chapter_id: liveChapter.chapter_id,
        question,
        answer: combinedAnswer
      });
      const result = await bookcourseRepository.diagnoseAssignment(assignmentId, submission.submission_id);
      setLatestDiagnosis(result);
      showToast("三题练习已完成，诊断已生成");
      go("diagnosis");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "作业诊断失败", "warning");
    } finally {
      setLoading(false);
    }
  }

  if (!uploadedFile || !liveChapter || !currentExercise) {
    return (
      <div className="screen-stack assignment-screen">
        <Card className="parse-empty-card">
          <ClipboardCheck size={34} aria-hidden="true" />
          <h2>暂无可练习的章节</h2>
          <p>{uploadedFile && liveChapter ? "本节练习内容尚未生成。" : "完成教材解析并进入章节后，就能按判断题、选择题、简答题的顺序检查理解。"}</p>
          <Button icon={<Upload size={18} aria-hidden="true" />} onClick={() => go("upload")}>上传教材</Button>
          <Button variant="secondary" onClick={() => go("library")}>查看课程库</Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="screen-stack assignment-screen">
      <div className="assignment-workspace assignment-practice-workspace">
        <Card className="assignment-progress-card" aria-label="练习进度">
          <div className="assignment-progress-heading">
            <span className="assignment-progress-icon" aria-hidden="true">
              <BookOpenCheck size={20} />
            </span>
            <div>
              <small>本节练习</small>
              <h2>{liveChapter.source_title}</h2>
            </div>
            <strong>{exerciseIndex + 1} / {assignmentExercises.length}</strong>
          </div>
          <div
            className="assignment-progress-track"
            role="progressbar"
            aria-label={`练习进度，第 ${exerciseIndex + 1} 题，共 ${assignmentExercises.length} 题`}
            aria-valuemin={1}
            aria-valuemax={assignmentExercises.length}
            aria-valuenow={exerciseIndex + 1}
          >
            <span style={{ width: `${((exerciseIndex + 1) / assignmentExercises.length) * 100}%` }} />
          </div>
        </Card>

        <Card
          className="assignment-card assignment-exercise-card"
          data-assignment-type={currentExercise.id}
          data-motion-assignment-card-state={cardSwitchState}
          data-motion-assignment-card-key={currentExercise.questionId}
          data-motion-assignment-selection={currentExercise.id === "short-answer" && answerFieldActive ? "selected" : "idle"}
          data-motion-assignment-submit={loading ? "submitting" : "idle"}
          aria-busy={cardSwitchState !== "idle"}
          aria-live="polite"
          onAnimationEnd={(event) => {
            if (event.target === event.currentTarget) settleCardSwitch(event.animationName);
          }}
        >
          <div className="assignment-exercise-kicker">
            <span aria-hidden="true"><ExerciseIcon size={19} /></span>
            <strong>{currentExercise.label} · 第 {exerciseIndex + 1} 题</strong>
          </div>
          <h2 ref={questionHeadingRef} tabIndex={-1} className="assignment-question">
            {currentExercise.prompt}
          </h2>

          {currentExercise.id !== "short-answer" ? (
            <p className="assignment-exercise-instruction">{currentExercise.instruction}</p>
          ) : null}

          {currentExercise.id === "judgment" ? (
            <div className="assignment-judgment-options" role="group" aria-label={currentExercise.instruction}>
              <button
                type="button"
                className={judgmentAnswer === "correct" ? "selected" : ""}
                aria-pressed={judgmentAnswer === "correct"}
                onClick={() => setJudgmentAnswer("correct")}
              >
                <span aria-hidden="true"><Check size={25} /></span>
                <strong>正确</strong>
              </button>
              <button
                type="button"
                className={judgmentAnswer === "incorrect" ? "selected" : ""}
                aria-pressed={judgmentAnswer === "incorrect"}
                onClick={() => setJudgmentAnswer("incorrect")}
              >
                <span aria-hidden="true"><X size={25} /></span>
                <strong>错误</strong>
              </button>
            </div>
          ) : null}

          {currentExercise.id === "choice" ? (
            <div className="assignment-choice-options" role="group" aria-label={currentExercise.instruction}>
              {currentExercise.options?.map((option) => {
                const selected = choiceAnswer === option.key;
                return (
                  <button
                    key={option.key}
                    type="button"
                    className={selected ? "selected" : ""}
                    aria-pressed={selected}
                    onClick={() => setChoiceAnswer(option.key)}
                  >
                    <span className="assignment-option-marker" aria-hidden="true">{option.key}</span>
                    <span>{option.text}</span>
                    {selected ? <Check className="assignment-option-check" size={21} aria-hidden="true" /> : null}
                  </button>
                );
              })}
            </div>
          ) : null}

          {currentExercise.id === "short-answer" ? (
            <div className="assignment-short-answer">
              <p className="assignment-short-hint">
                <Lightbulb size={19} aria-hidden="true" />
                <span>{currentExercise.instruction}</span>
              </p>
              <label className="answer-field">
                <span className="assignment-answer-label">
                  <strong>你的答案</strong>
                  <span className="assignment-character-count" aria-live="polite">{answer.length} / 300</span>
                  {hasAnswer ? (
                    <CheckCircle2
                      className="assignment-answer-check"
                      data-motion-assignment-answer-state={answerCheckState}
                      size={16}
                      strokeWidth={2.5}
                      aria-hidden="true"
                      onAnimationEnd={(event) => {
                        if (event.target === event.currentTarget && event.animationName === "motion-stage-check-in") setAnswerCheckState("idle");
                      }}
                    />
                  ) : null}
                </span>
                <MotionErrorShake
                  state={answerFeedback.state}
                  sequence={answerFeedback.sequence}
                  onSettle={answerFeedback.settle}
                  className="assignment-answer-input-shake"
                >
                  <textarea
                    ref={answerRef}
                    value={answer}
                    maxLength={300}
                    aria-invalid={answerError ? true : undefined}
                    aria-describedby={answerError ? "assignment-answer-error" : undefined}
                    onChange={(event) => {
                      const nextAnswer = event.target.value;
                      setAnswer(nextAnswer);
                      if (answerError && nextAnswer.trim()) {
                        setAnswerError(null);
                        answerFeedback.settle();
                      }
                    }}
                    onFocus={(event) => {
                      setAnswerFieldActive(true);
                      event.currentTarget.scrollIntoView({ block: "nearest", inline: "nearest" });
                    }}
                    onBlur={() => setAnswerFieldActive(false)}
                    placeholder="在这里写下你的思路……"
                    rows={7}
                  />
                </MotionErrorShake>
                {answerError ? <p id="assignment-answer-error" className="field-error" role="alert">{answerError}</p> : null}
              </label>
            </div>
          ) : null}

          <div className="assignment-exercise-footer">
            {canOpenSource ? (
              <button
                className="assignment-source-button"
                type="button"
                aria-label={`${sourcePageLabel}，查看原文`}
                title={sourcePageLabel}
                onClick={openAssignmentSource}
              >
                <span>查看原文</span>
              </button>
            ) : <span className="assignment-source-button" title={sourcePageLabel}>{sourcePageLabel}</span>}
          </div>

          <div className="assignment-primary-action">
            <Button
              aria-label={currentExercise.id === "short-answer" ? "提交作业" : `提交${currentExercise.label}答案并进入下一题`}
              data-motion-assignment-submit={loading ? "submitting" : "idle"}
              disabled={loading || cardSwitchState !== "idle" || (currentExercise.id !== "short-answer" && !canContinue)}
              loading={loading}
              onClick={currentExercise.id === "short-answer" ? submit : continueToNextExercise}
            >
              {currentExercise.id === "short-answer" ? "提交并查看诊断" : "提交答案"}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
