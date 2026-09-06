from __future__ import annotations

from app.rag.index_base import ArtifactVectorIndex


class ChromaIndex(ArtifactVectorIndex):
    name = "chroma"

    @property
    def available(self) -> bool:
        try:
            import chromadb  # noqa: F401
        except Exception:
            return False
        return True
