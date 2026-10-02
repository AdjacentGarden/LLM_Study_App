import { regionCropBox, type PageRegion } from "./regionAsk";

export type RegionCaptureOptions = {
  /** Longest edge of the reference copy, so one request stays small. */
  maxEdge?: number;
  quality?: number;
};

export type RegionCapture = {
  dataUrl: string;
  width: number;
  height: number;
};

/**
 * Copies the circled part of the textbook page into a data URL.
 *
 * The page image is already decoded in the reader, so the crop reuses those
 * natural pixels instead of re-fetching the asset. A tainted canvas (a page
 * image served from another origin) or a missing 2D context returns null and
 * the caller keeps the reader usable with a toast.
 */
export function captureRegionImage(
  image: Pick<HTMLImageElement, "naturalWidth" | "naturalHeight"> | null | undefined,
  region: PageRegion,
  options: RegionCaptureOptions = {}
): RegionCapture | null {
  if (!image) return null;
  if (typeof document === "undefined") return null;
  const box = regionCropBox(region, image.naturalWidth, image.naturalHeight);
  if (!box) return null;

  const maxEdge = Math.max(64, options.maxEdge ?? 1_280);
  const scale = Math.min(1, maxEdge / Math.max(box.width, box.height));
  const width = Math.max(1, Math.round(box.width * scale));
  const height = Math.max(1, Math.round(box.height * scale));

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.drawImage(
    image as unknown as CanvasImageSource,
    box.sx,
    box.sy,
    box.width,
    box.height,
    0,
    0,
    width,
    height
  );

  try {
    const dataUrl = canvas.toDataURL("image/jpeg", options.quality ?? 0.92);
    return dataUrl.startsWith("data:image/") ? { dataUrl, width, height } : null;
  } catch {
    return null;
  }
}
