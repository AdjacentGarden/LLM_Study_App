"""Indexed teaching media never bypasses the owner's shelf or local assets root."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from adaptive_learning.api.imported_assets_routes import imported_assets_router
from adaptive_learning.community import CommunityRepository


def _api(tmp_path: Path) -> tuple[TestClient, CommunityRepository, Path]:
    repo = CommunityRepository(tmp_path / "state" / "community.sqlite3")
    repo.register_asset({"book_id": "book_biology_2", "title": "生物教材"}, "biology-sha", None)
    repo.add_book("alice", "book_biology_2")
    app = FastAPI()

    def visitor(request: Request) -> str:
        return request.cookies.get("test_owner") or "guest"

    app.include_router(imported_assets_router(repo, tmp_path, visitor), prefix="/api")
    imported = tmp_path / "books" / "book_biology_2" / "imported"
    (imported / "assets" / "textbook").mkdir(parents=True)
    return TestClient(app), repo, imported


def _index(imported: Path, rows: dict[str, dict[str, object]]) -> None:
    (imported / "assets.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_indexed_image_and_video_are_private_local_media(tmp_path: Path) -> None:
    client, _repo, imported = _api(tmp_path)
    (imported / "assets" / "textbook" / "cell.webp").write_bytes(b"webp-image")
    (imported / "assets" / "textbook" / "division.mp4").write_bytes(b"local-video")
    _index(
        imported,
        {
            "cell-image": {
                "path": "assets/textbook/cell.webp",
                "kind": "image",
                "caption": "细胞",
                "source_kind": "textbook",
                "page_number": 12,
            },
            "division-video": {
                "path": "assets/textbook/division.mp4",
                "kind": "video",
                "caption": "分裂",
                "source_kind": "lesson",
            },
        },
    )
    client.cookies.set("test_owner", "alice")
    image = client.get("/api/books/book_biology_2/imported-assets/cell-image")
    assert image.status_code == 200
    assert image.content == b"webp-image"
    assert image.headers["content-type"] == "image/webp"
    assert image.headers["cache-control"] == "private, no-store"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["cross-origin-resource-policy"] == "same-origin"
    video = client.get("/api/books/book_biology_2/imported-assets/division-video")
    assert video.status_code == 200
    assert video.content == b"local-video"
    assert video.headers["content-type"] == "video/mp4"
    partial = client.get(
        "/api/books/book_biology_2/imported-assets/division-video",
        headers={"Range": "bytes=0-4"},
    )
    assert partial.status_code == 206
    assert partial.content == b"local"


def test_library_ownership_is_checked_on_every_asset_request(tmp_path: Path) -> None:
    client, repo, imported = _api(tmp_path)
    (imported / "assets" / "textbook" / "cell.webp").write_bytes(b"private")
    _index(imported, {"cell-image": {"path": "assets/textbook/cell.webp", "kind": "image"}})
    url = "/api/books/book_biology_2/imported-assets/cell-image"
    assert client.get(url).status_code == 403
    client.cookies.set("test_owner", "bob")
    assert client.get(url).status_code == 403
    client.cookies.set("test_owner", "alice")
    assert client.get(url).status_code == 200
    repo.remove_book("alice", "book_biology_2")
    assert client.get(url).status_code == 403


def test_mapping_cannot_escape_assets_or_serve_unindexed_or_active_content(tmp_path: Path) -> None:
    client, _repo, imported = _api(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"never-serve-this")
    (imported / "assets" / "textbook" / "active.html").write_text("<script>bad()</script>")
    _index(
        imported,
        {
            "traversal": {"path": "assets/../../../../outside.png", "kind": "image"},
            "absolute": {"path": outside.as_posix(), "kind": "image"},
            "external": {"path": "https://example.com/cell.png", "kind": "image"},
            "backslash": {"path": "assets\\textbook\\cell.webp", "kind": "image"},
            "active": {"path": "assets/textbook/active.html", "kind": "image"},
            "wrong-kind": {"path": "assets/textbook/cell.webp", "kind": "video"},
            "missing": {"path": "assets/textbook/absent.webp", "kind": "image"},
        },
    )
    client.cookies.set("test_owner", "alice")
    for asset_id in (
        "traversal",
        "absolute",
        "external",
        "backslash",
        "active",
        "wrong-kind",
        "missing",
        "unknown",
    ):
        response = client.get(f"/api/books/book_biology_2/imported-assets/{asset_id}")
        assert response.status_code == 404, (asset_id, response.text)
        assert b"never-serve-this" not in response.content


def test_supplementary_reading_exposes_only_unverified_missing_source_text(tmp_path: Path) -> None:
    client, _repo, imported = _api(tmp_path)
    (imported / "teaching.json").write_text(
        json.dumps(
            {
                "lessons": [
                    {
                        "book_id": "book_biology_2",
                        "chapter_id": "c1s1",
                        "page_start": 0,
                        "page_end": 0,
                        "status": "supplemental_draft",
                        "title": "孟德尔的实验",
                        "summary": "辅助理解遗传实验。",
                        "blocks": [
                            {
                                "title": "观察",
                                "content": "先控制亲本，再观察后代。",
                                "answer": "private-answer",
                            }
                        ],
                        "flashcards": [{"back": "private-flashcard"}],
                        "quiz": [{"expected_answer": "private-answer"}],
                    },
                    {
                        "book_id": "book_biology_2",
                        "chapter_id": "c2s1",
                        "page_start": 13,
                        "status": "source_aligned",
                        "title": "已核验课程",
                        "blocks": [{"title": "不应读取", "content": "source-backed"}],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    url = "/api/books/book_biology_2/supplementary-lessons/c1s1"
    assert client.get(url).status_code == 403
    client.cookies.set("test_owner", "alice")
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "book_id": "book_biology_2",
        "chapter_id": "c1s1",
        "title": "孟德尔的实验",
        "summary": "辅助理解遗传实验。",
        "blocks": [{"title": "观察", "content": "先控制亲本，再观察后代。"}],
        "cards": [],
        "questions": [],
        "notice": "AI补充内容；源PDF缺少本章正文，尚未核验",
    }
    assert "private-answer" not in response.text
    assert "private-flashcard" not in response.text
    assert client.get("/api/books/book_biology_2/supplementary-lessons/c2s1").status_code == 404
    assert client.get("/api/books/book_biology_2/supplementary-lessons/unknown").status_code == 404


def test_symlinked_pack_file_cannot_read_outside_the_imported_directory(tmp_path: Path) -> None:
    client, _repo, imported = _api(tmp_path)
    outside = tmp_path / "private-teaching.json"
    outside.write_text(
        json.dumps(
            {
                "lessons": [
                    {
                        "book_id": "book_biology_2",
                        "chapter_id": "c1s1",
                        "page_start": 0,
                        "status": "supplemental_draft",
                        "title": "private",
                        "summary": "secret",
                        "blocks": [{"title": "secret", "content": "private text"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    try:
        (imported / "teaching.json").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks is not permitted in this environment")
    client.cookies.set("test_owner", "alice")
    response = client.get("/api/books/book_biology_2/supplementary-lessons/c1s1")
    assert response.status_code == 404
    assert "private text" not in response.text


def _supplementary_pack(imported: Path) -> dict[str, object]:
    pack: dict[str, object] = {
        "schema_version": 1,
        "source_book_id": "book_biology_2",
        "lessons": [
            {
                "book_id": "book_biology_2",
                "chapter_id": "c1s1",
                "lesson_id": "lesson_mendel",
                "page_start": 0,
                "page_end": 0,
                "status": "supplemental_draft",
                "title": "孟德尔的实验",
                "summary": "AI 补充；原书正文缺失。",
                "blocks": [{"title": "观察", "content": "先控制亲本，再观察后代。"}],
            }
        ],
        "flashcards": [
            {
                "book_id": "book_biology_2",
                "chapter_id": "c1s1",
                "lesson_id": "lesson_mendel",
                "card_id": "fc_c1s1_01",
                "front": "为什么选择豌豆？",
                "back": "豌豆能自花传粉，性状容易辨认。",
                "source_kind": "ai_supplement",
                "source_quote": None,
                "page_start": 0,
                "page_end": 0,
                "due": "today",
                "mastery": 42,
            }
        ],
        "quiz": [
            {
                "book_id": "book_biology_2",
                "chapter_id": "c1s1",
                "lesson_id": "lesson_mendel",
                "question_id": "quiz_c1s1_01",
                "prompt": "F1 中看不到隐性性状意味着遗传因子消失了吗？",
                "choices": ["是", "否"],
                "answer": "否",
                "explanation": "隐性遗传因子仍可能存在于杂合子中。",
                "instruction": "选择一个选项",
                "question_type": "choice",
                "source_kind": "ai_supplement",
                "source_quote": None,
                "page_start": 0,
                "page_end": 0,
            },
            {
                "book_id": "book_biology_2",
                "chapter_id": "c1s1",
                "lesson_id": "lesson_mendel",
                "question_id": "quiz_c1s1_02",
                "prompt": "简述控制变量的作用。",
                "choices": [],
                "answer": "让亲本差异可比较。",
                "explanation": "可从稳定亲本与可计数后代解释。",
                "instruction": "用自己的话回答",
                "question_type": "short-answer",
                "source_kind": "ai_supplement",
                "source_quote": None,
                "page_start": 0,
                "page_end": 0,
            },
        ],
    }
    (imported / "teaching.json").write_text(
        json.dumps(pack, ensure_ascii=False), encoding="utf-8"
    )
    return pack


def test_supplementary_cards_and_questions_reveal_only_after_owned_interaction(
    tmp_path: Path,
) -> None:
    client, repo, imported = _api(tmp_path)
    _supplementary_pack(imported)
    base = "/api/books/book_biology_2/supplementary-lessons/c1s1"
    reveal = f"{base}/cards/fc_c1s1_01/reveal"
    check = f"{base}/check"
    assert client.get(base).status_code == 403
    assert client.post(reveal).status_code == 403
    assert client.post(check, json={"question_id": "quiz_c1s1_01", "answer": "否"}).status_code == 403
    client.cookies.set("test_owner", "bob")
    assert client.get(base).status_code == 403
    assert client.post(reveal).status_code == 403
    assert client.post(check, json={"question_id": "quiz_c1s1_01", "answer": "否"}).status_code == 403

    client.cookies.set("test_owner", "alice")
    listing = client.get(base)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "private, no-store"
    assert listing.json()["cards"] == [{"id": "fc_c1s1_01", "front": "为什么选择豌豆？"}]
    assert listing.json()["questions"] == [
        {
            "id": "quiz_c1s1_01",
            "prompt": "F1 中看不到隐性性状意味着遗传因子消失了吗？",
            "choices": ["是", "否"],
            "instruction": "选择一个选项",
            "question_type": "choice",
        },
        {
            "id": "quiz_c1s1_02",
            "prompt": "简述控制变量的作用。",
            "choices": [],
            "instruction": "用自己的话回答",
            "question_type": "short-answer",
        },
    ]
    for private in ("豌豆能自花传粉", "让亲本差异可比较", "隐性遗传因子仍可能", '"mastery"', '"due"'):
        assert private not in listing.text

    revealed = client.post(reveal)
    assert revealed.status_code == 200
    assert revealed.headers["cache-control"] == "private, no-store"
    assert revealed.json() == {"back": "豌豆能自花传粉，性状容易辨认。"}
    incorrect = client.post(check, json={"question_id": "quiz_c1s1_01", "answer": "是"})
    assert incorrect.json() == {
        "correct": False,
        "answer": "否",
        "explanation": "隐性遗传因子仍可能存在于杂合子中。",
    }
    correct = client.post(check, json={"question_id": "quiz_c1s1_01", "answer": "否"})
    assert correct.status_code == 200
    assert correct.headers["cache-control"] == "private, no-store"
    assert correct.json()["correct"] is True
    short = client.post(check, json={"question_id": "quiz_c1s1_02", "answer": "我自己的表述"})
    assert short.status_code == 200
    assert short.json() == {
        "correct": None,
        "answer": "让亲本差异可比较。",
        "explanation": "可从稳定亲本与可计数后代解释。",
    }
    assert client.post(check, json={"question_id": "quiz_c1s1_01", "answer": "第三个选项"}).status_code == 422
    assert client.post(check, json={"question_id": "quiz_other", "answer": "否"}).status_code == 404
    assert client.post(f"{base}/cards/fc_other/reveal").status_code == 404
    assert client.get("/api/books/book_biology_2/supplementary-lessons/c2s1").status_code == 404
    assert not (tmp_path / "state" / "assessments.sqlite3").exists()

    repo.remove_book("alice", "book_biology_2")
    assert client.get(base).status_code == 403
    assert client.post(reveal).status_code == 403
    assert client.post(check, json={"question_id": "quiz_c1s1_01", "answer": "否"}).status_code == 403


@pytest.mark.parametrize(
    "mutation", ["wrong_kind", "duplicate_id", "duplicate_question", "fake_page", "fake_quote"]
)
def test_supplementary_tools_reject_invalid_or_non_ai_source_data(
    tmp_path: Path, mutation: str
) -> None:
    client, _repo, imported = _api(tmp_path)
    pack = _supplementary_pack(imported)
    cards = pack["flashcards"]
    assert isinstance(cards, list)
    if mutation == "wrong_kind":
        cards[0]["source_kind"] = "textbook"
    elif mutation == "duplicate_id":
        cards.append(dict(cards[0]))
    elif mutation == "duplicate_question":
        questions = pack["quiz"]
        assert isinstance(questions, list)
        questions.append(dict(questions[0]))
    elif mutation == "fake_page":
        cards[0]["page_start"] = 2
    else:
        cards[0]["source_quote"] = "伪造原文"
    (imported / "teaching.json").write_text(
        json.dumps(pack, ensure_ascii=False), encoding="utf-8"
    )
    client.cookies.set("test_owner", "alice")
    url = "/api/books/book_biology_2/supplementary-lessons/c1s1"
    listing = client.get(url)
    assert listing.status_code == 503
    assert "豌豆能自花传粉" not in listing.text
    reveal = client.post(f"{url}/cards/fc_c1s1_01/reveal")
    assert reveal.status_code == 503
    assert "豌豆能自花传粉" not in reveal.text
