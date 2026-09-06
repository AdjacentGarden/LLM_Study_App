import type { EvidenceResult } from "../types/api";
import { Icon } from "./Icon";

export function PracticeFeedback({ evidence }: { evidence: EvidenceResult }) {
  const confirmed = evidence.scoring_confidence >= .78 && !evidence.needs_follow_up;
  const good = confirmed && evidence.score >= .7;
  return <section className={`feedback ${good ? "good" : "review"}`} role="status">
    <strong><Icon name={good ? "check" : "spark"} size={18}/> {good ? "关键理解已经到位" : confirmed ? "找到可以再补强的地方" : "这次判断还不够确定"}</strong>
    <p>{confirmed ? "把这次作答变成下一步的学习线索。" : "先参考下面的线索，别把这次结果当成确定结论。"}</p>
    <div className="feedback-details">
      {evidence.matched_rubric.length > 0 && <div><h3>你已经说清楚了</h3><ul>{evidence.matched_rubric.map((text,i)=><li key={i}>{text}</li>)}</ul></div>}
      {evidence.missing_rubric.length > 0 && <div><h3>下次试着补上</h3><ul>{evidence.missing_rubric.map((text,i)=><li key={i}>{text}</li>)}</ul></div>}
      {!evidence.matched_rubric.length && !evidence.missing_rubric.length && <p>可以回到原文，核对定义、条件和推理过程，再试一次。</p>}
    </div>
    <small>本次作答评分 {Math.round(evidence.score * 100)}% · 不是全章掌握率</small>
  </section>;
}
