import { request } from "./transport";
export interface Anchor {
  book_id: string;
  chapter_id?: string;
  chapter_title?: string;
  excerpt?: string;
  pages?: number[];
}
export interface InkPoint {
  x: number;
  y: number;
  p: number;
}
export interface InkStroke {
  points: InkPoint[];
  color: "#243148" | "#7655c9" | "#23836e";
  width: number;
}
export interface Suggestion {
  original: string;
  kind: string;
  suggestion: string;
  evidence: string;
  page: number;
}
export interface StudioResult {
  phase?: string;
  provisional?: boolean;
  title?: string;
  visual_scope?: string;
  visual_mode?: "illustration" | "diagram";
  explanation?: string;
  points?: string[];
  caution?: string;
  review?: string;
  transcript?: string;
  uncertain?: string[];
  summary?: string;
  suggestions?: Suggestion[];
  polished?: string;
  evidence?: Array<{ page: number; text: string }>;
}
export interface InkNote extends Anchor {
  id: string;
  revision: number;
  title: string;
  input_mode?: "ink" | "voice";
  strokes: InkStroke[];
  audio_ready?: boolean;
  audio_mime?: string;
  audio_duration_seconds?: number;
  audio_sha256?: string;
  updated?: number;
  edition?: (StudioResult & { job_id: string }) | null;
}
export interface StudioJob {
  id: string;
  book_id: string;
  kind: "image" | "video" | "recognize" | "improve" | "complete";
  status: string;
  result: StudioResult;
  error: string;
  created: number;
  note_id?: string;
  revision?: number;
  excerpt: string;
  chapter_title: string;
  asset_url?: string | null;
}
export interface StudioCaps {
  media_notice?: string;
  image: boolean;
  video: boolean;
  notes_ai: boolean;
  voice_notes: boolean;
  draft_scope: string;
}
const post = <T>(url: string, data: object) =>
  request<T>(url, { method: "POST", body: JSON.stringify(data) });
export const studioApi = {
  caps: () => request<StudioCaps>("/api/studio/capabilities"),
  notes: (book: string) =>
    request<{ items: InkNote[] }>(
      `/api/studio/notes?book_id=${encodeURIComponent(book)}`,
    ),
  note: (id: string) =>
    request<InkNote>(`/api/studio/notes/${encodeURIComponent(id)}`),
  save: (note: InkNote) => {
    const { updated: _, edition: __, ...data } = note;
    return post<InkNote>("/api/studio/notes", data);
  },
  audio: (note: InkNote, blob: Blob, duration: number) =>
    request<InkNote>(`/api/studio/notes/${encodeURIComponent(note.id)}/audio`, {
      method: "PUT",
      body: blob,
      headers: {
        "Content-Type": blob.type || "audio/webm",
        "X-Note-Revision": String(note.revision),
        "X-Audio-Duration": String(Math.max(0, duration)),
      },
    }),
  audioUrl: (note: InkNote) =>
    `/api/studio/notes/${encodeURIComponent(note.id)}/audio?v=${note.revision}`,
  jobs: (book: string) =>
    request<{ items: StudioJob[] }>(
      `/api/studio/jobs?book_id=${encodeURIComponent(book)}`,
    ),
  media: (
    anchor: Anchor,
    kind: "image" | "video",
    goal: string,
    level: string,
    id: string,
  ) =>
    post<StudioJob>("/api/studio/media", {
      ...anchor,
      kind,
      goal,
      level,
      request_id: id,
      consent: true,
    }),
  analyze: (
    note: InkNote,
    action: "recognize" | "improve" | "complete",
    transcript: string,
    id: string,
  ) =>
    post<StudioJob>(`/api/studio/notes/${note.id}/analyze`, {
      revision: note.revision,
      request_id: id,
      action,
      transcript,
      consent: true,
    }),
  edition: (note: InkNote, job: string, accepted: boolean) =>
    post<{ ok: boolean }>(`/api/studio/notes/${note.id}/edition`, {
      revision: note.revision,
      job_id: job,
      accepted,
    }),
};
export const studioId = () =>
  `studio_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
export const pendingStudio = (job: StudioJob) =>
  ["queued", "planning", "submitting", "polling", "reviewing"].includes(
    job.status,
  );
