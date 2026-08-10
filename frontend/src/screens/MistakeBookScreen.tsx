import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent
} from "react";
import {
  AlertCircle,
  BookOpenCheck,
  BrainCircuit,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  RotateCcw,
  Search,
  Target,
  X
} from "lucide-react";
import { runtimeConfig } from "../config/runtime";
import type { MistakeRecord } from "../types/api";
import { Button, Card, Pill } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { SlidingFilterGroup, useLocalMotionItem } from "../motion";

const allMistakesFilter = "全部";
const localReviewStoragePrefix = "bookcourse.mistake-mastery";

const mistakeFilterDefinitions = [
  { value: allMistakesFilter, label: allMistakesFilter, terms: [] as readonly string[] },
  { value: "meiosis", label: "减数分裂", terms: ["meiosis", "减数分裂", "同源染色体", "姐妹染色单体"] },
  { value: "inheritance", label: "遗传规律", terms: ["inheritance", "genetics", "遗传规律", "遗传"] }
] as const;

type MistakeMode = "overview" | "review";
type LocalMastery = "due" | "learning" | "mastered";
type MistakeReason = "知识盲区" | "审题疏忽" | "概念混淆" | "方法不熟";

const mistakeReasons: readonly MistakeReason[] = ["知识盲区", "审题疏忽", "概念混淆", "方法不熟"];

function normalizedText(value: string) {
  return value.trim().toLocaleLowerCase();
}

function errorCountFor(mistake: MistakeRecord) {
  return Math.max(1, mistake.error_count ?? 1);
}

function masteryFor(mistake: MistakeRecord, localMastery: Record<string, LocalMastery>): MistakeRecord["mastery"] {
  return localMastery[mistake.mistake_id]
    ?? mistake.mastery
    ?? (errorCountFor(mistake) >= 3 ? "repeated" : "due");
}

function subjectFor(mistake: MistakeRecord) {
  const content = normalizedText(`${mistake.question} ${mistake.knowledge_points.join(" ")}`);
  if (/english|grammar|语法|英语/.test(content)) return "英语";
  if (/physics|force|受力|物理/.test(content)) return "物理";
  if (/math|函数|方程|数学/.test(content)) return "数学";
  if (/chem|化学|反应/.test(content)) return "化学";
  if (/biology|meiosis|genetic|染色体|遗传|细胞|生物/.test(content)) return "生物";
  return "本课程";
}

function dateLabel(value?: string | null) {
  if (!value) return "最近记录";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(parsed);
}

function masteryLabel(mastery: MistakeRecord["mastery"]) {
  if (mastery === "mastered") return "已掌握";
  if (mastery === "repeated") return "反复出错";
  if (mastery === "learning") return "巩固中";
  return "待复习";
}

function loadLocalMastery(bookId: string): Record<string, LocalMastery> {
  try {
    const stored = window.localStorage.getItem(`${localReviewStoragePrefix}.${bookId}`);
    if (!stored) return {};
    const parsed = JSON.parse(stored) as Record<string, LocalMastery>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function MistakeBookScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const { go, showToast, uploadedFile } = useAppContext();
  const [mode, setMode] = useState<MistakeMode>("overview");
  const [filter, setFilter] = useState(allMistakesFilter);
  const [searchQuery, setSearchQuery] = useState("");
  const [mistakes, setMistakes] = useState<MistakeRecord[]>([]);
  const [loadingMistakes, setLoadingMistakes] = useState(false);
  const [mistakeError, setMistakeError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedMistakeId, setSelectedMistakeId] = useState<string | null>(null);
  const [detailMotionSelection, setDetailMotionSelection] = useState<string | null>(null);
  const [detailRevision, setDetailRevision] = useState(0);
  const [localMastery, setLocalMastery] = useState<Record<string, LocalMastery>>({});
  const [reviewAnswer, setReviewAnswer] = useState("");
  const [reviewRevealed, setReviewRevealed] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [selectedReason, setSelectedReason] = useState<MistakeReason | null>(null);
  const answerRef = useRef<HTMLTextAreaElement | null>(null);
  const filterOptions = mistakeFilterDefinitions.map(({ value, label }) => ({ value, label }));
  const activeFilter = mistakeFilterDefinitions.find((definition) => definition.value === filter) ?? mistakeFilterDefinitions[0];

  const filteredMistakes = useMemo(() => {
    const query = normalizedText(searchQuery);
    return mistakes.filter((mistake) => {
      const matchesKnowledgeFilter = filter === allMistakesFilter || mistake.knowledge_points.some((point) => {
        const normalizedPoint = normalizedText(point);
        return activeFilter.terms.some((term) => normalizedPoint.includes(normalizedText(term)));
      });
      if (!matchesKnowledgeFilter) return false;
      if (!query) return true;
      return normalizedText([
        mistake.question,
        mistake.answer,
        mistake.stuck_point,
        ...mistake.knowledge_points
      ].join(" ")).includes(query);
    });
  }, [activeFilter.terms, filter, mistakes, searchQuery]);

  const selectedMistake = filteredMistakes.find((mistake) => mistake.mistake_id === selectedMistakeId)
    ?? filteredMistakes[0]
    ?? null;
  const selectedIndex = selectedMistake
    ? filteredMistakes.findIndex((mistake) => mistake.mistake_id === selectedMistake.mistake_id)
    : -1;
  const reviewableMistakes = mistakes.filter((mistake) => masteryFor(mistake, localMastery) !== "mastered");
  const masteredCount = mistakes.filter((mistake) => masteryFor(mistake, localMastery) === "mastered").length;
  const repeatedCount = mistakes.filter((mistake) => (
    masteryFor(mistake, localMastery) === "repeated" || errorCountFor(mistake) > 1
  )).length;
  const masteryProgress = mistakes.length > 0 ? Math.round((masteredCount / mistakes.length) * 100) : 0;
  const topicSummary = Array.from(new Set(mistakes.flatMap((mistake) => mistake.knowledge_points))).slice(0, 3);
  const hasVisibleMistakeList = Boolean(uploadedFile && !mistakeError && !loadingMistakes && filteredMistakes.length > 0);
  const detailMotion = useLocalMotionItem(
    `mistake-detail:${detailMotionSelection ?? "initial"}:${detailRevision}`,
    "content",
    { animateInitial: false }
  );

  useEffect(() => {
    if (!uploadedFile) return;
    setLocalMastery(loadLocalMastery(uploadedFile.bookId));
  }, [uploadedFile]);

  useEffect(() => {
    if (!uploadedFile) return;
    try {
      window.localStorage.setItem(
        `${localReviewStoragePrefix}.${uploadedFile.bookId}`,
        JSON.stringify(localMastery)
      );
    } catch {
      // Mastery persistence is a convenience; storage failure must not block review.
    }
  }, [localMastery, uploadedFile]);

  useEffect(() => {
    if (!uploadedFile) return;
    let active = true;
    setLoadingMistakes(true);
    setMistakeError(null);
    bookcourseRepository
      .getMistakes(runtimeConfig.defaultUserId, uploadedFile.bookId)
      .then((records) => {
        if (!active) return;
        setMistakes(records);
        setSelectedMistakeId((current) => current && records.some((record) => record.mistake_id === current)
          ? current
          : records[0]?.mistake_id ?? null);
      })
      .catch((err) => {
        if (active) setMistakeError(err instanceof Error ? err.message : "错题记录加载失败");
      })
      .finally(() => {
        if (active) setLoadingMistakes(false);
      });
    return () => {
      active = false;
    };
  }, [bookcourseRepository, reloadKey, uploadedFile]);

  useEffect(() => {
    if (selectedMistakeId && !filteredMistakes.some((mistake) => mistake.mistake_id === selectedMistakeId)) {
      const nextMistakeId = filteredMistakes[0]?.mistake_id ?? null;
      setSelectedMistakeId(nextMistakeId);
      setDetailMotionSelection(nextMistakeId);
      setDetailRevision((current) => current + 1);
    }
  }, [filteredMistakes, selectedMistakeId]);

  function resetReviewFields() {
    setReviewAnswer("");
    setReviewRevealed(false);
    setReviewError(null);
    setSelectedReason(null);
  }

  function selectMistake(mistakeId: string) {
    if (mistakeId === selectedMistake?.mistake_id) return;
    setSelectedMistakeId(mistakeId);
    setDetailMotionSelection(mistakeId);
    setDetailRevision((current) => current + 1);
  }

  function startReview(mistakeId?: string) {
    const nextMistake = mistakes.find((mistake) => mistake.mistake_id === mistakeId)
      ?? reviewableMistakes[0]
      ?? mistakes[0];
    if (!nextMistake) return;
    setFilter(allMistakesFilter);
    setSearchQuery("");
    setSelectedMistakeId(nextMistake.mistake_id);
    setDetailMotionSelection(nextMistake.mistake_id);
    setDetailRevision((current) => current + 1);
    resetReviewFields();
    setMode("review");
  }

  function submitReview() {
    if (!reviewAnswer.trim()) {
      setReviewError("先写下你的答案，再查看纠错重点。");
      answerRef.current?.focus();
      return;
    }
    setReviewError(null);
    setReviewRevealed(true);
  }

  function markMastery(nextMastery: LocalMastery) {
    if (!selectedMistake) return;
    setLocalMastery((current) => ({ ...current, [selectedMistake.mistake_id]: nextMastery }));
    const nextMistake = filteredMistakes[selectedIndex + 1];
    const message = nextMastery === "mastered"
      ? "已标记为掌握，这道题会移出今日复习。"
      : nextMastery === "learning"
        ? "已加入短期巩固，稍后再复习一次。"
        : "已保留在待复习列表。";
    showToast(message, nextMastery === "mastered" ? "success" : "info");
    if (nextMistake) {
      setSelectedMistakeId(nextMistake.mistake_id);
      setDetailMotionSelection(nextMistake.mistake_id);
      setDetailRevision((current) => current + 1);
      resetReviewFields();
    } else {
      setMode("overview");
      resetReviewFields();
    }
  }

  function updateSearch(event: ChangeEvent<HTMLInputElement>) {
    setSearchQuery(event.target.value);
  }

  if (mode === "review" && selectedMistake) {
    const selectedMastery = masteryFor(selectedMistake, localMastery);
    const displayIndex = Math.max(1, selectedIndex + 1);
    return (
      <div className="mistake-book-screen mistake-review-screen">
        <section className="mistake-review-heading" aria-label="错题重做进度">
          <button className="mistake-inline-back" type="button" onClick={() => setMode("overview")}>
            <ChevronLeft size={19} aria-hidden="true" />
            返回错题集
          </button>
          <div className="mistake-review-progress-copy">
            <span>错题重做</span>
            <strong>{displayIndex} / {filteredMistakes.length}</strong>
          </div>
          <div
            className="mistake-review-progress"
            role="progressbar"
            aria-label={`错题复习进度 ${displayIndex}/${filteredMistakes.length}`}
            aria-valuemin={0}
            aria-valuemax={filteredMistakes.length}
            aria-valuenow={displayIndex}
          >
            <span style={{ transform: `scaleX(${filteredMistakes.length > 0 ? displayIndex / filteredMistakes.length : 0})` }} />
          </div>
        </section>

        <main className="mistake-review-workspace">
          <article className="mistake-review-question">
            <div className="mistake-review-meta">
              <span>{subjectFor(selectedMistake)} · {selectedMistake.knowledge_points[0] ?? "待复习"}</span>
              <span className="mistake-error-count">错 {errorCountFor(selectedMistake)} 次</span>
            </div>
            <h2>{selectedMistake.question}</h2>
            <div className="mistake-answer-field">
              <div>
                <label htmlFor="mistake-review-answer">写下你的答案</label>
                <button type="button" onClick={() => setReviewAnswer(selectedMistake.answer)}>填入上次答案</button>
              </div>
              <textarea
                ref={answerRef}
                id="mistake-review-answer"
                value={reviewAnswer}
                aria-describedby={reviewError ? "mistake-review-error" : undefined}
                aria-invalid={reviewError ? true : undefined}
                placeholder="先独立回忆，不急着看解析"
                onChange={(event) => setReviewAnswer(event.target.value)}
              />
              {reviewError ? <p id="mistake-review-error" className="mistake-review-error" role="alert"><AlertCircle size={16} aria-hidden="true" />{reviewError}</p> : null}
            </div>
            {!reviewRevealed ? (
              <Button className="mistake-review-submit" onClick={submitReview}>提交并对照</Button>
            ) : null}
          </article>

          {reviewRevealed ? (
            <section className="mistake-review-analysis" aria-live="polite">
              <div className="mistake-comparison">
                <div>
                  <span>我的新答案</span>
                  <p>{reviewAnswer}</p>
                </div>
                <div>
                  <span>上次答案</span>
                  <p>{selectedMistake.answer || "未记录"}</p>
                </div>
              </div>

              <div className="mistake-reflection">
                <h3>这次为什么会错？</h3>
                <div className="mistake-reason-options" aria-label="选择错因">
                  {mistakeReasons.map((reason) => (
                    <button
                      key={reason}
                      type="button"
                      aria-pressed={selectedReason === reason}
                      onClick={() => setSelectedReason(reason)}
                    >
                      {selectedReason === reason ? <Check size={15} aria-hidden="true" /> : null}
                      {reason}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mistake-core-idea">
                <span className="mistake-core-icon" aria-hidden="true"><BrainCircuit size={20} /></span>
                <div>
                  <h3>核心纠错点</h3>
                  <p>{selectedMistake.explanation ?? selectedMistake.stuck_point}</p>
                  {selectedMistake.correct_answer ? <p><strong>参考答案：</strong>{selectedMistake.correct_answer}</p> : null}
                  <button type="button" onClick={() => go("lesson")}>
                    回到教材原文 <ChevronRight size={16} aria-hidden="true" />
                  </button>
                </div>
              </div>

              <fieldset className="mistake-mastery-rating">
                <legend>现在掌握了吗？</legend>
                <button type="button" data-tone="danger" aria-pressed={selectedMastery === "due"} onClick={() => markMastery("due")}>
                  <RotateCcw size={19} aria-hidden="true" /><span><strong>还不会</strong><small>保留在今日复习</small></span>
                </button>
                <button type="button" data-tone="warning" aria-pressed={selectedMastery === "learning"} onClick={() => markMastery("learning")}>
                  <Clock3 size={19} aria-hidden="true" /><span><strong>有点模糊</strong><small>稍后再巩固一次</small></span>
                </button>
                <button type="button" data-tone="success" aria-pressed={selectedMastery === "mastered"} onClick={() => markMastery("mastered")}>
                  <CheckCircle2 size={19} aria-hidden="true" /><span><strong>已掌握</strong><small>移出今日复习</small></span>
                </button>
              </fieldset>
            </section>
          ) : null}
        </main>
      </div>
    );
  }

  return (
    <div className="screen-stack mistake-book-screen">
      {uploadedFile && !mistakeError && !loadingMistakes && mistakes.length > 0 ? (
        <section className="mistake-overview" aria-labelledby="mistake-overview-title">
          <div className="mistake-overview-main">
            <div>
              <span className="mistake-overview-icon" aria-hidden="true"><CalendarClock size={22} /></span>
              <div>
                <p id="mistake-overview-title">今日待复习</p>
                <strong>{reviewableMistakes.length}<small> 道</small></strong>
              </div>
            </div>
            <Button onClick={() => startReview()} disabled={mistakes.length === 0}>
              {reviewableMistakes.length > 0 ? "开始今日复习" : "再复习一轮"}
            </Button>
          </div>
          <div className="mistake-overview-stats" aria-label="错题掌握概览">
            <span><strong>{mistakes.length}</strong><small>全部错题</small></span>
            <span><strong>{repeatedCount}</strong><small>反复出错</small></span>
            <span><strong>{masteredCount}</strong><small>已经掌握</small></span>
            <span><strong>{masteryProgress}%</strong><small>掌握进度</small></span>
          </div>
          <div className="mistake-overview-progress" aria-hidden="true"><span style={{ transform: `scaleX(${masteryProgress / 100})` }} /></div>
        </section>
      ) : null}

      {uploadedFile && !mistakeError && !loadingMistakes && mistakes.length > 0 ? (
        <section className="mistake-insights" aria-label="薄弱知识点">
          <div>
            <Target size={18} aria-hidden="true" />
            <span><strong>优先复习</strong><small>从卡点最集中的知识开始</small></span>
          </div>
          <div className="mistake-insight-topics">
            {(topicSummary.length > 0 ? topicSummary : ["等待知识点分析"]).map((topic, index) => (
              <span key={topic}><i style={{ transform: `scaleX(${Math.max(.38, 1 - index * .22)})` }} />{topic}</span>
            ))}
          </div>
        </section>
      ) : null}

      {uploadedFile && !mistakeError && !loadingMistakes && mistakes.length > 0 ? (
        <div className="mistake-toolbar">
          <label className="mistake-search">
            <Search size={18} aria-hidden="true" />
            <span className="sr-only">搜索错题</span>
            <input value={searchQuery} placeholder="搜索题目或知识点" onChange={updateSearch} />
            {searchQuery ? (
              <button type="button" aria-label="清除错题搜索" onClick={() => setSearchQuery("")}><X size={16} aria-hidden="true" /></button>
            ) : null}
          </label>
          <div className="filter-row">
            <SlidingFilterGroup
              className="mistake-filter-group"
              value={filter}
              options={filterOptions}
              onChange={setFilter}
              ariaLabel="错题分类筛选"
            />
          </div>
        </div>
      ) : null}

      <div className="mistake-workspace" data-mistake-list-empty={hasVisibleMistakeList ? "false" : "true"}>
        {hasVisibleMistakeList ? (
          <div className="mistake-list" aria-label="错题列表">
            <div className="mistake-list-heading">
              <h2>错题记录</h2>
              <span>{filteredMistakes.length} 道</span>
            </div>
            {filteredMistakes.map((mistake) => {
              const mastery = masteryFor(mistake, localMastery);
              return (
                <button
                  className="mistake-list-item"
                  data-selected={mistake.mistake_id === selectedMistake?.mistake_id ? "true" : "false"}
                  type="button"
                  key={mistake.mistake_id}
                  aria-pressed={mistake.mistake_id === selectedMistake?.mistake_id}
                  onClick={() => selectMistake(mistake.mistake_id)}
                >
                  <span className="mistake-list-item-topline">
                    <span className="mistake-subject-badge">{subjectFor(mistake)}</span>
                    <span className="mistake-list-status" data-status={mastery}>{masteryLabel(mastery)}</span>
                  </span>
                  <strong>{mistake.question}</strong>
                  <span className="mistake-list-point">{mistake.knowledge_points[0] ?? "待复习"}</span>
                  <span className="mistake-list-meta">
                    <span><Clock3 size={14} aria-hidden="true" />{dateLabel(mistake.last_wrong_at)}</span>
                    <span>错 {errorCountFor(mistake)} 次</span>
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}

        {!uploadedFile ? (
          <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="mistake-card mistake-state-card">
            <span className="mistake-state-icon" aria-hidden="true"><BookOpenCheck size={24} /></span>
            <h3>还没有可以复习的错题</h3>
            <p>上传教材并完成一次作业诊断，系统会把真实卡点和对应原文整理到这里。</p>
            <Button onClick={() => go("upload")}>上传教材</Button>
          </Card>
        ) : mistakeError ? (
          <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="mistake-card mistake-state-card">
            <span className="mistake-state-icon mistake-state-icon-warning" aria-hidden="true"><AlertCircle size={24} /></span>
            <h3>错题记录加载失败</h3>
            <p>{mistakeError}</p>
            <Button onClick={() => setReloadKey((current) => current + 1)}>重新加载</Button>
          </Card>
        ) : loadingMistakes ? (
          <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="mistake-card mistake-state-card" aria-busy="true">
            <div className="mistake-loading-preview" aria-hidden="true"><span /><span /><span /></div>
            <h3>正在读取错题记录</h3>
            <p>正在连接作业诊断与教材来源，请稍候。</p>
          </Card>
        ) : selectedMistake ? (
          <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="mistake-card mistake-detail-card">
            <div className="mistake-detail-header">
              <div className="mistake-detail-badges">
                <span className="mistake-subject-badge">{subjectFor(selectedMistake)}</span>
                <span className="mistake-error-count">错 {errorCountFor(selectedMistake)} 次</span>
              </div>
              <h2>{selectedMistake.question}</h2>
              <div className="chip-row static">
                {(selectedMistake.knowledge_points.length > 0 ? selectedMistake.knowledge_points : ["待复习"]).map((point) => (
                  <Pill tone="purple" key={point}>{point}</Pill>
                ))}
              </div>
            </div>

            <div className="mistake-detail-content">
              <section className="mistake-detail-block">
                <span className="mistake-detail-block-icon" aria-hidden="true"><RotateCcw size={18} /></span>
                <div><h3>上次作答</h3><p>{selectedMistake.answer || "未记录答案"}</p></div>
              </section>
              <section className="mistake-detail-block mistake-detail-focus">
                <span className="mistake-detail-block-icon" aria-hidden="true"><Target size={18} /></span>
                <div><h3>纠错重点</h3><p>{selectedMistake.stuck_point}</p></div>
              </section>
              <section className="mistake-source-link">
                <BookOpenCheck size={19} aria-hidden="true" />
                <div>
                  <h3>已连接教材原文</h3>
                  <p>{selectedMistake.citation_ids.length} 条引用来源已记录，可回到章节上下文核对。</p>
                </div>
                <button type="button" aria-label="查看教材原文" onClick={() => go("lesson")}><ChevronRight size={19} aria-hidden="true" /></button>
              </section>
              <div className="mistake-actions">
                <div className="button-row">
                  <Button icon={<RotateCcw size={18} aria-hidden="true" />} onClick={() => startReview(selectedMistake.mistake_id)}>重做此题</Button>
                  <Button variant="secondary" onClick={() => go("lesson")}>查看原文</Button>
                </div>
                <Button variant="secondary" onClick={() => go("flashcards")}>用闪卡巩固</Button>
              </div>
            </div>
          </Card>
        ) : mistakes.length > 0 ? (
          <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="mistake-card mistake-state-card">
            <span className="mistake-state-icon" aria-hidden="true"><Search size={24} /></span>
            <h3>当前分类暂无错题</h3>
            <p>{searchQuery ? `没有找到与“${searchQuery}”相关的错题，可以换个关键词。` : `后端记录中没有匹配“${activeFilter.label}”的知识点，可切换分类查看其他错题。`}</p>
            <Button variant="secondary" onClick={() => { setFilter(allMistakesFilter); setSearchQuery(""); }}>查看全部错题</Button>
          </Card>
        ) : (
          <Card {...detailMotion.attributes} key={detailMotion.motionKey} className="mistake-card mistake-state-card">
            <span className="mistake-state-icon" aria-hidden="true"><CheckCircle2 size={24} /></span>
            <h3>暂无后端错题记录</h3>
            <p>完成一次作业诊断后，真实的题目、卡点和教材引用会自动出现在这里。</p>
            <Button onClick={() => go("assignment")}>去做一次诊断</Button>
          </Card>
        )}
      </div>
    </div>
  );
}
