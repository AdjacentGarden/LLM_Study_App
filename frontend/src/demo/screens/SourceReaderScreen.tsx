import { runtimeConfig } from "../config/runtime";
import { request } from "../../api/transport";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  Eraser,
  FileText,
  Hand,
  Highlighter,
  Mic2,
  MessageSquareText,
  NotebookPen,
  PenLine,
  Redo2,
  Sparkles,
  Trash2,
  Undo2
} from "lucide-react";
import {
  Button,
  Card
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { globalMotionFallbackMs, SkeletonReveal, useImageMotion, useLocalMotionItem, useMotionPresence, useReducedMotion, type LoadState } from "../motion";
import { sourcePageImageUrl } from "./shared";
import { InkAnnotationSurface } from "../features/studyNotes/InkAnnotationSurface";
import { highlighterBrushWidths, penBrushWidths } from "../features/studyNotes/ink";
import { SourceRegionAiPanel, type RegionAiReference } from "../features/studyNotes/SourceRegionAiPanel";
import { SourceTextNotePanel, sourceTextNoteAnimationNames, type SourceTextNotePanelHandle } from "../features/studyNotes/SourceTextNotePanel";
import { SourceTextAnnotationLayer, type TextNoteLocation } from "../features/studyNotes/SourceTextAnnotationLayer";
import { textNotePosition } from "../features/studyNotes/textAnnotations";
import { captureRegionImage } from "../features/studyNotes/regionCapture";
import { describeRegion, type PageRegion } from "../features/studyNotes/regionAsk";
import { createStudyNoteId, getStudyNote as ownedGetStudyNote, listStudyNotes, putStudyNote as ownedPutStudyNote } from "../features/studyNotes/repository";
import { resolveSourceReaderHeading } from "../features/studyNotes/sourceReaderHeading";
import { pageSwipeDirection, type PageSwipeDirection } from "../features/studyNotes/pageSwipe";
import { noteAnchorFromSource, type InkStroke, type InkStudyNote, type InkTool, type NoteAnchor, type NotePipelinePhase, type StudyNote, type TextStudyNote } from "../features/studyNotes/types";
import type { SourcePageTarget } from "../types/app";



type ReaderNoteMode = "read" | "ink" | "text" | "ai";
type TextNoteEditor = {
  key: string;
  instanceId: number;
  anchor: NoteAnchor;
  existing?: TextStudyNote;
  location: TextNoteLocation;
};

function getTextEditorKey(editor: TextNoteEditor) {
  return `${editor.key}:${editor.instanceId}`;
}

export function previousSourcePage(page: number) {
  return Math.max(1, Math.trunc(page) - 1);
}

export function nextSourcePage(page: number, maxPage: number) {
  const boundedMaximum = Math.max(1, Math.trunc(maxPage));
  return Math.min(boundedMaximum, Math.max(1, Math.trunc(page)) + 1);
}

export function SourceReaderScreen() {
  const owner=useRef(runtimeConfig.defaultUserId).current;
  const getStudyNote=(id:string)=>ownedGetStudyNote(id,owner);
  const putStudyNote=(note:Parameters<typeof ownedPutStudyNote>[0])=>ownedPutStudyNote(note,owner);
  const {
    activeChapterId,
    courses,
    finishNoteCapture,
    go,
    noteCaptureIntent,
    parsedChapters,
    parsedScanResult,
    sourcePageTarget,
    sourceReaderCurrentPage: currentPage,
    setSourceReaderCurrentPage: setCurrentPage,
    startNote,
    showToast,
    uploadedFile
  } = useAppContext();
  const bookId = sourcePageTarget?.bookId ?? uploadedFile?.bookId ?? "";
  const readerCourseId = sourcePageTarget?.courseId ?? courses.state.activeCourseId ?? undefined;
  const pageCount = parsedScanResult?.page_count ?? null;
  const targetStart = Math.max(1, sourcePageTarget?.pageStart ?? 1);
  const targetEnd = Math.max(targetStart, sourcePageTarget?.pageEnd ?? targetStart);
  const inlineCitationText = sourcePageTarget?.sourceText?.trim() ?? "";
  const [localPage, setLocalPage] = useState<{ bookId: string; page: number; image: string | null; text: string; pageCount: number } | null>(null);
  useEffect(() => {
    if(!bookId)return;let active=true;
    void request<{source_text:string;image_url:string|null}>(`/api/demo/books/${encodeURIComponent(bookId)}/pages/${currentPage}`).then(value=>{if(active)setLocalPage({bookId,page:currentPage,image:value.image_url,text:value.source_text,pageCount:pageCount??currentPage});}).catch(error=>{if(active)showToast(error.message,"warning");});
    return()=>{active=false;};
  },[bookId,currentPage,pageCount,showToast]);
  const [failedImageKey, setFailedImageKey] = useState<string | null>(null);
  const [noteMode, setNoteMode] = useState<ReaderNoteMode>(noteCaptureIntent?.kind === "ink" ? "ink" : noteCaptureIntent?.kind === "text" ? "text" : "read");
  const [selectedText, setSelectedText] = useState("");
  const [pageNotes, setPageNotes] = useState<StudyNote[]>([]);
  const [showPageNotes, setShowPageNotes] = useState(false);
  const [textEditor, setTextEditor] = useState<TextNoteEditor | null>(null);
  const textEditorInstanceRef = useRef(0);
  const textPanelRef = useRef<SourceTextNotePanelHandle | null>(null);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const switchingTextRef = useRef(false);
  const noteIdRef = useRef(noteCaptureIntent?.existingNoteId ?? createStudyNoteId("ink"));
  const latestInkNoteRef = useRef<InkStudyNote | null>(null);
  const autosaveTimerRef = useRef<number | null>(null);
  const inkSaveQueueRef = useRef<Promise<InkStudyNote | void>>(Promise.resolve());
  const inkHasChangesRef = useRef(false);
  const queuedInkVersionRef = useRef<number | null>(null);
  const readSwipeRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const [noteLoaded, setNoteLoaded] = useState(false);
  const [requestedInkNoteId, setRequestedInkNoteId] = useState(noteCaptureIntent?.existingNoteId);
  const [pages, setPages] = useState<Record<string, InkStroke[]>>({});
  const [undoStack, setUndoStack] = useState<InkStroke[][]>([]);
  const [redoStack, setRedoStack] = useState<InkStroke[][]>([]);
  const [inkTool, setInkTool] = useState<InkTool | "eraser">("pen");
  const [inkColor, setInkColor] = useState("#7c3aed");
  const [penWidth, setPenWidth] = useState<number>(penBrushWidths[1]);
  const [highlighterWidth, setHighlighterWidth] = useState<number>(highlighterBrushWidths[0]);
  const inkWidth = inkTool === "highlighter" ? highlighterWidth : penWidth;
  const brushWidths = inkTool === "highlighter" ? highlighterBrushWidths : penBrushWidths;
  const [pageDirection, setPageDirection] = useState<"forward" | "back">("forward");
  const [fingerWrite, setFingerWrite] = useState(false);
  const [noteVersion, setNoteVersion] = useState(1);
  const [regionAsk, setRegionAsk] = useState<RegionAiReference | null>(null);
  const reducedMotion = useReducedMotion();
  const textEditorMotion = useMotionPresence<TextNoteEditor>({
    requested: regionAsk ? null : textEditor,
    getKey: getTextEditorKey,
    reducedMotion,
    motionNames: sourceTextNoteAnimationNames,
    maxMotionMs: globalMotionFallbackMs
  });
  const renderedTextEditor = textEditorMotion.rendered;
  const [committedRegion, setCommittedRegion] = useState<PageRegion | null>(null);
  const [pipelinePhase, setPipelinePhase] = useState<NotePipelinePhase>("idle");
  const [recognizedText, setRecognizedText] = useState("");
  const [organizedText, setOrganizedText] = useState("");
  const [organizedFromVersion, setOrganizedFromVersion] = useState<number | undefined>();
  const annotationActive = noteMode === "ink";
  const regionAskActive = noteMode === "ai";
  const annotationInteractive = annotationActive || regionAskActive;
  const currentPageKey = String(currentPage);
  const currentStrokes = pages[currentPageKey] ?? [];
  const maxPage = Math.max(localPage?.bookId === bookId ? localPage.pageCount : pageCount ?? targetEnd, targetEnd, 1);
  const isOnTargetRange = currentPage >= targetStart && currentPage <= targetEnd;
  const currentInlineCitationText = localPage?.bookId === bookId && localPage.page === currentPage ? localPage.text : isOnTargetRange ? inlineCitationText : "";
  // Only URLs committed to the tracked + SHA-256 publication manifest are
  // eligible. An unknown book/page never becomes a guessed static URL.
  const publishedImageUrl = undefined;
  const localImageUrl = localPage?.bookId === bookId && localPage.page === currentPage ? localPage.image : null;
  const targetImageUrl = currentPage === targetStart ? sourcePageTarget?.previewImageUrl : undefined;
  const imageUrl = publishedImageUrl || localImageUrl || targetImageUrl || undefined;
  const imageKey = `${bookId}:${currentPage}:${imageUrl ?? "unpublished"}`;
  const imageFailed = Boolean(imageUrl) && failedImageKey === imageKey;
  const imageMotion = useImageMotion(imageUrl);
  const showInlineTextFallback = Boolean(currentInlineCitationText)
    && (!imageUrl || imageFailed || imageMotion.state === "failed");
  const sourceLoadState: LoadState = !imageUrl || imageFailed || imageMotion.state === "failed"
    ? "error"
    : imageMotion.state === "loading"
      ? "loading"
      : "ready";
  const pageMotion = useLocalMotionItem(`source-page:${imageKey}`, "source-page-content", { ready: sourceLoadState !== "loading" || showInlineTextFallback });
  const pageTextNotes = pageNotes.filter((note): note is TextStudyNote => note.kind === "text" && note.anchor?.bookId === bookId && note.anchor.pageStart === currentPage);
  const draftTextMarker = textEditor ? { id: textEditor.key, position: textEditor.location.position } : undefined;

  useEffect(() => {
    setCurrentPage(targetStart);
    setFailedImageKey(null);
    setSelectedText("");
    setRegionAsk(null);
    setCommittedRegion(null);
    setTextEditor(null);
    setPageDirection("forward");
    setNoteMode(noteCaptureIntent?.kind === "ink" ? "ink" : noteCaptureIntent?.kind === "text" ? "text" : "read");
  }, [sourcePageTarget]);

  useEffect(() => {
    if (noteCaptureIntent?.kind === "ink") setNoteMode("ink");
    if (noteCaptureIntent?.kind === "text") {
      setNoteMode("text");
      if (!noteCaptureIntent.existingNoteId) finishNoteCapture();
    }
  }, [noteCaptureIntent?.existingNoteId, noteCaptureIntent?.kind]);

  useEffect(() => {
    if (noteCaptureIntent?.kind !== "text" || !noteCaptureIntent.existingNoteId) return;
    const pageElement = workspaceRef.current?.querySelector<HTMLElement>(".source-annotation-page, .source-text-document-content");
    if (!pageElement) return;
    let active = true;
    void getStudyNote(noteCaptureIntent.existingNoteId).then((note) => {
      if (!active) return;
      if (note?.kind === "text") {
        setTextEditor({
          key: note.id,
          instanceId: ++textEditorInstanceRef.current,
          anchor: note.anchor ?? currentNoteAnchor(),
          existing: note,
          location: { position: note.position ?? { x: .5, y: .35 }, pageElement }
        });
        setNoteMode("text");
      }
      finishNoteCapture();
    });
    return () => { active = false; };
  }, [noteCaptureIntent?.existingNoteId, noteCaptureIntent?.kind, sourceLoadState, showInlineTextFallback]);

  useEffect(() => {
    setFailedImageKey(null);
  }, [currentPage, imageUrl]);

  const heading = resolveSourceReaderHeading(
    sourcePageTarget,
    currentPage,
    parsedChapters,
    parsedScanResult,
    uploadedFile?.name?.trim() || "教材原文"
  );
  const currentChapter = heading.chapter;
  const displayTitle = heading.title;

  useEffect(() => {
    let active = true;
    const load = () => {
      void listStudyNotes().then((notes) => {
        if (!active) return;
        setPageNotes(notes.filter((note) => note.anchor?.bookId === bookId && (!note.anchor.courseId || note.anchor.courseId === readerCourseId) && (
          note.anchor.pageStart === currentPage
          || note.kind === "ink" && Boolean(note.pages[currentPageKey]?.length)
        )));
      });
    };
    load();
    window.addEventListener("bookcourse:study-notes-changed", load);
    return () => {
      active = false;
      window.removeEventListener("bookcourse:study-notes-changed", load);
    };
  }, [bookId, currentPage, currentPageKey, readerCourseId]);

  useEffect(() => {
    if (noteMode !== "read") return;
    const savedInk = pageNotes.find((note): note is InkStudyNote => note.kind === "ink" && !note.id.startsWith("ink-demo-"));
    if (savedInk) setPages(savedInk.pages);
  }, [noteMode, pageNotes]);

  function currentSourceTarget() {
    return {
      courseId: readerCourseId,
      bookId,
      title: displayTitle,
      pageStart: currentPage,
      pageEnd: currentPage,
      printedPageStart: heading.printedPage,
      printedPageEnd: heading.printedPage,
      sourceText: currentInlineCitationText || undefined,
      from: sourcePageTarget?.from ?? "source"
    } satisfies SourcePageTarget;
  }

  function currentNoteAnchor(quote = selectedText) {
    return {
      ...noteAnchorFromSource(currentSourceTarget(), quote || undefined),
      bookTitle: uploadedFile?.name ?? displayTitle,
      chapterId: currentChapter?.chapter_id ?? activeChapterId ?? undefined,
      chapterTitle: currentChapter?.source_title ?? displayTitle
    };
  }

  function openInk(note?: StudyNote) {
    if (!imageUrl || imageFailed || sourceLoadState !== "ready") {
      showToast("当前页暂无可批注原图", "warning");
      return;
    }
    const existing = note?.kind === "ink" ? note : pageNotes.find((item) => item.kind === "ink" && !item.id.startsWith("ink-demo-"));
    const id = existing?.id ?? createStudyNoteId("ink");
    noteIdRef.current = id;
    setRequestedInkNoteId(id);
    setSelectedText("");
    setShowPageNotes(false);
    setNoteMode("ink");
  }

  async function flushTextEditor() {
    if (!textEditor) return true;
    try {
      await textPanelRef.current?.save();
      setTextEditor(null);
      return true;
    } catch {
      showToast("文字批注尚未保存，请重试", "warning");
      return false;
    }
  }

  async function openTextAt(location: TextNoteLocation, note?: TextStudyNote) {
    if (switchingTextRef.current || note && note.id === textEditor?.key) return;
    if (textEditor) {
      await textPanelRef.current?.close();
      return;
    }
    switchingTextRef.current = true;
    const anchor = note?.anchor ?? currentNoteAnchor();
    try {
      const stored = note ? await getStudyNote(note.id) : null;
      const latestNote = stored?.kind === "text" ? stored : note;
      setRegionAsk(null);
      setCommittedRegion(null);
      setShowPageNotes(false);
      setTextEditor({ key: latestNote?.id ?? createStudyNoteId("text"), instanceId: ++textEditorInstanceRef.current, anchor: latestNote?.anchor ?? anchor, existing: latestNote, location });
      setSelectedText("");
      setNoteMode("text");
      window.getSelection()?.removeAllRanges();
    } finally {
      switchingTextRef.current = false;
    }
  }

  function openText(note?: TextStudyNote) {
    setShowPageNotes(false);
    setRegionAsk(null);
    setCommittedRegion(null);
    const pageElement = workspaceRef.current?.querySelector<HTMLElement>(".source-annotation-page, .source-text-document-content");
    if (pageElement && (note || selectedText)) {
      const selection = window.getSelection();
      const selectionBox = selection?.rangeCount ? selection.getRangeAt(0).getBoundingClientRect() : null;
      const position = note?.position ?? (selectionBox && selectionBox.width
        ? textNotePosition(selectionBox.right, selectionBox.bottom, pageElement.getBoundingClientRect())
        : { x: .5, y: .35 });
      void openTextAt({ position, pageElement }, note);
      return;
    }
    setTextEditor(null);
    setNoteMode("text");
  }

  /** The purple star entry: the pen draws the AI reference instead of ink. */
  async function openRegionAsk() {
    if (regionAskActive) {
      closeRegionAsk();
      return;
    }
    if (!imageUrl || imageFailed || sourceLoadState !== "ready") {
      showToast("当前页暂无可圈选的原文图", "warning");
      return;
    }
    if (annotationActive) {
      const leftInk = await leaveInk();
      if (!leftInk) return;
    }
    if (!await flushTextEditor()) return;
    setSelectedText("");
    setShowPageNotes(false);
    setCommittedRegion(null);
    setRegionAsk(null);
    setNoteMode("ai");
  }

  function closeRegionAsk() {
    setRegionAsk(null);
    if (noteMode === "ai") setNoteMode("read");
  }

  /** Lifts the dashes off the page, crops them and opens the dialog. */
  function commitRegion(region: PageRegion) {
    const image = imageMotion.imageRef?.current ?? null;
    const capture = captureRegionImage(image, region);
    if (!capture) {
      showToast("圈选区域截图失败，请重新圈选一次", "warning");
      return;
    }
    // The selection's client rectangle is the morph origin of the dialog.
    const pageBox = image?.getBoundingClientRect();
    const origin = pageBox && pageBox.width > 0 && pageBox.height > 0
      ? {
          left: pageBox.left + region.x * pageBox.width,
          top: pageBox.top + region.y * pageBox.height,
          width: region.width * pageBox.width,
          height: region.height * pageBox.height
        }
      : null;
    setCommittedRegion(region);
    setRegionAsk({
      dataUrl: capture.dataUrl,
      label: describeRegion(region, heading.pageLabel),
      region,
      origin
    });
  }

  async function openVoice(note?: StudyNote) {
    if (!await flushTextEditor()) return;
    if (annotationActive && noteLoaded) {
      try {
        await saveDraft();
      } catch {
        showToast("本页批注保存失败，请重试", "warning");
        return;
      }
    }
    startNote({
      kind: "voice",
      anchor: note?.anchor ?? currentNoteAnchor(),
      source: currentSourceTarget(),
      existingNoteId: note?.kind === "voice" ? note.id : undefined,
      from: "source"
    });
  }

  function buildInkNote(overrides: Partial<InkStudyNote> = {}): InkStudyNote {
    const now = Date.now();
    const source = currentSourceTarget();
    return {
      id: noteIdRef.current,
      kind: "ink",
      title: `教材批注 · ${displayTitle}`,
      anchor: noteCaptureIntent?.anchor ?? {
        ...noteAnchorFromSource(source),
        chapterId: currentChapter?.chapter_id ?? activeChapterId ?? undefined,
        chapterTitle: currentChapter?.source_title ?? displayTitle
      },
      pages,
      createdAt: now,
      updatedAt: now,
      noteVersion,
      pipelinePhase,
      recognizedText: recognizedText || undefined,
      organizedText: organizedText || undefined,
      organizedFromVersion,

      ...overrides
    };
  }

  if (annotationActive && noteLoaded) latestInkNoteRef.current = buildInkNote();

  function saveDraft() {
    const snapshot = buildInkNote();
    if (!inkHasChangesRef.current || queuedInkVersionRef.current === snapshot.noteVersion) {
      return inkSaveQueueRef.current;
    }
    queuedInkVersionRef.current = snapshot.noteVersion;
    const persist = async () => {
      const existing = await getStudyNote(snapshot.id);
      const next: InkStudyNote = {
        ...snapshot,
        createdAt: existing?.createdAt ?? snapshot.createdAt,
        organizedVersions: existing?.organizedVersions ?? [],
        evidence: existing?.evidence,
        updatedAt: Date.now()
      };
      await putStudyNote(next);
      if (latestInkNoteRef.current?.noteVersion === snapshot.noteVersion) inkHasChangesRef.current = false;
      return next;
    };
    const queued = inkSaveQueueRef.current.catch(() => undefined).then(persist);
    inkSaveQueueRef.current = queued.catch((reason) => {
      if (queuedInkVersionRef.current === snapshot.noteVersion) queuedInkVersionRef.current = null;
      throw reason;
    });
    return queued;
  }

  useEffect(() => {
    if (!annotationActive) {
      setNoteLoaded(false);
      return;
    }
    let cancelled = false;
    const requestedId = requestedInkNoteId ?? noteCaptureIntent?.existingNoteId;
    if (requestedId) noteIdRef.current = requestedId;
    inkHasChangesRef.current = false;
    queuedInkVersionRef.current = null;
    void getStudyNote(noteIdRef.current).then((note) => {
      if (cancelled) return;
      if (note?.kind === "ink") {
        setPages(note.pages);
        setNoteVersion(note.noteVersion);
        setPipelinePhase(note.pipelinePhase);
        setRecognizedText(note.recognizedText ?? "");
        setOrganizedText(note.organizedText ?? "");
        setOrganizedFromVersion(note.organizedFromVersion);
      } else {
        setPages({});
        setNoteVersion(1);
        setPipelinePhase("idle");
        setRecognizedText("");
        setOrganizedText("");
        setOrganizedFromVersion(undefined);
      }
      setNoteLoaded(true);
    }).catch(() => {
      if (!cancelled) showToast("批注暂时无法读取，请重试", "warning");
    });
    return () => {
      cancelled = true;
    };
  }, [annotationActive, noteCaptureIntent?.existingNoteId, requestedInkNoteId]);

  useEffect(() => {
    if (!annotationActive || !noteLoaded || !inkHasChangesRef.current) return;
    if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current);
    autosaveTimerRef.current = window.setTimeout(() => {
      void saveDraft().catch(() => showToast("批注暂时无法自动保存，当前笔迹仍保留在页面", "warning"));
    }, 500);
    return () => {
      if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current);
    };
  }, [annotationActive, noteLoaded, noteVersion, pages]);

  useEffect(() => () => {
    if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current);
    const latest = latestInkNoteRef.current;
    if (latest && inkHasChangesRef.current) {
      void inkSaveQueueRef.current.catch(() => undefined)
        .then(async () => {
          const existing = await getStudyNote(latest.id);
          if (existing?.noteVersion && existing.noteVersion >= latest.noteVersion) return;
          await putStudyNote({
            ...latest,
            createdAt: existing?.createdAt ?? latest.createdAt,
            organizedVersions: existing?.organizedVersions ?? [],
            evidence: existing?.evidence,
            updatedAt: Date.now()
          });
        })
        .catch(() => undefined);
    }
  }, []);

  function updateCurrentStrokes(next: InkStroke[]) {
    inkHasChangesRef.current = true;
    setUndoStack((history) => [...history.slice(-49), currentStrokes]);
    setRedoStack([]);
    setPages((current) => ({ ...current, [currentPageKey]: next }));
    setNoteVersion((version) => version + 1);
    if (pipelinePhase !== "idle") setPipelinePhase("idle");
  }

  function undoInk() {
    const previous = undoStack.at(-1);
    if (!previous) return;
    inkHasChangesRef.current = true;
    setUndoStack((history) => history.slice(0, -1));
    setRedoStack((history) => [...history, currentStrokes]);
    setPages((current) => ({ ...current, [currentPageKey]: previous }));
    setNoteVersion((version) => version + 1);
  }

  function redoInk() {
    const next = redoStack.at(-1);
    if (!next) return;
    inkHasChangesRef.current = true;
    setRedoStack((history) => history.slice(0, -1));
    setUndoStack((history) => [...history, currentStrokes]);
    setPages((current) => ({ ...current, [currentPageKey]: next }));
    setNoteVersion((version) => version + 1);
  }

  async function changePage(next: number) {
    if (next === currentPage) return;
    if (!await flushTextEditor()) return;
    if (annotationActive && noteLoaded) {
      try {
        await saveDraft();
      } catch {
        showToast("本页批注保存失败，请重试后翻页", "warning");
        return;
      }
    }
    setUndoStack([]);
    setRedoStack([]);
    setSelectedText("");
    setShowPageNotes(false);
    setRegionAsk(null);
    setCommittedRegion(null);
    setPageDirection(next < currentPage ? "back" : "forward");
    setCurrentPage(next);
  }

  function swipePage(direction: PageSwipeDirection) {
    void changePage(direction === 1 ? nextSourcePage(currentPage, maxPage) : previousSourcePage(currentPage));
  }

  function selectInkTool(tool: InkTool | "eraser") {
    if (noteMode === "text") return;
    if (annotationActive && inkTool === tool) {
      void leaveInk();
      return;
    }
    setInkTool(tool);
    if (!annotationActive) openInk();
  }

  function chooseInkAppearance(update: () => void) {
    if (noteMode === "text") return;
    update();
    if (!annotationActive) openInk();
  }

  async function leaveInk() {
    if (noteLoaded) {
      try {
        await saveDraft();
      } catch {
        showToast("批注尚未保存，请重试", "warning");
        return false;
      }
    }
    finishNoteCapture();
    setNoteMode("read");
    return true;
  }

  useEffect(() => {
    if (!annotationInteractive && !regionAsk && noteMode !== "text") return;
    const handleBack = (event: Event) => {
      if (event.defaultPrevented) return;
      if (noteMode === "text" && textEditor && !regionAsk) return;
      event.preventDefault();
      if (regionAskActive || regionAsk) {
        closeRegionAsk();
        return;
      }
      if (noteMode === "text") {
        setNoteMode("read");
        return;
      }
      void leaveInk();
    };
    window.addEventListener("bookcourse:native-back", handleBack);
    return () => window.removeEventListener("bookcourse:native-back", handleBack);
  });

  if (!bookId && !imageUrl && !inlineCitationText) {
    return (
      <div className="screen-stack source-reader-screen">
        <Card className="source-reader-empty">
          <FileText size={34} aria-hidden="true" />
          <h2>还没有可查看的原文</h2>
          <p>请先上传并解析教材，再从闪卡、课程证据或做题诊断中打开原文页。</p>
          <Button onClick={() => go("upload")}>去上传教材</Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="screen-stack source-reader-screen" data-annotation-active={annotationActive ? "true" : "false"} data-note-mode={noteMode}>
      <div className="source-reader-topbar" aria-label="原文页工具栏">
        <div className="source-reader-ai-entry">
          <button
            className="source-region-ai-toggle"
            type="button"
            aria-label="圈选问 AI"
            aria-pressed={regionAskActive}
            title="圈选问 AI"
            onClick={() => void openRegionAsk()}
          >
            <Sparkles size={20} aria-hidden="true" />
          </button>
        </div>
        <div className="ink-annotation-tools ink-tools-scroll" aria-label="手写批注工具">
          <div className="ink-tool-row" role="group" aria-label="笔工具">
            <button type="button" aria-label="钢笔" aria-pressed={annotationActive && inkTool === "pen"} disabled={noteMode === "text" || !imageUrl || sourceLoadState !== "ready"} onClick={() => selectInkTool("pen")}><PenLine size={19} /></button>
            <button type="button" aria-label="荧光笔" aria-pressed={annotationActive && inkTool === "highlighter"} disabled={noteMode === "text" || !imageUrl || sourceLoadState !== "ready"} onClick={() => selectInkTool("highlighter")}><Highlighter size={19} /></button>
            <button type="button" aria-label="整笔擦除" aria-pressed={annotationActive && inkTool === "eraser"} disabled={noteMode === "text" || !imageUrl || sourceLoadState !== "ready"} onClick={() => selectInkTool("eraser")}><Eraser size={19} /></button>
          </div>
          <div className="ink-tool-row" role="group" aria-label="历史操作">
            <button type="button" aria-label="撤销" disabled={!annotationActive || undoStack.length === 0} onClick={undoInk}><Undo2 size={19} /></button>
            <button type="button" aria-label="重做" disabled={!annotationActive || redoStack.length === 0} onClick={redoInk}><Redo2 size={19} /></button>
            <button
              type="button"
              aria-label="清空本页批注"
              disabled={!annotationActive || currentStrokes.length === 0}
              onClick={() => {
                if (window.confirm("确认清空本页全部批注吗？")) updateCurrentStrokes([]);
              }}
            ><Trash2 size={19} /></button>
          </div>
          <div className="ink-color-row" role="group" aria-label="笔迹颜色">
            {["#7c3aed", "#2563eb", "#dc2626", "#20263a"].map((color) => (
              <button
                key={color}
                type="button"
                aria-label={`选择颜色 ${color}`}
                aria-pressed={inkColor === color}
                disabled={noteMode === "text" || !imageUrl || sourceLoadState !== "ready"}
                style={{ backgroundColor: color }}
                onClick={() => chooseInkAppearance(() => setInkColor(color))}
              />
            ))}
          </div>
          <div className="ink-width-row" role="group" aria-label="笔迹粗细" data-brush-tool={inkTool}>
            {brushWidths.map((size, index) => (
              <button key={size} type="button" aria-label={["细", "中", "粗"][index]} aria-pressed={inkWidth === size} data-brush-width={size} disabled={noteMode === "text" || !imageUrl || sourceLoadState !== "ready"} onClick={() => chooseInkAppearance(() => inkTool === "highlighter" ? setHighlighterWidth(size) : setPenWidth(size))}>
                <span style={{ width: `${(inkTool === "highlighter" ? 14 : 6) + index * 4}px`, height: `${(inkTool === "highlighter" ? 4 : 6) + index * 4}px` }} />
              </button>
            ))}
          </div>
          <button className="finger-write-toggle" type="button" aria-label="手指书写" aria-pressed={fingerWrite} disabled={noteMode === "text" || !imageUrl || sourceLoadState !== "ready"} onClick={() => chooseInkAppearance(() => setFingerWrite((value) => !value))}>
            <Hand size={18} /> <span>手指书写</span>
          </button>
        </div>
        <div className="source-reader-note-shortcuts" role="group" aria-label="笔记入口">
          <button className="ink-tool-secondary" type="button" aria-label="文字笔记" aria-pressed={noteMode === "text"} onClick={() => {
            if (noteMode === "text") {
              void flushTextEditor().then((saved) => { if (saved) { setRegionAsk(null); setNoteMode("read"); } });
              return;
            }
            if (annotationActive) void leaveInk().then((saved) => { if (saved) openText(); });
            else openText();
          }}><FileText size={20} /></button>
          <button className="ink-tool-secondary" type="button" aria-label={`本页笔记${pageNotes.length ? ` ${pageNotes.length} 条` : ""}`} aria-expanded={showPageNotes} disabled={noteMode === "text" || pageNotes.length === 0} onClick={() => {
            if (annotationActive) void leaveInk().then((saved) => { if (saved) setShowPageNotes((value) => !value); });
            else setShowPageNotes((value) => !value);
          }}><NotebookPen size={20} />{pageNotes.length > 0 ? <small>{pageNotes.length}</small> : null}</button>
        </div>
      </div>

      <div className="source-reader-workspace" ref={workspaceRef}>

      <figure
        {...pageMotion.attributes}
        className="source-page-frame"
        key={pageMotion.motionKey}
        data-page-direction={pageDirection}
        style={{ "--source-page-enter-x": pageDirection === "back" ? "calc(var(--motion-distance-medium) * -1)" : "var(--motion-distance-medium)" } as CSSProperties}
        tabIndex={0}
        aria-label={`${displayTitle}，${heading.pageLabel}，左右滑动翻页`}
        onPointerDown={(event) => {
          if (annotationActive || event.pointerType !== "touch") return;
          readSwipeRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
        }}
        onPointerUp={(event) => {
          if (annotationActive || event.pointerType !== "touch") return;
          const start = readSwipeRef.current;
          readSwipeRef.current = null;
          if (!start || start.pointerId !== event.pointerId) return;
          if (event.target instanceof Element && event.target.closest(".source-page-text-layer, .source-page-text-document")
            && window.getSelection()?.toString().trim()) return;
          const direction = pageSwipeDirection(event.clientX - start.x, event.clientY - start.y, event.currentTarget.clientWidth);
          if (direction) swipePage(direction);
        }}
        onPointerCancel={() => { readSwipeRef.current = null; }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          if (event.key === "ArrowRight") { event.preventDefault(); swipePage(1); }
          if (event.key === "ArrowLeft") { event.preventDefault(); swipePage(-1); }
        }}
      >
        {showInlineTextFallback ? (
          <article
            className="source-page-text-document citation-quote"
            aria-label={`${heading.pageLabel} 本地教材原文片段`}
            tabIndex={0}
            onPointerUp={(event) => {
              const selection = window.getSelection();
              if (selection?.rangeCount && event.currentTarget.contains(selection.getRangeAt(0).commonAncestorContainer)) {
                setSelectedText(selection.toString().trim().slice(0, 800));
              }
            }}
            onKeyUp={(event) => {
              const selection = window.getSelection();
              if (selection?.rangeCount && event.currentTarget.contains(selection.getRangeAt(0).commonAncestorContainer)) {
                setSelectedText(selection.toString().trim().slice(0, 800));
              }
            }}
          >
            <div className="source-text-document-content">
              {currentInlineCitationText.split(/\n{2,}/u).map((paragraph, index) => (
                <p key={`${index}:${paragraph.slice(0, 32)}`}>{paragraph.trim()}</p>
              ))}
              <SourceTextAnnotationLayer
                activeNoteId={textEditor?.key}
                enabled={!annotationInteractive && !regionAsk}
                notes={pageTextNotes}
                draft={draftTextMarker}
                onDraftActivate={() => textPanelRef.current?.focus()}
                placing={noteMode === "text" && !renderedTextEditor}
                onPlace={(location) => void openTextAt(location)}
                onOpen={(note, location) => void openTextAt(location, note)}
              />
            </div>
          </article>
        ) : (
          <div className="source-page-media">
            <SkeletonReveal
              className="source-page-skeleton-reveal"
              state={sourceLoadState}
              readyKind="content"
              skeleton={(
                <div className="source-page-skeleton" aria-hidden="true">
                  <span className="source-page-skeleton-heading" />
                  <span className="source-page-skeleton-line is-wide" />
                  <span className="source-page-skeleton-line" />
                  <span className="source-page-skeleton-line is-short" />
                </div>
              )}
              error={(
                <div
                  className="source-page-fallback"
                  data-motion-image-source={imageUrl}
                  data-motion-image-state="failed"
                  role="status"
                >
                  <FileText size={34} aria-hidden="true" />
                  <strong>原文页暂不可用</strong>
                  <span>{heading.pageLabel}</span>
                </div>
              )}
            >
              {imageUrl ? (
                <InkAnnotationSurface
                  key={imageKey}
                  active={annotationInteractive}
                  alt={`${displayTitle} ${heading.pageLabel}`}
                  color={inkColor}
                  fingerWrite={fingerWrite}
                  imageRef={imageMotion.imageRef}
                  imageState={imageMotion.state}
                  imageUrl={imageUrl}
                  region={committedRegion}
                  regionMode={regionAskActive}
                  strokes={currentStrokes}
                  tool={inkTool}
                  width={inkWidth}
                  onChange={updateCurrentStrokes}
                  onRegionCommit={commitRegion}
                  sourceText={annotationInteractive ? undefined : currentInlineCitationText}
                  onSelectText={setSelectedText}
                  onPageSwipe={swipePage}
                  pageOverlay={(
                    <SourceTextAnnotationLayer
                      activeNoteId={textEditor?.key}
                      enabled={!annotationInteractive && !regionAsk}
                      notes={pageTextNotes}
                      draft={draftTextMarker}
                      onDraftActivate={() => textPanelRef.current?.focus()}
                      placing={noteMode === "text" && !renderedTextEditor}
                      onPlace={(location) => void openTextAt(location)}
                      onOpen={(note, location) => void openTextAt(location, note)}
                    />
                  )}
                  onLoad={imageMotion.onLoad}
                  onAnimationEnd={imageMotion.settleAnimation}
                  onError={() => {
                    imageMotion.onError();
                    setFailedImageKey(imageKey);
                    showToast(
                      currentInlineCitationText
                        ? "本地教材页图加载失败，已切换到教材原文片段"
                        : "本地教材页图加载失败，请检查发布资源",
                      "warning"
                    );
                  }}
                />
              ) : null}
            </SkeletonReveal>
          </div>
        )}
        <figcaption className="source-page-caption">
          {showInlineTextFallback
            ? `本地教材原文片段 · ${heading.pageLabel}`
            : imageUrl
              ? `本地教材原页 · ${heading.pageLabel}`
              : `原文资源未发布 · ${heading.pageLabel}`}
        </figcaption>
        {selectedText && noteMode === "read" ? (
          <div className="source-reader-selection-action">
            <span>已选 {selectedText.length} 字 · {selectedText.slice(0, 50)}{selectedText.length > 50 ? "…" : ""}</span>
            <Button icon={<NotebookPen size={17} />} onPointerDown={(event) => event.preventDefault()} onClick={() => openText()}>记笔记</Button>
          </div>
        ) : null}
      </figure>

      {regionAskActive && !regionAsk ? (
        <p className="source-region-hint" role="status">
          <Sparkles size={15} aria-hidden="true" />
          用笔圈出区域，松手后弹出 AI 对话
        </p>
      ) : null}

      {noteMode === "text" && !textEditor && !regionAsk ? (
        <p className="source-text-placement-hint" role="status"><MessageSquareText size={15} aria-hidden="true" />点击原文任意位置，写下批注</p>
      ) : null}

      <SourceRegionAiPanel
        bookId={bookId}
        bookTitle={uploadedFile?.name ?? displayTitle}
        chapterId={currentChapter?.chapter_id ?? activeChapterId ?? null}
        chapterTitle={currentChapter?.source_title ?? displayTitle}
        pageLabel={heading.pageLabel}
        reference={regionAsk}
        onClose={closeRegionAsk}
        onClosed={() => setCommittedRegion(null)}
      />

      {renderedTextEditor ? (
          <SourceTextNotePanel
            ref={textPanelRef}
            key={getTextEditorKey(renderedTextEditor)}
            noteId={renderedTextEditor.key}
            anchor={renderedTextEditor.anchor}
            existing={renderedTextEditor.existing}
            location={renderedTextEditor.location}
            workspaceRef={workspaceRef}
            motion={textEditorMotion}
            onAskAi={(note) => {
              const box = renderedTextEditor.location.pageElement.getBoundingClientRect();
              setTextEditor({ ...renderedTextEditor, existing: note, key: note.id, instanceId: ++textEditorInstanceRef.current });
              setRegionAsk({
                kind: "text-note",
                note,
                label: `${heading.pageLabel} · 文字批注`,
                origin: { left: box.left + renderedTextEditor.location.position.x * box.width - 12, top: box.top + renderedTextEditor.location.position.y * box.height - 12, width: 24, height: 24 }
              });
            }}
            onClose={() => {
              setTextEditor(null);
              setSelectedText("");
              setNoteMode("read");
              workspaceRef.current?.querySelector<HTMLElement>(".source-page-frame")?.focus({ preventScroll: true });
            }}
          />
      ) : null}
      {showPageNotes && noteMode === "read" && pageNotes.length > 0 ? (
        <div className="source-page-notes" aria-label="本页已有笔记">
          <strong>本页已有笔记 · {pageNotes.length}</strong>
          {pageNotes.map((note) => (
            <button key={note.id} type="button" onClick={() => {
              setShowPageNotes(false);
              if (note.kind === "ink") openInk(note);
              else if (note.kind === "voice") void openVoice(note);
              else openText(note);
            }}>
              <span>{note.kind === "ink" ? "手写" : note.kind === "voice" ? "语音" : "文字"}</span>
              <span>{note.title}</span>
            </button>
          ))}
        </div>
      ) : null}
      {noteMode !== "text" ? (
        <button className="source-voice-fab" type="button" aria-label="语音笔记" title="语音笔记" onClick={() => void openVoice()}><Mic2 size={24} /></button>
      ) : null}
      </div>
    </div>
  );
}
