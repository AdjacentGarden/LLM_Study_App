import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const generatedDir = join(root, "src", "data", "generated");
const curatedDir = join(root, "src", "data", "seed");
const latestPath = join(root, ".cache", "mineru", "latest.json");
const curatedPath = join(curatedDir, "curated-content.json");

if (!existsSync(latestPath)) {
  throw new Error("No MinerU manifest found. Run `npm run demo:mineru` first.");
}

const manifest = JSON.parse(readFileSync(latestPath, "utf8"));
if (manifest.status !== "completed" || manifest.parser !== "mineru") {
  throw new Error("The latest content input was not completed by MinerU.");
}
if (!manifest.parser_version || !manifest.page_count) {
  throw new Error("MinerU manifest is missing parser_version or page_count. Re-run `npm run demo:mineru`.");
}

if (!existsSync(curatedPath)) {
  throw new Error(`Curated content input not found: ${curatedPath}`);
}

const curated = JSON.parse(readFileSync(curatedPath, "utf8"));
const rawOutput = manifest.output_dir;
const rawFiles = manifest.files ?? [];
const markdownFile = rawFiles.find((file) => file.toLowerCase().endsWith(".md"));
const middleFile = rawFiles.find((file) => file.toLowerCase().includes("middle") && file.toLowerCase().endsWith(".json"));
const contentListFile = rawFiles.find((file) => file.toLowerCase().includes("content_list") && file.toLowerCase().endsWith(".json"));
if (!markdownFile || !middleFile || !contentListFile) {
  throw new Error("MinerU manifest is missing markdown, middle JSON, or content list output.");
}

function manifestPath(relativePath) {
  const path = resolve(root, rawOutput, relativePath);
  if (!existsSync(path) || !statSync(path).isFile()) {
    throw new Error(`MinerU output not found: ${path}`);
  }
  return path;
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function jsonHash(value) {
  return sha256(JSON.stringify(value));
}

function textFromBlock(block) {
  return (block.lines ?? [])
    .flatMap((line) => line.spans ?? [])
    .map((span) => span.content ?? "")
    .filter(Boolean)
    .join("");
}

const markdownPath = manifestPath(markdownFile);
const middlePath = manifestPath(middleFile);
const contentListPath = manifestPath(contentListFile);
const markdown = readFileSync(markdownPath, "utf8");
const middle = JSON.parse(readFileSync(middlePath, "utf8"));
const contentList = JSON.parse(readFileSync(contentListPath, "utf8"));

if (!Array.isArray(contentList) || !Array.isArray(middle.pdf_info)) {
  throw new Error("MinerU content_list.json or middle.json has an unexpected structure.");
}
if (middle.pdf_info.length !== Number(manifest.page_count)) {
  throw new Error(`MinerU page count mismatch: manifest=${manifest.page_count}, middle=${middle.pdf_info.length}`);
}

const pages = new Map();
for (const [entryIndex, entry] of contentList.entries()) {
  const pageIdx = Number(entry.page_idx);
  if (!Number.isInteger(pageIdx)) continue;
  if (!pages.has(pageIdx)) pages.set(pageIdx, { entries: [], texts: [] });
  const page = pages.get(pageIdx);
  const item = { ...entry, entryIndex };
  page.entries.push(item);
  if (typeof entry.text === "string" && entry.text.trim()) page.texts.push(entry.text);
}

const middlePages = new Map(middle.pdf_info.map((page) => [Number(page.page_idx), page]));
const pageText = (pageIdx) => pages.get(pageIdx)?.texts.join("\n") ?? "";
const pageTextHash = (pageIdx) => sha256(pageText(pageIdx));

// The textbook starts at printed page 1 after six PDF front-matter pages. The
// offset is anchored by middle.json's clear printed-page footer at page_idx 12
// (printed page 18). page_idx 10's footer is an OCR-conflicted candidate (17),
// so the sequence is retained and the conflict is explicitly recorded below.
const printedPageOffset = 6;
function printedPageFor(pageIdx) {
  return pageIdx >= 6 ? pageIdx + printedPageOffset : null;
}

function discardedText(pageIdx) {
  const page = middlePages.get(pageIdx);
  return (page?.discarded_blocks ?? []).map(textFromBlock).filter(Boolean).join(" ");
}

function footerCandidates(pageIdx) {
  const matches = discardedText(pageIdx).match(/(?<!\d)\d{1,3}(?!\d)/g) ?? [];
  return [...new Set(matches.map(Number).filter((value) => value >= 1 && value <= 125))];
}

function middleBlockEvidence(pageIdx, rawText) {
  const page = middlePages.get(pageIdx);
  const blocks = page?.para_blocks ?? page?.preproc_blocks ?? [];
  return blocks
    .map((block, blockIndex) => ({
      blockIndex,
      text: textFromBlock(block),
      sha256: jsonHash(block)
    }))
    .filter((block) => block.text && (block.text.includes(rawText) || rawText.includes(block.text)))
    .slice(0, 4)
    .map(({ blockIndex, sha256: blockSha256 }) => ({ block_index: blockIndex, sha256: blockSha256 }));
}

function requireEntry(label, predicate) {
  const entry = contentList.find((item) => typeof item.text === "string" && predicate(item.text, Number(item.page_idx)));
  if (!entry) throw new Error(`MinerU content_list.json is missing required P0 entry: ${label}`);
  return { ...entry, entryIndex: contentList.indexOf(entry) };
}

const sectionTitle = requireEntry("第 1 节标题", (text, pageIdx) => pageIdx === 10 && text.includes("第1节") && text.includes("减数分裂"));
const definitionEntry = requireEntry("减数分裂定义", (text, pageIdx) => pageIdx === 10 && text.includes("染色体只复制一次") && text.includes("细胞分裂两次"));
const homologousEntry = requireEntry("同源染色体定义", (text, pageIdx) => pageIdx === 12 && text.includes("叫做同源染色体") && text.includes("联会"));
const segregationEntry = requireEntry("同源染色体分离", (text, pageIdx) => pageIdx === 12 && text.includes("同源染色体彼此分离"));
const halfEntry = requireEntry("染色体数目减半", (text, pageIdx) => pageIdx === 12 && text.includes("染色体数目的减半发生在减数第一次分裂"));
const sisterEntry = requireEntry("姐妹染色单体分离", (text, pageIdx) => pageIdx === 12 && text.includes("姐妹染色单体也随之分开"));
const fertilizationEntry = requireEntry("受精作用定义", (text, pageIdx) => pageIdx === 18 && text.includes("受精作用是卵细胞和精子相互识别"));
const restorationEntry = requireEntry("受精作用恢复染色体数目", (text, pageIdx) => pageIdx === 18 && text.includes("受精卵中的染色体数目又恢复到体细胞中的数目"));
const diversityEntry = requireEntry("受精作用的遗传意义", (text, pageIdx) => pageIdx === 19 && text.includes("减数分裂和受精作用对于维持"));

function entryReference(entry) {
  const pageIdx = Number(entry.page_idx);
  const rawText = typeof entry.text === "string" ? entry.text : "";
  const pdfPage = pageIdx + 1;
  return {
    page_idx: pageIdx,
    pdf_page: pdfPage,
    printed_page: printedPageFor(pageIdx),
    content_list_entry_index: entry.entryIndex,
    content_list_entry_sha256: jsonHash(entry),
    middle_blocks: middleBlockEvidence(pageIdx, rawText),
    raw_ocr_text: rawText,
    raw_ocr_text_sha256: sha256(rawText),
    page_text_sha256: pageTextHash(pageIdx)
  };
}

function sourceMetadata(entries) {
  const refs = entries.map(entryReference);
  const pageIdxs = [...new Set(refs.map((ref) => ref.page_idx))];
  const pdfPages = [...new Set(refs.map((ref) => ref.pdf_page))];
  const printedPages = [...new Set(refs.map((ref) => ref.printed_page).filter((value) => value !== null))];
  return {
    parser: "mineru",
    parser_version: manifest.parser_version,
    source_file: manifest.input_basename,
    source_sha256: manifest.input_sha256,
    pdf_pages: pdfPages,
    printed_pages: printedPages,
    page_idxs: pageIdxs,
    content_list_file: contentListFile,
    middle_file: middleFile,
    content_list_entry_indices: refs.map((ref) => ref.content_list_entry_index),
    content_list_entry_sha256: refs.map((ref) => ref.content_list_entry_sha256),
    raw_ocr_text: refs.map((ref) => ref.raw_ocr_text).join("\n"),
    raw_ocr_text_sha256: sha256(refs.map((ref) => ref.raw_ocr_text).join("\n")),
    page_text_sha256: [...new Set(refs.map((ref) => ref.page_text_sha256))],
    source_entries: refs
  };
}

function makeCitation(chunkId, entry, quote) {
  const reference = entryReference(entry);
  return {
    chunk_id: chunkId,
    page_start: reference.pdf_page,
    page_end: reference.pdf_page,
    printed_page_start: reference.printed_page,
    printed_page_end: reference.printed_page,
    quote,
    source_metadata: sourceMetadata([entry])
  };
}

function makeChunk(chunkId, entries, keyConcepts) {
  const references = entries.map(entryReference);
  const pdfPages = references.map((reference) => reference.pdf_page);
  const printedPages = references.map((reference) => reference.printed_page).filter((value) => value !== null);
  return {
    chunk_id: chunkId,
    book_id: "book_biology_2",
    chapter_id: "c2s1",
    page_start: Math.min(...pdfPages),
    page_end: Math.max(...pdfPages),
    printed_page_start: Math.min(...printedPages),
    printed_page_end: Math.max(...printedPages),
    content_type: "ocr_text",
    text: entries.map((entry) => entry.text).join("\n\n"),
    asset_ids: [],
    key_concepts: keyConcepts,
    source_metadata: sourceMetadata(entries),
    source_entries: references
  };
}

const p0Chunks = [
  makeChunk("chunk_c2s1_11", [definitionEntry], ["减数分裂", "配子形成"]),
  makeChunk("chunk_c2s1_13", [homologousEntry, segregationEntry, halfEntry, sisterEntry], ["同源染色体", "联会", "四分体", "姐妹染色单体", "减数第一次分裂"]),
  makeChunk("chunk_c2s1_19", [fertilizationEntry, restorationEntry], ["受精作用", "染色体数目稳定"]),
  makeChunk("chunk_c2s1_20", [diversityEntry], ["遗传多样性", "遗传稳定性"])
];
const chunkById = new Map(p0Chunks.map((chunk) => [chunk.chunk_id, chunk]));

function updateSourceFields(item, chunkId) {
  const chunk = chunkById.get(chunkId);
  if (!chunk) throw new Error(`Unknown P0 chunk: ${chunkId}`);
  return {
    ...item,
    source_chunk_ids: [chunkId],
    page_start: chunk.page_start,
    page_end: chunk.page_end,
    printed_page_start: chunk.printed_page_start,
    printed_page_end: chunk.printed_page_end,
    source_metadata: chunk.source_metadata
  };
}

const lessonCitations = {
  definition: makeCitation("chunk_c2s1_11", definitionEntry, "染色体只复制一次，而细胞分裂两次"),
  homologous: makeCitation("chunk_c2s1_13", homologousEntry, "配对的两条染色体，形状和大小一般都相同，一条来自父方，一条来自母方，叫做同源染色体"),
  segregation: makeCitation("chunk_c2s1_13", segregationEntry, "配对的两条同源染色体彼此分离"),
  half: makeCitation("chunk_c2s1_13", halfEntry, "染色体数目的减半发生在减数第一次分裂"),
  sister: makeCitation("chunk_c2s1_13", sisterEntry, "两条姐妹染色单体也随之分开"),
  fertilization: makeCitation("chunk_c2s1_19", fertilizationEntry, "受精作用是卵细胞和精子相互识别、融合成为受精卵的过程"),
  restoration: makeCitation("chunk_c2s1_19", restorationEntry, "受精卵中的染色体数目又恢复到体细胞中的数目")
};

const lessons = [{
  book_id: "book_biology_2",
  lesson_id: "lesson_meiosis",
  chapter_id: "c2s1",
  title: "减数分裂和受精作用",
  source_title: "第 2 章 第 1 节 减数分裂和受精作用",
  page_start: 11,
  page_end: 21,
  printed_page_start: 16,
  printed_page_end: 26,
  lesson_kind: "lesson",
  status: "generated",
  confidence: 94,
  objectives: [
    "理解减数分裂中染色体数目减半的过程",
    "区分同源染色体与姐妹染色单体的分离",
    "说明受精作用如何维持物种染色体数目稳定"
  ],
  key_concepts: ["减数分裂", "同源染色体", "联会", "四分体", "姐妹染色单体", "受精作用"],
  summary: "基于教材原文把减数分裂记成“一次复制、两次分裂”：同源染色体先联会并在减数第一次分裂中分离，姐妹染色单体随后分开；配子结合时，受精作用使染色体数目恢复到体细胞水平。",
  blocks: [
    {
      block_id: "block_meiosis_definition",
      block_type: "explanation",
      title: "先抓住总结构",
      content: "减数分裂发生在形成成熟生殖细胞的过程中。教材明确指出，染色体只复制一次而细胞分裂两次，结果是成熟生殖细胞的染色体数目减半。",
      citations: [lessonCitations.definition],
      source_chunk_ids: ["chunk_c2s1_11"],
      asset_ids: ["asset_meiosis_30"],
      ai_generated: false
    },
    {
      block_id: "block_homologous",
      block_type: "diagram",
      title: "同源染色体先分离",
      content: "联会后形成四分体；减数第一次分裂中分离的是同源染色体，而不是姐妹染色单体，这是染色体数目减半的关键。",
      citations: [lessonCitations.homologous, lessonCitations.segregation, lessonCitations.half],
      source_chunk_ids: ["chunk_c2s1_13"],
      asset_ids: ["asset_meiosis_30", "asset_meiosis_35"],
      ai_generated: false
    },
    {
      block_id: "block_sister",
      block_type: "comparison",
      title: "姐妹染色单体随后分开",
      content: "减数第二次分裂时，着丝点分裂，姐妹染色单体随之分开。记忆顺序是：第一次分裂看同源染色体，第二次分裂看姐妹染色单体。",
      citations: [lessonCitations.sister],
      source_chunk_ids: ["chunk_c2s1_13"],
      asset_ids: ["asset_meiosis_35"],
      ai_generated: false
    },
    {
      block_id: "block_fertilization",
      block_type: "application",
      title: "受精作用恢复数目",
      content: "精子和卵细胞相互识别、融合成为受精卵；两套配子染色体会合，使受精卵中的染色体数目恢复到体细胞水平。",
      citations: [lessonCitations.fertilization, lessonCitations.restoration],
      source_chunk_ids: ["chunk_c2s1_19"],
      asset_ids: [],
      ai_generated: false
    }
  ],
  source_chunk_ids: ["chunk_c2s1_11", "chunk_c2s1_13", "chunk_c2s1_19", "chunk_c2s1_20"],
  asset_ids: ["asset_meiosis_30", "asset_meiosis_35"],
  warnings: ["部分 OCR 文字存在识别噪声；课程讲解使用人工校订表述，引用保留原始 OCR 文本。"]
}];

const flashcardChunkMap = {
  fc_homologous: "chunk_c2s1_13",
  fc_tetrad: "chunk_c2s1_13",
  fc_meiosis_i: "chunk_c2s1_13",
  fc_sister: "chunk_c2s1_13",
  fc_fertilization: "chunk_c2s1_19",
  fc_one_two: "chunk_c2s1_11"
};
const flashcards = (curated.flashcards ?? []).map((card) => updateSourceFields(card, flashcardChunkMap[card.card_id] ?? "chunk_c2s1_13"));

const quizChunkMap = {
  quiz_meiosis_01: "chunk_c2s1_13",
  quiz_meiosis_02: "chunk_c2s1_13",
  quiz_meiosis_03: "chunk_c2s1_19"
};
const quizzes = (curated.quizzes ?? []).map((quiz) => updateSourceFields(quiz, quizChunkMap[quiz.question_id] ?? "chunk_c2s1_13"));

const updatedChapters = (curated.chapters ?? []).map((chapter) => chapter.chapter_id === "c2s1"
  ? {
      ...chapter,
      page_start: 11,
      page_end: 21,
      confidence: 100,
      status: "已确认",
      source: "manual_toc_reference+mineru"
    }
  : chapter);

const updatedAssets = (curated.assets ?? []).map((asset) => {
  if (asset.asset_id === "asset_meiosis_30") {
    return {
      ...asset,
      page: 11,
      caption: "减数分裂小节页面（视觉基线页图）",
      source_chunk_ids: ["chunk_c2s1_11"],
      source_page_pdf: 11,
      source_page_printed: 16,
      source_origin: "migrated-source-frontend-baseline",
      mineru_extracted: false,
      authorization_status: "internal-demo-only; verify before distribution",
      provenance_note: "该页图来自源前端既有视觉基线，本轮内容引用由 MinerU OCR 重新建立。"
    };
  }
  if (asset.asset_id === "asset_meiosis_35") {
    return {
      ...asset,
      page: 12,
      caption: "精子形成与减数分裂过程页面（视觉基线页图）",
      source_chunk_ids: ["chunk_c2s1_13"],
      source_page_pdf: 12,
      source_page_printed: 17,
      source_origin: "migrated-source-frontend-baseline",
      mineru_extracted: false,
      authorization_status: "internal-demo-only; verify before distribution",
      provenance_note: "该页图来自源前端既有视觉基线，本轮内容引用由 MinerU OCR 重新建立。"
    };
  }
  return asset;
});

const pageLocations = Array.from({ length: Number(manifest.page_count) }, (_, pageIdx) => {
  const pdfPage = pageIdx + 1;
  const printedPage = printedPageFor(pageIdx);
  const text = pageText(pageIdx);
  const candidates = footerCandidates(pageIdx);
  const hasExpectedCandidate = printedPage !== null && candidates.includes(printedPage);
  return {
    index: pdfPage,
    pdf_page: pdfPage,
    page_idx: pageIdx,
    printed_page: printedPage,
    label: printedPage === null ? `PDF 第 ${pdfPage} 页` : `教材第 ${printedPage} 页（PDF 第 ${pdfPage} 页）`,
    confidence: printedPage === null ? 0 : hasExpectedCandidate ? 96 : 82,
    source: "mineru",
    evidence: hasExpectedCandidate
      ? "middle.json 页脚 OCR 与连续页码偏移一致"
      : printedPage === null
        ? "未进入教材印刷页范围"
        : `连续页码偏移推断；middle.json 页脚候选为 ${candidates.length ? candidates.join(", ") : "无"}`,
    content_list_entry_count: pages.get(pageIdx)?.entries.length ?? 0,
    text_char_count: text.length,
    page_text_sha256: sha256(text)
  };
});

const groundingTerms = ["减数分裂", "同源染色体", "姐妹染色单体", "受精作用"];
const groundingEvidence = Object.fromEntries(groundingTerms.map((term) => {
  const hit = contentList.find((entry) => typeof entry.text === "string" && entry.text.includes(term));
  return [term, hit ? {
    present: true,
    page_idx: Number(hit.page_idx),
    pdf_page: Number(hit.page_idx) + 1,
    printed_page: printedPageFor(Number(hit.page_idx)),
    content_list_entry_index: contentList.indexOf(hit),
    content_list_entry_sha256: jsonHash({ ...hit, entryIndex: contentList.indexOf(hit) })
  } : { present: false }];
}));
const missingGroundingTerms = groundingTerms.filter((term) => !groundingEvidence[term].present || !markdown.includes(term));
if (missingGroundingTerms.length) {
  throw new Error(`MinerU output does not contain required P0 terms: ${missingGroundingTerms.join(", ")}`);
}

const outputFileHash = (relativePath) => manifest.output_file_hashes?.[relativePath]?.sha256 ?? null;
const p0FooterCandidates = footerCandidates(10);
const provenance = {
  ...curated.provenance,
  source_file: manifest.input_basename,
  source_sha256: manifest.input_sha256,
  parser: "mineru",
  parser_version: manifest.parser_version,
  parser_command: manifest.parser_command,
  parser_method: manifest.parser_method,
  parser_language: manifest.language,
  generated_at: manifest.generated_at,
  raw_output_dir: rawOutput,
  raw_markdown_files: [markdownFile],
  raw_middle_json_files: [middleFile],
  raw_content_list_files: [contentListFile],
  content_list_sha256: outputFileHash(contentListFile),
  middle_json_sha256: outputFileHash(middleFile),
  markdown_sha256: outputFileHash(markdownFile),
  page_count: Number(manifest.page_count),
  scan_detection: manifest.scan_detection,
  grounding: {
    markdown_file: markdownFile,
    markdown_bytes: Buffer.byteLength(markdown),
    required_terms: Object.fromEntries(groundingTerms.map((term) => [term, groundingEvidence[term].present])),
    term_evidence: groundingEvidence
  },
  printed_page_mapping: {
    method: "one-based PDF page_idx plus a six-page front-matter offset",
    offset: printedPageOffset,
    anchor: { page_idx: 12, pdf_page: 13, printed_page: 18, evidence: "middle.json discarded footer OCR" },
    p0_manual_review: {
      page_idx: 10,
      pdf_page: 11,
      printed_page: 16,
      evidence: p0FooterCandidates.includes(16)
        ? "middle.json discarded footer OCR candidate 16; migrated page image footer agrees"
        : `middle.json footer OCR candidates were ${p0FooterCandidates.length ? p0FooterCandidates.join(", ") : "none"}; migrated page image review confirms 16`
    },
    unresolved_pages: pageLocations.filter((location) => location.printed_page !== null && location.confidence < 90).map((location) => location.pdf_page)
  },
  p0_content: {
    section_title: sectionTitle.text,
    section_title_pdf_page: Number(sectionTitle.page_idx) + 1,
    section_title_printed_page: printedPageFor(Number(sectionTitle.page_idx)),
    chunks: p0Chunks.map((chunk) => ({
      chunk_id: chunk.chunk_id,
      pdf_pages: [chunk.page_start, chunk.page_end],
      printed_pages: [chunk.printed_page_start, chunk.printed_page_end],
      content_list_entry_indices: chunk.source_metadata.content_list_entry_indices,
      raw_ocr_text_sha256: chunk.source_metadata.raw_ocr_text_sha256
    })),
    citation_policy: "每条课程引用必须保留原始 OCR 文本、PDF 页、教材印刷页、content_list 条目索引与哈希。"
  },
  fixture_policy: "完整目录由教材目录参考图人工确认；第 2 章第 1 节的课程正文与引用继续使用 MinerU OCR 证据。"
};

const updatedAssignment = {
  ...curated.assignment,
  source: "《遗传与进化》第 2 章第 1 节 · 教材第 18 页（PDF 第 13 页）"
};
const updatedDiagnosis = {
  ...curated.diagnosis,
  review_page: "教材第 18 页（PDF 第 13 页）· 同源染色体分离"
};

const nextState = {
  ...curated,
  provenance,
  book: { ...curated.book, pages: Number(manifest.page_count) },
  scan: {
    ...curated.scan,
    filename: manifest.input_basename,
    page_count: Number(manifest.page_count),
    has_text_layer: manifest.scan_detection?.has_text_layer ?? false,
    needs_ocr: manifest.scan_detection?.needs_ocr ?? true,
    source_locations: pageLocations,
    quality_warnings: pageLocations.filter((location) => location.text_char_count === 0).map((location) => ({
      page: location.pdf_page,
      code: "ocr_page_without_text_entries",
      message: "MinerU content_list 没有文本条目；该页仍保留原始图片条目。"
    }))
  },
  chapters: updatedChapters,
  chunks: p0Chunks,
  assets: updatedAssets,
  lessons,
  flashcards,
  quizzes,
  assignment: updatedAssignment,
  diagnosis: updatedDiagnosis
};

mkdirSync(generatedDir, { recursive: true });
const outputs = {
  "demo-state.json": nextState,
  "book.json": { provenance: nextState.provenance, ...nextState.book },
  "chapters.json": nextState.chapters,
  "lessons.json": nextState.lessons,
  "quiz.json": nextState.quizzes,
  "flashcards.json": nextState.flashcards,
  "ai-responses.json": nextState.aiReplies,
  "demo-state-meta.json": {
    provenance: nextState.provenance,
    scan: nextState.scan,
    assignment: nextState.assignment,
    diagnosis: nextState.diagnosis,
    studyPlan: nextState.studyPlan
  }
};

for (const [name, value] of Object.entries(outputs)) {
  writeFileSync(join(generatedDir, name), `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

console.log(`Generated ${Object.keys(outputs).length} fixture files from MinerU content_list/middle output.`);
console.log(`P0 pages: PDF ${lessons[0].page_start}-${lessons[0].page_end}; textbook ${lessons[0].printed_page_start}-${lessons[0].printed_page_end}`);
console.log(`P0 chunks: ${p0Chunks.length}; citations: ${lessons[0].blocks.reduce((count, block) => count + block.citations.length, 0)}`);
