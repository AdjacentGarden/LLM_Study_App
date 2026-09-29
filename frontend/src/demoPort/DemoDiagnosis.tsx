import { useEffect, useState } from "react";
import { BookOpenCheck, Check, ClipboardCheck, Sparkles } from "lucide-react";
import { Button, Card } from "./DemoPrimitives";
import type { InterviewResponse } from "../types/api";

const stages = ["学习背景", "自适应小测", "确认方向"];
export function DemoDiagnosis({ session, busy, onSubmit }: {
  session: InterviewResponse | null; busy: boolean;
  onSubmit: (selected: string[], answer: string, confidence: number) => Promise<void>;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [answer, setAnswer] = useState("");
  const [confidence, setConfidence] = useState(.65);
  useEffect(() => { setSelected([]); setAnswer(""); setConfidence(.65); }, [session?.turn.turn_id]);
  if (!session) return <div className="screen-stack assignment-screen"><Card className="parse-empty-card"><ClipboardCheck size={34}/>
    <h2>诊断会话未就绪</h2><p>请返回教材页重新开始。</p></Card></div>;
  const turn = session.turn;
  const phase = turn.phase === "profile_confirmation" ? 2 : turn.phase === "adaptive_diagnosis" ? 1 : 0;
  const canSubmit = !busy && (selected.length > 0 || !!answer.trim() || (phase === 1 && !turn.item));
  return <div className="screen-stack assignment-screen demo-port-diagnosis"><div className="assignment-workspace assignment-practice-workspace">
    <ol className="demo-port-diagnosis-stages">{stages.map((label, index) => <li key={label} aria-current={index === phase ? "step" : undefined}>
      <span>{index < phase ? <Check size={14}/> : index + 1}</span>{label}</li>)}</ol>
    <Card className="assignment-progress-card" aria-label="学习诊断进度"><div className="assignment-progress-heading"><span className="assignment-progress-icon"><BookOpenCheck size={20}/></span>
      <div><small>学习向导</small><h2>{phase === 0 ? "先了解你的目标" : phase === 1 ? "从基础开始，再慢慢深入" : "确认学习方向"}</h2></div>
      <strong>{Math.round(turn.progress * 100)}%</strong></div>
      <div className="assignment-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(turn.progress * 100)}>
        <span style={{ width: `${Math.round(turn.progress * 100)}%` }}/></div></Card>
    <Card className="assignment-card assignment-exercise-card" aria-busy={busy}><div className="assignment-exercise-kicker"><Sparkles size={19}/>
      <strong>{phase === 1 ? `第 ${session.profile.diagnostic_observations.length + 1} 道` : "学习向导"}</strong></div>
      {turn.message && <p className="assignment-exercise-instruction">{turn.message}</p>}
      {turn.question && <h2 className="assignment-question">{turn.question}</h2>}
      {turn.why_asked && <details className="demo-port-diagnosis-why"><summary>为什么问这个？</summary><p>{turn.why_asked}</p></details>}
      {!!turn.options.length && <div className="assignment-choice-options" role="group" aria-label="回答选项">{turn.options.map((option, index) => <button
        key={option.id} type="button" className={selected.includes(option.id) ? "selected" : ""} aria-pressed={selected.includes(option.id)} disabled={busy}
        onClick={() => setSelected(current => turn.response_type === "multiple_choice" ? current.includes(option.id) ? current.filter(item => item !== option.id) : [...current, option.id] : [option.id])}>
        <span className="assignment-option-marker" aria-hidden="true">{String.fromCharCode(65 + index)}</span><span>{option.label}</span>
        {selected.includes(option.id) && <Check className="assignment-option-check" size={21}/>}</button>)}</div>}
      {(!turn.options.length || turn.response_type === "short_answer" || turn.response_type === "explanation") && turn.item && <div className="assignment-short-answer">
        <label className="answer-field"><span className="assignment-answer-label"><strong>你的回答</strong></span><textarea rows={5} value={answer}
          onChange={event => setAnswer(event.target.value)} placeholder="可以用自己的话回答，不确定也没关系"/></label></div>}
      {turn.item && <fieldset className="demo-port-diagnosis-confidence"><legend>你有多确定？</legend>
        {([[.25, "还不确定"], [.65, "大致确定"], [.95, "很有把握"]] as const).map(([value, label]) => <button type="button" key={value}
          aria-pressed={confidence === value} onClick={() => setConfidence(value)}>{label}</button>)}</fieldset>}
      <div className="assignment-primary-action"><Button disabled={!canSubmit} loading={busy} onClick={() => void onSubmit(selected, answer, confidence)}>
        {turn.phase === "profile_confirmation" ? "确认学习方向" : "确认并继续"}</Button></div>
    </Card></div></div>;
}
