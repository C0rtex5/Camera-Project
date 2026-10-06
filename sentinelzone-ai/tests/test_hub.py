import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock
from src.api.hub import live_packet, SNAPSHOTS

class HubTests(unittest.TestCase):
    def test_snapshot_is_atomic_and_carries_forecasts(self):
        import threading
        worker=MagicMock();worker.lock=threading.RLock();worker.frame_id='frame-a'
        worker.snapshot.return_value={'state':'STREAMING','device':'cpu','frame_age_seconds':.2}
        worker.jpeg=b'jpeg';worker.config.camera_id='gate';worker.config.site_id='SITE';worker.config.stale_after_seconds=1
        worker.telemetry={'frame_id':'frame-a','captured_at':12,'tracks':[{'forecast_trajectory':[[1,2]]}]}
        result=live_packet(worker)
        self.assertEqual(base64.b64decode(result['jpeg']),b'jpeg')
        self.assertEqual(result['frame_id'],'frame-a')
        worker.telemetry['tracks'].clear()
        self.assertTrue(result['tracks'])
        self.assertNotIn('jpeg',SNAPSHOTS[('live','frame-a')][1])

    def test_display_paths_are_selected_from_the_inference_output(self):
        import numpy as np
        import torch
        from src.edge.edge_runtime import SentinelEdgeRuntime
        runtime=SentinelEdgeRuntime({'K':np.eye(3).tolist(),'dist':[0]*5,'H':np.eye(3).tolist()},None,mqtt_host=None,device='cpu')
        runtime.forecast_available=True
        def model(history,classes,edges,attributes):
            count=len(classes)
            x=torch.tensor([0.,10.,20.]).reshape(1,3,1).expand(count,3,50)
            return {'mode_probs':torch.tensor([[.1,.8,.1]]).expand(count,3),'mu_x':x,'mu_y':x+1}
        runtime.gnn=model
        for frame in range(8):
            result=runtime.execute_frame_cycle(np.array([[0.,0.,2.,2.,.9,0],[8.,0.,10.,2.,.9,2]]),timestamp=frame*.1)
        self.assertEqual(len(result['forecast_paths']),2)
        for path in result['forecast_paths'].values():self.assertEqual(path,[[10.,11.]]*50)
        runtime.close()

    def test_hub_demo_and_review_isolation_without_application_login(self):
        code='''
import os,tempfile,json
from pathlib import Path
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as d:
 os.chdir(d)
 os.environ.update(SENTINEL_MODE='production',SENTINEL_SETUP_FILE=d+'/setup.json')
 from src.api.app import app
 from src.api.demo_service import DemoService
 with TestClient(app) as c:
  public=c.get('/api/v1/showcase/sources')
  assert public.status_code==200 and public.json()['cameras']==[]
  demo_name=public.json()['demos'][0]['id']
  assert c.get('/api/v1/showcase/demo/'+demo_name+'/frames/0').json()['jpeg']
  assert c.get('/api/v1/setup/camera').status_code==200
  assert c.get('/api/v1/hub/live/gate/snapshot').status_code==404
  assert c.post('/api/v1/hub/reviews',json={}).status_code==422
  assert c.get('/api/v1/hub/sources').status_code==200
  sources=c.get('/api/v1/hub/sources').json()
  assert len(sources['demos'])>=3 and sources['cameras']==[]
  curated=[d for d in sources['demos'] if d['kind']=='curated']
  originals=[d for d in sources['demos'] if d['kind']=='original']
  assert curated==[], 'curated bundles must not be listed as scenarios'
  assert originals, 'restored original videos must appear in the hub catalog'
  for row in originals: assert row['frames']>0 and row['title'] and row['id'].startswith('original:')
  assert DemoService._instance is None
  name=sources['demos'][0]['id']
  p=c.get('/api/v1/hub/demo/'+name+'/frames/0').json()
  assert p['source']=='demo' and p['jpeg'] and p['forecast_status']=='ILLUSTRATIVE_DEMO'
  assert c.get('/api/v1/hub/demo/'+name+'/frames/-1').status_code==404
  original=originals[0]['id']
  frame=c.get('/api/v1/hub/demo/'+original+'/frames/1')
  assert frame.status_code==200
  packet=frame.json()
  assert packet['source']=='demo' and packet['jpeg'] and packet['frame_width']>0
  assert packet['forecast_status']=='ILLUSTRATIVE_DEMO'
  # Restored recordings now carry calibrated person / heavy-machinery boxes.
  calibration=packet['detection_calibration']
  assert calibration['frame_width']==packet['frame_width'] and calibration['frame_height']==packet['frame_height']
  for box in packet['detections']:
   # [x1,y1,x2,y2,confidence,class,track_id,role] - both trailing columns are
   # always present so a client's indices cannot shift.
   assert len(box)==8 and box[0]>=0 and box[1]>=0
   assert box[2]<=packet['frame_width']+1 and box[3]<=packet['frame_height']+1
   assert int(box[5]) in (0,1,2,3)
   assert box[6] is None or isinstance(box[6],int)
   assert box[7] is None or box[7]=='operator'
  # Workers and machinery are placed on one shared metric ground plane.
  assert 'tracks_3d' in packet and packet['tracks_3d']
  for track in packet['tracks_3d']:
   assert track['class_name'] in ('WORKER','HEAVY_EQUIPMENT')
   assert len(track['position'])==2 and 'heading' in track and 'history' in track
  assert packet['tracks']==[] and packet['evaluated_pairs']==[] and packet['hazards']==[]
  assert c.get('/api/v1/hub/demo/'+original+'/frames/100000000').status_code==404
  r=c.post('/api/v1/hub/reviews',json={'source':'demo','frame_id':p['frame_id'],'verdict':'FALSE_POSITIVE'})
  assert r.status_code==200,r.text
  saved=json.loads(next(Path('data/demo_reviews').glob('*.json')).read_text())
  assert saved['snapshot']['frame_id']==p['frame_id']
  assert not Path('data/adjudications').exists()
  assert c.post('/api/v1/hub/reviews',json={'source':'live','frame_id':p['frame_id'],'verdict':'FALSE_POSITIVE'}).status_code==409
  assert c.get('/api/v1/hub/live/gate/snapshot').status_code==404
  assert DemoService._instance is None
'''
        result=subprocess.run([sys.executable,'-c',code],env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])},capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
