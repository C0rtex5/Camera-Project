"""Same-origin access, camera persistence and reconnect regressions."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.hub.api import create_app
from src.hub.service import DeviceManager, Hub
from src.hub.store import Store
from tests.hub_support import EmptyDetector, camera


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.delenv('SENTINEL_ORIGIN', raising=False)
    store = Store(tmp_path)
    hub = Hub(store, DeviceManager('cpu', factory=lambda d: EmptyDetector()))
    with TestClient(create_app(store, hub, authentication_required=False)) as client:
        client.headers['X-Sentinel-Request'] = 'hub'
        yield client, store, hub


@pytest.mark.parametrize('configured', [None, '', 'auto', ' AUTO '])
@pytest.mark.parametrize('address', ['http://localhost:8000', 'http://100.80.0.10:1221', 'https://factory.example'])
def test_same_origin_camera_save_check_and_websocket(panel, monkeypatch, configured, address):
    client, store, hub = panel
    if configured is not None:
        monkeypatch.setenv('SENTINEL_ORIGIN', configured)
    client.base_url = address
    client.headers['Origin'] = address
    body = camera(rtsp_username='camera-operator', rtsp_password='private-camera-password')
    assert client.put('/api/v1/cameras/cam1', json=body).status_code == 200
    with patch('src.hub.rtsp_check.check_rtsp', return_value={'ok': True, 'code': 'accepted', 'message': 'RTSP aceito.'}) as probe:
        result = client.post('/api/v1/cameras/check', json=camera())
    assert result.status_code == 200 and result.json()['ok']
    assert probe.call_args.args[1]['password'] == body['rtsp_password']
    public = client.get('/api/v1/cameras')
    assert body['rtsp_password'] not in public.text and body['rtsp_username'] not in public.text
    assert store.camera_credentials('cam1')['password'] == body['rtsp_password']
    hub.workers['cam1'] = SimpleNamespace(stop_event=threading.Event(), status=lambda: {'camera_id': 'cam1'}, stop=lambda: None)
    ws_url = address.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws/cameras/cam1'
    with client.websocket_connect(ws_url) as socket:
        assert socket.receive_json()['camera_id'] == 'cam1'


@pytest.mark.parametrize('origin', ['https://other.invalid', 'http://testserver:1221', 'https://testserver', 'null', ''])
def test_external_origins_rejected_everywhere(panel, origin):
    client, store, _ = panel
    with patch('src.hub.rtsp_check.check_rtsp') as probe:
        result = client.post('/api/v1/cameras/check', json=camera(), headers={'Origin': origin})
    assert result.status_code == 403
    assert 'Acesso ao painel bloqueado' in result.json()['detail']
    probe.assert_not_called()
    assert client.put('/api/v1/cameras/cam1', json=camera(), headers={'Origin': origin}).status_code == 403
    assert store.cameras() == []
    for path in ('/ws/cameras/cam1', '/ws/incidents', '/ws/demo_stream/test'):
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(path, headers={'Origin': origin}):
                pass
        assert error.value.code == 1008


def test_fixed_origin_stays_restrictive_for_http_and_ws(panel, monkeypatch):
    client, _, hub = panel
    monkeypatch.setenv('SENTINEL_ORIGIN', 'http://localhost:1221')
    assert client.put('/api/v1/cameras/cam1', json=camera(), headers={'Origin': 'http://testserver'}).status_code == 403
    assert client.put('/api/v1/cameras/cam1', json=camera(), headers={'Origin': 'http://localhost:1221'}).status_code == 200
    hub.workers['cam1'] = SimpleNamespace(stop_event=threading.Event(), status=lambda: {'camera_id': 'cam1'}, stop=lambda: None)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/cameras/cam1', headers={'Origin': 'http://testserver'}):
            pass
    with client.websocket_connect('/ws/cameras/cam1', headers={'Origin': 'http://localhost:1221'}) as socket:
        assert socket.receive_json()['camera_id'] == 'cam1'


def test_default_port_equivalence_and_write_header_required(panel):
    client, _, _ = panel
    assert client.put('/api/v1/cameras/cam1', json=camera(), headers={'Origin': 'http://TESTSERVER:80'}).status_code == 200
    del client.headers['X-Sentinel-Request']
    assert client.put('/api/v1/cameras/cam1', json=camera(), headers={'Origin': 'http://testserver'}).status_code == 403


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.02)
    return False


def test_save_offline_reconnect_and_restart_with_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('SENTINEL_ORIGIN', 'auto')
    available, attempted = threading.Event(), threading.Event()
    sources = []

    class Capture:
        def __init__(self, source, *args):
            sources.append(source)
            attempted.set()
        def isOpened(self):
            return available.is_set()
        def read(self):
            time.sleep(.02)
            return available.is_set(), np.zeros((64, 64, 3), np.uint8)
        def release(self):
            pass

    body = camera(enabled=True, rtsp_username='camera-user', rtsp_password='private-password', preview_fps=5, inference_fps=2)
    with patch('src.hub.service.cv2.VideoCapture', Capture):
        for restart in (False, True):
            store = Store(tmp_path)
            hub = Hub(store, DeviceManager('cpu', factory=lambda d: EmptyDetector()))
            with TestClient(create_app(store, hub, authentication_required=False)) as client:
                client.headers.update({'X-Sentinel-Request': 'hub', 'Origin': 'http://testserver'})
                if not restart:
                    result = client.put('/api/v1/cameras/cam1', json=body)
                    assert result.status_code == 200
                    assert attempted.wait(2)
                    assert wait_until(lambda: hub.workers['cam1'].status()['connection'] == 'disconnected')
                    assert store.camera_credentials('cam1')['password'] == body['rtsp_password']
                    available.set()
                assert wait_until(lambda: hub.workers['cam1'].status()['connection'] == 'online')
                assert client.get('/api/v1/cameras/cam1/frame').status_code == 200
                response = client.get('/api/v1/cameras')
                assert response.json()['cameras'][0]['credentials_configured']
                assert body['rtsp_password'] not in response.text
                assert body['rtsp_username'] not in response.text
                assert store.cameras()[0]['preview_fps'] == 5
        assert len(sources) >= 3  # Failed attempt, reconnect, then process restart.
        assert all('camera-user:private-password@' in source for source in sources)
