import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.api.camera_setup import SetupDraft, SetupStore


class CameraSetupTests(unittest.TestCase):
    def test_names_with_spaces_save_and_connection_does_not_require_models(self):
        draft=SetupDraft(camera_id='Aza 1', site_id='Test Site', rtsp_url='rtsp://camera:8554/')
        self.assertEqual(draft.camera_id, 'Aza-1')
        self.assertEqual(draft.site_id, 'Test-Site')
        self.assertEqual(draft.connection_missing(), [])

    def test_private_draft_survives_reload_without_returning_password(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'SENTINEL_SETUP_FILE':directory+'/camera.json'}):
            store=SetupStore()
            store.save({'rtsp_url':'rtsp://camera.local/stream','username':'operator','password':'private:@value'})
            result=SetupStore().public()
            self.assertTrue(result['password_saved'])
            self.assertNotIn('private',json.dumps(result))
            self.assertTrue(result['missing'])
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode),0o600)
            self.assertIn('operator:private%3A%40value@',store.load().source())
            store.save({'camera_id':'gate'})
            self.assertTrue(store.public()['password_saved'])
            store.save({'password':''})
            self.assertFalse(store.public()['password_saved'])

    def test_embedded_credentials_are_rejected(self):
        with self.assertRaises(ValueError):SetupDraft(rtsp_url='rtsp://user:secret@camera/stream')
        with self.assertRaises(ValueError):SetupDraft(rtsp_url='http://camera/stream')

    def test_app_opens_without_camera_and_accepts_partial_setup(self):
        code='''
import os,tempfile,json
from pathlib import Path
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as d:
 os.environ.update(SENTINEL_MODE='production',SENTINEL_SETUP_FILE=d+'/camera.json',SENTINEL_CAMERA_CONFIG=d+'/missing.json')
 from src.api.app import app
 with TestClient(app) as c:
  assert c.get('/').status_code==200
  assert c.get('/setup').status_code==200
  assert c.get('/health').status_code==200
  assert c.get('/ready').status_code==503
  assert c.get('/api/v1/setup/camera').status_code==200
  named=c.put('/api/v1/setup/camera',json={'camera_id':'Aza 1','rtsp_url':'rtsp://camera:8554/'})
  assert named.status_code==200 and named.json()['camera_id']=='Aza-1',named.text
  invalid=c.put('/api/v1/setup/camera',json={'width':-1,'password':'DO-NOT-LEAK'})
  assert invalid.status_code==400 and 'width' in invalid.text and 'DO-NOT-LEAK' not in invalid.text
  from unittest.mock import patch
  with patch('src.api.connection_check.check_connection',return_value={'ok':True,'width':640,'height':480}) as probe:
   checked=c.post('/api/v1/setup/camera/check',json={'rtsp_url':'rtsp://camera/stream'})
   assert checked.status_code==200 and checked.json()['ok'],checked.text
   probe.assert_called_once()
  assert c.put('/api/v1/setup/camera',json={'password':'hidden'}).status_code==200
  r=c.put('/api/v1/setup/camera',json={'rtsp_url':'rtsp://camera/stream','username':'operator','password':'never-return-me'})
  assert r.status_code==200,r.text
  assert 'never-return-me' not in r.text
  assert r.json()['password_saved']
  assert c.post('/api/v1/setup/camera/start').status_code==409
  r=c.put('/api/v1/setup/camera',json={'rtsp_url':'rtsp://user:never-return-me@host/stream'})
  assert r.status_code==400 and 'never-return-me' not in r.text
 with TestClient(app) as c:
  assert c.get('/setup').status_code==200
  assert c.get('/api/v1/setup/camera').json()['password_saved']
'''
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
