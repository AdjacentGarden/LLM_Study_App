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

export function NoteSheetContent({
  concept,
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

  useEffect(() => {
    setImageFailed(false);
  }, [image]);

  return (
    <div className="sheet-body concept-detail-sheet">
      <Pill tone="purple">{concept}</Pill>
      <h3>{concept}</h3>
      <p className="concept-detail-explanation">{conceptExplanation}</p>
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
        defaultValue={`# ${concept}\n${conceptExplanation}`}
      />
      <Button
        variant="secondary"
        icon={<Save size={18} aria-hidden="true" />}
        onClick={() => {
          setSavedNoteCount((count) => count + 1);
          closeSheet();
          showToast("已保存到导学笔记");
        }}
      >
        保存到笔记
      </Button>
    </div>
  );
}
