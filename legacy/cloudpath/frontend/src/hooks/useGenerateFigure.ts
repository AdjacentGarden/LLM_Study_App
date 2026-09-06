import { useEffect, useRef, useState } from "react";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import type { ImageGenerationJobResponse, ImageGenerationRequest } from "../types/api";

export function useGenerateFigure() {
  const bookcourseRepository = useBookCourseRepository();
  const [job, setJob] = useState<ImageGenerationJobResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingJobIdRef = useRef<string | null>(null);
  const lastRequestRef = useRef<{ lessonId: string; payload: ImageGenerationRequest } | null>(null);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    let active = true;
    const timer = window.setTimeout(() => {
      bookcourseRepository
        .getImageGenerationJob(job.job_id)
        .then((result) => {
          if (!active) return;
          setJob(result);
          if (result.status === "done" || result.status === "failed") {
            pendingJobIdRef.current = null;
          }
        })
        .catch((err) => {
          if (!active) return;
          setError(err instanceof Error ? err.message : "AI 示意图任务状态获取失败");
        });
    }, 1200);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [bookcourseRepository, job]);

  async function generateForLesson(lessonId: string, payload: ImageGenerationRequest) {
    if (pendingJobIdRef.current || loading || (job && job.status !== "done" && job.status !== "failed")) {
      return job;
    }
    lastRequestRef.current = { lessonId, payload };
    setLoading(true);
    try {
      const result = await bookcourseRepository.generateLessonFigure(lessonId, payload);
      if (result.status !== "done" && result.status !== "failed") {
        pendingJobIdRef.current = result.job_id;
      }
      setJob(result);
      setError(null);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : "AI 示意图生成失败";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }

  async function refresh(jobId: string) {
    const result = await bookcourseRepository.getImageGenerationJob(jobId);
    if (result.status === "done" || result.status === "failed") {
      pendingJobIdRef.current = null;
    }
    setJob(result);
    return result;
  }

  async function retry() {
    if (!lastRequestRef.current) throw new Error("No image generation request to retry");
    if (job?.status !== "failed") return job;
    setJob(null);
    pendingJobIdRef.current = null;
    return generateForLesson(lastRequestRef.current.lessonId, lastRequestRef.current.payload);
  }

  return { generateForLesson, refresh, retry, job, loading, error };
}
