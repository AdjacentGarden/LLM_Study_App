import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpenCheck, Check, CheckCircle2, ClipboardCheck, Layers3, Lightbulb, RotateCcw, Search, Target } from "lucide-react";
import { request } from "../api/transport";
import { captureStorage } from "../components/bookContext";
import { AppProvider, type AppContextValue } from "../demo/context/AppContext";
import { FlashcardScreen } from "../demo/screens/FlashcardScreen";
import type { Flashcard as DemoFlashcard } from "../demo/types/api";
import { Button, Card, Pill } from "./DemoPrimitives";
import type { BookCatalogItem } from "../types/api";
import type { ChapterMode } from "./types";

type Supplement = {
  book_id: string; chapter_id: string; title: string; summary: string;
  cards: { id: string; front: string }[];
  questions: { id: string; prompt: string; choices: string[]; instruction: string; question_type: string }[];
};
type CheckResult = { correct: boolean | null; answer: string; explanation: string };
type LocalRecord = { answer: string; expected: string; explanation: string; correct: boolean | null; everWrong: boolean; attempts: number;
  status: "unreviewed" | "reviewing" | "mastered"; reason: string };
type LocalRecords = Record<string, LocalRecord>;

export function DemoSupplementTools({ book, chapterId, mode, onMode, onSource, onBack, onNotice }: {
  book: BookCatalogItem; chapterId: string; mode: ChapterMode; onMode: (mode: ChapterMode) => void;
  onSource: (page: number) => void; onBack: () => void; onNotice: (value: string) => void;
}) {
  const [content, setContent] = useState<Supplement | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const storage = useMemo(captureStorage, []);
  const key = `cloudpath.chapter-history:${book.book_id}:${chapterId}`;
  const [records, setRecords] = useState<LocalRecords>(() => {
    try { return JSON.parse(storage.safeGet(key) || "{}") as LocalRecords; } catch { return {}; }
  });
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState<CheckResult | null>(null);
  const [finished, setFinished] = useState(false);
  const [working, setWorking] = useState(false);
  const [actionError, setActionError] = useState("");
  const [mistakeId, setMistakeId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const started = useRef(Date.now());
  const path = `/api/books/${encodeURIComponent(book.book_id)}/supplementary-lessons/${encodeURIComponent(chapterId)}`;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setContent(null);
    void request<Supplement>(path, { signal: controller.signal }, 20_000).then(value => {
      if (!controller.signal.aborted && value.book_id === book.book_id && value.chapter_id === chapterId) setContent(value);
    }).catch(cause => { if (!controller.signal.aborted) setError((cause as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [book.book_id, chapterId, path, revision]);
  function saveRecord(id: string, next: LocalRecord) {
    setRecords(previous => { const updated = { ...previous, [id]: next }; storage.safeSet(key, JSON.stringify(updated)); return updated; });
  }
  function nextQuestion() {
    if (!content) return;
    if (index >= content.questions.length - 1) setFinished(true);
    else { setIndex(value => value + 1); setSelected([]); setAnswer(""); setResult(null); setActionError(""); started.current = Date.now(); }
  }
  async function submit() {
    const item = content?.questions[index];
    if (!item || working || result) return;
    const response = item.choices.length ? selected.map(value => item.choices[Number(value)]).filter(Boolean).join("、") : answer.trim();
    if (!response) { setActionError("请先作答。"); return; }
    setWorking(true); setActionError("");
    try {
      const next = await request<CheckResult>(`${path}/check`, { method: "POST", body: JSON.stringify({ question_id: item.id, answer: response }) }, 20_000);
      setResult(next);
      const prior = records[item.id];
      saveRecord(item.id, { answer: response, expected: next.answer, explanation: next.explanation, correct: next.correct,
        everWrong: !!prior?.everWrong || prior?.correct === false || next.correct === false, attempts: (prior?.attempts ?? 0) + 1,
        status: next.correct === false ? "unreviewed" : prior?.everWrong ? "mastered" : "reviewing", reason: prior?.reason ?? "" });
    } catch (cause) { setActionError(cause instanceof Error ? cause.message : "答案暂时无法核验，请重试。"); }
    finally { setWorking(false); }
  }
  function retry(id: string) {
    const position = content?.questions.findIndex(item => item.id === id) ?? -1;
    if (position < 0) return;
    setIndex(position); setSelected([]); setAnswer(""); setResult(null); setFinished(false); started.current = Date.now(); onMode("practice");
  }
  function assessShortAnswer(id: string, needsReview: boolean) {
    const prior = records[id];
    if (!prior) return;
    saveRecord(id, {
      ...prior,
      correct: needsReview ? false : prior.everWrong ? false : null,
      everWrong: needsReview || prior.everWrong,
      status: needsReview ? "unreviewed" : "mastered",
    });
  }
  if (loading) return <Card className="parse-empty-card" aria-busy="true"><p role="status">正在打开章节工具…</p></Card>;
  if (error || !content) return <Card className="parse-empty-card" role="alert"><h2>本节内容暂不可用</h2><p>{error || "请返回目录重试。"}</p>
    <Button onClick={() => setRevision(value => value + 1)}>重新读取</Button></Card>;

  if (mode === "cards") {
    const cards: DemoFlashcard[] = content.cards.map(card => ({ card_id: card.id, book_id: book.book_id, lesson_id: chapterId,
      chapter_id: chapterId, front: card.front, back: "", concept: content.title, source_chunk_ids: [], page_start: 0, page_end: 0,
      source_kind: "ai_supplement", due: "补充卡片", mastery: 0, reason: "核对后再选择记住程度。" }));
    const bridge = {
      activeChapterId: chapterId, generatedFlashcards: cards, generatedLessons: [],
      uploadedFile: { bookId: book.book_id, name: book.title, sizeBytes: 0, contentType: "application/pdf", uploadedAt: 0, origin: "remote-course" },
      demoPort: {
        revealFlashcard: async (id: string) => (await request<{ back: string }>(`${path}/cards/${encodeURIComponent(id)}/reveal`, { method: "POST" }, 20_000)).back,
        reviewFlashcard: async (id: string, rating: "again" | "good") => { storage.safeSet(`cloudpath.chapter-card:${book.book_id}:${chapterId}:${id}`, rating); return true; },
      },
      showToast: onNotice, back: onBack, go: () => onMode("reading"), openSourcePage: (target: { pageStart: number }) => onSource(target.pageStart),
    } as unknown as AppContextValue;
    return <AppProvider value={bridge}><FlashcardScreen/></AppProvider>;
  }

  if (mode === "practice") {
    const item = content.questions[index];
    if (finished) return <div className="screen-stack assignment-screen"><Card className="assignment-card assignment-exercise-card">
      <div className="assignment-exercise-kicker"><ClipboardCheck size={19}/><strong>作业诊断</strong></div>
      <h2 className="assignment-question">本节练习已完成</h2><p>已完成 {content.questions.length} 道补充练习；本节结果保存在当前浏览器账号下。</p>
      <div className="assignment-primary-action"><Button onClick={() => onMode("mistakes")}>查看错题集</Button>
        <Button variant="secondary" onClick={() => onMode("cards")}>复习闪卡</Button></div></Card></div>;
    if (!item) return <Card className="parse-empty-card"><h2>本节暂无练习</h2></Card>;
    const judgment = item.question_type === "judgment" || (item.choices.length === 2 && item.choices.every(value => /^(正确|错误|对|错)$/.test(value)));
    return <div className="screen-stack assignment-screen"><div className="assignment-workspace assignment-practice-workspace">
      <Card className="assignment-progress-card"><div className="assignment-progress-heading"><span className="assignment-progress-icon"><BookOpenCheck size={20}/></span>
        <div><small>本节练习</small><h2>{content.title}</h2></div><strong>{index + 1} / {content.questions.length}</strong></div>
        <div className="assignment-progress-track" role="progressbar" aria-valuemin={1} aria-valuemax={content.questions.length} aria-valuenow={index + 1}>
          <span style={{ width: `${(index + 1) / content.questions.length * 100}%` }}/></div></Card>
      <Card className="assignment-card assignment-exercise-card" data-assignment-type={judgment ? "judgment" : item.choices.length ? "choice" : "short-answer"}>
        <div className="assignment-exercise-kicker"><ClipboardCheck size={19}/><strong>{judgment ? "判断题" : item.choices.length ? "选择题" : "简答题"} · 第 {index + 1} 题</strong></div>
        <h2 className="assignment-question">{item.prompt}</h2>{item.instruction && <p className="assignment-exercise-instruction">{item.instruction}</p>}
        {!result && !!item.choices.length && <div className={judgment ? "assignment-judgment-options" : "assignment-choice-options"} role="group" aria-label="题目选项">
          {item.choices.map((option, optionIndex) => <button key={optionIndex} type="button" className={selected.includes(String(optionIndex)) ? "selected" : ""}
            aria-pressed={selected.includes(String(optionIndex))} onClick={() => { setSelected([String(optionIndex)]); setActionError(""); }}>
            <span className={judgment ? "" : "assignment-option-marker"}>{judgment ? <Check size={24}/> : String.fromCharCode(65 + optionIndex)}</span>
            <strong>{option}</strong></button>)}</div>}
        {!result && !item.choices.length && <div className="assignment-short-answer"><p className="assignment-short-hint"><Lightbulb size={19}/>
          <span>写下你的理解，再核对参考答案。</span></p><label className="answer-field"><span className="assignment-answer-label"><strong>你的答案</strong></span>
          <textarea value={answer} onChange={event => setAnswer(event.target.value)} rows={6} placeholder="在这里写下你的思路……"/></label></div>}
        {result && <div className="demo-port-assignment-feedback" role="status"><strong>{result.correct === null ? "请自行对照" : result.correct ? "回答正确" : "再巩固一下"}</strong>
          <p>参考答案：{result.answer}</p>{result.explanation && <p>{result.explanation}</p>}
          {result.correct === null && <div className="demo-port-self-check" role="group" aria-label="简答题自评">
            <button type="button" aria-pressed={records[item.id]?.status === "unreviewed"} onClick={() => assessShortAnswer(item.id, true)}>需要再复习</button>
            <button type="button" aria-pressed={records[item.id]?.status === "mastered"} onClick={() => assessShortAnswer(item.id, false)}>已掌握</button>
          </div>}</div>}
        {actionError && <p className="field-error" role="alert">{actionError}</p>}
        <div className="assignment-primary-action"><Button disabled={working || (!result && !answer.trim() && !selected.length)} loading={working}
          onClick={result ? nextQuestion : () => void submit()}>{result ? index === content.questions.length - 1 ? "查看诊断结果" : "下一题" : "提交答案"}</Button></div>
      </Card></div></div>;
  }

  const mistaken = content.questions.filter(item => records[item.id]?.correct === false || records[item.id]?.everWrong);
  const filtered = mistaken.filter(item => !query || item.prompt.includes(query));
  const selectedMistake = filtered.find(item => item.id === mistakeId) ?? filtered[0];
  return <div className="screen-stack mistake-book-screen">
    <section className="mistake-overview"><div className="mistake-overview-main"><div><span className="mistake-overview-icon"><RotateCcw size={22}/></span>
      <div><p>补充练习 · 待复习</p><strong>{mistaken.filter(item => records[item.id]?.status !== "mastered").length}<small> 道</small></strong></div></div>
      <Button disabled={!mistaken.length} onClick={() => retry(mistaken[0].id)}>开始错题重做</Button></div>
      <div className="mistake-overview-stats"><span><strong>{mistaken.length}</strong><small>全部错题</small></span>
        <span><strong>{mistaken.filter(item => records[item.id]?.attempts > 1).length}</strong><small>重复作答</small></span>
        <span><strong>{mistaken.filter(item => records[item.id]?.status === "mastered").length}</strong><small>已自报掌握</small></span></div></section>
    <div className="mistake-toolbar"><label className="mistake-search"><Search size={18}/><span className="sr-only">搜索错题</span>
      <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索错题"/></label></div>
    <div className="mistake-workspace">{filtered.length > 0 && <div className="mistake-list"><div className="mistake-list-heading"><h2>错题记录</h2><span>{filtered.length} 道</span></div>
      {filtered.map(item => <button key={item.id} type="button" className="mistake-list-item" data-selected={selectedMistake?.id === item.id}
        onClick={() => setMistakeId(item.id)}><span className="mistake-list-item-topline"><span className="mistake-subject-badge">{content.title}</span>
          <span className="mistake-list-status">{records[item.id]?.status === "mastered" ? "已掌握" : "待复习"}</span></span><strong>{item.prompt}</strong>
          <span className="mistake-list-point">已作答 {records[item.id]?.attempts} 次</span></button>)}</div>}
      {selectedMistake ? <Card className="mistake-card mistake-detail-card"><div className="mistake-detail-header"><Pill tone="purple">补充练习</Pill>
        <h2>{selectedMistake.prompt}</h2></div><div className="mistake-detail-content"><section className="mistake-detail-block"><span className="mistake-detail-block-icon"><RotateCcw size={18}/></span>
          <div><h3>此前错答</h3><p>{records[selectedMistake.id]?.answer}</p></div></section>
        <section className="mistake-detail-block mistake-detail-focus"><span className="mistake-detail-block-icon"><Target size={18}/></span>
          <div><h3>参考答案</h3><p>{records[selectedMistake.id]?.expected}</p></div></section>
        <div className="mistake-actions"><Button icon={<RotateCcw size={17}/>} onClick={() => retry(selectedMistake.id)}>重做此题</Button></div></div></Card>
        : <Card className="mistake-card mistake-state-card"><Layers3 size={24}/><h3>暂无错题</h3><p>完成本节作业诊断后，错题会保存在当前浏览器账号下。</p></Card>}
    </div>
  </div>;
}
