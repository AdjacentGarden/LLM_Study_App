import { useMemo, useRef, useState, type FormEvent } from "react";
import { AlertCircle, Save, Trash2 } from "lucide-react";
import { Button, Pill } from "../../components/ui";
import type { ApiChapter, ChapterEvidence } from "../../types/api";
import {
  getChapterDescendantIds,
  validateChapterDraft,
  type ChapterValidationField,
  type ChapterValidationIssue
} from "../../utils/chapterStructure";
import { MotionErrorShake, useOneShotFeedback } from "../../motion";
import { ChapterEvidenceReasons, ChapterEvidenceSummary } from "../shared";

export function EditChapterSheetContent({
  chapter,
  chapters,
  evidence,
  pageCount,
  onSave,
  onDelete,
  closeSheet
}: {
  chapter: ApiChapter;
  chapters: ApiChapter[];
  evidence?: ChapterEvidence;
  pageCount?: number;
  onSave: (chapter: ApiChapter) => void;
  onDelete: (chapterIds: string[]) => void;
  closeSheet: () => void;
}) {
  const [sourceTitle, setSourceTitle] = useState(chapter.source_title);
  const [aiTitle, setAiTitle] = useState(chapter.ai_title);
  const [pageStart, setPageStart] = useState(String(chapter.page_start));
  const [pageEnd, setPageEnd] = useState(String(chapter.page_end));
  const [parentId, setParentId] = useState(chapter.parent_id ?? "");
  const [error, setError] = useState<ChapterValidationIssue | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const {
    state: errorMotionState,
    sequence: errorMotionSequence,
    trigger: triggerErrorShake,
    settle: settleErrorShake
  } = useOneShotFeedback();
  const sourceTitleRef = useRef<HTMLInputElement | null>(null);
  const aiTitleRef = useRef<HTMLInputElement | null>(null);
  const pageStartRef = useRef<HTMLInputElement | null>(null);
  const pageEndRef = useRef<HTMLInputElement | null>(null);
  const parentRef = useRef<HTMLSelectElement | null>(null);
  const errorId = `edit-chapter-validation-error-${chapter.chapter_id}`;

  const descendantIds = useMemo(
    () => getChapterDescendantIds(chapters, chapter.chapter_id),
    [chapter.chapter_id, chapters]
  );
  const excludedParentIds = useMemo(
    () => new Set([chapter.chapter_id, ...descendantIds]),
    [chapter.chapter_id, descendantIds]
  );
  const parentCandidates = chapters.filter(
    (candidate) => !excludedParentIds.has(candidate.chapter_id) && candidate.level < chapter.level
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextParent = parentId
      ? chapters.find((candidate) => candidate.chapter_id === parentId) ?? null
      : null;
    const parentChanged = parentId !== (chapter.parent_id ?? "");
    const nextChapter: ApiChapter = {
      ...chapter,
      source_title: sourceTitle.trim(),
      ai_title: aiTitle.trim(),
      page_start: Number(pageStart),
      page_end: Number(pageEnd),
      parent_id: parentId || null,
      level: parentChanged ? (nextParent ? nextParent.level + 1 : 1) : chapter.level,
      status: "已人工校对"
    };
    const validationIssue = validateChapterDraft(chapters, nextChapter, pageCount);
    if (validationIssue) {
      setError(validationIssue);
      triggerErrorShake();
      focusValidationField(validationIssue.field);
      return;
    }
    setError(null);
    settleErrorShake();
    onSave(nextChapter);
  }

  function clearErrorForField(field: ChapterValidationField) {
    if (error?.field !== field && error?.field !== "form") return;
    setError(null);
    settleErrorShake();
  }

  function focusValidationField(field: ChapterValidationField) {
    const target = field === "source_title"
      ? sourceTitleRef.current
      : field === "ai_title"
        ? aiTitleRef.current
        : field === "page_start"
          ? pageStartRef.current
          : field === "page_end"
            ? pageEndRef.current
            : field === "parent_id"
              ? parentRef.current
              : sourceTitleRef.current;
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: "center", inline: "nearest" });
  }

  return (
    <form className="sheet-body chapter-edit-form" onSubmit={submit}>
      <div className="chapter-edit-summary">
        <Pill tone={chapter.status === "匹配良好" ? "mint" : "orange"}>{chapter.status}</Pill>
        <strong>{chapter.confidence}% 综合置信度</strong>
      </div>

      <section className="chapter-inspection-details" aria-label="识别详情">
        <div className="chapter-inspection-heading">
          <strong>识别详情</strong>
          <span>层级 {chapter.level} · PDF {chapter.page_start}–{chapter.page_end} 页</span>
        </div>
        {evidence ? <ChapterEvidenceSummary evidence={evidence} /> : (
          <p className="helper-text">暂无单项识别证据，可继续人工调整名称、页码与所属上级。</p>
        )}
        <p className="chapter-source-detail">识别来源：{chapter.source || "未知"}</p>
        {evidence ? (
          <ChapterEvidenceReasons
            evidenceId={`chapter-edit-sheet-${chapter.chapter_id}`}
            reasons={evidence.reasons}
          />
        ) : null}
      </section>

      <label className="answer-field">
        <span>原书目录名称</span>
        <input
          ref={sourceTitleRef}
          value={sourceTitle}
          aria-invalid={error?.field === "source_title" ? true : undefined}
          aria-describedby={error?.field === "source_title" ? errorId : undefined}
          onChange={(event) => {
            clearErrorForField("source_title");
            setSourceTitle(event.target.value);
          }}
        />
      </label>
      <label className="answer-field">
        <span>课程章节名称</span>
        <input
          ref={aiTitleRef}
          value={aiTitle}
          aria-invalid={error?.field === "ai_title" ? true : undefined}
          aria-describedby={error?.field === "ai_title" ? errorId : undefined}
          onChange={(event) => {
            clearErrorForField("ai_title");
            setAiTitle(event.target.value);
          }}
        />
      </label>
      <div className="chapter-page-fields">
        <label className="answer-field">
          <span>PDF 起始页</span>
          <input
            inputMode="numeric"
            min={1}
            max={pageCount}
            type="number"
            ref={pageStartRef}
            value={pageStart}
            aria-invalid={error?.field === "page_start" ? true : undefined}
            aria-describedby={error?.field === "page_start" ? errorId : undefined}
            onChange={(event) => {
              clearErrorForField("page_start");
              setPageStart(event.target.value);
            }}
          />
        </label>
        <label className="answer-field">
          <span>PDF 结束页</span>
          <input
            inputMode="numeric"
            min={1}
            max={pageCount}
            type="number"
            ref={pageEndRef}
            value={pageEnd}
            aria-invalid={error?.field === "page_end" ? true : undefined}
            aria-describedby={error?.field === "page_end" ? errorId : undefined}
            onChange={(event) => {
              clearErrorForField("page_end");
              setPageEnd(event.target.value);
            }}
          />
        </label>
      </div>
      <label className="answer-field">
        <span>所属上级章节</span>
        <select
          ref={parentRef}
          value={parentId}
          aria-invalid={error?.field === "parent_id" ? true : undefined}
          aria-describedby={error?.field === "parent_id" ? errorId : undefined}
          onChange={(event) => {
            clearErrorForField("parent_id");
            setParentId(event.target.value);
          }}
        >
          <option value="">无（作为一级章节）</option>
          {parentCandidates.map((candidate) => (
            <option key={candidate.chapter_id} value={candidate.chapter_id}>
              {candidate.source_title}（{candidate.page_start}–{candidate.page_end} 页）
            </option>
          ))}
        </select>
      </label>

      {error ? (
        <MotionErrorShake
          state={errorMotionState}
          sequence={errorMotionSequence}
          onSettle={settleErrorShake}
        >
          <span id={errorId} className="chapter-edit-error" role="alert">
            <AlertCircle size={18} aria-hidden="true" />
            <span>{error.message}</span>
          </span>
        </MotionErrorShake>
      ) : null}

      <p className="helper-text">修改先保存在当前确认页，点击“确认生成课程”后再统一写入课程。</p>
      <Button icon={<Save size={18} aria-hidden="true" />} type="submit">保存本章修改</Button>

      {confirmingDelete ? (
        <div className="chapter-delete-confirm" role="alert">
          <strong>确定移除“{chapter.source_title}”吗？</strong>
          <p>
            {descendantIds.length > 0
              ? `它下面的 ${descendantIds.length} 个子章节也会一起移除。`
              : "该章节将不会用于生成课程。"}
          </p>
          <div>
            <Button variant="secondary" type="button" onClick={() => setConfirmingDelete(false)}>取消</Button>
            <Button
              variant="danger"
              type="button"
              onClick={() => onDelete([chapter.chapter_id, ...descendantIds])}
            >
              确认移除
            </Button>
          </div>
        </div>
      ) : (
        <Button
          icon={<Trash2 size={18} aria-hidden="true" />}
          variant="danger"
          type="button"
          onClick={() => setConfirmingDelete(true)}
        >
          移除此章节
        </Button>
      )}

      <Button variant="text" type="button" onClick={closeSheet}>返回目录</Button>
    </form>
  );
}
