"""Native course covers are fresh metadata, independent of teaching content."""

import os

from PIL import Image
from test_native_api import native as native
from test_native_api import uploaded, workspace

from adaptive_learning import native_catalog


def resource_from(response):
    assert response.status_code == 200, response.text
    return response.json()["workspaces"][0]["resources"][0]


def test_cover_appears_in_existing_workspace_without_changing_content_version(native):
    client, other, root = native
    book = uploaded(client)
    resource = workspace(client, book)
    path = f"/api/workspaces/{resource['workspace_id']}/resources/{resource['id']}"
    before = resource_from(client.get("/api/native/bootstrap?mode=metadata"))
    assert before["cover_url"] is None

    covers = root / "covers"
    covers.mkdir(exist_ok=True)
    image = covers / f"{book}.jpg"
    Image.new("RGB", (80, 120), "purple").save(image)

    light = resource_from(client.get("/api/native/bootstrap?mode=metadata"))
    full = resource_from(client.get("/api/native/bootstrap"))
    direct = client.get(path).json()
    assert light["cover_url"].startswith(f"/api/books/{book}/cover?v=")
    assert full["cover_url"] == direct["cover_url"] == light["cover_url"]
    assert light["content_key"] == before["content_key"]
    assert other.get("/api/native/bootstrap?mode=metadata").json()["workspaces"] == []

    # Updating the cover refreshes image caches, without redownloading the course.
    previous = image.stat()
    os.utime(image, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
    newer = resource_from(client.get("/api/native/bootstrap?mode=metadata"))
    assert newer["cover_url"] != light["cover_url"]
    assert newer["content_key"] == light["content_key"]

    claimed = client.post(f"/api/library/books/{book}/claim")
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["cover_url"] == newer["cover_url"]
    assert client.get("/api/library").json()[0]["cover_url"] == newer["cover_url"]
    assert client.get("/api/native/bootstrap?mode=metadata").json()["library"][0]["cover_url"] == newer["cover_url"]


def test_builtin_course_keeps_shipped_cover_when_no_original_pdf_exists(native):
    client, _, _ = native
    response = client.post(
        f"/api/native/catalog/{native_catalog.CATALOG_ID}/import",
        json={"id": "workspace_cover_builtin", "name": "生物"},
    )
    assert response.status_code == 200, response.text
    expected = native_catalog.snapshot()["source"]["summary"]["cover_url"]
    assert response.json()["resources"][0]["cover_url"] == expected
    for suffix in ("", "?mode=metadata"):
        assert resource_from(client.get("/api/native/bootstrap" + suffix))["cover_url"] == expected
