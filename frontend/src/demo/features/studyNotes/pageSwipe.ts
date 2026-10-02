export type PageSwipeDirection = -1 | 1;

/** A horizontal page turn must be deliberate, not a short text-selection drag. */
export function pageSwipeDirection(
  deltaX: number,
  deltaY: number,
  viewportWidth: number
): PageSwipeDirection | null {
  const minimumDistance = Math.max(48, Math.min(96, viewportWidth * 0.11));
  if (Math.abs(deltaX) < minimumDistance || Math.abs(deltaX) < Math.abs(deltaY) * 1.25) return null;
  return deltaX < 0 ? 1 : -1;
}
