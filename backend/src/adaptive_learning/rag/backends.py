from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .index import RAGIndexError

_MIB = 1024 * 1024


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch  # type: ignore[import-not-found]

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _require_free_gpu_memory(torch: Any, device: str, minimum_mib: int) -> None:
    if not device.startswith("cuda"):
        return
    try:
        free_bytes, _ = torch.cuda.mem_get_info(torch.device(device))
    except RuntimeError as error:
        raise RAGIndexError("GPU memory status is unavailable") from error
    if free_bytes < minimum_mib * _MIB:
        raise RAGIndexError(
            f"GPU has insufficient free memory; requires at least {minimum_mib} MiB"
        )


class TransformerQueryEncoder:
    def __init__(self, model_path: Path, *, device: str = "auto") -> None:
        self.model_path = model_path
        self.device = resolve_device(device)
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._lock = threading.Lock()
        self._dimensions = self._read_dimensions()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _read_dimensions(self) -> int:
        config_path = self.model_path / "config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            dimensions = int(config["hidden_size"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RAGIndexError("embedding model config is invalid") from error
        if dimensions < 1:
            raise RAGIndexError("embedding model dimensions are invalid")
        return dimensions

    def _load(self) -> tuple[Any, Any]:
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model
        try:
            from transformers import AutoModel, AutoTokenizer  # type: ignore[import-not-found]
        except ImportError as error:
            raise RAGIndexError(
                "transformer RAG dependencies are not installed; install backend[rag]"
            ) from error
        try:
            import torch  # type: ignore[import-not-found]

            _require_free_gpu_memory(torch, self.device, 512)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_path, local_files_only=True)
            self._model = (
                AutoModel.from_pretrained(self.model_path, local_files_only=True)
                .to(self.device)
                .eval()
            )
        except (OSError, RuntimeError) as error:
            raise RAGIndexError("embedding model could not be loaded") from error
        return self._tokenizer, self._model

    def encode_query(self, text: str) -> NDArray[np.float32]:
        try:
            import torch  # type: ignore[import-not-found]
            from torch.nn import functional  # type: ignore[import-not-found]
        except ImportError as error:
            raise RAGIndexError(
                "transformer RAG dependencies are not installed; install backend[rag]"
            ) from error
        with self._lock, torch.inference_mode():
            tokenizer, model = self._load()
            inputs = tokenizer(
                ["为这个句子生成表示以用于检索相关文章：" + text],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            ).to(self.device)
            try:
                vector = model(**inputs).last_hidden_state[:, 0]
                vector = functional.normalize(vector, p=2, dim=1)
                return np.asarray(vector[0].float().cpu().numpy(), dtype=np.float32)
            except RuntimeError as error:
                raise RAGIndexError("query embedding failed") from error


class TransformerPairReranker:
    def __init__(self, model_path: Path, *, device: str = "auto") -> None:
        self.model_path = model_path
        self.device = resolve_device(device)
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._lock = threading.Lock()

    def _load(self) -> tuple[Any, Any]:
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model
        try:
            from transformers import (  # type: ignore[import-not-found]
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )
        except ImportError as error:
            raise RAGIndexError(
                "transformer RAG dependencies are not installed; install backend[rag]"
            ) from error
        try:
            import torch  # type: ignore[import-not-found]

            _require_free_gpu_memory(torch, self.device, 2048)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_path, local_files_only=True)
            self._model = (
                AutoModelForSequenceClassification.from_pretrained(
                    self.model_path, local_files_only=True
                )
                .to(self.device)
                .eval()
            )
        except (OSError, RuntimeError) as error:
            raise RAGIndexError("reranker model could not be loaded") from error
        return self._tokenizer, self._model

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        try:
            import torch  # type: ignore[import-not-found]
        except ImportError as error:
            raise RAGIndexError(
                "transformer RAG dependencies are not installed; install backend[rag]"
            ) from error
        scores: list[float] = []
        with self._lock, torch.inference_mode():
            tokenizer, model = self._load()
            pairs = [[query, text] for text in texts]
            for start in range(0, len(pairs), 16):
                inputs = tokenizer(
                    pairs[start : start + 16],
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                ).to(self.device)
                try:
                    logits = model(**inputs, return_dict=True).logits.view(-1)
                    scores.extend(float(value) for value in logits.float().cpu().tolist())
                except RuntimeError as error:
                    raise RAGIndexError("reranking failed") from error
        return scores
