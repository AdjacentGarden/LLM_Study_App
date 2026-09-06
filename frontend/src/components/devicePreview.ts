// iPhone 16: 1179 × 2556 physical pixels, represented at 3× logical density.
export const IPHONE_16 = { width: 393, height: 852, bezel: 8 } as const;
export function previewScale(width: number, height: number): number {
  return Math.max(.05, Math.min(1,
    (width - 32) / (IPHONE_16.width + 2 * IPHONE_16.bezel),
    (height - 80) / (IPHONE_16.height + 2 * IPHONE_16.bezel)));
}
