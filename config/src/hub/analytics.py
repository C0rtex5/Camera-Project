"""Surveyed metric tracking with explicitly deterministic forecasts."""
import time
import numpy as np
from shapely.geometry import Point, Polygon
from src.edge.homography import HomographyProjector
from src.edge.tracker import MetricTrack
from src.edge.conflict_engine import AlertHysteresisDebouncer


class Analytics:
    def __init__(self, camera):
        self.camera = camera
        self.tracks = {}
        self.next_id = 1
        self.last_time = None
        self.seen = {}
        self.debouncer = AlertHysteresisDebouncer()

    def process(self, detections, ppe, width, height, captured):
        annotations = [{'bbox': d[:4].tolist(), 'confidence': float(d[4]), 'class_id': int(d[5]),
                        'class_name': ['WORKER', 'SPOTTER', 'HEAVY_EQUIPMENT', 'LIGHT_VEHICLE'][int(d[5])],
                        'ppe': next((p for p in ppe if p['detection_index'] == i), None)} for i, d in enumerate(detections)]
        calibration = self.camera.calibration
        result = {'annotations': annotations, 'prediction_method': 'constant_velocity',
                  'metric_status': 'unavailable_calibration', 'tracks': [], 'severity': 'UNAVAILABLE', 'min_ttc_seconds': None}
        if calibration is None:
            return result
        # Coordinates are restored to the surveyed resolution before undistortion.
        if abs(width / height - calibration.width / calibration.height) > 0.01:
            result['metric_status'] = 'invalid_aspect_ratio'
            return result
        projector = HomographyProjector(np.asarray(calibration.K), np.asarray(calibration.dist), np.asarray(calibration.H))
        anchors = projector.extract_bottom_center_anchors(detections[:, :4])
        anchors *= np.asarray([calibration.width / width, calibration.height / height])
        positions = projector.pixel_to_metric(anchors)
        dt = captured - self.last_time if self.last_time is not None else 0.1
        if dt <= 0 or dt > 2:
            self.tracks.clear()
            self.seen.clear()
            self.debouncer = AlertHysteresisDebouncer()
            dt = 0.1
        self.last_time = captured
        for tr in self.tracks.values():
            tr.step_predict(dt)
        # Greedy nearest matching across all candidates, with unique assignments.
        candidates = sorted((float(np.linalg.norm(tr.kf.state[:2] - p)), tid, i)
                            for tid, tr in self.tracks.items() for i, p in enumerate(positions)
                            if tr.class_id == int(detections[i, 5]))
        used_tracks, used_detections = set(), set()
        for distance, tid, i in candidates:
            if distance > 2 or tid in used_tracks or i in used_detections:
                continue
            self.tracks[tid].step_update(positions[i])
            self.seen[tid] = captured
            used_tracks.add(tid)
            used_detections.add(i)
        for i, p in enumerate(positions):
            if i not in used_detections:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = MetricTrack(tid, int(detections[i, 5]), p)
                self.seen[tid] = captured
        for tid in list(self.tracks):
            if captured - self.seen[tid] > 1:
                del self.tracks[tid]
                del self.seen[tid]
        active = [t for tid, t in self.tracks.items() if t.confirmed and self.seen[tid] == captured]
        severity, min_ttc = 'NORMAL_LEVEL_0', None
        rank = {'NORMAL_LEVEL_0': 0, 'ADVISORY_LEVEL_1': 1, 'WARNING_LEVEL_2': 2, 'CRITICAL_LEVEL_3': 3}
        for a in active:
            for b in active:
                if a.track_id >= b.track_id or (a.class_id in (0, 1)) == (b.class_id in (0, 1)):
                    continue
                human = a if a.class_id in (0, 1) else b
                multiplier = max([z.ttc_multiplier for z in self.camera.zones if Polygon(z.polygon).covers(Point(human.kf.state[:2]))] or [1])
                # First predicted footprint overlap within a 5-second horizon.
                relative = a.kf.state[:2] - b.kf.state[:2]
                velocity = a.kf.state[2:4] - b.kf.state[2:4]
                radius = (0.8 if a.class_id in (0, 1) else 2.5) + (0.8 if b.class_id in (0, 1) else 2.5)
                times = np.linspace(0, 5, 51)
                hits = times[np.linalg.norm(relative[None, :] + times[:, None] * velocity, axis=1) <= radius]
                if hits.size:
                    ttc = float(hits[0])
                    min_ttc = ttc if min_ttc is None else min(ttc, min_ttc)
                    level = 'CRITICAL_LEVEL_3' if ttc <= 1.5 * multiplier else ('WARNING_LEVEL_2' if ttc <= 2.5 * multiplier else 'ADVISORY_LEVEL_1')
                    if rank[level] > rank[severity]:
                        severity = level
        result.update(metric_status='survey_validated', severity=self.debouncer.step(severity), min_ttc_seconds=min_ttc,
                      tracks=[{'track_id': t.track_id, 'class_id': t.class_id, 'position': t.kf.state[:2].tolist(),
                               'velocity': t.kf.state[2:4].tolist(),
                               'forecast_trajectory': [(t.kf.state[:2] + t.kf.state[2:4] * dt).tolist() for dt in np.linspace(0.5, 5, 10)]} for t in active])
        return result
