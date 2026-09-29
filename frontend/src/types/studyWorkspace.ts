import type { CourseCitation, PublicPracticeItem } from "./api";

export type StudyTaskKind = "reading" | "practice" | "flashcards";
export type StudyTaskStatus = "todo" | "done";
export type MistakeReviewStatus = "unreviewed" | "reviewing" | "self_reported_mastered";

export interface StudyTask {
  historical?: boolean;
  task_id: string;
  kind: StudyTaskKind;
  chapter_id: string;
  chapter_title: string;
  section_id: string | null;
  section_title: string | null;
  title: string;
  estimated_minutes: number;
  status: StudyTaskStatus;
  completion_source: "self_report" | "activity" | null;
  activity_count: number;
  source_start_page: number | null;
  source_end_page: number | null;
}

export interface StudyPlan {
  minutes_per_day: number;
  minutes_source: "profile" | "default";
  days: { day_index: number; estimated_minutes: number; tasks: StudyTask[] }[];
  progress: { done: number; total: number; percent: number };
}

export interface StudyMistake {
  mistake_id: string;
  chapter_id: string;
  chapter_title: string;
  course_id: string;
  section_id: string | null;
  course_version: number | null;
  item_id: string;
  practice_item: PublicPracticeItem;
  attempt: { answer: string | null; score: number; scoring_confidence: number; at: string };
  review: { status: MistakeReviewStatus; reason: string | null; updated_at: string | null };
  review_material: { expected_answer: string | null; correct_option_ids: string[]; citations: CourseCitation[] };
  can_retry: boolean;
  attempt_count: number;
  latest_attempt: { score: number; at: string };
}

export interface StudyWorkspace {
  session_id: string;
  book_id: string;
  plan: StudyPlan;
  mistakes: StudyMistake[];
}
