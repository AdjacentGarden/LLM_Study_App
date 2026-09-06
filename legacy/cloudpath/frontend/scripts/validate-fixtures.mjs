import { createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const dir = join(root, "src", "data", "generated");
const latestPath = join(root, ".cache", "mineru", "latest.json");
const required = ["demo-state.json", "book.json", "chapters.json", "lessons.json", "quiz.json", "flashcards.json", "ai-responses.json"];

function fail(message) {
  throw new Error(message);
}

function assert(condition, message) {
  if (!condition) fail(message);
}

function read(name) {
  const path = join(dir, name);
  if (!existsSync(path)) fail(`Missing fixture: ${name}`);
  return JSON.parse(readFileSync(path, "utf8"));
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function jsonHash(value) {
  return sha256(JSON.stringify(value));
}

for (const name of required) read(name);
assert(existsSync(latestPath), "MinerU manifest is missing; run `npm run demo:mineru` first.");

const state = read("demo-state.json");
const book = read("book.json");
const chapters = read("chapters.json");
const lessons = read("lessons.json");
const quizzes = read("quiz.json");
const flashcards = read("flashcards.json");
const replies = read("ai-responses.json");
const manifest = JSON.parse(readFileSync(latestPath, "utf8"));

assert(manifest.status === "completed" && manifest.parser === "mineru", "Latest MinerU manifest is not completed by MinerU.");
assert(manifest.parser_version, "MinerU manifest must record the runtime version.");
assert(Number.isInteger(manifest.page_count) && manifest.page_count > 0, "MinerU manifest must record page_count.");
assert(typeof manifest.run_id === "string" && manifest.run_id.length > 0, "MinerU manifest must record a run_id.");
assert(typeof manifest.ingest_script_sha256 === "string" && manifest.ingest_script_sha256.length === 64, "MinerU manifest must record the ingestion script hash.");
assert(manifest.output_dir?.includes("/runs/"), "MinerU output must be isolated in a per-run directory.");
assert(state.provenance?.parser === "mineru", "Fixtures must declare MinerU as parser.");
assert(state.provenance?.parser_version === manifest.parser_version, "Fixture and manifest MinerU versions differ.");
assert(state.provenance?.source_sha256 === manifest.input_sha256, "Fixture and manifest source SHA-256 differ.");
assert(state.scan?.page_count === manifest.page_count, "Scan page count does not match MinerU manifest.");
assert(state.scan?.has_text_layer === Boolean(manifest.scan_detection?.has_text_layer), "Scan text-layer detection is not tied to MinerU preflight.");
assert(state.scan?.needs_ocr === Boolean(manifest.scan_detection?.needs_ocr), "Scan OCR decision is not tied to MinerU preflight.");
assert(state.scan?.has_text_layer === false && state.scan?.needs_ocr === true, "The designated PDF should be OCR-derived.");
assert(book.id === "book_biology_2" && book.pages === manifest.page_count, "Unexpected book metadata.");
assert(Array.isArray(chapters) && chapters.length >= 3, "Chapter fixture is too small.");
assert(Array.isArray(lessons) && lessons.some((lesson) => lesson.chapter_id === "c2s1"), "Core meiosis lesson is missing.");
assert(flashcards.length >= 6 && quizzes.length >= 3, "P0 practice fixtures are incomplete.");
assert(replies.default && replies.quiz, "AI response fixtures are incomplete.");

const rawFiles = manifest.files ?? [];
const rawOutput = resolve(root, manifest.output_dir);
function rawPath(file) {
  const path = resolve(rawOutput, file);
  assert(existsSync(path) && statSync(path).isFile(), `MinerU output not found: ${path}`);
  return path;
}

const markdownFile = rawFiles.find((file) => file.toLowerCase().endsWith(".md"));
const middleFile = rawFiles.find((file) => file.toLowerCase().includes("middle") && file.toLowerCase().endsWith(".json"));
const contentListFile = rawFiles.find((file) => file.toLowerCase().includes("content_list") && file.toLowerCase().endsWith(".json"));
assert(markdownFile && middleFile && contentListFile, "MinerU manifest must include markdown, middle JSON, and content_list JSON.");

const markdownPath = rawPath(markdownFile);
const middlePath = rawPath(middleFile);
const contentListPath = rawPath(contentListFile);
const markdown = readFileSync(markdownPath, "utf8");
const middle = JSON.parse(readFileSync(middlePath, "utf8"));
const contentList = JSON.parse(readFileSync(contentListPath, "utf8"));
assert(Array.isArray(contentList), "content_list.json must be an array.");
assert(Array.isArray(middle.pdf_info) && middle.pdf_info.length === manifest.page_count, "middle.json page count mismatch.");

const ingestScriptPath = join(root, "scripts", "ingest-pdf.mjs");
assert(sha256(readFileSync(ingestScriptPath)) === manifest.ingest_script_sha256, "MinerU ingestion script hash is stale.");

for (const file of rawFiles) {
  const path = rawPath(file);
  const expected = manifest.output_file_hashes?.[file];
  assert(expected, `Manifest is missing SHA-256 for ${file}.`);
  assert(Number(expected.bytes) === statSync(path).size, `MinerU output byte count mismatch for ${file}.`);
  assert(sha256(readFileSync(path)) === expected.sha256, `MinerU output hash mismatch for ${file}.`);
}

function outputHashMatches(file, path) {
  const expected = manifest.output_file_hashes?.[file]?.sha256;
  assert(expected, `Manifest is missing SHA-256 for ${file}.`);
  assert(sha256(readFileSync(path)) === expected, `MinerU output hash mismatch for ${file}.`);
}
outputHashMatches(markdownFile, markdownPath);
outputHashMatches(middleFile, middlePath);
outputHashMatches(contentListFile, contentListPath);
assert(state.provenance.markdown_sha256 === manifest.output_file_hashes[markdownFile].sha256, "Markdown provenance hash is stale.");
assert(state.provenance.middle_json_sha256 === manifest.output_file_hashes[middleFile].sha256, "middle.json provenance hash is stale.");
assert(state.provenance.content_list_sha256 === manifest.output_file_hashes[contentListFile].sha256, "content_list provenance hash is stale.");

const pages = new Map();
for (const [entryIndex, entry] of contentList.entries()) {
  const pageIdx = Number(entry.page_idx);
  if (!Number.isInteger(pageIdx)) continue;
  if (!pages.has(pageIdx)) pages.set(pageIdx, { entries: [], texts: [] });
  pages.get(pageIdx).entries.push({ ...entry, entryIndex });
  if (typeof entry.text === "string" && entry.text.trim()) pages.get(pageIdx).texts.push(entry.text);
}
const middlePages = new Map(middle.pdf_info.map((page) => [Number(page.page_idx), page]));
const pageText = (pageIdx) => pages.get(pageIdx)?.texts.join("\n") ?? "";
const printedPageFor = (pageIdx) => pageIdx >= 6 ? pageIdx + 6 : null;

assert(Array.isArray(state.scan.source_locations) && state.scan.source_locations.length === manifest.page_count, "Scan must expose one MinerU source location per PDF page.");
for (const location of state.scan.source_locations) {
  assert(Number.isInteger(location.pdf_page) && location.pdf_page >= 1 && location.pdf_page <= manifest.page_count, "Invalid scan source location PDF page.");
  assert(location.pdf_page === location.index, "Scan source location index must be the one-based PDF page.");
  assert(location.page_idx === location.pdf_page - 1, "Scan source location page_idx mismatch.");
  assert(location.page_text_sha256 === sha256(pageText(location.page_idx)), `Page text hash mismatch for PDF page ${location.pdf_page}.`);
}

const targetLocations = new Map(state.scan.source_locations.map((location) => [location.pdf_page, location]));
assert(targetLocations.get(11)?.printed_page === 16, "PDF page 11 must map to textbook printed page 16.");
assert(targetLocations.get(13)?.printed_page === 18, "PDF page 13 must map to textbook printed page 18.");
assert(targetLocations.get(19)?.printed_page === 24, "PDF page 19 must map to textbook printed page 24.");

function validateMiddleBlocks(reference, context) {
  const page = middlePages.get(Number(reference.page_idx));
  const blocks = page?.para_blocks ?? page?.preproc_blocks ?? [];
  for (const blockReference of reference.middle_blocks ?? []) {
    const block = blocks[Number(blockReference.block_index)];
    assert(block, `${context}: middle block ${blockReference.block_index} is missing.`);
    assert(jsonHash(block) === blockReference.sha256, `${context}: middle block hash mismatch.`);
  }
}

function validateReference(reference, context) {
  assert(reference && Number.isInteger(reference.page_idx), `${context}: source reference has no page_idx.`);
  const pageIdx = Number(reference.page_idx);
  const entryIndex = Number(reference.content_list_entry_index);
  const entry = contentList[entryIndex];
  assert(entry, `${context}: content_list entry ${entryIndex} is missing.`);
  assert(Number(entry.page_idx) === pageIdx, `${context}: content_list page_idx mismatch.`);
  const rawText = typeof entry.text === "string" ? entry.text : "";
  assert(rawText === reference.raw_ocr_text, `${context}: raw OCR text differs from content_list.`);
  assert(reference.raw_ocr_text_sha256 === sha256(rawText), `${context}: raw OCR text hash mismatch.`);
  assert(reference.content_list_entry_sha256 === jsonHash({ ...entry, entryIndex }), `${context}: content_list entry hash mismatch.`);
  assert(pageText(pageIdx).includes(rawText), `${context}: raw OCR text is not present on its MinerU page.`);
  assert(reference.page_text_sha256 === sha256(pageText(pageIdx)), `${context}: page text hash mismatch.`);
  assert(reference.pdf_page === pageIdx + 1, `${context}: PDF page mismatch.`);
  assert(reference.printed_page === printedPageFor(pageIdx), `${context}: printed page mapping mismatch.`);
  validateMiddleBlocks(reference, context);
}

function validateSourceMetadata(metadata, context) {
  assert(metadata?.parser === "mineru", `${context}: source metadata parser is not MinerU.`);
  assert(metadata.parser_version === manifest.parser_version, `${context}: source metadata MinerU version mismatch.`);
  assert(metadata.source_sha256 === manifest.input_sha256, `${context}: source metadata PDF hash mismatch.`);
  assert(metadata.content_list_file === contentListFile, `${context}: content_list filename mismatch.`);
  assert(metadata.middle_file === middleFile, `${context}: middle filename mismatch.`);
  assert(Array.isArray(metadata.source_entries) && metadata.source_entries.length > 0, `${context}: source metadata has no raw OCR entries.`);
  for (const [index, reference] of metadata.source_entries.entries()) validateReference(reference, `${context} entry ${index}`);
  const rawCombined = metadata.source_entries.map((reference) => reference.raw_ocr_text).join("\n");
  assert(metadata.raw_ocr_text === rawCombined, `${context}: combined raw OCR text mismatch.`);
  assert(metadata.raw_ocr_text_sha256 === sha256(rawCombined), `${context}: combined raw OCR hash mismatch.`);
  assert(JSON.stringify(metadata.content_list_entry_indices) === JSON.stringify(metadata.source_entries.map((reference) => reference.content_list_entry_index)), `${context}: entry index list mismatch.`);
}

const chunkById = new Map();
for (const chunk of state.chunks) {
  assert(chunk.chapter_id === "c2s1" && chunk.content_type === "ocr_text", `Chunk ${chunk.chunk_id} is not MinerU OCR content.`);
  assert(Array.isArray(chunk.source_entries) && chunk.source_entries.length > 0, `Chunk ${chunk.chunk_id} has no source entries.`);
  validateSourceMetadata(chunk.source_metadata, `Chunk ${chunk.chunk_id}`);
  for (const reference of chunk.source_entries) validateReference(reference, `Chunk ${chunk.chunk_id}`);
  assert(chunk.text === chunk.source_entries.map((reference) => reference.raw_ocr_text).join("\n\n"), `Chunk ${chunk.chunk_id} text is not generated from raw OCR entries.`);
  assert(chunk.page_start === Math.min(...chunk.source_entries.map((reference) => reference.pdf_page)), `Chunk ${chunk.chunk_id} start page mismatch.`);
  assert(chunk.page_end === Math.max(...chunk.source_entries.map((reference) => reference.pdf_page)), `Chunk ${chunk.chunk_id} end page mismatch.`);
  chunkById.set(chunk.chunk_id, chunk);
}
assert(chunkById.has("chunk_c2s1_11") && chunkById.has("chunk_c2s1_13") && chunkById.has("chunk_c2s1_19"), "Required P0 OCR chunks are missing.");

let citationCount = 0;
for (const lesson of lessons) {
  for (const block of lesson.blocks ?? []) {
    for (const citation of block.citations ?? []) {
      citationCount += 1;
      const chunk = chunkById.get(citation.chunk_id);
      assert(chunk, `Citation ${citation.chunk_id} points to a missing chunk.`);
      assert(citation.page_start === citation.page_end, `Citation ${citation.chunk_id} must identify an exact PDF page.`);
      validateSourceMetadata(citation.source_metadata, `Citation ${citation.chunk_id}`);
      assert(citation.source_metadata.source_entries.some((reference) => pageText(reference.page_idx).includes(citation.quote)), `Citation ${citation.chunk_id} quote is not present in its OCR page.`);
      assert(citation.source_metadata.pdf_pages.includes(citation.page_start), `Citation ${citation.chunk_id} metadata page mismatch.`);
    }
  }
}
assert(citationCount >= 7, "P0 lesson does not contain enough grounded citations.");

for (const card of flashcards) {
  assert(card.source_chunk_ids?.length === 1, `Flashcard ${card.card_id} must point to one source chunk.`);
  const chunk = chunkById.get(card.source_chunk_ids[0]);
  assert(chunk, `Flashcard ${card.card_id} points to a missing chunk.`);
  assert(card.page_start === chunk.page_start && card.page_end === chunk.page_end, `Flashcard ${card.card_id} page range is stale.`);
  validateSourceMetadata(card.source_metadata, `Flashcard ${card.card_id}`);
}

for (const quiz of quizzes) {
  assert(quiz.source_chunk_ids?.length === 1, `Quiz ${quiz.question_id} must point to one source chunk.`);
  const chunk = chunkById.get(quiz.source_chunk_ids[0]);
  assert(chunk, `Quiz ${quiz.question_id} points to a missing chunk.`);
  assert(quiz.page_start === chunk.page_start && quiz.page_end === chunk.page_end, `Quiz ${quiz.question_id} page range is stale.`);
  validateSourceMetadata(quiz.source_metadata, `Quiz ${quiz.question_id}`);
}

const coreChapter = chapters.find((chapter) => chapter.chapter_id === "c2s1");
const coreLesson = lessons.find((lesson) => lesson.chapter_id === "c2s1");
assert(coreChapter?.page_start === 11 && coreChapter?.page_end === 21, "Core chapter page range is not MinerU-grounded.");
assert(coreLesson?.page_start === 11 && coreLesson?.page_end === 21, "Core lesson page range is not MinerU-grounded.");
assert(state.assignment?.source?.includes("PDF 第 13 页") && state.diagnosis?.review_page?.includes("PDF 第 13 页"), "Assignment and diagnosis still reference the old mock page.");

for (const assetId of ["asset_meiosis_30", "asset_meiosis_35"]) {
  const asset = state.assets.find((item) => item.asset_id === assetId);
  assert(asset, `Missing P0 asset ${assetId}.`);
  assert(asset.mineru_extracted === false && asset.source_origin === "migrated-source-frontend-baseline", `Asset ${assetId} lacks explicit visual-baseline provenance.`);
  assert(asset.authorization_status, `Asset ${assetId} lacks authorization status.`);
}

for (const term of ["减数分裂", "同源染色体", "姐妹染色单体", "受精作用"]) {
  assert(markdown.includes(term), `MinerU markdown is missing required term ${term}.`);
  assert(state.provenance.grounding.required_terms?.[term] === true, `Fixture grounding term ${term} is not recorded.`);
  assert(state.provenance.grounding.term_evidence?.[term]?.present === true, `Fixture grounding evidence ${term} is missing.`);
}

assert(state.provenance.printed_page_mapping?.offset === 6, "Printed-page mapping offset is missing.");
assert(state.provenance.printed_page_mapping?.p0_manual_review?.printed_page === 16, "P0 printed-page review note is missing.");
assert(manifest.model_repository && "revision" in manifest.model_repository, "MinerU manifest must record model repository revision status.");
assert(Array.isArray(manifest.model_files) && manifest.model_files.length > 0, "MinerU manifest must hash local model artifacts.");
for (const model of manifest.model_files) {
  const path = resolve(root, model.path);
  assert(existsSync(path) && statSync(path).isFile(), `MinerU model artifact missing: ${model.path}`);
  assert(sha256(readFileSync(path)) === model.sha256, `MinerU model artifact hash mismatch: ${model.path}`);
}

console.log(`Fixtures valid: ${chapters.length} chapters, ${state.chunks.length} MinerU chunks, ${flashcards.length} flashcards, ${quizzes.length} quizzes, ${citationCount} grounded citations.`);
