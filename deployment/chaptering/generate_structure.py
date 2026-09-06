from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adaptive_learning.config import get_settings
from adaptive_learning.ingestion.chaptering import ChapterReconstructor, load_normalized_pages
from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a verified chapter-level book structure")
    parser.add_argument("--pages", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-chapter-characters", type=int, default=28_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    api_key = settings.text_api_key
    if not api_key:
        raise RuntimeError("Selected text provider API key is not configured")
    client = OpenAICompatibleClient(
        LLMConfig(
            base_url=settings.text_base_url,
            api_key=api_key,
            model=settings.text_model,
            timeout_seconds=120,
            max_retries=1,
            proxy_url=settings.llm_https_proxy,
        )
    )
    pages = load_normalized_pages(args.pages)
    structure = ChapterReconstructor(
        client,
        max_chapter_characters=args.max_chapter_characters,
    ).reconstruct_book(pages, args.title)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(structure.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "title": structure.title,
                "page_count": structure.source_page_count,
                "chapter_count": len(structure.chapters),
                "evidence_quotes": sum(len(chapter.evidence) for chapter in structure.chapters),
                "fallback": structure.used_fallback_chapter,
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"chapter generation failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise
