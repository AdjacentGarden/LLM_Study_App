import { api } from "../../api/client";
import { request } from "../../api/transport";
import type { SourcePage, StudyRepository } from "../contracts";
import type { StudyMistake, StudyTask, StudyWorkspace } from "../../types/studyWorkspace";

export const studyRepository: StudyRepository = {
  library: {
    list: api.books,
    structure: api.structure,
    remove: async (bookId) => { await api.removeBook(bookId); },
    restore: async (bookId) => { await api.restoreBook(bookId); },
    upload: api.uploadBook,
    bind: async (bookId) => { await api.bindUpload(bookId); },
    process: (bookId, retry) => retry ? api.retryBook(bookId) : api.processBook(bookId),
    status: api.bookStatus,
    buildStructure: api.buildStructure,
    claim: api.claimBook,
    diagnostics: async (bookId) => (await api.buildDiagnostics(bookId)).ready,
    page: (bookId, page) => request<SourcePage>(`/api/books/${encodeURIComponent(bookId)}/pages/${page}`, {}, 30_000),
    resources: api.resources,
    saveNote: api.saveNote,
  },
  learning: {
    start: api.start,
    resume: api.resume,
    profileAnswer: api.answerProfile,
    next: api.next,
    respond: api.respond,
    confirm: api.confirm,
    course: api.compileCourse,
    getCourse: api.getCourse,
    practice: api.practice,
    review: api.reviewFlashcard,
    records: api.learningRecords,
    workspace: (sessionId) => request<StudyWorkspace>(`/api/interviews/${encodeURIComponent(sessionId)}/study-workspace`),
    setTask: (sessionId, taskId, status) => request<StudyTask>(`/api/interviews/${encodeURIComponent(sessionId)}/study-tasks/${encodeURIComponent(taskId)}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    setMistake: (sessionId, mistakeId, status, reason) => request<StudyMistake>(`/api/interviews/${encodeURIComponent(sessionId)}/mistakes/${encodeURIComponent(mistakeId)}`, { method: "PATCH", body: JSON.stringify({ status, ...(reason !== undefined ? { reason } : {}) }) }),
  },
  qa: { ask: api.ask },
  profile: { get: api.userProfile },
};
