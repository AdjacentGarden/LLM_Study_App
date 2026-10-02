import {
  Suspense,
  createContext,
  lazy,
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

const RegisterPage = lazy(() =>
  import("./RegisterPage").then(module => ({ default: module.RegisterPage })),
);

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
      <Suspense fallback={<main className="stage"><section className="phone auth-phone"><div className="auth-loading" role="status">正在打开…</div></section></main>}>
        <RegisterPage
          state={state}
          onContinue={() => setScreen(false)}
          onSuccess={(value) => {
            initial = Promise.resolve(value);
            accept(value);
            channel.current?.postMessage("changed");
          }}
        />
      </Suspense>
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
            {context.state.account ? "邮箱账号" : "账号"}
          </strong>
          <small>
            {context.state.account?.email ?? "未登录"}
          </small>
        </span>
      </div>
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

/** Current authenticated identity for account-scoped page state. */
export function useAccount() {
  const context = useContext(AccountContext);
  if (!context) throw new Error("AccountGate is required for this page");
  return context;
}
