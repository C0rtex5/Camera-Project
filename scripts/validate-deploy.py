"""Reject accidental public binding of the password-free VPN panel."""
import ipaddress
import json
import sys
from urllib.parse import urlsplit


def validate(config):
    service = config['services']['sentinel']
    binding = service['ports'][0]
    address = ipaddress.ip_address(binding['host_ip'])
    if address.is_unspecified or address.is_multicast or address.is_global:
        raise ValueError('Use localhost or a private VPN interface address for SENTINEL_BIND_ADDRESS')
    origin = urlsplit(service['environment']['SENTINEL_ORIGIN'])
    if origin.scheme not in ('http', 'https') or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username:
        raise ValueError('SENTINEL_ORIGIN must be an exact http(s) origin without a path')
    if service['environment']['SENTINEL_DEVICE'] not in ('auto', 'cpu', 'cuda'):
        raise ValueError('SENTINEL_DEVICE must be auto, cpu, or cuda')
    if origin.hostname == 'localhost' and not address.is_loopback:
        raise ValueError('Set SENTINEL_ORIGIN to the VPN browser URL')


if __name__ == '__main__':
    try:
        validate(json.load(sys.stdin))
    except (ValueError, KeyError, IndexError) as error:
        print(f'Invalid deployment configuration: {error}', file=sys.stderr)
        sys.exit(2)
