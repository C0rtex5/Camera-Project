# Migration Plan — Logical Sub-Projects → Physical Sub-Projects

The decomposition shipped in this round is **logical**: ownership, interfaces, tests and dependency
rules are defined and machine-checked, but no source file has moved. That is deliberate — the code
uses `src.`-absolute imports everywhere, so a big-bang move would break the running application and
the whole test suite, and the two import cycles (findings F1/F2 in
[../docs/ARCHITECTURE_ANALYSIS.md](../docs/ARCHITECTURE_ANALYSIS.md)) would survive the move as
cross-repository cycles.

This plan removes the blockers in order. Each phase has a gate that must pass before the next one
starts, and the gate is a command, not an opinion.

## Phase 0 — Baseline (before touching anything)

```bash
git rev-parse HEAD > projects/baseline-commit.txt
docker build -f Dockerfile -t sentinelzone-ai:baseline .
docker run --rm -p 127.0.0.1:18080:8000 sentinelzone-ai:baseline &
curl -fsS http://127.0.0.1:18080/health
docker run --rm sentinelzone-ai:baseline python scripts/run_tests.py   # record pass count + exit code
node --test tests/*.test.cjs                                            # 66 tests, currently green
python scripts/verify_projects.py                                       # must be PASS before and after
```

**Gate:** a recorded baseline of the exact test counts, image id and `/health` payload. Everything
after Phase 0 is compared against these numbers so a decomposition regression cannot hide inside a
"refactor".

## Phase 1 — Pay the coupling debt (prerequisite for any split)

Five exceptions exist today; three of them are the agentic ↔ gateway cycle. The fix is the same in
every case: **move the shared state out of the gateway or invert the call**, then delete the
exception from `project.json` (the verifier fails if a paid exception is left behind).

| Step | Change | Files | Gate |
|---|---|---|---|
| 1.1 | Extract a runtime registry owned by `edge-runtime`: `src/edge/runtime_registry.py` with `get_runtime()` / `set_runtime()`. `app.py` registers its instance; `context_pipeline.py` and `supervisor_agent.py` import the registry, not the app. | `src/edge/runtime_registry.py` (new), `src/api/app.py`, `src/graph/context_pipeline.py`, `src/graph/supervisor_agent.py` | verifier reports 0 exceptions for these 3 edges; `tests/test_pipelines.py`, `tests/test_supervisor_agent.py`, `tests/test_agent_api.py` green |
| 1.2 | Define a telemetry port (protocol) and register a gateway adapter for it; pass the port into `build_supervisor_agent(...)`. Removes `supervisor_agent → src/api/demo_service.py`. | `src/schemas/telemetry.py` (new, owned by `event-contracts`), `src/api/app.py`, `src/graph/supervisor_agent.py` | `tests/test_supervisor_agent.py` green; verifier 0 exceptions |
| 1.3 | Give onboarding a worker-control protocol (or move `CameraConfig` into `event-contracts`) so `camera_setup.py` and `ssh_tunnel.py` stop importing `src/api/live_service.py`. | `src/schemas/camera.py` (new) or `src/edge/…` protocol, `src/api/camera_setup.py`, `src/api/ssh_tunnel.py`, `src/api/live_service.py` | verifier 0 exceptions; `tests/test_camera_setup.py`, `test_ssh_tunnel.py` green |

**Gate:** `python scripts/verify_projects.py` passes with `0` coupling exceptions, and the declared
dependency graph remains acyclic. At this point `agentic-supervisor`, `camera-onboarding` and
`api-gateway` are genuinely independent.

## Phase 2 — Split the flat `src/edge` package and rename the distribution packages

`src/edge` currently hosts five concerns across twelve modules. The target physical layout keeps one
repository (a monorepo is correct here: the projects share a release train and a single process) but
gives every sub-project its own importable package and its own `pyproject.toml`.

```
packages/
  sz-contracts/        src/sz_contracts/        # event-contracts
  sz-perception/       src/sz_perception/       # perception-detection
  sz-spatial/          src/sz_spatial/          # spatial-calibration
  sz-tracking/         src/sz_tracking/         # hazard-tracking
  sz-forecast/         src/sz_forecast/         # trajectory-forecasting
  sz-edge-runtime/     src/sz_edge_runtime/     # edge-runtime
  sz-agent/            src/sz_agent/            # agentic-supervisor
  sz-media/            src/sz_media/            # media-pipeline
  sz-onboarding/       src/sz_onboarding/       # camera-onboarding
  sz-gateway/          src/sz_gateway/          # api-gateway
apps/
  dashboard/           src/sz_dashboard/        # dashboard-ui (static assets + node tests)
deploy/                                          # deployment-runtime
tools/                                           # validation-harness
```

Module → package moves (one `git mv` per row, imports rewritten in the same commit):

| From | To | Owner |
|---|---|---|
| `src/schemas/*` | `packages/sz-contracts/src/sz_contracts/` | event-contracts |
| `src/perception/*` | `packages/sz-perception/src/sz_perception/` | perception-detection |
| `src/edge/homography.py`, `src/edge/calibration.py`, `src/spatial/*` | `packages/sz-spatial/src/sz_spatial/` | spatial-calibration |
| `src/edge/tracker.py`, `src/edge/zone_occupancy.py`, `src/edge/conflict_engine.py` | `packages/sz-tracking/src/sz_tracking/` | hazard-tracking |
| `src/edge/gatv2_model.py`, `src/edge/checkpoint.py`, `src/edge/export_tensorrt.py` | `packages/sz-forecast/src/sz_forecast/` | trajectory-forecasting |
| `src/edge/edge_runtime.py`, `src/edge/manifest_selector.py`, `src/edge/demo_pipeline.py`, `src/edge/__init__.py` | `packages/sz-edge-runtime/src/sz_edge_runtime/` | edge-runtime |
| `src/graph/*` | `packages/sz-agent/src/sz_agent/` | agentic-supervisor |
| `src/api/media*.py`, `src/api/detection_ghosts.py` | `packages/sz-media/src/sz_media/` | media-pipeline |
| `src/api/camera_setup.py`, `camera_survey.py`, `connection_check.py`, `ssh_tunnel.py` | `packages/sz-onboarding/src/sz_onboarding/` | camera-onboarding |
| `src/api/app.py`, `hub.py`, `demo_service.py`, `live_service.py`, `security.py`, `__init__.py` | `packages/sz-gateway/src/sz_gateway/` | api-gateway |
| `src/dashboard/*` | `apps/dashboard/` | dashboard-ui |

Import rewrite rule (mechanical, applies once per commit):

```bash
# example for one commit; review the diff, never blanket-replace across packages
grep -rl "from src.edge import" packages apps | xargs sed -i \
  -e 's/from src\.schemas\b/from sz_contracts/g' \
  -e 's/from src\.perception\b/from sz_perception/g' \
  -e 's/from src\.spatial\b/from sz_spatial/g'
python scripts/verify_projects.py     # ownership globs are updated in the same commit
```

Each `packages/<name>/pyproject.toml` declares its dependencies as **workspace paths plus version
ranges** (`sz-contracts>=0.1,<0.2`), and `requirements-runtime.txt` moves into the root as a
compatibility shim until the container is rebuilt on the package graph.

**Gate:** the container image builds from the new layout, `docker run … python scripts/run_tests.py`
reproduces the Phase 0 count, `node --test apps/dashboard/tests/*.cjs` is still 66/66, and the
verifier passes with the layout-specific globs.

## Phase 3 — Independent repositories (only after Phase 1)

Repositories are split **only for projects with no remaining cycle and a stable interface**:

| Order | Project | Why it can move first |
|---|---|---|
| 1 | `event-contracts` | Leaf, depended on by everyone; versioned schema releases stabilise the rest |
| 2 | `perception-detection` | Leaf; heavy dependencies make it the best candidate for its own image/build |
| 3 | `dashboard-ui` | No Python imports at all; HTTP contract only |
| 4 | `spatial-calibration` | Leaf; used as a library by three projects |
| 5+ | everything else | After Phase 1 removes the cycles; `api-gateway`, `agentic-supervisor` and `camera-onboarding` move as one unit if the debt is still open |

```bash
# preserve history for one project
git subtree split --prefix=packages/sz-contracts -b split/sz-contracts
git push <new-remote> split/sz-contracts:main
```

**Do not** create one repository per sub-project just because the folders exist: extracting
`hazard-tracking` (6 files) or `trajectory-forecasting` (6 files) into separate remotes buys
per-release overhead with no independence benefit. Keep them as packages and revisit only if the
release cadence actually diverges.

## Phase 4 — CI, ownership and drift control

1. **CI matrix** where each job runs exactly one slice:
   `python scripts/run_project_tests.py <id>` plus `python scripts/verify_projects.py`.
2. **CODEOWNERS** generated from `project.json` (`owned_paths` → owning team), so review routing
   matches the decomposition instead of a hand-maintained list.
3. **Contract job:** validate `deploy/SAFETY_EVENT_CONTRACT.md` against the Pydantic models in
   `event-contracts` field-for-field, failing on drift.
4. **UI contract job:** validate the `dashboard-ui` fixtures against a JSON Schema published by
   `event-contracts` (closes the untested HTTP boundary noted in that project's README).
5. **Deployment matrix:** build and smoke every Dockerfile × Compose pair; a variant that fails
   smoke is removed rather than left to rot.

## Rollback

Every phase is one commit series on a branch. The rollback is `git revert` of the series plus a
restore of the previous `projects/*/project.json` ownership globs; the verifier is the arbiter of
whether the revert is complete (ownership must again cover every repository file with no overlaps).

## Explicit non-goals

- **No microservice split.** The 13 sub-projects are packages in one deployable. The edge cycle
  (<120 ms budget) and the dashboard share one process on one host on purpose; splitting the runtime
  into services would add latency and failure modes that the safety path cannot afford.
- **No file moves in this round.** The deliverable of this round is the ownership, interface and
  dependency model plus the tooling that enforces it.
- **No claim of validation.** The decomposition changes nothing about what the product has proven;
  physical-site evidence remains gated by `validation-harness`.
