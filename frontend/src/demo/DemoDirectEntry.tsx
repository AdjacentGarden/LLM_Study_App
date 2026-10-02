import { resetCredits } from "./features/credits/creditStore";
import App from "./App";
import { AccountGate, useAccount } from "../components/AccountGate";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { bookcourseApi } from "./api/bookcourseApi";
import { BookCourseRepositoryProvider } from "./context/BookCourseRepositoryContext";
import { MotionHistoryProvider } from "./motion/MotionHistoryContext";
import { hydrateCourseState } from "./features/courses/repository";
import { useEffect, useState } from "react";
import { runtimeConfig } from "./config/runtime";
import "../styles/next/accounts.css";
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

function AuthenticatedApp() {
  const {state} = useAccount();
  const [readyOwner,setReadyOwner] = useState<string|null>(null), [error,setError] = useState("");
  useEffect(() => { let active=true;setReadyOwner(null);setError(""); runtimeConfig.defaultUserId=state.user_id; resetCredits();
    hydrateCourseState().then(() => {if(active)setReadyOwner(state.user_id);}).catch(e => {if(active)setError(e.message);});
    return () => {active=false;}; },[state.user_id]);
  if(error)return <div role="alert">{error}<button onClick={()=>window.location.reload()}>重新连接</button></div>;
  if(readyOwner!==state.user_id)return <div role="status">正在读取课程…</div>;
  return <BookCourseRepositoryProvider repository={bookcourseApi}><MotionHistoryProvider><App key={state.user_id} /></MotionHistoryProvider></BookCourseRepositoryProvider>;
}
export default function DemoDirectEntry() {return <ErrorBoundary><AccountGate><AuthenticatedApp /></AccountGate></ErrorBoundary>;}

import "./styles/settings.css";
import "./styles/sticker-icons.css";
