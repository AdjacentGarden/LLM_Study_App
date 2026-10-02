import { describe, expect, it } from "vitest";
import {
  MIN_REGION_EDGE,
  describeRegion,
  isRegionUsable,
  normalizeRegion,
  regionContextLabel,
  regionCropBox,
  regionPositionLabel
} from "./regionAsk";

describe("AI region ask geometry", () => {
  it("orders a drag in any direction into one clamped rectangle", () => {
    expect(normalizeRegion({ x: 0.8, y: 0.6 }, { x: 0.2, y: 0.1 })).toEqual({
      x: 0.2,
      y: 0.1,
      width: expect.closeTo(0.6, 6),
      height: expect.closeTo(0.5, 6)
    });
    expect(normalizeRegion({ x: 0.3, y: 0.4 }, { x: 0.9, y: 1.4 })).toEqual({
      x: 0.3,
      y: 0.4,
      width: expect.closeTo(0.6, 6),
      height: expect.closeTo(0.6, 6)
    });
  });

  it("clamps a drag that leaves the page and rejects stray taps", () => {
    const dragged = normalizeRegion({ x: -0.4, y: -0.2 }, { x: 0.5, y: 0.5 });
    expect(dragged).toMatchObject({ x: 0, y: 0 });
    expect(isRegionUsable(dragged)).toBe(true);

    const tap = normalizeRegion({ x: 0.5, y: 0.5 }, { x: 0.52, y: 0.51 });
    expect(isRegionUsable(tap)).toBe(false);
    expect(isRegionUsable({ x: 0, y: 0, width: MIN_REGION_EDGE, height: MIN_REGION_EDGE })).toBe(true);
  });

  it("maps a selection onto source pixels inside the image bounds", () => {
    const box = regionCropBox({ x: 0.25, y: 0.5, width: 0.5, height: 0.25 }, 1_300, 1_840);
    expect(box).toEqual({ sx: 325, sy: 920, width: 650, height: 460 });

    const clamped = regionCropBox({ x: 0.9, y: 0.9, width: 0.4, height: 0.4 }, 1_000, 1_000);
    expect(clamped).toEqual({ sx: 900, sy: 900, width: 100, height: 100 });
    expect(regionCropBox({ x: 0, y: 0, width: 1, height: 1 }, 0, 1_000)).toBeNull();
  });

  it("names the nine page cells the model and the panel share", () => {
    expect(regionPositionLabel({ x: 0.05, y: 0.05, width: 0.2, height: 0.2 })).toBe("左上");
    expect(regionPositionLabel({ x: 0.4, y: 0.4, width: 0.2, height: 0.2 })).toBe("中部");
    expect(regionPositionLabel({ x: 0.75, y: 0.75, width: 0.2, height: 0.2 })).toBe("右下");
    expect(regionPositionLabel({ x: 0.9, y: 0.1, width: 0.2, height: 0.2 })).toBe("右上");
  });

  it("builds the labels the panel and the request carry", () => {
    const region = { x: 0.1, y: 0.2, width: 0.3, height: 0.2 };
    expect(describeRegion(region, "第 16 页")).toBe("第 16 页 · 左上区域 · 约占页面 30% × 20%");
    expect(describeRegion(region)).toBe("左上区域 · 约占页面 30% × 20%");
    expect(regionContextLabel(region, "第 16 页")).toBe("第 16 页 左上圈选区域");
    expect(regionContextLabel(region, "   ")).toBe("左上圈选区域");
  });
});
