"""Opaque inference fences and account-scoped exports against real PostgreSQL."""
import hashlib
import json
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_postgres import pg as pg
from adaptive_learning.assistant_ledger import AssistantLedger
from adaptive_learning.assistant_sessions import AssistantSessions
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.native import NativeRepository
from adaptive_learning.api.private_forwarding import PrivateLedger
from adaptive_learning.api.private_export import private_export_router


def test_private_ledger_pg_never_writes_model_body_and_restart_is_unknown(pg, tmp_path):
    base = AssistantLedger(tmp_path / "state/assistant.sqlite3")
    ledger = PrivateLedger(base)
    claim = ledger.claim("alice", "private:run", "private:step", 0, context_digest="context-digest", digest="request-digest",
                         lease_seconds=30, estimated_input_tokens=12)
    ledger.finish("alice", "private:run", "private:step", claim.step.attempt, state="completed", posted=True,
                  elapsed=1, input_tokens=12, output_tokens=4, message={"content": "secret-model-output"})
    assert ledger.step("alice", "private:run", "private:step")[0].message["content"] == "secret-model-output"
    with base.connect() as db:
        row = db.execute("SELECT message_json,error FROM assistant_steps WHERE owner=?", ("alice",)).fetchone()
        assert row["message_json"] is None
    restarted = PrivateLedger(AssistantLedger(tmp_path / "state/assistant.sqlite3"))
    assert restarted.step("alice", "private:run", "private:step")[0].state == "uncertain"
    assert not restarted.claim("alice", "private:run", "private:step", 0, context_digest="context-digest", digest="request-digest",
                               lease_seconds=30, estimated_input_tokens=12).send


def test_private_export_pg_scopes_files_checks_digest_and_detects_legacy_delta(pg, tmp_path):
    native = NativeRepository(tmp_path / "state/native.sqlite3")
    community = CommunityRepository(tmp_path / "state/community.sqlite3")
    ledger = AssistantLedger(tmp_path / "state/assistant.sqlite3")
    AssistantSessions(ledger)
    services = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), native_repository=native,
        account_repository=SimpleNamespace(repo=community, session_owner=lambda token: token if token in {"alice", "bob"} else None),
        assistant_ledger=ledger, assessment_repository=SQLiteAssessmentRepository(tmp_path / "state/assessments.sqlite3"),
        job_repository=SQLiteOCRJobRepository(tmp_path / "state/ocr_jobs.sqlite3"))
    native.save_workspace("alice", "wa", "local-migrate", {})
    native.save_workspace("bob", "wb", "foreign", {})
    with community.connect() as db:
        for owner, note in [("alice", "note_alice"), ("bob", "note_bob")]:
            db.execute("INSERT INTO studio_notes VALUES(?,?,?,?,?,?,?)", (note, owner, "book", 1, "{}", 1, 1))
    media = tmp_path / "learning-studio"
    media.mkdir()
    for owner in ["alice", "bob"]:
        directory = media / "private-voice-notes" / hashlib.sha256(owner.encode()).hexdigest()[:24]
        directory.mkdir(parents=True)
        (directory / ("note_" + owner + ".audio")).write_bytes(("private-" + owner + "-voice").encode())
    app = FastAPI()
    app.include_router(private_export_router(services))
    with TestClient(app) as client:
        client.cookies.set("zhiwo_account", "alice")
        first = client.get("/api/private/export?limit=1")
        assert first.status_code == 200, first.text
        page = first.json()
        assert page["next_cursor"]
        files = page["files"]
        assert len(files) == 1
        payload = client.get(files[0]["download_url"]).content
        assert hashlib.sha256(payload).hexdigest() == files[0]["sha256"]
        assert payload == b"private-alice-voice"
        all_records = page["records"]
        cursor = page["next_cursor"]
        while cursor:
            following = client.get("/api/private/export", params={"cursor": cursor, "limit": 1}).json()
            all_records += following["records"]
            cursor = following["next_cursor"]
        assert "foreign" not in json.dumps(all_records)
        ack = {"snapshot_revision": page["snapshot_revision"], "digest": page["snapshot_revision"],
               "record_count": len(all_records), "file_count": len(files)}
        assert client.post("/api/private/export/checkpoint", json=ack).json()["purged"] is False
        native.save_workspace("alice", "wa", "old-client-updated", {})
        assert client.get("/api/private/export", params={"cursor": page["next_cursor"]}).status_code == 409
        client.cookies.set("zhiwo_account", "bob")
        assert client.get(files[0]["download_url"]).status_code == 404
