"""Private persistence contracts only; fixtures do not approve model quality."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from adaptive_learning.assessment.diagnostic_cache import DiagnosticChapterCacheError
from adaptive_learning.assessment.models import DiagnosticItem
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository


def identity(**changes):
    return {"scope": "book", "chapter_id": "chapter", "source_fingerprint": "source",
            "protocol_fingerprint": "protocol", "review_fingerprint": "independent-review",
            "point_ids": ["p1", "p2", "p3"], **changes}


def items():
    return [DiagnosticItem(item_id="item_" + point, chapter_id="chapter", knowledge_point_ids=[point],
        prompt="机械持久化合同，不代表真实题质批准", response_type="single_choice",
        options=["甲", "乙", "丙", "丁"], correct_option_ids=["1"], expected_answer="乙", source_pages=[1])
        for point in ["p1", "p2", "p3"]]


def repository(tmp_path):
    return SQLiteAssessmentRepository(tmp_path / "assessments.sqlite3")


def test_approved_chapter_is_private_complete_and_survives_restart(tmp_path):
    repo = repository(tmp_path)
    claim = repo.claim_diagnostic_chapter(identity())
    assert claim["status"] == "claimed"
    repo.approve_diagnostic_chapter(identity(), claim["token"], items())
    loaded = repository(tmp_path).claim_diagnostic_chapter(identity())
    assert loaded["status"] == "approved" and loaded["items"] == items()
    assert repo.get_bank("diagnostic:book") is None
    loaded["items"][0].options.reverse()
    assert repo.claim_diagnostic_chapter(identity())["items"] == items()
    with repo._connect() as db:
        assert db.execute("SELECT state FROM diagnostic_chapter_submissions").fetchone()[0] == "approved"
        assert db.execute("SELECT COUNT(*) FROM diagnostic_banks").fetchone()[0] == 0


def test_concurrent_claim_has_one_token_and_no_database_lock_during_model_work(tmp_path):
    repo = repository(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: repo.claim_diagnostic_chapter(identity()), range(8)))
    assert sum(row["status"] == "claimed" for row in claims) == 1
    assert sum(row["status"] == "running" for row in claims) == 7
    # A different chapter can claim immediately while the first has no result.
    assert repo.claim_diagnostic_chapter(identity(chapter_id="other"))["status"] == "claimed"


@pytest.mark.parametrize("state", ["running", "unknown"])
def test_restart_or_time_does_not_authorize_unknown_resubmission(tmp_path, state):
    repo = repository(tmp_path)
    claim = repo.claim_diagnostic_chapter(identity(), now=1)
    if state == "unknown":
        repo.fail_diagnostic_chapter(identity(), claim["token"], "unknown", now=2)
    again = repository(tmp_path).claim_diagnostic_chapter(identity(), now=10**12)
    assert again["status"] == state and again["token"] is None
    with repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM diagnostic_chapter_submissions").fetchone()[0] == 1


@pytest.mark.parametrize("outcome", ["rejected", "not_admitted"])
def test_known_completed_failure_allows_new_claim_and_fences_old_token(tmp_path, outcome):
    repo = repository(tmp_path)
    first = repo.claim_diagnostic_chapter(identity())
    repo.fail_diagnostic_chapter(identity(), first["token"], outcome)
    second = repo.claim_diagnostic_chapter(identity())
    assert second["status"] == "claimed" and second["token"] != first["token"]
    with pytest.raises(DiagnosticChapterCacheError, match="stale"):
        repo.approve_diagnostic_chapter(identity(), first["token"], items())
    repo.approve_diagnostic_chapter(identity(), second["token"], items())
    with repo._connect() as db:
        assert [row[0] for row in db.execute("SELECT state FROM diagnostic_chapter_submissions ORDER BY created_at")] == [outcome, "approved"]


@pytest.mark.parametrize("state", ["running", "unknown"])
def test_explicit_recovery_is_marker_idempotent_consumed_once_and_token_fenced(tmp_path, state):
    repo = repository(tmp_path)
    old = repo.claim_diagnostic_chapter(identity())
    if state == "unknown":
        repo.fail_diagnostic_chapter(identity(), old["token"], "unknown")
    assert repo.grant_diagnostic_recovery("book", "failed revision 1") == 1
    assert repo.grant_diagnostic_recovery("book", "failed revision 1") == 1
    current = repo.claim_diagnostic_chapter(identity())
    assert current["status"] == "claimed" and current["token"] != old["token"]
    assert repo.claim_diagnostic_chapter(identity())["status"] == "running"
    with pytest.raises(DiagnosticChapterCacheError, match="stale"):
        repo.approve_diagnostic_chapter(identity(), old["token"], items())
    repo.fail_diagnostic_chapter(identity(), current["token"], "unknown")
    assert repo.claim_diagnostic_chapter(identity())["status"] == "unknown"
    assert repo.grant_diagnostic_recovery("book", "failed revision 2") == 2
    # Even after a later action, replaying the old action never creates epoch 3.
    assert repo.grant_diagnostic_recovery("book", "failed revision 1") == 1
    assert repo.claim_diagnostic_chapter(identity())["status"] == "claimed"
    with repo._connect() as db:
        assert [row[0] for row in db.execute("SELECT recovery_epoch FROM diagnostic_chapter_submissions ORDER BY created_at")] == [0, 1, 2]


def test_book_recovery_does_not_invalidate_approved_chapters_or_authorize_another_book(tmp_path):
    repo = repository(tmp_path)
    approved = repo.claim_diagnostic_chapter(identity())
    repo.approve_diagnostic_chapter(identity(), approved["token"], items())
    other = identity(scope="other_book")
    repo.claim_diagnostic_chapter(other)
    repo.grant_diagnostic_recovery("book", "failed preparation")
    assert repo.claim_diagnostic_chapter(identity())["status"] == "approved"
    assert repo.claim_diagnostic_chapter(other)["status"] == "running"


@pytest.mark.parametrize("field,value", [("scope", "other"), ("source_fingerprint", "new source"),
    ("protocol_fingerprint", "new protocol"), ("review_fingerprint", "new review"),
    ("chapter_id", "other chapter"), ("point_ids", ["p1", "p3", "p2"])])
def test_any_exact_identity_change_does_not_reuse_approval(tmp_path, field, value):
    repo = repository(tmp_path)
    first = repo.claim_diagnostic_chapter(identity())
    repo.approve_diagnostic_chapter(identity(), first["token"], items())
    assert repo.claim_diagnostic_chapter(identity(**{field: value}))["status"] == "claimed"


@pytest.mark.parametrize("fault", ["partial", "duplicate", "foreign", "bad_answer", "wrong_order"])
def test_partial_or_invalid_chapter_never_becomes_approved(tmp_path, fault):
    repo = repository(tmp_path)
    claim = repo.claim_diagnostic_chapter(identity())
    values = items()
    if fault == "partial":
        values.pop()
    if fault == "duplicate":
        values[1] = values[0]
    if fault == "foreign":
        values[0].chapter_id = "other"
    if fault == "bad_answer":
        values[0].expected_answer = "甲"
    if fault == "wrong_order":
        values.reverse()
    with pytest.raises(DiagnosticChapterCacheError):
        repo.approve_diagnostic_chapter(identity(), claim["token"], values)
    assert repo.claim_diagnostic_chapter(identity())["status"] == "running"
    assert repo.get_bank("diagnostic:book") is None


def test_cache_and_ledger_approve_transaction_rolls_back_if_ledger_token_changed(tmp_path):
    repo = repository(tmp_path)
    claim = repo.claim_diagnostic_chapter(identity())
    with repo._connect() as db:
        db.execute("UPDATE diagnostic_chapter_submissions SET state='unknown' WHERE token=?", (claim["token"],))
    with pytest.raises(DiagnosticChapterCacheError, match="stale"):
        repo.approve_diagnostic_chapter(identity(), claim["token"], items())
    assert repo.claim_diagnostic_chapter(identity())["status"] == "running"
    with repo._connect() as db:
        assert db.execute("SELECT items_json FROM diagnostic_chapter_cache").fetchone()[0] is None


def test_digest_tampering_never_reuses_approved_payload(tmp_path):
    repo = repository(tmp_path)
    claim = repo.claim_diagnostic_chapter(identity())
    repo.approve_diagnostic_chapter(identity(), claim["token"], items())
    with repo._connect() as db:
        db.execute("UPDATE diagnostic_chapter_cache SET items_json='[]'")
    with pytest.raises(DiagnosticChapterCacheError, match="digest"):
        repo.claim_diagnostic_chapter(identity())


def test_legacy_banks_are_never_approval_input_or_overwritten_by_private_cache(tmp_path):
    repo = repository(tmp_path)
    original = items()
    repo.save_bank(book_id="book", structure_fingerprint="legacy", items=original)
    repo.save_bank(book_id="interview:existing", structure_fingerprint="session-snapshot-v1", items=original)
    claim = repo.claim_diagnostic_chapter(identity())
    assert claim["status"] == "claimed"
    changed = items()
    changed[0].prompt = "新的完整章批准不会改写历史题"
    repo.approve_diagnostic_chapter(identity(), claim["token"], changed)
    assert repo.get_bank("book") == original
    assert repo.get_bank("interview:existing") == original
