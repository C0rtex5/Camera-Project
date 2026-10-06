# 30-aspect audit — 2026-10-01

Run as a workflow: one agent per aspect, each followed by an adversarial verifier
that was told to falsify the findings rather than agree with them.

**Result: 30 aspects audited, 0 agent failures, 60 agent runs.**

| | |
|---|---|
| aspects | 30 |
| health: SERIOUS_ISSUES | 29 |
| health: MINOR_ISSUES | 1 |
| CRITICAL findings | 6 |
| HIGH findings | 55 |
| MEDIUM findings | 83 |
| LOW findings | 29 |
| findings sent to a verifier and CONFIRMED | 47 |

Only CRITICAL and HIGH findings were verified, so MEDIUM and LOW read as NOT VERIFIED
rather than as refuted. Nothing was refuted.

## Method, and the first attempt that failed

The first run used `pipeline()` with the whole 30-aspect list at once. **All 60
agents failed.** The audit prompt had invited them to run the Python test suite,
and every run of that suite loads YOLO and torch — thirty of those at once starved
the machine.

The retry, which succeeded, changed four things:

- **Bounded concurrency**: waves of five, not thirty at once.
- **Lightweight agents**: no test suite, no torch/ultralytics/cv2 imports. Read and
  grep, plus small standard-library snippets only.
- **One retry** per aspect when an agent returned nothing.
- **Verification only where it matters**: the second agent runs only for aspects
  that reported CRITICAL or HIGH findings, so the quiet aspects cost one agent
  instead of two.

That took the run from 0/30 to 30/30.

## Confirmed CRITICAL

### 1. A colour heuristic sets `measured = True`, so "NO HARDHAT" can come from pixels

`src/perception/detector.py:185-242`, telemetry at `:247-256`. Under the COCO
weights the app actually loads, a worker box of 20x10 px or more sets
`measured = True`, and the hardhat verdict then comes from HSV colour fractions of
the crop (`hh_coverage > 0.08`, white/blue/red/yellow bands). The dashboard prints
`NO HARDHAT` in red on that basis, as though PPE had been measured.

This is the project's own honesty rule inverted: a negative safety finding derived
from a colour threshold, presented as a measurement.

### 2. The adjudication endpoint fabricates TTC and collision probability

`src/api/app.py:355-383`, quote at `:370`:

```python
"telemetry_json": req.telemetry_json or {"min_ttc": 1.4, "p_col": 0.88},
```

The guard above it only fires in production. In the default configuration, a caller
that omits telemetry gets a verdict computed from invented kinematics, and the VLM
prompt is told these are observed.

### 3. An empty permit list becomes a fabricated hazard task

`src/graph/context_pipeline.py:62-114`. An LLM correctly answering `[]` does not
match `if isinstance(tasks, list) and len(tasks) > 0`, so control reaches the
heuristic block and invents "Site Earthmoving", MEDIUM risk, with a validity window.

**Verified myself, and the severity needs correcting.** The audit called this
CRITICAL without noting the guard two lines below it. Executed in both modes:

| mode | result |
|---|---|
| production | `refused (RuntimeError)` — an unconfigured provider, and the `SENTINEL_MODE` guard at `:84` raises for the empty-list case |
| development | `FABRICATED: ['Site Earthmoving']` |

So production is protected; the fabrication is reachable in development and demo
mode, which is what the restored-recording path uses. Real defect, narrower blast
radius than the audit claimed.

### 4. No BIM data yields a hardcoded fabricated rectangle

`src/spatial/bim_resolver.py:80-84` and `:118-122` return
`[(100,50),(125,50),(125,70),(100,70)]` — a 25x20 m exclusion zone — whenever the
named IFC element is not found, guarded only by a `logger.warning`. It is
validated, persisted into the manifests and enforced by the edge as a real
exclusion zone, indistinguishable from a surveyed one.

## The rest of the confirmed HIGH findings

Grouped by the aspect that raised them. Locations are as the auditors reported
them; I have personally reproduced only the ones marked.

- **detector.py**: `_heuristic_fallback_detect` publishes hardcoded detections and
  `ppe_compliant: True` into the normal path with no provenance marker; the
  "unsupported class mapping" `raise` is swallowed by the load handler and the
  schema is guessed as `construction` afterwards.
- **media_detection.py**: `detect_for_media` returned a 2-tuple on the disabled
  path, breaking its 3-tuple contract. **Fixed, verified.**
- **media.py**: `@lru_cache(maxsize=1)` on `_catalog_signature()` made the
  mtime-based invalidation dead — the comparison was always true after the first
  build. **Fixed, verified.**
- **media_tracks.py**: `anchored` is never published, so the assumed-band fallback
  is served as a fitted per-recording calibration; a footpoint above the horizon is
  reported as exactly 30.0 m rather than as unknown; the calibration anchor skips
  the height filter the tracker applies, so a documented person fragment can set a
  recording's scale; an unanchored plane still yields DANGER/NEAR/SAFE grades.
- **hub.py**: the same 2-tuple bug made every demo frame a 500 with the overlay
  disabled. **Fixed with the media_detection change.**
- **demo_service.py**: per-frame telemetry carries hardcoded "Validated" figures
  (`3.42 s (Target: >= 3.0 s)`, `0.72 / hr (Validated)`) that nothing measures;
  `min_ttc_seconds` is overwritten with a synthesized value when the distance is
  small; missing PPE data defaults to `hardhat=True, vest=True`.
- **app.py**: camera-connection check reuses the saved password against a
  caller-chosen host; incident publish returns `BROADCAST_SUCCESS` without
  verifying delivery, and an existing test encodes that false claim.
- **security.py**: the only authentication class in the repository is never
  installed on the app, while its docstring asserts production authentication.
- **edge_runtime.py**: `forecast_status` reports `AVAILABLE` from a configured
  checkpoint path rather than from whether the model ran; the GNN window is padded
  with a repeated oldest sample; zone exits caused by track loss are published as
  `MEASURED`.
- **zone_occupancy.py**: expiry is keyed on the agent rather than the
  (zone, agent) pair, so occupancy latches; no-risk-data defaults are the
  contract's "clear" values, so an unmeasured agent reads as a measured negative.
- **gatv2_model.py / evaluate_comparative.py**: the ST-GNN benchmark row is
  hardcoded — `evaluate_st_gnn()` takes no model, no checkpoint and no data and
  returns literals (96.80% recall, 3.42 s lead time).
- **manifest_selector.py**: `polygon_metric_epsg3857` is neither EPSG:3857 nor
  site-specific — all five manifests carry the same hardcoded rectangle; and
  selection never checks `valid_from`/`valid_until`, so an expired shift is
  enforced as in force.
- **export_tensorrt.py**: silently exports a randomly initialised model and
  reports success.
- **demo_pipeline.py**: presents the absence of a forecast model as the negative
  finding "no alarm"; prints "Full pipeline verified" and a "10 Hz" rate that are
  never measured.
- **supervisor_agent.py**: `query_site_telemetry` reads keys the telemetry never
  has and fabricates a 2.4 s TTC; `adjudicate_incident_packet` invents the
  kinematics and the video URI and defaults to `TRUE_POSITIVE` at 0.94 confidence;
  `compile_work_permit` raised `NameError` on the validation-failure path because
  `manifest_dir` was imported inside the `if manifest:` block. **Fixed, verified —
  and it was a regression I introduced the same day.**
- **adjudication_pipeline.py**: the fallback fabricates the missing inputs
  (`min_ttc = telemetry.get("min_ttc", 1.8)`) and the certainty of its verdict
  (`confidence=0.95`); the "multimodal" adjudicator is never given the video its
  own prompt asks about.
- **bim_resolver.py**: legitimate `0.0` IFC axis values are treated as missing,
  producing a 45 deg rotation with sqrt(2) magnification; the output is named
  EPSG:3857 with no reprojection anywhere.
- **safety_event.py**: risk that was never computed is persisted as
  `NORMAL_LEVEL_0` / `min_ttc_seconds = -1.0` — no UNKNOWN state exists;
  `build_event` defaults `detection_status` to `MEASURED`.
- **dashboard/media.js**: the restored-assets panel prints the server's normalized
  presentation geometry and frame count as the original file's own measured values.
- **tests**: the only test runner never executes the browser suites (`run_tests.py`
  discovers `test_*.py` only, so 65 browser tests are skipped by the documented
  procedure); two pipeline tests self-disable when the builder returns `None`; two
  "worker is outside the hull" tests assert arithmetic on constants declared in the
  test file.
- **deployment**: the documented read-only recipe mounts a root-owned `/state`, so
  the uid-10001 container cannot persist anything — **reproduced independently**;
  `Dockerfile.edge` and the systemd unit run `python3 -m src.edge.edge_runtime`,
  which has no `__main__`, so both exit instantly.
- **scripts/sprint_validation.py**: the docstring's own invocation omits `--strict`,
  so a report can mark a card PASS from inadmissible evidence with no violation
  recorded.

## Caveats on this audit

- The verifier was given only the CRITICAL and HIGH findings, so the 112 MEDIUM and
  LOW findings are unverified. Treat them as leads.
- One CRITICAL finding (#3) was overstated, as shown above. The same may be true of
  others: the verifiers were as fallible as the auditors.
- The agents were barred from running the test suite or importing torch, so nothing
  here rests on executed model behaviour. Everything is read from source or from
  small standard-library probes, and each finding carries its own location.
