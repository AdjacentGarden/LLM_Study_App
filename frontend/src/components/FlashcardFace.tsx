import type { Flashcard } from "../types/api";
import { useRef, useState } from "react";
import { swipeDirection } from "./cardGesture";
import { splitReadableParagraphs } from "./textStructure";

export function FlashcardFace({ card, flipped, busy, onFlip, onSwipe }: { card: Flashcard; flipped: boolean; busy: boolean; onFlip: () => void; onSwipe?: (direction: number) => void }) {
  const start = useRef<{x:number;y:number}|null>(null);
  const swiped = useRef(false);
  const [dragX,setDragX] = useState(0);
  const pages = [...new Set(card.citations.map(c => c.page_number))].join("、");
  const backParagraphs = splitReadableParagraphs(card.back);
  return <button type="button" className={`flashcard flip-card ${flipped ? "flipped" : ""} ${dragX ? "dragging" : ""}`} style={{"--drag-x":`${dragX}px`,"--drag-tilt":`${dragX/25}deg`} as React.CSSProperties} disabled={busy}
    onPointerDown={event=>{if(!event.isPrimary||event.button!==0)return;start.current={x:event.clientX,y:event.clientY};swiped.current=false;}}
    onPointerMove={event=>{if(!start.current)return;const dx=event.clientX-start.current.x,dy=event.clientY-start.current.y;if(Math.abs(dx)>12&&Math.abs(dx)>Math.abs(dy)*1.5){event.currentTarget.setPointerCapture(event.pointerId);setDragX(Math.max(-85,Math.min(85,dx*.5)));}}}
    onPointerCancel={()=>{start.current=null;swiped.current=true;setDragX(0);}}
    onPointerUp={event=>{setDragX(0);if(!start.current) return;const dx=event.clientX-start.current.x,dy=event.clientY-start.current.y;const delta=swipeDirection(dx,dy);start.current=null;swiped.current=Math.hypot(dx,dy)>12;if(delta&&onSwipe)onSwipe(delta);}}
    onClick={()=>{if(swiped.current){swiped.current=false;return;}onFlip();}}
    aria-label={flipped ? "闪卡答案，点击返回题目" : "闪卡题目，点击翻面查看答案"} aria-pressed={flipped}>
    <span className="flip-card-inner">
      <span className="flip-face flip-front" aria-hidden={flipped}><span className="flash-eyebrow">问题</span><span className="flash-text">{card.front}</span><span className="flash-hint">点击查看答案</span></span>
      <span className="flip-face flip-back" aria-hidden={!flipped}><span className="flash-eyebrow">答案</span><span className="flash-text">{backParagraphs.map((paragraph,index)=><span className="flash-copy-paragraph" key={`${index}-${paragraph.slice(0,20)}`}>{paragraph}</span>)}</span><span className="flash-hint">教材第 {pages} 页 · 点击返回</span></span>
    </span>
  </button>;
}
