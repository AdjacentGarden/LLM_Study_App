import { useEffect, useState } from "react";
import { BookOpenCheck, MessageCircleMore, Sparkles } from "lucide-react";
import { Button, Pill } from "./DemoPrimitives";
import { suggestedQuestions } from "../components/bookContext";
import { RememberQuestion } from "../components/LearningReturn";
import type { BookCatalogItem, BookStructure, QAResult } from "../types/api";

export function DemoQa({ book, structure, question, result, busy, error, onAsk, onSource, onRemembered, initialDraft = "" }: {
  book: BookCatalogItem | null; structure: BookStructure | null; question: string; result: QAResult | null; busy: boolean; error: string;
  onAsk: (question: string) => Promise<QAResult | null>; onSource: (page: number) => void; onRemembered: () => void; initialDraft?: string;
}) {
  const [draft, setDraft] = useState(initialDraft);
  useEffect(() => { setDraft(initialDraft); }, [initialDraft]);
  const suggestions = suggestedQuestions(structure);
  const chapter = structure?.chapters.find(item => result?.evidence_pages.some(page => page >= item.start_page && page <= item.end_page));
  async function ask(value = draft) {
    if (!value.trim() || busy) return;
    if (await onAsk(value.trim())) setDraft("");
  }
  return <div className="sheet-body chat-sheet demo-port-chat-page">
    <div className="chat-sheet-transcript">
      <Pill tone="sky">{book ? `当前教材：《${book.title}》` : "请先选择一本教材"}</Pill>
      <Pill tone="purple">单书问答 · 原文可核对</Pill>
      {!question && <div className="chat-bubble ai"><MessageCircleMore size={19}/><span>{book ? "你好！你可以就这本教材提问。有可靠来源时，我会标出对应原文页。" : "选择教材后即可开始答疑。"}</span></div>}
      {question && <div className="chat-bubble user">{question}</div>}
      {busy && <div className="chat-bubble ai" role="status" aria-busy="true"><Sparkles size={16}/> 正在这本教材中查找依据…</div>}
      {error && <div className="helper-text" role="alert">{error} <button type="button" disabled={busy} onClick={() => void ask(question)}>重试提问</button></div>}
      {result && <><div className="chat-bubble ai" aria-live="polite">{result.answer}</div>
        {result.status === "insufficient" && <p className="helper-text">教材证据不足{result.insufficiency_reason ? `：${result.insufficiency_reason}` : "。"}</p>}
        {result.claims.map((claim, index) => <div key={index} className="demo-port-chat-claim"><strong>结论 {index + 1}</strong><p>{claim.text}</p>
          {claim.citations.map((citation, citationIndex) => <article className="citation-card" key={`${citation.page_number}:${citationIndex}`}>
            <span className="citation-icon"><BookOpenCheck size={20}/></span><div><p className="citation-meta">PDF 第 {citation.page_number} 页</p>
              <p className="citation-quote">{citation.quote}</p><button className="inline-link" type="button" onClick={() => onSource(citation.page_number)}>查看教材原文</button></div></article>)}
        </div>)}
        {book && <RememberQuestion draft={{ book_id: book.book_id, question, chapter_id: chapter?.chapter_id, chapter_title: chapter?.title,
          excerpt: result.claims.flatMap(item => item.citations.map(citation => citation.quote)).join("\n").slice(0, 3000), pages: result.evidence_pages.slice(0, 30) }}
          onSaved={onRemembered}/>}
      </>}
      {!question && book && <div className="followups">{suggestions.map(value => <button type="button" key={value} disabled={busy} onClick={() => void ask(value)}>{value}</button>)}</div>}
    </div>
    <form className="chat-sheet-composer" onSubmit={event => { event.preventDefault(); void ask(); }}>
      <label className="chat-input"><span>向当前教材提问</span><input value={draft} maxLength={2000} autoComplete="off" enterKeyHint="send"
        onChange={event => setDraft(event.target.value)} placeholder="围绕本章继续提问…"/></label>
      <Button type="submit" loading={busy} disabled={busy || !book || !draft.trim()}>发送问题</Button>
    </form>
  </div>;
}
