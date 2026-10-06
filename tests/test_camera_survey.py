import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from src.api.camera_setup import SetupDraft
from src.api.camera_survey import SurveyStore, rtsp_endpoint
from src.api.connection_check import PROBE, _probe_env


class CameraSurveyTests(unittest.TestCase):
    def test_record_is_private_sanitized_and_selectable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SENTINEL_SURVEY_FILE': str(Path(directory) / 'survey.json')}):
            draft = SetupDraft(camera_id='Gate 1', site_id='North Yard', location='North Yard', host='camera.local', rtsp_port=554, stream_path='live', username='operator', password='private-value')
            result = {'ok': True, 'stage': 'connected', 'message': 'frame received', 'width': 1280, 'height': 720, 'quality': {'status': 'acceptable', 'notes': []}, 'blocker': None}
            record = SurveyStore().add(draft=draft, result=result)
            self.assertNotIn('private-value', json.dumps(record))
            self.assertEqual(record['rtsp_endpoint'], 'rtsp://camera.local:554/live')
            path = Path(os.environ['SENTINEL_SURVEY_FILE'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn('token=secret', path.read_text())
            public = SurveyStore().public()
            self.assertEqual(public['selected_camera_id'], None)
            self.assertEqual(public['records'][0]['selected'], False)
            SurveyStore().select(record['record_id'])
            report = SurveyStore().report()
            self.assertIn('Gate-1', report)
            self.assertIn('Selected camera', report)
            self.assertNotIn('private-value', report)
            self.assertNotIn('token=secret', report)

    def test_structured_endpoint_and_inventory_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SENTINEL_SURVEY_FILE': str(Path(directory) / 'survey.json')}):
            draft = SetupDraft(camera_id='CAM-01', site_id='SITE-01', location='North Yard / Gate 3', host='192.168.10.21', rtsp_port=8554, stream_path='Streaming/Channels/101', access_path='direct')
            self.assertEqual(draft.resolved_rtsp_url(), 'rtsp://192.168.10.21:8554/Streaming/Channels/101')
            result = {'ok': True, 'stage': 'connected', 'message': 'frame received', 'width': 1280, 'height': 720, 'quality': {'status': 'acceptable', 'notes': []}, 'blocker': None, 'transport': 'direct', 'host': '192.168.10.21', 'rtsp_port': 8554, 'stream_path': '/Streaming/Channels/101', 'access_path': 'direct', 'tcp_reachable': True, 'tunnel_established': None}
            record = SurveyStore().add(draft=draft, result=result)
            self.assertEqual(record['location'], 'North Yard / Gate 3')
            self.assertEqual(record['host'], '192.168.10.21')
            self.assertEqual(record['rtsp_port'], 8554)
            self.assertEqual(record['transport'], 'direct')
            self.assertEqual(record['result']['tcp_reachable'], True)
            report = SurveyStore().report()
            for expected in ('North Yard / Gate 3', '192.168.10.21:8554', 'direct', 'reachable'):
                self.assertIn(expected, report)

    def test_setup_store_round_trips_structured_inventory_fields(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SENTINEL_SETUP_FILE': str(Path(directory) / 'camera.json')}):
            from src.api.camera_setup import SetupStore
            store = SetupStore()
            store.save({'camera_id': 'CAM-01', 'location': 'North Yard / Gate 3', 'host': '192.168.10.21', 'rtsp_port': 8554, 'stream_path': 'Streaming/Channels/101', 'access_path': 'direct'})
            public = store.public()
            self.assertEqual(public['location'], 'North Yard / Gate 3')
            self.assertEqual(public['host'], '192.168.10.21')
            self.assertEqual(public['rtsp_port'], 8554)
            self.assertEqual(public['stream_path'], '/Streaming/Channels/101')
            self.assertEqual(store.load().resolved_rtsp_url(), 'rtsp://192.168.10.21:8554/Streaming/Channels/101')

    def test_structured_endpoint_validation_rejects_unsafe_values(self):
        with self.assertRaises(ValueError):
            SetupDraft(host='rtsp://camera/stream', rtsp_port=554, stream_path='live')
        with self.assertRaises(ValueError):
            SetupDraft(host='camera', rtsp_port=70000, stream_path='live')
        with self.assertRaises(ValueError):
            SetupDraft(host='camera', rtsp_port=554, stream_path='live?token=secret')

    def test_sec01_status_tracks_readiness(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SENTINEL_SURVEY_FILE': str(Path(directory) / 'survey.json')}):
            store = SurveyStore()
            self.assertFalse(store.public()['sec01_status']['complete'])
            draft = SetupDraft(camera_id='CAM-01', site_id='SITE-01', location='Gate 3', host='camera.local', rtsp_port=554, stream_path='live')
            record = store.add(draft=draft, result={'ok': True, 'stage': 'connected', 'message': 'frame received', 'width': 640, 'height': 480})
            store.select(record['record_id'])
            status = store.public()['sec01_status']
            self.assertTrue(status['complete'])
            self.assertEqual(status['missing'], [])

    def test_selection_requires_documented_inventory_fields(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SENTINEL_SURVEY_FILE': str(Path(directory) / 'survey.json')}):
            draft = SetupDraft(camera_id='incomplete-inventory', site_id='site', rtsp_url='rtsp://camera.local/live')
            record = SurveyStore().add(draft=draft, result={'ok': True, 'stage': 'connected', 'message': 'frame received', 'width': 640, 'height': 480})
            with self.assertRaisesRegex(ValueError, 'physical location'):
                SurveyStore().select(record['record_id'])

    def test_block_disposition_is_persisted_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SENTINEL_SURVEY_FILE': str(Path(directory) / 'survey.json')}):
            draft = SetupDraft(camera_id='blocked', site_id='site', rtsp_url='rtsp://camera.local/live')
            record = SurveyStore().add(draft=draft, result={'ok': False, 'stage': 'camera', 'blocker': 'connection_refused', 'message': 'blocked'})
            updated = SurveyStore().set_disposition(record['record_id'], 'reviewed', 'IT confirmed the port is closed')
            self.assertEqual(updated['disposition']['status'], 'reviewed')
            self.assertIn('IT confirmed', SurveyStore().public()['records'][0]['disposition']['note'])
            with self.assertRaises(ValueError):
                SurveyStore().set_disposition(record['record_id'], 'unknown')

    def test_blocked_camera_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SENTINEL_SURVEY_FILE': str(Path(directory) / 'survey.json')}):
            draft = SetupDraft(camera_id='blocked', site_id='site', rtsp_url='rtsp://camera.local/live')
            record = SurveyStore().add(draft=draft, result={'ok': False, 'stage': 'camera', 'blocker': 'connection_refused', 'message': 'blocked'})
            with self.assertRaisesRegex(ValueError, 'returned a video frame'):
                SurveyStore().select(record['record_id'])

    def test_endpoint_sanitizer_removes_userinfo_query_and_fragment(self):
        self.assertEqual(rtsp_endpoint('rtsp://user:secret@host:8554/live?token=x#frag'), 'rtsp://host:8554/live')
        self.assertEqual(rtsp_endpoint(''), '')

    def test_probe_reports_resolution_and_objective_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'sample.avi')
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'MJPG'), 5.0, (640, 480))
            if not writer.isOpened():
                self.skipTest('OpenCV MJPG writer is unavailable')
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            frame[100:380, 80:560] = 220
            for _ in range(3):
                writer.write(frame)
            writer.release()
            environment = _probe_env()
            environment['SENTINEL_PROBE_SOURCE'] = path
            result = subprocess.run([sys.executable, '-c', PROBE], env=environment, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertTrue(packet['ok'])
            self.assertEqual((packet['width'], packet['height']), (640, 480))
            self.assertEqual(packet['quality']['resolution_status'], 'meets_poc_minimum')
            self.assertIn(packet['quality']['status'], ('acceptable', 'review'))
            for field in ('brightness_mean', 'contrast_std', 'sharpness_laplacian'):
                self.assertIsInstance(packet['quality'][field], float)

    def test_api_logs_and_documents_survey_without_credentials(self):
        code = '''
import json, os, tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as directory:
    os.environ.update(SENTINEL_MODE='production', SENTINEL_SETUP_FILE=directory+'/setup.json', SENTINEL_SURVEY_FILE=directory+'/survey.json', SENTINEL_CAMERA_CONFIG=directory+'/missing.json')
    from src.api.app import app
    with patch('src.api.connection_check.check_connection', return_value={'ok':True,'stage':'connected','message':'frame received','width':1280,'height':720,'quality':{'status':'acceptable','notes':[]},'blocker':None}):
        with TestClient(app) as client:
            assert client.get('/api/v1/setup/survey').json()['records'] == []
            response=client.post('/api/v1/setup/survey', json={'camera_id':'Gate 1','site_id':'SITE-01','location':'North Yard','host':'camera.local','rtsp_port':554,'stream_path':'live','username':'operator','password':'private-value'})
            assert response.status_code == 200, response.text
            assert 'private-value' not in response.text
            record=response.json()['record']
            assert record['result']['width'] == 1280
            listing=client.get('/api/v1/setup/survey').json()
            assert len(listing['records']) == 1
            selected=client.post('/api/v1/setup/survey/select', json={'record_id':record['record_id']})
            assert selected.status_code == 200, selected.text
            report=client.get('/api/v1/setup/survey/report.md')
            assert report.status_code == 200 and 'Gate-1' in report.text
            assert 'private-value' not in report.text
            machine=client.get('/api/v1/setup/survey/report.json')
            assert machine.status_code == 200 and machine.json()['card_id']=='SEC-01'
            assert machine.json()['selected_camera']['location']=='North Yard'
            assert 'private-value' not in machine.text
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=40,
                                env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_batch_survey_keeps_valid_results_and_reports_invalid_candidates(self):
        code = '''
import os, tempfile
from unittest.mock import patch
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as directory:
    os.environ.update(SENTINEL_MODE='production', SENTINEL_SETUP_FILE=directory+'/setup.json', SENTINEL_SURVEY_FILE=directory+'/survey.json', SENTINEL_CAMERA_CONFIG=directory+'/missing.json')
    from src.api.app import app
    result={'ok':True,'stage':'connected','message':'frame received','width':640,'height':480,'quality':{'status':'acceptable','notes':[]},'blocker':None}
    with patch('src.api.connection_check.check_connection', return_value=result):
        with TestClient(app) as client:
            response=client.post('/api/v1/setup/survey/batch', json={'candidates':[
                {'camera_id':'one','site_id':'site','rtsp_url':'rtsp://one.local/live'},
                {'camera_id':'two','site_id':'site','rtsp_url':'rtsp://two.local/live'},
                {'camera_id':'incomplete','site_id':'site'},
            ]})
            assert response.status_code==200, response.text
            body=response.json()
            assert len(body['records'])==2
            assert len(body['errors'])==1
            assert body['errors'][0]['camera_id']=='incomplete'
            assert 'password' not in response.text.lower()
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=40,
                                env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        self.assertEqual(result.returncode, 0, result.stderr)
