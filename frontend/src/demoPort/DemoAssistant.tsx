import { useRef, useState, type Dispatch, type FormEvent, type SetStateAction } from "react";
import { BookOpenCheck, Bot, Loader2, MessageCircle, SendHorizontal, User } from "lucide-react";
import { suggestedQuestions } from "../components/bookContext";
import { RememberQuestion } from "../components/LearningReturn";
import type { BookCatalogItem, BookStructure, QAResult } from "../types/api";

export type AssistantMessage = { role: "user" | "ai"; text: string; question?: string; result?: QAResult };

export function DemoAssistant({ book, structure, busy, error, messages, onMessages, onAsk, onSource, onRemembered }: {
  book: BookCatalogItem | null;
  structure: BookStructure | null;
  busy: boolean;
  error: string;
  messages: AssistantMessage[];
  onMessages: Dispatch<SetStateAction<AssistantMessage[]>>;
  onAsk: (question: string) => Promise<QAResult | null>;
  onSource: (page: number) => void;
  onRemembered: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [failed, setFailed] = useState(false);
  const sending = useRef(false);
  const suggestions = suggestedQuestions(structure).slice(0, 3);

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const question = draft.trim();
    if (!book || !question || busy || sending.current) return;
    sending.current = true;
    setFailed(false);
    onMessages(current => [...current, { role: "user", text: question }]);
    try {
      const result = await onAsk(question).catch(() => null);
      if (result) {
        onMessages(current => [...current, { role: "ai", text: result.answer, question, result }]);
        setDraft("");
      } else {
        onMessages(current => current.slice(0, -1));
        setFailed(true);
      }
    } finally {
      sending.current = false;
    }
  }

  return <>
    <div className="ai-dialog-scroll">
      <div className="ai-intro"><p>下拉查看历史对话</p><h3>{book ? "有什么想弄懂的？" : "从一本教材开始"}</h3>
        <span>{book ? "围绕当前教材提问，回答会标出可核对的原文。" : "先从书架选择教材，再向导学助手提问。"}</span></div>
      {book && <section className="ai-current-book"><div className="ai-current-book-head"><strong>当前教材</strong><span>单书问答 · 原文可核对</span></div>
        <div className="ai-current-book-body"><h3>{book.title}</h3></div></section>}
      {book && messages.length === 0 && suggestions.length > 0 && <section className="ai-suggestions" aria-label="建议问题">
        <p className="ai-suggest-title">你可能感兴趣</p><div className="ai-suggest-list">{suggestions.map(value =>
          <button type="button" key={value} onClick={() => setDraft(value)}><MessageCircle size={15} aria-hidden="true" />{value}</button>)}</div>
      </section>}
      <div className="ai-message-list" aria-live="polite" aria-busy={busy}>
        {messages.map((message, index) => <div className={`ai-message-row ${message.role}`} key={`${index}:${message.role}`}>
          <span className="ai-message-avatar" aria-hidden="true">{message.role === "ai" ? <Bot size={15} /> : <User size={15} />}</span>
          <div className={`ai-message ${message.role}`}>
            {message.role === "ai" && <span className="ai-message-author">AI 导学助手</span>}
            <p>{message.text}</p>
            {message.result?.status === "insufficient" && <div className="ai-message-rag-status" role="status">当前教材未检索到可靠原文{message.result.insufficiency_reason ? `：${message.result.insufficiency_reason}` : "。"}</div>}
            {message.result?.claims.some(claim => claim.citations.length > 0) && <div className="ai-message-citations" aria-label="教材来源">
              <div className="ai-message-citations-head"><BookOpenCheck size={14} aria-hidden="true" /><strong>教材原文依据</strong><span>{message.result.evidence_pages.length} 处</span></div>
              {message.result.claims.flatMap(claim => claim.citations).map((citation, citationIndex) => <article className="ai-message-citation-item" key={`${citation.page_number}:${citationIndex}`}>
                <strong>教材原文</strong><span>PDF 第 {citation.page_number} 页</span><blockquote>{citation.quote}</blockquote>
                <button type="button" aria-label={`查看 PDF 第 ${citation.page_number} 页教材原文`} onClick={() => onSource(citation.page_number)}><BookOpenCheck size={14} aria-hidden="true"/><span>查看教材原文</span></button>
              </article>)}
            </div>}
            {book && message.result && message.question && <RememberQuestion draft={{ book_id: book.book_id, question: message.question,
              chapter_id: structure?.chapters.find(chapter => message.result!.evidence_pages.some(page => page >= chapter.start_page && page <= chapter.end_page))?.chapter_id,
              excerpt: message.result.claims.flatMap(claim => claim.citations.map(citation => citation.quote)).join("\n").slice(0, 3000),
              pages: message.result.evidence_pages.slice(0, 30) }} onSaved={onRemembered} />}
          </div>
        </div>)}
        {busy && <div className="ai-message-row ai" role="status"><span className="ai-message-avatar" aria-hidden="true"><Bot size={15}/></span>
          <div className="ai-message ai"><span className="ai-message-author">AI 导学助手</span><p>正在这本教材中查找依据…</p></div></div>}
        {failed && <div className="ai-message-rag-status" role="alert">{error || "这次没有完成回答，请重试。"}</div>}
      </div>
      {book && messages.length === 0 && <div className="ai-mode-row">{["本节讲解", "举例理解", "随堂测验"].map(value =>
        <button type="button" key={value} onClick={() => setDraft(value)}>{value}</button>)}</div>}
    </div>
    <form className="ai-compose" onSubmit={event => void submit(event)}>
      <input value={draft} aria-label="向 AI 助手提问" maxLength={2000} disabled={busy} onChange={event => setDraft(event.target.value)}
        placeholder={book ? "围绕当前教材提问…" : "请先选择教材"} />
      <button type="submit" aria-label="发送" disabled={!book || busy || !draft.trim()}>{busy ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <SendHorizontal size={18} aria-hidden="true" />}</button>
    </form>
  </>;
}
