import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import {
  ActionSheet,
  AppShell,
  Toast,
  actionSheetAnimationNames,
  type ActionSheetView
} from "./components/ui";
import { runtimeConfig, userStorageNamespace } from "./config/runtime";
import { AppProvider } from "./context/AppContext";
import type { CourseSummariesLoadState, CourseSummariesReadyKind } from "./context/AppContext";
import { useBookCourseRepository } from "./context/BookCourseRepositoryContext";
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
  ParseReadyScreen,
  ProcessingScreen,
  ProfileScreen,
  SourceReaderScreen,
  SourceSheetContent,
  StudyScreen,
  StudyPlanScreen,
  UploadScreen
} from "./screens";
import type {
  ApiAsset,
  ApiChapter,
  ApiChunk,
  CourseSummary,
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
import { createCourseSelectionCoordinator } from "./screens/homeBookModel";
import {
  hasCompleteLoadedCourseContext,
  resolveCourseSessionClear,
  shouldClearLoadedCourseAfterRefresh,
  shouldClearLoadedCourseForDeletedBook,
  shouldClearRemoteSessionAfterRefresh,
  type LoadedCourseContext
} from "./screens/courseResourceIdentity";

function studyLocationsStorageKey() {
  return `bookcourse.${userStorageNamespace()}.study-locations.v1`;
}
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
  upload: { title: "上传书籍", back: true, hideNav: true },
  parseReady: { title: "解析教材", back: true, hideNav: true },
  processing: { title: "解析教材", subtitle: "正在识别章节和知识点", back: true, hideNav: true },
  chapterConfirm: { title: "确认目录", subtitle: "核对原书和 AI 课程映射", back: true, hideNav: true },
  courseReady: { title: "生成成功", back: true, hideNav: true },
  library: { title: "我的课程", subtitle: "管理由书生成的 AI 课程" },
  community: { title: "社区", subtitle: "发现来源可验证的真实教材" },
  communityBook: { title: "共享课程", back: true, hideNav: true },
  communityImport: { title: "导入课程", back: true, hideNav: true },
  study: {},
  book: {},
  plan: { title: "学习计划", subtitle: "科学规划，高效学习", back: true, hideNav: true },
  flashcards: { title: "知识点闪卡", subtitle: "回忆、核对，再安排复习", back: true, hideNav: true },
  lesson: { title: "章节学习", subtitle: "第 2 章 1 节", back: true, hideNav: true },
  assignment: { title: "作业练习", subtitle: "按顺序练习，定位卡点", back: true, hideNav: true },
  diagnosis: { title: "作业诊断", subtitle: "看懂原因，马上巩固", back: true, hideNav: true },
  mistakes: { title: "错题集", subtitle: "重做卡点，直到真正掌握", back: true, hideNav: true },
  notes: { title: "导学笔记", subtitle: "沉淀学习产出", back: true, hideNav: true },
  source: { title: "原文文档", subtitle: "定位到引用页", back: true, hideNav: true },
  export: { title: "导出预览", subtitle: "选择要导出的模块", back: true, hideNav: true },
  report: { title: "章节报告", subtitle: "完成后调整计划", back: true, hideNav: true },
  profile: { title: "我的", subtitle: "学习数据与偏好" }
};

const toastQuietScreens = new Set<Screen>([
  "upload",
  "parseReady",
  "processing",
  "chapterConfirm",
  "courseReady",
  "communityImport"
]);

function getSheetViewKey(view: ActionSheetView) {
  return view.key;
}

type OpenSheetState = Exclude<SheetState, null>;

function snapshotSheetState(sheet: OpenSheetState): OpenSheetState {
  switch (sheet.type) {
    case "chat":
      return { type: "chat" };
    case "source":
      return { ...sheet };
    case "note":
      return { ...sheet };
    case "editChapter":
      return {
        ...sheet,
        evidence: sheet.evidence
          ? { ...sheet.evidence, reasons: [...sheet.evidence.reasons] }
          : undefined
      };
    case "bookSwitcher":
      return { type: "bookSwitcher" };
  }
}

export default function App() {
  const bookcourseRepository = useBookCourseRepository();
  const reducedMotion = useReducedMotion();
  const navigationRef = useRef<NavigationSnapshot>(createInitialNavigation());
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
  const [uploadedFile, setUploadedFile] = useState<UploadedCourseFile | null>(null);
  const uploadedFileRef = useRef(uploadedFile);
  uploadedFileRef.current = uploadedFile;
  const [parseJobId, setParseJobId] = useState<string | null>(null);
  const [parseJobStatus, setParseJobStatus] = useState<JobStatusResponse | null>(null);
  const [courseSummaries, setCourseSummaries] = useState<CourseSummary[]>([]);
  const [courseSummariesLoadState, setCourseSummariesLoadState] = useState<CourseSummariesLoadState>("loading");
  const [courseSummariesReadyKind, setCourseSummariesReadyKind] = useState<CourseSummariesReadyKind>("empty");
  const [courseSummariesError, setCourseSummariesError] = useState<string | null>(null);
  const [courseSummariesRefreshing, setCourseSummariesRefreshing] = useState(false);
  const [selectedCommunityBookId, setSelectedCommunityBookId] = useState("");
  const [pendingBookId, setPendingBookId] = useState<string | null>(null);
  const courseSelectionCoordinatorRef = useRef(createCourseSelectionCoordinator());
  const courseSummariesRef = useRef<CourseSummary[]>([]);
  courseSummariesRef.current = courseSummaries;
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
  const [savedNoteCount, setSavedNoteCount] = useState(6);
  const [sourcePageTarget, setSourcePageTarget] = useState<SourcePageTarget | null>(null);
  const [studyLocations, setStudyLocations] = useState<Record<string, StudyLocation>>(loadStudyLocations);
  const studyLocationsRef = useRef(studyLocations);
  studyLocationsRef.current = studyLocations;
  const completedParseJobRef = useRef<string | null>(null);
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
    const nextNavigation = navigate(current, { type: "go", screen: next });
    saveCurrentScreenScrollPosition(current, nextNavigation);
    requestSheetCloseForNavigation(nextNavigation.nonce === current.nonce);
    commitNavigation(() => nextNavigation);
  }, [commitNavigation, requestSheetCloseForNavigation, saveCurrentScreenScrollPosition]);

  const selectCommunityBook = useCallback((bookId: string) => {
    setSelectedCommunityBookId(bookId);
  }, []);

  const back = useCallback(() => {
    const current = navigationRef.current;
    const nextNavigation = navigate(current, { type: "back" });
    saveCurrentScreenScrollPosition(current, nextNavigation);
    requestSheetCloseForNavigation(nextNavigation.nonce === current.nonce);
    commitNavigation(() => nextNavigation);
  }, [commitNavigation, requestSheetCloseForNavigation, saveCurrentScreenScrollPosition]);

  const openSourcePage = useCallback((target: SourcePageTarget) => {
    const current = navigationRef.current;
    const nextNavigation = navigate(current, { type: "source" });
    saveCurrentScreenScrollPosition(current, nextNavigation);
    requestSheetCloseForNavigation(nextNavigation.nonce === current.nonce);
    setSourcePageTarget(target);
    commitNavigation(() => nextNavigation);
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
    }, 3200);
    toastTimerRef.current = timer;
  }, []);

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

  const refreshCourses = useCallback(async () => {
    const hasExistingCourses = courseSummariesRef.current.length > 0;
    if (!hasExistingCourses) setCourseSummariesLoadState("loading");
    setCourseSummariesRefreshing(true);
    try {
      const courses = await bookcourseRepository.listCourses();
      setCourseSummaries(courses);
      setCourseSummariesReadyKind(courses.length > 0 ? "content" : "empty");
      setCourseSummariesLoadState("ready");
      setCourseSummariesError(null);

      const activeUploadedFile = uploadedFileRef.current;
      if (shouldClearLoadedCourseAfterRefresh(loadedBookIdRef.current, activeUploadedFile, courses)) {
        clearLoadedCourse();
      }
      if (shouldClearRemoteSessionAfterRefresh(activeUploadedFile, courses)) {
        clearCourseSession(activeUploadedFile?.bookId);
      }
    } catch (err) {
      setCourseSummariesError(err instanceof Error ? err.message : "课程列表加载失败");
      if (!hasExistingCourses) setCourseSummariesLoadState("error");
    } finally {
      setCourseSummariesRefreshing(false);
    }
  }, [bookcourseRepository, clearCourseSession, clearLoadedCourse]);

  const updateStudyLocation = useCallback((bookId: string, location: Partial<StudyLocation>) => {
    setStudyLocations((current) => ({
      ...current,
      [bookId]: {
        expandedChapterId: current[bookId]?.expandedChapterId ?? null,
        expandedSectionId: current[bookId]?.expandedSectionId ?? null,
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

  const cancelCourseSelection = useCallback(() => {
    courseSelectionCoordinatorRef.current.invalidate();
    setPendingBookId(null);
  }, []);

  const selectCourse = useCallback(async (bookId: string) => {
    if (hasCompleteLoadedCourseContext(loadedCourseContextRef.current, bookId)) {
      courseSelectionCoordinatorRef.current.invalidate();
      setPendingBookId(null);
      return true;
    }
    const summary = courseSummariesRef.current.find((course) => course.book_id === bookId);
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
        const storedLocation = studyLocationsRef.current[bookId];
        const chapterIds = new Set(chapters.map((chapter) => chapter.chapter_id));
        const activeId = storedLocation?.expandedSectionId && chapterIds.has(storedLocation.expandedSectionId)
          ? storedLocation.expandedSectionId
          : lessons[0]?.chapter_id ?? chapters.find((chapter) => chapter.level > 1)?.chapter_id ?? chapters[0]?.chapter_id ?? null;
        const selectedCourseFile: UploadedCourseFile = {
          bookId,
          name: scan.filename || summary?.title || "已选择教材",
          sizeBytes: 0,
          contentType: scan.file_type === "pdf" ? "application/pdf" : scan.file_type,
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
      },
      onLatestError: (error) => {
        showToast(error instanceof Error ? error.message : "课程数据加载失败", "warning");
      },
      onPendingChange: setPendingBookId
    });
  }, [bookcourseRepository, clearCourseSession, showToast]);

  useEffect(() => {
    void refreshCourses();
  }, [refreshCourses]);

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
    const resumable = courseSummaries.find(
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
  }, [courseSummaries, parseJobId, uploadedFile]);

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

    const pollIntervalMs = runtimeConfig.useDemoRepository
      ? demoParseJobPollIntervalMs
      : parseJobPollIntervalMs;
    const retryIntervalMs = runtimeConfig.useDemoRepository
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
          completedParseJobRef.current = activeParseJobId;
          void refreshCourses();
          if (navigationRef.current.screen === "parseReady" || navigationRef.current.screen === "processing") {
            replaceScreen("chapterConfirm");
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
  }, [bookcourseRepository, parseJobId, refreshCourses, replaceScreen, uploadedFile]);

  const sharedProps = useMemo(
    () => ({
      go,
      back,
      openSourcePage,
      openSheet,
      closeSheet,
      showToast,
      selectCourse,
      updateStudyLocation,
      selectedUpload,
      setSelectedUpload,
      uploadedFile,
      setUploadedFile,
      parseJobId,
      setParseJobId,
      parseJobStatus,
      setParseJobStatus,
      courseSummaries,
      courseSummariesLoadState,
      courseSummariesReadyKind,
      courseSummariesError,
      courseSummariesRefreshing,
      selectedCommunityBookId,
      selectCommunityBook,
      loadedBookId,
      clearLoadedCourse,
      clearCourseSession,
      pendingBookId,
      courseSelectionLoadingId: pendingBookId,
      cancelCourseSelection,
      refreshCourses,
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
      studyLocations
    }),
    [
      activeChapterId,
      answer,
      back,
      cancelCourseSelection,
      clearCourseSession,
      clearLoadedCourse,
      closeSheet,
      currentStudyPlan,
      go,
      openSheet,
      openSourcePage,
      latestDiagnosis,
      lessonBuildJobId,
      lessonBuildJobStatus,
      courseSummaries,
      courseSummariesError,
      courseSummariesLoadState,
      courseSummariesReadyKind,
      courseSummariesRefreshing,
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
      refreshCourses,
      savedNoteCount,
      selectedUpload,
      selectCourse,
      showToast,
      sourcePageTarget,
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
            openSheet={openSheet}
            parsedChapters={parsedChapters}
            uploadedFile={uploadedFile}
          />
        )
      };
    }

    if (sheet.type === "source") {
      return {
        key: `source:${sheet.title}:${sheet.page}`,
        sheet,
        title: "查看原文",
        content: (
          <SourceSheetContent
            title={sheet.title}
            page={sheet.page}
            image={sheet.image}
            text={sheet.text}
            onCreateNote={(quote) => openSheet({
              type: "note",
              kind: "selection",
              concept: sheet.title,
              quote,
              sourceLabel: sheet.page
            })}
            onOpenFullSource={sheet.source ? () => openSourcePage(sheet.source!) : undefined}
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

    if (sheet.type === "bookSwitcher") {
      return {
        key: "bookSwitcher",
        sheet,
        title: "切换教材",
        content: <BookSwitcherSheetContent />
      };
    }

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

  const header = titles[screen];

  function renderScreen() {
    switch (screen) {
      case "home":
        return <HomeScreen />;
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
        return <CommunityBookScreen />;
      case "communityImport":
        return <CommunityImportScreen />;
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
      case "source":
        return <SourceReaderScreen />;
      case "export":
        return <ExportPreviewScreen />;
      case "report":
        return <LessonReportScreen />;
      case "profile":
        return <ProfileScreen />;
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
