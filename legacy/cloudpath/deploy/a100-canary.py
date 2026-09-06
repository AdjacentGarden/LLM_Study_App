"""Run on the target A100 host before enabling production traffic."""

from __future__ import annotations

import json
import os
from pathlib import Path

import torch

from app.core.config import (
    BGE_M3_DIMENSIONS,
    BGE_M3_MODEL,
    BGE_M3_REVISION,
    BGE_RERANKER_MODEL,
    BGE_RERANKER_REVISION,
)
from app.rag.embedding import BGEM3EmbeddingService
from app.rag.reranker import BGERerankerService


expected_cache_root = Path("/var/lib/bookcourse/models")
configured_cache_root = Path(os.environ.get("HF_HOME", "")).resolve()
if configured_cache_root != expected_cache_root:
    raise SystemExit(
        f"FAIL: HF_HOME must be {expected_cache_root}, found {configured_cache_root}"
    )
if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
    raise SystemExit("FAIL: canary must run with Hugging Face and Transformers offline")
if not torch.cuda.is_available():
    raise SystemExit("FAIL: CUDA is unavailable")
device_name = torch.cuda.get_device_name(0)
if "A100" not in device_name.upper():
    raise SystemExit(f"FAIL: expected A100, found {device_name}")
compute_capability = torch.cuda.get_device_capability(0)

embedder = BGEM3EmbeddingService(
    BGE_M3_MODEL,
    dimensions=BGE_M3_DIMENSIONS,
    revision=BGE_M3_REVISION,
    device="cuda:0",
)
vector = embedder.embed_query("BookCourse A100 release canary")
if len(vector) != BGE_M3_DIMENSIONS:
    raise SystemExit(f"FAIL: embedding dimension {len(vector)}")
embedding_device = str(getattr(embedder._load_model(), "device", "unknown"))
if not embedding_device.startswith("cuda"):
    raise SystemExit(f"FAIL: embedding model loaded on {embedding_device}")

reranker = BGERerankerService(
    BGE_RERANKER_MODEL,
    revision=BGE_RERANKER_REVISION,
    device="cuda:0",
    fail_open=False,
)
reranker_model = reranker._load_model()
reranker_device = str(getattr(reranker_model, "device", "unknown"))
if not reranker_device.startswith("cuda"):
    raise SystemExit(f"FAIL: reranker loaded on {reranker_device}")
score = float(reranker_model.predict([["细胞分裂", "同源染色体在减数分裂中分离"]])[0])
print(json.dumps({
    "status": "PASS",
    "gpu": device_name,
    "driver_cuda": torch.version.cuda,
    "compute_capability": list(compute_capability),
    "torch": torch.__version__,
    "hf_home": str(configured_cache_root),
    "hf_hub_offline": os.environ["HF_HUB_OFFLINE"],
    "transformers_offline": os.environ["TRANSFORMERS_OFFLINE"],
    "embedding_model": BGE_M3_MODEL,
    "embedding_revision": BGE_M3_REVISION,
    "embedding_device": embedding_device,
    "embedding_dimensions": len(vector),
    "reranker_model": BGE_RERANKER_MODEL,
    "reranker_revision": BGE_RERANKER_REVISION,
    "reranker_device": reranker_device,
    "reranker_score": score,
}, ensure_ascii=False))
