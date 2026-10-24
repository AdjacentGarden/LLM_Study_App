import type { StudioResult } from "../api/learningStudio";

function reviewState(result: StudioResult): "rejected" | "unknown" | undefined {
  if (result.review && typeof result.review === "object") {
    if (result.review.passed === false) return "rejected";
    if (result.review.passed !== true) return "unknown";
  }
  return undefined;
}

export function canKeepNoteResult(result: StudioResult, status: string): boolean {
  return status === "succeeded" && !result.provisional && !reviewState(result);
}

export function NoteResultNotice({ result, status }: { result: StudioResult; status?: string }) {
  const review = reviewState(result);
  const warning = status === "failed"
    ? "本次整理未完成，不能作为完成结果保留。"
    : review === "rejected"
      ? "本次整理未通过审核，不能作为完成结果保留。"
      : result.provisional
        ? "内容仍在完善，尚不能作为完成结果保留。"
        : review === "unknown"
          ? "审核状态尚未确认，暂不能作为完成结果保留。"
          : status !== undefined && status !== "succeeded"
            ? "整理尚未完成，暂不能作为完成结果保留。"
            : undefined;
  const notice = !warning && typeof result.quality_notice === "string" && result.quality_notice.trim()
    ? result.quality_notice : undefined;
  const count = !warning && typeof result.excluded_optional_count === "number"
    && Number.isSafeInteger(result.excluded_optional_count) && result.excluded_optional_count >= 0
    ? result.excluded_optional_count : undefined;
  if (!warning && !notice && count === undefined) return null;
  return (
    <aside className="studio-caution" role="status" aria-label="整理结果说明">
      {warning && <p>{warning}</p>}
      {notice && <p>{notice}</p>}
      {count !== undefined
        ? <p>未纳入的 AI 补充：{count} 项。</p>
        : notice && <p>未纳入的 AI 补充数量尚未确认。</p>}
    </aside>
  );
}
