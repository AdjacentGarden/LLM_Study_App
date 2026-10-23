from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient
from native_harness import api, configure, textbook_pdf

from adaptive_learning import native_catalog


@pytest.fixture
def catalog_client(tmp_path, monkeypatch):
    application = configure(tmp_path, monkeypatch)
    monkeypatch.setenv('NATIVE_LOCAL_LOGIN_ENABLED', '1')
    with TestClient(application, base_url='http://127.0.0.1', client=('127.0.0.1', 5100)) as client:
        assert client.post('/api/native/local-login').status_code == 200
        yield client, api.account_repository.repo


def test_server_curated_catalog_keeps_provenance_not_synthetic_statistics():
    original = copy.deepcopy(native_catalog.snapshot())
    entry = native_catalog.catalog()[0]
    assert entry['id'] == native_catalog.CATALOG_ID
    assert entry['owner'] == '原项目已有课程'
    assert entry['learners'] is None and entry['progress'] is None
    assert entry['flashcardCount'] == len(original['source']['flashcards']) == 90
    assert entry['catalog_origin']['source_sha256'] == original['provenance']['sources_sha256']
    entry['description'] = 'caller edit'
    assert native_catalog.snapshot() == original


def test_missing_curated_file_and_empty_public_store_return_empty_catalog(catalog_client, monkeypatch):
    client, _ = catalog_client

    def unavailable():
        raise FileNotFoundError('catalog fixture unavailable')

    monkeypatch.setattr(native_catalog, 'snapshot', unavailable)
    assert client.get('/api/native/catalog').json() == []
    assert client.get('/api/native/bootstrap').json()['catalog'] == []
    response = client.post('/api/native/catalog/community_genetics/import',
                           json={'id': 'workspace_no_fallback', 'name': 'No fallback'})
    assert response.status_code == 404
    assert client.get('/api/workspaces').json() == []


def publish_uploaded_book(client, repo):
    uploaded = client.post('/api/native/uploads', files={
        'file': ('shared.pdf', textbook_pdf(), 'application/pdf'),
    })
    assert uploaded.status_code == 200
    book_id = uploaded.json()['book_id']
    actor = api.account_repository.session_owner(client.cookies.get('zhiwo_account'))
    assert actor is not None
    repo.register_asset({'book_id': book_id, 'title': 'Actual uploaded textbook',
                         'status': 'uploaded', 'page_count': 3, 'chapter_count': 0,
                         'summary': '', 'diagnostics_ready': False, 'cover_url': None},
                        api.job_repository.source_fingerprint(book_id), None)
    repo.add_book(actor, book_id)
    published = client.post('/api/community/share', json={
        'kind': 'book', 'book_id': book_id, 'title': '',
        'description': 'Publisher supplied description', 'rights_confirmed': True,
    })
    assert published.status_code == 200, published.json()
    return actor, book_id, published.json()['post_id']


def test_real_active_book_shares_join_same_catalog_and_import_actual_source(catalog_client):
    client, repo = catalog_client
    actor, book_id, post_id = publish_uploaded_book(client, repo)
    catalog_id = 'community_post_' + post_id
    catalog = client.get('/api/native/catalog').json()
    entry = next(item for item in catalog if item['id'] == catalog_id)
    assert entry['title'] == 'Actual uploaded textbook'
    assert entry['description'] == 'Publisher supplied description'
    assert entry['owner'] == '社区公开分享'
    assert entry['learners'] is None and entry['progress'] is None
    assert entry['chapters'] == [] and entry['flashcardCount'] == 0
    assert client.get('/api/native/bootstrap').json()['catalog'] == catalog
    payload = {'id': 'workspace_real_public_book', 'name': 'Real share'}
    first = client.post('/api/native/catalog/' + catalog_id + '/import', json=payload)
    assert first.status_code == 200
    resource = first.json()['resources'][0]
    assert resource['book_id'] == book_id
    assert resource['structure'] is None and resource['courses'] == []
    assert resource['source_content'] is None
    assert resource['status'] != 'ready'
    second = client.post('/api/native/catalog/' + catalog_id + '/import', json=payload)
    assert second.status_code == 200
    assert second.json()['resources'][0]['id'] == resource['id']
    with repo.connect() as db:
        assert db.execute('SELECT count(*) FROM acquisitions WHERE owner=? AND post_id=?', (actor, post_id)).fetchone()[0] == 1
    assert repo.withdraw(actor, post_id)
    assert all(item['id'] != catalog_id for item in client.get('/api/native/catalog').json())
    denied = client.post('/api/native/catalog/' + catalog_id + '/import',
                         json={'id': 'workspace_withdrawn_share', 'name': 'Withdrawn'})
    assert denied.status_code == 404
    # A withdrawal does not erase an existing learner's acquired source/workspace.
    assert client.get('/api/workspaces/' + payload['id']).status_code == 200


def test_private_assets_and_non_book_publications_do_not_become_catalog_courses(catalog_client):
    client, repo = catalog_client
    actor, book_id, _ = publish_uploaded_book(client, repo)
    private_id = 'private_unpublished_book'
    repo.register_asset({'book_id': private_id, 'title': 'Private asset'}, 'private-source', None)
    repo.add_book(actor, private_id)
    note_id, _ = repo.publish(actor, 'note', book_id, 'Public note', '', {'body': 'Explicitly shared note'})
    entries = client.get('/api/native/catalog').json()
    assert not any(item['title'] in {'Private asset', 'Public note'} for item in entries)
    denied = client.post('/api/native/catalog/community_post_' + note_id + '/import',
                         json={'id': 'workspace_not_a_book', 'name': 'Invalid kind'})
    assert denied.status_code == 404


def test_community_catalog_requires_real_authenticated_owner(catalog_client):
    client, _ = catalog_client
    with TestClient(client.app) as anonymous:
        assert anonymous.get('/api/native/catalog').status_code == 401
