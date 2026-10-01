import { useEffect, useRef, useState } from "react";
import type { BookCatalogItem, QAResult } from "../types/api";
import { Icon } from "./Icon";
import { GeneratedText } from "./GeneratedText";

export function TutorChat({
  question,
  askedQuestion,
  result,
  busy,
  error,
  bookTitle,
  books,
  selectedBookId,
  currentBookId,
  onBookChange,
  suggestions,
  available,
  onQuestion,
  onAsk,
  onStop,
  followUp,
}: {
  question: string;
  askedQuestion: string;
  result: QAResult | null;
  busy: boolean;
  error: string;
  bookTitle: string;
  suggestions: string[];
  available: boolean;
  onQuestion: (value: string) => void;
  onAsk: (value?: string) => void;
  onStop: () => void;
  followUp?: React.ReactNode;
  books: BookCatalogItem[];
  selectedBookId: string;
  currentBookId?: string;
  onBookChange: (id: string) => void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [answerExpanded, setAnswerExpanded] = useState(false);
  useEffect(() => {
    scroll.current?.scrollTo({ top: 0, behavior: "instant" });
    setAnswerExpanded(false);
  }, [askedQuestion]);
  return (
    <div className="qa-page tutor-chat">
      <div className="tutor-context">
        <Icon name="book" size={19} />
        <label>

          <select
            aria-label="答疑使用的书籍"
            title={bookTitle}
            disabled={busy || !books.length}
            value={selectedBookId}
            onChange={(event) => {
              setShowSuggestions(false);
              onBookChange(event.target.value);
            }}
          >
            {!books.length && <option value="">请先将书籍加入书架</option>}
            {books.map((item) => (
              <option key={item.book_id} value={item.book_id}>
                {item.title}
                {item.book_id === currentBookId ? " · 正在学习" : ""}
              </option>
            ))}
          </select>
        </label>
        <span className="tutor-select-arrow" aria-hidden="true">
          ⌄
        </span>
      </div>
      <div className="qa-scroll" ref={scroll}>
        {!askedQuestion && (
          <section className="tutor-welcome">
            <span className="tutor-emblem"><Icon name="book" size={32}/></span>
            <h2>哪里还没读懂？</h2>
            <p>从书中找答案</p>
          </section>
        )}
        {askedQuestion && (
          <div className="question-bubble">{askedQuestion}</div>
        )}
        {busy && (
          <div className="thinking-panel" role="status">
            <div>
              <Icon name="spark" />
              <b>正在回答</b>
              <span className="loading-dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
            </div>

            <button className="qa-stop" onClick={onStop}>停止</button>
            <span className="skeleton-line" />
            <span className="skeleton-line" />
            <span className="skeleton-line short" />
          </div>
        )}
        {error && !busy && (
          <section className="qa-error" role="alert">
            <b>暂时无法回答</b>
            <p>{error}</p>
            <button
              disabled={!available}
              onClick={() => onAsk(question.trim() || askedQuestion)}
            >
              重新提问 <Icon name="arrow" size={16} />
            </button>
          </section>
        )}
        {result && (
          <section className="tutor-response">
            <div className="response-label">
              <Icon name="spark" size={18} />
              <b>回答</b>
            </div>
            <div className="qa-answer">
              {result.status === "supported" ? (
                <>
                  {result.claims.slice(0, 1).map((claim) => (
                    <GeneratedText
                      key={claim.text}
                      value={claim.text}
                      className="answer-paragraph answer-lead"
                    />
                  ))}
                  {result.claims.length > 1 && (
                    <div className={`answer-depth ${answerExpanded ? "is-open" : ""}`}>
                      <button
                        className="answer-depth-toggle"
                        aria-expanded={answerExpanded}
                        onClick={() => setAnswerExpanded((value) => !value)}
                      >
                        <span><Icon name="spark" size={15}/>{answerExpanded ? "收起" : "展开详细解释"}</span>
                        <span aria-hidden="true">{answerExpanded ? "−" : "+"}</span>
                      </button>
                      {answerExpanded && <div className="answer-depth-body">
                        {result.claims.slice(1).map((claim, index) => (
                          <GeneratedText
                            key={`${claim.text}-${index}`}
                            value={claim.text}
                            className="answer-paragraph"
                          />
                        ))}
                      </div>}
                    </div>
                  )}
                </>
              ) : (
                <>
                  <p>{result.answer}</p>
                  {result.insufficiency_reason && (
                    <p className="insufficiency-reason">
                      {result.insufficiency_reason}
                    </p>
                  )}
                </>
              )}
            </div>
            <div
              className={`answer-status ${result.status === "supported" ? "supported" : ""}`}
            >
              <Icon
                name={result.status === "supported" ? "check" : "book"}
                size={16}
              />
              <span>
                {result.status === "supported"
                  ? "已附教材依据"
                  : "暂未找到充分依据"}
              </span>
            </div>
            {result.claims.length > 0 && (
              <details className="source-drawer">
                <summary>
                  查看教材依据 <span>{result.claims.length} 条</span>
                </summary>
                <div>
                  {result.claims.map((claim, index) => (
                    <article key={index}>
                      <strong>{claim.text}</strong>
                      {claim.citations.map((citation, i) => (
                        <blockquote key={i}>
                          <span>教材第 {citation.page_number} 页</span>
                          {citation.quote}
                        </blockquote>
                      ))}
                    </article>
                  ))}
                </div>
              </details>
            )}
          </section>
        )}
        {followUp}
      </div>
      <div className="qa-composer">
        <button
          className="suggestion-toggle"
          aria-expanded={showSuggestions}
          onClick={() => setShowSuggestions((value) => !value)}
        >
          <Icon name="spark" size={14} />
          {showSuggestions ? "收起推荐" : "推荐问题"}
          <span>{showSuggestions ? "−" : "+"}</span>
        </button>
        {showSuggestions && (
          <div className="suggestions" aria-label="推荐问题">
            {suggestions.map((item) => (
              <button
                disabled={busy || !available}
                onClick={() => {
                  setShowSuggestions(false);
                  onAsk(item);
                }}
                key={item}
              >
                {item}
                <Icon name="arrow" size={14} />
              </button>
            ))}
          </div>
        )}
        <div className="composer-input">
          <textarea
            aria-label="向教材小助手提问"
            maxLength={2000}
            value={question}
            onChange={(event) => onQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                if (!busy && available && question.trim()) onAsk();
              }
            }}
            placeholder="输入问题"
            rows={2}
          />
          <button
            aria-label="发送问题"
            onClick={() => {
              setShowSuggestions(false);
              onAsk();
            }}
            disabled={busy || !available || !question.trim()}
          >
            {busy ? (
              <span className="button-spinner" />
            ) : (
              <Icon name="send" size={22} />
            )}
          </button>
        </div>
        <small className="composer-note">
          回答仅供学习，重要结论请核对原文
        </small>
      </div>
    </div>
  );
}
