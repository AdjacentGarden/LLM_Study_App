import { useAccount } from "../components/AccountGate";
import { listStudyNotes } from "./features/studyNotes/repository";
import { api as legacyApi } from "../api/client";
import { communityCatalog } from "./services/CommunityRepository";
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { Settings } from "lucide-react";
import {
  ActionSheet,
  AppShell,
  IconButton,
  Toast,
  actionSheetAnimationNames,
  type ActionSheetView
} from "./components/ui";
import { runtimeConfig } from "./config/runtime";
import { AppProvider } from "./context/AppContext";
import type { SourceSummariesLoadState, SourceSummariesReadyKind } from "./context/AppContext";

import { useBookCourseRepository } from "./context/BookCourseRepositoryContext";

import {
  dismissNativeKeyboardIfFocused,
  minimizeNativeAndroidApp,
  registerAndroidBackButton
} from "./platform/nativeApp";
import { globalMotionFallbackMs, ScreenTransition, useMotionPresence } from "./motion";
import {
  createInitialNavigation,
  navigate,
  type NavigationSnapshot
} from "./motion/navigationMachine";
import { useReducedMotion } from "./motion/useReducedMotion";
import {
  AssignmentScreen,
  BookSwitcherSheetContent,
  ChapterConfirmScreen,
  ChatSheetContent,
  CommunityBookScreen,
  CommunityImportScreen,
  CommunityScreen,
  CourseReadyScreen,
  DiagnosisScreen,
  EditChapterSheetContent,
  ExportPreviewScreen,
  FlashcardScreen,
  HomeScreen,
  LessonReportScreen,
  LessonScreen,
  LibraryScreen,
  MistakeBookScreen,
  NoteSheetContent,
  NotesScreen,
  NoteTypeSheetContent,
  ParseReadyScreen,
  ProcessingScreen,
  ProfileScreen,
  SettingsScreen,
  SourceReaderScreen,
  StudyScreen,
  StudyPlanScreen,
  UploadScreen,
  VoiceNoteScreen
} from "./screens";
import type {
  ApiAsset,
  ApiChapter,
  ApiChunk,
  CourseSourceSummary,
  DiagnosisResponse,
  Flashcard,
  JobStatusResponse,
  Lesson,
  LessonBuildJobResponse,
  QuizQuestion,
  ScanResult,
  StudyPlan
} from "./types/api";
import type { Screen, SheetState, SourcePageTarget, StudyLocation, ToastMessage, ToastTone, UploadedCourseFile } from "./types/app";
import type { NoteCaptureIntent } from "./features/studyNotes/types";
import { resolveSourceReaderHeading } from "./features/studyNotes/sourceReaderHeading";
import { useCourseStore } from "./features/courses/repository";
import { sourceResourceId } from "./features/courses/model";
import { CourseSetupScreen, OnboardingScreen } from "./features/courses/FlowScreens";
import { CourseDetailScreen } from "./features/courses/HomeScreens";
import { AddMaterialsSheetContent } from "./features/courses/AddMaterialsSheetContent";
import { courseLocationKey, preferredCourseSource } from "./features/courses/selectors";
import { bookIdFromResourceId } from "./features/courses/model";

import { createCourseSelectionCoordinator } from "./screens/homeBookModel";
import { lessonHeaderSubtitle } from "./screens/lessonHeader";
import {
  hasCompleteLoadedCourseContext,
  resolveCourseSessionClear,
  shouldClearLoadedCourseAfterRefresh,
  shouldClearLoadedCourseForDeletedBook,
  shouldClearRemoteSessionAfterRefresh,
  type LoadedCourseContext
} from "./screens/sourceResourceIdentity";

const studyLocationsStorageKey = () => `account:${runtimeConfig.defaultUserId}:bookcourse.study-locations.v1`;
const demoParseJobPollIntervalMs = 700;
const demoParseJobRetryIntervalMs = 1200;
const parseJobPollIntervalMs = 1500;
const parseJobRetryIntervalMs = 3000;

function loadStudyLocations(): Record<string, StudyLocation> {
  try {
    const stored = window.localStorage.getItem(studyLocationsStorageKey());
    if (!stored) return {};
    const parsed = JSON.parse(stored) as Record<string, StudyLocation>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

const titles: Record<Screen, { title?: string; subtitle?: string; back?: boolean; hideNav?: boolean }> = {
  home: {},
  onboarding: { hideNav: true },
  courseSetup: { hideNav: true },
  courseDetail: { title: "课程", back: true },
  upload: { title: "创建课程", back: true, hideNav: true },
  parseReady: { title: "解析教材", back: true, hideNav: true },
  processing: { title: "解析教材", subtitle: "正在识别章节和知识点", back: true, hideNav: true },
  chapterConfirm: { title: "确认目录", subtitle: "核对资料目录与 AI 课时映射", back: true, hideNav: true },
  courseReady: { hideNav: true },
  library: { title: "我的课程", subtitle: "管理课程、教材与学习资料", back: true, hideNav: true },
  community: { title: "发现", subtitle: "发现同学分享的优质课程" },
  communityBook: { title: "共享课程", back: true, hideNav: true },
  communityImport: { hideNav: true },
  study: {},
  book: {},
  plan: { title: "学习计划", subtitle: "科学规划，高效学习", back: true, hideNav: true },
  flashcards: { title: "知识点闪卡", subtitle: "回忆、核对，再安排复习", back: true, hideNav: true },
  lesson: { title: "章节学习", subtitle: "第 2 章 1 节", back: true, hideNav: true },
  assignment: { title: "作业练习", subtitle: "按顺序练习，定位卡点", back: true, hideNav: true },
  diagnosis: { title: "作业诊断", subtitle: "看懂原因，马上巩固", back: true, hideNav: true },
  mistakes: { title: "错题集", subtitle: "重做卡点，直到真正掌握", back: true, hideNav: true },
  notes: { title: "学习笔记", subtitle: "保留原始记录与独立整理版", back: true, hideNav: true },
  voiceNote: { title: "语音笔记", subtitle: "录下想法，再结合教材整理", back: true, hideNav: true },
  source: { back: true, hideNav: true },
  export: { title: "导出预览", subtitle: "选择要导出的模块", back: true, hideNav: true },
  report: { title: "章节报告", subtitle: "完成后调整计划", back: true, hideNav: true },
  profile: { title: "我的", subtitle: "学习数据与偏好" },
  settings: { title: "设置", back: true, hideNav: true }
};

const toastQuietScreens = new Set<Screen>([
  "upload",
  "parseReady",
  "processing",
  "chapterConfirm",
  "courseReady",
  "communityImport"
]);

const toastDwellDurationMs = 1000;

function getSheetViewKey(view: ActionSheetView) {
  return view.key;
}

type OpenSheetState = Exclude<SheetState, null>;

function snapshotSheetState(sheet: OpenSheetState): OpenSheetState {
  switch (sheet.type) {
    case "chat":
      return { type: "chat" };
    case "note":
      return { ...sheet };
    case "noteType":
      return { ...sheet, intent: { ...sheet.intent }, pageOptions: sheet.pageOptions?.map((option) => ({ ...option })) };
    case "editChapter":
      return {
        ...sheet,
        evidence: sheet.evidence
          ? { ...sheet.evidence, reasons: [...sheet.evidence.reasons] }
          : undefined
      };
    case "bookSwitcher":
      return { type: "bookSwitcher" };
    case "addMaterials":
      return { ...sheet };
  }
}

export default function App() {
  const { logout } = useAccount();
  const bookcourseRepository = useBookCourseRepository();
  const reducedMotion = useReducedMotion();
  const courses = useCourseStore();
  const coursesRef = useRef(courses);
  coursesRef.current = courses;
  const sourceSelectorRef = useRef<(bookId: string, courseId?: string) => Promise<boolean>>(async () => false);
  const parseDraft = courses.state.draft;
  const navigationRef = useRef<NavigationSnapshot>(createInitialNavigation(
    courses.state.preferences ? "home" : "onboarding"
  ));
  const [navigation, setNavigation] = useState<NavigationSnapshot>(navigationRef.current);
  const screen = navigation.screen;
  const [sheet, setSheet] = useState<SheetState>(null);
  const sheetRequestedRef = useRef<SheetState>(null);
  const sheetTriggerRef = useRef<HTMLElement | null>(null);
  const sheetRestoreFocusRef = useRef(true);
  const mainRef = useRef<HTMLElement | null>(null);
  const screenScrollPositionsRef = useRef(new Map<Screen, number>());
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const toastIdRef = useRef(0);
  const toastTimerRef = useRef<number | undefined>(undefined);
  const [selectedUpload, setSelectedUpload] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<UploadedCourseFile | null>(parseDraft?.uploadedCourse ?? null);
  const uploadedFileRef = useRef(uploadedFile);
  uploadedFileRef.current = uploadedFile;
  const [parseJobId, setParseJobId] = useState<string | null>(parseDraft?.parseJobId ?? null);
  const [parseJobStatus, setParseJobStatus] = useState<JobStatusResponse | null>(() =>
    parseDraft?.parseCompleted && parseDraft.parseJobId && parseDraft.uploadedCourse
      ? {
          job_id: parseDraft.parseJobId,
          book_id: parseDraft.uploadedCourse.bookId,
          status: "done",
          stage: "completed",
          progress: 100,
          message: "教材解析已完成",
          error: null
        }
      : null
  );
  const [sourceSummaries, setSourceSummaries] = useState<CourseSourceSummary[]>([]);
  const [sourceSummariesLoadState, setSourceSummariesLoadState] = useState<SourceSummariesLoadState>("loading");
  const [sourceSummariesReadyKind, setSourceSummariesReadyKind] = useState<SourceSummariesReadyKind>("empty");
  const [sourceSummariesError, setSourceSummariesError] = useState<string | null>(null);
  const [sourceSummariesRefreshing, setSourceSummariesRefreshing] = useState(false);
  const [selectedCommunityBookId, setSelectedCommunityBookId] = useState(communityCatalog[0]?.id ?? "");
  const [communityImportGeneration, setCommunityImportGeneration] = useState(0);
  const [pendingBookId, setPendingBookId] = useState<string | null>(null);
  const courseSelectionCoordinatorRef = useRef(createCourseSelectionCoordinator());
  const sourceSummariesRef = useRef<CourseSourceSummary[]>([]);
  sourceSummariesRef.current = sourceSummaries;
  const [loadedBookId, setLoadedBookId] = useState<string | null>(null);
  const [parsedScanResult, setParsedScanResult] = useState<ScanResult | null>(null);
  const [parsedChapters, setParsedChapters] = useState<ApiChapter[] | null>(null);
  const loadedBookIdRef = useRef(loadedBookId);
  loadedBookIdRef.current = loadedBookId;
  const parsedChaptersRef = useRef(parsedChapters);
  parsedChaptersRef.current = parsedChapters;
  const [parsedChunks, setParsedChunks] = useState<ApiChunk[] | null>(null);
  const [parsedAssets, setParsedAssets] = useState<ApiAsset[] | null>(null);
  const [generatedLessons, setGeneratedLessons] = useState<Lesson[] | null>(null);
  const [lessonBuildJobId, setLessonBuildJobId] = useState<string | null>(null);
  const [lessonBuildJobStatus, setLessonBuildJobStatus] = useState<LessonBuildJobResponse | null>(null);
  const [generatedFlashcards, setGeneratedFlashcards] = useState<Flashcard[] | null>(null);
  const [generatedQuizzes, setGeneratedQuizzes] = useState<QuizQuestion[] | null>(null);
  const [activeChapterId, setActiveChapterId] = useState<string | null>(null);
  const [currentStudyPlan, setCurrentStudyPlan] = useState<StudyPlan | null>(null);
  const [latestDiagnosis, setLatestDiagnosis] = useState<DiagnosisResponse | null>(null);
  const [answer, setAnswer] = useState("");
  const [savedNoteCount, setSavedNoteCount] = useState(0);
  const [sourcePageTarget, setSourcePageTarget] = useState<SourcePageTarget | null>(null);
  const [sourceReaderCurrentPage, setSourceReaderCurrentPage] = useState(1);
  const [noteCaptureIntent, setNoteCaptureIntent] = useState<NoteCaptureIntent | null>(null);
  const [studyLocations, setStudyLocations] = useState<Record<string, StudyLocation>>(loadStudyLocations);
  const studyLocationsRef = useRef(studyLocations);
  studyLocationsRef.current = studyLocations;
  const completedParseJobRef = useRef<string | null>(parseDraft?.parseCompleted ? parseDraft.parseJobId ?? null : null);
  const parseSessionGenerationRef = useRef(0);
  const loadedCourseContextRef = useRef<LoadedCourseContext>({
    loadedBookId,
    uploadedFile,
    parsedScanResult,
    parsedChapters,
    parsedChunks,
    parsedAssets,
    currentStudyPlan,
    generatedLessons,
    generatedFlashcards,
    generatedQuizzes
  });
  loadedCourseContextRef.current = {
    loadedBookId,
    uploadedFile,
    parsedScanResult,
    parsedChapters,
    parsedChunks,
    parsedAssets,
    currentStudyPlan,
    generatedLessons,
    generatedFlashcards,
    generatedQuizzes
  };

  const commitNavigation = useCallback((resolve: (current: NavigationSnapshot) => NavigationSnapshot) => {
    const next = resolve(navigationRef.current);
    navigationRef.current = next;
    if (toastQuietScreens.has(next.screen)) {
      if (toastTimerRef.current !== undefined) {
        window.clearTimeout(toastTimerRef.current);
        toastTimerRef.current = undefined;
      }
      setToast(null);
    }
    setNavigation(next);
    return next;
  }, []);

  const focusCurrentMain = useCallback(() => {
    if (mainRef.current?.isConnected) mainRef.current.focus({ preventScroll: true });
  }, []);

  const saveCurrentScreenScrollPosition = useCallback((current: NavigationSnapshot, next: NavigationSnapshot) => {
    if (current.nonce === next.nonce || !mainRef.current?.isConnected) return;
    screenScrollPositionsRef.current.set(current.screen, mainRef.current.scrollTop);
  }, []);

  const restoreSheetFocus = useCallback(() => {
    if (!sheetRestoreFocusRef.current) return;
    sheetRestoreFocusRef.current = false;
    if (sheetTriggerRef.current?.isConnected) {
      sheetTriggerRef.current.focus({ preventScroll: true });
      return;
    }
    focusCurrentMain();
  }, [focusCurrentMain]);

  const requestSheetClose = useCallback((restoreFocus: boolean) => {
    if (!sheetRequestedRef.current) return;
    sheetRestoreFocusRef.current = restoreFocus;
    sheetRequestedRef.current = null;
    setSheet(null);
  }, []);

  const requestSheetCloseForNavigation = useCallback((restoreFocus: boolean) => {
    // Navigation can arrive after a sheet has already entered its closing
    // generation. Record the policy even then so its eventual cleanup cannot
    // steal focus back from the destination screen's main landmark.
    sheetRestoreFocusRef.current = restoreFocus;
    if (!sheetRequestedRef.current) return;
    sheetRequestedRef.current = null;
    setSheet(null);
  }, []);

  const go = useCallback((next: Screen) => {
    const current = navigationRef.current;
    const draft = coursesRef.current.getState().draft;
    const pendingCourseImport = draft && !draft.editingCourseId && draft.uploadedCourse
      && draft.uploadedCourse.bookId === uploadedFileRef.current?.bookId;
    const target = next === "chapterConfirm" && pendingCourseImport
      ? draft.parseCompleted ? "courseSetup" : draft.parseJobId ? "processing" : "parseReady"
      : next;
    if (current.screen === "voiceNote" && target !== "voiceNote") setNoteCaptureIntent(null);
    const nextNavigation = navigate(current, { type: "go", screen: target });
    saveCurrentScreenScrollPosition(current, nextNavigation);
    requestSheetCloseForNavigation(nextNavigation.nonce === current.nonce);
    commitNavigation(() => nextNavigation);
  }, [commitNavigation, requestSheetCloseForNavigation, saveCurrentScreenScrollPosition]);

  const selectCommunityBook = useCallback((bookId: string) => {
    setSelectedCommunityBookId(bookId);
  }, []);

  const back = useCallback(() => {
    const current = navigationRef.current;
    if (current.screen === "source" || current.screen === "voiceNote" || current.screen === "onboarding" || current.screen === "courseSetup") {
      const recordingBackEvent = new CustomEvent("bookcourse:native-back", { cancelable: true });
      window.dispatchEvent(recordingBackEvent);
      if (recordingBackEvent.defaultPrevented) return;
    }
    if (current.screen === "source" || current.screen === "voiceNote") {
      setNoteCaptureIntent(null);
    }
    const nextNavigation = navigate(current, { type: "back" });
    saveCurrentScreenScrollPosition(current, nextNavigation);
    requestSheetCloseForNavigation(nextNavigation.nonce === current.nonce);
    commitNavigation(() => nextNavigation);
  }, [commitNavigation, requestSheetCloseForNavigation, saveCurrentScreenScrollPosition]);

  const openSourcePage = useCallback((target: SourcePageTarget) => {
    const store = coursesRef.current.getState();
    const parent = store.courses.find((course) => course.id === (target.courseId ?? store.activeCourseId) && course.resourceIds.some((id) => bookIdFromResourceId(id, store.resources) === target.bookId))
      ?? store.courses.find((course) => course.resourceIds.some((id) => bookIdFromResourceId(id, store.resources) === target.bookId));
    target = { ...target, courseId: parent?.id };
    const open = () => {
    const current = navigationRef.current;
    setSourceReaderCurrentPage(Math.max(1, target.pageStart));
    if (current.screen === "source") {
      requestSheetCloseForNavigation(true);
      setSourcePageTarget(target);
      return;
    }
    const nextNavigation = navigate(current, { type: "source" });
    saveCurrentScreenScrollPosition(current, nextNavigation);
    requestSheetCloseForNavigation(nextNavigation.nonce === current.nonce);
    setSourcePageTarget(target);
    commitNavigation(() => nextNavigation);
    };
    if (loadedBookIdRef.current !== target.bookId || (parent && parent.id !== store.activeCourseId)) {
      void sourceSelectorRef.current(target.bookId, parent?.id).then((selected) => { if (selected) open(); });
    } else open();
  }, [commitNavigation, requestSheetCloseForNavigation, saveCurrentScreenScrollPosition]);

  const replaceScreen = useCallback((next: Screen) => {
    const current = navigationRef.current;
    const nextNavigation = navigate(current, { type: "replace", screen: next });
    saveCurrentScreenScrollPosition(current, nextNavigation);
    requestSheetCloseForNavigation(nextNavigation.nonce === current.nonce);
    commitNavigation(() => nextNavigation);
  }, [commitNavigation, requestSheetCloseForNavigation, saveCurrentScreenScrollPosition]);

  const openSheet = useCallback((nextSheet: SheetState) => {
    if (!nextSheet) {
      requestSheetClose(true);
      return;
    }
    if (!sheetRequestedRef.current && !sheetTriggerRef.current?.isConnected && document.activeElement instanceof HTMLElement) {
      sheetTriggerRef.current = document.activeElement;
    }
    const sheetSnapshot = snapshotSheetState(nextSheet);
    sheetRestoreFocusRef.current = true;
    sheetRequestedRef.current = sheetSnapshot;
    setSheet(sheetSnapshot);
  }, [requestSheetClose]);

  const closeSheet = useCallback(() => {
    requestSheetClose(true);
  }, [requestSheetClose]);

  useEffect(() => registerAndroidBackButton(() => {
    // Give local dialogs (the global AI assistant, for example) the first
    // chance to consume Android's hardware/system back event.
    const dialogEvent = new CustomEvent("bookcourse:native-back", { cancelable: true });
    window.dispatchEvent(dialogEvent);
    if (dialogEvent.defaultPrevented) return;

    if (dismissNativeKeyboardIfFocused()) return;
    if (sheetRequestedRef.current || sheet) {
      closeSheet();
      return;
    }
    if (navigationRef.current.history.length > 0) {
      back();
      return;
    }
    minimizeNativeAndroidApp();
  }), [back, closeSheet, sheet]);

  const captureSheetTrigger = useCallback((event: ReactMouseEvent<HTMLDivElement>) => {
    // A closing sheet deliberately clears the requested value while its frozen
    // panel is still mounted. Ignore all overlay interactions in that window:
    // otherwise a blocked Save/Delete click could overwrite the original
    // restore target with an element that is about to unmount.
    if (
      sheetRequestedRef.current ||
      !(event.target instanceof Element) ||
      event.target.closest(".sheet-overlay")
    ) return;
    const trigger = event.target.closest<HTMLElement>(
      "button, a[href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
    );
    if (trigger) sheetTriggerRef.current = trigger;
  }, []);

  useEffect(() => () => {
    if (toastTimerRef.current !== undefined) window.clearTimeout(toastTimerRef.current);
  }, []);

  const showToast = useCallback((text: string, tone: ToastTone = "success") => {
    if (toastQuietScreens.has(navigationRef.current.screen)) return;
    if (toastTimerRef.current !== undefined) window.clearTimeout(toastTimerRef.current);
    const id = toastIdRef.current + 1;
    toastIdRef.current = id;
    setToast({ id, text, tone });
    const timer = window.setTimeout(() => {
      setToast((current) => (current?.id === id ? null : current));
      if (toastTimerRef.current === timer) toastTimerRef.current = undefined;
    }, toastDwellDurationMs);
    toastTimerRef.current = timer;
  }, []);

  const beginCommunityImport = useCallback(async () => {
    const book=communityCatalog.find(item=>item.id===selectedCommunityBookId);
    if(!book)return;
    try {
      const acquired=await legacyApi.acquire(book.id);
      await coursesRef.current.importCatalogCourse(acquired.book_id,book.title);
      await refreshSources();setCommunityImportGeneration(current=>current+1);go("communityImport");
    }catch(error){showToast(error instanceof Error?error.message:"课程领取失败", "warning");}
  }, [go,selectedCommunityBookId,showToast]);

  useEffect(()=>{const load=()=>{void listStudyNotes().then(notes=>setSavedNoteCount(notes.length)).catch(()=>undefined);};load();window.addEventListener("bookcourse:study-notes-changed",load);return()=>window.removeEventListener("bookcourse:study-notes-changed",load);},[]);
  useEffect(()=>{const show=(event:Event)=>showToast((event as CustomEvent<string>).detail,"warning");window.addEventListener("bookcourse:save-error",show);return()=>window.removeEventListener("bookcourse:save-error",show);},[showToast]);
  const finishNoteCapture = useCallback(() => {
    setNoteCaptureIntent(null);
  }, []);

  const startNote = useCallback((intent: NoteCaptureIntent) => {
    const courseId = intent.anchor?.courseId ?? intent.source?.courseId ?? coursesRef.current.getState().activeCourseId ?? undefined;
    intent = { ...intent, anchor: intent.anchor ? { ...intent.anchor, courseId } : undefined, source: intent.source ? { ...intent.source, courseId } : undefined };
    const source = intent.source ?? (intent.anchor?.bookId && intent.anchor.pageStart ? {
      courseId,
      bookId: intent.anchor.bookId,
      title: intent.anchor.chapterTitle ?? intent.anchor.bookTitle ?? "教材原文",
      pageStart: intent.anchor.pageStart,
      pageEnd: intent.anchor.pageEnd,
      printedPageStart: intent.anchor.printedPageStart,
      printedPageEnd: intent.anchor.printedPageEnd,
      sourceText: intent.anchor.sourceText,
      from: intent.from
    } satisfies SourcePageTarget : null);
    if (intent.kind === "text") {
      setNoteCaptureIntent(intent);
      if (source) {
        openSourcePage(source);
        return;
      }
      openSheet({
        type: "note",
        kind: intent.anchor?.quote ? "selection" : "concept",
        concept: intent.anchor?.chapterTitle ?? intent.anchor?.bookTitle ?? "学习笔记",
        quote: intent.anchor?.quote,
        sourceLabel: intent.anchor?.pageStart ? `教材第 ${intent.anchor.pageStart} 页` : undefined,
        source: intent.source
      });
      return;
    }
    if (intent.kind === "voice") {
      setNoteCaptureIntent(intent);
      go("voiceNote");
      return;
    }

    if (!source) {
      setNoteCaptureIntent(null);
      showToast("请先选择要批注的教材页", "warning");
      return;
    }
    setNoteCaptureIntent(intent);
    openSourcePage(source);
  }, [go, openSheet, openSourcePage, showToast]);

  const clearLoadedCourse = useCallback((expectedBookId?: string) => {
    if (
      expectedBookId
      && !shouldClearLoadedCourseForDeletedBook(loadedBookIdRef.current, expectedBookId)
    ) return false;
    courseSelectionCoordinatorRef.current.invalidate();
    setPendingBookId(null);
    loadedBookIdRef.current = null;
    parsedChaptersRef.current = null;
    loadedCourseContextRef.current = {
      loadedBookId: null,
      uploadedFile: uploadedFileRef.current,
      parsedScanResult: null,
      parsedChapters: null,
      parsedChunks: null,
      parsedAssets: null,
      currentStudyPlan: null,
      generatedLessons: null,
      generatedFlashcards: null,
      generatedQuizzes: null
    };
    setLoadedBookId(null);
    setParsedScanResult(null);
    setParsedChapters(null);
    setParsedChunks(null);
    setParsedAssets(null);
    setCurrentStudyPlan(null);
    setGeneratedLessons(null);
    setGeneratedFlashcards(null);
    setGeneratedQuizzes(null);
    setActiveChapterId(null);
    setLatestDiagnosis(null);
    setLessonBuildJobId(null);
    setLessonBuildJobStatus(null);
    setAnswer("");
    setSourcePageTarget(null);
    return true;
  }, []);

  const clearCourseSession = useCallback((expectedBookId?: string) => {
    const decision = resolveCourseSessionClear(
      uploadedFileRef.current,
      expectedBookId,
      parseSessionGenerationRef.current
    );
    if (!decision.shouldClear) return false;

    parseSessionGenerationRef.current = decision.nextGeneration;
    uploadedFileRef.current = null;
    loadedCourseContextRef.current = {
      ...loadedCourseContextRef.current,
      uploadedFile: null
    };
    setUploadedFile(null);
    setParseJobId(null);
    setParseJobStatus(null);
    completedParseJobRef.current = null;
    return true;
  }, []);

  const refreshSources = useCallback(async () => {
    const hasExistingCourses = sourceSummariesRef.current.length > 0;
    if (!hasExistingCourses) setSourceSummariesLoadState("loading");
    setSourceSummariesRefreshing(true);
    try {
      const courses = await bookcourseRepository.listSources();
      sourceSummariesRef.current = courses;
      // The prepared source may have committed just before a browser reload.
      // Reconcile it with its original file before creating orphan course entries.
      for (const resource of coursesRef.current.getState().resources) {
        const source = courses.find((item) => item.book_id === (resource.bookId ?? `book_local_${resource.id}`));
        if (source?.status === "ready" && (resource.status !== "ready" || resource.bookId !== source.book_id)) {
          coursesRef.current.updateResource(resource.id, { bookId: source.book_id, status: "ready", progress: 100, error: undefined });
        }
      }
      coursesRef.current.syncSources(courses);
      setSourceSummaries(courses);
      setSourceSummariesReadyKind(courses.length > 0 ? "content" : "empty");
      setSourceSummariesLoadState("ready");
      setSourceSummariesError(null);

      const activeUploadedFile = uploadedFileRef.current;
      if (shouldClearLoadedCourseAfterRefresh(loadedBookIdRef.current, activeUploadedFile, courses)) {
        clearLoadedCourse();
      }
      if (shouldClearRemoteSessionAfterRefresh(activeUploadedFile, courses)) {
        clearCourseSession(activeUploadedFile?.bookId);
      }
    } catch (err) {
      setSourceSummariesError(err instanceof Error ? err.message : "课程列表加载失败");
      if (!hasExistingCourses) setSourceSummariesLoadState("error");
    } finally {
      setSourceSummariesRefreshing(false);
    }
  }, [bookcourseRepository, clearCourseSession, clearLoadedCourse]);

  const updateStudyLocation = useCallback((bookId: string, location: Partial<StudyLocation>) => {
    const key = courseLocationKey(coursesRef.current.getState().activeCourseId, bookId);
    setStudyLocations((current) => ({
      ...current,
      [key]: {
        expandedChapterId: (current[key] ?? current[bookId])?.expandedChapterId ?? null,
        expandedSectionId: (current[key] ?? current[bookId])?.expandedSectionId ?? null,
        ...location
      }
    }));
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(studyLocationsStorageKey(), JSON.stringify(studyLocations));
    } catch {
      // Study position persistence is a convenience; an unavailable storage
      // backend must never block the learning flow.
    }
  }, [studyLocations]);

  const cancelSourceSelection = useCallback(() => {
    courseSelectionCoordinatorRef.current.invalidate();
    setPendingBookId(null);
  }, []);

  const selectSource = useCallback(async (bookId: string, courseId?: string) => {
    const store = coursesRef.current;
    const state = store.getState();
    const containsSource = (course: typeof state.courses[number]) => course.resourceIds.some((id) => bookIdFromResourceId(id, state.resources) === bookId);
    const parent = courseId ? state.courses.find((course) => course.id === courseId && containsSource(course))
      : state.courses.find((course) => course.id === state.activeCourseId && containsSource(course)) ?? state.courses.find(containsSource);
    if (courseId && !parent) { showToast("资料不属于这门课程", "warning"); return false; }
    const commitParent = () => {
      const resource = parent?.resourceIds.find((id) => bookIdFromResourceId(id, state.resources) === bookId);
      if (parent && resource) coursesRef.current.setActiveResource(parent.id, resource);
    };
    if (hasCompleteLoadedCourseContext(loadedCourseContextRef.current, bookId)) {
      courseSelectionCoordinatorRef.current.invalidate();
      setPendingBookId(null);
      commitParent();
      const saved = studyLocationsRef.current[courseLocationKey(parent?.id, bookId)] ?? studyLocationsRef.current[bookId];
      if (saved?.expandedSectionId) setActiveChapterId(saved.expandedSectionId);
      return true;
    }
    const summary = sourceSummariesRef.current.find((course) => course.book_id === bookId);
    return courseSelectionCoordinatorRef.current.run(bookId, {
      load: async () => {
        const [scan, chapters, chunks, assets, plan, lessons, cards, quizzes] = await Promise.all([
          bookcourseRepository.getScanResult(bookId),
          bookcourseRepository.getChapters(bookId),
          bookcourseRepository.getChunks(bookId),
          bookcourseRepository.getAssets(bookId),
          bookcourseRepository.getStudyPlan(bookId, runtimeConfig.defaultUserId),
          bookcourseRepository.getLessons(bookId),
          bookcourseRepository.getFlashcards(bookId),
          bookcourseRepository.getQuizzes(bookId)
        ]);
        return { scan, chapters, chunks, assets, plan, lessons, cards, quizzes };
      },
      commit: ({ scan, chapters, chunks, assets, plan, lessons, cards, quizzes }) => {
        const storedLocation = studyLocationsRef.current[courseLocationKey(parent?.id, bookId)] ?? studyLocationsRef.current[bookId];
        const chapterIds = new Set(chapters.map((chapter) => chapter.chapter_id));
        const activeId = storedLocation?.expandedSectionId && chapterIds.has(storedLocation.expandedSectionId)
          ? storedLocation.expandedSectionId
          : lessons[0]?.chapter_id ?? chapters.find((chapter) => chapter.level > 1)?.chapter_id ?? chapters[0]?.chapter_id ?? null;
        const selectedCourseFile: UploadedCourseFile = {
          bookId,
          name: scan.filename || summary?.title || "已选择教材",
          sizeBytes: 0,
          contentType: scan.file_type === "pdf" ? "application/pdf" : scan.file_type || "application/octet-stream",
          uploadedAt: Date.now(),
          origin: "remote-course"
        };
        clearCourseSession();
        uploadedFileRef.current = selectedCourseFile;
        loadedBookIdRef.current = bookId;
        parsedChaptersRef.current = chapters;
        loadedCourseContextRef.current = {
          loadedBookId: bookId,
          uploadedFile: selectedCourseFile,
          parsedScanResult: scan,
          parsedChapters: chapters,
          parsedChunks: chunks,
          parsedAssets: assets,
          currentStudyPlan: plan,
          generatedLessons: lessons,
          generatedFlashcards: cards,
          generatedQuizzes: quizzes
        };
        setUploadedFile(selectedCourseFile);
        setParsedScanResult(scan);
        setParsedChapters(chapters);
        setParsedChunks(chunks);
        setParsedAssets(assets);
        setCurrentStudyPlan(plan);
        setGeneratedLessons(lessons);
        setGeneratedFlashcards(cards);
        setGeneratedQuizzes(quizzes);
        setActiveChapterId(activeId);
        setLatestDiagnosis(null);
        setLessonBuildJobId(null);
        setLessonBuildJobStatus(null);
        setAnswer("");
        setSourcePageTarget(null);
        setLoadedBookId(bookId);
        commitParent();
      },
      onLatestError: (error) => {
        showToast(error instanceof Error ? error.message : "课程数据加载失败", "warning");
      },
      onPendingChange: setPendingBookId
    });
  }, [bookcourseRepository, clearCourseSession, showToast]);
  sourceSelectorRef.current = selectSource;

  const selectCourse = useCallback(async (courseId: string) => {
    const store = coursesRef.current;
    const state = store.getState();
    const course = state.courses.find((item) => item.id === courseId);
    if (!course) return false;
    const source = preferredCourseSource(course, state.resources, sourceSummariesRef.current);
    if (source?.bookId) return selectSource(source.bookId, courseId);
    cancelSourceSelection();
    clearLoadedCourse();
    clearCourseSession();
    store.setActiveCourse(courseId);
    return true;
  }, [cancelSourceSelection, clearCourseSession, clearLoadedCourse, selectSource]);

  const importsRef = useRef(new Map<string, Promise<void>>());
  const importCourseFile = useCallback(async (file: File, courseId?: string) => {
    const resource = await coursesRef.current.addLocalFile(file, courseId);
    if (resource.status === "ready") return;
    const existing = importsRef.current.get(resource.id);
    if (existing) return existing;
    const operation = (async () => {
      try {
        coursesRef.current.updateResource(resource.id, { status: "processing", progress: 0, error: undefined });
        const upload = await bookcourseRepository.initUpload({filename:file.name, content_type:file.type, size_bytes:file.size});
        await bookcourseRepository.uploadFile(upload.book_id, file);
        const job = await bookcourseRepository.startParse(upload.book_id);
        coursesRef.current.updateResource(resource.id, { bookId:upload.book_id, status:"processing" });
        let status = await bookcourseRepository.getJob(job.job_id);
        while(status.status !== "done" && status.status !== "failed") {
          await new Promise(resolve=>setTimeout(resolve,1500));
          status = await bookcourseRepository.getJob(job.job_id);
          coursesRef.current.updateResource(resource.id,{progress:status.progress});
        }
        if(status.status === "failed")throw new Error(status.error || "资料解析失败");
        coursesRef.current.updateResource(resource.id, { status: "ready", progress: 100, bookId: upload.book_id });
        await refreshSources();
      } catch (error) {
        const message = error instanceof Error ? error.message : "资料整理失败，原文件已保存，可以重试";
        coursesRef.current.updateResource(resource.id, { status: "error", error: message });
        throw error;
      } finally { importsRef.current.delete(resource.id); }
    })();
    importsRef.current.set(resource.id, operation);
    return operation;
  }, [refreshSources, bookcourseRepository]);

  useEffect(() => {
    void refreshSources();
  }, [refreshSources, bookcourseRepository]);

  useEffect(() => {
    // A locally chosen or freshly uploaded file is an explicit user decision.
    // Do not let an older resumable job from the course list replace it while
    // the learner is selecting or confirming the new upload.
    if (
      parseJobId ||
      uploadedFile ||
      navigationRef.current.screen === "upload" ||
      navigationRef.current.screen === "parseReady"
    ) return;
    const resumable = sourceSummaries.find(
      (course) => course.parse_job_id && ["pending", "processing", "failed"].includes(course.parse_job_status ?? "")
    );
    if (!resumable?.parse_job_id || !resumable.parse_job_status) return;

    setUploadedFile({
      bookId: resumable.book_id,
      name: resumable.filename || resumable.title,
      sizeBytes: 0,
      contentType: resumable.filename?.toLowerCase().endsWith(".pdf") ? "application/pdf" : "application/octet-stream",
      uploadedAt: resumable.updated_at ? resumable.updated_at * 1000 : Date.now(),
      origin: "remote-course"
    });
    setParseJobId(resumable.parse_job_id);
    setParseJobStatus({
      job_id: resumable.parse_job_id,
      book_id: resumable.book_id,
      status: resumable.parse_job_status,
      stage: resumable.parse_job_stage ?? "pending",
      progress: resumable.parse_job_progress ?? 0,
      message: resumable.parse_job_message,
      error: resumable.parse_job_error
    });
  }, [sourceSummaries, parseJobId, uploadedFile]);

  useEffect(() => {
    if (!parseJobId || !uploadedFile || completedParseJobRef.current === parseJobId) return;

    const activeParseJobId = parseJobId;
    const activeBookId = uploadedFile.bookId;
    const parseGeneration = parseSessionGenerationRef.current;
    let active = true;
    let timer: number | undefined;

    function isCurrentParseSession() {
      return active && parseGeneration === parseSessionGenerationRef.current;
    }

    async function loadParsedCourse(bookId: string) {
      const [scanResult, nextChapters, nextChunks, nextAssets, plan, lessons, cards, quizzes] = await Promise.all([
        bookcourseRepository.getScanResult(bookId),
        bookcourseRepository.getChapters(bookId),
        bookcourseRepository.getChunks(bookId),
        bookcourseRepository.getAssets(bookId),
        bookcourseRepository.getStudyPlan(bookId, runtimeConfig.defaultUserId),
        bookcourseRepository.getLessons(bookId),
        bookcourseRepository.getFlashcards(bookId),
        bookcourseRepository.getQuizzes(bookId)
      ]);
      if (!isCurrentParseSession()) return;
      loadedBookIdRef.current = bookId;
      parsedChaptersRef.current = nextChapters;
      loadedCourseContextRef.current = {
        loadedBookId: bookId,
        uploadedFile: uploadedFileRef.current,
        parsedScanResult: scanResult,
        parsedChapters: nextChapters,
        parsedChunks: nextChunks,
        parsedAssets: nextAssets,
        currentStudyPlan: plan,
        generatedLessons: lessons,
        generatedFlashcards: cards,
        generatedQuizzes: quizzes
      };
      setParsedScanResult(scanResult);
      setParsedChapters(nextChapters);
      setParsedChunks(nextChunks);
      setParsedAssets(nextAssets);
      setCurrentStudyPlan(plan);
      setGeneratedLessons(lessons);
      setGeneratedFlashcards(cards);
      setGeneratedQuizzes(quizzes);
      setActiveChapterId(nextChapters[0]?.chapter_id ?? null);
      setAnswer("");
      setSourcePageTarget(null);
      setLoadedBookId(bookId);
    }

    const pollIntervalMs = false
      ? demoParseJobPollIntervalMs
      : parseJobPollIntervalMs;
    const retryIntervalMs = false
      ? demoParseJobRetryIntervalMs
      : parseJobRetryIntervalMs;

    async function pollParseJob() {
      try {
        const job = await bookcourseRepository.getJob(activeParseJobId);
        if (!isCurrentParseSession()) return;
        setParseJobStatus(job);

        if (job.status === "done") {
          await loadParsedCourse(activeBookId);
          if (!isCurrentParseSession()) return;
          const setDraft = coursesRef.current.getState().draft;
          const continuesCourse = Boolean(
            setDraft && !setDraft.editingCourseId
            && setDraft.uploadedCourse?.bookId === activeBookId
            && setDraft.resourceIds.includes(sourceResourceId(activeBookId))
          );
          if (continuesCourse && setDraft) {
            coursesRef.current.updateDraft({
              step: Math.max(0, setDraft.step),
              parseJobId: activeParseJobId,
              parseCompleted: true
            });
          }
          completedParseJobRef.current = activeParseJobId;
          void refreshSources();
          if (navigationRef.current.screen === "parseReady" || navigationRef.current.screen === "processing") {
            replaceScreen(continuesCourse ? "courseSetup" : "chapterConfirm");
          }
          return;
        }

        if (job.status === "failed") {
          return;
        }

        timer = window.setTimeout(pollParseJob, pollIntervalMs);
      } catch {
        if (!isCurrentParseSession()) return;
        timer = window.setTimeout(pollParseJob, retryIntervalMs);
      }
    }

    void pollParseJob();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [bookcourseRepository, parseJobId, refreshSources, replaceScreen, uploadedFile]);

  const sharedProps = useMemo(
    () => ({
      courses,
      logout,
      go,
      replaceScreen,
      back,
      openSourcePage,
      startNote,
      finishNoteCapture,
      openSheet,
      closeSheet,
      showToast,
      selectSource,
      selectCourse,
      importCourseFile,
      updateStudyLocation,
      demoShelfEnabled: false,
      selectedUpload,
      setSelectedUpload,
      uploadedFile,
      setUploadedFile,
      parseJobId,
      setParseJobId,
      parseJobStatus,
      setParseJobStatus,
      sourceSummaries,
      sourceSummariesLoadState,
      sourceSummariesReadyKind,
      sourceSummariesError,
      sourceSummariesRefreshing,
      selectedCommunityBookId,
      selectCommunityBook,
      loadedBookId,
      clearLoadedCourse,
      clearCourseSession,
      pendingBookId,
      sourceSelectionLoadingId: pendingBookId,
      cancelSourceSelection,
      refreshSources,
      parsedScanResult,
      setParsedScanResult,
      parsedChapters,
      setParsedChapters,
      parsedChunks,
      setParsedChunks,
      parsedAssets,
      setParsedAssets,
      generatedLessons,
      setGeneratedLessons,
      lessonBuildJobId,
      setLessonBuildJobId,
      lessonBuildJobStatus,
      setLessonBuildJobStatus,
      generatedFlashcards,
      setGeneratedFlashcards,
      generatedQuizzes,
      setGeneratedQuizzes,
      activeChapterId,
      setActiveChapterId,
      currentStudyPlan,
      setCurrentStudyPlan,
      latestDiagnosis,
      setLatestDiagnosis,
      answer,
      setAnswer,
      savedNoteCount,
      setSavedNoteCount,
      sourcePageTarget,
      sourceReaderCurrentPage,
      setSourceReaderCurrentPage,
      noteCaptureIntent,
      studyLocations
    }),
    [
      courses,
      activeChapterId,
      answer,
      back,
      bookcourseRepository,
      cancelSourceSelection,
      clearCourseSession,
      clearLoadedCourse,
      closeSheet,
      currentStudyPlan,
      logout,
      go,
      replaceScreen,
      openSheet,
      openSourcePage,
      startNote,
      finishNoteCapture,
      latestDiagnosis,
      lessonBuildJobId,
      lessonBuildJobStatus,
      sourceSummaries,
      sourceSummariesError,
      sourceSummariesLoadState,
      sourceSummariesReadyKind,
      sourceSummariesRefreshing,
      selectedCommunityBookId,
      selectCommunityBook,
      loadedBookId,
      pendingBookId,
      generatedFlashcards,
      generatedLessons,
      generatedQuizzes,
      parseJobStatus,
      parseJobId,
      parsedAssets,
      parsedChapters,
      parsedChunks,
      parsedScanResult,
      refreshSources,
      savedNoteCount,
      selectedUpload,
      selectSource,
      selectCourse,
      importCourseFile,
      showToast,
      sourcePageTarget,
      sourceReaderCurrentPage,
      noteCaptureIntent,
      studyLocations,
      updateStudyLocation,
      uploadedFile
    ]
  );

  const requestedSheetView = useMemo<ActionSheetView | null>(() => {
    if (!sheet) return null;

    if (sheet.type === "chat") {
      return {
        key: "chat",
        sheet,
        title: "问 AI",
        content: (
          <ChatSheetContent
            activeChapterId={activeChapterId}
            openSourcePage={openSourcePage}
            parsedChapters={parsedChapters}
            uploadedFile={uploadedFile}
          />
        )
      };
    }

    if (sheet.type === "note") {
      return {
        key: `note:${sheet.kind ?? "concept"}:${sheet.concept}:${sheet.quote?.slice(0, 24) ?? ""}`,
        sheet,
        title: sheet.kind === "selection" ? "摘录笔记" : "核心概念",
        content: (
          <NoteSheetContent
            concept={sheet.concept}
            kind={sheet.kind}
            quote={sheet.quote}
            explanation={sheet.explanation}
            sourceLabel={sheet.sourceLabel}
            image={sheet.image}
            imageCaption={sheet.imageCaption}
            onOpenSource={sheet.source ? () => openSourcePage(sheet.source!) : undefined}
            setSavedNoteCount={setSavedNoteCount}
            closeSheet={closeSheet}
            showToast={showToast}
          />
        )
      };
    }

    if (sheet.type === "noteType") {
      return {
        key: `noteType:${sheet.intent.source?.bookId ?? "free"}:${sheet.intent.anchor?.pageStart ?? "none"}`,
        sheet,
        title: "记笔记",
        content: (
          <NoteTypeSheetContent
            contextLabel={sheet.contextLabel}
            inkAvailable={sheet.inkAvailable}
            pageOptions={sheet.pageOptions}
            initialPage={sheet.intent.source?.pageStart ?? sheet.intent.anchor?.pageStart}
            onChoose={(kind, page) => {
              const printedPage = page && sheet.intent.source?.printedPageStart != null
                ? sheet.intent.source.printedPageStart + page - sheet.intent.source.pageStart
                : undefined;
              const source = page && sheet.intent.source
                ? { ...sheet.intent.source, pageStart: page, pageEnd: page, printedPageStart: printedPage, printedPageEnd: printedPage }
                : sheet.intent.source;
              const anchor = page && sheet.intent.anchor
                ? { ...sheet.intent.anchor, pageStart: page, pageEnd: page, printedPageStart: printedPage, printedPageEnd: printedPage }
                : sheet.intent.anchor;
              startNote({ ...sheet.intent, source, anchor, kind });
            }}
          />
        )
      };
    }

    if (sheet.type === "bookSwitcher") {
      return {
        key: "bookSwitcher",
        sheet,
        title: "切换课程",
        content: <BookSwitcherSheetContent />
      };
    }

    if (sheet.type === "addMaterials") return { key: `addMaterials:${sheet.courseId}`, sheet, title: "添加课程资料", content: <AddMaterialsSheetContent courseId={sheet.courseId} /> };

    const chapter = parsedChapters?.find((item) => item.chapter_id === sheet.chapterId);
    if (!chapter || !parsedChapters) return null;
    const chapterSnapshot = { ...chapter };
    const chaptersSnapshot = parsedChapters.map((item) => ({ ...item }));
    const evidenceSnapshot = sheet.evidence
      ? { ...sheet.evidence, reasons: [...sheet.evidence.reasons] }
      : undefined;

    return {
      key: `editChapter:${sheet.chapterId}`,
      sheet,
      title: "编辑章节",
      content: (
        <EditChapterSheetContent
          chapter={chapterSnapshot}
          chapters={chaptersSnapshot}
          evidence={evidenceSnapshot}
          pageCount={parsedScanResult?.page_count}
          closeSheet={closeSheet}
          onSave={(nextChapter) => {
            setParsedChapters((current) => current?.map((item) => (
              item.chapter_id === nextChapter.chapter_id ? nextChapter : item
            )) ?? null);
            closeSheet();
          }}
          onDelete={(chapterIds) => {
            const removalIds = new Set(chapterIds);
            setParsedChapters((current) => current?.filter((item) => !removalIds.has(item.chapter_id)) ?? null);
            closeSheet();
          }}
        />
      )
    };
  }, [
    activeChapterId,
    closeSheet,
    openSheet,
    openSourcePage,
    startNote,
    parsedChapters,
    parsedScanResult?.page_count,
    sheet,
    showToast,
    uploadedFile
  ]);

  const sheetPresence = useMotionPresence({
    requested: requestedSheetView,
    getKey: getSheetViewKey,
    reducedMotion,
    motionNames: actionSheetAnimationNames,
    maxMotionMs: globalMotionFallbackMs
  });

  const sourceHeading = resolveSourceReaderHeading(
    sourcePageTarget,
    sourceReaderCurrentPage,
    parsedChapters,
    parsedScanResult,
    uploadedFile?.name?.trim() || "教材原文"
  );
  const header = screen === "lesson"
    ? {
        ...titles.lesson,
        subtitle: lessonHeaderSubtitle(parsedChapters, activeChapterId)
      }
    : screen === "source"
      ? { ...titles.source, title: sourceHeading.title, subtitle: sourceHeading.pageLabel }
    : screen === "courseDetail"
      ? { ...titles.courseDetail, title: courses.state.courses.find((item) => item.id === courses.state.activeCourseId)?.name ?? "课程" }
    : titles[screen];

  function renderScreen() {
    switch (screen) {
      case "home":
        return <HomeScreen />;
      case "onboarding":
        return <OnboardingScreen />;
      case "courseSetup":
        return <CourseSetupScreen />;
      case "courseDetail":
        return <CourseDetailScreen />;
      case "upload":
        return <UploadScreen />;
      case "parseReady":
        return <ParseReadyScreen />;
      case "processing":
        return <ProcessingScreen />;
      case "chapterConfirm":
        return <ChapterConfirmScreen />;
      case "courseReady":
        return <CourseReadyScreen />;
      case "library":
        return <LibraryScreen />;
      case "community":
        return <CommunityScreen />;
      case "communityBook":
        return <CommunityBookScreen onImport={beginCommunityImport} />;
      case "communityImport":
        return <CommunityImportScreen importGeneration={communityImportGeneration} />;
      case "study":
        return <StudyScreen />;
      case "book":
        return <StudyScreen />;
      case "plan":
        return <StudyPlanScreen />;
      case "flashcards":
        return <FlashcardScreen />;
      case "lesson":
        return <LessonScreen />;
      case "assignment":
        return <AssignmentScreen />;
      case "diagnosis":
        return <DiagnosisScreen />;
      case "mistakes":
        return <MistakeBookScreen />;
      case "notes":
        return <NotesScreen />;
      case "voiceNote":
        return <VoiceNoteScreen />;
      case "source":
        return <SourceReaderScreen />;
      case "export":
        return <ExportPreviewScreen />;
      case "report":
        return <LessonReportScreen />;
      case "profile":
        return <ProfileScreen />;
      case "settings":
        return <SettingsScreen />;
      default:
        return <HomeScreen />;
    }
  }

  const setMainElement = useCallback((element: HTMLElement | null) => {
    mainRef.current = element;
  }, []);

  const contentScrollTop = navigation.direction === "back"
    ? screenScrollPositionsRef.current.get(screen) ?? 0
    : 0;

  return (
    <AppProvider value={sharedProps}>
      <AppShell
        active={screen}
        motionReduced={reducedMotion}
        focusMainNonce={navigation.nonce}
        contentScrollTop={contentScrollTop}
        onMainElement={setMainElement}
        onClickCapture={captureSheetTrigger}
        overlays={(
          <>
            <ActionSheet
              view={sheetPresence.rendered}
              state={sheetPresence.state}
              presenceId={sheetPresence.presenceId}
              close={closeSheet}
              onAnimationEnd={sheetPresence.onAnimationEnd}
              onAnimationCancel={sheetPresence.onAnimationCancel}
              onExited={restoreSheetFocus}
            />
            <Toast toast={toast} />
          </>
        )}
        title={header.title}
        subtitle={header.subtitle}
        rightAction={screen === "profile" ? (
          <IconButton
            className="profile-header-settings"
            label="设置"
            onClick={() => go("settings")}
          >
            <Settings size={21} aria-hidden="true" />
          </IconButton>
        ) : undefined}
        showBack={header.back}
        hideNav={header.hideNav}
        onBack={back}
        go={go}
      >
        <ScreenTransition
          screenKey={screen}
          direction={navigation.direction}
          nonce={navigation.nonce}
          initial={navigation.nonce === 0}
          reducedMotion={reducedMotion}
        >
          {renderScreen()}
        </ScreenTransition>
      </AppShell>
    </AppProvider>
  );
}
