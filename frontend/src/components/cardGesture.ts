export function swipeDirection(dx: number, dy: number): -1 | 0 | 1 {
  if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return 0;
  return dx < 0 ? 1 : -1;
}
export function nextCardIndex(index: number, direction: number, total: number) {
  return Math.max(0, Math.min(Math.max(0, total - 1), index + direction));
}
