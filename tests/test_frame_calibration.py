"""Tests for detection-frame calibration and overlay detection.

The calibration contract is the thing the dashboard depends on: a box must be
expressed in the pixel space of the exact frame the browser decodes. These tests
pin that contract numerically, with synthetic geometry and a real decode.
"""
import os
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from src.api import media_detection
from src.perception.frame_calibration import (
    HEAVY_EQUIPMENT,
    WORKER,
    calibration_report,
    clip_boxes,
    geometry_for,
    inference_size,
    iou,
    resize_for_inference,
    scale_boxes,
    to_display,
    to_native,
)


class CalibrationGeometryTests(unittest.TestCase):
    def test_common_resolutions_keep_the_aspect_ratio_exactly(self):
        for width, height in [(1280, 720), (1920, 1080), (3840, 2160), (2160, 3840), (640, 480)]:
            geometry = geometry_for(width, height, 960)
            self.assertTrue(geometry.uniform, f"{width}x{height}")
            self.assertEqual(geometry.max_relative_error, 0.0, f"{width}x{height}")

    def test_odd_aspect_ratios_stay_within_a_tiny_error(self):
        geometry = geometry_for(607, 1080, 960)
        self.assertLess(geometry.max_relative_error, 1e-3)

    def test_inference_never_exceeds_the_requested_size(self):
        for width, height in [(640, 480), (1920, 1080), (3840, 2160), (607, 1080), (100, 4000)]:
            target_w, target_h = inference_size(width, height, 960)
            self.assertLessEqual(max(target_w, target_h), 960)
            self.assertGreaterEqual(target_w, 1)
            self.assertGreaterEqual(target_h, 1)

    def test_small_frames_are_not_upscaled(self):
        self.assertEqual(inference_size(320, 240, 960), (320, 240))

    def test_invalid_dimensions_are_rejected(self):
        with self.assertRaises(ValueError):
            inference_size(0, 100, 960)

    def test_scale_boxes_maps_both_axes(self):
        boxes = np.array([[10, 20, 30, 40, 0.9, 0]], dtype=np.float32)
        scaled = scale_boxes(boxes, 2.0, 4.0)
        self.assertEqual(list(scaled[0][:4]), [20, 80, 60, 160])

    def test_scale_boxes_handles_empty_input(self):
        self.assertEqual(len(scale_boxes(np.empty((0, 6), dtype=np.float32), 2, 2)), 0)

    def test_round_trip_through_inference_space_is_lossless(self):
        geometry = geometry_for(1920, 1080, 960)
        original = np.array([[100.0, 200.0, 300.0, 400.0, 0.8, 0]], dtype=np.float32)
        shrunk = original.copy()
        shrunk[:, 0] /= geometry.scale_x
        shrunk[:, 1] /= geometry.scale_y
        shrunk[:, 2] /= geometry.scale_x
        shrunk[:, 3] /= geometry.scale_y
        restored = to_native(shrunk, geometry)
        self.assertTrue(np.allclose(restored, original, atol=1e-3))

    def test_to_display_matches_the_frame_the_packet_advertises(self):
        boxes = np.array([[0.0, 0.0, 960.0, 540.0, 0.5, 0]], dtype=np.float32)
        displayed = to_display(boxes, 960, 540, 1280, 720)
        self.assertEqual(list(displayed[0][:4]), [0.0, 0.0, 1280.0, 720.0])

    def test_clip_boxes_constrains_to_the_frame(self):
        boxes = np.array([[-50.0, -20.0, 5000.0, 4000.0, 0.5, 0]], dtype=np.float32)
        clipped = clip_boxes(boxes, 1280, 720)
        self.assertEqual(list(clipped[0][:4]), [0.0, 0.0, 1280.0, 720.0])

    def test_iou_of_identical_and_disjoint_boxes(self):
        box = np.array([0.0, 0.0, 10.0, 10.0])
        self.assertAlmostEqual(iou(box, box), 1.0, places=6)
        self.assertEqual(iou(box, np.array([100.0, 100.0, 110.0, 110.0])), 0.0)

    def test_calibration_report_flags_boxes_outside_the_frame(self):
        geometry = geometry_for(1280, 720, 960)
        inside = np.array([[10, 10, 100, 100, 0.5, 0]], dtype=np.float32)
        outside = np.array([[10, 10, 5000, 100, 0.5, 0]], dtype=np.float32)
        self.assertEqual(calibration_report(geometry, inside)["out_of_frame_boxes"], 0)
        self.assertEqual(calibration_report(geometry, outside)["out_of_frame_boxes"], 1)

    def test_resize_produces_the_advertised_geometry(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        resized, geometry = resize_for_inference(frame, 960)
        self.assertEqual(resized.shape[:2], (geometry.inference_height, geometry.inference_width))
        self.assertEqual(geometry.source_width, 1920)
        self.assertEqual(geometry.source_height, 1080)


class OverlayDetectionTests(unittest.TestCase):
    def setUp(self):
        media_detection.clear_cache()
        media_detection._DETECTOR = None
        media_detection._DETECTOR_ERROR = None

    def test_detection_can_be_disabled_explicitly(self):
        with patch.dict(os.environ, {"SENTINEL_MEDIA_DETECT": "0"}):
            boxes, calibration, ppe = media_detection.detect_boxes(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertEqual(boxes, [])
        self.assertFalse(calibration["enabled"])

    def test_missing_weights_degrade_with_an_explicit_reason(self):
        with patch.dict(os.environ, {"SENTINEL_MEDIA_DETECT": "1", "SENTINEL_DETECTOR_WEIGHTS": "/nonexistent/weights.pt"}):
            boxes, calibration, ppe = media_detection.detect_boxes(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertEqual(boxes, [])
        self.assertFalse(calibration["enabled"])
        self.assertIn("not available", calibration["reason"])

    def test_boxes_are_returned_in_native_frame_coordinates(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        geometry = geometry_for(1920, 1080, 960)
        fake = np.array([[100.0, 50.0, 400.0, 900.0, 0.9, WORKER]], dtype=np.float32)

        class StubDetector:
            def detect(self, image):
                self.seen = image.shape
                shrunk = fake.copy()
                shrunk[:, 0] /= geometry.scale_x
                shrunk[:, 1] /= geometry.scale_y
                shrunk[:, 2] /= geometry.scale_x
                shrunk[:, 3] /= geometry.scale_y
                return shrunk, []

        with patch.dict(os.environ, {"SENTINEL_MEDIA_DETECT": "1"}), \
             patch.object(media_detection, "_DETECTOR", StubDetector()), \
             patch.object(media_detection, "_DETECTOR_ERROR", None):
            boxes, calibration, ppe = media_detection.detect_boxes(frame, 960)

        self.assertEqual(len(boxes), 1)
        self.assertEqual([int(v) for v in boxes[0][:4]], [100, 50, 400, 900])
        self.assertEqual(int(boxes[0][5]), WORKER)
        self.assertTrue(calibration["uniform_scale"])
        self.assertEqual(calibration["workers"], 1)
        self.assertEqual(calibration["frame_width"], 1920)
        self.assertIn("inference_ms", calibration)

    def test_boxes_are_clipped_to_the_frame(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        geometry = geometry_for(1280, 720, 960)
        fake = np.array([[0.0, 0.0, 5000.0, 5000.0, 0.5, HEAVY_EQUIPMENT]], dtype=np.float32)

        class StubDetector:
            def detect(self, image):
                shrunk = fake.copy()
                shrunk[:, 0] /= geometry.scale_x
                shrunk[:, 1] /= geometry.scale_y
                shrunk[:, 2] /= geometry.scale_x
                shrunk[:, 3] /= geometry.scale_y
                return shrunk, []

        with patch.dict(os.environ, {"SENTINEL_MEDIA_DETECT": "1"}), \
             patch.object(media_detection, "_DETECTOR", StubDetector()), \
             patch.object(media_detection, "_DETECTOR_ERROR", None):
            boxes, calibration, ppe = media_detection.detect_boxes(frame, 960)

        self.assertEqual([int(v) for v in boxes[0][:4]], [0, 0, 1280, 720])
        self.assertEqual(calibration["heavy_equipment"], 1)
        self.assertEqual(calibration["out_of_frame_boxes"] if "out_of_frame_boxes" in calibration else 0, 0)


class HubCalibrationContractTests(unittest.TestCase):
    def test_served_packet_boxes_match_the_served_frame_size(self):
        import base64
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {"SENTINEL_MODE": "production"}):
            from src.api.app import app
            with TestClient(app) as client:
                sources = client.get("/api/v1/hub/sources").json()
                target = next(d for d in sources["demos"] if d["group"] == "real_videos")
                packet = client.get(f"/api/v1/hub/demo/{target['id']}/frames/5").json()

        frame = cv2.imdecode(np.frombuffer(base64.b64decode(packet["jpeg"]), dtype=np.uint8), cv2.IMREAD_COLOR)
        # The dashboard divides by these values, so they must describe the decoded image.
        self.assertEqual(packet["frame_width"], frame.shape[1])
        self.assertEqual(packet["frame_height"], frame.shape[0])
        for box in packet["detections"]:
            self.assertLessEqual(box[2], frame.shape[1] + 1)
            self.assertLessEqual(box[3], frame.shape[0] + 1)
            self.assertGreaterEqual(box[0], -1)
            self.assertGreaterEqual(box[1], -1)
        calibration = packet["detection_calibration"]
        self.assertEqual(calibration["frame_width"], frame.shape[1])
        self.assertEqual(calibration["frame_height"], frame.shape[0])


if __name__ == "__main__":
    unittest.main()
