"""Locate immutable candidate evidence for one writing/repair task.

Retrieval is navigation only. Every selected citation still passes the unchanged
assertion, mathematical truth, provenance and scope review before publication.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from .teaching_evidence import MAX_SOURCE_CHARACTERS, TeachingEvidence, _scope_terms


def repair_evidence_pack(evidence: TeachingEvidence, *, label: str, candidate: Any,
                         feedback: Any, anchors: list[str], expansion: int = 0,
                         overview: bool = False) -> dict[str, Any]:
    keys = list(evidence.sources)
    source_terms = {key: _scope_terms(source.quote) for key, source in evidence.sources.items()}
    frequency = Counter(term for terms in source_terms.values() for term in terms)
    candidate_text = json.dumps(candidate, ensure_ascii=False)
    feedback_text = json.dumps(feedback, ensure_ascii=False)
    query = _scope_terms(label + ' ' + candidate_text + ' ' + feedback_text)
    feedback_terms = _scope_terms(feedback_text)
    mentioned = set(re.findall(r'src_[A-Za-z0-9_]+', feedback_text)) & evidence.sources.keys()
    ranked = sorted(keys, key=lambda key: (-sum(
        (2 if term in feedback_terms else 1) * math.log1p(len(keys) / frequency[term])
        for term in query & source_terms[key]), keys.index(key)))
    # This registry is already chapter-bounded. Do not hide a distant condition
    # merely because eight higher-ranked fragments filled an arbitrary count.
    limit = len(keys)
    character_budget = MAX_SOURCE_CHARACTERS
    chosen: list[str] = []

    def add(key: str) -> None:
        if (key in evidence.sources and key not in chosen and len(chosen) < limit
                and sum(len(evidence.sources[item].quote) for item in chosen)
                    + len(evidence.sources[key].quote) <= character_budget):
            chosen.append(key)

    # Own sources and reviewer-located sources remain visible, with room for
    # independently retrieved definitions instead of filling the pack with anchors.
    for key in [*sorted(mentioned), *anchors]:
        add(key)
    if overview:
        for i in range(min(10, len(keys))):
            add(keys[round(i * (len(keys) - 1) / max(1, min(10, len(keys)) - 1))])
    # Protect adjoining conditions before lower-ranked material consumes budget.
    for key in chosen[:]:
        index = keys.index(key)
        for pos in (index - 1, index + 1):
            if 0 <= pos < len(keys) and abs(evidence.sources[keys[pos]].page_number
                                          - evidence.sources[key].page_number) <= 1:
                add(keys[pos])
    for key in ranked:
        add(key)
    return {
        'sources': {key: evidence.sources[key].model_dump() for key in chosen},
        'citation_repair': {
            'target_claims_and_feedback': feedback,
            'existing_source_ids': anchors,
            'candidate_additional_source_ids': [key for key in chosen if key not in anchors],
            'instruction': '逐一核对候选原文是否支持本组件结论和必要条件；需要的依据加入本组件自己的source_ids。'
                           '候选检索不代表事实批准；章节阅读列表不能替代本题引用。',
        },
        'retrieval': {'registry_size': len(keys), 'selected': len(chosen),
                      'expansion': expansion, 'evidence_fingerprint': evidence.fingerprint,
                      'source_character_budget': character_budget,
                      'registry_characters': sum(len(source.quote) for source in evidence.sources.values()),
                      'all_registered_sources_included': len(chosen) == len(keys),
                      'selection_policy': 'complete-registered-source-v1'},
    }
