import type {
  BookCatalogItem,
  BookStructure,
  Course,
  CourseActivity,
  InterviewResponse,
  LearningRecords,
  CommunityPost, LibraryResource, ShareCandidate, ShareRequest,
  Profile,
  QAResult,
  Turn,
  UserProfile, UserProfileUpdate,
  UploadResponse, BookStatus,
} from "../types/api";

import { request } from "./transport";

const post = <T>(path: string, body?: object) =>
  request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

export const api = {
  uploadBook:(file:File)=>{const body=new FormData();body.append("file",file);return request<UploadResponse>("/api/books",{method:"POST",body},600_000);},
  bindUpload:(bookId:string)=>post<{ok:boolean}>(`/api/library/books/${bookId}/bind-upload`),
  processBook:(bookId:string)=>post<BookStatus>(`/api/books/${bookId}/process`),
  retryBook:(bookId:string)=>post<BookStatus>(`/api/books/${bookId}/process/retry`),
  bookStatus:(bookId:string)=>request<BookStatus>(`/api/books/${bookId}/status`,{},30_000),
  buildStructure:(bookId:string)=>post<BookStructure>(`/api/books/${bookId}/structure`),
  buildDiagnostics:(bookId:string)=>post<{ready:boolean}>(`/api/books/${bookId}/diagnostics`),
  claimBook:(bookId:string)=>post<BookCatalogItem>(`/api/library/books/${bookId}/claim`),
  userProfile:()=>request<UserProfile>("/api/user/profile"),
  saveUserProfile:(body:UserProfileUpdate)=>post<UserProfile>("/api/user/profile",body),
  learningRecords:(sessionId:string)=>request<LearningRecords>(`/api/interviews/${sessionId}/learning-records`),
  books: () => request<BookCatalogItem[]>("/api/library", {}, 30_000),
  removeBook:(bookId:string)=>post<{ok:boolean}>(`/api/library/books/${bookId}/remove`),
  restoreBook:(bookId:string)=>post<{ok:boolean}>(`/api/library/books/${bookId}/restore`),
  resources:()=>request<LibraryResource[]>("/api/library/resources"),
  archiveResource:(id:string,archived=true)=>post<{ok:boolean}>(`/api/library/resources/${id}/archive?archived=${archived}`),
  saveNote:(data:{book_id:string;title:string;body:string;resource_id?:string})=>post<LibraryResource>("/api/library/notes",data),
  community:(kind:string,search:string,page=0)=>request<{items:CommunityPost[];has_more:boolean}>(`/api/community?kind=${encodeURIComponent(kind)}&search=${encodeURIComponent(search)}&page=${page}`),
  shareCandidates:(sessionId:string)=>request<ShareCandidate[]>(`/api/community/share-candidates/${sessionId}`),
  share:(data:ShareRequest)=>post<{post_id:string;status:"shared"|"already_shared"}>("/api/community/share",data),
  checkShared:(id:string)=>request<{already_owned:boolean;similar_titles:string[];book_id:string;method:string}>(`/api/community/${id}/check`),
  acquire:(id:string)=>post<{status:"added"|"already_owned";book_id:string;kind:string;resource_id:string|null}>(`/api/community/${id}/acquire`),
  withdraw:(id:string)=>post<{ok:boolean}>(`/api/community/${id}/withdraw`),
  structure: (bookId: string) => request<BookStructure>(`/api/books/${bookId}/structure`),
  start: (bookId: string) =>
    post<InterviewResponse>("/api/interviews/start", {
      user_id: `web_${Math.random().toString(36).slice(2, 10)}`,
      book_id: bookId,
    }),
  resume: (sessionId: string) => request<InterviewResponse>(`/api/interviews/${sessionId}`),
  answerProfile: (sessionId: string, answer: string, selectedOptionIds: string[]) =>
    post<InterviewResponse>(`/api/interviews/${sessionId}/profile`, {
      answer,
      selected_option_ids: selectedOptionIds,
    }),
  next: (sessionId: string) => post<InterviewResponse>(`/api/interviews/${sessionId}/next`),
  respond: (
    sessionId: string,
    body: {
      item_id: string;
      answer: string;
      selected_option_ids: string[];
      confidence: number;
      response_seconds: number;
      hints_used: number;
      revisions: number;
    },
  ) =>
    post<{ evidence: unknown; next_turn: Turn; profile: Profile }>(
      `/api/interviews/${sessionId}/respond`,
      body,
    ),
  confirm: (sessionId: string, confirmed = true) =>
    post<InterviewResponse>(`/api/interviews/${sessionId}/confirm`, { confirmed }),
  compileCourse: (sessionId: string, chapterId: string) =>
    post<Course>(`/api/interviews/${sessionId}/courses/${chapterId}`),
  getCourse: (sessionId: string, chapterId: string) =>
    request<Course>(`/api/interviews/${sessionId}/courses/${chapterId}`),
  practice: (
    sessionId: string,
    courseId: string,
    itemId: string,
    body: object,
  ) => post<CourseActivity>(`/api/interviews/${sessionId}/courses/${courseId}/practice/${itemId}`, body),
  reviewFlashcard: (
    sessionId: string,
    courseId: string,
    cardId: string,
    body: object,
  ) => post<CourseActivity>(`/api/interviews/${sessionId}/courses/${courseId}/flashcards/${cardId}`, body),
  ask: (bookId: string, question: string) =>
    post<QAResult>(`/api/books/${bookId}/qa`, { question }),
};
