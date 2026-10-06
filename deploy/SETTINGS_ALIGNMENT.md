# Original settings and how they are aligned

This records the original application's settings, the three deliberate
deviations from the original repository, and the two inconsistencies the
original repository carries that had to be handled in code.

The original repository is `https://github.com/Free-devloper/SentinelZone-AI`
at commit `bc8e2c88d376575503e77351f1b123b13c547535`, which is also this
repository's `HEAD`. "Original" below means that commit.

## Original settings, unchanged

These files are **byte-for-byte identical to the original repository**. They
are asserted as such by `tests/test_settings_alignment.py`, so a change to any
of them fails the suite.

| Setting | Original value |
|---|---|
| `config/default_config.json` `camera_id` | `CAM-02-MAST` |
| `config/default_config.json` `site_id` | `SITE-5-EARTHMOVING` |
| `config/default_config.json` `homography.K` | principal point `(960, 540)` for 1920x1080 |
| `config/default_config.json` `homography.H` | `[[72.825, 25.245, 640.0], [0.0, -2.612, 760.448], [0.0, 0.0394, 1.0]]` |
| `data/manifests/active_manifest.json` | `SHIFT_2026-09-07_SITE-EAST`, site `SITE-EAST` |
| The four other shift manifests | `SITE-01`, `SITE_5`, `SITE-EAST`, `SITE-NORTH` |
| Risk thresholds | `warning_ttc` 2.5 s, `critical_ttc` 1.5 s, `prob_threshold` 0.65, `min_separation_distance` 0.0 |
| `data/roboflow_downloaded/data.yaml` | unchanged |
| The five sample incidents in `data/incidents/` | unchanged |

## The three deliberate deviations

Each is a difference from the original that the current application requires.
None of them changes a calibration, a threshold or a zone definition.

1. **`data/construction_safety/data.yaml`** - `path:` was the absolute Windows
   path `D:/Projects/Python/Sentinel_ai/data/construction_safety`, now `.`. The
   absolute path does not resolve on this host, so the dataset could not be
   loaded at all. The relative path is the only portable form.
2. **`pyproject.toml`** - dependencies moved to `dynamic = ["dependencies"]` with
   `requirements-runtime.txt` / `requirements-runtime.lock` as the source. This is
   the pinned runtime documented for the dependency record (SEC-02.4). No
   dependency was added, removed or unpinned.
3. **The two 4K recordings** deleted at the project owner's instruction, each
   recorded in `data/original_assets_manifest.json` under `removed_objects` with
   its upstream SHA-256, size, reason and restore command.

## Inconsistency 1: the site name (fixed in code)

`config/default_config.json` declares `SITE-5-EARTHMOVING`, but
`data/manifests/active_manifest.json` is the `SITE-EAST` shift, and the
`SITE_5` manifest is spelled with an underscore. The original live path loaded
envelopes **only** on an exact string match:

```python
if manifest.site_id == config.site_id:   # never true in the original repository
    runtime.conflict_engine.load_manifest_envelopes(...)
```

so a live camera silently received **zero** hazard zones - no zone overlay, no
entry/exit events, no zone risk adjustment.

The settings are left exactly as the original repository has them and the lookup
was made tolerant instead. `src/edge/manifest_selector.py` resolves a manifest by
exact site, then by a normalised site (`SITE-5` and `SITE_5` compare equal), then
falls back to the active manifest, and reports which of those matched. The
runtime adopts the **manifest's** `site_id` so a safety event always reports the
same site as the zone it refers to, and `site_identity()` is published on the
frame packet:

```json
{"site_id": "SITE-EAST", "manifest_site_id": "SITE-EAST",
 "manifest_shift_id": "SHIFT_2026-09-07_SITE-EAST",
 "manifest_match": "active_fallback", "zone_source": "SITE_MANIFEST"}
```

Without this, an event could claim `site_id: SITE-5-EARTHMOVING` while its
`zone_id` came from the `SITE-EAST` manifest - an internally inconsistent
record, which the event contract forbids.

## Inconsistency 2: the zone geometry (needs site data, not code)

All five original shift manifests define their envelope at metric
**x[100, 125], y[50, 70]**. The original `CAM-02-MAST` homography sees
approximately metric x[-5, 3], y[0, 10] within a 1920x1080 frame. The manifest
zones therefore project to image x[2578, 3706] - **beyond the right edge of the
frame**.

Consequences, which are correct rather than broken:

* No zone is drawn on the camera image. The projector returns "no overlay" for a
  zone it cannot see, instead of clamping it somewhere it does not belong.
* Metric-space behaviour is unaffected: zone occupancy, entry/continued
  presence/exit events, the envelope separation and TTC multiplier, and the 3D
  twin all work, because those are ground-plane computations and do not require
  the zone to be in view.

This cannot be fixed in code. It needs the real geometry for the real site: the
measured calibration for the actual camera, and the actual zone coordinates for
the actual hazard. Until then the manifest zones are placeholders in a
coordinate space that does not match the example calibration - which is why
`deploy/SAFETY_EVENT_CONTRACT.md` marks scenario and recorded events
`ILLUSTRATIVE` and only a measured live camera as `MEASURED`.

## What this means for the sprint validation

Both inconsistencies are recorded as findings rather than silently patched. A
live camera with a real plant manifest and a measured homography resolves
inconsistency 1 automatically; inconsistency 2 disappears once the site supplies
real geometry. Neither should be closed by a code change that makes a test pass.
