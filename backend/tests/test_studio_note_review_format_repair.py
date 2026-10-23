"""Offline process_note recovery contracts, never measured semantic quality.

Four fixtures preserve parsed diagnostic-02 content. input.json keeps only the
synthetic transcript and the three source hits needed by those suggestions;
book/note identifiers and credentials are absent. Historical writer span IDs
are rebound to this test's current registry by exact original/quote/page only.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import threading
from pathlib import Path
from types import MethodType

import httpx
import pytest

from adaptive_learning import studio
from adaptive_learning import studio_note_coverage as coverage
from adaptive_learning.llm.client import LLMConfig, LLMError, OpenAICompatibleClient

BASE = Path(__file__).parent / "fixtures/studio_note_review_format"


def fixture(name):
    return json.loads((BASE / name).read_text())


def packet_from_prompt(prompt):
    assert prompt.startswith("审核笔记整理")
    packet, _ = json.JSONDecoder().raw_decode(prompt.split("\n待审核数据：", 1)[1])
    return packet


def complete_review(prompt, *, failed=(), negative_global=(), scope=None):
    directory = packet_from_prompt(prompt)["claim_coverage"]
    negatives = {key: "S" for key in failed}
    negatives.update(("g_" + key, "F") for key in negative_global)
    return {
        "candidate_id": directory["candidate_id"], "passed": not negatives,
        "reason": "Explicit offline full-review mock; not a quality verdict.",
        "global_checks": {key: key not in negative_global for key in directory["global_ids"]},
        "clause_checks": [[row["id"], row["id"] not in negatives] for row in directory["clauses"]],
        "findings": [[key, code] for key, code in negatives.items()],
        "failure_scope": scope or ("other_or_uncertain" if negatives else "none"),
    }


class NoteHarness(studio.Studio):
    def __init__(self, answers):
        self._note_trace = threading.local()
        self._media_trace = threading.local()
        self.answers = list(answers)
        self.calls, self.updates, self.traces = [], [], []

    def note(self, owner, key):
        assert owner == "offline-owner" and key == "offline-format-note"
        return {"revision": 1}

    def evidence(self, note, transcript):
        assert transcript == fixture("input.json")["transcript"]
        return fixture("input.json")["evidence"]

    def update(self, key, **fields):
        self.updates.append(copy.deepcopy(fields))

    def _save_note_trace(self, stage, **fields):
        inspect.signature(studio.Studio._save_note_trace).bind(self, stage, **fields)
        self.traces.append({"stage": stage, **copy.deepcopy(fields)})

    def llm(self, prompt, images=None):
        assert images is None
        self.calls.append(prompt)
        assert len(self.calls) <= 5, "A sixth logical request must never be sent"
        assert self.answers, "An unplanned paid request would have been sent"
        reply = self.answers.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply(prompt, self) if callable(reply) else reply)

    def run_note(self, job_id="offline-format-job"):
        self.process_note({"id": job_id, "owner": "offline-owner", "kind": "improve"},
            {"book_id": "offline-format-book", "note_id": "offline-format-note", "revision": 1,
             "transcript": fixture("input.json")["transcript"]})

    def assert_not_published(self):
        assert not any(update.get("status") == "succeeded" for update in self.updates)


def actual_draft(_prompt, harness):
    raw = fixture("actual-draft-01.json")
    original = fixture("actual-review-input.json")
    context = harness._note_trace.context
    for wire, literal in zip(raw["suggestions"], original["suggestions"], strict=True):
        wire["original_span_id"] = next(row["id"] for row in context["original_spans"]
            if row["text"] == literal["original"]) if literal["original"] else None
        wire["evidence_span_id"] = next(row["id"] for row in context["evidence_spans"]
            if row["text"] == literal["evidence"] and row["page"] == literal["page"])
    return raw


def repaired_writer(prompt, harness):
    assert prompt.startswith("根据审核意见")
    raw = actual_draft(prompt, harness)
    del raw["suggestions"][1]  # Explicit writer edit; preserve summary/polished.
    return raw


def actual_missing_id_review(prompt, _harness):
    assert packet_from_prompt(prompt) == fixture("actual-review-input.json")
    raw = fixture("actual-response-02.json")
    assert "candidate_id" not in raw and raw["passed"] is False
    return raw


def repaired_actual_review(prompt, _harness):
    assert packet_from_prompt(prompt) == fixture("actual-review-input.json")
    raw = fixture("actual-response-02.json")
    # Only this explicit mock model reply supplies the missing ID. The service
    # must neither patch raw nor turn the existing rejection into an approval.
    raw["candidate_id"] = packet_from_prompt(prompt)["claim_coverage"]["candidate_id"]
    return raw


def approved_review(prompt, _harness):
    return complete_review(prompt)


def rejected_global_review(prompt, _harness):
    return complete_review(prompt, negative_global=("fidelity",))


def malformed_no_negative_review(prompt, _harness):
    raw = complete_review(prompt)
    del raw["candidate_id"]
    return raw


def test_actual02_missing_id_reasks_once_preserves_false_then_writer_and_fifth_review():
    fake = NoteHarness([actual_draft, actual_missing_id_review, repaired_actual_review,
                        repaired_writer, approved_review])
    fake.run_note()
    assert len(fake.calls) == 5 and not fake.answers
    result = json.loads(fake.updates[-1]["result"])
    assert fake.updates[-1]["status"] == "succeeded"
    historical = fixture("actual-draft-01.json")
    assert result["summary"] == historical["summary"] and result["polished"] == historical["polished"]
    assert result["transcript"] == fixture("input.json")["transcript"]
    assert len(result["suggestions"]) == 2 and "quality_notice" not in result
    first, repair, final = (packet_from_prompt(fake.calls[i]) for i in (1, 2, 4))
    assert first == repair and first["claim_coverage"]["candidate_id"] != final["claim_coverage"]["candidate_id"]
    # The changed writer candidate gets a new review, not permanent old-ID locks.
    assert all("体细胞中成对的基因一个来自父方" not in item["suggestion"] for item in final["suggestions"])
    raw_traces = [item["response"] for item in fake.traces if "response" in item]
    assert fixture("actual-response-02.json") in raw_traces
    assert any(raw.get("candidate_id") == first["claim_coverage"]["candidate_id"]
        and raw.get("passed") is False and ["s1c2", "S"] in raw.get("findings", []) for raw in raw_traces)


def test_harness_checks_the_actual_trace_signature_instead_of_swallowing_extra_keywords():
    fake = NoteHarness([])
    with pytest.raises(TypeError):
        fake._save_note_trace("invalid-call", negative_locks={})
    assert fake.traces == []


def real_file_trace_run(tmp_path, answers):
    fake = NoteHarness(answers)
    fake.assets = tmp_path
    # Bypass the collecting override entirely: exercise production serialization,
    # filename validation, atomic file replacement and its real keyword signature.
    fake._save_note_trace = MethodType(studio.Studio._save_note_trace, fake)
    job_id = "f" * 32
    fake.run_note(job_id=job_id)
    assert len(fake.calls) == 5 and not fake.answers and fake.traces == []
    directory_ = tmp_path / "private-note-traces"
    paths = list(directory_.glob(job_id + "-*.json"))
    assert paths and not list(directory_.glob("*.tmp"))
    assert directory_.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
    return fake, [json.loads(path.read_text()) for path in paths]


def test_actual02_format_path_writes_real_private_traces_with_rejection_and_repair_locks(tmp_path):
    fake, records = real_file_trace_run(tmp_path, [actual_draft, actual_missing_id_review,
        repaired_actual_review, repaired_writer, approved_review])
    assert fake.updates[-1]["status"] == "succeeded"
    bad = fixture("actual-response-02.json")
    rejected = next(row for row in records if row["stage"] == "final_review" and row["response"]["passed"] is False)
    assert rejected["response"] == bad and "candidate_id" not in rejected["response"]
    expected_locks = coverage.repair_locks(bad, directory())
    format_rejection = next(row for row in records if row["stage"] == "review_format_rejection")
    assert format_rejection["feedback"]["previous_response"] == bad
    assert format_rejection["feedback"]["negative_locks"] == expected_locks
    assert format_rejection["feedback"]["calls_consumed"] == 2
    assert "calls_consumed" not in format_rejection
    repair = next(row for row in records if row["stage"] == "review_format_repair")
    assert repair["feedback"] == {"negative_locks": expected_locks}
    assert repair["response"]["passed"] is False and ["s1c2", "S"] in repair["response"]["findings"]
    assert repair["response"]["candidate_id"] == expected_locks["candidate_id"]
    assert "negative_locks" not in repair
    assert format_rejection["review_input"] == repair["review_input"] == fixture("actual-review-input.json")


def test_existing_optional_omission_path_writes_all_metadata_through_real_trace_signature(tmp_path):
    def optional_negative(prompt, _harness):
        return complete_review(prompt, failed=("s1c2",), scope="optional_suggestions_only")

    fake, records = real_file_trace_run(tmp_path, [actual_draft, optional_negative,
        actual_draft, optional_negative, approved_review])
    assert fake.updates[-1]["status"] == "succeeded"
    result = json.loads(fake.updates[-1]["result"])
    assert result["excluded_optional_count"] == 1 and result["quality_notice"] == studio.NOTE_OPTIONAL_QUALITY_NOTICE
    assert not any(row["stage"].startswith("review_format") for row in records)
    omitted = next(row for row in records if row["stage"] == "optional_suggestions_omitted")
    metadata = omitted["feedback"]
    assert set(metadata) == {"excluded_indices", "excluded_suggestions", "candidate", "prior_rejected_review",
        "retained_fields_sha256", "calls_consumed"}
    assert metadata["calls_consumed"] == 4 and metadata["excluded_indices"] == [1]
    excluded = fixture("actual-review-input.json")["suggestions"][1]
    excluded.pop("suggestion_index")
    assert metadata["excluded_suggestions"] == [excluded]
    assert metadata["candidate"] == {key: result[key] for key in ("summary", "suggestions", "polished")}
    assert metadata["prior_rejected_review"]["passed"] is False
    assert metadata["prior_rejected_review"]["failure_scope"] == "optional_suggestions_only"
    assert metadata["prior_rejected_review"]["findings"][0]["suggestion_index"] == 1
    retained = {key: result[key] for key in ("summary", "polished", "transcript")}
    assert metadata["retained_fields_sha256"] == hashlib.sha256(json.dumps(retained,
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert "excluded_indices" not in omitted and "calls_consumed" not in omitted
    assert next(row for row in records if row["stage"] == "omission_final_review")["response"]["passed"] is True


@pytest.mark.parametrize("final_passes", [False, True])
def test_no_negative_fourth_malformed_review_may_use_fifth_slot_but_never_sixth(final_passes):
    final = approved_review if final_passes else rejected_global_review
    fake = NoteHarness([actual_draft, rejected_global_review, repaired_writer,
                        malformed_no_negative_review, final])
    if final_passes:
        fake.run_note()
        assert fake.updates[-1]["status"] == "succeeded"
    else:
        with pytest.raises((ValueError, LLMError)):
            fake.run_note()
        fake.assert_not_published()
    assert len(fake.calls) == 5 and not fake.answers
    assert packet_from_prompt(fake.calls[3]) == packet_from_prompt(fake.calls[4])


def test_explicit_stale_candidate_is_terminal_without_format_reask():
    def stale(prompt, _harness):
        raw = complete_review(prompt)
        raw["candidate_id"] = "0" * 64
        return raw
    fake = NoteHarness([actual_draft, stale])
    with pytest.raises(studio.StudioNoteContractError):
        fake.run_note()
    assert len(fake.calls) == 2
    fake.assert_not_published()


@pytest.mark.parametrize("error", [TimeoutError("offline lost response"),
    httpx.ReadTimeout("offline unknown transport"), LLMError("offline provider unavailable"),
    json.JSONDecodeError("offline invalid response body", "{", 1)])
def test_transport_or_unparsed_response_unknown_never_triggers_format_repair(error):
    fake = NoteHarness([actual_draft, error])
    with pytest.raises(type(error)):
        fake.run_note()
    assert len(fake.calls) == 2
    fake.assert_not_published()


def test_known_false_with_only_two_slots_left_does_not_spend_format_or_writer():
    fake = NoteHarness([{"summary": "Invalid initial writer shape"}, actual_draft, actual_missing_id_review])
    with pytest.raises((ValueError, LLMError)):
        fake.run_note()
    assert len(fake.calls) == 3 and not fake.answers
    assert not any(prompt.startswith("根据审核意见") for prompt in fake.calls)
    fake.assert_not_published()


def test_new_false_from_format_repair_with_one_slot_left_never_wastes_a_writer():
    fake = NoteHarness([{"summary": "Invalid initial writer shape"}, actual_draft,
                        malformed_no_negative_review, rejected_global_review])
    with pytest.raises((ValueError, LLMError)):
        fake.run_note()
    assert len(fake.calls) == 4 and not fake.answers
    assert not any(prompt.startswith("根据审核意见") for prompt in fake.calls)
    fake.assert_not_published()


def test_format_repair_still_malformed_is_terminal_without_second_format_or_writer():
    fake = NoteHarness([actual_draft, actual_missing_id_review, actual_missing_id_review])
    with pytest.raises(studio.StudioNoteContractError):
        fake.run_note()
    assert len(fake.calls) == 3
    fake.assert_not_published()


def test_shared_format_allowance_does_not_reset_for_the_second_candidate():
    fake = NoteHarness([actual_draft, actual_missing_id_review, repaired_actual_review,
                        repaired_writer, malformed_no_negative_review])
    with pytest.raises(studio.StudioNoteContractError):
        fake.run_note()
    assert len(fake.calls) == 5
    fake.assert_not_published()


def test_a_valid_second_negative_after_format_repair_cannot_request_sixth_omission_review():
    def optional_negative(prompt, _harness):
        return complete_review(prompt, failed=("s0c0",), scope="optional_suggestions_only")

    fake = NoteHarness([actual_draft, actual_missing_id_review, repaired_actual_review,
                        repaired_writer, optional_negative])
    with pytest.raises(ValueError):
        fake.run_note()
    assert len(fake.calls) == 5 and not fake.answers
    assert not any(item["stage"] == "optional_suggestions_omitted" for item in fake.traces)
    fake.assert_not_published()


@pytest.mark.parametrize("scope", ["other_or_uncertain", "optional_suggestions_only"])
def test_scope_only_negative_cannot_be_washed_into_true_by_format_repair(scope):
    def scope_only(prompt, _harness):
        raw = complete_review(prompt)
        raw.pop("candidate_id")
        raw["failure_scope"] = scope  # Other otherwise readable checks are true.
        return raw

    fake = NoteHarness([actual_draft, scope_only, approved_review])
    with pytest.raises(studio.StudioNoteContractError):
        fake.run_note()
    assert len(fake.calls) == 3
    fake.assert_not_published()


@pytest.mark.parametrize("lost_response", [False, True])
def test_actual_format_prompt_keeps_thousand_token_limit_and_one_physical_http_attempt(monkeypatch, lost_response):
    configs, requests = [], []

    def handler(request):
        requests.append(request)
        assert json.loads(request.content)["max_tokens"] == 1000
        if lost_response:
            raise httpx.ReadTimeout("offline format response unknown")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})

    def factory(config):
        configs.append(config)
        client = OpenAICompatibleClient(config)
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        return client

    fake = NoteHarness([])
    monkeypatch.setattr(studio, "OpenAICompatibleClient", factory)
    monkeypatch.setattr(fake, "client", lambda *_: factory(LLMConfig("https://api.deepseek.com",
        "offline-unit-key", "deepseek-flash", max_retries=1, thinking_enabled=False)))
    prompt = "审核笔记整理：本improve唯一一次输出格式修复复核，仍须完整审核同一未改候选。"
    if lost_response:
        with pytest.raises(LLMError):
            studio.Studio.llm(fake, prompt)
    else:
        assert studio.Studio.llm(fake, prompt) == {}
    assert len(requests) == 1 and configs[-1].max_retries == 0


def directory():
    packet = fixture("actual-review-input.json")
    del packet["claim_coverage"]
    return coverage.registry(packet)


@pytest.mark.parametrize("fault", ["passed", "clause", "code", "scope", "global"])
def test_format_repair_cannot_weaken_current_candidate_negative_locks(fault):
    expected = directory()
    raw = fixture("actual-response-02.json")
    if fault == "passed":
        raw["clause_checks"] = [[key, True] for key, _ in raw["clause_checks"]]
        raw["findings"] = []
        raw.pop("failure_scope")  # Isolate strict passed=false as the only veto.
    elif fault == "global":
        raw["global_checks"]["fidelity"] = False
        raw["findings"].append(["g_fidelity", "F"])
    snapshot = copy.deepcopy(raw)
    locks = coverage.repair_locks(raw, expected)
    repaired = copy.deepcopy(raw)
    repaired["candidate_id"] = expected["candidate_id"]
    if fault in ("passed", "clause"):
        repaired["passed"] = True
        repaired["clause_checks"] = [[key, True] for key, _ in repaired["clause_checks"]]
        repaired["findings"] = []
        repaired["failure_scope"] = "none"
    elif fault == "code":
        repaired["findings"] = [["s1c2", "A"]]
    elif fault == "scope":
        repaired["failure_scope"] = "optional_suggestions_only"
    else:
        repaired["global_checks"]["fidelity"] = True
        repaired["findings"] = [["s1c2", "S"]]
    with pytest.raises(coverage.CoverageError):
        coverage.validate_repair(repaired, expected, locks)
    assert raw == snapshot


def test_actual02_locks_allow_only_complete_same_candidate_rejection_to_continue():
    expected = directory()
    raw = fixture("actual-response-02.json")
    before = copy.deepcopy(raw)
    locks = coverage.repair_locks(raw, expected)
    repaired = copy.deepcopy(raw)
    repaired["candidate_id"] = expected["candidate_id"]
    result = coverage.validate_repair(repaired, expected, locks)
    assert result["passed"] is False and result["failure_scope"] == "other_or_uncertain"
    claim = next(row["text"] for row in expected["clauses"] if row["id"] == "s1c2")
    assert result["findings"] == [{"field": "suggestion", "suggestion_index": 1,
        "claim": claim, "issue": coverage.ISSUES["S"]}]
    assert raw == before and "candidate_id" not in raw


@pytest.mark.parametrize("fault", ["stale", "foreign_clause", "foreign_finding"])
def test_wrong_identity_and_foreign_negative_targets_are_not_rebound(fault):
    raw = fixture("actual-response-02.json")
    if fault == "stale":
        raw["candidate_id"] = "0" * 64
    elif fault == "foreign_clause":
        raw["clause_checks"].append(["other_candidate_clause", False])
    else:
        raw["findings"].append(["other_candidate_clause", "S"])
    with pytest.raises(coverage.CoverageError):
        coverage.repair_locks(raw, directory())


def test_fixtures_have_no_real_book_note_identity_and_no_extra_source_pool():
    value = fixture("input.json")
    assert set(value) == {"transcript", "evidence"}
    assert {item["page"] for item in value["evidence"]} == {22, 23, 54}
    packet = fixture("actual-review-input.json")
    assert len(packet["claim_coverage"]["clauses"]) == 9
    assert all(item["evidence"] in next(hit["text"] for hit in value["evidence"] if hit["page"] == item["page"])
        for item in packet["suggestions"])
