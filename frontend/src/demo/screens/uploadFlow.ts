import type {
  FileSaveResponse,
  ParseJobResponse,
  UploadInitRequest,
  UploadInitResponse
} from "../types/api";
import type { UploadedCourseFile } from "../types/app";

export type UploadConfirmationApi = {
  initUpload: (payload: UploadInitRequest) => Promise<UploadInitResponse>;
  uploadFile: (bookId: string, file: File) => Promise<FileSaveResponse>;
};

export type ParseStarterApi = {
  startParse: (bookId: string) => Promise<ParseJobResponse>;
};

export const presetBiologyCourseFile = {
  name: "人教版高中生物必修2遗传与进化.pdf",
  sizeBytes: Math.round(38.8 * 1024 * 1024),
  contentType: "application/pdf",
  lastModified: 1_785_628_800_000
} as const;

/**
 * The product demo creates its biology textbook only after the learner clicks
 * the upload add control. The file keeps a tiny in-memory body while exposing
 * the real demo metadata, so selecting it never allocates a 38.8 MB placeholder.
 */
export function createPresetBiologyCourseFile(): File {
  const file = new File(["BookCourse AI preset biology textbook"], presetBiologyCourseFile.name, {
    type: presetBiologyCourseFile.contentType,
    lastModified: presetBiologyCourseFile.lastModified
  });
  Object.defineProperty(file, "size", {
    configurable: false,
    enumerable: true,
    value: presetBiologyCourseFile.sizeBytes
  });
  return file;
}

export function isPresetBiologyCourseFile(file: File): boolean {
  return file.name === presetBiologyCourseFile.name
    && file.size === presetBiologyCourseFile.sizeBytes
    && file.lastModified === presetBiologyCourseFile.lastModified;
}

export async function uploadConfirmedCourseFile(
  file: File,
  api: UploadConfirmationApi,
  uploadedAt = Date.now()
): Promise<UploadedCourseFile> {
  const init = await api.initUpload({
    filename: file.name,
    content_type: file.type || "application/octet-stream",
    size_bytes: file.size
  });
  await api.uploadFile(init.book_id, file);
  return {
    bookId: init.book_id,
    name: file.name,
    sizeBytes: file.size,
    contentType: file.type || "application/octet-stream",
    uploadedAt,
    origin: "local-upload"
  };
}

export async function uploadConfirmedCourseFiles(
  files: readonly File[],
  api: UploadConfirmationApi,
  uploadedAt = Date.now()
): Promise<UploadedCourseFile> {
  const [primaryFile, ...supportingFiles] = files;
  if (!primaryFile) throw new Error("请先选择学习资料");
  const uploaded = await uploadConfirmedCourseFile(primaryFile, api, uploadedAt);
  for (const file of supportingFiles) {
    await api.uploadFile(uploaded.bookId, file);
  }
  return uploaded;
}

export function startConfirmedCourseParse(
  uploadedFile: UploadedCourseFile,
  api: ParseStarterApi
): Promise<ParseJobResponse> {
  return api.startParse(uploadedFile.bookId);
}
