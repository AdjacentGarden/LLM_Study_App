export type ResponseType =
  | "single_choice"
  | "multiple_choice"
  | "short_answer"
  | "explanation"
  | "self_report";

export interface MasteryPosterior {
  alpha: number;
  beta: number;
  evidence_count: number;
  tracked_mastery: number | null;
}

export interface DiagnosticItem {
  item_id: string;
  chapter_id: string;
  knowledge_point_labels: string[];
  prompt: string;
  options: string[];
  response_type: ResponseType;
  estimated_seconds: number;
}

export interface Turn {
  turn_id: string;
  phase: string;
  message: string;
  question: string | null;
  response_type: ResponseType | null;
  options: Array<{ id: string; label: string }>;
  item: DiagnosticItem | null;
  why_asked: string | null;
  progress: number;
}

export interface Profile {
  user_id: string;
  book_id: string;
  goal: string;
  declared_background: string;
  background_level: string;
  focus_chapter_ids: string[];
  profile_confidence: number;
  misconception_candidates: string[];
  constraints: { minutes_per_day: number };
  chapter_mastery: Record<string, MasteryPosterior>;
  knowledge_mastery: Record<string, MasteryPosterior>;
  diagnostic_observations: unknown[];
  flashcard_reviews: Record<string, {
    algorithm: string;
    due_at: string | null;
    interval_days: number;
    last_rating: string | null;
    stability: number | null;
    difficulty: number | null;
  }>;
}

export interface InterviewResponse {
  session_id: string;
  phase: string;
  turn: Turn;
  profile: Profile;
}

export interface BookCatalogItem {
  cover_url?: string | null;
  diagnostics_ready?: boolean;
  book_id: string;
  title: string;
  status: string;
  page_count: number | null;
  chapter_count: number;
  summary: string;
}

export interface LearningRecords {
  book_id:string;
  knowledge:Array<{id:string;title:string;chapter:string;mastery:number;evidence_count:number;pages:number[]}>;
  evidence:Array<{id:string;title:string;kind:string;chapter:string;score:number;confidence:number;seconds:number;at:string;answer:string|null;pages:number[]}>;
  flashcards:Array<{id:string;front:string;back:string|null;chapter:string;rating:string|null;due_at:string|null;reviewed_at:string|null;pages:number[]}>;
}

export type CommunityKind = "book" | "flashcards" | "note";
export interface SharedCard { id?:string; front:string; back:string; pages:number[] }
export interface LibraryResource {
  id:string; kind:"flashcards"|"note"; book_id:string; title:string;
  content:{body?:string;cards?:SharedCard[]}; source_post:string|null; created:number;
}
export interface CommunityPost {
  id:string; kind:CommunityKind; book_id:string; title:string; description:string;
  content:{body?:string;cards?:SharedCard[]}; mine:boolean; author:string;
  in_library:boolean; downloads:number; created:number; book:BookCatalogItem;
}
export interface ShareCandidate { course_id:string; title:string; cards:SharedCard[] }
export interface ShareRequest {
  kind:CommunityKind; book_id:string; title?:string; description?:string; rights_confirmed:true;
  resource_id?:string; session_id?:string; course_id?:string; card_ids?:string[];
}

export interface Citation {
  page_number: number;
  quote: string;
}

export interface Chapter {
  chapter_id: string;
  order: number;
  title: string;
  start_page: number;
  end_page: number;
  summary: string;
  knowledge_points: string[];
  evidence: Citation[];
}

export interface BookStructure {
  title: string;
  summary: string;
  source_page_count: number;
  chapters: Chapter[];
}

export interface CourseCitation extends Citation { block_id?: string }
export interface LessonSection { title: string; content: string; purpose: string; citations: CourseCitation[] }
export interface CourseKnowledgePoint { point_id: string; title: string; explanation: string; importance: string; mastery: number; state: string; citations: CourseCitation[] }
export interface Flashcard { card_id: string; point_id: string; front: string; back: string; reason_for_user: string; citations: CourseCitation[] }
export interface PublicPracticeItem { item_id: string; point_id: string; prompt: string; response_type: string; options: string[]; estimated_seconds: number }
export interface Course {
  course_id: string;
  version: number;
  chapter_id: string;
  chapter_title: string;
  decision: { depth: "foundation" | "standard" | "advanced"; explanation: string; emphasis: string[]; scaffolds: string[] };
  opening: string;
  summary: string;
  original_reading: LessonSection[];
  knowledge_points: CourseKnowledgePoint[];
  flashcards: Flashcard[];
  worked_examples: LessonSection[];
  checkpoint_questions: string[];
  practice_items: PublicPracticeItem[];
  estimated_minutes: number;
  created_at: string;
  unresolved_source_warnings: string[];
}

export interface EvidenceResult { score: number; scoring_confidence: number; needs_follow_up: boolean; matched_rubric: string[]; missing_rubric: string[] }
export interface CourseActivity { duplicate: boolean; evidence: EvidenceResult; profile: Profile; course_stale: boolean; review_state: { algorithm: string; interval_days: number; due_at: string; last_rating: string; stability: number | null; difficulty: number | null } | null }

export interface QAResult {
  semantic_checked?: boolean;
  status: "supported" | "insufficient";
  answer: string;
  confidence: number;
  evidence_pages: number[];
  insufficiency_reason: string | null;
  claims: Array<{ text: string; citations: Citation[] }>;
}
export interface UserProfile { nickname:string; age:number|null; bio:string; revision:number; avatar_url:string|null }
export interface UserProfileUpdate { nickname:string; age:number|null; bio:string; revision:number; avatar_data_url?:string; reset_avatar?:boolean }
