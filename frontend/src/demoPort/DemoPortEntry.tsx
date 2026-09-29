import { ErrorBoundary } from "../components/ErrorBoundary";
import { AccountGate } from "../components/AccountGate";
import { DemoPortApp } from "./DemoPortApp";
import "../styles/next/index.css";
import "../demo/styles/tokens.css";
import "../demo/styles/glass.css";
import "../demo/styles/base.css";
import "../demo/styles/responsive.css";
import "../demo/styles/home.css";
import "../demo/styles/chapter-tools.css";
import "../demo/styles/study.css";
import "../demo/styles/mistake-book.css";
import "../demo/styles/motion.css";
import "../demo/styles/device-preview.css";
import "../demo/styles/card-system.css";
import "../demo/styles/community.css";
import "../demo/styles/upload.css";
import "../demo/styles/parse-ready.css";
import "../demo/styles/processing.css";
import "../demo/styles/chapter-confirm.css";
import "../demo/styles/course-ready.css";
import "../demo/styles/profile.css";
import "./demoPort.css";

export default function DemoPortEntry() {
  return <ErrorBoundary><AccountGate><DemoPortApp /></AccountGate></ErrorBoundary>;
}
