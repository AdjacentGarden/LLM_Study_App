import type { BookCatalogItem, BookStructure, Profile } from "../types/api";
import { prioritizeChapters } from "./learningPlan";
import { Icon } from "./Icon";
import { ChapterMap } from "./ChapterMap";

interface Props {
  children?: React.ReactNode;
  uploadEntry?: React.ReactNode;
  book: BookCatalogItem | null;
  structure: BookStructure | null;
  profile?: Profile;
  completed: boolean;
  diagnosed: number;
  busy: boolean;
  onStart: () => void;
  onContinue: () => void;
  onCourse: (chapterId: string, tab?: "guide" | "cards") => void;
}

export function LearningHome({
  book,
  structure,
  profile,
  completed,
  diagnosed,
  busy,
  onStart,
  onContinue,
  onCourse,
  children,
  uploadEntry,
}: Props) {
  const chapters = structure?.chapters ?? [];
  const ranked = prioritizeChapters(chapters, profile);
  const priorities = ranked.filter(({ mastery }) => mastery !== null);
  const first = priorities[0];
  return (
    <div className="page-stack home-studio">
      <div className="home-welcome">
        <div>
          <span className="kicker">
            {completed ? "你的今日路径" : "先找到起点，再开始阅读"}
          </span>
          <h2>
            {completed ? "继续走一小段，就很好。" : "这本书，会按你的节奏展开。"}
          </h2>
        </div>
      </div>
      <section className="home-start-choice" aria-label="开始学习">
        <div>
          <span className="kicker">两种开始方式</span>
          <strong>{book ? "继续今天的课，或读一本新书" : "上传一本书，生成你的专属课程"}</strong>
        </div>
        {uploadEntry}
      </section>
      {book?.diagnostics_ready === false && !profile && (
        <p className="connection-note" role="status">
          诊断题正在准备与校验。现在可以先查看下方全书主线，或前往「答疑」针对这本书提问。
        </p>
      )}
      <section className="focus-session">
        <div className="session-label">
          <span>
            <Icon name="spark" size={16} />
            {completed ? "为你挑选的下一课" : "找到你的学习起点"}
          </span>
          <span>
            <Icon name="clock" size={14} />
            {profile?.constraints.minutes_per_day ?? 30} 分钟/天
          </span>
        </div>
        <div className="session-focus-heading">
          <h2>
            {first
              ? first.chapter.title
              : completed
                ? "每一章，都按你的起点学"
                : "先认识你，再定制课程。"}
          </h2>
          <img src="/assets/brand/cloud-mascot-home.png" alt="" />
        </div>
        <p>
          {first
            ? "根据你的作答，这一章值得优先巩固。"
            : "选一选目标，做几道由浅入深的题，让学习从合适的难度开始。"}
        </p>
        {first && (
          <div className="session-mastery">
            <div>
              <span>当前掌握估计</span>
              <b>
                {Math.round((first.mastery ?? 0) * 100)}
                <small>%</small>
              </b>
            </div>
            <div className="mastery-track">
              <i
                style={{ width: `${Math.round((first.mastery ?? 0) * 100)}%` }}
              />
            </div>
          </div>
        )}
        {first ? (
          <div className="session-actions">
            <button
              className="primary"
              disabled={busy}
              onClick={() => onCourse(first.chapter.chapter_id)}
            >
              开始这一课 <Icon name="arrow" />
            </button>
            <button
              className="session-card-action"
              aria-label="练习推荐章节闪卡"
              disabled={busy}
              onClick={() => onCourse(first.chapter.chapter_id, "cards")}
            >
              <Icon name="cards" />
              <span>闪卡</span>
            </button>
          </div>
        ) : (
          !completed && (
            <button
              className="primary"
              disabled={busy || !book}
              onClick={profile ? onContinue : onStart}
            >
              {busy ? "正在准备…" : profile ? "继续我的诊断" : "开始选择题诊断"}
              <Icon name="arrow" />
            </button>
          )
        )}
        {!completed && (
          <small className="session-note">
            {profile
              ? `已完成 ${diagnosed} 道题 · 进度已保存`
              : "无需写长答案，也不是一场排名考试"}
          </small>
        )}
      </section>
      {children && <div className="home-companions">{children}</div>}
      <ChapterMap
        chapters={chapters}
        profile={profile}
        completed={completed}
        busy={busy}
        onCourse={onCourse}
      />
      {structure?.summary && (
        <details className="book-summary">
          <summary>读之前，看看全书主线</summary>
          <p>{structure.summary}</p>
        </details>
      )}
    </div>
  );
}
