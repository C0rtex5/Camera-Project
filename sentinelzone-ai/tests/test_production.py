import json
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from src.api.security import TokenAuthMiddleware
from src.api.live_service import CameraConfig, CameraWorker
from src.edge.edge_runtime import SentinelEdgeRuntime
from src.perception.detector import ConstructionSafetyDetector


class ProductionRegressionTests(unittest.TestCase):
    def detector(self, schema='construction'):
        detector = ConstructionSafetyDetector.__new__(ConstructionSafetyDetector)
        detector.conf_threshold = .35
        detector.iou_threshold = .45
        detector.device = 'cpu'
        detector.class_schema = schema
        detector.allow_demo_fallback = False
        detector.model = MagicMock()
        return detector

    def test_empty_scene_never_fabricates_objects(self):
        detector = self.detector()
        detector.model.predict.return_value = [MagicMock(boxes=None)]
        detections, ppe = detector.detect(np.zeros((64, 64, 3), np.uint8))
        self.assertEqual(detections.shape, (0, 6))
        self.assertEqual(ppe, [])

    def test_model_failure_is_visible(self):
        detector = self.detector()
        detector.model.predict.side_effect = RuntimeError('model failed')
        with self.assertRaises(RuntimeError):
            detector.detect(np.zeros((64, 64, 3), np.uint8))

    def test_hardhat_is_not_person_and_negative_ppe_is_not_vehicle(self):
        detector = self.detector()
        boxes = np.array([[0, 0, 20, 30]] * 3, float)
        detections, ppe = detector._process_detections(boxes, np.ones(3), np.array([0, 2, 7]))
        self.assertEqual(len(detections), 0)

    def test_coco_bus_is_machine_and_ppe_unknown(self):
        detector = self.detector('coco')
        boxes = np.array([[0, 0, 20, 30]] * 2, float)
        detections, ppe = detector._process_detections(boxes, np.ones(2), np.array([0, 5]))
        self.assertEqual(detections[:, 5].tolist(), [0, 2])
        self.assertIsNone(ppe[0]['ppe_compliant'])

    def runtime(self):
        with open('config/default_config.json') as source:
            return SentinelEdgeRuntime(json.load(source)['homography'], mqtt_host=None)

    def test_no_checkpoint_disables_random_forecasts(self):
        runtime = self.runtime()
        result = runtime.execute_frame_cycle(np.empty((0, 6)), 1)
        self.assertEqual(result['forecast_status'], 'UNAVAILABLE_NO_CHECKPOINT')

    def test_one_track_cannot_absorb_two_detections(self):
        runtime = self.runtime()
        runtime.projector.pixel_to_metric = lambda anchors: anchors
        detections = np.array([[0, 0, 1, 1, .9, 0], [.1, 0, 1.1, 1, .9, 0]])
        runtime.execute_frame_cycle(detections, 1)
        runtime.execute_frame_cycle(detections, 1.2)
        self.assertEqual(len(runtime.tracks), 2)
        self.assertEqual([t.hits for t in runtime.tracks.values()], [2, 2])

    def test_stale_camera_suppresses_old_safety_status(self):
        config = CameraConfig(camera_id='gate', url_env='CAMERA_GATE', width=64, height=64,
                              homography={'K': np.eye(3).tolist(), 'H': np.eye(3).tolist(), 'dist': [0]*5})
        worker = CameraWorker(config, MagicMock(), MagicMock())
        worker.state = 'STREAMING'
        worker.last_frame = time.monotonic() - 11
        worker.telemetry = {'debounced_alarm': 'NORMAL_LEVEL_0'}
        self.assertEqual(worker.snapshot()['state'], 'STALE')
        self.assertIsNone(worker.snapshot()['telemetry'])

    def test_optional_token_middleware_protects_private_routes(self):
        app = FastAPI()
        app.add_middleware(TokenAuthMiddleware, token='a'*32)
        @app.get('/private')
        def private():
            return {'ok': True}
        @app.websocket('/socket')
        async def socket(ws: WebSocket):
            await ws.accept()
            await ws.send_json({'ok': True})
        with TestClient(app) as client:
            self.assertEqual(client.get('/private').status_code, 401)
            self.assertEqual(client.get('/private', headers={'Authorization': 'Bearer '+'a'*32}).status_code, 200)
            from starlette.websockets import WebSocketDisconnect
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('/socket'):
                    pass

    def test_invalid_calibration_rejected(self):
        with self.assertRaises(ValueError):
            CameraConfig(camera_id='gate', url_env='CAMERA_GATE', width=64, height=64,
                         homography={'K': np.eye(3).tolist(), 'H': np.zeros((3,3)).tolist(), 'dist': [0]*5})

    def test_disconnect_invalidates_pending_frame_and_clears_telemetry(self):
        config = CameraConfig(camera_id='gate', url_env='CAMERA_GATE', width=64, height=64,
                              homography={'K': np.eye(3).tolist(), 'H': np.eye(3).tolist(), 'dist': [0]*5})
        worker = CameraWorker(config, MagicMock(), MagicMock())
        capture = MagicMock()
        capture.read.side_effect = [(True, np.zeros((64,64,3), np.uint8)), (False, None)]
        def stop_after_failure(delay):
            worker.stop_event.set()
            return True
        worker.stop_event.wait = stop_after_failure
        with patch.dict('os.environ', {'CAMERA_GATE': 'rtsp://user:secret@camera/live'}), patch('src.api.live_service.cv2.VideoCapture', return_value=capture):
            worker.capture()
        self.assertEqual(worker.snapshot()['state'], 'UNAVAILABLE')
        self.assertIsNone(worker.pending_frame)
        self.assertEqual(worker.generation, 1)
        self.assertNotIn('secret', json.dumps(worker.snapshot()))
        capture.release.assert_called_once()

    def test_camera_rejects_resolution_different_from_calibration(self):
        config = CameraConfig(camera_id='gate', url_env='CAMERA_GATE', width=64, height=64,
                              homography={'K': np.eye(3).tolist(), 'H': np.eye(3).tolist(), 'dist': [0]*5})
        worker = CameraWorker(config, MagicMock(), MagicMock())
        capture = MagicMock()
        capture.read.return_value = (True, np.zeros((32,32,3), np.uint8))
        worker.stop_event.wait = lambda delay: worker.stop_event.set()
        with patch.dict('os.environ', {'CAMERA_GATE': 'rtsp://camera/live'}), patch('src.api.live_service.cv2.VideoCapture', return_value=capture):
            worker.capture()
        self.assertEqual(worker.snapshot()['state'], 'UNAVAILABLE')
        self.assertIsNone(worker.pending_frame)

    def test_checkpoint_path_executes_forecasting_branch(self):
        import tempfile
        import torch
        from src.edge.gatv2_model import WorkZoneSTGNN
        torch.set_num_threads(2)
        # Synthetic weights exercise execution only; they are not production weights.
        with tempfile.NamedTemporaryFile(suffix='.pt') as checkpoint:
            torch.save(WorkZoneSTGNN().state_dict(), checkpoint.name)
            with open('config/default_config.json') as source:
                runtime = SentinelEdgeRuntime(json.load(source)['homography'], checkpoint.name, mqtt_host=None)
            runtime.projector.pixel_to_metric = lambda anchors: anchors
            detections = np.array([[0,0,1,1,.9,0], [2,0,3,1,.9,2]])
            for index in range(6):
                result = runtime.execute_frame_cycle(detections, 1 + index * .2)
            self.assertEqual(result['forecast_status'], 'AVAILABLE')
            self.assertEqual(len(result['evaluated_pairs']), 1)

    def test_missing_checkpoint_is_not_silently_ignored(self):
        with open('config/default_config.json') as source:
            with self.assertRaises(RuntimeError):
                SentinelEdgeRuntime(json.load(source)['homography'], '/missing/checkpoint.pt', mqtt_host=None)

    def test_nonfinite_forecast_cannot_report_normal_monitoring(self):
        import torch
        runtime = self.runtime()
        runtime.forecast_available = True
        runtime.projector.pixel_to_metric = lambda anchors: anchors
        runtime.gnn = MagicMock(return_value={'mu_x': torch.tensor([float('nan')])})
        detections = np.array([[0,0,1,1,.9,0], [2,0,3,1,.9,2]])
        with self.assertRaisesRegex(RuntimeError, 'nonfinite'):
            for index in range(6):
                runtime.execute_frame_cycle(detections, 1 + index * .2)

    def test_slow_forecasting_does_not_block_health_or_capture_state(self):
        import threading
        config = CameraConfig(camera_id='gate', url_env='CAMERA_GATE', width=64, height=64,
                              homography={'K': np.eye(3).tolist(), 'H': np.eye(3).tolist(), 'dist': [0]*5})
        runtime = MagicMock()
        runtime.tracks = {}
        detector = MagicMock()
        detector.detect.return_value = (np.empty((0,6)), [])
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        def slow_cycle(*args, **kwargs):
            entered.set()
            release.wait(timeout=3)
            return {'forecast_status':'AVAILABLE'}
        runtime.execute_frame_cycle.side_effect = slow_cycle
        worker = CameraWorker(config, detector, runtime)
        worker.pending_frame = (np.zeros((64,64,3),np.uint8),time.monotonic(),0)
        worker.frame_event.set()
        worker.thread.start()
        reader = None
        try:
            self.assertTrue(entered.wait(timeout=2))
            reader = threading.Thread(target=lambda:(worker.snapshot(),read_done.set()),daemon=True)
            reader.start()
            self.assertTrue(read_done.wait(timeout=.5),'Status was blocked by model execution')
        finally:
            worker.stop_event.set()
            release.set()
            worker.thread.join(timeout=3)
            if reader:
                reader.join(timeout=1)

    def test_default_freshness_rejects_a_two_second_old_result(self):
        config = CameraConfig(camera_id='gate', url_env='CAMERA_GATE', width=64, height=64,
                              homography={'K': np.eye(3).tolist(), 'H': np.eye(3).tolist(), 'dist': [0]*5})
        worker = CameraWorker(config, MagicMock(), MagicMock())
        worker.state = 'STREAMING'
        worker.last_frame = time.monotonic()-2
        worker.telemetry = {'debounced_alarm':'NORMAL_LEVEL_0'}
        self.assertEqual(worker.snapshot()['state'],'STALE')
        self.assertIsNone(worker.snapshot()['telemetry'])

    def test_detection_order_does_not_swap_nearby_worker_identities(self):
        runtime = self.runtime()
        runtime.projector.pixel_to_metric = lambda anchors: anchors
        detections = np.array([[0,0,1,1,.9,0], [1,0,2,1,.9,0]])
        runtime.execute_frame_cycle(detections,1)
        runtime.execute_frame_cycle(detections[::-1],1.2)
        self.assertAlmostEqual(runtime.tracks[1].kf.state[0],.5)
        self.assertAlmostEqual(runtime.tracks[2].kf.state[0],1.5)

    def test_unobserved_tracks_are_not_fed_as_current_forecast_measurements(self):
        runtime = self.runtime()
        runtime.projector.pixel_to_metric = lambda anchors: anchors
        detections = np.array([[0,0,1,1,.9,0], [2,0,3,1,.9,2]])
        for index in range(6):
            runtime.execute_frame_cycle(detections,1+index*.2)
        runtime.forecast_available = True
        runtime.gnn = MagicMock(side_effect=AssertionError('Stale track sent to forecast'))
        result = runtime.execute_frame_cycle(detections[:1],3)
        self.assertEqual(result['evaluated_pairs'],[])
        runtime.gnn.assert_not_called()
