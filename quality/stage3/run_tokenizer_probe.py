from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from app.document.chunk_protocol import (  # noqa: E402
    BgeM3TokenCounter,
    FrozenChunkConfig,
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    TOKENIZERS_VERSION,
    TRANSFORMERS_VERSION,
    render_embedding_text,
)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    counter = BgeM3TokenCounter()
    config = FrozenChunkConfig()
    samples = [
        "Cell biology: the plasma membrane regulates transport.",
        "细胞生物学：细胞膜调节物质运输。",
        "Stage | Chromosome state\nG1 | unreplicated\nG2 | replicated",
        r"Probability explanation: $P(A \cap B)=P(A)P(B)$.",
    ]
    results = []
    for source in samples:
        token_ids = counter.encode(source)
        results.append(
            {
                "source": source,
                "token_count": len(token_ids),
                "stable_repeat": token_ids == counter.encode(source),
                "decoded": counter.decode(token_ids),
            }
        )
    complete = render_embedding_text(
        samples[0],
        ["Biology", "Cell membrane"],
        "Prior context for overlap.",
    )
    complete_count = counter.count(complete)
    passed = bool(
        metadata.version("transformers") == TRANSFORMERS_VERSION
        and metadata.version("tokenizers") == TOKENIZERS_VERSION
        and all(item["token_count"] > 0 and item["stable_repeat"] for item in results)
        and complete_count > counter.count(samples[0])
    )
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "model": TOKENIZER_MODEL,
        "revision": TOKENIZER_REVISION,
        "transformers_version": metadata.version("transformers"),
        "tokenizers_version": metadata.version("tokenizers"),
        "tokenizer_class": counter._tokenizer.__class__.__name__,
        "chunk_config": {
            "target": config.target_tokens,
            "max": config.max_tokens,
            "min": config.min_tokens,
            "overlap": config.overlap_tokens,
            "atomic_hard_max": config.atomic_content_hard_max_tokens,
            "quality_threshold": config.quality_threshold,
        },
        "samples": results,
        "complete_embedding_text_token_count": complete_count,
        "passed": passed,
    }
    output = STAGE / "tokenizer_probe.json"
    _atomic_write(output, payload)
    print(json.dumps({"passed": passed, "output": str(output), "complete_tokens": complete_count}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
