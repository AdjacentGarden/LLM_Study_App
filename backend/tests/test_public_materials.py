"""Shared group publication/download without private workspace or model calls."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pymupdf
import pytest

from adaptive_learning.api.public_material_routes import public_material_router
from adaptive_learning.assessment.item_generation import diagnostic_bank_key
from adaptive_learning.assessment.models import DiagnosticItem, ResponseType
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft
from adaptive_learning.native import NativeRepository
from adaptive_learning.public_materials import PublicMaterials


def make_services(root: Path):
    community = CommunityRepository(root / "state/community.sqlite3")
    jobs = SQLiteOCRJobRepository(root / "state/ocr_jobs.sqlite3")
    assessments = SQLiteAssessmentRepository(root / "state/assessments.sqlite3")
    native = NativeRepository(root / "state/native.sqlite3")
    return SimpleNamespace(settings=SimpleNamespace(data_dir=root, published_book_ids=[], ocr_backend="paddle-gpu"),
        account_repository=SimpleNamespace(repo=community, session_owner=lambda token: {"token-a":"alice","token-b":"bob"}.get(token)),
        job_repository=jobs, assessment_repository=assessments, native_repository=native,
        diagnostic_fingerprint=lambda structure: "source:"+structure.title)


def add_book(services, book: str, *, owner="alice", ready=True):
    directory = services.settings.data_dir / "books" / book
    directory.mkdir(parents=True)
    source = directory / "source.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((40, 60), "Public textbook passage")
    source.write_bytes(document.tobytes())
    document.close()
    services.job_repository.register_book(book_id=book, original_name=book+".pdf", file_path=source,
                                          source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    services.account_repository.repo.bind_upload(owner, book)
    structure = BookStructure(title="教材"+book, summary="公共概要", source_page_count=1,
        chapters=[ChapterDraft(chapter_id="chapter-"+book, order=1,title="第一章",start_page=1,end_page=1,
                   summary="共享章节",knowledge_points=["向量"],source_block_ids=["block-one"],
                   source_coverage={"private_path":"DO_NOT_PUBLISH"})])
    services.job_repository.save_structure(book, structure)
    normalized = directory / "ocr/normalized"
    normalized.mkdir(parents=True)
    (normalized / "pages.jsonl").write_text(json.dumps({"page_number":1,"text":"向量具有大小和方向。",
        "raw_path":"DO_NOT_PUBLISH", "blocks":[{"block_id":"block-one","text":"向量具有大小和方向。",
            "raw":{"img_path":"DO_NOT_PUBLISH"}}]},ensure_ascii=False)+"\n")
    if ready:
        services.assessment_repository.save_bank(book_id=diagnostic_bank_key(book),
            structure_fingerprint=services.diagnostic_fingerprint(structure),items=[DiagnosticItem(
                item_id="item-"+book, chapter_id="chapter-"+book,knowledge_point_ids=["kp"],prompt="向量有什么属性？",
                response_type=ResponseType.SINGLE_CHOICE,options=["大小和方向","只有大小"],correct_option_ids=["0"],
                expected_answer="大小和方向",rubric=["同时包含大小和方向"],source_pages=[1])])
    return structure


@pytest.fixture
def material_services(tmp_path):
    return make_services(tmp_path)


def client(services):
    app = FastAPI()
    app.include_router(public_material_router(services))
    value = TestClient(app)
    value.cookies.set("zhiwo_account", "token-a")
    return value


def test_multi_book_group_downloads_all_public_files_and_never_private_workspaces(material_services):
    services=material_services
    add_book(services,"one");add_book(services,"two")
    with client(services) as api:
        response=api.post("/api/public/library/course-groups",json={"title":"两本教材课程","book_ids":["one","two"]})
        assert response.status_code==200,response.text
        group=response.json(); assert group["status"]=="ready"
        manifest=api.get(group["manifest_url"]).json()
        assert [book["book_id"] for book in manifest["books"]]==["one","two"]
        assert len(manifest["version"])==64
        for book in manifest["books"]:
            assert {file["kind"] for file in book["files"]}=={"pdf","ocr_pages","structure","diagnostics","grading","figure_manifest"}
            for file in book["files"]:
                fetched=api.get(file["download_url"])
                assert fetched.status_code==200
                assert len(fetched.content)==file["bytes"]
                assert hashlib.sha256(fetched.content).hexdigest()==file["sha256"]
                assert b"DO_NOT_PUBLISH" not in fetched.content
                if file["kind"]=="diagnostics":assert "correct_option_ids" not in fetched.json()["items"][0]
                if file["kind"]=="grading":assert fetched.json()["items"][0]["correct_option_ids"]==["0"]
                if file["kind"]=="figure_manifest":assert fetched.json()["extraction"]["status"]=="not_extracted"
        assert len(api.get("/api/public/library/course-groups").json()["items"])==1
        api.cookies.set("zhiwo_account","token-b")
        assert api.get(group["manifest_url"]).status_code==200
    assert services.native_repository.workspaces("alice")==[]
    assert services.native_repository.workspaces("bob")==[]


def test_discovery_catalog_has_complete_named_server_files_and_manual_withdrawal(material_services):
    import shutil
    services = material_services
    add_book(services, "one")
    public = PublicMaterials(services)
    group = public.register("alice", title="统一目录教材", book_ids=["one"])
    manifest = public.manifest(group["id"])
    directory = public.root / "catalog" / group["id"]
    assert json.loads((directory / "manifest.json").read_text()) == manifest
    files = directory / "one"
    assert (files / "source.pdf").read_bytes().startswith(b"%PDF-")
    assert json.loads((files / "ocr-pages.jsonl").read_text())["page_number"] == 1
    assert (files / "structure.json").is_file()
    assert (files / "diagnostics.json").is_file()
    shutil.rmtree(directory)
    assert public.list_ready() == []
    with pytest.raises(KeyError):
        public.manifest(group["id"])
    # A read never recreates deleted discovery data or deletes uploader storage.
    assert not directory.exists()
    assert services.job_repository.get_book("one").file_path.is_file()
    public.register("alice", title="重新明确发布", book_ids=["one"], group_id=group["id"])
    assert public.list_ready()[0]["id"] == group["id"]


def test_unshared_historical_upload_never_auto_appears_and_private_fields_rejected(material_services):
    add_book(material_services,"private-old")
    with client(material_services) as api:
        assert api.get("/api/public/library/course-groups").json()["items"]==[]
        for extra in [{"preferences":{"goal":["private"]}},{"lecture":"private"}]:
            assert api.post("/api/public/library/course-groups",json={"title":"课程","book_ids":["private-old"],**extra}).status_code==422


def test_group_waits_for_every_book_then_automatically_publishes(material_services):
    services=material_services
    add_book(services,"one");structure=add_book(services,"two",ready=False)
    with client(services) as api:
        group=api.post("/api/public/library/course-groups",json={"title":"全部教材","book_ids":["one","two"]}).json()
        assert group["status"]=="preparing"
        assert api.get(group["manifest_url"]).status_code==409
        assert api.get("/api/public/library/course-groups").json()["items"]==[]
        services.assessment_repository.save_bank(book_id=diagnostic_bank_key("two"),
            structure_fingerprint=services.diagnostic_fingerprint(structure), items=[DiagnosticItem(item_id="two",
                chapter_id="chapter-two",knowledge_point_ids=["kp"],prompt="问题",response_type="short_answer",
                expected_answer="答案",rubric=["评分标准"])])
        PublicMaterials(services).refresh_for_book("two")
        assert api.get("/api/public/library/course-groups").json()["items"][0]["id"]==group["id"]
        grading=next(f for f in api.get(group["manifest_url"]).json()["books"][1]["files"] if f["kind"]=="grading")
        assert api.get(grading["download_url"]).json()["items"][0]["rubric"]==["评分标准"]


def test_authenticated_registration_idempotence_owner_and_download_allowlist(material_services):
    add_book(material_services,"one");add_book(material_services,"other",owner="bob")
    with client(material_services) as api:
        data={"id":"stable_group_one","title":"公共教材","book_ids":["one"]}
        a=api.post("/api/public/library/course-groups",json=data).json()
        b=api.post("/api/public/library/course-groups",json=data).json()
        assert a==b
        assert api.post("/api/public/library/course-groups",json={**data,"book_ids":["other"]}).status_code==403
        assert api.post("/api/public/library/course-groups",json={**data,"book_ids":["one","one"]}).status_code==422
        assert api.post("/api/public/library/course-groups",json=data,headers={"Origin":"https://untrusted.invalid"}).status_code==403
        assert api.get(f'/api/public/library/course-groups/{a["id"]}/assets/'+"0"*64).status_code==404
        file=api.get(a["manifest_url"]).json()["books"][0]["files"][0]
        api.cookies.set("zhiwo_account","token-b")
        assert api.delete(f'/api/public/library/course-groups/{a["id"]}').status_code==403
        api.cookies.set("zhiwo_account","token-a")
        assert api.delete(f'/api/public/library/course-groups/{a["id"]}').status_code==200
        assert api.get(file["download_url"]).status_code==404
        api.cookies.clear()
        assert api.get("/api/public/library/course-groups").status_code==401


def test_whole_book_figure_failure_does_not_block_publication(material_services):
    add_book(material_services,"one")
    normalized=material_services.settings.data_dir/"books/one/ocr/normalized"
    source=normalized.parent.parent/"source.pdf"
    (normalized/"figure_manifest.json").write_text(json.dumps({"status":"unavailable","reason":"layout_not_configured",
        "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),"figures":[]}))
    group=PublicMaterials(material_services).register("alice",title="课程",book_ids=["one"])
    assert group["status"]=="ready"
    book=PublicMaterials(material_services).manifest(group["id"])["books"][0]
    assert book["figures"]==[]
    descriptor=next(file for file in book["files"] if file["kind"]=="figure_manifest")
    path,_=PublicMaterials(material_services).asset(group["id"],descriptor["sha256"])
    assert json.loads(path.read_text())["extraction"]=={"status":"unavailable","reason":"layout_not_configured"}


def test_legacy_single_book_share_is_discovered_and_withdrawal_revokes_download(material_services):
    add_book(material_services,"one")
    repo=material_services.account_repository.repo
    repo.register_asset({"book_id":"one","title":"课程","status":"ready"},"hash-one",None)
    repo.add_book("alice","one")
    post,_=repo.publish("alice","book","one","历史公开教材","",{})
    service=PublicMaterials(material_services)
    group=service.list_ready()[0]
    assert group["id"]=="community_post_"+post
    descriptor=service.manifest(group["id"])["books"][0]["files"][0]
    with repo.connect() as db:db.execute("UPDATE posts SET active=0 WHERE id=?",(post,))
    with pytest.raises(KeyError):service.asset(group["id"],descriptor["sha256"])


def test_only_manifest_listed_independent_figures_are_downloadable(material_services,tmp_path):
    from PIL import Image
    add_book(material_services,"one")
    normalized=material_services.settings.data_dir/"books/one/ocr/normalized"
    figures=normalized/"figures";figures.mkdir()
    image=figures/"one.png";Image.new("RGB",(100,100),"white").save(image)
    digest=hashlib.sha256(image.read_bytes()).hexdigest()
    outside=tmp_path/"not-a-figure.png";outside.write_bytes(image.read_bytes())
    source=normalized.parent.parent/"source.pdf"
    good={"asset_id":"figure_"+digest,"file":"figures/one.png","page_number":1,"bbox":[10,10,100,100],
          "caption":"","label":"image","score":.9,"sha256":digest,"media_type":"image/png"}
    (normalized/"figure_manifest.json").write_text(json.dumps({"status":"partial",
        "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
        "figures":[good,{**good,"file":str(outside)},{**good,"label":"table"}]}))
    service=PublicMaterials(material_services)
    group=service.register("alice",title="教材",book_ids=["one"])
    manifest=service.manifest(group["id"])
    assert len(manifest["books"][0]["figures"])==1
    path,descriptor=service.asset(group["id"],digest)
    assert path.read_bytes()==image.read_bytes() and descriptor["media_type"]=="image/png"
    # Corrupt a blob after publication: immutable digest contract refuses to serve it.
    path.write_bytes(b"corrupt")
    with pytest.raises(KeyError):service.asset(group["id"],digest)


def test_ready_manifest_is_stable_and_downloads_a_complete_frozen_snapshot(material_services):
    add_book(material_services,"one")
    service=PublicMaterials(material_services)
    group=service.register("alice",title="固定版本",book_ids=["one"])
    first=service.manifest(group["id"])
    assert service.manifest(group["id"])==first
    descriptor=next(f for f in first["books"][0]["files"] if f["kind"]=="pdf")
    path,_=service.asset(group["id"],descriptor["sha256"])
    original=path.read_bytes()
    # Publication owns immutable copies, not mutable source paths.
    material_services.job_repository.get_book("one").file_path.write_bytes(b"%PDF-1.7 changed")
    assert service.manifest(group["id"])["version"]==first["version"]
    saved,_=service.asset(group["id"],descriptor["sha256"])
    assert saved.read_bytes()==original


def test_source_changed_before_publication_is_not_mixed_with_old_ocr(material_services):
    add_book(material_services,"one")
    material_services.job_repository.get_book("one").file_path.write_bytes(b"%PDF-1.7 changed")
    group=PublicMaterials(material_services).register("alice",title="待核对版本",book_ids=["one"])
    assert group["status"]=="preparing"
    assert PublicMaterials(material_services).status(group["id"],"alice")["pending_reason"]=="original_source_changed"


def test_corrupt_optional_figure_manifest_never_blocks_text_material_publication(material_services):
    add_book(material_services,"one")
    path=material_services.settings.data_dir/"books/one/ocr/normalized/figure_manifest.json"
    path.write_text("not-json")
    service=PublicMaterials(material_services)
    group=service.register("alice",title="教材",book_ids=["one"])
    assert group["status"]=="ready"
    descriptor=next(f for f in service.manifest(group["id"])["books"][0]["files"] if f["kind"]=="figure_manifest")
    asset,_=service.asset(group["id"],descriptor["sha256"])
    assert json.loads(asset.read_text())["extraction"]["reason"]=="figure_manifest_invalid"


def test_ready_group_cover_permission_is_revocable_and_does_not_grant_other_book_reads(material_services,monkeypatch):
    from importlib import import_module
    from adaptive_learning.api.account_guard import learning_guard
    from adaptive_learning.public_materials import public_group_contains_book
    api=import_module("adaptive_learning.api.app")
    services=material_services
    add_book(services,"one");add_book(services,"private-other")
    coverdir=services.settings.data_dir/"covers";coverdir.mkdir()
    (coverdir/"one.jpg").write_bytes(b"cover-one")
    (coverdir/"private-other.jpg").write_bytes(b"private-cover")
    tokens={"token-a":"alice","token-b":"bob"}
    services.account_repository.request_owner=lambda request:tokens.get(request.cookies.get("zhiwo_account"))
    monkeypatch.setattr(api,"settings",services.settings)
    monkeypatch.setattr(api,"account_repository",services.account_repository)
    application=FastAPI()
    application.middleware("http")(learning_guard(services.account_repository))
    application.add_api_route("/api/books/{book_id}/cover",api.book_cover,methods=["GET"])
    application.include_router(public_material_router(services))
    assert not public_group_contains_book(services.account_repository.repo,"one")
    service=PublicMaterials(services)
    group=service.register("alice",title="公开封面课程",book_ids=["one"])
    assert group["cover_url"]=="/api/books/one/cover"
    manifest=service.manifest(group["id"])
    assert manifest["books"][0]["cover_url"]==group["cover_url"]
    assert public_group_contains_book(services.account_repository.repo,"one")
    with TestClient(application) as reader:
        reader.cookies.set("zhiwo_account","token-b")
        response=reader.get(group["cover_url"])
        assert response.status_code==200 and response.content==b"cover-one"
        assert response.headers["cache-control"]=="private, no-store"
        assert reader.get("/api/books/private-other/cover").status_code in {403,404}
        service.withdraw(group["id"],"alice")
        assert not public_group_contains_book(services.account_repository.repo,"one")
        assert reader.get(group["cover_url"]).status_code in {403,404}
        # Revocation restores upload ownership access; it does not delete the permitted cover.
        reader.cookies.set("zhiwo_account","token-a")
        assert reader.get(group["cover_url"]).status_code==200
