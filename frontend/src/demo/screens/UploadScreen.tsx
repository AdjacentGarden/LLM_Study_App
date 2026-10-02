import { useRef, useState } from "react";
import { FileText, Plus, X } from "lucide-react";
import { Button } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { acceptedCourseFileTypes, validateCourseFile } from "./shared";

export function UploadScreen() {
  const { courses, importCourseFile, go } = useAppContext();
  const picker = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    setBusy(true); setError(null);
    try {
      courses.startDraft(undefined, { suggestedName: files[0]?.name.replace(/\.[^.]+$/, "") ?? "我的课程" });
      const errors: string[] = [];
      for (const file of files) { try { await importCourseFile(file); } catch (cause) { errors.push(`${file.name}：${cause instanceof Error ? cause.message : "整理失败"}`); } }
      setFiles([]);
      if (errors.length) setError(errors.join("；")); else go("courseSetup");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "资料保存失败"); }
    finally { setBusy(false); }
  }
  return <div className="screen-stack upload-sheet-screen upload-flow-screen"><section className="upload-sheet-card upload-flow-primary">
    <input ref={picker} type="file" multiple accept={acceptedCourseFileTypes} className="hidden-file-input" onChange={(event) => {
      const selected = [...(event.target.files ?? [])]; event.target.value = "";
      const invalid = selected.map(validateCourseFile).find(Boolean);
      if (invalid) { setError(invalid); return; }
      setFiles((current) => [...current, ...selected.filter((file) => !current.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified))]); setError(null);
    }} />
    <button className="upload-add-tile" type="button" disabled={busy} onClick={() => picker.current?.click()} aria-label="选择课程资料"><span className="upload-add-icon"><Plus size={38} /></span></button>
    <h3>添加课程资料</h3><p>支持 PDF、图片、Office 文件和文本。每份文件会分别整理并保留原文。</p>
    <div className="course-upload-files">{files.map((file, index) => <div key={`${file.name}:${index}`}><FileText size={20} /><span>{file.name}</span><button type="button" disabled={busy} aria-label={`移除 ${file.name}`} onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}><X size={18} /></button></div>)}</div>
    {error ? <p className="status-error-copy" role="alert">{error}</p> : null}
    <Button disabled={!files.length || busy} loading={busy} onClick={() => void save()}>{busy ? "正在整理…" : "保存资料并继续"}</Button>
    {courses.state.draft?.resourceIds.length ? <Button variant="secondary" disabled={busy} onClick={() => go("courseSetup")}>继续设置课程</Button> : null}
  </section></div>;
}
