import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = resolve(import.meta.dirname, "..");
const pdfPath = process.env.DEMO_PDF_PATH;
const pythonPath = resolve(root, process.env.MINERU_PYTHON ?? ".venv-mineru\\Scripts\\python.exe");
const method = process.env.MINERU_METHOD ?? "ocr";
const language = process.env.MINERU_LANGUAGE ?? "ch";

if (!pdfPath) {
  console.error("DEMO_PDF_PATH is required. The original PDF is intentionally never copied into the repository.");
  process.exit(2);
}

const input = resolve(pdfPath);
if (!existsSync(input) || !statSync(input).isFile()) {
  console.error(`PDF not found: ${input}`);
  process.exit(2);
}

if (!existsSync(pythonPath)) {
  console.error(`MinerU Python runtime not found: ${pythonPath}`);
  console.error("Install MinerU locally or set MINERU_PYTHON to its executable.");
  process.exit(2);
}

function sha256(filePath) {
  return createHash("sha256").update(readFileSync(filePath)).digest("hex");
}

function writeJsonAtomic(filePath, value) {
  const temporaryPath = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  writeFileSync(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  renameSync(temporaryPath, filePath);
}

function walk(directory) {
  const entries = readdirSync(directory, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path) : [path];
  });
}

function runPythonJson(script, args) {
  const result = spawnSync(pythonPath, ["-c", script, ...args], {
    cwd: root,
    encoding: "utf8",
    windowsHide: true,
    env: process.env
  });
  if (result.error) throw new Error(`Unable to inspect PDF with Python: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`Python PDF inspection failed: ${result.stderr || result.stdout}`);
  try {
    return JSON.parse(result.stdout.trim());
  } catch (error) {
    throw new Error(`Python PDF inspection returned invalid JSON: ${error.message}`, { cause: error });
  }
}

const scanDetection = runPythonJson(String.raw`
import json
import sys
import fitz

path = sys.argv[1]
doc = fitz.open(path)
page_char_counts = []
total_chars = 0
for index, page in enumerate(doc):
    text = page.get_text("text") or ""
    count = len(text.strip())
    page_char_counts.append(count)
    total_chars += count
print(json.dumps({
    "method": "PyMuPDF get_text(text) preflight",
    "page_count": len(doc),
    "pages_with_text": sum(1 for count in page_char_counts if count > 0),
    "total_text_chars": total_chars,
    "has_text_layer": total_chars > 0,
    "needs_ocr": total_chars == 0,
    "sample_page_char_counts": page_char_counts[:10]
}, ensure_ascii=False))
`, [input]);

const runtime = runPythonJson(String.raw`
import importlib.metadata as metadata
import json
import platform
import sys

def version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None

print(json.dumps({
    "python": platform.python_version(),
    "magic_pdf": version("magic-pdf") or version("magic_pdf"),
    "torch": version("torch"),
    "platform": platform.platform(),
    "executable": sys.executable
}, ensure_ascii=False))
`, []);

const digest = sha256(input);
const outputRoot = resolve(root, ".cache", "mineru", digest);
const runId = `${new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-")}-${randomUUID()}`;
const outputDir = resolve(outputRoot, "runs", runId);
const configPath = resolve(root, ".cache", "mineru", "magic-pdf.json");
const modelsDir = resolve(root, ".cache", "mineru", "models");
mkdirSync(outputDir, { recursive: true });
mkdirSync(modelsDir, { recursive: true });
writeFileSync(configPath, `${JSON.stringify({
  "device-mode": "cpu",
  "models-dir": modelsDir,
  "table-config": { "model": "rapid_table", "enable": false, "max_time": 400 },
  "formula-config": { "mfd_model": "yolo_v8_mfd", "mfr_model": "unimernet_small", "enable": false },
  "layout-config": { "model": "doclayout_yolo" },
  "latex-delimiter-config": null
}, null, 2)}\n`, "utf8");

console.log(`MinerU input: ${input}`);
console.log(`SHA-256: ${digest}`);
console.log(`PDF preflight: ${scanDetection.page_count} pages; text chars=${scanDetection.total_text_chars}; needs_ocr=${scanDetection.needs_ocr}`);
console.log(`Output: ${outputDir}`);
console.log(`Method: ${method}; language: ${language}`);

const result = spawnSync(
  pythonPath,
  ["-m", "magic_pdf.tools.cli", "-p", input, "-o", outputDir, "-m", method, "-l", language],
  {
    cwd: root,
    stdio: "inherit",
    windowsHide: true,
    env: {
      ...process.env,
      MINERU_TOOLS_CONFIG_JSON: configPath,
      // MinerU 1.3.12 loads its trusted YOLOv10 checkpoint without passing
      // weights_only. PyTorch 2.6+ defaults that argument to true, so opt in
      // to the checkpoint-compatible behavior for this local official model.
      TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD: process.env.TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD ?? "1"
    }
  }
);

if (result.error) {
  console.error(`Unable to start MinerU: ${result.error.message}`);
  process.exit(1);
}
if (result.status !== 0) {
  console.error(`MinerU exited with status ${result.status}. No non-MinerU fallback was used.`);
  process.exit(result.status ?? 1);
}

const files = walk(outputDir).map((path) => relative(outputDir, path).replaceAll("\\", "/"));
const producedFiles = files.filter((file) => file !== "ingest-manifest.json");
if (producedFiles.length === 0) {
  console.error("MinerU returned no output files. The CLI may have swallowed an internal error; inspect the log above.");
  process.exit(1);
}

const middleFile = producedFiles.find((file) => file.toLowerCase().includes("middle") && file.toLowerCase().endsWith(".json"));
if (!middleFile) throw new Error("MinerU output is missing middle.json.");
const middle = JSON.parse(readFileSync(join(outputDir, middleFile), "utf8"));
const pageCount = Array.isArray(middle.pdf_info) ? middle.pdf_info.length : null;
if (!pageCount) throw new Error("MinerU middle.json does not contain pdf_info pages.");
if (scanDetection.page_count !== pageCount) {
  throw new Error(`PDF page count mismatch after MinerU: preflight=${scanDetection.page_count}, middle=${pageCount}`);
}

const outputFileHashes = Object.fromEntries(producedFiles.map((file) => {
  const path = join(outputDir, file);
  return [file, { bytes: statSync(path).size, sha256: sha256(path) }];
}));

const modelFiles = existsSync(modelsDir)
  ? walk(modelsDir).map((path) => ({
      path: relative(root, path).replaceAll("\\", "/"),
      bytes: statSync(path).size,
      sha256: sha256(path)
    }))
  : [];

const manifest = {
  status: "completed",
  parser: "mineru",
  run_id: runId,
  ingest_script_sha256: sha256(fileURLToPath(import.meta.url)),
  parser_version: runtime.magic_pdf,
  parser_command: "magic_pdf.tools.cli",
  parser_method: method,
  language,
  runtime,
  input_basename: input.split(/[\\/]/).pop(),
  input_sha256: digest,
  page_count: pageCount,
  scan_detection: scanDetection,
  output_dir: relative(root, outputDir).replaceAll("\\", "/"),
  files: producedFiles,
  output_file_hashes: outputFileHashes,
  model_repository: {
    name: process.env.MINERU_MODEL_REPOSITORY ?? "opendatalab/PDF-Extract-Kit-1.0",
    revision: process.env.MINERU_MODEL_REVISION ?? null,
    revision_source: process.env.MINERU_MODEL_REVISION
      ? "MINERU_MODEL_REVISION environment variable"
      : "magic-pdf local runtime did not expose a repository revision; local artifact SHA-256 values are recorded"
  },
  model_files: modelFiles,
  generated_at: new Date().toISOString()
};

writeFileSync(join(outputDir, "ingest-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
writeJsonAtomic(join(root, ".cache", "mineru", "latest.json"), manifest);
console.log(`MinerU completed: ${files.length} raw files; ${pageCount} pages; ${modelFiles.length} model artifacts hashed`);
