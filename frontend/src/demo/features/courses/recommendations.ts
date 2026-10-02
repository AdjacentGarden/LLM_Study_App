import type { StudyPlan, StudyTask } from "../../types/api";
import {
  bookIdFromResourceId,
  dailyMinuteBudget,
  type LearnerPreferences,
  type Course,
  type PrimaryGoal,
  type CourseDiagnosis
  , type CourseResource
} from "./model";

export type RecommendationKind = "lesson" | "practice" | "mistakes" | "flashcards" | "notes" | "visual";

export type CourseRecommendation = {
  bookId: string;
  taskId: string;
  chapterId: string | null;
  title: string;
  taskType: string;
  kind: RecommendationKind;
  minutes: number;
  originalMinutes: number;
  segments: number[];
  reason: string;
};

function taskKind(task: StudyTask): RecommendationKind {
  const text = `${task.title} ${task.task_type}`;
  if (/错题|卡点/.test(text)) return "mistakes";
  if (/练习|测验|诊断|考试|限时/.test(text)) return "practice";
  if (/闪卡|复习|回忆/.test(text)) return "flashcards";
  if (/笔记|总结/.test(text)) return "notes";
  if (/图示|动画|视频/.test(text)) return "visual";
  return "lesson";
}

const goalPriority: Record<CourseDiagnosis["goals"][number], RecommendationKind[]> = {
  systematic: ["lesson", "practice", "flashcards"],
  exam: ["practice", "mistakes", "flashcards"],
  professional: ["lesson", "visual", "practice"],
  interest: ["lesson", "visual", "notes"],
  gaps: ["mistakes", "practice", "flashcards"]
};

const globalGoalPriority: Record<PrimaryGoal, RecommendationKind[]> = {
  systematic: ["lesson", "practice", "flashcards"],
  exam: ["practice", "mistakes", "flashcards"],
  growth: ["lesson", "practice", "notes"],
  interest: ["lesson", "visual", "notes"]
};

function rankKind(kind: RecommendationKind, diagnosis: CourseDiagnosis, globalGoal: PrimaryGoal) {
  let score = 0;
  const globalPosition = globalGoalPriority[globalGoal].indexOf(kind);
  if (globalPosition >= 0) score += 3 - globalPosition;
  diagnosis.goals.forEach((goal, index) => {
    const position = goalPriority[goal].indexOf(kind);
    if (position >= 0) score += Math.max(1, 12 - index * 2 - position * 3);
  });
  if (diagnosis.urgency === "urgent" && (kind === "practice" || kind === "mistakes")) score += 8;
  if (diagnosis.urgency === "relaxed" && (kind === "lesson" || kind === "notes")) score += 6;
  if (diagnosis.contentFoci.includes("exercises") && kind === "practice") score += 4;
  if (diagnosis.contentFoci.includes("notes") && kind === "notes") score += 4;
  if (diagnosis.contentFoci.includes("principles") && kind === "lesson") score += 3;
  if (diagnosis.contentFoci.includes("practice") && kind === "visual") score += 2;
  if (diagnosis.contentFoci.includes("keyPoints") && kind === "flashcards") score += 3;
  if (diagnosis.aids.includes("mistakes") && kind === "mistakes") score += 3;
  if (diagnosis.aids.includes("guidedNotes") && kind === "notes") score += 3;
  if (diagnosis.aids.includes("video") && kind === "visual") score += 3;
  if (diagnosis.reviews.includes("alongside") && kind === "practice") score += 3;
  if (diagnosis.reviews.includes("periodic") && kind === "flashcards") score += 3;
  if (diagnosis.reviews.includes("mistakes") && kind === "mistakes") score += 3;
  if (diagnosis.reviews.includes("notes") && kind === "notes") score += 3;
  return score;
}

function reasonFor(kind: RecommendationKind, diagnosis: CourseDiagnosis) {
  if (diagnosis.urgency === "urgent" && (kind === "practice" || kind === "mistakes")) return "紧迫学习：先抓练习和薄弱点";
  if (diagnosis.goals.includes("exam") && kind === "practice") return "应试备考：优先练习";
  if (diagnosis.goals.includes("gaps") && kind === "mistakes") return "查漏补缺：先看卡点";
  if (diagnosis.contentFoci.includes("notes") && kind === "notes") return "内容侧重：整理笔记";
  if (diagnosis.reviews.includes("periodic") && kind === "flashcards") return "复习偏好：定期回忆";
  if (diagnosis.urgency === "relaxed") return "按轻松节奏继续学习";
  return "根据课程目标推荐";
}

function splitMinutes(minutes: number, fragmented: boolean) {
  if (!fragmented) return [minutes];
  const count = Math.ceil(minutes / 10);
  const base = Math.floor(minutes / count);
  return Array.from({ length: count }, (_, index) => base + (index < minutes % count ? 1 : 0));
}

export function reviewIntervalDays(diagnosis: CourseDiagnosis) {
  if (diagnosis.reviews.includes("free")) return null;
  return diagnosis.urgency === "urgent" ? 1 : diagnosis.urgency === "steady" ? 3 : 7;
}

export function preferredToolIds(diagnosis: CourseDiagnosis): Array<"assignment" | "flashcards" | "mistakes" | "notes"> {
  const order: Array<"assignment" | "flashcards" | "mistakes" | "notes"> = [];
  const add = (value: typeof order[number]) => { if (!order.includes(value)) order.push(value); };
  for (const goal of diagnosis.goals) {
    if (goal === "exam" || goal === "gaps") { add("assignment"); add("mistakes"); }
    if (goal === "systematic") add("flashcards");
  }
  for (const focus of diagnosis.contentFoci) {
    if (focus === "exercises") add("assignment");
    if (focus === "notes") add("notes");
    if (focus === "keyPoints") add("flashcards");
  }
  for (const aid of diagnosis.aids) {
    if (aid === "mistakes") add("mistakes");
    if (aid === "guidedNotes") add("notes");
  }
  for (const review of diagnosis.reviews) {
    if (review === "alongside") add("assignment");
    if (review === "periodic") add("flashcards");
    if (review === "mistakes") add("mistakes");
    if (review === "notes") add("notes");
  }
  (["assignment", "flashcards", "mistakes", "notes"] as const).forEach(add);
  return order;
}

export function buildCourseRecommendations(
  course: Course,
  preferences: LearnerPreferences | null,
  plans: ReadonlyMap<string, StudyPlan>,
  resources: readonly CourseResource[] = []
): { budget: number; recommendations: CourseRecommendation[]; reviewDays: number | null } {
  const budget = dailyMinuteBudget(preferences);
  const globalGoal = preferences?.primaryGoal ?? "systematic";
  const ranked: Array<{ bookId: string; task: StudyTask; bookOrder: number; taskOrder: number; score: number; kind: RecommendationKind }> = [];
  course.resourceIds.forEach((resourceId, bookOrder) => {
    const bookId = bookIdFromResourceId(resourceId, resources);
    if (!bookId) return;
    plans.get(bookId)?.tasks.forEach((task, taskOrder) => {
      if (task.status === "done") return;
      const kind = taskKind(task);
      ranked.push({ bookId, task, bookOrder, taskOrder, kind, score: rankKind(kind, course.diagnosis, globalGoal) });
    });
  });
  ranked.sort((a, b) => b.score - a.score || a.bookOrder - b.bookOrder || a.task.day - b.task.day || a.taskOrder - b.taskOrder);
  let remaining = budget;
  const recommendations: CourseRecommendation[] = [];
  for (const candidate of ranked) {
    if (remaining < 5) break;
    const minutes = Math.min(Math.max(5, candidate.task.minutes), remaining);
    remaining -= minutes;
    recommendations.push({
      bookId: candidate.bookId,
      taskId: candidate.task.task_id,
      chapterId: candidate.task.chapter_id ?? null,
      title: candidate.task.title,
      taskType: candidate.task.task_type,
      kind: candidate.kind,
      minutes,
      originalMinutes: candidate.task.minutes,
      segments: splitMinutes(minutes, course.diagnosis.timePattern === "fragmented"),
      reason: reasonFor(candidate.kind, course.diagnosis)
    });
  }
  return { budget, recommendations, reviewDays: reviewIntervalDays(course.diagnosis) };
}
