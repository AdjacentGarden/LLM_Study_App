import type { CourseSourceSummary, StudyPlan } from "../../types/api";
import { bookIdFromResourceId, courseSourceBookIds, localResourceId, type Course, type CourseResource, type CourseState } from "./model";

export function courseRagScope(state: CourseState, bookId: string) {
  const course = state.courses.find((item) => item.id === state.activeCourseId);
  const bookIds = courseSourceBookIds(course, state.resources);
  return course && bookIds.includes(bookId) ? { course_id: course.id, book_ids: bookIds } : {};
}

export function courseSources(course: Course | null | undefined, resources: readonly CourseResource[], summaries: readonly CourseSourceSummary[]) {
  return (course?.resourceIds ?? []).map((id) => {
    const local = resources.find((resource) => localResourceId(resource.id) === id);
    const bookId = bookIdFromResourceId(id, resources);
    const source = summaries.find((item) => item.book_id === bookId);
    return { id, bookId, local, source, name: local?.name ?? source?.title ?? "资料暂时不可用", status: source?.status ?? local?.status ?? "pending" };
  });
}

export function preferredCourseSource(course: Course, resources: readonly CourseResource[], summaries: readonly CourseSourceSummary[]) {
  const sources = courseSources(course, resources, summaries);
  const usable = sources.filter((item) => item.bookId && (item.status === "ready" || item.status === "needs_review"));
  return usable.find((item) => item.id === course.activeResourceId) ?? usable[0] ?? null;
}

export function courseLocationKey(courseId: string | null | undefined, bookId: string) {
  return courseId ? `${courseId}::${bookId}` : bookId;
}

export function courseProgress(plans: readonly StudyPlan[]) {
  const tasks = plans.flatMap((plan) => plan.tasks);
  return tasks.length ? Math.round(tasks.filter((task) => task.status === "done").length / tasks.length * 100) : 0;
}
