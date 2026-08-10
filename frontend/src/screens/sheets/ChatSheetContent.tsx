import { useState } from "react";
import { textbookAssets } from "../../data/mockBook";
import type {
  ApiChapter
} from "../../types/api";
import type {
  AppActions,
  UploadedCourseFile
} from "../../types/app";
import {
  Button,
  CitationCard,
  Pill
} from "../../components/ui";
import { useBookCourseRepository } from "../../context/BookCourseRepositoryContext";
import {
  backendAssetUrl
} from "../shared";

export function ChatSheetContent({
  activeChapterId,
  openSheet,
  parsedChapters,
  uploadedFile
}: Pick<AppActions, "openSheet"> & {
  activeChapterId: string | null;
  parsedChapters: ApiChapter[] | null;
  uploadedFile: UploadedCourseFile | null;
}) {
  const bookcourseRepository = useBookCourseRepository();
  const activeChapter = parsedChapters?.find((chapter) => chapter.chapter_id === activeChapterId) ?? parsedChapters?.[0] ?? null;
  const [question, setQuestion] = useState(activeChapter ? `请解释：${activeChapter.source_title}` : "");
  const [reply, setReply] = useState("请输入问题，后端会基于真实教材片段回答。");
  const [citation, setCitation] = useState<{ page: string; quote: string; image: string; title: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(nextQuestion = question) {
    if (!uploadedFile) {
      setError("请先上传并解析教材，再使用 RAG 问答。");
      return;
    }
    if (!nextQuestion.trim()) {
      setError("请输入要提问的问题。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await bookcourseRepository.queryRag({
        book_id: uploadedFile.bookId,
        chapter_id: activeChapter?.chapter_id ?? null,
        question: nextQuestion
      });
      const firstCitation = result.citations[0];
      const firstAsset = result.related_assets[0];
      setReply(result.answer);
      setCitation(firstCitation ? {
        page: firstCitation.location_label || `第 ${firstCitation.page} 页`,
        quote: firstCitation.quote,
        image: backendAssetUrl(firstAsset?.image_url, textbookAssets.meiosisOne),
        title: firstCitation.chapter_title
      } : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "RAG 检索失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="sheet-body chat-sheet">
      <div className="chat-sheet-transcript">
        <Pill tone="sky">{uploadedFile ? `基于《${uploadedFile.name}》${activeChapter ? "当前章节" : "全书"}回答` : "需要先上传教材"}</Pill>
        <div className="chat-bubble user">{question}</div>
        <div className="chat-bubble ai">{reply}</div>
        {error ? <p className="helper-text">{error}</p> : null}
        {citation ? (
          <CitationCard
            title={citation.title}
            page={citation.page}
            quote={citation.quote}
            image={citation.image}
            onOpen={() => openSheet({ type: "source", title: "AI 回答来源", page: citation.page, image: citation.image })}
          />
        ) : (
          <p className="helper-text">回答后会显示 reranker 选出的教材引用片段。</p>
        )}
        <div className="followups">
          <button type="button" onClick={() => void ask(`请结合原文给一个${activeChapter?.source_title ?? "本节"}的例子`)}>举一个例子</button>
          <button type="button" onClick={() => void ask(`围绕${activeChapter?.source_title ?? "本章"}给我出一道题`)}>给我出一道题</button>
        </div>
      </div>
      <div className="chat-sheet-composer">
        <label className="chat-input">
          <span>继续提问</span>
          <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="围绕本章继续提问..." />
        </label>
        <Button loading={loading} disabled={loading} onClick={() => void ask()}>
          发送问题
        </Button>
      </div>
    </div>
  );
}
