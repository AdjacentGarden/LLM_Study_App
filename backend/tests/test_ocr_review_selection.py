from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pymupdf
import pytest

from adaptive_learning.ingestion.ocr_review import OCRReviewUnavailable, review_pages


@pytest.fixture
def accepted_pages(tmp_path):
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as doc:
        for text in ('ordinary source', 'Cu2S formula source'):
            doc.new_page().insert_text((50, 80), text)
        doc.save(source)
    normalized = tmp_path / 'normalized'
    normalized.mkdir()
    rows = [{'page_number': n, 'text': text,
             'blocks': [{'block_index': 0, 'type': 'text', 'text': text}]}
            for n, text in enumerate(('ordinary source', 'wrong formula'), 1)]
    (normalized / 'pages.jsonl').write_text(''.join(json.dumps(p) + '\n' for p in rows))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    (normalized / 'quality_report.json').write_text(json.dumps({
        'source_sha256': source_hash, 'page_count_normalized': 2,
        'mean_heuristic_score': .95,
        'pages': [{'page_number': n, 'band': 'accepted'} for n in (1, 2)],
    }))
    return source, normalized, source_hash


class Vision:
    config = SimpleNamespace(model='test', base_url='https://test.invalid')

    def __init__(self, verified=True):
        self.calls = []
        self.responses = [
            {'action': 'correct', 'reason': 'subscript was omitted',
             'blocks': [{'type': 'equation', 'text': 'Cu₂S'}]},
            {'verified': verified, 'reason': 'independent image verification'},
        ]

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def manifest(tmp_path, monkeypatch, source_hash, pages):
    path = tmp_path / 'review-pages.json'
    path.write_text(json.dumps({source_hash: {'pages': pages, 'reason': 'observed formula error'}}))
    monkeypatch.setenv('OCR_REVIEW_PAGE_MANIFEST', str(path))


@pytest.mark.parametrize('verified', [True, False])
def test_requested_accepted_page_still_requires_independent_verification(
    accepted_pages, tmp_path, monkeypatch, verified,
):
    source, normalized, source_hash = accepted_pages
    original = (normalized / 'pages.jsonl').read_bytes()
    manifest(tmp_path, monkeypatch, source_hash, [2])
    model = Vision(verified)
    result = review_pages(source, normalized, tmp_path / 'review', lambda *args: True, client=model)
    rows = [json.loads(line) for line in (result / 'pages.jsonl').read_text().splitlines()]
    assert len(model.calls) == 2
    assert json.loads(model.calls[0]['user'])['page_number'] == 2
    assert rows[0]['text'] == 'ordinary source'
    assert rows[1]['processing_status'] == ('corrected' if verified else 'skipped')
    assert rows[1]['text'] == ('Cu₂S' if verified else '')
    assert (normalized / 'pages.jsonl').read_bytes() == original
    report = json.loads((result / 'quality_report.json').read_text())
    assert report['review_selection']['requested_pages'] == [2]
    assert report['review_selection']['source_sha256'] == source_hash


def test_selection_for_another_pdf_does_not_submit_any_model_call(
    accepted_pages, tmp_path, monkeypatch,
):
    source, normalized, _ = accepted_pages
    manifest(tmp_path, monkeypatch, '0' * 64, [2])
    model = Vision()
    review_pages(source, normalized, tmp_path / 'review', lambda *args: True, client=model)
    assert model.calls == []


@pytest.mark.parametrize('pages', [[3], [True], [2, 2], []])
def test_invalid_page_selection_fails_before_model_submission(
    accepted_pages, tmp_path, monkeypatch, pages,
):
    source, normalized, source_hash = accepted_pages
    manifest(tmp_path, monkeypatch, source_hash, pages)
    model = Vision()
    with pytest.raises(OCRReviewUnavailable, match='manifest'):
        review_pages(source, normalized, tmp_path / 'review', lambda *args: True, client=model)
    assert model.calls == []
