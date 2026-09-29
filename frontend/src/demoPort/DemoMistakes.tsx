import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, BookOpenCheck, BrainCircuit, CalendarClock, Check, CheckCircle2, ChevronLeft, ChevronRight, Clock3, RotateCcw, Search, Target, X } from "lucide-react";
import { Button, Card, Pill } from "./DemoPrimitives";
import type { BookCatalogItem, CourseActivity } from "../types/api";
import type { MistakeReviewStatus, StudyMistake, StudyWorkspace } from "../types/studyWorkspace";

const filters: { id: "all" | MistakeReviewStatus; label: string }[] = [
  { id: "all", label: "全部" }, { id: "unreviewed", label: "待复习" },
  { id: "reviewing", label: "巩固中" }, { id: "self_reported_mastered", label: "自报已掌握" },
];
const reasons = ["知识盲区", "审题疏忽", "概念混淆", "方法不熟"];
const statusLabel = (status: MistakeReviewStatus) => status === "self_reported_mastered" ? "自报已掌握" : status === "reviewing" ? "巩固中" : "待复习";
const statusTone = (status: MistakeReviewStatus) => status === "self_reported_mastered" ? "mastered" : status === "reviewing" ? "learning" : "due";
const score = (value: number) => `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;

export function DemoMistakes({ book, workspace, loading, error, busy, initialChapterId, onReload, onRetry, onStatus, onCourse, onSource, onUpload }: {
  book: BookCatalogItem | null; workspace: StudyWorkspace | null; loading: boolean; error: string | null; busy: boolean; initialChapterId?: string;
  onReload: () => void;
  onRetry: (mistake: StudyMistake, answer: string, selected: string[], confidence: number, seconds: number) => Promise<CourseActivity | null>;
  onStatus: (mistakeId: string, status: MistakeReviewStatus, reason?: string | null) => Promise<boolean>;
  onCourse: (chapterId: string) => void; onSource: (page: number) => void; onUpload: () => void;
}) {
  const mistakes = workspace && book && workspace.book_id === book.book_id ? workspace.mistakes : [];
  const [filter, setFilter] = useState<"all" | MistakeReviewStatus>("all");
  const [query, setQuery] = useState("");
  const [allChapters, setAllChapters] = useState(!initialChapterId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<"overview" | "review">("overview");
  const [answer, setAnswer] = useState("");
  const [options, setOptions] = useState<string[]>([]);
  const [confidence, setConfidence] = useState(.65);
  const [reason, setReason] = useState<string | null>(null);
  const [result, setResult] = useState<CourseActivity | null>(null);
  const [pendingPayload, setPendingPayload] = useState<{ answer: string; options: string[]; confidence: number; seconds: number } | null>(null);
  const [working, setWorking] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const started = useRef(Date.now());
  const answerRef = useRef<HTMLTextAreaElement>(null);
  const filtered = useMemo(() => mistakes.filter(item => {
    if (!allChapters && initialChapterId && item.chapter_id !== initialChapterId && item.section_id !== initialChapterId) return false;
    if (filter !== "all" && item.review.status !== filter) return false;
    const term = query.trim().toLocaleLowerCase();
    return !term || [item.practice_item.prompt, item.chapter_title, item.attempt.answer || ""].some(value => value.toLocaleLowerCase().includes(term));
  }), [mistakes, allChapters, initialChapterId, filter, query]);
  const selected = filtered.find(item => item.mistake_id === selectedId) ?? filtered[0] ?? null;
  const selectedIndex = selected ? filtered.findIndex(item => item.mistake_id === selected.mistake_id) : -1;
  const reviewable = mistakes.filter(item => item.review.status !== "self_reported_mastered").length;
  const mastered = mistakes.length - reviewable;
  const repeated = mistakes.filter(item => item.attempt_count > 1).length;
  const progress = mistakes.length ? Math.round(mastered / mistakes.length * 100) : 0;
  const sourcePages = selected ? [...new Set(selected.review_material.citations.map(item => item.page_number).filter(page => page > 0))] : [];

  useEffect(() => { setSelectedId(null); setFilter("all"); setQuery(""); setAllChapters(!initialChapterId); setMode("overview"); }, [book?.book_id, initialChapterId]);
  useEffect(() => { setAnswer(""); setOptions([]); setConfidence(.65); setReason(selected?.review.reason ?? null); setResult(null); setPendingPayload(null); setActionError(""); started.current = Date.now(); }, [selected?.mistake_id]);

  function beginReview(item?: StudyMistake) {
    const next = item ?? filtered.find(value => value.review.status !== "self_reported_mastered") ?? filtered[0];
    if (!next) return;
    setSelectedId(next.mistake_id); setMode("review"); setAnswer(""); setOptions([]); setResult(null); setPendingPayload(null);
    setReason(next.review.reason ?? null); setActionError(""); setNotice(""); started.current = Date.now();
  }

  async function retry() {
    if (!selected || !selected.can_retry || working || busy || result) return;
    const question = selected.practice_item;
    const chosen = options.map(index => question.options[Number(index)]).filter(Boolean).join("、");
    const payload = pendingPayload ?? { answer: answer.trim() || chosen, options, confidence, seconds: (Date.now() - started.current) / 1000 };
    if (!payload.answer) { setActionError("先写下答案或选择选项，再提交评分。"); answerRef.current?.focus(); return; }
    setPendingPayload(payload); setWorking(true); setActionError("");
    try {
      const next = await onRetry(selected, payload.answer, payload.options, payload.confidence, payload.seconds);
      if (!next) { setActionError("服务端尚未确认这次评分，请重试同一次提交。"); return; }
      setResult(next); setNotice(next.duplicate ? "这次作答此前已保存，没有重复计分。" : "新答案已由服务端评分并保存。");
    } catch (cause) { setActionError(cause instanceof Error ? cause.message : "提交失败，请重试。"); }
    finally { setWorking(false); }
  }

  async function setStatus(next: MistakeReviewStatus) {
    if (!selected || working || busy) return;
    setWorking(true); setActionError("");
    try {
      if (!await onStatus(selected.mistake_id, next, reason)) { setActionError("回访状态没有保存，请重试。"); return; }
      setNotice(next === "self_reported_mastered" ? "已保存自我掌握标记，题目评分仍以服务端记录为准。" : "回访状态已保存。");
      const following = filtered[selectedIndex + 1];
      if (mode === "review" && following) beginReview(following);
      else if (mode === "review") setMode("overview");
    } catch (cause) { setActionError(cause instanceof Error ? cause.message : "回访状态没有保存，请重试。"); }
    finally { setWorking(false); }
  }

  if (mode === "review" && selected) return <div className="mistake-book-screen mistake-review-screen">
    <section className="mistake-review-heading" aria-label="错题重做进度">
      <button className="mistake-inline-back" type="button" onClick={() => setMode("overview")}><ChevronLeft size={19}/>返回错题集</button>
      <div className="mistake-review-progress-copy"><span>错题重做</span><strong>{selectedIndex + 1} / {filtered.length}</strong></div>
      <div className="mistake-review-progress" role="progressbar" aria-valuemin={0} aria-valuemax={filtered.length} aria-valuenow={selectedIndex + 1}>
        <span style={{ transform: `scaleX(${filtered.length ? (selectedIndex + 1) / filtered.length : 0})` }}/></div>
    </section>
    <main className="mistake-review-workspace"><article className="mistake-review-question">
      <div className="mistake-review-meta"><span>{selected.chapter_title}</span><span className="mistake-error-count">已作答 {selected.attempt_count} 次</span></div>
      <h2>{selected.practice_item.prompt}</h2>
      {selected.practice_item.options.length ? <div className="assignment-choice-options" role="group" aria-label="重做选项">
        {selected.practice_item.options.map((option, index) => <button key={index} type="button" className={options.includes(String(index)) ? "selected" : ""}
          disabled={!!pendingPayload || !!result || working} aria-pressed={options.includes(String(index))}
          onClick={() => setOptions(old => selected.practice_item.response_type === "multiple_choice" ? old.includes(String(index)) ? old.filter(value => value !== String(index)) : [...old, String(index)] : [String(index)])}>
          <span className="assignment-option-marker">{String.fromCharCode(65 + index)}</span><span>{option}</span></button>)}</div>
        : <div className="mistake-answer-field"><div><label htmlFor="mistake-review-answer">写下你的答案</label>
          <button type="button" disabled={!!pendingPayload} onClick={() => setAnswer(selected.attempt.answer || "")}>填入上次答案</button></div>
          <textarea ref={answerRef} id="mistake-review-answer" value={answer} disabled={!!pendingPayload || !!result || working}
            placeholder="先独立回忆，不急着看解析" onChange={event => setAnswer(event.target.value)}/></div>}
      <label className="demo-port-confidence">作答信心 <strong>{Math.round(confidence * 100)}%</strong><input type="range" min="0" max="100" value={Math.round(confidence * 100)}
        disabled={!!pendingPayload || !!result || working} onChange={event => setConfidence(Number(event.target.value) / 100)}/></label>
      {actionError && <p className="mistake-review-error" role="alert"><AlertCircle size={16}/>{actionError}</p>}
      {!result && <Button className="mistake-review-submit" disabled={!selected.can_retry || working || busy || (!pendingPayload && !answer.trim() && !options.length)} loading={working}
        onClick={() => void retry()}>{pendingPayload ? "重试同一次提交" : "提交并对照"}</Button>}
      {!selected.can_retry && <p>历史课程题目当前不能重做，可查看此前作答和原文。</p>}
    </article>
    {result && <section className="mistake-review-analysis" aria-live="polite">
      <div className="mistake-comparison"><div><span>我的新答案 · {score(result.evidence.score)}</span><p>{pendingPayload?.answer}</p></div>
        <div><span>上次答案</span><p>{selected.attempt.answer || "未记录"}</p></div></div>
      {notice && <p role="status">{notice}</p>}
      <div className="mistake-reflection"><h3>这次为什么会错？</h3><div className="mistake-reason-options" aria-label="选择错因">
        {reasons.map(value => <button key={value} type="button" aria-pressed={reason === value} onClick={() => setReason(value)}>
          {reason === value && <Check size={15}/>} {value}</button>)}</div></div>
      <div className="mistake-core-idea"><span className="mistake-core-icon"><BrainCircuit size={20}/></span><div><h3>核心纠错点</h3>
        {result.evidence.missing_rubric.length ? <p>{result.evidence.missing_rubric.join("；")}</p> : <p>本次评分已保存，可对照教材证据。</p>}
        {selected.review_material.expected_answer && <p><strong>参考答案：</strong>{selected.review_material.expected_answer}</p>}
        {sourcePages.length > 0 && <button type="button" onClick={() => onSource(sourcePages[0])}>回到教材原文 <ChevronRight size={16}/></button>}</div></div>
      <fieldset className="mistake-mastery-rating"><legend>现在掌握了吗？</legend>
        <button type="button" data-tone="danger" disabled={working} onClick={() => void setStatus("unreviewed")}><RotateCcw size={19}/><span><strong>还不会</strong><small>保留在待复习列表</small></span></button>
        <button type="button" data-tone="warning" disabled={working} onClick={() => void setStatus("reviewing")}><Clock3 size={19}/><span><strong>有点模糊</strong><small>继续巩固</small></span></button>
        <button type="button" data-tone="success" disabled={working} onClick={() => void setStatus("self_reported_mastered")}><CheckCircle2 size={19}/><span><strong>已掌握</strong><small>保存自我标记</small></span></button>
      </fieldset></section>}
    </main></div>;

  return <div className="screen-stack mistake-book-screen">
    {book && mistakes.length > 0 && <section className="mistake-overview" aria-labelledby="mistake-overview-title"><div className="mistake-overview-main">
      <div><span className="mistake-overview-icon"><CalendarClock size={22}/></span><div><p id="mistake-overview-title">当前教材 · 待复习</p>
        <strong>{reviewable}<small> 道</small></strong></div></div><Button onClick={() => beginReview()} disabled={!reviewable || loading || busy}>开始错题重做</Button></div>
      <div className="mistake-overview-stats" aria-label="错题掌握概览"><span><strong>{mistakes.length}</strong><small>全部错题</small></span>
        <span><strong>{repeated}</strong><small>重复作答</small></span><span><strong>{mastered}</strong><small>自报已掌握</small></span>
        <span><strong>{progress}%</strong><small>自报进度</small></span></div>
      <div className="mistake-overview-progress" aria-hidden="true"><span style={{ transform: `scaleX(${progress / 100})` }}/></div></section>}
    {book && <div className="mistake-toolbar"><label className="mistake-search"><Search size={18}/><span className="sr-only">搜索错题</span>
      <input type="search" value={query} placeholder="搜索题目或章节" onChange={event => setQuery(event.target.value)}/>
      {query && <button type="button" aria-label="清除错题搜索" onClick={() => setQuery("")}><X size={16}/></button>}</label>
      <div className="filter-row"><div className="mistake-filter-group" role="group" aria-label="按回访状态筛选">{filters.map(item => <button key={item.id}
        type="button" className={filter === item.id ? "active" : ""} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}</button>)}</div></div>
      {initialChapterId && <div className="mistake-filter-group" role="group" aria-label="错题范围"><button aria-pressed={!allChapters} onClick={() => setAllChapters(false)}>当前章节</button>
        <button aria-pressed={allChapters} onClick={() => setAllChapters(true)}>全部教材</button></div>}</div>}
    <div className="mistake-workspace" data-mistake-list-empty={filtered.length ? "false" : "true"}>
      {!!filtered.length && <div className="mistake-list" aria-label="错题列表"><div className="mistake-list-heading"><h2>错题记录</h2><span>{filtered.length} 道</span></div>
        {filtered.map(item => <button className="mistake-list-item" key={item.mistake_id} type="button" data-selected={selected?.mistake_id === item.mistake_id}
          aria-pressed={selected?.mistake_id === item.mistake_id} onClick={() => setSelectedId(item.mistake_id)}>
          <span className="mistake-list-item-topline"><span className="mistake-subject-badge">{item.chapter_title}</span>
            <span className="mistake-list-status" data-status={statusTone(item.review.status)}>{statusLabel(item.review.status)}</span></span>
          <strong>{item.practice_item.prompt}</strong><span className="mistake-list-point">上次评分 {score(item.attempt.score)}</span>
          <span className="mistake-list-meta"><span><Clock3 size={14}/>{item.attempt.at ? new Date(item.attempt.at).toLocaleDateString("zh-CN") : "最近记录"}</span>
            <span>已作答 {item.attempt_count} 次</span></span></button>)}</div>}
      {!book ? <Card className="mistake-card mistake-state-card"><span className="mistake-state-icon"><BookOpenCheck size={24}/></span><h3>还没有可以复习的错题</h3>
        <p>上传教材并完成一次作业诊断后，这里会显示真实卡点。</p><Button onClick={onUpload}>上传教材</Button></Card>
      : error ? <Card className="mistake-card mistake-state-card"><span className="mistake-state-icon mistake-state-icon-warning"><AlertCircle size={24}/></span>
        <h3>错题记录加载失败</h3><p>{error}</p><Button onClick={onReload}>重新加载</Button></Card>
      : loading ? <Card className="mistake-card mistake-state-card" aria-busy="true"><h3>正在读取错题记录</h3></Card>
      : selected ? <Card className="mistake-card mistake-detail-card"><div className="mistake-detail-header"><div className="mistake-detail-badges">
        <span className="mistake-subject-badge">{selected.chapter_title}</span><span className="mistake-error-count">已作答 {selected.attempt_count} 次</span></div>
        <h2>{selected.practice_item.prompt}</h2><div className="chip-row static"><Pill tone="purple">{statusLabel(selected.review.status)}</Pill></div></div>
        <div className="mistake-detail-content"><section className="mistake-detail-block"><span className="mistake-detail-block-icon"><RotateCcw size={18}/></span>
          <div><h3>上次作答</h3><p>{selected.attempt.answer || "未记录答案"}</p></div></section>
          <section className="mistake-detail-block mistake-detail-focus"><span className="mistake-detail-block-icon"><Target size={18}/></span>
            <div><h3>纠错重点</h3><p>{selected.review.reason || `上次评分 ${score(selected.attempt.score)}；重做后可记录具体错因。`}</p></div></section>
          {sourcePages.length > 0 && <section className="mistake-source-link"><BookOpenCheck size={19}/><div><h3>已连接教材原文</h3>
            <p>PDF 第 {sourcePages.join("、")} 页</p></div><button type="button" aria-label="查看教材原文" onClick={() => onSource(sourcePages[0])}><ChevronRight size={19}/></button></section>}
          <details className="demo-port-mistake-answer"><summary>查看参考答案</summary><p>{selected.review_material.expected_answer || "当前记录没有可展示的参考答案。"}</p></details>
          {actionError && <p role="alert">{actionError}</p>}{notice && <p role="status">{notice}</p>}
          <div className="mistake-actions"><div className="button-row"><Button icon={<RotateCcw size={18}/>} disabled={!selected.can_retry || busy} onClick={() => beginReview(selected)}>重做此题</Button>
            <Button variant="secondary" onClick={() => onCourse(selected.section_id || selected.chapter_id)}>打开章节课程</Button></div></div></div></Card>
      : <Card className="mistake-card mistake-state-card"><span className="mistake-state-icon"><CheckCircle2 size={24}/></span>
        <h3>{mistakes.length ? "当前筛选暂无错题" : "暂无后端错题记录"}</h3><p>{mistakes.length ? "调整筛选条件或查看全部教材。" : "完成作业诊断后，真实题目与教材证据会出现在这里。"}</p>
        {mistakes.length > 0 && <Button variant="secondary" onClick={() => { setFilter("all"); setQuery(""); setAllChapters(true); }}>查看全部错题</Button>}
      </Card>}
    </div></div>;
}
