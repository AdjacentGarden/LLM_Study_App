import { describe, expect, it } from "vitest";
import { processingStageIndex, summarizeParseError } from "./processingState";

describe("summarizeParseError", () => {
  it("turns the verbose quality gate page lists into one useful sentence", () => {
    expect(summarizeParseError(
      "Document quality gate failed (unrecoverable=[1, 2, 3], low_quality=[1, 2, 3], score=0.000)"
    )).toBe("3 页未获得可用文字，OCR 识别没有完成。请检查 OCR 服务后重试。");
  });

  it("gives OCR outages a stable user-facing message", () => {
    expect(summarizeParseError("OCR provider 'paddleocr' is unavailable (initialization_failed)"))
      .toBe("OCR 服务当前不可用，请检查识别模型与运行环境后重试。");
  });

  it("caps unexpected backend details so the mobile layout cannot be flooded", () => {
    expect(summarizeParseError("x".repeat(500))).toHaveLength(178);
  });
});

describe("processingStageIndex", () => {
  it.each([
    ["detecting", 0],
    ["ocr_running", 1],
    ["parser_running", 1],
    ["toc_analyzing", 2],
    ["rag_indexing", 3],
    ["course_ready", 4]
  ])("maps backend stage %s to the visible product step", (stage, expected) => {
    expect(processingStageIndex(stage)).toBe(expected);
  });

  it("keeps a failed terminal stage from inventing completed work", () => {
    expect(processingStageIndex("failed")).toBe(-1);
  });
});
