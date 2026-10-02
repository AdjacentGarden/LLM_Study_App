import { ChevronRight } from "lucide-react";
import { StickerIcon, type StickerIconName } from "../icons/StickerIcon";
import { useHorizontalDragScroll } from "./useHorizontalDragScroll";
import {
  type ChapterToolPreviewContent,
  studyToolDefinitions,
  type StudyToolDefinition,
  type StudyToolId
} from "../../screens/studyTools";

export type ChapterToolId = Exclude<StudyToolId, "source">;

export type ChapterToolCardsProps = Readonly<{
  chapterTitle: string;
  onSelectTool: (toolId: ChapterToolId) => void;
  ariaLabel?: string;
  previewContent?: ChapterToolPreviewContent;
  orderedToolIds?: readonly ChapterToolId[];
  layout?: "grid" | "rail";
}>;

const toolIcons: Record<ChapterToolId, StickerIconName> = {
  assignment: "ClipboardCheck",
  flashcards: "Layers3",
  mistakes: "BookX",
  notes: "NotebookPen"
};

function isChapterTool(
  tool: StudyToolDefinition
): tool is StudyToolDefinition & { readonly id: ChapterToolId } {
  return tool.id !== "source";
}

export function ChapterToolCards({
  chapterTitle,
  onSelectTool,
  ariaLabel = "本节辅助工具",
  previewContent,
  orderedToolIds,
  layout = "grid"
}: ChapterToolCardsProps) {
  const scrollerRef = useHorizontalDragScroll(layout === "rail");
  const cardTools = studyToolDefinitions.filter(isChapterTool).slice().sort((a, b) => {
    if (!orderedToolIds) return 0;
    return orderedToolIds.indexOf(a.id) - orderedToolIds.indexOf(b.id);
  });
  const previewTitle = chapterTitle.replace(/^第\s*\d+\s*[章节]\s*/, "");
  const assignmentKicker = previewContent?.assignmentKicker ?? "知识检测";
  const assignmentPrompt = previewContent?.assignmentPrompt ?? "这一节的核心概念是？";
  const assignmentOptionLabel = previewContent?.assignmentOptionLabel ?? "选择你的答案";
  const flashcardTitle = previewContent?.flashcardTitle ?? previewTitle;
  const mistakeKicker = previewContent?.mistakeKicker ?? "今日待复习";
  const mistakeCount = previewContent?.mistakeCount ?? "3 道";
  const mistakeItems = previewContent?.mistakeItems ?? [
    { label: "减数分裂", status: "错 2 次" },
    { label: "同源染色体", status: "待复习" }
  ];

  return (
    <div className="study-tool-grid" ref={scrollerRef} role="group" aria-label={ariaLabel} data-mouse-drag-scroll={layout === "rail" ? "self" : undefined}>
      {cardTools.map((tool) => {
        return (
          <button
            aria-label={`${tool.title} ${tool.description}`}
            className="study-tool-card"
            data-tool={tool.id}
            type="button"
            key={tool.id}
            onClick={() => onSelectTool(tool.id)}
          >
            <span className="study-tool-cover" aria-hidden="true">
              {tool.id === "assignment" ? (
                <span className="study-assignment-preview">
                  <small>{assignmentKicker}</small>
                  <strong>{assignmentPrompt}</strong>
                  <span><b>A</b>{assignmentOptionLabel}</span>
                </span>
              ) : tool.id === "mistakes" ? (
                <span className="study-mistake-preview">
                  <span className="study-mistake-preview-head">
                    <small>{mistakeKicker}</small>
                    <strong>{mistakeCount}</strong>
                  </span>
                  {mistakeItems.slice(0, 2).map((item) => (
                    <span className="study-mistake-preview-row" key={`${item.label}:${item.status}`}>
                      <i />{item.label} <b>{item.status}</b>
                    </span>
                  ))}
                </span>
              ) : tool.id === "notes" ? (
                <span className="study-note-preview">
                  <i />
                  <i />
                  <i />
                  <small>原始记录 · 整理版</small>
                </span>
              ) : (
                <span className="study-flashcard-preview">
                  <span>{flashcardTitle}</span>
                </span>
              )}
            </span>
            <span className="study-tool-card-footer">
              <span className="study-tool-card-icon" aria-hidden="true"><StickerIcon name={toolIcons[tool.id]} size={17} /></span>
              <span className="study-tool-copy">
                <strong>{tool.title}</strong>
                <small>{tool.description}</small>
              </span>
              <ChevronRight size={17} aria-hidden="true" />
            </span>
          </button>
        );
      })}
    </div>
  );
}
