from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException

from ..config import get_settings
from ..ingestion.chaptering import ChapterReconstructor
from ..llm.client import LLMConfig, OpenAICompatibleClient


class ChapterConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def build_chapter_reconstructor() -> ChapterReconstructor:
    settings = get_settings()
    if not settings.text_api_key:
        raise ChapterConfigurationError("Selected text provider API key is not configured")
    return ChapterReconstructor(
        OpenAICompatibleClient(
            LLMConfig(
                base_url=settings.text_base_url,
                api_key=settings.text_api_key,
                model=settings.text_model,
                timeout_seconds=120,
                max_retries=1,
                proxy_url=settings.llm_https_proxy,
            )
        )
    )


def require_chapter_reconstructor() -> ChapterReconstructor:
    try:
        return build_chapter_reconstructor()
    except ChapterConfigurationError as error:
        raise HTTPException(status_code=503, detail="章节分析服务尚未配置") from error
