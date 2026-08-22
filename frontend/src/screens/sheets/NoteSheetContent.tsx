import { useEffect, useState } from "react";
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
import { saveStudyNote } from "../savedStudyNotes";

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
  const [imageFailed, setImageFailed] = useState(false);
  const conceptExplanation = explanation ?? `围绕“${concept}”梳理本节教材中的定义、过程和关键作用。`;
  const isSelectionNote = kind === "selection" && Boolean(quote);
  const defaultNote = isSelectionNote
    ? `摘录：${quote}\n\n我的理解：`
    : `# ${concept}\n${conceptExplanation}`;
  const [noteText, setNoteText] = useState(defaultNote);

  useEffect(() => {
    setImageFailed(false);
    setNoteText(defaultNote);
  }, [defaultNote, image]);

  return (
    <div className="sheet-body concept-detail-sheet">
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
        onChange={(event) => setNoteText(event.target.value)}
      />
      <Button
        variant="secondary"
        icon={<Save size={18} aria-hidden="true" />}
        disabled={!noteText.trim()}
        onClick={() => {
          saveStudyNote({
            title: isSelectionNote ? `教材摘录：${concept}` : concept,
            body: noteText.trim(),
            quote: isSelectionNote ? quote : undefined,
            sourceLabel
          });
          setSavedNoteCount((count) => count + 1);
          closeSheet();
          showToast(isSelectionNote ? "摘录已保存到导学笔记" : "已保存到导学笔记");
        }}
      >
        {isSelectionNote ? "保存摘录笔记" : "保存到笔记"}
      </Button>
    </div>
  );
}
