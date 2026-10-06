"""Tests for the three playback guarantees.

1. Every playable recording is presented as 1920x1080 at a fixed 30 fps.
2. Identical recordings are listed once in the UI, and the duplicate stays
   addressable with its provenance intact.
3. A warmed clip is served from cache without a stall, including across the
   loop wrap.
"""
import os
import time
import json
import unittest
from pathlib import Path

import numpy as np

from src.api import hub, media, media_detection

NORMALIZED = (1920, 1080, 30)


class PresentationFormatTests(unittest.TestCase):
    def test_every_playable_recording_is_1080p30(self):
        playable = [item for item in media.unique_catalog(refresh=True) if item["playable"]]
        self.assertGreaterEqual(len(playable), 10)
        for item in playable:
            self.assertEqual((item["width"], item["height"], item["fps"]), NORMALIZED, item["filename"])
            self.assertEqual((item["presentation_width"], item["presentation_height"], item["presentation_fps"]), NORMALIZED)
            self.assertTrue(item["normalized"])

    def test_served_frames_are_1920x1080(self):
        for item in media.unique_catalog(refresh=True):
            if not item["playable"]:
                continue
            frame, _, _ = media.decode_frame(item["id"], 0)
            self.assertEqual((frame.shape[1], frame.shape[0]), (1920, 1080), item["filename"])

    def test_output_timeline_is_thirty_fps(self):
        for item in media.unique_catalog(refresh=True):
            if not item["playable"]:
                continue
            expected = media.output_frame_count(item["source_frame_count"], item["source_fps"])
            self.assertEqual(item["frame_count"], expected, item["filename"])

    def test_output_index_maps_to_a_real_source_frame(self):
        item = next(i for i in media.unique_catalog(refresh=True) if i["playable"])
        total = item["frame_count"]
        seen = set()
        for index in range(0, total, max(1, total // 40)):
            source = media.source_index_for_output(index, item["source_fps"], item["source_frame_count"])
            self.assertGreaterEqual(source, 0)
            self.assertLess(source, item["source_frame_count"])
            seen.add(source)
        self.assertGreater(len(seen), 1)

    def test_letterbox_preserves_aspect_ratio(self):
        # Sources that are not already 1920x1080 are scaled to fit and padded.
        for shape in ((2160, 3840), (720, 1280)):
            frame = np.full((shape[0], shape[1], 3), 200, np.uint8)
            out, info = media.normalize_frame(frame)
            self.assertEqual((out.shape[1], out.shape[0]), (1920, 1080))
            self.assertTrue(info["letterbox"])
            # Content is centred and never stretched beyond the canvas.
            self.assertGreaterEqual(info["offset_x"], 0)
            self.assertGreaterEqual(info["offset_y"], 0)

    def test_native_1080p_is_not_letterboxed(self):
        frame = np.full((1080, 1920, 3), 10, np.uint8)
        out, info = media.normalize_frame(frame)
        self.assertFalse(info["letterbox"])
        self.assertTrue(np.array_equal(out, frame))


class DeduplicationTests(unittest.TestCase):
    def test_duplicate_recordings_are_listed_once(self):
        everything = media.catalog(refresh=True)
        unique = media.unique_catalog(refresh=True)
        self.assertLess(len(unique), len(everything), "the export contains duplicate recordings")
        keys = [media._content_key(item) for item in unique]
        self.assertEqual(len(keys), len(set(keys)), "a recording is listed more than once")

    def test_the_three_known_duplicates_are_collapsed(self):
        unique = {item["id"] for item in media.unique_catalog(refresh=True)}
        everything = {item["id"]: item for item in media.catalog(refresh=True)}
        duplicates = [item for item in everything.values() if item.get("duplicate_of")]
        self.assertGreaterEqual(len(duplicates), 3)
        for alias in duplicates:
            self.assertNotIn(alias["id"], unique, f"{alias['filename']} is a duplicate and must not be listed")
            self.assertIn(alias["duplicate_of"], unique)

    def test_primary_copy_wins_and_provenance_is_kept(self):
        unique = {item["id"]: item for item in media.unique_catalog(refresh=True)}
        for item in unique.values():
            if not item["duplicate_count"]:
                continue
            self.assertEqual(item["group"], "real_videos", "the primary copy comes from real_videos")
            self.assertEqual(item["duplicate_count"], len(item["aliases"]))
            for alias in item["aliases"]:
                self.assertIn(alias["filename"], {"real_site_active.mp4", "exca_near_miss.mp4", "Worker_in_excavator_blind_spot.mp4"})

    def test_duplicates_remain_addressable(self):
        everything = {item["id"]: item for item in media.catalog(refresh=True)}
        aliases = [item for item in everything.values() if item.get("duplicate_of")]
        self.assertTrue(aliases)
        for alias in aliases:
            entry = media.media_index().get(alias["id"])
            self.assertIsNotNone(entry, alias["id"])
            self.assertTrue(entry["playable"])


class UiResolutionLimitTests(unittest.TestCase):
    """The UI must not list any recording larger than 1080p."""

    def test_no_listed_recording_exceeds_1080p(self):
        for item in media.unique_catalog(refresh=True):
            width = item.get("source_width") or 0
            height = item.get("source_height") or 0
            if width <= 0 or height <= 0:
                continue
            self.assertLessEqual(max(width, height), media.UI_MAX_LONG_SIDE,
                                 f"{item['filename']} is larger than 1080p and must not be listed")

    def test_the_4k_recordings_are_not_in_the_catalog(self):
        """The two files above 1080p were removed from the delivery."""
        listed = {item["filename"] for item in media.unique_catalog(refresh=True)}
        indexed = {item["filename"] for item in media.catalog(refresh=True)}
        for name in ("14117669-uhd_3840_2160_30fps.mp4", "15100676_2160_3840_30fps.mp4"):
            self.assertNotIn(name, listed)
            self.assertNotIn(name, indexed)
            self.assertFalse(any(Path(item["path"]).name == name
                                 for item in media.media_index().values()))

    def test_the_removal_is_recorded_with_a_reason(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] /
                               "data" / "original_assets_manifest.json").read_text())
        removed = {Path(item["path"]).name: item for item in manifest.get("removed_objects", [])}
        for name in ("14117669-uhd_3840_2160_30fps.mp4", "15100676_2160_3840_30fps.mp4"):
            self.assertIn(name, removed, f"{name} was deleted but is not recorded as removed")
            self.assertTrue(removed[name]["reason"].strip())
            self.assertTrue(removed[name]["oid"])

    def test_exactly_1080p_and_portrait_720p_are_kept(self):
        listed = {item["filename"] for item in media.unique_catalog(refresh=True)}
        self.assertIn("10810477-hd_1920_1080_30fps.mp4", listed, "1080p is not more than 1080p")
        self.assertIn("user_site_video.mp4", listed, "720x1280 is a 720p frame, not above 1080p")

    def test_no_minimum_resolution_is_imposed(self):
        """Small sources are listed: 1080p is a ceiling, not a floor."""
        listed = media.unique_catalog(refresh=True)
        small = [item for item in listed
                 if max(item.get("source_width") or 0, item.get("source_height") or 0) < 1080]
        self.assertTrue(small, "the smallest recordings should still be listed")
        # The very smallest source in the library must survive the filter.
        smallest = min(small, key=lambda i: max(i.get("source_width") or 0, i.get("source_height") or 0))
        self.assertLess(max(smallest.get("source_width") or 0, smallest.get("source_height") or 0), 1080)
        # And the predicate must accept anything under the ceiling, however small.
        for width, height in ((320, 240), (362, 398), (640, 480), (720, 1280), (1280, 720)):
            self.assertTrue(media.within_ui_resolution({"source_width": width, "source_height": height}),
                            f"{width}x{height} is under 1080p and must be allowed")
        self.assertFalse(media.within_ui_resolution({"source_width": 1921, "source_height": 1080}))
        self.assertFalse(media.within_ui_resolution({"source_width": 1080, "source_height": 1921}))

    def test_every_listed_recording_is_within_the_ceiling_and_unique(self):
        keys = [media._content_key(item) for item in media.unique_catalog(refresh=True)]
        self.assertEqual(len(keys), len(set(keys)), "the UI must not list a copy of a listed file")

    def test_an_unmeasured_source_is_not_assumed_to_be_oversized(self):
        self.assertTrue(media.within_ui_resolution({"source_width": 0, "source_height": 0}))
        self.assertTrue(media.within_ui_resolution({}))

    def test_the_predicate_is_orientation_independent(self):
        self.assertTrue(media.within_ui_resolution({"source_width": 1280, "source_height": 720}))
        self.assertTrue(media.within_ui_resolution({"source_width": 720, "source_height": 1280}))
        self.assertTrue(media.within_ui_resolution({"source_width": 1920, "source_height": 1080}))
        self.assertFalse(media.within_ui_resolution({"source_width": 3840, "source_height": 2160}))
        self.assertFalse(media.within_ui_resolution({"source_width": 2160, "source_height": 3840}))

    def test_a_removed_file_would_be_withheld_if_it_were_restored(self):
        """The guard still works, proved with a synthetic oversized entry."""
        oversized = {"id": "synthetic_4k", "filename": "synthetic_4k.mp4",
                     "source_width": 3840, "source_height": 2160, "playable": True}
        self.assertFalse(media.within_ui_resolution(oversized))
        allowed = dict(oversized, source_width=1920, source_height=1080)
        self.assertTrue(media.within_ui_resolution(allowed))

    def test_displayed_counts_match_the_listed_recordings(self):
        summary = media.summary()
        self.assertEqual(summary["original_media"], len(media.unique_catalog(refresh=True)))
        self.assertEqual(summary["playable_media"],
                         sum(1 for item in media.unique_catalog(refresh=True) if item["playable"]))
        # Nothing listed exceeds 1080p, so no recording is withheld any more.
        self.assertEqual(media.hidden_by_resolution(refresh=True), [])


class WarmPlaybackTests(unittest.TestCase):
    CLIP = "real_videos_Worker_in_excavator_blind_spot"

    def setUp(self):
        media_detection.clear_cache()
        media_detection._PREWARMERS.clear()
        media.close_frame_sources()

    def tearDown(self):
        media_detection.clear_cache()
        media_detection._PREWARMERS.clear()
        media.close_frame_sources()

    def _warm(self):
        entry = media.media_index()[self.CLIP]
        mtime = Path(entry["path"]).stat().st_mtime_ns
        media_detection.prewarm(self.CLIP, entry["path"], 0, entry["source_frame_count"], 960, mtime)
        deadline = time.time() + 180
        while time.time() < deadline and media_detection.prewarm_state(self.CLIP)["active"]:
            time.sleep(0.3)
        return entry

    def test_warmed_clip_is_fully_cached(self):
        entry = self._warm()
        mtime = Path(entry["path"]).stat().st_mtime_ns
        missing_detections = [
            index for index in range(entry["source_frame_count"])
            if (self.CLIP, index, mtime, 960) not in media_detection._CACHE
        ]
        missing_frames = [
            index for index in range(entry["frame_count"])
            if media.cached_frame(media.frame_key(self.CLIP, index)) is None
        ]
        self.assertEqual(missing_detections, [], "a warmed clip must have every detection cached")
        self.assertEqual(missing_frames, [], "a warmed clip must have every frame pre-encoded")

    def test_warm_playback_has_no_stall_including_the_loop_wrap(self):
        entry = self._warm()
        total = entry["frame_count"]
        latencies = []
        for index in range(total):
            started = time.perf_counter()
            packet = hub.original_frame(self.CLIP, index)
            latencies.append(1000 * (time.perf_counter() - started))
            self.assertEqual((packet["frame_width"], packet["frame_height"]), (1920, 1080))
        # Wrap: the player loops straight back to the first frame.
        for index in range(20):
            started = time.perf_counter()
            hub.original_frame(self.CLIP, index)
            latencies.append(1000 * (time.perf_counter() - started))
        latencies.sort()
        median = latencies[len(latencies) // 2]
        worst = latencies[-1]
        # 30 fps budget is 33.3 ms; a warm clip must have headroom and no spike.
        self.assertLess(median, 10.0, f"median {median:.1f} ms")
        self.assertLess(worst, 33.3, f"worst frame {worst:.1f} ms exceeds the 30 fps budget")

    def test_packets_still_carry_per_frame_detections(self):
        entry = self._warm()
        seen = []
        for index in (5, 60, 120, 200, 280):
            packet = hub.original_frame(self.CLIP, index)
            seen.append(tuple(tuple(box) for box in packet["detections"]))
        self.assertGreater(len(set(seen)), 1, "frames must not share one detection payload")

    def test_disabled_prewarm_still_serves_correct_frames(self):
        with patch_env({"SENTINEL_MEDIA_PREWARM": "0"}):
            media_detection.clear_cache()
            packet = hub.original_frame(self.CLIP, 3)
        self.assertEqual((packet["frame_width"], packet["frame_height"]), (1920, 1080))
        self.assertTrue(packet["jpeg"])


class _patch_env:
    def __init__(self, values):
        self.values = values
        self.previous = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            os.environ[key] = value
        media_detection.clear_cache()
        return self

    def __exit__(self, *args):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def patch_env(values):
    return _patch_env(values)


if __name__ == "__main__":
    unittest.main()
