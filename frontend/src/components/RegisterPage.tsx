import { useEffect, useRef, useState } from "react";
import {
  accountsApi,
  type AccountState,
  type CodeChallenge,
} from "../api/accounts";
import { ApiError } from "../api/transport";
import { savedLearningSessions } from "./bookContext";
import { Icon } from "./Icon";

export function RegisterPage({
  state,
  onContinue,
  onSuccess,
}: {
  state: AccountState;
  onContinue: () => void;
  onSuccess: (value: AccountState) => void;
}) {
  const [mode, setMode] = useState<"register" | "login">("register");
  const [step, setStep] = useState(1),
    [email, setEmail] = useState(""),
    [code, setCode] = useState("");
  const [nickname, setNickname] = useState(""),
    [stage, setStage] = useState("university"),
    [goal, setGoal] = useState("interest"),
    [interests, setInterests] = useState<string[]>([]),
    [consent, setConsent] = useState(false);
  const [challenge, setChallenge] = useState<CodeChallenge | null>(null),
    [retryAt, setRetryAt] = useState(0),
    [expiresAt, setExpiresAt] = useState(0),
    [now, setNow] = useState(Date.now());
  const [action, setAction] = useState<"sending" | "saving" | null>(null),
    [error, setError] = useState(""),
    [emailTouched, setEmailTouched] = useState(false);
  const scroll = useRef<HTMLDivElement>(null),
    codeInput = useRef<HTMLInputElement>(null),
    heading = useRef<HTMLHeadingElement>(null),
    focusCode = useRef(false);
  const busy = action !== null,
    cooldown = Math.max(0, Math.ceil((retryAt - now) / 1000));
  const validEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const expired = !!challenge && now >= expiresAt;
  const canContinue =
    !!challenge && !expired && code.length === 6 && validEmail;
  const showEmail = mode === "login" || step === 1;
  useEffect(() => {
    if (!retryAt && !expiresAt) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [retryAt, expiresAt]);
  useEffect(() => {
    scroll.current?.scrollTo({ top: 0, behavior: "instant" });
    heading.current?.focus({ preventScroll: true });
  }, [step, mode]);
  useEffect(() => {
    if (action === null && showEmail && focusCode.current) {
      focusCode.current = false;
      codeInput.current?.focus();
    }
  }, [action, step, mode, challenge, showEmail]);
  function reset() {
    focusCode.current = false;
    setChallenge(null);
    setCode("");
    setExpiresAt(0);
    setError("");
  }
  function changeMode(value: "register" | "login") {
    if (value === mode) return;
    setMode(value);
    setStep(1);
    reset();
  }
  async function send() {
    if (busy || !validEmail || cooldown) return;
    setAction("sending");
    setError("");
    setChallenge(null);
    setCode("");
    try {
      const result = await accountsApi.code(email.trim(), mode);
      if (result.delivery !== "email") {
        setError("邮箱验证服务暂时不可用，请稍后再试。");
        return;
      }
      const t = Date.now();
      setNow(t);
      setChallenge(result);
      setCode("");
      setRetryAt(t + result.retry_after * 1000);
      setExpiresAt(t + result.expires_in * 1000);
      focusCode.current = true;
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof ApiError && e.status === 429) {
        setNow(Date.now());
        setRetryAt(Date.now() + 60000);
      }
    } finally {
      setAction(null);
    }
  }
  async function submit() {
    if (busy || !canContinue) return;
    if (mode === "register" && step === 1) {
      setStep(2);
      setError("");
      return;
    }
    if (mode === "register" && (!nickname.trim() || !consent)) return;
    setAction("saving");
    setError("");
    try {
      onSuccess(
        await accountsApi.verify(
          challenge!.challenge_id,
          code,
          mode === "register"
            ? { nickname: nickname.trim(), stage, goal, interests }
            : undefined,
          mode === "register" ? savedLearningSessions() : [],
        ),
      );
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof ApiError && [400, 403, 409].includes(e.status)) {
        focusCode.current = true;
        setStep(1);
      }
    } finally {
      setAction(null);
    }
  }
  return (
    <main className="stage">
      <section
        className={`phone auth-phone${!showEmail ? " auth-preferences" : ""}`}
        aria-label="云径注册与登录"
      >
        <div className="statusbar">
          <span>9:41</span>
          <span>●●● 100%</span>
        </div>
        <div className="auth-scroll" ref={scroll}>
          <div className="auth-brand-row">
            <span className="auth-brand">
              <Icon name="book" size={22} />
              云径<span>CloudPath</span>
            </span>
            <span className="auth-brand-note">个性化教材学习</span>
          </div>
          <header className="auth-hero">
            <div className="auth-art" aria-hidden="true">
              <Icon name={step === 2 ? "user" : "book"} size={37} />
              <i />
              <b>
                <Icon name="spark" size={17} />
              </b>
            </div>
            <span className="auth-eyebrow">
              {mode === "login"
                ? "ACCOUNT"
                : step === 1
                  ? "CREATE ACCOUNT"
                  : "LEARNING PROFILE"}
            </span>
            <h1 tabIndex={-1} ref={heading}>
              {mode === "login"
                ? "登录"
                : step === 1
                  ? "注册账号"
                  : "学习偏好"}
            </h1>
            <p>
              {mode === "login"
                ? "使用邮箱验证码登录。"
                : step === 1
                  ? "填写邮箱和昵称以创建账号。"
                  : "用于调整内容难度与学习重点。"}
            </p>
          </header>
          {showEmail && (
            <div className="auth-tabs" role="group" aria-label="注册登录切换">
              <button
                type="button"
                disabled={busy}
                aria-pressed={mode === "register"}
                onClick={() => changeMode("register")}
              >
                注册账号
              </button>
              <button
                type="button"
                disabled={busy}
                aria-pressed={mode === "login"}
                onClick={() => changeMode("login")}
              >
                邮箱登录
              </button>
            </div>
          )}
          {mode === "register" && (
            <ol className="auth-progress" aria-label="注册步骤">
              <li aria-current={step === 1 ? "step" : undefined}>
                <span>01</span>填写账号
              </li>
              <li aria-current={step === 2 ? "step" : undefined}>
                <span>02</span>学习偏好
              </li>
            </ol>
          )}
          <form
            id="account-form"
            className="auth-form"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <div key={`${mode}-${step}`} className="auth-fields">
              {showEmail ? (
                <>
                  <label>
                    常用邮箱
                    <input
                      aria-label="注册登录邮箱"
                      aria-invalid={(emailTouched && !validEmail) || undefined}
                      aria-describedby={
                        emailTouched && !validEmail
                          ? "email-format-error"
                          : undefined
                      }
                      type="email"
                      autoComplete="email"
                      autoCapitalize="none"
                      spellCheck={false}
                      required
                      maxLength={254}
                      disabled={busy}
                      value={email}
                      onBlur={() => setEmailTouched(!!email)}
                      onChange={(e) => {
                        setEmail(e.target.value);
                        reset();
                      }}
                      placeholder="name@example.com"
                    />
                  </label>
                  {emailTouched && !validEmail && (
                    <p id="email-format-error" className="auth-field-error">
                      请填写完整邮箱，例如 name@example.com
                    </p>
                  )}
                  <label>
                    邮箱验证码
                    <div className="auth-code-row">
                      <input
                        ref={codeInput}
                        aria-label="邮箱验证码"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        pattern="[0-9]{6}"
                        maxLength={6}
                        disabled={busy}
                        value={code}
                        onChange={(e) =>
                          setCode(e.target.value.replace(/\D/g, ""))
                        }
                        placeholder="输入 6 位验证码"
                      />
                      <button
                        type="button"
                        disabled={busy || cooldown > 0 || !validEmail}
                        onClick={() => void send()}
                      >
                        {action === "sending"
                          ? "正在发送…"
                          : cooldown
                            ? `${cooldown} 秒后重发`
                            : "获取验证码"}
                      </button>
                    </div>
                  </label>
                  {challenge && (
                    <div className="auth-code-note" role="status">
                      <b>请查收邮箱验证码</b>
                      <span>
                        {challenge.message.replace(/[。！!]+$/, "")}。有效期{" "}
                        {Math.ceil(challenge.expires_in / 60)}
                        分钟，也可以查看垃圾邮件。
                      </span>
                    </div>
                  )}
                  {!challenge && (
                    <div className="auth-reassurance">
                      <Icon name="check" size={17} />
                      <p>
                        无需设置密码
                        <br />
                        <span>邮箱仅用于登录验证，不向好友公开。</span>
                      </p>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <label>
                    怎么称呼你
                    <input
                      aria-label="注册昵称"
                      required
                      maxLength={32}
                      autoComplete="nickname"
                      disabled={busy}
                      value={nickname}
                      onChange={(e) => setNickname(e.target.value)}
                      placeholder="给自己取一个昵称"
                    />
                  </label>
                  <div className="auth-profile-row">
                    <label>
                      学习阶段
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
                    <label>
                      学习目标
                      <select
                        aria-label="学习目标"
                        value={goal}
                        disabled={busy}
                        onChange={(e) => setGoal(e.target.value)}
                      >
                        <option value="interest">拓展兴趣</option>
                        <option value="exam">准备考试</option>
                        <option value="work">提升专业能力</option>
                      </select>
                    </label>
                  </div>
                  <fieldset disabled={busy}>
                    <legend>
                      感兴趣的方向 <small>可多选，也可以暂时不选</small>
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
                          {interests.includes(id) && (
                            <Icon name="check" size={13} />
                          )}
                          <span>{label}</span>
                        </button>
                      ))}
                    </div>
                  </fieldset>
                  <label className="auth-consent">
                    <input
                      type="checkbox"
                      checked={consent}
                      disabled={busy}
                      onChange={(e) => setConsent(e.target.checked)}
                    />
                    <span>
                      我了解：邮箱用于登录；学习阶段与兴趣保存在账号中，不向好友公开。
                    </span>
                  </label>
                </>
              )}
            </div>
          </form>
          {!state.email_available && (
            <p className="auth-service-note">
              邮箱验证服务暂时不可用，请稍后再试。
            </p>
          )}
        </div>
        <footer className="auth-bottom">
          {expired && (
            <p role="alert" className="auth-field-error">
              验证码已过期，请
              {step === 2 && (
                <button type="button" onClick={() => setStep(1)}>
                  返回上一步
                </button>
              )}
              重新获取。
            </p>
          )}
          {error && (
            <p role="alert" className="auth-error">
              <Icon name="spark" size={17} />
              <span>{error}</span>
            </p>
          )}
          <div className="auth-bottom-actions">
            {mode === "register" && step === 2 && (
              <button
                type="button"
                className="auth-back"
                disabled={busy}
                onClick={() => {
                  focusCode.current = true;
                  setStep(1);
                  setError("");
                }}
                aria-label="返回邮箱信息"
              >
                <Icon name="back" />
              </button>
            )}
            <button
              form="account-form"
              type="submit"
              className="primary auth-submit"
              disabled={
                busy ||
                !canContinue ||
                (mode === "register" &&
                  step === 2 &&
                  (!nickname.trim() || !consent))
              }
            >
              {action === "saving" ? (
                <>
                  <span className="button-spinner" />
                  正在进入…
                </>
              ) : mode === "login" ? (
                "登录并继续学习"
              ) : step === 1 ? (
                <>
                  下一步 · 学习偏好
                  <Icon name="arrow" size={18} />
                </>
              ) : (
                "创建我的学习空间"
              )}
            </button>
          </div>
          <button
            className="auth-skip"
            type="button"
            disabled={busy}
            onClick={onContinue}
          >
            {state.legacy_profile ? "返回学习空间" : "暂不登录"}
          </button>
        </footer>
        <div className="home-indicator" />
      </section>
    </main>
  );
}
