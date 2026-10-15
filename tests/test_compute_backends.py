import importlib.util
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from src.hub.compute import accelerators
from src.hub.hardware import inventory
from src.hub.service import DeviceManager
from src.perception.detector import ConstructionSafetyDetector
from src.perception.openvino_detector import OpenVINOSafetyDetector
from tests.hub_support import EmptyDetector

spec = importlib.util.spec_from_file_location('deploy_hub', 'scripts/deploy_hub.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


def gpu(device='cuda:0', backend='cuda', name='Test dedicated GPU'):
    return {'device': device, 'backend': backend, 'name': name, 'memory_bytes': 1000, 'dedicated': True}


@pytest.mark.parametrize('candidate', [gpu(), gpu(backend='rocm'), gpu('openvino:GPU.1', 'openvino')])
def test_real_inference_required_before_gpu_is_active(candidate, monkeypatch):
    monkeypatch.setenv('SENTINEL_ACCELERATOR', 'auto')
    calls = []
    manager = DeviceManager(discover=lambda: [candidate], factory=lambda device: calls.append(device) or EmptyDetector())
    manager.initialize()
    assert calls == [candidate['device']]
    assert manager.status()['accelerated']
    assert manager.status()['backend'] == candidate['backend']
    assert manager.status()['device_name'] == candidate['name']


@pytest.mark.parametrize('candidate', [gpu(backend='rocm'), gpu('openvino:GPU.0', 'openvino')])
def test_runtime_failure_drops_frame_and_falls_back_on_each_backend(candidate, monkeypatch):
    monkeypatch.setenv('SENTINEL_ACCELERATOR', 'auto')
    class LostGPU(EmptyDetector):
        calls = 0
        def detect(self, frame):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError('Device disconnected')
            return super().detect(frame)
    calls = []
    def factory(device):
        calls.append(device)
        return EmptyDetector() if device == 'cpu' else LostGPU()
    manager = DeviceManager(discover=lambda: [candidate], factory=factory)
    manager.initialize()
    generation = manager.generation
    with pytest.raises(RuntimeError):
        manager.detect(np.zeros((10, 10, 3), np.uint8))
    assert manager.device == 'cpu'
    assert not manager.status()['accelerated']
    assert manager.generation > generation
    manager.detect(np.zeros((10, 10, 3), np.uint8))
    assert calls == [candidate['device'], 'cpu']  # No automatic GPU retry loop.
    manager.retry_gpu()
    assert manager.status()['accelerated']


def test_try_second_gpu_when_first_fails(monkeypatch):
    monkeypatch.setenv('SENTINEL_ACCELERATOR', 'auto')
    def factory(device):
        if device == 'cuda:0':
            raise RuntimeError('Unsupported GPU')
        return EmptyDetector()
    manager = DeviceManager(factory=factory, discover=lambda: [gpu(), gpu('cuda:1')])
    manager.initialize()
    assert manager.device == 'cuda:1' and manager.status()['accelerated']
    assert len(manager.status()['gpu_failures']) == 1


def test_strict_gpu_failure_exposes_unavailable_monitoring(monkeypatch):
    monkeypatch.setenv('SENTINEL_ACCELERATOR', 'auto')
    detector = EmptyDetector()
    manager = DeviceManager('gpu', factory=lambda d: detector, discover=lambda: [gpu()])
    manager.initialize()
    detector.detect = lambda frame: (_ for _ in ()).throw(RuntimeError('Lost GPU'))
    with pytest.raises(RuntimeError):
        manager.detect(np.zeros((32, 32, 3), np.uint8))
    assert manager.model is None and not manager.status()['accelerated']
    assert manager.device == 'unavailable'


def test_configured_gpu_index_and_backend(monkeypatch):
    monkeypatch.setenv('SENTINEL_ACCELERATOR', 'openvino')
    monkeypatch.setenv('SENTINEL_GPU_INDEX', '1')
    manager = DeviceManager(factory=lambda d: EmptyDetector(), discover=lambda: [gpu(), gpu('openvino:GPU.1', 'openvino')])
    manager.initialize()
    assert manager.device == 'openvino:GPU.1'


def test_discovery_prefers_discrete_intel_gpu_and_labels_amd_correctly():
    torch = SimpleNamespace(version=SimpleNamespace(hip='6.4'), cuda=SimpleNamespace(
        is_available=lambda: True, device_count=lambda: 1,
        get_device_properties=lambda _: SimpleNamespace(name='AMD Radeon', total_memory=100)))
    core = SimpleNamespace(available_devices=['GPU.0', 'GPU.1'], get_property=lambda d, key:
                           ('INTEGRATED' if d == 'GPU.0' else 'DISCRETE') if key == 'DEVICE_TYPE' else 'Intel ' + d)
    with patch.dict('sys.modules', {'torch': torch, 'openvino': SimpleNamespace(Core=lambda: core)}):
        found = accelerators()
    assert found[0]['backend'] == 'rocm'
    assert found[1]['device'] == 'openvino:GPU.1'
    assert found[-1]['dedicated'] is False


def test_detector_does_not_change_cuda_visibility_when_switching_devices(monkeypatch):
    import torch
    from unittest.mock import Mock
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0,1')
    model = ConstructionSafetyDetector('/missing/weights.pt', device='cuda:1')
    model.model = Mock()
    model.model.predict.return_value = [SimpleNamespace(boxes=[])]
    model.detect(np.zeros((32, 32, 3), np.uint8))
    assert model.model.predict.call_args.kwargs['device'] == torch.device('cuda:1')
    import os
    assert os.environ['CUDA_VISIBLE_DEVICES'] == '0,1'


def test_discovery_skips_broken_gpu_without_hiding_the_other_cards():
    def properties(index):
        if index == 0:
            raise RuntimeError('Unresponsive device')
        return SimpleNamespace(name='Working GPU', total_memory=100, is_integrated=False)
    torch = SimpleNamespace(version=SimpleNamespace(hip=None), cuda=SimpleNamespace(
        is_available=lambda: True, device_count=lambda: 2, get_device_properties=properties))
    with patch.dict('sys.modules', {'torch': torch, 'openvino': SimpleNamespace(Core=lambda: SimpleNamespace(available_devices=[]))}):
        assert accelerators()[0]['device'] == 'cuda:1'


def test_host_inventory_filters_non_gpu_devices_and_collects_permissions(tmp_path):
    sysroot, devroot = tmp_path / 'sys', tmp_path / 'dev'
    for slot, vendor, kind in [('gpu', '0x1002', '0x030000'), ('nic', '0x8086', '0x020000')]:
        card = sysroot / 'bus/pci/devices' / slot
        card.mkdir(parents=True)
        (card / 'vendor').write_text(vendor)
        (card / 'class').write_text(kind)
    render = sysroot / 'class/drm/renderD129/device'
    render.mkdir(parents=True); (render / 'vendor').write_text('0x1002')
    (devroot / 'dri').mkdir(parents=True)
    (devroot / 'dri/renderD129').touch(); (devroot / 'kfd').touch()
    hardware = inventory(sysroot, devroot)
    assert hardware['vendors'] == ['rocm']
    access = deployment.device_override(hardware, 'rocm')['services']['sentinel']
    assert access['devices'] == ['/dev/dri/renderD129:/dev/dri/renderD129', '/dev/kfd:/dev/kfd']
    assert access['group_add'] == [str((devroot / 'kfd').stat().st_gid)]
    assert 'privileged' not in access


def test_arm_without_nvidia_is_not_assumed_to_be_spark():
    assert deployment.plans({'vendors': ['openvino']}, 'aarch64', []) == []
    assert deployment.plans({'vendors': ['nvidia'], 'tegra': True}, 'aarch64', [{'compute_capability': 8.7}]) == []


def test_openvino_boxes_respect_resize_class_mapping_and_empty_results():
    detector = object.__new__(OpenVINOSafetyDetector)
    detector.size = (640, 640)
    detector.conf_threshold, detector.iou_threshold = .35, .45
    detector.model = SimpleNamespace(names={0: 'truck', 1: 'person'})
    detector.output = 'output'
    values = np.zeros((1, 6, 2), np.float32)
    values[0, :4, 0] = [320, 320, 100, 100]
    values[0, 5, 0] = .9
    detector.compiled = lambda inputs: {'output': values}
    frame = np.zeros((320, 640, 3), np.uint8)
    boxes, ppe = detector.detect(frame)
    np.testing.assert_allclose(boxes[0, :4], [270, 110, 370, 210])
    assert boxes[0, 5] == 0 and ppe[0]['state'] == 'unknown'
    values[:] = 0
    boxes, ppe = detector.detect(frame)
    assert boxes.shape == (0, 6) and ppe == []
