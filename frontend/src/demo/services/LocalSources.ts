import { strFromU8, unzipSync } from "fflate";
import type { ApiChapter, ApiChunk, CourseSourceSummary, Lesson, ScanResult, StudyPlan } from "../types/api";

export type LocalSource = {
  bookId: string;
  resourceId: string;
  summary: CourseSourceSummary;
  scan: ScanResult;
  chapters: ApiChapter[];
  chunks: ApiChunk[];
  lessons: Lesson[];
  plan: StudyPlan;
};

let database: Promise<IDBDatabase> | null = null;
function openDatabase() {
  database ??= new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("bookcourse-local-sources", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("sources", { keyPath: "bookId" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { database = null; reject(request.error); };
  });
  return database;
}
export async function readLocalSource(bookId: string): Promise<LocalSource | null> {
  if (!bookId.startsWith("book_local_") || typeof indexedDB === "undefined") return null;
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db.transaction("sources").objectStore("sources").get(bookId);
    request.onsuccess = () => resolve(request.result ?? null);
    request.onerror = () => reject(request.error);
  });
}
export async function listLocalSources(): Promise<LocalSource[]> {
  if (typeof indexedDB === "undefined") return [];
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db.transaction("sources").objectStore("sources").getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
export async function saveLocalSource(source: LocalSource) {
  const db = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction("sources", "readwrite");
    transaction.objectStore("sources").put(source);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

async function pdfLibrary() {
  const pdf = await import("pdfjs-dist");
  const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
  pdf.GlobalWorkerOptions.workerSrc = worker.default;
  return pdf;
}

function xmlText(bytes: Uint8Array) {
  const xml = new DOMParser().parseFromString(strFromU8(bytes), "application/xml");
  if (xml.querySelector("parsererror")) throw new Error("文件内容无法读取，请重新导出后添加");
  return [...xml.getElementsByTagNameNS("*", "p")].map((paragraph) => paragraph.textContent?.trim()).filter(Boolean).join("\n")
    || xml.documentElement.textContent?.trim() || "";
}

/** Import original content; never substitute another textbook's fixture. */
export async function prepareLocalSource(resourceId: string, file: File, onProgress: (value: number) => void): Promise<LocalSource> {
  const bookId = `book_local_${resourceId}`;
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  const bytes = new Uint8Array(await file.arrayBuffer());
  let pages: string[] = [];
  onProgress(15);
  if (extension === "pdf") {
    const pdf = await pdfLibrary();
    const loading = pdf.getDocument({ data: bytes });
    const document = await loading.promise;
    try {
      for (let index = 1; index <= document.numPages; index++) {
        const content = await (await document.getPage(index)).getTextContent();
        pages.push(content.items.map((item) => "str" in item ? item.str + (item.hasEOL ? "\n" : " ") : "").join("").trim());
        onProgress(15 + Math.round(index / document.numPages * 65));
      }
    } finally { await loading.destroy(); }
  } else if (["docx", "pptx", "xlsx"].includes(extension)) {
    const zip = unzipSync(bytes);
    if (extension === "docx") pages = [xmlText(zip["word/document.xml"] ?? new Uint8Array())];
    if (extension === "pptx") pages = Object.keys(zip).filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name)).sort((a, b) => Number(a.match(/slide(\d+)/)?.[1]) - Number(b.match(/slide(\d+)/)?.[1])).map((name) => xmlText(zip[name]));
    if (extension === "xlsx") {
      const stringsXml = zip["xl/sharedStrings.xml"];
      const strings = stringsXml ? [...new DOMParser().parseFromString(strFromU8(stringsXml), "application/xml").getElementsByTagNameNS("*", "si")].map((item) => item.textContent ?? "") : [];
      pages = Object.keys(zip).filter((name) => /^xl\/worksheets\/sheet\d+\.xml$/.test(name)).sort().map((name) => {
        const xml = new DOMParser().parseFromString(strFromU8(zip[name]), "application/xml");
        return [...xml.getElementsByTagNameNS("*", "row")].map((row) => [...row.getElementsByTagNameNS("*", "c")].map((cell) => {
          const value = cell.getElementsByTagNameNS("*", "v")[0]?.textContent ?? cell.textContent ?? "";
          return cell.getAttribute("t") === "s" ? strings[Number(value)] ?? "" : value;
        }).join("\t")).join("\n");
      });
    }
  } else if (["txt", "md", "csv"].includes(extension)) pages = [new TextDecoder().decode(bytes)];
  else if (file.type.startsWith("image/") || ["png", "jpg", "jpeg", "webp", "gif"].includes(extension)) pages = [""];
  else throw new Error("原文件已保存。此格式暂不能整理，请添加 PDF、图片或新版 Office 文件。");
  if (!pages.length) throw new Error("未找到可读取的页面，原文件仍保留在课程资料中");
  const hasText = pages.some((text) => text.trim());
  const title = file.name.replace(/\.[^.]+$/, "");
  const rootId = `${bookId}:root`;
  const chapters: ApiChapter[] = [{ chapter_id: rootId, level: 1, parent_id: null, source_title: title, ai_title: title, page_start: 1, page_end: pages.length, confidence: 100, status: "confirmed", source: "local-original" }, ...pages.map((text, index) => ({ chapter_id: `${bookId}:page:${index + 1}`, level: 2, parent_id: rootId, source_title: text.split(/\n/)[0]?.trim().slice(0, 32) || `第 ${index + 1} 页原文`, ai_title: text.split(/\n/)[0]?.trim().slice(0, 32) || `第 ${index + 1} 页原文`, page_start: index + 1, page_end: index + 1, confidence: 100, status: "confirmed", source: "local-original" }))];
  const chunks: ApiChunk[] = pages.flatMap((text, index) => (text.match(/[\s\S]{1,1200}/g) ?? []).map((part, partIndex) => ({ chunk_id: `${bookId}:p${index + 1}:${partIndex}`, book_id: bookId, chapter_id: `${bookId}:page:${index + 1}`, page_start: index + 1, page_end: index + 1, content_type: "text", text: part, asset_ids: [], key_concepts: [], source_metadata: { filename: file.name, parser: "local-original", resource_id: resourceId } })));
  const lessons: Lesson[] = chapters.slice(1).map((chapter) => {
    const sourceChunks = chunks.filter((chunk) => chunk.chapter_id === chapter.chapter_id);
    return { book_id: bookId, lesson_id: `${chapter.chapter_id}:reading`, chapter_id: chapter.chapter_id, title: chapter.source_title, source_title: chapter.source_title, page_start: chapter.page_start, page_end: chapter.page_end, lesson_kind: "lesson", status: "ready", confidence: 100, objectives: [], key_concepts: [], summary: sourceChunks[0]?.text.slice(0, 160) ?? "打开原文查看本页内容", blocks: sourceChunks.map((chunk) => ({ block_id: `${chunk.chunk_id}:block`, block_type: "source", title: "资料原文", content: chunk.text, citations: [{ chunk_id: chunk.chunk_id, page_start: chunk.page_start, page_end: chunk.page_end, quote: chunk.text }], source_chunk_ids: [chunk.chunk_id], asset_ids: [], ai_generated: false })), source_chunk_ids: sourceChunks.map((chunk) => chunk.chunk_id), asset_ids: [], warnings: hasText ? [] : ["此资料没有可提取的文字，可以阅读原文；AI 暂无文字依据。"] };
  });
  const now = Date.now();
  const source: LocalSource = { bookId, resourceId, summary: { book_id: bookId, title, filename: file.name, status: "ready", page_count: pages.length, chapter_count: chapters.length, chunk_count: chunks.length, asset_count: 0, average_confidence: 100, updated_at: now / 1000 }, scan: { book_id: bookId, filename: file.name, file_type: extension, page_count: pages.length, has_text_layer: hasText, needs_ocr: !hasText, source_unit: extension === "pptx" ? "slide" : extension === "xlsx" ? "sheet" : "page", source_locations: pages.map((_, index) => ({ index: index + 1, pdf_page: index + 1 })), quality_warnings: hasText ? [] : [{ code: "no-text", message: "原文可阅读，尚无可提取文字用于 AI 问答" }] }, chapters, chunks, lessons, plan: { book_id: bookId, user_id: "local_user", days: Math.max(1, pages.length), daily_minutes: 25, tasks: lessons.map((lesson, index) => ({ task_id: `${lesson.lesson_id}:task`, chapter_id: lesson.chapter_id, lesson_id: lesson.lesson_id, user_id: "local_user", day: index + 1, task_type: "lesson", title: lesson.title, minutes: 25, status: "pending", weak_points: [] })) } };
  onProgress(95);
  await saveLocalSource(source);
  onProgress(100);
  return source;
}

export async function renderLocalSourcePage(file: Blob, page: number): Promise<string | null> {
  if (file.type.startsWith("image/")) return URL.createObjectURL(file);
  if (file.type !== "application/pdf") return null;
  const pdf = await pdfLibrary();
  const loading = pdf.getDocument({ data: new Uint8Array(await file.arrayBuffer()) });
  const document = await loading.promise;
  try {
    const pdfPage = await document.getPage(Math.max(1, Math.min(page, document.numPages)));
    const viewport = pdfPage.getViewport({ scale: 1.5 });
    const canvas = window.document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await pdfPage.render({ canvas, viewport }).promise;
    return canvas.toDataURL("image/png");
  } finally { await loading.destroy(); }
}
