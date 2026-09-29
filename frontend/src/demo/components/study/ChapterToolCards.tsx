import { type ComponentType } from "react";
import { BookX, ChevronRight, ClipboardCheck, Layers3, Plus } from "lucide-react";
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
}>;

const toolIcons: Record<
  ChapterToolId,
  ComponentType<{ size?: number; "aria-hidden"?: boolean }>
> = {
  assignment: ClipboardCheck,
  flashcards: Layers3,
  mistakes: BookX
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
  previewContent
}: ChapterToolCardsProps) {
  const cardTools = studyToolDefinitions.filter(isChapterTool);
  const previewTitle = chapterTitle.replace(/^第\s*\d+\s*[章节]\s*/, "");
  const assignmentKicker = previewContent?.assignmentKicker ?? "知识检测";
  const assignmentPrompt = previewContent?.assignmentPrompt ?? "进入后查看本节真实练习";
  const assignmentOptionLabel = previewContent?.assignmentOptionLabel ?? "题目由课程提供";
  const flashcardTitle = previewContent?.flashcardTitle ?? previewTitle;
  const mistakeKicker = previewContent?.mistakeKicker ?? "今日待复习";
  const mistakeCount = previewContent?.mistakeCount ?? "诊断后查看";
  const mistakeItems = previewContent?.mistakeItems ?? [];

  return (
    <div className="study-tool-grid" aria-label={ariaLabel}>
      {cardTools.map((tool) => {
        const Icon = toolIcons[tool.id];
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
              ) : (
                <span className="study-flashcard-preview">
                  <span>{flashcardTitle}</span>
                </span>
              )}
            </span>
            <span className="study-tool-card-footer">
              <span className="study-tool-card-icon" aria-hidden="true"><Icon size={17} /></span>
              <span className="study-tool-copy">
                <strong>{tool.title}</strong>
                <small>{tool.description}</small>
              </span>
              <ChevronRight size={17} aria-hidden="true" />
            </span>
          </button>
        );
      })}
      <button
        aria-label="更多功能 预留新学习工具"
        className="study-tool-card study-tool-card-future"
        data-tool="future"
        type="button"
        disabled
      >
        <span className="study-tool-cover study-future-preview" aria-hidden="true">
          <span><Plus size={25} /></span>
          <small>新工具</small>
        </span>
        <span className="study-tool-card-footer">
          <span className="study-tool-card-icon" aria-hidden="true"><Plus size={17} /></span>
          <span className="study-tool-copy">
            <strong>更多功能</strong>
            <small>预留新学习工具</small>
          </span>
        </span>
      </button>
    </div>
  );
}
