"""Real PG migrations and shared group persistence, using existing disposable schema fixture."""
from test_postgres import pg as pg
from test_public_materials import make_services, add_book
from adaptive_learning.public_materials import PublicMaterials
from adaptive_learning.migrate import SCHEMA_VERSION, TABLES
from adaptive_learning.database import connect


def test_public_groups_and_export_checkpoints_exist_after_migration15(pg,tmp_path):
    assert SCHEMA_VERSION==15
    assert "public_course_groups" in TABLES["community.sqlite3"]
    assert "private_export_checkpoints" in TABLES["native.sqlite3"]
    services=make_services(tmp_path)
    add_book(services,"one");add_book(services,"two")
    service=PublicMaterials(services)
    group=service.register("alice",title="共享双教材",book_ids=["one","two"])
    assert group["status"]=="ready"
    reopened=PublicMaterials(services)
    manifest=reopened.manifest(group["id"])
    assert [b["book_id"] for b in manifest["books"]]==["one","two"]
    descriptor=manifest["books"][0]["files"][0]
    path,_=reopened.asset(group["id"],descriptor["sha256"])
    assert path.is_file()
    with connect(tmp_path/"unused") as db:
        assert db.execute("SELECT COUNT(*) FROM public_course_groups").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM private_export_checkpoints").fetchone()[0]==0
