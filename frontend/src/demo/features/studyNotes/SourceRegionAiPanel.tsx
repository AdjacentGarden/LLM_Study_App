import { StickerIcon } from "../../components/icons/StickerIcon";
import { courseRagScope } from "../courses/selectors";
import { useAppContext } from "../../context/AppContext";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { BookOpenCheck, X } from "lucide-react";
import { Button } from "../../components/ui";
import { useBookCourseRepository } from "../../context/BookCourseRepositoryContext";
import { globalMotionFallbackMs, useMotionPresence, useReducedMotion, type MotionAnimationEvent } from "../../motion";
import { regionContextLabel, type PageRegion } from "./regionAsk";
import { creditCosts, useCredits } from "../credits/creditStore";
import { getStudyNote, updateTextStudyNote } from "./repository";
import { textNoteQuestion } from "./textAnnotations";
import type { TextNoteMessage, TextStudyNote } from "./types";
import { getCitationSourceText } from "../../screens/sheets/citationSource";

/** Client-pixel rectangle of the dashed selection the dialog grows out of. */
export type RegionOrigin = {
  left: number;
  top: number;
  width: number;
  height: number;
};

export type RegionAiReference = {
  /** Readable label, e.g. "第 16 页 · 左上区域 · 约占页面 30% × 20%". */
  label: string;
  /** Where the selection sits on screen, so the dialog can morph out of it. */
  origin?: RegionOrigin | null;
} & ({
  kind?: "region";
  /** JPEG data URL cropped from the dashed rectangle. */
  dataUrl: string;
  region: PageRegion;
} | {
  kind: "text-note";
  note: TextStudyNote;
});

type RegionAiMorph = {
  x: number;
  y: number;
  scaleX: number;
  scaleY: number;
};

type RegionAiMessage = TextNoteMessage;

const regionAiAnimationNames = [
  "motion-region-ai-in",
  "motion-region-ai-out"
] as const;

const REGION_AI_TITLE_ID = "source-region-ai-title";

function getRegionAiKey(reference: RegionAiReference) {
  return reference.kind === "text-note" ? `text-note-ai:${reference.note.id}` : "source-region-ai";
}

/**
 * The dialog the reader opens when the pen lifts off a circled region.
 *
 * The crop travels with every question as `reference_image`, so the reference
 * is visible here and available to the runtime; the page hint in
 * `context.page_label` keeps a text-only runtime pointing at the same area.
 * Presence follows the shared motion contract: `reference` requests the
 * surface, and the machine keeps it mounted through its exit keyframe.
 */
export function SourceRegionAiPanel({
  bookId,
  bookTitle,
  chapterId,
  chapterTitle,
  onClose,
  onClosed,
  pageLabel,
  reference
}: {
  bookId: string;
  bookTitle: string;
  chapterId: string | null;
  chapterTitle: string;
  onClose: () => void;
  /** Fires once the exit keyframe settled, so the dashed selection can clear. */
  onClosed?: () => void;
  pageLabel: string;
  reference: RegionAiReference | null;
}) {
  const bookcourseRepository = useBookCourseRepository();
  const { courses, openSourcePage } = useAppContext();
  const credits = useCredits();
  const reducedMotion = useReducedMotion();
  const motion = useMotionPresence<RegionAiReference>({
    requested: reference,
    getKey: getRegionAiKey,
    reducedMotion,
    motionNames: regionAiAnimationNames,
    maxMotionMs: globalMotionFallbackMs
  });
  const open = motion.rendered;
  const [messages, setMessages] = useState<RegionAiMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [morph, setMorph] = useState<RegionAiMorph | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const hasRenderedRef = useRef(false);
  const activeReferenceRef = useRef(reference);
  activeReferenceRef.current = reference;
  const chatSaveQueueRef = useRef<Promise<unknown>>(Promise.resolve());

  // The shared-element step of the original AI dialog: the surface starts as the
  // circled rectangle and grows into its final box, so the variables are the
  // translate and scale that put the dialog exactly on the selection. Measured
  // in a layout effect, which commits before the enter keyframe paints.
  useLayoutEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    // The enter keyframe is already transforming this element, so read the
    // layout box with the animation switched off for one synchronous measure.
    const previousAnimation = panel.style.animation;
    panel.style.animation = "none";
    const box = panel.getBoundingClientRect();
    panel.style.animation = previousAnimation;
    if (box.width <= 0 || box.height <= 0) return;
    const origin = open.origin;
    if (origin && origin.width > 0 && origin.height > 0) {
      setMorph({
        x: origin.left - box.left,
        y: origin.top - box.top,
        scaleX: Math.max(.04, origin.width / box.width),
        scaleY: Math.max(.04, origin.height / box.height)
      });
      return;
    }
    // No measurable selection: keep the plain pop, centred on the card itself.
    setMorph({ x: box.width * .03, y: box.height * .03, scaleX: .94, scaleY: .94 });
  }, [open]);

  useEffect(() => {
    if (motion.rendered) {
      hasRenderedRef.current = true;
      return;
    }
    if (!hasRenderedRef.current) return;
    hasRenderedRef.current = false;
    onClosed?.();
  }, [motion.rendered, onClosed]);

  // Same contract as the other dialogs: only the keyframe belonging to the
  // current phase may settle the presence, so a stale cancellation cannot end a
  // newer generation.
  const settleAnimation = (
    event: MotionAnimationEvent,
    settle: (event: MotionAnimationEvent) => void
  ) => {
    const expectedName = motion.state === "entering"
      ? "motion-region-ai-in"
      : motion.state === "closing"
        ? "motion-region-ai-out"
        : null;
    if (!expectedName || event.animationName !== expectedName) return;
    settle(event);
  };

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel || !open) return;
    const handleAnimationCancel = (event: AnimationEvent) => settleAnimation(event, motion.onAnimationCancel);
    panel.addEventListener("animationcancel", handleAnimationCancel);
    return () => panel.removeEventListener("animationcancel", handleAnimationCancel);
  });

  // A new circle starts a new conversation about the new reference.
  useEffect(() => {
    if (!reference) return;
    setMessages(reference.kind === "text-note" ? reference.note.conversation ?? [] : []);
    setLoading(false);
    setQuestion("");
    setError(null);
    inputRef.current?.focus({ preventScroll: true });
    if (reference.kind === "text-note") {
      void getStudyNote(reference.note.id).then((note) => {
        if (activeReferenceRef.current === reference && note?.kind === "text") setMessages(note.conversation ?? []);
      });
    }
  }, [reference]);

  // The bundled corpus is only published for biology, and only the open dialog
  // needs it: warming it then overlaps with reading the reference, while a
  // closed reader never pays for a corpus it is not asking about.
  useEffect(() => {
    if (!open || open.kind === "text-note") return;
    if (bookId !== "book_biology_2") return;
  }, [bookId, open]);

  async function ask(nextQuestion = question) {
    const text = nextQuestion.trim();
    if (!open || loading || !text) return;
    let reservationId: string;
    try {
      reservationId = await credits.reserve("chat");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "积分不足，暂时无法提问。");
      return;
    }
    const history = messages.map((message) => ({
      role: message.role === "assistant" ? "assistant" : "user",
      content: message.text
    }));
    setMessages((items) => [...items, { role: "user", text }]);
    setQuestion("");
    setError(null);
    setLoading(true);
    const nextMessages: RegionAiMessage[] = [...messages, { role: "user", text }];
    const saveConversation = (conversation: RegionAiMessage[]) => {
      if (open.kind !== "text-note") return;
      chatSaveQueueRef.current = chatSaveQueueRef.current.catch(() => undefined).then(async () => {
        await updateTextStudyNote(open.note.id, (current) => current ? { ...current, conversation, updatedAt: Date.now() } : undefined);
      }).catch(() => {
        if (activeReferenceRef.current === open) setError("对话暂时未能保存，当前内容仍可查看。");
      });
    };
    try {
      const result = await bookcourseRepository.queryRag({
        reservation_id: reservationId,
        ...courseRagScope(courses.state, bookId),
        book_id: bookId,
        chapter_id: chapterId,
        history,
        question: open.kind === "text-note" ? textNoteQuestion(open.note, text) : text,
        reference_image: open.kind === "text-note" ? undefined : {
          data_url: open.dataUrl,
          label: open.label,
          page_label: pageLabel,
          region: open.region
        },
        context: {
          book_title: bookTitle,
          chapter_title: chapterTitle,
          section_title: null,
          page_label: open.kind === "text-note" ? open.label : regionContextLabel(open.region, pageLabel),
          key_concepts: []
        }
      });
      if(result.retrieval?.status === "no_match")await credits.refund(reservationId);else await credits.complete(reservationId);
      const conversation: RegionAiMessage[] = [
        ...nextMessages,
        {
          role: "assistant",
          text: result.answer,
          citations: result.citations.slice(0, 3),
          noReliableSource: Boolean(result.retrieval?.attempted) && result.retrieval?.status !== "hit"
        }
      ];
      if (activeReferenceRef.current === open) setMessages(conversation);
      saveConversation(conversation);
    } catch (err) {
      await credits.refund(reservationId);
      if (activeReferenceRef.current === open) setError(err instanceof Error ? err.message : "教材资料暂时不可用");
      saveConversation(nextMessages);
    } finally {
      if (activeReferenceRef.current === open) setLoading(false);
    }
  }

  if (!open) return null;
  const isTextNote = open.kind === "text-note";

  const morphStyle = {
    "--region-morph-x": `${morph?.x ?? 0}px`,
    "--region-morph-y": `${morph?.y ?? 0}px`,
    "--region-morph-scale-x": String(morph?.scaleX ?? .94),
    "--region-morph-scale-y": String(morph?.scaleY ?? .94)
  } as CSSProperties;

  return (
    <aside
      ref={panelRef}
      className="source-region-ai"
      role="dialog"
      aria-labelledby={REGION_AI_TITLE_ID}
      data-region-ai="open"
      data-reference-kind={isTextNote ? "text-note" : "region"}
      data-motion-state={motion.state}
      data-motion-presence={motion.presenceId}
      style={morphStyle}
      onAnimationEnd={(event) => settleAnimation(event, motion.onAnimationEnd)}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        onClose();
      }}
    >
      <header className="source-region-ai-head">
        <span className="source-region-ai-avatar" aria-hidden="true">
          <StickerIcon name="Sparkles" size={16} />
        </span>
        <div>
          <strong id={REGION_AI_TITLE_ID}>{isTextNote ? "批注 AI 对话" : "提问"}</strong>
        </div>
        <button className="icon-button source-region-ai-close" type="button" aria-label={isTextNote ? "关闭批注 AI 对话" : "关闭提问"} onClick={onClose}>
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <p className="source-region-ai-credit" aria-live="polite">剩余 {credits.balance} 积分 · 每次提问消耗 {creditCosts.chat} 积分</p>

      {open.kind === "text-note" ? (
        <div className="source-text-ai-reference">
          <strong>{open.label}</strong>
          <p>{open.note.body}</p>
        </div>
      ) : (
        <figure className="source-region-ai-reference"><img src={open.dataUrl} alt="圈选区域参照图" /></figure>
      )}

      <div className="source-region-ai-transcript" aria-live="polite">
        {messages.length === 0 ? (
          <div className="chat-bubble ai">{isTextNote ? "我会结合你的批注和所在教材页一起讨论。可以检查你的理解，也可以继续追问。" : "已收到你圈出的这一块原文。想问什么？例如这一处讲了什么、图里的箭头代表什么。"}</div>
        ) : null}
        {messages.map((message, index) => (
          <div className="source-region-ai-turn" key={`${message.role}-${index}`}>
            <div className={`chat-bubble ${message.role === "assistant" ? "ai" : "user"}`}>{message.text}</div>
            {message.citations?.length ? (
              <div className="source-region-ai-citations" aria-label="教材原文依据">
                <div className="source-region-ai-citations-head">
                  <BookOpenCheck size={13} aria-hidden="true" />
                  <strong>教材原文依据</strong>
                </div>
                {message.citations.map((citation) => (
                  <article key={citation.chunk_id || `${citation.page}:${citation.quote.slice(0, 16)}`}>
                    <span>{typeof citation.source_metadata.filename === "string" ? `${citation.source_metadata.filename} · ` : ""}{citation.chapter_title || "教材原文"} · {citation.location_label || `第 ${citation.page} 页`}</span>
                    {citation.quote ? <blockquote>{citation.quote}</blockquote> : null}
                    <button type="button" onClick={() => {
                      onClose();
                      openSourcePage({ courseId: courses.state.activeCourseId ?? undefined, bookId: citation.book_id ?? String(citation.source_metadata.book_id ?? bookId), title: citation.chapter_title, pageStart: citation.page, pageEnd: citation.page, sourceText: getCitationSourceText(citation) });
                    }}>查看原文</button>
                  </article>
                ))}
              </div>
            ) : message.noReliableSource ? (
              <p className="source-region-ai-status" role="status">当前教材未检索到可靠原文</p>
            ) : null}
          </div>
        ))}
        {loading ? <div className="chat-bubble ai" aria-busy="true" role="status">{isTextNote ? "正在结合批注和教材回答…" : "正在结合圈选内容回答…"}</div> : null}
        {error ? <p className="helper-text" role="alert">{error}</p> : null}
      </div>

      <div className="source-region-ai-followups">
        <button type="button" disabled={loading} onClick={() => void ask(isTextNote ? "我的这条批注理解正确吗？" : "这一处原文在讲什么？")}>{isTextNote ? "检查我的理解" : "这一处讲了什么？"}</button>
        <button type="button" disabled={loading} onClick={() => void ask(isTextNote ? "用一个例子解释我批注中的内容" : "用一个例子解释圈选的内容")}>举个例子</button>
      </div>

      <form
        className="source-region-ai-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void ask();
        }}
      >
        <label className="chat-input">
          <span>{isTextNote ? "针对这条批注提问" : "针对圈选区域提问"}</span>
          <input
            ref={inputRef}
            value={question}
            autoComplete="off"
            enterKeyHint="send"
            placeholder={isTextNote ? "例如：我的理解正确吗？" : "例如：这一处的箭头表示什么？"}
            onChange={(event) => setQuestion(event.target.value)}
          />
        </label>
        <Button type="submit" loading={loading} disabled={loading}>提问</Button>
      </form>
    </aside>
  );
}