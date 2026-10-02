import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { bookcourseApi } from "./api/bookcourseApi";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { BookCourseRepositoryProvider } from "./context/BookCourseRepositoryContext";
import { MotionHistoryProvider } from "./motion/MotionHistoryContext";
import { configureNativeAppShell } from "./platform/nativeApp";
import "./styles/tokens.css";
import "./styles/glass.css";
import "./styles/base.css";
import "./styles/responsive.css";
import "./styles/home.css";
import "./styles/chapter-tools.css";
import "./styles/study-notes.css";
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
import "./styles/course-ready.css";
import "./styles/profile.css";
import "./styles/courses.css";
import "./styles/tablet.css";
import "./styles/typography.css";

const searchParams = new URLSearchParams(window.location.search);
const isPreviewStudio = searchParams.get("preview") === "device-preview";
const DevicePreviewStudio = lazy(async () => {
  const module = await import("./preview/DevicePreviewStudio");
  return { default: module.DevicePreviewStudio };
});

// `/` is now the product entry point. The preview workbench remains available
// to the design/recording workflow at `/?preview=device-preview`, while its
// iframe keeps using `?embedded=device-preview` to render the inner app.
void configureNativeAppShell();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <BookCourseRepositoryProvider repository={bookcourseApi}>
        <MotionHistoryProvider>
          {isPreviewStudio ? (
            <Suspense fallback={<div className="device-preview-loading">正在加载设备预览…</div>}>
              <DevicePreviewStudio />
            </Suspense>
          ) : (
            <App />
          )}
        </MotionHistoryProvider>
      </BookCourseRepositoryProvider>
    </ErrorBoundary>
  </StrictMode>
);
