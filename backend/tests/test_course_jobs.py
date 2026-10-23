import json
import threading

import pytest

from adaptive_learning.api.course_jobs import (
    CourseJobFailure, CourseJobQueue, CourseRetryLimit,
)


def test_network_retries_and_restart_reuse_the_same_task(tmp_path):
    calls = []
    path = tmp_path / 'jobs.sqlite3'
    q = CourseJobQueue(path, lambda sid, cid: calls.append((sid, cid)) or {'course_id': 'approved'})
    first = q.enqueue('session', 'chapter', 'source-profile-version')
    repeated = q.enqueue('session', 'chapter', 'source-profile-version')
    assert first['job_id'] == repeated['job_id'] and not calls
    assert q.process_one()
    assert not q.process_one()
    restarted = CourseJobQueue(path, lambda *_: pytest.fail('cached task must not regenerate'))
    saved = restarted.enqueue('session', 'chapter', 'source-profile-version')
    assert saved['status'] == 'succeeded' and saved['attempts'] == 1
    assert saved['course'] == {'course_id': 'approved'}
    assert calls == [('session', 'chapter')]
    assert restarted.get('someone-else', first['job_id']) is None
    changed = restarted.enqueue('session', 'chapter', 'changed-source-profile-version')
    assert changed['job_id'] != first['job_id']


def test_parallel_submissions_share_one_identity(tmp_path):
    q = CourseJobQueue(tmp_path / 'jobs.sqlite3', lambda *_: {})
    tasks = []
    threads = [threading.Thread(target=lambda: tasks.append(q.enqueue('s', 'c', 'v'))) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(tasks) == 8
    assert len({r['job_id'] for r in tasks}) == 1


def test_failure_requires_explicit_bounded_retry_and_hides_private_errors(tmp_path):
    def fail(*_): raise RuntimeError('PRIVATE_PROVIDER_BODY_AND_PROMPT')
    q = CourseJobQueue(tmp_path / 'jobs.sqlite3', fail, max_attempts=2)
    first = q.enqueue('s', 'c', 'v')
    q.process_one()
    failed = q.get('s', first['job_id'])
    assert failed['status'] == 'failed' and failed['retryable']
    assert 'PRIVATE' not in json.dumps(failed)
    assert q.enqueue('s', 'c', 'v')['status'] == 'failed'
    assert not q.process_one()
    assert q.enqueue('s', 'c', 'v', retry=True)['job_id'] == first['job_id']
    q.process_one()
    assert not q.get('s', first['job_id'])['retryable']
    with pytest.raises(CourseRetryLimit): q.enqueue('s', 'c', 'v', retry=True)


def test_crashed_running_task_recovers_without_losing_identity(tmp_path):
    q = CourseJobQueue(tmp_path / 'jobs.sqlite3', lambda *_: {'course_id': 'recovered'})
    first = q.enqueue('s', 'c', 'v')
    with q.connect() as db:
        db.execute("UPDATE course_jobs SET status='running',stage='generating',attempts=1")
    q.recover()
    assert q.get('s', first['job_id'])['status'] == 'queued'
    q.process_one()
    assert q.get('s', first['job_id'])['course'] == {'course_id': 'recovered'}


def test_user_safe_rejection_does_not_publish_a_result(tmp_path):
    def reject(*_): raise CourseJobFailure('本章未通过审核')
    q = CourseJobQueue(tmp_path / 'jobs.sqlite3', reject)
    task = q.enqueue('s', 'c', 'v')
    q.process_one()
    row = q.get('s', task['job_id'])
    assert row['status'] == 'failed' and row['course'] is None
    assert row['error'] == '本章未通过审核'


def test_lost_submission_reuses_request_after_profile_change_and_restart(tmp_path):
    path = tmp_path / 'jobs.sqlite3'
    q = CourseJobQueue(path, lambda *_: {'course_id': 'original'})
    first = q.enqueue('s', 'c', 'profile-1', request_id='client-operation')
    q.process_one()
    restarted = CourseJobQueue(path, lambda *_: pytest.fail('must not generate twice'))
    recovered = restarted.enqueue('s', 'c', 'profile-2', request_id='client-operation')
    assert recovered['job_id'] == first['job_id']
    assert recovered['status'] == 'succeeded'
    with pytest.raises(CourseJobFailure):
        restarted.enqueue('s', 'different', 'profile-2', request_id='client-operation')
    # Session-scoped aliases do not reveal another user's operation.
    assert restarted.enqueue('other', 'c', 'profile-2', request_id='client-operation')['job_id'] != first['job_id']
    # An explicit new action can request a new profile; retries of identical
    # current inputs still coalesce even when two clients minted different IDs.
    fresh = restarted.enqueue('s', 'c', 'profile-2', request_id='explicit-new-action')
    assert fresh['job_id'] != first['job_id']
    assert restarted.enqueue('s', 'c', 'profile-2', request_id='second-tab')['job_id'] == fresh['job_id']
