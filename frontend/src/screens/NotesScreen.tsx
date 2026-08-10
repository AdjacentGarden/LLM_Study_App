import { useMemo, useState } from "react";
import {
  BookOpen,
  FileDown,
  NotebookPen,
  Upload
} from "lucide-react";
import {
  Button,
  Card
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";

type LiveNote = {
  id: string;
  title: string;
  body: string;
};

export function NotesScreen() {
  const { go, parsedAssets, parsedChunks, uploadedFile } = useAppContext();
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [detailRevision, setDetailRevision] = useState(0);
  const noteItems = useMemo<LiveNote[]>(() => uploadedFile
    ? [
        ...((parsedChunks ?? []).slice(0, 4).map((chunk) => ({
          id: `chunk:${chunk.chunk_id}`,
          title: `RAG 片段：第 ${chunk.page_start}-${chunk.page_end} 页`,
          body: chunk.text.slice(0, 160)
        }))),
        ...((parsedAssets ?? []).slice(0, 2).map((asset) => ({
          id: `asset:${asset.asset_id}`,
          title: `课程插图：${asset.source_type === "extracted" ? "源文件抽取" : "AI 生成"}`,
          body: asset.caption
        })))
      ]
    : [], [parsedAssets, parsedChunks, uploadedFile]);
  const selectedNote = noteItems.find((item) => item.id === selectedNoteId) ?? noteItems[0] ?? null;
  const detailMotion = useLocalMotionItem(
    `notes-detail:${uploadedFile?.bookId ?? "none"}:${selectedNote?.id ?? "empty"}:${detailRevision}`,
    "content",
    { animateInitial: false }
  );

  function selectNote(noteId: string) {
    if (noteId === selectedNote?.id) return;
    setSelectedNoteId(noteId);
    setDetailRevision((current) => current + 1);
  }

  if (!uploadedFile) {
    return (
      <div className="screen-stack notes-screen">
        <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="parse-empty-card">
          <NotebookPen size={34} aria-hidden="true" />
          <h2>暂无真实导学笔记</h2>
          <p>上传教材并完成解析后，chunk 摘要、插图说明和引用片段会汇总到这里。</p>
          <Button icon={<Upload size={18} aria-hidden="true" />} onClick={() => go("upload")}>上传教材</Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="screen-stack notes-screen">
      <div className="notes-workspace">
        <div className="notes-list" aria-label="导学笔记列表">
          {noteItems.length > 0 ? noteItems.map((item) => (
            <button
              className="card note-card note-list-item"
              data-selected={item.id === selectedNote?.id ? "true" : "false"}
              type="button"
              key={item.id}
              aria-pressed={item.id === selectedNote?.id}
              onClick={() => selectNote(item.id)}
            >
              <NotebookPen size={18} aria-hidden="true" />
              <div>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </div>
            </button>
          )) : (
            <Card className="note-card">
              <NotebookPen size={18} aria-hidden="true" />
              <div>
                <h3>暂无可整理内容</h3>
                <p>等待后端返回 chunks 或课程插图后，这里会展示可复习笔记。</p>
              </div>
            </Card>
          )}
        </div>

        <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="book-summary notes-detail-panel">
          <span className="book-summary-icon">
            <NotebookPen size={30} aria-hidden="true" />
          </span>
          <div>
            {selectedNote ? (
              <>
                <h2>{selectedNote.title}</h2>
                <p>{selectedNote.body}</p>
              </>
            ) : (
              <>
                <h2>暂无可选笔记</h2>
                <p>{uploadedFile.name} 已完成解析，等待可用于导学笔记的 chunk 或课程插图。</p>
              </>
            )}
          </div>
        </Card>

        <div className="notes-actions">
          <Button icon={<BookOpen size={18} aria-hidden="true" />} onClick={() => go("flashcards")}>从笔记生成闪卡</Button>
          <Button variant="secondary" icon={<FileDown size={18} aria-hidden="true" />} onClick={() => go("export")}>导出 PDF</Button>
        </div>
      </div>
    </div>
  );
}
