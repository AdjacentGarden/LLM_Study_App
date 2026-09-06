import { runtimeConfig } from "../config/runtime";
import type {
  AssistantChatRequest,
  AssistantChatResponse,
  AssignmentSubmitRequest,
  AssignmentSubmitResponse,
  ApiAsset,
  ApiChapter,
  ApiChunk,
  ApiErrorPayload,
  AssetSourceType,
  ChapterUpdate,
  CommunityBookSummary,
  CommunityImportResponse,
  CourseSummary,
  DiagnosisResponse,
  FileSaveResponse,
  Flashcard,
  ImageGenerationJobResponse,
  ImageGenerationRequest,
  JobStatusResponse,
  LearningState,
  Lesson,
  LessonBuildJobResponse,
  LessonBuildRequest,
  MistakeRecord,
  PageMapEntry,
  ParseJobResponse,
  QuizQuestion,
  RagQuery,
  RagResponse,
  RuntimeCapabilities,
  ScanResult,
  StudyPlan,
  StudyPlanRequest,
  StudyTask,
  StudyTaskUpdate,
  TocAnalysis,
  UploadInitRequest,
  UploadInitResponse
} from "../types/api";

export class BookCourseApiError extends Error {
  code: string;
  details: Record<string, unknown>;
  status: number;

  constructor(payload: ApiErrorPayload, status: number) {
    super(payload.message || `Request failed with status ${status}`);
    this.name = "BookCourseApiError";
    this.code = payload.code || "request_failed";
    this.details = payload.details ?? {};
    this.status = status;
  }
}

export function resolveApiAssetUrl(source: string | null | undefined): string | null {
  if (!source) return null;
  if (/^https?:\/\//i.test(source) || source.startsWith("data:") || source.startsWith("blob:")) return source;
  return `${runtimeConfig.apiBaseUrl}${source.startsWith("/") ? source : `/${source}`}`;
}

function withResolvedCommunityCover(book: CommunityBookSummary): CommunityBookSummary {
  return { ...book, cover: resolveApiAssetUrl(book.cover) ?? book.cover };
}

function delay(ms: number) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

function shouldRetryRequest(error: unknown, attempt: number, retries: number): boolean {
  if (attempt >= retries) return false;
  if (error instanceof BookCourseApiError && error.status >= 400 && error.status < 500) return false;
  return true;
}

function queryString(params: Record<string, string | undefined | null>) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) search.set(key, value);
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function requestJson<T>(path: string, options: RequestInit = {}, retries?: number): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const retryLimit = retries ?? (method === "GET" ? 2 : 0);
  let lastError: unknown;

  for (let attempt = 0; attempt <= retryLimit; attempt += 1) {
    try {
      const headers = new Headers(options.headers);
      if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }
      if (runtimeConfig.defaultUserId && !headers.has("X-BookCourse-User-Id")) {
        headers.set("X-BookCourse-User-Id", runtimeConfig.defaultUserId);
      }
      const response = await fetch(`${runtimeConfig.apiBaseUrl}${path}`, {
        ...options,
        headers
      });

      if (!response.ok) {
        let payload: ApiErrorPayload;
        try {
          payload = await response.json();
        } catch {
          payload = { code: "request_failed", message: response.statusText };
        }
        throw new BookCourseApiError(payload, response.status);
      }

      if (response.status === 204) {
        return undefined as T;
      }

      return response.json() as Promise<T>;
    } catch (error) {
      lastError = error;
      if ((error as DOMException)?.name === "AbortError") {
        throw error instanceof Error ? error : new Error("请求被取消");
      }
      if (!shouldRetryRequest(error, attempt, retryLimit)) {
        throw error instanceof Error ? error : new Error("请求失败");
      }
      await delay(300 * (attempt + 1));
    }
  }

  throw lastError instanceof Error ? lastError : new Error("请求失败");
}

export const bookcourseApi = {
  health() {
    return requestJson<{ status: string; service: string }>("/api/health");
  },

  runtimeCapabilities() {
    return requestJson<RuntimeCapabilities>("/api/runtime-capabilities");
  },

  async listCourses(): Promise<CourseSummary[]> {
    const courses = await requestJson<CourseSummary[]>("/api/books");
    return courses.map((course) => ({
      ...course,
      cover_url: resolveApiAssetUrl(course.cover_url)
    }));
  },

  chatAssistant(payload: AssistantChatRequest) {
    return requestJson<AssistantChatResponse>("/api/assistant/chat", {
      method: "POST",
      body: JSON.stringify(payload)
    }, 0);
  },

  listCommunityBooks() {
    return requestJson<CommunityBookSummary[]>("/api/community/books")
      .then((books) => books.map(withResolvedCommunityCover));
  },

  getCommunityBook(catalogId: string) {
    return requestJson<CommunityBookSummary>(`/api/community/books/${encodeURIComponent(catalogId)}`)
      .then(withResolvedCommunityCover);
  },

  importCommunityBook(catalogId: string) {
    return requestJson<CommunityImportResponse>(`/api/community/books/${encodeURIComponent(catalogId)}/import`, {
      method: "POST"
    }, 0);
  },

  deleteCourse(bookId: string) {
    return requestJson<void>(`/api/books/${encodeURIComponent(bookId)}`, {
      method: "DELETE"
    }, 0);
  },

  initUpload(payload: UploadInitRequest) {
    return requestJson<UploadInitResponse>("/api/uploads/init", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },

  uploadFile(bookId: string, file: File) {
    const form = new FormData();
    form.append("file", file);
    return requestJson<FileSaveResponse>(`/api/books/${encodeURIComponent(bookId)}/files`, {
      method: "POST",
      body: form
    });
  },

  startParse(bookId: string) {
    return requestJson<ParseJobResponse>(`/api/books/${encodeURIComponent(bookId)}/parse`, {
      method: "POST",
      body: JSON.stringify({ force: false })
    });
  },

  getJob(jobId: string) {
    return requestJson<JobStatusResponse>(`/api/jobs/${encodeURIComponent(jobId)}`);
  },

  getScanResult(bookId: string) {
    return requestJson<ScanResult>(`/api/books/${encodeURIComponent(bookId)}/scan-result`);
  },

  getChapters(bookId: string) {
    return requestJson<ApiChapter[]>(`/api/books/${encodeURIComponent(bookId)}/chapters`);
  },

  updateChapter(bookId: string, chapterId: string, payload: ChapterUpdate) {
    return requestJson<ApiChapter>(`/api/books/${encodeURIComponent(bookId)}/chapters/${encodeURIComponent(chapterId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },

  rebuildChapters(bookId: string) {
    return requestJson<ApiChapter[]>(`/api/books/${encodeURIComponent(bookId)}/chapters/rebuild`, {
      method: "POST"
    });
  },

  getTocAnalysis(bookId: string) {
    return requestJson<TocAnalysis>(`/api/books/${encodeURIComponent(bookId)}/toc-candidates`);
  },

  getPageMap(bookId: string) {
    return requestJson<PageMapEntry[]>(`/api/books/${encodeURIComponent(bookId)}/page-map`);
  },

  confirmChapters(bookId: string, chapters?: ApiChapter[]) {
    return requestJson<ApiChapter[]>(`/api/books/${encodeURIComponent(bookId)}/chapters/confirm`, {
      method: "POST",
      body: JSON.stringify({ chapters: chapters ?? null })
    });
  },

  getChunks(bookId: string) {
    return requestJson<ApiChunk[]>(`/api/books/${encodeURIComponent(bookId)}/chunks`);
  },

  buildLessons(bookId: string, payload: LessonBuildRequest = {}) {
    return requestJson<LessonBuildJobResponse>(`/api/books/${encodeURIComponent(bookId)}/lessons/build`, {
      method: "POST",
      body: JSON.stringify(payload)
    }, 0);
  },

  getLessonJob(jobId: string) {
    return requestJson<LessonBuildJobResponse>(`/api/lesson-generation/jobs/${encodeURIComponent(jobId)}`);
  },

  getLessons(bookId: string) {
    return requestJson<Lesson[]>(`/api/books/${encodeURIComponent(bookId)}/lessons`);
  },

  getLesson(bookId: string, lessonId: string) {
    return requestJson<Lesson>(`/api/books/${encodeURIComponent(bookId)}/lessons/${encodeURIComponent(lessonId)}`);
  },

  buildFlashcards(bookId: string, payload: LessonBuildRequest = {}) {
    return requestJson<Flashcard[]>(`/api/books/${encodeURIComponent(bookId)}/flashcards/build`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },

  getFlashcards(bookId: string) {
    return requestJson<Flashcard[]>(`/api/books/${encodeURIComponent(bookId)}/flashcards`);
  },

  buildQuizzes(bookId: string, payload: LessonBuildRequest = {}) {
    return requestJson<QuizQuestion[]>(`/api/books/${encodeURIComponent(bookId)}/quizzes/build`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },

  getQuizzes(bookId: string) {
    return requestJson<QuizQuestion[]>(`/api/books/${encodeURIComponent(bookId)}/quizzes`);
  },

  getAssets(bookId: string, sourceType?: AssetSourceType) {
    return requestJson<ApiAsset[]>(`/api/books/${encodeURIComponent(bookId)}/assets${queryString({ source_type: sourceType })}`);
  },

  getChapterFigures(chapterId: string, bookId?: string, sourceType?: AssetSourceType) {
    return requestJson<ApiAsset[]>(`/api/chapters/${encodeURIComponent(chapterId)}/figures${queryString({
      book_id: bookId,
      source_type: sourceType
    })}`);
  },

  generateLessonFigure(lessonId: string, payload: ImageGenerationRequest) {
    return requestJson<ImageGenerationJobResponse>(`/api/lessons/${encodeURIComponent(lessonId)}/figures/generate`, {
      method: "POST",
      body: JSON.stringify(payload)
    }, 0);
  },

  generateAsset(payload: ImageGenerationRequest) {
    return requestJson<ImageGenerationJobResponse>("/api/assets/generate", {
      method: "POST",
      body: JSON.stringify(payload)
    }, 0);
  },

  getImageGenerationJob(jobId: string) {
    return requestJson<ImageGenerationJobResponse>(`/api/image-generation/jobs/${encodeURIComponent(jobId)}`);
  },

  queryRag(payload: RagQuery) {
    return requestJson<RagResponse>("/api/rag/query", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },

  submitAssignment(assignmentId: string, payload: AssignmentSubmitRequest) {
    return requestJson<AssignmentSubmitResponse>(`/api/assignments/${encodeURIComponent(assignmentId)}/submit`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },

  diagnoseAssignment(assignmentId: string, submissionId: string) {
    return requestJson<DiagnosisResponse>(`/api/assignments/${encodeURIComponent(assignmentId)}/diagnose${queryString({
      submission_id: submissionId
    })}`, {
      method: "POST"
    });
  },

  getMistakes(userId: string, bookId?: string) {
    return requestJson<MistakeRecord[]>(`/api/users/${encodeURIComponent(userId)}/mistakes${queryString({ book_id: bookId })}`);
  },

  createStudyPlan(bookId: string, payload: StudyPlanRequest) {
    return requestJson<StudyPlan>(`/api/books/${encodeURIComponent(bookId)}/plan`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },

  getStudyPlan(bookId: string, userId = runtimeConfig.defaultUserId) {
    return requestJson<StudyPlan>(`/api/books/${encodeURIComponent(bookId)}/plan${queryString({ user_id: userId })}`);
  },

  patchStudyTask(taskId: string, payload: StudyTaskUpdate) {
    return requestJson<StudyTask>(`/api/study-tasks/${encodeURIComponent(taskId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },

  getLearningState(userId: string) {
    return requestJson<LearningState>(`/api/users/${encodeURIComponent(userId)}/learning-state`);
  }
};

export type BookCourseRepository = Omit<typeof bookcourseApi, "runtimeCapabilities">;
