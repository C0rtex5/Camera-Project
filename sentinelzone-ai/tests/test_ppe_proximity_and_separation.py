"""PPE compliance, person-to-machinery proximity, and separating the two in the UI.

Covers the three things the camera panel now reports for a worker: the original
application's PPE compliance, the distance to the nearest machinery, and the
requirement that a person is never hidden by the machine they are standing in.
"""
import math
import os
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

from src.api import media_detection, media_tracks as mt
from src.api.media_tracks import (
    MACHINERY_FOOTPRINT_METRES,
    PROXIMITY_DANGER_METRES,
    PROXIMITY_NEAR_METRES,
    WORKER_FOOTPRINT_METRES,
    MediaTracker,
    ground_plane,
)

ROOT = Path(__file__).resolve().parents[1]
W, H = 1920, 1080
BARRIER_CLIP = "real_videos_Worker_and_excavator_near_barrier"


class PPERecordingTests(unittest.TestCase):
    """The PPE records are measured, and enabling them changes no detection."""

    def setUp(self):
        self._env = unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"})
        self._env.start()
        self.addCleanup(self._env.stop)
        media_detection.clear_cache()
        self.addCleanup(media_detection.clear_cache)

    def test_enabling_ppe_leaves_the_detections_identical(self):
        """PPE must not be able to change what is drawn on the frame."""
        from src.api import media
        from src.perception.detector import ConstructionSafetyDetector
        frame, _, _ = media.decode_frame(BARRIER_CLIP, 30)
        self.assertIsNotNone(frame, "the restored recording must decode")
        results = {}
        for flag in (False, True):
            detector = ConstructionSafetyDetector(
                weights_path="yolov8n.pt", device="cpu",
                conf_threshold=0.25, iou_threshold=0.45, allow_demo_fallback=flag)
            raw, _ = detector.detect(frame)
            results[flag] = np.asarray(raw, dtype=np.float32).reshape(-1, 6)
        self.assertEqual(results[False].shape, results[True].shape)
        self.assertTrue(np.allclose(results[False], results[True]),
                        "enabling the PPE rule must not change a single box")

    def test_ppe_is_actually_measured_on_a_restored_recording(self):
        from src.api import media
        from src.perception.detector import ConstructionSafetyDetector
        frame, _, _ = media.decode_frame(BARRIER_CLIP, 30)
        detector = ConstructionSafetyDetector(
            weights_path="yolov8n.pt", device="cpu", conf_threshold=0.25,
            iou_threshold=0.45, allow_demo_fallback=True)
        _, telemetry = detector.detect(frame)
        self.assertTrue(telemetry, "the clip must yield at least one worker PPE record")
        for record in telemetry:
            self.assertIn("measured", record)
            self.assertTrue(record["measured"], "a close worker is large enough to judge")

    def test_an_unmeasurable_worker_is_reported_as_unmeasured(self):
        """A tiny box cannot yield a verdict, and must not look like a finding."""
        from src.perception.detector import ConstructionSafetyDetector
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        detector = ConstructionSafetyDetector(
            weights_path=None, device="cpu", allow_demo_fallback=True)
        # A worker box below the 20x10 crop threshold must not be judged.
        blank[300:312, 300:308] = 255  # 8px tall, 12px wide: too small
        detector.model = _StubModel([[300, 300, 308, 312, 0.9, 0.0]])
        _, telemetry = detector.detect(blank)
        self.assertTrue(telemetry)
        self.assertFalse(telemetry[0]["measured"],
                         "a box too small to crop must be reported unmeasured")
        self.assertFalse(telemetry[0]["hardhat_detected"])

    def test_ppe_boxes_are_returned_in_native_coordinates(self):
        boxes, calibration, ppe = media_detection.detect_boxes(
            np.zeros((1080, 1920, 3), dtype=np.uint8), 640)
        self.assertEqual(calibration["frame_width"], 1920)
        self.assertEqual(calibration["frame_height"], 1080)
        for record in ppe:
            for value in record["bbox"]:
                self.assertGreaterEqual(value, 0)
            self.assertLessEqual(record["bbox"][2], 1920)
            self.assertLessEqual(record["bbox"][3], 1080)

    def test_ppe_rides_the_cache_without_a_second_inference(self):
        """The PPE records must arrive with the boxes, not from a second pass."""
        from src.api import media
        path, mtime = _path_for(BARRIER_CLIP), _mtime_for(BARRIER_CLIP)
        raw = media.frame_source(path).read(media_detection.source_index_for(BARRIER_CLIP, 20))
        frame, _ = media.normalize_frame(raw)
        real = media_detection.detect_boxes
        calls = []

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        # Prewarm is stopped and the cache cleared so nothing can evict the entry
        # between the two calls; a prewarm thread filling the bounded cache would
        # otherwise turn this into a flaky test rather than a wrong one.
        with unittest.mock.patch.object(media_detection, "prewarm", lambda *a, **k: None):
            media_detection.clear_cache()
            first_boxes, _, first_ppe = real(frame, media_detection.DEFAULT_IMGSZ)
            media_detection.clear_cache()
            media_detection._store((BARRIER_CLIP, media_detection.source_index_for(BARRIER_CLIP, 20),
                                    mtime, media_detection.DEFAULT_IMGSZ),
                                   (first_boxes, {"enabled": True}, first_ppe))
            with unittest.mock.patch.object(media_detection, "detect_boxes", counting):
                boxes, _, ppe = media_detection.detect_for_media(BARRIER_CLIP, path, 20, mtime)
        self.assertEqual(len(calls), 0, "a cached frame must not be inferred again")
        self.assertEqual(ppe, first_ppe, "the cached PPE records must be returned intact")
        self.assertEqual(boxes, first_boxes)

    def test_a_disabled_detector_yields_no_ppe_records(self):
        with unittest.mock.patch.object(media_detection, "detection_enabled", lambda: False):
            boxes, calibration, ppe = media_detection.detect_boxes(
                np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertEqual((boxes, ppe), ([], []))
        self.assertFalse(calibration["enabled"])


class ProximityTests(unittest.TestCase):
    """The distance ladder a worker is graded against."""

    def setUp(self):
        self.tracker = MediaTracker("prox", W, H, 300)

    def boxes_at(self, x, y):
        plane = ground_plane(W, H)
        row = float(np.clip(plane.row_for_depth(y), plane.min_row, plane.max_row))
        column = plane.principal[0] + x * (plane.focal / y)
        return [[column - 40, row - 220, column + 40, row, 0.9, 0.0]], \
               [[column - 60, row - 300, column + 60, row, 0.8, 2.0]]

    def state_for(self, gap):
        """Place a worker and a machine `gap` metres apart, return the grade."""
        worker, machine = self.boxes_at(0.0, 6.0)
        dx = gap * (ground_plane(W, H).focal / 6.0)
        machine = [[machine[0][0] - dx, machine[0][1], machine[0][2] - dx, machine[0][3], 0.8, 2.0]]
        snapshot = self.tracker.step(0, worker + machine, 0.0)
        self.assertEqual(len(snapshot["worker_states"]), 1)
        return snapshot["worker_states"][0]

    def test_inside_the_collision_radius_is_danger(self):
        state = self.state_for(1.0)
        self.assertEqual(state["proximity_state"], "DANGER")
        self.assertLess(state["nearest_machinery_metres"], PROXIMITY_DANGER_METRES)

    def test_outside_the_collision_radius_is_near(self):
        state = self.state_for(5.0)
        self.assertEqual(state["proximity_state"], "NEAR")
        self.assertGreater(state["nearest_machinery_metres"], PROXIMITY_DANGER_METRES)

    def test_far_from_the_machinery_is_safe(self):
        state = self.state_for(20.0)
        self.assertEqual(state["proximity_state"], "SAFE")

    def test_a_worker_with_no_machinery_is_never_called_safe(self):
        """The absence of a machine is not evidence that the worker is safe."""
        plane = ground_plane(W, H)
        row = float(np.clip(plane.row_for_depth(6.0), plane.min_row, plane.max_row))
        snapshot = self.tracker.step(0, [[900, row - 220, 980, row, 0.9, 0.0]], 0.0)
        state = snapshot["worker_states"][0]
        self.assertEqual(state["proximity_state"], "NO_MACHINE")
        self.assertIsNone(state["nearest_machinery_metres"])
        self.assertIsNone(state["nearest_machinery_track_id"])

    def test_the_nearest_of_several_machines_is_graded(self):
        plane = ground_plane(W, H)
        scale = plane.focal / 6.0
        row = float(np.clip(plane.row_for_depth(6.0), plane.min_row, plane.max_row))
        worker = [[900, row - 220, 980, row, 0.9, 0.0]]
        far = [[900 + 20 * scale, row - 300, 900 + 20 * scale + 120, row, 0.8, 2.0]]
        near = [[900 - 2 * scale, row - 300, 900 - 2 * scale + 120, row, 0.8, 2.0]]
        snapshot = self.tracker.step(0, worker + far + near, 0.0)
        state = snapshot["worker_states"][0]
        self.assertEqual(state["proximity_state"], "DANGER")
        self.assertLess(state["nearest_machinery_metres"], 4.0)

    def test_the_danger_radius_is_the_engine_collision_radius(self):
        """The grade must use the same separation the engine would enforce."""
        import re

        from src.edge.conflict_engine import DynamicConflictEngine
        self.assertEqual(PROXIMITY_DANGER_METRES,
                         WORKER_FOOTPRINT_METRES + MACHINERY_FOOTPRINT_METRES)
        # The engine forms r_col the same way; read the expression it actually
        # uses so this test cannot pass against a stale copy of the rule.
        source = (ROOT / "src" / "edge" / "conflict_engine.py").read_text()
        match = re.search(
            r"r_col = max\(self\.min_separation_distance, "
            r'agent_a\["footprint_radius"\] \+ agent_b\["footprint_radius"\]\)', source)
        self.assertIsNotNone(match, "the engine's collision radius rule changed shape")
        worker = {"footprint_radius": WORKER_FOOTPRINT_METRES, "mode_probs": np.zeros((1, 3)),
                  "mu_x": np.zeros((1, 50, 1)), "mu_y": np.zeros((1, 50, 1)),
                  "class_name": "WORKER", "vx": 0.0, "vy": 0.0}
        machine = dict(worker, footprint_radius=MACHINERY_FOOTPRINT_METRES, class_name="HEAVY_EQUIPMENT")
        engine = DynamicConflictEngine()
        r_col = max(0.0, worker["footprint_radius"] + machine["footprint_radius"])
        self.assertAlmostEqual(r_col, PROXIMITY_DANGER_METRES, places=6,
                               msg="the proximity DANGER radius must equal the engine's r_col")
        self.assertTrue(callable(engine.evaluate_pair_risk),
                        "the engine must still expose its pair evaluation")

    def test_the_footprints_match_what_the_runtime_uses(self):
        source = (ROOT / "src" / "edge" / "edge_runtime.py").read_text()
        self.assertIn(f'0.8 if is_human_a else 2.5', source)
        self.assertEqual(WORKER_FOOTPRINT_METRES, 0.8)
        self.assertEqual(MACHINERY_FOOTPRINT_METRES, 2.5)

    def test_proximity_is_stable_under_backward_scrubbing(self):
        plane = ground_plane(W, H)
        scale = plane.focal / 6.0
        row = float(np.clip(plane.row_for_depth(6.0), plane.min_row, plane.max_row))
        for index in range(20):
            worker = [[900, row - 220, 980, row, 0.9, 0.0]]
            machine = [[900 - 2 * scale, row - 300, 900 - 2 * scale + 120, row, 0.8, 2.0]]
            self.tracker.step(index, worker + machine, index / 30.0)
        stored = self.tracker.snapshot(10)
        self.assertEqual(self.tracker.step(10, [], 10 / 30.0)["worker_states"][0]["proximity_state"],
                         stored["worker_states"][0]["proximity_state"])


class SeparationTests(unittest.TestCase):
    """A person is never hidden by, nor attributed to, the machine."""

    def setUp(self):
        self.tracker = MediaTracker("sep", W, H, 300)

    def test_a_worker_inside_a_machine_box_keeps_its_own_track(self):
        """The cab operator is a real person and must stay a tracked worker."""
        machine = [[400, 200, 1200, 700, 0.5, 2.0]]
        operator = [[600, 260, 700, 420, 0.27, 0.0]]
        snapshot = self.tracker.step(0, machine + operator, 0.0)
        self.assertEqual(len(snapshot["tracks_3d"]), 2)
        self.assertEqual(len(snapshot["worker_states"]), 1)
        ids = snapshot["detection_track_ids"]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1], "machine and operator must be distinct tracks")

    def test_the_packet_reports_proximity_and_ppe_together(self):
        from src.api import hub
        packet = hub.original_frame(BARRIER_CLIP, 60)
        self.assertIn("ppe", packet)
        self.assertIn("worker_states", packet)
        for state in packet["worker_states"]:
            self.assertIn("proximity_state", state)
            self.assertIn(state["proximity_state"],
                          {"SAFE", "NEAR", "DANGER", "NO_MACHINE"})
        for removed in ("zone_overlays", "machine_id", "zone_id"):
            self.assertNotIn(removed, packet)

    def test_removed_fields_stay_removed(self):
        from src.api import hub
        packet = hub.original_frame(BARRIER_CLIP, 105)
        for removed in ("zone_overlays", "machine_id", "zone_id"):
            self.assertNotIn(removed, packet, f"{removed} must stay gone")


class _StubModel:
    """A model that returns fixed boxes, so the PPE crop rule can be tested alone."""

    def __init__(self, rows):
        self.rows = rows
        # The detector reads class names off the model to pick its schema.
        self.names = {0: "person", 2: "car", 5: "bus", 7: "truck"}

    def predict(self, source, **kwargs):
        import types
        rows = np.asarray(self.rows, dtype=np.float32).reshape(-1, 6)
        return [types.SimpleNamespace(boxes=_CpuBoxes(
            xyxy=_CpuTensor(rows[:, :4]), conf=_CpuTensor(rows[:, 4]),
            cls=_CpuTensor(rows[:, 5].astype(int))))]


class _CpuTensor:
    """The minimal torch-like surface the detector reads off a result."""

    def __init__(self, array):
        self._array = np.asarray(array)

    def cpu(self):
        return self

    def numpy(self):
        return self._array

    def astype(self, dtype):
        return _CpuTensor(self._array.astype(dtype))

    def __len__(self):
        return len(self._array)


class _CpuBoxes(_CpuTensor):
    """The detector calls len() on the boxes object before reading it."""

    def __init__(self, xyxy, conf, cls):
        self.xyxy, self.conf, self.cls = xyxy, conf, cls
        self._array = xyxy._array


def _path_for(media_id):
    from src.api import media
    return media.media_index()[media_id]["path"]


def _mtime_for(media_id):
    return os.stat(_path_for(media_id)).st_mtime_ns


if __name__ == "__main__":
    unittest.main()
