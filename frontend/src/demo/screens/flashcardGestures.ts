const swipeThreshold = 56;
const maximumDragOffset = 148;

export function isHorizontalFlashcardGesture(deltaX: number, deltaY: number) {
  return Math.abs(deltaX) > 8 && Math.abs(deltaX) > Math.abs(deltaY);
}

export function clampFlashcardDrag(deltaX: number) {
  return Math.max(-maximumDragOffset, Math.min(maximumDragOffset, deltaX));
}

export function isLastFlashcard(index: number, cardCount: number) {
  return cardCount > 0 && index >= cardCount - 1;
}

export function nextFlashcardIndex(index: number, cardCount: number) {
  return Math.min(index + 1, Math.max(0, cardCount - 1));
}

export function shouldAdvanceFlashcardSwipe(
  deltaX: number,
  deltaY: number,
  cardCount: number,
  currentIndex = 0
) {
  return cardCount > 1
    && !isLastFlashcard(currentIndex, cardCount)
    && deltaX <= -swipeThreshold
    && Math.abs(deltaX) > Math.abs(deltaY) * 1.2;
}
