import { useState } from "react";
import { StickerIcon, type StickerIconName } from "../../components/icons/StickerIcon";
import type { NoteKind } from "../../features/studyNotes/types";

const choices: readonly {
  kind: NoteKind;
  title: string;
  description: string;
  icon: StickerIconName;
}[] = [
  {
    kind: "text",
    title: "文字笔记",
    description: "点击原文任意位置，输入文字批注",
    icon: "NotebookPen"
  },
  {
    kind: "ink",
    title: "手写批注",
    description: "打开教材原页，用触控笔圈画",
    icon: "PenLine"
  },
  {
    kind: "voice",
    title: "语音笔记",
    description: "录下想法，确认逐字稿后自动整理",
    icon: "Mic2"
  }
];

export function NoteTypeSheetContent({
  contextLabel,
  inkAvailable = true,
  initialPage,
  pageOptions,
  onChoose
}: {
  contextLabel?: string;
  inkAvailable?: boolean;
  initialPage?: number;
  pageOptions?: Array<{ page: number; label: string }>;
  onChoose: (kind: NoteKind, page?: number) => void;
}) {
  const [selectedPage, setSelectedPage] = useState(initialPage ?? pageOptions?.[0]?.page);
  const hasInkPage = inkAvailable || Boolean(pageOptions?.length);
  return (
    <div className="sheet-body note-type-sheet">
      {contextLabel ? <p className="note-type-context">将关联到：{contextLabel}</p> : null}
      {pageOptions?.length ? (
        <label className="note-page-picker">
          <span>教材批注页</span>
          <select value={selectedPage} onChange={(event) => setSelectedPage(Number(event.target.value))}>
            {pageOptions.map((option) => <option key={option.page} value={option.page}>{option.label}</option>)}
          </select>
          <small>选择后会在对应教材原页上直接批注</small>
        </label>
      ) : null}
      <div className="note-type-options" role="group" aria-label="选择笔记类型">
        {choices.map((choice) => {
          const disabled = choice.kind === "ink" && !hasInkPage;
          return (
            <button
              className={`note-type-option is-${choice.kind}`}
              disabled={disabled}
              key={choice.kind}
              type="button"
              onClick={() => onChoose(choice.kind, choice.kind !== "voice" ? selectedPage : undefined)}
            >
              <span className="note-type-icon" aria-hidden="true"><StickerIcon name={choice.icon} size={23} /></span>
              <span>
                <strong>{choice.title}</strong>
                <small>{disabled ? "当前页暂无可批注原图" : choice.description}</small>
              </span>
            </button>
          );
        })}
      </div>
      <p className="helper-text">原始内容会一直保留，整理版不会覆盖你的记录。</p>
    </div>
  );
}
