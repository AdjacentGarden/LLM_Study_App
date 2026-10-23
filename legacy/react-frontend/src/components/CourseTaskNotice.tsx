export interface CourseWork {
  chapterId: string; status: string; error: string; retryable: boolean; jobId?: string;
}

export function CourseTaskNotice({ work, loading, onOpen, onCheck }: {
  work: CourseWork; loading: boolean; onOpen: () => void; onCheck: () => void;
}) {
  const exhausted = work.status === "failed" && !work.retryable;
  const message = work.status === "succeeded" ? "章节学习内容已准备好。"
    : exhausted ? "此任务已停止，本版本的尝试次数已用完。任务和学习记录已保留；请先检查服务状态，待可用的新版本就绪后再生成。"
    : work.status === "failed" || work.status === "paused" ? work.error
    : work.status === "checking" ? "正在查找已保存课程…"
    : "本章正在后台生成与审核，你可以继续浏览。";
  const label = work.status === "succeeded" ? "打开课程"
    : work.status === "failed" && work.retryable ? "重试本次任务"
    : exhausted ? "查看任务状态" : "继续查看";
  return <section className="connection-note" role="status" aria-live="polite">
    <span>{message}</span>
    {!loading && <button disabled={exhausted && !work.jobId} onClick={exhausted ? onCheck : onOpen}>{label}</button>}
  </section>;
}
