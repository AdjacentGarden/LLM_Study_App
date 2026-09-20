"""Source-reviewed relationship cards, not fabricated molecular/physical drawings.

Only bounded plain text enters this renderer. No model code or SVG is executed.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .studio_models import TeachingPlan


def render_diagram(plan: TeachingPlan) -> bytes:
    candidates = [
        os.getenv("STUDIO_CJK_FONT", ""),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]
    path = next((p for p in candidates if p and Path(p).is_file()), None)
    if not path or not plan.diagram_facts:
        raise ValueError("Chinese font and diagram facts are required")
    canvas = Image.new("RGB", (1152, 864), "#f6f5fb")
    draw = ImageDraw.Draw(canvas)

    def label(
        text: str, box: tuple[int, int, int, int], size: int, color: str, centered: bool = True
    ) -> None:
        left, top, right, bottom = box
        for pixels in range(size, 15, -1):
            font = ImageFont.truetype(path, pixels)
            lines: list[str] = []
            current = ""
            for char in text:
                if char == "\n" or (
                    current and draw.textlength(current + char, font=font) > right - left
                ):
                    lines.append(current)
                    current = "" if char == "\n" else char
                else:
                    current += char
            if current:
                lines.append(current)
            line_height = pixels + 12
            if len(lines) * line_height <= bottom - top:
                y = top + ((bottom - top - len(lines) * line_height) / 2 if centered else 0)
                for line in lines:
                    x = left + (
                        (right - left - draw.textlength(line, font=font)) / 2 if centered else 0
                    )
                    draw.text((x, y), line, font=font, fill=color, anchor="lt")
                    y += line_height
                return
        raise ValueError("diagram label cannot fit; never silently truncate")

    draw.rounded_rectangle((34, 26, 1118, 838), radius=32, fill="#ffffff")
    label("读懂关系", (66, 49, 1086, 88), 24, "#795bc0", False)
    label(plan.title, (66, 96, 1086, 192), 40, "#293047", False)
    count = len(plan.diagram_facts)
    row_height = min(170, 560 // count)
    start = 208 + (560 - row_height * count) // 2
    for index, fact in enumerate(plan.diagram_facts):
        y = start + index * row_height
        bottom = y + row_height - 20
        draw.rounded_rectangle((66, y, 420, bottom), radius=22, fill="#f0eafb")
        draw.rounded_rectangle((732, y, 1086, bottom), radius=22, fill="#e9f5f0")
        label(fact.subject, (84, y + 14, 402, bottom - 14), 31, "#45376c")
        label(fact.object, (750, y + 14, 1068, bottom - 14), 31, "#235d52")
        middle = (y + bottom) // 2
        label(fact.relation, (446, y + 3, 706, middle + 10), 25, "#58627b")
        arrow_y = middle + 22
        draw.line((460, arrow_y, 694, arrow_y), fill="#9584bd", width=4)
        draw.polygon([(704, arrow_y), (686, arrow_y - 9), (686, arrow_y + 9)], fill="#9584bd")
    label("这是知识关系示意，不是实物结构图。", (66, 784, 1086, 821), 21, "#778096")
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=93)
    return out.getvalue()
