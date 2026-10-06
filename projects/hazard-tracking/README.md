# Metric Tracking & Hazard Zones

> Sub-project `hazard-tracking` · status **extraction-ready** · cards **SEC-04, SEC-05, SEC-06**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

This is the deterministic safety core: it keeps identity across frames, decides entry / continued
presence / exit for a hazard zone, and evaluates worker–machinery pairs against dynamic exclusion
envelopes into a debounced risk verdict. Nothing in this project touches the network, the LLM or
the browser — it is testable in isolation and must stay that way.

## Owned paths

| Path | Contents |
|---|---|
| `src/edge/tracker.py` | `MetricKalmanFilter`, `MetricTrack`: constant-velocity EKF over metric ground coordinates |
| `src/edge/zone_occupancy.py` | `ZoneOccupancyTracker`, `ZoneTransition`: ENTRY / PRESENT / EXIT with hysteresis |
| `src/edge/conflict_engine.py` | `DynamicConflictEngine`, `AlertHysteresisDebouncer`: pair evaluation, min TTC, collision probability |
| `tests/test_tracker.py`, `tests/test_conflict_engine.py` | Unit tests for tracking and conflict evaluation |
| `tests/test_safety_zone_and_event.py` | Zone occupancy, business-state mapping, event emission and live overlay contract |

## Interfaces it publishes

- **`MetricKalmanFilter` / `MetricTrack`** — stable track identity with explicit coast/expiry
  behaviour. Consumed by `edge-runtime`, `media-pipeline`.
- **`ZoneOccupancyTracker` transitions** — exactly one ENTRY and one EXIT for a continuously present
  agent. Consumed by `edge-runtime`, `api-gateway`.
- **`DynamicConflictEngine.evaluate`** — per-pair risk with min TTC and collision probability,
  debounced before alerting. Consumed by `edge-runtime`, `api-gateway`.

## Dependencies

- **Depends on:** `spatial-calibration` (metric coordinates), `event-contracts` (risk levels, business
  states).
- **Depended on by:** `edge-runtime`, `api-gateway`, `trajectory-forecasting`, `validation-harness`.

## How to run it

```bash
python scripts/run_project_tests.py hazard-tracking
```

## Acceptance criteria

1. A synthetic crossing worker/machinery pair yields a DANGER evaluation before closest approach.
2. A worker standing still inside a zone produces exactly one ENTRY and one EXIT.
3. Debouncing suppresses alert flapping across single-frame boundary jitter.
4. Risk levels and business states are imported from `event-contracts`, never redefined locally.

## Risks and open edges

- **Threshold-driven behaviour:** changing a shift manifest silently changes what counts as danger,
  so manifest resolution belongs to `edge-runtime` and must not be duplicated here.
- **Shared package:** the three modules live in the flat `src/edge` package alongside runtime and
  forecast code; extraction requires a package rename and an import rewrite (see
  [../MIGRATION_PLAN.md](../MIGRATION_PLAN.md)).
- Forecast quality is *not* validated here: this project is the deterministic fallback that must
  remain correct when the GNN is unavailable.
