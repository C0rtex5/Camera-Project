"""Read-only Docker-host inventory. No dependency on a GPU utility on the client."""
import json
from pathlib import Path

VENDORS = {'0x10de': 'nvidia', '0x1002': 'rocm', '0x8086': 'openvino'}


def inventory(sys_root='/sys', dev_root='/dev'):
    root, dev = Path(sys_root), Path(dev_root)
    vendors, nodes = set(), []
    for card in sorted((root / 'bus/pci/devices').glob('*')):
        try:
            vendor = VENDORS.get((card / 'vendor').read_text().strip())
            device_class = (card / 'class').read_text().strip()
            if vendor and device_class.startswith(('0x03', '0x12')):
                vendors.add(vendor)
        except OSError:
            pass
    for render in sorted((root / 'class/drm').glob('renderD*')):
        try:
            vendor = VENDORS.get((render / 'device/vendor').read_text().strip())
            path = dev / 'dri' / render.name
            if vendor and path.exists():
                vendors.add(vendor)
                nodes.append({'path': '/dev/dri/' + render.name, 'vendor': vendor,
                              'group': path.stat().st_gid})
        except OSError:
            pass
    kfd = dev / 'kfd'
    tegra = (root / 'devices/soc0/family')
    return {'vendors': sorted(vendors), 'render_nodes': nodes,
            'kfd_group': kfd.stat().st_gid if kfd.exists() else None,
            'tegra': tegra.exists() and 'tegra' in tegra.read_text().lower()}


if __name__ == '__main__':
    print(json.dumps(inventory('/host-sys', '/host-dev')))
