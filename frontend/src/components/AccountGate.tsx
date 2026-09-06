import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  accountsApi,
  type AccountState,
  type CodeChallenge,
} from "../api/accounts";
import {
  safeSet,
  setStorageIdentity,
  savedLearningSessions,
} from "./bookContext";
import { ApiError } from "../api/transport";
import { Icon } from "./Icon";

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
          if(ticket===epoch.current){setError((failure as Error).message);setLoading(false);}
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

function RegisterPage({
  state,
  onContinue,
  onSuccess,
}: {
  state: AccountState;
  onContinue: () => void;
  onSuccess: (value: AccountState) => void;
}) {
  const [mode, setMode] = useState<"register" | "login">("register"),
    [email, setEmail] = useState(""),
    [nickname, setNickname] = useState(""),
    [stage, setStage] = useState("university"),
    [goal, setGoal] = useState("interest"),
    [interests, setInterests] = useState<string[]>([]);
  const [code, setCode] = useState(""),
    [challenge, setChallenge] = useState<CodeChallenge | null>(null),
    [cooldown, setCooldown] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [consent, setConsent] = useState(false);
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(
      () => setCooldown((n) => Math.max(0, n - 1)),
      1000,
    );
    return () => clearTimeout(timer);
  }, [cooldown]);
  function reset() {
    setChallenge(null);
    setCode("");
    setError("");
  }
  async function send() {
    setBusy(true);
    setError("");
    try {
      const result = await accountsApi.code(email.trim(), mode);
      setChallenge(result);
      setCooldown(result.retry_after);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (!challenge) return;
    setBusy(true);
    setError("");
    try {
      const value = await accountsApi.verify(
        challenge.challenge_id,
        code,
        mode === "register" ? { nickname, stage, goal, interests } : undefined,
        mode === "register" ? savedLearningSessions() : [],
      );
      onSuccess(value);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="stage">
      <section className="phone auth-phone">
        <div className="statusbar">
          <span>9:41</span>
          <span>●●● 100%</span>
        </div>
        <div className="auth-scroll">
          <header className="auth-hero">
            <span className="auth-brand">✦ 知我</span>
            <div className="auth-orbit" aria-hidden="true">
              <Icon name="book" size={42} />
              <i>✧</i>
              <b>✦</b>
            </div>
            <h1>
              {mode === "register"
                ? "从认识你开始，\n读懂每一本书。"
                : "欢迎回来，\n继续你的阅读旅程。"}
            </h1>
            <p>
              一本书，无数种起点。
              <br />
              让学习内容，真正适合你。
            </p>
          </header>
          <div className="auth-tabs">
            <button
              type="button"
              aria-pressed={mode === "register"}
              disabled={busy}
              onClick={() => {
                setMode("register");
                reset();
              }}
            >
              注册账号
            </button>
            <button
              type="button"
              aria-pressed={mode === "login"}
              disabled={busy}
              onClick={() => {
                setMode("login");
                reset();
              }}
            >
              邮箱登录
            </button>
          </div>
          <form
            className="auth-form"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <label>
              邮箱
              <input
                aria-label="注册登录邮箱"
                type="email"
                autoComplete="email"
                maxLength={254}
                required
                disabled={busy}
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  reset();
                }}
                placeholder="你的常用邮箱"
              />
            </label>
            <label>
              邮箱验证码
              <div className="auth-code-row">
                <input
                  aria-label="邮箱验证码"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  disabled={busy}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  placeholder="6 位数字"
                />
                <button
                  type="button"
                  disabled={busy || cooldown > 0 || !email.includes("@")}
                  onClick={() => void send()}
                >
                  {cooldown ? `${cooldown} 秒后重发` : "获取验证码"}
                </button>
              </div>
            </label>
            {challenge && (
              <p
                className={
                  challenge.delivery === "demo"
                    ? "auth-demo-code"
                    : "auth-code-note"
                }
                role="status"
              >
                {challenge.delivery === "demo" ? (
                  <>
                    仅供预览的测试验证码：<strong>{challenge.demo_code}</strong>
                    <small>
                      不会发送邮件；任何测试者都可以访问此演示账号。
                    </small>
                  </>
                ) : (
                  <>验证码有效期 10 分钟，请查看收件箱和垃圾邮件。</>
                )}
              </p>
            )}
            {mode === "register" && (
              <>
                <label>
                  昵称
                  <input
                    aria-label="注册昵称"
                    required
                    maxLength={32}
                    autoComplete="nickname"
                    disabled={busy}
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    placeholder="希望大家怎么称呼你？"
                  />
                </label>
                <label>
                  当前学习阶段
                  <select
                    aria-label="学习阶段"
                    value={stage}
                    disabled={busy}
                    onChange={(e) => setStage(e.target.value)}
                  >
                    {[
                      ["middle", "初中"],
                      ["high", "高中"],
                      ["university", "大学"],
                      ["working", "工作中"],
                      ["other", "其他"],
                    ].map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <fieldset disabled={busy}>
                  <legend>
                    感兴趣的方向 <small>可多选，之后也能换书探索</small>
                  </legend>
                  <div className="interest-chips">
                    {[
                      ["science", "自然科学"],
                      ["engineering", "工程技术"],
                      ["humanities", "人文社科"],
                      ["language", "语言学习"],
                      ["business", "经管商业"],
                      ["other", "更多领域"],
                    ].map(([id, label]) => (
                      <button
                        type="button"
                        key={id}
                        aria-pressed={interests.includes(id)}
                        onClick={() =>
                          setInterests((s) =>
                            s.includes(id)
                              ? s.filter((v) => v !== id)
                              : [...s, id],
                          )
                        }
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <label>
                  这次主要想
                  <select
                    aria-label="学习目标"
                    value={goal}
                    disabled={busy}
                    onChange={(e) => setGoal(e.target.value)}
                  >
                    <option value="interest">拓展兴趣，读懂内容</option>
                    <option value="exam">准备考试，掌握重点</option>
                    <option value="work">提升工作或专业能力</option>
                  </select>
                </label>
                <label className="auth-consent">
                  <input
                    type="checkbox"
                    checked={consent}
                    disabled={busy}
                    onChange={(e) => setConsent(e.target.checked)}
                  />
                  <span>
                    我了解：邮箱用于登录验证；学习阶段与兴趣会保存到个人账号，不公开给好友。演示账号不适合保存隐私。
                  </span>
                </label>
              </>
            )}
            {error && (
              <p role="alert" className="community-error">
                {error}
              </p>
            )}
            <button
              className="primary auth-submit"
              disabled={
                busy ||
                !challenge ||
                code.length !== 6 ||
                (mode === "register" && (!nickname.trim() || !consent))
              }
            >
              {busy
                ? "正在处理…"
                : mode === "register"
                  ? "创建我的学习空间"
                  : "登录并继续学习"}
            </button>
          </form>
          {state.demo_available && (
            <details className="auth-demo-options">
              <summary>使用演示账号测试聊天</summary>
              <p>选择账号后获取测试验证码。请不要上传隐私资料。</p>
              {state.demo_users.map((user) => (
                <button
                  key={user.email}
                  disabled={busy}
                  onClick={() => {
                    setMode("login");
                    setEmail(user.email);
                    reset();
                  }}
                >
                  <strong>{user.nickname}</strong>
                  <span>{user.email}</span>
                </button>
              ))}
              <button
                disabled={busy}
                onClick={() => {
                  setMode("register");
                  setEmail("reader@zhiwo.test");
                  setNickname("阅读体验者 · 演示");
                  reset();
                }}
              >
                试注册一个新的演示账号
              </button>
            </details>
          )}
          {!state.email_available && (
            <p className="auth-service-note">
              当前是受控预览，真实邮箱邮件发送尚未开通。
            </p>
          )}
          <button
            className="auth-skip"
            type="button"
            disabled={busy}
            onClick={onContinue}
          >
            {state.legacy_profile ? "返回当前学习空间" : "暂时体验，稍后注册"}
          </button>
          <p className="auth-footer">你的起点不同，学习路径也应该不同。</p>
        </div>
        <div className="home-indicator" />
      </section>
    </main>
  );
}
