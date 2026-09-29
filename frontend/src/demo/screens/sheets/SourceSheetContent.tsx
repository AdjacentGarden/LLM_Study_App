import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, FileText, NotebookPen } from "lucide-react";
import { Button, Pill } from "../../components/ui";

export function SourceSheetContent({
  title,
  page,
  image,
  text,
  onCreateNote,
  onOpenFullSource
}: {
  title: string;
  page: string;
  image?: string;
  text?: string;
  onCreateNote: (quote: string) => void;
  onOpenFullSource?: () => void;
}) {
  const [selectedText, setSelectedText] = useState("");
  const selectableTextRef = useRef<HTMLDivElement | null>(null);
  const hasSelectableText = Boolean(text?.trim());
  const paragraphs = text?.split(/\n{2,}/).map((paragraph) => paragraph.trim()).filter(Boolean) ?? [];

  const captureSelection = useCallback(() => {
    const selection = window.getSelection();
    const selectableText = selectableTextRef.current;
    if (!selection || selection.rangeCount === 0 || !selectableText) {
      setSelectedText("");
      return;
    }
    const range = selection.getRangeAt(0);
    const selectionBelongsToText = selectableText.contains(range.commonAncestorContainer);
    setSelectedText(selectionBelongsToText ? selection.toString().trim().slice(0, 800) : "");
  }, []);

  useEffect(() => {
    document.addEventListener("selectionchange", captureSelection);
    return () => document.removeEventListener("selectionchange", captureSelection);
  }, [captureSelection]);

  const selectableExcerpt = hasSelectableText ? (
    <div
      ref={selectableTextRef}
      className={image ? "source-page-text-layer" : "citation-quote"}
      tabIndex={0}
      role="region"
      aria-label={image ? `${page}教材原页可选择文字` : `${page}教材引用摘录，可选择文字`}
      onPointerUp={captureSelection}
      onKeyUp={captureSelection}
    >
      {paragraphs.map((paragraph, index) => (
        <p key={`${index}:${paragraph.slice(0, 24)}`}>{paragraph}</p>
      ))}
    </div>
  ) : null;

  return (
    <div className="sheet-body source-reference-sheet">
      <Pill tone="purple">{page}</Pill>

      <div className="source-unified-reader">
        <div className="source-page-panel">
          {image ? (
            <>
              <figure className="textbook-preview source-page-preview">
                <div className="source-page-selection-surface">
                  <img src={image} alt={`${title} ${page}`} />
                  {selectableExcerpt}
                </div>
                <figcaption>
                  <FileText size={15} aria-hidden="true" />
                  {title} {page}
                </figcaption>
              </figure>
              {hasSelectableText ? (
                <p className="source-selection-hint">在教材原页上长按或拖动选中文字，即可直接做笔记。</p>
              ) : (
                <p className="helper-text">当前教材页暂无可选择文字，可通过原页核对内容。</p>
              )}
            </>
          ) : (
            <>
              <p className="helper-text">当前引用没有可验证的教材原页图。请按 {page} 在完整教材中查看原文。</p>
              {selectableExcerpt}
              {hasSelectableText ? (
                <p className="source-selection-hint">长按、拖动或使用键盘选择摘录文字，即可直接做笔记。</p>
              ) : null}
            </>
          )}
        </div>
      </div>

      {selectedText ? (
        <div className="source-selection-action" aria-live="polite">
          <div>
            <small>已选择 {selectedText.length} 字</small>
            <p>{selectedText}</p>
          </div>
          <Button
            icon={<NotebookPen size={17} aria-hidden="true" />}
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => onCreateNote(selectedText)}
          >
            做笔记
          </Button>
        </div>
      ) : null}

      {onOpenFullSource ? (
        <Button
          variant="text"
          icon={<BookOpen size={17} aria-hidden="true" />}
          onClick={onOpenFullSource}
        >
          全屏阅读教材
        </Button>
      ) : null}
    </div>
  );
}
