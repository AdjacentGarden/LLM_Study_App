import { describe, expect, it, vi } from "vitest";
import { validateCourseFile } from "./shared";
import { startConfirmedCourseParse, uploadConfirmedCourseFile, uploadConfirmedCourseFiles } from "./uploadFlow";

const selectedPdf = {
  name: "biology.pdf",
  size: 2048,
  type: "application/pdf"
} as File;

describe("upload confirmation flow", () => {
  it("keeps file choice local until the learner confirms upload", async () => {
    const initUpload = vi.fn();
    const uploadFile = vi.fn();

    expect(validateCourseFile(selectedPdf)).toBeNull();
    expect(initUpload).not.toHaveBeenCalled();
    expect(uploadFile).not.toHaveBeenCalled();

    initUpload.mockResolvedValue({
      book_id: "book_selected",
      upload_url: "demo://selected",
      max_upload_bytes: 20_000_000
    });
    uploadFile.mockResolvedValue({
      book_id: "book_selected",
      filename: selectedPdf.name,
      size_bytes: selectedPdf.size,
      status: "uploaded"
    });

    const uploaded = await uploadConfirmedCourseFile(selectedPdf, { initUpload, uploadFile }, 123);

    expect(initUpload).toHaveBeenCalledTimes(1);
    expect(uploadFile).toHaveBeenCalledWith("book_selected", selectedPdf);
    expect(uploaded).toEqual({
      bookId: "book_selected",
      name: "biology.pdf",
      sizeBytes: 2048,
      contentType: "application/pdf",
      uploadedAt: 123,
      origin: "local-upload"
    });
  });

  it("starts parsing only from the explicit ParseReady action", async () => {
    const startParse = vi.fn().mockResolvedValue({
      book_id: "book_selected",
      job_id: "parse_selected",
      status: "pending"
    });
    const uploadedFile = {
      bookId: "book_selected",
      name: "biology.pdf",
      sizeBytes: 2048,
      contentType: "application/pdf",
      uploadedAt: 123
    };

    expect(startParse).not.toHaveBeenCalled();
    await startConfirmedCourseParse(uploadedFile, { startParse });
    expect(startParse).toHaveBeenCalledWith("book_selected");
  });

  it("adds supporting materials to the primary upload before parsing", async () => {
    const supportingPdf = {
      name: "biology-notes.pdf",
      size: 1024,
      type: "application/pdf"
    } as File;
    const initUpload = vi.fn().mockResolvedValue({
      book_id: "book_selected",
      upload_url: "demo://selected",
      max_upload_bytes: 20_000_000
    });
    const uploadFile = vi.fn().mockResolvedValue({
      book_id: "book_selected",
      filename: selectedPdf.name,
      size_bytes: selectedPdf.size,
      status: "uploaded"
    });

    const uploaded = await uploadConfirmedCourseFiles(
      [selectedPdf, supportingPdf],
      { initUpload, uploadFile },
      456
    );

    expect(initUpload).toHaveBeenCalledTimes(1);
    expect(uploadFile).toHaveBeenNthCalledWith(1, "book_selected", selectedPdf);
    expect(uploadFile).toHaveBeenNthCalledWith(2, "book_selected", supportingPdf);
    expect(uploaded.name).toBe("biology.pdf");
    expect(uploaded.uploadedAt).toBe(456);
  });
});
