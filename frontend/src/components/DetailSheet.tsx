import { useEffect, useRef, type ReactNode } from "react";

export function DetailSheet({ title, onClose, children }: {title:string;onClose:()=>void;children:ReactNode}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    return () => { dialog?.close(); previous?.focus(); };
  }, []);
  return <dialog ref={ref} className="detail-sheet" aria-labelledby="detail-sheet-title"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target !== event.currentTarget) return; const rect=event.currentTarget.getBoundingClientRect(); if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom) onClose(); }}>
    <header><div><span className="kicker">我的阅读空间</span><h2 id="detail-sheet-title">{title}</h2></div><button className="sheet-close" aria-label="关闭详情" onClick={onClose}>×</button></header>
    <div className="sheet-body">{children}</div>
  </dialog>;
}
