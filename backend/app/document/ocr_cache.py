from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import threading
import time
from typing import Any

from app.schemas.books import QualityWarning, TextBlock


_cache_lock = threading.Lock()


class OCRResultCache:
    """Content-addressed disk cache for expensive OCR inference results."""

    def __init__(self, root: Path, *, enabled: bool, ttl_seconds: int, max_items: int) -> None:
        self.root = root
        self.enabled = enabled and ttl_seconds > 0
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items

    def key_for(
        self,
        image_path: Path,
        *,
        page: int,
        provider: str,
        model: str,
        language: str,
        device: str,
        pipeline_version: str,
        quality_profile: str,
    ) -> str:
        digest = sha256()
        for value in (provider, model, language, device, pipeline_version, quality_profile, str(page)):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        with image_path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def get(self, key: str) -> tuple[list[TextBlock], list[QualityWarning]] | None:
        if not self.enabled:
            return None
        path = self.root / f"{key}.json"
        try:
            stat = path.stat()
            if time.time() - stat.st_mtime > self.ttl_seconds:
                path.unlink(missing_ok=True)
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            blocks = [TextBlock.model_validate(item) for item in payload.get("blocks", [])]
            warnings = [QualityWarning.model_validate(item) for item in payload.get("warnings", [])]
            # Touch only the directory entry so pruning approximates LRU.
            path.touch()
            return blocks, warnings
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def put(self, key: str, blocks: list[TextBlock], warnings: list[QualityWarning]) -> None:
        if not self.enabled:
            return
        payload: dict[str, Any] = {
            "version": 1,
            "blocks": [item.model_dump(mode="json") for item in blocks],
            "warnings": [item.model_dump(mode="json") for item in warnings],
        }
        with _cache_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / f"{key}.json"
            temporary = self.root / f".{key}.{threading.get_ident()}.tmp"
            try:
                temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                temporary.replace(target)
                self._prune_locked()
            finally:
                temporary.unlink(missing_ok=True)

    def _prune_locked(self) -> None:
        try:
            entries = sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        except OSError:
            return
        now = time.time()
        for index, path in enumerate(entries):
            try:
                expired = now - path.stat().st_mtime > self.ttl_seconds
                if expired or index >= self.max_items:
                    path.unlink(missing_ok=True)
            except OSError:
                continue


__all__ = ["OCRResultCache"]
