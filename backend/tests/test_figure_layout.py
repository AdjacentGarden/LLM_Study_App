"""No inference: verify approved post-filter, isolation and honest failure states."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from adaptive_learning.ingestion.figure_filter import filter_manifest
from adaptive_learning.ingestion import figure_layout


def baseline():
    return {"threshold":0.5,"pages":[{"page_number":1,"width":1000,"height":1400,
        "boxes":[],"figures":[
            {"file":"tiny","label":"image","score":.99,"bbox_pixels":[10,10,40,40]},
            {"file":"photo","label":"image","score":.8,"bbox_pixels":[600,700,900,1050]},
            {"file":"chart-composite","label":"chart","score":.9,"bbox_pixels":[100,800,500,1100]},
        ]}]}


def test_frozen_filter_discards_tiny_without_inventing_icon_identity_or_splitting():
    original=baseline()
    context={"1":[{"text":"图1.1","bbox_normalized":[.01,.03,.04,.04]}]}
    filtered,audit=filter_manifest(original,context)
    assert [figure["file"] for figure in filtered["pages"][0]["figures"]]==["photo","chart-composite"]
    assert audit["decisions"][0]["reason_codes"]==["tiny_unverified_region"]
    assert len(original["pages"][0]["figures"])==3
    assert filtered["pages"][0]["figures"][1]["bbox_pixels"]==[100,800,500,1100]


def test_filter_allows_missing_text_and_refuses_threshold_lowering():
    filtered,audit=filter_manifest(baseline())
    assert audit["kept"]==2
    assert all(not item["caption_evidence"] for item in audit["decisions"])
    changed=baseline();changed["threshold"]=.4
    with pytest.raises(ValueError,match="threshold"):filter_manifest(changed)


def test_missing_layout_runtime_is_unavailable_not_successful_empty(monkeypatch,tmp_path):
    monkeypatch.delenv("FIGURE_LAYOUT_PYTHON",raising=False)
    monkeypatch.delenv("FIGURE_LAYOUT_MODEL_DIR",raising=False)
    source=tmp_path/"source.pdf";source.write_bytes(b"%PDF-1.7\nfixture")
    result=figure_layout.extract_figures(source,tmp_path/"normalized")
    assert result["status"]=="unavailable" and result["reason"]=="layout_not_configured"
    assert result["source_sha256"]==hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["figures"]==[]
    assert json.loads((tmp_path/"normalized/figure_manifest.json").read_text())["status"]=="unavailable"
    assert not list(tmp_path.rglob("*.png"))


def test_isolated_inference_failure_keeps_ocr_and_does_not_save_pages(monkeypatch,tmp_path):
    source=tmp_path/"source.pdf";source.write_bytes(b"%PDF-1.7\nfixture")
    model=tmp_path/"model";model.mkdir()
    monkeypatch.setattr(figure_layout,"layout_configuration",lambda:(Path("/isolated/python"),model))
    calls=[]
    class FailedProcess:
        returncode=1
        def communicate(self,timeout=None):return ("", "")
        def poll(self):return 1
    def fail(command,**kwargs):
        calls.append((command,kwargs))
        return FailedProcess()
    monkeypatch.setattr(figure_layout.subprocess,"Popen",fail)
    result=figure_layout.extract_figures(source,tmp_path/"normalized")
    assert result["status"]=="unavailable" and result["reason"]=="layout_inference_failed"
    assert calls[0][0][0]=="/isolated/python"
    assert "PYTHONPATH" not in calls[0][1]["env"]
    assert source.read_bytes().startswith(b"%PDF-")
    assert not list(tmp_path.rglob("*.png"))


def test_layout_model_must_match_pinned_checksum(monkeypatch,tmp_path):
    interpreter=tmp_path/"python";interpreter.write_text("placeholder");interpreter.chmod(0o700)
    model=tmp_path/"model";model.mkdir();(model/"inference.onnx").write_bytes(b"wrong-model")
    (model/"inference.yml").write_bytes(b"wrong-config")
    monkeypatch.setenv("FIGURE_LAYOUT_PYTHON",str(interpreter))
    monkeypatch.setenv("FIGURE_LAYOUT_MODEL_DIR",str(model))
    with pytest.raises(RuntimeError,match="checksum"):figure_layout.layout_configuration()


def test_figure_location_identity_does_not_collapse_identical_bytes_on_different_pages():
    source="a"*64; image="b"*64; bbox=[10.0,20.0,100.0,120.0]
    first=figure_layout.figure_identity(source,1,bbox,image)
    assert first==figure_layout.figure_identity(source,1,bbox,image)
    assert first!=figure_layout.figure_identity(source,2,bbox,image)
    assert first!=figure_layout.figure_identity(source,1,[11.0,20.0,100.0,120.0],image)
    assert first!=figure_layout.figure_identity("c"*64,1,bbox,image)
