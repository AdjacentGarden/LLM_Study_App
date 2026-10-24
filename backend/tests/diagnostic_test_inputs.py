"""Explicit offline adapters; never imported by the product.

ArchivedFreeformDiagnosticGenerator keeps v6-v9 *archived* writer/reviewer-wire
regressions executable. It deliberately does not exercise the v10 application
boundary. Production-generator tests separately prove those free-form responses
are rejected, including the two published F defects. No archive output is
converted into a v10 approval or stored in a production repository.
"""

from adaptive_learning.assessment.item_generation import DiagnosticItemGenerator


class ArchivedFreeformDiagnosticGenerator(DiagnosticItemGenerator):
    @staticmethod
    def _render_application(entry, source):
        return entry, {}


def controlled_application(request, index=2):
    """A test-only candidate, whose semantic approval is always separately scripted."""
    point = request['knowledge_points'][index]
    return {'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
        'difficulty': 0, 'estimated_seconds': 45, 'application_task': {
            'source_refs': [request['application_source_refs'][point['knowledge_point_id']][0]['ref']],
            'records': [{'id': 'r1', 'conditions': '受控输入条件甲成立'},
                        {'id': 'r2', 'conditions': '受控输入条件甲不成立'}],
            'decision': '受控过程的适用性', 'outcomes': ['适用', '不适用'], 'mapping': ['a', 'b']}}
