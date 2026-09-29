import { useEffect, useState } from "react";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import type { JobStatusResponse } from "../types/api";

export function useParseJob(jobId: string | null) {
  const bookcourseRepository = useBookCourseRepository();
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    if (!jobId) return;

    const currentJobId = jobId;
    let active = true;
    let timer: number | undefined;

    async function poll() {
      try {
        setLoading(true);
        const result = await bookcourseRepository.getJob(currentJobId);
        if (!active) return;
        setJob(result);
        setError(null);
        if (result.status !== "done" && result.status !== "failed") {
          timer = window.setTimeout(poll, 1200);
        }
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "解析任务状态获取失败");
        timer = window.setTimeout(poll, 2000);
      } finally {
        if (active) setLoading(false);
      }
    }

    void poll();

    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [bookcourseRepository, jobId, retryKey]);

  return {
    job,
    error,
    loading,
    retry: () => setRetryKey((value) => value + 1)
  };
}
