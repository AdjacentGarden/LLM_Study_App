import { useEffect, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent
} from "react";
import {
  Check,
  RotateCcw
} from "lucide-react";
import {
  Button,
  Card,
  Pill
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { localMotionFallbackMs, useReducedMotion } from "../motion";
import {
  clampFlashcardDrag,
  isHorizontalFlashcardGesture,
  isLastFlashcard,
  nextFlashcardIndex,
  shouldAdvanceFlashcardSwipe
} from "./flashcardGestures";

type FlashcardSwitchState = "idle" | "exiting" | "entering";

const flashcardExitAnimationName = "motion-flashcard-postcard-out";
const flashcardEnterAnimationName = "motion-flashcard-postcard-in";

const flashcardThemes = [
  {
    surface: "#ece8ff",
    answer: "#e3deff",
    footer: "#ffffff",
    ink: "#26233c",
    muted: "#5b5674",
    accent: "#5b4bd6",
    pill: "#d9d2ff"
  },
  {
    surface: "#dff1ff",
    answer: "#d3ebfc",
    footer: "#ffffff",
    ink: "#193047",
    muted: "#4d6479",
    accent: "#2477b6",
    pill: "#c7e6fa"
  },
  {
    surface: "#ddf5ea",
    answer: "#d2efdf",
    footer: "#ffffff",
    ink: "#18382e",
    muted: "#4b6a60",
    accent: "#22795f",
    pill: "#c5e9d8"
  },
  {
    surface: "#ffe8d8",
    answer: "#ffdfca",
    footer: "#ffffff",
    ink: "#472a1f",
    muted: "#765749",
    accent: "#a94f2d",
    pill: "#ffd4bc"
  },
  {
    surface: "#fbe4ec",
    answer: "#f5d9e4",
    footer: "#ffffff",
    ink: "#432532",
    muted: "#765461",
    accent: "#a9436c",
    pill: "#f0ccd9"
  },
  {
    surface: "#fff2c9",
    answer: "#fbe9b3",
    footer: "#ffffff",
    ink: "#42351d",
    muted: "#736344",
    accent: "#7e5f17",
    pill: "#f5dfa0"
  }
] as const;

function getFlashcardThemeStyle(theme: (typeof flashcardThemes)[number]) {
  return {
    "--flashcard-card-surface": theme.surface,
    "--flashcard-card-answer": theme.answer,
    "--flashcard-card-footer": theme.footer,
    "--flashcard-card-ink": theme.ink,
    "--flashcard-card-muted": theme.muted,
    "--flashcard-card-accent": theme.accent,
    "--flashcard-card-pill": theme.pill
  } as CSSProperties;
}

export function FlashcardScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const { activeChapterId, back, generatedFlashcards, generatedLessons, go, openSourcePage, setGeneratedFlashcards, showToast, uploadedFile } = useAppContext();
  const reducedMotion = useReducedMotion();
  const [index, setIndex] = useState(0);
  const [showAnswer, setShowAnswer] = useState(false);
  const [buildingCards, setBuildingCards] = useState(false);
  const [flipState, setFlipState] = useState<"flipping" | "idle">("idle");
  const [cardSwitchState, setCardSwitchState] = useState<FlashcardSwitchState>("idle");
  const [pendingCardIndex, setPendingCardIndex] = useState<number | null>(null);
  const [cardExitOffset, setCardExitOffset] = useState(0);
  const [dragOffset, setDragOffset] = useState(0);
  const [dragging, setDragging] = useState(false);
  const swipeStartRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const suppressCardClickRef = useRef(false);
  const liveCards = uploadedFile
    ? (generatedFlashcards ?? [])
        .filter((card) => !activeChapterId || card.chapter_id === activeChapterId)
        .map((card) => ({
          id: card.card_id,
          front: card.front,
          back: card.back,
          concept: card.concept,
          due: card.due === "today" ? "今天复习" : card.due,
          mastery: card.mastery,
          source: card.source_kind === "ai_supplement" || card.page_start <= 0
            ? "AI 补充 · 待原文核验"
            : (() => {
                const start = card.printed_page_start ?? card.page_start;
                const end = card.printed_page_end ?? card.page_end;
                return start === end ? `教材第 ${start} 页` : `教材第 ${start}-${end} 页`;
              })(),
          pageStart: card.page_start,
          pageEnd: card.page_end,
          canOpenSource: card.source_kind !== "ai_supplement" && card.page_start > 0,
          reason: card.reason || "基于本节核心概念生成"
        }))
    : [];
  const cards = liveCards;
  const current = cards.length > 0 ? cards[index] ?? cards[cards.length - 1] : null;
  const lastCard = isLastFlashcard(index, cards.length);
  const currentTheme = flashcardThemes[index % flashcardThemes.length];
  const nextTheme = flashcardThemes[(index + 1) % flashcardThemes.length];
  const afterNextTheme = flashcardThemes[(index + 2) % flashcardThemes.length];
  const stackedCards = [
    ...(cards[index + 2]
      ? [{ card: cards[index + 2], depth: "back" as const, offset: 2, theme: afterNextTheme }]
      : []),
    ...(cards[index + 1]
      ? [{ card: cards[index + 1], depth: "middle" as const, offset: 1, theme: nextTheme }]
      : [])
  ];
  const dueCount = cards.filter((card) => card.due === "今天复习" || card.due === "today").length;
  const hasLesson = Boolean(uploadedFile && (generatedLessons ?? []).some((lesson) => !activeChapterId || lesson.chapter_id === activeChapterId));

  useEffect(() => {
    if (!reducedMotion) return;
    setFlipState("idle");
    if (pendingCardIndex !== null) setIndex(pendingCardIndex);
    setShowAnswer(false);
    setCardSwitchState("idle");
    setPendingCardIndex(null);
    setCardExitOffset(0);
  }, [pendingCardIndex, reducedMotion]);

  useEffect(() => {
    if (cardSwitchState === "idle") return;

    const fallback = window.setTimeout(() => {
      if (cardSwitchState === "exiting") {
        if (pendingCardIndex !== null) setIndex(pendingCardIndex);
        setShowAnswer(false);
        setCardSwitchState(pendingCardIndex === null ? "idle" : "entering");
        return;
      }
      setCardSwitchState("idle");
      setPendingCardIndex(null);
      setCardExitOffset(0);
    }, localMotionFallbackMs);

    return () => window.clearTimeout(fallback);
  }, [cardSwitchState, pendingCardIndex]);

  useEffect(() => {
    const settleInterruptedMotion = () => {
      setFlipState("idle");
      if (pendingCardIndex !== null) setIndex(pendingCardIndex);
      setShowAnswer(false);
      setCardSwitchState("idle");
      setPendingCardIndex(null);
      setCardExitOffset(0);
      setDragging(false);
      setDragOffset(0);
    };
    const visualViewport = window.visualViewport;
    window.addEventListener("resize", settleInterruptedMotion);
    window.addEventListener("orientationchange", settleInterruptedMotion);
    visualViewport?.addEventListener("resize", settleInterruptedMotion);
    return () => {
      window.removeEventListener("resize", settleInterruptedMotion);
      window.removeEventListener("orientationchange", settleInterruptedMotion);
      visualViewport?.removeEventListener("resize", settleInterruptedMotion);
    };
  }, [pendingCardIndex]);

  function toggleAnswer() {
    if (!current || flipState === "flipping" || cardSwitchState !== "idle") return;
    setShowAnswer((value) => !value);
    setFlipState(reducedMotion ? "idle" : "flipping");
  }

  function settleAnswerMotion(animationName: string) {
    if (
      animationName === "motion-flashcard-flip-to-back"
      || animationName === "motion-flashcard-flip-to-front"
      || animationName === "motion-flashcard-crossfade-in"
    ) {
      setFlipState("idle");
    }
  }

  function settleCardSwitch(animationName: string) {
    if (cardSwitchState === "exiting" && animationName === flashcardExitAnimationName) {
      if (pendingCardIndex !== null) setIndex(pendingCardIndex);
      setShowAnswer(false);
      setCardSwitchState(pendingCardIndex === null ? "idle" : "entering");
      return;
    }

    if (cardSwitchState === "entering" && animationName === flashcardEnterAnimationName) {
      setCardSwitchState("idle");
      setPendingCardIndex(null);
      setCardExitOffset(0);
    }
  }

  async function buildCards() {
    if (!uploadedFile) return;
    if (!hasLesson) {
      showToast("需要先生成本章全文课程，再生成闪卡", "warning");
      return;
    }
    setBuildingCards(true);
    try {
      const cards = await bookcourseRepository.buildFlashcards(uploadedFile.bookId, {
        chapter_ids: activeChapterId ? [activeChapterId] : undefined
      });
      setGeneratedFlashcards([...(generatedFlashcards ?? []).filter((card) => activeChapterId && card.chapter_id !== activeChapterId), ...cards]);
      setIndex(0);
      setShowAnswer(false);
      setFlipState("idle");
      setCardSwitchState("idle");
      setPendingCardIndex(null);
      setCardExitOffset(0);
      showToast("闪卡已基于结构化课程生成");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "闪卡生成失败", "warning");
    } finally {
      setBuildingCards(false);
    }
  }

  async function advanceCard(feedback?: "again" | "known", exitOffset = 0) {
    if (!current || cardSwitchState !== "idle") return;
    if (feedback && generatedFlashcards && uploadedFile) {
      try {
        const saved=await bookcourseRepository.reviewFlashcard(uploadedFile.bookId,current.id,{rating:feedback==="known"?"good":"again",event_id:crypto.randomUUID()});
        setGeneratedFlashcards(generatedFlashcards.map(card=>card.card_id===current.id?saved:card));
      }catch(error){showToast(error instanceof Error?error.message:"复习记录保存失败", "warning");return;}
    }
    if (feedback) {
      showToast(feedback === "known" ? "已记录为掌握" : "已加入复习队列", feedback === "known" ? "success" : "info");
    }
    setFlipState("idle");
    setDragging(false);
    setDragOffset(0);
    const nextIndex = nextFlashcardIndex(index, cards.length);
    if (nextIndex === index) return;
    if (reducedMotion) {
      setIndex(nextIndex);
      setShowAnswer(false);
      return;
    }
    setPendingCardIndex(nextIndex);
    setCardExitOffset(exitOffset);
    setCardSwitchState("exiting");
  }

  function moveNext(feedback: "again" | "known") {
    advanceCard(feedback);
  }

  async function completeMemory() {
    if (!current || cardSwitchState !== "idle") return;
    if (generatedFlashcards && uploadedFile) {
      try {const saved=await bookcourseRepository.reviewFlashcard(uploadedFile.bookId,current.id,{rating:"good",event_id:crypto.randomUUID()});setGeneratedFlashcards(generatedFlashcards.map(card=>card.card_id===current.id?saved:card));}
      catch(error){showToast(error instanceof Error?error.message:"复习保存失败", "warning");return;}
    }
    showToast("已完成本轮闪卡记忆", "success");
    back();
  }

  function startSwipe(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0 || flipState === "flipping" || cardSwitchState !== "idle") return;
    swipeStartRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY
    };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Synthetic pointer events used by accessibility tooling may not own capture.
    }
  }

  function moveSwipe(event: ReactPointerEvent<HTMLButtonElement>) {
    const start = swipeStartRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - start.x;
    const deltaY = event.clientY - start.y;
    if (!isHorizontalFlashcardGesture(deltaX, deltaY)) return;
    setDragging(true);
    setDragOffset(clampFlashcardDrag(deltaX));
  }

  function finishSwipe(event: ReactPointerEvent<HTMLButtonElement>) {
    const start = swipeStartRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - start.x;
    const deltaY = event.clientY - start.y;
    const movedHorizontally = isHorizontalFlashcardGesture(deltaX, deltaY);
    const shouldAdvance = shouldAdvanceFlashcardSwipe(deltaX, deltaY, cards.length, index);
    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    } catch {
      // The gesture can still settle if capture was released by the browser.
    }
    swipeStartRef.current = null;
    suppressCardClickRef.current = movedHorizontally;
    setDragging(false);
    setDragOffset(0);
    if (shouldAdvance) advanceCard(undefined, clampFlashcardDrag(deltaX));
  }

  function cancelSwipe(event: ReactPointerEvent<HTMLButtonElement>) {
    if (swipeStartRef.current?.pointerId !== event.pointerId) return;
    swipeStartRef.current = null;
    suppressCardClickRef.current = dragging;
    setDragging(false);
    setDragOffset(0);
  }

  function handleCardClick() {
    if (suppressCardClickRef.current) {
      suppressCardClickRef.current = false;
      return;
    }
    toggleAnswer();
  }

  function handleCardKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowLeft" || cards.length <= 1 || cardSwitchState !== "idle") return;
    event.preventDefault();
    advanceCard();
  }

  if (!current) {
    return (
      <div className="screen-stack flashcard-screen">
        <Card className="flashcard-hero">
          <div>
            <Pill tone={hasLesson ? "orange" : "sky"}>{uploadedFile ? hasLesson ? "待生成" : "需要课程" : "需要教材"}</Pill>
            <h2>本章暂无结构化闪卡</h2>
            <p>{uploadedFile ? hasLesson ? "闪卡会根据课程目标、核心概念和原文证据生成。" : "请先回到章节学习页生成本章课程。" : "请先上传并解析教材，再开始本章闪卡复习。"}</p>
          </div>
        </Card>
        <Button loading={buildingCards} disabled={buildingCards || !hasLesson} onClick={buildCards}>生成本章闪卡</Button>
        <Button variant="secondary" onClick={() => go(uploadedFile ? "lesson" : "upload")}>{uploadedFile ? "回到章节" : "上传教材"}</Button>
      </div>
    );
  }

  return (
    <div className="screen-stack flashcard-screen">
      <div
        className="flashcard-workspace"
        style={getFlashcardThemeStyle(currentTheme)}
      >
        <header className="flashcard-hero">
          <div>
            <strong>{dueCount > 0 ? `${dueCount} 张今天复习` : `${cards.length} 张本节闪卡`}</strong>
            <span>{cards.length} 张卡片</span>
          </div>
          <p>{lastCard ? "最后一张 · 完成后结束本轮" : "点击卡片翻面 · 左滑下一张"}</p>
        </header>

        <div className="flashcard-deck-stage">
          {stackedCards.map(({ card, depth, offset, theme }) => (
            <article
              key={`${depth}:${card.id}`}
              className={`flashcard-deck-preview flashcard-deck-preview-${depth}`}
              style={getFlashcardThemeStyle(theme)}
              aria-hidden="true"
            >
              <div className="flashcard-deck-preview-surface">
                <div className="memory-card-head">
                  <span className="memory-card-status">{card.due}</span>
                  <span className="memory-card-mastery">
                    {card.mastery >= 80 ? "较熟悉" : card.mastery >= 50 ? "巩固中" : "待加强"}
                  </span>
                </div>
                <div className="flashcard-deck-preview-content">
                  <span className="memory-card-side-label">问题 · {card.concept}</span>
                  <h2>{card.front}</h2>
                </div>
                <span className="flashcard-deck-preview-hint">下一张</span>
              </div>
              <footer className="flashcard-deck-preview-footer">
                <div className="flashcard-deck-preview-source">
                  <span>{card.source}</span>
                  {card.canOpenSource ? <strong>查看原文</strong> : null}
                </div>
                <div className="flashcard-deck-preview-progress">
                  <div>
                    <span>本轮进度</span>
                    <strong>{index + offset + 1} / {cards.length}</strong>
                  </div>
                  <span className="flashcard-progress-track">
                    <span style={{ width: `${((index + offset + 1) / cards.length) * 100}%` }} />
                  </span>
                </div>
              </footer>
            </article>
          ))}
          <section
            className={`memory-card ${showAnswer ? "revealed" : ""}`}
            aria-label="当前闪卡"
            aria-busy={cardSwitchState !== "idle"}
            data-swipe-state={dragging ? "dragging" : "idle"}
            data-motion-flash-card-state={cardSwitchState}
            style={{
              "--flashcard-drag-x": `${dragOffset}px`,
              "--flashcard-drag-rotation": `${dragOffset / 28}deg`,
              "--flashcard-switch-start-x": `${cardExitOffset}px`,
              "--flashcard-switch-start-rotation": `${cardExitOffset / 28}deg`
            } as CSSProperties}
            onAnimationEnd={(event) => {
              if (event.target === event.currentTarget) settleCardSwitch(event.animationName);
            }}
          >
            <button
              className="memory-card-trigger memory-reveal"
              data-mouse-drag-scroll="self"
              type="button"
              aria-label={`${showAnswer ? "参考答案" : "问题"}：${showAnswer ? current.back : current.front}。${showAnswer ? "点击返回问题" : "点击查看答案"}，${lastCard ? "这是最后一张，完成后点击完成记忆。" : "左滑切换下一张。"}`}
              aria-pressed={showAnswer}
              disabled={flipState === "flipping" || cardSwitchState !== "idle"}
              onClick={handleCardClick}
              onKeyDown={handleCardKeyDown}
              onPointerDown={startSwipe}
              onPointerMove={moveSwipe}
              onPointerUp={finishSwipe}
              onPointerCancel={cancelSwipe}
            >
              <div className="memory-card-head">
                <span className="memory-card-status">{current.due}</span>
                <span className="memory-card-mastery">
                  {current.mastery >= 80 ? "较熟悉" : current.mastery >= 50 ? "巩固中" : "待加强"}
                </span>
              </div>
              <div
                className="memory-card-answer-motion"
                data-motion-flash-card={current.id}
                data-motion-flash-side={showAnswer ? "back" : "front"}
                data-motion-flash-state={flipState}
                onAnimationEnd={(event) => settleAnswerMotion(event.animationName)}
              >
                <div className="memory-card-answer-3d">
                  <div className="memory-card-answer-face memory-card-answer-face-front" aria-hidden={showAnswer} inert={showAnswer}>
                    <span className="memory-card-side-label">问题 · {current.concept}</span>
                    <h2>{current.front}</h2>
                  </div>
                  <div className="memory-card-answer-face memory-card-answer-face-back" aria-hidden={!showAnswer} inert={!showAnswer}>
                    <span className="memory-card-side-label">参考答案</span>
                    <h2>{current.back}</h2>
                    <p>{current.reason}</p>
                  </div>
                </div>
              </div>
              <span className="memory-card-tap-hint">{showAnswer ? "点击返回问题" : "点击查看答案"}</span>
            </button>

            <div className="memory-card-source-row">
              <p className="memory-card-source">{current.source}</p>
              {uploadedFile && current.canOpenSource ? (
                <button
                  className="inline-link"
                  type="button"
                  onClick={() => {
                    const pageStart = current.pageStart;
                    if (!uploadedFile || !pageStart) return;
                    const pageEnd = current.pageEnd ?? pageStart;
                    openSourcePage({
                      bookId: uploadedFile.bookId,
                      title: current.concept,
                      pageStart,
                      pageEnd
                    });
                  }}
                >
                  查看原文
                </button>
              ) : null}
            </div>

            <div
              className="flashcard-progress"
              role="progressbar"
              aria-label={`本轮复习进度 ${index + 1} / ${cards.length}`}
              aria-valuemin={1}
              aria-valuemax={cards.length}
              aria-valuenow={index + 1}
            >
              <div>
                <span>本轮进度</span>
                <strong>{index + 1} / {cards.length}</strong>
              </div>
              <span className="flashcard-progress-track" aria-hidden="true">
                <span style={{ width: `${((index + 1) / cards.length) * 100}%` }} />
              </span>
            </div>
          </section>
        </div>

        <div className={`flashcard-actions ${lastCard ? "is-complete" : ""}`} data-motion-flash-card-state={cardSwitchState} aria-live="polite">
          {lastCard ? (
            <Button
              icon={<Check size={18} aria-hidden="true" />}
              disabled={cardSwitchState !== "idle"}
              onClick={completeMemory}
            >
              <span className="flashcard-rating-copy">
                <strong>完成记忆</strong>
                <small>结束本轮闪卡复习</small>
              </span>
            </Button>
          ) : showAnswer ? (
            <>
              <Button
                variant="secondary"
                icon={<RotateCcw size={18} aria-hidden="true" />}
                disabled={cardSwitchState !== "idle"}
                onClick={() => moveNext("again")}
              >
                <span className="flashcard-rating-copy">
                  <strong>还不熟</strong>
                  <small>稍后再复习</small>
                </span>
              </Button>
              <Button
                icon={<Check size={18} aria-hidden="true" />}
                disabled={cardSwitchState !== "idle"}
                onClick={() => moveNext("known")}
              >
                <span className="flashcard-rating-copy">
                  <strong>记住了</strong>
                  <small>进入下一张</small>
                </span>
              </Button>
            </>
          ) : null}
        </div>

      </div>
    </div>
  );
}
