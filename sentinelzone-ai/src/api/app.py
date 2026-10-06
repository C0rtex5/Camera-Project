from datetime import date
import os
import asyncio
import threading
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import Request, Depends, FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src.schemas.contracts import ShiftSafetyManifest, IncidentTriageVerdict
from src.api.demo_service import DemoService
from src.api.media import video_dir

logger = logging.getLogger("SentinelAPI")

production = os.getenv("SENTINEL_MODE", "demo") == "production"
live_service = None
setup_error = None
setup_activation_lock = threading.Lock()

@asynccontextmanager
async def lifespan(app):
    global live_service
    if production:
        from src.api.live_service import LiveService
        from src.api.camera_setup import SetupStore
        from src.api.ssh_tunnel import saved_service
        global setup_error
        try:
            store = SetupStore()
            if store.path.exists():
                if not store.public()['missing']:
                    draft = store.load()
                    live_service = saved_service(draft)
            elif os.environ.get('SENTINEL_CAMERA_CONFIG'):
                live_service = LiveService(os.environ['SENTINEL_CAMERA_CONFIG'])
            if live_service is not None:
                live_service.start()
        except FileNotFoundError:
            if getattr(live_service, 'tunnel', None): live_service.tunnel.close()
            live_service = None
            setup_error = None
        except Exception:
            if getattr(live_service, 'tunnel', None): live_service.tunnel.close()
            live_service = None
            setup_error = 'Camera setup requires attention. Review the saved details and model files.'
    try:
        yield
    finally:
        if live_service is not None:
            live_service.close()
            live_service = None

app = FastAPI(lifespan=lifespan, title="SentinelZone-AI Incident Broker & Interactive Demo Gateway", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if production else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Demo Service on GPU
class LazyDemo:
    def __getattr__(self, name):
        return getattr(DemoService.get_instance(), name)

demo_service = None if production else LazyDemo()
if production:
    @app.middleware("http")
    async def isolate_demo(request, call_next):
        if request.url.path.startswith(("/api/v1/demo", "/media")):
            return JSONResponse({"detail": "Demo playback is disabled in production"}, status_code=404)
        return await call_next(request)

# Ensure directories exist (original media roots are preferred when populated)
media_dir = video_dir("test_videos")
if not media_dir.is_dir():
    media_dir = Path("data/test_videos").resolve()
    media_dir.mkdir(parents=True, exist_ok=True)
dashboard_dir = Path(__file__).resolve().parents[1] / "dashboard"

# Mount media video files
app.mount("/assets", StaticFiles(directory=str(dashboard_dir / "assets")), name="assets")
app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


@app.get("/")
async def serve_dashboard():
    index_file = dashboard_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard index.html not found")
    return FileResponse(str(index_file))


@app.get("/health")
async def health_check():
    cameras = live_service.status() if live_service else []
    devices = {camera.get("device", "unknown") for camera in cameras}
    device = next(iter(devices)) if len(devices) == 1 else ("mixed" if devices else "unavailable")
    return {
        "status": "HEALTHY",
        "monitoring_ready": bool(cameras) and all(c["state"] == "STREAMING" for c in cameras),
        "system": "SentinelZone-AI",
        "version": "4.0.0",
        "mode": "production" if production else "demo",
        "device": device if production else getattr(DemoService._instance, "device", "unavailable"),
        "active_connections": len(manager.active_connections)
    }


# ==================== Demo API Endpoints ====================

@app.get("/api/v1/demo/scenarios")
def get_demo_scenarios():
    """
    Returns available unseen test scenarios from the real dataset.
    """
    return demo_service.get_scenarios()


@app.get("/api/v1/demo/telemetry/{scenario_id}")
def get_scenario_telemetry(scenario_id: str):
    """
    Returns complete sequential telemetry for the given unseen scenario.
    """
    try:
        return demo_service.get_scenario_telemetry(scenario_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/demo/frame/{scenario_id}/{frame_idx}")
def get_demo_frame(scenario_id: str, frame_idx: int):
    """
    Returns GPU inference telemetry for a specific frame index.
    """
    try:
        return demo_service.process_frame_by_index(scenario_id, frame_idx)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/demo/frame_image/{scenario_id}/{frame_idx}")
def get_demo_frame_image(scenario_id: str, frame_idx: int):
    """
    Returns the real raw video/dataset frame as a JPEG image.
    Guarantees cross-browser rendering with zero video codec failure.
    """
    try:
        img_bytes = demo_service.get_frame_image_bytes(scenario_id, frame_idx)
        return Response(content=img_bytes, media_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.websocket("/ws/demo_stream/{scenario_id}")
async def websocket_demo_stream(websocket: WebSocket, scenario_id: str):
    """
    Live streaming telemetry WebSocket for real-time synchronized playback.
    """
    if production:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        telemetry = demo_service.get_scenario_telemetry(scenario_id)
        for frame_data in telemetry:
            await websocket.send_json(frame_data)
            await asyncio.sleep(0.05)  # 20 FPS streaming rate
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.close(code=1011, reason=str(e))


# ==================== Incident & Adjudication Endpoints ====================

@app.websocket("/ws/incidents")
async def websocket_incident_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/v1/incidents/publish")
async def publish_edge_incident(incident_packet: dict):
    await manager.broadcast(incident_packet)
    return {"status": "BROADCAST_SUCCESS", "received_event": incident_packet.get("event_id")}


@app.post("/api/v1/adjudication/submit")
async def submit_human_adjudication(verdict: IncidentTriageVerdict):
    import uuid
    target = Path("data/adjudications")
    target.mkdir(parents=True, exist_ok=True)
    temp = target / f".{uuid.uuid4().hex}.tmp"
    temp.write_text(verdict.model_dump_json(indent=2))
    temp.replace(target / f"{verdict.event_id}.json")
    return {
        "status": "ADJUDICATION_STORED",
        "event_id": verdict.event_id,
        "recorded_verdict": verdict.verdict,
        "retraining_priority": verdict.retraining_priority
    }


# ==================== Autonomous Agentic Endpoints ====================

class AgentChatRequest(BaseModel):
    message: str
    site_id: Optional[str] = "SITE-01"
    history: Optional[List[Dict[str, str]]] = None


class CompileManifestRequest(BaseModel):
    permit_text: str
    site_id: Optional[str] = "SITE-01"
    shift_date: Optional[str] = Field(default_factory=lambda: date.today().isoformat())
    ifc_file_path: Optional[str] = "models/site_pier_b4.ifc"


class AdjudicateRequest(BaseModel):
    event_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    video_s3_uri: Optional[str] = "s3://sentinel-incidents/scenario_worker_in_excavator_blind_spot.mp4"
    telemetry_json: Optional[Dict[str, Any]] = None
    shift_context: Optional[str] = None


@app.post("/api/v1/agent/supervisor/chat")
def supervisor_agent_chat(req: AgentChatRequest):
    """
    Direct multi-turn interaction with SentinelZone-AI's Autonomous Site Safety Supervisor ReAct agent.
    Autonomously invokes real domain tools (telemetry, permit compilation, adjudication, toolbox briefing, etc.).
    """
    try:
        from src.graph.supervisor_agent import build_supervisor_agent
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        
        messages = []
        if req.history:
            for item in req.history:
                role = item.get("role")
                content = item.get("content", "")
                if role == "user":
                    messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    messages.append(AIMessage(content=content))
        messages.append(HumanMessage(content=req.message))

        agent = build_supervisor_agent()
        result = agent.invoke({
            "messages": messages,
            "site_id": req.site_id or "SITE-01",
            "current_step": 0
        })

        out_messages = result.get("messages", [])
        final_text = ""
        tool_traces = []

        for m in out_messages:
            if isinstance(m, ToolMessage):
                tool_traces.append({
                    "tool": m.name,
                    "output": m.content
                })
            elif isinstance(m, AIMessage):
                if m.content:
                    final_text = m.content

        return {
            "response": final_text,
            "tool_executions": tool_traces,
            "site_id": req.site_id,
            "total_steps": result.get("current_step", 1)
        }
    except Exception as e:
        logger.error(f"Error in supervisor_agent_chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/agent/pipeline1/compile_manifest")
def pipeline1_compile_manifest(req: CompileManifestRequest):
    """
    Executes Pipeline 1: Natural language PTW extraction -> BIM spatial envelope resolution
    -> ShiftSafetyManifest Pydantic v2 validation -> Edge collision engine injection.
    """
    try:
        from src.graph.context_pipeline import build_context_pipeline
        pipe = build_context_pipeline()
        res = pipe.invoke({
            "site_id": req.site_id or "SITE-01",
            "shift_date": req.shift_date or date.today().isoformat(),
            "raw_permit_text": req.permit_text,
            "ifc_file_path": req.ifc_file_path or "models/site_pier_b4.ifc",
            "extracted_tasks": [],
            "resolved_envelopes": [],
            "validated_manifest": None,
            "validation_errors": [],
            "dispatch_status": ""
        })

        manifest = res.get("validated_manifest")
        return {
            "shift_id": manifest.shift_id if manifest else None,
            "site_id": req.site_id,
            "tasks_count": len(res.get("extracted_tasks", [])),
            "envelopes_count": len(res.get("resolved_envelopes", [])),
            "dispatch_status": res.get("dispatch_status"),
            "manifest": manifest.model_dump() if manifest else None,
            "validation_errors": res.get("validation_errors", [])
        }
    except Exception as e:
        logger.error(f"Error in pipeline1_compile_manifest: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/agent/pipeline2/adjudicate")
def pipeline2_adjudicate(req: AdjudicateRequest):
    """
    Executes Pipeline 2: Multimodal VLM Triage -> Active Learning Curation -> Forensic Archival.
    """
    try:
        import uuid
        if production and not req.telemetry_json:
            raise HTTPException(422, "Observed incident telemetry is required")
        from src.graph.adjudication_pipeline import build_adjudication_pipeline
        evt_id = req.event_id or f"INC-{uuid.uuid4().hex[:8].upper()}"
        pipe = build_adjudication_pipeline()
        res = pipe.invoke({
            "event_id": evt_id,
            "video_s3_uri": req.video_s3_uri or f"s3://sentinel-incidents/{evt_id}.mp4",
            "telemetry_json": req.telemetry_json or {"min_ttc": 1.4, "p_col": 0.88},
            "shift_context": req.shift_context or "Active excavation work zone",
            "final_verdict": None,
            "active_learning_curated": False,
            "archive_path": None
        })

        v = res.get("final_verdict")
        return {
            "event_id": evt_id,
            "verdict": v.model_dump() if v else None,
            "active_learning_curated": res.get("active_learning_curated", False),
            "archive_path": res.get("archive_path")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in pipeline2_adjudicate: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/agent/toolbox_talk/{site_id}")
def get_toolbox_talk(site_id: str, focus_hazard: Optional[str] = None):
    """
    Synthesizes an OSHA-compliant daily safety toolbox briefing (29 CFR 1926).
    """
    try:
        from src.graph.supervisor_agent import generate_daily_toolbox_talk
        raw = generate_daily_toolbox_talk.invoke({"site_id": site_id, "focus_hazard": focus_hazard})
        import json
        return json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/agent/active_learning/queue")
def get_curated_active_learning_queue():
    """
    Returns statistics and curated edge-case dataset samples ready for retraining.
    """
    try:
        from src.graph.supervisor_agent import get_active_learning_queue
        raw = get_active_learning_queue.invoke({})
        import json
        return json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ready")
def readiness():
    cameras = live_service.status() if live_service else []
    ready = bool(cameras) and all(c["state"] == "STREAMING" for c in cameras)
    return JSONResponse({"ready": ready, "cameras": cameras}, status_code=200 if ready else 503)


@app.get("/api/v1/cameras")
def cameras_status():
    return live_service.status() if live_service else []


@app.get("/api/v1/cameras/{camera_id}/frame")
def camera_frame(camera_id: str):
    worker = live_service.workers.get(camera_id) if live_service else None
    if worker is None:
        raise HTTPException(404, "Unknown camera")
    with worker.lock:
        if worker.snapshot()["state"] != "STREAMING" or worker.jpeg is None:
            raise HTTPException(503, "Camera frame unavailable")
        return Response(worker.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})




@app.middleware('http')
async def private_setup_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(('/api/v1/setup/', '/api/v1/hub/')):
        response.headers['Cache-Control'] = 'no-store'
    return response


# Setup stays available before a camera, calibration, or forecast is supplied.
@app.get('/setup')
def camera_setup_page():
    return FileResponse(str(dashboard_dir / 'setup.html'))


@app.get('/api/v1/setup/camera')
def read_camera_setup():
    from src.api.camera_setup import SetupStore
    try:
        result = SetupStore().public()
    except Exception:
        raise HTTPException(503, 'Saved setup could not be read; check the private setup file')
    return {**result, 'monitoring_active': live_service is not None, 'setup_error': setup_error}


@app.put('/api/v1/setup/camera')
def save_camera_setup(payload: Dict[str, Any]):
    global setup_error
    from src.api.camera_setup import SetupStore
    try:
        SetupStore().save(payload)
        setup_error = None
    except (ValueError, TypeError) as error:
        raise HTTPException(400, setup_validation_message(error))
    except OSError:
        raise HTTPException(503, 'Unable to save setup in the private data volume')
    return read_camera_setup()


def setup_validation_message(error):
    from pydantic import ValidationError
    labels = {'camera_id': 'Camera name: use letters, numbers, spaces, hyphens or underscores (up to 64 characters).',
              'site_id': 'Site name: use letters, numbers, spaces, hyphens or underscores (up to 64 characters).',
              'location': 'Physical location is required for the camera inventory and must be descriptive.',
              'host': 'Camera host must be an IP address or hostname without scheme, path, or credentials.',
              'rtsp_port': 'RTSP port must be a number between 1 and 65535.',
              'stream_path': 'Stream path cannot contain a query string, fragment, or credentials.',
              'access_path': 'Access path is a short label such as direct, vpn, or ssh-tunnel.',
              'ssh_tunnel': 'SSH connection: use ssh -p PORT username@server, without additional commands.',
              'rtsp_url': 'RTSP address: use rtsp://host:port/stream with credentials in separate fields.',
              'width': 'Stream width must be a positive integer up to 16384.',
              'height': 'Stream height must be a positive integer up to 16384.',
              'inference_fps': 'Analysis rate must be greater than zero and at most 30.',
              'stale_after_seconds': 'Freshness limit must be greater than zero and at most 30.',
              'homography': 'Measured calibration must be a JSON object or left blank.',
              'ssh_key_file': 'SSH private-key path is invalid.',
              'ssh_known_hosts': 'SSH known-hosts path is invalid.'}
    if isinstance(error, ValidationError):
        # Never return Pydantic input/context: they can contain camera passwords.
        return ' '.join(dict.fromkeys(labels.get(str(item['loc'][0]) if item['loc'] else '',
            'Check the connection fields. SSH forwarding requires rtsp:// over TCP.') for item in error.errors()))
    return 'Check the setup field types and connection format.'


connection_check_lock = threading.Lock()


@app.post('/api/v1/setup/camera/check')
def check_camera_connection(payload: Dict[str, Any]):
    from src.api.camera_setup import SetupDraft, SetupStore
    from src.api.connection_check import check_connection
    try:
        if 'password' not in payload:
            payload = {**payload, 'password': SetupStore().load().password.get_secret_value()}
        draft = SetupDraft.model_validate(payload)
    except (ValueError, TypeError) as error:
        raise HTTPException(400, setup_validation_message(error))
    missing = draft.connection_missing()
    if missing:
        raise HTTPException(409, 'Connection needs: ' + '; '.join(missing))
    if not connection_check_lock.acquire(blocking=False):
        raise HTTPException(409, 'A connection check is already running. Please wait.')
    try:
        return check_connection(draft)
    finally:
        connection_check_lock.release()


def _survey_draft(payload: Dict[str, Any]):
    from src.api.camera_setup import SetupDraft, SetupStore
    candidate = dict(payload)
    # A survey must never silently reuse credentials for a different candidate.
    if 'password' not in candidate:
        saved = SetupStore().load()
        if (candidate.get('rtsp_url') == saved.rtsp_url and
                candidate.get('camera_id', '') in ('', saved.camera_id)):
            candidate['password'] = saved.password.get_secret_value()
    return SetupDraft.model_validate(candidate)


@app.get('/api/v1/setup/survey')
def read_camera_survey():
    from src.api.camera_survey import SurveyStore
    try:
        return SurveyStore().public()
    except (OSError, ValueError):
        raise HTTPException(503, 'Camera survey records could not be read from the private data volume')


@app.post('/api/v1/setup/survey')
def survey_camera(payload: Dict[str, Any]):
    from src.api.camera_survey import SurveyStore, QUALITY_THRESHOLDS
    from src.api.connection_check import check_connection
    try:
        draft = _survey_draft(payload)
    except (ValueError, TypeError) as error:
        raise HTTPException(400, setup_validation_message(error))
    missing = draft.connection_missing()
    if missing:
        raise HTTPException(409, 'Survey needs: ' + '; '.join(missing))
    if not connection_check_lock.acquire(blocking=False):
        raise HTTPException(409, 'A camera connection check is already running. Please wait.')
    try:
        result = check_connection(draft)
    finally:
        connection_check_lock.release()
    try:
        record = SurveyStore().add(draft=draft, result=result)
    except (OSError, ValueError):
        raise HTTPException(503, 'Camera survey result could not be written to the private data volume')
    return {'record': record, 'quality_thresholds': QUALITY_THRESHOLDS}


@app.post('/api/v1/setup/survey/batch')
def survey_camera_batch(payload: Dict[str, Any]):
    from src.api.camera_survey import SurveyStore, QUALITY_THRESHOLDS
    from src.api.connection_check import check_connection
    candidates = payload.get('candidates')
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 20:
        raise HTTPException(400, 'Provide between 1 and 20 camera candidates')
    if not connection_check_lock.acquire(blocking=False):
        raise HTTPException(409, 'A camera connection check is already running. Please wait.')
    records, errors = [], []
    try:
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                errors.append({'index': index, 'message': 'Each candidate must be a JSON object'})
                continue
            try:
                draft = _survey_draft(candidate)
            except (ValueError, TypeError) as error:
                errors.append({'index': index, 'camera_id': str(candidate.get('camera_id', ''))[:64],
                               'message': setup_validation_message(error)})
                continue
            missing = draft.connection_missing()
            if missing:
                errors.append({'index': index, 'camera_id': draft.camera_id,
                               'message': 'Survey needs: ' + '; '.join(missing)})
                continue
            result = check_connection(draft)
            try:
                records.append(SurveyStore().add(draft=draft, result=result))
            except (OSError, ValueError):
                raise HTTPException(503, 'Camera survey results could not be written to the private data volume')
    finally:
        connection_check_lock.release()
    return {'records': records, 'errors': errors, 'quality_thresholds': QUALITY_THRESHOLDS}


@app.post('/api/v1/setup/survey/disposition')
def set_survey_disposition(payload: Dict[str, Any]):
    from src.api.camera_survey import SurveyStore
    record_id = payload.get('record_id')
    status = payload.get('status', 'reviewed')
    note = payload.get('note', '')
    if not isinstance(record_id, str) or not record_id:
        raise HTTPException(400, 'Choose a survey record to update')
    if not isinstance(note, str):
        raise HTTPException(400, 'Disposition note must be text')
    try:
        return {'record': SurveyStore().set_disposition(record_id, status, note)}
    except KeyError:
        raise HTTPException(404, 'Survey record not found')
    except ValueError as error:
        raise HTTPException(400, str(error))
    except OSError:
        raise HTTPException(503, 'Camera survey disposition could not be written to the private data volume')


@app.post('/api/v1/setup/survey/select')
def select_survey_camera(payload: Dict[str, Any]):
    from src.api.camera_survey import SurveyStore
    record_id = payload.get('record_id')
    if not isinstance(record_id, str) or not record_id:
        raise HTTPException(400, 'Choose a survey record to select')
    try:
        return {'selected': SurveyStore().select(record_id), 'survey': SurveyStore().public()}
    except KeyError:
        raise HTTPException(404, 'Survey record not found')
    except ValueError as error:
        raise HTTPException(409, str(error))
    except OSError:
        raise HTTPException(503, 'Camera survey records could not be written to the private data volume')


@app.get('/api/v1/setup/survey/report.md')
def camera_survey_report():
    from src.api.camera_survey import SurveyStore
    try:
        return Response(SurveyStore().report(), media_type='text/markdown',
                        headers={'Cache-Control': 'no-store'})
    except (OSError, ValueError):
        raise HTTPException(503, 'Camera survey report could not be generated')


@app.get('/api/v1/setup/survey/report.json')
def camera_survey_report_json():
    from src.api.camera_survey import SurveyStore
    try:
        return JSONResponse(SurveyStore().report_data(), headers={'Cache-Control': 'no-store'})
    except (OSError, ValueError):
        raise HTTPException(503, 'Camera survey JSON report could not be generated')


@app.post('/api/v1/setup/camera/start')
def start_saved_camera():
    global live_service, setup_error
    from src.api.camera_setup import SetupStore
    from src.api.ssh_tunnel import saved_service
    with setup_activation_lock:
        if not production:
            raise HTTPException(409, 'Saved setup is available; start the app in production mode to monitor it')
        if live_service is not None:
            raise HTTPException(409, 'Monitoring is already active. Restart the service to apply saved changes.')
        store = SetupStore()
        service = None
        try:
            if store.public()['missing']:
                raise HTTPException(409, 'Setup saved. Complete the listed requirements before starting monitoring.')
            draft = store.load()
            service = saved_service(draft)
            service.start()
            live_service = service
            setup_error = None
        except HTTPException:
            raise
        except Exception:
            if getattr(service, 'tunnel', None): service.tunnel.close()
            setup_error = 'Unable to start monitoring. Check SSH key/host access if configured, calibration, models, and camera settings.'
            raise HTTPException(503, setup_error)
        return {'status':'CONNECTING'}


from src.api.hub import router as hub_router
from src.api.media import router as media_router
app.include_router(hub_router(lambda: live_service))
app.include_router(media_router())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
