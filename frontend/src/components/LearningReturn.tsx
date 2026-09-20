import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import {
  retention,
  type Doubt,
  type DoubtDraft,
  type MemorySnapshot,
  type MemoryItem,
} from "../api/retention";
import type { CourseActivity } from "../types/api";
import { Icon } from "./Icon";
import { FlashcardDeck } from "./FlashcardDeck";

const date = (value: string | number) =>
  new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" }).format(
    new Date(typeof value === "number" ? value * 1000 : value),
  );

export function MemoryInvitation() {
  return (
    <section className="return-panel memory-return" aria-label="记忆回访介绍">
      <div className="return-title">
        <span className="return-symbol">
          <Icon name="clock" size={21} />
        </span>
        <div>
          <span className="kicker">不只今天记得</span>
          <h3>学过的，我们一起再回忆。</h3>
        </div>
      </div>
      <p className="return-description">
        学完第一组闪卡，留下你的回忆感受。之后回到这里，就能看到适合你的复习安排。
      </p>
    </section>
  );
}

export function RememberQuestion({
  draft,
  onSaved,
  label = "还没懂，记下来",
}: {
  draft: DoubtDraft;
  onSaved: () => void;
  label?: string;
}) {
  const [busy, setBusy] = useState(false),
    [saved, setSaved] = useState(false),
    [error, setError] = useState("");
  const lock = useRef(false);
  useEffect(() => {
    setSaved(false);
    setError("");
  }, [draft.book_id, draft.question]);
  return (
    <div className="remember-question">
      <button
        className="return-link"
        disabled={busy || saved}
        onClick={async () => {
          if (lock.current) return;
          lock.current = true;
          setBusy(true);
          setError("");
          try {
            await retention.save(draft);
            setSaved(true);
            onSaved();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            lock.current = false;
            setBusy(false);
          }
        }}
      >
        <Icon name={saved ? "check" : "book"} size={16} />
        {busy ? "正在记下…" : saved ? "已放入待解疑问" : label}
      </button>
      {saved && <small role="status">回到相关章节或到期时，再一起看看。</small>}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

export function DoubtReturn({
  bookId,
  chapterId,
  revision,
  onAsk,
}: {
  bookId: string;
  chapterId?: string;
  revision: number;
  onAsk: (doubt: Doubt) => void;
}) {
  const [items, setItems] = useState<Doubt[]>([]),
    [now, setNow] = useState(0),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [reload, setReload] = useState(0),
    [show, setShow] = useState(false),
    [message, setMessage] = useState("");
  const lock = useRef(false);
  useEffect(() => {
    let alive = true;
    setError("");
    void retention
      .doubts(bookId)
      .then((r) => {
        if (alive) {
          setItems(r.items);
          setNow(r.now);
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [bookId, revision, reload]);
  const open = items.filter((d) => d.status === "open");
  // Snoozing suppresses even chapter hints until due; never surface unrelated chapters.
  const related = open.filter((d) =>
    chapterId
      ? d.chapter_id === chapterId &&
        (d.due_at <= now || d.updated === d.created)
      : d.due_at <= now,
  );
  async function update(
    d: Doubt,
    action: "resolve" | "reopen" | "snooze",
    note = "",
  ) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const next = await retention.update(d.id, action, note);
      setItems((s) => s.map((v) => (v.id === next.id ? next : v)));
      setNow(Date.now() / 1000);
      setMessage(
        action === "snooze"
          ? "已延后 7 天，这段时间不再提示。"
          : action === "resolve"
            ? "这次理解已保存。"
            : "已重新放回待解疑问。",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  if (!items.length && !error) return null;
  return (
    <section className="return-panel doubt-return" aria-label="阅读疑问回访">
      {busy && (
        <p role="status" className="return-description">
          正在保存…
        </p>
      )}
      {!busy && message && (
        <p role="status" className="return-success">
          {message}
        </p>
      )}
      <div className="return-title">
        <span className="return-symbol">
          <Icon name="book" size={20} />
        </span>
        <div>
          <span className="kicker">让疑问，慢慢有答案</span>
          <h3>
            {related.length
              ? chapterId
                ? "这里有你之前的疑问"
                : "有个问题，值得再看看"
              : "你的待解疑问"}
          </h3>
        </div>
        <span className="return-count">{open.length}</span>
      </div>
      {!show &&
        related.slice(0, 1).map((d) => (
          <button
            className="doubt-preview"
            disabled={busy}
            key={d.id}
            onClick={() => onAsk(d)}
          >
            <span>{d.question}</span>
            <Icon name="arrow" size={17} />
          </button>
        ))}
      <button
        className="return-link"
        aria-expanded={show}
        onClick={() => setShow((s) => !s)}
      >
        {show ? "收起疑问" : `查看疑问与理解记录 · ${items.length}`}
        <Icon name="arrow" size={15} />
      </button>
      {show && (
        <div className="doubt-list">
          {items.map((d) => (
            <DoubtRow
              key={d.id}
              doubt={d}
              busy={busy}
              onAsk={() => onAsk(d)}
              onUpdate={(a, n) => update(d, a, n)}
            />
          ))}
        </div>
      )}
      {error && (
        <p role="alert">
          {error}
          <button onClick={() => setReload((v) => v + 1)}>重新加载</button>
        </p>
      )}
    </section>
  );
}
function DoubtRow({
  doubt: d,
  busy,
  onAsk,
  onUpdate,
}: {
  doubt: Doubt;
  busy: boolean;
  onAsk: () => void;
  onUpdate: (a: "resolve" | "reopen" | "snooze", note?: string) => void;
}) {
  const [note, setNote] = useState(d.note);
  return (
    <article className={`doubt-row ${d.status}`}>
      <small>
        {d.status === "resolved"
          ? "已理解 · 自己确认"
          : `下次回访 ${date(d.due_at)}`}
        {d.chapter_title && ` · ${d.chapter_title}`}
      </small>
      <h4>{d.question}</h4>
      {d.excerpt && (
        <details>
          <summary>
            当时的阅读线索
            {d.pages?.length ? ` · 第 ${d.pages.join("、")} 页` : ""}
          </summary>
          <blockquote>{d.excerpt}</blockquote>
        </details>
      )}
      {d.status === "resolved" ? (
        <>
          <p>{d.note || "这次理解已记录。以后仍可以回来继续问。"}</p>
          <button
            className="return-link"
            disabled={busy}
            onClick={() => onUpdate("reopen")}
          >
            还想再想想
          </button>
        </>
      ) : (
        <>
          <div className="doubt-actions">
            <button disabled={busy} onClick={onAsk}>
              继续问
            </button>
            <button disabled={busy} onClick={() => onUpdate("snooze")}>
              7 天后再看
            </button>
          </div>
          <details className="doubt-resolve">
            <summary>我已经理解了</summary>
            <textarea
              aria-label="我的理解（选填）"
              maxLength={2000}
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="留一句自己的理解，也可以不填"
            />
            <button disabled={busy} onClick={() => onUpdate("resolve", note)}>
              确认已理解
            </button>
          </details>
        </>
      )}
    </article>
  );
}

export function MemoryReturn({
  sessionId,
  revision,
  onReviewed,
}: {
  sessionId: string;
  revision: number;
  onReviewed: (a: CourseActivity) => void;
}) {
  const [data, setData] = useState<MemorySnapshot | null>(null),
    [error, setError] = useState(""),
    [reload, setReload] = useState(0),
    [active, setActive] = useState<MemoryItem | null>(null),
    [flipped, setFlipped] = useState(false),
    [busy, setBusy] = useState(false),
    [activity, setActivity] = useState<CourseActivity | null>(null),
    [history, setHistory] = useState(false);
  const started = useRef(Date.now()),
    lock = useRef(false),
    nonce = useRef("");
  const mounted = useRef(true);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (active)
      panel.current?.scrollIntoView({ block: "start", behavior: "instant" });
  }, [active?.card.card_id]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    let alive = true;
    setError("");
    void retention
      .memory(sessionId)
      .then((r) => {
        if (alive) setData(r);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [sessionId, revision, reload]);
  function start(item: MemoryItem) {
    setActive(item);
    setFlipped(false);
    setActivity(null);
    started.current = Date.now();
    nonce.current =
      globalThis.crypto?.randomUUID?.() ??
      `return-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  async function rate(rating: "again" | "hard" | "good" | "easy") {
    if (!active || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api.reviewFlashcard(
        sessionId,
        active.course_id,
        active.card.card_id,
        {
          event_id: nonce.current,
          rating,
          response_seconds: Math.max(1, (Date.now() - started.current) / 1000),
        },
      );
      if (!mounted.current) return;
      setActivity(result);
      setActive(null);
      onReviewed(result);
      setReload((x) => x + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      lock.current = false;
    }
  }
  return (
    <section
      ref={panel}
      className="return-panel memory-return"
      aria-label="长期记忆回访"
    >
      <div className="return-title">
        <span className="return-symbol">
          <Icon name="clock" size={21} />
        </span>
        <div>
          <span className="kicker">不只今天记得</span>
          <h3>
            {data?.due_count
              ? `${data.due_count} 张卡，今天再想一遍`
              : "把学过的，留在记忆里"}
          </h3>
        </div>
      </div>
      {!data && !error && <p role="status">正在整理复习安排…</p>}
      {data && (
        <>
          <p className="return-description">
            {data.reviewed_count
              ? data.due_count
                ? "先不看答案，试着回忆。几分钟就好。"
                : `今天没有到期卡片，下次是 ${date(data.items[0].due_at)}。`
              : "在章节闪卡中记录一次回忆感受，这里就会安排后续复习。"}
          </p>
          {!active && data.due_count > 0 && (
            <button
              className="return-primary"
              onClick={() => start(data.items.find((i) => i.due)!)}
            >
              开始今天的回忆 <Icon name="arrow" size={17} />
            </button>
          )}
          {active && (
            <div className="memory-session">
              <div className="memory-context">
                <span>{active.chapter_title}</span>
                <button disabled={busy} onClick={() => setActive(null)}>
                  稍后继续
                </button>
              </div>
              <FlashcardDeck
                cards={[active.card]}
                index={0}
                flipped={flipped}
                busy={busy}
                activity={null}
                onFlip={() => setFlipped((f) => !f)}
                onCard={() => {}}
                onRate={rate}
              />
            </div>
          )}
          {activity?.review_state && !active && (
            <p className="return-success" role="status">
              已记录这次回忆，下次安排在 {date(activity.review_state.due_at)}。
            </p>
          )}
          {data.reviewed_count > 0 && (
            <>
              <div className="memory-facts">
                <span>
                  <b>{data.delayed_count}</b>隔日回访
                </span>
                <span>
                  <b>
                    {data.week_checks
                      ? `${data.week_recalled}/${data.week_checks}`
                      : "—"}
                  </b>
                  间隔至少七天后自评记得
                </span>
              </div>
              <small className="memory-disclaimer">
                依据你的回忆自评安排，不等同于测验验证；间隔会随表现调整。
              </small>
              <button
                className="return-link"
                aria-expanded={history}
                onClick={() => setHistory((h) => !h)}
              >
                {history ? "收起回忆记录" : "查看回忆记录与安排"}
              </button>
              {history && (
                <div className="memory-history">
                  {data.items.map((i) => (
                    <div key={i.card.card_id}>
                      <b>{i.card.front}</b>
                      <small>
                        {i.due ? "待复习" : `下次 ${date(i.due_at)}`} · 已回忆{" "}
                        {i.repetitions} 次
                      </small>
                    </div>
                  ))}
                  {data.history
                    .slice(-10)
                    .reverse()
                    .map((h, i) => (
                      <p key={i}>
                        {date(h.at)} ·{" "}
                        {h.delayed ? `间隔 ${h.gap_days} 天` : "首次或当天回忆"}{" "}
                        ·{" "}
                        {
                          {
                            again: "还没记住",
                            hard: "需要提示",
                            good: "基本记得",
                            easy: "轻松想起",
                          }[h.rating]
                        }
                      </p>
                    ))}
                </div>
              )}
            </>
          )}
        </>
      )}
      {error && (
        <p role="alert">
          {error}
          <button onClick={() => setReload((v) => v + 1)}>重新加载</button>
        </p>
      )}
    </section>
  );
}
