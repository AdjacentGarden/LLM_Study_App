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
  image: string;
  text?: string;
  onCreateNote: (quote: string) => void;
  onOpenFullSource?: () => void;
}) {
  const [selectedText, setSelectedText] = useState("");
  const selectableTextRef = useRef<HTMLDivElement | null>(null);
  const hasSelectableText = Boolean(text?.trim());
  const paragraphs = text?.split(/\n{2,}/).filter(Boolean) ?? [];

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

  return (
    <div className="sheet-body source-reference-sheet">
      <Pill tone="purple">{page}</Pill>

      <div className="source-unified-reader">
        <div className="source-page-panel">
          <figure className="textbook-preview source-page-preview">
            <div className="source-page-selection-surface">
              <img src={image} alt={`${title} ${page}`} />
              {hasSelectableText ? (
                <div
                  ref={selectableTextRef}
                  className="source-page-text-layer"
                  tabIndex={0}
                  aria-label={`${page}教材原页可选择文字`}
                  onPointerUp={captureSelection}
                  onKeyUp={captureSelection}
                >
                  {paragraphs.map((paragraph, index) => (
                    <p key={`${index}:${paragraph.slice(0, 24)}`}>{paragraph}</p>
                  ))}
                </div>
              ) : null}
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
