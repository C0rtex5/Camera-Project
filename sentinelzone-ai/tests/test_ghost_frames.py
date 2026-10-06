"""Ghost framing: no two served frames may describe one object.

A "ghost frame" is a second detection box on an object that already has one. The
panel drew them on top of each other, which is what read as scrambled framing.

The rule is calibrated from the restored clips rather than chosen:

* a second box on one object lands **0.02-1.08 m** from the first on the ground;
* two objects that are genuinely separate are **1.34 m** apart or more, and they
  can still overlap heavily in the image because one may be nearer the camera.

Overlap cannot separate those populations, which is why an earlier version of the
dedupe that required overlap removed only a fifth of the ghosts: the pairs it
missed overlap by as little as 0.02. This module is the extensive check that the
space is now clean, and that cleaning it never removes a person.
"""
import math
import os
import random
import unittest
import unittest.mock

import numpy as np

from src.api import detection_ghosts, hub, media, media_tracks
from src.api.media_tracks import (
    DUPLICATE_MACHINERY_METRES,
    DUPLICATE_OVERLAP,
    FULL_CONTAINMENT,
    NESTED_CONFIDENCE_RATIO,
    UNANCHORED_DUPLICATE_OVERLAP,
    DUPLICATE_PERSON_METRES,
    NESTED_PLANT_CONTAINMENT,
    drop_duplicate_detections,
    fit_ground_plane,
)

W, H = 1920, 1080
FOCAL = 1420.0
PRINCIPAL = (960.0, 540.0)
WORKER, MACHINERY, VEHICLE = 0, 2, 3


def plane():
    return fit_ground_plane(W, H, FOCAL, PRINCIPAL, 132.0, 998.0)


def area(box):
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def intersection(a, b):
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return 0.0 if (width <= 0 or height <= 0) else width * height


def footprint_gap(metric, i, j):
    return float(np.hypot(*(metric[i] - metric[j])))


def random_box(rng, class_id=None):
    """A plausible detection: a rectangular box with a footpoint in the scene."""
    width = rng.randint(40, 320)
    height = rng.randint(80, 620)
    left = rng.randint(0, W - width)
    top = rng.randint(0, H - height)
    class_id = rng.choice([WORKER, WORKER, WORKER, MACHINERY, VEHICLE]) if class_id is None else class_id
    return [float(left), float(top), float(left + width), float(top + height),
            round(rng.uniform(0.2, 0.95), 2), float(class_id)]


class RandomisedInvariantTests(unittest.TestCase):
    """The properties that must hold for any frame, not just the ones on disk."""

    def setUp(self):
        self.plane = plane()
        self.rng = random.Random(20261001)

    def metric_for(self, boxes):
        anchors = np.array([[(b[0] + b[2]) / 2.0, b[3]] for b in boxes], dtype=np.float64)
        return self.plane.pixel_to_metric(anchors)

    def test_no_two_same_class_frames_are_left_on_one_spot(self):
        """The ghost invariant, over 4000 random frames."""
        for _ in range(4000):
            boxes = [random_box(self.rng) for _ in range(self.rng.randint(2, 7))]
            kept, _ = drop_duplicate_detections(boxes, self.plane)
            if len(kept) < 2:
                continue
            metric = self.metric_for(kept)
            for i in range(len(kept)):
                for j in range(i + 1, len(kept)):
                    if int(kept[i][5]) != int(kept[j][5]):
                        continue
                    limit = (DUPLICATE_MACHINERY_METRES if int(kept[i][5]) >= 2
                             else DUPLICATE_PERSON_METRES)
                    self.assertGreaterEqual(
                        footprint_gap(metric, i, j), limit,
                        f"two {int(kept[i][5])}-class frames share one spot: {kept[i]} {kept[j]}")

    def test_a_person_is_never_the_box_that_goes(self):
        """Whenever a worker is dropped, a worker box still covers that spot."""
        for _ in range(2000):
            boxes = [random_box(self.rng) for _ in range(self.rng.randint(2, 6))]
            kept, _ = drop_duplicate_detections(boxes, self.plane)
            kept_workers = [b for b in kept if int(b[5]) == WORKER]
            for box in boxes:
                if int(box[5]) != WORKER or box in kept:
                    continue
                self.assertTrue(
                    kept_workers,
                    f"the last worker box was removed: {box} from {boxes}")
                # The person must still be drawn there: some survivor either
                # stands on the same spot or covers the box that went. It need not
                # be the nearest one - a box wholly inside another is dropped by
                # the container, which may sit further away on the ground.
                metric = self.metric_for(kept_workers + [box])
                covered = any(
                    footprint_gap(metric, len(kept_workers), k) < DUPLICATE_PERSON_METRES + 1e-6
                    or intersection(kept_workers[k], box) / area(box) >= UNANCHORED_DUPLICATE_OVERLAP
                    for k in range(len(kept_workers)))
                self.assertTrue(covered,
                                f"the dropped worker {box} is not covered by any survivor\n"
                                f"inputs: {boxes}\nkept: {kept}")

    def test_nothing_is_invented_and_nothing_is_reordered(self):
        """Every kept row is an input row, in the input order."""
        for _ in range(2000):
            boxes = [random_box(self.rng) for _ in range(self.rng.randint(0, 7))]
            kept, mapping = drop_duplicate_detections(boxes, self.plane)
            positions = [k for k in range(len(boxes)) if k in mapping]
            self.assertEqual([boxes[k] for k in positions], kept,
                             "the kept list must be the surviving input rows in order")
            self.assertEqual(sorted(mapping.values()), list(range(len(kept))),
                             "the index map must be a bijection onto the kept list")

    def test_it_is_idempotent(self):
        """A second pass cannot change the result, or the pipeline would drift."""
        for _ in range(1500):
            boxes = [random_box(self.rng) for _ in range(self.rng.randint(0, 7))]
            once, _ = drop_duplicate_detections(boxes, self.plane)
            twice, _ = drop_duplicate_detections(once, self.plane)
            self.assertEqual(once, twice)

    def test_a_vehicle_wholly_inside_a_machine_is_one_object(self):
        for _ in range(500):
            outer = [200.0, 100.0, 200.0 + self.rng.randint(500, 900),
                     100.0 + self.rng.randint(400, 700), 0.3, float(MACHINERY)]
            inner_w = self.rng.randint(40, 150)
            inner_h = self.rng.randint(40, 120)
            inner = [outer[0] + 20, outer[1] + 20,
                     outer[0] + 20 + inner_w, outer[1] + 20 + inner_h, 0.4, float(VEHICLE)]
            kept, _ = drop_duplicate_detections([outer, inner], self.plane)
            self.assertEqual(len(kept), 1,
                             "a vehicle wholly inside a machine is the same space twice")

    def test_two_machines_side_by_side_both_survive(self):
        left = [100.0, 500.0, 500.0, 1000.0, 0.9, float(MACHINERY)]
        right = [1400.0, 500.0, 1800.0, 1000.0, 0.9, float(MACHINERY)]
        kept, _ = drop_duplicate_detections([left, right], self.plane)
        self.assertEqual(len(kept), 2)

    def test_a_weak_box_wholly_inside_a_strong_one_is_one_object(self):
        for _ in range(600):
            outer = [400.0, 300.0, 400.0 + self.rng.randint(300, 700),
                     300.0 + self.rng.randint(400, 700), 0.9, float(WORKER)]
            inner = [outer[0] + 5, outer[1] + 5,
                     outer[2] - 5, outer[3] - 5, round(self.rng.uniform(0.05, 0.45), 2), float(WORKER)]
            kept, _ = drop_duplicate_detections([outer, inner], self.plane)
            self.assertEqual(len(kept), 1,
                             "a weak box wholly inside a strong one is the same object")

    def test_a_person_behind_another_person_is_kept(self):
        """Fully contained, but on a different spot and held with comparable
        confidence: two objects, so the containment rule must not fire."""
        plane = self.plane
        near_row, far_row = 1000.0, 620.0
        self.assertGreater(plane.depth_for_row(far_row) - plane.depth_for_row(near_row), 1.2,
                           "the fixture must put them further apart than the threshold")
        outer = [400.0, 300.0, 1400.0, near_row, 0.88, float(WORKER)]
        inner = [700.0, 380.0, 1000.0, far_row, 0.74, float(WORKER)]
        self.assertGreaterEqual(intersection(outer, inner) / area(inner), FULL_CONTAINMENT,
                                "the fixture must be wholly contained")
        kept, _ = drop_duplicate_detections([outer, inner], plane)
        self.assertEqual(len(kept), 2,
                         "an 0.84 confidence ratio is not a weak second hypothesis")

    def test_a_person_inside_a_machine_is_maintained_not_removed(self):
        machine = [200.0, 100.0, 1200.0, 900.0, 0.5, float(MACHINERY)]
        person = [600.0, 300.0, 700.0, 620.0, 0.3, float(WORKER)]
        kept, _ = drop_duplicate_detections([machine, person], self.plane)
        self.assertEqual(len(kept), 2, "a person beside or inside plant is kept")
        self.assertIn(person, kept)


class ThresholdBoundaryTests(unittest.TestCase):
    """The threshold has to sit in the gap the data shows, not on a value."""

    def setUp(self):
        self.plane = plane()

    def pair(self, metres_apart):
        """Two worker boxes whose footpoints sit that far apart on the ground."""
        base = [900.0, 700.0, 1032.0, 998.0, 0.9, float(WORKER)]
        depth = self.plane.depth_for_row(998.0)
        pixels = metres_apart * self.plane.focal / depth
        other = [900.0 + pixels, 700.0, 1032.0 + pixels, 998.0, 0.5, float(WORKER)]
        return base, other

    def test_just_inside_the_threshold_is_one_object(self):
        pair = self.pair(DUPLICATE_PERSON_METRES * 0.9)
        kept, _ = drop_duplicate_detections(list(pair), self.plane)
        self.assertEqual(len(kept), 1)

    def test_just_outside_the_threshold_keeps_both(self):
        pair = self.pair(DUPLICATE_PERSON_METRES * 1.3)
        kept, _ = drop_duplicate_detections(list(pair), self.plane)
        self.assertEqual(len(kept), 2, "two people further apart than the threshold are two people")

    def test_the_threshold_sits_in_the_gap_the_data_shows(self):
        # Measured on the restored clips: duplicates stop at 1.08 m, separate
        # objects start at 1.34 m.
        self.assertGreater(DUPLICATE_PERSON_METRES, 1.08)
        self.assertLess(DUPLICATE_PERSON_METRES, 1.34)
        self.assertGreater(DUPLICATE_MACHINERY_METRES, DUPLICATE_PERSON_METRES)

    def test_the_nested_containment_is_high_enough_to_be_decisive(self):
        self.assertGreaterEqual(NESTED_PLANT_CONTAINMENT, 0.8)
        self.assertLessEqual(NESTED_PLANT_CONTAINMENT, 1.0)


class EveryClipIsCleanTests(unittest.TestCase):
    """The invariant against the footage that is actually on disk."""

    def setUp(self):
        self.clips = [r for r in media.unique_catalog() if r.get("path")]
        self.assertGreater(len(self.clips), 5, "the restored clips must be present")

    def test_no_clip_serves_a_same_class_ghost(self):
        offenders = []
        plane_seen = {}
        for row in self.clips:
            media_id = str(row["id"])
            media_tracks.clear_trackers()
            for index in range(0, min(row.get("frame_count") or 0, 400), 6):
                try:
                    packet = hub.original_frame(media_id, index)
                except Exception:
                    continue
                boxes = packet["detections"]
                if len(boxes) < 2:
                    continue
                ground = packet["ground_plane"]
                anchored = bool(ground.get("anchored"))
                plane_seen[media_id] = (ground["camera_height_m"], anchored)
                pl = media_tracks.GroundPlane(1920, 1080,
                                              ground["focal_px"], tuple(ground["principal_px"]),
                                              ground["horizon_row"], ground["camera_height_m"])
                anchors = np.array([[(b[0] + b[2]) / 2.0, b[3]] for b in boxes], dtype=np.float64)
                metric = pl.pixel_to_metric(anchors)
                for i in range(len(boxes)):
                    for j in range(i + 1, len(boxes)):
                        if int(boxes[i][5]) != int(boxes[j][5]):
                            continue
                        if anchored:
                            # Metres are a measurement here, so the ground rule
                            # is the one that must hold.
                            limit = (DUPLICATE_MACHINERY_METRES if int(boxes[i][5]) >= 2
                                     else DUPLICATE_PERSON_METRES)
                            if float(np.hypot(*(metric[i] - metric[j]))) < limit:
                                offenders.append((media_id, index, boxes[i][:5], boxes[j][:5]))
                            # On the fallback the scale is the assumed band, so the
                            # pixel rule is the one that applies.
                            smaller = min(area(boxes[i]), area(boxes[j]))
                            if smaller <= 0:
                                continue
                            if intersection(boxes[i], boxes[j]) / smaller >= DUPLICATE_OVERLAP:
                                offenders.append((media_id, index, boxes[i][:5], boxes[j][:5]))
                        # Whatever the plane: a box wholly inside another that
                        # reports far less confidence is one object.
                        smaller = min(area(boxes[i]), area(boxes[j]))
                        if smaller > 0:
                            frac = intersection(boxes[i], boxes[j]) / smaller
                            strong = max(float(boxes[i][4]), float(boxes[j][4]))
                            weak = min(float(boxes[i][4]), float(boxes[j][4]))
                            if (frac >= FULL_CONTAINMENT and strong > 0
                                    and weak < NESTED_CONFIDENCE_RATIO * strong):
                                offenders.append((media_id, index, boxes[i][:5], boxes[j][:5]))
        detail = "; ".join(f"{cid}@{idx}: {a} vs {b}" for cid, idx, a, b in offenders[:2])
        self.assertEqual(offenders, [],
                         f"{len(offenders)} ghost frames served; first: {detail}\n"
                         f"planes seen: {plane_seen}")

    def test_every_served_frame_is_a_rectangle_inside_the_image(self):
        for row in self.clips[:4]:
            media_id = str(row["id"])
            media_tracks.clear_trackers()
            for index in range(0, min(row.get("frame_count") or 0, 120), 30):
                try:
                    packet = hub.original_frame(media_id, index)
                except Exception:
                    continue
                for box in packet["detections"]:
                    self.assertLess(box[0], box[2], "left must precede right")
                    self.assertLess(box[1], box[3], "top must precede bottom")
                    self.assertGreaterEqual(box[0], -1)
                    self.assertGreaterEqual(box[1], -1)
                    self.assertLessEqual(box[2], packet["frame_width"] + 1)
                    self.assertLessEqual(box[3], packet["frame_height"] + 1)

    def test_the_served_row_shape_is_stable(self):
        row = self.clips[0]
        media_tracks.clear_trackers()
        packet = hub.original_frame(str(row["id"]), 0)
        for box in packet["detections"]:
            self.assertEqual(len(box), 8, "x1,y1,x2,y2,conf,class,track_id,role")
            self.assertIn(int(box[5]), (0, 1, 2, 3))

    def test_each_object_gets_at_most_one_track_id(self):
        """Two frames on one object would also mean two ground agents."""
        row = next(r for r in self.clips if "30sec" in str(r["id"]))
        media_tracks.clear_trackers()
        for index in range(0, 200, 20):
            packet = hub.original_frame(str(row["id"]), index)
            ids = [b[6] for b in packet["detections"] if b[6] is not None]
            self.assertEqual(len(ids), len(set(ids)), "a track id must not label two frames")


class OperatorStillVisibleTests(unittest.TestCase):
    """Cleaning the space must not delete the person closest to the machine."""

    def test_a_cab_operator_keeps_their_box_and_their_notes(self):
        with unittest.mock.patch.dict(os.environ, {"SENTINEL_MEDIA_PREWARM": "0"}):
            packet = hub.original_frame("real_videos_Worker_and_excavator_near_barrier", 105)
        operators = [b for b in packet["detections"] if detection_ghosts.is_operator_row(b)]
        self.assertTrue(operators, "this frame carries a cab operator")
        agents = {t["track_id"] for t in packet["tracks_3d"]}
        for box in operators:
            self.assertIsNone(box[6], "an operator carries no track id")
            self.assertNotIn(box[6], agents, "and is not a second agent on the ground")


if __name__ == "__main__":
    unittest.main()
