import { useEffect, useMemo, useState } from "react";
import { useAppContext } from "../../context/AppContext";
import { useBookCourseRepository } from "../../context/BookCourseRepositoryContext";
import { runtimeConfig } from "../../config/runtime";
import type { StudyPlan } from "../../types/api";
import { courseSourceBookIds } from "./model";

export function useCoursePlans(courseId?: string) {
  const [revision, setRevision] = useState(0);
  const { courses, sourceSummaries, currentStudyPlan, loadedBookId } = useAppContext();
  const repository = useBookCourseRepository();
  const bookIds = [...new Set(courses.state.courses.filter((course) => !courseId || course.id === courseId).flatMap((course) => courseSourceBookIds(course, courses.state.resources)))].filter((id) => sourceSummaries.some((source) => source.book_id === id && source.status === "ready"));
  const key = bookIds.join("|");
  const [state, setState] = useState<{ key: string; plans: Map<string, StudyPlan>; errors: string[] }>({ key: "", plans: new Map(), errors: [] });
  useEffect(() => {
    let active = true;
    void Promise.allSettled(bookIds.map((id) => repository.getStudyPlan(id, runtimeConfig.defaultUserId))).then((results) => {
      if (!active) return;
      const plans = new Map<string, StudyPlan>(); const errors: string[] = [];
      results.forEach((result, index) => { if (result.status === "fulfilled") plans.set(bookIds[index], result.value); else errors.push(bookIds[index]); });
      setState({ key, plans, errors });
    });
    return () => { active = false; };
  }, [key, repository, revision]);
  const plans = useMemo(() => {
    const next = new Map(state.key === key ? state.plans : []);
    if (loadedBookId && currentStudyPlan && bookIds.includes(loadedBookId)) next.set(loadedBookId, currentStudyPlan);
    return next;
  }, [state, key, currentStudyPlan, loadedBookId]);
  return { plans, loading: state.key !== key, errors: state.key === key ? state.errors : [], refresh: () => setRevision((value) => value + 1) };
}
