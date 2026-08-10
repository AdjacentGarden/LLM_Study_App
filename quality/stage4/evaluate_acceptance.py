from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "quality" / "stage4"


def _json(name: str) -> dict:
    return json.loads((STAGE / name).read_text(encoding="utf-8"))


def main() -> None:
    evaluation = _json("acceptance_evaluation.json")
    environment = _json("environment_probe.json")
    tests = _json("test_results.json")
    indexing = (ROOT / "backend" / "app" / "rag" / "indexing.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "backend" / "app" / "document" / "pipeline.py").read_text(encoding="utf-8")
    migration = (ROOT / "backend" / "migrations" / "001_pgvector_v2.sql").read_text(encoding="utf-8").lower()

    checks = evaluation["checks"]
    assert evaluation["check_count"] == len(checks) == 13
    assert all(check["passed"] is True for check in checks)
    assert evaluation["failed_count"] == 0 and evaluation["failures"] == []
    isolation = environment["isolation_declaration"]
    assert (isolation["host"], isolation["port"], isolation["database"]) == (
        "127.0.0.1",
        55432,
        "cloudpath_stage4",
    )
    assert isolation["shared_or_production_database_touched"] is False
    assert environment["postgresql"]["post_test_state_rows"] == 0
    assert environment["postgresql"]["post_test_vector_rows"] == 0
    assert environment["embedding_cuda_probe"]["shape"] == [2, 1024]
    assert environment["embedding_cuda_probe"]["device"] == "cuda:0"
    assert tests["results"]["backend_with_isolated_pgvector"]["passed"] == 296
    assert tests["results"]["backend_with_isolated_pgvector"]["failed"] == 0
    assert tests["results"]["frontend_tests"]["passed"] == 4
    assert tests["results"]["frontend_lint"] == "passed"
    assert tests["results"]["frontend_build"] == "passed"
    assert "apply_migration" not in indexing
    assert "reserve_index_build" in pipeline and "publish_index_build" in pipeline
    assert "create table if not exists rag_index_state" in migration
    assert "create table if not exists rag_chunk_vectors" in migration
    assert "vector(1024)" in migration
    print(
        json.dumps(
            {
                "status": "passed",
                "checks": len(checks),
                "backend_isolated_passed": 296,
                "frontend_passed": 4,
                "database_rows_after_tests": {"state": 0, "vectors": 0},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
