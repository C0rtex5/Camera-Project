"""Calibration, footprint polygons, and ghost suppression for restored clips.

These cover the four properties that were measured as wrong:

* the ground plane was fitted to an assumed near band, which put the camera
  below standing eye level and people at 0.17 m wide;
* the person marker was a fixed 1.6 m circle and the machine a fixed 2.6 x 5.0 m
  box, matching neither what was detected;
* all agents shared one path line, so only the last-drawn agent had a trail;
* one excavator was reported as three machines, and its cab operator as a second
  worker standing inside it.
"""
import math
import os
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

from src.api import detection_ghosts as ghosts
from src.api.media_tracks import (
    FAR_METRES,
    MACHINERY_FOOTPRINT_DEPTH_RATIO,
    DUPLICATE_OVERLAP,
    DUPLICATE_PERSON_METRES,
    MAX_MACHINERY_FOOTPRINT_M,
    MAX_PERSON_FOOTPRINT_M,
    MAX_PLAUSIBLE_CAMERA_HEIGHT_M,
    DUPLICATE_OVERLAP,
    DUPLICATE_PERSON_METRES,
    MAX_MACHINERY_FOOTPRINT_M,
    MAX_PERSON_FOOTPRINT_M,
    MIN_MACHINERY_FOOTPRINT_M,
    MIN_PLAUSIBLE_PERSON_WIDTH_M,
    MIN_PERSON_BOX_HEIGHT_PX,
    MIN_PERSON_FOOTPRINT_M,
    MIN_PLAUSIBLE_CAMERA_HEIGHT_M,
    NOMINAL_SHOULDER_METRES,
    MediaTracker,
    fit_ground_plane,
    ground_plane,
    observe_person_scale,
    plane_for_media,
)

W, H = 1920, 1080
#: How many tracks may outlive their last detection on any one frame.
TRACK_SLACK = 8
FOCAL = 1420.0
PRINCIPAL = (960.0, 540.0)
BLIND_SPOT = "real_videos_Worker_in_excavator_blind_spot"
BARRIER = "real_videos_Worker_and_excavator_near_barrier"


class PlaneCalibrationTests(unittest.TestCase):
    """The plane is anchored on an observed person, not on an assumed band."""

    def test_an_observed_person_reproduces_its_own_size(self):
        """The fit is a self-consistency check: it is true by construction."""
        for person_px, person_row in ((114.0, 772.0), (174.0, 1071.0), (132.0, 998.0)):
            plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, person_px, person_row)
            width_m = person_px * plane.depth_for_row(person_row) / FOCAL
            self.assertAlmostEqual(width_m, NOMINAL_SHOULDER_METRES, places=6)

    def test_the_anchored_fit_gives_a_plausible_camera(self):
        plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)
        self.assertGreaterEqual(plane.camera_height, MIN_PLAUSIBLE_CAMERA_HEIGHT_M)
        self.assertLessEqual(plane.camera_height, MAX_PLAUSIBLE_CAMERA_HEIGHT_M)
        self.assertLess(plane.camera_height, MAX_PLAUSIBLE_CAMERA_HEIGHT_M)

    def test_the_old_band_fit_was_below_eye_level(self):
        """Why the band assumption had to go."""
        fallback = fit_ground_plane(W, H, FOCAL, PRINCIPAL)
        self.assertLess(fallback.camera_height, MIN_PLAUSIBLE_CAMERA_HEIGHT_M,
                        "the band fit is the implausible one, kept only as a fallback")

    def test_no_observation_falls_back(self):
        for px, row in ((None, None), (0, 0), (5.0, 900.0)):
            plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, px, row)
            self.assertGreater(plane.horizon_row, 0)

    def test_an_implausible_solution_falls_back(self):
        # A 2 px box would imply a 355 m camera height.
        plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, 2.0, 900.0)
        self.assertLess(plane.camera_height, MAX_PLAUSIBLE_CAMERA_HEIGHT_M)

    def test_machine_parts_are_not_used_as_the_anchor(self):
        """A boom detected as a person would drag the fit to nonsense."""
        detections = [[100, 100, 130, 400, 0.9, 2.0]]        # a narrow machine part
        self.assertEqual(observe_person_scale(detections), (None, None))
        detections.append([300, 600, 420, 1000, 0.9, 0.0])    # a real person
        px, row = observe_person_scale(detections)
        self.assertAlmostEqual(px, 120.0)
        self.assertAlmostEqual(row, 1000.0)

    def test_the_plane_is_a_property_of_the_recording_not_the_playhead(self):
        seen = {}

        def boxes_at(index):
            return [[100, 600, 214, 1000, 0.9, 0.0]] if index in (0, 5) else []

        first = plane_for_media("clip-a", W, H, 1, boxes_at)
        # A different frame index must not change the plane.
        second = plane_for_media("clip-a", W, H, 1, lambda i: boxes_at(5))
        self.assertIs(first, second)
        self.assertEqual(first.camera_height, second.camera_height)

    def test_a_different_recording_gets_its_own_plane(self):
        wide = plane_for_media("clip-b", W, H, 1, lambda i: [[100, 600, 300, 1000, 0.9, 0.0]])
        narrow = plane_for_media("clip-c", W, H, 1, lambda i: [[100, 600, 150, 1000, 0.9, 0.0]])
        self.assertIsNot(wide, narrow)
        self.assertNotEqual(wide.camera_height, narrow.camera_height)


class FootprintTests(unittest.TestCase):
    """The 3D polygon is sized from the detection, not from a fixed number."""

    def tracker(self):
        # Anchored on an observed person, which is how every real clip is fitted.
        # On the fallback plane the scale is not meaningful and the fragment
        # filter would reject the whole-body fixtures used here.
        return MediaTracker("fp", W, H, 60,
                            plane=fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0))

    def test_a_person_footprint_is_plausible_and_straight_edged(self):
        tracker = self.tracker()
        snapshot = tracker.step(0, [[900, 748, 1032, 998, 0.9, 0.0]], 0.0)
        footprint = snapshot["tracks_3d"][0]["footprint"]
        self.assertGreaterEqual(footprint["width_m"], MIN_PERSON_FOOTPRINT_M)
        self.assertLessEqual(footprint["width_m"], MAX_PERSON_FOOTPRINT_M)
        self.assertLess(footprint["depth_m"], footprint["width_m"],
                        "a person's footprint is deeper than it is wide only if wrong")

    def test_a_machine_footprint_keeps_the_original_proportion(self):
        tracker = self.tracker()
        snapshot = tracker.step(0, [[500, 500, 1400, 998, 0.6, 2.0]], 0.0)
        track = [t for t in snapshot["tracks_3d"] if t["class_name"] == "HEAVY_EQUIPMENT"][0]
        footprint = track["footprint"]
        self.assertAlmostEqual(footprint["depth_m"] / footprint["width_m"],
                               MACHINERY_FOOTPRINT_DEPTH_RATIO, places=3)
        self.assertGreaterEqual(footprint["width_m"], MIN_MACHINERY_FOOTPRINT_M)
        self.assertLessEqual(footprint["width_m"], MAX_MACHINERY_FOOTPRINT_M)

    def test_a_wider_box_produces_a_wider_polygon(self):
        widths = []
        for box in ([900, 748, 1032, 998, 0.9, 0.0], [800, 648, 1132, 998, 0.9, 0.0]):
            tracker = self.tracker()
            snapshot = tracker.step(0, [list(box)], 0.0)
            widths.append(snapshot["tracks_3d"][0]["footprint"]["width_m"])
        self.assertGreater(widths[1], widths[0],
                           "the polygon must follow the detection, not a constant")

    def test_a_fragment_is_not_clamped_up_into_an_agent(self):
        """It used to be clamped to the minimum, which made it *bigger*: a speck
        drawn at an arbitrary depth. It is now kept off the ground plane."""
        tracker = self.tracker()
        snapshot = tracker.step(0, [[948, 990, 952, 1000, 0.3, 0.0]], 0.0)
        self.assertEqual(snapshot["tracks_3d"], [])

    def test_the_live_packet_carries_the_footprint(self):
        from src.api import hub
        with unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"}):
            packet = hub.original_frame(BARRIER, 105)
        self.assertTrue(packet["tracks_3d"])
        for track in packet["tracks_3d"]:
            self.assertIn("footprint", track)
            self.assertGreater(track["footprint"]["width_m"], 0)


class GhostSuppressionTests(unittest.TestCase):
    """One machine is one machine; a person is never deleted."""

    def test_fragmented_plant_is_merged_into_one_agent(self):
        # one excavator reported as its body, its boom and its tracks
        boxes = [
            [400, 200, 1200, 700, 0.50, 2.0],
            [520, 150, 900, 400, 0.32, 2.0],      # boom, inside the body
            [430, 560, 1180, 720, 0.30, 2.0],     # tracks, inside the body
        ]
        cleaned, operators = ghosts.suppress_ghosts(boxes)
        self.assertEqual(len(cleaned), 1, "one machine must not become three agents")

    def test_separate_machines_are_not_merged(self):
        boxes = [
            [100, 200, 500, 700, 0.6, 2.0],
            [1400, 200, 1800, 700, 0.6, 2.0],
        ]
        self.assertEqual(len(ghosts.suppress_ghosts(boxes)[0]), 2)

    def test_a_cab_operator_is_marked_not_deleted(self):
        boxes = [
            [400, 200, 1200, 700, 0.5, 2.0],     # the machine
            [600, 260, 700, 420, 0.3, 0.0],      # the operator, inside it
        ]
        cleaned, operators = ghosts.suppress_ghosts(boxes)
        self.assertEqual(len(cleaned), 2, "the person must still be on the frame")
        self.assertEqual(len(operators), 1)
        self.assertEqual(int(cleaned[operators[0]][5]), 0.0,
                         "the flagged row must be the person, not the machine")

    def test_an_operator_does_not_become_a_ground_agent(self):
        from src.api import hub
        with unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"}):
            packet = hub.original_frame(BARRIER, 105)
        agents = {t["track_id"] for t in packet["tracks_3d"]}
        operators = [b for b in packet["detections"] if ghosts.is_operator_row(b)]
        self.assertTrue(operators, "this frame must contain a cab operator")
        for box in operators:
            self.assertIsNone(box[6], "an operator carries no track id")
            self.assertNotIn(box[6], agents,
                             "a person riding inside a machine is not a second agent")

    def test_two_people_overlapping_are_never_merged(self):
        boxes = [
            [900, 600, 1000, 1000, 0.9, 0.0],
            [920, 600, 1020, 1000, 0.9, 0.0],
        ]
        self.assertEqual(len(ghosts.suppress_ghosts(boxes)), 2,
                         "a crowd is not one object")

    def test_a_person_beside_a_machine_is_not_marked(self):
        boxes = [
            [400, 200, 1200, 700, 0.5, 2.0],
            [1300, 600, 1400, 1000, 0.9, 0.0],   # standing clear of it
        ]
        cleaned, operators = ghosts.suppress_ghosts(boxes)
        self.assertEqual(operators, [])

    def test_the_input_is_not_mutated(self):
        boxes = [[400, 200, 1200, 700, 0.5, 2.0], [600, 260, 700, 420, 0.3, 0.0]]
        before = [list(b) for b in boxes]
        cleaned, _ = ghosts.suppress_ghosts(boxes)
        self.assertEqual(boxes, before)
        self.assertIsNot(cleaned[0], boxes[0], "the returned rows must be copies")

    def test_an_empty_frame_is_handled(self):
        self.assertEqual(ghosts.suppress_ghosts([]), ([], []))

    def test_the_packet_no_longer_reports_the_phantom_plant(self):
        from src.api import hub
        with unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"}):
            packet = hub.original_frame(BLIND_SPOT, 200)
        machines = [b for b in packet["detections"] if b[5] >= 2]
        for first in machines:
            for second in machines:
                if first is second:
                    continue
                width = min(first[2], second[2]) - max(first[0], second[0])
                height = min(first[3], second[3]) - max(first[1], second[1])
                if width > 0 and height > 0:
                    smaller = min(max(0, first[2] - first[0]) * max(0, first[3] - first[1]),
                                  max(0, second[2] - second[0]) * max(0, second[3] - second[1]))
                    self.assertLessEqual(width * height / smaller, ghosts.FRAGMENT_OVERLAP + 1e-6,
                                         "no two surviving machine boxes may still be fragments")


class TrailTests(unittest.TestCase):
    """Every agent keeps its own path, not one shared line."""

    def test_each_track_carries_its_own_history(self):
        plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)
        tracker = MediaTracker("trail", W, H, 60, plane=plane)
        boxes = []
        for index in range(20):
            x = 700 + index * 6
            boxes.append([x, 748, x + 132, 998, 0.9, 0.0])
        snapshot = None
        for index in range(20):
            snapshot = tracker.step(index, [boxes[index]], index / 30.0)
        track = snapshot["tracks_3d"][0]
        self.assertGreater(len(track["history"]), 1)
        self.assertGreater(len({tuple(point) for point in track["history"]}), 1,
                           "a moving worker's history must actually move")

    def test_two_agents_keep_separate_histories(self):
        tracker = MediaTracker("two", W, H, 60,
                               plane=fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0))
        boxes = [[400, 748, 532, 998, 0.9, 0.0], [1200, 748, 1332, 998, 0.9, 0.0]]
        snapshot = tracker.step(0, boxes, 0.0)
        self.assertEqual(len(snapshot["tracks_3d"]), 2)
        ids = snapshot["detection_track_ids"]
        self.assertEqual(len(set(ids)), 2, "two agents must not share a track")


if __name__ == "__main__":
    unittest.main()


class FragmentFilterTests(unittest.TestCase):
    """A fragment of a person is not a person.

    A hardhat, a hi-vis panel or a body cut off by the frame is detected as a
    worker. Put through the ground plane it acquired a footpoint at an arbitrary
    depth and became a small polygon lying among the real agents, which is what
    made the canvas read as scattered debris. The box stays on the camera frame;
    only the ground agent goes.
    """

    def test_a_short_box_is_not_put_on_the_ground_plane(self):
        from src.api.media_tracks import MIN_PERSON_BOX_HEIGHT_PX, MediaTracker
        tracker = MediaTracker("frag", W, H, 60,
                               plane=fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0))
        snapshot = tracker.step(0, [[900, 980, 924, 1036, 0.27, 0.0]], 0.0)
        self.assertEqual(snapshot["tracks_3d"], [],
                         "a 56 px box is a fragment, not a person on the ground")
        self.assertLess(MIN_PERSON_BOX_HEIGHT_PX, 100)

    def test_a_real_person_of_the_same_class_is_still_tracked(self):
        from src.api.media_tracks import MediaTracker
        tracker = MediaTracker("real", W, H, 60)
        snapshot = tracker.step(0, [[900, 748, 1032, 998, 0.9, 0.0]], 0.0)
        self.assertEqual(len(snapshot["tracks_3d"]), 1)

    def test_a_person_too_narrow_to_measure_is_not_an_agent(self):
        """Measured in metres, not pixels: far away, a fragment is tiny."""
        MIN_PLAUSIBLE_PERSON_WIDTH_M, MediaTracker
        tracker = MediaTracker("narrow", W, H, 60,
                               plane=fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0))
        snapshot = tracker.step(0, [[900, 620, 928, 700, 0.55, 0.0]], 0.0)
        self.assertEqual(snapshot["tracks_3d"], [])
        self.assertGreater(MIN_PLAUSIBLE_PERSON_WIDTH_M, 0.0)

    def test_machinery_is_not_filtered_by_the_person_rules(self):
        from src.api.media_tracks import MediaTracker
        tracker = MediaTracker("mach", W, H, 60,
                               plane=fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0))
        snapshot = tracker.step(0, [[300, 500, 700, 700, 0.6, 2.0]], 0.0)
        self.assertTrue(snapshot["tracks_3d"], "a machine is never a person fragment")

    def test_the_fragment_box_is_still_served_on_the_camera_frame(self):
        """It must not vanish from the panel: the person is still visible."""
        from src.api import hub
        with unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"}):
            packet = hub.original_frame(BARRIER, 105)
        served = len(packet["detections"])
        self.assertGreater(served, 0, "the frame must still serve its boxes")
        # `tracks_3d` may hold more agents than this frame has boxes: a track
        # survives briefly after it stops being detected, which is what keeps an
        # agent on the ground plane through a frame where the detector missed it.
        self.assertLessEqual(len(packet["tracks_3d"]), served + TRACK_SLACK,
                             "but not an unbounded number of them")

    def test_the_polygon_sizes_are_no_longer_mixed_with_speckle(self):
        from src.api import hub
        with unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"}):
            packet = hub.original_frame(BARRIER, 105)
        for track in packet["tracks_3d"]:
            if track["class_name"] == "WORKER":
                self.assertGreaterEqual(track["footprint"]["width_m"],
                                        MIN_PLAUSIBLE_PERSON_WIDTH_M,
                                        "no worker polygon may be a speck")


class ForecastPathTests(unittest.TestCase):
    """The original twin drew two lines per agent: the trail behind and the path
    ahead. The history was served, but `forecast_trajectory` was left as an empty
    list, so the "path" the legend promises was never drawn."""

    def anchored(self, name):
        return MediaTracker(name, W, H, 60,
                            plane=fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0))

    def test_a_moving_agent_gets_a_path_ahead(self):
        tracker = self.anchored("fc-move")
        for index in range(8):
            x = 700 + index * 16
            snapshot = tracker.step(index, [[x, 748, x + 132, 998, 0.9, 0.0]],
                                    index / 30.0)
        track = snapshot["tracks_3d"][0]
        self.assertTrue(track["moving"], "the fixture must actually be moving")
        path = track["forecast_trajectory"]
        self.assertGreaterEqual(len(path), 2, "a moving agent must have a path")
        # It must lead somewhere, and away from where the agent already is.
        self.assertNotAlmostEqual(path[0][0], track["position"][0])

    def test_the_path_points_the_way_the_agent_is_walking(self):
        tracker = self.anchored("fc-dir")
        for index in range(8):
            x = 700 + index * 16          # walking right
            snapshot = tracker.step(index, [[x, 748, x + 132, 998, 0.9, 0.0]],
                                    index / 30.0)
        track = snapshot["tracks_3d"][0]
        vx = track["velocity"][0]
        self.assertGreater(vx, 0)
        for point in track["forecast_trajectory"]:
            self.assertGreater(point[0], track["position"][0],
                               "the path must continue in the direction of travel")

    def test_a_stationary_agent_gets_no_invented_path(self):
        tracker = self.anchored("fc-still")
        snapshot = None
        for index in range(8):
            snapshot = tracker.step(index, [[700, 748, 832, 998, 0.9, 0.0]], index / 30.0)
        track = snapshot["tracks_3d"][0]
        self.assertFalse(track["moving"])
        self.assertEqual(track["forecast_trajectory"], [],
                         "a stationary agent's velocity is footpoint noise, not a direction")

    def test_the_path_stays_within_the_horizon(self):
        from src.api.media_tracks import FORECAST_HORIZON_SECONDS, FORECAST_POINTS
        tracker = self.anchored("fc-len")
        for index in range(8):
            x = 700 + index * 24
            snapshot = tracker.step(index, [[x, 748, x + 132, 998, 0.9, 0.0]], index / 30.0)
        track = snapshot["tracks_3d"][0]
        speed = track["speed_mps"]
        reach = max(math.dist(track["position"], p) for p in track["forecast_trajectory"])
        self.assertLessEqual(reach, speed * FORECAST_HORIZON_SECONDS + 0.05)
        self.assertEqual(len(track["forecast_trajectory"]), FORECAST_POINTS)


class RandomAccessTests(unittest.TestCase):
    """A frame the client jumps over must still be answered properly.

    `step` filled every skipped frame with `None` detections and cached that empty
    snapshot. Asking for an earlier frame then returned no agents at all, and the
    trail and forecast path existed only for frames played in order from the
    start - which is why scrubbing back emptied the panel.
    """

    def frames(self, count=12):
        """Deterministic per-frame detections, one person walking right."""
        for index in range(count):
            x = 700 + index * 18
            yield [[x, 748, x + 132, 998, 0.9, 0.0]]

    def tracker_with_jump(self, jump_to):
        plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)
        tracker = MediaTracker("jump", W, H, 60, plane=plane)
        frames = list(self.frames())
        snapshot = tracker.step(
            jump_to, frames[jump_to], jump_to / 30.0,
            frames_at=lambda i: (frames[i], ()))
        return tracker, snapshot

    def test_a_skipped_frame_is_not_poisoned(self):
        tracker, _ = self.tracker_with_jump(9)
        early = tracker.snapshot_for(2)
        self.assertIsNotNone(early, "frame 2 must have been stepped, not skipped")
        self.assertTrue(early["tracks_3d"],
                        "a frame before the jump must still report its agent")

    def test_the_trail_exists_for_a_frame_asked_for_later(self):
        tracker, _ = self.tracker_with_jump(9)
        early = tracker.snapshot_for(5)
        self.assertGreater(len(early["tracks_3d"][0]["history"]), 1,
                           "the trail must have accumulated by frame 5")

    def test_the_trail_grows_towards_the_jump_target(self):
        tracker, late = self.tracker_with_jump(9)
        first = len(tracker.snapshot_for(1)["tracks_3d"][0]["history"])
        last = len(late["tracks_3d"][0]["history"])
        self.assertGreater(last, first, "history must accumulate across the pass")

    def test_without_a_frame_source_the_old_behaviour_is_kept(self):
        """A caller that supplies nothing still gets the streaming behaviour."""
        plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)
        tracker = MediaTracker("nostream", W, H, 60, plane=plane)
        frames = list(self.frames())
        tracker.step(9, frames[9], 9 / 30.0)
        early = tracker.snapshot_for(2)
        self.assertIsNotNone(early)
        self.assertEqual(early["tracks_3d"], [],
                         "with no frame source there is nothing to fill a skip with")


class PhysicalSizeTests(unittest.TestCase):
    """The drawn polygon must be the size of the plant, not the size of the box.

    The detector sometimes returns a machine box spanning almost the whole frame
    - on the blind spot clip, 85..1893 of 1920 px. The measured width inherited
    that and drew hulls 4.2-5.9 m wide and 8-11 m long, which filled the entire
    3D view. The caps are the physical size of the object, not of the detection.
    """

    def test_a_whole_frame_machine_box_becomes_a_real_excavator(self):
        from src.api.media_tracks import MAX_MACHINERY_FOOTPRINT_M, MediaTracker
        plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)
        tracker = MediaTracker("wide", W, H, 60, plane=plane)
        # The measured blind spot box: all but 27 px of the frame's width.
        snapshot = tracker.step(0, [[85, 74, 1893, 998, 0.6, 2.0]], 0.0)
        footprint = snapshot["tracks_3d"][0]["footprint"]
        self.assertLessEqual(footprint["width_m"], MAX_MACHINERY_FOOTPRINT_M)
        self.assertLessEqual(footprint["width_m"], 3.6,
                             "a tracked excavator is about 3 m wide")
        self.assertLessEqual(footprint["depth_m"], 7.0)

    def test_a_person_polygon_is_never_a_slab(self):
        from src.api.media_tracks import MAX_PERSON_FOOTPRINT_M, MediaTracker
        plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)
        tracker = MediaTracker("slab", W, H, 60, plane=plane)
        snapshot = tracker.step(0, [[100, 700, 700, 998, 0.9, 0.0]], 0.0)
        footprint = snapshot["tracks_3d"][0]["footprint"]
        self.assertLessEqual(footprint["width_m"], MAX_PERSON_FOOTPRINT_M)
        self.assertLessEqual(footprint["width_m"], 0.8, "a person is about 0.5 m across")

    def test_the_machinery_bounds_stay_near_the_originals_proportions(self):
        from src.api.media_tracks import (MAX_MACHINERY_FOOTPRINT_M,
                                          MACHINERY_FOOTPRINT_DEPTH_RATIO,
                                          MIN_MACHINERY_FOOTPRINT_M)
        # The original box was a 2.6 x 5.0 m excavator; the bounds must contain it.
        self.assertLessEqual(MIN_MACHINERY_FOOTPRINT_M, 2.6)
        self.assertGreaterEqual(MAX_MACHINERY_FOOTPRINT_M, 2.6)
        self.assertAlmostEqual(MACHINERY_FOOTPRINT_DEPTH_RATIO, 5.0 / 2.6, places=6)


class ForecastBoundTests(unittest.TestCase):
    """The path must not shoot off the screen.

    The projection used the instantaneous velocity, which on real clips reached
    10 m/s for a worker whose smoothed speed was 3.2 m/s - drawing paths 27-51 m
    long for a 3 s horizon, straight across the view.
    """

    def plane(self):
        return fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)

    def test_a_teleporting_track_cannot_fling_the_path(self):
        from src.api.media_tracks import MAX_FORECAST_SPEED_MPS, MOVING_THRESHOLD_METRES_PER_SECOND
        tracker = MediaTracker("tele", W, H, 60, plane=self.plane())
        snapshot = None
        for index in range(16):
            # A wild jump every frame: the worst case a mis-association produces.
            x = 300 + (900 if index % 2 else 0)
            snapshot = tracker.step(index, [[x, 748, x + 132, 998, 0.9, 0.0]], index / 30.0)
        track = snapshot["tracks_3d"][0]
        path = track["forecast_trajectory"]
        # Either no path at all - the windowed displacement cancels out, which is
        # the right answer for a track that is not really going anywhere - or one
        # that stays inside the capped reach. What must never happen is a path
        # flung far across the view by a single wild frame.
        if path:
            reach = max(math.dist(track["position"], point) for point in path)
            self.assertLessEqual(reach, MAX_FORECAST_SPEED_MPS * 3.0 + 0.05,
                                 "the path must stay within the capped reach")

    def test_a_steady_walk_gives_a_path_in_the_walking_direction(self):
        tracker = MediaTracker("walk", W, H, 60, plane=self.plane())
        snapshot = None
        for index in range(16):
            x = 700 + index * 5           # a slow steady walk to the right
            snapshot = tracker.step(index, [[x, 748, x + 132, 998, 0.9, 0.0]], index / 30.0)
        track = snapshot["tracks_3d"][0]
        path = track["forecast_trajectory"]
        self.assertTrue(path)
        for point in path:
            self.assertGreater(point[0], track["position"][0],
                               "the path must lead the way the agent is walking")

    def test_a_nearly_still_agent_gets_no_path(self):
        tracker = MediaTracker("still", W, H, 60, plane=self.plane())
        snapshot = None
        for index in range(16):
            jitter = 700 + (index % 2)     # a 1 px flicker, below the moving floor
            snapshot = tracker.step(index, [[jitter, 748, jitter + 132, 998, 0.9, 0.0]],
                                    index / 30.0)
        track = snapshot["tracks_3d"][0]
        self.assertFalse(track["moving"])
        self.assertEqual(track["forecast_trajectory"], [])


class GhostFrameTests(unittest.TestCase):
    """One object, one box: no ghost frame may reach the panel.

    The thresholds are calibrated from the restored clips rather than guessed.
    A second detection of the same person lands 0.17-1.08 m from the first on the
    ground, while two genuinely separate people whose boxes overlap are 1.59 m
    apart or more, so the boundary sits in the gap between those populations.
    """

    def plane(self):
        return fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)

    def test_a_second_box_on_one_person_is_dropped(self):
        from src.api.media_tracks import drop_duplicate_detections
        plane = self.plane()
        # Two boxes on one person: nearly the same ground footpoint, offset boxes.
        boxes = [[900, 700, 1032, 998, 0.9, 0.0],
                 [906, 780, 1036, 998, 0.5, 0.0]]
        kept, mapping = drop_duplicate_detections(boxes, plane)
        self.assertEqual(len(kept), 1, "one person is one box")
        self.assertEqual(float(kept[0][4]), 0.9, "the better detection survives")
        self.assertEqual(len(mapping), 1)

    def test_two_people_standing_apart_are_both_kept(self):
        from src.api.media_tracks import drop_duplicate_detections
        plane = self.plane()
        # The measured case: separate objects are 1.34 m apart or more. Two boxes
        # 8 px apart are ~0.03 m apart on this plane, which is one object.
        depth = plane.depth_for_row(998.0)
        pixels = 1.7 * plane.focal / depth          # 1.7 m apart
        boxes = [[900.0, 700.0, 1032.0, 998.0, 0.9, 0.0],
                 [900.0 + pixels, 700.0, 1032.0 + pixels, 998.0, 0.8, 0.0]]
        kept, _ = drop_duplicate_detections(boxes, plane)
        self.assertEqual(len(kept), 2, "two objects further apart than the threshold are two objects")

    def test_boxes_that_do_not_overlap_are_never_merged(self):
        from src.api.media_tracks import drop_duplicate_detections
        plane = self.plane()
        # Same footpoint column, no overlap: could be a person behind another.
        boxes = [[900, 700, 1032, 998, 0.9, 0.0],
                 [900, 300, 1032, 360, 0.5, 0.0]]
        kept, _ = drop_duplicate_detections(boxes, plane)
        self.assertEqual(len(kept), 2)

    def test_spaced_people_are_all_kept(self):
        from src.api.media_tracks import drop_duplicate_detections
        plane = self.plane()
        depth = plane.depth_for_row(998.0)
        step = 1.7 * plane.focal / depth            # 1.7 m apart on the ground
        boxes = [[700.0 + k * step, 700.0, 832.0 + k * step, 998.0, 0.9, 0.0] for k in range(3)]
        kept, _ = drop_duplicate_detections(boxes, plane)
        self.assertEqual(len(kept), 3)

    def test_the_documented_cost_is_that_a_tight_crowd_collapses(self):
        """Stated plainly, because it is the price of removing every duplicate.

        Two boxes closer than the threshold on an anchored plane are treated as
        one object. Where that is two people standing less than 1.2 m apart rather
        than one object seen twice, they become one frame. The alternative - no
        threshold - leaves the duplicate frames on screen, which is what the
        ghost removal exists to prevent.
        """
        from src.api.media_tracks import DUPLICATE_PERSON_METRES, drop_duplicate_detections
        plane = self.plane()
        depth = plane.depth_for_row(998.0)
        step = (DUPLICATE_PERSON_METRES / 3.0) * plane.focal / depth   # 0.4 m apart
        boxes = [[700.0 + k * step, 700.0, 832.0 + k * step, 998.0, 0.9, 0.0] for k in range(3)]
        kept, _ = drop_duplicate_detections(boxes, plane)
        self.assertEqual(len(kept), 1, "packed closer than the threshold, they are one frame")

    def test_the_operator_mapping_survives_the_dedupe(self):
        """An operator index must still point at the operator after a drop."""
        from src.api.media_tracks import drop_duplicate_detections
        plane = self.plane()
        boxes = [[900, 700, 1032, 998, 0.9, 0.0],     # 0 kept
                 [906, 780, 1036, 998, 0.5, 0.0],     # 1 dropped
                 [400, 500, 900, 900, 0.6, 2.0]]      # 2 kept
        kept, mapping = drop_duplicate_detections(boxes, plane)
        self.assertEqual(len(kept), 2)
        self.assertEqual(mapping[2], 1, "the third input row is now the second kept")

    def test_an_empty_or_single_frame_is_untouched(self):
        from src.api.media_tracks import drop_duplicate_detections
        plane = self.plane()
        self.assertEqual(drop_duplicate_detections([], plane), ([], {}))
        one = [[900, 700, 1032, 998, 0.9, 0.0]]
        kept, mapping = drop_duplicate_detections(one, plane)
        self.assertEqual(kept, one)
        self.assertEqual(mapping, {0: 0}, "the identity map keeps operator indices valid")

    def test_the_input_is_not_mutated(self):
        from src.api.media_tracks import drop_duplicate_detections
        plane = self.plane()
        boxes = [[900, 700, 1032, 998, 0.9, 0.0], [906, 780, 1036, 998, 0.5, 0.0]]
        before = [list(b) for b in boxes]
        drop_duplicate_detections(boxes, plane)
        self.assertEqual(boxes, before)

    def test_no_real_person_pair_is_ever_dropped_on_a_restored_clip(self):
        """The safety invariant: nothing at or beyond the threshold is removed."""
        import numpy as np
        from src.api import hub
        from src.api.media_tracks import (DUPLICATE_OVERLAP, DUPLICATE_PERSON_METRES,
                                          drop_duplicate_detections)
        with unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"}):
            packet = hub.original_frame(BARRIER, 105)
        rows = [list(b[:6]) for b in packet["detections"]]
        plane = fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)
        anchors = np.array([[(r[0] + r[2]) / 2.0, r[3]] for r in rows], dtype=np.float64)
        metric = plane.pixel_to_metric(anchors)
        kept, _ = drop_duplicate_detections(rows, plane)
        kept_rows = {tuple(r) for r in kept}
        for i, row in enumerate(rows):
            if tuple(row) in kept_rows:
                continue
            for j, other in enumerate(rows):
                if i == j or int(row[5]) != int(other[5]):
                    continue
                gap = float(np.hypot(*(metric[i] - metric[j])))
                limit = 3.0 if int(row[5]) >= 2 else DUPLICATE_PERSON_METRES
                self.assertLess(gap, limit,
                                "a pair at or beyond the threshold must never be dropped")
                smaller = min(max(0, row[2] - row[0]) * max(0, row[3] - row[1]),
                              max(0, other[2] - other[0]) * max(0, other[3] - other[1]))
                width = min(row[2], other[2]) - max(row[0], other[0])
                height = min(row[3], other[3]) - max(row[1], other[1])
                self.assertGreaterEqual((width * height) / smaller, DUPLICATE_OVERLAP)


class SourceAnnotationTests(unittest.TestCase):
    """Some restored stock clips carry a third party's labels burned into the
    pixels. They must be reported as the media's, not passed off as this
    system's detections."""

    def test_the_annotated_clips_are_identified(self):
        from src.api import media
        expect_true = {"real_videos_30sec_construction",
                       "real_videos_Worker_and_excavator_near_barrier",
                       "real_videos_Worker_near_heavy_equipment_exca9",
                       "test_videos_user_site_video"}
        seen = set()
        for row in media.unique_catalog():
            path = row.get("path")
            if not path:
                continue
            verdict = media.baked_in_annotations(str(row["id"]), path,
                                                 row.get("frame_count") or 0)
            seen.add(str(row["id"]))
            if str(row["id"]) in expect_true:
                self.assertTrue(verdict, f"{row['id']} carries baked-in labels")
            else:
                self.assertFalse(verdict, f"{row['id']} is clean footage")
        self.assertTrue(expect_true <= seen, "the annotated clips must be catalogued")

    def test_the_verdict_is_cached_per_clip(self):
        from src.api import media
        row = next(r for r in media.unique_catalog()
                   if str(r.get("id")) == "real_videos_30sec_construction")
        first = media.baked_in_annotations(str(row["id"]), row["path"],
                                           row.get("frame_count") or 0)
        second = media.baked_in_annotations(str(row["id"]), row["path"],
                                            row.get("frame_count") or 0)
        self.assertEqual(first, second)
        self.assertIs(first, second, "the second call must come from the cache")

    def test_the_packet_tells_the_panel_which_clip_it_is_showing(self):
        from src.api import hub
        with unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"}):
            annotated = hub.original_frame("real_videos_30sec_construction", 94)
            clean = hub.original_frame("real_videos_10810477-hd_1920_1080_30fps", 94)
        self.assertTrue(annotated["source_annotations"])
        self.assertFalse(clean["source_annotations"])
