import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const generatedDir = join(root, "src", "data", "generated");
const curatedPath = join(root, "src", "data", "seed", "curated-content.json");

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function writeJson(path, value) {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const curated = readJson(curatedPath);
const demoStatePath = join(generatedDir, "demo-state.json");
const bookPath = join(generatedDir, "book.json");
const chaptersPath = join(generatedDir, "chapters.json");
const metaPath = join(generatedDir, "demo-state-meta.json");
const demoState = readJson(demoStatePath);
const generatedBook = readJson(bookPath);
const meta = readJson(metaPath);

const chapters = curated.chapters.map((chapter) => chapter.chapter_id === "c2s1"
  ? { ...chapter, source: "manual_toc_reference+mineru" }
  : chapter);
const ids = new Set(chapters.map((chapter) => chapter.chapter_id));
const rootCount = chapters.filter((chapter) => chapter.level === 1).length;
const formalSectionCount = chapters.filter((chapter) => /^第\s*\d+\s*节/.test(chapter.source_title)).length;

assert(ids.size === chapters.length, "Directory contains duplicate chapter IDs.");
assert(rootCount === curated.book.chapterCount, "Directory chapter count does not match book metadata.");
assert(formalSectionCount === curated.book.sectionCount, "Directory section count does not match book metadata.");
assert(chapters.every((chapter) => !chapter.parent_id || ids.has(chapter.parent_id)), "Directory contains an unknown parent ID.");
assert(chapters.every((chapter) => chapter.page_start <= chapter.page_end), "Directory contains an invalid PDF page range.");
assert(chapters.every((chapter) => chapter.printed_page_start <= chapter.printed_page_end), "Directory contains an invalid printed page range.");
const directoryPolicy = "目录按当前 PDF 实际页核验；源文件缺少第 1 章印刷页 1–14，第 2 章起使用 MinerU OCR 证据。";
const provenance = {
  ...demoState.provenance,
  content_scope: curated.provenance.content_scope,
  fixture_policy: directoryPolicy
};
const book = {
  ...generatedBook,
  provenance,
  ...curated.book,
  pages: demoState.book.pages
};

writeJson(chaptersPath, chapters);
writeJson(bookPath, book);
writeJson(demoStatePath, {
  ...demoState,
  provenance,
  book: {
    ...demoState.book,
    ...curated.book,
    pages: demoState.book.pages
  },
  chapters,
  studyPlan: curated.studyPlan
});
writeJson(metaPath, { ...meta, provenance, studyPlan: curated.studyPlan });

console.log(`Synced ${chapters.length} directory entries (${rootCount} chapters, ${formalSectionCount} formal sections).`);
