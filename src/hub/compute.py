"""Discover usable accelerators inside the running container."""
def accelerators():
    devices = []
    try:
        import torch
        if torch.cuda.is_available():
            backend = 'rocm' if getattr(torch.version, 'hip', None) else 'cuda'
            for index in range(torch.cuda.device_count()):
                try:
                    props = torch.cuda.get_device_properties(index)
                except Exception:
                    continue
                devices.append({'device': f'cuda:{index}', 'backend': backend,
                                'name': props.name, 'memory_bytes': props.total_memory,
                                'dedicated': not getattr(props, 'is_integrated', False)})
    except Exception:
        pass
    try:
        import openvino as ov
        core = ov.Core()
        for device in core.available_devices:
            if device == 'GPU' or device.startswith('GPU.'):
                kind = str(core.get_property(device, 'DEVICE_TYPE')).upper()
                devices.append({'device': f'openvino:{device if device != "GPU" else "GPU.0"}', 'backend': 'openvino',
                                'name': str(core.get_property(device, 'FULL_DEVICE_NAME')),
                                'memory_bytes': 0, 'dedicated': 'DISCRETE' in kind})
    except (ImportError, RuntimeError):
        pass
    # Prefer a dedicated card to integrated graphics, then the largest memory.
    return sorted(devices, key=lambda d: (not d['dedicated'], -d['memory_bytes'], d['device']))
