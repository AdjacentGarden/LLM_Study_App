import { copyFile, mkdir, readdir, stat } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const expectedPageCount = 125;
const destination = join(root, "public", "assets", "textbook", "pages");

function pageFile(page) {
  return `page_${String(page).padStart(3, "0")}.jpeg`;
}

async function findDefaultSource() {
  const exportsRoot = resolve(
    root,
    "..",
    "study_app",
    "backend",
    "data",
    "books",
    "book_2a3605650b10",
    "exports"
  );
  const entries = await readdir(exportsRoot, { withFileTypes: true });
  const candidates = entries
    .filter((entry) => entry.isDirectory() && /^formal_text_image_v\d+$/.test(entry.name))
    .sort((left, right) => Number(right.name.match(/\d+$/)?.[0]) - Number(left.name.match(/\d+$/)?.[0]));
  if (candidates.length === 0) {
    throw new Error(`No formal_text_image_v* export was found under ${exportsRoot}`);
  }
  return join(exportsRoot, candidates[0].name, "page_images");
}

const sourceArgument = process.argv.find((value) => value.startsWith("--source="));
const source = sourceArgument
  ? resolve(sourceArgument.slice("--source=".length))
  : process.env.DEMO_PAGE_IMAGE_DIR
    ? resolve(process.env.DEMO_PAGE_IMAGE_DIR)
    : await findDefaultSource();

if (!(await stat(source)).isDirectory()) {
  throw new Error(`Page image source is not a directory: ${source}`);
}

const sourceFiles = new Set(await readdir(source));
const missing = Array.from({ length: expectedPageCount }, (_, index) => pageFile(index + 1))
  .filter((file) => !sourceFiles.has(file));
if (missing.length > 0) {
  throw new Error(`Page image export is incomplete; missing ${missing.length} files (first: ${missing[0]}).`);
}

await mkdir(destination, { recursive: true });
for (let page = 1; page <= expectedPageCount; page += 1) {
  const file = pageFile(page);
  await copyFile(join(source, file), join(destination, file));
}

console.log(`Synced ${expectedPageCount} source pages from ${basename(resolve(source, ".."))} to ${destination}`);
