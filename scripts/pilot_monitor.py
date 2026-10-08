"""Sample readiness, latency, and throughput for commissioning."""
import argparse
import getpass
import json
import time
import httpx


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://localhost:8000')
    p.add_argument('--username', help='Only needed when optional API authentication is enabled')
    p.add_argument('--hours', type=float, default=72)
    p.add_argument('--interval', type=float, default=30)
    p.add_argument('--output', default='pilot-results.jsonl')
    a = p.parse_args()
    if a.hours <= 0 or a.interval < 1:
        p.error('Hours must be positive; interval must be at least one second')
    password = getpass.getpass('Password: ') if a.username else None
    with httpx.Client(base_url=a.url, timeout=10, headers={'X-Sentinel-Request': 'hub'}) as client:
        def login():
            client.post('/api/v1/auth/login', json={'username': a.username, 'password': password}).raise_for_status()
        if a.username:
            login()
        end = time.monotonic() + a.hours * 3600
        with open(a.output, 'a', encoding='utf-8') as out:
            while time.monotonic() < end:
                try:
                    response = client.get('/health/ready')
                    if response.status_code == 401 and a.username:
                        login()
                        response = client.get('/health/ready')
                    record = {'sample_timestamp': time.time(), 'http_status': response.status_code, 'health': response.json()}
                except Exception as exc:
                    record = {'sample_timestamp': time.time(), 'error': type(exc).__name__}
                out.write(json.dumps(record) + '\n')
                out.flush()
                time.sleep(min(a.interval, max(0, end - time.monotonic())))


if __name__ == '__main__':
    main()
