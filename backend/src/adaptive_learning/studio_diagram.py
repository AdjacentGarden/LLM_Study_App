"""Source-reviewed relationship cards, not fabricated molecular/physical drawings.

Only bounded plain text enters this renderer. No model code or SVG is executed.
"""

from __future__ import annotations

import io
import math
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
    canvas = Image.new("RGB", (1152, 864), "#f7f4ff")
    draw = ImageDraw.Draw(canvas)

    # A calm, colorful editorial background. The semantic content is still made only
    # from reviewed diagram_facts; decorative shapes never introduce new facts.
    top, bottom_color = (248, 245, 255), (239, 249, 247)
    for y in range(864):
        ratio = y / 863
        color = tuple(round(a + (b - a) * ratio) for a, b in zip(top, bottom_color, strict=True))
        draw.line((0, y, 1152, y), fill=color)
    draw.ellipse((-110, -90, 350, 370), fill="#ebe0ff")
    draw.ellipse((920, 600, 1260, 940), fill="#dff6ef")

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

    palettes = [
        ("#6f52b5", "#eee7fc", "#3a2c61", "#279887", "#e1f6f0", "#195f55"),
        ("#2d83bd", "#e4f3fb", "#22536e", "#ef8b52", "#fff0e5", "#8a4928"),
        ("#b85b88", "#fbe7f1", "#6f3652", "#6c9c3d", "#edf7df", "#405f27"),
        ("#7357b8", "#ede8fb", "#45356d", "#d09a2f", "#fff3d5", "#735218"),
    ]

    def icon(text: str, center: tuple[int, int], color: str) -> None:
        """Draw a generic semantic icon without claiming an unverified structure."""
        cx, cy = center
        key = text.lower()
        if any(word in key for word in ("dna", "基因", "染色体", "碱基", "脱氧核糖")):
            points_a, points_b = [], []
            for offset in range(-23, 24, 5):
                wave = math.sin((offset + 23) / 46 * math.pi * 2) * 9
                points_a.append((cx + wave, cy + offset))
                points_b.append((cx - wave, cy + offset))
            draw.line(points_a, fill=color, width=4, joint="curve")
            draw.line(points_b, fill=color, width=4, joint="curve")
            for index in range(0, len(points_a), 2):
                draw.line((points_a[index], points_b[index]), fill=color, width=2)
        elif any(word in key for word in ("细胞", "细胞核", "卵", "精子")):
            draw.ellipse((cx - 24, cy - 22, cx + 24, cy + 22), outline=color, width=4)
            draw.ellipse((cx - 8, cy - 8, cx + 9, cy + 9), fill=color)
            draw.ellipse((cx + 12, cy - 13, cx + 17, cy - 8), fill=color)
        elif any(word in key for word in ("能量", "光", "太阳", "热")):
            draw.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=color)
            for angle in range(0, 360, 45):
                radians = math.radians(angle)
                draw.line(
                    (
                        cx + math.cos(radians) * 17,
                        cy + math.sin(radians) * 17,
                        cx + math.cos(radians) * 29,
                        cy + math.sin(radians) * 29,
                    ),
                    fill=color,
                    width=3,
                )
        elif any(word in key for word in ("酶", "蛋白", "分子", "原料")):
            nodes = [(cx - 19, cy + 10), (cx - 4, cy - 18), (cx + 20, cy - 5), (cx + 13, cy + 21)]
            for first, second in zip(nodes, nodes[1:] + nodes[:1], strict=True):
                draw.line((*first, *second), fill=color, width=3)
            for x, y in nodes:
                draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color)
        else:
            draw.rounded_rectangle(
                (cx - 23, cy - 18, cx + 23, cy + 18), radius=8, outline=color, width=4
            )
            draw.line((cx - 12, cy - 5, cx + 13, cy - 5), fill=color, width=3)
            draw.line((cx - 12, cy + 5, cx + 6, cy + 5), fill=color, width=3)
            draw.ellipse((cx + 16, cy + 12, cx + 25, cy + 21), fill=color)

    draw.rounded_rectangle(
        (30, 24, 1122, 840), radius=42, fill="#ffffff", outline="#e9e1f3", width=2
    )
    draw.rounded_rectangle((66, 52, 118, 104), radius=17, fill="#7655c9")
    draw.ellipse((82, 68, 102, 88), fill="#ffffff")
    draw.ellipse((91, 58, 97, 64), fill="#8ee0d0")
    label("教材知识图谱", (134, 53, 1086, 88), 22, "#7655c9", False)
    label(plan.title, (66, 105, 1086, 186), 40, "#293047", False)
    count = len(plan.diagram_facts)
    row_height = min(170, 545 // count)
    start = 202 + (545 - row_height * count) // 2
    for index, fact in enumerate(plan.diagram_facts):
        y = start + index * row_height
        bottom = y + row_height - 20
        left_accent, left_fill, left_text, right_accent, right_fill, right_text = palettes[index]
        draw.rounded_rectangle((66, y + 5, 426, bottom + 5), radius=28, fill="#ddd5e8")
        draw.rounded_rectangle((66, y, 426, bottom), radius=28, fill=left_fill)
        draw.ellipse((84, (y + bottom) // 2 - 37, 158, (y + bottom) // 2 + 37), fill="#ffffff")
        icon(fact.subject, (121, (y + bottom) // 2), left_accent)
        draw.rounded_rectangle((726, y + 5, 1086, bottom + 5), radius=28, fill="#d7e5e2")
        draw.rounded_rectangle((726, y, 1086, bottom), radius=28, fill=right_fill)
        draw.ellipse((744, (y + bottom) // 2 - 37, 818, (y + bottom) // 2 + 37), fill="#ffffff")
        icon(fact.object, (781, (y + bottom) // 2), right_accent)
        label(fact.subject, (174, y + 14, 406, bottom - 14), 29, left_text)
        label(fact.object, (834, y + 14, 1066, bottom - 14), 29, right_text)
        middle = (y + bottom) // 2
        draw.rounded_rectangle((452, middle - 52, 700, middle + 20), radius=24, fill="#f5f1fa")
        label(fact.relation, (468, middle - 47, 684, middle + 15), 23, "#58627b")
        arrow_y = middle + 34
        draw.line((466, arrow_y, 690, arrow_y), fill="#8467bd", width=6)
        draw.ellipse((459, arrow_y - 5, 469, arrow_y + 5), fill="#8467bd")
        draw.polygon([(704, arrow_y), (685, arrow_y - 11), (685, arrow_y + 11)], fill="#8467bd")
    draw.rounded_rectangle((66, 782, 1086, 824), radius=18, fill="#f5f7fb")
    label(
        "彩色图标帮助区分概念；关系来自教材，不代表真实大小或结构。",
        (88, 787, 1064, 819),
        19,
        "#6f788d",
    )
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=93)
    return out.getvalue()
