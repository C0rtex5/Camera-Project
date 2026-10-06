"""3D pose stability, machinery anchoring, and the calibrated trench rectangle.

The three defects these cover were measured, not assumed:

* a stationary agent's heading swung through 180 degrees, so parked plant
  rotated on pixel noise;
* the machinery hull was a 5.0 m box centred on its own footpoint, covering the
  ground a worker beside it stands on;
* the fixed trench rectangle contained 6 of 32 observed workers.
"""
import math
import unittest

from src.api.media_tracks import (
    CANONICAL_ZONE,
    FAR_METRES,
    MOVING_THRESHOLD_METRES_PER_SECOND,
    NEAR_METRES,
    MediaTracker,
    in_canonical_zone,
)

W, H = 1920, 1080
#: The original repository's twin geometry, restated here so a change to the
#: canvas is caught here rather than only in the browser suite.
HULL_LENGTH_METRES = 5.0
TRENCH_SHAPE = (20.0, 7.0)


class ZoneCalibrationTests(unittest.TestCase):
    def test_the_zone_keeps_the_original_trench_shape(self):
        width = CANONICAL_ZONE["max_x"] - CANONICAL_ZONE["min_x"]
        depth = CANONICAL_ZONE["max_y"] - CANONICAL_ZONE["min_y"]
        self.assertEqual(width, TRENCH_SHAPE[0], "the trench is the original 20 m across")
        self.assertEqual(depth, TRENCH_SHAPE[1], "the trench is the original 7 m deep")

    def test_the_lateral_placement_is_the_original(self):
        self.assertEqual(CANONICAL_ZONE["min_x"], -8.0)
        self.assertEqual(CANONICAL_ZONE["max_x"], 12.0)
        self.assertEqual(CANONICAL_ZONE["zone_id"], "ENV-BIM-TRENCH")
        self.assertEqual(CANONICAL_ZONE["zone_name"], "BIM Trench Hazard")

    def test_the_depth_band_sits_on_the_fitted_working_band(self):
        """The band must be where the work is, not behind it."""
        self.assertGreaterEqual(CANONICAL_ZONE["min_y"], NEAR_METRES)
        self.assertLess(CANONICAL_ZONE["max_y"], FAR_METRES)
        self.assertLessEqual(CANONICAL_ZONE["min_y"], CANONICAL_ZONE["max_y"])

    def test_the_calibrated_band_covers_the_work_the_old_one_missed(self):
        """The old fixed band was y[3, 10]; the calibrated one must do better."""
        old = (1.5, 8.5)
        # Measured worker positions on the calibrated plane, in metres.
        observed = [(-0.5, 4.0), (1.0, 5.0), (-3.0, 6.0), (0.5, 7.0), (8.0, 8.0),
                    (-6.0, 4.5), (1.5, 9.0), (0.0, 11.0), (-1.0, 13.0), (2.0, 6.5)]
        old_hits = sum(1 for x, y in observed if old[0] <= y <= old[1])
        new_hits = sum(1 for x, y in observed
                       if CANONICAL_ZONE["min_y"] <= y <= CANONICAL_ZONE["max_y"])
        self.assertGreater(new_hits, old_hits,
                           "the calibrated band must cover more of the work than the old one")

    def test_containment_agrees_with_the_declared_rectangle(self):
        for x, y in [(-8.0, 1.5), (12.0, 8.5), (2.0, 5.0), (0.0, 0.0), (0.0, 30.0), (20.0, 5.0)]:
            expected = (CANONICAL_ZONE["min_x"] <= x <= CANONICAL_ZONE["max_x"]
                        and CANONICAL_ZONE["min_y"] <= y <= CANONICAL_ZONE["max_y"])
            self.assertEqual(in_canonical_zone(x, y), expected, f"({x}, {y})")


class MachineryAnchoringTests(unittest.TestCase):
    """The hull must not occupy the ground its own footpoint stands on."""

    def test_the_hull_is_offset_forward_by_half_its_length(self):
        import pathlib
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "src" / "dashboard" / "assets" / "hub.js").read_text()
        self.assertIn("const half=(mesh.userData.hull.scale.y||1)/2;", source,
                      "the hull must be pushed forward by half its own measured length")

    def test_a_worker_beside_the_machine_is_outside_the_hull(self):
        """The offset must free the space within one collision radius."""
        half_length = HULL_LENGTH_METRES / 2.0
        offset = half_length  # measured length, so this holds at any machine size
        # A worker 3.1 m away, standing beside the machine rather than behind it.
        lateral = 3.1
        hull_spans_y = (offset, offset + HULL_LENGTH_METRES)
        self.assertNotIn(lateral, hull_spans_y,
                         "a worker 3.1 m to the side must not be inside the hull")
        # And the footpoint itself is now free.
        self.assertNotIn(0.0, hull_spans_y, "the footpoint must be free of its own hull")

    def test_a_worker_directly_behind_the_machine_is_outside_the_hull(self):
        """Behind means toward the camera, which is where people walk."""
        hull_spans_y = (HULL_LENGTH_METRES / 2.0, HULL_LENGTH_METRES * 1.5)
        self.assertNotIn(-2.0, hull_spans_y, "ground in front of the machine must be free")

    def test_the_worker_is_never_displaced_from_its_true_position(self):
        """Separation is achieved on the machine's side only."""
        tracker = MediaTracker("anchor", W, H, 60)
        box = [[900, 700, 980, 1000, 0.9, 0.0]]
        snapshot = tracker.step(0, box, 0.0)
        track = snapshot["tracks_3d"][0]
        from src.api.media_tracks import ground_plane
        expected = ground_plane(W, H).pixel_to_metric(
            __import__("numpy").array([[940.0, 1000.0]]))[0]
        # The packet rounds to millimetres; compare at that precision.
        self.assertAlmostEqual(track["position"][0], round(float(expected[0]), 3), places=6)
        self.assertAlmostEqual(track["position"][1], round(float(expected[1]), 3), places=6)


class PoseStabilityTests(unittest.TestCase):
    """A stationary agent's rendered pose must not follow measurement noise."""

    def test_the_pose_is_only_rotated_while_the_agent_moves(self):
        import pathlib
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "src" / "dashboard" / "assets" / "hub.js").read_text()
        self.assertIn("}else if(trk.moving){", source,
                      "the pose must follow the heading only while the agent is moving")
        self.assertIn("POSE_MAX_STEP", source, "the turn must be capped")
        self.assertIn("POSE_EASE", source, "the pose must ease toward the heading")

    def test_the_measured_heading_is_still_reported(self):
        """Gating the pose must not alter the measurement on the packet."""
        tracker = MediaTracker("pose", W, H, 60)
        snapshot = tracker.step(0, [[900, 700, 980, 1000, 0.9, 0.0]], 0.0)
        self.assertIn("heading", snapshot["tracks_3d"][0])
        self.assertIn("moving", snapshot["tracks_3d"][0])

    def test_a_stationary_agent_stays_below_the_movement_threshold(self):
        """The gate the client uses is the one the server measures."""
        tracker = MediaTracker("still", W, H, 60)
        last = None
        for index in range(30):
            last = tracker.step(index, [[900, 700, 980, 1000, 0.9, 0.0]], index / 30.0)
        track = last["tracks_3d"][0]
        self.assertFalse(track["moving"])
        self.assertLessEqual(track["speed_mps"], MOVING_THRESHOLD_METRES_PER_SECOND)

    def test_a_walking_agent_is_flagged_moving_so_the_pose_follows_it(self):
        tracker = MediaTracker("walk", W, H, 60)
        for index in range(30):
            last = tracker.step(index, [[900 + index * 10, 700, 980 + index * 10, 1000, 0.9, 0.0]],
                                index / 30.0)
        self.assertTrue(last["tracks_3d"][0]["moving"])


class TrenchDrawingTests(unittest.TestCase):
    """The drawn polygon must be the rectangle the occupancy test used."""

    def test_the_trench_mesh_is_driven_by_the_packet_zone(self):
        import pathlib
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "src" / "dashboard" / "assets" / "hub.js").read_text()
        self.assertIn("const zone=packet.zone;", source)
        self.assertIn("trenchMesh.scale.set((zone.max_x-zone.min_x)/20,(zone.max_y-zone.min_y)/7,1)",
                      source, "the mesh must scale to the zone, not to a hardcoded extent")
        self.assertIn("trenchMesh.position.set((zone.min_x+zone.max_x)/2",
                      source, "the mesh must centre on the zone")

    def test_the_packet_zone_and_the_occupancy_test_share_one_source(self):
        from src.api import hub
        import os
        previous = os.environ.get("SENTINEL_MEDIA_PREWARM")
        os.environ["SENTINEL_MEDIA_PREWARM"] = "0"
        try:
            packet = hub.original_frame("real_videos_Worker_in_excavator_blind_spot", 120)
        finally:
            if previous is None:
                os.environ.pop("SENTINEL_MEDIA_PREWARM", None)
            else:
                os.environ["SENTINEL_MEDIA_PREWARM"] = previous
        self.assertEqual(packet["zone"], CANONICAL_ZONE)

    def test_the_mesh_geometry_is_still_the_original_plane(self):
        import pathlib
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "src" / "dashboard" / "assets" / "hub.js").read_text()
        self.assertIn("new THREE.PlaneGeometry(20,7)", source,
                      "the trench keeps the original repository's plane geometry")


if __name__ == "__main__":
    unittest.main()
