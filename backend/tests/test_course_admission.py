"""Actual production SQLite queue and route AST; full app bootstrap is outside this scope."""

from __future__ import annotations

import ast
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from adaptive_learning.api import course_jobs as queue_module
from adaptive_learning.api.course_jobs import (
    CourseAdmissionDeferred,
    CourseJobFailure,
    CourseJobInput,
    CourseJobQueue,
    CourseRetryLimit,
)
from adaptive_learning.llm import client as module
from adaptive_learning.llm.client import LLMConfig, LLMCooldownError, OpenAICompatibleClient
from adaptive_learning.llm.model_admission import ModelAdmission, route_key
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def snapshot(queue):
    with queue.connect() as db:
        return {
            'jobs': [dict(row) for row in db.execute('SELECT * FROM course_jobs ORDER BY job_id')],
            'aliases': [dict(row) for row in db.execute(
                'SELECT * FROM course_job_requests ORDER BY session_id,request_id')],
        }


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = Clock()
    gate = ModelAdmission(clock)
    monkeypatch.setattr(module, 'admission', gate)
    sends, processor, admission_calls = [], [], []

    def model_transport(request):
        sends.append(request)
        pytest.fail('queue admission must not issue a model HTTP request')

    clients = []
    for base, model in [('https://api.deepseek.com/v1', 'deepseek-flash'),
                        ('https://pucoding.com/v1', 'gpt-6-sol')]:
        obj = OpenAICompatibleClient(LLMConfig(base, 'fake-test-key', model, max_retries=0))
        obj._http.close()
        obj._http = httpx.Client(transport=httpx.MockTransport(model_transport))
        clients.append(obj)

    # Execute the actual production route/adapter AST, not a copied route implementation.
    path = Path(queue_module.__file__).with_name('app.py')
    tree = ast.parse(path.read_text())
    wanted = {'_check_course_admission', 'start_course_task'}
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in selected} == wanted
    app = FastAPI()
    namespace = {
        'app': app, 'math': math, 'Request': Request, 'JSONResponse': JSONResponse,
        'HTTPException': HTTPException, 'CourseJobInput': CourseJobInput,
        'CourseJobFailure': CourseJobFailure, 'CourseRetryLimit': CourseRetryLimit,
        'CourseAdmissionDeferred': CourseAdmissionDeferred, 'LLMCooldownError': LLMCooldownError,
        'teaching_client': clients[0], 'teaching_review_client': clients[1],
        '_require_course_actor': lambda *args: None,
        '_course_context': lambda *args: (None, None, None, 'profile', 'source'),
        'get_course': lambda *args: SimpleNamespace(model_dump=lambda **kwargs: {'course_id': 'cached'}),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)

    def admission_check():
        admission_calls.append(True)
        namespace['_check_course_admission']()

    def generate(session_id, chapter_id):
        processor.append((session_id, chapter_id))
        return {'course_id': 'fake-approved'}

    queue = CourseJobQueue(tmp_path / 'jobs.sqlite3', generate, admission_check=admission_check)
    namespace['course_job_queue'] = queue
    result = SimpleNamespace(queue=queue, http=TestClient(app), clock=clock, gate=gate,
        clients=clients, sends=sends, processor=processor, admission_calls=admission_calls)

    def block(role='reviewer'):
        obj = clients[1] if role == 'reviewer' else clients[0]
        key = route_key(obj.config.base_url, obj.config.model, obj.config.api_key)
        gate.finish(gate.begin(key), overload=30)

    result.block = block
    yield result
    for obj in clients:
        obj.close()


def post(env, *, retry=False, request_id='durable-operation', chapter_id='chapter'):
    return env.http.post('/api/interviews/session/course-jobs', json={
        'chapter_id': chapter_id, 'retry': retry, 'request_id': request_id})


@pytest.mark.parametrize('blocked_role', ['writer', 'reviewer'])
def test_new_known_cooldown_three_clicks_cost_no_job_alias_attempt_or_processor(env, blocked_role):
    env.block(blocked_role)
    for _ in range(3):
        response = post(env)
        assert response.status_code == 503
        assert response.headers['Retry-After'] == '30'
        assert 'fake-test-key' not in response.text and 'pucoding.com' not in response.text
    assert snapshot(env.queue) == {'jobs': [], 'aliases': []}
    assert env.processor == env.sends == []
    assert not env.queue.wake.is_set()
    assert env.queue.process_one() is False
    # An unbound rejected durable ID remains usable after the same route recovers.
    env.clock.now += 31
    accepted = post(env)
    assert accepted.status_code == 202
    assert accepted.json()['attempts'] == 0
    assert snapshot(env.queue)['aliases'][0]['request_id'] == 'durable-operation'
    assert env.queue.process_one() is True
    assert accepted.json()['job_id'] == snapshot(env.queue)['jobs'][0]['job_id']
    assert snapshot(env.queue)['jobs'][0]['attempts'] == 1
    assert len(env.processor) == 1 and not env.sends


@pytest.mark.parametrize('retry_request_id', ['durable-operation', 'new-explicit-retry'])
def test_failed_retry_rejection_preserves_entire_row_and_existing_alias(env, retry_request_id):
    job = post(env).json()

    def fail(*args):
        raise CourseJobFailure('earlier real attempt failed')

    env.queue.generate = fail
    assert env.queue.process_one()
    before = snapshot(env.queue)
    assert before['jobs'][0]['attempts'] == 1
    env.queue.wake.clear()
    env.block()
    for _ in range(3):
        rejected = post(env, retry=True, request_id=retry_request_id)
        assert rejected.status_code == 503
    assert snapshot(env.queue) == before
    assert not env.queue.wake.is_set()
    assert not env.queue.process_one()
    assert not env.sends
    env.clock.now += 31
    retried = post(env, retry=True, request_id=retry_request_id)
    assert retried.status_code == 202
    assert retried.json()['job_id'] == job['job_id']
    assert retried.json()['attempts'] == 1  # No reset, no increment until worker claim.
    env.queue.generate = lambda *_: {'course_id': 'recovered'}
    assert env.queue.process_one()
    assert snapshot(env.queue)['jobs'][0]['attempts'] == 2


@pytest.mark.parametrize('status,retry', [
    ('queued', False), ('queued', True), ('running', False), ('running', True),
    ('succeeded', False), ('succeeded', True), ('failed', False),
])
def test_existing_pure_return_bypasses_admission_even_while_cooling(env, status, retry):
    job = post(env).json()
    with env.queue.connect() as db:
        db.execute("UPDATE course_jobs SET status=?,stage=?,result=?,attempts=1,error=?", (
            status, status, '{"course_id":"cached"}' if status == 'succeeded' else None,
            'preserved old failure' if status == 'failed' else None))
    before = snapshot(env.queue)
    env.block()
    calls = len(env.admission_calls)
    response = post(env, retry=retry)
    assert response.status_code == (202 if status in {'queued', 'running'} else 200)
    assert response.json()['job_id'] == job['job_id']
    assert snapshot(env.queue) == before
    assert len(env.admission_calls) == calls
    assert not env.processor and not env.sends


def test_same_bound_request_id_explicit_retry_semantics_survive_profile_change(env):
    job = post(env).json()
    with env.queue.connect() as db:
        db.execute("UPDATE course_jobs SET status='failed',stage='failed',attempts=1,error='old'")
    retried = env.queue.enqueue('session', 'chapter', 'changed-fingerprint',
        retry=True, request_id='durable-operation')
    assert retried['job_id'] == job['job_id']
    assert retried['status'] == 'queued' and retried['attempts'] == 1
    assert len(snapshot(env.queue)['jobs']) == 1
    assert len(snapshot(env.queue)['aliases']) == 1


def test_exhausted_retry_keeps_original_limit_and_durable_error_even_during_cooldown(env):
    post(env)
    with env.queue.connect() as db:
        db.execute("UPDATE course_jobs SET status='failed',stage='failed',attempts=3,error='private old error'")
    before = snapshot(env.queue)
    calls = len(env.admission_calls)
    env.block()
    rejected = post(env, retry=True)
    assert rejected.status_code == 409
    assert snapshot(env.queue) == before
    assert len(env.admission_calls) == calls
    assert not env.processor and not env.sends


def test_bound_request_id_other_chapter_conflict_remains_409(env):
    post(env)
    before = snapshot(env.queue)
    env.block()
    response = post(env, retry=True, chapter_id='different')
    assert response.status_code == 409
    assert snapshot(env.queue) == before
    assert not env.processor and not env.sends


def test_worker_rechecks_cooldown_after_enqueue_before_increment_and_processor(env):
    job = post(env).json()
    env.queue.wake.clear()
    before = snapshot(env.queue)
    env.block()
    for _ in range(3):
        assert env.queue.process_one() is False
        assert snapshot(env.queue) == before
    assert not env.processor and not env.sends
    env.clock.now += 31
    assert env.queue.process_one()
    current = env.queue.get('session', job['job_id'])
    assert current['status'] == 'succeeded' and current['attempts'] == 1
    assert len(env.processor) == 1 and not env.sends


def test_worker_recheck_preserves_prior_attempt_after_explicit_retry(env):
    job = post(env).json()
    with env.queue.connect() as db:
        db.execute("UPDATE course_jobs SET status='failed',stage='failed',attempts=1,error='old'")
    assert post(env, retry=True).status_code == 202
    before = snapshot(env.queue)
    env.block()
    assert env.queue.process_one() is False
    assert snapshot(env.queue) == before
    assert before['jobs'][0]['attempts'] == 1
    assert len(before['aliases']) == 1
    assert env.queue.get('session', job['job_id'])['status'] == 'queued'
    assert not env.processor and not env.sends


@pytest.mark.parametrize('existing_failed', [False, True])
def test_eight_concurrent_sqlite_submissions_cannot_write_while_known_cooldown(env, existing_failed):
    if existing_failed:
        post(env)
        with env.queue.connect() as db:
            db.execute("UPDATE course_jobs SET status='failed',stage='failed',attempts=1,error='old'")
    before = snapshot(env.queue)
    env.queue.wake.clear()
    env.block()

    def enqueue(index):
        try:
            env.queue.enqueue('session', 'chapter', 'different-fingerprint', retry=existing_failed,
                request_id='durable-operation' if existing_failed else 'new-' + str(index))
        except CourseAdmissionDeferred:
            return 'deferred'
        pytest.fail('known cooldown must reject new work')

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(enqueue, range(8))) == ['deferred'] * 8
    assert snapshot(env.queue) == before
    assert not env.processor and not env.sends
    assert not env.queue.wake.is_set()
