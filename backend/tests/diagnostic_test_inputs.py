"""Explicit offline adapters; never imported by the product.

ArchivedFreeformDiagnosticGenerator keeps v6-v9 *archived* writer/reviewer-wire
regressions executable. It deliberately does not exercise the v10 application
boundary. Production-generator tests separately prove those free-form responses
are rejected, including the two published F defects. No archive output is
converted into a v10 approval or stored in a production repository.
"""

from typing import Annotated

from pydantic import Field, TypeAdapter

from adaptive_learning.assessment.item_generation import (
    DiagnosticGenerationError,
    DiagnosticItemGenerator,
    _ApplicationTask,
    _ForwardInstancesPlan,
    _RequiredConditionsPlan,
    _RoleTransferPlan,
)


class ArchivedFreeformDiagnosticGenerator(DiagnosticItemGenerator):
    def _select_points(self, chapter):
        # Historical replays must not silently apply a later role assignment.
        from adaptive_learning.assessment.item_generation import stable_knowledge_point_id
        candidates = [(stable_knowledge_point_id(chapter.chapter_id, label), label,
                       chapter.knowledge_point_evidence[label]) for label in chapter.knowledge_points
                      if chapter.knowledge_point_evidence.get(label)]
        ranked = sorted(enumerate(candidates), key=lambda pair: (
            self._point_value(pair[1][1], pair[1][2]), -pair[0]), reverse=True)[:self.points_per_chapter]
        return [point for _, point in sorted(ranked)]

    @staticmethod
    def _render_application(entry, source):
        return entry, {}


def controlled_application_v10(request, index=2):
    """A test-only candidate, whose semantic approval is always separately scripted."""
    point = request['knowledge_points'][index]
    return {'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
        'difficulty': 0, 'estimated_seconds': 45, 'application_task': {
            'source_refs': [request['application_source_refs'][point['knowledge_point_id']][0]['ref']],
            'records': [{'id': 'r1', 'conditions': '受控输入条件甲成立'},
                        {'id': 'r2', 'conditions': '受控输入条件甲不成立'}],
            'decision': '受控过程的适用性', 'outcomes': ['适用', '不适用'], 'mapping': ['a', 'b']}}


def _archived_echo_checked_render(entry, task):
    """Preserve the historical v10/v11 echo contract only in explicit replay adapters."""
    rendered = DiagnosticItemGenerator._render_application_task(entry, task)
    if any(key in entry and entry[key] != rendered[key] for key in
           ("prompt", "options", "correct_index", "expected_answer", "rubric")):
        raise DiagnosticGenerationError("archived application echo differs from its task renderer")
    return rendered


class ArchivedV10StructuredGenerator(ArchivedFreeformDiagnosticGenerator):
    """Historical v10 task boundary, not the current forward-plan production boundary."""
    @staticmethod
    def _render_application(entry, source):
        task = _ApplicationTask.model_validate(entry.get('application_task'))
        directory = source['application_source_refs'][entry['knowledge_point_id']]
        if not set(task.source_refs) <= {row['ref'] for row in directory}:
            raise DiagnosticGenerationError('application source_refs must identify exact evidence for this point')
        return _archived_echo_checked_render(entry, task), task.model_dump(mode='json')


class ArchivedV11PlanGenerator(ArchivedFreeformDiagnosticGenerator):
    """Explicit v11 wire adapter; its AA mode is forbidden in the current product."""
    @staticmethod
    def _render_application(entry, source):
        raw = entry.get('application_task')
        adapter = TypeAdapter(Annotated[_ForwardInstancesPlan | _RoleTransferPlan | _RequiredConditionsPlan,
                                        Field(discriminator='mode')])
        plan = adapter.validate_python({key: value for key, value in raw.items() if key != 'derivation'})
        directory = source['application_source_refs'][entry['knowledge_point_id']]
        evidence = next((row['evidence'] for row in directory if row['ref'] == plan.basis.source_ref), None)
        if evidence is None or plan.basis.quote not in evidence['quote']:
            raise DiagnosticGenerationError('application basis must quote a continuous literal passage from its exact source_ref')
        task = DiagnosticItemGenerator._derive_application(plan)
        derived = task.model_dump(mode='json')
        if 'derivation' in raw and raw['derivation'] != derived:
            raise DiagnosticGenerationError('application derivation is read-only')
        return _archived_echo_checked_render(entry, task), {
            **plan.model_dump(mode='json'), 'derivation': derived}


def controlled_application(request, index=2):
    """Explicit condition-list test input; a scripted verdict never certifies semantics."""
    point = request['knowledge_points'][index]
    source = request['application_source_refs'][point['knowledge_point_id']][0]
    return {'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
        'difficulty': 0, 'estimated_seconds': 45, 'application_task': {
            'mode': 'required_conditions', 'basis': {'source_ref': source['ref'],
                'input_role': '受控过程乙的必需条件', 'output_role': '受控条件齐备情况'},
            'required': ['条件甲', '条件乙'],
            'records': [{'available': [0, 1]}, {'available': [0]}]}}
