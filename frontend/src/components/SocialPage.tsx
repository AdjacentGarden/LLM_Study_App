import { useEffect, useState } from "react";
import { socialApi } from "../api/social";
import type { SocialMe, SocialContacts, SocialUser } from "../types/social";
import type { BookCatalogItem } from "../types/api";
import { DEFAULT_AVATAR } from "./UserProfilePage";
import { FriendChat } from "./FriendChat";
import { Icon } from "./Icon";

export function SocialPage({
  books,
  onLibraryChanged,
}: {
  books: BookCatalogItem[];
  onLibraryChanged: () => Promise<void>;
}) {
  const [me, setMe] = useState<SocialMe | null>(null),
    [contacts, setContacts] = useState<SocialContacts | null>(null),
    [peer, setPeer] = useState<SocialUser | null>(null);
  const [tab, setTab] = useState<"messages" | "friends" | "search">("messages"),
    [query, setQuery] = useState(""),
    [results, setResults] = useState<SocialUser[]>([]);
  const [error, setError] = useState(""),
    [searchError, setSearchError] = useState(""),
    [working, setWorking] = useState(false),
    [searching, setSearching] = useState(false),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    if (peer) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      if (document.hidden) {
        timer = setTimeout(refresh, 5000);
        return;
      }
      try {
        const identity = await socialApi.me();
        const value = await socialApi.contacts();
        if (cancelled) return;
        setMe(identity);
        setContacts(value);
        setResults((rows) =>
          rows
            .filter(
              (row) => !value.blocked.some((p) => p.user_id === row.user_id),
            )
            .map((row) => ({
              ...row,
              relationship: value.friends.some((p) => p.user_id === row.user_id)
                ? "friend"
                : value.incoming.some((p) => p.user_id === row.user_id)
                  ? "incoming"
                  : value.outgoing.some((p) => p.user_id === row.user_id)
                    ? "outgoing"
                    : "none",
            })),
        );
        setError("");
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
      if (!cancelled) timer = setTimeout(refresh, 5000);
    }
    void refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [revision, peer]);
  useEffect(() => {
    let cancelled = false;
    setResults([]);
    setSearchError("");
    setSearching(false);
    if (tab !== "search" || query.trim().length < 2) return;
    setSearching(true);
    const timer = setTimeout(() => {
      void socialApi
        .search(query.trim())
        .then((r) => {
          if (!cancelled) setResults(r);
        })
        .catch((e) => {
          if (!cancelled) setSearchError(e.message);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, tab, revision]);
  async function visibility() {
    if (!me) return;
    setWorking(true);
    setError("");
    try {
      setMe(await socialApi.visibility(!me.discoverable));
      setRevision((r) => r + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }
  async function act(
    user: SocialUser,
    action: Parameters<typeof socialApi.friend>[1],
  ) {
    setWorking(true);
    setError("");
    try {
      await socialApi.friend(user.user_id, action);
      setContacts(await socialApi.contacts());
      setRevision((r) => r + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }
  function person(
    user: SocialUser,
    actions: React.ReactNode,
    subtitle?: string,
  ) {
    return (
      <article className="social-person" key={user.user_id}>
        <img src={user.avatar_url ?? DEFAULT_AVATAR} alt="" />
        <div className="social-person-copy">
          <strong>{user.nickname}</strong>
          <small>{subtitle ?? user.user_id}</small>
        </div>
        <div className="social-person-actions">{actions}</div>
      </article>
    );
  }
  if (peer)
    return (
      <FriendChat
        key={peer.user_id}
        peer={peer}
        books={books}
        onBack={() => {
          setPeer(null);
          setRevision((r) => r + 1);
        }}
        onLibraryChanged={onLibraryChanged}
      />
    );
  return (
    <div className="social-page">
      <section className="social-intro">
        <span className="kicker">让学习，有来有往</span>
        <h2>遇见一起进步的人</h2>
        <p>交流一个新发现，交换一份好资料。</p>
        {me && (
          <div className="social-my-id">
            <span>我的 ID</span>
            <strong>{me.user_id}</strong>
          </div>
        )}
      </section>
      {me && (
        <section
          className={`social-discovery ${me.discoverable ? "enabled" : ""}`}
        >
          <div>
            <strong>
              {me.discoverable ? "好友可以找到你" : "开启你的学习社交圈"}
            </strong>
            <p>
              {me.discoverable
                ? "可通过昵称或 ID 查找你。关闭后，现有好友仍可聊天。"
                : "开启后，昵称、头像和 ID 可被查找；年龄、简介和学习记录不会公开。"}
            </p>
          </div>
          <button disabled={working} onClick={() => void visibility()}>
            {me.discoverable ? "关闭查找" : "开启好友查找"}
          </button>
        </section>
      )}
      <div className="social-tabs" role="group" aria-label="好友页面切换">
        {(
          [
            ["messages", "消息"],
            ["friends", "好友"],
            ["search", "添加"],
          ] as const
        ).map(([id, label]) => (
          <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>
            {label}
            {id === "messages" &&
              !!contacts?.conversations.some((c) => c.unread > 0) && <i />}
            {id === "friends" && !!contacts?.incoming.length && (
              <b>{contacts.incoming.length}</b>
            )}
          </button>
        ))}
      </div>
      {error && (
        <div className="community-error" role="alert">
          {error}
          <button onClick={() => setRevision((r) => r + 1)}>重试</button>
        </div>
      )}
      {!contacts && !error && <p role="status">正在整理好友消息…</p>}
      {tab === "messages" && contacts && (
        <div className="social-list">
          {contacts.conversations.map((user) => (
            <button
              className="social-conversation"
              key={user.user_id}
              onClick={() => setPeer(user)}
            >
              <img src={user.avatar_url ?? DEFAULT_AVATAR} alt="" />
              <span>
                <strong>{user.nickname}</strong>
                <small>{user.preview}</small>
              </span>
              <em>{user.unread > 0 ? Math.min(user.unread, 99) : "›"}</em>
            </button>
          ))}
          {!contacts.conversations.length && (
            <div className="social-empty">
              <Icon name="community" size={38} />
              <h3>好对话，还没开始</h3>
              <p>添加好友后，就能交换书籍、闪卡和笔记。</p>
              <button
                onClick={() =>
                  setTab(contacts.friends.length ? "friends" : "search")
                }
              >
                {contacts.friends.length ? "找好友聊聊" : "寻找同路人"}
              </button>
            </div>
          )}
        </div>
      )}
      {tab === "friends" && contacts && (
        <div className="social-list">
          {!!contacts.incoming.length && <h3>新的好友申请</h3>}
          {contacts.incoming.map((user) =>
            person(
              user,
              <>
                <button
                  disabled={working}
                  onClick={() => void act(user, "accept")}
                >
                  接受
                </button>
                <button
                  className="muted-action"
                  disabled={working}
                  onClick={() => void act(user, "decline")}
                >
                  婉拒
                </button>
              </>,
            ),
          )}
          <h3>
            我的好友 <small>{contacts.friends.length}</small>
          </h3>
          {contacts.friends.map((user) =>
            person(user, <button onClick={() => setPeer(user)}>聊天</button>),
          )}
          {!contacts.friends.length && (
            <p className="social-empty-copy">
              还没有好友，去「添加」搜搜昵称或 ID 吧。
            </p>
          )}
          {!!contacts.outgoing.length && <h3>等待对方回应</h3>}
          {contacts.outgoing.map((user) =>
            person(
              user,
              <button
                disabled={working}
                onClick={() => void act(user, "cancel")}
              >
                撤回
              </button>,
            ),
          )}
          {!!contacts.blocked.length && (
            <details className="social-blocked">
              <summary>已屏蔽用户（{contacts.blocked.length}）</summary>
              {contacts.blocked.map((user) =>
                person(
                  user,
                  <button
                    disabled={working}
                    onClick={() => void act(user, "unblock")}
                  >
                    解除屏蔽
                  </button>,
                ),
              )}
            </details>
          )}
        </div>
      )}
      {tab === "search" && (
        <div className="social-list">
          <label className="social-search">
            <Icon name="user" size={18} />
            <input
              aria-label="搜索好友"
              placeholder="输入昵称或完整用户 ID"
              value={query}
              maxLength={80}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button aria-label="清空好友搜索" onClick={() => setQuery("")}>
                ×
              </button>
            )}
          </label>
          {searching && <p role="status">正在寻找…</p>}
          {searchError && (
            <p className="community-error" role="alert">
              {searchError}
            </p>
          )}
          {results.map((user) =>
            person(
              user,
              user.relationship === "friend" ? (
                <button onClick={() => setPeer(user)}>聊天</button>
              ) : user.relationship === "incoming" ? (
                <button
                  disabled={working}
                  onClick={() => void act(user, "accept")}
                >
                  接受
                </button>
              ) : user.relationship === "outgoing" ? (
                <span>已申请</span>
              ) : (
                <button
                  disabled={working || !me?.discoverable}
                  onClick={() => void act(user, "request")}
                >
                  加好友
                </button>
              ),
            ),
          )}
          {!searching && !results.length && !searchError && (
            <p className="social-empty-copy">
              {query.trim().length < 2
                ? "输入至少两个字符。昵称相同的用户，可以通过 ID 区分。"
                : "没有找到匹配用户，试试完整 ID；对方需要先开启好友查找。"}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
