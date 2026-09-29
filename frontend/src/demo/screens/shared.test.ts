import { describe, expect, it } from "vitest";

import {
  acceptedCourseFileTypes,
  fileTitleBeforeParenthesis,
  getFileKind,
  liveBookTitle,
  sourceUnitCountLabel,
  supportedCourseExtensions
} from "./shared";


describe("stage 5 upload format contract", () => {
  it("matches the backend extension whitelist and excludes legacy Office", () => {
    expect([...supportedCourseExtensions]).toEqual([
      ".pdf", ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".docx", ".pptx", ".xlsx"
    ]);
    const acceptedExtensions = acceptedCourseFileTypes.split(",").filter((item) => item.startsWith("."));
    expect(acceptedExtensions).toEqual([...supportedCourseExtensions]);
    expect(acceptedExtensions).not.toContain(".doc");
    expect(acceptedExtensions).not.toContain(".ppt");
    expect(acceptedExtensions).not.toContain(".xls");
  });

  it("labels all Office formats without pretending they are generic files", () => {
    expect(getFileKind("course.docx", "application/octet-stream")).toBe("Word 文档");
    expect(getFileKind("slides.pptx", "application/octet-stream")).toBe("PowerPoint 演示文稿");
    expect(getFileKind("data.xlsx", "application/octet-stream")).toBe("Excel 工作簿");
  });

  it("uses logical source-unit count labels", () => {
    expect(sourceUnitCountLabel({
      book_id: "book",
      filename: "slides.pptx",
      file_type: "pptx",
      page_count: 3,
      has_text_layer: true,
      needs_ocr: false,
      source_unit: "slide",
      source_locations: [],
      quality_warnings: []
    })).toBe("3 个幻灯片");
  });

  it("shortens the demo textbook name for display", () => {
    expect(liveBookTitle({
      bookId: "book_biology_2",
      name: "人教版高中生物必修2遗传与进化 (人民教育出版社, 课程教材研究所, 生物课程教材研究开发中心.pdf",
      sizeBytes: 0,
      contentType: "application/pdf",
      uploadedAt: 0
    })).toBe("人教版高中生物必修二遗传与进化");
  });

  it("shows only the uploaded title before its first parenthesis", () => {
    expect(fileTitleBeforeParenthesis(
      "人教版高中生物必修2遗传与进化 (人民教育出版社, 课程教材研究所.pdf"
    )).toBe("人教版高中生物必修2遗传与进化");
    expect(fileTitleBeforeParenthesis("高等数学（同济大学）.pdf")).toBe("高等数学");
    expect(fileTitleBeforeParenthesis("普通文件.pdf")).toBe("普通文件.pdf");
  });
});
