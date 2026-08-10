import { useRef, useState } from "react";
import {
  CircleAlert,
  FileText,
  Plus,
  X
} from "lucide-react";
import { Button } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import {
  acceptedCourseFileTypes,
  validateCourseFile
} from "./shared";
import { uploadConfirmedCourseFiles } from "./uploadFlow";

const maxSelectedFiles = 4;
const fileOrdinalLabels = ["文件一", "文件二", "文件三", "文件四"];

export function UploadScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const { clearCourseSession, clearLoadedCourse, go, setSelectedUpload, setUploadedFile, showToast } = useAppContext();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  function resetCourseGenerationState() {
    clearLoadedCourse();
    clearCourseSession();
  }

  function chooseFile() {
    if (!uploading) fileInputRef.current?.click();
  }

  async function uploadSelectedFiles() {
    if (selectedFiles.length === 0) {
      chooseFile();
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      const uploadedFile = await uploadConfirmedCourseFiles(selectedFiles, bookcourseRepository);
      resetCourseGenerationState();
      setUploadedFile(uploadedFile);
      setSelectedUpload(true);
      showToast(`${selectedFiles.length} 份资料已上传。你可以确认后再开始后台解析`);
      go("parseReady");
    } catch (err) {
      const message = err instanceof Error ? err.message : "文件上传失败";
      setUploadError(message);
      showToast(message, "warning");
    } finally {
      setUploading(false);
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;
    const validationError = files.map(validateCourseFile).find(Boolean);
    if (validationError) {
      setUploadError(validationError);
      return;
    }
    const existingKeys = new Set(selectedFiles.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
    const additions = files.filter((file) => !existingKeys.has(`${file.name}:${file.size}:${file.lastModified}`));
    const nextFiles = [...selectedFiles, ...additions];
    if (nextFiles.length > maxSelectedFiles) {
      setUploadError(`一次最多选择 ${maxSelectedFiles} 份学习资料`);
      return;
    }
    setSelectedFiles(nextFiles);
    setUploadError(null);
  }

  function removeSelectedFile(index: number) {
    if (uploading) return;
    setSelectedFiles((files) => files.filter((_, fileIndex) => fileIndex !== index));
    setUploadError(null);
  }

  return (
    <div className="screen-stack upload-sheet-screen upload-flow-screen">
      <section className="upload-sheet-card upload-flow-primary">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={acceptedCourseFileTypes}
          onChange={handleFileChange}
          className="hidden-file-input"
        />
        {selectedFiles.length > 0 ? (
          <div
            className={`upload-add-tile has-selection ${uploading ? "is-loading" : ""}`}
            role="group"
            aria-label={`已选择 ${selectedFiles.length} 份学习资料`}
          >
            <div className="upload-selected-file-visual">
              {selectedFiles.map((file, index) => (
                <div className="upload-selected-file-item" key={`${file.name}:${file.size}:${file.lastModified}`}>
                  <span className="upload-selected-file-icon">
                    <FileText size={30} aria-hidden="true" />
                    <button
                      className="upload-remove-file"
                      type="button"
                      disabled={uploading}
                      onClick={() => removeSelectedFile(index)}
                      aria-label={`删除${fileOrdinalLabels[index]}`}
                    >
                      <span className="upload-remove-file-mark"><X size={14} /></span>
                    </button>
                  </span>
                  <strong>{fileOrdinalLabels[index]}</strong>
                </div>
              ))}
              {selectedFiles.length < maxSelectedFiles ? (
                <button
                  className="upload-add-more"
                  type="button"
                  disabled={uploading}
                  onClick={chooseFile}
                  aria-label="添加更多学习资料"
                >
                  <Plus size={22} aria-hidden="true" />
                </button>
              ) : null}
            </div>
          </div>
        ) : (
          <button
            className="upload-add-tile"
            type="button"
            disabled={uploading}
            onClick={chooseFile}
            aria-label="选择学习资料"
          >
            <span className="upload-add-icon"><Plus size={38} aria-hidden="true" /></span>
          </button>
        )}
        <div className="upload-source-copy">
          {uploading ? (
            <div key="upload-status:uploading" className="upload-status-feedback" aria-live="polite">
              <h3>正在上传文件</h3>
              <p>上传完成后，你可以自行决定何时开始解析。</p>
            </div>
          ) : (
            <div className="upload-status-feedback">
              <h3>选择学习资料</h3>
              <p>支持 PDF、常用图片、Word、PowerPoint 和 Excel</p>
            </div>
          )}
        </div>
        {uploadError ? (
          <p key={`upload-error:${uploadError}`} className="helper-text upload-error status-error-copy upload-status-feedback" role="alert">
            <CircleAlert size={16} aria-hidden="true" />
            <span>{uploadError}</span>
          </p>
        ) : null}
        <Button loading={uploading} disabled={uploading} onClick={() => void uploadSelectedFiles()}>
          {uploading ? "上传中" : selectedFiles.length > 0 ? uploadError ? "重试上传" : "上传并继续" : "上传文件"}
        </Button>
      </section>
    </div>
  );
}
