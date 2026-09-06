from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
import re
import unicodedata
from typing import Any

from app.core.config import Settings, get_settings


TOKENIZER_MODEL = "BAAI/bge-m3"
TOKENIZER_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
TRANSFORMERS_VERSION = "5.5.4"
TOKENIZERS_VERSION = "0.22.2"


class ChunkProtocolError(RuntimeError):
    """The persisted Chunk V2 protocol cannot be reproduced exactly."""


@dataclass(frozen=True, slots=True)
class FrozenChunkConfig:
    """Runtime policy paired with the immutable Stage 0 tokenizer protocol."""

    target_tokens: int = 450
    max_tokens: int = 700
    min_tokens: int = 120
    overlap_tokens: int = 80
    atomic_content_hard_max_tokens: int = 900
    quality_threshold: float = 0.45
    chunk_version: str = "v2"
    tokenizer_model: str = field(default=TOKENIZER_MODEL, init=False)
    tokenizer_revision: str = field(default=TOKENIZER_REVISION, init=False)
    transformers_version: str = field(default=TRANSFORMERS_VERSION, init=False)
    tokenizers_version: str = field(default=TOKENIZERS_VERSION, init=False)

    def __post_init__(self) -> None:
        if not (0 < self.min_tokens <= self.target_tokens <= self.max_tokens):
            raise ChunkProtocolError(
                "Chunk token sizes must satisfy 0 < min_tokens <= target_tokens <= max_tokens"
            )
        if not 0 <= self.overlap_tokens < self.min_tokens:
            raise ChunkProtocolError("overlap_tokens must satisfy 0 <= overlap_tokens < min_tokens")
        if self.atomic_content_hard_max_tokens < self.max_tokens:
            raise ChunkProtocolError("atomic_content_hard_max_tokens must be >= max_tokens")
        if not 0.0 <= self.quality_threshold <= 1.0:
            raise ChunkProtocolError("quality_threshold must be between 0 and 1")
        if not self.chunk_version.strip():
            raise ChunkProtocolError("chunk_version must not be empty")

    @property
    def version(self) -> str:
        return self.chunk_version

    @property
    def atomic_hard_max_tokens(self) -> int:
        return self.atomic_content_hard_max_tokens

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> FrozenChunkConfig:
        source = settings or get_settings()
        return cls(
            target_tokens=source.chunk_target_tokens,
            max_tokens=source.chunk_max_tokens,
            min_tokens=source.chunk_min_tokens,
            overlap_tokens=source.chunk_overlap_tokens,
            atomic_content_hard_max_tokens=source.chunk_atomic_content_hard_max_tokens,
            quality_threshold=source.chunk_quality_threshold,
            chunk_version=source.chunk_version,
        )


class BgeM3TokenCounter:
    """Exact BGE-M3 tokenizer used for all persisted Chunk V2 token counts.

    There is deliberately no approximate or whitespace-token fallback. A
    dependency mismatch, missing model revision, or tokenizer load error must
    fail the chunking operation so two installations cannot persist different
    counts for the same input.
    """

    model_name = TOKENIZER_MODEL
    revision = TOKENIZER_REVISION

    def __init__(self) -> None:
        self._verify_dependency_versions()
        try:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                TOKENIZER_MODEL,
                revision=TOKENIZER_REVISION,
                use_fast=True,
                trust_remote_code=False,
            )
            if getattr(self._tokenizer, "is_fast", False) is not True:
                raise ChunkProtocolError("Locked BGE-M3 fast tokenizer is unavailable")
        except Exception as exc:
            raise ChunkProtocolError(
                f"Unable to load locked tokenizer {TOKENIZER_MODEL}@{TOKENIZER_REVISION}"
            ) from exc

    @staticmethod
    def _verify_dependency_versions() -> None:
        required = {
            "transformers": TRANSFORMERS_VERSION,
            "tokenizers": TOKENIZERS_VERSION,
        }
        for package, expected in required.items():
            try:
                actual = metadata.version(package)
            except metadata.PackageNotFoundError as exc:
                raise ChunkProtocolError(
                    f"Required tokenizer dependency is missing: {package}=={expected}"
                ) from exc
            if actual != expected:
                raise ChunkProtocolError(
                    f"Tokenizer dependency mismatch: expected {package}=={expected}, found {actual}"
                )

    def encode(self, text: str) -> list[int]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not text:
            return []
        try:
            encoded = self._tokenizer.encode(text, add_special_tokens=False)
        except Exception as exc:
            raise ChunkProtocolError("BGE-M3 tokenizer encode failed") from exc
        return [int(token_id) for token_id in encoded]

    def decode(self, token_ids: Sequence[int]) -> str:
        try:
            return str(
                self._tokenizer.decode(
                    [int(token_id) for token_id in token_ids],
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )
            )
        except Exception as exc:
            raise ChunkProtocolError("BGE-M3 tokenizer decode failed") from exc

    def count(self, text: str) -> int:
        return len(self.encode(text))


def _clean_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\t \f\v]+", " ", line).strip() for line in value.split("\n")]
    output: list[str] = []
    for line in lines:
        if line or (output and output[-1]):
            output.append(line)
    return "\n".join(output).strip()


def render_embedding_text(
    body: str,
    heading_path: Sequence[str] | None = None,
    overlap_text: str | None = None,
) -> str:
    """Render the complete, deterministic text counted and embedded for V2."""

    heading = " > ".join(
        cleaned
        for item in (heading_path or ())
        if (cleaned := _clean_text(str(item)))
    )
    # Prior-chunk overlap is context for the current body and therefore comes
    # before it. Metadata not represented here is explicitly excluded from the
    # persisted token_count.
    parts = [heading, _clean_text(overlap_text or ""), _clean_text(body)]
    return "\n\n".join(part for part in parts if part)


def normalize_for_hash(text: str) -> str:
    """Normalize visible text for exact-duplicate hashes and stable IDs."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(normalized.split())


def _field(source: object, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def is_chunk_indexable(
    chunk_or_quality: object | float | None,
    quality_threshold: float | FrozenChunkConfig | None = None,
    *,
    config: FrozenChunkConfig | None = None,
) -> bool:
    """Apply the V2 quality quarantine while preserving legacy artifacts.

    A bare numeric value is treated as a quality score. For a Chunk/mapping,
    explicit exclusion always wins. Legacy records with no version and no
    quality score stay eligible for backward compatibility; a V2 record with
    no score fails closed. V2 records use their persisted generation threshold
    first, then an explicit caller config, then the current frozen settings.
    """

    if isinstance(chunk_or_quality, (float, int)) and not isinstance(chunk_or_quality, bool):
        threshold = _quality_threshold(
            explicit=quality_threshold,
            config=config,
        )
        return float(chunk_or_quality) >= threshold
    if chunk_or_quality is None:
        return False

    text = str(_field(chunk_or_quality, "text", "") or "").strip()
    if not text:
        return False
    if str(_field(chunk_or_quality, "content_type", "")).strip().lower() == "ocr_pending":
        return False

    raw_metadata = _field(chunk_or_quality, "metadata", {})
    chunk_metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    if chunk_metadata.get("indexable") is False or chunk_metadata.get("quarantined") is True:
        return False
    if str(chunk_metadata.get("index_status", "")).strip().lower() in {
        "excluded",
        "pending",
        "quarantined",
        "rejected",
    }:
        return False

    score = _field(chunk_or_quality, "quality_score")
    version = str(_field(chunk_or_quality, "chunk_version", "") or "").strip().lower()
    if score is None:
        return not version.startswith("v2")
    persisted_threshold = chunk_metadata.get("quality_threshold")
    try:
        threshold = _quality_threshold(
            persisted=persisted_threshold,
            explicit=quality_threshold,
            config=config,
        )
    except (TypeError, ValueError, ChunkProtocolError):
        # A V2 artifact whose persisted protocol cannot be reproduced is not
        # safe to retrieve. Keep corrupt metadata from crashing every query,
        # but fail the individual chunk closed.
        return False
    try:
        return float(score) >= threshold
    except (TypeError, ValueError):
        return False


def _quality_threshold(
    *,
    persisted: object | None = None,
    explicit: float | FrozenChunkConfig | None = None,
    config: FrozenChunkConfig | None = None,
) -> float:
    """Resolve the quality gate without a second hard-coded protocol value."""

    if persisted is not None:
        threshold = float(persisted)
    elif isinstance(explicit, FrozenChunkConfig):
        threshold = explicit.quality_threshold
    elif explicit is not None:
        threshold = float(explicit)
    elif config is not None:
        threshold = config.quality_threshold
    else:
        threshold = FrozenChunkConfig.from_settings().quality_threshold
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("quality_threshold must be between 0 and 1")
    return threshold
