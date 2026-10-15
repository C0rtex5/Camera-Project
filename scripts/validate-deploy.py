"""Validate the hub bind address and browser origin."""
import ipaddress
import json
import sys
from urllib.parse import urlsplit


def validate(config):
    service = config['services']['sentinel']
    binding = service['ports'][0]
    address = ipaddress.ip_address(binding['host_ip'])
    if (address.is_unspecified and str(address) != '0.0.0.0') or address.is_multicast or address.is_global:
        raise ValueError('Use 0.0.0.0, localhost or a private VPN interface address for SENTINEL_BIND_ADDRESS')
    configured_origin = service['environment'].get('SENTINEL_ORIGIN', '').strip()
    automatic = not configured_origin or configured_origin.lower() == 'auto'
    origin = urlsplit(configured_origin) if not automatic else None
    if origin is not None and (origin.scheme not in ('http', 'https') or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username is not None or origin.password is not None or origin.port == 0):
        raise ValueError('SENTINEL_ORIGIN must be auto or an exact http(s) origin without a path')
    if service['environment']['SENTINEL_DEVICE'] not in ('auto', 'cpu', 'gpu', 'cuda', 'rocm', 'openvino'):
        raise ValueError('SENTINEL_DEVICE must be auto, cpu, gpu, cuda, rocm, or openvino')
    if service['environment'].get('SENTINEL_ACCELERATOR', 'auto') not in ('auto', 'cpu', 'nvidia', 'cuda', 'rocm', 'openvino'):
        raise ValueError('SENTINEL_ACCELERATOR must be auto, cpu, nvidia, cuda, rocm, or openvino')
    index = service['environment'].get('SENTINEL_GPU_INDEX', 'auto')
    if index != 'auto' and (not str(index).isdigit() or int(index) > 255):
        raise ValueError('SENTINEL_GPU_INDEX must be auto or an index from 0 to 255')
    if origin is not None and origin.hostname == 'localhost' and not (address.is_loopback or address.is_unspecified):
        raise ValueError('Set SENTINEL_ORIGIN to the VPN browser URL')


if __name__ == '__main__':
    try:
        validate(json.load(sys.stdin))
    except (ValueError, KeyError, IndexError) as error:
        print(f'Invalid deployment configuration: {error}', file=sys.stderr)
        sys.exit(2)
