from __future__ import annotations

from app.core.config import get_settings
from app.document.parsers.external import ExternalParser, ExternalParserConfig


class MarkerParser(ExternalParser):
    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(
            ExternalParserConfig(
                name="marker",
                command=settings.marker_command,
                endpoint=settings.marker_endpoint,
            )
        )
