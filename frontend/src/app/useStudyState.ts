import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAccount } from "../components/AccountGate";
import { captureStorage } from "../components/bookContext";
import { messageNonce } from "../components/socialState";
import { ApiError } from "../api/transport";
import type { BookCatalogItem, BookStructure, Course, CourseActivity, InterviewResponse, QAResult, UserProfile } from "../types/api";
import { studyRepository } from "../services/http/studyRepository";
import type { StudyMistake } from "../types/studyWorkspace";

const bookKey = "zhiwo.active-book";
const sessionKey = "zhiwo.active-session";
const newEventId = () => `event_${messageNonce()}`;

export function useStudyState() {
  const { state: account } = useAccount();
  const { safeGet, safeSet } = useMemo(captureStorage, [account.user_id]);
  const [books, setBooks] = useState<BookCatalogItem[]>([]);
  const [book, setBook] = useState<BookCatalogItem | null>(null);
  const [structure, setStructure] = useState<BookStructure | null>(null);
  const [session, setSession] = useState<InterviewResponse | null>(null);
  const [course, setCourse] = useState<Course | null>(null);
  const [courseStale, setCourseStale] = useState(false);
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null);
  const [qaResult, setQaResult] = useState<QAResult | null>(null);
  const [qaQuestion, setQaQuestion] = useState("");
  const [initializing, setInitializing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [activity, setActivity] = useState<CourseActivity | null>(null);
  const [returnRevision, setReturnRevision] = useState(0);
  const contextEpoch = useRef(0);
  const eventIds = useRef(new Map<string, string>());
  const responseStarted = useRef(Date.now());

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(""), 4000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const sessionIdFor = useCallback((bookId: string) => safeGet(`${sessionKey}:${bookId}`) || account.learning_sessions[bookId] || null,
    [account.learning_sessions, safeGet]);

  const load = useCallback(async (preferred?: string, spinner = true) => {
    const ticket = ++contextEpoch.current;
    if (spinner) setInitializing(true);
    setError("");
    try {
      const catalog = await studyRepository.library.list();
      const desired = preferred || safeGet(bookKey);
      if (preferred && !catalog.some(item => item.book_id === preferred)) {
        throw new Error("教材已处理完成，但暂时未出现在当前书架。请重试刷新书架。");
      }
      const candidate = catalog.find(item => item.book_id === desired) || catalog[0] || null;
      const savedId = candidate ? sessionIdFor(candidate.book_id) : null;
      const [nextStructure, restored] = candidate ? await Promise.all([
        studyRepository.library.structure(candidate.book_id),
        savedId ? studyRepository.learning.resume(savedId).catch(value => {
          if (value instanceof ApiError && (value.status === 404 || value.status === 403)) return null;
          throw value;
        }) : Promise.resolve(null),
      ]) : [null, null];
      if (ticket !== contextEpoch.current) return false;
      const matched = restored?.profile.book_id === candidate?.book_id ? restored : null;
      setBooks(catalog); setBook(candidate); setStructure(nextStructure); setSession(matched);
      setCourse(null); setCourseStale(false); setActivity(null); setQaResult(null); setQaQuestion("");
      safeSet(bookKey, candidate?.book_id ?? null);
      safeSet(sessionKey, matched?.session_id ?? null);
      return true;
    } catch (value) {
      if (ticket === contextEpoch.current) setError((value as Error).message);
      return false;
    } finally {
      if (ticket === contextEpoch.current) {
        // A quiet refresh can supersede boot or an older request. It owns the
        // visible context and must release both locks when it settles.
        setInitializing(false);
        setBusy(false);
      }
    }
  }, [safeGet, safeSet, sessionIdFor]);

  useEffect(() => {
    void load();
    return () => { ++contextEpoch.current; };
  }, [load]);

  useEffect(() => {
    let live = true;
    void studyRepository.profile.get().then(value => { if (live) setUserProfile(value); }).catch(() => {});
    return () => { live = false; };
  }, [account.user_id]);

  const selectBook = useCallback(async (bookId: string) => {
    if (bookId === book?.book_id) return true;
    const candidate = books.find(item => item.book_id === bookId);
    if (!candidate) { setError("这本教材不在当前书架中，请刷新后重试。"); return false; }
    const ticket = ++contextEpoch.current;
    setBusy(true); setError("");
    try {
      const savedId = sessionIdFor(bookId);
      const [nextStructure, restored] = await Promise.all([
        studyRepository.library.structure(bookId),
        savedId ? studyRepository.learning.resume(savedId).catch(value => {
          if (value instanceof ApiError && (value.status === 404 || value.status === 403)) return null;
          throw value;
        }) : Promise.resolve(null),
      ]);
      if (ticket !== contextEpoch.current) return false;
      const matched = restored?.profile.book_id === bookId ? restored : null;
      setBook(candidate); setStructure(nextStructure); setSession(matched); setCourse(null); setCourseStale(false);
      setActivity(null); setQaResult(null); setQaQuestion(""); eventIds.current.clear();
      safeSet(bookKey, bookId); safeSet(sessionKey, matched?.session_id ?? null);
      setNotice(`已切换到《${candidate.title}》`);
      return true;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return false; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [book?.book_id, books, safeSet, sessionIdFor]);

  const refreshLibrary = useCallback(async (preferred?: string) => {
    return await load(preferred, false);
  }, [load]);

  const removeBook = useCallback(async (bookId: string) => {
    const ticket = contextEpoch.current;
    const selectedId = book?.book_id;
    let completionEpoch = ticket;
    setBusy(true); setError("");
    try {
      await studyRepository.library.remove(bookId);
      if (ticket !== contextEpoch.current) return;
      const refresh = load(selectedId === bookId ? undefined : selectedId, false);
      completionEpoch = contextEpoch.current;
      await refresh;
      if (completionEpoch === contextEpoch.current) setNotice("教材已移出书架；学习记录仍保留。");
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); throw value; }
    finally { if (completionEpoch === contextEpoch.current) setBusy(false); }
  }, [book?.book_id, load]);

  const acceptSession = useCallback((value: InterviewResponse) => {
    if (value.profile.book_id !== book?.book_id) return false;
    setSession(value); safeSet(sessionKey, value.session_id); safeSet(`${sessionKey}:${value.profile.book_id}`, value.session_id);
    responseStarted.current = Date.now();
    return true;
  }, [book?.book_id, safeSet]);

  const startDiagnosis = useCallback(async () => {
    if (!book || busy) return false;
    if (book.diagnostics_ready === false) { setNotice("诊断题仍在准备，可以先读目录或向教材提问。"); return false; }
    const ticket = contextEpoch.current;
    setBusy(true); setError("");
    try {
      const next = await studyRepository.learning.start(book.book_id, account.user_id);
      if (ticket !== contextEpoch.current || !acceptSession(next)) return false;
      setCourse(null); setCourseStale(false); setActivity(null); eventIds.current.clear();
      return true;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return false; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [book, busy, account.user_id, acceptSession]);

  const submitDiagnosis = useCallback(async (selected: string[], answer: string, confidence: number) => {
    if (!session || busy) return null;
    const ticket = contextEpoch.current;
    const turn = session.turn;
    setBusy(true); setError("");
    try {
      let next: InterviewResponse;
      if (turn.phase === "profile_confirmation") {
        next = await studyRepository.learning.confirm(session.session_id, !selected.includes("edit"));
      } else if (turn.phase === "adaptive_diagnosis" && !turn.item) {
        next = await studyRepository.learning.next(session.session_id);
      } else if (["book_briefing", "goal_discovery", "background_discovery", "constraint_discovery"].includes(turn.phase)) {
        const labels = turn.options.filter(option => selected.includes(option.id)).map(option => option.label);
        next = await studyRepository.learning.profileAnswer(session.session_id, answer.trim() || labels.join("、"), selected);
      } else if (turn.item) {
        const labels = turn.options.filter(option => selected.includes(option.id)).map(option => option.label);
        const result = await studyRepository.learning.respond(session.session_id, {
          item_id: turn.item.item_id,
          answer: answer.trim() || labels.join("、") || "不确定",
          selected_option_ids: selected, confidence,
          response_seconds: Math.max(1, (Date.now() - responseStarted.current) / 1000), hints_used: 0, revisions: 0,
        });
        next = { ...session, phase: result.next_turn.phase, turn: result.next_turn, profile: result.profile };
      } else return null;
      if (ticket !== contextEpoch.current || !acceptSession(next)) return null;
      if (next.phase === "complete") setNotice("学习方向已保存，可以选择章节开始课程。");
      return next;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return null; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [session, busy, acceptSession]);

  const openCourse = useCallback(async (chapterId: string) => {
    if (!session || session.phase !== "complete" || busy) return false;
    const ticket = contextEpoch.current;
    setBusy(true); setError("");
    try {
      let next: Course;
      try { next = await studyRepository.learning.getCourse(session.session_id, chapterId); }
      catch (value) {
        if (!(value instanceof ApiError) || value.status !== 404) throw value;
        try { next = await studyRepository.learning.course(session.session_id, chapterId); }
        catch (failure) {
          if (!(failure instanceof ApiError) || failure.status !== 408) throw failure;
          // A synchronous compile may finish after the request timed out.
          next = await studyRepository.learning.getCourse(session.session_id, chapterId);
        }
      }
      if (ticket !== contextEpoch.current || next.chapter_id !== chapterId) return false;
      setCourse(next); setActivity(null); setCourseStale(false); eventIds.current.clear();
      responseStarted.current = Date.now();
      return true;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return false; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [session, busy]);

  const updateCourse = useCallback(async () => {
    if (!session || !course || busy) return false;
    const ticket = contextEpoch.current;
    setBusy(true); setError("");
    try {
      let next: Course;
      try { next = await studyRepository.learning.course(session.session_id, course.chapter_id); }
      catch (value) {
        if (!(value instanceof ApiError) || value.status !== 408) throw value;
        next = await studyRepository.learning.getCourse(session.session_id, course.chapter_id);
      }
      if (ticket !== contextEpoch.current) return false;
      const changed = next.course_id !== course.course_id || next.version !== course.version;
      setCourse(next); setCourseStale(false); setActivity(null); eventIds.current.clear();
      setNotice(changed ? `课程已更新到第 ${next.version} 版；旧版学习证据仍保留。` : "课程已与当前学习画像同步，无需更新。");
      return true;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return false; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [session, course, busy]);

  const eventFor = useCallback((kind: string, id: string, targetCourse: Pick<Course, "course_id" | "version"> | null = course) => {
    const key = `${session?.session_id}:${targetCourse?.course_id}:${targetCourse?.version}:${kind}:${id}`;
    let value = eventIds.current.get(key);
    if (!value) {
      // Keep the same logical submission id across a lost response, navigation,
      // and a page reload. A new session or course version gets a new key.
      value = safeGet(`cloudpath.next.event:${key}`) || newEventId();
      eventIds.current.set(key, value);
      safeSet(`cloudpath.next.event:${key}`, value);
    }
    return { key, value };
  }, [session?.session_id, course?.course_id, course?.version, safeGet, safeSet]);

  const review = useCallback(async (cardId: string, rating: "again" | "hard" | "good" | "easy", seconds: number) => {
    if (!session || !course || busy) return null;
    const ticket = contextEpoch.current;
    const event = eventFor("card", cardId);
    setBusy(true); setError("");
    try {
      const result = await studyRepository.learning.review(session.session_id, course.course_id, cardId, {
        event_id: event.value, rating, response_seconds: Math.max(1, seconds),
      });
      if (ticket !== contextEpoch.current) return null;
      // Once the server confirms this review, a later revisit is a distinct
      // FSRS review. Keep the id only while the outcome is unknown/retriable.
      eventIds.current.delete(event.key);
      safeSet(`cloudpath.next.event:${event.key}`, null);
      setActivity(result); setCourseStale(value => value || result.course_stale);
      setSession({ ...session, profile: result.profile }); setReturnRevision(value => value + 1);
      responseStarted.current = Date.now();
      return result;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return null; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [session, course, busy, eventFor, safeSet]);

  const submitPractice = useCallback(async (itemId: string, answer: string, selected: string[], confidence: number, seconds: number) => {
    if (!session || !course || busy) return null;
    const ticket = contextEpoch.current;
    const event = eventFor("practice", itemId);
    setBusy(true); setError("");
    try {
      const result = await studyRepository.learning.practice(session.session_id, course.course_id, itemId, {
        event_id: event.value, answer: answer.trim(), selected_option_ids: selected,
        confidence, response_seconds: Math.max(1, seconds), hints_used: 0, revisions: 0,
      });
      if (ticket !== contextEpoch.current) return null;
      // The current answer has a confirmed outcome. A later answer to this
      // item is a new attempt, while an unknown outcome keeps its retry id.
      eventIds.current.delete(event.key);
      safeSet(`cloudpath.next.event:${event.key}`, null);
      setActivity(result); setCourseStale(value => value || result.course_stale);
      setSession({ ...session, profile: result.profile }); responseStarted.current = Date.now();
      return result;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return null; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [session, course, busy, eventFor, safeSet]);

  const ask = useCallback(async (question: string) => {
    if (!book || busy || !question.trim()) return null;
    const ticket = contextEpoch.current;
    setBusy(true); setError(""); setQaResult(null); setQaQuestion(question.trim());
    try {
      const next = await studyRepository.qa.ask(book.book_id, question.trim());
      if (ticket !== contextEpoch.current) return null;
      setQaResult(next); return next;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return null; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [book, busy]);

  const retryMistake = useCallback(async (mistake: StudyMistake, answer: string, selected: string[], confidence: number, seconds: number) => {
    if (!session || !mistake.can_retry || mistake.course_version === null || busy) return null;
    const ticket = contextEpoch.current;
    const event = eventFor("practice", mistake.item_id, { course_id: mistake.course_id, version: mistake.course_version });
    setBusy(true); setError("");
    try {
      const result = await studyRepository.learning.practice(session.session_id, mistake.course_id, mistake.item_id, {
        event_id: event.value, answer: answer.trim(), selected_option_ids: selected,
        confidence, response_seconds: Math.max(1, seconds), hints_used: 0, revisions: 0,
      });
      if (ticket !== contextEpoch.current) return null;
      eventIds.current.delete(event.key); safeSet(`cloudpath.next.event:${event.key}`, null);
      setActivity(result); setSession({ ...session, profile: result.profile });
      // A mistake may belong to an older bundle; its course_stale flag cannot
      // describe the currently open course. New evidence warrants rechecking it.
      if (course && JSON.stringify(session.profile) !== JSON.stringify(result.profile)) setCourseStale(true);
      setReturnRevision(value => value + 1);
      return result;
    } catch (value) { if (ticket === contextEpoch.current) setError((value as Error).message); return null; }
    finally { if (ticket === contextEpoch.current) setBusy(false); }
  }, [session, course, busy, eventFor, safeSet]);

  return {
    account, repository: studyRepository, books, book, structure, session, course, courseStale, userProfile,
    setUserProfile, qaResult, qaQuestion, initializing, busy, error, setError, notice, setNotice, activity,
    returnRevision, setReturnRevision, load, refreshLibrary, selectBook, removeBook, startDiagnosis,
    submitDiagnosis, openCourse, updateCourse, review, submitPractice, retryMistake, ask,
  };
}
