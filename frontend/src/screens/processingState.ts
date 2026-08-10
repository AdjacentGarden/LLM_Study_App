export function summarizeParseError(error: string | null | undefined): string {
  const value = error?.trim();
  if (!value) return "解析失败，请重试；如果问题持续，请检查 OCR 服务状态。";
  if (value.startsWith("Document quality gate failed")) {
    const pageList = value.match(/unrecoverable=\[([^\]]*)\]/)?.[1] ?? "";
    const pageCount = pageList ? pageList.split(",").filter(Boolean).length : 0;
    const scope = pageCount > 0 ? `${pageCount} 页` : "部分页面";
    return `${scope}未获得可用文字，OCR 识别没有完成。请检查 OCR 服务后重试。`;
  }
  if (/OCR provider|ocr.*unavailable/i.test(value)) {
    return "OCR 服务当前不可用，请检查识别模型与运行环境后重试。";
  }
  return value.length <= 180 ? value : `${value.slice(0, 177)}…`;
}

export function processingStageIndex(stage: string | null | undefined, done = false): number {
  if (done || stage === "done") return 4;
  if (["queued", "detecting"].includes(stage ?? "")) return 0;
  if (["mineru_running", "parser_running", "ocr_running"].includes(stage ?? "")) return 1;
  if (["toc_analyzing", "chapter_generating"].includes(stage ?? "")) return 2;
  if (["asset_extracting", "rag_chunking", "rag_indexing"].includes(stage ?? "")) return 3;
  if (stage === "course_ready") return 4;
  return -1;
}
