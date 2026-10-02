import { useState } from "react";
import { Check, ChevronRight, LibraryBig, Plus } from "lucide-react";
import { Button } from "../../components/ui";
import { useAppContext } from "../../context/AppContext";
import { preferredCourseSource } from "../../features/courses/selectors";
import { courseCoverImageUrl } from "../shared";

export function BookSwitcherSheetContent() {
  const { courses, sourceSummaries, closeSheet, selectCourse, go, showToast } = useAppContext();
  const [openingId, setOpeningId] = useState<string | null>(null);
  async function chooseCourse(courseId: string) {
    setOpeningId(courseId);
    try {
      if (await selectCourse(courseId)) { closeSheet(); go("study"); }
    } catch (error) { showToast(error instanceof Error ? error.message : "课程暂时无法打开", "warning"); }
    finally { setOpeningId(null); }
  }
  return <div className="book-switcher-sheet">
    <p className="book-switcher-helper">切换后会恢复这门课程上次打开的资料和小节。</p>
    <div className="book-switcher-list">{courses.state.courses.map((course) => {
      const selected = course.id === courses.state.activeCourseId;
      const source = preferredCourseSource(course, courses.state.resources, sourceSummaries);
      return <button className={`book-switcher-row ${selected ? "is-selected" : ""}`} type="button" key={course.id} disabled={Boolean(openingId)} onClick={() => void chooseCourse(course.id)}>
        <img src={courseCoverImageUrl(source?.bookId ?? "")} alt="" />
        <span><strong>{course.name}</strong><small>{openingId === course.id ? "正在打开…" : `${course.resourceIds.length} 份资料`}</small></span>
        {selected ? <Check size={19} aria-label="当前课程" /> : <ChevronRight size={19} aria-hidden="true" />}
      </button>;
    })}</div>
    {courses.state.courses.length === 0 ? <p className="book-switcher-empty">还没有课程，创建课程并添加资料后即可学习。</p> : null}
    <div className="book-switcher-actions">
      <Button icon={<Plus size={18} />} onClick={() => { try { courses.startDraft(); closeSheet(); go("courseSetup"); } catch (error) { showToast(error instanceof Error ? error.message : "创建失败", "warning"); } }}>创建新课程</Button>
      <Button variant="secondary" icon={<LibraryBig size={18} />} onClick={() => { closeSheet(); go("library"); }}>管理全部课程</Button>
    </div>
  </div>;
}
