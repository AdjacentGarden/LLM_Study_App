from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.schemas.books import LayoutRegion


class LayoutAdapter(Protocol):
    name: str

    def detect_regions(self, image_path: Path, page: int) -> list[LayoutRegion]:
        ...


class OpenCVLayoutAdapter:
    name = "opencv_layout"

    def detect_regions(self, image_path: Path, page: int) -> list[LayoutRegion]:
        import cv2  # type: ignore

        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return []
        height, width = image.shape[:2]
        _, binary = cv2.threshold(image, 245, 255, cv2.THRESH_BINARY_INV)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions: list[LayoutRegion] = []
        min_area = max(120, int(width * height * 0.004))
        for index, contour in enumerate(contours):
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < min_area:
                continue
            region_type = "figure" if area > width * height * 0.08 else "text"
            regions.append(
                LayoutRegion(
                    region_id=f"p{page}_layout_{index + 1:03d}",
                    page=page,
                    type=region_type,
                    bbox=[float(x), float(y), float(x + w), float(y + h)],
                    confidence=0.55,
                    source=self.name,
                )
            )
        if not regions:
            regions.append(
                LayoutRegion(
                    region_id=f"p{page}_layout_full_page",
                    page=page,
                    type="text",
                    bbox=[0.0, 0.0, float(width), float(height)],
                    confidence=0.2,
                    source=self.name,
                )
            )
        return sorted(regions, key=lambda item: (item.bbox[1], item.bbox[0]))


class SuryaLayoutAdapter:
    name = "surya_layout"

    def __init__(self, fallback: LayoutAdapter | None = None) -> None:
        self.fallback = fallback or OpenCVLayoutAdapter()

    def detect_regions(self, image_path: Path, page: int) -> list[LayoutRegion]:
        try:
            from surya.layout import LayoutPredictor
            from PIL import Image
        except Exception:
            return self.fallback.detect_regions(image_path, page)

        try:
            predictor = LayoutPredictor()
            image = Image.open(image_path).convert("RGB")
            predictions = predictor([image])
        except Exception:
            return self.fallback.detect_regions(image_path, page)

        regions: list[LayoutRegion] = []
        for index, region in enumerate(_iter_surya_regions(predictions), start=1):
            bbox = getattr(region, "bbox", None) or getattr(region, "polygon", None)
            if not bbox:
                continue
            normalized_bbox = _surya_bbox_to_xyxy(bbox)
            label = str(getattr(region, "label", "text")).lower()
            confidence = float(getattr(region, "confidence", 0.7) or 0.7)
            regions.append(
                LayoutRegion(
                    region_id=f"p{page}_surya_{index:03d}",
                    page=page,
                    type=label,
                    bbox=normalized_bbox,
                    confidence=confidence,
                    source=self.name,
                )
            )
        return sorted(regions, key=lambda item: (item.bbox[1], item.bbox[0])) or self.fallback.detect_regions(image_path, page)


class LayoutService:
    def __init__(self, adapter: LayoutAdapter | None = None) -> None:
        self.adapter = adapter or OpenCVLayoutAdapter()

    def detect_regions(self, image_path: Path, page: int) -> list[LayoutRegion]:
        try:
            return self.adapter.detect_regions(image_path, page)
        except Exception:
            return []


def get_layout_service() -> LayoutService:
    settings = get_settings()
    if settings.layout_provider == "surya":
        return LayoutService(SuryaLayoutAdapter())
    return LayoutService()


def _iter_surya_regions(predictions):
    for page_prediction in predictions or []:
        if isinstance(page_prediction, dict):
            for key in ("bboxes", "boxes", "layout"):
                for region in page_prediction.get(key, []) or []:
                    yield region
        else:
            for attr in ("bboxes", "boxes", "layout"):
                for region in getattr(page_prediction, attr, []) or []:
                    yield region


def _surya_bbox_to_xyxy(bbox) -> list[float]:
    if len(bbox) == 4 and all(isinstance(value, (int, float)) for value in bbox):
        return [float(value) for value in bbox]
    points = bbox or []
    xs = [float(point[0]) for point in points if len(point) >= 2]
    ys = [float(point[1]) for point in points if len(point) >= 2]
    if not xs or not ys:
        return [0.0, 0.0, 0.0, 0.0]
    return [min(xs), min(ys), max(xs), max(ys)]
