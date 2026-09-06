import { type ComponentType } from "react";
import { BookX, ChevronRight, ClipboardCheck, Layers3, Plus } from "lucide-react";
import {
  studyToolDefinitions,
  type StudyToolDefinition,
  type StudyToolId
} from "../../screens/studyTools";

export type ChapterToolId = Exclude<StudyToolId, "source">;

export type ChapterToolCardsProps = Readonly<{
  chapterTitle: string;
  onSelectTool: (toolId: ChapterToolId) => void;
  ariaLabel?: string;
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
  ariaLabel = "本节辅助工具"
}: ChapterToolCardsProps) {
  const cardTools = studyToolDefinitions.filter(isChapterTool);
  const previewTitle = chapterTitle.replace(/^第\s*\d+\s*[章节]\s*/, "");

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
                  <small>知识检测</small>
                  <strong>这一节的核心概念是？</strong>
                  <span><b>A</b>选择你的答案</span>
                </span>
              ) : tool.id === "mistakes" ? (
                <span className="study-mistake-preview">
                  <span className="study-mistake-preview-head">
                    <small>今日待复习</small>
                    <strong>3 道</strong>
                  </span>
                  <span className="study-mistake-preview-row"><i />减数分裂 <b>错 2 次</b></span>
                  <span className="study-mistake-preview-row"><i />同源染色体 <b>待复习</b></span>
                </span>
              ) : (
                <span className="study-flashcard-preview">
                  <span>{previewTitle}</span>
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
