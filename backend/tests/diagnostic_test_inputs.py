"""Explicit offline adapters; never imported by the product.

ArchivedFreeformDiagnosticGenerator keeps v6-v9 *archived* writer/reviewer-wire
regressions executable. It deliberately does not exercise the v10 application
boundary. Production-generator tests separately prove those free-form responses
are rejected, including the two published F defects. No archive output is
converted into a v10 approval or stored in a production repository.
"""

from adaptive_learning.assessment.item_generation import DiagnosticItemGenerator, _ApplicationTask


class ArchivedFreeformDiagnosticGenerator(DiagnosticItemGenerator):
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


class ArchivedV10StructuredGenerator(DiagnosticItemGenerator):
    """Historical v10 task boundary, not the current forward-plan production boundary."""
    @staticmethod
    def _render_application(entry, source):
        task = _ApplicationTask.model_validate(entry.get('application_task'))
        directory = source['application_source_refs'][entry['knowledge_point_id']]
        if not set(task.source_refs) <= {row['ref'] for row in directory}:
            from adaptive_learning.assessment.item_generation import DiagnosticGenerationError
            raise DiagnosticGenerationError('application source_refs must identify exact evidence for this point')
        return DiagnosticItemGenerator._render_application_task(entry, task), task.model_dump(mode='json')


def controlled_application(request, index=2):
    """Explicit forward-plan test input; a scripted verdict never certifies semantics."""
    point = request['knowledge_points'][index]
    source = request['application_source_refs'][point['knowledge_point_id']][0]
    return {'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
        'difficulty': 0, 'estimated_seconds': 45, 'application_task': {
            'mode': 'forward_instances', 'basis': {'source_ref': source['ref'],
                'quote': '条件甲成立时过程乙适用。', 'input_role': '受控给定条件', 'output_role': '受控过程的适用性'},
            'input_condition': '条件甲成立', 'positive_result': '过程乙适用',
            'alternative_result': '过程乙不适用', 'instances': ['受控实例甲', '受控实例乙']}}
