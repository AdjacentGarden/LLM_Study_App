import { courseRagScope } from "../../features/courses/selectors";
import { useAppContext } from "../../context/AppContext";
import { useEffect, useState } from "react";
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
import { getCitationSourceText, getExtractedCitationSourcePageImage } from "./citationSource";
import { creditCosts, useCredits } from "../../features/credits/creditStore";

type ChatMessage = {
  role: "user" | "assistant";
  text: string;
};

type ChatCitation = {
  bookId: string;
  page: string;
  pdfPage: number;
  quote: string;
  pageText: string;
  image?: string;
  title: string;
};

export function ChatSheetContent({
  activeChapterId,
  openSourcePage,
  parsedChapters,
  uploadedFile
}: Pick<AppActions, "openSourcePage"> & {
  activeChapterId: string | null;
  parsedChapters: ApiChapter[] | null;
  uploadedFile: UploadedCourseFile | null;
}) {
  const bookcourseRepository = useBookCourseRepository();
  const { courses } = useAppContext();
  const currentCourse = courses.state.courses.find((course) => course.id === courses.state.activeCourseId);
  const credits = useCredits();
  const activeChapter = parsedChapters?.find((chapter) => chapter.chapter_id === activeChapterId) ?? parsedChapters?.[0] ?? null;
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [citations, setCitations] = useState<ChatCitation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intro = activeChapter
    ? `你好！可以聊聊，也可以问“${activeChapter.source_title}”。有可靠教材来源时会标出引用位置。`
    : "你好！可以聊天；需要教材依据的问题在有可靠来源时会标出引用位置。";

  useEffect(() => {
    if (uploadedFile?.bookId !== "book_biology_2") return;
  }, [uploadedFile?.bookId]);

  async function ask(nextQuestion = question) {
    if (loading) return;
    if (!uploadedFile) {
      setError("请先上传并解析教材，再使用教材问答。");
      return;
    }
    if (!nextQuestion.trim()) {
      setError("请输入要提问的问题。");
      return;
    }
    let reservationId: string;
    try {
      reservationId = await credits.reserve("chat");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "积分不足，暂时无法提问。");
      return;
    }
    const normalizedQuestion = nextQuestion.trim();
    const history = messages.map((message) => ({
      role: message.role,
      content: message.text
    }));
    setQuestion("");
    setMessages((items) => [...items, { role: "user", text: normalizedQuestion }]);
    setCitations([]);
    setLoading(true);
    setError(null);
    try {
      const result = await bookcourseRepository.queryRag({
        reservation_id: reservationId,
        ...courseRagScope(courses.state, uploadedFile.bookId),
        book_id: uploadedFile.bookId,
        chapter_id: activeChapter?.chapter_id ?? null,
        history,
        question: normalizedQuestion,
        context: {
          book_title: uploadedFile.name,
          chapter_title: activeChapter?.source_title ?? null,
          section_title: activeChapter?.ai_title ?? null,
          page_label: activeChapter
            ? `原书 ${activeChapter.printed_page_start ?? activeChapter.page_start}–${activeChapter.printed_page_end ?? activeChapter.page_end} 页`
            : null,
          key_concepts: []
        }
      });
      if(result.retrieval?.status === "no_match")await credits.refund(reservationId);else await credits.complete(reservationId);
      setMessages((items) => [...items, { role: "assistant", text: result.answer }]);
      setCitations(result.citations.slice(0, 3).map((item) => ({
        bookId: item.book_id ?? String(item.source_metadata.book_id ?? uploadedFile.bookId),
        page: item.location_label || `第 ${item.page} 页`,
        pdfPage: item.page,
        quote: item.quote,
        pageText: getCitationSourceText(item),
        image: getExtractedCitationSourcePageImage(item.chunk_id, result.related_assets),
        title: typeof item.source_metadata.filename === "string" ? `${item.source_metadata.filename} · ${item.chapter_title}` : item.chapter_title
      })));
    } catch (err) {
      await credits.refund(reservationId);
      setError(err instanceof Error ? err.message : "教材资料暂时不可用");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="sheet-body chat-sheet">
      <div className="chat-sheet-transcript">
        <p className="chat-credit-summary" aria-live="polite">剩余 {credits.balance} 积分 · 每次提问消耗 {creditCosts.chat} 积分</p>
        <Pill tone="sky">{currentCourse ? `当前课程：《${currentCourse.name}》` : "请先选择课程"}</Pill>
        <Pill tone="sky">课程资料问答</Pill>
        {messages.length === 0 ? (
          <div className="chat-bubble ai" aria-live="polite">{intro}</div>
        ) : messages.map((message, index) => (
          <div
            className={`chat-bubble ${message.role === "assistant" ? "ai" : "user"}`}
            key={`${message.role}-${index}-${message.text.slice(0, 24)}`}
            aria-live={message.role === "assistant" && index === messages.length - 1 ? "polite" : undefined}
          >
            {message.text}
          </div>
        ))}
        {loading ? <div className="chat-bubble ai" aria-live="polite" aria-busy="true">正在准备回答…</div> : null}
        {error ? <p className="helper-text" role="alert">{error}</p> : null}
        {citations.length > 0 ? citations.map((citation) => (
          <CitationCard
            key={`${citation.pdfPage}:${citation.title}:${citation.quote.slice(0, 24)}`}
            title={citation.title}
            page={citation.page}
            quote={citation.quote}
            image={citation.image}
            openLabel="查看教材原文"
            onOpen={() => openSourcePage({
              courseId: currentCourse?.id,
              bookId: citation.bookId,
              title: citation.title,
              pageStart: citation.pdfPage,
              pageEnd: citation.pdfPage,
              sourceText: citation.pageText
            })}
          />
        )) : (
          <p className="helper-text">有可靠教材来源时会显示对应页码。</p>
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