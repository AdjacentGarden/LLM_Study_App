export type DeviceId = "iphone-17-pro" | "ipad-pro-11";
export type Orientation = "portrait" | "landscape";
export type QualityId = "fit" | "hd-2x" | "retina-3x" | "4k";
export type DeviceLayout = "phone" | "pad";

export type LogicalViewport = Readonly<{
  width: number;
  height: number;
}>;

export type DevicePreset = Readonly<{
  id: DeviceId;
  label: string;
  layout: DeviceLayout;
  portrait: LogicalViewport;
}>;

export type OutputGeometry = Readonly<{
  canvasHeight: number;
  canvasWidth: number;
  contentOffsetX: number;
  contentOffsetY: number;
  contentScale: number;
  logicalHeight: number;
  logicalWidth: number;
  quality: QualityId;
}>;

export type PreviewSettings = Readonly<{
  chrome: boolean;
  device: DeviceId;
  orientation: Orientation;
  quality: QualityId;
}>;

export const EMBEDDED_PREVIEW_SRC = "/?embedded=device-preview" as const;
export const DEFAULT_PREVIEW_SETTINGS: PreviewSettings = {
  chrome: true,
  device: "iphone-17-pro",
  orientation: "portrait",
  quality: "fit"
};

export const DEVICE_PRESETS: Readonly<Record<DeviceId, DevicePreset>> = {
  "iphone-17-pro": {
    id: "iphone-17-pro",
    label: "iPhone 17 Pro",
    layout: "phone",
    portrait: { width: 402, height: 874 }
  },
  "ipad-pro-11": {
    id: "ipad-pro-11",
    label: "iPad Pro 11\u2033",
    layout: "pad",
    portrait: { width: 834, height: 1194 }
  }
} as const;

export const QUALITY_LABELS: Readonly<Record<QualityId, string>> = {
  fit: "Fit",
  "hd-2x": "HD 2x",
  "retina-3x": "Retina 3x",
  "4k": "4K"
} as const;

export const QUALITY_MULTIPLIERS: Readonly<Record<Exclude<QualityId, "fit" | "4k">, number>> = {
  "hd-2x": 2,
  "retina-3x": 3
} as const;

export const FOUR_K_CANVAS: Readonly<Record<Orientation, LogicalViewport>> = {
  portrait: { width: 2160, height: 3840 },
  landscape: { width: 3840, height: 2160 }
} as const;

const DEVICE_IDS = Object.keys(DEVICE_PRESETS) as DeviceId[];
const ORIENTATIONS: readonly Orientation[] = ["portrait", "landscape"];
const QUALITY_IDS: readonly QualityId[] = ["fit", "hd-2x", "retina-3x", "4k"];

function isDeviceId(value: string | null): value is DeviceId {
  return value !== null && DEVICE_IDS.includes(value as DeviceId);
}

function isOrientation(value: string | null): value is Orientation {
  return value !== null && ORIENTATIONS.includes(value as Orientation);
}

function isQualityId(value: string | null): value is QualityId {
  return value !== null && QUALITY_IDS.includes(value as QualityId);
}

function finiteNonNegative(value: number) {
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

export function getLogicalViewport(device: DeviceId, orientation: Orientation): LogicalViewport {
  const portrait = DEVICE_PRESETS[device].portrait;
  return orientation === "portrait"
    ? { ...portrait }
    : { width: portrait.height, height: portrait.width };
}

export function getDeviceLayout(device: DeviceId): DeviceLayout {
  return DEVICE_PRESETS[device].layout;
}

export function getScaledOutputSize(
  logical: LogicalViewport,
  multiplier: Exclude<QualityId, "fit" | "4k">
): LogicalViewport {
  const scale = QUALITY_MULTIPLIERS[multiplier];
  return {
    width: logical.width * scale,
    height: logical.height * scale
  };
}

export function calculateFitScale(
  availableWidth: number,
  availableHeight: number,
  logicalWidth: number,
  logicalHeight: number
) {
  if (logicalWidth <= 0 || logicalHeight <= 0) return 0;
  const widthScale = finiteNonNegative(availableWidth) / logicalWidth;
  const heightScale = finiteNonNegative(availableHeight) / logicalHeight;
  return Math.min(1, widthScale, heightScale);
}

export function getOutputGeometry(
  device: DeviceId,
  orientation: Orientation,
  quality: QualityId,
  fitScale = 1
): OutputGeometry {
  const logical = getLogicalViewport(device, orientation);

  if (quality === "4k") {
    const canvas = FOUR_K_CANVAS[orientation];
    const contentScale = Math.min(canvas.width / logical.width, canvas.height / logical.height);
    return {
      canvasHeight: canvas.height,
      canvasWidth: canvas.width,
      contentOffsetX: Math.round((canvas.width - logical.width * contentScale) / 2),
      contentOffsetY: Math.round((canvas.height - logical.height * contentScale) / 2),
      contentScale,
      logicalHeight: logical.height,
      logicalWidth: logical.width,
      quality
    };
  }

  const contentScale = quality === "fit"
    ? Math.max(0, Math.min(1, Number.isFinite(fitScale) ? fitScale : 0))
    : QUALITY_MULTIPLIERS[quality];
  return {
    canvasHeight: Math.round(logical.height * contentScale),
    canvasWidth: Math.round(logical.width * contentScale),
    contentOffsetX: 0,
    contentOffsetY: 0,
    contentScale,
    logicalHeight: logical.height,
    logicalWidth: logical.width,
    quality
  };
}

export function parsePreviewSettings(search: string): PreviewSettings {
  const params = new URLSearchParams(search);
  const device = params.get("device");
  const orientation = params.get("orientation");
  const quality = params.get("quality");
  return {
    chrome: params.get("chrome") !== "0",
    device: isDeviceId(device) ? device : DEFAULT_PREVIEW_SETTINGS.device,
    orientation: isOrientation(orientation)
      ? orientation
      : DEFAULT_PREVIEW_SETTINGS.orientation,
    quality: isQualityId(quality) ? quality : DEFAULT_PREVIEW_SETTINGS.quality
  };
}

export function buildPreviewSearch(settings: PreviewSettings, currentSearch = "") {
  const params = new URLSearchParams(currentSearch);
  params.delete("preview");
  params.set("device", settings.device);
  params.set("orientation", settings.orientation);
  params.set("quality", settings.quality);
  params.set("chrome", settings.chrome ? "1" : "0");
  // The production root renders the actual app. Keep the preview route
  // explicit whenever the workbench updates its recording parameters.
  params.set("preview", "device-preview");
  params.delete("embedded");
  return `?${params.toString()}`;
}
