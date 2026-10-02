import { runtimeConfig } from "../../config/runtime";
import { request as httpRequest } from "../../api/transport";
import { useCallback, useMemo, useRef, useState } from "react";
import { validateCourseFile } from "../../screens/shared";
import type { UploadedCourseFile } from "../../types/app";
import type { CourseSourceSummary } from "../../types/api";
import {
  sourceResourceId,
  emptyCourseState,
  isCompleteDiagnosis,
  courseStorageKey,
  localResourceId,
  bookIdFromResourceId,
  defaultCourseDiagnosis,
  legacyCourseStorageKey,
  migrateCourseState,
  type LearnerPreferences,
  type CourseResource,
  type CourseDraft,
  type CourseState,
  type OnboardingDraft,
  type CourseDiagnosis,
  type Course
} from "./model";

const resourceDatabaseName = "bookcourse-learning-resources";
const maximumLocalFileBytes = 100 * 1024 * 1024;
let databasePromise: Promise<IDBDatabase> | null = null;

function openResourceDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  if (typeof indexedDB === "undefined") return Promise.reject(new Error("当前设备无法保存资料文件"));
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(resourceDatabaseName, 1);
    request.onupgradeneeded = () => request.result.createObjectStore("files", { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => {
      databasePromise = null;
      reject(request.error ?? new Error("资料文件存储无法打开"));
    };
  });
  return databasePromise;
}

function waitForTransaction(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error("资料文件保存失败"));
    transaction.onabort = () => reject(transaction.error ?? new Error("资料文件保存中断"));
  });
}

export async function saveLearningResourceFile(id: string, file: File): Promise<void> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const database = await openResourceDatabase();
  const transaction = database.transaction("files", "readwrite");
  // Binary bytes avoid WebKit's Blob/File storage failure in the Android and
  // browser test paths; the display name stays in versioned metadata.
  const request = transaction.objectStore("files").put({ id, bytes, contentType: file.type });
  await new Promise<void>((resolve, reject) => {
    request.onerror = () => reject(request.error ?? new Error("资料文件写入失败"));
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? request.error ?? new Error("资料文件保存失败"));
    transaction.onabort = () => reject(transaction.error ?? request.error ?? new Error("资料文件保存中断"));
  });
}

export async function getLearningResourceFile(id: string): Promise<Blob | null> {
  const database = await openResourceDatabase();
  return new Promise((resolve, reject) => {
    const request = database.transaction("files", "readonly").objectStore("files").get(id);
    request.onsuccess = () => {
      const stored = request.result as { bytes?: Uint8Array; contentType?: string } | undefined;
      if (!stored?.bytes) {
        resolve(null);
        return;
      }
      const bytes = new Uint8Array(stored.bytes.length);
      bytes.set(stored.bytes);
      resolve(new Blob([bytes.buffer], { type: stored.contentType ?? "application/octet-stream" }));
    };
    request.onerror = () => reject(request.error ?? new Error("资料文件读取失败"));
  });
}

async function deleteLearningResourceFile(id: string) {
  const database = await openResourceDatabase();
  const transaction = database.transaction("files", "readwrite");
  transaction.objectStore("files").delete(id);
  await waitForTransaction(transaction);
}

let accountEpoch=0;
let serverRevision=0;
let hydratedState: CourseState = emptyCourseState();
let saveQueue: Promise<unknown> = Promise.resolve();
let lastSaveError: unknown = null;
export async function hydrateCourseState() {
  const epoch=++accountEpoch; hydratedState=emptyCourseState();saveQueue=Promise.resolve();lastSaveError=null;
  const state=await httpRequest<CourseState>("/api/demo/course-state");
  if(epoch!==accountEpoch)throw new Error("账号已切换，请重新读取课程");
  hydratedState=state;serverRevision=(state as CourseState & {revision?:number}).revision??0;
}
export function loadCourseState(): CourseState { return structuredClone(hydratedState); }
function persistCourseState(state: CourseState) {
  const snapshot = structuredClone(state);
  const epoch=accountEpoch;
  const owner=runtimeConfig.defaultUserId;
  saveQueue = saveQueue.catch(() => undefined).then(() => {if(epoch!==accountEpoch)throw new Error("账号已切换，旧保存已取消");return httpRequest<CourseState & {revision?:number}>("/api/demo/course-state", {method:"PUT",headers:{"X-Demo-Account-Id":owner},body:JSON.stringify({...snapshot,revision:serverRevision})}).then(saved=>{if(epoch===accountEpoch)serverRevision=saved.revision??serverRevision;return saved;});});
  void saveQueue.then(() => {if(epoch===accountEpoch)lastSaveError=null;}, error => {if(epoch!==accountEpoch)return;lastSaveError=error;window.dispatchEvent(new CustomEvent("bookcourse:save-error",{detail: error.message}));});
}
async function confirmSavedState() { await saveQueue; if(lastSaveError)throw lastSaveError; }

export type CourseController = ReturnType<typeof useCourseStore>;

export function useCourseStore() {
  const storeEpoch=useRef(accountEpoch);
  const [state, setState] = useState(loadCourseState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const getState = useCallback(() => stateRef.current, []);

  const commit = useCallback((update: (current: CourseState) => CourseState) => {
    if(storeEpoch.current!==accountEpoch)throw new Error("账号已切换，旧课程操作已取消");
    const next = update(stateRef.current);
    persistCourseState(next);
    stateRef.current = next;
    setState(next);
    return next;
  }, []);

  const updateOnboardingDraft = useCallback((patch: Partial<OnboardingDraft>) => {
    commit((current) => ({
      ...current,
      onboardingDraft: { ...current.onboardingDraft, ...patch }
    }));
  }, [commit]);

  const completeOnboarding = useCallback(async () => {
    const draft = stateRef.current.onboardingDraft;
    const displayName = draft.displayName.trim();
    if (!displayName) throw new Error("请先填写称呼");
    const preferences: LearnerPreferences = {
      displayName,
      primaryGoal: draft.primaryGoal,
      dailyTime: draft.dailyTime,
      completedAt: Date.now()
    };
    commit((current) => ({ ...current, preferences }));
    await confirmSavedState();
  }, [commit]);

  const updatePreferences = useCallback(async (patch: Partial<Pick<LearnerPreferences, "displayName" | "primaryGoal" | "dailyTime">>) => {
    commit((current) => {
      if (!current.preferences) throw new Error("请先完成首次引导");
      const next = { ...current.preferences, ...patch };
      if (!next.displayName.trim()) throw new Error("称呼不能为空");
      return { ...current, preferences: { ...next, displayName: next.displayName.trim() } };
    });
    await confirmSavedState();
  }, [commit]);

  const startDraft = useCallback((resourceId?: string, options?: { suggestedName?: string; uploadedCourse?: UploadedCourseFile | null }) => {
    commit((current) => {
      const draft = (current.draft?.editingCourseId ? null : current.draft) ?? {
        id: crypto.randomUUID(),
        name: options?.suggestedName?.trim() ?? "",
        resourceIds: [],
        diagnosis: {},
        step: -1,
        editingCourseId: null,
        uploadedCourse: null,
        parseJobId: null,
        parseCompleted: false
      };
      const updatedDraft = {
        ...draft,
        name: draft.name || options?.suggestedName?.trim() || "",
        uploadedCourse: options && "uploadedCourse" in options ? options.uploadedCourse : draft.uploadedCourse
      };
      return {
        ...current,
        draft: resourceId && !updatedDraft.resourceIds.includes(resourceId)
          ? { ...updatedDraft, resourceIds: [...updatedDraft.resourceIds, resourceId] }
          : updatedDraft
      };
    });
  }, [commit]);

  const editCourse = useCallback((courseId: string) => {
    commit((current) => {
      const course = current.courses.find((item) => item.id === courseId);
      if (!course) throw new Error("课程不存在");
      return {
        ...current,
        draft: {
          id: course.id,
          name: course.name,
          resourceIds: [...course.resourceIds],
          diagnosis: { ...course.diagnosis },
          step: -1,
          editingCourseId: course.id,
          uploadedCourse: null,
          parseJobId: null,
          parseCompleted: false
        }
      };
    });
  }, [commit]);

  const updateDraft = useCallback((patch: Partial<CourseDraft>) => {
    commit((current) => {
      if (!current.draft) throw new Error("请先创建课程");
      return { ...current, draft: { ...current.draft, ...patch } };
    });
  }, [commit]);

  const completeDraft = useCallback(async () => {
    const previousState=structuredClone(stateRef.current);
    const draft = stateRef.current.draft;
    if (!draft) throw new Error("课程草稿不存在");
    if (!draft.name.trim()) throw new Error("请填写课程名称");
    if (draft.resourceIds.length === 0) throw new Error("请至少添加一份书籍资料");
    if (!isCompleteDiagnosis(draft.diagnosis)) throw new Error("请回答全部六类问题");
    const now = Date.now();
    commit((current) => {
      const previous = current.courses.find((item) => item.id === draft.editingCourseId);
      const course = {
        id: draft.id,
        name: draft.name.trim(),
        resourceIds: draft.resourceIds,
        diagnosis: draft.diagnosis as CourseDiagnosis,
        createdAt: previous?.createdAt ?? now,
        updatedAt: now,
        activeResourceId: previous?.activeResourceId && draft.resourceIds.includes(previous.activeResourceId) ? previous.activeResourceId : draft.resourceIds[0]
      };
      return {
        ...current,
        courses: previous
          ? current.courses.map((item) => item.id === previous.id ? course : item)
          : [...current.courses, course],
        activeCourseId: course.id,
        draft: null
      };
    });
    try {await confirmSavedState();}catch(error){stateRef.current=previousState;setState(previousState);throw error;}
    return draft.id;
  }, [commit]);

  const clearDraft = useCallback(() => {
    commit((current) => ({ ...current, draft: null }));
  }, [commit]);

  const setActiveCourse = useCallback((courseId: string) => {
    commit((current) => {
      if (!current.courses.some((item) => item.id === courseId)) throw new Error("课程不存在");
      return { ...current, activeCourseId: courseId };
    });
  }, [commit]);

  const addSource = useCallback(async (courseId: string, bookId: string) => {
    commit((current) => {
      if (!current.courses.some((item) => item.id === courseId)) throw new Error("课程不存在");
      return {
        ...current,
        courses: current.courses.map((item) => item.id !== courseId ? item : {
          ...item,
          resourceIds: [...new Set([...item.resourceIds, sourceResourceId(bookId)])],
          updatedAt: Date.now()
        })
      };
    });
    await confirmSavedState();
  }, [commit]);

  const addExistingResource = useCallback(async (courseId: string, resourceId: string) => {
    commit((current) => {
      if (!current.courses.some((item) => item.id === courseId)) throw new Error("课程不存在");
      if (!resourceId.startsWith("local:") || !current.resources.some((item) => localResourceId(item.id) === resourceId)) {
        throw new Error("资料不存在");
      }
      return {
        ...current,
        courses: current.courses.map((item) => item.id !== courseId ? item : {
          ...item,
          resourceIds: [...new Set([...item.resourceIds, resourceId])],
          updatedAt: Date.now()
        })
      };
    });
    await confirmSavedState();
  }, [commit]);

  const removeResource = useCallback(async (courseId: string, resourceId: string) => {
    commit((current) => {
      if (!current.courses.some((item) => item.id === courseId)) throw new Error("课程不存在");
      return {
        ...current,
        dismissedSourceIds: [...new Set([...current.dismissedSourceIds, ...[bookIdFromResourceId(resourceId, current.resources)].filter((id): id is string => Boolean(id))])],
        courses: current.courses.map((item) => item.id !== courseId ? item : {
          ...item,
          resourceIds: item.resourceIds.filter((id) => id !== resourceId),
          activeResourceId: item.activeResourceId === resourceId ? item.resourceIds.find((id) => id !== resourceId) ?? null : item.activeResourceId,
          updatedAt: Date.now()
        })
      };
    });
    await confirmSavedState();
  }, [commit]);

  const addLocalFile = useCallback(async (file: File, courseId?: string) => {
    const validation = validateCourseFile(file);
    if (validation) throw new Error(validation);
    if (file.size > maximumLocalFileBytes) throw new Error("单份资料不能超过 100MB");
    const id = crypto.randomUUID();
    const contentHash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    let existing = stateRef.current.resources.find((resource) => resource.contentHash === contentHash);
    if (!existing) {
      for (const resource of stateRef.current.resources.filter((item) => !item.contentHash && item.name === file.name && item.sizeBytes === file.size)) {
        const original = await getLearningResourceFile(resource.id);
        if (!original) continue;
        const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", await original.arrayBuffer()))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
        if (hash === contentHash) { existing = resource; break; }
      }
    }
    if (existing) {
      if (courseId) await addExistingResource(courseId, localResourceId(existing.id));
      else updateDraft({ resourceIds: [...new Set([...(stateRef.current.draft?.resourceIds ?? []), localResourceId(existing.id)])] });
      return existing;
    }
    const resource: CourseResource = {
      id,
      name: file.name,
      contentType: file.type || "application/octet-stream",
      sizeBytes: file.size,
      contentHash,
      addedAt: Date.now(),
      status: "pending"
    };
    await saveLearningResourceFile(id, file);
    try {
      commit((current) => {
        const resourceId = localResourceId(id);
        if (courseId) {
          if (!current.courses.some((item) => item.id === courseId)) throw new Error("课程不存在");
          return {
            ...current,
            resources: [...current.resources, resource],
            courses: current.courses.map((item) => item.id !== courseId ? item : {
              ...item,
              resourceIds: [...item.resourceIds, resourceId],
              updatedAt: Date.now()
            })
          };
        }
        if (!current.draft) throw new Error("请先创建课程");
        return {
          ...current,
          resources: [...current.resources, resource],
          draft: { ...current.draft, resourceIds: [...current.draft.resourceIds, resourceId] }
        };
      });
    } catch (error) {
      await deleteLearningResourceFile(id).catch(() => undefined);
      throw error;
    }
    await confirmSavedState();
    return resource;
  }, [addExistingResource, commit, updateDraft]);

  const updateResource = useCallback((id: string, patch: Partial<CourseResource>) => {
    commit((current) => ({ ...current, resources: current.resources.map((resource) => resource.id === id ? { ...resource, ...patch, id } : resource) }));
  }, [commit]);

  const setActiveResource = useCallback((courseId: string, resourceId: string) => {
    commit((current) => {
      const course = current.courses.find((item) => item.id === courseId);
      if (!course?.resourceIds.includes(resourceId)) throw new Error("资料不属于这门课程");
      if (current.activeCourseId === courseId && course.activeResourceId === resourceId) return current;
      return { ...current, activeCourseId: courseId, courses: current.courses.map((item) => item.id === courseId ? { ...item, activeResourceId: resourceId } : item) };
    });
  }, [commit]);

  const syncSources = useCallback((sources: readonly Pick<CourseSourceSummary, "book_id" | "title" | "updated_at">[]) => {
    const current = stateRef.current;
    const known = new Set([...current.courses.flatMap((course) => course.resourceIds), ...(current.draft?.resourceIds ?? [])]
      .map((id) => bookIdFromResourceId(id, current.resources)).filter(Boolean));
    for (const resource of current.resources) if (resource.bookId) known.add(resource.bookId);
    const additions = sources.filter((source) => !known.has(source.book_id) && !current.dismissedSourceIds.includes(source.book_id));
    if (!additions.length) return;
    commit((state) => {
      const now = Date.now();
      const added = additions.map((source) => ({ id: `course:${source.book_id}`, name: source.title.replace(/\.(pdf|docx?|pptx?|xlsx?)$/i, ""), resourceIds: [sourceResourceId(source.book_id)], activeResourceId: sourceResourceId(source.book_id), diagnosis: defaultCourseDiagnosis(), createdAt: now, updatedAt: now }));
      return { ...state, courses: [...state.courses, ...added], activeCourseId: state.activeCourseId ?? added[0]?.id ?? null };
    });
  }, [commit]);

  const removeCourse = useCallback(async (courseId: string) => {
    commit((current) => {
      const removed = current.courses.find((course) => course.id === courseId);
      const courses = current.courses.filter((course) => course.id !== courseId);
      return { ...current, courses, activeCourseId: current.activeCourseId === courseId ? courses[0]?.id ?? null : current.activeCourseId,
        dismissedSourceIds: [...new Set([...current.dismissedSourceIds, ...(removed?.resourceIds.map((id) => bookIdFromResourceId(id, current.resources)).filter((id): id is string => Boolean(id)) ?? [])])] };
    });
    await confirmSavedState();
  }, [commit]);

  const importCatalogCourse = useCallback(async (bookId: string, name: string) => {
    const id = `catalog:${bookId}`;
    commit((current) => {
      const existing = current.courses.find((course) => course.id === id);
      if (existing) return { ...current, activeCourseId: id };
      const now = Date.now();
      const course: Course = { id, name, resourceIds: [sourceResourceId(bookId)], activeResourceId: sourceResourceId(bookId), diagnosis: defaultCourseDiagnosis(), createdAt: now, updatedAt: now };
      return { ...current, courses: [...current.courses, course], activeCourseId: id };
    });
    await confirmSavedState();
    return id;
  }, [commit]);

  return useMemo(() => ({
    state,
    getState,
    updateOnboardingDraft,
    completeOnboarding,
    updatePreferences,
    startDraft,
    editCourse,
    updateDraft,
    completeDraft,
    clearDraft,
    setActiveCourse,
    addSource,
    updateResource,
    setActiveResource,
    syncSources,
    removeCourse,
    importCatalogCourse,
    addExistingResource,
    removeResource,
    addLocalFile
  }), [
    state, getState, updateOnboardingDraft, completeOnboarding, updatePreferences, startDraft,
    editCourse, updateDraft, completeDraft, clearDraft, setActiveCourse,
    addSource, addExistingResource, removeResource, addLocalFile, updateResource, setActiveResource, syncSources, removeCourse, importCatalogCourse
  ]);
}
