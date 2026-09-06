from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
import multiprocessing
from pathlib import Path
import threading
from typing import Any, Mapping, Protocol
import atexit
import json

from app.core.config import get_settings
from app.schemas.books import QualityWarning, TextBlock


class OCRUnavailable(RuntimeError):
    """Raised when the configured real OCR provider cannot be used."""

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"OCR provider '{provider}' is unavailable ({reason})")


def _ocr_failure_reason(exc: BaseException, phase: str) -> str:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = " ".join(f"{type(item).__name__}: {item}" for item in chain).lower()
    if "dependencyerror" in text or "requires additional dependencies" in text:
        return "dependency_missing"
    if "out of memory" in text or "memoryerror" in text:
        return "out_of_memory"
    if "download" in text or "connection" in text or "model" in text and "not found" in text:
        return "model_unavailable"
    return f"{phase}_failed"


class OCRAdapter(Protocol):
    name: str

    def recognize(self, image_path: Path, page: int) -> tuple[list[TextBlock], list[QualityWarning]]:
        ...


def _image_size(image_path: Path) -> tuple[int, int]:
    import cv2  # type: ignore

    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return (1000, 1400)
    height, width = image.shape[:2]
    return width, height


class MockOCRAdapter:
    name = "mock"

    def recognize(self, image_path: Path, page: int) -> tuple[list[TextBlock], list[QualityWarning]]:
        settings = get_settings()
        width, height = _image_size(image_path)
        confidence = 0.01
        block = TextBlock(
            block_id=f"p{page}_ocr_mock_001",
            page=page,
            type="ocr_pending",
            text=f"OCR pending for page {page}. Install PaddleOCR or set BOOKCOURSE_OCR_PROVIDER=paddleocr for Chinese OCR.",
            bbox=[0.08 * width, 0.08 * height, 0.92 * width, 0.18 * height],
            confidence=confidence,
            low_confidence=confidence < settings.ocr_low_confidence_threshold,
        )
        warning = QualityWarning(page=page, code="ocr_mock_provider", message="mock OCR provider used; text requires cloud OCR")
        return [block], [warning]


class PaddleOCRAdapter:
    name = "paddleocr"

    def __init__(self) -> None:
        settings = get_settings()
        self._ocr: object | None = None
        self._api_version = 0
        self._worker = None
        self._connection = None
        self._worker_lock = threading.Lock()
        self._request_number = 0
        self._recognition_timeout = float(settings.ocr_recognition_timeout_seconds)
        options = {
            "language": settings.ocr_language,
            "device": settings.ocr_device,
            "enable_mkldnn": settings.ocr_enable_mkldnn,
        }
        if settings.ocr_process_isolation:
            self._start_worker(options, float(settings.ocr_worker_startup_timeout_seconds))
        else:
            self._ocr, self._api_version = _create_paddle_engine(options)

    def _start_worker(self, options: Mapping[str, object], timeout_seconds: float) -> None:
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        worker = context.Process(
            target=_paddle_worker_main,
            args=(child_connection, dict(options)),
            name="cloudpath-paddleocr-worker",
            daemon=True,
        )
        worker.start()
        child_connection.close()
        self._worker = worker
        self._connection = parent_connection
        if not parent_connection.poll(timeout_seconds):
            self.close()
            raise OCRUnavailable(self.name, "initialization_timeout")
        try:
            ready = parent_connection.recv()
        except (EOFError, OSError):
            self.close()
            raise OCRUnavailable(self.name, "initialization_failed") from None
        if not isinstance(ready, Mapping) or ready.get("kind") != "ready" or not ready.get("ok"):
            self.close()
            raise OCRUnavailable(self.name, "initialization_failed")
        atexit.register(self.close)

    def close(self) -> None:
        connection = self._connection
        worker = self._worker
        self._connection = None
        self._worker = None
        if connection is not None:
            try:
                connection.send({"op": "stop"})
            except (BrokenPipeError, EOFError, OSError):
                pass
        if worker is not None:
            worker.join(timeout=2.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2.0)
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _worker_lines(self, image_path: Path) -> list[tuple[list[float], str, float]]:
        connection = self._connection
        worker = self._worker
        if connection is None or worker is None or not worker.is_alive():
            raise OCRUnavailable(self.name, "worker_unavailable")
        with self._worker_lock:
            self._request_number += 1
            request_id = self._request_number
            try:
                connection.send({"op": "recognize", "request_id": request_id, "image_path": str(image_path)})
            except (BrokenPipeError, EOFError, OSError):
                raise OCRUnavailable(self.name, "worker_unavailable") from None
            if not connection.poll(self._recognition_timeout):
                self.close()
                raise OCRUnavailable(self.name, "recognition_timeout")
            try:
                response = connection.recv()
            except (EOFError, OSError):
                raise OCRUnavailable(self.name, "worker_unavailable") from None
        if (
            not isinstance(response, Mapping)
            or response.get("request_id") != request_id
            or not response.get("ok")
        ):
            raise OCRUnavailable(self.name, "recognition_failed")
        lines = response.get("lines")
        if not isinstance(lines, list):
            raise OCRUnavailable(self.name, "recognition_failed")
        parsed: list[tuple[list[float], str, float]] = []
        for line in lines:
            if not isinstance(line, (list, tuple)) or len(line) != 3:
                continue
            bbox, text, confidence = line
            try:
                normalized_bbox = [float(value) for value in bbox]
                normalized_confidence = float(confidence)
            except (TypeError, ValueError):
                continue
            if len(normalized_bbox) == 4 and str(text).strip():
                parsed.append((normalized_bbox, str(text).strip(), normalized_confidence))
        return parsed

    def recognize(self, image_path: Path, page: int) -> tuple[list[TextBlock], list[QualityWarning]]:
        settings = get_settings()
        try:
            if self._connection is not None:
                raw_lines = self._worker_lines(image_path)
            elif self._api_version >= 3 and self._ocr is not None and hasattr(self._ocr, "predict"):
                raw_lines = list(_paddle_v3_lines(self._ocr.predict(str(image_path))))
            else:
                if self._ocr is None:
                    raise OCRUnavailable(self.name, "worker_unavailable")
                result = self._ocr.ocr(str(image_path), cls=True)
                lines = result[0] if result and isinstance(result[0], list) else result
                raw_lines = [parsed for item in (lines or []) if (parsed := _parse_paddle_line(item)) is not None]
        except OCRUnavailable:
            raise
        except Exception:
            raise OCRUnavailable(self.name, "recognition_failed") from None
        blocks: list[TextBlock] = []
        warnings: list[QualityWarning] = []
        for index, (bbox, text, confidence) in enumerate(raw_lines):
            spaced_text = _restore_visual_word_spacing(image_path, bbox, text)
            if spaced_text != text:
                warnings.append(
                    QualityWarning(
                        page=page,
                        code="ocr_visual_word_spacing",
                        message="OCR word spacing was restored from a visible gap in the source line",
                    )
                )
                text = spaced_text
            block = TextBlock(
                block_id=f"p{page}_ocr_{index + 1:03d}",
                page=page,
                type="ocr_text",
                text=text,
                bbox=bbox,
                confidence=confidence,
                low_confidence=confidence < settings.ocr_low_confidence_threshold,
            )
            blocks.append(block)
            if confidence < settings.ocr_low_confidence_threshold:
                warnings.append(QualityWarning(page=page, code="low_ocr_confidence", message="OCR text confidence is low"))
        if not blocks:
            warnings.append(QualityWarning(page=page, code="ocr_empty", message="OCR returned no text blocks"))
        return blocks, warnings


class PaddleOCRVLAdapter:
    """Structured PaddleOCR-VL 1.6 adapter for difficult document pages.

    The complete layout + VLM pipeline is used instead of calling the VLM
    component directly, preserving reading order, tables, formulas and titles.
    Heavy inference stays in a child process so API progress and cancellation
    remain responsive on Windows.
    """

    name = "paddleocr-vl-1.6"

    def __init__(self) -> None:
        settings = get_settings()
        self._ocr: object | None = None
        self._worker = None
        self._connection = None
        self._worker_lock = threading.Lock()
        self._request_number = 0
        self._recognition_timeout = float(settings.ocr_recognition_timeout_seconds)
        options: dict[str, object] = {
            "pipeline_version": settings.ocr_vl_pipeline_version,
            "model": settings.ocr_model,
            "device": settings.ocr_device,
            "backend": settings.ocr_vl_backend,
            "server_url": settings.ocr_vl_server_url,
            "api_key": settings.ocr_vl_api_key or "",
            "max_concurrency": settings.ocr_vl_max_concurrency,
        }
        if settings.ocr_process_isolation:
            self._start_worker(options, float(settings.ocr_worker_startup_timeout_seconds))
        else:
            self._ocr = _create_paddle_vl_engine(options)

    def _start_worker(self, options: Mapping[str, object], timeout_seconds: float) -> None:
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        worker = context.Process(
            target=_paddle_vl_worker_main,
            args=(child_connection, dict(options)),
            name="cloudpath-paddleocr-vl-worker",
            daemon=True,
        )
        worker.start()
        child_connection.close()
        self._worker = worker
        self._connection = parent_connection
        if not parent_connection.poll(timeout_seconds):
            self.close()
            raise OCRUnavailable(self.name, "initialization_timeout")
        try:
            ready = parent_connection.recv()
        except (EOFError, OSError):
            self.close()
            raise OCRUnavailable(self.name, "initialization_failed") from None
        if not isinstance(ready, Mapping) or ready.get("kind") != "ready" or not ready.get("ok"):
            self.close()
            reason = str(ready.get("reason") or "initialization_failed") if isinstance(ready, Mapping) else "initialization_failed"
            raise OCRUnavailable(self.name, reason)
        atexit.register(self.close)

    def close(self) -> None:
        connection = self._connection
        worker = self._worker
        self._connection = None
        self._worker = None
        if connection is not None:
            try:
                connection.send({"op": "stop"})
            except (BrokenPipeError, EOFError, OSError):
                pass
        if worker is not None:
            worker.join(timeout=2.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2.0)
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _worker_blocks(self, image_path: Path) -> list[dict[str, object]]:
        connection = self._connection
        worker = self._worker
        if connection is None or worker is None or not worker.is_alive():
            raise OCRUnavailable(self.name, "worker_unavailable")
        with self._worker_lock:
            self._request_number += 1
            request_id = self._request_number
            try:
                connection.send({"op": "recognize", "request_id": request_id, "image_path": str(image_path)})
            except (BrokenPipeError, EOFError, OSError):
                raise OCRUnavailable(self.name, "worker_unavailable") from None
            if not connection.poll(self._recognition_timeout):
                self.close()
                raise OCRUnavailable(self.name, "recognition_timeout")
            try:
                response = connection.recv()
            except (EOFError, OSError):
                raise OCRUnavailable(self.name, "worker_unavailable") from None
        if (
            not isinstance(response, Mapping)
            or response.get("request_id") != request_id
            or not response.get("ok")
            or not isinstance(response.get("blocks"), list)
        ):
            reason = str(response.get("reason") or "recognition_failed") if isinstance(response, Mapping) else "recognition_failed"
            raise OCRUnavailable(self.name, reason)
        return [dict(block) for block in response["blocks"] if isinstance(block, Mapping)]

    def recognize(self, image_path: Path, page: int) -> tuple[list[TextBlock], list[QualityWarning]]:
        settings = get_settings()
        try:
            raw_blocks = self._worker_blocks(image_path) if self._connection is not None else list(
                _paddle_vl_blocks(self._ocr.predict(str(image_path)) if self._ocr is not None else [])
            )
        except OCRUnavailable:
            raise
        except Exception as exc:
            raise OCRUnavailable(self.name, _ocr_failure_reason(exc, "recognition")) from None

        blocks: list[TextBlock] = []
        for index, item in enumerate(raw_blocks):
            text = str(item.get("text", "")).strip()
            bbox = item.get("bbox")
            if not text or not isinstance(bbox, list) or len(bbox) != 4:
                continue
            raw_confidence = item.get("confidence")
            confidence = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else None
            label = str(item.get("label", "text")).strip().lower() or "text"
            block_type = _paddle_vl_block_type(label)
            blocks.append(
                TextBlock(
                    block_id=f"p{page}_ocr_vl_{index + 1:03d}",
                    page=page,
                    type=block_type,
                    text=text,
                    bbox=[float(value) for value in bbox],
                    confidence=confidence,
                    low_confidence=confidence is not None and confidence < settings.ocr_low_confidence_threshold,
                    heading_level=1 if block_type == "heading" else None,
                    content_format="markdown" if block_type in {"table", "formula"} else "plain_text",
                    metadata={"ocr_layout_label": label, "ocr_model": settings.ocr_model},
                )
            )
        warnings = [] if blocks else [QualityWarning(page=page, code="ocr_empty", message="PaddleOCR-VL returned no semantic blocks")]
        return blocks, warnings


def _create_paddle_engine(options: Mapping[str, object]) -> tuple[object, int]:
    from paddleocr import PaddleOCR  # type: ignore

    # PaddleOCR 3.x is the supported runtime on Python 3.12. Keep a narrow
    # compatibility path for an existing 2.x installation.
    try:
        engine = PaddleOCR(
            lang=str(options["language"]),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device=str(options["device"]),
            enable_mkldnn=bool(options["enable_mkldnn"]),
        )
        return engine, 3
    except TypeError:
        engine = PaddleOCR(
            use_angle_cls=True,
            lang=str(options["language"]),
            show_log=False,
            use_gpu=str(options["device"]).lower().startswith("gpu"),
        )
        return engine, 2


def _create_paddle_vl_engine(options: Mapping[str, object]) -> object:
    from paddleocr import PaddleOCRVL  # type: ignore

    kwargs: dict[str, object] = {
        "pipeline_version": str(options.get("pipeline_version") or "v1.6"),
        "device": str(options.get("device") or "cpu"),
        "use_doc_orientation_classify": True,
        "use_doc_unwarping": True,
        "use_layout_detection": True,
        "use_chart_recognition": True,
        "use_seal_recognition": True,
        "use_ocr_for_image_block": True,
        "format_block_content": True,
        "vl_rec_max_concurrency": int(options.get("max_concurrency") or 4),
    }
    backend = str(options.get("backend") or "").strip()
    server_url = str(options.get("server_url") or "").strip()
    api_key = str(options.get("api_key") or "").strip()
    model = str(options.get("model") or "PaddleOCR-VL-1.6").strip()
    if backend:
        kwargs["vl_rec_backend"] = backend
    if server_url:
        kwargs["vl_rec_server_url"] = server_url
    if model and (backend or server_url):
        kwargs["vl_rec_api_model_name"] = model
    if api_key:
        kwargs["vl_rec_api_key"] = api_key
    return PaddleOCRVL(**kwargs)


def _paddle_vl_block_type(label: str) -> str:
    normalized = label.strip().lower()
    if "title" in normalized or "heading" in normalized:
        return "heading"
    if "table" in normalized:
        return "table"
    if "formula" in normalized or "equation" in normalized:
        return "formula"
    if "image" in normalized or "figure" in normalized or "chart" in normalized:
        return "figure_caption"
    return "ocr_text"


def _paddle_vl_bbox(value: object) -> list[float] | None:
    try:
        raw = list(value)  # type: ignore[arg-type]
    except TypeError:
        return None
    if len(raw) == 4 and all(not hasattr(item, "__iter__") for item in raw):
        try:
            return [float(item) for item in raw]
        except (TypeError, ValueError):
            return None
    return _points_bbox(raw)


def _paddle_vl_blocks(results: object):
    try:
        iterable = list(results)  # generators returned by PaddleX are consumed once
    except TypeError:
        iterable = [results]
    for result in iterable:
        mapping = _result_mapping(result)
        if mapping is None:
            continue
        payload = mapping.get("res") if isinstance(mapping.get("res"), Mapping) else mapping
        items = payload.get("parsing_res_list")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("block_content", "")).strip()
            bbox = _paddle_vl_bbox(item.get("block_bbox"))
            if not text or bbox is None:
                continue
            raw_confidence = item.get("block_score", item.get("score"))
            confidence = None
            if isinstance(raw_confidence, (int, float)):
                confidence = max(0.0, min(1.0, float(raw_confidence)))
            yield {
                "bbox": bbox,
                "text": text,
                "confidence": confidence,
                "label": str(item.get("block_label", "text")),
            }


def _paddle_vl_worker_main(connection: object, options: dict[str, object]) -> None:
    try:
        engine = _create_paddle_vl_engine(options)
        connection.send({"kind": "ready", "ok": True})
    except BaseException as exc:
        try:
            connection.send({"kind": "ready", "ok": False, "reason": _ocr_failure_reason(exc, "initialization")})
        except BaseException:
            pass
        connection.close()
        return
    while True:
        try:
            request = connection.recv()
        except (EOFError, OSError):
            break
        if not isinstance(request, Mapping):
            continue
        if request.get("op") == "stop":
            break
        if request.get("op") != "recognize":
            continue
        request_id = request.get("request_id")
        try:
            blocks = list(_paddle_vl_blocks(engine.predict(str(request["image_path"]))))
            connection.send({"request_id": request_id, "ok": True, "blocks": blocks})
        except BaseException as exc:
            try:
                connection.send(
                    {"request_id": request_id, "ok": False, "reason": _ocr_failure_reason(exc, "recognition")}
                )
            except BaseException:
                break
    try:
        connection.close()
    except OSError:
        pass


def _recognize_paddle_lines(
    engine: object,
    api_version: int,
    image_path: str,
) -> list[tuple[list[float], str, float]]:
    if api_version >= 3 and hasattr(engine, "predict"):
        return list(_paddle_v3_lines(engine.predict(image_path)))
    result = engine.ocr(image_path, cls=True)
    lines = result[0] if result and isinstance(result[0], list) else result
    return [parsed for item in (lines or []) if (parsed := _parse_paddle_line(item)) is not None]


def _paddle_worker_main(connection: object, options: dict[str, object]) -> None:
    """Own Paddle in another process so the API process remains responsive."""

    try:
        engine, api_version = _create_paddle_engine(options)
        connection.send({"kind": "ready", "ok": True})
    except BaseException:
        try:
            connection.send({"kind": "ready", "ok": False})
        except BaseException:
            pass
        connection.close()
        return
    while True:
        try:
            request = connection.recv()
        except (EOFError, OSError):
            break
        if not isinstance(request, Mapping):
            continue
        if request.get("op") == "stop":
            break
        if request.get("op") != "recognize":
            continue
        request_id = request.get("request_id")
        try:
            lines = _recognize_paddle_lines(engine, api_version, str(request["image_path"]))
            connection.send({"request_id": request_id, "ok": True, "lines": lines})
        except BaseException:
            try:
                connection.send({"request_id": request_id, "ok": False})
            except BaseException:
                break
    try:
        connection.close()
    except OSError:
        pass


def _restore_visual_word_spacing(image_path: Path, bbox: list[float], text: str) -> str:
    """Restore one obvious word gap lost by OCR without a language dictionary.

    This is intentionally conservative: only long all-uppercase ASCII lines
    with exactly two substantial connected ink groups are considered.  The
    character split is derived from the relative group widths, so the source
    image—not a domain-specific vocabulary—determines the inserted space.
    """

    if len(text) < 6 or not text.isascii() or not text.isalpha() or not text.isupper() or " " in text:
        return text
    try:
        import cv2  # type: ignore
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return text
        height, width = image.shape[:2]
        x0 = max(0, min(width - 1, int(bbox[0])))
        y0 = max(0, min(height - 1, int(bbox[1])))
        x1 = max(x0 + 1, min(width, int(bbox[2]) + 1))
        y1 = max(y0 + 1, min(height, int(bbox[3]) + 1))
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            return text
        _, binary = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        count, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
        minimum_area = max(2, int(crop.shape[0] * crop.shape[1] * 0.01))
        groups = sorted(
            [
                tuple(int(value) for value in stats[index])
                for index in range(1, count)
                if int(stats[index, cv2.CC_STAT_AREA]) >= minimum_area
                and int(stats[index, cv2.CC_STAT_HEIGHT]) >= max(2, int(crop.shape[0] * 0.45))
            ],
            key=lambda item: item[cv2.CC_STAT_LEFT],
        )
        if len(groups) != 2:
            return text
        left, right = groups
        gap = right[cv2.CC_STAT_LEFT] - (left[cv2.CC_STAT_LEFT] + left[cv2.CC_STAT_WIDTH])
        if gap < 1:
            return text
        group_width = left[cv2.CC_STAT_WIDTH] + right[cv2.CC_STAT_WIDTH]
        if group_width <= 0:
            return text
        split = round(len(text) * left[cv2.CC_STAT_WIDTH] / group_width)
        if split < 2 or len(text) - split < 2:
            return text
        return f"{text[:split]} {text[split:]}"
    except Exception:
        return text


def _result_mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    candidate = getattr(value, "json", None)
    try:
        candidate = candidate() if callable(candidate) else candidate
    except Exception:
        return None
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except (TypeError, ValueError):
            return None
    return candidate if isinstance(candidate, Mapping) else None


def _points_bbox(points: object) -> list[float] | None:
    if points is None:
        return None
    try:
        pairs = list(points)  # supports numpy arrays and plain lists
        xs = [float(pair[0]) for pair in pairs]
        ys = [float(pair[1]) for pair in pairs]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _paddle_v3_lines(results: object):
    try:
        iterable = list(results)  # generators returned by PaddleX are consumed once
    except TypeError:
        iterable = [results]
    for result in iterable:
        mapping = _result_mapping(result)
        if mapping is None:
            continue
        payload = mapping.get("res") if isinstance(mapping.get("res"), Mapping) else mapping
        texts = payload.get("rec_texts")
        scores = payload.get("rec_scores")
        polygons = payload.get("rec_polys")
        if polygons is None:
            polygons = payload.get("dt_polys")
        texts = [] if texts is None else texts
        scores = [] if scores is None else scores
        polygons = [] if polygons is None else polygons
        try:
            count = min(len(texts), len(scores), len(polygons))
        except TypeError:
            continue
        for index in range(count):
            text = str(texts[index]).strip()
            bbox = _points_bbox(polygons[index])
            try:
                confidence = float(scores[index])
            except (TypeError, ValueError):
                continue
            if text and bbox is not None:
                yield bbox, text, max(0.0, min(1.0, confidence))


def _parse_paddle_line(item) -> tuple[list[float], str, float] | None:
    if not isinstance(item, Sequence) or len(item) < 2:
        return None
    points = item[0]
    text_conf = item[1]
    if not isinstance(text_conf, Sequence) or len(text_conf) < 2:
        return None
    text = str(text_conf[0]).strip()
    confidence = float(text_conf[1])
    if not text:
        return None
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return [min(xs), min(ys), max(xs), max(ys)], text, confidence


@lru_cache(maxsize=1)
def _paddle_adapter() -> PaddleOCRAdapter:
    return PaddleOCRAdapter()


@lru_cache(maxsize=1)
def _paddle_vl_adapter() -> PaddleOCRVLAdapter:
    return PaddleOCRVLAdapter()


def get_ocr_adapter() -> OCRAdapter:
    provider = get_settings().ocr_provider.strip().lower()
    if provider == "mock":
        return MockOCRAdapter()
    if provider in {"paddle", "paddleocr"}:
        try:
            return _paddle_adapter()
        except Exception:
            raise OCRUnavailable("paddleocr", "initialization_failed") from None
    if provider in {"paddleocr-vl", "paddleocr_vl", "paddlevl", "paddle-vl"}:
        try:
            return _paddle_vl_adapter()
        except OCRUnavailable:
            raise
        except Exception as exc:
            raise OCRUnavailable("paddleocr-vl-1.6", _ocr_failure_reason(exc, "initialization")) from None
    raise OCRUnavailable(provider or "unconfigured", "unsupported_provider")


def get_text_ocr_adapter() -> OCRAdapter:
    """Return the PP-OCRv6 text specialist used after a weak VL result."""

    try:
        return _paddle_adapter()
    except OCRUnavailable:
        raise
    except Exception as exc:
        raise OCRUnavailable("paddleocr", _ocr_failure_reason(exc, "initialization")) from None
