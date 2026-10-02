import { useCallback, useEffect, useRef, useState } from "react";
import type { StudyRepository } from "../services/contracts";
import type { MistakeReviewStatus, StudyTaskStatus, StudyWorkspace } from "../types/studyWorkspace";

/** Persisted planning and mistake review belong to the current learning session. */
export function useStudyWorkspace(repository: StudyRepository, sessionId: string | null, bookId: string | null, revision: unknown) {
  const identity = `${sessionId ?? ""}:${bookId ?? ""}`;
  const current = useRef(identity);
  current.current = identity;
  const requestId = useRef(0);
  const writing = useRef(false);
  const [workspace, setWorkspace] = useState<StudyWorkspace | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    const ticket = ++requestId.current;
    if (!sessionId || !bookId) { setWorkspace(null); setLoading(false); setError(""); return false; }
    setLoading(true); setError("");
    try {
      const next = await repository.learning.workspace(sessionId);
      if (current.current !== identity || requestId.current !== ticket) return false;
      if (next.book_id !== bookId || next.session_id !== sessionId) throw new Error("学习记录与当前教材不匹配，请重新打开教材。");
      setWorkspace(next);
      return true;
    } catch (failure) {
      if (current.current === identity && requestId.current === ticket) setError((failure as Error).message);
      return false;
    } finally {
      if (current.current === identity && requestId.current === ticket) setLoading(false);
    }
  }, [repository, sessionId, bookId, identity]);

  useEffect(() => {
    writing.current = false; setBusy(false);
    return () => { ++requestId.current; };
  }, [identity]);
  useEffect(() => { void reload(); }, [reload, revision]);

  const mutate = useCallback(async (write: (id: string) => Promise<unknown>) => {
    if (!sessionId || !bookId || writing.current) return false;
    writing.current = true; setBusy(true); setError("");
    try {
      await write(sessionId);
      if (current.current !== identity) return false;
      return await reload();
    } catch (failure) {
      if (current.current === identity) setError(`${(failure as Error).message} 可以刷新核对已保存的状态，再重试。`);
      return false;
    } finally {
      if (current.current === identity) { writing.current = false; setBusy(false); }
    }
  }, [sessionId, bookId, identity, reload]);

  const setTask = useCallback((taskId: string, status: StudyTaskStatus) =>
    mutate(id => repository.learning.setTask(id, taskId, status)), [repository, mutate]);
  const setMistakeStatus = useCallback((mistakeId: string, status: MistakeReviewStatus, reason?: string | null) =>
    mutate(id => repository.learning.setMistake(id, mistakeId, status, reason)), [repository, mutate]);

  return {
    workspace: workspace?.session_id === sessionId && workspace.book_id === bookId ? workspace : null,
    loading, busy, error, reload, setTask, setMistakeStatus,
  };
}
