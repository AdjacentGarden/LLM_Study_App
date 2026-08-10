const swipeThreshold = 56;
const maximumDragOffset = 148;

export function isHorizontalFlashcardGesture(deltaX: number, deltaY: number) {
  return Math.abs(deltaX) > 8 && Math.abs(deltaX) > Math.abs(deltaY);
}

export function clampFlashcardDrag(deltaX: number) {
  return Math.max(-maximumDragOffset, Math.min(maximumDragOffset, deltaX));
}

export function shouldAdvanceFlashcardSwipe(deltaX: number, deltaY: number, cardCount: number) {
  return cardCount > 1
    && deltaX <= -swipeThreshold
    && Math.abs(deltaX) > Math.abs(deltaY) * 1.2;
}
