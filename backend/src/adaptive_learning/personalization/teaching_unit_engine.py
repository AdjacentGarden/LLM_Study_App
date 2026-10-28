"""Independently scheduled course components with source-located repair tasks."""
from __future__ import annotations

import json
import re
import time
import uuid
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..database import DATABASE_ERRORS
from ..llm.teaching_parallel import ordered_review_map
from .course_progress import CourseStage, report_course_stage
from .teaching_repair_evidence import repair_evidence_pack
from .teaching_repairs import TeachingRepairPending, current_repair, current_unit, invalidate_repair_response
from .teaching_review_reuse import VerifiedComponentReuse, fingerprint

ENGINE_VERSION = 'isolated-source-located-units-v2'
PEDAGOGICAL_POLICY = '''教学形式采用以下统一口径，事实和引用审核标准保持不变：
知识点完整标签用于核对整组讲解、闪卡、练习的联合覆盖，不要求每张闪卡或每道练习重复所有子目标。
讲解讲清核心知识与必要条件；闪卡可以只有一个中心问题，练习只需围绕该知识点的明确问项完整正确作答。
不得为覆盖标签其他子项而强行增加题干未问的评分目标；必要时由其他教学组件承载这些内容。
不能因参考答案简洁、没有重复讲解中已教授的其他子项或省略显然的等价式而单独拒绝。
讲解、导入、总结允许沿用正确的数学公式、概念、结论和合理组织顺序；结构或措辞相近不是独立拒绝理由。
重新设计的实质变化检查用于题目问项；非题目正文只按内容正确、条件完整、引用支持及教学目标审核。
实际使用的每项事实仍须有原文依据，计算与推理必须正确，题设不能矛盾或泄露所问答案。'''


class _TeacherVerdict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    accepted: StrictBool
    redesign_ok: StrictBool
    issues: list[str] = Field(max_length=12)


class _UnitRejected(RuntimeError):
    def __init__(self, feedback: Any, stage: str) -> None:
        super().__init__('当前组件需要修正')
        self.feedback, self.stage = feedback, stage


def _question(candidate: dict[str, Any]) -> str:
    return next((candidate[key] for key in ('prompt', 'problem', 'question', 'front')
                 if isinstance(candidate.get(key), str)), '')


def _question_shape(candidate: dict[str, Any]) -> str:
    # Exact text or a mere numerical substitution cannot satisfy redesign.
    text = re.sub(r'[\s，。；！？、,:;!?]+', '', _question(candidate)).lower()
    return re.sub(r'\d+(?:\.\d+)?', '#', text)


class UnitEngine:
    def __init__(self, generator: Any, bundle: Any, chapter: Any, evidence: Any) -> None:
        from .teaching_content import _FACT_REUSE
        self.g, self.bundle, self.chapter, self.evidence = generator, bundle, chapter, evidence
        self.run = current_repair.get()
        assert self.run is not None
        self.state = self.run.load()
        self.inputs = generator._inputs(bundle, evidence)
        self.by_id = {item['point_id']: item for item in self.inputs}
        self.fact_reuse = _FACT_REUSE

    def _specs(self) -> list[dict[str, Any]]:
        specs = []
        for item in self.inputs:
            for kind in ('explanation', 'flashcard', 'practice'):
                specs.append({'unit_id': item['point_id'] + '.' + kind,
                              'owner_id': item['point_id'], 'kind': kind,
                              'label': item['label'], 'focus_ids': [item['point_id']]})
        for kind in ('opening', 'summary', 'example_1', 'example_2', 'example_3',
                     'checkpoint_1', 'checkpoint_2', 'checkpoint_3'):
            broad = kind in ('opening', 'summary')
            slot = int(kind.rsplit('_', 1)[-1]) - 1 if not broad else 0
            index = min(len(self.inputs) - 1, (slot + (1 if kind.startswith('checkpoint') else 0))
                        * max(1, len(self.inputs) // 4))
            focus = self.inputs if broad else [self.inputs[index]]
            specs.append({'unit_id': 'lesson.' + kind, 'owner_id': None, 'kind': kind,
                          'label': self.chapter.title if broad else focus[0]['label'],
                          'focus_ids': [row['point_id'] for row in focus]})
        return specs

    def advance(self) -> Any:
        from .teaching_content import _AuditedScopeClient, TeachingScopePlanner, validate_scope_plan
        if not self.g.client or not self.g.review_client:
            raise TeachingRepairPending(30)
        if not self.state.get('plans'):
            report_course_stage(CourseStage.PLANNING)
            planner = self.g.scope_planner
            if type(planner) is TeachingScopePlanner and planner.client is self.g.client:
                planner = TeachingScopePlanner(_AuditedScopeClient(self.g), max_attempts=planner.max_attempts)
            self.state['plans'] = validate_scope_plan(planner.plan(self.inputs, self.evidence),
                                                       self.inputs, self.evidence)
            self.state['engine'] = ENGINE_VERSION
            self.run.save_state(self.state)
        plans = validate_scope_plan(self.state['plans'], self.inputs, self.evidence)
        for item in self.inputs:
            item['scope_conditions'] = plans[item['point_id']]
        self.fact_reuse.set(VerifiedComponentReuse(
            source_texts={key: value.quote for key, value in self.evidence.sources.items()},
            context=self.g._reuse_context(self.chapter, self.evidence, self.inputs), persistence=self.run))
        self.run.initialize_units(self._specs())
        rows = self.run.units()
        due = [row for row in rows if row['status'] != 'approved' and row['next_retry_at'] <= time.time()]
        report_course_stage(CourseStage.WRITING if all(not row['candidate'] for row in rows)
                            else CourseStage.REFINING)
        # Every leaf handles its own content, format and service failure. A bad
        # item cannot cancel the map or prevent later items from being submitted.
        ordered_review_map(self._advance_one, due)
        rows = self.run.units()
        self.state['passes'] = int(self.state.get('passes', 0)) + 1
        self.run.save_state(self.state)
        if any(row['status'] != 'approved' for row in rows):
            delay = min((max(1, row['next_retry_at'] - time.time()) for row in rows
                         if row['status'] != 'approved'), default=5)
            raise TeachingRepairPending(delay)
        return self._assemble(rows, plans)

    def _advance_one(self, unit: dict[str, Any]) -> None:
        from .teaching_content import _AUDIT_CONTEXT
        token = current_unit.set(unit['unit_id'])
        audit_token = _AUDIT_CONTEXT.set({**(_AUDIT_CONTEXT.get() or {}), 'unit_id': unit['unit_id']})
        try:
            work = unit['work']
            if self.run.submission_blocked(unit['unit_id']):
                work['recovery_checks'] = int(work.get('recovery_checks', 0)) + 1
                if (work.get('phase') == 'review' and unit['candidate'] and
                        work['recovery_checks'] >= 2 and self.run.retire_lost_review(unit['unit_id'])):
                    work.update(mode='redesign', phase='write',
                                semantic_feedback={},
                                feedback={'issue': '旧版本审核请求已在本地结束，但响应不可恢复；旧请求永久禁止重放。'
                                                   '废弃旧版本，完整重新设计不同的题设或问项，并重做答案、评分及引用。'})
                    self.g._audit('lost_review_retired_for_new_design', unit_id=unit['unit_id'],
                                  old_request_replay_allowed=False)
                else:
                    self.run.write_unit(unit, status='waiting', delay=120)
                    return
            mode = work['mode']
            unit['attempts'] += 1
            work['attempt_nonce'] = uuid.uuid4().hex
            if mode == 'redesign':
                work['history'] = [*work.get('history', []), {
                    'revision': work['revision'], 'candidate': unit['candidate'],
                    'feedback': work.get('feedback', []),
                }][-6:]
                work['revision'] += 1
                work['phase'] = 'write'
                work['rejected_design'] = deepcopy(unit['candidate'])
                work['redesign_pending_review'] = True
            self.run.write_unit(unit, status='running')
            try:
                if work.get('phase', 'write') == 'write' or not unit['candidate']:
                    must_redesign = (mode == 'redesign' or (work.get('redesign_pending_review') and
                        _question_shape(unit['candidate']) == _question_shape(work.get('rejected_design') or {})))
                    candidate = self._write_one(unit)
                    self._validate_one(unit, candidate)
                    if unit['candidate'] and fingerprint(candidate) == fingerprint(unit['candidate']):
                        raise _UnitRejected({'issue': '本轮返回与被拒组件完全相同，包括引用；必须完成repair_task列出的字段修正。'}, 'unchanged')
                    if must_redesign and _question(unit['candidate']) and (
                            _question_shape(candidate) == _question_shape(work.get('rejected_design') or unit['candidate'])):
                        raise _UnitRejected({'issue': '重新设计必须改变题设或实际问项；仅替换数字、措辞或原样返回不算完成。'}, 'redesign_unchanged')
                    work['phase'] = 'review'
                    self.run.write_unit(unit, status='running', candidate=candidate)
                    self.g._audit('unit_draft_saved', revision=work['revision'], mode=mode,
                                  candidate_sha256=fingerprint(candidate), approved=False)
                self._review_one(unit)
                work.update(failures=0, repair_failures=0, feedback=[], phase='accepted',
                            redesign_pending_review=False)
                self.run.write_unit(unit, status='approved')
                self.g._audit('unit_approved', revision=work['revision'],
                              candidate_sha256=fingerprint(unit['candidate']))
            except DATABASE_ERRORS:
                raise
            except Exception as error:
                self._failed(unit, error, mode)
        finally:
            _AUDIT_CONTEXT.reset(audit_token)
            current_unit.reset(token)

    def _failed(self, unit: dict[str, Any], error: Exception, attempted_mode: str) -> None:
        work = unit['work']
        work['failures'] = int(work.get('failures', 0)) + 1
        # Initial draft/redesign rejection starts a new two-repair cycle. All
        # subsequent failures count, including format/service/unchanged output.
        if attempted_mode == 'repair':
            work['repair_failures'] = int(work.get('repair_failures', 0)) + 1
        else:
            work['repair_failures'] = 0
        work['mode'] = 'redesign' if work['repair_failures'] >= 2 else 'repair'
        if isinstance(error, _UnitRejected):
            if error.stage in ('fact', 'teacher', 'rubric', 'whole_course', 'assembly'):
                work['semantic_feedback'] = deepcopy(error.feedback)
                work['feedback'] = error.feedback
            else:
                # A format/unchanged failure must not erase the actual rejected
                # claim or redesign obligation that the next writer must fix.
                work['feedback'] = {'current_failure': error.feedback,
                                    'unresolved_review': work.get('semantic_feedback', {})}
            work['failure_stage'] = error.stage
            work['phase'] = 'write'
            invalidate_repair_response()
        else:
            work['feedback'] = {'issue': str(error)[:1200],
                                'detail': getattr(error, 'review_feedback', None),
                                'type': type(error).__name__,
                                'unresolved_review': work.get('semantic_feedback', {})}
            work['failure_stage'] = 'request_or_format'
            # Received malformed objects can be replaced. Unknown requests stay
            # fenced; the next pass observes them without another paid POST.
            invalidate_repair_response()
        delay = min(120, 5 * 2 ** min(work['failures'] - 1, 5))
        if isinstance(error, TeachingRepairPending):
            delay = max(delay, error.retry_after)
        blocked = self.run.submission_blocked(unit['unit_id'])
        self.run.write_unit(unit, status='waiting' if blocked else 'pending', delay=delay)
        self.g._audit('unit_retry_scheduled', unit_id=unit['unit_id'], revision=work['revision'],
                      error_type=type(error).__name__, next_mode=work['mode'],
                      repair_failures=work['repair_failures'], retry_after_seconds=delay,
                      feedback=work['feedback'], blocked=blocked)

    def _pack(self, unit: dict[str, Any]) -> dict[str, Any]:
        work = unit['work']
        anchors = list(unit['candidate'].get('source_ids', []))
        if not anchors:
            anchors = list(dict.fromkeys(key for owner in work['focus_ids']
                                         for key in self.evidence.point_sources.get(owner, [])))
        return repair_evidence_pack(self.evidence, label=work['label'], candidate=unit['candidate'],
                                    feedback=work.get('feedback', []), anchors=anchors,
                                    expansion=int(work.get('failures', 0)),
                                    overview=work['kind'] in ('opening', 'summary'))

    def _write_one(self, unit: dict[str, Any]) -> dict[str, Any]:
        from .teaching_content import GENERATION_PROMPT
        work = unit['work']
        kind = work['kind']
        shape = deepcopy(self.g._writer_shapes()[kind.split('_')[0]])
        if kind in ('opening', 'summary'):
            shape = {'content': shape, 'source_ids': ['真实来源ID']}
        owner = work['owner_id']
        if kind == 'flashcard':
            shape['card_id'] = self.by_id[owner]['card_id']
        elif kind == 'practice':
            shape['item_id'] = self.by_id[owner]['item_id']
        pack = self._pack(unit)
        payload = {
            'engine': ENGINE_VERSION, 'epoch': self.run.identity['epoch'],
            'unit_id': unit['unit_id'], 'attempt_nonce': work['attempt_nonce'],
            'mode': work['mode'], 'revision': work['revision'],
            'chapter_title': self.chapter.title, 'knowledge_goal': work['label'],
            'scope_conditions': {owner: self.by_id[owner]['scope_conditions'] for owner in work['focus_ids']},
            'repair_task': {
                'component': unit['unit_id'], 'failure': work.get('feedback', []),
                'editable_fields': list(shape),
                'require_effective_change': bool(unit['candidate']),
                'must_redesign_question': work['mode'] == 'redesign' or bool(work.get('redesign_pending_review') and
                    _question_shape(unit['candidate']) == _question_shape(work.get('rejected_design') or {})),
                'instruction': '修复引用时，明确核对并修改本题自身source_ids；阅读目录不算本题引用。'
                               '重新设计时重构题设或问项，并同步重做答案、评分和引用；解除旧的rubric_only编辑限制。',
            },
            'previous_candidate': unit['candidate'], 'rejected_design': work.get('rejected_design'),
            'prior_failed_designs': [{'candidate': entry.get('candidate'), 'feedback': entry.get('feedback')}
                                     for entry in work.get('history', [])][-6:],
            'output_shape': {'component': shape}, **pack,
        }
        prompt = GENERATION_PROMPT + '''\n当前为独立组件任务，优先采用本请求output_shape。
只返回一个JSON对象，唯一顶层字段component，不能返回points/lesson或其他组件。
组件为opening/summary时返回content与source_ids。其他组件保留其原ID。
只按knowledge_goal和本组件sources中的明确原文写作，宁可简洁完整，不增加未引用的知识。
mode=redesign时必须构造新的题设或实际问项，不能照抄旧题、只换数字或措辞；同步重写答案、评分和引用。
对照prior_failed_designs避免反复使用已经失败的作图结构、情境和问法；必要时切换为辨析、等式应用或条件判断。
优先设计题意简明、问项少、可直接由现有明确原文支持的小题，不为难度或情境丰富而增加额外前提。
repair_task.must_redesign_question=true时，即使mode=repair也必须完成上述重设；非题目组件则完整重写讲解或总结。
mode=repair时落实repair_task的每项缺口；正确题干可以保留，但缺失的本题引用必须实际补齐。
引用的来源只能选sources已有ID，先核对其结论和条件；检索命中不代表已经获得事实批准。'''
        raw = self.g._structured(system=prompt, user=json.dumps(payload, ensure_ascii=False), max_tokens=5000)
        if not isinstance(raw, dict) or set(raw) != {'component'} or not isinstance(raw['component'], dict):
            raise _UnitRejected({'issue': '仅返回output_shape要求的完整component对象。'}, 'writer_format')
        return raw['component']

    def _validate_one(self, unit: dict[str, Any], candidate: dict[str, Any]) -> None:
        from .teaching_content import (_Card, _Checkpoint, _Example, _ExplanationPatch, _Practice,
                                       _check_text, _citations)
        work = unit['work']
        kind = work['kind']
        if kind in ('opening', 'summary'):
            if set(candidate) != {'content', 'source_ids'} or not isinstance(candidate['content'], str):
                raise ValueError('导入/总结须返回content与source_ids')
            low, high = (12, 300) if kind == 'opening' else (30, 680)
            if not low <= len(candidate['content']) <= high:
                raise ValueError('导入/总结长度不符合组件要求')
        else:
            model = {'explanation': _ExplanationPatch, 'flashcard': _Card, 'practice': _Practice,
                     'example': _Example, 'checkpoint': _Checkpoint}[kind.split('_')[0]]
            model.model_validate(candidate)
        _citations(candidate['source_ids'], self.evidence)
        if kind == 'practice' and candidate['item_id'] != self.by_id[work['owner_id']]['item_id']:
            raise ValueError('练习ID必须保持不变')
        if kind == 'flashcard' and candidate['card_id'] != self.by_id[work['owner_id']]['card_id']:
            raise ValueError('闪卡ID必须保持不变')
        def check(value: Any) -> None:
            if isinstance(value, str):
                _check_text(value)
            elif isinstance(value, list):
                for item in value:
                    check(item)
        for key, value in candidate.items():
            if key not in ('source_ids', 'item_id', 'card_id'):
                check(value)

    def _scaffold_point(self, owner: str) -> Any:
        from .teaching_content import _Point
        item = self.by_id[owner]
        ids = self.evidence.point_sources[owner][:1]
        # Unselected placeholders are removed from the fact request below and
        # can never be approved, stored as real content, or published.
        return _Point.model_validate({'point_id': owner, 'title': '内部结构',
            'explanation': '仅供构造内部结构的未生成内容，不作为事实或教学内容。' * 4,
            'source_ids': ids, 'flashcard': {'card_id': item['card_id'], 'front': '内部未生成占位问题',
                'back': '未生成', 'source_ids': ids},
            'practice': {'item_id': item['item_id'], 'prompt': '内部未生成占位问题',
                'expected_answer': '未生成', 'rubric': ['未生成'], 'source_ids': ids}})

    def _scaffold_lesson(self) -> Any:
        from .teaching_content import _Lesson
        ids = [next(iter(self.evidence.sources))]
        return _Lesson.model_validate({
            'opening': '本组件尚未生成，此处仅供内部结构使用。',
            'summary': '本组件尚未生成，此处仅供内部结构使用，不属于任何已经审核或可以发布的教学内容。',
            'reading_source_ids': ids,
            'worked_examples': [{'title': '内部结构', 'problem': '内部未生成占位问题',
                'steps': ['未生成第一步', '未生成第二步'], 'answer': '未生成', 'source_ids': ids} for _ in range(3)],
            'checkpoints': [{'question': '内部未生成占位问题', 'expected_answer': '未生成',
                             'source_ids': ids} for _ in range(3)]})

    def _fact_job(self, unit: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        from .teaching_content import _Point, _Lesson
        owner, kind = unit['work']['owner_id'], unit['work']['kind']
        if owner:
            data = self._scaffold_point(owner).model_dump()
            if kind == 'explanation':
                data.update(unit['candidate'])
            else:
                data[kind] = unit['candidate']
            point = _Point.model_validate(data)
            job = self.g._build_fact_jobs([point], None, self.inputs, self.chapter, self.evidence, [])[0]
            payload = job['payload']
            row = payload['candidate']['points'][0]
            part = row['component_evidence'][kind]
            keep = {'point_id', 'scope_boundaries', 'component_evidence'} | (
                {'title', 'explanation', 'source_ids'} if kind == 'explanation' else {kind})
            payload['candidate']['points'] = [{key: value for key, value in row.items() if key in keep}]
            payload['candidate']['points'][0]['component_evidence'] = {kind: part}
            payload['output_contract']['component_ids_by_point'] = {owner: [kind]}
            fields = self.g._point_component_fields(point, kind)
        else:
            data = self._scaffold_lesson().model_dump()
            if kind in ('opening', 'summary'):
                data[kind] = unit['candidate']['content']
                data['reading_source_ids'] = unit['candidate']['source_ids']
            else:
                group = 'worked_examples' if kind.startswith('example') else 'checkpoints'
                data[group][int(kind.rsplit('_', 1)[1]) - 1] = unit['candidate']
            lesson = _Lesson.model_validate(data)
            job = self.g._build_fact_jobs([], lesson, self.inputs, self.chapter, self.evidence, [],
                                          lesson_component_ids={kind})[0]
            payload = job['payload']
            part = payload['lesson_components'][kind]
            fields = self.g._lesson_component_fields(lesson, kind)
        envelope = {'owner_id': owner or 'lesson', 'component_id': kind, 'fields': fields,
                    'source_ids': unit['candidate']['source_ids'],
                    'boundaries': self.g._lesson_expanded_boundaries(part),
                    'navigation': self.g._candidate_navigation_identity(part)}
        return job, part, envelope

    def _fact_one(self, unit: dict[str, Any]) -> dict[str, Any]:
        from .teaching_content import FACT_REVIEW_PROMPT, _ComponentFactSelection
        from .teaching_fact_wire import compact_fact_payload
        job, part, envelope = self._fact_job(unit)
        owner, kind = unit['work']['owner_id'], unit['work']['kind']
        cache = self.fact_reuse.get()
        found = cache.get(envelope) if cache else None
        if found:
            self.g._audit('unit_fact_proof_reused', reuse_key=found[0])
            return found[1]
        if unit['work'].get('failure_stage') in ('request_or_format', 'fact_format'):
            job['payload']['format_feedback'] = [{
                'validation_error': json.dumps(unit['work'].get('feedback', {}), ensure_ascii=False)[:1800],
                'instruction': '重新完整审核当前同一组件；每条判为通过的事实断言必须选择本组件已引用原文的真实support_span_ids。'
                               '找不到支持时按原规则返回否决与具体finding，不能省略断言或范围矩阵，不能把真实事实改标为非事实，不能改写候选。',
            }]
        raw = self.g._structured(_review=True, system=FACT_REVIEW_PROMPT,
            user=json.dumps(compact_fact_payload(job['payload']), ensure_ascii=False), max_tokens=10000)
        if not isinstance(raw, dict) or set(raw) != {'components'} or not isinstance(raw['components'], list) or len(raw['components']) != 1:
            raise _UnitRejected({'issue': '事实审核须恰好返回本组件的一个完整components记录。'}, 'fact_format')
        row = deepcopy(raw['components'][0])
        if row.get('point_id') != owner or row.get('component_id') != kind:
            raise ValueError('事实审核返回了其他组件ID')
        self.g._preserve_lesson_negative_findings(row, job['payload'], self.evidence, job['spans'])
        self.g._normalize_component_selection(row, job['payload'], self.evidence, job['spans'])
        selection = _ComponentFactSelection.model_validate(
            {key: value for key, value in row.items() if key not in ('point_id', 'component_id')})
        self.g._validate_boundary_checks(selection, envelope['fields'], envelope['boundaries'])
        self.g._validate_component_findings(selection, envelope['fields'],
                                             set(envelope['source_ids']), self.evidence)
        issues = [finding.issue for finding in selection.findings]
        added: list[str] = []
        proof = self.g._materialize_proof(selection, set(envelope['source_ids']), self.evidence,
                                           job['spans'], issues, added, unit['unit_id'])
        if not proof.source_entailment or not proof.scope_exceptions or proof.findings:
            raise _UnitRejected({'issues': [*issues, *added],
                'findings': [finding.model_dump() for finding in proof.findings],
                'missing_citation_source_ids': list(proof._missing_citation_source_ids)}, 'fact')
        if cache and proof.source_ids:
            cache.remember(envelope, proof.model_dump())
        return proof.model_dump()

    def _review_one(self, unit: dict[str, Any]) -> None:
        from .teaching_content import REVIEW_PROMPT, QUESTION_REVIEW_POLICY
        from .rubric_alignment import make_rubric_alignment_batch
        fact = self._fact_one(unit)
        pack = self._pack(unit)
        # Always include every current own citation for the independent teacher.
        for key in unit['candidate']['source_ids']:
            pack['sources'][key] = self.evidence.sources[key].model_dump()
        payload = {'unit_id': unit['unit_id'], 'kind': unit['work']['kind'],
                   'knowledge_goal': unit['work']['label'], 'candidate': unit['candidate'],
                   'must_check_redesign': bool(_question(unit['candidate'])) and unit['work'].get('redesign_pending_review', False),
                   'rejected_design': unit['work'].get('rejected_design'), **pack}
        prompt = REVIEW_PROMPT.split('仅返回严格 JSON')[0] + '\n' + QUESTION_REVIEW_POLICY + '\n' + PEDAGOGICAL_POLICY + '''\n本次只审核unit_id指定的一个独立组件。
不得因为其他组件尚未提供而否决；知识、计算、题设一致性、引用支持和必要条件标准不变。
不要返回整课points/lesson结构，只返回{"accepted":true,"redesign_ok":true,"issues":[]}。
must_check_redesign=true时，核对题设或问项确实重新设计，不能仅换措辞、数字或顺序；否则redesign_ok=false。
非题目组件不以结构或措辞相似为由拒绝；must_check_redesign=false时redesign_ok=true。
拒绝必须具体定位本组件真实字段的问题，列出可依原文完成的修改方向；可选风格建议不列入issues。
接受时issues必须为空，拒绝或redesign_ok=false时issues必须非空。'''
        raw = self.g._structured(_review=True, system=prompt,
                                 user=json.dumps(payload, ensure_ascii=False), max_tokens=3500)
        teacher = _TeacherVerdict.model_validate(raw)
        if teacher.accepted and teacher.redesign_ok and not teacher.issues:
            pass
        elif teacher.issues:
            raise _UnitRejected(teacher.model_dump(), 'teacher')
        else:
            raise ValueError('独立组件审核拒绝时必须给出具体issues')
        rubric = None
        if unit['work']['kind'] == 'practice':
            item = unit['candidate']
            result = self.g._request_rubric_review(make_rubric_alignment_batch([
                {'item_id': item['item_id'], 'prompt': item['prompt'], 'rubric': item['rubric']}]))
            if not result.accepted:
                raise _UnitRejected(result.to_dict(), 'rubric')
            rubric = result.to_dict()
        unit['work']['approval'] = {'candidate_hash': fingerprint(unit['candidate']),
            'evidence_fingerprint': self.evidence.fingerprint,
            'review_identity': self.g.review_identity, 'fact': fact,
            'teacher': teacher.model_dump(), 'rubric': rubric}

    def _assemble(self, rows: list[dict[str, Any]], plans: Any) -> Any:
        from .teaching_content import _Point, _Lesson, TeachingContentError
        from .teaching_scope import TEACHING_SCOPE_VERSION
        by_unit = {row['unit_id']: row for row in rows}
        points = []
        for item in self.inputs:
            owner = item['point_id']
            points.append(_Point.model_validate({'point_id': owner,
                **by_unit[owner + '.explanation']['candidate'],
                'flashcard': by_unit[owner + '.flashcard']['candidate'],
                'practice': by_unit[owner + '.practice']['candidate']}))
        opening, summary = by_unit['lesson.opening']['candidate'], by_unit['lesson.summary']['candidate']
        lesson = _Lesson.model_validate({'opening': opening['content'], 'summary': summary['content'],
            'reading_source_ids': list(dict.fromkeys([*opening['source_ids'], *summary['source_ids'],
                *(ids[0] for ids in self.evidence.point_sources.values() if ids)])),
            'worked_examples': [by_unit['lesson.example_' + str(i)]['candidate'] for i in range(1, 4)],
            'checkpoints': [by_unit['lesson.checkpoint_' + str(i)]['candidate'] for i in range(1, 4)]})
        token = current_unit.set('__assembly__')
        try:
            report_course_stage(CourseStage.REVIEWING)
            try:
                self.g._validate_distinct_points(points)
                self.g._validate_lesson(lesson, points, self.evidence)
                fact, teacher = self.g._review_candidate(points, lesson, self.inputs, self.chapter,
                    self.evidence, [], [], attempt=int(self.state.get('passes', 1)))
                feedback = [*self.g._fact_feedback(fact, set(self.by_id)),
                            *self.g._review_feedback(teacher, set(self.by_id))]
                if feedback:
                    plan = self.g._build_repair_plan(fact, teacher, points, lesson)
                    targets = {owner + '.' + kind for owner, kinds in plan.allowed_points.items() for kind in kinds}
                    lesson_targets = set(plan.allowed_lesson)
                    for group, prefix in (('worked_examples', 'example_'), ('checkpoints', 'checkpoint_')):
                        if group in lesson_targets:
                            lesson_targets.update(prefix + str(i) for i in range(1, 4))
                    if 'reading_source_ids' in lesson_targets and not lesson_targets & {'opening', 'summary'}:
                        lesson_targets.update({'opening', 'summary'})
                    targets.update('lesson.' + kind for kind in lesson_targets)
                    for key in sorted(targets & by_unit.keys()):
                        unit = by_unit[key]
                        from .teaching_feedback_wire import compact_writer_feedback
                        scoped = self.g._scoped_feedback(feedback, {unit['work']['owner_id']} if unit['work']['owner_id'] else set(),
                                                         unit['work']['owner_id'] is None)
                        scoped = compact_writer_feedback(scoped, boundaries=[boundary for item in self.inputs
                            for boundary in self.g._scope_boundaries(item, self.evidence)],
                            sources={key: value.model_dump() for key, value in self.evidence.sources.items()})
                        if unit['work']['owner_id'] is None:
                            kind = unit['work']['kind']
                            for entry in scoped:
                                if isinstance(entry, dict) and isinstance(entry.get('factual_lesson'), dict):
                                    parent = entry['factual_lesson']
                                    parent['components'] = {name: proof for name, proof in parent.get('components', {}).items() if name == kind}
                                    parent['issues'] = [issue for issue in parent.get('issues', []) if 'lesson.' + kind in issue]
                        self._failed(unit, _UnitRejected(scoped, 'whole_course'), unit['work']['mode'])
                    raise TeachingRepairPending(5)
            except TeachingRepairPending:
                raise
            except TeachingContentError as error:
                details = error.review_feedback if isinstance(error.review_feedback, dict) else {}
                lesson_parts = details.get('lesson', {}).get('structural_validation', {}).get('lesson_components', [])
                targets = {'lesson.' + kind for kind in lesson_parts}
                for owner in details.get('point_ids', []):
                    targets.update(owner + '.' + kind for kind in ('explanation', 'flashcard', 'practice'))
                if not targets:
                    self.state['assembly_failures'] = int(self.state.get('assembly_failures', 0)) + 1
                    if self.state['assembly_failures'] >= 2:
                        targets = {key for key, unit in by_unit.items() if _question(unit['candidate'])}
                        for key in targets:
                            by_unit[key]['work']['repair_failures'] = max(1, by_unit[key]['work'].get('repair_failures', 0))
                for key in targets & by_unit.keys():
                    self._failed(by_unit[key], _UnitRejected({'issue': str(error), 'detail': details}, 'assembly'), 'repair')
                self.run.save_state(self.state)
                raise TeachingRepairPending(10) from error
            result = self.g._apply(self.bundle, points, lesson, self.evidence)
            result.teaching_review = {**teacher.model_dump(), 'review_identity': self.g.review_identity,
                                      'rubric_alignment': teacher._rubric_alignment}
            result.teaching_fact_review = {**fact.model_dump(), 'review_identity': self.g.review_identity}
            result.teaching_scope_version = TEACHING_SCOPE_VERSION
            result.teaching_scope_plan = plans
            self.state['published_candidate_hash'] = fingerprint({'points': [p.model_dump() for p in points],
                                                                 'lesson': lesson.model_dump()})
            self.run.save_state(self.state)
            return result
        finally:
            current_unit.reset(token)
