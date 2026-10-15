"""Select images/devices using the Docker daemon host, then validate real inference."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROBE = ('import json; from src.hub.service import DeviceManager; '
         'd=DeviceManager("gpu"); d.initialize(); print(json.dumps(d.status())); '
         'assert d.status()["accelerated"], "GPU inference validation failed"')


def run(command, *, env=None, capture=False, timeout=None):
    return subprocess.run(command, cwd=ROOT, env=env, text=True, check=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, timeout=timeout)


def plans(hardware, architecture, nvidia_gpus, nvidia_access='gpus'):
    """Return compatible GPU stacks in priority order; CPU is always separate."""
    choices = []
    if nvidia_gpus and not hardware.get('tegra'):
        modern = architecture in ('arm64', 'aarch64') or any(g.get('compute_capability', 0) >= 10 for g in nvidia_gpus)
        profile = 'compose.nvidia-cdi.yaml' if nvidia_access == 'cdi' else 'compose.gpu.yaml'
        choices.append(('nvidia', [profile] + (['compose.spark.yaml'] if modern else [])))
    if architecture in ('amd64', 'x86_64'):
        if 'rocm' in hardware.get('vendors', []) and hardware.get('kfd_group') is not None:
            choices.append(('rocm', ['compose.amd.yaml']))
        if 'openvino' in hardware.get('vendors', []):
            choices.append(('openvino', ['compose.intel.yaml']))
    return choices


def device_override(hardware, backend):
    nodes = [node for node in hardware.get('render_nodes', []) if node['vendor'] == backend]
    if not nodes:
        raise ValueError('No accessible render device for ' + backend)
    devices = [node['path'] + ':' + node['path'] for node in nodes]
    groups = {str(node['group']) for node in nodes}
    if backend == 'rocm':
        if hardware.get('kfd_group') is None:
            raise ValueError('AMD compute device /dev/kfd is unavailable')
        devices.append('/dev/kfd:/dev/kfd')
        groups.add(str(hardware['kfd_group']))
    return {'services': {'sentinel': {'devices': devices, 'group_add': sorted(groups)}}}


def deploy(mode):
    env_file = os.getenv('SENTINEL_ENV_FILE', '.env.production')
    if not (ROOT / env_file).is_file():
        raise ValueError('Copy .env.production.example to ' + env_file + ' and configure the server first.')
    base = ['docker', 'compose', '--env-file', env_file, '-f', 'compose.yaml']
    config = json.loads(run(base + ['config', '--format', 'json'], capture=True).stdout)
    validated = subprocess.run([sys.executable, 'scripts/validate-deploy.py'], cwd=ROOT,
                               input=json.dumps(config), text=True)
    if validated.returncode:
        raise ValueError('Invalid deployment configuration')
    environment = config['services']['sentinel']['environment']
    requested = environment.get('SENTINEL_ACCELERATOR', 'auto')
    requested = {'cuda': 'nvidia'}.get(requested, requested)
    setting = environment.get('SENTINEL_DEVICE', 'auto')
    if mode == 'auto' and (setting == 'cpu' or requested == 'cpu'):
        mode = 'cpu'
    if requested == 'auto' and setting in ('cuda', 'rocm', 'openvino'):
        requested = {'cuda': 'nvidia'}.get(setting, setting)
    # Always prepare an independent CPU image, including on non-NVIDIA ARM64 hosts.
    run(base + ['build', 'sentinel'])
    image = config['services']['sentinel']['image']
    failures = []
    if mode != 'cpu':
        architecture = run(['docker', 'info', '--format', '{{.Architecture}}'], capture=True, timeout=15).stdout.strip()
        hardware = {'vendors': [], 'render_nodes': []}
        try:
            result = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                          '--mount', 'type=bind,source=/sys,target=/host-sys,readonly',
                          '--mount', 'type=bind,source=/dev,target=/host-dev,readonly',
                          '--entrypoint', 'python', image, '-m', 'src.hub.hardware'], capture=True, timeout=30)
            hardware = json.loads(result.stdout)
        except (subprocess.SubprocessError, ValueError):
            failures.append('Docker-host hardware inventory failed')
        nvidia = []
        nvidia_access = 'gpus'
        if 'nvidia' in hardware['vendors'] or requested == 'nvidia':
            for access, flags in (('gpus', ['--gpus', 'all']), ('cdi', ['--device', 'nvidia.com/gpu=all'])):
                try:
                    result = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                                  *flags, '--entrypoint', 'nvidia-smi', image,
                                  '--query-gpu=name,compute_cap', '--format=csv,noheader,nounits'], capture=True, timeout=30)
                    detected = []
                    for line in result.stdout.splitlines():
                        name, capability = line.rsplit(',', 1)
                        detected.append({'name': name.strip(), 'compute_capability': float(capability.strip())})
                    if detected:
                        nvidia, nvidia_access = detected, access
                        break
                except (subprocess.SubprocessError, ValueError):
                    continue
            if not nvidia:
                failures.append('NVIDIA runtime probe failed; check host driver and Container Toolkit')
        choices = plans(hardware, architecture, nvidia, nvidia_access)
        if requested not in ('auto', 'cpu'):
            choices = [choice for choice in choices if choice[0] == requested]
        if not choices and hardware['vendors'] and not failures:
            failures.append('Detected GPU has no compatible image/device access on this architecture')
        print('Docker host: ' + architecture + '; detected GPU families: ' + (', '.join(hardware['vendors']) or 'none'), flush=True)
        with tempfile.TemporaryDirectory(prefix='camera-project-devices-') as temporary:
            for backend, files in choices:
                gpu = list(base)
                for file in files:
                    gpu.extend(['-f', file])
                try:
                    if backend in ('rocm', 'openvino'):
                        override = Path(temporary) / (backend + '.json')
                        override.write_text(json.dumps(device_override(hardware, backend)))
                        gpu.extend(['-f', str(override)])
                    run(gpu + ['build', 'sentinel'])
                    # Probe has no persistent volume: it cannot read camera secrets.
                    effective = json.loads(run(gpu + ['config', '--format', 'json'], capture=True).stdout)
                    probe_service = effective['services']['sentinel']
                    probe_service['volumes'] = []
                    probe_service['ports'] = []
                    probe_service['environment']['SENTINEL_DATA_DIR'] = '/tmp/hub-probe'
                    probe_file = Path(temporary) / (backend + '-probe.json')
                    probe_file.write_text(json.dumps({'services': {'sentinel': probe_service}}))
                    probe_name = 'camera-project-gpu-probe-' + uuid.uuid4().hex[:8]
                    try:
                        run(['docker', 'compose', '-f', str(probe_file), 'run', '--rm', '--no-deps', '-T',
                             '--name', probe_name, 'sentinel', 'python', '-c', PROBE], timeout=120)
                    finally:
                        # A client timeout must not leave a GPU process behind.
                        subprocess.run(['docker', 'rm', '-f', probe_name],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    run(gpu + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '120'])
                    print('Camera hub started with validated ' + backend + ' GPU inference.', flush=True)
                    return
                except (subprocess.SubprocessError, ValueError):
                    failures.append(backend + ' image build, GPU inference or container startup failed')
                    print(failures[-1], file=sys.stderr, flush=True)
        if mode == 'gpu':
            raise ValueError('; '.join(failures) or 'No compatible GPU detected')
    cpu_env = dict(os.environ, SENTINEL_DEVICE='cpu', SENTINEL_ACCELERATOR='cpu',
                   SENTINEL_DEPLOYMENT_NOTICE='; '.join(failures))
    print('Starting CPU deployment.' + (' GPU diagnostics: ' + '; '.join(failures) if failures else ''), flush=True)
    run(base + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '120'], env=cpu_env)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=('auto', 'cpu', 'gpu'), default='auto')
    try:
        deploy(parser.parse_args().mode)
    except (ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
