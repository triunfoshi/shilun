from hashlib import sha256
import json

from fastapi import FastAPI
import pytest

from shilun.api.theme_acceptance import router
from shilun.review import theme_acceptance
from shilun.review.theme_selection_data import write_gzip
from shilun.review.event_theme_taxonomy import VERSION as EVENT_SCHEMA
from test_review_remote import LocalASGIClient


@pytest.fixture
def archive(tmp_path,monkeypatch):
    monkeypatch.setattr('shilun.review.theme_distribution.enabled', lambda: False)
    revision='a'*16;day='2026-09-03'
    payload={'revision':revision,'date':day,'events':[{'ticker':'601086.SH'}]}
    path=tmp_path/revision/f'{day}.json.gz'
    write_gzip(path,payload)
    manifest={'revision':revision,'topic_schema':EVENT_SCHEMA,'dates':[day],'payload_hashes':{day:sha256(path.read_bytes()).hexdigest()}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(theme_acceptance,'ROOT',tmp_path)
    theme_acceptance._read_bundle.cache_clear()
    app=FastAPI();app.include_router(router,prefix='/api/v1/review')
    return LocalASGIClient(app),tmp_path


def test_acceptance_reads_exact_immutable_date_without_database(archive):
    client,_=archive
    meta=client.get('/api/v1/review/theme-selection')
    assert meta.status_code==200
    response=client.get('/api/v1/review/theme-selection/day?date=2026-09-03&revision='+meta.json()['revision'])
    assert response.status_code==200
    assert response.json()['date']=='2026-09-03'
    assert client.get('/api/v1/review/theme-selection/day?date=2026-09-23').status_code==404
    assert client.get('/api/v1/review/theme-selection/day?date=2026-09-03&revision='+'b'*16).status_code==409
    assert client.get('/api/v1/review/theme-selection/day?date=../secret').status_code==422
    assert client.post('/api/v1/review/theme-selection').status_code==405


def test_corrupt_bundle_fails_with_sanitized_message(archive):
    client,root=archive
    (root/('a'*16)/'2026-09-03.json.gz').write_bytes(b'corrupted')
    response=client.get('/api/v1/review/theme-selection/day?date=2026-09-03')
    assert response.status_code==409
    assert str(root) not in response.text


def test_missing_manifest_is_explicit(archive):
    client,root=archive
    (root/'manifest.json').unlink()
    response=client.get('/api/v1/review/theme-selection')
    assert response.status_code==404
    assert '尚未生成' in response.text
