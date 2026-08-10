import {
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent
} from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Search, SearchX, X } from "lucide-react";
import { CommunitySearchKeyboard } from "../components/CommunitySearchKeyboard";
import { communityBooks } from "../data/mockBook";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";
import { CommunityCover } from "./CommunityCover";
import {
  communityCategories,
  filterCommunityBooks,
  type CommunityCategory
} from "./communityCatalog";

export function CommunityScreen() {
  const { go, selectCommunityBook } = useAppContext();
  const discoveryMotion = useLocalMotionItem("community:discovery");
  const searchInputId = useId();
  const categoryMenuId = useId();
  const searchInputRef = useRef<HTMLInputElement>(null);
  const categoryListRef = useRef<HTMLDivElement>(null);
  const categoryDragRef = useRef<{
    pointerId: number;
    startX: number;
    startScrollLeft: number;
    moved: boolean;
  } | null>(null);
  const suppressCategoryClickRef = useRef(false);
  const [selectedCategory, setSelectedCategory] = useState<CommunityCategory>("推荐");
  const [categoryMenuOpen, setCategoryMenuOpen] = useState(false);
  const [categoryDragging, setCategoryDragging] = useState(false);
  const [searchKeyboardOpen, setSearchKeyboardOpen] = useState(false);
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim();
  const visibleBooks = useMemo(
    () => filterCommunityBooks(communityBooks, normalizedQuery ? "全部" : selectedCategory, query),
    [normalizedQuery, query, selectedCategory]
  );
  const resultSummary = normalizedQuery
    ? `“${normalizedQuery}”找到 ${visibleBooks.length} 本书`
    : selectedCategory === "推荐"
      ? `为你推荐 ${visibleBooks.length} 本书`
      : `${selectedCategory}分类共 ${visibleBooks.length} 本书`;

  function openCommunityBook(bookId: string) {
    selectCommunityBook(bookId);
    go("communityBook");
  }

  function resetDiscovery() {
    setSelectedCategory("推荐");
    setQuery("");
    setCategoryMenuOpen(false);
    setSearchKeyboardOpen(false);
  }

  function selectCategory(category: CommunityCategory) {
    setSelectedCategory(category);
    setQuery("");
    setCategoryMenuOpen(false);
    setSearchKeyboardOpen(false);
  }

  function restoreSearchSelection(position: number) {
    window.requestAnimationFrame(() => {
      const input = searchInputRef.current;
      if (!input) return;
      input.focus({ preventScroll: true });
      input.setSelectionRange(position, position);
    });
  }

  function insertSearchText(text: string) {
    const input = searchInputRef.current;
    const start = input?.selectionStart ?? query.length;
    const end = input?.selectionEnd ?? start;
    const nextQuery = `${query.slice(0, start)}${text}${query.slice(end)}`;
    setQuery(nextQuery);
    restoreSearchSelection(start + text.length);
  }

  function deleteSearchText() {
    const input = searchInputRef.current;
    const start = input?.selectionStart ?? query.length;
    const end = input?.selectionEnd ?? start;

    if (start !== end) {
      setQuery(`${query.slice(0, start)}${query.slice(end)}`);
      restoreSearchSelection(start);
      return;
    }

    if (start === 0) return;
    const prefix = Array.from(query.slice(0, start));
    prefix.pop();
    const nextPrefix = prefix.join("");
    setQuery(`${nextPrefix}${query.slice(end)}`);
    restoreSearchSelection(nextPrefix.length);
  }

  function useSearchSuggestion(suggestion: string) {
    setQuery(suggestion);
    restoreSearchSelection(suggestion.length);
  }

  function closeSearchKeyboard() {
    setSearchKeyboardOpen(false);
    searchInputRef.current?.blur();
  }

  function handleSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape" || event.key === "Enter") {
      event.preventDefault();
      closeSearchKeyboard();
    }
  }

  function startCategoryDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const categoryList = categoryListRef.current;
    if (
      event.pointerType !== "mouse" ||
      event.button !== 0 ||
      !categoryList ||
      categoryList.scrollWidth <= categoryList.clientWidth
    ) {
      return;
    }

    categoryDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: categoryList.scrollLeft,
      moved: false
    };
  }

  function moveCategoryDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = categoryDragRef.current;
    const categoryList = categoryListRef.current;
    if (!drag || !categoryList || drag.pointerId !== event.pointerId) return;

    const deltaX = event.clientX - drag.startX;
    if (!drag.moved && Math.abs(deltaX) < 4) return;

    if (!drag.moved) {
      drag.moved = true;
      suppressCategoryClickRef.current = true;
      setCategoryDragging(true);
      categoryList.setPointerCapture(event.pointerId);
    }

    categoryList.scrollLeft = drag.startScrollLeft - deltaX;
    event.preventDefault();
  }

  function finishCategoryDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = categoryDragRef.current;
    const categoryList = categoryListRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;

    categoryDragRef.current = null;
    setCategoryDragging(false);
    if (categoryList?.hasPointerCapture(event.pointerId)) {
      categoryList.releasePointerCapture(event.pointerId);
    }

    if (drag.moved) {
      window.setTimeout(() => {
        suppressCategoryClickRef.current = false;
      }, 0);
    }
  }

  function cancelCategoryDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = categoryDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;

    categoryDragRef.current = null;
    suppressCategoryClickRef.current = false;
    setCategoryDragging(false);
  }

  function preventCategoryClickAfterDrag(event: ReactMouseEvent<HTMLDivElement>) {
    if (!suppressCategoryClickRef.current) return;

    suppressCategoryClickRef.current = false;
    event.preventDefault();
    event.stopPropagation();
  }

  return (
    <div className="screen-stack community-screen">
      <div {...discoveryMotion.attributes} className="community-discovery-controls">
        <div className="community-search-panel" role="search">
          <label className="community-search-label" htmlFor={searchInputId}>
            搜索课程
          </label>
          <div className="community-search-field">
            <Search size={19} aria-hidden="true" />
            <input
              ref={searchInputRef}
              id={searchInputId}
              type="search"
              inputMode="none"
              enterKeyHint="search"
              value={query}
              placeholder="搜索课程"
              autoComplete="off"
              aria-controls="community-search-keyboard"
              aria-expanded={searchKeyboardOpen}
              onChange={(event) => setQuery(event.target.value)}
              onClick={() => setSearchKeyboardOpen(true)}
              onFocus={() => setSearchKeyboardOpen(true)}
              onKeyDown={handleSearchKeyDown}
            />
            {query ? (
              <button
                type="button"
                aria-label="清除搜索内容"
                onClick={() => {
                  setQuery("");
                  closeSearchKeyboard();
                }}
              >
                <X size={17} aria-hidden="true" />
              </button>
            ) : null}
          </div>
        </div>

        <section className="community-catalog-section community-category-section" aria-label="课程分类">
          <div className="community-category-rail">
            <div
              ref={categoryListRef}
              className={`community-category-list${categoryDragging ? " is-dragging" : ""}`}
              role="group"
              aria-label="按学科筛选书籍"
              onClickCapture={preventCategoryClickAfterDrag}
              onPointerDown={startCategoryDrag}
              onPointerMove={moveCategoryDrag}
              onPointerUp={finishCategoryDrag}
              onPointerCancel={cancelCategoryDrag}
              onLostPointerCapture={cancelCategoryDrag}
            >
              {communityCategories.map((category) => (
                <button
                  className="community-category-button"
                  type="button"
                  key={category}
                  aria-pressed={!normalizedQuery && selectedCategory === category}
                  onClick={() => selectCategory(category)}
                >
                  <span>{category}</span>
                </button>
              ))}
            </div>
            <button
              className="community-category-more"
              type="button"
              aria-label={categoryMenuOpen ? "收起全部分类" : "查看更多分类"}
              aria-expanded={categoryMenuOpen}
              aria-controls={categoryMenuId}
              onClick={() => setCategoryMenuOpen((open) => !open)}
            >
              <ChevronDown size={21} aria-hidden="true" />
            </button>
          </div>
          {categoryMenuOpen ? (
            <div className="community-category-menu" id={categoryMenuId} role="group" aria-label="全部课程分类">
              {communityCategories.map((category) => (
                <button
                  className="community-category-menu-button"
                  type="button"
                  key={category}
                  aria-pressed={!normalizedQuery && selectedCategory === category}
                  onClick={() => selectCategory(category)}
                >
                  {category}
                </button>
              ))}
            </div>
          ) : null}
        </section>
      </div>

      <section
        className="community-catalog-section community-popular-section"
        aria-label="社区课程"
        aria-describedby="community-result-summary"
      >
        <p className="community-result-summary" id="community-result-summary" aria-live="polite">
          {resultSummary}
        </p>

        {visibleBooks.length ? (
          <div className="community-grid">
            {visibleBooks.map((book) => (
              <button
                className="community-book-card"
                type="button"
                key={book.id}
                aria-label={`进入课程：${book.catalogTitle}`}
                data-community-book-id={book.id}
                data-community-subject={book.subject}
                onClick={() => openCommunityBook(book.id)}
              >
                <CommunityCover source={book.cover} title={book.title} variant="tile" />
                <span className="community-book-copy">
                  <strong>{book.catalogTitle}</strong>
                  <span className="community-book-bottom">
                    <span className="community-book-meta">
                      <small>{book.grade} · {book.version}</small>
                      <span>{book.learners} 人学习</span>
                    </span>
                    <span className="community-book-enter" aria-hidden="true">进入</span>
                  </span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="community-empty-state">
            <SearchX size={24} aria-hidden="true" />
            <div role="status">
              <h3>没有找到匹配书籍</h3>
              <p>换一个关键词，或返回推荐分类继续看看。</p>
            </div>
            <button type="button" onClick={resetDiscovery}>查看推荐</button>
          </div>
        )}
      </section>

      {searchKeyboardOpen && searchInputRef.current?.closest(".app-shell")
        ? createPortal(
            <CommunitySearchKeyboard
              onBackspace={deleteSearchText}
              onClose={closeSearchKeyboard}
              onInsert={insertSearchText}
              onSuggestion={useSearchSuggestion}
            />,
            searchInputRef.current.closest(".app-shell") as HTMLElement
          )
        : null}
    </div>
  );
}
