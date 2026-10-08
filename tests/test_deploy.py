import importlib.util
import json
import os
from pathlib import Path
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('deploy_validator', 'scripts/validate-deploy.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def config(address='127.0.0.1', origin='http://localhost:1221'):
    return {'services': {'sentinel': {'ports': [{'host_ip': address}], 'environment': {
        'SENTINEL_ORIGIN': origin, 'SENTINEL_DEVICE': 'auto'}}}}


@pytest.mark.parametrize('address', ['0.0.0.0', '::', '8.8.8.8'])
def test_no_public_binding(address):
    with pytest.raises(ValueError):
        validator.validate(config(address))


def test_vpn_origin_required():
    with pytest.raises(ValueError):
        validator.validate(config('100.80.0.10'))
    validator.validate(config('100.80.0.10', 'http://100.80.0.10:1221'))


@pytest.mark.parametrize('architecture', ['x86_64', 'aarch64'])
@pytest.mark.parametrize('failure', ['probe', 'startup', 'build', 'none'])
def test_launcher_hardware_fallback(tmp_path, failure, architecture):
    calls = tmp_path / 'calls'
    docker = tmp_path / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import os, sys, json
args=sys.argv[1:]
with open(os.environ['TEST_CALLS'],'a') as f:
 f.write(json.dumps({'args':args,'device':os.getenv('SENTINEL_DEVICE')})+'\\n')
if 'info' in args:
 print(os.environ['TEST_ARCHITECTURE']); sys.exit(0)
if 'config' in args:
 print(os.environ['TEST_CONFIG']); sys.exit(0)
gpu='compose.gpu.yaml' in args
failure=os.environ['TEST_FAILURE']
if gpu and ((failure=='probe' and 'run' in args) or (failure=='startup' and 'up' in args) or (failure=='build' and 'build' in args)):
 sys.exit(1)
''')
    docker.chmod(0o755)
    nvidia = tmp_path / 'nvidia-smi'
    nvidia.write_text('#!/bin/sh\nexit 0\n'); nvidia.chmod(0o755)
    env = {**os.environ, 'PATH':str(tmp_path)+':'+os.environ['PATH'],
           'SENTINEL_ENV_FILE': '.env.production.example', 'TEST_CALLS': str(calls),
           'TEST_CONFIG': json.dumps(config()), 'TEST_ARCHITECTURE': architecture, 'TEST_FAILURE':failure, 'SENTINEL_DEVICE':'cuda'}
    result = subprocess.run(['bash','scripts/start-hub.sh','auto'], env=env, capture_output=True, text=True)
    history = [json.loads(line) for line in calls.read_text().splitlines()]
    cpu_start = [call for call in history if 'up' in call['args'] and 'compose.gpu.yaml' not in call['args']]
    if failure in ('probe', 'startup'):
        assert result.returncode == 0, result.stderr
        assert cpu_start and cpu_start[0]['device']=='cpu'
        assert 'Starting CPU' in result.stderr
    elif failure=='build':
        assert result.returncode != 0
        assert not cpu_start
    else:
        assert result.returncode == 0
        assert not cpu_start

    compose_calls=[c for c in history if 'compose' in c['args']]
    assert all(('compose.spark.yaml' in c['args']) == (architecture == 'aarch64') for c in compose_calls)
