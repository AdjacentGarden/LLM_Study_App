import { useEffect, useState } from "react";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import type { AssignmentSubmitRequest, DiagnosisResponse, MistakeRecord } from "../types/api";

export function useAssignmentDiagnosis() {
  const bookcourseRepository = useBookCourseRepository();
  const [diagnosis, setDiagnosis] = useState<DiagnosisResponse | null>(null);
  const [mistakeRecorded, setMistakeRecorded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submitAndDiagnose(assignmentId: string, payload: AssignmentSubmitRequest) {
    setLoading(true);
    try {
      const submission = await bookcourseRepository.submitAssignment(assignmentId, payload);
      const result = await bookcourseRepository.diagnoseAssignment(assignmentId, submission.submission_id);
      setDiagnosis(result);
      setMistakeRecorded(result.mistake_recorded);
      setError(null);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Assignment diagnosis failed";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }

  return { submitAndDiagnose, diagnosis, mistakeRecorded, loading, error };
}

export function useMistakes(userId: string | null, bookId?: string | null) {
  const bookcourseRepository = useBookCourseRepository();
  const [mistakes, setMistakes] = useState<MistakeRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!userId) return;
    let active = true;
    setLoading(true);
    bookcourseRepository
      .getMistakes(userId, bookId ?? undefined)
      .then((result) => {
        if (!active) return;
        setMistakes(result);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Mistake records failed to load");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [bookcourseRepository, bookId, reloadKey, userId]);

  return { mistakes, loading, error, retry: () => setReloadKey((value) => value + 1) };
}
