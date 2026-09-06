import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  BookCatalogItem,
  LibraryResource,
  ShareCandidate,
} from "../types/api";
import type { AttachmentKind, AttachmentSpec } from "../types/social";
import { DetailSheet } from "./DetailSheet";
import { safeGet } from "./bookContext";

export const attachmentLabels: Record<AttachmentKind, string> = {
  book: "书籍",
  flashcards: "闪卡",
  note: "笔记",
  chapter: "章节总结",
  points: "知识点清单",
};
export function FriendSharePicker({
  books,
  onClose,
  onChoose,
}: {
  books: BookCatalogItem[];
  onClose: () => void;
  onChoose: (spec: AttachmentSpec, title: string) => void;
}) {
  const [kind, setKind] = useState<AttachmentKind>("book"),
    [bookId, setBookId] = useState(books[0]?.book_id ?? "");
  const [resources, setResources] = useState<LibraryResource[]>([]),
    [courses, setCourses] = useState<ShareCandidate[]>([]),
    [source, setSource] = useState("");
  const [cards, setCards] = useState<string[]>([]),
    [rights, setRights] = useState(false),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0);
  const sessionId = safeGet(`zhiwo.active-session:${bookId}`);
  useEffect(() => {
    let cancelled = false;
    setRights(false);
    setSource("");
    setCards([]);
    setCourses([]);
    setResources([]);
    setError("");
    setLoading(false);
    if (kind === "book") return;
    setLoading(true);
    void Promise.all([
      api.resources(),
      sessionId && kind !== "note"
        ? api.shareCandidates(sessionId)
        : Promise.resolve([]),
    ])
      .then(([r, c]) => {
        if (cancelled) return;
        setResources(r.filter((x) => x.book_id === bookId));
        setCourses(c);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [bookId, kind, sessionId, revision]);
  const resource = resources.find((r) => `r:${r.id}` === source),
    course = courses.find((c) => `c:${c.course_id}` === source);
  const available =
    !!bookId &&
    (kind === "book" ||
      !!resource ||
      (!!course && (kind !== "flashcards" || cards.length > 0)));
  function choose() {
    if (!available || !rights) return;
    const spec: AttachmentSpec = {
      kind,
      book_id: bookId,
      rights_confirmed: true,
    };
    let title = books.find((b) => b.book_id === bookId)?.title ?? "";
    if (resource) {
      spec.resource_id = resource.id;
      title = resource.title;
    }
    if (course) {
      spec.session_id = sessionId!;
      spec.course_id = course.course_id;
      spec.card_ids = cards;
      title = course.title + " · " + attachmentLabels[kind];
    }
    onChoose(spec, title);
  }
  return (
    <DetailSheet title="分享给好友" onClose={onClose}>
      <div className="community-form">
        <div className="share-kind-grid">
          {(Object.entries(attachmentLabels) as [AttachmentKind, string][]).map(
            ([id, label]) => (
              <button
                key={id}
                aria-pressed={kind === id}
                onClick={() => {
                  setKind(id);
                  setRights(false);
                }}
              >
                {label}
              </button>
            ),
          )}
        </div>
        <label>
          选择教材
          <select
            aria-label="好友分享教材"
            value={bookId}
            onChange={(e) => setBookId(e.target.value)}
          >
            {books.map((b) => (
              <option key={b.book_id} value={b.book_id}>
                {b.title}
              </option>
            ))}
          </select>
        </label>
        {kind === "book" ? (
          <p className="share-book-info">
            好友可直接加入这本书。你的答题记录和学习进度不会一起发送。
          </p>
        ) : loading ? (
          <p role="status">正在整理可分享的内容…</p>
        ) : (
          <>
            <label>
              选择内容
              <select
                aria-label="好友分享内容"
                value={source}
                onChange={(e) => {
                  setSource(e.target.value);
                  setCards(
                    courses
                      .find((c) => `c:${c.course_id}` === e.target.value)
                      ?.cards.map((c) => c.id!) ?? [],
                  );
                }}
              >
                <option value="">请选择…</option>
                {resources
                  .filter((r) => r.kind === kind)
                  .map((r) => (
                    <option key={r.id} value={`r:${r.id}`}>
                      收藏 · {r.title}
                    </option>
                  ))}
                {kind !== "note" &&
                  courses.map((c) => (
                    <option key={c.course_id} value={`c:${c.course_id}`}>
                      课程 · {c.title}
                    </option>
                  ))}
              </select>
            </label>
            {!resources.some((r) => r.kind === kind) &&
              !(kind !== "note" && courses.length) && (
                <p className="community-fineprint">
                  还没有这一类内容。可以先写一篇笔记，或在学习页生成章节课程。
                </p>
              )}
            {kind === "flashcards" && course && (
              <div className="share-card-options">
                {course.cards.map((card) => (
                  <label key={card.id}>
                    <input
                      type="checkbox"
                      checked={cards.includes(card.id!)}
                      onChange={(e) =>
                        setCards(
                          e.target.checked
                            ? [...cards, card.id!]
                            : cards.filter((id) => id !== card.id),
                        )
                      }
                    />
                    <span>{card.front}</span>
                  </label>
                ))}
              </div>
            )}
            {resource?.content.body && (
              <div className="share-note-preview">{resource.content.body}</div>
            )}
          </>
        )}
        {error && (
          <div className="community-error" role="alert">
            {error}
            <button onClick={() => setRevision((r) => r + 1)}>重试</button>
          </div>
        )}
        <label className="rights-confirm">
          <input
            type="checkbox"
            checked={rights}
            onChange={(e) => setRights(e.target.checked)}
          />
          <span>我有权分享这些内容，并确认不包含不想公开的隐私。</span>
        </label>
        <p className="community-fineprint">
          内容只发送到这段好友会话，不会自动发布到社区。发送后，对方可以保存副本。
        </p>
        <button
          className="primary"
          disabled={!available || !rights || loading || !!error}
          onClick={choose}
        >
          添加到消息
        </button>
      </div>
    </DetailSheet>
  );
}
