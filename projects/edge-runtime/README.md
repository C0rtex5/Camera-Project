# Edge Runtime Orchestration

> Sub-project `edge-runtime` · status **extraction-ready** · cards **SEC-05, SEC-06, SEC-07**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

Perception, calibration, tracking, forecasting and the conflict engine are only useful when they
run as one deterministic cycle. This project is that cycle: one frame in, one telemetry packet out,
with every optional subsystem degrading to a labelled status instead of an exception. It also
resolves which shift manifest governs a camera and provides the offline demo pipeline.

## Owned paths

| Path | Contents |
|---|---|
| `src/edge/edge_runtime.py` | `SentinelEdgeRuntime`: the per-frame safety cycle and packet assembly |
| `src/edge/manifest_selector.py` | `resolve_manifest`: camera → governing shift manifest by site |
| `src/edge/demo_pipeline.py` | `run_pipeline_demo`, synthetic frame generation for offline operation |
| `src/edge/__init__.py` | Deterministic edge-core package exports |
| `data/manifests/**` | Persisted shift manifests and the active manifest pointer |
| `tests/test_edge_runtime.py` | Runtime cycle and degradation tests |

## Interfaces it publishes

- **`SentinelEdgeRuntime.process_frame`** — detections, metric tracks, zones, forecast status,
  events and latency in one packet; deterministic for identical input. Consumed by `api-gateway`,
  `media-pipeline`.
- **Live envelope ingestion (`runtime.conflict_engine`)** — manifests compiled by the agentic layer
  take effect on the next frame without a worker restart. Consumed by `agentic-supervisor`,
  `api-gateway`.
- **`resolve_manifest` / `active_manifest_path`** — an unmatched camera gets an explicit default,
  never another site's envelopes. Consumed by `agentic-supervisor`, `api-gateway`.

## Dependencies

- **Depends on:** `event-contracts`, `hazard-tracking`, `perception-detection`, `spatial-calibration`,
  `trajectory-forecasting`.
- **Depended on by:** `agentic-supervisor`, `api-gateway`, `media-pipeline`, `validation-harness`.

## How to run it

```bash
python scripts/run_project_tests.py edge-runtime
python -c "from src.edge.demo_pipeline import run_pipeline_demo; run_pipeline_demo()"
```

## Acceptance criteria

1. A single frame produces a complete packet with an explicit latency measurement.
2. A missing detector, checkpoint or calibration produces a status field, not an exception.
3. Injected exclusion envelopes change the evaluation on the next frame without a restart.
4. `data/manifests` stays writable by `agentic-supervisor` and readable here under one documented rule.

## Risks and open edges

- **Integration point:** this project imports almost everything, so a broken boundary shows up here
  first. It is the natural place for the contract tests that CI should run on every change.
- **Stateful envelope injection:** the agent and an operator can both write envelopes; the ownership
  rule is documented but not yet enforced by code.
- `data/manifests` is a shared handoff directory: this project owns the format contract while
  `agentic-supervisor` owns the production of its contents.
