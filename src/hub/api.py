import asyncio
import collections
import importlib
import json
import os
import time
import threading
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from src.hub.models import Camera, CameraConnection, Login, Review, ManifestDraft, IncidentPublish, SupervisorRequest
from src.hub.service import Hub
from src.hub.store import Store

ROOT = Path(__file__).resolve().parents[2]


def create_app(store=None, hub=None, authentication_required=None):
    if authentication_required is None:
        authentication_required = os.getenv("SENTINEL_AUTH_REQUIRED", "false").lower() == "true"
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store or Store(os.getenv('SENTINEL_DATA_DIR', 'data/hub'))
        app.state.hub = hub or Hub(app.state.store)
        app.state.hub.start()
        try:
            yield
        finally:
            await run_in_threadpool(app.state.hub.stop)

    app = FastAPI(title='SentinelZone Industrial Camera Hub', version='5.0.0', lifespan=lifespan, docs_url=None, redoc_url=None)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation bodies can contain camera passwords, including nested model errors.
        errors = [{'loc': e['loc'], 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]
        return JSONResponse({'detail': errors}, status_code=422)

    # These are initialized by lifespan; explicit dependencies simplify isolated tests.
    if store is not None:
        app.state.store = store
    if hub is not None:
        app.state.hub = hub
    attempts = collections.OrderedDict()
    attempts_lock = threading.Lock()
    secure_cookie = os.getenv('SENTINEL_COOKIE_SECURE', 'false').lower() == 'true'

    def session_user(token):
        if not authentication_required:
            return {'name': 'local-panel', 'role': 'administrator'}
        return app.state.store.user(token)

    @app.middleware('http')
    async def security(request, call_next):
        path = request.url.path
        public = path in ('/', '/api/v1/auth/login', '/health/live') or path.startswith('/assets/')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('x-sentinel-request') != 'hub':
                return JSONResponse({'detail': 'Missing same-origin request header'}, status_code=403)
            origin = request.headers.get('origin')
            allowed = os.getenv('SENTINEL_ORIGIN', str(request.base_url).rstrip('/'))
            if origin and origin != allowed:
                return JSONResponse({'detail': 'Origin denied'}, status_code=403)
            try:
                if int(request.headers.get('content-length', '0')) > 1024 * 1024:
                    return JSONResponse({'detail': 'Request too large'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': 'Invalid content length'}, status_code=400)
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1024 * 1024:
                    return JSONResponse({'detail': 'Request too large'}, status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        if not public:
            identity = await run_in_threadpool(session_user, request.cookies.get('sentinel_session', ''))
            if not identity:
                return JSONResponse({'detail': 'Authentication required'}, status_code=401)
            request.state.user = identity
        try:
            response = await call_next(request)
        except (sqlite3.Error, OSError):
            app.state.hub.storage_error = 'Local storage unavailable; check disk and permissions'
            response = JSONResponse({'detail': app.state.hub.storage_error}, status_code=503)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'"
        if path.startswith('/api/') or path.startswith('/demo'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def require(request, roles):
        user = request.state.user
        if user['role'] not in roles:
            raise HTTPException(403, 'Role does not permit this action')
        return user['name']

    @app.get('/')
    def dashboard():
        return FileResponse(ROOT / 'src/dashboard/hub.html')

    app.mount('/assets', StaticFiles(directory=ROOT / 'src/dashboard/assets'), name='assets')

    @app.post('/api/v1/auth/login')
    def login(req: Login, request: Request):
        ip = request.client.host if request.client else 'local'
        now = time.time()
        with attempts_lock:
            recent = [t for t in attempts.get(ip, []) if now - t < 60]
            if len(recent) >= 10:
                raise HTTPException(429, 'Too many attempts; retry in a minute')
            attempts[ip] = [*recent, now]
            if len(attempts) > 512:
                attempts.popitem(last=False)
        token = app.state.store.login(req.username, req.password)
        if not token:
            raise HTTPException(401, 'Invalid credentials')
        response = JSONResponse({'status': 'authenticated'})
        response.set_cookie('sentinel_session', token, httponly=True, secure=secure_cookie, samesite='strict', max_age=28800)
        return response

    @app.post('/api/v1/auth/logout')
    def logout(request: Request):
        app.state.store.logout(request.cookies.get('sentinel_session', ''))
        response = JSONResponse({'status': 'signed_out'})
        response.delete_cookie('sentinel_session')
        return response

    @app.get('/api/v1/auth/me')
    def me(request: Request):
        return {**request.state.user, 'authentication_required': authentication_required}

    @app.get('/health/live')
    def live():
        return {'status': 'alive'}

    @app.get('/health')
    @app.get('/health/ready')
    def ready():
        current = app.state.hub
        cameras = current.statuses()
        healthy = bool(cameras) and current.device.model is not None and not current.storage_error and all(
            c['connection'] == 'online' and c['telemetry'] and not c['inference_error'] for c in cameras)
        return JSONResponse({'status': 'ready' if healthy else 'degraded', 'system': 'SentinelZone-AI',
                             'compute': current.device.status(), 'storage_error': current.storage_error,
                             'cameras': cameras}, status_code=200 if healthy else 503)

    @app.get('/api/v1/cameras')
    def cameras(request: Request):
        configs = [{**c, 'credentials_configured': bool(c.get('credential_secret') or app.state.store.camera_credentials(c['camera_id']))}
                   for c in app.state.store.cameras()]
        if request.state.user['role'] != 'administrator':
            configs = [{k: v for k, v in c.items() if k not in ('rtsp_url', 'credential_secret')} for c in configs]
        return {'cameras': configs, 'statuses': app.state.hub.statuses(), 'compute': app.state.hub.device.status(),
                'storage_error': app.state.hub.storage_error, 'demo_enabled': os.getenv('SENTINEL_ENABLE_DEMO', 'false').lower() == 'true'}

    @app.put('/api/v1/cameras/{camera_id}')
    def save_camera(camera_id: str, config: CameraConnection, request: Request):
        actor = require(request, ('administrator',))
        if config.camera_id != camera_id:
            raise HTTPException(400, 'Camera identity mismatch')
        try:
            public_config = Camera.model_validate(config.model_dump(exclude={'rtsp_username', 'rtsp_password', 'clear_credentials'}))
            if config.clear_credentials:
                public_config.credential_secret = None
            credentials = None if config.rtsp_username is None else {
                'username': config.rtsp_username.get_secret_value(), 'password': config.rtsp_password.get_secret_value()}
            app.state.store.save_camera(public_config.model_dump(), actor, credentials=credentials, clear_credentials=config.clear_credentials)
            app.state.hub.reload(public_config)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {'status': 'saved', 'camera_id': camera_id}

    @app.delete('/api/v1/cameras/{camera_id}')
    def delete_camera(camera_id: str, request: Request):
        actor = require(request, ('administrator',))
        app.state.hub.delete(camera_id)
        app.state.store.delete_camera(camera_id, actor)
        return {'status': 'deleted'}

    def worker_for(camera_id):
        worker = app.state.hub.workers.get(camera_id)
        if not worker:
            raise HTTPException(404, 'Active camera not found')
        return worker

    @app.get('/api/v1/cameras/{camera_id}/status')
    def status(camera_id: str):
        return worker_for(camera_id).status()

    @app.get('/api/v1/cameras/{camera_id}/frame')
    def frame(camera_id: str, frame_id: int | None = None):
        from fastapi.responses import Response
        worker = worker_for(camera_id)
        with worker.lock:
            if frame_id is not None:
                t = worker.telemetry
                if not t or t['frame_id'] != frame_id or not worker.processed_jpeg or time.time() - t['capture_timestamp'] > 2:
                    raise HTTPException(409, 'Processed frame replaced or stale; request latest preview')
                return Response(worker.processed_jpeg, media_type='image/jpeg', headers={
                    'X-Frame-ID': str(frame_id), 'X-Capture-Timestamp': str(t['capture_timestamp']), 'Cache-Control': 'no-store'})
            if not worker.jpeg or time.time() - worker.captured > 2:
                raise HTTPException(503, 'Fresh camera frame unavailable')
            return Response(worker.jpeg, media_type='image/jpeg', headers={
                'X-Frame-ID': str(worker.frame_id), 'X-Capture-Timestamp': str(worker.captured), 'Cache-Control': 'no-store'})

    @app.get('/api/v1/cameras/{camera_id}/stream')
    def stream(camera_id: str, request: Request):
        worker = worker_for(camera_id)
        token = request.cookies.get('sentinel_session', '')
        async def frames():
            previous = -1
            while not await request.is_disconnected() and not worker.stop_event.is_set():
                if not await run_in_threadpool(session_user, token):
                    return
                with worker.lock:
                    jpeg, fid, captured = worker.jpeg, worker.frame_id, worker.captured
                if jpeg and fid != previous and time.time() - captured <= 2:
                    previous = fid
                    yield (f'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\nX-Frame-ID: {fid}\r\nX-Capture-Timestamp: {captured}\r\n\r\n'.encode() + jpeg + b'\r\n')
                await asyncio.sleep(0.1)
        return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame')

    @app.websocket('/ws/cameras/{camera_id}')
    async def telemetry(ws: WebSocket, camera_id: str):
        token = ws.cookies.get('sentinel_session', '')
        origin = ws.headers.get('origin')
        allowed = os.getenv('SENTINEL_ORIGIN') or ('http' + ('s' if ws.url.scheme == 'wss' else '') + '://' + ws.headers.get('host', ''))
        if not session_user(token) or (origin and origin != allowed):
            await ws.close(code=1008)
            return
        worker = app.state.hub.workers.get(camera_id)
        if not worker:
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while not worker.stop_event.is_set():
                if not await run_in_threadpool(session_user, token):
                    await ws.close(code=1008)
                    return
                await asyncio.wait_for(ws.send_json(worker.status()), timeout=2)
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass

    @app.websocket('/ws/incidents')
    async def incident_events(ws: WebSocket):
        token = ws.cookies.get('sentinel_session', '')
        origin = ws.headers.get('origin')
        allowed = os.getenv('SENTINEL_ORIGIN') or ('http' + ('s' if ws.url.scheme == 'wss' else '') + '://' + ws.headers.get('host', ''))
        if not await run_in_threadpool(session_user, token) or (origin and origin != allowed):
            await ws.close(code=1008)
            return
        seen = {r['event_id'] for r in await run_in_threadpool(app.state.store.incidents)}
        await ws.accept()
        try:
            while True:
                if not await run_in_threadpool(session_user, token):
                    await ws.close(code=1008)
                    return
                records = await run_in_threadpool(app.state.store.incidents)
                for record in reversed(records):
                    if record['event_id'] not in seen:
                        await asyncio.wait_for(ws.send_json(record), timeout=2)
                seen = {r['event_id'] for r in records}
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass

    @app.websocket('/ws/demo_stream/{scenario_id}')
    async def demo_events(ws: WebSocket, scenario_id: str):
        token = ws.cookies.get('sentinel_session', '')
        origin = ws.headers.get('origin')
        allowed = os.getenv('SENTINEL_ORIGIN') or ('http' + ('s' if ws.url.scheme == 'wss' else '') + '://' + ws.headers.get('host', ''))
        if not await run_in_threadpool(session_user, token) or (origin and origin != allowed) or os.getenv('SENTINEL_ENABLE_DEMO', 'false').lower() != 'true':
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            from src.api.demo_service import DemoService
            frames = await run_in_threadpool(DemoService.get_instance().get_scenario_telemetry, scenario_id)
            for frame in frames:
                if not await run_in_threadpool(session_user, token):
                    await ws.close(code=1008)
                    return
                await asyncio.wait_for(ws.send_json(frame), timeout=2)
                await asyncio.sleep(0.05)
            await ws.close()
        except WebSocketDisconnect:
            pass
        except Exception:
            await ws.close(code=1011, reason='Recorded demo unavailable')

    @app.post('/api/v1/compute/retry')
    def retry(request: Request):
        actor = require(request, ('administrator',))
        app.state.hub.device.initialize()
        with app.state.store.transaction() as db:
            app.state.store.audit(db, actor, 'compute_reinitialized', app.state.hub.device.status())
        return app.state.hub.device.status()

    @app.get('/api/v1/incidents')
    def incidents():
        return app.state.store.incidents()

    @app.post('/api/v1/incidents/publish')
    def publish(body: IncidentPublish, request: Request):
        actor = require(request, ('administrator', 'operator'))
        try:
            record = app.state.hub.create_incident(body.model_dump(exclude_none=True), actor=actor)
        except ValueError:
            raise HTTPException(409, 'Incident already exists') from None
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None
        return {'status': 'INCIDENT_STORED', 'received_event': record['event_id']}

    @app.post('/api/v1/incidents/{event_id}/review')
    def review(event_id: str, body: Review, request: Request):
        actor = require(request, ('administrator', 'operator'))
        try:
            return app.state.store.update_incident(event_id, {'review': {**body.model_dump(), 'actor': actor, 'timestamp': time.time()}}, actor)
        except KeyError:
            raise HTTPException(404, 'Incident not found') from None

    @app.post('/api/v1/adjudication/submit')
    def legacy_review(body: dict, request: Request):
        actor = require(request, ('administrator', 'operator'))
        try:
            validated = Review.model_validate({'verdict': body.get('verdict'), 'notes': body.get('root_cause_summary', '')})
            app.state.store.update_incident(body.get('event_id'), {'review': {**validated.model_dump(), 'actor': actor, 'timestamp': time.time()}}, actor)
        except KeyError:
            raise HTTPException(404, 'Incident not found') from None
        except ValueError:
            raise HTTPException(422, 'Invalid verdict') from None
        return {'status': 'ADJUDICATION_STORED', 'event_id': body['event_id']}

    @app.get('/api/v1/incidents/{event_id}/evidence/{kind}')
    def evidence(event_id: str, kind: str):
        record = app.state.store.get_incident(event_id)
        name = record.get('evidence', {}).get(kind) if record else None
        if kind not in ('snapshot', 'clip') or not name:
            raise HTTPException(404, 'Evidence unavailable')
        root = (app.state.store.root / 'evidence').resolve()
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            raise HTTPException(404, 'Evidence expired or unavailable')
        return FileResponse(path, media_type='image/jpeg' if kind == 'snapshot' else 'video/x-msvideo', filename=name)

    @app.get('/api/v1/manifests')
    def manifests():
        return app.state.store.manifests()

    @app.post('/api/v1/manifests')
    def draft(body: ManifestDraft, request: Request):
        actor = require(request, ('administrator', 'operator'))
        config = next((c for c in app.state.store.cameras() if c['camera_id'] == body.camera_id), None)
        if not config:
            raise HTTPException(404, 'Camera not found')
        try:
            Camera.model_validate({**config, 'zones': [z.model_dump() for z in body.zones]})
        except ValueError:
            raise HTTPException(422, 'Zones require matching surveyed calibration') from None
        return app.state.store.draft(body.model_dump(), actor)

    @app.post('/api/v1/manifests/{manifest_id}/approve')
    def approve(manifest_id: str, request: Request):
        actor = require(request, ('administrator',))
        try:
            body = app.state.store.approve(manifest_id, actor)
            config = next(c for c in app.state.store.cameras() if c['camera_id'] == body['camera_id'])
            app.state.hub.reload(Camera.model_validate(config))
        except KeyError:
            raise HTTPException(404, 'Manifest not found') from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {**body, 'application_status': 'applied_to_camera_configuration'}

    @app.post('/api/v1/agent/supervisor/chat')
    def supervisor(body: SupervisorRequest, request: Request):
        actor = require(request, ('administrator', 'operator', 'viewer'))
        cameras = [c for c in app.state.hub.statuses() if not body.site_id or c['site_id'] == body.site_id]
        records = [r for r in app.state.store.incidents() if not body.site_id or r.get('site_id') == body.site_id]
        message = body.message.lower()
        tool, output = 'query_site_telemetry', {'cameras': cameras, 'incident_count': len(records)}
        text = f"Offline rules supervisor: {len(cameras)} active cameras and {len(records)} stored incidents. "
        if any(word in message for word in ('permit', 'zone', 'corridor')):
            require(request, ('administrator', 'operator'))
            tool = 'compile_work_permit'
            if not body.camera_id or body.zones is None:
                output = {'status': 'SURVEYED_ZONES_REQUIRED', 'applied': False}
                text += 'Select a camera and supply surveyed zones to create an approval draft.'
            else:
                config = next((c for c in app.state.store.cameras() if c['camera_id'] == body.camera_id), None)
                if not config:
                    raise HTTPException(404, 'Camera not found')
                try:
                    Camera.model_validate({**config, 'zones': [z.model_dump() for z in body.zones]})
                except ValueError:
                    raise HTTPException(422, 'Zones require matching surveyed calibration') from None
                output = app.state.store.draft({'camera_id': body.camera_id, 'zones': [z.model_dump() for z in body.zones],
                                               'permit_text': body.permit_text or body.message, 'reasoning_mode': 'rules'}, actor)
                text += 'Zone draft stored. Administrator approval is required before application.'
        elif any(word in message for word in ('adjudicat', 'incident review', 'triage')):
            tool = 'adjudicate_incident_packet'
            record = app.state.store.get_incident(body.event_id or '')
            output = {'event_id': body.event_id, 'review_status': 'HUMAN_REVIEW_REQUIRED',
                      'visual_analysis_performed': False, 'observed_telemetry': record}
            text += 'Review the stored incident evidence and submit a human verdict. No visual verdict was generated.'
        elif any(word in message for word in ('toolbox', 'briefing')):
            tool = 'generate_daily_toolbox_talk'
            output = {'site_id': body.site_id, 'requires_human_review': True,
                      'offline_cameras': [c['camera_id'] for c in cameras if c['connection'] != 'online'],
                      'unreviewed_incidents': len([r for r in records if not r.get('review')])}
            text += 'Shift briefing: confirm camera visibility and calibration, review unreviewed incidents, and verify approved exclusion zones.'
        elif any(word in message for word in ('learning', 'retrain', 'queue')):
            tool = 'get_active_learning_queue'
            output = {'reviewed_cases': [r for r in records if r.get('review', {}).get('verdict') in ('FALSE_POSITIVE', 'UNCERTAIN')]}
            text += f"{len(output['reviewed_cases'])} human-reviewed cases are available for learning export."
        else:
            unavailable = len([c for c in cameras if not c['telemetry']])
            text += f'{unavailable} cameras currently have no fresh AI telemetry. Ask for telemetry, a shift briefing, incident triage, or the learning queue.'
        return {'response': text, 'reasoning_mode': 'rules', 'visual_review': 'human_required', 'cameras': cameras,
                'incident_count': len(records), 'tool_executions': [{'tool': tool, 'output': json.dumps(output)}], 'total_steps': 1}

    @app.post('/api/v1/agent/pipeline1/compile_manifest')
    def permit(body: dict, request: Request):
        actor = require(request, ('administrator', 'operator'))
        camera_id = body.get('camera_id')
        if not any(c['camera_id'] == camera_id for c in app.state.store.cameras()):
            raise HTTPException(422, 'Select a camera; surveyed zones must be supplied for approval')
        zones = body.get('zones', [])
        try:
            validated = ManifestDraft.model_validate({'camera_id': camera_id, 'zones': zones, 'permit_text': body.get('permit_text', '')})
            config = next(c for c in app.state.store.cameras() if c['camera_id'] == camera_id)
            Camera.model_validate({**config, 'zones': [z.model_dump() for z in validated.zones]})
        except ValueError:
            raise HTTPException(422, 'Invalid manifest draft') from None
        return app.state.store.draft({**validated.model_dump(), 'reasoning_mode': 'rules', 'requires_human_spatial_review': True}, actor)

    @app.post('/api/v1/agent/pipeline2/adjudicate')
    def rule_triage(body: dict, request: Request):
        require(request, ('administrator', 'operator'))
        record = app.state.store.get_incident(body.get('event_id', ''))
        if not record:
            raise HTTPException(404, 'Select a stored incident')
        return {'event_id': record['event_id'], 'reasoning_mode': 'rules', 'visual_analysis_performed': False,
                'review_status': 'HUMAN_REVIEW_REQUIRED', 'observed_severity': record.get('severity'), 'telemetry': record}

    @app.get('/api/v1/agent/toolbox_talk/{site_id}')
    def toolbox(site_id: str):
        relevant = [c for c in app.state.hub.statuses() if c['site_id'] == site_id]
        return {'site_id': site_id, 'reasoning_mode': 'rules', 'requires_human_review': True,
                'briefing': 'Confirm camera visibility and calibration, review recorded incidents, and verify approved exclusion zones before the shift.',
                'cameras': relevant}

    @app.get('/api/v1/agent/active_learning/queue')
    def learning():
        return [r for r in app.state.store.incidents(10000) if r.get('review', {}).get('verdict') in ('FALSE_POSITIVE', 'UNCERTAIN')]

    @app.get('/api/v1/agent/active_learning/export')
    def export(request: Request):
        require(request, ('administrator', 'operator'))
        records = learning()
        return StreamingResponse(iter([json.dumps(r) + '\n' for r in records]), media_type='application/x-ndjson',
                                 headers={'Content-Disposition': 'attachment; filename=active-learning.jsonl'})

    class LazyDemo:
        def __init__(self):
            self.application = None

        async def __call__(self, scope, receive, send):
            if scope['type'] == 'websocket':
                # Legacy unauthenticated websockets are deliberately not exposed.
                await send({'type': 'websocket.close', 'code': 1008})
                return
            if os.getenv('SENTINEL_ENABLE_DEMO', 'false').lower() != 'true':
                await JSONResponse({'detail': 'Demo mode is disabled'}, status_code=503)(scope, receive, send)
                return
            if self.application is None:
                try:
                    self.application = importlib.import_module('src.api.demo_app').app
                except Exception:
                    await JSONResponse({'detail': 'Demo dependencies or model unavailable'}, status_code=503)(scope, receive, send)
                    return
            # Only recorded scenario reads are routed to legacy code, never agent writes.
            if scope['method'] not in ('GET', 'HEAD'):
                await JSONResponse({'detail': 'Use production hub workflows'}, status_code=403)(scope, receive, send)
                return
            scope = dict(scope)
            path = scope['path']
            if path in ('/demo', '/demo/'):
                scope['path'] = '/'
            elif not (path.startswith('/api/v1/demo/') or path.startswith('/media/')):
                await JSONResponse({'detail': 'Demo route not found'}, status_code=404)(scope, receive, send)
                return
            scope['root_path'] = ''
            await self.application(scope, receive, send)

    demo = LazyDemo()
    app.mount('/api/v1/demo', demo)
    # Mounted application expects original absolute endpoint paths.
    app.mount('/demo', demo)
    app.mount('/media', demo)
    return app


app = create_app()
