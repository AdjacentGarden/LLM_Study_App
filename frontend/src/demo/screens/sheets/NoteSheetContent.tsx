import { useAppContext } from "../../context/AppContext";
import {
  useEffect,
  useRef,
  useState,
  type AnimationEvent as ReactAnimationEvent,
  type MouseEvent as ReactMouseEvent
} from "react";
import { createPortal } from "react-dom";
import {
  BookOpenCheck,
  Save
} from "lucide-react";
import type {
  AppActions
} from "../../types/app";
import {
  Button,
  Pill
} from "../../components/ui";
import {
  globalEmphasisMotionDurationMs,
  localMotionMaxMs,
  useReducedMotion
} from "../../motion";
import { saveStudyNote } from "../savedStudyNotes";

const noteSaveCelebrationDurationMs = globalEmphasisMotionDurationMs * 3 + localMotionMaxMs;
const noteSaveSpriteSource = "/assets/brand/success/cloud-mascot-success-strip-v1.png";

type NoteSavePhase = "idle" | "celebrating" | "exiting";

function NoteSaveCelebration({
  phase
}: {
  phase: Exclude<NoteSavePhase, "idle">;
}) {
  return (
    <div
      className="note-save-celebration-layer"
      data-note-save-phase={phase}
      onAnimationEnd={(event: ReactAnimationEvent<HTMLDivElement>) => event.stopPropagation()}
    >
      <div className="note-save-celebration-glow" aria-hidden="true" />
      <div className="note-save-celebration-ring" aria-hidden="true" />
      <div className="note-save-sparks" aria-hidden="true">
        {Array.from({ length: 8 }, (_, index) => <span key={index} />)}
      </div>
      <div className="note-save-mascot-stage" aria-hidden="true">
        <div className="note-save-mascot-viewport">
          <img className="note-save-mascot-strip" src={noteSaveSpriteSource} alt="" />
        </div>
      </div>
      <p className="motion-visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        摘录笔记保存成功
      </p>
    </div>
  );
}

export function NoteSheetContent({
  concept,
  kind = "concept",
  quote,
  explanation,
  sourceLabel,
  image,
  imageCaption,
  onOpenSource,
  setSavedNoteCount,
  closeSheet,
  showToast
}: {
  concept: string;
  kind?: "concept" | "selection";
  quote?: string;
  explanation?: string;
  sourceLabel?: string;
  image?: string;
  imageCaption?: string;
  onOpenSource?: () => void;
  setSavedNoteCount: (fn: (count: number) => number) => void;
  closeSheet: () => void;
  showToast: AppActions["showToast"];
}) {
  const {loadedBookId,activeChapterId,courses,sourcePageTarget} = useAppContext();
  const [imageFailed, setImageFailed] = useState(false);
  const [savePhase, setSavePhase] = useState<NoteSavePhase>("idle");
  const saveSequenceTimerRef = useRef<number | null>(null);
  const reducedMotion = useReducedMotion();
  const conceptExplanation = explanation ?? `围绕“${concept}”梳理本节教材中的定义、过程和关键作用。`;
  const isSelectionNote = kind === "selection" && Boolean(quote);
  const defaultNote = isSelectionNote
    ? `摘录：${quote}\n\n我的理解：`
    : `# ${concept}\n${conceptExplanation}`;
  const [noteText, setNoteText] = useState(defaultNote);
  const saveInProgress = savePhase !== "idle";
  const portalHost = typeof document === "undefined"
    ? null
    : document.querySelector<HTMLElement>(".app-shell");

  useEffect(() => {
    setImageFailed(false);
    setNoteText(defaultNote);
    setSavePhase("idle");
  }, [defaultNote, image]);

  useEffect(() => () => {
    if (saveSequenceTimerRef.current !== null) {
      window.clearTimeout(saveSequenceTimerRef.current);
    }
  }, []);

  const saveNote = async (event: ReactMouseEvent<HTMLButtonElement>) => {
    if (saveInProgress) return;

    const origin=event.currentTarget;
    try {await saveStudyNote({
      title: isSelectionNote ? `教材摘录：${concept}` : concept,
      body: noteText.trim(),
      quote: isSelectionNote ? quote : undefined,
      sourceLabel,
      anchor: loadedBookId ? {bookId:loadedBookId,courseId:courses.state.activeCourseId??undefined,chapterId:activeChapterId??undefined,pageStart:sourcePageTarget?.pageStart,quote} : undefined
    });}catch(error){showToast(error instanceof Error?error.message:"笔记保存失败", "warning");return;}
    setSavedNoteCount((count) => count + 1);

    const successMessage = isSelectionNote ? "摘录已保存到导学笔记" : "已保存到导学笔记";
    if (!isSelectionNote || reducedMotion) {
      closeSheet();
      showToast(successMessage);
      return;
    }

    const sheetElement = origin.closest<HTMLElement>(".sheet");
    const shellElement = origin.closest<HTMLElement>(".app-shell");
    if (sheetElement && shellElement) {
      const sheetBounds = sheetElement.getBoundingClientRect();
      const shellBounds = shellElement.getBoundingClientRect();
      const sheetCenterX = sheetBounds.left + sheetBounds.width / 2;
      const sheetCenterY = sheetBounds.top + sheetBounds.height / 2;
      const shellCenterX = shellBounds.left + shellBounds.width / 2;
      const shellCenterY = shellBounds.top + shellBounds.height / 2;
      sheetElement.style.setProperty("--note-save-collapse-x", `${shellCenterX - sheetCenterX}px`);
      sheetElement.style.setProperty("--note-save-collapse-y", `${shellCenterY - sheetCenterY}px`);
    }

    setSavePhase("celebrating");
    saveSequenceTimerRef.current = window.setTimeout(() => {
      setSavePhase("exiting");
      closeSheet();
    }, noteSaveCelebrationDurationMs);
  };

  return (
    <div
      className="sheet-body concept-detail-sheet"
      data-note-save-state={savePhase}
      aria-busy={saveInProgress ? true : undefined}
    >
      <Pill tone="purple">{isSelectionNote ? sourceLabel ?? "教材摘录" : concept}</Pill>
      <h3>{isSelectionNote ? `摘录自：${concept}` : concept}</h3>
      {isSelectionNote ? (
        <blockquote className="selection-note-quote">{quote}</blockquote>
      ) : (
        <p className="concept-detail-explanation">{conceptExplanation}</p>
      )}
      {image && !imageFailed ? (
        <figure className="concept-detail-figure">
          <img
            src={image}
            alt={imageCaption ? `${concept}：${imageCaption}` : `${concept}教材配图`}
            loading="lazy"
            decoding="async"
            onError={() => setImageFailed(true)}
          />
          {imageCaption ? <figcaption>{imageCaption}</figcaption> : null}
        </figure>
      ) : null}
      {sourceLabel && onOpenSource ? (
        <button className="concept-detail-source" type="button" onClick={onOpenSource}>
          <BookOpenCheck size={17} aria-hidden="true" />
          <span>{sourceLabel}</span>
        </button>
      ) : null}
      <label className="concept-note-label" htmlFor="concept-note-textarea">导学笔记</label>
      <textarea
        id="concept-note-textarea"
        className="note-textarea"
        value={noteText}
        disabled={saveInProgress}
        onChange={(event) => setNoteText(event.target.value)}
      />
      <Button
        variant="secondary"
        icon={<Save size={18} aria-hidden="true" />}
        disabled={!noteText.trim() || saveInProgress}
        onClick={saveNote}
      >
        {isSelectionNote ? "保存摘录笔记" : "保存到笔记"}
      </Button>
      {portalHost && isSelectionNote && savePhase !== "idle"
        ? createPortal(<NoteSaveCelebration phase={savePhase} />, portalHost)
        : null}
    </div>
  );
}
