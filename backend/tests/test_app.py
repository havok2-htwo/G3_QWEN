from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from qwen_tts_server.app import create_app
from qwen_tts_server.config import Settings


TEST_MODELS_DIR = Path(__file__).resolve().parents[2] / 'test-models'
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_client(**overrides) -> TestClient:
    app = create_app(
        Settings(
            admin_api_key='test-admin-key',
            runtime_backend='mock',
            models_root_dir=TEST_MODELS_DIR,
            **overrides,
        )
    )
    client = TestClient(app)
    client.__enter__()
    return client


def auth_headers() -> dict[str, str]:
    return {'X-Admin-Key': 'test-admin-key'}


def wait_for_job_status(client: TestClient, job_id: str, allowed: set[str], timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    last_payload: dict | None = None
    while time.time() < deadline:
        response = client.get(f'/v1/jobs/{job_id}', headers=auth_headers())
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload['status'] in allowed:
            return last_payload
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {job_id} in {allowed}; last payload: {last_payload}")


def test_health_is_open() -> None:
    client = make_client()
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'ok': True}
    api_health = client.get('/api/health')
    assert api_health.status_code == 200
    assert api_health.json() == {'ok': True}


def test_frontend_dist_is_served() -> None:
    frontend_dist = PROJECT_ROOT / 'frontend' / 'dist'
    asset_dir = frontend_dist / 'assets'
    asset_file = next(asset_dir.glob('*.*'))

    assert (frontend_dist / 'index.html').exists()

    client = make_client(frontend_dist_dir=frontend_dist)

    root = client.get('/')
    assert root.status_code == 200
    assert '<!doctype html>' in root.text.lower()

    admin_route = client.get('/admin')
    assert admin_route.status_code == 200
    assert '<!doctype html>' in admin_route.text.lower()

    asset = client.get(f'/assets/{asset_file.name}')
    assert asset.status_code == 200
    assert len(asset.text) > 0

    missing_api = client.get('/v1/unknown', headers=auth_headers())
    assert missing_api.status_code == 404
    missing_admin_api = client.get('/api/admin/unknown', headers=auth_headers())
    assert missing_admin_api.status_code == 404


def test_settings_roundtrip() -> None:
    client = make_client()
    response = client.get('/api/admin/settings', headers=auth_headers())
    assert response.status_code == 200
    assert response.json()['model_directory'].endswith('test-models')

    updated = client.put(
        '/api/admin/settings',
        headers=auth_headers(),
        json={
            'model_directory': str(TEST_MODELS_DIR / 'raid-cache'),
            'default_model': 'Qwen3-TTS-12Hz-0.6B-Base',
            'default_voice': 'Ryan',
            'queue_limit': 12,
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload['default_model'] == 'Qwen3-TTS-12Hz-0.6B-Base'
    assert payload['queue_limit'] == 12
    assert payload['model_directory'].endswith('raid-cache')


def test_admin_key_metadata_and_rotation() -> None:
    client = make_client()

    metadata = client.get('/api/admin/keys', headers=auth_headers())
    assert metadata.status_code == 200
    assert metadata.json()['admin_key']['label'] == 'Master Admin Key'

    rotated = client.post('/api/admin/keys', headers=auth_headers())
    assert rotated.status_code == 200
    payload = rotated.json()
    assert payload['admin_key']['label'] == 'Master Admin Key'
    assert payload['token'].startswith('qwen_tts_')

    old_key = client.get('/api/admin/keys', headers={'X-Admin-Key': 'test-admin-key'})
    assert old_key.status_code == 401

    new_key = client.get('/api/admin/keys', headers={'X-Admin-Key': payload['token']})
    assert new_key.status_code == 200


def test_speech_returns_audio() -> None:
    client = make_client()
    response = client.post('/v1/audio/speech', headers=auth_headers(), json={'input': 'Hallo Welt', 'response_format': 'wav'})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('audio/wav')
    assert len(response.content) > 44


def test_jobs_queue_and_lookup() -> None:
    client = make_client()
    create = client.post('/v1/jobs', headers=auth_headers(), json={'input': 'Test job'})
    assert create.status_code == 200
    job_id = create.json()['job_id']
    lookup = client.get(f'/v1/jobs/{job_id}', headers=auth_headers())
    assert lookup.status_code == 200
    assert lookup.json()['job_id'] == job_id


def test_job_list_includes_metrics_after_completion() -> None:
    client = make_client()
    create = client.post('/v1/jobs', headers=auth_headers(), json={'input': 'Metrics please'})
    assert create.status_code == 200
    job_id = create.json()['job_id']

    finished = wait_for_job_status(client, job_id, {'completed'})
    assert finished['metrics']['job_wall_ms'] > 0

    listed = client.get('/v1/jobs', headers=auth_headers())
    assert listed.status_code == 200
    item = next(entry for entry in listed.json() if entry['job_id'] == job_id)
    assert item['metrics']['job_wall_ms'] > 0
    assert item['metrics']['ttfa_ms'] >= 0


def test_delete_job_cancels_active_job() -> None:
    client = make_client()
    original_render_wav = client.app.state.synthesizer.render_wav

    async def slow_render_wav(request):
        await asyncio.sleep(0.2)
        return await original_render_wav(request)

    client.app.state.synthesizer.render_wav = slow_render_wav

    create = client.post('/v1/jobs', headers=auth_headers(), json={'input': 'Cancel me while running'})
    assert create.status_code == 200
    job_id = create.json()['job_id']

    time.sleep(0.05)
    deleted = client.delete(f'/v1/jobs/{job_id}', headers=auth_headers())
    assert deleted.status_code == 200

    interim = wait_for_job_status(client, job_id, {'cancelling', 'cancelled'})
    assert interim['status'] in {'cancelling', 'cancelled'}

    finished = wait_for_job_status(client, job_id, {'cancelled'})
    assert finished['error_message']
    assert 'Cancelled' in finished['error_message']


def test_api_key_protection() -> None:
    client = make_client()
    response = client.get('/v1/stats')
    assert response.status_code == 401
    admin_response = client.get('/api/admin/keys')
    assert admin_response.status_code == 401


def test_benchmark_creation() -> None:
    client = make_client()
    response = client.post(
        '/v1/benchmarks/runs',
        headers=auth_headers(),
        json={
            'name': 'smoke',
            'dataset': 'de_standard_v1',
            'iterations': 1,
            'warmup_iterations': 0,
            'exclusive': True,
            'cases': [{'label': 'default', 'request': {'response_format': 'wav'}}],
        },
    )
    assert response.status_code == 200
    assert response.json()['name'] == 'smoke'
