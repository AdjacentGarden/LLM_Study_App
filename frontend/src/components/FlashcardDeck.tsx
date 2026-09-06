import { useRef } from "react";
import type { CourseActivity, Flashcard } from "../types/api";
import { FlashcardFace } from "./FlashcardFace";
import { Icon } from "./Icon";
import { nextCardIndex } from "./cardGesture";

export function FlashcardDeck({ cards, index, flipped, busy, activity, onFlip, onCard, onRate }: {
  cards: Flashcard[]; index: number; flipped: boolean; busy: boolean; activity: CourseActivity | null;
  onFlip: () => void; onCard: (index: number) => void; onRate: (rating: "again" | "hard" | "good" | "easy") => void;
}) {
  const direction = useRef(1);
  const card = cards[index];
  if (!card) return <p className="empty-state">本章暂无可用闪卡。</p>;
  function move(next: number) {
    if (busy || next === index) return;
    direction.current = next > index ? 1 : -1;
    onCard(next);
  }
  return <section className="study-deck" aria-label="知识闪卡" onKeyDown={event=>{
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault(); move(nextCardIndex(index, event.key === "ArrowRight" ? 1 : -1, cards.length));
    }
  }}>
    <div className="deck-heading"><span><Icon name="cards"/>一张卡，一个知识点</span><b aria-live="polite">{index+1}<small> / {cards.length}</small></b></div>
    <div className="deck-progress" aria-hidden="true">{cards.map((_,i)=><i key={i} className={i<=index?"reached":""}/>)}</div>
    {activity?.review_state && <div className="review-scheduled" role="status"><Icon name="check" size={18}/><div><strong>已保存，下次复习也安排好了</strong><span>{new Intl.DateTimeFormat("zh-CN",{month:"long",day:"numeric",hour:"2-digit",minute:"2-digit"}).format(new Date(activity.review_state.due_at))}</span></div></div>}
    <div className="deck-card-wrap" key={card.card_id} style={{"--card-direction":direction.current} as React.CSSProperties}>
      <FlashcardFace card={card} flipped={flipped} busy={busy} onFlip={onFlip} onSwipe={delta=>move(nextCardIndex(index,delta,cards.length))}/>
    </div>
    <p className="deck-reason"><Icon name="spark" size={15}/>{card.reason_for_user}</p>
    {flipped && <div className="recall-controls"><p>刚才，你记得多少？</p><div className="recall-options">{([
      ["again","再学一次","还没记住"],["hard","有点模糊","需要提示"],["good","基本记得","再巩固下"],["easy","很有把握","轻松想起"],
    ] as const).map(([id,label,hint])=><button key={id} className={`recall-${id}`} disabled={busy} onClick={()=>onRate(id)}><b>{label}</b><small>{hint}</small></button>)}</div></div>}
    <div className="deck-navigation"><button aria-label="上一张闪卡" disabled={busy||index===0} onClick={()=>move(index-1)}><Icon name="back"/><span>上一张</span></button><span>左右滑动也能换卡</span><button aria-label="下一张闪卡" disabled={busy||index===cards.length-1} onClick={()=>move(index+1)}><span>下一张</span><Icon name="arrow"/></button></div>
    <div className="dot-row deck-dots">{cards.map((_,i)=><button key={i} aria-label={`第${i+1}张闪卡`} aria-current={i===index?"step":undefined} disabled={busy} className={i===index?"active":""} onClick={()=>move(i)}/>)}</div>
  </section>;
}
