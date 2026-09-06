"""Back up and review existing course flashcards; run on the deployment server.

Uses configured model credentials without printing them. Failure keeps that course
unchanged, and the API quality gate prevents serving its unreviewed cards.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.config import get_settings
from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient
from adaptive_learning.personalization.flashcard_quality import (
    FlashcardQualityError,
    FlashcardQualityGate,
)
from adaptive_learning.personalization.models import ChapterLearningBundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.report_dir / stamp
    output.mkdir(parents=True, exist_ok=False)
    database = settings.data_dir / "state" / "assessments.sqlite3"
    with sqlite3.connect(database) as source, sqlite3.connect(output / "assessments-before.sqlite3") as backup:
        source.backup(backup)
        rows = source.execute("SELECT bundle_json FROM course_bundles ORDER BY created_at DESC").fetchall()
    client = OpenAICompatibleClient(LLMConfig(base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key, model=settings.deepseek_model,
        proxy_url=settings.llm_https_proxy, timeout_seconds=120))
    gate = FlashcardQualityGate(client, settings.data_dir / "state" / "flashcard_quality.sqlite3",
                              f"{settings.deepseek_base_url}|{settings.deepseek_model}")
    repository = SQLiteAssessmentRepository(database)
    started = time.monotonic()
    failures = []
    report = []
    for index, (payload,) in enumerate(rows):
        bundle = ChapterLearningBundle.model_validate_json(payload)
        try:
            reviewed = gate.ensure(bundle)
            repository.update_reviewed_course(reviewed)
            changes = [{"card_id": before.card_id, "before_front": before.front,
                        "before_back": before.back, "after_front": after.front,
                        "after_back": after.back,
                        "evidence": [c.model_dump() for c in after.citations]}
                       for before, after in zip(bundle.flashcards, reviewed.flashcards, strict=True)]
            report.append({"course_id": bundle.course_id, "chapter": bundle.chapter_title,
                           "depth": bundle.decision.depth.value, "cards": changes})
            print(f"PASS {index + 1}/{len(rows)} {bundle.chapter_title} {len(changes)} cards", flush=True)
        except FlashcardQualityError as error:
            failures.append({"course_id": bundle.course_id, "error": str(error),
                             "review_feedback": error.review_feedback})
            print(f"FAIL {index + 1}/{len(rows)} {bundle.chapter_title}: {error}", flush=True)
        (output / "review-report.json").write_text(json.dumps(
            {"courses": report, "failures": failures, "seconds": round(time.monotonic() - started, 2)},
            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {output}; passed={len(report)} failed={len(failures)}", flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
