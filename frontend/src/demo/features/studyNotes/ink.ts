import type { InkPoint, InkStroke } from "./types";

export const penBrushWidths = [0.004, 0.006, 0.01] as const;
export const highlighterBrushWidths = [0.01, 0.02, 0.03] as const;

export function clampUnit(value: number) {
  return Math.min(1, Math.max(0, value));
}

export function containImageRect(viewportWidth: number, viewportHeight: number, imageWidth: number, imageHeight: number) {
  if (viewportWidth <= 0 || viewportHeight <= 0 || imageWidth <= 0 || imageHeight <= 0) {
    return { left: 0, top: 0, width: Math.max(0, viewportWidth), height: Math.max(0, viewportHeight) };
  }
  const fit = Math.min(viewportWidth / imageWidth, viewportHeight / imageHeight);
  const width = imageWidth * fit;
  const height = imageHeight * fit;
  return {
    left: (viewportWidth - width) / 2,
    top: (viewportHeight - height) / 2,
    width,
    height
  };
}

export function normalizedInkPoint(
  clientX: number,
  clientY: number,
  bounds: Pick<DOMRect, "left" | "top" | "width" | "height">,
  pressure = 0.5,
  timestamp = Date.now()
): InkPoint {
  return {
    x: clampUnit((clientX - bounds.left) / Math.max(bounds.width, 1)),
    y: clampUnit((clientY - bounds.top) / Math.max(bounds.height, 1)),
    pressure: clampUnit(pressure || 0.5),
    t: timestamp
  };
}

function distanceToSegment(point: InkPoint, start: InkPoint, end: InkPoint) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (dx === 0 && dy === 0) return Math.hypot(point.x - start.x, point.y - start.y);
  const projection = Math.min(1, Math.max(0, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(point.x - (start.x + projection * dx), point.y - (start.y + projection * dy));
}

export function strokeHitTest(stroke: InkStroke, point: InkPoint, tolerance = 0.018) {
  if (stroke.points.length === 1) {
    return Math.hypot(stroke.points[0].x - point.x, stroke.points[0].y - point.y) <= tolerance;
  }
  return stroke.points.some((candidate, index) => (
    index > 0 && distanceToSegment(point, stroke.points[index - 1], candidate) <= tolerance
  ));
}

export function drawInkStrokes(
  context: CanvasRenderingContext2D,
  strokes: readonly InkStroke[],
  width: number,
  height: number
) {
  context.clearRect(0, 0, width, height);
  context.lineCap = "round";
  context.lineJoin = "round";

  for (const stroke of strokes) {
    if (stroke.points.length === 0) continue;
    context.save();
    context.globalAlpha = stroke.opacity;
    context.strokeStyle = stroke.color;
    context.fillStyle = stroke.color;
    context.lineCap = stroke.tool === "highlighter" ? "square" : "round";
    context.lineWidth = stroke.width * Math.min(width, height);
    if (stroke.points.length === 1) {
      const point = stroke.points[0];
      if (stroke.tool === "highlighter") {
        context.fillRect(point.x * width - context.lineWidth / 2, point.y * height - context.lineWidth / 2, context.lineWidth, context.lineWidth);
      } else {
        context.beginPath();
        context.arc(point.x * width, point.y * height, context.lineWidth / 2, 0, Math.PI * 2);
        context.fill();
      }
    } else {
      context.beginPath();
      stroke.points.forEach((point, index) => {
        const x = point.x * width;
        const y = point.y * height;
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
    }
    context.restore();
  }
}
