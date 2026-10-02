import { useEffect, useId, useLayoutEffect, useRef, type RefObject } from "react";
import { Sparkles, Video } from "lucide-react";
import { creditCosts } from "../features/credits/creditStore";
import type { LessonAnimation } from "./lessonAnimations";

export function LessonVideoConfirmDialog({
  caption,
  animation,
  balance,
  error,
  loading=false,
  originRef,
  onCancel,
  onConfirm
}: {
  caption: string;
  animation: LessonAnimation | null;
  balance: number;
  error: string | null;
  loading?: boolean;
  originRef: RefObject<HTMLButtonElement | null>;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const skipFocusRestoreRef = useRef(false);
  const titleId = useId();
  const descriptionId = useId();
  const creditId = useId();

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    cancelRef.current?.focus({ preventScroll: true });
    return () => {
      if (dialog.open) dialog.close();
      if (skipFocusRestoreRef.current) return;
      const origin = originRef.current;
      if (origin?.isConnected) window.requestAnimationFrame(() => origin.focus({ preventScroll: true }));
    };
  }, [originRef]);

  useEffect(() => {
    const onNativeBack = (event: Event) => {
      event.preventDefault();
      onCancel();
    };
    window.addEventListener("bookcourse:native-back", onNativeBack);
    return () => window.removeEventListener("bookcourse:native-back", onNativeBack);
  }, [onCancel]);

  return (
    <dialog
      ref={dialogRef}
      id="lesson-video-confirm-dialog"
      className="lesson-video-confirm-layer"
      aria-labelledby={titleId}
      aria-describedby={animation ? `${descriptionId} ${creditId}` : descriptionId}
      onCancel={(event) => { event.preventDefault(); onCancel(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right
          || event.clientY < bounds.top || event.clientY > bounds.bottom) onCancel();
      }}
    >
      <span className="lesson-video-confirm-icon" aria-hidden="true"><Video size={30} strokeWidth={2.3} /><Sparkles className="lesson-video-confirm-sparkle" size={17} strokeWidth={2.5} /></span>
      <h2 id={titleId}>{"是否生成讲解视频？"}</h2>
      <p id={descriptionId}>
        {`为“${caption}”准备基于当前资料的讲解视频。服务可用性由后端确认。`}
      </p>
      {<div id={creditId} className="lesson-video-confirm-credit" aria-live="polite"><strong>本次生成消耗 {creditCosts.video} 积分</strong><span>当前剩余 {balance} 积分</span></div>}
      {error ? <p className="lesson-video-confirm-error" role="alert">{error}</p> : null}
      <div className="lesson-video-confirm-actions">
        <button ref={cancelRef} type="button" onClick={onCancel}>{"否，暂不生成"}</button>
        {<button className="primary" disabled={loading} type="button" onClick={() => {
          skipFocusRestoreRef.current = true;
          onConfirm();
        }}>{loading ? "正在生成…" : "是，生成视频"}</button>}
      </div>
    </dialog>
  );
}
