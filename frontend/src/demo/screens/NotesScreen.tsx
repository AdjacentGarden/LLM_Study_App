import { StickerIcon } from "../components/icons/StickerIcon";
import { useEffect, useMemo, useState } from "react";
import { BookOpen, FileDown, NotebookPen, Plus, Sparkles, Upload } from "lucide-react";
import { Button, Card, Pill } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { listStudyNotes } from "../features/studyNotes/repository";
import { noteKindLabel, type NoteKind, type StudyNote } from "../features/studyNotes/types";
import { useLocalMotionItem } from "../motion";
import { sourcePageImageUrl, sourcePageLabel } from "./shared";
import { courseSourceBookIds } from "../features/courses/model";

type AiGuideNote = { id: string; title: string; body: string; label: string };
type NotesView = "mine" | "guide";
type NoteFilter = "all" | NoteKind;

function notePreview(note: StudyNote) {
  if (note.kind === "text") return note.body;
  if (note.kind === "voice") return note.organizedText ?? note.transcript ?? `录音 ${Math.max(1, Math.round(note.durationMs / 1000))} 秒`;
  return note.organizedText ?? note.recognizedText ?? `${Object.values(note.pages).reduce((sum, strokes) => sum + strokes.length, 0)} 条手写笔迹`;
}

function noteIcon(note: StudyNote) {
  if (note.kind === "voice") return <StickerIcon name="Mic2" size={18} aria-hidden="true" />;
  if (note.kind === "ink") return <StickerIcon name="PenLine" size={18} aria-hidden="true" />;
  return <StickerIcon name="FileText" size={18} aria-hidden="true" />;
}

export function NotesScreen() {
  const { activeChapterId, courses, go, openSheet, parsedAssets, parsedChapters, parsedChunks, parsedScanResult, startNote, uploadedFile } = useAppContext();
  const [notes, setNotes] = useState<StudyNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError,setLoadError]=useState("");
  const [view, setView] = useState<NotesView>("mine");
  const [filter, setFilter] = useState<NoteFilter>("all");
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [selectedGuideId, setSelectedGuideId] = useState<string | null>(null);
  const [detailRevision, setDetailRevision] = useState(0);
  const currentChapter = parsedChapters?.find((chapter) => chapter.chapter_id === activeChapterId)
    ?? parsedChapters?.find((chapter) => chapter.level > 1)
    ?? parsedChapters?.[0];

  useEffect(() => {
    let active = true;
    const load = () => {
      void listStudyNotes().then((items) => {
        if (!active) return;
        setNotes(items);
        setLoading(false);
      }).catch(error=>{if(active){setLoadError(error.message);setLoading(false);}});
    };
    load();
    window.addEventListener("bookcourse:study-notes-changed", load);
    return () => {
      active = false;
      window.removeEventListener("bookcourse:study-notes-changed", load);
    };
  }, []);

  const activeCourse = courses.state.courses.find((course) => course.id === courses.state.activeCourseId);
  const courseBookIds = courseSourceBookIds(activeCourse, courses.state.resources);
  const filteredNotes = notes.filter((note) => (filter === "all" || note.kind === filter) && (!activeCourse || !note.anchor || (note.anchor.courseId ? note.anchor.courseId === activeCourse.id : courseBookIds.includes(note.anchor.bookId))));
  const guideNotes = useMemo<AiGuideNote[]>(() => uploadedFile ? [
    ...((parsedChunks ?? []).slice(0, 4).map((chunk) => ({
      id: `chunk:${chunk.chunk_id}`,
      title: `RAG 片段：第 ${chunk.page_start}-${chunk.page_end} 页`,
      body: chunk.text.slice(0, 240),
      label: "教材摘录"
    }))),
    ...((parsedAssets ?? []).slice(0, 2).map((asset) => ({
      id: `asset:${asset.asset_id}`,
      title: `课程插图：${asset.source_type === "extracted" ? "源文件抽取" : "AI 生成"}`,
      body: asset.caption,
      label: "课程插图"
    })))
  ] : [], [parsedAssets, parsedChunks, uploadedFile]);

  const selectedNote = filteredNotes.find((note) => note.id === selectedNoteId) ?? filteredNotes[0] ?? null;
  const selectedGuide = guideNotes.find((note) => note.id === selectedGuideId) ?? guideNotes[0] ?? null;
  const detailKey = view === "mine" ? selectedNote?.id ?? "empty" : selectedGuide?.id ?? "empty";
  const detailMotion = useLocalMotionItem(`notes-detail:${uploadedFile?.bookId ?? "none"}:${detailKey}:${detailRevision}`, "content", { animateInitial: false });

  function selectItem(id: string) {
    if (view === "mine") setSelectedNoteId(id);
    else setSelectedGuideId(id);
    setDetailRevision((current) => current + 1);
  }

  function createContext() {
    if (!uploadedFile) return { anchor: undefined, source: undefined };
    const pageStart = currentChapter?.page_start ?? 1;
    const pageEnd = currentChapter?.page_end ?? pageStart;
    const source = {
      bookId: uploadedFile.bookId,
      title: currentChapter?.source_title ?? uploadedFile.name,
      pageStart,
      pageEnd,
      printedPageStart: currentChapter?.printed_page_start,
      printedPageEnd: currentChapter?.printed_page_end,
      from: "notes" as const
    };
    return {
      source,
      anchor: {
        bookId: uploadedFile.bookId,
        bookTitle: uploadedFile.name,
        chapterId: currentChapter?.chapter_id,
        chapterTitle: currentChapter?.source_title,
        pageStart,
        pageEnd,
        printedPageStart: currentChapter?.printed_page_start ?? undefined,
        printedPageEnd: currentChapter?.printed_page_end ?? undefined
      }
    };
  }

  function openNewNote() {
    const context = createContext();
    const start = Math.max(1, currentChapter?.page_start ?? 1);
    const end = Math.max(start, currentChapter?.page_end ?? parsedScanResult?.page_count ?? start);
    const candidatePages = Array.from({ length: Math.min(40, end - start + 1) }, (_, index) => start + index);
    const pageOptions = uploadedFile ? candidatePages
      .filter((page) => Boolean(sourcePageImageUrl(uploadedFile.bookId, page)))
      .map((page) => ({ page, label: `${currentChapter?.source_title ?? "当前章节"} · PDF 第 ${page} 页` })) : [];
    openSheet({
      type: "noteType",
      intent: { ...context, from: "notes" },
      contextLabel: currentChapter?.source_title ?? uploadedFile?.name,
      inkAvailable: Boolean(context.source && sourcePageImageUrl(context.source.bookId, context.source.pageStart)),
      pageOptions
    });
  }

  function reopenNote(note: StudyNote) {
    const source = note.anchor?.bookId && note.anchor.pageStart ? {
      courseId: note.anchor.courseId,
      bookId: note.anchor.bookId,
      title: note.anchor.chapterTitle ?? note.anchor.bookTitle ?? note.title,
      pageStart: note.anchor.pageStart,
      pageEnd: note.anchor.pageEnd,
      printedPageStart: note.anchor.printedPageStart,
      printedPageEnd: note.anchor.printedPageEnd,
      sourceText: note.anchor.sourceText,
      from: "notes" as const
    } : undefined;
    startNote({ kind: note.kind, anchor: note.anchor, source, existingNoteId: note.id, from: "notes" });
  }

  return (
    <div className="screen-stack notes-screen study-notes-hub">
      <div className="notes-hub-toolbar">
        <div className="notes-view-tabs" role="tablist" aria-label="学习笔记内容">
          <button type="button" role="tab" aria-selected={view === "mine"} onClick={() => setView("mine")}>我的笔记</button>
          <button type="button" role="tab" aria-selected={view === "guide"} onClick={() => setView("guide")}>AI 导学</button>
        </div>
        <Button icon={<Plus size={18} />} onClick={openNewNote}>新建笔记</Button>
      </div>

      {view === "mine" ? (
        <div className="notes-kind-filters" role="group" aria-label="筛选笔记类型">
          {(["all", "text", "ink", "voice"] as const).map((kind) => (
            <button key={kind} type="button" aria-pressed={filter === kind} onClick={() => setFilter(kind)}>
              {kind === "all" ? "全部" : noteKindLabel(kind)}
              <span>{kind === "all" ? notes.length : notes.filter((note) => note.kind === kind).length}</span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="notes-workspace">
        <div className="notes-list" aria-label={view === "mine" ? "我的学习笔记列表" : "AI 导学列表"}>
          {view === "mine" ? (
            loading ? <Card className="note-card"><p>正在载入本地笔记…</p></Card> : filteredNotes.length > 0 ? filteredNotes.map((note) => (
              <button
                className="card note-card note-list-item study-note-list-item"
                data-selected={note.id === selectedNote?.id ? "true" : "false"}
                type="button"
                key={note.id}
                aria-pressed={note.id === selectedNote?.id}
                onClick={() => selectItem(note.id)}
              >
                <span className={`study-note-kind-icon is-${note.kind}`}>{noteIcon(note)}</span>
                <div>
                  <span className="study-note-list-meta"><b>{noteKindLabel(note.kind)}</b>{note.anchor?.pageStart ? ` · ${sourcePageLabel(note.anchor.pageStart, note.anchor.pageEnd)}` : ""}</span>
                  <h3>{note.title}</h3>
                  <p>{notePreview(note)}</p>
                </div>
              </button>
            )) : (
              <Card className="notes-empty-state">
                <span className="notes-ink-wave" aria-hidden="true" />
                <NotebookPen size={30} aria-hidden="true" />
                <h2>{loadError || "把课堂想法留在教材旁"}</h2>
                <p>你可以输入文字、直接批注教材原页，或录下语音后整理。</p>
                <Button icon={<Plus size={18} />} onClick={openNewNote}>新建第一条笔记</Button>
              </Card>
            )
          ) : guideNotes.length > 0 ? guideNotes.map((item) => (
            <button
              className="card note-card note-list-item"
              data-selected={item.id === selectedGuide?.id ? "true" : "false"}
              type="button"
              key={item.id}
              aria-pressed={item.id === selectedGuide?.id}
              onClick={() => selectItem(item.id)}
            >
              <Sparkles size={18} aria-hidden="true" />
              <div><h3>{item.title}</h3><p>{item.body}</p></div>
            </button>
          )) : (
            <Card className="note-card"><p>当前教材还没有可展示的 AI 导学内容。</p></Card>
          )}
        </div>

        <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="notes-detail-panel study-note-detail">
          {view === "mine" ? selectedNote ? (
            <>
              <div className="study-note-detail-heading">
                <span className={`study-note-kind-icon is-${selectedNote.kind}`}>{noteIcon(selectedNote)}</span>
                <div>
                  <Pill tone={selectedNote.kind === "voice" ? "sky" : selectedNote.kind === "ink" ? "purple" : "mint"}>{noteKindLabel(selectedNote.kind)}笔记</Pill>
                  <h2>{selectedNote.title}</h2>
                  <small>{selectedNote.anchor?.chapterTitle ?? selectedNote.anchor?.bookTitle ?? "本地学习笔记"}</small>
                </div>
              </div>
              {selectedNote.anchor?.quote ? <blockquote>{selectedNote.anchor.quote}</blockquote> : null}
              <p className="study-note-original">{selectedNote.kind === "text" ? selectedNote.body : selectedNote.kind === "voice" ? selectedNote.transcript ?? "录音已保存，尚未生成逐字稿。" : selectedNote.recognizedText ?? "手写笔迹已保存，尚未整理。"}</p>
              {selectedNote.organizedText ? (
                <div className="study-note-organized"><strong>独立整理版</strong><p>{selectedNote.organizedText.replace(/^##\s*/u, "").replace(/\n[-\d.\s*]+/gu, " ")}</p></div>
              ) : null}
              {selectedNote.kind !== "text" || selectedNote.anchor?.bookId && selectedNote.anchor.pageStart ? <Button onClick={() => reopenNote(selectedNote)}>{selectedNote.kind === "ink" ? "回到教材批注" : selectedNote.kind === "text" ? "回到原文批注" : "打开语音笔记"}</Button> : null}
            </>
          ) : (
            <div className="notes-empty-detail"><NotebookPen size={30} /><h2>选择一条笔记</h2><p>原始记录和整理版会在这里分别展示。</p></div>
          ) : selectedGuide ? (
            <><Pill tone="sky">{selectedGuide.label}</Pill><h2>{selectedGuide.title}</h2><p>{selectedGuide.body}</p></>
          ) : (
            <div className="notes-empty-detail"><Sparkles size={30} /><h2>暂无 AI 导学内容</h2></div>
          )}
        </Card>

        <div className="notes-actions"><Button variant="secondary" onClick={()=>go("report")}>章节学习报告</Button>
          <Button icon={<BookOpen size={18} aria-hidden="true" />} onClick={() => go("flashcards")}>从笔记生成闪卡</Button>
          <Button variant="secondary" icon={<FileDown size={18} aria-hidden="true" />} onClick={() => go("export")}>导出 PDF</Button>
          {!uploadedFile ? <Button variant="text" icon={<Upload size={18} />} onClick={() => go("upload")}>上传教材</Button> : null}
        </div>
      </div>
    </div>
  );
}
