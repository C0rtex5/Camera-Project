"""Exercise real SSH forwarding, recovery and host verification using disposable keys/containers.

Build test fixture from tests/integration/Dockerfile.ssh; no host ports or real credentials.
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import uuid
from docker_rtsp_smoke import api, logs, wait_marker

SERVER = '''import os,socket,subprocess,threading
os.makedirs('/run/sshd',exist_ok=True)
subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f','/fixture/sshd_config'])
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('127.0.0.1',8554));s.listen()
print('SSH_SERVER_READY',flush=True)
def echo(c):
 with c:
  data=c.recv(4096)
  if data:c.sendall(data)
while True:
 c,_=s.accept();threading.Thread(target=echo,args=(c,),daemon=True).start()
'''
PROBE = '''import os,socket,time
from src.api.ssh_tunnel import CameraTunnel
os.environ['SENTINEL_SSH_KEY_FILE']='/opt/sentinelzone/data/client_key'
os.environ['SENTINEL_SSH_KNOWN_HOSTS']='/opt/sentinelzone/data/wrong_hosts'
try:
 bad=CameraTunnel('ssh -p 2222 root@ssh-test','rtsp://127.0.0.1:8554/fixture')
 try:bad.start()
 except ValueError:print('SSH_HOST_REJECTED',flush=True)
 else:raise AssertionError('unverified host was accepted')
 finally:bad.close()
 os.environ['SENTINEL_SSH_KNOWN_HOSTS']='/opt/sentinelzone/data/known_hosts'
 tunnel=CameraTunnel('ssh -p 2222 root@ssh-test','rtsp://camera:private@127.0.0.1:8554/fixture')
 tunnel.start()
 def echo():
  with socket.create_connection(('127.0.0.1',tunnel.port),timeout=1) as c:
   c.settimeout(1);c.sendall(b'sentinel-test');assert c.recv(128)==b'sentinel-test'
 echo();print('SSH_FORWARD_OK',flush=True)
 deadline=time.monotonic()+40
 while time.monotonic()<deadline:
  try:echo()
  except (OSError,AssertionError):break
  time.sleep(.2)
 else:raise AssertionError('disconnect not observed')
 print('SSH_DISCONNECT_OK',flush=True)
 deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  try:echo();break
  except (OSError,AssertionError):time.sleep(.5)
 else:raise AssertionError('tunnel did not recover')
 print('SSH_RECOVERY_OK',flush=True)
 tunnel.close();assert tunnel.process.poll() is not None
 print('SSH_SHUTDOWN_OK',flush=True)
finally:
 if 'tunnel' in locals():tunnel.close()
'''


def put_files(container, destination, files, uid):
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as archive:
        for name,content in files.items():
            data=content if isinstance(content,bytes) else content.encode()
            item=tarfile.TarInfo(name);item.size=len(data);item.uid=item.gid=uid;item.mode=0o600
            archive.addfile(item,io.BytesIO(data))
    api('PUT',f'/containers/{container}/archive?path={destination}',binary=buffer.getvalue())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='sentinelzone-ai:remote-candidate')
    parser.add_argument('--fixture',default='sentinelzone-ai:ssh-test-fixture')
    args=parser.parse_args();containers=[];network=None
    name='sentinel-ssh-test-'+uuid.uuid4().hex[:10]
    try:
        network=json.loads(api('POST','/networks/create',{'Name':name,'Internal':True}))['Id']
        def create(config):
            config.setdefault('HostConfig',{})['NetworkMode']=name
            config['Labels']={'sentinelzone.test':name}
            container=json.loads(api('POST','/containers/create',config))['Id'];containers.append(container);return container
        with tempfile.TemporaryDirectory(prefix='sentinel-ssh-keys-') as tmp:
            folder=Path(tmp)
            for key in ('host','client','wrong'):
                subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(folder/key)],check=True)
            server=create({'Image':args.fixture,'Volumes':{'/fixture':{}},'HostConfig':{'ReadonlyRootfs':True,'Tmpfs':{'/run':'rw,mode=0755','/tmp':'rw,mode=1777'}},'NetworkingConfig':{'EndpointsConfig':{name:{'Aliases':['ssh-test']}}}})
            config='Port 2222\nHostKey /fixture/host_key\nAuthorizedKeysFile /fixture/client.pub\nStrictModes yes\nPermitRootLogin prohibit-password\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\nAllowTcpForwarding local\nPermitOpen 127.0.0.1:8554\nPidFile /tmp/sshd.pid\n'
            put_files(server,'/fixture',{'server.py':SERVER,'sshd_config':config,'host_key':(folder/'host').read_bytes(),'client.pub':(folder/'client.pub').read_bytes()},0)
            api('POST',f'/containers/{server}/start');wait_marker(server,'SSH_SERVER_READY')
            probe=create({'Image':args.image,'Cmd':['python','data/ssh_probe.py'],'Env':['PYTHONPATH=/opt/sentinelzone'],'Volumes':{'/opt/sentinelzone/data':{}},'HostConfig':{'ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],'Tmpfs':{'/tmp':'rw,mode=1777'}}})
            files={'ssh_probe.py':PROBE,'client_key':(folder/'client').read_bytes()}
            for filename,key in [('known_hosts','host'),('wrong_hosts','wrong')]:
                files[filename]='[ssh-test]:2222 '+(folder/(key+'.pub')).read_text()
            put_files(probe,'/opt/sentinelzone/data',files,10001)
            api('POST',f'/containers/{probe}/start');wait_marker(probe,'SSH_FORWARD_OK',40)
            api('POST',f'/containers/{server}/stop?t=2');wait_marker(probe,'SSH_DISCONNECT_OK',35)
            api('POST',f'/containers/{server}/start');wait_marker(probe,'SSH_RECOVERY_OK',50)
            result=json.loads(api('POST',f'/containers/{probe}/wait'))
            output=logs(probe)
            if result['StatusCode'] or 'SSH_SHUTDOWN_OK' not in output:raise RuntimeError(output)
            print(output,flush=True)
            print('SSH_INTEGRATION_OK: private key, strict host verification, loopback forwarding, reconnect, shutdown',flush=True)
    except Exception:
        for c in containers:print(logs(c)[-4000:],flush=True)
        raise
    finally:
        for c in reversed(containers):api('DELETE',f'/containers/{c}?force=1&v=1')
        if network:api('DELETE',f'/networks/{network}')


if __name__=='__main__':main()
