#!/usr/bin/env python3
"""Create or verify a deterministic manifest for an immutable release tag."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "bookcourse.release-manifest/v1"
REQUIRED_FRONTEND_ARTIFACTS = {
    "index.html",
    "manifest.webmanifest",
    "sw.js",
}


class ManifestError(RuntimeError):
    pass


def git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ManifestError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip()


def require_clean_repository() -> None:
    dirty = git("status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        preview = "\n".join(dirty.splitlines()[:20])
        raise ManifestError(f"release repository is not clean:\n{preview}")


def release_identity() -> tuple[str, str, str]:
    require_clean_repository()
    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    tags = [line for line in git("tag", "--points-at", "HEAD").splitlines() if line]
    if len(tags) != 1:
        raise ManifestError(
            f"release HEAD must have exactly one immutable tag; found {len(tags)}"
        )
    signature = subprocess.run(
        ["git", "tag", "--verify", tags[0]],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if signature.returncode != 0:
        raise ManifestError(f"release tag is not signed or its signature is invalid: {tags[0]}")
    return commit, tree, tags[0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_file(path: Path) -> dict[str, int | str]:
    if path.is_symlink() or not path.is_file():
        raise ManifestError(f"release input must be a regular file: {path}")
    return {"sha256": sha256(path), "size": path.stat().st_size}


def tracked_files() -> dict[str, dict[str, int | str]]:
    entries: dict[str, dict[str, int | str]] = {}
    for relative in git("ls-files").splitlines():
        path = REPOSITORY_ROOT / relative
        entries[relative.replace("\\", "/")] = describe_file(path)
    if not entries:
        raise ManifestError("release repository has no tracked files")
    return entries


def frontend_artifacts() -> dict[str, dict[str, int | str]]:
    dist = REPOSITORY_ROOT / "frontend" / "dist"
    if not dist.is_dir():
        raise ManifestError("frontend/dist is missing; build the production frontend first")
    entries = {
        path.relative_to(dist).as_posix(): describe_file(path)
        for path in sorted(dist.rglob("*"))
        if path.is_file()
    }
    missing = sorted(REQUIRED_FRONTEND_ARTIFACTS - entries.keys())
    if missing:
        raise ManifestError(f"frontend/dist is incomplete: missing {', '.join(missing)}")
    if not any(name.startswith("assets/") for name in entries):
        raise ManifestError("frontend/dist contains no bundled assets")
    return entries


def build_manifest() -> dict[str, object]:
    commit, tree, tag = release_identity()
    return {
        "schema": SCHEMA,
        "source": {"commit": commit, "tag": tag, "tree": tree},
        "tracked_files": tracked_files(),
        "frontend_dist": frontend_artifacts(),
    }


def ensure_external_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return
    raise ManifestError("write release evidence outside the repository working tree")


def create(path: Path) -> None:
    ensure_external_output(path)
    manifest = build_manifest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"release manifest created: {path}")


def verify_entries(
    expected: object,
    actual: dict[str, dict[str, int | str]],
    label: str,
) -> None:
    if not isinstance(expected, dict):
        raise ManifestError(f"manifest {label} is not an object")
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))[:10]
        extra = sorted(set(actual) - set(expected))[:10]
        raise ManifestError(f"{label} file set changed; missing={missing}, extra={extra}")
    for name, description in actual.items():
        if expected[name] != description:
            raise ManifestError(f"{label} digest or size changed: {name}")


def verify(path: Path) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ManifestError("unsupported release manifest schema")
    commit, tree, tag = release_identity()
    expected_source = {"commit": commit, "tag": tag, "tree": tree}
    if manifest.get("source") != expected_source:
        raise ManifestError(
            f"release identity changed; expected {expected_source}, got {manifest.get('source')}"
        )
    verify_entries(manifest.get("tracked_files"), tracked_files(), "tracked_files")
    verify_entries(manifest.get("frontend_dist"), frontend_artifacts(), "frontend_dist")
    print("release manifest verification: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create", "verify"))
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "create":
            create(arguments.manifest)
        else:
            verify(arguments.manifest)
    except (ManifestError, OSError, json.JSONDecodeError) as error:
        print(f"release manifest: FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
