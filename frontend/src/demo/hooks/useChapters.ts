import { useEffect, useState } from "react";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import type { ApiChapter, ChapterUpdate } from "../types/api";

export function useChapters(bookId: string | null) {
  const bookcourseRepository = useBookCourseRepository();
  const [chapters, setChapters] = useState<ApiChapter[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!bookId) return;
    let active = true;
    setLoading(true);
    bookcourseRepository
      .getChapters(bookId)
      .then((result) => {
        if (!active) return;
        setChapters(result);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "章节加载失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [bookcourseRepository, bookId, reloadKey]);

  return { chapters, loading, error, retry: () => setReloadKey((value) => value + 1) };
}

export function useUpdateChapter(bookId: string | null) {
  const bookcourseRepository = useBookCourseRepository();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function updateChapter(chapterId: string, payload: ChapterUpdate) {
    if (!bookId) throw new Error("bookId is required");
    setLoading(true);
    try {
      const result = await bookcourseRepository.updateChapter(bookId, chapterId, payload);
      setError(null);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : "章节保存失败";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }

  return { updateChapter, loading, error };
}
