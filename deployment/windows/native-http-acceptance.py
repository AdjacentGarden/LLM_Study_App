"""No-provider loopback acceptance for a fresh isolated native API deployment.

Run --phase before, restart only its owned candidate, then --phase after.
Cookies stay in a private session file; reports contain hashes/counts only.
No model, SMTP, OCR, QA, teaching generation, image or video endpoint is called.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def store(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.chmod(0o600)
    os.replace(temp, path)


def request(client: httpx.Client, method: str, path: str, expected=200, **kwargs) -> httpx.Response:
    response = client.request(method, path, **kwargs)
    allowed = (expected,) if isinstance(expected, int) else expected
    if response.status_code not in allowed:
        # Never dump bodies, headers, cookies, email addresses or supplier errors.
        raise AssertionError(f'{method} {path}: HTTP {response.status_code}; expected {allowed}')
    return response


def small_pdf() -> bytes:
    # A real single-page PDF built from standard objects and a correct xref table.
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    text = b'BT /F1 16 Tf 30 220 Td (CloudPath isolated upload acceptance) Tj ET\n'
    objects.append(b'<< /Length ' + str(len(text)).encode() + b' >>\nstream\n' + text + b'endstream')
    output = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(output)
    output.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(output)


def save_session(client: httpx.Client, path: Path) -> None:
    jar = http.cookiejar.LWPCookieJar(str(path))
    for cookie in client.cookies.jar:
        jar.set_cookie(cookie)
    jar.save(ignore_discard=True, ignore_expires=True)
    path.chmod(0o600)


def resource_snapshot(client, receipt):
    row = request(client, 'GET', receipt['resource_path']).json()
    records = request(client, 'GET', receipt['resource_path'] + '/catalog-records').json()['evidence']
    bootstrap = request(client, 'GET', '/api/native/bootstrap').json()
    source = request(client, 'GET', f"/api/books/{receipt['uploaded_book_id']}/source").content
    return {
        'resource_id': row['id'],
        'preferences_sha256': digest(bootstrap['learner_preferences']),
        'progress_sha256': digest(row['progress']),
        'flashcard_reviews_sha256': digest(row['flashcard_reviews']),
        'practice_records_sha256': digest(records),
        'practice_record_count': len(records),
        'selection_sha256': digest(bootstrap['selection']),
        'uploaded_pdf_sha256': hashlib.sha256(source).hexdigest(),
        'course_content_counts': {key: len(row['source_content'][key]) for key in ('lessons', 'flashcards', 'quizzes')},
    }


def check_anonymous(base, receipt):
    with httpx.Client(base_url=base, timeout=20, trust_env=False) as anonymous:
        request(anonymous, 'GET', '/api/native/bootstrap', 401)
        request(anonymous, 'GET', receipt['resource_path'], (401, 403, 404))
        request(anonymous, 'GET', f"/api/books/{receipt['uploaded_book_id']}/source", (401, 403, 404))


def before(client, args, receipt):
    health = request(client, 'GET', '/api/health').json()
    assert health.get('ok') is True, 'Health must verify a real Studio worker'
    rag = request(client, 'GET', '/api/rag/health').json()
    assert not rag['configured'] and not rag['ready'], 'Smoke must have no RAG/provider configuration'
    root = request(client, 'GET', '/')
    assert 'text/html' not in root.headers.get('content-type', ''), 'Native API root must not serve old React HTML'
    assert root.json(), 'API metadata missing'
    request(client, 'GET', '/docs')
    schema = request(client, 'GET', '/openapi.json').json()
    required = ['/api/native/bootstrap', '/api/native/learner-preferences', '/api/native/uploads']
    assert all(path in schema['paths'] for path in required)
    request(client, 'GET', '/api/native/bootstrap', 401)
    request(client, 'POST', '/api/native/local-login', 403, headers={'Origin': 'https://foreign.invalid'})
    request(client, 'POST', '/api/native/local-login', 403, headers={'Host': 'foreign.invalid'})
    request(client, 'POST', '/api/native/local-login')
    save_session(client, args.session)
    receipt['checks'].append('loopback_auth_and_foreign_origin_host_rejected')
    me = request(client, 'GET', '/api/auth/me').json()
    receipt['account_identity_sha256'] = digest(me)
    preferences = {'displayName': 'Deployment acceptance', 'primaryGoal': 'exam', 'dailyTime': 'under30',
                   'learningBarriers': ['understanding', 'memory'], 'guidanceStyles': ['examples', 'stepByStep'],
                   'planningSupport': 'collaborative', 'onboardingVersion': 2}
    assert request(client, 'PUT', '/api/native/learner-preferences', json=preferences).json() == preferences
    assert request(client, 'GET', '/api/native/bootstrap').json()['learner_preferences'] == preferences
    receipt['checks'].append('six_step_preferences_persisted')
    entries = request(client, 'GET', '/api/native/catalog').json()
    assert any(entry['id'] == 'community_genetics' for entry in entries)
    workspace_id = 'workspace_acceptance_' + uuid.uuid4().hex
    body = {'id': workspace_id, 'name': 'Deployment acceptance', 'preferences': {'goal': ['exam']}}
    imported = request(client, 'POST', '/api/native/catalog/community_genetics/import', json=body).json()
    row = imported['resources'][0]
    again = request(client, 'POST', '/api/native/catalog/community_genetics/import', json=body).json()
    assert again['resources'][0]['id'] == row['id']
    receipt.update(workspace_id=workspace_id, resource_id=row['id'],
                   resource_path=f"/api/workspaces/{workspace_id}/resources/{row['id']}")
    content = row['source_content']
    assert {key: len(content[key]) for key in ('lessons', 'flashcards', 'quizzes')} == {'lessons': 15, 'flashcards': 90, 'quizzes': 45}
    assert all(not q['answer'] and not q['explanation'] for q in content['quizzes']), 'Private answer leaked'
    assert content['lessons'][0]['status'] == 'supplemental_draft', 'AI supplemental status lost'
    receipt['checks'].append('curated_import_idempotent_and_unverified_markers_retained')
    question = next(q for q in content['quizzes'] if q.get('choices'))
    practice_path = receipt['resource_path'] + '/catalog-practice/' + question['question_id']
    practice = {'event_id': 'acceptance_practice_' + uuid.uuid4().hex, 'answer': 'A', 'response_seconds': 10, 'confidence': .6}
    initial = request(client, 'POST', practice_path, json=practice).json()
    duplicate = request(client, 'POST', practice_path, json=practice).json()
    assert initial['evidence']['score'] in (0, 1) and not initial['duplicate']
    assert duplicate['duplicate'] and duplicate['evidence'] == initial['evidence']
    request(client, 'POST', practice_path, 409, json={**practice, 'answer': 'different'})
    assert len(request(client, 'GET', receipt['resource_path'] + '/catalog-records').json()['evidence']) == 1
    card = content['flashcards'][0]
    card_path = receipt['resource_path'] + '/catalog-flashcards/' + card['card_id']
    review = {'event_id': 'acceptance_review_' + uuid.uuid4().hex, 'rating': 'again', 'response_seconds': 12}
    initial_review = request(client, 'POST', card_path, json=review).json()
    duplicated_review = request(client, 'POST', card_path, json=review).json()
    assert initial_review['review_state']['algorithm'] == 'fsrs-6'
    assert initial_review['review_state']['repetitions'] == 1
    assert duplicated_review['duplicate'] and duplicated_review['review_state'] == initial_review['review_state']
    request(client, 'POST', card_path, 409, json={**review, 'rating': 'good'})
    receipt['checks'].append('choice_and_fsrs_review_idempotent')
    # Curated supplemental lessons may have unresolved page_start=0. Pick a
    # genuinely mapped chapter instead of claiming that placeholder is a PDF page.
    mapped = [(lesson, chapter) for lesson in content['lessons']
              for chapter in row['structure']['chapters']
              if chapter['chapter_id'] == lesson['chapter_id']
              and 1 <= chapter['start_page'] <= chapter['end_page'] <= row['structure']['source_page_count']]
    assert mapped, 'Curated course has no legal mapped lesson for progress acceptance'
    lesson, chapter = mapped[0]
    progress = {'revision': row['progress']['revision'], 'mutation_id': 'acceptance_progress_' + uuid.uuid4().hex,
                'page': chapter['start_page'], 'chapter_id': chapter['chapter_id'], 'lesson_id': lesson['lesson_id']}
    progress_path = receipt['resource_path'] + '/progress'
    saved = request(client, 'PUT', progress_path, json=progress).json()
    assert request(client, 'PUT', progress_path, json=progress).json() == saved
    request(client, 'PUT', progress_path, 409, json={**progress, 'mutation_id': 'acceptance_conflict_' + uuid.uuid4().hex})
    request(client, 'PUT', '/api/native/selection', json={'workspace_id': workspace_id, 'resource_id': row['id']})
    receipt['checks'].append('progress_mutation_idempotent_stale_revision_rejected')
    pdf = small_pdf()
    uploaded = request(client, 'POST', '/api/native/uploads', files={'file': ('acceptance.pdf', pdf, 'application/pdf')}).json()
    receipt['uploaded_book_id'] = uploaded['book_id']
    assert hashlib.sha256(request(client, 'GET', f"/api/books/{uploaded['book_id']}/source").content).hexdigest() == hashlib.sha256(pdf).hexdigest()
    readiness = request(client, 'GET', f"/api/native/books/{uploaded['book_id']}/readiness").json()
    assert not readiness['rag_ready'] and not readiness['structure_ready'], 'Disabled OCR smoke must not claim book ready'
    request(client, 'GET', f"/api/books/{uploaded['book_id']}/status")
    receipt['checks'].append('real_pdf_upload_and_download_sha256_unready_truth')
    check_anonymous(args.base_url, receipt)
    receipt['checks'].append('anonymous_resource_and_pdf_denied')
    receipt['before_snapshot'] = resource_snapshot(client, receipt)
    save_session(client, args.session)
    receipt['before_passed'] = True


def after(client, args, receipt):
    assert receipt.get('before_passed') is True, 'Before-restart acceptance is incomplete'
    assert request(client, 'GET', '/api/health').json().get('ok') is True
    assert digest(request(client, 'GET', '/api/auth/me').json()) == receipt['account_identity_sha256'], 'Existing session identity changed'
    assert resource_snapshot(client, receipt) == receipt['before_snapshot'], 'Restart changed persistent native state'
    check_anonymous(args.base_url, receipt)
    receipt['checks'].append('restart_preserved_session_preferences_events_progress_selection_pdf')
    receipt['after_passed'] = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['before', 'after'], required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8101')
    parser.add_argument('--commit', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--session', type=Path)
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.port is None or not 1024 <= parsed.port <= 65535 or parsed.port == 8100 or parsed.path not in ('', '/') or parsed.username or parsed.password):
        parser.error('Acceptance only runs against an isolated loopback candidate outside 8100')
    if len(args.commit) != 40 or any(c not in '0123456789abcdef' for c in args.commit):
        parser.error('Expected exact 40-character commit')
    args.session = args.session or args.receipt.with_suffix('.private-session')
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    if args.phase == 'before':
        # Never silently replay a partly executed mutation sequence.
        descriptor = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        receipt = {'kind': 'isolated-native-http-acceptance', 'commit': args.commit, 'checks': [],
                   'base_url': args.base_url, 'before_passed': False, 'after_passed': False,
                   'created_utc': datetime.now(UTC).isoformat(), 'paid_model_calls': 0,
                   'smtp_messages_sent': 0, 'new_pdf_ocr_validated': False, 'iphone_device_validated': False}
    else:
        receipt = json.loads(args.receipt.read_text(encoding='utf-8-sig'))
        if receipt['commit'] != args.commit or receipt['base_url'] != args.base_url:
            raise AssertionError('Receipt identity mismatch')
    jar = http.cookiejar.LWPCookieJar(str(args.session))
    if args.phase == 'after':
        jar.load(ignore_discard=True, ignore_expires=True)
    try:
        with httpx.Client(base_url=args.base_url, timeout=30, cookies=jar, trust_env=False) as client:
            (before if args.phase == 'before' else after)(client, args, receipt)
    except Exception as error:  # noqa: BLE001 - redact the top-level acceptance failure receipt
        receipt['failure'] = {'phase': args.phase, 'type': type(error).__name__, 'detail': str(error)[:300] if isinstance(error, AssertionError) else 'acceptance_contract_or_transport_failed'}
        store(args.receipt, receipt)
        print(json.dumps({'passed': False, 'phase': args.phase, 'error_type': type(error).__name__, 'receipt': str(args.receipt)}))
        return 1
    receipt.pop('failure', None)
    receipt['updated_utc'] = datetime.now(UTC).isoformat()
    store(args.receipt, receipt)
    print(json.dumps({'passed': True, 'phase': args.phase, 'commit': args.commit,
                      'checks': len(receipt['checks']), 'receipt': str(args.receipt),
                      'paid_model_calls': 0, 'smtp_messages_sent': 0}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
