import { useEffect, useRef, useState } from "react";
import { messageNonce, mergeMessages } from "./socialState";
import { socialApi } from "../api/social";
import type { BookCatalogItem } from "../types/api";
import type {
  SocialUser,
  ChatMessage,
  ChatSubmission,
  AttachmentSpec,
} from "../types/social";
import { DEFAULT_AVATAR } from "./UserProfilePage";
import { FriendSharePicker, attachmentLabels } from "./FriendSharePicker";
import { DetailSheet } from "./DetailSheet";
import { SharedContent } from "./CommunitySharing";
import { Icon } from "./Icon";

const when = (value: number) =>
  new Date(value * 1000).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
export function FriendChat({
  peer,
  books,
  onBack,
  onLibraryChanged,
}: {
  peer: SocialUser;
  books: BookCatalogItem[];
  onBack: () => void;
  onLibraryChanged: () => Promise<void>;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]),
    [text, setText] = useState(""),
    [canChat, setCanChat] = useState(peer.relationship === "friend");
  const [sharing, setSharing] = useState(false),
    [attachment, setAttachment] = useState<{
      spec: AttachmentSpec;
      title: string;
    } | null>(null),
    [preview, setPreview] = useState<ChatMessage | null>(null);
  const [loading, setLoading] = useState(true),
    [older, setOlder] = useState(false),
    [sending, setSending] = useState(false),
    [saving, setSaving] = useState(false),
    [hasOlder, setHasOlder] = useState(false);
  const [failure, setFailure] = useState(""),
    [pollError, setPollError] = useState(""),
    [notice, setNotice] = useState(""),
    [pending, setPending] = useState<ChatSubmission | null>(null),
    [newMessages, setNewMessages] = useState(false);
  const [confirm, setConfirm] = useState<"remove" | "block" | null>(null),
    [managing, setManaging] = useState(false),
    [revision, setRevision] = useState(0);
  const scroll = useRef<HTMLDivElement>(null),
    last = useRef(0),
    nearBottom = useRef(true),
    alive = useRef(true),
    pendingRef = useRef<ChatSubmission | null>(null),
    readCursor = useRef(0);
  function markRead() {
    const cursor = last.current;
    if (!cursor || document.hidden || readCursor.current >= cursor) return;
    readCursor.current = cursor;
    void socialApi.read(peer.user_id, cursor).catch(() => {
      readCursor.current = 0;
    });
  }
  function bottom() {
    scroll.current?.scrollTo({
      top: scroll.current.scrollHeight,
      behavior: "instant",
    });
    nearBottom.current = true;
    setNewMessages(false);
  }
  function received(rows: ChatMessage[], initial = false) {
    if (!rows.length) return;
    last.current = Math.max(last.current, ...rows.map((r) => r.id));
    setMessages((current) => mergeMessages(current, rows));
    if (rows.some((r) => r.client_id === pendingRef.current?.client_id)) {
      pendingRef.current = null;
      setPending(null);
      setText("");
      setAttachment(null);
      setFailure("");
    }
    if (initial || nearBottom.current) {
      requestAnimationFrame(bottom);
      markRead();
    } else setNewMessages(true);
  }
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (cancelled) return;
      if (document.hidden) {
        timer = setTimeout(poll, 3000);
        return;
      }
      try {
        const batch = await socialApi.chat(peer.user_id, last.current);
        if (cancelled) return;
        setCanChat(batch.can_chat);
        setPollError("");
        if (!last.current) setHasOlder(batch.has_more);
        received(batch.messages, !last.current);
        if (nearBottom.current) markRead();
        timer = setTimeout(
          poll,
          batch.has_more && last.current && batch.messages.length === 50
            ? 250
            : 3000,
        );
      } catch (e) {
        if (cancelled) return;
        setPollError((e as Error).message);
        timer = setTimeout(poll, 5000);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void poll();
    return () => {
      cancelled = true;
      alive.current = false;
      clearTimeout(timer);
    };
  }, [peer.user_id, revision]);
  async function send() {
    if (sending || !canChat) return;
    const payload = pending ?? {
      client_id: messageNonce(),
      text: text.trim(),
      attachment: attachment?.spec,
    };
    if (!payload.text && !payload.attachment) return;
    setSending(true);
    setFailure("");
    setPending(payload);
    pendingRef.current = payload;
    try {
      const result = await socialApi.send(peer.user_id, payload);
      if (!alive.current) return;
      nearBottom.current = true;
      received([result]);
    } catch (e) {
      if (alive.current)
        setFailure(
          (e as Error).message + " · 发送结果未确认，重试不会重复发送。",
        );
    } finally {
      if (alive.current) setSending(false);
    }
  }
  async function history() {
    if (older || !messages.length) return;
    setOlder(true);
    const height = scroll.current?.scrollHeight ?? 0;
    try {
      const result = await socialApi.chat(peer.user_id, 0, messages[0].id);
      if (!alive.current) return;
      setHasOlder(result.has_more);
      setMessages((current) => {
        const map = new Map(
          [...result.messages, ...current].map((r) => [r.id, r]),
        );
        return [...map.values()].sort((a, b) => a.id - b.id);
      });
      requestAnimationFrame(() => {
        if (scroll.current)
          scroll.current.scrollTop += scroll.current.scrollHeight - height;
      });
    } catch (e) {
      if (alive.current) setPollError((e as Error).message);
    } finally {
      if (alive.current) setOlder(false);
    }
  }
  async function acquire() {
    if (!preview) return;
    setSaving(true);
    setFailure("");
    try {
      const result = await socialApi.acquire(preview.id);
      await onLibraryChanged();
      if (!alive.current) return;
      setNotice(
        result.status === "already_owned"
          ? "已经收藏过啦，没有重复添加。"
          : "已收藏到书架。",
      );
      setPreview(null);
    } catch (e) {
      if (alive.current) setFailure((e as Error).message);
    } finally {
      if (alive.current) setSaving(false);
    }
  }
  async function manage() {
    if (!confirm) return;
    setManaging(true);
    try {
      await socialApi.friend(peer.user_id, confirm);
      if (alive.current) {
        setCanChat(false);
        setConfirm(null);
        setNotice(
          confirm === "block"
            ? "已屏蔽，对方无法再联系你。"
            : "已解除好友关系，历史消息保留。",
        );
      }
    } catch (e) {
      if (alive.current) setPollError((e as Error).message);
    } finally {
      if (alive.current) setManaging(false);
    }
  }
  return (
    <div className="friend-chat">
      <header className="friend-chat-heading">
        <button aria-label="返回好友列表" onClick={onBack}>
          <Icon name="back" size={18} />
        </button>
        <img src={peer.avatar_url ?? DEFAULT_AVATAR} alt="好友头像" />
        <div>
          <strong>{peer.nickname}</strong>
          <small>{peer.user_id}</small>
        </div>
        <details className="chat-menu">
          <summary aria-label="会话设置">···</summary>
          <div>
            {canChat && (
              <button onClick={() => setConfirm("remove")}>删除好友</button>
            )}
            <button onClick={() => setConfirm("block")}>屏蔽此人</button>
          </div>
        </details>
      </header>
      {pollError && (
        <div className="chat-connection" role="status">
          {pollError}
          <button onClick={() => setRevision((r) => r + 1)}>重新连接</button>
        </div>
      )}
      {notice && (
        <div className="chat-connection" role="status">
          {notice}
          <button aria-label="关闭提示" onClick={() => setNotice("")}>
            ×
          </button>
        </div>
      )}
      <div
        className="friend-chat-history"
        ref={scroll}
        onScroll={() => {
          const e = scroll.current;
          if (!e) return;
          nearBottom.current =
            e.scrollHeight - e.scrollTop - e.clientHeight < 70;
          if (nearBottom.current) {
            setNewMessages(false);
            markRead();
          }
        }}
      >
        {hasOlder && (
          <button
            className="older-messages"
            disabled={older}
            onClick={() => void history()}
          >
            {older ? "正在读取…" : "查看更早消息"}
          </button>
        )}
        {loading && (
          <p className="chat-empty" role="status">
            正在打开会话…
          </p>
        )}
        {!loading && !messages.length && (
          <div className="chat-empty">
            <Icon name="community" size={35} />
            <h3>从一句「你好」开始</h3>
            <p>聊聊正在读的书，分享一个新发现。</p>
          </div>
        )}
        {messages.map((message) => (
          <article
            className={`friend-message ${message.mine ? "mine" : ""}`}
            key={message.id}
          >
            <time>{when(message.created)}</time>
            {message.text && <p>{message.text}</p>}
            {message.attachment && (
              <button
                className="chat-attachment"
                onClick={() => {
                  setFailure("");
                  setPreview(message);
                }}
              >
                {message.attachment.kind === "book" &&
                  message.attachment.cover_url && (
                    <img
                      className="chat-book-cover"
                      src={message.attachment.cover_url}
                      alt="书籍封面"
                      loading="lazy"
                    />
                  )}
                <span>
                  {attachmentLabels[message.attachment.kind]} · 点开查看
                </span>
                <strong>{message.attachment.title}</strong>
                <small>
                  {message.attachment.kind === "book"
                    ? "已解析 · 加入后独立学习"
                    : message.attachment.book_title}
                </small>
                <Icon name="arrow" size={15} />
              </button>
            )}
          </article>
        ))}
      </div>
      {newMessages && (
        <button
          className="new-chat-messages"
          onClick={() => {
            bottom();
            markRead();
          }}
        >
          有新消息 ↓
        </button>
      )}
      <footer className="friend-composer">
        {attachment && (
          <div className="attached-preview">
            <span>附上：{attachment.title}</span>
            <button
              aria-label="移除待发送资料"
              disabled={!!pending || sending}
              onClick={() => setAttachment(null)}
            >
              ×
            </button>
          </div>
        )}
        {failure && !preview && (
          <div className="chat-send-error" role="alert">
            {failure}
            {pending && (
              <button
                onClick={() => {
                  setPending(null);
                  pendingRef.current = null;
                  setFailure("");
                }}
              >
                取消重试，保留草稿
              </button>
            )}
          </div>
        )}
        {!canChat ? (
          <p className="chat-readonly">
            当前无法发送新消息，历史内容仍可查看。
          </p>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <button
              type="button"
              className="chat-share-button"
              aria-label="分享站内内容"
              disabled={!!pending || sending}
              onClick={() => setSharing(true)}
            >
              ＋
            </button>
            <textarea
              aria-label="给好友的消息"
              placeholder="聊聊今天的收获…"
              maxLength={2000}
              rows={2}
              disabled={!!pending || sending}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button
              className="chat-send"
              disabled={sending || (!text.trim() && !attachment && !pending)}
            >
              {sending ? "发送中" : pending ? "重试" : "发送"}
            </button>
          </form>
        )}
      </footer>
      {sharing && (
        <FriendSharePicker
          books={books}
          onClose={() => setSharing(false)}
          onChoose={(spec, title) => {
            setAttachment({ spec, title });
            setSharing(false);
          }}
        />
      )}
      {preview?.attachment && (
        <DetailSheet
          title={attachmentLabels[preview.attachment.kind]}
          onClose={() => {
            if (!saving) setPreview(null);
          }}
        >
          <h3 className="preview-book-title">{preview.attachment.title}</h3>
          {preview.attachment.kind === "book" ? (
            <>
              <div className="book-preview-cover">
                <img
                  src={preview.attachment.cover_url ?? ""}
                  alt={preview.attachment.title}
                />
              </div>
              <p className="community-fineprint">
                加入书架后，按自己的起点开始学习，不会带入好友的学习进度。
              </p>
            </>
          ) : (
            <SharedContent content={preview.attachment.content} />
          )}
          <button
            className="primary sheet-primary"
            disabled={saving}
            onClick={() => void acquire()}
          >
            {saving
              ? "正在收藏…"
              : preview.attachment.kind === "book"
                ? "加入我的书架"
                : "收藏到闪卡与笔记"}
          </button>
          {failure && (
            <p className="community-error" role="alert">
              {failure}
            </p>
          )}
        </DetailSheet>
      )}
      {confirm && (
        <DetailSheet
          title={confirm === "block" ? "屏蔽这位好友？" : "解除好友关系？"}
          onClose={() => {
            if (!managing) setConfirm(null);
          }}
        >
          <p className="preview-summary">
            {confirm === "block"
              ? "对方将无法搜索或联系你；你可以在好友页解除屏蔽。"
              : "解除后将无法发送新消息，需要重新申请成为好友。"}
            历史消息和已收藏的资料不会删除。
          </p>
          <div className="library-actions">
            <button disabled={managing} onClick={() => setConfirm(null)}>
              取消
            </button>
            <button disabled={managing} onClick={() => void manage()}>
              确认{confirm === "block" ? "屏蔽" : "解除"}
            </button>
          </div>
        </DetailSheet>
      )}
    </div>
  );
}
