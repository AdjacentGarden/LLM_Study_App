// iPhone 16: 1179 × 2556 physical pixels, represented at 3× logical density.
export const IPHONE_16 = {
  id: "iphone-16",
  label: "iPhone 16",
  width: 393,
  height: 852,
  bezel: 8,
} as const;

// iPhone 17: 1206 × 2622 physical pixels, represented at 3× logical density.
export const IPHONE_17 = {
  id: "iphone-17",
  label: "iPhone 17",
  width: 402,
  height: 874,
  bezel: 8,
} as const;

export type PreviewDevice = typeof IPHONE_16 | typeof IPHONE_17;

export function previewDevice(value: string | null): PreviewDevice {
  return value === IPHONE_17.id ? IPHONE_17 : IPHONE_16;
}

export function previewScale(
  width: number,
  height: number,
  device: PreviewDevice = IPHONE_16,
): number {
  return Math.max(.05, Math.min(1,
    (width - 32) / (device.width + 2 * device.bezel),
    (height - 80) / (device.height + 2 * device.bezel)));
}
