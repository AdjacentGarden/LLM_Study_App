import { describe, expect, it } from "vitest";
import {
  calculateFitScale,
  buildPreviewSearch,
  getLogicalViewport,
  getOutputGeometry,
  getScaledOutputSize,
  parsePreviewSettings
} from "./devicePreview";

describe("device preview geometry", () => {
  it.each([
    ["iphone-17-pro", "portrait", 402, 874],
    ["iphone-17-pro", "landscape", 874, 402],
    ["ipad-pro-11", "portrait", 834, 1194],
    ["ipad-pro-11", "landscape", 1194, 834]
  ] as const)("returns the %s %s logical viewport", (device, orientation, width, height) => {
    expect(getLogicalViewport(device, orientation)).toEqual({ width, height });
  });

  it.each([
    ["iphone-17-pro", 402, 874, 804, 1748, 1206, 2622],
    ["ipad-pro-11", 834, 1194, 1668, 2388, 2502, 3582]
  ] as const)("calculates HD and Retina output sizes for %s", (device, width, height, hdWidth, hdHeight, retinaWidth, retinaHeight) => {
    const logical = { width, height };
    expect(getScaledOutputSize(logical, "hd-2x")).toEqual({ width: hdWidth, height: hdHeight });
    expect(getScaledOutputSize(logical, "retina-3x")).toEqual({ width: retinaWidth, height: retinaHeight });
  });

  it("keeps HD and Retina geometry tied to the rotated logical viewport", () => {
    expect(getOutputGeometry("iphone-17-pro", "landscape", "hd-2x")).toMatchObject({
      canvasWidth: 1748,
      canvasHeight: 804,
      logicalWidth: 874,
      logicalHeight: 402
    });
    expect(getOutputGeometry("ipad-pro-11", "landscape", "retina-3x")).toMatchObject({
      canvasWidth: 3582,
      canvasHeight: 2502,
      logicalWidth: 1194,
      logicalHeight: 834
    });
  });

  it("keeps the 4K canvas fixed and centers the portrait phone", () => {
    const geometry = getOutputGeometry("iphone-17-pro", "portrait", "4k");
    expect(geometry.canvasWidth).toBe(2160);
    expect(geometry.canvasHeight).toBe(3840);
    expect(geometry.contentScale).toBeCloseTo(Math.min(2160 / 402, 3840 / 874));
    expect(geometry.contentOffsetX).toBeGreaterThan(0);
    expect(geometry.contentOffsetY).toBe(0);
  });

  it("uses the landscape 4K canvas without stretching content", () => {
    const geometry = getOutputGeometry("ipad-pro-11", "landscape", "4k");
    expect(geometry.canvasWidth).toBe(3840);
    expect(geometry.canvasHeight).toBe(2160);
    expect(geometry.contentScale).toBeCloseTo(Math.min(3840 / 1194, 2160 / 834));
    expect(geometry.contentOffsetX).toBeGreaterThan(0);
    expect(geometry.contentOffsetY).toBe(0);
  });

  it("caps fit scale at 1 and handles narrow, short, and zero spaces", () => {
    expect(calculateFitScale(1200, 1200, 402, 874)).toBe(1);
    expect(calculateFitScale(201, 874, 402, 874)).toBeCloseTo(0.5);
    expect(calculateFitScale(402, 437, 402, 874)).toBeCloseTo(0.5);
    expect(calculateFitScale(0, 437, 402, 874)).toBe(0);
    expect(calculateFitScale(Number.NaN, Number.POSITIVE_INFINITY, 402, 874)).toBe(0);
  });

  it("parses valid preview parameters and falls back safely", () => {
    expect(parsePreviewSettings("?device=ipad-pro-11&orientation=landscape&quality=retina-3x&chrome=0")).toEqual({
      device: "ipad-pro-11",
      orientation: "landscape",
      quality: "retina-3x",
      chrome: false
    });
    expect(parsePreviewSettings("?device=unknown&orientation=sideways&quality=wat&chrome=invalid")).toEqual({
      device: "iphone-17-pro",
      orientation: "portrait",
      quality: "fit",
      chrome: true
    });
  });

  it("builds an explicit workbench link while retaining unrelated parameters", () => {
    expect(buildPreviewSearch({
      device: "ipad-pro-11",
      orientation: "landscape",
      quality: "4k",
      chrome: false
    }, "?preview=devices&source=lesson-2")).toBe(
      "?source=lesson-2&device=ipad-pro-11&orientation=landscape&quality=4k&chrome=0&preview=device-preview"
    );
  });
});
