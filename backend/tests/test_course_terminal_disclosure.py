"""Portable production regressions; execute against the draft-only overlay before apply."""
import json

import pytest
from adaptive_learning.api.course_jobs import CourseJobFailure, CourseJobQueue, CourseRetryLimit


def failed_queue(tmp_path, *, attempts=3):
    calls = []

    def fail(*_):
        calls.append(1)
        raise CourseJobFailure('教学服务暂时不可用，课程尚未完成审核，请稍后重试')

    queue = CourseJobQueue(tmp_path / 'jobs.sqlite3', fail, max_attempts=attempts)
    job = queue.enqueue('owner-session', 'chapter', 'version', request_id='fixed-operation')
    for attempt in range(attempts):
        if attempt:
            queue.enqueue('owner-session', 'chapter', 'version', retry=True, request_id='fixed-operation')
        assert queue.process_one()
    return queue, job['job_id'], calls


def full_row(queue, job_id):
    with queue.connect() as db:
        return dict(db.execute('SELECT * FROM course_jobs WHERE job_id=?', (job_id,)).fetchone())


def test_exhausted_get_explains_terminal_without_rewriting_saved_failure(tmp_path):
    queue, job_id, calls = failed_queue(tmp_path)
    original = full_row(queue, job_id)
    assert '请稍后重试' in original['error']
    public = queue.get('owner-session', job_id)
    assert public['status'] == 'failed' and public['stage'] == 'failed'
    assert public['attempts'] == 3 and public['retryable'] is False
    assert '已停止' in public['error'] and '尝试次数已用完' in public['error']
    assert '请稍后重试' not in public['error'] and public['course'] is None
    assert '新版本' in public['error']
    assert full_row(queue, job_id) == original
    assert len(calls) == 3 and not queue.process_one()


def test_retryable_failure_keeps_specific_error_and_explicit_retry(tmp_path):
    queue = CourseJobQueue(tmp_path / 'jobs.sqlite3', lambda *_: (_ for _ in ()).throw(CourseJobFailure('审核暂不可用，请稍后重试')))
    job = queue.enqueue('s', 'c', 'v', request_id='fixed')
    queue.process_one()
    public = queue.get('s', job['job_id'])
    assert public['retryable'] is True
    assert public['error'] == '审核暂不可用，请稍后重试'
    assert queue.enqueue('s', 'c', 'v', request_id='fixed')['status'] == 'failed'
    assert not queue.process_one()
    retried = queue.enqueue('s', 'c', 'v', retry=True, request_id='fixed')
    assert retried['job_id'] == job['job_id'] and retried['status'] == 'queued'


def test_exhausted_retry409_matches_get_and_keeps_all_saved_columns(tmp_path):
    queue, job_id, calls = failed_queue(tmp_path)
    original = full_row(queue, job_id)
    with pytest.raises(CourseRetryLimit) as error:
        queue.enqueue('owner-session', 'chapter', 'version', retry=True, request_id='fixed-operation')
    assert str(error.value) == queue.get('owner-session', job_id)['error']
    assert full_row(queue, job_id) == original and len(calls) == 3


def test_fixed_id_and_same_fingerprint_cannot_bypass_exhaustion(tmp_path):
    queue, job_id, calls = failed_queue(tmp_path)
    same_id = queue.enqueue('owner-session', 'chapter', 'new-version', request_id='fixed-operation')
    new_id = queue.enqueue('owner-session', 'chapter', 'version', request_id='new-id')
    assert same_id['job_id'] == new_id['job_id'] == job_id
    assert not same_id['retryable'] and not new_id['retryable']
    assert not queue.process_one() and len(calls) == 3
    with queue.connect() as db:
        assert db.execute('SELECT count(*) FROM course_jobs').fetchone()[0] == 1


def test_running_and_succeeded_states_do_not_get_terminal_copy(tmp_path):
    queue = CourseJobQueue(tmp_path / 'jobs.sqlite3', lambda *_: {'course_id': 'approved'})
    job = queue.enqueue('s', 'c', 'v')
    assert job['status'] == 'queued' and job['error'] is None
    with queue.connect() as db:
        db.execute("UPDATE course_jobs SET status='running',stage='generating',attempts=1 WHERE job_id=?", (job['job_id'],))
    running = queue.get('s', job['job_id'])
    assert running['error'] is None and running['status'] == 'running'
    queue.recover()
    queue.process_one()
    done = queue.get('s', job['job_id'])
    assert done['status'] == 'succeeded' and done['course'] == {'course_id': 'approved'}
    assert done['error'] is None


def test_other_owner_cannot_read_failed_task_or_cause(tmp_path):
    queue, job_id, _ = failed_queue(tmp_path)
    assert queue.get('foreign-session', job_id) is None
    before = json.dumps(full_row(queue, job_id), sort_keys=True)
    queue.get('owner-session', job_id)
    assert json.dumps(full_row(queue, job_id), sort_keys=True) == before
