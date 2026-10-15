import importlib.util
import json
import os
from pathlib import Path
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('deploy_validator', 'scripts/validate-deploy.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def config(address='0.0.0.0', origin='auto'):
    return {'services': {'sentinel': {'image': 'camera-project:cpu', 'ports': [{'host_ip': address}], 'environment': {
        'SENTINEL_ORIGIN': origin, 'SENTINEL_DEVICE': 'auto'}}}}


@pytest.mark.parametrize('address', ['::', '8.8.8.8'])
def test_no_public_binding(address):
    with pytest.raises(ValueError):
        validator.validate(config(address))


def test_all_ipv4_interfaces_binding():
    validator.validate(config())
    validator.validate(config('0.0.0.0', 'http://100.80.0.10:1221'))


def test_vpn_origin_required():
    with pytest.raises(ValueError):
        validator.validate(config('100.80.0.10', 'http://localhost:1221'))
    validator.validate(config('100.80.0.10', 'http://100.80.0.10:1221'))


@pytest.mark.parametrize('origin', ['', 'auto', ' AUTO '])
def test_automatic_origin_on_vpn(origin):
    validator.validate(config('100.80.0.10', origin))


def test_origin_not_configured():
    deployment = config()
    del deployment['services']['sentinel']['environment']['SENTINEL_ORIGIN']
    validator.validate(deployment)


@pytest.mark.parametrize('origin', ['*', 'http://localhost:invalid', 'http://localhost:0', 'http://user:pass@localhost', 'http://localhost/path'])
def test_invalid_origin_rejected(origin):
    with pytest.raises(ValueError):
        validator.validate(config(origin=origin))


@pytest.mark.parametrize('architecture', ['x86_64', 'aarch64'])
@pytest.mark.parametrize('failure', ['probe', 'startup', 'build', 'none', 'runtime'])
def test_launcher_hardware_fallback(tmp_path, failure, architecture):
    result, history = launch(tmp_path, failure, architecture)
    cpu_start = [call for call in history if 'up' in call['args'] and 'compose.gpu.yaml' not in call['args']]
    if failure != 'none':
        assert result.returncode == 0, result.stderr
        assert cpu_start and cpu_start[0]['device'] == 'cpu'
        assert cpu_start[0]['backend'] == 'cpu'
        assert 'Starting CPU' in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert not cpu_start
    gpu_calls = [c for c in history if 'compose.gpu.yaml' in c['args']]
    assert all(('compose.spark.yaml' in c['args']) == (architecture == 'aarch64') for c in gpu_calls)
    base_builds = [c for c in history if 'build' in c['args'] and 'compose.gpu.yaml' not in c['args']]
    assert base_builds and all('compose.spark.yaml' not in c['args'] for c in base_builds)
    for call in history:
        if call.get('probe'):
            assert call['probe']['volumes'] == []
            assert call['probe']['ports'] == []


@pytest.mark.parametrize('backend', ['rocm', 'openvino'])
def test_other_gpu_families(tmp_path, backend):
    result, history = launch(tmp_path, 'none', 'x86_64', backend)
    assert result.returncode == 0, result.stderr
    override = 'compose.amd.yaml' if backend == 'rocm' else 'compose.intel.yaml'
    assert any('up' in c['args'] and override in c['args'] for c in history)
    assert not any('--gpus' in c['args'] for c in history)
    probes = [c['probe'] for c in history if c.get('probe')]
    assert probes


def test_required_gpu_does_not_silently_use_cpu(tmp_path):
    result, history = launch(tmp_path, 'probe', 'x86_64', mode='gpu')
    assert result.returncode != 0
    assert not any('up' in c['args'] for c in history)


def test_blackwell_x86_uses_modern_image(tmp_path):
    result, history = launch(tmp_path, 'none', 'x86_64', capability='12.0')
    assert result.returncode == 0, result.stderr
    assert any('up' in c['args'] and 'compose.spark.yaml' in c['args'] for c in history)


def test_native_cdi_when_legacy_nvidia_hook_fails(tmp_path):
    result, history = launch(tmp_path, 'legacy', 'x86_64')
    assert result.returncode == 0, result.stderr
    assert any('nvidia.com/gpu=all' in c['args'] for c in history)
    assert any('up' in c['args'] and 'compose.nvidia-cdi.yaml' in c['args'] for c in history)


def launch(tmp_path, failure, architecture, backend='nvidia', mode='auto', capability='8.6'):
    calls = tmp_path / 'calls'
    docker = tmp_path / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import os, sys, json
from pathlib import Path
args=sys.argv[1:]
record={'args':args,'device':os.getenv('SENTINEL_DEVICE'),'backend':os.getenv('SENTINEL_ACCELERATOR')}
probe_file=next((Path(a) for a in args if a.endswith('-probe.json')),None)
if probe_file is not None and probe_file.exists(): record['probe']=json.loads(probe_file.read_text())['services']['sentinel']
with open(os.environ['TEST_CALLS'],'a') as f: f.write(json.dumps(record)+'\\n')
if 'info' in args: print(os.environ['TEST_ARCHITECTURE']); sys.exit(0)
if 'config' in args: print(os.environ['TEST_CONFIG']); sys.exit(0)
if 'src.hub.hardware' in args: print(os.environ['TEST_HARDWARE']); sys.exit(0)
failure=os.environ['TEST_FAILURE']
if '--entrypoint' in args and 'nvidia-smi' in args:
 if failure=='runtime': sys.exit(1)
 if failure=='legacy' and '--gpus' in args: sys.exit(1)
 print('NVIDIA test GPU, '+os.environ['TEST_CAPABILITY']); sys.exit(0)
gpu=any(a in args for a in ('compose.gpu.yaml','compose.amd.yaml','compose.intel.yaml'))
if failure=='probe' and probe_file is not None and 'run' in args: sys.exit(1)
if gpu and ((failure=='startup' and 'up' in args) or (failure=='build' and 'build' in args)): sys.exit(1)
''')
    docker.chmod(0o755)
    hardware = {'vendors': [backend], 'render_nodes': [{'path': '/dev/dri/renderD128', 'vendor': backend, 'group': 109}], 'kfd_group': 110, 'tegra': False}
    env = {**os.environ, 'PATH': str(tmp_path)+':'+os.environ['PATH'],
           'SENTINEL_ENV_FILE': '.env.production.example', 'TEST_CALLS': str(calls),
           'TEST_CONFIG': json.dumps(config()), 'TEST_ARCHITECTURE': architecture,
           'TEST_FAILURE': failure, 'TEST_HARDWARE': json.dumps(hardware), 'TEST_CAPABILITY': capability}
    result = subprocess.run(['bash', 'scripts/start-hub.sh', mode], env=env, capture_output=True, text=True)
    history = [json.loads(line) for line in calls.read_text().splitlines()]
    return result, history
