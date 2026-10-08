"""Run inside the built image with --network none and a read-only filesystem."""
import asyncio
import os
import numpy as np
import httpx
from src.hub.api import create_app


async def main():
    app = create_app(authentication_required=True)
    async with app.router.lifespan_context(app):
        await asyncio.to_thread(app.state.hub.device_thread.join, 60)
        device = app.state.hub.device
        assert device.device == 'cpu', device.status()
        detections, _ = await asyncio.to_thread(device.detect, np.zeros((320, 320, 3), np.uint8))
        assert detections.shape == (0, 6)
        app.state.store.add_user('smoke', 'offline-smoke-password', 'administrator')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:8000', headers={'X-Sentinel-Request': 'hub'}) as client:
            assert (await client.get('/api/v1/cameras')).status_code == 401
            assert (await client.get('/health/live')).status_code == 200
            response = await client.post('/api/v1/auth/login', json={'username': 'smoke', 'password': 'offline-smoke-password'})
            assert response.status_code == 200, response.text
            assert (await client.get('/api/v1/cameras')).status_code == 200
            assert (await client.get('/assets/three.r128.min.js')).status_code == 200
            assert (await client.get('/health/ready')).status_code == 503  # No cameras commissioned.
            response = await client.post('/api/v1/incidents/publish', json={'event_id': 'offline-smoke'})
            assert response.status_code == 200, response.text
            assert (await client.post('/api/v1/incidents/offline-smoke/review', json={'verdict': 'UNCERTAIN'})).status_code == 200
            os.environ['SENTINEL_ENABLE_DEMO'] = 'true'
            response = await client.get('/api/v1/demo/scenarios')
            assert response.status_code == 200, response.text
        print('Offline CPU container smoke passed: real model, authentication, persistence, assets, and demo routes.')


if __name__ == '__main__':
    asyncio.run(main())
