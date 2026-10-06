"""Real RTSP transport/inference smoke test, using disposable Docker resources.

Requires a local Docker socket and the built sentinelzone-ai:production image.
No host ports are exposed; no real cameras or production credentials are used.
Synthetic training verifies mechanics only, not site forecasting accuracy.
"""
import argparse
import http.client
import io
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import tarfile
import time
from urllib.parse import quote
import uuid


class DockerConnection(http.client.HTTPConnection):
    socket_path = '/var/run/docker.sock'
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def api(method, path, payload=None, binary=None, timeout=180):
    connection = DockerConnection('localhost',timeout=timeout)
    body = binary if binary is not None else (json.dumps(payload) if payload is not None else None)
    connection.request(method,'/v1.40'+path,body=body,headers={'Content-Type':'application/x-tar' if binary is not None else 'application/json'})
    response = connection.getresponse()
    data = response.read()
    if response.status>=300:
        raise RuntimeError(f'Docker {method} {path}: {response.status} {data[:400]!r}')
    connection.close()
    return data


def logs(container):
    data = api('GET',f'/containers/{container}/logs?stdout=1&stderr=1')
    chunks=[]
    while len(data)>=8:
        size=int.from_bytes(data[4:8],'big')
        chunks.append(data[8:8+size].decode(errors='replace'))
        data=data[8+size:]
    return ''.join(chunks)


def upload(container, destination, files, uid=10001):
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as archive:
        for name,path in files.items():
            info=archive.gettarinfo(str(path),arcname=name)
            info.uid=uid;info.gid=uid;info.mode=0o644
            with path.open('rb') as stream:
                archive.addfile(info,stream)
    api('PUT',f'/containers/{container}/archive?path={quote(destination)}',binary=buffer.getvalue())


def wait_marker(container, marker, timeout=75):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        output=logs(container)
        if marker in output:
            print(marker,flush=True)
            return
        state=json.loads(api('GET',f'/containers/{container}/json'))['State']
        if not state['Running']:
            raise RuntimeError(f'Probe exited before {marker}:\n{output[-5000:]}')
        time.sleep(1)
    raise TimeoutError(f'Timed out waiting for {marker}:\n{logs(container)[-5000:]}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='sentinelzone-ai:production')
    parser.add_argument('--media-image',default='bluenviron/mediamtx:1-ffmpeg')
    parser.add_argument('--setup',action='store_true',help='Start monitoring through the saved setup API')
    parser.add_argument('--ssh',action='store_true',help='Route the fixture camera through a real SSH server; implies --setup')
    parser.add_argument('--gpu',action='store_true',help='Expose NVIDIA GPU and verify automatic CUDA inference')
    parser.add_argument('--socket',default='/var/run/docker.sock')
    args=parser.parse_args()
    if args.ssh: args.setup=True
    DockerConnection.socket_path=args.socket
    repo=Path(__file__).resolve().parents[1]
    name='sentinel-rtsp-test-'+uuid.uuid4().hex[:10]
    containers=[];network=None
    try:
        if not args.media_image.startswith('sha256:'):
            print('Pulling RTSP test dependency',flush=True)
            pull=api('POST','/images/create?fromImage='+quote(args.media_image,safe=''),timeout=300)
            for line in pull.splitlines():
                event=json.loads(line)
                if 'error' in event:
                    raise RuntimeError(event['error'])
        media_id=json.loads(api('GET','/images/'+quote(args.media_image,safe='')+'/json'))['Id']
        print('RTSP dependency '+media_id,flush=True)
        network=json.loads(api('POST','/networks/create',{'Name':name,'Internal':True}))['Id']
        def create(payload):
            payload.setdefault('HostConfig',{})['NetworkMode']=name
            payload['Labels']={'sentinelzone.test':name}
            result=json.loads(api('POST','/containers/create',payload))['Id']
            containers.append(result)
            return result
        server=create({'Image':media_id,'Env':['MTX_RTSPTRANSPORTS=tcp'],
                       'NetworkingConfig':{'EndpointsConfig':{name:{'Aliases':['rtsp-server']}}}})
        api('POST',f'/containers/{server}/start')
        ssh_files={}
        if args.ssh:
            from docker_ssh_smoke import SERVER, put_files
            with tempfile.TemporaryDirectory(prefix='sentinel-rtsp-ssh-') as tmp:
                folder=Path(tmp)
                for key in ('host','client'):
                    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(folder/key)],check=True)
                ssh_server=create({'Image':'sentinelzone-ai:ssh-test-fixture','Volumes':{'/fixture':{}},
                    'HostConfig':{'ReadonlyRootfs':True,'Tmpfs':{'/run':'rw,mode=0755','/tmp':'rw,mode=1777'}},
                    'NetworkingConfig':{'EndpointsConfig':{name:{'Aliases':['ssh-test']}}}})
                config='Port 2222\nHostKey /fixture/host_key\nAuthorizedKeysFile /fixture/client.pub\nStrictModes yes\nPermitRootLogin prohibit-password\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\nAllowTcpForwarding local\nPermitOpen rtsp-server:8554\nPidFile /tmp/sshd.pid\n'
                put_files(ssh_server,'/fixture',{'server.py':SERVER,'sshd_config':config,'host_key':(folder/'host').read_bytes(),'client.pub':(folder/'client.pub').read_bytes()},0)
                ssh_files={'client_key':(folder/'client').read_bytes(),'known_hosts':'[ssh-test]:2222 '+(folder/'host.pub').read_text()}
            api('POST',f'/containers/{ssh_server}/start')
            wait_marker(ssh_server,'SSH_SERVER_READY')
        publisher=create({'Image':media_id,'Entrypoint':['ffmpeg'],
             'Cmd':['-hide_banner','-loglevel','error','-re','-loop','1','-i','/fixtures/scene.jpg',
                    '-vf','scale=640:480','-r','10','-c:v','libx264','-preset','ultrafast','-tune','zerolatency',
                    '-pix_fmt','yuv420p','-g','10','-f','rtsp','-rtsp_transport','tcp','rtsp://rtsp-server:8554/fixture'],
             'Volumes':{'/fixtures':{}}})
        upload(publisher,'/fixtures',{'scene.jpg':repo/'dam_sample_0.jpg'},uid=0)
        api('POST',f'/containers/{publisher}/start')
        probe=create({'Image':args.image,'Cmd':['python','/opt/sentinelzone/data/rtsp_probe.py'],
             'Env':['SENTINEL_MODE=production','CAMERA_FIXTURE=rtsp://rtsp-server:8554/fixture',
                    'PYTHONPATH=/opt/sentinelzone','OMP_NUM_THREADS=2','RTSP_SETUP_VIA_PANEL='+str(int(args.setup)),'RTSP_EXPECT_DEVICE='+('cuda' if args.gpu else 'cpu')]+(['RTSP_SSH=ssh -p 2222 root@ssh-test','SENTINEL_SSH_KEY_FILE=/opt/sentinelzone/data/client_key','SENTINEL_SSH_KNOWN_HOSTS=/opt/sentinelzone/data/known_hosts'] if args.ssh else []),
             'Volumes':{'/opt/sentinelzone/data':{}},
             'HostConfig':{'ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],
                           'Tmpfs':{'/tmp':'rw,size=268435456,mode=1777'},
                           'DeviceRequests':[{'Driver':'nvidia','Count':-1,'Capabilities':[['gpu']]}] if args.gpu else []}})
        upload(probe,'/opt/sentinelzone/data',{'rtsp_probe.py':repo/'tests/integration/rtsp_probe.py',
                    'train_forecaster.py':repo/'scripts/train_forecaster.py','detector.pt':repo/'yolov8n.pt'})
        if args.ssh:
            put_files(probe,'/opt/sentinelzone/data',ssh_files,10001)
        api('POST',f'/containers/{probe}/start')
        wait_marker(probe,'RTSP_STREAMING_OK')
        api('POST',f'/containers/{publisher}/stop?t=5')
        wait_marker(probe,'RTSP_DISCONNECT_OK',30)
        api('POST',f'/containers/{publisher}/start')
        wait_marker(probe,'RTSP_RECONNECT_OK')
        result=json.loads(api('POST',f'/containers/{probe}/wait'))
        output=logs(probe)
        if result['StatusCode']!=0 or 'RTSP_LIFECYCLE_OK' not in output:
            raise RuntimeError(output[-5000:])
        for line in output.splitlines():
            if line.startswith('RTSP_DEVICE_OK'):
                print(line,flush=True)
        if args.ssh: print('SSH_RTSP_INTEGRATION_OK: actual camera decode, model output and reviews through SSH',flush=True)
        print('RTSP_INTEGRATION_OK: real H264/TCP decode, detector, API, disconnect, reconnect, shutdown',flush=True)
    except Exception:
        for container in containers:
            print(logs(container)[-1500:],flush=True)
        raise
    finally:
        for container in reversed(containers):
            api('DELETE',f'/containers/{container}?force=1&v=1')
        if network:
            api('DELETE',f'/networks/{network}')


if __name__=='__main__':
    main()
