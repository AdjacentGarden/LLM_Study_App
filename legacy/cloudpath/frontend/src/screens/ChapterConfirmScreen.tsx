import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import {
  AlertTriangle,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  PencilLine,
  Save,
  Trash2
} from "lucide-react";
import { runtimeConfig } from "../config/runtime";
import type {
  ApiChapter,
  ChapterEvidence,
  TocAnalysis
} from "../types/api";
import {
  Button,
  Card,
  Metric,
  Pill,
  Section
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import {
  CollapsibleRegion,
  MotionErrorShake,
  MotionIconSwap,
  useMotionHistory,
  useOneShotFeedback,
  useReducedMotion
} from "../motion";
import {
  buildChapterTree,
  findChapterRangeConflicts,
  flattenChapterTree,
  getChapterDescendantIds,
  validateChapterDraft,
  type ChapterTreeNode,
  type ChapterValidationField,
  type ChapterValidationIssue
} from "../utils/chapterStructure";
import {
  isLessonBuildTerminal,
  successfulLessonChapterIds
} from "../utils/lessonGeneration";
import {
  apiChapterToChapter,
  averageConfidence,
  ChapterEvidenceSummary,
  ChapterEvidenceReasons,
  fileTitleBeforeParenthesis,
  sourceUnitCountLabel,
} from "./shared";

const reviewedStatuses = new Set(["匹配良好", "已人工校对", "已确认"]);

type ChapterDraft = {
  sourceTitle: string;
  aiTitle: string;
  pageStart: string;
  pageEnd: string;
  parentId: string;
};

type SelectionFeedback = {
  chapterId: string;
  sequence: number;
};

type DirectoryFeedback = {
  kind: "saved" | "deleted";
  sequence: number;
  message: string;
  motionState: "entering" | "idle";
};

type SaveFeedback = {
  chapterId: string;
  sequence: number;
};

function createChapterDraft(chapter: ApiChapter): ChapterDraft {
  return {
    sourceTitle: chapter.source_title,
    aiTitle: chapter.ai_title,
    pageStart: String(chapter.page_start),
    pageEnd: String(chapter.page_end),
    parentId: chapter.parent_id ?? ""
  };
}

function hasChapterDraftChanges(chapter: ApiChapter, draft: ChapterDraft): boolean {
  const current = createChapterDraft(chapter);
  return current.sourceTitle !== draft.sourceTitle
    || current.aiTitle !== draft.aiTitle
    || current.pageStart !== draft.pageStart
    || current.pageEnd !== draft.pageEnd
    || current.parentId !== draft.parentId;
}

function chapterPageLabel(node: ChapterTreeNode): string {
  const { page_start: start, page_end: end } = node.chapter;
  return start === end ? `${start}` : `${start}–${end}`;
}

function chapterTreeIds(node: ChapterTreeNode): string[] {
  return [node.chapter.chapter_id, ...node.children.flatMap(chapterTreeIds)];
}

function useChapterStatusMotion({
  bookId,
  chapterId,
  reducedMotion,
  status
}: {
  bookId: string | null;
  chapterId: string;
  reducedMotion: boolean;
  status: string;
}) {
  const { consume } = useMotionHistory();
  const motionKey = bookId ? `chapter-confirm:${bookId}:${chapterId}:${status}` : null;
  const [motionState, setMotionState] = useState<"entering" | "idle">("idle");
  const appliedKeyRef = useRef<string | null>(null);
  const reviewed = reviewedStatuses.has(status);

  useLayoutEffect(() => {
    if (!motionKey || !reviewed) {
      setMotionState("idle");
      return;
    }
    if (appliedKeyRef.current === motionKey) {
      if (reducedMotion) setMotionState("idle");
      return;
    }
    appliedKeyRef.current = motionKey;
    setMotionState(consume(motionKey) && !reducedMotion ? "entering" : "idle");
  }, [consume, motionKey, reducedMotion, reviewed]);

  return {
    motionKey,
    motionState,
    settle: () => setMotionState((current) => current === "entering" ? "idle" : current)
  };
}

function ChapterStatusMark({
  bookId,
  chapterId,
  status
}: {
  bookId: string | null;
  chapterId: string;
  status: string;
}) {
  const reducedMotion = useReducedMotion();
  const { motionKey, motionState, settle } = useChapterStatusMotion({ bookId, chapterId, reducedMotion, status });
  if (motionState !== "entering" || !motionKey) return null;

  return (
    <span
      key={motionKey}
      className="chapter-status-mark"
      data-motion-chapter-key={motionKey}
      data-motion-chapter-state="entering"
      aria-hidden="true"
      onAnimationEnd={(event) => {
        if (event.animationName === "motion-chapter-check-in") settle();
      }}
    >
      <CheckCircle2 size={14} strokeWidth={2.5} />
    </span>
  );
}

function ChapterDirectoryNode({
  node,
  depth,
  bookId,
  expandedIds,
  conflictChapterIds,
  evidenceByChapter,
  selectedChapterId,
  selectionFeedback,
  onToggle,
  onEdit,
  onSelect
}: {
  node: ChapterTreeNode;
  depth: number;
  bookId: string | null;
  expandedIds: Set<string>;
  conflictChapterIds: Set<string>;
  evidenceByChapter: Map<string, ChapterEvidence>;
  selectedChapterId: string | null;
  selectionFeedback: SelectionFeedback | null;
  onToggle: (chapterId: string) => void;
  onEdit: (chapterId: string, evidence?: ChapterEvidence) => void;
  onSelect: (chapterId: string, evidence?: ChapterEvidence) => void;
}) {
  const chapterId = node.chapter.chapter_id;
  const hasChildren = node.children.length > 0;
  const expanded = expandedIds.has(chapterId);
  const subtreeIds = chapterTreeIds(node);
  const conflictCount = subtreeIds.filter((id) => conflictChapterIds.has(id)).length;
  const needsReview = subtreeIds.some((id) => {
    const candidate = id === chapterId
      ? node.chapter
      : flattenChapterTree(node.children).find((item) => item.chapter.chapter_id === id)?.chapter;
    return candidate ? !reviewedStatuses.has(candidate.status) : false;
  });
  const isUnassigned = depth === 0 && node.chapter.level > 1 && !node.chapter.parent_id;
  const selected = selectedChapterId === chapterId;
  const reducedMotion = useReducedMotion();
  const expandButtonRef = useRef<HTMLButtonElement | null>(null);
  const childrenRegionId = `chapter-subtree-${chapterId}`;
  const expandButtonId = `${childrenRegionId}-toggle`;
  const [selectionMotionState, setSelectionMotionState] = useState<"entering" | "idle">("idle");
  const appliedSelectionSequenceRef = useRef<number | null>(null);

  useLayoutEffect(() => {
    if (!selected || selectionFeedback?.chapterId !== chapterId) {
      setSelectionMotionState("idle");
      return;
    }
    if (appliedSelectionSequenceRef.current === selectionFeedback.sequence) {
      if (reducedMotion) setSelectionMotionState("idle");
      return;
    }
    appliedSelectionSequenceRef.current = selectionFeedback.sequence;
    setSelectionMotionState(reducedMotion ? "idle" : "entering");
  }, [chapterId, reducedMotion, selected, selectionFeedback]);

  return (
    <li className={`toc-node toc-depth-${Math.min(depth, 3)}`}>
      <div
        className={`toc-entry${conflictCount > 0 ? " has-conflict" : needsReview || isUnassigned ? " needs-review" : ""}${selected ? " is-selected" : ""}`}
        data-motion-chapter-selection={selectionMotionState}
        onAnimationEnd={(event) => {
          if (event.animationName === "motion-chapter-selection-in") setSelectionMotionState("idle");
        }}
      >
        {hasChildren ? (
          <button
            ref={expandButtonRef}
            className="toc-expand-button"
            type="button"
            id={expandButtonId}
            aria-expanded={expanded}
            aria-controls={childrenRegionId}
            aria-label={`${expanded ? "收起" : "展开"} ${node.chapter.source_title}`}
            onClick={() => onToggle(chapterId)}
          >
            <MotionIconSwap
              state={expanded ? "expanded" : "collapsed"}
              firstState="collapsed"
              secondState="expanded"
              firstIcon={<ChevronRight size={17} />}
              secondIcon={<ChevronDown size={17} />}
            />
          </button>
        ) : (
          <span className="toc-leaf-marker" aria-hidden="true"><i /></span>
        )}

        <button
          className="toc-entry-title"
          type="button"
          data-chapter-id={chapterId}
          aria-current={selected ? "true" : undefined}
          onClick={() => onSelect(chapterId, evidenceByChapter.get(chapterId))}
        >
          <span className="toc-entry-title-copy">
            <strong>{node.chapter.source_title}</strong>
            <small>
              <span>第 {chapterPageLabel(node)} 页</span>
              {hasChildren ? <span>{node.children.length} 个下级目录</span> : null}
              {conflictCount > 0 ? (
                <em className="toc-entry-issue conflict"><AlertTriangle size={12} aria-hidden="true" />页码冲突</em>
              ) : isUnassigned ? (
                <em className="toc-entry-issue">未归类</em>
              ) : needsReview ? (
                <em className="toc-entry-issue">需检查</em>
              ) : null}
            </small>
          </span>
          <ChapterStatusMark bookId={bookId} chapterId={chapterId} status={node.chapter.status} />
        </button>

        <button
          className="toc-edit-button"
          type="button"
          aria-label={`编辑 ${node.chapter.source_title}`}
          onClick={() => onEdit(chapterId, evidenceByChapter.get(chapterId))}
        >
          <PencilLine size={14} aria-hidden="true" />
          <span>编辑</span>
        </button>
      </div>

      {hasChildren ? (
        <CollapsibleRegion
          expanded={expanded}
          id={childrenRegionId}
          labelledBy={expandButtonId}
          focusFallbackRef={expandButtonRef}
        >
          <ul className="toc-children">
            {node.children.map((child) => (
              <ChapterDirectoryNode
                key={child.chapter.chapter_id}
                node={child}
                depth={depth + 1}
                bookId={bookId}
                expandedIds={expandedIds}
                conflictChapterIds={conflictChapterIds}
                evidenceByChapter={evidenceByChapter}
                selectedChapterId={selectedChapterId}
                selectionFeedback={selectionFeedback}
                onToggle={onToggle}
                onEdit={onEdit}
                onSelect={onSelect}
              />
            ))}
          </ul>
        </CollapsibleRegion>
      ) : null}
    </li>
  );
}

function ChapterDetailEditor({
  chapter,
  chapters,
  draft,
  evidence,
  pageCount,
  saveFeedback,
  onDraftChange,
  onSave,
  onDelete
}: {
  chapter: ApiChapter;
  chapters: ApiChapter[];
  draft: ChapterDraft;
  evidence?: ChapterEvidence;
  pageCount?: number;
  saveFeedback: SaveFeedback | null;
  onDraftChange: (next: ChapterDraft) => void;
  onSave: (chapter: ApiChapter) => void;
  onDelete: (chapterIds: string[]) => void;
}) {
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
  const errorId = `chapter-validation-error-${chapter.chapter_id}`;
  const descendantIds = getChapterDescendantIds(chapters, chapter.chapter_id);
  const excludedParentIds = new Set([chapter.chapter_id, ...descendantIds]);
  const parentCandidates = chapters.filter(
    (candidate) => !excludedParentIds.has(candidate.chapter_id) && candidate.level < chapter.level
  );
  const dirty = hasChapterDraftChanges(chapter, draft);
  const reducedMotion = useReducedMotion();
  const [saveMotionState, setSaveMotionState] = useState<"entering" | "idle">("idle");
  const appliedSaveSequenceRef = useRef<number | null>(null);
  const activeSaveSequence = saveFeedback?.chapterId === chapter.chapter_id ? saveFeedback.sequence : null;

  useEffect(() => {
    setError(null);
    settleErrorShake();
    setConfirmingDelete(false);
    setSaveMotionState("idle");
  }, [chapter.chapter_id, settleErrorShake]);

  useLayoutEffect(() => {
    if (activeSaveSequence === null) {
      setSaveMotionState("idle");
      return;
    }
    if (appliedSaveSequenceRef.current === activeSaveSequence) {
      setSaveMotionState("idle");
      return;
    }
    appliedSaveSequenceRef.current = activeSaveSequence;
    setSaveMotionState(reducedMotion ? "idle" : "entering");
  }, [activeSaveSequence, reducedMotion]);

  function updateDraft(patch: Partial<ChapterDraft>) {
    const errorFieldMatches = error?.field === "form"
      || (error?.field === "source_title" && "sourceTitle" in patch)
      || (error?.field === "ai_title" && "aiTitle" in patch)
      || (error?.field === "page_start" && "pageStart" in patch)
      || (error?.field === "page_end" && "pageEnd" in patch)
      || (error?.field === "parent_id" && "parentId" in patch);
    if (errorFieldMatches) {
      setError(null);
      settleErrorShake();
    }
    onDraftChange({ ...draft, ...patch });
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

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextParent = draft.parentId
      ? chapters.find((candidate) => candidate.chapter_id === draft.parentId) ?? null
      : null;
    const parentChanged = draft.parentId !== (chapter.parent_id ?? "");
    const nextChapter: ApiChapter = {
      ...chapter,
      source_title: draft.sourceTitle.trim(),
      ai_title: draft.aiTitle.trim(),
      page_start: Number(draft.pageStart),
      page_end: Number(draft.pageEnd),
      parent_id: draft.parentId || null,
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

  return (
    <section className="chapter-detail-panel" aria-labelledby="chapter-detail-title">
      <div className="chapter-detail-heading">
        <div>
          <p>当前章节</p>
          <h3 id="chapter-detail-title">{chapter.source_title}</h3>
        </div>
        <Pill tone={chapter.status === "匹配良好" ? "mint" : "orange"}>{chapter.status}</Pill>
      </div>

      <section className="chapter-inspection-details" aria-label="章节证据与预览">
        <div className="chapter-inspection-heading">
          <strong>识别证据</strong>
          <span>层级 {chapter.level} · PDF {chapter.page_start}–{chapter.page_end} 页</span>
        </div>
        {evidence ? <ChapterEvidenceSummary evidence={evidence} /> : (
          <p className="helper-text">暂无单项识别证据，可继续人工调整名称、页码与所属上级。</p>
        )}
        <p className="chapter-source-detail">识别来源：{chapter.source || "未知"}</p>
        {evidence ? (
          <ChapterEvidenceReasons
            evidenceId={`chapter-detail-${chapter.chapter_id}`}
            reasons={evidence.reasons}
          />
        ) : null}
      </section>

      <form className="chapter-detail-form" onSubmit={submit}>
        <label className="answer-field">
          <span>原书目录名称</span>
          <input
            ref={sourceTitleRef}
            value={draft.sourceTitle}
            aria-invalid={error?.field === "source_title" ? true : undefined}
            aria-describedby={error?.field === "source_title" ? errorId : undefined}
            onChange={(event) => updateDraft({ sourceTitle: event.target.value })}
          />
        </label>
        <label className="answer-field">
          <span>课程章节名称</span>
          <input
            ref={aiTitleRef}
            value={draft.aiTitle}
            aria-invalid={error?.field === "ai_title" ? true : undefined}
            aria-describedby={error?.field === "ai_title" ? errorId : undefined}
            onChange={(event) => updateDraft({ aiTitle: event.target.value })}
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
            value={draft.pageStart}
            aria-invalid={error?.field === "page_start" ? true : undefined}
            aria-describedby={error?.field === "page_start" ? errorId : undefined}
            onChange={(event) => updateDraft({ pageStart: event.target.value })}
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
            value={draft.pageEnd}
            aria-invalid={error?.field === "page_end" ? true : undefined}
            aria-describedby={error?.field === "page_end" ? errorId : undefined}
            onChange={(event) => updateDraft({ pageEnd: event.target.value })}
            />
          </label>
        </div>
        <label className="answer-field">
          <span>所属上级章节</span>
          <select
            ref={parentRef}
            value={draft.parentId}
            aria-invalid={error?.field === "parent_id" ? true : undefined}
            aria-describedby={error?.field === "parent_id" ? errorId : undefined}
            onChange={(event) => updateDraft({ parentId: event.target.value })}
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

        <p
          key={`chapter-save-feedback:${activeSaveSequence ?? "idle"}`}
          className="chapter-draft-hint chapter-save-feedback"
          data-motion-chapter-save-key={activeSaveSequence ?? undefined}
          data-motion-chapter-save={saveMotionState}
          role="status"
          onAnimationEnd={(event) => {
            if (event.animationName === "motion-chapter-feedback-in") setSaveMotionState("idle");
          }}
        >
          {dirty ? "本地草稿已保留；保存本章修改后才会参与确认生成。" : "本章已与当前目录同步。"}
        </p>
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
      </form>
    </section>
  );
}

export function ChapterConfirmScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const { go, openSheet, parsedChapters, parsedScanResult, setActiveChapterId, setCurrentStudyPlan, setGeneratedFlashcards, setGeneratedLessons, setGeneratedQuizzes, setLessonBuildJobId, setLessonBuildJobStatus, setParsedChapters, uploadedFile } = useAppContext();
  const reducedMotion = useReducedMotion();
  const [generatingCourse, setGeneratingCourse] = useState(false);
  const [tocAnalysis, setTocAnalysis] = useState<TocAnalysis | null>(null);
  const [expandedChapterIds, setExpandedChapterIds] = useState<Set<string>>(() => new Set());
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [selectionFeedback, setSelectionFeedback] = useState<SelectionFeedback | null>(null);
  const [directoryFeedback, setDirectoryFeedback] = useState<DirectoryFeedback | null>(null);
  const [saveFeedback, setSaveFeedback] = useState<SaveFeedback | null>(null);
  const [tocAnalysisError, setTocAnalysisError] = useState<string | null>(null);
  const [courseGenerationError, setCourseGenerationError] = useState<string | null>(null);
  const [chapterDrafts, setChapterDrafts] = useState<Record<string, ChapterDraft>>({});
  const feedbackSequenceRef = useRef(0);
  const saveFeedbackSequenceRef = useRef(0);
  const isLiveResult = Boolean(uploadedFile && parsedChapters);
  const chapterMotionBookId = uploadedFile?.bookId ?? parsedScanResult?.book_id ?? null;
  const displayChapters = parsedChapters?.map(apiChapterToChapter) ?? [];
  const confidence = averageConfidence(displayChapters);
  const reviewCount = displayChapters.filter((chapter) => chapter.status !== "匹配良好").length;
  const documentTitle = uploadedFile?.name ?? parsedScanResult?.filename ?? "未选择教材";
  const displayDocumentTitle = fileTitleBeforeParenthesis(documentTitle);
  const pageCount = parsedScanResult?.page_count ?? 0;
  const sourceCount = sourceUnitCountLabel(parsedScanResult);
  const chapterRangeConflicts = findChapterRangeConflicts(parsedChapters ?? []);
  const conflictChapterIds = new Set(chapterRangeConflicts.flatMap((conflict) => [conflict.chapterId, conflict.otherChapterId]));
  const chaptersById = new Map((parsedChapters ?? []).map((chapter) => [chapter.chapter_id, chapter]));
  const chapterTree = buildChapterTree(parsedChapters ?? []);
  const conflictParentIds = [...new Set([...conflictChapterIds]
    .map((chapterId) => chaptersById.get(chapterId)?.parent_id)
    .filter((parentId): parentId is string => Boolean(parentId)))];
  const conflictParentKey = conflictParentIds.sort().join("|");

  useEffect(() => {
    if (!uploadedFile) return;
    let active = true;
    bookcourseRepository
      .getTocAnalysis(uploadedFile.bookId)
      .then((analysis) => {
        if (active) {
          setTocAnalysisError(null);
          setTocAnalysis(analysis);
        }
      })
      .catch((err) => {
        if (active) setTocAnalysisError(err instanceof Error ? err.message : "目录证据加载失败");
      });
    return () => {
      active = false;
    };
  }, [bookcourseRepository, uploadedFile]);

  useEffect(() => {
    if (!conflictParentKey) return;
    setExpandedChapterIds((current) => {
      const next = new Set(current);
      conflictParentKey.split("|").forEach((chapterId) => next.add(chapterId));
      return next;
    });
  }, [conflictParentKey]);

  useEffect(() => {
    if (!parsedChapters?.length) {
      setSelectedChapterId(null);
      return;
    }
    setSelectedChapterId((current) => (
      current && parsedChapters.some((chapter) => chapter.chapter_id === current)
        ? current
        : parsedChapters[0].chapter_id
    ));
  }, [parsedChapters]);

  const evidenceByChapter = new Map((tocAnalysis?.chapter_evidence ?? []).map((item) => [item.chapter_id, item]));
  const selectedChapter = parsedChapters?.find((chapter) => chapter.chapter_id === selectedChapterId)
    ?? parsedChapters?.[0]
    ?? null;
  const selectedDraft = selectedChapter
    ? chapterDrafts[selectedChapter.chapter_id] ?? createChapterDraft(selectedChapter)
    : null;

  function usesTabletChapterWorkspace() {
    return window.matchMedia("(min-width: 768px) and (min-height: 600px)").matches;
  }

  function publishDirectoryFeedback(kind: DirectoryFeedback["kind"], message: string) {
    feedbackSequenceRef.current += 1;
    setDirectoryFeedback({
      kind,
      message,
      motionState: reducedMotion ? "idle" : "entering",
      sequence: feedbackSequenceRef.current
    });
  }

  function selectChapter(chapterId: string, evidence = evidenceByChapter.get(chapterId)) {
    if (chapterId !== selectedChapterId) {
      feedbackSequenceRef.current += 1;
      setSelectionFeedback({ chapterId, sequence: feedbackSequenceRef.current });
    }
    setSelectedChapterId(chapterId);
    if (!usesTabletChapterWorkspace()) {
      openSheet({ type: "editChapter", chapterId, evidence });
    }
  }

  function saveChapterDraft(nextChapter: ApiChapter) {
    saveFeedbackSequenceRef.current += 1;
    setSaveFeedback({ chapterId: nextChapter.chapter_id, sequence: saveFeedbackSequenceRef.current });
    setParsedChapters(parsedChapters?.map((item) => (
      item.chapter_id === nextChapter.chapter_id ? nextChapter : item
    )) ?? null);
    setChapterDrafts((current) => ({
      ...current,
      [nextChapter.chapter_id]: createChapterDraft(nextChapter)
    }));
    publishDirectoryFeedback("saved", `已保存“${nextChapter.source_title}”的目录修改。`);
  }

  function deleteChapters(chapterIds: string[]) {
    const removalIds = new Set(chapterIds);
    setParsedChapters(parsedChapters?.filter((item) => !removalIds.has(item.chapter_id)) ?? null);
    setChapterDrafts((current) => Object.fromEntries(
      Object.entries(current).filter(([chapterId]) => !removalIds.has(chapterId))
    ));
    publishDirectoryFeedback("deleted", `已移除 ${chapterIds.length} 个目录项。`);
  }

  function toggleChapter(chapterId: string) {
    setExpandedChapterIds((current) => {
      const next = new Set(current);
      const rootNode = chapterTree.find((node) => node.chapter.chapter_id === chapterId);
      if (rootNode) {
        if (next.has(chapterId)) {
          chapterTreeIds(rootNode).forEach((id) => next.delete(id));
        } else {
          chapterTree.flatMap(chapterTreeIds).forEach((id) => next.delete(id));
          next.add(chapterId);
        }
        return next;
      }
      if (next.has(chapterId)) next.delete(chapterId);
      else next.add(chapterId);
      return next;
    });
  }

  async function confirmCourse() {
    if (!uploadedFile || !parsedChapters?.length) {
      setCourseGenerationError("没有可确认的后端目录结果，请先完成解析");
      return;
    }
    if (chapterRangeConflicts.length > 0) {
      setCourseGenerationError(`请先处理 ${chapterRangeConflicts.length} 处同级章节页码冲突`);
      return;
    }
    setCourseGenerationError(null);
    setGeneratingCourse(true);
    try {
      const confirmedChapters = await bookcourseRepository.confirmChapters(uploadedFile.bookId, parsedChapters ?? []);
      setParsedChapters(confirmedChapters);
      const lessonJob = await bookcourseRepository.buildLessons(uploadedFile.bookId, {
        force: true
      });
      setLessonBuildJobId(lessonJob.job_id);
      setLessonBuildJobStatus(lessonJob);
      let finalJob = lessonJob;
      for (let attempt = 0; attempt < 50 && !isLessonBuildTerminal(finalJob.status); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        finalJob = await bookcourseRepository.getLessonJob(lessonJob.job_id);
        setLessonBuildJobStatus(finalJob);
      }
      if (finalJob.status === "failed") {
        throw new Error(finalJob.error ?? "课程内容生成失败");
      }
      const finalChapterIds = successfulLessonChapterIds(finalJob);
      if (finalChapterIds.length === 0) {
        throw new Error("没有章节成功生成课程");
      }
      const lessons = finalJob.lessons;
      const [cards, quizzes, plan] = await Promise.all([
        bookcourseRepository.buildFlashcards(uploadedFile.bookId, { chapter_ids: finalChapterIds }),
        bookcourseRepository.buildQuizzes(uploadedFile.bookId, { chapter_ids: finalChapterIds }),
        bookcourseRepository.createStudyPlan(uploadedFile.bookId, { user_id: runtimeConfig.defaultUserId })
      ]);
      setGeneratedLessons(lessons);
      setGeneratedFlashcards(cards);
      setGeneratedQuizzes(quizzes);
      setCurrentStudyPlan(plan);
      setActiveChapterId(finalJob.lessons[0]?.chapter_id ?? null);
      go("courseReady");
    } catch (err) {
      setCourseGenerationError(err instanceof Error ? err.message : "课程内容生成失败");
    } finally {
      setGeneratingCourse(false);
    }
  }

  return (
    <div className="screen-stack chapter-confirm-screen">
      <Card className="book-summary">
        <span className="book-summary-icon">
          <FileText size={32} aria-hidden="true" />
        </span>
        <div>
          <Pill tone="purple">{isLiveResult ? "本次文件解析结果" : "等待后端解析结果"}</Pill>
          <h2 title={documentTitle}>{displayDocumentTitle}</h2>
          <p>
            {displayChapters.length > 0
              ? `已识别 ${displayChapters.length} 个目录项 · ${sourceCount} · 平均置信度 ${confidence}%`
              : "解析完成后会在这里展示目录、页码和 chunk 归属证据"}
          </p>
        </div>
      </Card>
      <div className="mapping-summary">
        <Metric label="目录匹配" value={`${confidence}%`} />
        <Metric label="来源定位" value={isLiveResult ? sourceCount : "20 节"} />
        <Metric label="需检查" value={`${reviewCount} 项`} />
      </div>
      {chapterRangeConflicts.length > 0 ? (
        <Card className="chapter-conflict-card">
          <div className="chapter-conflict-heading">
            <span className="chapter-conflict-icon">
              <AlertTriangle size={20} aria-hidden="true" />
            </span>
            <div>
              <h3>发现 {chapterRangeConflicts.length} 处页码冲突</h3>
              <p>同一上级下的并列章节不能占用同一 PDF 页。请编辑页码或移除误识别章节。</p>
            </div>
          </div>
          <div className="chapter-conflict-list">
            {chapterRangeConflicts.slice(0, 3).map((conflict) => {
              const first = chaptersById.get(conflict.chapterId);
              const second = chaptersById.get(conflict.otherChapterId);
              const overlapPages = conflict.overlapStart === conflict.overlapEnd
                ? `第 ${conflict.overlapStart} 页`
                : `第 ${conflict.overlapStart}–${conflict.overlapEnd} 页`;
              return (
                <button
                  key={`${conflict.chapterId}-${conflict.otherChapterId}`}
                  type="button"
                  onClick={() => selectChapter(conflict.chapterId)}
                >
                  <span>{overlapPages}</span>
                  <strong>{first?.source_title ?? conflict.chapterId}</strong>
                  <small>与“{second?.source_title ?? conflict.otherChapterId}”重叠，点此处理</small>
                </button>
              );
            })}
          </div>
        </Card>
      ) : null}
      <div className="chapter-confirm-workspace">
      <Section
        className="chapter-confirm-directory"
        title="本次解析目录"
        action={chapterTree.length > 0 ? <span className="toc-directory-count">{chapterTree.length} 章</span> : null}
      >
        {tocAnalysisError ? (
          <p className="toc-directory-feedback toc-directory-error" role="alert">
            {tocAnalysisError}
          </p>
        ) : null}
        {directoryFeedback ? (
          <p
            key={`chapter-directory-feedback:${directoryFeedback.sequence}`}
            className="toc-directory-feedback"
            data-motion-chapter-feedback={directoryFeedback.motionState}
            role="status"
            onAnimationEnd={(event) => {
              if (event.animationName !== "motion-chapter-feedback-in") return;
              setDirectoryFeedback((current) => (
                current?.sequence === directoryFeedback.sequence
                  ? { ...current, motionState: "idle" }
                  : current
              ));
            }}
          >
            {directoryFeedback.message}
          </p>
        ) : null}
        {chapterTree.length > 0 ? (
          <ol className="toc-directory" aria-label="解析后的书籍目录">
            {chapterTree.map((node) => (
              <ChapterDirectoryNode
                key={node.chapter.chapter_id}
                node={node}
                depth={0}
                bookId={chapterMotionBookId}
                expandedIds={expandedChapterIds}
                conflictChapterIds={conflictChapterIds}
                evidenceByChapter={evidenceByChapter}
                selectedChapterId={selectedChapterId}
                selectionFeedback={selectionFeedback}
                onToggle={toggleChapter}
                onEdit={selectChapter}
                onSelect={selectChapter}
              />
            ))}
          </ol>
        ) : (
          <Card className="parse-empty-card">
            <FileText size={30} aria-hidden="true" />
            <h3>还没有目录结果</h3>
            <p>请等待后端解析任务完成，或返回上传页重新创建任务。</p>
          </Card>
        )}
      </Section>
        <aside className="chapter-confirm-detail" aria-label="章节证据与编辑区">
          {selectedChapter && selectedDraft && parsedChapters ? (
            <ChapterDetailEditor
              chapter={selectedChapter}
              chapters={parsedChapters}
              draft={selectedDraft}
              evidence={evidenceByChapter.get(selectedChapter.chapter_id)}
              pageCount={pageCount}
              saveFeedback={saveFeedback}
              onDraftChange={(nextDraft) => {
                setChapterDrafts((current) => ({
                  ...current,
                  [selectedChapter.chapter_id]: nextDraft
                }));
              }}
              onSave={saveChapterDraft}
              onDelete={deleteChapters}
            />
          ) : (
            <Card className="parse-empty-card chapter-detail-empty">
              <FileText size={30} aria-hidden="true" />
              <h3>选择一个章节</h3>
              <p>左侧目录会保留当前本地草稿；选择章节后可查看证据并编辑。</p>
            </Card>
          )}
        </aside>
      </div>
      <div className="chapter-confirm-actions">
      {generatingCourse || chapterRangeConflicts.length > 0 || courseGenerationError ? (
        <div
          key={`chapter-confirm-action:${generatingCourse ? "generating" : courseGenerationError ? "error" : "blocked"}`}
          className="chapter-confirm-action-feedback"
          data-motion-chapter-action={generatingCourse ? "generating" : courseGenerationError ? "error" : "blocked"}
        >
          {generatingCourse ? <p role="status">正在确认目录并生成课程内容。</p> : null}
          {chapterRangeConflicts.length > 0 ? <p className="confirm-blocked-helper">处理完成后即可生成课程</p> : null}
          {courseGenerationError ? <p className="confirm-blocked-helper" role="alert">{courseGenerationError}</p> : null}
        </div>
      ) : null}
      <Button
        loading={generatingCourse}
        disabled={generatingCourse || chapterRangeConflicts.length > 0}
        onClick={confirmCourse}
      >
        {chapterRangeConflicts.length > 0 ? `请先处理 ${chapterRangeConflicts.length} 处冲突` : "确认生成课程"}
      </Button>
      </div>
    </div>
  );
}
