"""Native scheduling and SQLite boundaries; controlled reviews do not certify content."""
from collections import Counter
from copy import deepcopy
from types import MethodType, SimpleNamespace

import pytest
from test_teaching_content import course, lesson_candidate, point_candidate

from adaptive_learning.llm.client import LLMTimeoutError
from adaptive_learning.personalization import teaching_repairs as repairs
from adaptive_learning.personalization.teaching_content import (
    _FACT_REUSE,
    TeachingContentError,
    TeachingContentGenerator,
)
from adaptive_learning.personalization.teaching_review_reuse import fingerprint
from adaptive_learning.personalization.teaching_unit_engine import (
    ENGINE_VERSION,
    UnitEngine,
    _UnitRejected,
)


class EmptyScope:
    def plan(self, inputs, evidence):
        return {row['point_id']: [] for row in inputs}


class Control:
    def __init__(self):
        self.writes = Counter()
        self.reviews = Counter()
        self.post_calls = Counter()
        self.reject_once = None
        self.unknown_writer = None
        self.assembly_calls = 0
        self.apply_calls = 0
        self.whole_course_veto = None


class ScheduledControlEngine(UnitEngine):
    """Retain native scheduling, schema checks and writes; replace paid boundaries."""
    def __init__(self, generator, bundle, chapter, evidence, control):
        super().__init__(generator, bundle, chapter, evidence)
        self.control = control

    def _write_one(self, unit):
        key, kind = unit['unit_id'], unit['work']['kind']
        self.control.writes[key] += 1
        if key == self.control.unknown_writer:
            def post():
                self.control.post_calls[key] += 1
                raise LLMTimeoutError('controlled unknown writer outcome')
            return self.run.structured(post, {'unit': key, 'attempt': unit['attempts']})
        if unit['work']['owner_id']:
            point = point_candidate(self.by_id[unit['work']['owner_id']])
            value = ({name: point[name] for name in ('title', 'explanation', 'source_ids')}
                     if kind == 'explanation' else point[kind])
        else:
            lesson = lesson_candidate()
            if kind in ('opening', 'summary'):
                value = {'content': lesson[kind], 'source_ids': ['s1']}
            else:
                group = 'worked_examples' if kind.startswith('example') else 'checkpoints'
                value = deepcopy(lesson[group][(int(kind.rsplit('_', 1)[1]) - 1) % 2])
        # A genuine controlled candidate revision, not a same-output escape.
        if unit['candidate']:
            field = next((key for key in ('prompt', 'problem', 'question', 'front', 'content', 'explanation')
                          if key in value), None)
            if field:
                value[field] += '请结合原文适用前提说明。' * unit['attempts']
        return value

    def _review_one(self, unit):
        key = unit['unit_id']
        self.control.reviews[key] += 1
        if key == self.control.reject_once and self.control.reviews[key] == 1:
            raise _UnitRejected({'issue': 'controlled independent content veto'}, 'teacher')
        unit['work']['approval'] = {
            'candidate_hash': fingerprint(unit['candidate']),
            'evidence_fingerprint': self.evidence.fingerprint,
            'controlled_review': True,
        }

    def _assemble(self, rows, plans):
        self.control.assembly_calls += 1
        assert {row['unit_id'] for row in rows} == {spec['unit_id'] for spec in self._specs()}
        assert all(row['status'] == 'approved' for row in rows)
        assert all(row['work']['approval']['candidate_hash'] == fingerprint(row['candidate']) for row in rows)
        if self.control.whole_course_veto:
            unit = next(row for row in rows if row['unit_id'] == self.control.whole_course_veto)
            self._failed(unit, _UnitRejected({'issue': 'controlled cross-component collision'}, 'whole_course'),
                         unit['work']['mode'])
            raise repairs.TeachingRepairPending(5)
        return self.bundle


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('DATABASE_HOST', raising=False)
    monkeypatch.setenv('TEACHING_REVIEW_CONCURRENCY', '1')
    clock = [1000.0]
    monkeypatch.setattr(repairs.time, 'time', lambda: clock[0])
    bundle, chapter, evidence = course(1)
    path = tmp_path / 'course.sqlite3'
    control = Control()

    def open_run():
        store = repairs.TeachingRepairStore(path)
        run = store.open('isolated-user-book', chapter.chapter_id,
                         {'engine': ENGINE_VERSION, 'evidence_fingerprint': evidence.fingerprint})
        return store, run

    def engine(run):
        generator = TeachingContentGenerator(object(), review_client=object(), scope_planner=EmptyScope(),
                                            review_identity={'provider': 'controlled', 'model': 'offline'})
        return ScheduledControlEngine(generator, bundle, chapter, evidence, control)

    def advance():
        store, run = open_run()
        token = repairs.current_repair.set(run)
        proof_token = _FACT_REUSE.set(None)
        try:
            return engine(run).advance()
        finally:
            _FACT_REUSE.reset(proof_token)
            repairs.current_repair.reset(token)

    return SimpleNamespace(clock=clock, bundle=bundle, chapter=chapter, evidence=evidence,
                           control=control, open_run=open_run, engine=engine, advance=advance)


def test_approved_siblings_persist_and_rejected_component_alone_resumes_with_attempt_count(rig):
    target = rig.bundle.knowledge_points[0].point_id + '.practice'
    rig.control.reject_once = target
    with pytest.raises(repairs.TeachingRepairPending):
        rig.advance()
    _, first_run = rig.open_run()
    before = {row['unit_id']: row for row in first_run.units()}
    assert len(before) == 11 and sum(row['status'] == 'approved' for row in before.values()) == 10
    assert rig.control.assembly_calls == 0
    assert all(count == 1 for count in rig.control.writes.values())
    assert all(row['attempts'] == 1 for row in before.values())
    rig.clock[0] += 200
    assert rig.advance() == rig.bundle
    _, reopened = rig.open_run()
    after = {row['unit_id']: row for row in reopened.units()}
    assert all(row['status'] == 'approved' for row in after.values())
    for key in before:
        if key != target:
            assert after[key]['candidate_hash'] == before[key]['candidate_hash']
            assert after[key]['attempts'] == before[key]['attempts'] == 1
            assert rig.control.writes[key] == 1
    assert after[target]['attempts'] == 2 and rig.control.writes[target] == 2
    assert rig.control.assembly_calls == 1


def test_unknown_writer_stays_fenced_after_restart_without_repaying_or_blocking_siblings(rig):
    target = rig.bundle.knowledge_points[0].point_id + '.practice'
    rig.control.unknown_writer = target
    with pytest.raises(repairs.TeachingRepairPending):
        rig.advance()
    store, run = rig.open_run()
    assert run.submission_blocked(target)
    assert rig.control.post_calls[target] == 1
    assert sum(row['status'] == 'approved' for row in run.units()) == 10
    store.recover()
    rig.clock[0] += 200
    with pytest.raises(repairs.TeachingRepairPending):
        rig.advance()
    assert rig.control.post_calls[target] == 1 and rig.control.writes[target] == 1
    assert rig.control.assembly_calls == 0


def test_exact_responded_request_is_reused_across_store_reopen(rig):
    _, run = rig.open_run()
    unit = repairs.current_unit.set('controlled-unit')
    calls = []
    try:
        assert run.structured(lambda: calls.append(1) or {'actual': 'response'}, {'request': 'exact'}) == {'actual': 'response'}
        _, reopened = rig.open_run()
        assert reopened.structured(lambda: calls.append(2) or {}, {'request': 'exact'}) == {'actual': 'response'}
    finally:
        repairs.current_unit.reset(unit)
    assert calls == [1]


@pytest.mark.parametrize('final_verdict', ['unavailable', 'rejected', 'accepted'])
def test_native_final_assembly_requires_complete_review_before_apply_and_publication_marker(rig, final_verdict):
    # Complete the controlled per-component reviews first, then exercise the
    # native final gate separately from unrelated layout/duplicate heuristics.
    rig.advance()
    _, run = rig.open_run()
    token = repairs.current_repair.set(run)
    try:
        engine = rig.engine(run)
    finally:
        repairs.current_repair.reset(token)
    seen = []
    engine.g._validate_distinct_points = lambda points: None
    engine.g._validate_lesson = lambda lesson, points, evidence: None

    def review(*args, **kwargs):
        seen.append('final_review')
        if final_verdict == 'unavailable':
            raise TeachingContentError('controlled missing final review')
        return (SimpleNamespace(model_dump=lambda: {'source_entailment': final_verdict == 'accepted'}),
                SimpleNamespace(model_dump=lambda: {'accepted': True}, _rubric_alignment={}))

    def apply(*args, **kwargs):
        seen.append('apply')
        return rig.bundle.model_copy(deep=True)

    engine.g._review_candidate = review
    engine.g._fact_feedback = lambda *args: ([{'issues': ['controlled final factual veto']}]
                                            if final_verdict == 'rejected' else [])
    engine.g._review_feedback = lambda *args: []
    engine.g._build_repair_plan = lambda *args: SimpleNamespace(
        allowed_points={}, allowed_lesson={'summary'},
        requirements=SimpleNamespace(rubric_only_points=set()))
    engine.g._scoped_feedback = lambda feedback, *args: deepcopy(feedback)
    engine.g._apply = apply
    if final_verdict == 'accepted':
        result = UnitEngine._assemble(engine, run.units(), run.load()['plans'])
        assert result.teaching_fact_review['source_entailment'] is True
        assert run.load()['published_candidate_hash']
        assert seen == ['final_review', 'apply']
    else:
        with pytest.raises(repairs.TeachingRepairPending):
            UnitEngine._assemble(engine, run.units(), run.load()['plans'])
        assert seen == ['final_review'] and 'published_candidate_hash' not in run.load()
        if final_verdict == 'rejected':
            rows = {row['unit_id']: row for row in run.units()}
            assert rows['lesson.summary']['status'] != 'approved'
            assert sum(row['status'] == 'approved' for row in rows.values()) == 10


@pytest.mark.parametrize('gate', ['fact', 'teacher', 'rubric', 'all_pass'])
def test_native_unit_review_requires_all_three_applicable_independent_gates(rig, gate):
    _, run = rig.open_run()
    token = repairs.current_repair.set(run)
    try:
        engine = rig.engine(run)
    finally:
        repairs.current_repair.reset(token)
    spec = next(spec for spec in engine._specs() if spec['kind'] == 'practice')
    run.initialize_units([spec])
    unit = run.units()[0]
    unit['candidate'] = point_candidate(engine.by_id[spec['owner_id']])['practice']
    seen = []

    def fact(self, row):
        seen.append('fact')
        if gate == 'fact':
            raise _UnitRejected({'issue': 'controlled factual veto'}, 'fact')
        return {'source_entailment': True}

    def teacher(**kwargs):
        seen.append('teacher')
        return {'accepted': gate != 'teacher', 'redesign_ok': True,
                'issues': ['controlled teacher veto'] if gate == 'teacher' else []}

    def rubric(batch):
        seen.append('rubric')
        return SimpleNamespace(accepted=gate != 'rubric', to_dict=lambda: {'accepted': gate != 'rubric'})

    engine._fact_one = MethodType(fact, engine)
    engine.g._structured = teacher
    engine.g._request_rubric_review = rubric
    if gate == 'all_pass':
        UnitEngine._review_one(engine, unit)
        assert unit['work']['approval']['candidate_hash'] == fingerprint(unit['candidate'])
    else:
        with pytest.raises(_UnitRejected) as failure:
            UnitEngine._review_one(engine, unit)
        assert failure.value.stage == gate and 'approval' not in unit['work']
    assert seen == {'fact': ['fact'], 'teacher': ['fact', 'teacher'],
                    'rubric': ['fact', 'teacher', 'rubric'], 'all_pass': ['fact', 'teacher', 'rubric']}[gate]


def test_repeated_whole_course_veto_must_eventually_trigger_redesign(rig):
    target = 'lesson.example_1'
    rig.control.whole_course_veto = target
    modes = []
    for _ in range(4):
        with pytest.raises(repairs.TeachingRepairPending):
            rig.advance()
        _, run = rig.open_run()
        row = next(row for row in run.units() if row['unit_id'] == target)
        modes.append((row['work']['mode'], row['work']['repair_failures']))
        rig.clock[0] += 200
    assert any(mode == 'redesign' for mode, _ in modes), modes
