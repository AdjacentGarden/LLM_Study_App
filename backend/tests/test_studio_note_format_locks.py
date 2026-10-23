"""Offline actual02 replay and monotonic-negative guards; no model calls."""
import copy
import json
from pathlib import Path

import pytest

from adaptive_learning import studio_note_coverage as M

BASE = Path(__file__).resolve().parent / 'fixtures/studio_note_review_format'


def sample():
    raw = json.loads((BASE / 'actual-response-02.json').read_text())
    packet = json.loads((BASE / 'actual-review-input.json').read_text())
    expected = M.registry({key: value for key, value in packet.items() if key != 'claim_coverage'})
    return raw, expected



def test_real02_missing_id_false_cannot_be_program_completed_or_upgraded():
    raw, expected = sample()
    before = copy.deepcopy(raw)
    with pytest.raises(M.CoverageError):
        M.parse_review(raw, expected)
    locks = M.repair_locks(raw, expected)
    assert locks['has_negative'] and locks['passed_false']
    assert locks['clause_false'] == ['s1c2'] and locks['findings'] == [['s1c2', 'S']]
    assert locks['other_scope'] and raw == before and 'candidate_id' not in raw
    # Explicit new-response fixture, never a claimed genuine model correction.
    repaired = copy.deepcopy(raw)
    repaired['candidate_id'] = expected['candidate_id']
    result = M.validate_repair(repaired, expected, locks)
    assert result['passed'] is False and len(result['findings']) == 1 and raw == before


@pytest.mark.parametrize('fault', ['parent_upgrade', 'clause_upgrade', 'drop_finding', 'change_code', 'optional_scope', 'global_upgrade', 'global_optional'])
def test_new_complete_review_may_not_erase_same_candidate_negatives(fault):
    raw, expected = sample()
    if fault.startswith('global'):
        raw['global_checks']['fidelity'] = False
        raw['findings'].append(['g_fidelity', 'F'])
    locks = M.repair_locks(raw, expected)
    new = copy.deepcopy(raw)
    new['candidate_id'] = expected['candidate_id']
    if fault == 'parent_upgrade':
        new['passed'] = True
    elif fault == 'clause_upgrade':
        new['clause_checks'] = [[name, True] for name, _ in new['clause_checks']]
        new['findings'] = []
        new['passed'] = True
        new['failure_scope'] = 'none'
    elif fault == 'drop_finding':
        new['findings'] = []
    elif fault == 'change_code':
        new['findings'][0][1] = 'U'
    elif fault in ['optional_scope', 'global_optional']:
        new['failure_scope'] = 'optional_suggestions_only'
    else:
        new['global_checks']['fidelity'] = True
        new['findings'] = [p for p in new['findings'] if p[0] != 'g_fidelity']
    snapshot = copy.deepcopy(new)
    with pytest.raises(M.CoverageError):
        M.validate_repair(new, expected, locks)
    assert new == snapshot


@pytest.mark.parametrize('fault', ['stale', 'wrong_identity_type', 'foreign_false', 'foreign_finding', 'foreign_global', 'ambiguous_bool'])
def test_ambiguous_identity_or_unlocatable_negative_is_not_reasked(fault):
    raw, expected = sample()
    if fault == 'stale':
        raw['candidate_id'] = '0' * 64
    elif fault == 'wrong_identity_type':
        raw['candidate_id'] = None
    elif fault == 'foreign_false':
        raw['clause_checks'].append(['foreign', False])
    elif fault == 'foreign_finding':
        raw['findings'].append(['foreign', 'S'])
    elif fault == 'foreign_global':
        raw['global_checks']['foreign'] = False
    else:
        raw['clause_checks'][0][1] = 'false'
    with pytest.raises(M.CoverageError):
        M.repair_locks(raw, expected)


@pytest.mark.parametrize('scope', ['other_or_uncertain', 'optional_suggestions_only'])
def test_explicit_rejection_scope_cannot_be_format_repaired_to_true(scope):
    raw, expected = sample()
    raw['passed'] = True
    raw['global_checks'] = {key: True for key in M.GLOBALS}
    raw['clause_checks'] = [[row['id'], True] for row in expected['clauses']]
    raw['findings'] = []
    raw['failure_scope'] = scope
    locks = M.repair_locks(raw, expected)
    assert locks['has_negative']
    new = copy.deepcopy(raw)
    new['candidate_id'] = expected['candidate_id']
    new['failure_scope'] = 'none'
    with pytest.raises(M.CoverageError):
        M.validate_repair(new, expected, locks)


@pytest.mark.parametrize('scope', [[], {}])
def test_nonstring_scope_is_typed_contract_failure_not_typeerror(scope):
    raw, expected = sample()
    raw['failure_scope'] = scope
    with pytest.raises(M.CoverageError, match='ambiguous failure scope'):
        M.repair_locks(raw, expected)


def test_no_negative_missing_identity_can_only_pass_after_a_new_complete_response():
    raw, expected = sample()
    raw['passed'] = True
    raw['global_checks'] = {name: True for name in M.GLOBALS}
    raw['clause_checks'] = [[row['id'], True] for row in expected['clauses']]
    raw['findings'] = []
    raw['failure_scope'] = 'none'
    before = copy.deepcopy(raw)
    locks = M.repair_locks(raw, expected)
    assert locks['has_negative'] is False
    with pytest.raises(M.CoverageError):
        M.parse_review(raw, expected)
    new = copy.deepcopy(raw)
    new['candidate_id'] = expected['candidate_id']  # Explicit mechanical new-response fixture.
    assert M.validate_repair(new, expected, locks)['passed'] is True
    assert raw == before and 'candidate_id' not in raw
