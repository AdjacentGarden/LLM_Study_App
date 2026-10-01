import type { BookCatalogItem, BookStructure, Profile } from "../types/api";
import { prioritizeChapters } from "./learningPlan";
import { Icon } from "./Icon";
import { ChapterMap } from "./ChapterMap";
import { splitReadableParagraphs } from "./textStructure";
import { BookCover } from "./LibraryShelf";

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
        <h2>{book ? "正在学习" : "我的教材"}</h2>
        {uploadEntry}
      </section>
      {book?.diagnostics_ready === false && !profile && (
        <p className="connection-note" role="status">
          部分学习内容正在准备，章节与答疑已可使用。
        </p>
      )}
      <section className="focus-session">
        <div className="reading-book">
          {book && <div className="reading-book-cover"><BookCover book={book} eager /></div>}
          <div className="reading-book-copy">
            <h2>{book?.title ?? "从一本书开始"}</h2>
            <p>{book ? `${book.chapter_count} 章 · ${book.page_count} 页` : "上传 PDF，开始学习"}</p>
            {book && <span className="reading-book-status"><span />{completed ? "个性化课程" : "待评估基础"}</span>}
          </div>
        </div>
        {first && <p className="next-chapter"><span>接下来</span>{first.chapter.title}</p>}
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
