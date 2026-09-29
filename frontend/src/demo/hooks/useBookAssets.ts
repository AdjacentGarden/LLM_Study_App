import { useEffect, useState } from "react";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import type { ApiAsset } from "../types/api";

export function useBookAssets(bookId: string | null) {
  const bookcourseRepository = useBookCourseRepository();
  const [assets, setAssets] = useState<ApiAsset[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!bookId) return;
    let active = true;
    setLoading(true);

    bookcourseRepository
      .getAssets(bookId)
      .then((result) => {
        if (!active) return;
        setAssets(result);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "课程插图加载失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [bookcourseRepository, bookId]);

  return { assets, loading, error };
}
