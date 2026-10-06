# Trajectory Forecasting (GATv2 ST-GNN)

> Sub-project `trajectory-forecasting` · status **extraction-ready** · cards **SEC-05, SEC-06, SEC-11**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

Predictive safety is the product differentiator: warning before a struck-by event rather than
drawing a box after it. This sub-project owns the spatio-temporal graph attention model that
forecasts multi-agent trajectories, the checkpoint contract that ties a model artifact to one
camera calibration, and the optional ONNX/TensorRT acceleration path.

## Owned paths

| Path | Contents |
|---|---|
| `src/edge/gatv2_model.py` | `WorkZoneSTGNN`: GATv2-style spatio-temporal trajectory forecaster |
| `src/edge/checkpoint.py` | `camera_fingerprint`, `model_contract`, `validate_checkpoint`: artifact ↔ calibration binding |
| `src/edge/export_tensorrt.py` | `export_onnx`, `build_tensorrt_engine`: optional acceleration export |
| `scripts/train_forecaster.py` | Camera-aligned training from reviewed metric trajectory windows |
| `deploy/FORECAST_TRAINING.md` | Training and checkpoint-compatibility procedure |
| `tests/test_forecast_training.py` | Checkpoint contract and training-path tests |

## Interfaces it publishes

- **`WorkZoneSTGNN.forward`** — a graph of metric track windows in, a 3.0 s horizon per agent with
  confidence out; runs on CPU when no accelerator exists. Consumed by `edge-runtime`, `api-gateway`.
- **Checkpoint contract** — a checkpoint is usable only with the camera calibration and sampling
  rate it was trained for; mismatches are refused with a named reason. Consumed by `edge-runtime`,
  `camera-onboarding`, `validation-harness`.
- **Export path** — offline and optional; absence of TensorRT must not affect the CPU runtime.

## Dependencies

- **Depends on:** `spatial-calibration` (calibration fingerprint), `hazard-tracking` (track windows).
- **Depended on by:** `edge-runtime`, `api-gateway`, `deployment-runtime`, `validation-harness`.

## How to run it

```bash
python scripts/run_project_tests.py trajectory-forecasting
python scripts/train_forecaster.py            # training path (requires torch + data)
```

## Acceptance criteria

1. A checkpoint trained for camera A is refused for camera B with a named reason.
2. Forecast status is reported as AVAILABLE / UNAVAILABLE on the wire; an absent checkpoint never
   fabricates a trajectory.
3. Training is reproducible from reviewed metric trajectory windows only.
4. CPU inference stays inside the documented edge-cycle latency budget.

## Risks and open edges

- **Forecast quality is unvalidated against physical-site data** — the repository labels forecast
  claims as design targets, and this project should be the first to say so.
- **Release coupling:** a new detector or recalibration invalidates existing checkpoints; the
  checkpoint contract is the control that makes that failure loud instead of silent.
- `torch-geometric` / `tensorrt` are optional extras; the empirical evidence in
  `deploy/validation/` must record which extra was present for any latency claim.
