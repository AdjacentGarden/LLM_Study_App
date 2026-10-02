import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { DevicePreview } from "./components/DevicePreview";

const DemoDirectEntry = lazy(() => import("./demo/DemoDirectEntry"));

const query = new URLSearchParams(window.location.search);
const device = query.get("device");
const preview = query.get("embedded") !== "1" &&
  (device === "iphone-16" || device === "iphone-17" || device === "ipad");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {preview ? <DevicePreview device={device} />
      : <Suspense fallback={<div role="status">正在打开学习空间…</div>}><DemoDirectEntry /></Suspense>}
  </StrictMode>,
);
