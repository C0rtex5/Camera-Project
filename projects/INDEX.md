# SentinelZone-AI Sub-Project Portfolio

**What this directory is.** The camera-monitoring application in this repository is a single
Python service (`src/`, ~11,300 LOC of source plus assets) that mixes nine distinct concerns. This
directory decomposes it into **13 sub-projects**, one per core functionality, each with:

- [`project.json`](perception-detection/project.json) — the machine-readable manifest: purpose,
  **exclusively owned paths**, published interfaces, dependencies, coupling debt, environment
  variables, owned tests, acceptance criteria, build commands and risks;
- `README.md` — the human charter that explains why the project exists and what it must never do.

The decomposition is **evidence-based and machine-checked**: every repository file is owned by
exactly one sub-project, explicitly shared, or explicitly excluded as portfolio tooling; every
cross-project import is either a declared dependency or documented debt; and the declared dependency
graph is acyclic. Run [`scripts/verify_projects.py`](../scripts/verify_projects.py) to re-prove it.

- Analysis that produced this split: [docs/ARCHITECTURE_ANALYSIS.md](../docs/ARCHITECTURE_ANALYSIS.md)
- How to turn these logical projects into physical ones: [MIGRATION_PLAN.md](MIGRATION_PLAN.md)

## Portfolio

| # | Sub-project | Core functionality | Cards | Files | Depends on |
|---|---|---|---|---|---|
| 1 | [perception-detection](perception-detection/README.md) | YOLO detection, PPE, frame geometry, dataset & detector evaluation | SEC-02, SEC-03 | 43 | — |
| 2 | [spatial-calibration](spatial-calibration/README.md) | Homography, ground-plane calibration, BIM/geodetic resolution | SEC-01, SEC-04, SEC-10 | 10 | — |
| 3 | [hazard-tracking](hazard-tracking/README.md) | Metric EKF tracking, zone occupancy, conflict engine | SEC-04, SEC-05, SEC-06 | 6 | spatial-calibration, event-contracts |
| 4 | [trajectory-forecasting](trajectory-forecasting/README.md) | GATv2 ST-GNN forecasting, checkpoint contract, TensorRT export | SEC-05, SEC-06, SEC-11 | 6 | spatial-calibration, hazard-tracking |
| 5 | [event-contracts](event-contracts/README.md) | Safety event, risk levels, manifests — the shared schema | SEC-06, SEC-07 | 9 | — |
| 6 | [edge-runtime](edge-runtime/README.md) | Per-frame edge cycle, manifest resolution, demo pipeline | SEC-05, SEC-06, SEC-07 | 10 | event-contracts, hazard-tracking, perception-detection, spatial-calibration, trajectory-forecasting |
| 7 | [agentic-supervisor](agentic-supervisor/README.md) | LangGraph ReAct supervisor, PTW pipeline, incident adjudication | SEC-09, SEC-10, SEC-12 | 16 | event-contracts, edge-runtime, spatial-calibration |
| 8 | [api-gateway](api-gateway/README.md) | FastAPI routes, hub, demo/live services, auth middleware | SEC-02, SEC-08, SEC-10 | 13 | agentic-supervisor, camera-onboarding, dashboard-ui, edge-runtime, event-contracts, hazard-tracking, media-pipeline, perception-detection |
| 9 | [media-pipeline](media-pipeline/README.md) | Restored video catalog, detection overlay, metric tracks, ghost rules | SEC-02, SEC-09, SEC-10 | 256 | perception-detection, spatial-calibration |
| 10 | [camera-onboarding](camera-onboarding/README.md) | Camera setup drafts, SEC-01 survey, connection checks, SSH tunnel | SEC-01, SEC-02 | 9 | spatial-calibration |
| 11 | [dashboard-ui](dashboard-ui/README.md) | Operator hub, 3D metric twin, setup and media pages | SEC-08, SEC-10, SEC-11 | 13 | — |
| 12 | [deployment-runtime](deployment-runtime/README.md) | Docker images, hardened Compose, systemd, container smoke gates | SEC-02, SEC-12 | 17 | all code sub-projects |
| 13 | [validation-harness](validation-harness/README.md) | Sprint policy engine, isolated test runner, evidence artefacts | SEC-09, SEC-12 | 25 | all other sub-projects |

`Files` counts repository files owned exclusively by the project (verified: 433 owned + 7 shared +
37 excluded portfolio files = 477; the 439 committed application files are all owned or shared).

## Dependency graph

Arrows point from a project to what it depends on. The declared graph is acyclic; the recommended
build order is the reverse topological order printed by the verifier.

```
perception-detection ─┐
spatial-calibration ──┼─> hazard-tracking ──> trajectory-forecasting ──┐
event-contracts ──────┘            │                                    │
      │                            └──────────────┐                     │
      │                                           v                     v
      └──────────────────────────────────> edge-runtime ────────> agentic-supervisor
                                                     │                    │
dashboard-ui ────────────────────────────────┐       │                    │
media-pipeline ──────────────────────────────┼───────┴────────────────────┤
camera-onboarding ───────────────────────────┘                            v
                                                            api-gateway ──> deployment-runtime
                                                                  │
                                                        validation-harness (top gate)
```

## Verified status (last run)

```
python scripts/verify_projects.py --write-report projects/verification-report.json
repository files     : 477   (439 committed + 38 files of this decomposition)
owned by one project : 433
shared platform files: 7
excluded portfolio   : 37
projects             : 13
cross-project imports: 45
documented coupling  : 5
RESULT: PASS (0 errors, 0 warnings)
```

Per-project owned test slices run with `python scripts/run_project_tests.py <id>` (or `all`).
Last recorded run (`projects/evidence/`):

| | |
|---|---|
| Slices fully green | **6/13** — dashboard-ui (66/66 Node tests), camera-onboarding (24), spatial-calibration (10), edge-runtime (2), trajectory-forecasting (2), deployment-runtime (no code tests) |
| Failing tests in the other 7 | **101**, and `scripts/attribute_slice_failures.py` attributes **101/101 (0 unclassified)** to the three exclusions `SOURCE_BUNDLE.md` documents: restored recordings absent (81), Roboflow splits absent (10), upstream commit `bc8e2c88…` absent from this clone (10) |
| Decomposition-caused failures | **0** — no failure is an ownership, import-boundary or interface error |

Evidence and reproduction commands: [projects/evidence/README.md](evidence/README.md).

## Ownership policy

1. **Exclusive ownership.** A tracked file is owned by at most one sub-project. Two owners is an
   error, because it makes review and extraction ambiguous.
2. **Shared platform surface.** `README.md`, `pyproject.toml`, `requirements-runtime.txt`,
   `requirements-runtime.lock`, `.gitattributes`, `.gitignore` and `SOURCE_BUNDLE.md` are shared by
   every project (see [`repo-scope.json`](repo-scope.json)); changing them is a cross-project change
   that needs review from the affected owners.
3. **Dependencies are declared, not inferred.** Every cross-project import must appear in
   `depends_on`. Anything that does not is an error unless it is listed in `coupling_exceptions`
   with a reason *and* a named resolution.
4. **Debt is visible.** `coupling_exceptions` is a liability ledger. If the code is fixed, the
   exception must be deleted — the verifier fails an exception that no longer matches any import.
5. **Tests belong to the project they test**, not to the project that imports them. Cross-project
   imports inside `tests/` are expected and are not treated as architecture.

## Documented coupling debt (5 exceptions)

| From | To | Why it is wrong | Named resolution |
|---|---|---|---|
| `src/graph/context_pipeline.py` | `src/api/app.py` | Pipeline 1 reads module-level runtime state held by the gateway | Move the runtime registry into `edge-runtime` or inject the runtime into `build_context_pipeline` |
| `src/graph/supervisor_agent.py` | `src/api/app.py` | Telemetry tool reads the gateway's runtime/demo state | Consume a narrow telemetry-provider protocol |
| `src/graph/supervisor_agent.py` | `src/api/demo_service.py` | Reaches into the gateway's `DemoService` | Register a telemetry port adapter in the gateway |
| `src/api/camera_setup.py` | `src/api/live_service.py` | Setup starts/stops the gateway-owned live worker | Accept a worker-control protocol, or move the camera config model into `event-contracts` |
| `src/api/ssh_tunnel.py` | `src/api/live_service.py` | Tunnel state reported through the live-service camera model | Depend on the camera configuration contract instead |

Two of these (agentic ↔ gateway, onboarding ↔ gateway) are true cycles. The declared `depends_on`
graph is acyclic because the reverse edges are carried as debt, so today `api-gateway`,
`agentic-supervisor` and `camera-onboarding` must be extracted together — see
[MIGRATION_PLAN.md](MIGRATION_PLAN.md) for the order of operations that removes each exception.

## How to use this portfolio

```bash
python scripts/verify_projects.py --write-report projects/verification-report.json  # integrity gate
python scripts/run_project_tests.py --list                                          # 13 sub-projects
python scripts/run_project_tests.py dashboard-ui                                    # run one slice
python scripts/run_project_tests.py all --keep-going                                # full portfolio
```

`project.json` is the contract every tool reads. Adding a sub-project means adding a directory with
`project.json` + `README.md`, then re-running the verifier until it passes with zero errors.
