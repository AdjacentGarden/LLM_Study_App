import { request } from "./transport";
import type { Flashcard } from "../types/api";
export interface DoubtDraft {
  book_id: string;
  question: string;
  chapter_id?: string;
  chapter_title?: string;
  excerpt?: string;
  pages?: number[];
}
export interface Doubt extends DoubtDraft {
  id: string;
  status: "open" | "resolved";
  note: string;
  due_at: number;
  created: number;
  updated: number;
}
export interface MemoryItem {
  card: Flashcard;
  course_id: string;
  chapter_title: string;
  chapter_id: string;
  due_at: string;
  due: boolean;
  last_rating: string;
  repetitions: number;
}
export interface MemorySnapshot {
  items: MemoryItem[];
  due_count: number;
  reviewed_count: number;
  delayed_count: number;
  week_recalled: number;
  week_checks: number;
  history: Array<{
    card_id: string;
    at: number;
    rating: string;
    gap_days: number;
    delayed: boolean;
    week_later: boolean;
  }>;
  now: string;
}
const post = <T>(path: string, data: object) =>
  request<T>(path, { method: "POST", body: JSON.stringify(data) });
export const retention = {
  doubts: (book: string) =>
    request<{ items: Doubt[]; now: number }>(
      `/api/learning/doubts?book_id=${encodeURIComponent(book)}`,
    ),
  save: (data: DoubtDraft) => post<Doubt>("/api/learning/doubts", data),
  update: (id: string, action: "resolve" | "reopen" | "snooze", note = "") =>
    post<Doubt>(`/api/learning/doubts/${encodeURIComponent(id)}`, {
      action,
      note,
    }),
  memory: (session: string) =>
    request<MemorySnapshot>(
      `/api/learning/memory/${encodeURIComponent(session)}`,
    ),
};
