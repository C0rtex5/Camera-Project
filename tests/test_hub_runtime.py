import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from pydantic import ValidationError
from src.hub.analytics import Analytics
from src.hub.models import Camera
from src.hub.service import CameraWorker, DeviceManager, Hub
from src.hub.store import Store
from tests.hub_support import camera, calibration, EmptyDetector


def wait_until(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestComputeFallback(unittest.TestCase):
    def test_cpu_start_without_gpu(self):
        calls = []
        manager = DeviceManager(factory=lambda d: calls.append(d) or EmptyDetector(), gpu_available=lambda: False)
        manager.initialize()
        self.assertEqual(calls, ['cpu'])
        self.assertEqual(manager.device, 'cpu')

    def test_gpu_start_and_runtime_cpu_recovery(self):
        class FailingGPU(EmptyDetector):
            def __init__(self):
                self.calls = 0
            def detect(self, frame):
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError('GPU lost')
                return super().detect(frame)
        calls = []
        manager = DeviceManager(factory=lambda d: calls.append(d) or (FailingGPU() if d.startswith('cuda') else EmptyDetector()), gpu_available=lambda: True)
        manager.initialize()
        generation = manager.generation
        self.assertEqual(manager.device, 'cuda:0')
        with self.assertRaises(RuntimeError):
            manager.detect(np.zeros((10, 10, 3), np.uint8))
        self.assertEqual(manager.device, 'cpu')
        self.assertGreater(manager.generation, generation)
        self.assertIn('fallback', manager.reason)
        self.assertEqual(len(manager.detect(np.zeros((10, 10, 3), np.uint8))[0]), 0)
        self.assertEqual(calls, ['cuda:0', 'cpu'])

    def test_gpu_initialization_failure(self):
        def factory(d):
            if d.startswith('cuda'):
                raise RuntimeError('driver failure')
            return EmptyDetector()
        manager = DeviceManager(factory=factory, gpu_available=lambda: True)
        manager.initialize()
        self.assertEqual(manager.device, 'cpu')
        self.assertIn('initialization failed', manager.reason)

    def test_missing_model_and_required_gpu(self):
        def broken(d):
            raise RuntimeError('missing model')
        manager = DeviceManager(factory=broken, gpu_available=lambda: False)
        manager.initialize()
        self.assertEqual(manager.device, 'unavailable')
        self.assertIsNone(manager.model)
        required = DeviceManager('cuda', factory=lambda d: EmptyDetector(), gpu_available=lambda: False)
        required.initialize()
        self.assertEqual(required.device, 'unavailable')


class TestMetricAnalytics(unittest.TestCase):
    def test_calibration_resize_and_missing_calibration(self):
        config = Camera.model_validate(camera(calibration=calibration()))
        analytics = Analytics(config)
        detections = np.asarray([[40, 40, 60, 100, 0.9, 0]], dtype=np.float32)
        analytics.process(detections, [], 500, 500, 1)
        np.testing.assert_allclose(analytics.tracks[1].kf.state[:2], [1, 2], atol=1e-5)
        raw = Analytics(Camera.model_validate(camera())).process(detections, [], 500, 500, 1)
        self.assertEqual(raw['severity'], 'UNAVAILABLE')
        self.assertEqual(len(raw['annotations']), 1)
        self.assertEqual(analytics.process(detections, [], 500, 300, 2)['metric_status'], 'invalid_aspect_ratio')

    def test_invalid_survey_and_coordinate_frames(self):
        bad = calibration()
        bad['ground_references'][0] = [50, 50]
        with self.assertRaises(ValidationError):
            Camera.model_validate(camera(calibration=bad))
        with self.assertRaises(ValidationError):
            Camera.model_validate(camera(calibration=calibration(), zones=[{'zone_id': 'z', 'coordinate_frame': 'other', 'polygon': [[0, 0], [1, 0], [1, 1]]}]))

    def test_unique_matching_and_measured_elapsed_time(self):
        analytics = Analytics(Camera.model_validate(camera(calibration=calibration())))
        def boxes(xs):
            return np.asarray([[x-5, 10, x+5, 100, .9, 0] for x in xs], np.float32)
        analytics.process(boxes([100]), [], 1000, 1000, 1)
        analytics.process(boxes([101, 102]), [], 1000, 1000, 1.5)
        self.assertEqual(len(analytics.tracks), 2)
        self.assertEqual(analytics.tracks[1].hits, 2)
        with patch.object(analytics.tracks[1], 'step_predict', wraps=analytics.tracks[1].step_predict) as predict:
            analytics.process(boxes([103, 104]), [], 1000, 1000, 2.2)
            self.assertAlmostEqual(predict.call_args.args[0], .7)

    def test_approved_zone_multiplier_changes_risk(self):
        from src.edge.tracker import MetricTrack
        def make(zones):
            analytics = Analytics(Camera.model_validate(camera(calibration=calibration(), zones=zones)))
            a = MetricTrack(1, 0, np.array([2., 2.])); b = MetricTrack(2, 2, np.array([8.8, 2.]))
            a.confirmed = b.confirmed = True
            b.kf.state[2] = -1
            analytics.tracks = {1: a, 2: b}; analytics.seen = {1: 1, 2: 1}; analytics.next_id = 3; analytics.last_time = 1
            return analytics
        zones = [{'zone_id': 'z', 'coordinate_frame': 'surveyed-floor', 'polygon': [[0, 0], [5, 0], [5, 5], [0, 5]], 'ttc_multiplier': 2}]
        first, second = make([]), make(zones)
        for i in range(3):
            captured = 1.1 + i / 10
            dets = np.asarray([[195, 195, 205, 200, .9, 0], [875-10*i, 195, 885-10*i, 200, .9, 2]], np.float32)
            low = first.process(dets, [], 1000, 1000, captured)
            high = second.process(dets, [], 1000, 1000, captured)
        self.assertNotEqual(low['severity'], high['severity'])


class TestCaptureAndStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.hub = Hub(self.store, DeviceManager('cpu', factory=lambda d: EmptyDetector()))
        self.hub.device.initialize()

    def tearDown(self):
        for worker in self.hub.workers.values():
            worker.stop()
        self.tmp.cleanup()

    def test_four_camera_capture_isolation_and_disconnect(self):
        workers = []
        class Capture:
            def __init__(self, source, *args):
                self.disconnected = 'offline' in source
                self.frame = np.full((180, 320, 3), 50 if 'cam1' in source else 100, np.uint8)
            def isOpened(self):
                return not self.disconnected
            def read(self):
                time.sleep(.03)
                return True, self.frame.copy()
            def release(self):
                pass
        with patch('src.hub.service.cv2.VideoCapture', Capture):
            for i in range(4):
                worker = CameraWorker(Camera.model_validate(camera(f'cam{i}', enabled=True, rtsp_url=f'rtsp://{"offline" if i==0 else "cam"+str(i)}/stream', inference_fps=10)), self.hub)
                self.hub.workers[worker.camera.camera_id] = worker
                workers.append(worker); worker.start()
            self.assertTrue(wait_until(lambda: all(w.telemetry is not None for w in workers[1:])))
            self.assertEqual(workers[0].status()['connection'], 'disconnected')
            self.assertIsNone(workers[0].frame)
            self.assertNotEqual(workers[1].jpeg, workers[2].jpeg)
            self.assertIsNot(workers[1].analytics.tracks, workers[2].analytics.tracks)
            self.assertLessEqual(len(workers[1].ring), 16)
            for worker in workers:
                worker.stop()

    def test_stale_frames_and_inference_state_are_unavailable(self):
        worker = CameraWorker(Camera.model_validate(camera()), self.hub)
        worker.captured = time.time() - 5; worker.connection = 'online'
        worker.telemetry = {'capture_timestamp': worker.captured}
        self.assertEqual(worker.status()['connection'], 'stale')
        self.assertIsNone(worker.status()['telemetry'])

    def test_backup_retention_and_restore(self):
        self.store.save_camera(Camera.model_validate(camera()).model_dump(), 'admin')
        self.store.incident({'event_id': 'i1'})
        self.store.update_incident('i1', {'review': {'verdict': 'FALSE_POSITIVE'}})
        root = self.store.root / 'evidence'
        evidence = root / 'old.jpg'; evidence.write_bytes(b'a' * 20)
        self.store.update_incident('i1', {'evidence': {'snapshot': 'old.jpg'}})
        self.hub.max_bytes = 1
        self.hub.cleanup()
        self.assertFalse(evidence.exists())
        self.assertEqual(self.store.get_incident('i1')['evidence_status'], 'expired')
        backup = self.store.root / 'backup.sqlite3'
        self.store.backup(backup)
        import sqlite3
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM incidents').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM cameras').fetchone()[0], 1)

    def test_snapshot_clip_and_bounded_queue(self):
        worker = CameraWorker(Camera.model_validate(camera()), self.hub)
        ok, jpeg = cv2.imencode('.jpg', np.zeros((64, 64, 3), np.uint8))
        self.assertTrue(ok)
        created = time.time() - 6
        self.store.incident({'event_id': 'clip'})
        frames = [(created - 1, jpeg.tobytes()), (created + 1, jpeg.tobytes())]
        self.hub.evidence_queue.put(('clip', worker, frames, jpeg.tobytes(), created))
        self.hub.evidence_thread.start()
        try:
            self.assertTrue(wait_until(lambda: self.store.get_incident('clip').get('evidence_status') == 'stored'))
            record = self.store.get_incident('clip')
            self.assertTrue((self.store.root / 'evidence' / record['evidence']['snapshot']).exists())
            cap = cv2.VideoCapture(str(self.store.root / 'evidence' / record['evidence']['clip']))
            self.assertTrue(cap.read()[0]); cap.release()
        finally:
            self.hub.stop_event.set(); self.hub.evidence_thread.join(3)
        for i in range(8):
            self.hub.evidence_queue.put_nowait((i,))
        with self.assertRaises(queue.Full):
            self.hub.evidence_queue.put_nowait(('overflow',))


# Launcher scenarios are covered by tests/test_deploy.py.
