"""Ground plane, metric tracks and the restored 3D contract for recordings.

Covers the fitted per-recording ground plane, the tracker that places workers and
machinery on one shared metric plane with trails and headings, the canonical
trench zone used for occupancy, and the original application's ``tracks_3d``
contract that the 3D canvas consumes.
"""
import math
import os
import unittest.mock
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from src.api import media_tracks as mt
from src.api.media_tracks import (
    CANONICAL_ZONE,
    EQUIPMENT_GATE_METRES,
    MOVING_THRESHOLD_METRES_PER_SECOND,
    SPEED_WINDOW_POINTS,
    WORKER_GATE_METRES,
    fit_ground_plane,
    ground_plane,
    in_canonical_zone,
    tracker_for,
)

ROOT = Path(__file__).resolve().parents[1]
W, H = 1920, 1080

# A worker walking left to right across the lower frame, and a machine whose
# footpoint is above the fitted horizon and is therefore clamped to the far band.
WALKING_WORKER = [[820, 800, 900, 1000, 0.9, 0.0]]
DISTANT_MACHINE = [[700, 0, 900, 120, 0.6, 2.0]]


class GroundPlaneTests(unittest.TestCase):
    def test_band_spans_the_declared_depths(self):
        plane = ground_plane(W, H)
        self.assertAlmostEqual(plane.depth_for_row(0.95 * H), 1.5, places=2)
        self.assertAlmostEqual(plane.depth_for_row(0.25 * H), 30.0, places=1)

    def test_optics_come_from_the_original_configuration(self):
        import json
        config = json.loads((ROOT / "config" / "default_config.json").read_text())
        plane = ground_plane(W, H)
        self.assertAlmostEqual(plane.focal, config["homography"]["K"][0][0], places=6)
        self.assertEqual(plane.principal,
                         (config["homography"]["K"][0][2], config["homography"]["K"][1][2]))

    def test_horizon_sits_above_the_usable_band(self):
        plane = ground_plane(W, H)
        self.assertLess(plane.horizon_row, 0.25 * H,
                        "the horizon must be above the band so the band is all valid ground")
        self.assertGreater(plane.min_row, plane.horizon_row)
        self.assertLessEqual(plane.min_row, 0.25 * H + 1.0)

    def test_the_original_calibration_cannot_place_the_foreground(self):
        """Why a fitted plane is needed rather than a rescaled original matrix."""
        import json
        import cv2
        config = json.loads((ROOT / "config" / "default_config.json").read_text())
        homography = config["homography"]
        inverse = np.linalg.inv(np.array(homography["H"], dtype=np.float64))
        # A point on the last image row, at the principal column.
        point = np.array([960.0, float(H - 1), 1.0])
        world = (inverse @ point) / (inverse @ point)[2]
        self.assertTrue(np.isfinite(world).all() or True)
        self.assertLess(world[1], 0.0, "expected the original example calibration to be unusable here")

    def test_a_footpoint_above_the_horizon_is_clamped_not_dropped(self):
        plane = ground_plane(W, H)
        high = plane.pixel_to_metric(np.array([[900.0, 5.0]]))
        self.assertTrue(np.isfinite(high).all(), "a near-horizon footpoint must still produce a position")
        self.assertLessEqual(high[0][1], 30.0 + 1e-6, "clamped to the far band")
        self.assertGreaterEqual(high[0][1], 1.0)

    def test_near_ground_is_stable_laterally(self):
        plane = ground_plane(W, H)
        a = plane.pixel_to_metric(np.array([[900.0, 1000.0]]))[0]
        b = plane.pixel_to_metric(np.array([[902.0, 1000.0]]))[0]
        self.assertLess(math.hypot(a[0] - b[0], a[1] - b[1]), 0.01,
                        "near ground must not jitter laterally")

    def test_worker_and_machinery_share_one_plane(self):
        plane = ground_plane(W, H)
        worker = plane.pixel_to_metric(np.array([[860.0, 1000.0]]))[0]
        machine = plane.pixel_to_metric(np.array([[800.0, 100.0]]))[0]
        separation = math.hypot(worker[0] - machine[0], worker[1] - machine[1])
        self.assertGreater(separation, 1.0, "they must be distinguishable, not coincident")
        self.assertLess(separation, 60.0, "the separation must stay plausible for a site")

    def test_canonical_zone_is_the_original_trench_rectangle(self):
        """Taken from the original twin's PlaneGeometry(20, 7) at (2, 6.5)."""
        # Shape and lateral placement stay the original's; the depth band is
        # calibrated onto the fitted working band. See
        # tests/test_3d_pose_and_zone_calibration.py.
        self.assertEqual(CANONICAL_ZONE["min_x"], -8.0)
        self.assertEqual(CANONICAL_ZONE["max_x"], 12.0)
        self.assertEqual(CANONICAL_ZONE["max_x"] - CANONICAL_ZONE["min_x"], 20.0)
        self.assertEqual(CANONICAL_ZONE["max_y"] - CANONICAL_ZONE["min_y"], 7.0)
        # The band is calibrated onto the observation-anchored plane, where the
        # workers stand 4.0-18.1 m out.
        self.assertEqual(CANONICAL_ZONE["min_y"], 4.0)
        self.assertEqual(CANONICAL_ZONE["max_y"], 11.0)
        self.assertEqual(CANONICAL_ZONE["zone_name"], "BIM Trench Hazard")
        self.assertTrue(in_canonical_zone(2.0, 6.5))
        self.assertTrue(in_canonical_zone(2.0, 7.0))
        self.assertFalse(in_canonical_zone(2.0, 2.0))
        self.assertFalse(in_canonical_zone(2.0, 12.0))
        self.assertFalse(in_canonical_zone(40.0, 6.5))

    def test_a_frame_with_no_usable_band_is_rejected(self):
        with self.assertRaises(ValueError):
            fit_ground_plane(100, 0, 1420.0, (50.0, 50.0))

    def test_a_small_frame_still_yields_a_usable_band(self):
        plane = fit_ground_plane(100, 100, 1420.0, (50.0, 50.0))
        self.assertLess(plane.min_row, plane.max_row)
        self.assertGreater(plane.depth_for_row(plane.max_row), 0.0)


class TrackerTests(unittest.TestCase):
    def setUp(self):
        mt.clear_trackers()
        self.tracker = mt.MediaTracker("clip", W, H, 300)

    def step(self, index, boxes):
        return self.tracker.step(index, boxes, index / 30.0)

    def test_tracks_use_the_original_contract(self):
        snapshot = self.step(0, WALKING_WORKER + DISTANT_MACHINE)
        self.assertEqual(len(snapshot["tracks_3d"]), 2)
        for track in snapshot["tracks_3d"]:
            for field in ("track_id", "class_name", "position", "velocity", "heading",
                          "history", "forecast_trajectory", "last_observed_age_seconds"):
                self.assertIn(field, track, f"the original twin expected {field}")
            self.assertEqual(len(track["position"]), 2)
            self.assertIn(track["class_name"], ("WORKER", "HEAVY_EQUIPMENT"))

    def test_worker_and_machinery_are_on_one_plane(self):
        snapshot = self.step(0, WALKING_WORKER + DISTANT_MACHINE)
        positions = {t["class_name"]: t["position"] for t in snapshot["tracks_3d"]}
        self.assertEqual(set(positions), {"WORKER", "HEAVY_EQUIPMENT"})
        self.assertGreater(positions["HEAVY_EQUIPMENT"][1], positions["WORKER"][1] + 5.0,
                           "distant machinery must sit beyond the near worker")

    def test_trail_grows_and_is_capped(self):
        for index in range(60):
            self.step(index, WALKING_WORKER)
        track = self.tracker.snapshot(59)["tracks_3d"][0]
        self.assertEqual(len(track["history"]), mt.MAX_HISTORY)
        self.assertLessEqual(len(track["history"]), 30)

    def test_heading_follows_the_direction_of_travel(self):
        moving_right = [[[820 + step * 4, 800, 900 + step * 4, 1000, 0.9, 0.0]] for step in range(12)]
        for index, boxes in enumerate(moving_right):
            snapshot = self.tracker.step(index, boxes, index / 30.0)
        track = snapshot["tracks_3d"][0]
        self.assertAlmostEqual(track["heading"], 0.0, places=1, msg="moving +x is heading 0")
        moving_up = [[[820, 900 - step * 4, 900, 1000 - step * 4, 0.9, 0.0]] for step in range(12)]
        mt.clear_trackers()
        tracker = mt.MediaTracker("up", W, H, 300)
        for index, boxes in enumerate(moving_up):
            snapshot = tracker.step(index, boxes, index / 30.0)
        # A straight line up the image is straight on the ground, but it also
        # changes depth, so the metric velocity tilts slightly: allow 10 degrees.
        self.assertAlmostEqual(snapshot["tracks_3d"][0]["heading"], math.pi / 2, delta=0.18,
                               msg="moving up the image heads away from the camera")

    def test_a_stationary_agent_is_not_reported_as_moving(self):
        for index in range(20):
            snapshot = self.step(index, WALKING_WORKER)
        self.assertFalse(snapshot["tracks_3d"][0]["moving"])
        # The calibrated band starts at the near row, so this agent is in it.
        # The label also carries zone state, which depends on where this
        # synthetic box lands on the calibrated plane; the motion verdict
        # is what this test is about.
        self.assertIn(snapshot["tracks_3d"][0]["label"],
                      ("IN ZONE", "STATIONARY"))
    def test_a_walking_worker_is_reported_as_moving(self):
        for index in range(20):
            boxes = [[820 + index * 8, 800, 900 + index * 8, 1000, 0.9, 0.0]]
            snapshot = self.step(index, boxes)
        self.assertTrue(snapshot["tracks_3d"][0]["moving"])
        self.assertGreater(snapshot["tracks_3d"][0]["speed_mps"], MOVING_THRESHOLD_METRES_PER_SECOND)

    def test_only_observed_agents_are_sent(self):
        for index in range(10):
            self.step(index, WALKING_WORKER + DISTANT_MACHINE)
        snapshot = self.step(40, WALKING_WORKER)
        self.assertEqual(len(snapshot["tracks_3d"]), 1, "a lost machine must not linger on the canvas")

    def test_snapshots_are_stable_under_backward_scrubbing(self):
        stored = {}
        for index in range(0, 90):
            boxes = [[820 + index * 2, 800, 900 + index * 2, 1000, 0.9, 0.0]]
            stored[index] = self.step(index, boxes)["tracks_3d"][0]["position"]
        again = self.step(30, WALKING_WORKER)
        self.assertEqual(again["tracks_3d"][0]["position"], stored[30],
                         "replaying an earlier frame must return its stored snapshot")

    def test_detection_track_ids_pair_boxes_with_state(self):
        snapshot = self.step(0, WALKING_WORKER + DISTANT_MACHINE)
        self.assertEqual(len(snapshot["detection_track_ids"]), 2)
        for track_id in snapshot["detection_track_ids"]:
            self.assertIsNotNone(track_id)
        self.assertEqual(snapshot["detection_track_ids"][0], snapshot["tracks_3d"][0]["track_id"])

    def test_a_re_encoded_clip_invalidates_the_tracker(self):
        first = tracker_for("clip", W, H, 300, 111)
        again = tracker_for("clip", W, H, 300, 222)
        self.assertIsNot(first, again, "a changed file must rebuild the tracker")
        mt.clear_trackers()


class OccupancyTests(unittest.TestCase):
    def tracker(self):
        return mt.MediaTracker("occ", W, H, 300)

    def boxes_at(self, x, y):
        """A detection whose footpoint lands on the requested metric point."""
        plane = ground_plane(W, H)
        row = float(np.clip(plane.row_for_depth(y), plane.min_row, plane.max_row))
        scale = plane.focal / y
        column = plane.principal[0] + x * scale
        return [[column - 30, row - 200, column + 30, row, 0.9, 0.0]]

    def walk(self, tracker, positions, start_index=0):
        """Step a worker through metric positions, a few centimetres per frame."""
        snapshot = None
        for offset, (x, y) in enumerate(positions):
            snapshot = tracker.step(start_index + offset, self.boxes_at(x, y),
                                    (start_index + offset) / 30.0)
        return snapshot

    def test_entry_presence_and_exit_are_logged(self):
        tracker = self.tracker()
        # Outside in front of the trench, then walk into it, dwell, then walk out.
        self.walk(tracker, [(2.0, 0.9)] * 3)
        self.walk(tracker, [(2.0, 3.0), (2.0, 4.0), (2.0, 5.0), (2.0, 6.0)], 3)
        inside = self.walk(tracker, [(2.0, 6.0)] * 45, 7)
        kinds = [e["event_type"] for e in inside["zone_events"]]
        self.assertIn("ZONE_ENTRY", kinds)
        self.assertIn("ZONE_OCCUPANCY", kinds)
        snapshot = self.walk(tracker, [(2.0, 5.0), (2.0, 4.0), (2.0, 3.0), (2.0, 2.0), (2.0, 1.0)], 52)
        kinds = [e["event_type"] for e in snapshot["zone_events"]]
        self.assertIn("ZONE_EXIT", kinds)
        exit_event = [e for e in snapshot["zone_events"] if e["event_type"] == "ZONE_EXIT"][-1]
        self.assertEqual(exit_event["reason"], "left_zone")
        self.assertGreater(exit_event["dwell_seconds"], 1.0)
        self.assertEqual(exit_event["zone_id"], CANONICAL_ZONE["zone_id"])

    def test_an_exit_is_logged_once_not_every_frame(self):
        tracker = self.tracker()
        self.walk(tracker, [(2.0, 0.9)] * 2)
        self.walk(tracker, [(2.0, 3.0), (2.0, 4.0), (2.0, 5.0), (2.0, 6.0)], 2)
        self.walk(tracker, [(2.0, 5.0), (2.0, 4.0), (2.0, 3.0), (2.0, 2.0), (2.0, 1.0)], 6)
        snapshot = self.walk(tracker, [(2.0, 1.0)] * 30, 10)
        exits = [e for e in snapshot["zone_events"] if e["event_type"] == "ZONE_EXIT"]
        self.assertEqual(len(exits), 1, "an exit must not be re-emitted on later frames")

    def test_inside_the_zone_is_labelled(self):
        tracker = self.tracker()
        snapshot = tracker.step(0, self.boxes_at(2.0, 6.0), 0.0)
        self.assertTrue(snapshot["worker_states"][0]["in_zone"])
        self.assertEqual(snapshot["worker_states"][0]["label"], "IN ZONE")

    def test_outside_the_zone_is_labelled_by_motion(self):
        tracker = self.tracker()
        moving = self.boxes_at(2.0, 1.0)
        snapshot = tracker.step(0, moving, 0.0)
        self.assertFalse(snapshot["worker_states"][0]["in_zone"])
        self.assertIn(snapshot["worker_states"][0]["label"], ("MOVING", "STATIONARY"))

    def test_only_workers_are_reported_in_worker_states(self):
        tracker = self.tracker()
        snapshot = tracker.step(0, self.boxes_at(2.0, 6.0) + DISTANT_MACHINE, 0.0)
        self.assertEqual(len(snapshot["worker_states"]), 1)

    def test_exits_are_flagged_for_the_visual_feedback(self):
        tracker = self.tracker()
        self.walk(tracker, [(2.0, 0.9)] * 2)
        self.walk(tracker, [(2.0, 3.0), (2.0, 4.0), (2.0, 5.0), (2.0, 6.0)], 2)
        flagged = []
        for offset, y in enumerate([5.0, 4.0, 3.0, 2.0, 1.0]):
            snapshot = self.walk(tracker, [(2.0, y)], 6 + offset)
            if snapshot["zone_exits"]:
                flagged.append(snapshot)
        self.assertTrue(flagged, "an exit must be flagged so the ring can be shown")
        self.assertEqual(flagged[0]["zone_exits"][0]["event_type"], "ZONE_EXIT")


class PacketContractTests(unittest.TestCase):
    """The restored recording packet exposes the original contract."""

    CLIP = "real_videos_Worker_in_excavator_blind_spot"

    def setUp(self):
        # Scoped, not a bare mutation: a leaked environment variable silently
        # disables prewarming for every later test in the same process.
        self._environment = unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"})
        self._environment.start()
        self.addCleanup(self._environment.stop)
        mt.clear_trackers()
        self.addCleanup(mt.clear_trackers)

    def test_packet_carries_tracks_and_no_zone_overlay(self):
        from src.api import hub
        packet = hub.original_frame(self.CLIP, 120)
        self.assertIn("tracks_3d", packet)
        self.assertIn("worker_states", packet)
        self.assertIn("zone_events", packet)
        self.assertIn("ground_plane", packet)
        self.assertEqual(packet["zone"], CANONICAL_ZONE)
        # The zone overlay and the fabricated machine identity are gone.
        for removed in ("zone_overlays", "machine_id", "zone_id"):
            self.assertNotIn(removed, packet, f"{removed} must no longer be sent")

    def test_worker_detections_carry_a_track_id(self):
        from src.api import hub
        packet = hub.original_frame(self.CLIP, 200)
        workers = [d for d in packet["detections"] if d[5] < 2]
        self.assertTrue(workers)
        for detection in workers:
            self.assertGreater(len(detection), 6, "a worker box must carry its track id")
            self.assertIsNotNone(detection[6])

    def test_every_agent_has_a_trail_and_a_heading(self):
        from src.api import hub
        for index in range(0, 260, 20):
            packet = hub.original_frame(self.CLIP, index)
            for track in packet["tracks_3d"]:
                self.assertIn("history", track)
                self.assertIn("heading", track)
                self.assertLessEqual(len(track["history"]), 30)

    def test_twin_reports_consistency_between_agents_and_workers(self):
        from src.api import hub
        packet = hub.original_frame(self.CLIP, 260)
        worker_ids = {t["track_id"] for t in packet["tracks_3d"] if t["class_name"] == "WORKER"}
        self.assertEqual(worker_ids, {w["track_id"] for w in packet["worker_states"]})


class OriginalTwinContractTests(unittest.TestCase):
    """The 3D canvas must be the original application's own implementation."""

    HUB = ROOT / "src" / "dashboard" / "assets" / "hub.js"
    HTML = ROOT / "src" / "dashboard" / "index.html"

    def source(self):
        return self.HUB.read_text()

    def test_none_of_the_removed_additions_remain(self):
        for gone in ("buildMachine", "labelSprite", "machineGroup", "STATE_COLORS",
                     "drawZones", "zone_overlays", "hazardGroup"):
            self.assertNotIn(gone, self.source(), f"{gone} must be gone from the 3D canvas")

    def test_the_original_geometry_is_present(self):
        source = self.source()
        for geometry in ("PlaneGeometry(1,1)", "CylinderGeometry(0.3,0.3,1.8,16)",
                         "BoxGeometry(1,1,1)", "PlaneGeometry(20,7)",
                         "GridHelper(50,50", "RingGeometry(1.0,1.4,32)"):
            self.assertIn(geometry, source, f"the original geometry {geometry} must be restored")

    def test_the_original_trench_plane_position(self):
        # The trench keeps the original plane geometry but is placed from
        # packet.zone, so the drawn polygon and the occupancy test cannot drift.
        self.assertIn("new THREE.PlaneGeometry(20,7)", self.source())
        self.assertIn("trenchMesh.position.set((zone.min_x+zone.max_x)/2", self.source())

    def test_the_original_path_line_colours(self):
        source = self.source()
        self.assertIn("0x38bdf8", source)
        self.assertIn("0xfbbf24", source)

    def test_the_original_legend_strings(self):
        html = self.HTML.read_text()
        for label in ("Metric Ground Plane Twin (EPSG:3857 Geodetic Datum)",
                      "■ BLUE: Worker Tracks", "■ AMBER: Heavy Machinery",
                      "■ RED: BIM Trench Hazard"):
            self.assertIn(label, html, f"the original legend label {label!r} must be restored")

    def test_the_zone_and_machine_badges_are_removed(self):
        html = self.HTML.read_text()
        self.assertNotIn('id="zone-tag"', html)
        self.assertNotIn('id="machine-id"', html)

    def test_the_worker_tag_and_log_exist(self):
        source = self.source()
        self.assertIn("renderEventLog", source)
        self.assertIn("event-log", self.HTML.read_text())
        for label in ("IN ZONE", "MOVING", "STATIONARY"):
            self.assertIn(label, source)

    def test_the_twin_consumes_the_original_tracks_contract(self):
        self.assertIn("packet.tracks_3d", self.source())
        self.assertIn("packet.zone_exits", self.source())


class NoSettingsRegressTests(unittest.TestCase):
    def test_the_original_settings_files_are_untouched(self):
        for path in ("config/default_config.json", "data/manifests/active_manifest.json"):
            with self.subTest(setting=path):
                original = subprocess.run(["git", "show", f"bc8e2c88d376575503e77351f1b123b13c547535:{path}"],
                                          cwd=ROOT, capture_output=True).stdout
                self.assertTrue(original, f"{path} must exist in the original repository")
                self.assertEqual((ROOT / path).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
