import { useEffect, useRef, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { ArrowRight, BookOpenText, Plus } from "lucide-react";
import { useReducedMotion } from "../../motion/useReducedMotion";
import type { HomeBookListState, HomeBookModel } from "../../screens/homeBookModel";

type HomeBookCarouselProps = Readonly<{
  books: readonly HomeBookModel[];
  selectedBookId: string | null;
  listState: HomeBookListState;
  onSelectBook: (bookId: string) => void;
  onAddBook: () => void;
  onOpenLibrary: () => void;
}>;

const selectionAnchorRatio = .42;

function centeredScrollLeft(scroller: HTMLDivElement, option: HTMLButtonElement) {
  const scrollerBounds = scroller.getBoundingClientRect();
  const optionBounds = option.getBoundingClientRect();
  const optionCenterInsideScroller = scroller.scrollLeft
    + optionBounds.left
    - scrollerBounds.left
    + optionBounds.width / 2;
  const maximumScrollLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
  return Math.max(0, Math.min(
    maximumScrollLeft,
    optionCenterInsideScroller - scroller.clientWidth * selectionAnchorRatio
  ));
}

export function HomeBookCarousel({
  books,
  selectedBookId,
  listState,
  onSelectBook,
  onAddBook,
  onOpenLibrary
}: HomeBookCarouselProps) {
  const reducedMotion = useReducedMotion();
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const optionRefs = useRef(new Map<string, HTMLButtonElement>());
  const scrollSettleTimerRef = useRef<number | null>(null);
  const programmaticScrollBookIdRef = useRef<string | null>(null);
  const dragStateRef = useRef<{
    moved: boolean;
    pointerId: number;
    startScrollLeft: number;
    startX: number;
  } | null>(null);
  const suppressClickRef = useRef(false);
  const selectedIndex = Math.max(0, books.findIndex((book) => book.bookId === selectedBookId));

  useEffect(() => {
    if (!selectedBookId) return;
    const scroller = scrollerRef.current;
    const option = optionRefs.current.get(selectedBookId);
    if (!scroller || !option) return;
    if (scrollSettleTimerRef.current !== null) {
      window.clearTimeout(scrollSettleTimerRef.current);
      scrollSettleTimerRef.current = null;
    }
    const nextScrollLeft = centeredScrollLeft(scroller, option);
    if (Math.abs(scroller.scrollLeft - nextScrollLeft) < .5) {
      programmaticScrollBookIdRef.current = null;
      return;
    }
    programmaticScrollBookIdRef.current = selectedBookId;
    scroller.scrollTo({
      behavior: reducedMotion ? "auto" : "smooth",
      left: nextScrollLeft
    });
  }, [reducedMotion, selectedBookId]);

  useEffect(() => () => {
    if (scrollSettleTimerRef.current !== null) {
      window.clearTimeout(scrollSettleTimerRef.current);
    }
  }, []);

  function selectByIndex(index: number, focus = false) {
    const book = books[Math.max(0, Math.min(books.length - 1, index))];
    if (!book) return;
    onSelectBook(book.bookId);
    if (focus) {
      window.requestAnimationFrame(() => optionRefs.current.get(book.bookId)?.focus({ preventScroll: true }));
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (books.length === 0) return;
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") selectByIndex(0, true);
    else if (event.key === "End") selectByIndex(books.length - 1, true);
    else selectByIndex(selectedIndex + (event.key === "ArrowRight" ? 1 : -1), true);
  }

  function settleSelectionFromScroll() {
    const scroller = scrollerRef.current;
    if (!scroller || books.length < 2) return;
    const programmaticBookId = programmaticScrollBookIdRef.current;
    if (programmaticBookId) {
      const programmaticOption = optionRefs.current.get(programmaticBookId);
      if (programmaticOption) {
        const remainingDistance = Math.abs(
          scroller.scrollLeft - centeredScrollLeft(scroller, programmaticOption)
        );
        if (remainingDistance <= 2) {
          programmaticScrollBookIdRef.current = null;
          return;
        }
        scrollSettleTimerRef.current = window.setTimeout(settleSelectionFromScroll, 120);
        return;
      }
      programmaticScrollBookIdRef.current = null;
    }
    const scrollerAnchor = scroller.getBoundingClientRect().left + scroller.clientWidth * selectionAnchorRatio;
    let nearestBookId = selectedBookId;
    let nearestDistance = Number.POSITIVE_INFINITY;
    for (const book of books) {
      const option = optionRefs.current.get(book.bookId);
      if (!option) continue;
      const bounds = option.getBoundingClientRect();
      const distance = Math.abs(bounds.left + bounds.width / 2 - scrollerAnchor);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestBookId = book.bookId;
      }
    }
    if (nearestBookId && nearestBookId !== selectedBookId) onSelectBook(nearestBookId);
  }

  function handleScroll() {
    if (dragStateRef.current) return;
    if (scrollSettleTimerRef.current !== null) {
      window.clearTimeout(scrollSettleTimerRef.current);
    }
    scrollSettleTimerRef.current = window.setTimeout(settleSelectionFromScroll, 120);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    programmaticScrollBookIdRef.current = null;
    if (event.pointerType !== "mouse" || event.button !== 0 || books.length < 2) return;
    if (scrollSettleTimerRef.current !== null) {
      window.clearTimeout(scrollSettleTimerRef.current);
      scrollSettleTimerRef.current = null;
    }
    event.currentTarget.scrollTo({ behavior: "auto", left: event.currentTarget.scrollLeft });
    dragStateRef.current = {
      moved: false,
      pointerId: event.pointerId,
      startScrollLeft: event.currentTarget.scrollLeft,
      startX: event.clientX
    };
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - dragState.startX;
    if (!dragState.moved && Math.abs(deltaX) >= 4) {
      dragState.moved = true;
      event.currentTarget.setPointerCapture(event.pointerId);
      event.currentTarget.classList.add("is-dragging");
    }
    if (!dragState.moved) return;
    event.preventDefault();
    event.currentTarget.scrollLeft = dragState.startScrollLeft - deltaX;
  }

  function finishPointerDrag(event: ReactPointerEvent<HTMLDivElement>, commitSelection: boolean) {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    const scroller = event.currentTarget;
    const dragDistance = event.clientX - dragState.startX;
    const shouldSwitchBook = commitSelection && Math.abs(dragDistance) >= 24;
    dragStateRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    suppressClickRef.current = dragState.moved;
    if (shouldSwitchBook) {
      selectByIndex(selectedIndex + (dragDistance < 0 ? 1 : -1));
      window.requestAnimationFrame(() => scroller.classList.remove("is-dragging"));
    } else {
      scroller.classList.remove("is-dragging");
      const selectedOption = selectedBookId ? optionRefs.current.get(selectedBookId) : null;
      if (selectedOption) {
        scroller.scrollTo({
          behavior: reducedMotion ? "auto" : "smooth",
          left: centeredScrollLeft(scroller, selectedOption)
        });
      }
    }
    window.setTimeout(() => {
      suppressClickRef.current = false;
    }, 0);
  }

  function handleBookClick(bookId: string) {
    if (suppressClickRef.current) return;
    onSelectBook(bookId);
  }

  const selectedBook = books[selectedIndex] ?? null;

  return (
    <section className="home-book-picker" aria-labelledby="home-book-picker-title">
      <div className="home-book-picker-heading">
        <div>
          <h2 id="home-book-picker-title">我的教材</h2>
          <span>{books.length > 0 ? `共 ${books.length} 本` : "从原书开始学习"}</span>
        </div>
        {books.length > 0 ? (
          <button type="button" onClick={onOpenLibrary}>
            全部教材 <ArrowRight size={15} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      {listState === "loading" ? (
        <div className="home-book-carousel-skeleton" aria-label="正在加载教材" aria-busy="true">
          <p className="home-visually-hidden" role="status" aria-live="polite" aria-atomic="true">
            正在加载教材列表，上传和学习操作暂不可用。
          </p>
          <span />
          <span />
          <span />
        </div>
      ) : listState === "empty" ? (
        <div className="home-book-empty">
          <span aria-hidden="true"><BookOpenText size={24} /></span>
          <div>
            <strong>还没有教材</strong>
            <small>上传教材后，可以从原书位置继续学习。</small>
          </div>
          <button type="button" onClick={onAddBook}>
            <Plus size={17} aria-hidden="true" />上传第一本教材
          </button>
        </div>
      ) : listState === "content" ? (
        <>
          <div
            ref={scrollerRef}
            className={`home-book-carousel ${books.length === 1 ? "is-single" : ""}`}
            data-mouse-drag-scroll="self"
            role="listbox"
            aria-label="选择教材"
            aria-orientation="horizontal"
            onKeyDown={handleKeyDown}
            onPointerCancel={(event) => finishPointerDrag(event, false)}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={(event) => finishPointerDrag(event, true)}
            onScroll={handleScroll}
          >
            {books.map((book, index) => {
              const selected = book.bookId === selectedBookId;
              return (
                <button
                  ref={(node) => {
                    if (node) optionRefs.current.set(book.bookId, node);
                    else optionRefs.current.delete(book.bookId);
                  }}
                  className={`home-book-option ${selected ? "is-selected" : ""}`}
                  data-book-id={book.bookId}
                  key={book.bookId}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  aria-label={`${book.title}，第 ${index + 1} 本，共 ${books.length} 本，${book.statusLabel}${selected ? "，当前选中" : ""}`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => handleBookClick(book.bookId)}
                >
                  <span
                    className={`home-book-cover ${book.coverUrl ? "has-original-cover" : ""}`}
                    data-cover-variant={book.coverVariant}
                    aria-hidden="true"
                  >
                    {book.coverUrl ? (
                      <img
                        src={book.coverUrl}
                        alt=""
                        draggable="false"
                        loading={selected ? "eager" : "lazy"}
                      />
                    ) : (
                      <>
                        <span className="home-book-cover-kicker">BOOKCOURSE</span>
                        <strong>{book.title}</strong>
                        <span className="home-book-cover-mark">{book.title.trim().slice(0, 2) || "教材"}</span>
                        <small>{book.statusLabel}</small>
                      </>
                    )}
                  </span>
                </button>
              );
            })}
          </div>

          {selectedBook ? (
            <div className="home-book-selection-summary" aria-live="polite">
              <strong title={selectedBook.title}>{selectedBook.title}</strong>
              <span>{selectedIndex + 1} / {books.length}</span>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
