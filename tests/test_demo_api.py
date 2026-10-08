from unittest.mock import patch
from fastapi import FastAPI
from tests.hub_support import HubAPITest


class TestDemoAPI(HubAPITest):
    def test_dashboard_and_offline_assets(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('SENTINELZONE-AI', response.text)
        self.assertNotIn('https://', response.text)
        self.assertEqual(self.client.get('/assets/hub.js').status_code, 200)
        self.assertEqual(self.client.get('/assets/three.r128.min.js').status_code, 200)
        self.assertNotIn('unsafe-inline', response.headers['content-security-policy'].split('script-src')[1].split(';')[0])

    def test_demo_is_explicitly_disabled(self):
        self.assertEqual(self.client.get('/api/v1/demo/scenarios').status_code, 503)

    def test_legacy_routes_are_preserved_and_protected(self):
        legacy = FastAPI()
        @legacy.get('/api/v1/demo/scenarios')
        def scenarios():
            return [{'id': 'recorded'}]
        @legacy.get('/')
        def dashboard():
            return {'page': 'recorded_demo'}
        with patch.dict('os.environ', {'SENTINEL_ENABLE_DEMO': 'true'}), patch('src.hub.api.importlib.import_module') as module:
            module.return_value.app = legacy
            self.assertEqual(self.client.get('/api/v1/demo/scenarios').json(), [{'id': 'recorded'}])
            self.assertEqual(self.client.get('/demo/').json(), {'page': 'recorded_demo'})
            self.assertEqual(self.client.get('/demo/health').status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/v1/demo/scenarios').status_code, 401)
