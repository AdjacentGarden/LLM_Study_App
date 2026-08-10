import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { bookcourseApi } from "./api/bookcourseApi";
import { runtimeConfig } from "./config/runtime";
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
const repository = runtimeConfig.useDemoRepository
  ? (await import("./services/DemoRepository")).demoRepository
  : bookcourseApi;

createRoot(document.getElementById("root")!).render(
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
