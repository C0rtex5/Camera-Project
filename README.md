# SentinelZone-AI

An offline industrial camera hub for up to four fixed RTSP cameras, with live viewing, detection overlays, surveyed metric tracking, deterministic proximity forecasts, incident evidence, and human review.

The production hub replaces the former on-demand demo gateway. Camera capture runs independently of browser requests and AI processing. GPU inference is preferred when available; automatic CPU fallback covers startup and runtime failure. The dashboard reports camera freshness, device degradation, and measured inference throughput.

## Start

```bash
bash scripts/start-hub.sh auto
```

Open http://localhost:8000. The panel opens directly, without a username or password. Its layout follows the original interface: live video on the left, the spatial twin on the right, and telemetry below. Add cameras under Camera configuration and enter the camera username and password there. Credentials stay on the local server; when editing, leave both fields empty to keep the saved credentials. External secret files remain available as an advanced option.

See [deployment, calibration, maintenance, and commissioning instructions](docs/industrial-hub.md) for CPU/GPU requirements, TLS access, backup/restore, incident retention, and the 72-hour pilot.

## Capabilities

- Independent capture and tracking contexts, bounded latest-frame buffers, reconnect backoff, and stale-frame detection.
- Four-camera grid, focused ground-plane view, synchronized detection overlays, and locally bundled browser assets.
- Direct local panel access, audited configuration changes, and optional API session authentication via `SENTINEL_AUTH_REQUIRED=true`.
- SQLite persistence for cameras, incidents, reviews, manifests, users, and audit history; bounded incident snapshots and sampled clips on a persistent Docker volume.
- Survey-validated per-camera calibration; unavailable metric monitoring is explicit. Camera movement requires a new survey check.
- Rules-based supervisor workflows and drafts requiring human approval before zone application. No automated visual adjudication or equipment actuation.
- Recorded-video demo at `/demo/` when explicitly enabled. Existing scenario REST paths follow the selected access mode.

The bundled YOLO model detects COCO people/vehicles, **not specialized PPE or all industrial machinery**. Use a site-validated construction model for those classes. Empty detections stay empty; missing models and inference failures never generate synthetic workers. PPE can be compliant, noncompliant, or unknown.

Production forecasts use constant velocity. Historical specifications in `agent.md` describe intended research architecture, not commissioned accuracy or latency. Neural forecast checkpoints in the legacy runtime require validation metadata and are never initialized randomly for alerts. There are no validated lead-time/false-alarm claims in the production dashboard.

## Development

Use Python 3.11 or 3.12. Choose CPU or CUDA PyTorch wheels before installing the remaining dependencies:

```bash
python -m venv .venv
. .venv/bin/activate
pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-hub.txt pytest==8.3.4
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --workers 1
python -m pytest tests -q
```

TensorRT, IFC parsing, training tools, and PyTorch Geometric are optional extras. Jetson packaging is a separate hardware-specific deployment; the supplied container targets a Linux PC/server.

## API

| Interface | Purpose |
| --- | --- |
| `/api/v1/auth/login`, `/logout`, `/me` | Local authenticated sessions |
| `/api/v1/cameras` and `/api/v1/cameras/{id}` | Camera registry and administrator configuration |
| `/api/v1/cameras/{id}/status`, `/frame`, `/stream` | Fresh status, JPEG preview, and multipart stream |
| `/ws/cameras/{id}` | Live camera telemetry |
| `/api/v1/compute/retry` | Administrator-triggered device recovery |
| `/api/v1/incidents`, `/publish`, `/{id}/review`, `/{id}/evidence/{kind}` | Persisted events, evidence, and reviews |
| `/api/v1/manifests`, `/{id}/approve` | Draft and approved exclusion zones |
| `/api/v1/agent/*` | Local rules supervisor, human triage, briefing, learning queue/export |
| `/health/live`, `/health/ready` | Process liveness and operational readiness |
| `/api/v1/demo/*` | Optional recorded scenarios |

All mutations require `X-Sentinel-Request: hub` and same-origin requests. The default local panel has full operator access without sign-in. Optional API session mode enforces administrator/operator/viewer roles; provision those accounts with `scripts.hub_admin`. Live telemetry carries camera identity, frame identity, capture timestamp, device, capability status, prediction method, and measured latency.

## License

The repository declares Apache 2.0. Bundled Three.js retains its MIT license notice in the vendored asset.
