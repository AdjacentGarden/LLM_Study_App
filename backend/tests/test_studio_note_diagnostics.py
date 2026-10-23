import json
import stat
from types import SimpleNamespace

import pytest
from studio_note_review_fixtures import note_review_fixture
from test_community import community as community
from test_studio import note, run
from test_studio import setup as setup

from adaptive_learning.studio import StudioNoteContractError

TRANSCRIPT = "同源染色体在减数第一次分裂时分离。"
EVIDENCE = [{"page": 7, "text": TRANSCRIPT}]


def draft(**change):
    return {
        "summary": "笔记记录了同源染色体分离的时间，可结合教材继续核对。",
        "suggestions": [{"original": TRANSCRIPT, "kind": "可以补充", "suggestion": "可记录发生阶段。",
                         "evidence": TRANSCRIPT, "page": 7}],
        "polished": TRANSCRIPT,
        **change,
    }


def submit(client, request_id):
    saved = client.post("/api/studio/notes", json=note()).json()
    response = client.post("/api/studio/notes/note_123456/analyze", json={
        "revision": saved["revision"], "transcript": TRANSCRIPT, "action": "improve",
        "consent": True, "request_id": request_id,
    })
    assert response.status_code == 200
    return response.json()["id"]


def traces(studio, key):
    return [json.loads(p.read_text()) for p in sorted((studio.assets / "private-note-traces").glob(key + "-*.json"))]


@pytest.mark.parametrize("fault,code,field", [
    ({"original": "同源染色体总会分离。"}, "invented_original", "suggestions[0].original"),
    ({"page": 8}, "unsupported_evidence", "suggestions[0].evidence/page"),
])
def test_note_repair_receives_the_rejected_original_or_page_and_still_needs_review(setup, monkeypatch, fault, code, field):
    a, _, studio = setup
    key = submit(a, "note_diagnostic_repair_111")
    bad = draft()
    bad["suggestions"][0].update(fault)
    answers = iter([bad, draft(), {"passed": True, "reason": "已独立核对教材和原意"}])
    calls = []

    def llm(prompt, images=None):
        calls.append(prompt)
        return note_review_fixture(prompt, next(answers))

    monkeypatch.setattr(studio, "evidence", lambda *_: EVIDENCE)
    monkeypatch.setattr(studio, "llm", llm)
    run(studio, a, key)
    result = a.get("/api/studio/jobs/" + key).json()
    assert result["status"] == "succeeded"
    assert len(calls) == 3 and calls[2].startswith("审核笔记整理")
    assert code in calls[1] and field in calls[1] and '"suggestion_index": 0' in calls[1]
    assert "JSON schema" in calls[1]
    if "page" in fault:
        assert '"provided_page": 8' in calls[1]
        assert '"available_source_pages": [7]' in calls[1]
        assert '"pages_with_matching_evidence": [7]' in calls[1]
    events = traces(studio, key)
    assert {e["stage"] for e in events} == {"improve_draft", "contract_rejection", "repair_draft", "final_review"}
    assert next(e for e in events if e["stage"] == "improve_draft")["response"] == bad
    assert all(e["context"]["transcript"] == TRANSCRIPT and e["context"]["evidence"] == EVIDENCE for e in events)
    assert "private-note-traces" not in json.dumps(result)


def test_note_schema_repair_feedback_locates_field_without_echoing_pydantic_input(setup, monkeypatch):
    a, _, studio = setup
    key = submit(a, "note_diagnostic_schema_111")
    answers = iter([draft(summary="甲" * 301), draft(), {"passed": True, "reason": "通过"}])
    calls = []

    def llm(prompt, images=None):
        calls.append(prompt)
        return note_review_fixture(prompt, next(answers))

    monkeypatch.setattr(studio, "evidence", lambda *_: EVIDENCE)
    monkeypatch.setattr(studio, "llm", llm)
    run(studio, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "succeeded"
    rejection = next(e["feedback"] for e in traces(studio, key) if e["stage"] == "contract_rejection")
    issue = rejection["violations"][0]
    assert issue["code"] == "schema_invalid" and issue["errors"][0]["loc"] == ["summary"]
    assert "input" not in issue["errors"][0]
    assert "schema_invalid" in calls[1] and '"summary"' in calls[1] and '"maxLength": 300' in calls[1]


@pytest.mark.parametrize("trace_write_failure", [False, True])
def test_invalid_repaired_note_fails_closed_with_private_specific_diagnostic(setup, monkeypatch, trace_write_failure):
    a, _, studio = setup
    key = submit(a, "note_diagnostic_refusal_111")
    bad = draft()
    bad["suggestions"][0]["page"] = 8
    calls = []

    def llm(prompt, images=None):
        calls.append(prompt)
        return bad

    monkeypatch.setattr(studio, "evidence", lambda *_: EVIDENCE)
    monkeypatch.setattr(studio, "llm", llm)
    if trace_write_failure:
        monkeypatch.setattr("adaptive_learning.studio.os.replace", lambda *_: (_ for _ in ()).throw(OSError("disk unavailable")))
    original_process = studio.process

    def one_job(row):
        try:
            original_process(row)
        finally:
            studio.stop_event.set()

    monkeypatch.setattr(studio, "process", one_job)
    studio.loop("notes")
    public = a.get("/api/studio/jobs/" + key).json()
    assert public["status"] == "failed" and len(calls) == 2
    assert not any(prompt.startswith("审核笔记整理") for prompt in calls)
    assert "_diagnostic" not in public["result"] and "polished" not in public["result"]
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    private = json.loads(studio.job(owner, key)["result"])["_diagnostic"]
    assert private["type"] == "StudioNoteContractError"
    rejected = private["note_contract"]["violations"][0]
    assert rejected["field"] == "suggestions[0].evidence/page" and rejected["provided_page"] == 8
    assert rejected["pages_with_matching_evidence"] == [7]
    assert not list((studio.assets / "private-note-traces").glob("*.tmp"))


def test_private_note_trace_records_parsed_output_with_safe_route_and_atomic_private_files(setup, monkeypatch):
    a, _, studio = setup
    key = submit(a, "note_trace_privacy_111")
    secret = "a-real-provider-secret-without-prefix"
    parsed = {"summary": "模型解析结果", "api_key": secret,
              "nested": {"Authorization": "Bearer " + secret},
              "text": "echo " + secret + " sk-1234567890abcdef Bearer someothercredential", secret: "echoed key name"}

    class Client:
        config = SimpleNamespace(base_url="https://user:password@api.deepseek.com/v1?secret=query", model="test-model", thinking_enabled=False, api_key=secret)

        def structured(self, **kwargs):
            return parsed

        def close(self):
            pass

    monkeypatch.setattr(studio, "client", lambda *_: Client())
    studio._note_trace.job_id = key
    studio._note_trace.context = {"transcript": TRANSCRIPT, "evidence": EVIDENCE}
    response = studio.llm("检查并局部补全笔记")
    assert response is parsed  # Tracing does not mutate the draft sent to validators.
    studio._save_note_trace("improve_draft", response=response)
    directory = studio.assets / "private-note-traces"
    paths = list(directory.glob(key + "-*.json"))
    assert len(paths) == 1 and not list(directory.glob("*.tmp"))
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    value = json.loads(paths[0].read_text())
    assert value["route"]["base_host"] == "api.deepseek.com" and value["route"]["model"] == "test-model"
    assert value["context"] == {"transcript": TRANSCRIPT, "evidence": EVIDENCE}
    assert value["response"]["summary"] == parsed["summary"]
    assert value["response"]["api_key"] == "[redacted]"
    assert secret not in paths[0].read_text() and "sk-1234567890abcdef" not in paths[0].read_text()
    assert "someothercredential" not in paths[0].read_text() and "password" not in paths[0].read_text() and "secret=query" not in paths[0].read_text()


def test_note_trace_is_bounded_and_does_not_change_the_failure_gate(setup, monkeypatch):
    a, _, studio = setup
    key = submit(a, "note_trace_bound_111")
    studio._note_trace.job_id = key
    studio._note_trace.context = {"transcript": TRANSCRIPT, "evidence": EVIDENCE}
    for _ in range(14):
        studio._save_note_trace("oversized", response={"huge": "汉" * 400_000})
    paths = list((studio.assets / "private-note-traces").glob(key + "-*.json"))
    assert len(paths) == 12 and all(p.stat().st_size <= 256_000 for p in paths)
    assert all(json.loads(p.read_text())["response"]["huge"]["trace_truncated"] for p in paths)
    bad = draft()
    bad["suggestions"][0]["page"] = 8
    monkeypatch.setattr(studio, "evidence", lambda *_: EVIDENCE)
    monkeypatch.setattr(studio, "llm", lambda *_: bad)
    with pytest.raises(StudioNoteContractError, match="unsupported suggestion"):
        run(studio, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] != "succeeded"
