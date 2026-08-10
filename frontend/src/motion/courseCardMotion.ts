export const maxAnimatedCourseCards = 5;
export const courseCardStaggerStepMs = 30;
export const maxCourseCardStaggerDelayMs = (maxAnimatedCourseCards - 1) * courseCardStaggerStepMs;

export function shouldAnimateCourseCard(index: number) {
  return Number.isInteger(index) && index >= 0 && index < maxAnimatedCourseCards;
}

export function clampCourseCardMotionIndex(index: number) {
  if (!Number.isFinite(index)) return 0;
  return Math.min(Math.max(Math.trunc(index), 0), maxAnimatedCourseCards - 1);
}

export function getCourseCardStaggerDelayMs(index: number) {
  return clampCourseCardMotionIndex(index) * courseCardStaggerStepMs;
}
