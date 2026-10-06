# SentinelZone-AI — Architecture Analysis & Sub-Project Rationale

**Subject:** the camera-monitoring Git repository at the root of this workspace
(`sentinelzone-ai` v3.0.0, "Predictive Dynamic Spatial Work-Zone Safety System").
**Deliverable it feeds:** the 13 sub-projects in [`projects/`](../projects/INDEX.md).
**Method:** static inspection only — `git ls-files`, Python `ast` import parsing, module docstrings,
route decorators, environment-variable scan, test-to-module mapping, documentation cross-checks. No
application code was executed except the Node UI suite; no repository data was modified.

Reproduce with:

```bash
git ls-files | wc -l                                   # 439 tracked files
python scripts/verify_projects.py                      # ownership / dependency integrity
python scripts/run_project_tests.py --list             # 13 sub-projects
```

## 1. What the system actually is

A single Python process (FastAPI + uvicorn) that runs a per-frame safety cycle and serves a browser
dashboard. There is **no database**: all state is files under `data/` (JSON manifests, incident
records, JSONL event logs, curated active-learning queue) plus in-process worker threads and caches.

| Dimension | Measured |
|---|---|
| Tracked files | **439** (root 20, `src/` 294, `data/` 47, `tests/` 40, `deploy/` 23, `scripts/` 11, `config/` 3, `weights/` 1) |
| Application Python | **42 modules, 9,863 LOC** in `src/` |
| Browser assets | 3 JS modules (586 LOC), 3 HTML pages (373 LOC), 1 CSS (479 LOC), vendored three.js r128 |
| Test suite | **32 unittest modules (5,399 LOC)** + **4 Node test files** (66 tests, all passing here) |
| Tooling | 11 scripts, 2,228 LOC |
| Documentation | 23 files under `deploy/`, ~2,525 LOC of markdown |
| Runtime dependencies | 22 pinned packages (`requirements-runtime.txt`), dominated by torch / torchvision / ultralytics / opencv |
| Configuration surface | 30+ `SENTINEL_*`, `CAMERA_*`, `YOLO_*` environment variables (see §5) |
| Media assets | 240 demo JPEGs under `src/demo_assets/` + 2 root samples + Git-LFS restored media |

**Runtime shape.** `src/api/app.py` is the composition root: it constructs the demo service, the
live service, the media catalog and the agent pipelines during lifespan startup, mounts the static
dashboard, and exposes REST + WebSocket routes. Inference runs in threads owned by the web process
(`CameraWorker`, `_Prewarmer`), and the edge cycle is assembled in `SentinelEdgeRuntime.process_frame`.

## 2. Module inventory

| Package | Modules | LOC | Role |
|---|---|---|---|
| `src/api` | 14 | 3,916 | Gateway, hub, media catalog/tracks/detection, live + demo services, camera setup/survey/connection check, SSH tunnel, security |
| `src/edge` | 12 | 2,382 | Per-frame runtime, tracker, zone occupancy, conflict engine, homography, calibration, GATv2 model, checkpoint, export, manifest selector, demo pipeline |
| `src/graph` | 5 | 1,375 | LangGraph supervisor (6 tools), LLM provider, PTW context pipeline, adjudication pipeline |
| `src/perception` | 6 | 965 | Detector, frame calibration, dataset downloader, training, test-video generation |
| `src/schemas` | 3 | 312 | Safety event contract, Pydantic operational contracts |
| `src/spatial` | 2 | 128 | BIM/IFC geodetic resolver |
| `src/dashboard` | 7 | 1,438 | Hub/live/setup pages, Canvas overlay, Three.js twin, media panel |

`src/edge` is the clearest smell in the codebase: twelve modules from **five different concerns**
(tracking, calibration, forecasting, runtime orchestration and manifest resolution) share one flat
package, which is why four sub-projects own files inside it.

## 3. Coupling: the measured dependency structure

Cross-project imports counted by the verifier: **45 edges** between the 13 sub-projects, all declared
or documented. The hottest modules are exactly what you would expect from a monolith:

| Module | Outgoing `src.*` imports | Assessment |
|---|---|---|
| `src/api/app.py` | 12 | Composition root + route table + module-level state |
| `src/edge/edge_runtime.py` | 7 | Legitimate integration point of the edge layer |
| `src/graph/supervisor_agent.py` | 6 | Reaches into the API layer |
| `src/api/live_service.py` | 5 | Correct: consumes edge + contracts |
| `src/graph/context_pipeline.py` | 5 | Reaches into the API layer |

### 3.1 Cycle 1 — agentic layer ↔ API gateway (3 edges)

```
src/graph/context_pipeline.py  →  src/api/app.py          (reads module-level runtime)
src/graph/supervisor_agent.py  →  src/api/app.py          (telemetry tool)
src/graph/supervisor_agent.py  →  src/api/demo_service.py (scenario/telemetry tool)
```

The API imports the pipelines at startup (`app.py → src.graph.*`), so the gateway and the agentic
layer are mutually dependent. **Impact:** they cannot be versioned, tested or extracted
independently; the LangGraph layer cannot be run without a FastAPI import graph. **Fix:** move the
runtime registry into `edge-runtime` and inject it, or define a telemetry-provider protocol the
gateway implements.

### 3.2 Cycle 2 — camera onboarding ↔ API gateway (2 edges)

```
src/api/camera_setup.py  →  src/api/live_service.py
src/api/ssh_tunnel.py    →  src/api/live_service.py
```

`app.py` imports camera setup/survey, so the dependency closes the cycle. **Impact:** lower than
cycle 1 (same `src/api` package today, so extraction cost is a package split rather than a design
change), but it blocks splitting `src/api` into three repositories.

### 3.3 Handled coupling (healthy)

- `edge-runtime` → 5 dependencies: the intended integration point.
- `perception-detection` and `event-contracts` are **leaves**: clean, independently extractable today.
- `dashboard-ui` has **no** Python import surface at all — only the HTTP packet contract.

## 4. Architecture drift: documentation vs code

The root `README.md` describes a structure that does not exist. Verified absences:

| Documented | Reality |
|---|---|
| `agent.md` ("mathematical specification") | **missing** — linked twice from README |
| `src/tracking/{ekf,gnn_forecaster,conflict_engine}.py` | code lives in `src/edge/{tracker,gatv2_model,conflict_engine}.py` |
| `tests/test_ekf.py`, `test_gnn_forecaster.py`, `test_incident_agent.py`, `test_safety_engine.py` | **missing**; equivalents are `test_tracker.py`, `test_forecast_training.py`, `test_safety_zone_and_event.py`, `test_conflict_engine.py` |
| `data/real_videos/`, `data/test_videos/` ("dynamic drop folders") | **missing** on disk; the code creates/expects them at runtime |

This is documentation debt, not architectural debt, but it matters for a decomposition: the
sub-project charters in `projects/` are written from the code, and this table is the reason.

## 5. Configuration surface

30+ environment variables are read directly across the codebase, grouped by the project that should
own them once the split is physical:

| Group | Variables | Owner |
|---|---|---|
| Runtime mode & auth | `SENTINEL_MODE`, `SENTINEL_API_TOKEN` | api-gateway |
| Camera identity | `SENTINEL_CAMERA_CONFIG`, `SENTINEL_SETUP_FILE`, `SENTINEL_SURVEY_FILE`, `CAMERA_GATE_RTSP`, `CAMERA_OFFLINE`, `CAMERA_HEIGHT_M` | camera-onboarding / spatial-calibration |
| Model artifacts | `SENTINEL_DETECTOR_WEIGHTS`, `SENTINEL_GNN_CHECKPOINT`, `SENTINEL_DEVICE`, `SENTINEL_DETECTOR_CONF`, `SENTINEL_DETECTOR_IOU`, `SENTINEL_CLASS_MAPPING` | perception-detection / trajectory-forecasting |
| Media & demo | `SENTINEL_DEMO_DIR`, `SENTINEL_MEDIA_DIRS`, `SENTINEL_DATASET_DIRS`, `SENTINEL_MEDIA_*` (6) | media-pipeline |
| Governance | `SENTINEL_MANIFEST_DIR` | edge-runtime |
| Agent | `SENTINEL_LLM_MODEL` | agentic-supervisor |
| Tunnelling | `SENTINEL_SSH_KEY_FILE`, `SENTINEL_SSH_KNOWN_HOSTS`, `SENTINEL_PROBE_SOURCE` | camera-onboarding |
| Host tuning | `SENTINEL_CPU_THREADS`, `OMP_NUM_THREADS` | deployment-runtime |

There is no central settings object; each module calls `os.environ` itself. That is tolerable today
and is a concrete extraction task per project (documented as `config_env` in every `project.json`).

## 6. Test and validation surface

- **32 Python unittest modules** map cleanly onto the proposed projects (each test module was mapped
  by its primary module under test, verified through its imports).
- **4 Node test files, 66 tests** exercise the dashboard with no browser and no network; this slice
  was executed during the analysis: **66/66 passing**.
- Python slices require the pinned runtime stack. In the analysis sandbox only `numpy` was present,
  so the Python slices report import errors (`cv2`, `fastapi`, `pydantic`, `torch`, …) rather than
  application failures; the container image is the authoritative environment.
- `scripts/sprint_validation.py` already encodes a 12-card validation policy with an explicit
  **physical-site evidence gate** — the strongest existing process control in the repository, and the
  reason `validation-harness` is a first-class sub-project rather than a folder of scripts.

## 7. Findings and risks, by owner

| # | Severity | Finding | Owner |
|---|---|---|---|
| F1 | **High** | Agentic layer and API gateway form an import cycle via three `src.api` imports | agentic-supervisor, api-gateway |
| F2 | Medium | Camera onboarding and gateway form a second cycle through `live_service` | camera-onboarding, api-gateway |
| F3 | **High** | `src/edge` mixes five concerns in one flat package; no extraction is possible without a package rename | 4 projects own files inside it |
| F4 | Medium | Metric accuracy depends entirely on surveyed calibration, while examples ship by default and the runtime happily runs uncalibrated | spatial-calibration |
| F5 | Medium | Forecast and latency claims are design targets; only `DEMO/RECORDING` evidence exists | trajectory-forecasting, validation-harness |
| F6 | Medium | 240 tracked demo JPEGs + weights bloat the repository; no LFS policy for them | media-pipeline, perception-detection |
| F7 | Medium | README documents non-existent modules and tests (§4) | repo documentation (shared surface) |
| F8 | Low | 30+ environment variables read ad hoc with no central settings object | all |
| F9 | Low | Camera credentials and SSH keys flow through the same process as the operator UI | camera-onboarding |
| F10 | Low | Four Dockerfiles × four Compose files with only smoke scripts coupling them | deployment-runtime |

## 8. Why 13 sub-projects, and what was rejected

**Criteria used:** (a) a sub-project must own a coherent capability with its own lifecycle; (b) it
must publish interfaces other projects consume; (c) it must be independently testable from its owned
slice; (d) its boundary must be expressible as a path glob with no overlap; (e) where the code is
currently coupled, the coupling is declared as debt with a resolution rather than ignored.

**Applied result:** the split follows the *data flow* (perceive → calibrate → track → forecast →
orchestrate → reason → serve → show) plus the two cross-cutting projects that always deserve their
own lifecycle: `event-contracts` (shared schema) and `validation-harness` (evidence and policy).
`deployment-runtime` packages everything, and `media-pipeline` is separate because
restored-recording support has its own tests, its own cache and its own evidence rules.

**Rejected alternatives**

| Alternative | Why rejected |
|---|---|
| Keep the monolith, document modules only | Gives no ownership, no test slice, no extraction path — the request was explicitly to create sub-projects |
| 4 layer-projects (perception / edge / agents / api) | Reproduces the current flat `src/edge` problem: 12 modules, 5 concerns, one owner, one test slice |
| One project per SEC card (12 folders) | Cards are sprint deliverables, not code boundaries; several cards are satisfied by the same module and several modules serve multiple cards |
| One project per `src/` package (6 folders) | Would leave `src/api` at 3,916 LOC and 4 concerns while splitting a 128-LOC resolver into its own project — inverted granularity |
| Physically move code during this round | Would break the running application and the test suite with today's `src.`-absolute imports; extraction is sequenced in [MIGRATION_PLAN.md](../projects/MIGRATION_PLAN.md) instead |

## 9. Mapping to the project's own SEC cards

| Card | Sub-projects that carry it |
|---|---|
| SEC-01 camera inventory & validation | camera-onboarding, spatial-calibration |
| SEC-02 CV environment | perception-detection, deployment-runtime, api-gateway, media-pipeline |
| SEC-03 worker detection | perception-detection |
| SEC-04 virtual hazard zone | spatial-calibration, hazard-tracking |
| SEC-05 proximity | hazard-tracking, edge-runtime, trajectory-forecasting |
| SEC-06 risk classification | hazard-tracking, event-contracts, edge-runtime, trajectory-forecasting |
| SEC-07 structured event | event-contracts, edge-runtime |
| SEC-08 visual alert | dashboard-ui, api-gateway |
| SEC-09 real-imagery validation | validation-harness, media-pipeline, agentic-supervisor |
| SEC-10 digital-twin preparation | api-gateway, dashboard-ui, spatial-calibration, agentic-supervisor, media-pipeline |
| SEC-11 3D safety prototype | dashboard-ui, trajectory-forecasting |
| SEC-12 documentation & closeout | validation-harness, deployment-runtime, agentic-supervisor |

Every card is covered by at least two sub-projects, which is expected: the cards describe
deliverables, the sub-projects describe code ownership. `scripts/sprint_validation.py` remains the
authority on card verdicts; the sub-projects are the authority on code.
