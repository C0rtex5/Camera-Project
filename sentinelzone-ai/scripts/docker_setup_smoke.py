"""Check setup-only startup and private draft persistence inside a built image."""
import argparse
import json
from docker_rtsp_smoke import api, logs

CODE = '''
from fastapi.testclient import TestClient
from pathlib import Path
import stat
from src.api.app import app
headers={}
with TestClient(app) as c:
 assert c.get('/setup').status_code==200
 assert c.get('/assets/vendor/three.min.js').status_code==200
 public=c.get('/api/v1/showcase/sources')
 assert public.status_code==200 and not public.json()['cameras']
 scenario=public.json()['demos'][0]['id']
 assert scenario.startswith('original:'),scenario
 assert c.get('/api/v1/showcase/demo/'+scenario+'/frames/0').json()['jpeg']
 assert c.get('/api/v1/setup/camera').status_code==200
 survey=c.get('/api/v1/setup/survey')
 assert survey.status_code==200 and survey.json()['records']==[]
 assert 'survey-section' in c.get('/setup').text
 assert 'survey-batch-run' in c.get('/setup').text
 assert c.get('/api/v1/setup/survey/report.md').status_code==200
 assert c.get('/api/v1/setup/survey/report.json').json()['card_id']=='SEC-01'
 assert c.post('/api/v1/setup/survey/batch',json={'candidates':[]}).status_code==400
 from unittest.mock import patch
 probe={'ok':True,'stage':'connected','message':'frame received','width':1280,'height':720,'quality':{'status':'acceptable','notes':[]},'blocker':None,'transport':'direct','host':'192.168.10.21','rtsp_port':8554,'stream_path':'/Streaming/Channels/101','access_path':'direct','tcp_reachable':True,'tunnel_established':None}
 with patch('src.api.connection_check.check_connection',return_value=probe):
  structured=c.post('/api/v1/setup/survey',json={'camera_id':'CAM-01','location':'North Yard / Gate 3','host':'192.168.10.21','rtsp_port':8554,'stream_path':'Streaming/Channels/101','access_path':'direct'})
 assert structured.status_code==200 and structured.json()['record']['location']=='North Yard / Gate 3'
 assert c.get('/api/v1/hub/live/gate/snapshot').status_code==404
 assert c.get('/api/v1/hub/sources').status_code==200
 sources=c.get('/api/v1/hub/sources',headers=headers).json()
 assert sources['demos'] and not sources['cameras']
 assert all(row['kind']=='original' for row in sources['demos'])
 frame=c.get('/api/v1/hub/demo/'+sources['demos'][0]['id']+'/frames/0',headers=headers).json()
 assert frame['source']=='demo' and frame['jpeg']
 from src.api.demo_service import DemoService
 assert DemoService._instance is None
 import time,statistics,resource
 timings=[]
 for _ in range(10):
  before=time.perf_counter()
  assert c.get('/api/v1/hub/demo/'+sources['demos'][0]['id']+'/frames/1',headers=headers).status_code==200
  timings.append(1000*(time.perf_counter()-before))
 print('DEMO_API_MEASUREMENT median_ms='+str(round(statistics.median(timings),2))+' peak_RSS_MiB='+str(round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)))
 print('OFFLINE_HUB_OK: restored original recordings, no inference worker')
 assert c.get('/health').json()['monitoring_ready'] is False
 assert c.get('/ready',headers=headers).status_code==503
 assert c.get('/api/v1/setup/camera').status_code==200
 r=c.put('/api/v1/setup/camera',headers=headers,json={'username':'fixture','password':'test-only-password'})
 assert r.status_code==200,r.text
 assert 'test-only-password' not in r.text
 assert r.headers['cache-control']=='no-store'
 assert r.json()['missing'] and r.json()['password_saved']
 assert stat.S_IMODE(Path('data/setup/camera.json').stat().st_mode)==0o600
 assert c.post('/api/v1/setup/camera/start',headers=headers).status_code==409
with TestClient(app) as c:
 r=c.get('/api/v1/setup/camera',headers=headers)
 assert r.status_code==200 and r.json()['password_saved']
 assert c.get('/health').status_code==200
print('SETUP_INTEGRATION_OK: no camera, incomplete draft, no application login, private password, persisted reload')
'''

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--image',default='sentinelzone-ai:production')
 args=parser.parse_args()
 c=json.loads(api('POST','/containers/create',{'Image':args.image,'Cmd':['python','-c',CODE],
  'Env':['SENTINEL_MODE=production','OMP_NUM_THREADS=2'],
  'Volumes':{'/opt/sentinelzone/data':{}},
  'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],'Tmpfs':{'/tmp':'rw,size=268435456,mode=1777'}}}))['Id']
 try:
  api('POST',f'/containers/{c}/start')
  result=json.loads(api('POST',f'/containers/{c}/wait'))
  output=logs(c)
  if result['StatusCode']!=0:raise RuntimeError(output[-3000:])
  print(output.strip())
 finally:api('DELETE',f'/containers/{c}?force=true&v=true')

if __name__=='__main__':main()
