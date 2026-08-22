import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { bookcourseApi } from "./api/bookcourseApi";
import { runtimeConfig, setVerifiedUserId } from "./config/runtime";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { BookCourseRepositoryProvider } from "./context/BookCourseRepositoryContext";
import { MotionHistoryProvider } from "./motion/MotionHistoryContext";
import { DevicePreviewStudio } from "./preview/DevicePreviewStudio";
import "./styles/tokens.css";
import "./styles/glass.css";
import "./styles/base.css";
import "./styles/responsive.css";
import "./styles/home.css";
import "./styles/chapter-tools.css";
import "./styles/study.css";
import "./styles/mistake-book.css";
import "./styles/motion.css";
import "./styles/device-preview.css";
import "./styles/card-system.css";
import "./styles/community.css";
import "./styles/upload.css";
import "./styles/parse-ready.css";
import "./styles/processing.css";
import "./styles/chapter-confirm.css";

const searchParams = new URLSearchParams(window.location.search);
const isEmbeddedPreview = searchParams.get("embedded") === "device-preview";
const Root = isEmbeddedPreview ? App : DevicePreviewStudio;
let sessionBootstrapError: string | null = null;

if (import.meta.env.PROD && !runtimeConfig.useDemoRepository) {
  try {
    const sessionResponse = await fetch(`${runtimeConfig.apiBaseUrl}/api/session`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    });
    if (!sessionResponse.ok) {
      sessionBootstrapError = `会话验证失败（${sessionResponse.status}）`;
    } else {
      const session = await sessionResponse.json() as { user_id?: string };
      setVerifiedUserId(session.user_id ?? "");
    }
  } catch {
    sessionBootstrapError = "暂时无法连接身份服务";
  }
}

const applicationRoot = createRoot(document.getElementById("root")!);
if (sessionBootstrapError) {
  const returnTarget = encodeURIComponent(window.location.href);
  applicationRoot.render(
    <main className="screen-loading-state" role="alert" aria-live="assertive">
      <strong>需要重新验证登录</strong>
      <span>{sessionBootstrapError}。为保护同一设备上的个人笔记和学习记录，应用尚未载入。</span>
      <div className="button-row">
        <button className="button" type="button" onClick={() => window.location.reload()}>重试</button>
        <a className="button button-primary" href={`/oauth2/start?rd=${returnTarget}`}>重新登录</a>
      </div>
    </main>
  );
} else {
  const repository = import.meta.env.DEV && runtimeConfig.useDemoRepository
    ? (await import("./services/DemoRepository")).demoRepository
    : bookcourseApi;
  applicationRoot.render(
    <StrictMode>
      <ErrorBoundary>
        <BookCourseRepositoryProvider repository={repository}>
          <MotionHistoryProvider>
            <Root />
          </MotionHistoryProvider>
        </BookCourseRepositoryProvider>
      </ErrorBoundary>
    </StrictMode>
  );
}

if ("serviceWorker" in navigator && import.meta.env.PROD && !runtimeConfig.useDemoRepository) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((error: unknown) => {
      console.warn("Service worker registration failed", error);
    });
  });
}
