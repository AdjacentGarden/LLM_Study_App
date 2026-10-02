import { describe, expect, it, vi } from "vitest";
import { containImageRect, drawInkStrokes, normalizedInkPoint, strokeHitTest } from "./ink";
import type { InkStroke } from "./types";

describe("normalized ink coordinates", () => {
  it("maps a textbook image to its actual contained rectangle in portrait and landscape", () => {
    const portrait = containImageRect(600, 800, 650, 920);
    const landscape = containImageRect(800, 600, 650, 920);
    expect(portrait.width).toBeCloseTo(565.22, 1);
    expect(portrait.left).toBeCloseTo(17.39, 1);
    expect(landscape.height).toBe(600);
    expect(landscape.left).toBeCloseTo(188.04, 1);
    for (const rectangle of [portrait, landscape]) {
      const center = normalizedInkPoint(rectangle.left + rectangle.width / 2, rectangle.top + rectangle.height / 2, rectangle);
      expect(center.x).toBeCloseTo(0.5);
      expect(center.y).toBeCloseTo(0.5);
    }
  });

  it("stores points relative to the textbook image rather than screen pixels", () => {
    const point = normalizedInkPoint(250, 375, { left: 50, top: 75, width: 400, height: 600 }, 0.7, 123);
    expect(point).toEqual({ x: 0.5, y: 0.5, pressure: 0.7, t: 123 });
  });

  it("clamps input outside the drawable page", () => {
    expect(normalizedInkPoint(-10, 900, { left: 0, top: 0, width: 400, height: 600 }, 1.5, 1))
      .toEqual({ x: 0, y: 1, pressure: 1, t: 1 });
  });
});

describe("whole-stroke eraser hit testing", () => {
  const stroke: InkStroke = {
    id: "stroke-1",
    tool: "pen",
    color: "#7c3aed",
    width: 0.006,
    opacity: 0.92,
    points: [
      { x: 0.1, y: 0.2, pressure: 0.5, t: 1 },
      { x: 0.9, y: 0.2, pressure: 0.5, t: 2 }
    ]
  };

  it("matches a point near any line segment", () => {
    expect(strokeHitTest(stroke, { x: 0.5, y: 0.205, pressure: 0.5, t: 3 })).toBe(true);
  });

  it("does not erase an unrelated stroke", () => {
    expect(strokeHitTest(stroke, { x: 0.5, y: 0.4, pressure: 0.5, t: 3 })).toBe(false);
  });
});

describe("ink rendering", () => {
  function canvasContext() {
    return {
      clearRect: vi.fn(), save: vi.fn(), restore: vi.fn(), beginPath: vi.fn(),
      moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(), fill: vi.fn(), fillRect: vi.fn(),
      lineCap: "butt", lineJoin: "miter", lineWidth: 1, globalAlpha: 1, strokeStyle: "", fillStyle: ""
    };
  }

  const marker: InkStroke = {
    id: "marker", tool: "highlighter", color: "#dc2626", width: .03, opacity: .28,
    points: [{ x: .2, y: .3, pressure: .5, t: 1 }, { x: .8, y: .3, pressure: .5, t: 2 }]
  };

  it("uses flat marker ends at the same size on the live canvas and exported previews, then restores round pen ends", () => {
    const context = canvasContext();
    const rendered: Array<{ cap: string; width: number; opacity: number }> = [];
    context.stroke.mockImplementation(() => rendered.push({ cap: context.lineCap, width: context.lineWidth, opacity: context.globalAlpha }));
    drawInkStrokes(context as unknown as CanvasRenderingContext2D, [marker, { ...marker, id: "pen", tool: "pen", width: .006, opacity: .92 }], 600, 900);
    expect(rendered).toEqual([{ cap: "square", width: 18, opacity: .28 }, { cap: "round", width: 3.6, opacity: .92 }]);
  });

  it("renders a marker tap as a square nib and a pen tap as a round dot", () => {
    const context = canvasContext();
    drawInkStrokes(context as unknown as CanvasRenderingContext2D, [{ ...marker, points: [marker.points[0]] }], 600, 900);
    expect(context.fillRect).toHaveBeenCalledWith(111, 261, 18, 18);
    expect(context.arc).not.toHaveBeenCalled();
    context.fillRect.mockClear();
    drawInkStrokes(context as unknown as CanvasRenderingContext2D, [{ ...marker, tool: "pen", points: [marker.points[0]] }], 600, 900);
    expect(context.arc).toHaveBeenCalledWith(120, 270, 9, 0, Math.PI * 2);
    expect(context.fillRect).not.toHaveBeenCalled();
  });
});
