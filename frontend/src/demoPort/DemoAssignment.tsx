import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpenCheck, Check, CheckCircle2, ClipboardCheck, Lightbulb, X } from "lucide-react";
import { Button, Card } from "./DemoPrimitives";
import type { Course, CourseActivity, PublicPracticeItem } from "../types/api";

type AnswerRecord = { answer: string; activity: CourseActivity };
const isJudgment = (item: PublicPracticeItem) => item.response_type === "judgment" ||
  (item.options.length === 2 && item.options.every(value => /^(正确|错误|对|错)$/.test(value)));
const kind = (item: PublicPracticeItem) => isJudgment(item) ? "judgment" : item.options.length ? "choice" : "short-answer";
const kindLabel = (item: PublicPracticeItem) => isJudgment(item) ? "判断题" : item.response_type === "multiple_choice" ? "多选题" : item.options.length ? "选择题" : "简答题";

export function DemoAssignment({ course, onPractice, onSource, onMistakes, onCards, onReading, onBack }: {
  course: Course;
  onPractice: (id: string, answer: string, selected: string[], confidence: number, seconds: number) => Promise<CourseActivity | null>;
  onSource: (page: number) => void; onMistakes: () => void; onCards: () => void; onReading: () => void; onBack: () => void;
}) {
  const items = course.practice_items;
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [answer, setAnswer] = useState("");
  const [confidence, setConfidence] = useState(.65);
  const [records, setRecords] = useState<Record<string, AnswerRecord>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [finished, setFinished] = useState(false);
  const start = useRef(Date.now());
  const item = items[index];
  const submitted = item ? records[item.item_id] : undefined;
  const questionKind = item ? kind(item) : "short-answer";
  const page = useMemo(() => item?.citations?.find(citation => citation.page_number > 0)?.page_number
    ?? course.knowledge_points.find(point => point.point_id === item?.point_id)?.citations.find(citation => citation.page_number > 0)?.page_number,
    [item, course]);

  useEffect(() => { setIndex(0); setSelected([]); setAnswer(""); setRecords({}); setFinished(false); start.current = Date.now(); }, [course.course_id, course.version]);

  async function submit() {
    if (!item || saving || submitted) return;
    const response = item.options.length ? selected.map(value => item.options[Number(value)]).filter(Boolean).join("、") : answer.trim();
    if (!response) { setError("请先作答。"); return; }
    setSaving(true); setError("");
    try {
      const activity = await onPractice(item.item_id, response, selected, confidence, (Date.now() - start.current) / 1000);
      if (!activity) { setError("答案尚未保存，请重试。"); return; }
      setRecords(previous => ({ ...previous, [item.item_id]: { answer: response, activity } }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "答案尚未保存，请重试。"); }
    finally { setSaving(false); }
  }

  function next() {
    if (index >= items.length - 1) { setFinished(true); return; }
    setIndex(value => value + 1); setSelected([]); setAnswer(""); setConfidence(.65); setError(""); start.current = Date.now();
  }

  if (!items.length) return <div className="screen-stack assignment-screen"><Card className="parse-empty-card"><ClipboardCheck size={34}/>
    <h2>本节暂无练习</h2><p>当前课程尚未提供可提交的练习题。</p><Button variant="secondary" onClick={onReading}>返回章节学习</Button>
  </Card></div>;

  if (finished) return <div className="screen-stack assignment-screen"><div className="assignment-workspace assignment-practice-workspace">
    <Card className="assignment-card assignment-exercise-card" aria-label="作业诊断结果">
      <div className="assignment-exercise-kicker"><span aria-hidden="true"><ClipboardCheck size={19}/></span><strong>作业诊断</strong></div>
      <h2 className="assignment-question">本节练习已完成</h2>
      <p className="assignment-exercise-instruction">已提交 {Object.keys(records).length} / {items.length} 题，结果由学习服务保存。</p>
      <div className="demo-port-assignment-results">{items.map((question, number) => <div key={question.item_id}>
        <strong>第 {number + 1} 题 · {kindLabel(question)}</strong><p>{question.prompt}</p>
        <span>{records[question.item_id] ? `评分 ${Math.round(records[question.item_id].activity.evidence.score * 100)}%` : "未提交"}</span>
      </div>)}</div>
      <div className="assignment-primary-action"><Button onClick={onMistakes}>查看错题集</Button><Button variant="secondary" onClick={onCards}>复习闪卡</Button><Button variant="text" onClick={onBack}>返回学习目录</Button></div>
    </Card></div></div>;

  return <div className="screen-stack assignment-screen"><div className="assignment-workspace assignment-practice-workspace">
    <Card className="assignment-progress-card" aria-label="练习进度">
      <div className="assignment-progress-heading"><span className="assignment-progress-icon" aria-hidden="true"><BookOpenCheck size={20}/></span>
        <div><small>本节练习</small><h2>{course.chapter_title}</h2></div><strong>{index + 1} / {items.length}</strong></div>
      <div className="assignment-progress-track" role="progressbar" aria-label={`练习进度，第 ${index + 1} 题，共 ${items.length} 题`}
        aria-valuemin={1} aria-valuemax={items.length} aria-valuenow={index + 1}>
        <span style={{ width: `${((index + 1) / items.length) * 100}%` }}/>
      </div>
    </Card>
    <Card className="assignment-card assignment-exercise-card" data-assignment-type={questionKind} aria-live="polite" aria-busy={saving}>
      <div className="assignment-exercise-kicker"><span aria-hidden="true"><ClipboardCheck size={19}/></span>
        <strong>{kindLabel(item)} · 第 {index + 1} 题</strong></div>
      <h2 className="assignment-question">{item.prompt}</h2>
      {!submitted && questionKind === "judgment" && <div className="assignment-judgment-options" role="group" aria-label="请选择判断结果">
        {item.options.map((option, optionIndex) => <button key={optionIndex} type="button" className={selected.includes(String(optionIndex)) ? "selected" : ""}
          aria-pressed={selected.includes(String(optionIndex))} onClick={() => { setSelected([String(optionIndex)]); setError(""); }}>
          <span aria-hidden="true">{optionIndex === 0 ? <Check size={25}/> : <X size={25}/>}</span><strong>{option}</strong></button>)}</div>}
      {!submitted && questionKind === "choice" && <div className="assignment-choice-options" role="group" aria-label={item.response_type === "multiple_choice" ? "请选择所有正确答案" : "请选择答案"}>
        {item.options.map((option, optionIndex) => <button key={optionIndex} type="button" className={selected.includes(String(optionIndex)) ? "selected" : ""}
          aria-pressed={selected.includes(String(optionIndex))} onClick={() => { const id = String(optionIndex); setSelected(previous => item.response_type === "multiple_choice" ? previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id] : [id]); setError(""); }}>
          <span className="assignment-option-marker" aria-hidden="true">{String.fromCharCode(65 + optionIndex)}</span><span>{option}</span>
          {selected.includes(String(optionIndex)) && <Check className="assignment-option-check" size={21} aria-hidden="true"/>}</button>)}</div>}
      {!submitted && questionKind === "short-answer" && <div className="assignment-short-answer"><p className="assignment-short-hint"><Lightbulb size={19}/>
        <span>写下自己的理解，再查看服务端反馈。</span></p><label className="answer-field"><span className="assignment-answer-label">
        <strong>你的答案</strong><span className="assignment-character-count">{answer.length} / 1000</span>
        {answer.trim() && <CheckCircle2 className="assignment-answer-check" size={16} aria-hidden="true"/>}</span>
        <textarea value={answer} maxLength={1000} onChange={event => { setAnswer(event.target.value); setError(""); }} placeholder="在这里写下你的思路……" rows={7}/>
      </label></div>}
      {!submitted && <div className="demo-port-confidence"><label htmlFor="demo-port-confidence">作答信心 <strong>{Math.round(confidence * 100)}%</strong></label>
        <input id="demo-port-confidence" type="range" min="0" max="100" value={Math.round(confidence * 100)} onChange={event => setConfidence(Number(event.target.value) / 100)}/></div>}
      {submitted && <div className="demo-port-assignment-feedback" role="status"><strong>本题评分 {Math.round(submitted.activity.evidence.score * 100)}%</strong>
        {submitted.activity.evidence.matched_rubric.length > 0 && <p>已掌握：{submitted.activity.evidence.matched_rubric.join("、")}</p>}
        {submitted.activity.evidence.missing_rubric.length > 0 && <p>待巩固：{submitted.activity.evidence.missing_rubric.join("、")}</p>}
      </div>}
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="assignment-exercise-footer">{page ? <button className="assignment-source-button" type="button" onClick={() => onSource(page)}>查看原文 · PDF 第 {page} 页</button>
        : <span className="assignment-source-button">本题暂无原文页码</span>}</div>
      <div className="assignment-primary-action"><Button disabled={saving || (!submitted && !(selected.length || answer.trim()))} loading={saving} onClick={submitted ? next : () => void submit()}>
        {submitted ? index === items.length - 1 ? "查看作业诊断" : "下一题" : "提交答案"}</Button></div>
    </Card>
  </div></div>;
}
