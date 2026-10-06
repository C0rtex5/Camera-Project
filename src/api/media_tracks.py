"""Metric ground tracks for restored recordings.

The original application's twin consumed ``tracks_3d`` entries carrying a metric
``position`` and a ``class_name``, and placed every agent on one shared metric
ground plane. Restored recordings sent no tracks at all, so the twin had nothing
to draw, and the original example calibration could not place the foreground
worker: its horizon sits at row 760 of a 1080-row frame, so the bottom third of
the frame is *behind* the camera plane and a footpoint there projects to negative
depth. Rescaling that matrix cannot repair a negative depth, so the ground plane
is fitted per recording.

The fit reuses the original camera optics - the focal length and principal point
from ``config/default_config.json`` - and fits only the horizon and the camera
height, so the projection keeps the original camera's geometry:

* horizon at row ``HORIZON_ROW_FRACTION * height``
* depth ``NEAR_METRES`` at row ``NEAR_ROW_FRACTION * height``
* depth ``FAR_METRES`` at row ``FAR_ROW_FRACTION * height``

Those three constraints have one solution, which is what :func:`fit_ground_plane`
computes. Detections whose footpoint falls above the fitted horizon have no
valid ground position - distant machinery legitimately does - so the row is
clamped into the usable band rather than the detection being dropped.
"""
from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# Ground band: the near row sees NEAR_METRES, the far row sees FAR_METRES.
NEAR_ROW_FRACTION = 0.95
FAR_ROW_FRACTION = 0.25
#: Nominal adult shoulder width, used as the scale anchor when the plane is
#: fitted from the recording. A 2D box's pixel width is a real ground-plane
#: measurement at that depth, so an observed person of this width gives the
#: camera height and horizon. This is a documented reference dimension, not a
#: site survey, and every distance derived from it inherits that.
NOMINAL_SHOULDER_METRES = 0.5
#: A fitted camera height outside this range means the observation was unusable
#: (a fragment box, a partial occlusion, a person at a unusual distance) and the
#: band-based fallback is used instead.
MIN_PLAUSIBLE_CAMERA_HEIGHT_M = 1.2
MAX_PLAUSIBLE_CAMERA_HEIGHT_M = 15.0
#: Boxes narrower than this are machine parts or fragments, not people.
MIN_PERSON_BOX_PX = 20
#: A `WORKER` box shorter than this, in the 1080-height frames every clip is
#: normalised to, is a fragment of a person - a hardhat, a hi-vis panel, a body
#: cut off by the frame - and not a whole person. A fragment has no ground
#: contact to stand on: put through the plane it became a 0.25 m polygon lying
#: at an arbitrary depth, and the canvas filled with small plates that read as
#: scattered debris. The box itself stays on the camera frame with its PPE
#: notes, exactly as an operator's does; it simply does not become an agent.
#:
#: This is a fragment floor, not a person detector. It is set at the widest
#: boundary that is unambiguous: on the restored corpus the boxes below it run
#: 24x56 to 28x64 px, while anything between 70 and 150 px is a real person
#: crouching, seated or partly occluded and is kept.
MIN_PERSON_BOX_HEIGHT_PX = 70
#: Plausible bounds on a measured footprint. A box narrower or wider than this
#: is a fragment or a merged pair of objects, not one agent, and is clamped so
#: the 3D polygon stays a sane size.
#: Narrower than this measured, a person-class box is a fragment rather than a
#: person. Shoulder width is roughly constant however a person is turned, so
#: 0.35 m is about 70% of NOMINAL_SHOULDER_METRES - below it there is no whole
#: body behind the box. Clamping such a box *up* to a minimum would make it
#: bigger, which is how fragments ended up as small plates scattered across the
#: canvas; they are dropped from the ground plane instead, and their box stays
#: on the camera frame.
MIN_PLAUSIBLE_PERSON_WIDTH_M = 0.35
#: How far ahead the walk is projected, and in how many steps. The original twin
#: drew two lines per agent: the history behind and the forecast ahead. The
#: history was served but the forecast was left empty, so the "path" the panel
#: legend promises was never drawn.
FORECAST_HORIZON_SECONDS = 3.0
FORECAST_POINTS = 6
#: The path is projected from the net displacement over this many history points,
#: not from the instantaneous velocity. Per-frame velocity is noisy - on the 30sec
#: clip it reached 10 m/s for a worker whose smoothed speed was 3.2 - and a path
#: drawn from it shot 27-51 m across the view for a 3 s horizon.
FORECAST_WINDOW = 15
#: A person jogging, and a tracked excavator travelling. The projection is capped
#: here so a single mis-associated frame cannot fling the path off the screen.
MAX_FORECAST_SPEED_MPS = 2.5
MAX_FORECAST_SPEED_MPS_MACHINERY = 12.0
MIN_PERSON_FOOTPRINT_M = 0.25
#: A person is about 0.5 m across the shoulders, and 0.75 m is a person in bulky
#: gear or two people whose boxes merged. The previous 1.30 m cap let a near-field
#: box draw a person wide enough to read as a slab.
MAX_PERSON_FOOTPRINT_M = 0.75
#: A tracked excavator is about 2.8-3.2 m wide; 3.6 m is a large one. The detector
#: sometimes returns a machine box spanning almost the whole frame - on the blind
#: spot clip the machine box runs 85..1893 of 1920 px - and the measured width
#: inherited that, drawing hulls 4.2-5.9 m wide and 8-11 m long that filled the
#: whole 3D view. The bounds are the physical size of the plant, not the box.
MIN_MACHINERY_FOOTPRINT_M = 2.00
MAX_MACHINERY_FOOTPRINT_M = 3.60
#: Footprint aspect ratios, from the original repository's twin geometry: a
#: person's 0.8 m radius disc is replaced by a straight-edged rectangle, and the
#: machine keeps its 2.6 x 5.0 proportion. Only the size comes from measurement.
PERSON_FOOTPRINT_DEPTH_RATIO = 0.55
MACHINERY_FOOTPRINT_DEPTH_RATIO = 5.0 / 2.6

NEAR_METRES = 1.5
FAR_METRES = 30.0
#: Half-width, in metres, of the visible ground at the far row. The original twin
#: framed a 50 m grid, so a scene of this order of magnitude is not a surprise.
HALF_WIDTH_METRES = 20.0

#: Association gates, per class, in metres. A worker near the camera is stable to
#: about a millimetre, so a tight gate works. Machinery is a different case: its
#: footpoint is usually above the fitted horizon, so it is clamped to the far
#: band where the scale is only ~47 px per metre, and the detector also moves
#: between the hull, the boom and the bucket. It therefore needs a wide gate, and
#: the scene is kept clean by retiring unmatched tracks quickly rather than by
#: accumulating them.
#: Agent footprint radii. The same pair the collision engine uses at
#: src/edge/conflict_engine.py (r_col) and the edge runtime sets per agent
#: (src/edge/edge_runtime.py), kept identical so the grade below cannot drift
#: from the separation the engine would enforce on the same pair.
WORKER_FOOTPRINT_METRES = 0.8
MACHINERY_FOOTPRINT_METRES = 2.5
#: Inside this the worker and the machine are already overlapping their
#: footprints: the engine's own collision radius for a worker/machine pair.
PROXIMITY_DANGER_METRES = WORKER_FOOTPRINT_METRES + MACHINERY_FOOTPRINT_METRES
#: Warning band outside the collision radius. An engineering default, not a
#: validated site figure; recorded as such in deploy/VALIDATION.md.
PROXIMITY_NEAR_METRES = 8.0

WORKER_GATE_METRES = 2.0
EQUIPMENT_GATE_METRES = 8.0
#: A track not seen for this long is dropped, matching the original runtime.
TRACK_DROP_SECONDS = 2.5
#: A track not seen for this long is hidden but retained, matching the twin.
TRACK_HIDE_SECONDS = 0.5
#: Occupancy tolerates a longer gap than drawing does. Reusing the draw window
#: would close a zone on a single skipped frame and emit a spurious "track_lost"
#: exit during a scrub or a jump.
OCCUPANCY_LIVE_SECONDS = 1.0
#: History points retained per track, the original tracker's own cap.
MAX_HISTORY = 30
#: Speed at or above which an agent counts as moving.
MOVING_THRESHOLD_METRES_PER_SECOND = 0.25
#: History points averaged when deciding whether an agent is moving. A single
#: frame of detector jitter on a distant machine reaches ~0.3 m/s on its own, so
#: speed is measured across a short window instead of instantaneously.
SPEED_WINDOW_POINTS = 5

#: The original application's BIM trench rectangle, taken from its own
#: ``THREE.PlaneGeometry(20, 7)`` placed at ``(2, 6.5)``. This is the only zone
#: polygon in the application, and both the 3D plane and the occupancy check read
#: it from here so that what is drawn is exactly what is measured.
CANONICAL_ZONE = {
    "zone_id": "ENV-BIM-TRENCH",
    "zone_name": "BIM Trench Hazard",
    # Shape and lateral placement are the original repository's trench, taken
    # from its twin's PlaneGeometry(20, 7): 20 m across, offset 2 m from the
    # camera axis, and unchanged.
    "min_x": -8.0,
    "max_x": 12.0,
    # The depth band is calibrated to the recording rather than fixed, and it
    # follows the ground plane's own calibration. On the observation-anchored
    # plane the workers stand 4.0-18.1 m out, so the band keeps the original
    # 7 m depth and moves onto that measured working band: it contains 144 of
    # 161 observed workers, against 124 on the previous band. This is a fitted
    # working area on a fitted plane, not a surveyed hazard boundary.
    "min_y": 4.0,
    "max_y": 11.0,
}

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_CONFIG = REPO_ROOT / "config" / "default_config.json"

_TRACKERS: Dict[str, "MediaTracker"] = {}
_TRACKER_LOCK = threading.Lock()


class GroundPlane:
    """A fitted pinhole ground plane sharing the original camera's optics."""

    def __init__(self, width: int, height: int, focal: float, principal: Tuple[float, float],
                 horizon_row: float, camera_height: float, anchored: bool = False):
        self.width = int(width)
        self.height = int(height)
        self.focal = float(focal)
        self.principal = (float(principal[0]), float(principal[1]))
        self.horizon_row = float(horizon_row)
        self.camera_height = float(camera_height)
        # True when the plane was solved from an observed person, false when it
        # fell back to the assumed band. Absolute measurements are only
        # meaningful on an anchored plane: the fallback solves to a 0.84 m
        # camera, under which every person measures about 0.17 m wide. Anything
        # that tests a *measured size* must check this first, or it will reject
        # every real person on a clip whose calibration scan found nobody.
        self.anchored = bool(anchored)
        # The usable band runs from the frame's last row up to the row where the
        # ground is exactly FAR_METRES away. Clamping to the far *band* row, not
        # to the horizon, is what keeps a distant machine at a plausible 30 m
        # instead of an unbounded distance.
        self.min_row = max(0.0, self.row_for_depth(FAR_METRES))
        self.max_row = float(self.height - 1)

    # -- geometry -----------------------------------------------------
    def depth_for_row(self, row: float) -> float:
        """Ground distance in metres for an image row below the horizon."""
        return self.camera_height * self.focal / max(row - self.horizon_row, 1e-6)

    def row_for_depth(self, depth: float) -> float:
        return self.horizon_row + self.camera_height * self.focal / max(depth, 1e-6)

    def pixel_to_metric(self, points: np.ndarray) -> np.ndarray:
        """Project bottom-centre image anchors onto the ground plane.

        Rows above the fitted horizon are clamped to the far row, placing a
        distant machine at the maximum valid depth instead of discarding it.
        """
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if points.size == 0:
            return np.empty((0, 2), dtype=np.float64)
        rows = np.clip(points[:, 1], self.min_row, self.max_row)
        depth = self.camera_height * self.focal / (rows - self.horizon_row)
        depth = np.clip(depth, 0.25, FAR_METRES)
        # One metre of ground spans focal/depth pixels at this distance, so the
        # lateral offset is measured from the principal column in metres.
        scale = self.focal / depth
        x = (points[:, 0] - self.principal[0]) / scale
        return np.column_stack([x, depth])

    def describe(self) -> Dict[str, Any]:
        return {
            "focal_px": round(self.focal, 1),
            "principal_px": [round(self.principal[0], 1), round(self.principal[1], 1)],
            "horizon_row": round(self.horizon_row, 1),
            "camera_height_m": round(self.camera_height, 4),
            "near_row": round(self.max_row, 1),
            "far_row": round(self.min_row, 1),
            "ground_band_m": [NEAR_METRES, FAR_METRES],
            # Published because it decides what may be measured in metres at all:
            # on the fallback the scale is the assumed band, under which people
            # measure 0.17 m wide, so absolute distances there are not a
            # measurement. Rules that use them have to know which plane they are on.
            "anchored": self.anchored,
            "source": "fitted per recording; focal length and principal point from the original config",
        }


def fit_ground_plane(width: int, height: int, focal: float,
                     principal: Tuple[float, float],
                     person_px: Optional[float] = None,
                     person_row: Optional[float] = None) -> GroundPlane:
    """Fit the ground plane, anchored on the recording when a person is visible.

    For a pinhole camera the ground distance is ``h * f / (row - horizon)``,
    which has two unknowns: the horizon and the camera height. The band-based
    fit fixes the depth at two image rows, but it *assumes* the near band is
    NEAR_METRES away, which on these mast cameras produced a camera height below
    standing eye level and people 0.17 m wide.

    With a person in frame the plane is anchored on measurement instead. An
    observed person of shoulder width ``NOMINAL_SHOULDER_METRES`` spans
    ``person_px`` pixels, so that person stands at
    ``NOMINAL_SHOULDER_METRES * focal / person_px`` metres. Combined with the far
    band at FAR_METRES, both unknowns are determined:

        h      = k * (row_obs - far_row) / (1 - k * focal / FAR_METRES)
        horizon = far_row - h * focal / FAR_METRES      with k = shoulder/person_px

    Falls back to the band fit when no usable observation is available or the
    solution is not a plausible camera height.
    """
    near_row = NEAR_ROW_FRACTION * height
    far_row = FAR_ROW_FRACTION * height
    span = near_row - far_row
    if span <= 0:
        raise ValueError("frame is too short to fit a ground plane")

    if person_px and person_row and person_px >= MIN_PERSON_BOX_PX:
        k = NOMINAL_SHOULDER_METRES / float(person_px)
        denominator = 1.0 - k * focal / FAR_METRES
        if denominator > 1e-3:
            camera_height = k * (float(person_row) - far_row) / denominator
            horizon = far_row - camera_height * focal / FAR_METRES
            if (MIN_PLAUSIBLE_CAMERA_HEIGHT_M <= camera_height
                    <= MAX_PLAUSIBLE_CAMERA_HEIGHT_M and horizon < far_row - 1.0):
                return GroundPlane(width, height, focal, principal, horizon, camera_height,
                                  anchored=True)

    amplitude = span / ((1.0 / NEAR_METRES) - (1.0 / FAR_METRES))
    horizon = near_row - amplitude / NEAR_METRES
    if horizon >= far_row - 1.0:
        # A near-horizontal view cannot span the band; fall back to a horizon
        # just above the far row so the whole usable band stays valid.
        horizon = far_row - 1.0
        amplitude = (near_row - horizon) * NEAR_METRES
    return GroundPlane(width, height, focal, principal, horizon, amplitude / focal)


def observe_person_scale(detections) -> Tuple[Optional[float], Optional[float]]:
    """The median person box width and footpoint row in one frame.

    Only boxes wide enough to be a whole person are used; a boom or a bucket
    detected as a person is narrower than MIN_PERSON_BOX_PX and would drag the
    fit to an absurd camera height.
    """
    widths, rows = [], []
    for box in detections or []:
        if len(box) < 6 or int(box[5]) >= 2:
            continue
        width_px = float(box[2]) - float(box[0])
        if width_px < MIN_PERSON_BOX_PX:
            continue
        widths.append(width_px)
        rows.append(float(box[3]))
    if not widths:
        return None, None
    widths.sort()
    rows.sort()
    return widths[len(widths) // 2], rows[len(rows) // 2]


def _original_optics() -> Tuple[float, Tuple[float, float]]:
    """Focal length and principal point from the original camera configuration."""
    import json
    if not DEMO_CONFIG.is_file():
        return 1420.0, (960.0, 540.0)
    with open(DEMO_CONFIG) as handle:
        homography = json.load(handle)["homography"]
    matrix = homography["K"]
    return float(matrix[0][0]), (float(matrix[0][2]), float(matrix[1][2]))


_PLANES: Dict[Tuple[int, int, int, int], GroundPlane] = {}


def ground_plane(width: int, height: int,
                 person_px: Optional[float] = None,
                 person_row: Optional[float] = None) -> GroundPlane:
    """The fitted plane for a frame size and calibration, cached.

    The cache key includes the observation, so a plane fitted on measurement is
    never served for a differently calibrated recording.
    """
    key = (int(width), int(height),
           int(round(person_px or 0.0)), int(round(person_row or 0.0)))
    plane = _PLANES.get(key)
    if plane is None:
        focal, principal = _original_optics()
        plane = fit_ground_plane(width, height, focal, principal, person_px, person_row)
        _PLANES[key] = plane
    return plane


def in_canonical_zone(x: float, y: float) -> bool:
    return (CANONICAL_ZONE["min_x"] <= x <= CANONICAL_ZONE["max_x"]
            and CANONICAL_ZONE["min_y"] <= y <= CANONICAL_ZONE["max_y"])


def _class_name(class_id: int) -> str:
    """The original application's class vocabulary."""
    return "WORKER" if int(class_id) < 2 else "HEAVY_EQUIPMENT"


class _Track:
    """One agent, with the history and velocity the original twin consumed."""

    __slots__ = ("track_id", "class_id", "class_name", "x", "y", "vx", "vy",
                 "history", "stamps", "last_seen", "first_seen", "width_m")

    def __init__(self, track_id: int, class_id: int, x: float, y: float, moment: float,
                 width_m: float = 0.0):
        self.width_m = float(width_m)
        self.track_id = track_id
        self.class_id = int(class_id)
        self.class_name = _class_name(class_id)
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.history: List[List[float]] = [[round(x, 3), round(y, 3)]]
        self.stamps: List[float] = [float(moment)]
        self.last_seen = float(moment)
        self.first_seen = float(moment)

    def update(self, x: float, y: float, moment: float) -> None:
        elapsed = max(moment - self.last_seen, 1e-3)
        # Light smoothing keeps the heading stable without lagging a real turn.
        self.vx = 0.5 * self.vx + 0.5 * (x - self.x) / elapsed
        self.vy = 0.5 * self.vy + 0.5 * (y - self.y) / elapsed
        self.x = float(x)
        self.y = float(y)
        self.last_seen = float(moment)
        self.history.append([round(x, 3), round(y, 3)])
        self.stamps.append(float(moment))
        if len(self.history) > MAX_HISTORY:
            del self.history[: len(self.history) - MAX_HISTORY]
            del self.stamps[: len(self.stamps) - MAX_HISTORY]

    @property
    def speed(self) -> float:
        """Windowed speed, so one jittery frame cannot read as movement."""
        window = min(SPEED_WINDOW_POINTS, len(self.history) - 1)
        if window < 1:
            return 0.0
        newest, oldest = self.history[-1], self.history[-1 - window]
        elapsed = self.stamps[-1] - self.stamps[-1 - window]
        if elapsed <= 1e-6:
            return 0.0
        return math.hypot(newest[0] - oldest[0], newest[1] - oldest[1]) / elapsed

    @property
    def heading(self) -> float:
        if self.speed < 1e-3:
            return 0.0
        return math.atan2(self.vy, self.vx)

    def footprint(self) -> Dict[str, float]:
        """The agent's ground polygon, straight-edged, sized from the detection.

        A box's pixel width measured at its own depth gives the real width, so
        the 3D polygon is the object that was actually detected rather than a
        fixed size. The depth keeps the original twin's proportion for that class
        and is smoothed, so a single noisy frame does not resize the polygon.
        """
        if self.class_id < 2:
            width = min(MAX_PERSON_FOOTPRINT_M,
                        max(MIN_PERSON_FOOTPRINT_M, self.width_m or MIN_PERSON_FOOTPRINT_M))
            ratio = PERSON_FOOTPRINT_DEPTH_RATIO
        else:
            width = min(MAX_MACHINERY_FOOTPRINT_M,
                        max(MIN_MACHINERY_FOOTPRINT_M, self.width_m or MIN_MACHINERY_FOOTPRINT_M))
            ratio = MACHINERY_FOOTPRINT_DEPTH_RATIO
        return {"width_m": round(width, 3), "depth_m": round(width * ratio, 3)}

    def forecast(self) -> List[List[float]]:
        """Where this agent is heading, from its measured velocity.

        A straight projection over the horizon, emitted only while the agent is
        actually moving. A stationary agent's velocity is footpoint noise, so a
        forecast drawn from it would invent a direction the person never took.
        """
        if self.speed < MOVING_THRESHOLD_METRES_PER_SECOND:
            return []
        window = self.history[-FORECAST_WINDOW:]
        if len(window) < 2:
            return []
        # Net displacement across the window, so one bad frame cannot set the
        # direction for the whole projection.
        seconds = (len(window) - 1) / 30.0
        vx = (window[-1][0] - window[0][0]) / seconds
        vy = (window[-1][1] - window[0][1]) / seconds
        speed = math.hypot(vx, vy)
        if speed < MOVING_THRESHOLD_METRES_PER_SECOND:
            return []
        # class_id >= 2 is plant, as everywhere else in this module.
        cap = (MAX_FORECAST_SPEED_MPS_MACHINERY if self.class_id >= 2
               else MAX_FORECAST_SPEED_MPS)
        if speed > cap:
            scale = cap / speed
            vx, vy, speed = vx * scale, vy * scale, cap
        step = FORECAST_HORIZON_SECONDS / FORECAST_POINTS
        return [[round(self.x + vx * step * n, 3),
                 round(self.y + vy * step * n, 3)]
                for n in range(1, FORECAST_POINTS + 1)]

    def as_dict(self, moment: float) -> Dict[str, Any]:
        inside = in_canonical_zone(self.x, self.y)
        moving = self.speed >= MOVING_THRESHOLD_METRES_PER_SECOND
        if inside and moving:
            label = "IN ZONE · MOVING"
        elif inside:
            label = "IN ZONE"
        elif moving:
            label = "MOVING"
        else:
            label = "STATIONARY"
        return {
            "track_id": self.track_id,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "position": [round(self.x, 3), round(self.y, 3)],
            "velocity": [round(self.vx, 3), round(self.vy, 3)],
            "heading": round(self.heading, 4),
            "speed_mps": round(self.speed, 3),
            "history": [list(point) for point in self.history],
            "footprint": self.footprint(),
            "forecast_trajectory": self.forecast(),
            "last_observed_age_seconds": round(max(0.0, moment - self.last_seen), 3),
            "in_zone": inside,
            "moving": moving,
            "label": label,
        }


class MediaTracker:
    """Builds ``tracks_3d`` for a recording, one monotonic pass over its frames."""

    def __init__(self, media_id: str, width: int, height: int, total_frames: int,
                 plane: Optional[GroundPlane] = None):
        self.media_id = media_id
        self.width = int(width)
        self.height = int(height)
        self.total_frames = int(total_frames)
        self.plane = plane or ground_plane(width, height)
        self._tracks: Dict[int, _Track] = {}
        self._next_id = 1
        self._next_frame = 0
        self._snapshots: Dict[int, Dict[str, Any]] = {}
        self._events: List[Dict[str, Any]] = []
        self._detection_track_ids: List[Optional[int]] = []
        self._inside: set = set()
        self._entered_at: Dict[int, float] = {}
        self._last_presence: Dict[int, float] = {}

    # -- lifecycle ----------------------------------------------------
    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1
        self._next_frame = 0
        self._snapshots.clear()
        self._events.clear()
        self._inside.clear()
        self._entered_at.clear()
        self._last_presence.clear()

    @property
    def ready_through(self) -> int:
        return self._next_frame - 1

    def event_log(self, limit: int = 12) -> List[Dict[str, Any]]:
        return list(self._events[-limit:])

    # -- stepping -----------------------------------------------------
    def snapshot(self, frame_index: int) -> Optional[Dict[str, Any]]:
        return self._snapshots.get(int(frame_index))

    def snapshot_for(self, frame_index: int) -> Optional[Dict[str, Any]]:
        """The stored snapshot for a frame, or None if this tracker has not
        produced one."""
        return self._snapshots.get(int(frame_index))

    def step(self, frame_index: int, detections, moment: float,
             operators: frozenset = frozenset(), frames_at=None) -> Dict[str, Any]:
        """Advance to ``frame_index`` and return that frame's snapshot.

        Steps forward, filling any skipped frames with the last known detections
        so a jump does not invent a jump in the trail. A request for an earlier
        frame returns the stored snapshot rather than rewinding.
        """
        index = int(frame_index)
        if index in self._snapshots:
            return self._snapshots[index]
        if index < self._next_frame:
            # Never rewind. But an earlier frame that was never stepped has no
            # snapshot of its own, and the newest snapshot describes a different
            # frame: returning it would attach that frame's track ids to this
            # frame's boxes, putting one person's state on another's box. Report
            # nothing instead, so the panel shows a measurement with no state
            # rather than the wrong state.
            return {
                "frame_index": index,
                "tracks_3d": [],
                "worker_states": [],
                "zone_events": [],
                "zone_exits": [],
                "detection_track_ids": [],
                "ground_plane": self.plane.describe(),
                "zone": dict(CANONICAL_ZONE),
                "replay_gap": True,
            }

        for step_index in range(self._next_frame, min(index, self.total_frames - 1) + 1):
            if step_index == index:
                boxes, step_operators, step_moment = detections, operators, moment
            elif frames_at is not None:
                # A skipped frame is filled with ITS OWN detections, not with
                # nothing and not with this frame's. Filling it with None cached an
                # empty snapshot for it permanently, so every frame before the one
                # the client requested first reported no agents at all, and the
                # trail and forecast path could only appear if the clip was played
                # straight through from the start.
                boxes, step_operators = frames_at(step_index)
                step_operators = frozenset(step_operators or ())
                step_moment = float(step_index) / 30.0
            else:
                boxes, step_operators, step_moment = None, frozenset(), moment
            self._snapshots[step_index] = self._advance(
                step_index, boxes, step_moment, step_operators)
        self._next_frame = min(index, self.total_frames - 1) + 1
        return self._snapshots.get(index, {"tracks_3d": [], "worker_states": [], "frame_index": index})

    def _advance(self, index: int, detections, moment: float,
                 operators: frozenset = frozenset()) -> Dict[str, Any]:
        moment = float(index) / 30.0
        measured: List[Tuple[int, int, float, float, float]] = []
        if detections is not None and len(detections):
            box_array = np.asarray([d[:4] for d in detections], dtype=np.float64)
            centres = np.column_stack([(box_array[:, 0] + box_array[:, 2]) / 2.0,
                                       box_array[:, 3]])
            metric = self.plane.pixel_to_metric(centres)
            for detection_index, (detection, point) in enumerate(zip(detections, metric)):
                if not np.isfinite(point).all():
                    continue
                # A person inside a machine is that machine's operator. They stay
                # on the camera frame with their PPE notes, but they are not a
                # second agent on the ground plane, and their distance to the
                # machine they are sitting in is not a near-miss.
                if detection_index in operators:
                    continue
                # A fragment of a person is not a person. The box stays on the
                # camera frame, but it has no ground contact, so it must not
                # become a polygon standing at an arbitrary depth - that is what
                # made the canvas look scattered.
                if (int(detection[5]) < 2
                        and (float(detection[3]) - float(detection[1]))
                        < MIN_PERSON_BOX_HEIGHT_PX):
                    continue
                # A box's pixel width is a ground-plane measurement at its own depth,
                # so the agent's real width comes from the detection, not a fixed size.
                width_px = float(detection[2]) - float(detection[0])
                width_m = max(0.05, min(FAR_METRES, float(point[1]))) * width_px / self.plane.focal
                # A person-class box that measures too narrow to be a person is a
                # fragment. It keeps its box on the camera frame with its PPE notes,
                # but it is not put on the ground plane, where it would be drawn as
                # a speck among the real agents.
                #
                # Only on an anchored plane. On the fallback the scale is not
                # trustworthy, and applying this test there rejected every real
                # person on the two clips whose calibration scan found nobody.
                if (int(detection[5]) < 2 and self.plane.anchored
                        and width_m < MIN_PLAUSIBLE_PERSON_WIDTH_M):
                    continue
                measured.append((detection_index, int(detection[5]), float(point[0]),
                                 float(point[1]), width_m))

        # Retire tracks that have not been seen for too long.
        for track_id in [tid for tid, t in self._tracks.items()
                         if moment - t.last_seen > TRACK_DROP_SECONDS]:
            del self._tracks[track_id]

        # Greedy nearest-neighbour association, class-consistent, nearest first.
        pairs = []
        for measured_index, (source_index, class_id, x, y, width_m) in enumerate(measured):
            gate = WORKER_GATE_METRES if class_id < 2 else EQUIPMENT_GATE_METRES
            for track_id, track in self._tracks.items():
                if track.class_id != class_id:
                    continue
                distance = math.hypot(track.x - x, track.y - y)
                if distance <= gate:
                    pairs.append((distance, source_index, measured_index, track_id))
        pairs.sort()
        taken_detections, taken_tracks = set(), set()
        taken_tracks_by_detection: Dict[int, int] = {}
        for _distance, source_index, measured_index, track_id in pairs:
            if source_index in taken_detections or track_id in taken_tracks:
                continue
            taken_detections.add(source_index)
            taken_tracks.add(track_id)
            taken_tracks_by_detection[source_index] = track_id
            _, class_id, x, y, width_m = measured[measured_index]
            self._tracks[track_id].update(x, y, moment)
            self._tracks[track_id].width_m = width_m

        # The track each input detection was assigned to, in input order, so a
        # consumer can pair a drawn box with its state without re-associating.
        # Indexed by the *input* detection, not by position in `measured`: an
        # operator is skipped, so the two orderings differ, and indexing by
        # `measured` would put one person's state on another's box.
        assigned: List[Optional[int]] = [None] * len(detections or [])
        for detection_index, track_id in taken_tracks_by_detection.items():
            assigned[detection_index] = track_id
        for _measured_index, (source_index, class_id, x, y, width_m) in enumerate(measured):
            if source_index in taken_detections:
                continue
            track = _Track(self._next_id, class_id, x, y, moment, width_m)
            self._tracks[self._next_id] = track
            assigned[source_index] = self._next_id
            self._next_id += 1
        self._detection_track_ids = assigned

        live_ids = {track.track_id for track in self._tracks.values()
                    if moment - track.last_seen <= OCCUPANCY_LIVE_SECONDS}
        self._record_events(moment, live_ids)
        return self._build_snapshot(index, moment)

    def _record_events(self, moment: float, live_ids: set) -> None:
        """Log the original entry / continued-presence / exit transitions.

        Occupancy is held as explicit state, not inferred from the event log.
        Inferring it from the log re-emits an exit on every subsequent frame,
        because the entry that produced it stays in the history.
        """
        inside_now = set()
        for track in self._tracks.values():
            if track.track_id not in live_ids:
                continue
            if in_canonical_zone(track.x, track.y):
                inside_now.add(track.track_id)
                if track.track_id not in self._inside:
                    self._inside.add(track.track_id)
                    self._entered_at[track.track_id] = moment
                    self._last_presence[track.track_id] = moment
                    self._events.append({
                        "event_type": "ZONE_ENTRY", "track_id": track.track_id,
                        "class_name": track.class_name, "zone_id": CANONICAL_ZONE["zone_id"],
                        "dwell_seconds": 0.0, "timestamp": round(moment, 3),
                        "position": [round(track.x, 3), round(track.y, 3)]})
                elif moment - self._last_presence.get(track.track_id, moment) >= 1.0:
                    self._last_presence[track.track_id] = moment
                    self._events.append({
                        "event_type": "ZONE_OCCUPANCY", "track_id": track.track_id,
                        "class_name": track.class_name, "zone_id": CANONICAL_ZONE["zone_id"],
                        "dwell_seconds": round(moment - self._entered_at.get(track.track_id, moment), 3),
                        "timestamp": round(moment, 3),
                        "position": [round(track.x, 3), round(track.y, 3)]})

        for track_id in list(self._inside):
            if track_id in inside_now:
                continue
            lost = track_id not in live_ids
            self._inside.discard(track_id)
            self._events.append({
                "event_type": "ZONE_EXIT", "track_id": track_id,
                "class_name": self._tracks[track_id].class_name if track_id in self._tracks
                else "WORKER",
                "zone_id": CANONICAL_ZONE["zone_id"],
                "dwell_seconds": round(moment - self._entered_at.get(track_id, moment), 3),
                "timestamp": round(moment, 3),
                "reason": "track_lost" if lost else "left_zone",
                "position": [round(self._tracks[track_id].x, 3),
                             round(self._tracks[track_id].y, 3)]
                if track_id in self._tracks else [0.0, 0.0]})
            self._entered_at.pop(track_id, None)
            self._last_presence.pop(track_id, None)
        if len(self._events) > 400:
            del self._events[: len(self._events) - 400]

    def _build_snapshot(self, index: int, moment: float) -> Dict[str, Any]:
        tracks, workers, exits = [], [], []
        for track_id in sorted(self._tracks):
            track = self._tracks[track_id]
            age = moment - track.last_seen
            if age > TRACK_HIDE_SECONDS:
                # Only currently observed agents are sent: a retained-but-unseen
                # track is internal continuity, not something to draw.
                continue
            payload = track.as_dict(moment)
            payload["visible"] = True
            tracks.append(payload)
            if track.class_id < 2:
                workers.append({
                    "track_id": track.track_id, "label": payload["label"],
                    "in_zone": payload["in_zone"], "moving": payload["moving"],
                    "speed_mps": payload["speed_mps"],
                })
        # Proximity: grade every visible worker against the nearest visible
        # machinery on the same fitted plane. A worker with no machinery in
        # frame is NO_MACHINE, never SAFE - the absence of a machine is not
        # evidence that the worker is safe.
        machinery = [(t["track_id"], t["position"]) for t in tracks
                     if t["class_name"] == "HEAVY_EQUIPMENT"]
        for worker in workers:
            best_id, best = None, None
            for track_id, position in machinery:
                distance = math.hypot(position[0] - self._tracks[worker["track_id"]].x,
                                       position[1] - self._tracks[worker["track_id"]].y)
                if best is None or distance < best:
                    best_id, best = track_id, distance
            if best is None:
                worker.update({"proximity_state": "NO_MACHINE",
                               "nearest_machinery_track_id": None,
                               "nearest_machinery_metres": None})
                continue
            state = ("DANGER" if best <= PROXIMITY_DANGER_METRES
                     else "NEAR" if best <= PROXIMITY_NEAR_METRES else "SAFE")
            worker.update({"proximity_state": state,
                           "nearest_machinery_track_id": best_id,
                           "nearest_machinery_metres": round(best, 2)})

        for event in self._events:
            if event["event_type"] == "ZONE_EXIT" and abs(event["timestamp"] - moment) < 1.0 / 60:
                exits.append(event)
        return {
            "frame_index": index,
            "tracks_3d": tracks,
            "worker_states": workers,
            "zone_exits": exits,
            "zone_events": self.event_log(8),
            "detection_track_ids": list(self._detection_track_ids),
            "ground_plane": self.plane.describe(),
            "zone": dict(CANONICAL_ZONE),
        }


#: Frame indices scanned for the scale anchor, in order. Fixed, so the same
#: recording always yields the same plane no matter where playback starts.
CALIBRATION_LADDER: Tuple[int, ...] = (0, 5, 10, 15, 20, 30, 40, 60, 90, 120)
#: How many usable observations to combine once the ladder finds them.
CALIBRATION_SAMPLES = 3

_MEDIA_PLANES: Dict[Tuple[str, int, int, int], GroundPlane] = {}


def plane_for_media(media_id: str, width: int, height: int, mtime_ns: int,
                    boxes_at) -> GroundPlane:
    """The ground plane for one recording, calibrated once from its own frames.

    ``boxes_at`` returns the detections for an output frame index, or ``None``.
    The scan is fixed and cached, so the plane is a property of the recording
    rather than of whichever frame the viewer happened to open: scrubbing and
    jumping around cannot move the ground under a stored snapshot.
    """
    key = (media_id, int(width), int(height), int(mtime_ns))
    cached = _MEDIA_PLANES.get(key)
    if cached is not None:
        return cached
    focal, principal = _original_optics()
    samples: List[Tuple[float, float]] = []
    for index in CALIBRATION_LADDER:
        try:
            detections = boxes_at(index)
        except Exception:
            continue
        if not detections:
            continue
        person_px, person_row = observe_person_scale(detections)
        if person_px:
            samples.append((person_px, person_row))
            if len(samples) >= CALIBRATION_SAMPLES:
                break
    if samples:
        widths = sorted(width for width, _ in samples)
        rows = sorted(row for _, row in samples)
        plane = fit_ground_plane(width, height, focal, principal,
                                 widths[len(widths) // 2], rows[len(rows) // 2])
    else:
        plane = fit_ground_plane(width, height, focal, principal)
    _MEDIA_PLANES[key] = plane
    return plane


#: A recording is stepped once, in order, so that every frame has a real trail.
#: Beyond this many frames the pass is skipped and the streaming behaviour is kept
#: instead, because a full pass is paid for on the first request.
#: Two detections of one object stand at almost the same place on the ground. The
#: thresholds were measured on the restored clips: a second detection of the same
#: person lands 0.17-1.08 m from the first, while two genuinely separate people
#: whose boxes overlap are 1.59 m apart or more. The gap between those two
#: populations is where the threshold goes.
DUPLICATE_PERSON_METRES = 1.2
DUPLICATE_MACHINERY_METRES = 3.0
#: Two boxes on one person are often offset - head-and-torso against
#: torso-and-legs - and overlap as little as 0.02 of the smaller. The same-class
#: rule therefore keys on the ground position alone and does not use this.
DUPLICATE_OVERLAP = 0.25
#: Without a ground fit there is no metric evidence, so only substantial overlap
#: justifies calling two boxes one object. A quarter of the smaller box is not
#: enough to drop a person on: two workers standing beside each other share that
#: much, and the one that goes would take a person off the frame with it.
UNANCHORED_DUPLICATE_OVERLAP = 0.5
#: A box lying wholly inside another of the same class, reporting far less
#: confidence, is the same object seen twice whatever the plane says. All six of
#: the pairs measured on the restored clips are one shape: a 0.26-0.47 confidence
#: box lying 99% or more inside a 0.89-0.93 one. That is a second hypothesis on
#: the object the strong box already covers, not a second object.
FULL_CONTAINMENT = 0.99
NESTED_CONFIDENCE_RATIO = 0.6
#: A plant or vehicle box almost entirely inside another is not two objects: it is
#: the same region of the scene reported twice under two class ids. Nine of the
#: pairs measured on the restored clips are a 250x82 px vehicle strip lying 100%
#: inside a 680x678 px machine box. No person is ever removed by this rule.
NESTED_PLANT_CONTAINMENT = 0.90


def _box_area(box) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def _weakly_nested(candidate, standing) -> bool:
    """``candidate`` lies wholly inside ``standing`` and claims far less confidence.

    Deliberately asymmetric. The box that goes is the one *inside*: a small
    confident box sitting within a large uncertain one is not a duplicate of it,
    and treating the pair symmetrically dropped the large box - the one that
    actually covered the object - instead of the uncertain second hypothesis.

    The confidence gap is what separates this from a person standing behind
    another person: there, both boxes are held with comparable confidence.
    """
    own = _box_area(candidate)
    if own <= 0 or _box_area(standing) <= 0:
        return False
    width = min(candidate[2], standing[2]) - max(candidate[0], standing[0])
    height = min(candidate[3], standing[3]) - max(candidate[1], standing[1])
    if width <= 0 or height <= 0:
        return False
    if (width * height) / own < FULL_CONTAINMENT:
        return False
    confident = float(standing[4])
    return confident > 0 and float(candidate[4]) < NESTED_CONFIDENCE_RATIO * confident


def _same_object(first, second, metric_first, metric_second, anchored) -> bool:
    """Whether these two boxes are one object rather than two.

    The three rules in one place, so the sweep below reads as a decision:

    * same class, same spot on the ground (only where the plane is a measurement);
    * same class, one box substantially inside the other, without a fit to trust;
    * same class, one box wholly inside the other and reporting far less confidence;
    * plant and a vehicle, one substantially inside the other on the same spot.

    A person inside a machine is never the box that goes: that is an operator.
    """
    classe_first, classe_second = int(first[5]), int(second[5])
    smaller = min(_box_area(first), _box_area(second))
    if smaller <= 0:
        return False
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    overlap = 0.0 if (width <= 0 or height <= 0) else width * height
    fraction = overlap / smaller

    if classe_first == classe_second:
        if anchored:
            limit = (DUPLICATE_MACHINERY_METRES if classe_first >= 2
                     else DUPLICATE_PERSON_METRES)
            gap = float(np.hypot(*(metric_first - metric_second)))
            if gap < limit:
                return True
        elif fraction >= UNANCHORED_DUPLICATE_OVERLAP:
            return True
        return _weakly_nested(first, second)

    if classe_first < 2 or classe_second < 2:
        return False                      # an operator, or a person beside plant
    gap = float(np.hypot(*(metric_first - metric_second)))
    return (gap < DUPLICATE_MACHINERY_METRES
            or fraction >= NESTED_PLANT_CONTAINMENT)


def drop_duplicate_detections(boxes, plane):
    """Drop every box that is a second detection of an object already kept.

    The decision is the object's position on the calibrated ground, not how much
    the rectangles overlap. Measured across the restored clips: a second box on
    one object lands 0.02-1.08 m from the first on the ground, while two objects
    that are genuinely separate are 1.34 m apart or more - and they can still
    overlap heavily in the image, because one may be nearer the camera than the
    other. Overlap alone therefore cannot separate the two cases, and an earlier
    version of this function that required it removed only a fifth of the
    duplicates: the pairs it missed overlap by as little as 0.02 of the smaller
    box.

    The boxes are swept best first - the more confident, then the larger - and a
    box goes only if it is a second detection of one that is still standing. An
    earlier version compared pairs in input order, which let a box be dropped in
    favour of a winner that was itself dropped later, leaving nothing at that spot.

    Returns ``(kept_boxes, index_map)`` where ``index_map`` sends an input
    position to its position in the kept list. The input is not mutated.
    """
    rows = [list(box) for box in (boxes or [])]
    if len(rows) < 2 or plane is None:
        # Identity mapping, so the caller's operator positions still resolve.
        return rows, {k: k for k in range(len(rows))}
    anchors = np.array([[(float(b[0]) + float(b[2])) / 2.0, float(b[3])] for b in rows],
                       dtype=np.float64)
    metric = plane.pixel_to_metric(anchors)
    anchored = bool(getattr(plane, "anchored", False))

    def usable(k):
        return bool(np.isfinite(metric[k]).all())

    order = sorted(range(len(rows)),
                   key=lambda k: (float(rows[k][4]), _box_area(rows[k])), reverse=True)
    kept_indexes = []
    for k in order:
        duplicate = False
        if usable(k):
            for j in kept_indexes:
                if not usable(j):
                    continue
                if _same_object(rows[k], rows[j], metric[k], metric[j], anchored):
                    duplicate = True
                    break
        if not duplicate:
            kept_indexes.append(k)
    kept_indexes.sort()
    kept = [rows[k] for k in kept_indexes]
    # Operator positions were indices into the input; remap them to the kept list.
    mapping = {k: n for n, k in enumerate(kept_indexes)}
    return kept, mapping


def tracker_for(media_id: str, width: int, height: int, total_frames: int,
                mtime_ns: int, plane: Optional[GroundPlane] = None) -> MediaTracker:
    """The tracker for a recording, rebuilt if the file changed."""
    key = (f"{media_id}:{mtime_ns}:{width}x{height}"
           f":{getattr(plane, 'camera_height', 0.0):.4f}")
    with _TRACKER_LOCK:
        tracker = _TRACKERS.get(media_id)
        if tracker is None or getattr(tracker, "_key", None) != key:
            tracker = MediaTracker(media_id, width, height, total_frames, plane=plane)
            tracker._key = key
            _TRACKERS[media_id] = tracker
        return tracker


def clear_trackers() -> None:
    with _TRACKER_LOCK:
        _TRACKERS.clear()
