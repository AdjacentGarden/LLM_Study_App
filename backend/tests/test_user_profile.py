import base64
import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_community import community as community


def avatar(color="purple"):
    buffer = io.BytesIO()
    Image.new("RGB", (480, 320), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def save(client, **kwargs):
    return client.post(
        "/api/user/profile",
        json={"nickname": "测试同学", "age": 20, "bio": "喜欢数学与阅读", "revision": 0, **kwargs},
    )


def test_defaults_and_profile_persist_across_clients_and_library_changes(community):
    a, b, repo, _ = community
    first = a.get("/api/user/profile")
    assert first.status_code == 200
    assert first.json() == {
        "nickname": "新同学",
        "age": None,
        "bio": "",
        "revision": 0,
        "avatar_url": None,
    }
    assert first.headers["cache-control"] == "private, no-store"
    result = save(a, nickname="  小林  ").json()
    assert result["nickname"] == "小林" and result["revision"] == 1
    a.post("/api/library/books/book-0/remove")
    assert a.get("/api/user/profile").json() == result
    assert b.get("/api/user/profile").json() == first.json()
    owner, _, _ = repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    with repo.connect() as db:
        row = db.execute(
            "SELECT nickname,age,bio FROM user_profiles WHERE owner=?", (owner,)
        ).fetchone()
        assert tuple(row) == ("小林", 20, "喜欢数学与阅读")
    with TestClient(a.app) as reloaded:
        reloaded.cookies.set("zhiwo_visitor", a.cookies.get("zhiwo_visitor"))
        assert reloaded.get("/api/user/profile").json() == result


@pytest.mark.parametrize(
    "changes",
    [
        {"nickname": "   "},
        {"nickname": "长" * 33},
        {"nickname": "hello\nworld"},
        {"age": 0},
        {"age": 121},
        {"age": 20.5},
        {"age": True},
        {"age": "20"},
        {"bio": "x" * 161},
        {"owner": "somebody-else"},
        {"revision": -1},
        {"reset_avatar": True, "avatar_data_url": "data:image/png;base64,AA=="},
    ],
)
def test_invalid_fields_do_not_persist(community, changes):
    a, *_ = community
    assert save(a, **changes).status_code == 422
    assert a.get("/api/user/profile").json()["revision"] == 0


def test_nullable_age_and_avatar_default_reset(community):
    a, *_ = community
    assert save(a).status_code == 200
    response = save(a, revision=1, age=None, bio="", reset_avatar=True)
    assert response.status_code == 200
    assert response.json()["age"] is None and response.json()["avatar_url"] is None


def test_avatar_normalized_private_and_persists_on_text_only_update(community):
    a, b, _, _ = community
    result = save(a, avatar_data_url=avatar()).json()
    assert result["avatar_url"] == "/api/user/profile/avatar?v=1"
    image = a.get(result["avatar_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "private, no-store"
    assert image.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(image.content)) as decoded:
        assert decoded.size == (256, 256) and decoded.mode == "RGB"
        assert not decoded.getexif()
    assert b.get(result["avatar_url"]).status_code == 404
    assert save(a, revision=1, nickname="新的昵称").status_code == 200
    assert a.get("/api/user/profile/avatar").content == image.content
    assert save(a, revision=2, reset_avatar=True).status_code == 200
    assert a.get("/api/user/profile/avatar").status_code == 404


@pytest.mark.parametrize(
    "image",
    [
        "data:image/svg+xml;base64,PHN2Zy8+",
        "data:image/png;base64,not-real-base64",
        "data:image/png;base64," + base64.b64encode(b"not an image").decode(),
        "https://example.com/picture.jpg",
        "data:image/png;base64," + base64.b64encode(b"a" * (2 * 1024 * 1024 + 1)).decode(),
    ],
)
def test_unsafe_avatar_rejected_without_partial_profile_write(community, image):
    a, *_ = community
    assert save(a, avatar_data_url=image).status_code == 422
    assert a.get("/api/user/profile").json()["nickname"] == "新同学"


def test_stale_write_conflict_and_origin_protection(community):
    a, *_ = community
    a.get("/api/user/profile")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda i: save(a, nickname=f"并发{i}"), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert a.get("/api/user/profile").json()["revision"] == 1
    response = a.post(
        "/api/user/profile",
        json={"nickname": "恶意覆盖", "revision": 1},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert a.get("/api/user/profile").json()["nickname"] != "恶意覆盖"


def test_photo_metadata_is_stripped_and_oversized_dimensions_rejected(community):
    a, *_ = community
    photo = Image.new("RGB", (120, 80), "orange")
    metadata = Image.Exif()
    metadata[315] = "PRIVATE CAMERA OWNER"
    metadata[274] = 6
    buffer = io.BytesIO()
    photo.save(buffer, format="JPEG", exif=metadata)
    data = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    assert save(a, avatar_data_url=data).status_code == 200
    raw = a.get("/api/user/profile/avatar").content
    assert b"PRIVATE CAMERA OWNER" not in raw
    assert not Image.open(io.BytesIO(raw)).getexif()
    large = io.BytesIO()
    Image.new("1", (4001, 4001)).save(large, format="PNG")
    response = save(
        a,
        revision=1,
        avatar_data_url="data:image/png;base64," + base64.b64encode(large.getvalue()).decode(),
    )
    assert response.status_code == 422
    assert a.get("/api/user/profile/avatar").content == raw
