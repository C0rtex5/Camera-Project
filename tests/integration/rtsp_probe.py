"""Executed only inside the disposable RTSP integration container."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import numpy as np

from src.api.live_service import CameraConfig
from src.edge.checkpoint import camera_fingerprint

root = Path('/opt/sentinelzone/data')
profile = CameraConfig(camera_id='fixture', site_id='RTSP-TEST', url_env='CAMERA_FIXTURE', width=640, height=480,
                       homography={'K': [[600,0,320],[0,600,240],[0,0,1]],
                                   'dist': [0]*5, 'H': [[30,0,0],[0,30,0],[0,0,1]]}).model_dump()
(root/'camera.json').write_text(json.dumps([profile]))
dataset = root/'synthetic-training'
dataset.mkdir()
(dataset/'metadata.json').write_text(json.dumps({'camera_fingerprint': camera_fingerprint(profile), 'sample_interval_seconds': .1}))
for split, offset in [('train',0.), ('validation',.1)]:
    (dataset/split).mkdir()
    history = np.zeros((2,30,8),np.float32)
    history[1,:,0] = 3+offset
    future = np.zeros((2,50,2),np.float32)
    future[1,:,0] = 3+offset
    np.savez(dataset/split/'sample.npz',node_history=history,class_ids=np.array([0,2]),
             edge_index=np.array([[0,1],[1,0]]),edge_attr=np.zeros((2,5),np.float32),future_xy=future)
subprocess.run(['python',str(root/'train_forecaster.py'),'--dataset',str(dataset),'--camera-config',str(root/'camera.json'),
                '--output',str(root/'test-only-forecast.pt'),'--epochs','1'],check=True)
os.environ.update(SENTINEL_CAMERA_CONFIG=str(root/'camera.json'), SENTINEL_GNN_CHECKPOINT=str(root/'test-only-forecast.pt'),
                  SENTINEL_DETECTOR_WEIGHTS=str(root/'detector.pt'), SENTINEL_API_TOKEN='isolated-rtsp-test-token-'*2)
via_setup = os.getenv('RTSP_SETUP_VIA_PANEL') == '1'
if via_setup:
    os.environ['SENTINEL_CAMERA_CONFIG'] = str(root/'not-configured.json')
import httpx
headers = {'Authorization': 'Bearer '+os.environ['SENTINEL_API_TOKEN']}


def until(client, expected, seconds=50):
    deadline = time.monotonic()+seconds
    latest = None
    while time.monotonic()<deadline:
        try:
            response = client.get('/ready',headers=headers)
        except httpx.ConnectError:
            time.sleep(.5)
            continue
        latest = response.json()
        if response.status_code == expected:
            return latest
        time.sleep(.5)
    raise AssertionError(f'Expected readiness {expected}; got {latest}')

server_log = (root/'http-server.log').open('w')
server = subprocess.Popen(['python','-m','uvicorn','src.api.app:app','--host','127.0.0.1','--port','8000'],
                          stdout=server_log,stderr=subprocess.STDOUT)
try:
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=5) as client:
        if via_setup:
            until(client,503)
            draft={key:value for key,value in profile.items() if key!='url_env'}
            draft['rtsp_url']=os.environ['CAMERA_FIXTURE']
            if os.getenv('RTSP_SSH'):draft['ssh_tunnel']=os.environ['RTSP_SSH']
            checked=client.post('/api/v1/setup/camera/check',json={key:value for key,value in draft.items() if key not in ('homography','width','height')},timeout=40)
            assert checked.status_code==200 and checked.json()['ok'],checked.text
            print('CONNECTION_CHECK_OK',flush=True)
            saved=client.put('/api/v1/setup/camera',headers=headers,json=draft)
            assert saved.status_code==200 and not saved.json()['missing'],saved.text
            started=client.post('/api/v1/setup/camera/start',headers=headers,timeout=30)
            assert started.status_code==200,started.text
            print('SETUP_ACTIVATION_OK',flush=True)
        first=until(client,200)
        assert client.get('/api/v1/cameras').status_code==200
        device=first['cameras'][0]['device']
        assert device.split(':')[0]==os.environ.get('RTSP_EXPECT_DEVICE','cpu'),device
        assert client.get('/health').json()['device']==device
        print('RTSP_DEVICE_OK '+device,flush=True)
        telemetry=first['cameras'][0]['telemetry']
        assert telemetry['forecast_status']=='AVAILABLE'
        assert telemetry['inference_age_seconds']<10
        frame=client.get('/api/v1/cameras/fixture/frame',headers=headers)
        assert frame.status_code==200 and frame.content[:2]==b'\xff\xd8'
        snapshot=client.get('/api/v1/hub/live/fixture/snapshot',headers=headers)
        assert snapshot.status_code==200,snapshot.text
        packet=snapshot.json()
        assert packet['source']=='live' and packet['frame_id'] and packet['captured_at']>0
        assert packet['frame_width']==640 and packet['jpeg'] and 'forecast_paths' in packet
        for track in packet['tracks']:
            assert track['forecast_trajectory']==packet['forecast_paths'].get(str(track['track_id']),[])
        review=client.post('/api/v1/hub/reviews',headers=headers,json={'source':'live','frame_id':packet['frame_id'],'verdict':'CONTROLLED_WORK'})
        assert review.status_code==200 and review.json()['frame_id']==packet['frame_id']
        print('HUB_LIVE_SNAPSHOT_OK',flush=True)
        print('RTSP_STREAMING_OK',flush=True)
        lost=until(client,503)
        assert lost['cameras'][0]['telemetry'] is None
        assert client.get('/api/v1/cameras/fixture/frame',headers=headers).status_code==503
        assert client.get('/api/v1/hub/live/fixture/snapshot',headers=headers).status_code==503
        print('RTSP_DISCONNECT_OK',flush=True)
        recovered=until(client,200)
        assert recovered['cameras'][0]['telemetry'] is not None
        print('RTSP_RECONNECT_OK',flush=True)
finally:
    server.terminate()
    try:
        exit_code = server.wait(timeout=25)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
        raise AssertionError('Server failed graceful shutdown')
    finally:
        server_log.close()
        server_output = (root/'http-server.log').read_text()
        print(server_output,flush=True)
# Uvicorn restores and re-raises SIGTERM after completing its lifespan cleanup.
if exit_code not in (0, -signal.SIGTERM) or 'Application shutdown complete.' not in server_output:
    raise AssertionError(f'Server did not complete graceful shutdown: {exit_code}')
print('RTSP_LIFECYCLE_OK',flush=True)
