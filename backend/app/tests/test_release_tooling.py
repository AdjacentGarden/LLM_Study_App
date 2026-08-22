from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str, relative_path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPOSITORY_ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "release-test@example.invalid")
    _git(repository, "config", "user.name", "Release Test")
    (repository / ".gitignore").write_text("frontend/dist/\n", encoding="utf-8")
    (repository / "README.md").write_text("candidate\n", encoding="utf-8")
    _git(repository, "add", ".gitignore", "README.md")
    _git(repository, "commit", "--quiet", "-m", "candidate")
    return repository


def test_release_manifest_rejects_dirty_and_unsigned_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _load_script("release_manifest_test", "deploy/release-manifest.py")
    repository = _repository(tmp_path)
    monkeypatch.setattr(manifest, "REPOSITORY_ROOT", repository)

    (repository / "README.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(manifest.ManifestError, match="not clean"):
        manifest.release_identity()

    _git(repository, "restore", "README.md")
    _git(repository, "tag", "v0.0.0-test")
    with pytest.raises(manifest.ManifestError, match="not signed"):
        manifest.release_identity()


def test_release_manifest_hashes_tracked_source_and_complete_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _load_script("release_manifest_artifacts_test", "deploy/release-manifest.py")
    repository = _repository(tmp_path)
    dist = repository / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    for relative, content in {
        "index.html": "<main>release</main>",
        "manifest.webmanifest": "{}",
        "sw.js": "self.addEventListener('fetch', () => {});",
        "assets/app.js": "console.log('release');",
    }.items():
        path = dist / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(manifest, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(manifest, "release_identity", lambda: ("commit", "tree", "v1.0.0"))

    result = manifest.build_manifest()

    assert result["source"] == {"commit": "commit", "tree": "tree", "tag": "v1.0.0"}
    assert set(result["tracked_files"]) == {".gitignore", "README.md"}
    assert set(result["frontend_dist"]) == {
        "assets/app.js",
        "index.html",
        "manifest.webmanifest",
        "sw.js",
    }
    with pytest.raises(manifest.ManifestError, match="outside"):
        manifest.ensure_external_output(repository / "release-manifest.json")


@pytest.mark.parametrize(
    "url",
    [
        "http://bookcourse.example.com",
        "https://user:secret@bookcourse.example.com",
        "https://bookcourse.example.com/path",
    ],
)
def test_production_pdf_canary_requires_a_credential_free_https_origin(url: str) -> None:
    canary = _load_script("production_pdf_canary_test", "deploy/production-pdf-canary.py")
    with pytest.raises(canary.CanaryError):
        canary.PublicClient(url, Path("cookie.txt"))


def test_production_pdf_canary_rejects_cross_origin_upload_url() -> None:
    canary = _load_script("production_pdf_canary_origin_test", "deploy/production-pdf-canary.py")
    client = canary.PublicClient("https://bookcourse.example.com", Path("cookie.txt"))
    with pytest.raises(canary.CanaryError, match="cross-origin"):
        client.request("POST", "https://attacker.example/upload")


def test_target_collector_binds_manifest_and_live_service_to_deployed_candidate() -> None:
    collector = (REPOSITORY_ROOT / "deploy" / "collect-target-evidence.sh").read_text(encoding="utf-8")
    assert 'if [[ "${repo_root}" != "/srv/bookcourse" ]]' in collector
    assert '[[ "${service_workdir}" == "/srv/bookcourse/backend" ]]' in collector
    assert '[[ "$(readlink -e "/proc/${service_pid}/cwd")" == "/srv/bookcourse/backend" ]]' in collector
