from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha1
from math import sqrt
from typing import Any, Protocol

from app.core.config import BGE_M3_DIMENSIONS, BGE_M3_REVISION, get_settings
from app.document.chunk_protocol import render_embedding_text
from app.schemas.books import Chunk


@dataclass(frozen=True)
class EmbeddingDescriptor:
    """Auditable identity for vectors produced by an embedding service."""

    provider: str
    model: str
    revision: str
    version: str
    dimension: int
    device: str

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


class EmbeddingDimensionError(RuntimeError):
    """Raised when a model emits vectors incompatible with its descriptor."""


class EmbeddingConfigurationError(RuntimeError):
    """Raised when configured embedding metadata violates the frozen contract."""


class EmbeddingService(Protocol):
    name: str
    dimensions: int
    descriptor: EmbeddingDescriptor

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...


class Embedder(Protocol):
    name: str

    def embed(self, text: str) -> list[float]:
        ...


class HashingEmbeddingService:
    name = "hashing"

    def __init__(self, dimensions: int = 256, *, version: str = "v1") -> None:
        self.dimensions = dimensions
        self.descriptor = EmbeddingDescriptor(
            provider=self.name,
            model="sha1-feature-hashing",
            revision="algorithm-v1",
            version=version,
            dimension=dimensions,
            device="cpu",
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed(self, text: str) -> list[float]:
        return self.embed_query(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _embedding_tokens(text):
            digest = sha1(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimensions
            sign = 1 if digest[2] % 2 == 0 else -1
            vector[index] += sign
        norm = sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class HashingEmbedder(HashingEmbeddingService):
    name = "local_hashing"

    def __init__(self, dimensions: int = 64, *, version: str = "v1") -> None:
        super().__init__(dimensions=dimensions, version=version)


class BGEM3EmbeddingService:
    name = "bge_m3"

    def __init__(
        self,
        model_name: str,
        dimensions: int = BGE_M3_DIMENSIONS,
        batch_size: int = 16,
        *,
        revision: str = BGE_M3_REVISION,
        version: str = "v1",
        device: str = "auto",
    ) -> None:
        if dimensions != BGE_M3_DIMENSIONS:
            raise EmbeddingConfigurationError(
                f"BGE-M3 dimension must be {BGE_M3_DIMENSIONS}, got {dimensions}"
            )
        if revision != BGE_M3_REVISION:
            raise EmbeddingConfigurationError(
                f"BGE-M3 revision must be {BGE_M3_REVISION}, got {revision}"
            )
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.revision = revision
        self.version = version
        self.device = device
        self.descriptor = EmbeddingDescriptor(
            provider=self.name,
            model=model_name,
            revision=revision,
            version=version,
            dimension=dimensions,
            device=device,
        )
        self._model = None

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as exc:
                raise RuntimeError("sentence_transformers is not installed") from exc
            kwargs: dict[str, object] = {"revision": self.revision}
            if self.device != "auto":
                kwargs["device"] = self.device
            self._model = SentenceTransformer(self.model_name, **kwargs)
        get_dimension = getattr(self._model, "get_embedding_dimension", None)
        if not callable(get_dimension):
            get_dimension = getattr(self._model, "get_sentence_embedding_dimension", None)
        reported_dimension = get_dimension() if callable(get_dimension) else None
        if reported_dimension is not None and int(reported_dimension) != self.dimensions:
            self._model = None
            raise EmbeddingDimensionError(
                f"BGE-M3 reported dimension {reported_dimension}; expected {self.dimensions}"
            )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        vectors = model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True)
        normalized = [list(map(float, vector)) for vector in vectors]
        for vector in normalized:
            if len(vector) != self.dimensions:
                raise EmbeddingDimensionError(
                    f"BGE-M3 emitted dimension {len(vector)}; expected {self.dimensions}"
                )
        return normalized

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed(self, text: str) -> list[float]:
        return self.embed_query(text)


def render_chunk_embedding_text(chunk: Chunk) -> str:
    """Render the one canonical text representation used by RAG consumers."""

    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    raw_overlap = metadata.get("overlap_text")
    overlap_text = str(raw_overlap) if raw_overlap is not None else None
    return render_embedding_text(
        chunk.text,
        heading_path=chunk.heading_path,
        overlap_text=overlap_text,
    )


def _embedding_tokens(text: str) -> list[str]:
    normalized = text.lower()
    ascii_tokens = []
    current = []
    for char in normalized:
        if char.isascii() and char.isalnum():
            current.append(char)
        else:
            if current:
                ascii_tokens.append("".join(current))
                current = []
    if current:
        ascii_tokens.append("".join(current))

    cjk = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
    cjk_bigrams = ["".join(cjk[index : index + 2]) for index in range(max(0, len(cjk) - 1))]
    ascii_compounds = [
        "".join(ascii_tokens[index : index + width])
        for width in (2, 3)
        for index in range(max(0, len(ascii_tokens) - width + 1))
        if 4 <= len("".join(ascii_tokens[index : index + width])) <= 64
    ]
    return ascii_tokens + ascii_compounds + cjk_bigrams


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0
    return sum(a * b for a, b in zip(left, right, strict=True))


def get_embedding_service() -> EmbeddingService:
    settings = get_settings()
    provider = settings.embedding_provider
    if provider in {"bge_m3", "bge-m3"}:
        return BGEM3EmbeddingService(
            settings.bge_m3_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
            revision=settings.bge_m3_revision,
            version=settings.embedding_version,
            device=settings.embedding_device,
        )
    return HashingEmbeddingService(
        dimensions=settings.embedding_dimensions,
        version=settings.embedding_version,
    )


def get_hashing_fallback_service() -> HashingEmbeddingService:
    """Build a visibly distinct fallback using the configured index dimension."""

    settings = get_settings()
    return HashingEmbeddingService(
        dimensions=settings.embedding_dimensions,
        version=settings.embedding_version,
    )


def get_embedder() -> Embedder:
    settings = get_settings()
    provider = getattr(settings, "embedding_provider", "hashing")
    if provider in {"local_hashing", "hashing"}:
        return HashingEmbedder(
            dimensions=settings.embedding_dimensions,
            version=settings.embedding_version,
        )
    service = get_embedding_service()
    return service if hasattr(service, "embed") else HashingEmbedder(
        dimensions=settings.embedding_dimensions,
        version=settings.embedding_version,
    )
