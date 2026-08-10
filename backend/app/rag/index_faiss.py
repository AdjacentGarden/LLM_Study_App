from __future__ import annotations

from app.rag.index_base import ArtifactVectorIndex


class FaissIndex(ArtifactVectorIndex):
    name = "faiss"

    @property
    def available(self) -> bool:
        try:
            import faiss  # noqa: F401
        except Exception:
            return False
        return True
