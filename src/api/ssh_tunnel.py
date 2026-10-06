"""One loopback-only RTSP/TCP forward through an SSH test server."""
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
import tempfile
from urllib.parse import urlsplit, urlunsplit


def parse_connection(value):
    parts = shlex.split(value)
    if len(parts) == 4 and parts[:2] == ['ssh', '-p']:
        port, destination = parts[2:]
    elif len(parts) == 2 and parts[0] == 'ssh':
        port, destination = '22', parts[1]
    else:
        raise ValueError('Use ssh -p PORT username@server')
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid SSH port')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*', destination):
        raise ValueError('Use username@server without extra SSH options or commands')
    return destination, int(port)


def key_paths(key_file='', known_hosts=''):
    return (os.path.expanduser(key_file or os.getenv('SENTINEL_SSH_KEY_FILE', '')),
            os.path.expanduser(known_hosts or os.getenv('SENTINEL_SSH_KNOWN_HOSTS', '')))


def requirements(key_file='', known_hosts=''):
    missing = []
    if not shutil.which('ssh'):
        missing.append('SSH client installed on the application server')
    for value, label in zip(key_paths(key_file, known_hosts), ['Readable SSH private key file on the application server',
                       'Readable verified SSH known-hosts file']):
        if not value or not Path(value).is_file() or not os.access(value, os.R_OK):
            missing.append(label)
    return missing


class CameraTunnel:
    def __init__(self, connection, source, key_file='', known_hosts=''):
        destination, port = parse_connection(connection)
        remote = urlsplit(source)
        if remote.scheme != 'rtsp':
            raise ValueError('SSH camera forwarding currently requires RTSP over TCP')
        if requirements(key_file, known_hosts):
            raise ValueError('Complete the SSH key and verified host configuration')
        key_file, known_hosts = key_paths(key_file, known_hosts)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.port = reservation.getsockname()[1]
        target = remote.hostname
        if ':' in target:
            target = '[' + target + ']'
        forward = f'127.0.0.1:{self.port}:{target}:{remote.port or 554}'
        self.command = [shutil.which('ssh'), '-F', '/dev/null', '-N', '-T', '-p', str(port),
                        '-i', key_file,
                        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'GlobalKnownHostsFile=/dev/null',
                        '-o', 'UserKnownHostsFile=' + known_hosts,
                        '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=8',
                        '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
                        '-L', forward, destination]
        auth = remote.netloc.rsplit('@', 1)[0] + '@' if '@' in remote.netloc else ''
        self.source = urlunsplit((remote.scheme, auth + f'127.0.0.1:{self.port}', remote.path, remote.query, ''))
        self.stop = threading.Event()
        self.process = None
        self.thread = None
        self.error_file = None

    def launch(self):
        if self.error_file: self.error_file.close()
        self.error_file = tempfile.TemporaryFile()
        self.process = subprocess.Popen(self.command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.error_file)

    def start(self):
        self.launch()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                with socket.create_connection(('127.0.0.1', self.port), timeout=.2):
                    self.thread = threading.Thread(target=self.watch, daemon=True, name='camera-ssh-tunnel')
                    self.thread.start()
                    return
            except OSError:
                self.stop.wait(.1)
        self.error_file.seek(0)
        error = self.error_file.read(16384).lower()
        message = 'SSH tunnel could not connect. Check the server address, port and key.'
        for marker, detail in [(b'host key verification failed', 'SSH host verification failed. Configure the independently verified server host key.'),
                               (b'permission denied', 'SSH authentication failed. Check the SSH username and private key; the camera password is separate.'),
                               (b'connection refused', 'SSH server refused the connection. Check its port and SSH service.'),
                               (b'timed out', 'SSH connection timed out. Check routing, firewall and server availability.'),
                               (b'could not resolve hostname', 'SSH server hostname could not be resolved.'),
                               (b'bad permissions', 'SSH private key permissions are too open. Restrict access to its owner.')]:
            if marker in error: message = detail; break
        self.close()
        raise ValueError(message)

    def watch(self):
        while not self.stop.wait(2):
            if self.process.poll() is not None:
                try:
                    self.launch()
                except OSError:
                    pass

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if getattr(self, 'error_file', None): self.error_file.close()


def saved_service(draft):
    from src.api.live_service import LiveService
    tunnel = None
    try:
        source = draft.source()
        if draft.ssh_tunnel:
            tunnel = CameraTunnel(draft.ssh_tunnel, source, draft.ssh_key_file, draft.ssh_known_hosts)
            tunnel.start()
            source = tunnel.source
        service = LiveService(configs=[draft.camera()], sources={draft.camera_id: source})
        service.tunnel = tunnel
        return service
    except Exception:
        if tunnel:
            tunnel.close()
        raise
