# API Gateway & Live Services

> Sub-project `api-gateway` · status **extraction-ready** · cards **SEC-02, SEC-08, SEC-10**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

One process serves the operator, the browser and the integration surface. This project owns the
FastAPI application: REST + WebSocket routes, the shared detection hub, the demo telemetry service,
bounded live camera workers and the production auth middleware. It is the composition root where
every other project is wired together.

## Owned paths

| Path | Contents |
|---|---|
| `src/api/app.py` | ASGI app, route table, lifespan, WebSocket streams, `/setup` and `/api/v1/*` |
| `src/api/hub.py` | Shared detection-hub display adapters and demo/live routing |
| `src/api/demo_service.py` | `DemoService`: scenario discovery, GPU/demo processing, telemetry cache |
| `src/api/live_service.py` | `CameraConfig`, `CameraWorker`, `LiveService`: bounded live camera workers |
| `src/api/security.py` | `TokenAuthMiddleware`, `is_public_showcase`: production auth rules |
| `src/api/__init__.py` | Package marker (import `src.api.app` explicitly to initialise a server) |
| `tests/test_api.py`, `test_demo_api.py`, `test_agent_api.py`, `test_hub.py` | Route and hub tests |
| `tests/test_live_api.py`, `test_production.py`, `test_presentation_1080p30.py` | Production-mode, isolation and presentation guarantees |

## Interfaces it publishes

- **ASGI app** (`uvicorn src.api.app:app`) — `/`, `/health`, `/ready`, `/setup`, `/api/v1/*`,
  `/ws/*`; demo mode works with no camera and no token. Consumed by `dashboard-ui`,
  `deployment-runtime`, `validation-harness`, `camera-onboarding`.
- **Telemetry packet contract** — source, frame identity, timestamps, detections, tracks, zones,
  forecast status and risk; the UI never has to guess freshness. Consumed by `dashboard-ui`,
  `media-pipeline`.
- **`LiveService` worker supervision** — one bounded worker per camera, no credential leakage into
  telemetry, stale frames reported as stale. Consumed by `camera-onboarding`, `media-pipeline`.
- **`TokenAuthMiddleware` / `is_public_showcase`** — production requires `SENTINEL_API_TOKEN` on
  protected routes; showcase routes stay open by design. Consumed by `deployment-runtime`,
  `validation-harness`.

## Dependencies

- **Depends on:** `agentic-supervisor`, `camera-onboarding`, `dashboard-ui`, `edge-runtime`,
  `event-contracts`, `hazard-tracking`, `media-pipeline`, `perception-detection`.
- **Depended on by:** `deployment-runtime`, `validation-harness` (plus the documented reverse edges
  from `agentic-supervisor` and `camera-onboarding`).
- **Inbound coupling debt:** three imports from `agentic-supervisor` and two from
  `camera-onboarding` reach into this project's modules; see those manifests.

## How to run it

```bash
python scripts/run_project_tests.py api-gateway
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

## Acceptance criteria

1. `/health` and `/ready` answer in demo mode with no camera configured.
2. A production deployment without `SENTINEL_API_TOKEN` refuses protected routes.
3. Live workers stop on shutdown and never log credentials.
4. Every route is covered by at least one test in this project's slice.

## Risks and open edges

- **Composition root with mutable module-level state.** `app.py` both wires subsystems and holds
  their instances, which is exactly why the agentic layer and onboarding reach back into it. Paying
  that debt (dependency injection / a small runtime registry) is the prerequisite for extracting
  this project independently.
- **Threads inside the web process:** a hard camera failure must not take the API down; the worker
  supervisor is the current guard.
- Splitting `src/api` into three projects (gateway, media, onboarding) is complete at the ownership
  level but not yet at the package level (see [../MIGRATION_PLAN.md](../MIGRATION_PLAN.md)).
