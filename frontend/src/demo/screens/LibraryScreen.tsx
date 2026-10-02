import { useState } from "react";
import { BookOpen, Plus, Settings, Trash2 } from "lucide-react";
import { Button, Card, ProgressBar } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { CourseCardMotion } from "../motion";
import { courseProgress, courseSources, preferredCourseSource } from "../features/courses/selectors";
import { useCoursePlans } from "../features/courses/useCoursePlans";
import { courseCoverImageUrl } from "./shared";

export function LibraryScreen() {
  const { courses, sourceSummaries, selectCourse, go, showToast, clearLoadedCourse, clearCourseSession } = useAppContext();
  const { plans } = useCoursePlans();
  const [opening, setOpening] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  function create() { courses.startDraft(); go("courseSetup"); }
  async function open(id: string) {
    setOpening(id);
    try { if (await selectCourse(id)) go("study"); }
    finally { setOpening(null); }
  }
  async function remove(id: string) {
    try {
      if (id === courses.state.activeCourseId) { clearLoadedCourse(); clearCourseSession(); }
      await courses.removeCourse(id); setConfirmDelete(null); showToast("课程已移除，原资料和笔记仍保留");
    } catch (error) { showToast(error instanceof Error ? error.message : "移除失败", "warning"); }
  }
  return <div className="screen-stack library-screen">
    <div className="course-library-heading"><span>{courses.state.courses.length} 门课程</span><Button icon={<Plus size={18} />} onClick={create}>创建课程</Button></div>
    {courses.state.courses.length === 0 ? <Card className="course-library-empty"><BookOpen size={30} /><h2>还没有课程</h2><p>创建课程，再添加教材或其他学习文件。</p><Button onClick={create}>创建第一门课程</Button></Card> : null}
    <div className="course-library-list">{courses.state.courses.map((course, index) => {
      const sources = courseSources(course, courses.state.resources, sourceSummaries);
      const primary = preferredCourseSource(course, courses.state.resources, sourceSummaries);
      const directoryOnly = primary?.source?.content_mode === "directory";
      const readyCount = sources.filter((source) => source.status === "ready" && source.source?.content_mode !== "directory").length;
      const progress = courseProgress(sources.flatMap((source) => source.bookId && plans.has(source.bookId) ? [plans.get(source.bookId)!] : []));
      return <CourseCardMotion key={course.id} bookId={course.id} index={index}>{(motion) => <Card {...motion} className="course-library-card">
        <button className="course-library-open" type="button" disabled={Boolean(opening)} onClick={() => void open(course.id)}>
          <img src={primary?.source?.cover_url ?? courseCoverImageUrl(primary?.bookId ?? "")} alt="" /><span><strong>{course.name}</strong><small>{directoryOnly ? "演示课程 · 目录预览" : `${sources.length} 份资料 · ${readyCount} 份可学习`}</small><small>{opening === course.id ? "正在打开…" : primary?.name ?? "添加资料后开始学习"}</small></span>
        </button>
        {!directoryOnly ? <ProgressBar value={progress} label={`课程完成 ${progress}%`} /> : null}
        <div className="course-library-actions"><button type="button" onClick={() => { void selectCourse(course.id).then((selected) => { if (selected) go("courseDetail"); }); }}><Settings size={16} />课程资料与偏好</button><button type="button" aria-label={`删除课程 ${course.name}`} onClick={() => setConfirmDelete(course.id)}><Trash2 size={16} /></button></div>
        {confirmDelete === course.id ? <div className="course-library-delete-confirm"><p>移除这门课程？资料文件和已有笔记会保留。</p><button type="button" onClick={() => remove(course.id)}>确认移除课程</button><button type="button" onClick={() => setConfirmDelete(null)}>取消</button></div> : null}
      </Card>}</CourseCardMotion>;
    })}</div>
  </div>;
}
