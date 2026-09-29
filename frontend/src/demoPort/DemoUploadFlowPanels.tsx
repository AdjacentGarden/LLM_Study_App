import { Check, CheckCircle2, FileText, RotateCcw } from "lucide-react";
import type { BookStatus } from "../types/api";
import { Button, ProgressBar } from "./DemoPrimitives";

function fileSize(bytes?: number) {
  if (bytes === undefined) return "待确认";
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function ParseReadyPanel({ filename, sizeBytes, onStart }: { filename: string; sizeBytes?: number; onStart: () => void }) {
  return <div className="screen-stack community-detail-screen parse-ready-screen demo-port-parse-ready">
    <div className="community-detail-workspace parse-ready-workspace"><article className="community-detail-overview parse-ready-overview">
      <div className="community-detail-visual parse-ready-visual"><div className="community-detail-cover-fallback parse-ready-cover" aria-hidden="true">
        <span className="parse-ready-cover-icon"><FileText size={56} /></span><strong>PDF</strong><span>AI 课程资料</span>
      </div></div>
      <div className="community-detail-summary parse-ready-summary">
        <p className="community-detail-owner parse-ready-owner"><span className="upload-success-mark" aria-hidden="true"><CheckCircle2 size={15} /></span><span>已上传 · 待解析</span></p>
        <h2 title={filename}>{filename.replace(/\.pdf$/iu, "")}</h2><p className="community-detail-edition">PDF · {fileSize(sizeBytes)}</p>
        <div className="parse-info-grid" aria-label="教材解析信息">
          <div className="metric-card"><small>文件类型</small><strong>PDF</strong></div>
          <div className="metric-card"><small>文件大小</small><strong>{fileSize(sizeBytes)}</strong></div>
          <div className="metric-card"><small>下一步</small><strong>后台解析</strong></div>
        </div>
      </div>
    </article></div>
    <div className="community-detail-actions parse-flow-actions parse-ready-actions"><Button onClick={onStart}>开始解析</Button></div>
  </div>;
}

const stages = ["解析页面与版面", "整理章节结构", "加入个人书架", "准备学习诊断"];
function activeIndex(stage: string) {
  if (stage === "structuring") return 1;
  if (stage === "claiming") return 2;
  if (stage === "diagnostics") return 3;
  return 0;
}

export function ProcessingPanel({ stage, status, progress, message, error, warning, onClose, onRetry, retryDisabled, onChoose }: {
  stage: string; status: BookStatus | null; progress: number | null; message: string; error: string; warning: string;
  onClose: () => void; onRetry: () => void; retryDisabled: boolean; onChoose: () => void;
}) {
  const current = activeIndex(stage);
  return <div className="screen-stack processing-flow-screen demo-port-processing">
    <div className="processing-flow-primary"><div className="processing-animation-stage" aria-hidden="true"><div className="processing-sprite-viewport">
      <img className="processing-sprite-strip" src="/assets/brand/loading/cloud-course-loading-strip-v1.png" alt="" />
    </div></div></div>
    <div className="processing-flow-support"><div className="stage-list" aria-label="解析处理阶段">
      {stages.map((name, index) => {
        const complete = index < current;
        const active = index === current;
        return <div key={name} className={`stage-row ${complete ? "done" : ""} ${active ? "is-processing" : ""}`}
          data-stage-status={complete ? "done" : active ? error ? "error" : "processing" : "waiting"} aria-current={active && !error ? "step" : undefined}>
          <span>{complete ? <Check size={18} aria-hidden="true" /> : index + 1}</span><strong>{name}</strong>
          <small className="stage-status">{complete ? "已完成" : active ? error ? "待重试" : "处理中" : "等待中"}</small>
        </div>;
      })}
    </div>
      <p className={`processing-status-message ${error ? "is-error" : ""}`} role={error ? "alert" : "status"}>{error || message || "正在等待后端状态…"}</p>
      {warning && <p className="demo-port-processing-warning" role="status">{warning}</p>}
      {progress !== null && !error && <ProgressBar value={progress} label={`后端解析进度 ${progress}%`} />}
      {status?.page_count && <p className="demo-port-processing-pages">已读取 {status.page_count} 页</p>}
    </div>
    <div className="processing-flow-actions">
      {error ? <><Button icon={<RotateCcw size={18} />} disabled={retryDisabled} onClick={onRetry}>{status?.status === "failed" || status?.status === "ocr_review_required" ? "重试 OCR" : "继续任务"}</Button>
        <Button variant="secondary" onClick={onChoose}>重新选择文件</Button></>
        : <Button variant="secondary" onClick={onClose}>后台运行，先回首页</Button>}
    </div>
  </div>;
}

export function UploadCompletePanel({ title, onEnter, onNew }: { title: string; onEnter: () => void; onNew: () => void }) {
  return <div className="screen-stack centered-flow parse-complete-screen course-ready-screen course-ready-focus demo-port-upload-complete" data-course-ready-phase="settled">
    <div className="course-ready-focus-stage"><div className="course-ready-mascot-scene"><div className="course-ready-mascot-motion">
      <div className="course-ready-final-image"><img className="success-hero-image" src="/assets/brand/cloud-mascot-success-transparent-v1.png" alt="云朵举起完成标记和教材" /></div>
    </div></div>
      <div className="course-ready-focus-copy"><div className="course-ready-success-heading"><h1>教材已准备好</h1>
        <span className="course-ready-success-mark" aria-hidden="true"><Check size={24} /></span></div>
        <p>《{title}》已加入书架，学习前诊断可以开始。</p>
      </div>
    </div>
    <div className="course-ready-actions course-ready-focus-actions"><Button onClick={onEnter}>进入学习</Button>
      <Button variant="secondary" onClick={onNew}>继续上传</Button></div>
  </div>;
}
