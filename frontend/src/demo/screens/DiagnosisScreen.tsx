import type { CSSProperties } from "react";
import {
  BookOpenCheck,
  CheckCircle2,
  ClipboardCheck,
  Lightbulb,
  RotateCcw,
  Save
} from "lucide-react";
import {
  Button,
  Card,
  Pill
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useDiagnosisMotion } from "../motion";
import {
  backendAssetUrl
} from "./shared";

function DiagnosisKnowledgeProgress({
  count,
  motionKey,
  motionState,
  settle
}: {
  count: number;
  motionKey: string;
  motionState: "entering" | "idle";
  settle: (key: string) => void;
}) {
  const value = 1;
  const progressKey = `${motionKey}:knowledge-points`;

  return (
    <div className="diagnosis-knowledge-progress">
      <div
        className="diagnosis-knowledge-progress-track"
        role="progressbar"
        aria-label={`诊断完成，已定位 ${count} 个相关知识点`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value * 100}
      >
        <span
          className="diagnosis-knowledge-progress-fill"
          data-motion-diagnosis-progress-key={progressKey}
          data-motion-diagnosis-progress-state={motionState}
          style={{ "--diagnosis-progress-scale": value } as CSSProperties}
          onAnimationEnd={(event) => {
            if (event.target === event.currentTarget && event.animationName === "motion-diagnosis-progress-in") settle(motionKey);
          }}
        />
      </div>
      <span>{`诊断完成 · 已定位 ${count} 个相关知识点`}</span>
    </div>
  );
}

export function DiagnosisScreen() {
  const { go, latestDiagnosis, showToast, openSourcePage, openSheet, uploadedFile } = useAppContext();
  const diagnosisMotion = useDiagnosisMotion(latestDiagnosis?.submission_id ?? null);
  const liveCitation = latestDiagnosis?.review_citations[0] ?? null;
  const liveAsset = latestDiagnosis?.related_assets[0] ?? null;

  if (!latestDiagnosis) {
    return (
      <div className="screen-stack diagnosis-screen">
        <Card className="parse-empty-card">
          <ClipboardCheck size={34} aria-hidden="true" />
          <h2>还没有真实诊断结果</h2>
          <p>提交作业后，系统会对照教材依据定位卡点，并给出下一步复习建议。</p>
          <Button onClick={() => go("assignment")}>去提交作业</Button>
          <Button variant="secondary" onClick={() => go("lesson")}>回到章节</Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="screen-stack diagnosis-screen">
      <div className="diagnosis-workspace">
        <div className="diagnosis-results-column">
      <Card
        className="diagnosis-card"
        data-motion-item="content"
        data-motion-item-key={diagnosisMotion.motionKey ?? "diagnosis:empty"}
        data-motion-item-state={diagnosisMotion.state}
        data-motion-diagnosis-key={diagnosisMotion.motionKey ?? "diagnosis:empty"}
        data-motion-diagnosis-state={diagnosisMotion.state}
      >
        <span className="diagnosis-status-icon" aria-hidden="true">
          <CheckCircle2 size={24} />
        </span>
        <div className="diagnosis-summary-copy">
          <Pill tone={latestDiagnosis.mistake_recorded ? "orange" : "mint"}>
            {latestDiagnosis.mistake_recorded ? "需要再巩固" : "理解已到位"}
          </Pill>
          <h2>{latestDiagnosis.mistake_recorded ? "找到一个关键卡点" : "本题诊断完成"}</h2>
          <p>{latestDiagnosis.stuck_point}</p>
        </div>
      </Card>
      <DiagnosisKnowledgeProgress
        count={latestDiagnosis.knowledge_points.length}
        motionKey={diagnosisMotion.motionKey ?? "diagnosis:empty"}
        motionState={diagnosisMotion.state}
        settle={diagnosisMotion.settle}
      />
      <Card className="diagnosis-analysis-card">
        <div className="diagnosis-section-heading">
          <span aria-hidden="true"><BookOpenCheck size={19} /></span>
          <div>
            <small>为什么会卡住</small>
            <h3>诊断解析</h3>
          </div>
        </div>
        <p className="diagnosis-result-copy">{latestDiagnosis.result}</p>
        {liveCitation ? (
          <div className="diagnosis-evidence">
            <div>
              <span>教材依据 · 第 {liveCitation.page} 页</span>
              <strong>{liveCitation.chapter_title}</strong>
              <p>{liveCitation.quote}</p>
            </div>
            <button className="inline-link" type="button" onClick={() => uploadedFile
              ? openSourcePage({
                  bookId: uploadedFile.bookId,
                  title: liveCitation.chapter_title || "诊断来源页",
                  pageStart: liveCitation.page,
                  pageEnd: liveCitation.page
                })
              : openSheet({
                  type: "source",
                  title: "诊断来源页",
                  page: `第 ${liveCitation.page} 页`,
                  image: backendAssetUrl(liveAsset?.image_url)
                })}>
              查看原文
            </button>
          </div>
        ) : (
          <p className="helper-text">这次没有找到可核对的教材页，建议回到章节重新查看上下文。</p>
        )}
      </Card>
        </div>
        <div className="diagnosis-next-steps-column">
      <Card className="ai-feedback-card">
        <div className="diagnosis-section-heading">
          <span aria-hidden="true"><Lightbulb size={19} /></span>
          <div>
            <small>建议先做这一步</small>
            <h3>带着提示再想一次</h3>
          </div>
        </div>
        <p className="diagnosis-hint">{latestDiagnosis.hint}</p>
        {latestDiagnosis.needs_followup && latestDiagnosis.followup_question ? (
          <div className="diagnosis-followup">
            <span>继续追问</span>
            <p>{latestDiagnosis.followup_question}</p>
          </div>
        ) : null}
        <div className="diagnosis-knowledge-list">
          <span>相关知识点</span>
          <div>
            {latestDiagnosis.knowledge_points.length > 0
              ? latestDiagnosis.knowledge_points.map((point) => <span key={point}>{point}</span>)
              : <span>暂无标注</span>}
          </div>
        </div>
      </Card>
      <div className="diagnosis-actions">
        <Button
          variant="secondary"
          icon={<RotateCcw size={18} aria-hidden="true" />}
          onClick={() => go("assignment")}
        >
          重新作答
        </Button>
        <Button variant="secondary" onClick={() => go("flashcards")}>复习相关闪卡</Button>
        <Button icon={<Save size={18} aria-hidden="true" />} onClick={() => {
          if (latestDiagnosis.mistake_recorded) {
            showToast("这条卡点已在错题本中");
            go("mistakes");
            return;
          }
          showToast("继续本节学习");
          go("lesson");
        }}>
          {latestDiagnosis.mistake_recorded ? "查看错题本" : "继续学习"}
        </Button>
      </div>
        </div>
      </div>
    </div>
  );
}
