import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { accountsApi, type AccountState } from "../api/accounts";
import { safeSet, setStorageIdentity } from "./bookContext";
import { ApiError } from "../api/transport";
import { Icon } from "./Icon";
import { RegisterPage } from "./RegisterPage";

const AccountContext = createContext<{
  state: AccountState;
  open: () => void;
  logout: () => Promise<void>;
} | null>(null);
let initial: Promise<AccountState> | null = null;
export function AccountGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AccountState | null>(null),
    [screen, setScreen] = useState(true),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const channel = useRef<BroadcastChannel | null>(null),
    epoch = useRef(0);
  function accept(value: AccountState) {
    setStorageIdentity(value.user_id, !value.account && value.legacy_profile);
    for (const [book, sid] of Object.entries(value.learning_sessions))
      safeSet("zhiwo.active-session:" + book, sid);
    setState(value);
    setScreen(!value.account && !value.legacy_profile);
    setLoading(false);
    setError("");
  }
  async function reload() {
    const ticket = ++epoch.current;
    setState(null);
    setLoading(true);
    try {
      const value = await accountsApi.me();
      if (ticket === epoch.current) accept(value);
    } catch (e) {
      if (ticket !== epoch.current) return;
      if (e instanceof ApiError && e.status === 401) {
        try {
          await accountsApi.logout();
          const value = await accountsApi.me();
          if (ticket === epoch.current) accept(value);
        } catch (failure) {
          if (ticket === epoch.current) {
            setError((failure as Error).message);
            setLoading(false);
          }
        }
      } else {
        setError((e as Error).message);
        setLoading(false);
      }
    }
  }
  useEffect(() => {
    let cancelled = false;
    initial ??= accountsApi.me();
    void initial
      .then((v) => {
        if (!cancelled) accept(v);
      })
      .catch(() => {
        if (!cancelled) void reload();
      });
    try {
      const c = new BroadcastChannel("zhiwo-account");
      channel.current = c;
      c.onmessage = () => void reload();
      return () => {
        cancelled = true;
        c.close();
      };
    } catch {
      return () => {
        cancelled = true;
      };
    }
  }, []);
  async function logout() {
    await accountsApi.logout();
    initial = null;
    channel.current?.postMessage("changed");
    await reload();
  }
  if (loading)
    return (
      <main className="stage">
        <section className="phone auth-phone">
          <div className="auth-loading" role="status">
            正在打开你的学习空间…
          </div>
        </section>
      </main>
    );
  if (!state)
    return (
      <main className="stage">
        <section className="phone auth-phone">
          <div className="auth-loading">
            <p role="alert">{error}</p>
            <button onClick={() => void reload()}>重新连接</button>
          </div>
        </section>
      </main>
    );
  if (screen)
    return (
      <RegisterPage
        state={state}
        onContinue={() => setScreen(false)}
        onSuccess={(value) => {
          initial = Promise.resolve(value);
          accept(value);
          channel.current?.postMessage("changed");
        }}
      />
    );
  return (
    <AccountContext.Provider
      value={{ state, open: () => setScreen(true), logout }}
    >
      <div key={state.user_id} className="account-app">
        {children}
      </div>
    </AccountContext.Provider>
  );
}

export function AccountControls() {
  const context = useContext(AccountContext);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  if (!context) return null;
  return (
    <section className="account-controls">
      <div>
        <Icon name="user" />
        <span>
          <strong>
            {context.state.account ? "邮箱账号" : "让学习记录跟着你"}
          </strong>
          <small>
            {context.state.account?.email ?? "绑定邮箱，下次换设备也能继续"}
          </small>
        </span>
      </div>
      {context.state.account?.is_demo && (
        <p className="auth-demo-label">公开演示账号，请勿存入私人资料</p>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          if (!context.state.account) {
            context.open();
            return;
          }
          setBusy(true);
          setError("");
          void context
            .logout()
            .catch((e) => setError(e.message))
            .finally(() => setBusy(false));
        }}
      >
        {context.state.account ? "退出登录 / 切换账号" : "注册或登录"}
      </button>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

export function DemoFriends() {
  const context = useContext(AccountContext);
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  if (!context?.state.demo_available) return null;
  return (
    <aside className="demo-friends">
      <strong>找几位演示伙伴试一试</strong>
      <p>
        小林、默默和阿辰都是测试账号，不是真人。添加后可互发消息、分享资料。
      </p>
      <button
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void accountsApi
            .demoFriends()
            .then((r) =>
              setNotice(
                r.count
                  ? `已添加 ${r.count} 位演示好友，进入「消息」或「好友」查看。`
                  : "演示账号还在准备中。",
              ),
            )
            .catch((e) => setNotice(e.message))
            .finally(() => setBusy(false));
        }}
      >
        {busy ? "正在添加…" : "添加演示好友"}
      </button>
      {notice && <p role="status">{notice}</p>}
    </aside>
  );
}
