from adaptive_learning.ingestion.preparation import PreparationQueue


def test_stale_completed_preparation_can_refresh_but_active_work_cannot_duplicate(tmp_path):
    calls = []
    queue = PreparationQueue(tmp_path / 'preparation.db', [('diagnostics', lambda key: calls.append(key))])
    assert queue.enqueue('book')['status'] == 'queued'
    queue.process_one()
    assert queue.get('book')['status'] == 'succeeded'
    assert queue.enqueue('book')['status'] == 'succeeded'
    assert not queue.process_one()
    assert queue.enqueue('book', refresh=True)['status'] == 'queued'
    queue.update('book', 'running', 'diagnostics')
    assert queue.enqueue('book', refresh=True)['status'] == 'running'
    queue.update('book', 'queued', 'queued')
    queue.process_one()
    assert calls == ['book', 'book']
