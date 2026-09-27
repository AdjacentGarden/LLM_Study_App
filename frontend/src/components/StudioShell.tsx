import {
  Suspense,
  createContext,
  lazy,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { Anchor } from "../api/learningStudio";

export type StudioOpening = { anchor: Anchor; mode: "image" | "video" | "notes" };

const StudioContext = createContext<(opening: StudioOpening) => void>(() => {});
const loadStudio = () => import("./LearningStudio");
const StudioDialog = lazy(() => loadStudio().then(module => ({ default: module.StudioDialog })));
const warmStudio = () => { void loadStudio(); };

export function StudioProvider({ children }: { children: ReactNode }) {
  const [opening, setOpening] = useState<StudioOpening | null>(null);
  return (
    <StudioContext.Provider value={setOpening}>
      {children}
      {opening && (
        <Suspense fallback={<div className="studio-code-loading" role="status">正在打开…</div>}>
          <StudioDialog
            key={opening.anchor.book_id}
            opening={opening}
            onClose={() => setOpening(null)}
          />
        </Suspense>
      )}
    </StudioContext.Provider>
  );
}

export function StudioEntry({ anchor }: { anchor: Anchor }) {
  const open = useContext(StudioContext);
  return (
    <button
      className="learning-studio-entry"
      onPointerEnter={warmStudio}
      onFocus={warmStudio}
      onTouchStart={warmStudio}
      onClick={() => open({ anchor, mode: "notes" })}
    >
      <span className="studio-entry-icon" aria-hidden="true">✎</span>
      <span><strong>学习笔记</strong><small>手写、语音与生成内容</small></span>
      <span aria-hidden="true">↗</span>
    </button>
  );
}

export function StudioMediaActions({ anchor, label = "辅助学习" }: { anchor: Anchor; label?: string }) {
  const open = useContext(StudioContext);
  const ready = (anchor.excerpt ?? "").trim().length >= 4;
  return (
    <div className="studio-media-actions" aria-label={label} onPointerEnter={warmStudio}>
      <span>{label}</span>
      <div>
        <button type="button" disabled={!ready} onFocus={warmStudio} onClick={() => open({ anchor, mode: "image" })}>▧ 生成图解</button>
        <button type="button" disabled={!ready} onFocus={warmStudio} onClick={() => open({ anchor, mode: "video" })}>▷ 生成短片</button>
      </div>
    </div>
  );
}

export function StudyPassage({ anchor, text }: { anchor: Anchor; text: string }) {
  const open = useContext(StudioContext);
  const ref = useRef<HTMLParagraphElement>(null);
  const [selected, setSelected] = useState("");
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    function capture() {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const selection = window.getSelection();
        if (selection && ref.current?.contains(selection.anchorNode) && ref.current.contains(selection.focusNode)) {
          const value = selection.toString().trim();
          setSelected(value.length >= 4 ? value.slice(0, 3000) : "");
        }
      }, 120);
    }
    document.addEventListener("selectionchange", capture);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("selectionchange", capture);
    };
  }, []);
  const act = (mode: StudioOpening["mode"]) => {
    warmStudio();
    open({ anchor: { ...anchor, excerpt: selected || text.slice(0, 3000) }, mode });
  };
  return (
    <>
      <p className="studio-selectable" ref={ref}>{text}</p>
      <div className="passage-tools" aria-label="选段学习工具" onPointerEnter={warmStudio}>
        <small>{selected ? `已选 ${selected.length} 字` : "长按选择教材内容"}</small>
        <div>
          <button onFocus={warmStudio} onClick={() => act("image")}>▧ 看图理解</button>
          <button onFocus={warmStudio} onClick={() => act("video")}>▷ 看短片</button>
          <button onFocus={warmStudio} onClick={() => act("notes")}>✎ 记笔记</button>
        </div>
      </div>
    </>
  );
}
