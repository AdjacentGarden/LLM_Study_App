import json

import pytest

from adaptive_learning.rag.index import RAGIndexError
from adaptive_learning.rag.upload_index import upload_chunks


@pytest.mark.parametrize('fields,expected', [
    ({'cleaned_text': '已复核正文', 'raw_text': '识别原文', 'text': '旧内容'}, '已复核正文'),
    ({'cleaned_text': '  \n', 'raw_text': '识别原文', 'text': '旧内容'}, '识别原文'),
    ({'blocks': [{'text': '保留的文本块'}], 'text': '旧内容'}, '保留的文本块'),
    ({'text': '旧格式教材正文'}, '旧格式教材正文'),
])
def test_private_index_retains_authoritative_source_before_legacy_text(tmp_path, fields, expected):
    source = tmp_path / 'pages.jsonl'
    source.write_text(json.dumps({'page_number': 7, **fields}) + '\n', encoding='utf-8')
    chunks = upload_chunks(source)
    assert [(chunk.page_number, chunk.text) for chunk in chunks] == [(7, expected)]


def test_legacy_text_does_not_make_a_skipped_page_searchable(tmp_path):
    source = tmp_path / 'pages.jsonl'
    source.write_text(json.dumps({'page_number': 7, 'processing_status': 'skipped',
                                  'text': '不可引用的旧内容'}) + '\n', encoding='utf-8')
    with pytest.raises(RAGIndexError, match='no searchable text'):
        upload_chunks(source)
