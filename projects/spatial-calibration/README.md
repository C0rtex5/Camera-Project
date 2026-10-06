# Spatial Calibration & Geodetic Projection

> Sub-project `spatial-calibration` · status **extraction-ready** · cards **SEC-01, SEC-04, SEC-10**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

The product's central claim is metric: distances, time-to-collision and hazard-zone membership are
meaningless unless pixels are tied to a surveyed ground plane. This sub-project owns that tie — the
homography projector, the calibration fit/verification tool and the BIM/IFC resolver that positions
hazard geometry in the digital twin.

## Owned paths

| Path | Contents |
|---|---|
| `src/edge/homography.py` | `HomographyProjector`: native pixels → metric ground coordinates |
| `src/edge/calibration.py` | `fit_ground_plane`: least-squares fit over surveyed correspondences with RMSE |
| `src/spatial/bim_resolver.py` | `BIMSpatialResolver`: BIM/IFC entity → geodetic boundary resolution |
| `scripts/calibrate_camera.py` | Operator tool that turns measured point pairs into a camera profile |
| `config/calibration_survey.example.json`, `config/cameras.example.json` | Example survey and camera inventory inputs |
| `deploy/CAMERA_CALIBRATION.md` | Calibration procedure and tolerances |
| `tests/test_homography.py`, `tests/test_camera_calibration.py` | Unit tests for this project |

## Interfaces it publishes

- **`HomographyProjector.project` / `project_box`** — metric projection plus a calibration-error
  estimate; no silent extrapolation outside the surveyed region. Consumed by `hazard-tracking`,
  `media-pipeline`, `edge-runtime`.
- **`fit_ground_plane`** — ≥ 4 surveyed correspondences in, homography + residual RMSE out;
  degenerate configurations are rejected. Consumed by `camera-onboarding`, `validation-harness`.
- **`BIMSpatialResolver.resolve`** — BIM entity → geodetic boundaries for dynamic exclusion
  envelopes. Consumed by `agentic-supervisor`, `api-gateway`.

## Dependencies

- **Depends on:** nothing (leaf project).
- **Depended on by:** `hazard-tracking`, `media-pipeline`, `edge-runtime`, `trajectory-forecasting`,
  `camera-onboarding`, `agentic-supervisor`, `api-gateway`, `validation-harness`.

## How to run it

```bash
python scripts/run_project_tests.py spatial-calibration
python scripts/calibrate_camera.py --help
```

## Acceptance criteria

1. A surveyed 4+ point calibration yields a homography and an explicit RMSE.
2. A known image marker projects within the calibrated tolerance on the ground plane.
3. BIM resolution degrades to a labelled unavailable state when `ifcopenshell` is absent.
4. Calibration artefacts are bound to a camera identity; a mismatched profile cannot be used silently.

## Risks and open edges

- **Largest accuracy risk in the product.** Every metric downstream inherits the calibration error.
- **Example configuration ships by default**; without surveyed points the digital twin is
  decorative, and the runtime must say so rather than implying metric accuracy.
- `src/edge/homography.py` and `src/edge/calibration.py` sit in the flat `src/edge` package shared
  with four other sub-projects; the physical extraction moves them into this project's own package
  (see [../MIGRATION_PLAN.md](../MIGRATION_PLAN.md)).
