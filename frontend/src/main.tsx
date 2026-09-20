import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { PhoneSimulator } from "./components/PhoneSimulator";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { AccountGate } from "./components/AccountGate";
import "./styles/inherited-tokens.css";
import "./styles/app.css";
import "./styles/learning-motion.css";
import "./styles/studio.css";
import "./styles/refinement.css";
import "./styles/library-profile.css";
import "./styles/device-preview.css";
import "./styles/chapter-map.css";
import "./styles/community.css";
import "./styles/user-profile.css";
import "./styles/social.css";
import "./styles/accounts.css";
import "./styles/learning-return.css";
import "./styles/learning-studio.css";
import "./styles/calm-redesign.css";

const query = new URLSearchParams(window.location.search);
const embedded = query.get("embedded") === "1";
const requestedDevice = query.get("device");
if (embedded)
  document.documentElement.dataset.previewDevice =
    requestedDevice === "iphone-17" ? "iphone-17" : "iphone-16";
const simulate =
  !embedded &&
  (["iphone-16", "iphone-17"].includes(requestedDevice ?? "") ||
    window.innerWidth > 470);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>{simulate ? <PhoneSimulator /> : <AccountGate><App /></AccountGate>}</ErrorBoundary>
  </StrictMode>,
);
