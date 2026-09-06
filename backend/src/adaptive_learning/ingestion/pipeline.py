from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import ExtractionMethod, PageExtraction, PageKind
from .quality import evaluate_text_quality, quality_band


class PageExtractor(Protocol):
    name: str

    def extract(self, source: Path, output_dir: Path) -> list[PageExtraction]: ...


class TextCleaner(Protocol):
    def clean_pages(self, pages: list[PageExtraction]) -> list[PageExtraction]: ...


@dataclass(slots=True)
class PipelineDecision:
    accepted_pages: int
    review_pages: int
    rescued_pages: int
    requires_manual_review: bool


class MinerUExtractor:
    """MinerU sidecar adapter. The GPU model is deployed separately from the API process."""

    name = "mineru"

    def __init__(self, command: str = "mineru", backend: str = "hybrid-auto-engine") -> None:
        self.command = command
        self.backend = backend

    def run(self, source: Path, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            self.command,
            "-p",
            str(source),
            "-o",
            str(output_dir),
            "-b",
            self.backend,
            "--dump-content-list",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if completed.returncode != 0:
            raise RuntimeError(f"MinerU failed with exit code {completed.returncode}")
        matches = list(output_dir.rglob("content_list.json"))
        if not matches:
            raise RuntimeError("MinerU did not produce content_list.json")
        return matches[0]

    def extract(self, source: Path, output_dir: Path) -> list[PageExtraction]:
        raise NotImplementedError(
            "content_list.json normalization is handled by MinerUOutputNormalizer"
        )


class ReconstructionPipeline:
    """Quality-gated orchestration; low-quality pages are escalated, never silently accepted."""

    def __init__(
        self,
        *,
        accept_threshold: float,
        review_threshold: float,
        cleaner: TextCleaner | None = None,
    ) -> None:
        self.accept_threshold = accept_threshold
        self.review_threshold = review_threshold
        self.cleaner = cleaner

    def assess_candidate(
        self,
        *,
        page_number: int,
        text: str,
        secondary_text: str | None = None,
        page_kind: PageKind = PageKind.SCANNED,
        method: ExtractionMethod = ExtractionMethod.MINERU_PIPELINE,
        expected_coverage: float = 1.0,
    ) -> PageExtraction:
        quality = evaluate_text_quality(
            text,
            expected_coverage=expected_coverage,
            secondary_candidate=secondary_text,
        )
        return PageExtraction(
            page_number=page_number,
            page_kind=page_kind,
            method=method,
            raw_text=text,
            quality=quality,
            band=quality_band(
                quality.overall_score,
                accept=self.accept_threshold,
                review=self.review_threshold,
            ),
        )

    def finalize(
        self, pages: list[PageExtraction]
    ) -> tuple[list[PageExtraction], PipelineDecision]:
        cleaned = self.cleaner.clean_pages(pages) if self.cleaner else pages
        accepted = sum(page.band.value == "accepted" for page in cleaned)
        review = sum(page.band.value == "review" for page in cleaned)
        rescue = sum(page.band.value == "rescue" for page in cleaned)
        return cleaned, PipelineDecision(
            accepted_pages=accepted,
            review_pages=review,
            rescued_pages=rescue,
            requires_manual_review=rescue > 0,
        )


def new_book_id() -> str:
    return f"book_{uuid.uuid4().hex}"
