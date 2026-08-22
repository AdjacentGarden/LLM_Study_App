import { useState } from "react";
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
  backendAssetUrl,
  sourcePageImageUrl
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
  const [question, setQuestion] = useState("");
  const [submittedQuestion, setSubmittedQuestion] = useState<string | null>(null);
  const [reply, setReply] = useState(activeChapter
    ? `你好，我会结合“${activeChapter.source_title}”的教材原文回答，并标出引用位置。`
    : "你好，我会结合教材原文回答，并标出引用位置。");
  const [citation, setCitation] = useState<{ page: string; quote: string; image: string; title: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(nextQuestion = question) {
    if (loading) return;
    if (!uploadedFile) {
      setError("请先上传并解析教材，再使用 RAG 问答。");
      return;
    }
    if (!nextQuestion.trim()) {
      setError("请输入要提问的问题。");
      return;
    }
    const normalizedQuestion = nextQuestion.trim();
    setQuestion(normalizedQuestion);
    setSubmittedQuestion(normalizedQuestion);
    setCitation(null);
    setLoading(true);
    setError(null);
    try {
      const result = await bookcourseRepository.queryRag({
        book_id: uploadedFile.bookId,
        chapter_id: activeChapter?.chapter_id ?? null,
        question: normalizedQuestion
      });
      const firstCitation = result.citations[0];
      const firstAsset = result.related_assets[0];
      setReply(result.answer);
      setCitation(firstCitation ? {
        page: firstCitation.location_label || `第 ${firstCitation.page} 页`,
        quote: firstCitation.quote,
        image: backendAssetUrl(firstAsset?.image_url, sourcePageImageUrl(uploadedFile.bookId, firstCitation.page)),
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
        {submittedQuestion ? <div className="chat-bubble user">{submittedQuestion}</div> : null}
        <div className="chat-bubble ai" aria-live="polite" aria-busy={loading}>
          {loading ? "正在检索当前章节的教材片段…" : reply}
        </div>
        {error ? <p className="helper-text" role="alert">{error}</p> : null}
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
          <button disabled={loading} type="button" onClick={() => void ask(`请结合原文给一个${activeChapter?.source_title ?? "本节"}的例子`)}>举一个例子</button>
          <button disabled={loading} type="button" onClick={() => void ask(`围绕${activeChapter?.source_title ?? "本章"}给我出一道题`)}>给我出一道题</button>
        </div>
      </div>
      <form className="chat-sheet-composer" onSubmit={(event) => {
        event.preventDefault();
        void ask();
      }}>
        <label className="chat-input">
          <span>继续提问</span>
          <input
            value={question}
            autoComplete="off"
            enterKeyHint="send"
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="围绕本章继续提问..."
          />
        </label>
        <Button type="submit" loading={loading} disabled={loading}>
          发送问题
        </Button>
      </form>
    </div>
  );
}
