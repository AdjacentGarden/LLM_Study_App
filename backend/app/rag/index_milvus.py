from __future__ import annotations

from app.rag.index_base import ArtifactVectorIndex


class MilvusIndex(ArtifactVectorIndex):
    name = "milvus"

    @property
    def available(self) -> bool:
        try:
            import pymilvus  # noqa: F401
        except Exception:
            return False
        return True
