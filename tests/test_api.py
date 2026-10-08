import time
import unittest
from unittest.mock import patch
from tests.hub_support import HubAPITest, camera


class TestHubAPI(HubAPITest):
    def test_authentication_and_roles(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/v1/cameras').status_code, 401)
        self.assertEqual(self.client.get('/health/live').status_code, 200)
        self.login('viewer')
        self.assertEqual(self.client.put('/api/v1/cameras/cam1', json=camera()).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/cameras').status_code, 200)
        self.login('operator')
        self.assertEqual(self.client.put('/api/v1/cameras/cam1', json=camera()).status_code, 403)

    def test_csrf_and_session_expiration(self):
        self.assertEqual(self.client.post('/api/v1/auth/logout', headers={'Origin': 'https://attacker.invalid'}).status_code, 403)
        del self.client.headers['X-Sentinel-Request']
        self.assertEqual(self.client.post('/api/v1/auth/logout').status_code, 403)
        with self.store.transaction() as db:
            db.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
        self.assertEqual(self.client.get('/api/v1/cameras').status_code, 401)

    def test_passwords_are_hashed_and_session_is_http_only(self):
        with self.store.transaction() as db:
            user = db.execute('SELECT * FROM users WHERE name="admin"').fetchone()
            self.assertNotEqual(user['digest'], 'test-password-123')
        response = self.client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password-123'})
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])

    def test_camera_limit_and_secret_redaction(self):
        for i in range(4):
            self.save_camera(camera_id=f'cam{i}', credential_secret='camera_secret')
        self.assertEqual(self.client.put('/api/v1/cameras/fifth', json=camera('fifth')).status_code, 400)
        self.login('viewer')
        data = self.client.get('/api/v1/cameras').json()
        self.assertNotIn('rtsp_url', data['cameras'][0])
        self.assertNotIn('credential_secret', data['cameras'][0])

    def test_embedded_credentials_and_unsafe_secrets_rejected(self):
        self.assertEqual(self.client.put('/api/v1/cameras/cam1', json=camera(rtsp_url='rtsp://user:password@host/stream')).status_code, 422)
        self.assertEqual(self.client.put('/api/v1/cameras/cam1', json=camera(credential_secret='../password')).status_code, 422)

    def test_incidents_and_reviews_survive_new_store(self):
        response = self.client.post('/api/v1/incidents/publish', json={'event_id': 'INC-test', 'severity': 'WARNING_LEVEL_2'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/v1/incidents/INC-test/review', json={'verdict': 'FALSE_POSITIVE', 'notes': 'Reviewed snapshot'}).status_code, 200)
        from src.hub.store import Store
        self.assertEqual(Store(self.directory.name).get_incident('INC-test')['review']['verdict'], 'FALSE_POSITIVE')
        self.assertIn('INC-test', self.client.get('/api/v1/agent/active_learning/export').text)

    def test_disk_failure_reports_unavailable(self):
        with patch.object(self.store, 'incident', side_effect=OSError('disk full')):
            response = self.client.post('/api/v1/incidents/publish', json={'event_id': 'failed'})
        self.assertEqual(response.status_code, 503)
        self.assertIsNotNone(self.hub.storage_error)

    def test_health_is_degraded_without_cameras(self):
        response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'degraded')

    def test_websocket_requires_authentication(self):
        self.client.cookies.clear()
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/ws/cameras/cam1'):
                pass

    def test_large_request_is_rejected(self):
        response = self.client.post('/api/v1/incidents/publish', content=b'x' * (1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)

    def test_duplicate_incident_is_conflict_not_storage_failure(self):
        self.assertEqual(self.client.post('/api/v1/incidents/publish', json={'event_id': 'duplicate'}).status_code, 200)
        self.assertEqual(self.client.post('/api/v1/incidents/publish', json={'event_id': 'duplicate'}).status_code, 409)
        self.assertIsNone(self.hub.storage_error)

    def test_evidence_download_and_path_guards(self):
        self.store.incident({'event_id': 'evidence'})
        path = self.store.root / 'evidence' / 'snapshot.jpg'
        path.write_bytes(b'snapshot')
        self.store.update_incident('evidence', {'evidence': {'snapshot': path.name}})
        self.assertEqual(self.client.get('/api/v1/incidents/evidence/evidence/snapshot').content, b'snapshot')
        self.store.update_incident('evidence', {'evidence': {'snapshot': '../hub.sqlite3'}})
        self.assertEqual(self.client.get('/api/v1/incidents/evidence/evidence/snapshot').status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/v1/incidents/evidence/evidence/snapshot').status_code, 401)

    def test_frame_and_telemetry_ids_match(self):
        from src.hub.service import CameraWorker
        from src.hub.models import Camera
        worker = CameraWorker(Camera.model_validate(camera()), self.hub)
        worker.jpeg = b'latest-preview'; worker.processed_jpeg = b'processed-frame'
        worker.frame_id = 6; worker.captured = time.time()
        worker.telemetry = {'frame_id': 5, 'capture_timestamp': time.time()}
        self.hub.workers['cam1'] = worker
        response = self.client.get('/api/v1/cameras/cam1/frame?frame_id=5')
        self.assertEqual(response.content, b'processed-frame')
        self.assertEqual(response.headers['x-frame-id'], '5')
        self.assertEqual(self.client.get('/api/v1/cameras/cam1/frame?frame_id=4').status_code, 409)
        self.assertEqual(self.client.get('/api/v1/cameras/cam1/frame').content, b'latest-preview')
        # These workers are intentionally not started.
        self.hub.workers.clear()

    def test_live_incident_socket_publishes_persisted_event(self):
        with self.client.websocket_connect('/ws/incidents') as socket:
            self.client.post('/api/v1/incidents/publish', json={'event_id': 'socket-event'})
            self.assertEqual(socket.receive_json()['event_id'], 'socket-event')


class TestDirectLocalPanel(unittest.TestCase):
    def setUp(self):
        import tempfile
        from fastapi.testclient import TestClient
        from src.hub.api import create_app
        from src.hub.service import DeviceManager, Hub
        from src.hub.store import Store
        from tests.hub_support import EmptyDetector
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.hub = Hub(self.store, DeviceManager('cpu', factory=lambda d: EmptyDetector()))
        self.client = TestClient(create_app(self.store, self.hub))
        self.client.__enter__()
        self.client.headers['X-Sentinel-Request'] = 'hub'

    def tearDown(self):
        self.hub.workers.clear()  # Test workers were not started.
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def test_panel_and_configuration_work_without_credentials(self):
        self.assertFalse(self.client.cookies)
        identity = self.client.get('/api/v1/auth/me').json()
        self.assertFalse(identity['authentication_required'])
        self.assertEqual(identity['role'], 'administrator')
        self.assertEqual(self.client.get('/api/v1/cameras').status_code, 200)
        self.assertEqual(self.client.put('/api/v1/cameras/cam1', json=camera()).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/cameras').json()['cameras'][0]['camera_id'], 'cam1')
        page = self.client.get('/').text
        self.assertNotIn('id="login-form"', page)
        self.assertNotIn('id="password"', page)
        self.assertIn('id="rtsp-password"', page)
        self.assertEqual(self.client.post('/api/v1/incidents/publish', json={'event_id': 'local-incident'}).status_code, 200)
        self.assertEqual(self.client.post('/api/v1/incidents/local-incident/review', json={'verdict': 'UNCERTAIN'}).status_code, 200)
        self.assertEqual(self.store.get_incident('local-incident')['review']['actor'], 'local-panel')

    def test_local_telemetry_socket_needs_no_session(self):
        from src.hub.service import CameraWorker
        from src.hub.models import Camera
        self.hub.workers['cam1'] = CameraWorker(Camera.model_validate(camera()), self.hub)
        with self.client.websocket_connect('/ws/cameras/cam1') as socket:
            self.assertEqual(socket.receive_json()['camera_id'], 'cam1')

    def test_direct_access_keeps_same_origin_protection(self):
        response = self.client.put('/api/v1/cameras/cam1', json=camera(), headers={'Origin': 'https://other.invalid'})
        self.assertEqual(response.status_code, 403)
        del self.client.headers['X-Sentinel-Request']
        self.assertEqual(self.client.put('/api/v1/cameras/cam1', json=camera()).status_code, 403)

    def test_camera_credentials_persist_and_are_write_only(self):
        import json
        from src.hub.store import Store
        from src.hub.models import Camera
        from src.hub.service import CameraWorker, Hub
        username, password = 'operator@factory', 'p@ss:/?#% word'
        body = camera(rtsp_username=username, rtsp_password=password)
        response = self.client.put('/api/v1/cameras/cam1', json=body)
        self.assertEqual(response.status_code, 200)
        public = self.client.get('/api/v1/cameras')
        self.assertNotIn(username, public.text)
        self.assertNotIn(password, public.text)
        self.assertTrue(public.json()['cameras'][0]['credentials_configured'])
        self.assertNotIn('rtsp_password', self.store.cameras()[0])
        reopened = Store(self.directory.name)
        worker = CameraWorker(Camera.model_validate(reopened.cameras()[0]), Hub(reopened, self.hub.device))
        self.assertEqual(worker.source(), 'rtsp://operator%40factory:p%40ss%3A%2F%3F%23%25%20word@192.168.10.20/stream')
        self.assertEqual(self.client.put('/api/v1/cameras/cam1', json=camera(name='Renamed camera')).status_code, 200)
        self.assertEqual(reopened.camera_credentials('cam1')['password'], password)
        with self.store.transaction() as db:
            audit = json.dumps([dict(r) for r in db.execute('SELECT * FROM audit')])
        self.assertNotIn(password, audit)
        self.assertNotIn(username, audit)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        backup = self.store.root / 'camera-backup.sqlite3'
        reopened.backup(backup)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        import sqlite3
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute('SELECT username,password FROM camera_credentials WHERE camera_id=?', ('cam1',)).fetchone(), (username, password))
        self.assertEqual(self.client.put('/api/v1/cameras/cam1', json=camera(clear_credentials=True, credential_secret='external_secret')).status_code, 200)
        self.assertIsNone(reopened.cameras()[0]['credential_secret'])
        self.assertIsNone(reopened.camera_credentials('cam1'))
        self.assertEqual(worker.source(), camera()['rtsp_url'])

    def test_camera_password_never_appears_in_validation_errors(self):
        password = 'private-camera-password'
        response = self.client.put('/api/v1/cameras/cam1', json=camera(rtsp_password=password))
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(password, response.text)
        response = self.client.put('/api/v1/cameras/cam1', json=camera(rtsp_username='operator', rtsp_password=password, clear_credentials=True))
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(password, response.text)
        response = self.client.put('/api/v1/cameras/cam1', json=camera(rtsp_username='operator', rtsp_password=password, calibration={'invalid': password}))
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(password, response.text)

    def test_deleting_camera_removes_its_credentials(self):
        response = self.client.put('/api/v1/cameras/cam1', json=camera(rtsp_username='operator', rtsp_password='camera-pass'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.delete('/api/v1/cameras/cam1').status_code, 200)
        self.assertIsNone(self.store.camera_credentials('cam1'))
