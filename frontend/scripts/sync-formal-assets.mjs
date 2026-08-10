import { copyFile, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const defaultBookRoot = resolve(
  root,
  "..",
  "study_app",
  "backend",
  "data",
  "books",
  "book_2a3605650b10"
);
const bookRoot = resolve(process.env.DEMO_FORMAL_BOOK_ROOT ?? defaultBookRoot);
const artifactsRoot = join(bookRoot, "artifacts");
const sourceAssetsRoot = join(bookRoot, "assets");
const destinationRoot = join(root, "public", "assets", "textbook", "figures");
const demoStatePath = join(root, "src", "data", "generated", "demo-state.json");
const lessonsPath = join(root, "src", "data", "generated", "lessons.json");
const generatedAssetsPath = join(root, "src", "data", "generated", "assets.json");

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function writeJson(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function selectDemoChapter(chapters, page) {
  return chapters
    .filter((chapter) => chapter.page_start <= page && page <= chapter.page_end)
    .sort((left, right) => (
      right.level - left.level
      || (left.page_end - left.page_start) - (right.page_end - right.page_start)
    ))[0] ?? null;
}

function conciseCaption(asset) {
  const lines = String(asset.caption ?? "")
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  const numberedCaption = [...lines].reverse().find((line) => /^(?:图|表)\s*\d/.test(line));
  const selected = numberedCaption ?? lines.find((line) => line.length >= 4) ?? "";
  if (selected) return selected.length > 88 ? `${selected.slice(0, 86)}…` : selected;
  const labels = { chart: "教材图表", formula: "教材公式", table: "教材表格", figure: "教材配图" };
  return `${labels[asset.type] ?? "教材配图"} · PDF 第 ${asset.page} 页`;
}

function overlapsCitation(asset, block) {
  return block.citations?.some((citation) => (
    citation.page_start <= asset.page && asset.page <= citation.page_end
  ));
}

const [formalAssets, tocAnalysis, demoState, sourceFiles] = await Promise.all([
  readJson(join(artifactsRoot, "assets.json")),
  readJson(join(artifactsRoot, "toc_analysis.json")),
  readJson(demoStatePath),
  readdir(sourceAssetsRoot)
]);

const printedPages = new Map(
  tocAnalysis.page_map.map((entry) => [Number(entry.pdf_page), entry.printed_page ?? null])
);
const sourceFileSet = new Set(sourceFiles);
const demoChunkIds = new Set(demoState.chunks.map((chunk) => chunk.chunk_id));

await mkdir(destinationRoot, { recursive: true });

const syncedAssets = [];
for (const formalAsset of formalAssets) {
  const imageName = `${formalAsset.asset_id}.jpg`;
  const thumbnailName = `thumb_${formalAsset.asset_id}.png`;
  if (!sourceFileSet.has(imageName) || !sourceFileSet.has(thumbnailName)) {
    throw new Error(`Formal asset files are incomplete for ${formalAsset.asset_id}`);
  }

  await Promise.all([
    copyFile(join(sourceAssetsRoot, imageName), join(destinationRoot, imageName)),
    copyFile(join(sourceAssetsRoot, thumbnailName), join(destinationRoot, thumbnailName))
  ]);

  const page = Number(formalAsset.page);
  const chapter = selectDemoChapter(demoState.chapters, page);
  if (!chapter) throw new Error(`No demo chapter covers formal asset page ${page}`);
  const localChunkIds = demoState.chunks
    .filter((chunk) => chunk.page_start <= page && page <= chunk.page_end)
    .map((chunk) => chunk.chunk_id)
    .filter((chunkId) => demoChunkIds.has(chunkId));

  syncedAssets.push({
    ...formalAsset,
    chapter_id: chapter.chapter_id,
    source_type: "extracted",
    caption: conciseCaption(formalAsset),
    image_url: `/assets/textbook/figures/${imageName}`,
    thumbnail_url: `/assets/textbook/figures/${thumbnailName}`,
    source_page_image_url: `/assets/textbook/pages/page_${String(page).padStart(3, "0")}.jpeg`,
    source_chunk_ids: localChunkIds,
    metadata: {
      ...(formalAsset.metadata ?? {}),
      printed_page: printedPages.get(page) ?? null,
      formal_book_id: formalAsset.book_id,
      formal_chapter_id: formalAsset.chapter_id,
      formal_source_type: formalAsset.source_type,
      formal_source_chunk_ids: formalAsset.source_chunk_ids ?? [],
      full_ocr_caption: formalAsset.caption ?? ""
    },
    book_id: demoState.book.id
  });
}

syncedAssets.sort((left, right) => (
  left.page - right.page
  || (left.bbox?.[1] ?? 0) - (right.bbox?.[1] ?? 0)
  || left.asset_id.localeCompare(right.asset_id)
));

const syncedById = new Map(syncedAssets.map((asset) => [asset.asset_id, asset]));
const lessons = demoState.lessons.map((lesson) => {
  const chapter = demoState.chapters.find((item) => item.chapter_id === lesson.chapter_id);
  if (!chapter) return lesson;
  const lessonAssets = syncedAssets.filter((asset) => (
    chapter.page_start <= asset.page && asset.page <= chapter.page_end
  ));
  const blocks = lesson.blocks.map((block) => {
    const related = lessonAssets.filter((asset) => (
      overlapsCitation(asset, block)
      || asset.source_chunk_ids.some((chunkId) => block.source_chunk_ids.includes(chunkId))
      || block.asset_ids.includes(asset.asset_id)
    ));
    return { ...block, asset_ids: related.map((asset) => asset.asset_id) };
  });
  return {
    ...lesson,
    blocks,
    asset_ids: lessonAssets.map((asset) => asset.asset_id).filter((assetId) => syncedById.has(assetId))
  };
});

const nextState = {
  ...demoState,
  provenance: {
    ...demoState.provenance,
    formal_asset_sync: {
      source_book_id: formalAssets[0]?.book_id ?? null,
      asset_count: syncedAssets.length,
      source: "formal assets.json + persisted MinerU image files"
    }
  },
  assets: syncedAssets,
  lessons
};

await Promise.all([
  writeJson(demoStatePath, nextState),
  writeJson(lessonsPath, lessons),
  writeJson(generatedAssetsPath, syncedAssets)
]);

const readyCount = syncedAssets.filter((asset) => asset.review_status === "ready").length;
console.log(`Synced ${syncedAssets.length} formal assets (${readyCount} ready, ${syncedAssets.length - readyCount} needs review).`);
console.log(`Copied ${syncedAssets.length * 2} image files to ${destinationRoot}.`);
