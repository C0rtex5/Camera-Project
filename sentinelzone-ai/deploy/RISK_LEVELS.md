# Risk levels and the zone rules

This documents the risk levels and the conditions behind them (project cards
SEC-05 proximity rules and SEC-06 risk levels). The structured event these rules
produce is specified in `deploy/SAFETY_EVENT_CONTRACT.md`.

> Advisory demonstration. These are not a certified protection system and the
> thresholds below have not been validated against a plant risk assessment.

## Business states and engine levels

The site vocabulary has three states. The engine keeps four severity levels, so
the mapping is explicit:

| Business state | Engine level | Meaning |
|---|---|---|
| `SAFE` | `NORMAL_LEVEL_0` | No interaction at risk |
| `ATTENTION` | `ADVISORY_LEVEL_1` | Proximity developing, below the warning horizon |
| `RISK` | `WARNING_LEVEL_2` | Collision predicted inside the warning horizon |
| `RISK` | `CRITICAL_LEVEL_3` | Collision predicted inside the critical horizon |

`WARNING` and `CRITICAL` share the `RISK` business state. A consumer that
collapses them loses the distinction the operator needs, so events retain
`risk_level` alongside `business_state`.

## Proximity criterion

For each tracked worker and each tracked machine, once per frame:

1. **Collision radius** `r_col = max(min_separation_distance, r_worker + r_machine)`,
   with `r_worker = 0.8 m` and `r_machine = 2.5 m`.
2. If the worker's ground footpoint is inside a zone envelope, `r_col` is raised
   to at least that envelope's `min_separation_distance`, and the envelope's
   `ttc_multiplier` scales the TTC thresholds. An exempt entity inside the zone
   (for example a `SPOTTER`) returns `NORMAL_LEVEL_0` immediately.
3. Each forecast mode pair is compared at every forecast instant. The joint mode
   mass of all colliding outcomes is accumulated, because summing only the
   largest mode pair understates risk when the model splits mass across similar
   paths.
4. Probability and time-to-collision must refer to the **same** forecast
   instant, so a tiny early mode cannot combine with an unrelated late peak.

Defaults: `warning_ttc` 2.5 s, `critical_ttc` 1.5 s, `prob_threshold` 0.65,
`min_separation_distance` 0.0, `ttc_multiplier` 1.0.

## Conditions

Levels are tested most severe first:

| Level | Condition |
|---|---|
| `CRITICAL_LEVEL_3` | colliding mass >= `prob_threshold` (0.65) within `critical_ttc x ttc_multiplier` |
| `WARNING_LEVEL_2` | colliding mass >= `0.6 x prob_threshold` (0.39) within `warning_ttc x ttc_multiplier` |
| `ADVISORY_LEVEL_1` | colliding mass >= 0.20 within 5.0 s |
| `NORMAL_LEVEL_0` | none of the above |

`min_ttc` is the time of the first breaching forecast instant, or `-1.0` when
clear.

## Hysteresis

A single noisy frame must not flip the state, so transitions are debounced:
**3 frames to escalate, 15 frames to de-escalate**. Escalation is fast because
an operator needs the warning immediately; de-escalation is slow so the alert
does not flap as a pair separates.

## Zone rules

* A zone is a ground-plane polygon in metric coordinates, from the signed shift
  manifest (`data/manifests/active_manifest.json`) or, for the scenario demos,
  from the scenario configuration.
* Entry, continued presence and exit are tracked per (zone, agent) pair in
  `src/edge/zone_occupancy.py`. The risk engine itself is stateless per frame,
  so without that tracker an entry cannot be told from steady occupancy.
* Continued presence is reported on a 1 s cadence, so the rate of presence
  events measures time inside the zone rather than the frame rate.
* An exit reports `dwell_seconds` and the highest level reached while inside.
  A track lost for more than 3 s is closed as `track_lost`, so occupancy cannot
  latch when a stream stalls. A one-frame detector dropout is tolerated.
* Zone geometry is projected into the camera image with the inverse homography
  (`HomographyProjector.metric_polygon_to_pixels`). A zone that is not visible
  in the frame is not reported, so the panel never draws a zone the camera
  cannot see.

## What is not validated

The thresholds above are engineering defaults, not site-validated values. A
plant risk assessment must set `warning_ttc`, `critical_ttc`, `prob_threshold`,
`min_separation_distance` and the envelope geometry, and the measured
false-alarm and detection rates on plant footage. Until that happens the levels
demonstrate the mechanism only.
