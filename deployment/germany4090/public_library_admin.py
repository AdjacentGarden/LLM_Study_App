#!/usr/bin/env python3
"""Local server administration of discovery PDFs; no client or model mocks."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys
import time

from adaptive_learning.public_materials import PublicMaterials

services = importlib.import_module("adaptive_learning.api.app")


def add_pdf(public: PublicMaterials, path: Path) -> dict:
    path = path.resolve(strict=True)
    if path.suffix.lower() != ".pdf" or not 0 < path.stat().st_size <= 100 * 1024 * 1024:
        raise ValueError("请选择非空且不超过 100 MB 的 PDF")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        first = stream.read(1024)
        if b"%PDF-" not in first:
            raise ValueError("文件内容不是 PDF")
        digest.update(first)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    checksum = digest.hexdigest()
    owner = "server-public"
    book_id = "book_manual_" + checksum[:32]
    directory = services.settings.data_dir / "books" / book_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "source.pdf"
    if not target.exists():
        shutil.copyfile(path, target)
    stored, duplicate = services.job_repository.register_owned_book(
        owner=owner, ownership_database_path=services.account_repository.repo.path,
        book_id=book_id, original_name=path.name, file_path=target, source_sha256=checksum)
    if stored.status == "uploaded":
        services.job_repository.enqueue(book_id=stored.book_id,
            output_dir=services.settings.data_dir / "books" / stored.book_id / "ocr",
            max_attempts=services.settings.ocr_max_attempts)
    # The resident workers produce real OCR, layout, structure and diagnostics.
    group = public.register(owner, title=path.stem, book_ids=[stored.book_id],
        group_id="manual_" + checksum[:32])
    # OCR can finish after durable publication registration. Submit preparation
    # once it is ready, rather than leaving a manually added PDF at OCR-only.
    deadline = time.monotonic() + 900
    while stored.status in {"uploaded", "queued", "running", "processing"} and time.monotonic() < deadline:
        time.sleep(2)
        stored = services.job_repository.get_book(stored.book_id)
    if stored.status in {"ocr_ready", "structured", "ready"}:
        group = public.register(owner, title=path.stem, book_ids=[stored.book_id], group_id=group["id"])
    receipt = {"source": str(path), "sha256": checksum, "book_id": stored.book_id,
               "group_id": group["id"], "status": group["status"], "reused": duplicate}
    (public.root / ("import-" + checksum[:32] + ".json")).write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add"); add.add_argument("pdf", type=Path)
    sub.add_parser("sync", help="Import PDFs from public-materials/incoming; refresh catalog")
    remove = sub.add_parser("remove"); remove.add_argument("group_id")
    sub.add_parser("verify", help="Verify every published file against its manifest digest")
    args = parser.parse_args()
    public = PublicMaterials(services)
    incoming = public.root / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    if args.command == "add":
        print(json.dumps(add_pdf(public, args.pdf), ensure_ascii=False))
    elif args.command == "sync":
        for path in sorted(incoming.glob("*.pdf")):
            print(json.dumps(add_pdf(public, path), ensure_ascii=False))
        print(json.dumps(public.list_ready(), ensure_ascii=False))
    elif args.command == "remove":
        row = public._row(args.group_id)
        public.withdraw(args.group_id, row["owner"])
        shutil.rmtree(public.catalog / args.group_id, ignore_errors=True)
        print(json.dumps({"group_id": args.group_id, "withdrawn": True}))
    elif args.command == "verify":
        count = 0
        for group in public.list_ready():
            manifest = public.manifest(group["id"])
            for book in manifest["books"]:
                for descriptor in [*book["files"], *book["figures"]]:
                    public.asset(group["id"], descriptor["sha256"])
                    count += 1
        print(json.dumps({"verified_files": count}))
    else:
        print(json.dumps(public.list_ready(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, PermissionError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
