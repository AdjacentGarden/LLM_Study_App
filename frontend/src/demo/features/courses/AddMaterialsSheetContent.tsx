import { useRef, useState } from "react";
import { BookOpen, Check, FilePlus2, Plus } from "lucide-react";
import { useAppContext } from "../../context/AppContext";
import { acceptedCourseFileTypes } from "../../screens/shared";
import { bookIdFromResourceId, localResourceId, sourceResourceId } from "./model";

export function AddMaterialsSheetContent({ courseId }: { courseId: string }) {
  const { courses, sourceSummaries, importCourseFile, showToast } = useAppContext();
  const course = courses.state.courses.find((item) => item.id === courseId);
  const picker = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!course) return <p role="alert">课程已不存在，请重新选择课程。</p>;
  const sources = sourceSummaries.filter((source) => !source.book_id.startsWith("book_local_"));
  const choices = [...sources.map((source) => ({ id: sourceResourceId(source.book_id), bookId: source.book_id, name: source.title, status: source.status === "ready" ? "可开始学习" : "待整理" })), ...courses.state.resources.map((resource) => ({ id: localResourceId(resource.id), bookId: resource.bookId, name: resource.name, status: resource.status === "ready" ? "可开始学习" : resource.error ?? "原文件已保留" }))];
  async function add(id: string, bookId?: string | null) {
    try {
      if (id.startsWith("local:")) await courses.addExistingResource(courseId, id);
      else if (bookId) await courses.addSource(courseId, bookId);
      showToast("资料已添加到课程");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "添加失败"); }
  }
  return <div className="course-materials-sheet">
    <p className="book-switcher-helper">添加到《{course.name}》。每份资料保留自己的原文和学习记录。</p>
    <input ref={picker} type="file" multiple accept={acceptedCourseFileTypes} hidden onChange={(event) => {
      const files = [...(event.target.files ?? [])]; event.target.value = "";
      if (!files.length) return;
      setBusy(true); setError(null);
      void (async () => {
        const errors: string[] = [];
        for (const file of files) {
          try { await importCourseFile(file, courseId); }
          catch (cause) { errors.push(`${file.name}：${cause instanceof Error ? cause.message : "整理失败"}`); }
        }
        setError(errors.length ? errors.join("；") : null);
        if (!errors.length) showToast("资料已添加，可以继续学习");
        setBusy(false);
      })();
    }} />
    <button className="learning-add-file" type="button" disabled={busy} onClick={() => picker.current?.click()}><FilePlus2 size={20} />{busy ? "正在整理资料…" : "上传文件或教材"}</button>
    {busy ? <div className="course-import-progress" aria-live="polite">{courses.state.resources.filter((item) => item.status === "processing" && course.resourceIds.includes(localResourceId(item.id))).map((item) => <p key={item.id}>{item.name} · {item.progress ?? 0}%</p>)}</div> : null}
    {error ? <p className="status-error-copy" role="alert">{error}</p> : null}
    <h3>已有资料</h3>
    <div className="course-space-picker">{choices.map((choice) => {
      const selected = course.resourceIds.includes(choice.id) || Boolean(choice.bookId && course.resourceIds.some((id) => bookIdFromResourceId(id, courses.state.resources) === choice.bookId));
      return <button className={`learning-resource-option ${selected ? "selected" : ""}`} type="button" key={choice.id} disabled={selected} onClick={() => add(choice.id, choice.bookId)}><BookOpen size={20} /><span><strong>{choice.name}</strong><small>{selected ? "已在当前课程" : choice.status}</small></span>{selected ? <Check size={18} /> : <Plus size={18} />}</button>;
    })}</div>
  </div>;
}
