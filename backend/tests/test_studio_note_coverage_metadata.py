"""Observed-wire replay and negative boundaries; no model call or semantic waiver."""
import copy
import json
from pathlib import Path

import pytest

from adaptive_learning import studio_note_coverage as MODULE

BASE = Path(__file__).resolve().parent / 'fixtures/studio_note_coverage_metadata'


def inputs():
    raw = json.loads((BASE / 'actual-response-02.json').read_text())
    packet = json.loads((BASE / 'actual-review-input.json').read_text())
    directory = MODULE.registry({key: value for key, value in packet.items() if key != 'claim_coverage'})
    assert directory['candidate_id'] == packet['claim_coverage']['candidate_id']
    return raw, directory



def test_actual_returned_false_normalizes_without_mutating_raw_or_losing_any_negative():
    raw, expected = inputs()
    snapshot = copy.deepcopy(raw)
    result = MODULE.parse_review(raw, expected)
    stripped = {key: value for key, value in raw.items() if key != 'type'}
    assert result == MODULE.parse_review(stripped, expected)
    assert result['passed'] is False and len(result['findings']) == 3
    assert [row['claim'] for row in result['findings']] == [row['text'] for row in expected['clauses'] if row['id'] in ['s0c1', 's1c1', 's1c2']]
    assert raw == snapshot and raw['type'] == 'json_object'
    assert raw['clause_checks'] == snapshot['clause_checks'] and raw['global_checks'] == snapshot['global_checks']


def test_marker_is_optional_without_change_to_existing_valid_wire():
    raw, expected = inputs()
    del raw['type']
    assert MODULE.parse_review(raw, expected) == MODULE.parse_review(raw, expected)


@pytest.mark.parametrize('marker', [None, True, 1, {}, [], 'object', 'json', 'JSON_OBJECT', '', 'json_object '])
def test_every_other_format_value_still_fails_closed(marker):
    raw, expected = inputs()
    raw['type'] = marker
    with pytest.raises(MODULE.CoverageError):
        MODULE.parse_review(raw, expected)


@pytest.mark.parametrize('fault', ['extra_key', 'missing_clause', 'duplicate_clause', 'foreign_clause', 'stale_candidate',
                                  'string_bool', 'missing_global', 'extra_global', 'missing_finding',
                                  'foreign_finding', 'duplicate_finding', 'unknown_code', 'contradictory_true', 'old_sparse', 'missing_top_key'])
def test_recognized_metadata_does_not_bypass_any_existing_content_contract(fault):
    raw, expected = inputs()
    if fault == 'extra_key':
        raw['confidence'] = 1
    elif fault == 'missing_clause':
        raw['clause_checks'].pop()
    elif fault == 'duplicate_clause':
        raw['clause_checks'][-1] = copy.deepcopy(raw['clause_checks'][0])
    elif fault == 'foreign_clause':
        raw['clause_checks'][0][0] = 'foreign'
    elif fault == 'stale_candidate':
        raw['candidate_id'] = '0' * 64
    elif fault == 'string_bool':
        raw['clause_checks'][0][1] = 'true'
    elif fault == 'missing_global':
        raw['global_checks'].pop('fidelity')
    elif fault == 'extra_global':
        raw['global_checks']['type'] = 'json_object'
    elif fault == 'missing_finding':
        raw['findings'].pop()
    elif fault == 'foreign_finding':
        raw['findings'][0][0] = 'other'
    elif fault == 'duplicate_finding':
        raw['findings'][1] = copy.deepcopy(raw['findings'][0])
    elif fault == 'unknown_code':
        raw['findings'][0][1] = 'APPROVE'
    elif fault == 'contradictory_true':
        raw['passed'] = True
    elif fault == 'missing_top_key':
        raw.pop('reason')
    else:
        raw = {'type': 'json_object', 'passed': False, 'reason': 'Old sparse response remains invalid.'}
    snapshot = copy.deepcopy(raw)
    with pytest.raises(MODULE.CoverageError):
        MODULE.parse_review(raw, expected)
    assert raw == snapshot


def test_valid_marker_cannot_hide_a_global_failure_or_enable_optional_omission():
    raw, expected = inputs()
    raw['global_checks']['fidelity'] = False
    raw['findings'].append(['g_fidelity', 'F'])
    result = MODULE.parse_review(raw, expected)
    assert result['passed'] is False and result['failure_scope'] == 'other_or_uncertain'
    assert len(result['findings']) == 3 and '[g_fidelity]' in result['reason']
    raw['failure_scope'] = 'optional_suggestions_only'
    with pytest.raises(MODULE.CoverageError):
        MODULE.parse_review(raw, expected)
