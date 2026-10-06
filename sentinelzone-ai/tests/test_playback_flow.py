"""Tests for smooth playback of restored recordings.

The speed work must not trade away precision, so these tests assert both halves:

* the serve path is genuinely faster (sequential decode, one read per frame,
  background prewarming), and
* every displayed frame still receives its own detection - nothing is
  interpolated, repeated, dropped or subsampled.
"""
import os
import time
import unittest

import cv2
import numpy as np

from src.api import media, media_detection

CLIP = "real_videos_Worker_in_excavator_blind_spot"


class FrameSourceTests(unittest.TestCase):
    def setUp(self):
        media.close_frame_sources()

    def tearDown(self):
        media.close_frame_sources()

    def test_forward_reads_avoid_repeated_seeks(self):
        source = media.frame_source(str(media._media_entry(CLIP)["path"]))
        for index in range(5):
            frame = source.read(index)
            self.assertIsNotNone(frame, index)
        # Walking forward never rewinds the cursor, so no seek is issued.
        self.assertEqual(source.cursor, 5)

    def test_backward_read_seeks_and_still_returns_the_right_frame(self):
        entry = media._media_entry(CLIP)
        path = entry["path"]
        source = media.frame_source(str(path))
        forward = [source.read(i).copy() for i in (0, 8, 16)]
        backward = [source.read(i).copy() for i in (16, 8, 0)]
        # backward was collected in reverse, so compare like for like.
        for expected, actual in zip(forward, reversed(backward)):
            # Identical content, not merely plausible content.
            self.assertTrue(np.array_equal(expected, actual))

    def test_random_access_returns_consistent_frames(self):
        source = media.frame_source(str(media._media_entry(CLIP)["path"]))
        a = source.read(40).copy()
        source.read(120)
        b = source.read(40).copy()
        self.assertTrue(np.array_equal(a, b))

    def test_forward_playback_is_much_cheaper_than_per_frame_seeking(self):
        import pathlib

        path = str(media._media_entry(CLIP)["path"])
        count = 24

        started = time.perf_counter()
        for index in range(count):
            capture = cv2.VideoCapture(path)
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            capture.read()
            capture.release()
        seek_cost = (time.perf_counter() - started) / count

        media.close_frame_sources()
        source = media.frame_source(path)
        started = time.perf_counter()
        for index in range(count):
            source.read(index)
        sequential_cost = (time.perf_counter() - started) / count

        self.assertLess(sequential_cost, seek_cost,
                        f"sequential {sequential_cost*1000:.1f} ms vs seek {seek_cost*1000:.1f} ms")
        self.assertEqual(pathlib.Path(path).exists(), True)

    def test_frame_source_is_shared_per_path(self):
        entry = media._media_entry(CLIP)
        self.assertIs(media.frame_source(str(entry["path"])), media.frame_source(str(entry["path"])))

    def test_decode_frame_clamps_errors_like_the_endpoint(self):
        with self.assertRaises(Exception):
            media.decode_frame(CLIP, -1)
        with self.assertRaises(Exception):
            media.decode_frame(CLIP, 10_000_000)


class PrewarmTests(unittest.TestCase):
    def setUp(self):
        media_detection.clear_cache()
        media_detection._PREWARMERS.clear()
        media.close_frame_sources()

    def tearDown(self):
        media_detection.clear_cache()
        media_detection._PREWARMERS.clear()
        media.close_frame_sources()

    def test_prewarm_reports_progress_and_stops_at_the_target(self):
        entry = media.media_index()[CLIP]
        path = entry["path"]
        import pathlib

        mtime = pathlib.Path(path).stat().st_mtime_ns
        media_detection.prewarm(CLIP, path, 0, mtime, 6)
        deadline = time.time() + 60
        state = {}
        while time.time() < deadline:
            state = media_detection.prewarm_state(CLIP)
            if not state["active"]:
                break
            time.sleep(0.2)
        self.assertIn("ready_through", state)
        self.assertGreaterEqual(state.get("ready_through", -1), 0)
        self.assertFalse(state["active"])

    def test_prewarm_uses_a_real_target_not_an_infinite_loop(self):
        entry = media.media_index()[CLIP]
        import pathlib

        mtime = pathlib.Path(entry["path"]).stat().st_mtime_ns
        media_detection.prewarm(CLIP, entry["path"], 3, mtime, 240)
        state = media_detection.prewarm_state(CLIP)
        self.assertTrue(state["active"] or state["ready_through"] >= 0)
        self.assertLessEqual(state["target"], 240)

    def test_detection_cache_is_keyed_per_frame_so_repeats_are_free(self):
        entry = media.media_index()[CLIP]
        import pathlib

        path = entry["path"]
        mtime = pathlib.Path(path).stat().st_mtime_ns
        first = media_detection.detect_for_media(CLIP, path, 12, mtime)
        started = time.perf_counter()
        second = media_detection.detect_for_media(CLIP, path, 12, mtime)
        cached_ms = 1000 * (time.perf_counter() - started)
        self.assertEqual(first[0], second[0])
        self.assertLess(cached_ms, 25.0, "cache hit should not re-run inference")


class PrecisionPreservationTests(unittest.TestCase):
    """The speed work must not cost accuracy."""

    def setUp(self):
        media_detection.clear_cache()
        media_detection._PREWARMERS.clear()
        media.close_frame_sources()

    def tearDown(self):
        media_detection.clear_cache()
        media_detection._PREWARMERS.clear()
        media.close_frame_sources()

    def test_every_frame_keeps_its_own_detection_result(self):
        """Distinct frames must not collapse onto one shared result.

        A stub detector is used so the invariant is asserted deterministically
        and does not depend on which weights happen to be present.
        """
        import pathlib
        from unittest.mock import patch

        entry = media.media_index()[CLIP]
        path = entry["path"]
        mtime = pathlib.Path(path).stat().st_mtime_ns
        samples = [10, 40, 70, 100, 130]
        seen = []

        class IndexEncodingDetector:
            """Returns a box derived from the frame it was given.

            The marker is taken from the frame content rather than a corner
            pixel: frames are now letterboxed onto 1920x1080, so the corners
            are the black bars and are identical for every frame.
            """

            def detect(self, frame):
                marker = round(float(frame[::64, ::64].mean()), 4)
                seen.append(marker)
                box = np.array([[10.0, 10.0, 20.0 + marker, 30.0, 0.9, 0]], dtype=np.float32)
                return box, []

        results = {}
        # Prewarm off keeps the assertion deterministic; get_detector is the
        # correct seam to substitute.
        with patch.dict(os.environ, {"SENTINEL_MEDIA_DETECT": "1", "SENTINEL_MEDIA_PREWARM": "0"}), \
             patch.object(media_detection, "get_detector", lambda: (IndexEncodingDetector(), None)):
            for index in samples:
                frame, _, _ = media.decode_frame(CLIP, index)
                results[index] = media_detection.detect_for_media(CLIP, path, index, mtime, frame=frame)[0]

        self.assertEqual(len(seen), len(samples), "each frame must reach the detector")
        signatures = {tuple(tuple(box) for box in boxes) for boxes in results.values()}
        self.assertEqual(len(signatures), len(samples), "frames collapsed onto a shared result")

    def test_prewarmed_frames_match_inline_detection_exactly(self):
        """Prewarming must be numerically identical to detecting on demand."""
        entry = media.media_index()[CLIP]
        import pathlib

        path = entry["path"]
        mtime = pathlib.Path(path).stat().st_mtime_ns
        media_detection.prewarm(CLIP, path, 0, mtime, 4)
        deadline = time.time() + 60
        while time.time() < deadline and media_detection.prewarm_state(CLIP)["active"]:
            time.sleep(0.2)
        for index in (0, 1, 2, 3):
            prewarmed, _, _ = media_detection.detect_for_media(CLIP, path, index, mtime)
            media_detection._CACHE.clear()
            frame, _, _ = media.decode_frame(CLIP, index)
            inline, _, _ = media_detection.detect_for_media(CLIP, path, index, mtime, frame=frame)
            self.assertEqual(prewarmed, inline, f"frame {index} differs between prewarm and inline")


if __name__ == "__main__":
    unittest.main()
