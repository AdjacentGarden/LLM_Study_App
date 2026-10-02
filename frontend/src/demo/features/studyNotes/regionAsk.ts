/**
 * Geometry for the reader's AI region ask.
 *
 * The pen draws a rectangle over the fitted textbook page and the pixels inside
 * it become the AI reference. Every value here lives in the same normalized
 * page space the ink strokes use (0–1 from the page image's top-left), so a
 * selection survives zoom, resize, page turns and device-pixel-ratio changes.
 */

export type RegionPoint = {
  x: number;
  y: number;
};

export type PageRegion = {
  x: number;
  y: number;
  width: number;
  height: number;
};

/**
 * A rectangle thinner than this share of either page edge carries too little of
 * the textbook to be worth sending, and is far more likely to be a stray tap.
 */
export const MIN_REGION_EDGE = 0.06;

const REGION_POSITION_LABELS = [
  "左上",
  "上中",
  "右上",
  "左中",
  "中部",
  "右中",
  "左下",
  "下中",
  "右下"
] as const;

function clampUnit(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** Orders two drag points into a clamped rectangle; either drag direction works. */
export function normalizeRegion(start: RegionPoint, end: RegionPoint): PageRegion {
  const left = clampUnit(Math.min(start.x, end.x));
  const top = clampUnit(Math.min(start.y, end.y));
  const right = clampUnit(Math.max(start.x, end.x));
  const bottom = clampUnit(Math.max(start.y, end.y));
  return { x: left, y: top, width: right - left, height: bottom - top };
}

export function isRegionUsable(region: PageRegion, minimumEdge = MIN_REGION_EDGE) {
  return region.width >= minimumEdge && region.height >= minimumEdge;
}

/** Maps a normalized region onto the source image's natural pixels. */
export function regionCropBox(region: PageRegion, naturalWidth: number, naturalHeight: number) {
  if (!(naturalWidth > 0) || !(naturalHeight > 0)) return null;
  const left = Math.round(clampUnit(region.x) * naturalWidth);
  const top = Math.round(clampUnit(region.y) * naturalHeight);
  const right = Math.round(clampUnit(region.x + region.width) * naturalWidth);
  const bottom = Math.round(clampUnit(region.y + region.height) * naturalHeight);
  const width = Math.min(naturalWidth, Math.max(1, right - left));
  const height = Math.min(naturalHeight, Math.max(1, bottom - top));
  return {
    sx: Math.max(0, Math.min(left, naturalWidth - width)),
    sy: Math.max(0, Math.min(top, naturalHeight - height)),
    width,
    height
  };
}

/** Nine-cell name for where the rectangle sits on the page. */
export function regionPositionLabel(region: PageRegion) {
  const column = Math.min(2, Math.floor(clampUnit(region.x + region.width / 2) * 3));
  const row = Math.min(2, Math.floor(clampUnit(region.y + region.height / 2) * 3));
  return REGION_POSITION_LABELS[row * 3 + column];
}

export function regionPositionRow(region: PageRegion) {
  return Math.min(2, Math.floor(clampUnit(region.y + region.height / 2) * 3));
}

/** Human label carried by the request, the panel and the citation-free answer. */
export function describeRegion(region: PageRegion, pageLabel?: string | null) {
  const width = Math.round(region.width * 100);
  const height = Math.round(region.height * 100);
  const area = `${regionPositionLabel(region)}区域 · 约占页面 ${width}% × ${height}%`;
  const label = pageLabel?.trim();
  return label ? `${label} · ${area}` : area;
}

/** Short phrase the model receives as its page hint. */
export function regionContextLabel(region: PageRegion, pageLabel?: string | null) {
  const label = pageLabel?.trim();
  const area = `${regionPositionLabel(region)}圈选区域`;
  return label ? `${label} ${area}` : area;
}
