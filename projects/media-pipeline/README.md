# Restored Media, Tracks & Ghost Suppression

> Sub-project `media-pipeline` · status **extraction-ready** · cards **SEC-02, SEC-09, SEC-10**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

The platform must be demonstrable and reviewable without a camera. This project serves and analyses
the restored original recordings and dataset splits: a read-only catalog, calibrated detection
overlay, metric ground tracks, smooth playback, and the ghost-suppression rule that guarantees two
served frames never describe one object twice.

## Owned paths

| Path | Contents |
|---|---|
| `src/api/media.py` | Read-only catalog of restored videos and dataset splits (ids, checksums, probing) |
| `src/api/media_detection.py` | Calibrated person/machinery detection cache for recordings |
| `src/api/media_tracks.py` | Ground-plane fit, metric tracks, zone membership for recordings |
| `src/api/detection_ghosts.py` | `suppress_ghosts`: one served detection per real object |
| `src/demo_assets/**` | Bundled offline demo scenarios (barrier, blind_spot, near_miss + manifests) |
| `dam_sample_0.jpg`, `dam_sample_1.jpg` | Sample frames from the restored site imagery |
| `tests/test_media_catalog.py`, `test_media_tracks_ground.py`, `test_ghost_frames.py` | Catalog, ground-track and ghost invariants |
| `tests/test_calibration_and_ghosts.py`, `test_playback_flow.py`, `test_3d_pose_and_zone_calibration.py`, `test_ppe_proximity_and_separation.py` | Calibration, playback, twin-geometry and PPE/proximity behaviour |

## Interfaces it publishes

- **Read-only media catalog** (`media_roots`, `source_index_for_output`) — stable ids and checksums;
  never mutates the underlying files. Consumed by `api-gateway`, `validation-harness`.
- **Calibrated detection cache** (`detect_for_media`, `cached_detection`) — native-frame boxes with a
  bounded cache and an explicit disabled state when weights are absent. Consumed by `api-gateway`,
  `dashboard-ui`.
- **Metric ground tracks + ground plane** (`plane_for_media`, `tracker_for`) — shaped for the
  digital-twin packet. Consumed by `api-gateway`, `dashboard-ui`.
- **Ghost suppression contract** (`suppress_ghosts`) — at most one served detection per real object
  per frame, operator rows preserved. Consumed by `api-gateway`.

## Dependencies

- **Depends on:** `perception-detection` (detector, frame geometry), `spatial-calibration`
  (ground-plane projection).
- **Depended on by:** `api-gateway`, `validation-harness`, `deployment-runtime` (demo-media image).

## How to run it

```bash
python scripts/run_project_tests.py media-pipeline
```

## Acceptance criteria

1. The catalog lists every restored video and dataset split with no camera present.
2. Ghost suppression holds under the randomised invariant test and at threshold boundaries.
3. A ground plane fitted from a recording yields metric tracks consistent with the calibrated band.
4. Playback stays smooth at the documented presentation format (1080p / 30 fps).

## Risks and open edges

- **Demo evidence is not site validation.** The validation policy forbids closing physical-site
  items with recorded evidence; this project supplies `DEMO/RECORDING` class evidence only.
- **Large tracked JPEG assets** (243 demo frames plus root samples) dominate repository size; the
  extraction step should move them to LFS or a release artifact.
- **Git LFS dependency:** a shallow clone without LFS leaves pointer files; the catalog must report
  them as unavailable rather than serving broken media.
