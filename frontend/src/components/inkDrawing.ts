import type { InkStroke } from "../api/learningStudio";

/** Render vector strokes in the same normalized coordinate space on any PDF aspect ratio. */
export function drawInk(
  canvas: HTMLCanvasElement | null,
  strokes: readonly InkStroke[],
  active?: InkStroke | null,
): void {
  const ctx = canvas?.getContext("2d");
  if (!ctx || !canvas) return;
  const scaleX = canvas.width / 1000;
  const scaleY = canvas.height / 1400;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (const stroke of [...strokes, ...(active ? [active] : [])]) {
    ctx.strokeStyle = stroke.color;
    ctx.fillStyle = stroke.color;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    stroke.points.forEach((point, index) => {
      const width = Math.max(2, stroke.width * (0.6 + point.p * 0.8)) * scaleX;
      const x = point.x * scaleX;
      const y = point.y * scaleY;
      if (index) {
        const previous = stroke.points[index - 1];
        ctx.lineWidth = width;
        ctx.beginPath();
        ctx.moveTo(previous.x * scaleX, previous.y * scaleY);
        ctx.lineTo(x, y);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.arc(x, y, width / 2, 0, Math.PI * 2);
        ctx.fill();
      }
    });
  }
}
