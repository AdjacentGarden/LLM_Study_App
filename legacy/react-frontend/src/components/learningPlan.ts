import type { Chapter, Profile } from "../types/api";

export function chapterEstimate(profile: Profile | undefined, chapterId: string): number | null {
  const evidence = profile?.chapter_mastery[chapterId];
  if (!evidence || evidence.evidence_count === 0) return null;
  return Math.max(0, Math.min(1, evidence.tracked_mastery ?? evidence.alpha / (evidence.alpha + evidence.beta)));
}

export function prioritizeChapters(chapters: Chapter[], profile?: Profile) {
  return chapters.map((chapter) => ({ chapter, mastery: chapterEstimate(profile, chapter.chapter_id) }))
    .sort((a, b) => (a.mastery ?? 2) - (b.mastery ?? 2) || a.chapter.order - b.chapter.order);
}

export function masteryLabel(value: number | null): string {
  if (value === null) return "尚待诊断";
  return value < .5 ? "建议先补基础" : value < .75 ? "重点巩固" : "可以进阶";
}

export function chaptersToConsolidate(chapters: Chapter[], profile?: Profile) {
  return prioritizeChapters(chapters, profile)
    .filter(row => row.mastery !== null && row.mastery < .75);
}
