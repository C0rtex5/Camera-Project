"""Exercise the real production app in an isolated interpreter."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class LiveApiTests(unittest.TestCase):
    def test_production_routes_and_lifecycle(self):
        code = '''
import os
os.environ['SENTINEL_MODE'] = 'production'
os.environ['SENTINEL_CAMERA_CONFIG'] = 'unused-mocked-config.json'
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
with patch('src.api.live_service.LiveService') as factory:
    service = factory.return_value
    service.status.return_value = [{'camera_id': 'gate', 'state': 'UNAVAILABLE', 'device': 'cpu', 'telemetry': None}]
    from src.api.app import app
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        assert client.get('/health').status_code == 200
        assert client.get('/health').json()['device'] == 'cpu'
        assert client.get('/api/v1/cameras').status_code == 200
        assert client.get('/ready').status_code == 503
        assert client.get('/api/v1/demo/scenarios').status_code == 404
        assert client.post('/api/v1/agent/pipeline2/adjudicate', json={}).status_code == 422
        service.status.return_value = [{'camera_id': 'gate', 'state': 'STREAMING', 'telemetry': {}}]
        assert client.get('/ready').status_code == 200
        from src.graph.supervisor_agent import query_site_telemetry
        import json
        result = json.loads(query_site_telemetry.invoke({}))
        assert result['source'] == 'live_cameras'
        service.start.assert_called_once()
    service.close.assert_called_once()
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30,
                                env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        self.assertEqual(result.returncode, 0, result.stderr)
