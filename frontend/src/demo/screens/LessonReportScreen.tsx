import { useEffect, useState } from "react";
import { request } from "../api/transport";
import {
  Button,
  Card,
  Metric,
  Pill
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";

export function LessonReportScreen() {
  const { go, showToast, loadedBookId, activeChapterId, parsedChapters } = useAppContext();
  const [report,setReport]=useState<{mastery:number|null;minutes:number;minutes_recorded?:boolean;mistakes:number;summary:string}|null>(null);
  const [error,setError]=useState("");
  useEffect(()=>{let active=true;if(loadedBookId)void request<typeof report>(`/api/demo/report/${encodeURIComponent(loadedBookId)}?chapter_id=${encodeURIComponent(activeChapterId??"")}`).then(value=>{if(active)setReport(value);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[loadedBookId,activeChapterId]);
  const chapter=parsedChapters?.find(item=>item.chapter_id===activeChapterId);
  const mastery=report?.mastery == null ? "暂无作答" : `${Math.round(report.mastery)}%`;
  const summaryMotion = useLocalMotionItem("lesson-report:summary");
  return (
    <div className="screen-stack report-screen">
      <div className="report-workspace">
        <div className="report-summary-column">
      <Card {...summaryMotion.attributes} surface="celebration" className="report-card">
        <p className="eyebrow">章节学习报告</p>
        <h2>{report?.mastery == null ? "开始积累学习记录" : "你的学习进展"}</h2>
        <p>{chapter?.source_title??"课程学习记录"}</p>
        <div className="report-score-ring">
          <strong>{mastery}</strong>
        </div>
      </Card>
      <div className="metric-grid">
        <Metric label="正确率" value={mastery} />
        <Metric label="用时" value={report?.minutes_recorded ? `${report.minutes} 分` : "暂无记录"} />
        <Metric label="错题" value={report ? `${report.mistakes} 题` : "—"} />
      </div>
        </div>
        <div className="report-guidance-column">
      <Card className="knowledge-map-card">
        <div className="section-head">
          <h3>知识点掌握情况</h3>
          <button className="inline-link" type="button" onClick={() => go("notes")}>查看详情</button>
        </div>
        <div className="chip-row static">
          <Pill>{error || report?.summary || "完成作答后显示真实知识证据"}</Pill>
        </div>
      </Card>
      <Card className="ai-suggestion-card">
        <h3>AI 学习建议</h3>
        <div className="suggestion-grid">
          {["背本节闪卡", "再做专项练习", "与 AI 深入问答"].map((item) => (
            <button type="button" key={item} onClick={() => item.includes("闪卡") ? go("flashcards") : item.includes("问答") ? showToast("可以打开右侧 AI 助手继续提问", "info") : go("assignment")}>
              {item}
            </button>
          ))}
        </div>
      </Card>
        </div>
      </div>
      <div className="report-actions">
      <Button onClick={() => go("study")}>返回学习目录·继续下一章</Button>
      <Button variant="secondary" onClick={() => go("mistakes")}>查看错题复习</Button>
      </div>
    </div>
  );
}
