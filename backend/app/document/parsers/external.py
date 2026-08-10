from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess

from app.document.parsers.base import ParsedDocument, ParserUnavailable, ParseRequest, parsed_page_from_page_result
from app.schemas.books import PageResult


@dataclass(frozen=True)
class ExternalParserConfig:
    name: str
    command: str | None = None
    endpoint: str | None = None


class ExternalParser:
    def __init__(self, config: ExternalParserConfig) -> None:
        self.name = config.name
        self._command = config.command
        self._endpoint = config.endpoint

    def parse(self, request: ParseRequest) -> ParsedDocument:
        if self._command:
            self._run_command(request)
        elif self._endpoint:
            raise ParserUnavailable(self.name, "HTTP parser endpoint is configured but not implemented in the local runner")
        else:
            raise ParserUnavailable(self.name, "no endpoint or command configured")

        pages_path = request.artifact_path / "pages.json"
        if not pages_path.exists():
            raise ParserUnavailable(self.name, "parser did not produce pages.json")
        pages = [PageResult.model_validate(item) for item in json.loads(pages_path.read_text(encoding="utf-8"))]
        return ParsedDocument(
            book_id=request.book_id,
            parser_name=self.name,
            pages=[parsed_page_from_page_result(page, self.name) for page in pages],
            scan=request.scan,
            metadata={"external_command": self._command, "external_endpoint": self._endpoint},
        )

    def _run_command(self, request: ParseRequest) -> None:
        executable = self._command.split()[0]
        if shutil.which(executable) is None:
            raise ParserUnavailable(self.name, f"command not found: {executable}")
        completed = subprocess.run(
            [
                *self._command.split(),
                "--input",
                str(request.file_path),
                "--output",
                str(request.artifact_path),
                "--book-id",
                request.book_id,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if completed.returncode != 0:
            reason = completed.stderr.strip() or completed.stdout.strip() or f"exit_code={completed.returncode}"
            raise ParserUnavailable(self.name, reason[:500])
