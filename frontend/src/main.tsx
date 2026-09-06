import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { PhoneSimulator } from "./components/PhoneSimulator";
import { ErrorBoundary } from "./components/ErrorBoundary";
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

const query = new URLSearchParams(window.location.search);
const embedded = query.get("embedded") === "1";
if (embedded) document.documentElement.dataset.previewDevice = "iphone-16";
const simulate = !embedded && (query.get("device") === "iphone-16" || window.innerWidth > 470);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>{simulate ? <PhoneSimulator /> : <App />}</ErrorBoundary>
  </StrictMode>,
);
