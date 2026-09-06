import { useEffect, useState } from "react";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import type { LearningState, StudyPlan, StudyPlanRequest, StudyTaskUpdate } from "../types/api";

export function useStudyPlan(bookId: string | null, userId = "anonymous") {
  const bookcourseRepository = useBookCourseRepository();
  const [plan, setPlan] = useState<StudyPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!bookId) return;
    let active = true;
    setLoading(true);
    bookcourseRepository
      .getStudyPlan(bookId, userId)
      .then((result) => {
        if (!active) return;
        setPlan(result);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Study plan failed to load");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [bookcourseRepository, bookId, reloadKey, userId]);

  async function create(payload: StudyPlanRequest) {
    if (!bookId) throw new Error("bookId is required");
    setLoading(true);
    try {
      const result = await bookcourseRepository.createStudyPlan(bookId, { ...payload, user_id: payload.user_id ?? userId });
      setPlan(result);
      setError(null);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Study plan generation failed";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }

  async function patchTask(taskId: string, payload: StudyTaskUpdate) {
    setLoading(true);
    try {
      const updated = await bookcourseRepository.patchStudyTask(taskId, payload);
      setPlan((current) => {
        if (!current) return current;
        const tasks = current.tasks.map((task) => (task.task_id === updated.task_id ? updated : task));
        const exists = tasks.some((task) => task.task_id === updated.task_id);
        return { ...current, tasks: exists ? tasks : [...tasks, updated] };
      });
      setReloadKey((value) => value + 1);
      setError(null);
      return updated;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Study task update failed";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }

  return { plan, create, patchTask, loading, error, retry: () => setReloadKey((value) => value + 1) };
}

export function useLearningState(userId: string | null) {
  const bookcourseRepository = useBookCourseRepository();
  const [state, setState] = useState<LearningState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!userId) return;
    let active = true;
    setLoading(true);
    bookcourseRepository
      .getLearningState(userId)
      .then((result) => {
        if (!active) return;
        setState(result);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Learning state failed to load");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [bookcourseRepository, reloadKey, userId]);

  return { state, loading, error, retry: () => setReloadKey((value) => value + 1) };
}
