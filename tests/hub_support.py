import tempfile
import unittest
import numpy as np
from fastapi.testclient import TestClient
from src.hub.api import create_app
from src.hub.service import DeviceManager, Hub
from src.hub.store import Store


class EmptyDetector:
    def detect(self, frame):
        return np.empty((0, 6), np.float32), []


def calibration():
    return {'width': 1000, 'height': 1000, 'K': [[1000, 0, 500], [0, 1000, 500], [0, 0, 1]],
            'dist': [0] * 5, 'H': [[100, 0, 0], [0, 100, 0], [0, 0, 1]],
            'pixel_references': [[100, 100], [800, 100], [800, 800], [100, 800]],
            'ground_references': [[1, 1], [8, 1], [8, 8], [1, 8]], 'coordinate_frame': 'surveyed-floor'}


def camera(camera_id='cam1', **kwargs):
    return {'camera_id': camera_id, 'site_id': 'factory', 'name': camera_id, 'rtsp_url': 'rtsp://192.168.10.20/stream',
            'enabled': False, **kwargs}


class HubAPITest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.store.add_user('admin', 'test-password-123', 'administrator')
        self.store.add_user('operator', 'test-password-123', 'operator')
        self.store.add_user('viewer', 'test-password-123', 'viewer')
        self.hub = Hub(self.store, DeviceManager('cpu', factory=lambda d: EmptyDetector()))
        self.client = TestClient(create_app(self.store, self.hub, authentication_required=True))
        self.client.__enter__()
        self.client.headers['X-Sentinel-Request'] = 'hub'
        self.login('admin')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def login(self, username):
        response = self.client.post('/api/v1/auth/login', json={'username': username, 'password': 'test-password-123'})
        self.assertEqual(response.status_code, 200)

    def save_camera(self, **kwargs):
        config = camera(**kwargs)
        response = self.client.put('/api/v1/cameras/' + config['camera_id'], json=config)
        self.assertEqual(response.status_code, 200, response.text)
        return config
