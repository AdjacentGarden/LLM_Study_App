import type { Profile, Turn } from "../types/api";

interface Props {
  turn: Turn | null;
  profile?: Profile;
  selected: string[];
  confidence: number;
  busy: boolean;
  canSubmit: boolean;
  onSelect: (id: string) => void;
  onConfidence: (value: number) => void;
  onSubmit: () => void;
}

export function DiagnosticJourney({ turn, profile, selected, confidence, busy, canSubmit, onSelect, onConfidence, onSubmit }: Props) {
  if (!turn) return <div className="empty-state" role="status">正在恢复诊断会话…</div>;
  const diagnosing = turn.phase === "adaptive_diagnosis";
  const confirming = turn.phase === "profile_confirmation";
  const phase = confirming ? 2 : diagnosing ? 1 : 0;
  const count = profile?.diagnostic_observations.length ?? 0;
  return <div className="interview-page">
    <ol className="diagnostic-stages" aria-label="当前诊断阶段">{["选择目标", "自适应小测", "学习方向"].map((label, index) => <li key={label} aria-current={phase === index ? "step" : undefined} className={index <= phase ? "reached" : ""}><span>{index < phase ? "✓" : index + 1}</span>{label}</li>)}</ol>
    <div className="interview-progress"><span>{diagnosing ? "知识诊断" : confirming ? "学习方案" : "学习目标"}</span><b>{Math.round(turn.progress * 100)}%</b><i role="progressbar" aria-label="诊断进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(turn.progress * 100)}><em style={{ width: `${turn.progress * 100}%` }}/></i></div>
    <div className="question-transition" key={turn.turn_id}>
      <section className="assistant-message question-card"><div className="assistant-title"><div><strong>{confirming ? "学习方案" : turn.item ? `第 ${count + 1} 题` : "基础信息"}</strong><span>{turn.item ? "请选择答案与确定程度" : "用于生成个性化课程"}</span></div></div><p>{turn.message}</p>{turn.question && <h2>{turn.question}</h2>}</section>
      {turn.options.length > 0 && <div className="choice-block"><span className="selection-hint">{turn.response_type === "multiple_choice" ? "可多选，选好后继续" : "请选择一项，选好后继续"}</span><div className="option-grid" role="group" aria-label="回答选项">{turn.options.map((option, index) => <button key={option.id} type="button" disabled={busy} aria-pressed={selected.includes(option.id)} className={selected.includes(option.id) ? "selected" : ""} onClick={() => onSelect(option.id)} style={{ "--option-index": index } as React.CSSProperties}><i aria-hidden="true">{selected.includes(option.id) ? "✓" : String.fromCharCode(65 + index)}</i><span>{option.label}</span></button>)}</div></div>}
      {turn.item && <fieldset className="confidence-options" disabled={busy}><legend>这次选择，你有多确定？</legend><div>{[[.25, "还不确定"], [.65, "大致确定"], [.95, "很有把握"]].map(([value, label]) => <button key={value} type="button" aria-pressed={confidence === value} className={confidence === value ? "selected" : ""} onClick={() => onConfidence(Number(value))}>{label}</button>)}</div></fieldset>}
      {turn.why_asked && <details className="why-card"><summary>为什么问这个？</summary><p>{turn.why_asked}</p></details>}
    </div>
    <div className="diagnostic-footer"><p>{busy ? "正在保存…" : confirming ? "确认后生成课程。" : "作答将用于调整章节优先级。"}</p><button className="primary" disabled={!canSubmit || busy} onClick={onSubmit}>{busy ? <><span className="button-spinner" aria-hidden="true"/>正在分析</> : confirming ? "确认方案" : diagnosing && !turn.item ? "开始诊断" : "下一题"}</button></div>
  </div>;
}
