from __future__ import annotations

from importlib import metadata
import json
from pathlib import Path
import sys
import tomllib
from types import ModuleType

import pytest

from app.core.config import Settings, get_settings
from app.document.chunk_protocol import (
    BgeM3TokenCounter,
    ChunkProtocolError,
    FrozenChunkConfig,
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    TOKENIZERS_VERSION,
    TRANSFORMERS_VERSION,
    is_chunk_indexable,
    normalize_for_hash,
    render_embedding_text,
)
from app.schemas.books import Chunk


def _legacy_payload() -> dict[str, object]:
    return {
        "chunk_id": "legacy-1",
        "book_id": "book-1",
        "chapter_id": "chapter-1",
        "page_start": 1,
        "page_end": 1,
        "content_type": "text",
        "text": "Legacy content remains readable.",
        "asset_ids": [],
        "key_concepts": [],
    }


def test_chunk_schema_reads_legacy_json_without_inventing_v2_evidence() -> None:
    chunk = Chunk.model_validate_json(json.dumps(_legacy_payload()))

    assert chunk.chunk_version is None
    assert chunk.parser is None
    assert chunk.quality_score is None
    assert chunk.token_count is None
    assert chunk.heading_path == []
    assert chunk.source_block_ids == []
    assert chunk.metadata == {}
    assert is_chunk_indexable(chunk)


def test_chunk_schema_round_trips_v2_fields() -> None:
    chunk = Chunk.model_validate(
        {
            **_legacy_payload(),
            "parser": "mineru",
            "parser_version": "3.4.4",
            "chunk_version": "v2",
            "heading_path": ["Chapter 1", "Section A"],
            "source_block_ids": ["p1-b1", "p1-b2"],
            "quality_score": 0.91,
            "token_count": 321,
            "content_hash": "abc123",
            "bbox": [1.0, 2.0, 100.0, 200.0],
            "metadata": {"quality_formula_version": "quality-v1"},
        }
    )

    restored = Chunk.model_validate_json(chunk.model_dump_json())
    assert restored == chunk
    assert restored.heading_path == ["Chapter 1", "Section A"]
    assert restored.metadata["quality_formula_version"] == "quality-v1"


def test_frozen_config_matches_stage0_acceptance_protocol() -> None:
    thresholds_path = Path(__file__).parents[3] / "quality" / "stage0" / "acceptance_thresholds.json"
    frozen = json.loads(thresholds_path.read_text(encoding="utf-8"))["chunking"]
    config = FrozenChunkConfig()

    assert config.tokenizer_model == frozen["tokenizer_model"] == TOKENIZER_MODEL
    assert config.tokenizer_revision == frozen["tokenizer_revision"] == TOKENIZER_REVISION
    assert config.transformers_version == frozen["transformers_version"] == TRANSFORMERS_VERSION
    assert config.tokenizers_version == frozen["tokenizers_version"] == TOKENIZERS_VERSION
    assert config.target_tokens == frozen["target_tokens"] == 450
    assert config.max_tokens == frozen["max_tokens"] == 700
    assert config.min_tokens == frozen["min_tokens"] == 120
    assert config.overlap_tokens == frozen["overlap_tokens"] == 80
    assert config.atomic_content_hard_max_tokens == frozen["atomic_content_hard_max_tokens"] == 900
    assert config.quality_threshold == frozen["chunk_quality_threshold"] == 0.45
    assert config.version == "v2"


def test_chunk_settings_expose_v2_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    names = {
        "BOOKCOURSE_CHUNK_TARGET_TOKENS": "500",
        "BOOKCOURSE_CHUNK_MAX_TOKENS": "750",
        "BOOKCOURSE_CHUNK_MIN_TOKENS": "140",
        "BOOKCOURSE_CHUNK_OVERLAP_TOKENS": "90",
        "BOOKCOURSE_CHUNK_ATOMIC_CONTENT_HARD_MAX_TOKENS": "950",
        "BOOKCOURSE_CHUNK_QUALITY_THRESHOLD": "0.55",
        "BOOKCOURSE_CHUNK_VERSION": "v2-test",
    }
    for name, value in names.items():
        monkeypatch.setenv(name, value)

    settings = Settings()
    config = FrozenChunkConfig.from_settings(settings)

    assert config.target_tokens == 500
    assert config.max_tokens == 750
    assert config.min_tokens == 140
    assert config.overlap_tokens == 90
    assert config.atomic_content_hard_max_tokens == 950
    assert config.quality_threshold == 0.55
    assert config.chunk_version == "v2-test"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_tokens": 500, "target_tokens": 450},
        {"overlap_tokens": 120},
        {"atomic_content_hard_max_tokens": 699},
        {"quality_threshold": 1.01},
        {"chunk_version": " "},
    ],
)
def test_frozen_config_rejects_protocol_invariant_drift(kwargs: dict[str, object]) -> None:
    with pytest.raises(ChunkProtocolError):
        FrozenChunkConfig(**kwargs)  # type: ignore[arg-type]


def test_embedding_text_and_hash_normalization_are_deterministic() -> None:
    rendered = render_embedding_text(
        "  Main\t body.  \r\n",
        [" Chapter 1 ", "Section  A"],
        " Previous   context. ",
    )

    assert rendered == "Chapter 1 > Section A\n\nPrevious context.\n\nMain body."
    assert normalize_for_hash(" ＡＢＣ\n  Mixed CASE ") == "abc mixed case"


def test_indexability_fails_closed_for_v2_and_honors_explicit_quarantine() -> None:
    base = {**_legacy_payload(), "chunk_version": "v2"}
    assert not is_chunk_indexable(base)
    assert not is_chunk_indexable({**base, "quality_score": 0.449})
    assert is_chunk_indexable({**base, "quality_score": 0.45})
    assert not is_chunk_indexable({**base, "quality_score": 1.0, "metadata": {"quarantined": True}})
    assert not is_chunk_indexable({**base, "quality_score": 1.0, "content_type": "ocr_pending"})
    assert is_chunk_indexable(0.8, quality_threshold=0.5)
    assert not is_chunk_indexable(0.4, config=FrozenChunkConfig(quality_threshold=0.5))


@pytest.mark.parametrize(
    ("configured_threshold", "score", "expected"),
    [
        ("0.30", 0.35, True),
        ("0.70", 0.60, False),
    ],
)
def test_indexability_uses_configured_threshold_when_v2_artifact_has_no_persisted_value(
    monkeypatch: pytest.MonkeyPatch,
    configured_threshold: str,
    score: float,
    expected: bool,
) -> None:
    monkeypatch.setenv("BOOKCOURSE_CHUNK_QUALITY_THRESHOLD", configured_threshold)
    get_settings.cache_clear()
    chunk = {
        **_legacy_payload(),
        "chunk_version": "v2",
        "quality_score": score,
        "metadata": {"indexable": True},
    }
    try:
        assert is_chunk_indexable(chunk) is expected
    finally:
        get_settings.cache_clear()


def test_persisted_v2_quality_threshold_is_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_CHUNK_QUALITY_THRESHOLD", "0.90")
    get_settings.cache_clear()
    chunk = {
        **_legacy_payload(),
        "chunk_version": "v2",
        "quality_score": 0.35,
        "metadata": {"indexable": True, "quality_threshold": 0.30},
    }
    try:
        assert is_chunk_indexable(chunk)
        assert not is_chunk_indexable(
            {**chunk, "metadata": {"indexable": True, "quality_threshold": 0.40}}
        )
    finally:
        get_settings.cache_clear()


class _FakeTokenizer:
    is_fast = True

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert add_special_tokens is False
        return [len(part) for part in text.split()]

    def decode(
        self,
        token_ids: list[int],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        assert skip_special_tokens is True
        assert clean_up_tokenization_spaces is False
        return ",".join(map(str, token_ids))


def _install_fake_transformers(monkeypatch: pytest.MonkeyPatch, *, load_error: bool = False) -> dict[str, object]:
    calls: dict[str, object] = {}
    module = ModuleType("transformers")

    class _AutoTokenizer:
        @staticmethod
        def from_pretrained(model_name: str, **kwargs: object) -> _FakeTokenizer:
            calls.update({"model_name": model_name, **kwargs})
            if load_error:
                raise OSError("model unavailable")
            return _FakeTokenizer()

    module.AutoTokenizer = _AutoTokenizer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", module)
    monkeypatch.setattr(
        metadata,
        "version",
        lambda package: {
            "transformers": TRANSFORMERS_VERSION,
            "tokenizers": TOKENIZERS_VERSION,
        }[package],
    )
    return calls


def test_bge_counter_uses_exact_model_revision_and_has_no_special_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_transformers(monkeypatch)
    counter = BgeM3TokenCounter()

    assert calls == {
        "model_name": TOKENIZER_MODEL,
        "revision": TOKENIZER_REVISION,
        "use_fast": True,
        "trust_remote_code": False,
    }
    assert counter.encode("aa bbb") == [2, 3]
    assert counter.count("aa bbb") == 2
    assert counter.decode([2, 3]) == "2,3"


def test_bge_counter_hard_fails_on_dependency_or_model_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "version", lambda _package: "0.0.0")
    with pytest.raises(ChunkProtocolError, match="dependency mismatch"):
        BgeM3TokenCounter()

    _install_fake_transformers(monkeypatch, load_error=True)
    with pytest.raises(ChunkProtocolError, match="Unable to load locked tokenizer"):
        BgeM3TokenCounter()


def test_tokenizer_runtime_dependencies_are_exactly_pinned() -> None:
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]

    assert f"transformers=={TRANSFORMERS_VERSION}" in project["dependencies"]
    assert f"tokenizers=={TOKENIZERS_VERSION}" in project["dependencies"]
