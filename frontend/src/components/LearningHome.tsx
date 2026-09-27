import type { BookCatalogItem, BookStructure, Profile } from "../types/api";
import { prioritizeChapters } from "./learningPlan";
import { Icon } from "./Icon";
import { ChapterMap } from "./ChapterMap";
import { splitReadableParagraphs } from "./textStructure";

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
  const summaryParagraphs = splitReadableParagraphs(structure?.summary ?? "");
  return (
    <div className="page-stack home-studio">
      <section className="home-start-choice" aria-label="开始学习">
        <div>
          <span className="kicker">导入教材</span>
          <strong>{book ? "PDF 或扫描版" : "导入后自动解析章节"}</strong>
        </div>
        {uploadEntry}
      </section>
      {book?.diagnostics_ready === false && !profile && (
        <p className="connection-note" role="status">
          部分学习内容正在准备，章节与答疑已可使用。
        </p>
      )}
      <section className="focus-session">
        <div className="session-label">
          <span>
            <Icon name="spark" size={16} />
            {completed ? "推荐章节" : "学习诊断"}
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
                ? "章节学习"
                : "完成基础评估"}
          </h2>
        </div>
        <p>
          {first
            ? "建议优先学习"
            : "通过选择题生成章节优先级与内容难度。"}
        </p>
        {first && (
          <div className="session-mastery">
            <div>
              <span>掌握估计</span>
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
              开始学习 <Icon name="arrow" />
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
              {busy ? "正在准备…" : profile ? "继续诊断" : "开始诊断"}
              <Icon name="arrow" />
            </button>
          )
        )}
        {!completed && (
          <small className="session-note">
            {profile
              ? `已完成 ${diagnosed} 题`
              : "选择题 · 约 3 分钟"}
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
          <summary>全书概览</summary>
          <div className="book-summary-body">
            {summaryParagraphs.map((paragraph, index) => (
              <p key={`${index}-${paragraph.slice(0, 16)}`}>{paragraph}</p>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
