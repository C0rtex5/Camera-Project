# Safety event contract

This is the contract the project cards require (SEC-07: create the event
structure, record the source camera, the zone or machine, the date and time, the
risk level and the event type; test automatic generation; validate the format;
document the contract).

Events are produced **automatically** by the edge runtime on the frame that
caused them. Nothing in the application calls a builder by hand: the runtime
emits an event when a tracked agent's zone occupancy changes, or when the
debounced risk state changes. Consumers read the same record whether it comes
from the JSONL sink or the WebSocket broadcast.

> This is an advisory demonstration output. It is not a certified safety control
> and must not be presented as one. Every record carries `advisory_only: true`.

## Where events go

| Sink | Location | Purpose |
|---|---|---|
| Durable record | `data/incidents/events-YYYYMMDD.jsonl`, one JSON object per line | Greppable history, replay, adjudication |
| Live broadcast | WebSocket, same payload as the frame packets | Panel and future digital-twin consumers |
| Recent in memory | last 200 events | `safety_events` on the frame packet |

The file sink is best-effort: a read-only filesystem never takes the inference
loop down, and a failing subscriber never loses the event. Override the
directory with `SENTINEL_EVENT_DIR`.

## Contract version

`sentinel.safety_event/1.0`, carried in `schema_version` on every record. A
consumer must reject a version it does not understand rather than guess.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `event_id` | string | Unique identifier for this event |
| `schema_version` | string | `sentinel.safety_event/1.0` |
| `event_type` | enum | `ZONE_ENTRY`, `ZONE_EXIT`, `ZONE_OCCUPANCY`, `RISK_ESCALATION`, `RISK_DEESCALATION`, `CAMERA_OFFLINE` |
| `source_camera_id` | string | **Source camera.** `CAM-07` on a site, `RECORDED::<media id>` for a recording |
| `site_id` | string | Site identifier from the camera profile or the shift manifest |
| `zone_id` | string | **Zone.** Envelope identifier, empty when the event is not zone-related |
| `zone_name` | string | Operator-facing zone name |
| `machine_id` | string | **Machine.** The machine the zone belongs to, empty when unknown |
| `event_timestamp_utc` | string | When the event was raised, ISO-8601 UTC |
| `frame_id` | string | Frame the event was observed on, linking back to the frame packet |
| `frame_captured_at_utc` | string | Capture time of that frame, ISO-8601 UTC |
| `risk_level` | enum | Engine severity: all four levels retained |
| `business_state` | enum | **Risk level as the site sees it:** `SAFE`, `ATTENTION`, `RISK` |
| `min_ttc_seconds` | float | Time to collision in the forecast; `-1.0` when clear |
| `collision_probability` | float | Peak mode-mass collision probability, 0..1 |
| `detection_status` | enum | `MEASURED`, `ILLUSTRATIVE`, `UNAVAILABLE` |
| `detected_class` | string | Internal class of the driving detection, e.g. `WORKER` |
| `track_id` | int \| null | Track identifier of the driving detection |
| `dwell_seconds` | float | Time spent in the zone before an exit |
| `peak_risk_level` | string | Highest level reached during the occupancy period |
| `forecast_status` | string | Forecast availability on the source frame |
| `advisory_only` | bool | Always `true`; a PoC event is not a certified control |

## Risk level and business state

The engine has four severity levels. The cards name three business states, so
the mapping is declared once, here, and implemented in
`src/schemas/safety_event.py`:

| `risk_level` | Meaning | `business_state` |
|---|---|---|
| `NORMAL_LEVEL_0` | No interaction at risk | `SAFE` |
| `ADVISORY_LEVEL_1` | Proximity developing, below the warning horizon | `ATTENTION` |
| `WARNING_LEVEL_2` | Collision predicted inside the warning horizon | `RISK` |
| `CRITICAL_LEVEL_3` | Collision predicted inside the critical horizon | `RISK` |

`WARNING` and `CRITICAL` both map to `RISK`: the business vocabulary has three
values, so the two escalation levels collapse into it. Consumers that need the
four-level detail read `risk_level`; consumers that use the site vocabulary read
`business_state`. An unknown level maps to `SAFE` rather than inventing a risk.

## Conditions for each state

`business_state` follows `risk_level`, which is selected in
`src/edge/conflict_engine.py` per pair per frame, with the zone multiplier
applied:

| State | Condition |
|---|---|
| `SAFE` | Every pair is clear, or the agent is an exempt entity inside the zone, or no colliding mode mass is predicted within the advisory horizon |
| `ATTENTION` | Colliding mode mass >= 0.20 within 5.0 s |
| `RISK` | Colliding mode mass >= `0.6 x prob_threshold` within `warning_ttc x ttc_multiplier`, or >= `prob_threshold` within `critical_ttc x ttc_multiplier` |

Defaults: `warning_ttc` 2.5 s, `critical_ttc` 1.5 s, `prob_threshold` 0.65.
Transitions are debounced: 3 frames to escalate, 15 frames to de-escalate, so a
single noisy frame cannot flip the state.

## Event types and when they fire

| Event | Fires when |
|---|---|
| `ZONE_ENTRY` | An agent's ground footpoint first comes inside a zone polygon |
| `ZONE_OCCUPANCY` | The agent is still inside, on a 1 s cadence. This is the observable evidence of continued presence |
| `ZONE_EXIT` | The footpoint leaves the polygon, with `dwell_seconds` and the peak level reached. `reason` is `left_polygon` or `track_lost` |
| `RISK_ESCALATION` | The debounced level rises |
| `RISK_DEESCALATION` | The debounced level falls, including the return to `SAFE` |

A lost track closes occupancy after 3 s (`track_lost`), so occupancy can never
latch forever when a stream stalls. A one-frame detector dropout is tolerated
and is not reported as an exit.

## Evidence honesty

`detection_status` is not decoration:

* `MEASURED` - produced by the detector on a live camera with a measured
  homography and a signed site manifest.
* `ILLUSTRATIVE` - produced from a scenario configuration, a recorded clip, or
  an example calibration. The zone overlay on a restored recording is drawn
  from an uncalibrated example projection and is labelled as such on the image
  itself; events for it are `ILLUSTRATIVE`.
* `UNAVAILABLE` - no detection supported the event.

## Example

```json
{
  "event_id": "0f1c9a2e...",
  "schema_version": "sentinel.safety_event/1.0",
  "event_type": "ZONE_ENTRY",
  "source_camera_id": "CAM-07",
  "site_id": "SITE-EAST",
  "zone_id": "ENV_SITE-EAST_Trench_excavation",
  "zone_name": "Trench Excavation",
  "machine_id": "MACHINE-EXCAVATOR-07",
  "event_timestamp_utc": "2026-09-29T12:00:03.412000+00:00",
  "frame_id": "a1b2c3",
  "frame_captured_at_utc": "2026-09-29T12:00:03.380000+00:00",
  "risk_level": "WARNING_LEVEL_2",
  "business_state": "RISK",
  "min_ttc_seconds": 1.2,
  "collision_probability": 0.74,
  "detection_status": "MEASURED",
  "detected_class": "WORKER",
  "track_id": 11,
  "dwell_seconds": 0.0,
  "peak_risk_level": "WARNING_LEVEL_2",
  "forecast_status": "AVAILABLE",
  "advisory_only": true
}
```

## Validating a consumer

Reject a record when `schema_version` is unknown, when `business_state` does not
match the declared mapping for `risk_level`, or when `advisory_only` is absent
or false. `risk_level` alone is not enough for a site-facing display: two
distinct levels share the `RISK` business state, and collapsing them in the
event loses the distinction the operator needs.

## Worker-to-machinery proximity and PPE compliance on restored recordings

Added 2026-09-29. Both travel on every `worker_states` entry, which the camera panel renders on the worker's own box.

### Proximity

Each visible worker is graded against the nearest visible `HEAVY_EQUIPMENT` track on the same fitted ground plane.

| Grade | Distance | Meaning |
|---|---|---|
| `DANGER` | ≤ 3.3 m | Footprints already overlap |
| `NEAR` | ≤ 8.0 m | Outside the collision radius, inside the warning band |
| `SAFE` | > 8.0 m | Clear of the machinery |
| `NO_MACHINE` | no machinery in frame | Not graded |

3.3 m is the collision radius the engine would enforce on the same pair: it is `WORKER_FOOTPRINT_METRES (0.8) + MACHINERY_FOOTPRINT_METRES (2.5)`, the same footprint radii [src/edge/edge_runtime.py](../../../src/edge/edge_runtime.py) sets per agent and the same sum [src/edge/conflict_engine.py](../../../src/edge/conflict_engine.py) forms as `r_col`. A test reads that expression out of the engine so the two cannot drift apart. **8.0 m is an engineering default, not a validated site figure**, and is recorded as such in `deploy/VALIDATION.md`.

`NO_MACHINE` is deliberately distinct from `SAFE`: the absence of a machine in frame is not evidence that the worker is safe, so it is never graded as safe. The field is `nearest_machinery_metres: null` in that case, never a fabricated distance.

### PPE compliance

`packet.ppe` carries one record per worker, mapped back to native frame coordinates from the same inference call that produced the boxes, so it costs no extra inference and rides the detection cache.

Each record is `{bbox, hardhat, vest, measured, ppe_compliant}`.

`measured` is the field that matters for honesty. On restored recordings the detector is COCO-80, which has **no hardhat or vest class**, so compliance is judged by the original repository's own HSV rule: hardhat colour bands on the head crop (top 24 % of the box) and high-visibility or reflective coverage on the torso crop (18–58 %). A worker box below the 20 × 10 px crop threshold is reported `measured: false` and the panel renders ` HARDHAT: UNKNOWN` / ` VEST: UNKNOWN`. **A hardhat that was never measured is never reported as missing.** This is a colour estimate, not learned PPE detection, and it is unvalidated on this data.

### Separation

A worker whose box is more than 60 % inside a machinery box is the machine's operator, which is a real person and stays a tracked worker with its own track id, PPE notes and proximity grade. Nesting is used only to keep the two legible: the worker's box is drawn thinner and dashed, and its note chip moves clear of the machine's. In the 3D canvas the machinery hull and cab are made see-through and worker groups take a higher `renderOrder`. No geometry was added or changed, and no detection is ever dropped because of it.

## 3D pose, machinery anchoring, and the calibrated trench — 2026-09-29

### The trench polygon is calibrated, not fixed

The trench keeps the original repository's shape and lateral placement exactly: 20 m across, `x[-8, 12]`, `zone_id` and `zone_name` unchanged, from the original twin's `PlaneGeometry(20, 7)`.

Its **depth band** is now fitted per recording. The original fixed band `y[3, 10]` sat behind the work on every restored clip: agents are measured at 1.4–9.8 m, so it contained **6 of 32** observed workers. The band keeps the original 7 m depth but moves onto the fitted plane's near working band, `y[1.5, 8.5]`, which contains **21–23 of 30–32**.

**What `IN ZONE` now means, stated plainly:** the agent is inside the fitted working area of a fitted plane. It is **not** a surveyed hazard boundary, and its value as a hazard discriminator is unvalidated. No site survey defines where the real hazard is, so the shape is the original's and the placement is derived, not measured on site.

The drawn polygon is positioned and scaled from `packet.zone` in the twin's update loop, so the rectangle drawn on the canvas and the rectangle the occupancy test uses are the same object. A test asserts the mesh centre and extent follow the packet. This removes the class of bug where the drawn rectangle and the tested rectangle silently diverge.

### The rendered pose is gated on motion

A stationary agent's heading is computed from the pixel-level jitter of its footpoint, and on parked plant that swung the hull through **286 degrees** of measured heading across the blind-spot clip. The twin now applies the measured heading to the mesh **only while the agent is moving** (`track.moving`, the existing 0.25 m/s windowed-speed flag); a stationary agent holds the angle it last had, and a first-seen stationary agent gets one stable angle rather than a noise-derived one.

**The measurement itself is unchanged** and still travels on `tracks_3d` as `heading`; only the rendered pose is gated. Tests drive the real update loop and assert one distinct rotation across twelve frames of jittering input, and five distinct rotations while moving.

### The machinery hull is anchored off its own footpoint

The 2D box footpoint is the machine's **near-bottom edge**, not its centre. A 5.0 m hull centred there extended 2.5 m toward the camera, over the ground a worker walks on, so a worker standing beside the machine was inside the mesh. The hull is now pushed forward by half its length to local `(0, 2.5, 0.9)`, occupying the ground behind the machine and leaving its footpoint and the ground in front of it free.

**The worker is never moved off its true metric position.** The separation is entirely on the machine's side, because relocating the person would fabricate a position. The reported proximity distance is unchanged and remains the measured value.

## Overlay annotation layout — 2026-09-30

Detection labels and worker note chips are placed by a packer rather than at a fixed offset from their box. Each panel claims a rectangle; the next one walks away from its preferred slot in both directions, then sweeps a coarse grid in both axes, and only falls back to overlapping when the drawn image is genuinely full. Every placed panel is clamped inside the image.

Measured on the two clips in the review, replayed against live packets with the real packer, 86 frames, 228 labels and 192 chips:

| Collision | Before | After |
|---|---|---|
| label on label | 55 | 0 |
| note chip on note chip | 34 | 0 |
| label over a note chip | 6 | 0 |
| panel clipped off the image | 131 | 0 |

Clamping is why panels that previously ran off the bottom or left edge now fold back inside; the label and the notes stay readable at the frame borders.

The zone event log now renders **one entry per line** (`white-space: pre-line`) instead of four entries joined into a single 145-character line, which the browser clipped so the oldest events were unreadable. Entries also carry their `reason` where present, so a `track_lost` exit is distinguishable from a worker who actually walked out.

## Ground-plane calibration, footprint polygons and ghost suppression — 2026-10-01

### The ground plane is anchored on the recording, not on an assumed band

The plane previously assumed the frame's near row was 1.5 m away, which solved to a **camera height of 0.84 m — below standing eye level** — and produced people **0.17 m wide**. These are mast cameras.

The plane is now fitted from an observed person. A 2D box's pixel width is a real ground-plane measurement at that depth, so a person of nominal shoulder width `NOMINAL_SHOULDER_METRES` (0.5 m) spanning `w` pixels stands at `0.5 * focal / w` metres. With the far band at 30 m, both unknowns are determined:

```
k        = 0.5 / w_px
h        = k * (row_obs - far_row) / (1 - k * focal / 30)
horizon  = far_row - h * focal / 30
```

Measured camera heights are now **2.6–5.4 m**, and person widths **0.46–0.83 m**, machine widths **2.6–5.0 m**.

The fit is a **property of the recording, not of the playhead**: a fixed ladder of frame indices is scanned once and cached per `(media, size, mtime)`, so scrubbing or jumping cannot move the ground under a stored snapshot. A solution outside 1.2–15 m, or an observation narrower than 20 px, falls back to the band fit.

**`NOMINAL_SHOULDER_METRES` is a reference dimension, not a site survey.** Every distance, the proximity grades and the zone band inherit that assumption.

### The zone band follows the new scale

Shape and lateral placement are still the original repository's (20 m wide, `x[-8, 12]`, 7 m deep). The depth band moved to `y[4, 11]`, where the workers now stand on the calibrated plane, containing **144 of 161** observed workers against 124 on the previous band.

### Footprint polygons are measured

`tracks_3d` now carries `footprint: {width_m, depth_m}` per agent, derived from the detection's pixel width at that agent's own depth. The 3D polygons use it directly:

- **Person**: a straight-edged `PlaneGeometry` footprint — the 3D equivalent of the person's detection frame — sized from the measurement and turned with the group to the walking direction. It replaces the fixed 1.6 m circle. The original cylinder post is kept as the direction indicator.
- **Machinery**: the original hull and cab as unit boxes scaled to the measurement, keeping the original 2.6:5.0:1.8 proportion. The hull is pushed forward by **half its own measured length** so it never covers the ground its footpoint stands on.

### Trails are per agent

There is one trail line per track, in the agent's class colour. Previously two shared lines existed and the last-drawn agent overwrote everyone's path, so only one person ever had a trail.

### Ghost detection

Two artefacts were measured and removed:

- **Fragmented plant.** One excavator reported as its body, its boom and its tracks became three agents. Same-class boxes overlapping more than 35 % of the smaller are merged into the larger, more confident one.
- **The cab operator.** A worker box almost entirely inside a machine. The person is **not deleted** — the box stays on the camera frame with its hardhat and vest notes — but the row is marked `role = "operator"` and the tracker skips it, so one human being is not two agents and their distance to the machine they are sitting in is not reported as a near-miss.

A served detection is now exactly `[x1,y1,x2,y2,confidence,class,track_id,role]`, both trailing columns always present so a client's indices cannot shift. A worker nested more than 80 % inside a machine and not so marked: **0** across 206 frames.
