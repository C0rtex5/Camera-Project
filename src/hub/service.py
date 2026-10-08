"""Independent capture, latest-frame inference, and bounded evidence writing."""
import collections
import json
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2
import numpy as np

from src.hub.analytics import Analytics
from src.hub.models import Camera

logger = logging.getLogger(__name__)


class DeviceManager:
    def __init__(self, mode='auto', factory=None, gpu_available=None):
        if mode not in ('auto', 'cpu', 'cuda'):
            raise ValueError('Device mode must be auto, cpu, or cuda')
        if factory is None:
            from src.perception.detector import ConstructionSafetyDetector
            factory = lambda device: ConstructionSafetyDetector(device=device)
        self.factory = factory
        self.mode = mode
        self.lock = threading.Lock()
        self.model = None
        self.device = 'unavailable'
        self.reason = None
        self.generation = 0
        self.gpu_diagnostic = None
        self.gpu_available = gpu_available or self._gpu_available

    @staticmethod
    def _gpu_available():
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def _load(self, device):
        model = self.factory(device)
        model.detect(np.zeros((320, 320, 3), np.uint8))
        self.model, self.device = model, device
        self.generation += 1

    def initialize(self):
        with self.lock:
            self.model = None
            self.device = 'unavailable'
            self.reason = None
            self.gpu_diagnostic = None
            self.generation += 1
            try:
                if self.mode != 'cpu' and self.gpu_available():
                    self._load('cuda:0')
                    self.gpu_diagnostic = 'GPU ativa; inferência validada.'
                    return
                self.gpu_diagnostic = self._gpu_diagnostic()
                if self.mode == 'cuda':
                    self.reason = 'Requested GPU is unavailable'
                    return
            except Exception:
                if self.mode == 'cuda':
                    self.reason = 'GPU inference initialization failed'
                    return
                self.reason = 'GPU initialization failed; CPU fallback active'
            try:
                self._load('cpu')
            except Exception:
                self.model = None
                self.device = 'unavailable'
                self.reason = 'CPU model initialization failed; live viewing remains available'

    def detect(self, frame):
        with self.lock:
            if self.model is None:
                raise RuntimeError('Inference unavailable')
            try:
                return self.model.detect(frame)
            except Exception:
                if self.mode == 'auto' and self.device.startswith('cuda'):
                    self.model = None
                    self.device = 'unavailable'
                    self.reason = 'GPU inference failed; CPU fallback active'
                    try:
                        self._load('cpu')
                    except Exception:
                        self.reason = 'GPU failed and CPU initialization failed'
                # Drop the failed frame. Each worker resets state on generation change.
                raise RuntimeError('Inference failed; waiting for a fresh frame') from None

    @staticmethod
    def _gpu_diagnostic():
        try:
            import torch
            if torch.version.cuda is None:
                return 'Esta imagem contém PyTorch CPU. Na DGX Spark, reinicie usando a imagem Spark pelo launcher.'
            if not torch.cuda.is_available():
                return 'CUDA não está acessível. Verifique o runtime NVIDIA e os dispositivos liberados ao container.'
            return 'GPU disponível; modo CPU selecionado na configuração.'
        except Exception:
            return 'Não foi possível verificar a disponibilidade de CUDA.'

    def retry_gpu(self):
        # An explicit operator action revokes forced CPU and requests auto fallback.
        with self.lock:
            self.mode = 'auto'
        self.initialize()

    def status(self):
        return {'device': self.device, 'mode': self.mode, 'degradation': self.reason,
                'generation': self.generation, 'gpu_diagnostic': self.gpu_diagnostic}


class CameraWorker:
    def __init__(self, camera, hub):
        self.camera, self.hub = camera, hub
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame = None
        self.jpeg = None
        self.frame_id = 0
        self.captured = 0
        self.telemetry = None
        self.processed_jpeg = None
        self.capture_epoch = 0
        self.connection = 'connecting'
        self.inference_error = None
        self.ring = collections.deque(maxlen=16)
        self.analytics = Analytics(camera)
        self.generation = -1
        self.last_incident = 0
        self.latencies = collections.deque(maxlen=100)
        self.processed = collections.deque(maxlen=100)
        self.threads = [threading.Thread(target=self.capture, daemon=True, name=f'capture-{camera.camera_id}'),
                        threading.Thread(target=self.infer, daemon=True, name=f'infer-{camera.camera_id}')]

    def start(self):
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=8)
        if any(t.is_alive() for t in self.threads):
            raise RuntimeError('Camera worker did not stop within its timeout')

    def source(self):
        url = self.camera.rtsp_url
        auth = self.hub.store.camera_credentials(self.camera.camera_id)
        if auth is None and self.camera.credential_secret:
            secret = Path(os.getenv('SENTINEL_SECRET_DIR', '/run/secrets')) / self.camera.credential_secret
            auth = json.loads(secret.read_text())
        if auth is not None:
            parsed = urlsplit(url)
            url = urlunsplit((parsed.scheme, quote(auth['username'], safe='') + ':' + quote(auth['password'], safe='') + '@' + parsed.netloc,
                             parsed.path, parsed.query, parsed.fragment))
        return url

    def capture(self):
        backoff = 1
        while not self.stop_event.is_set():
            cap = None
            try:
                # FFmpeg owns network timeouts; secrets are never written to application logs.
                cap = cv2.VideoCapture(self.source(), cv2.CAP_FFMPEG,
                                       [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000])
                if not cap.isOpened():
                    raise RuntimeError('Camera unavailable')
                backoff = 1
                last_preview, last_ring = 0, 0
                while not self.stop_event.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError('Read failed')
                    now = time.time()
                    # Always drain input; publish only at the requested preview rate.
                    if now - last_preview < 1 / self.camera.preview_fps:
                        continue
                    last_preview = now
                    h, w = frame.shape[:2]
                    if w > self.camera.max_width:
                        frame = cv2.resize(frame, (self.camera.max_width, round(h * self.camera.max_width / w)))
                    ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if not ok:
                        continue
                    jpeg = encoded.tobytes()
                    with self.lock:
                        self.frame = frame
                        self.jpeg = jpeg
                        self.frame_id += 1
                        self.captured = now
                        self.connection = 'online'
                        if now - last_ring >= 1:
                            self.ring.append((now, jpeg))
                            last_ring = now
            except Exception:
                with self.lock:
                    self.connection = 'disconnected'
                    self.telemetry = None
                    self.processed_jpeg = None
                    self.capture_epoch += 1
                    self.frame = None
                    self.jpeg = None
                    self.ring.clear()
                # Full tracking reset occurs on reconnect, independently of GPU resets.
                self.generation = -1
            finally:
                if cap is not None:
                    cap.release()
            self.stop_event.wait(backoff)
            backoff = min(backoff * 2, 30)

    def infer(self):
        last_id = -1
        while not self.stop_event.wait(1 / self.camera.inference_fps):
            with self.lock:
                frame, fid, captured = self.frame, self.frame_id, self.captured
                epoch = self.capture_epoch
            if frame is None or fid == last_id or time.time() - captured > 2:
                continue
            last_id = fid
            if self.generation != self.hub.device.generation:
                self.analytics = Analytics(self.camera)
                self.generation = self.hub.device.generation
                # Start with a new capture after a device change.
                continue
            start = time.perf_counter()
            try:
                detections, ppe = self.hub.device.detect(frame)
                if self.generation != self.hub.device.generation or time.time() - captured > 2:
                    continue
                result = self.analytics.process(detections, ppe, frame.shape[1], frame.shape[0], captured)
                result.update(camera_id=self.camera.camera_id, site_id=self.camera.site_id,
                              frame_id=fid, capture_timestamp=captured, frame_width=frame.shape[1], frame_height=frame.shape[0],
                              processing_device=self.hub.device.device, inference_ms=(time.perf_counter() - start) * 1000,
                              end_to_end_ms=(time.time() - captured) * 1000, capability_status='model_backed_rules')
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                with self.lock:
                    if epoch != self.capture_epoch or self.stop_event.is_set():
                        continue
                    self.processed_jpeg = encoded.tobytes() if ok else None
                    self.telemetry = result
                    self.inference_error = None
                    self.latencies.append(result['end_to_end_ms'])
                    self.processed.append(time.time())
                ppe_alert = any(p['state'] == 'noncompliant' for p in ppe)
                if (result['severity'] in ('WARNING_LEVEL_2', 'CRITICAL_LEVEL_3') or ppe_alert) and time.time() - self.last_incident > 30:
                    self.last_incident = time.time()
                    self.hub.create_incident(result, self, evidence_jpeg=self.processed_jpeg)
            except Exception:
                with self.lock:
                    self.telemetry = None
                    self.processed_jpeg = None
                    self.inference_error = 'Inference unavailable or failed'

    def status(self):
        with self.lock:
            now = time.time()
            age = now - self.captured if self.captured else None
            frames = [t for t in self.processed if now - t <= 60]
            rate = (len(frames) - 1) / (frames[-1] - frames[0]) if len(frames) > 1 and frames[-1] > frames[0] else 0
            stale = age is None or age > 2 or self.connection != 'online'
            telemetry = self.telemetry
            if telemetry and (now - telemetry['capture_timestamp'] > 2 or self.generation != self.hub.device.generation):
                telemetry = None
            return {'camera_id': self.camera.camera_id, 'name': self.camera.name, 'site_id': self.camera.site_id,
                    'connection': 'stale' if stale and self.connection == 'online' else self.connection,
                    'last_frame_age_seconds': age, 'frame_id': self.frame_id, 'processing_device': self.hub.device.device,
                    'inference_error': self.inference_error, 'telemetry': telemetry,
                    'calibration_monitoring': 'survey_at_configuration; recheck_after_camera_movement',
                    'delivered_ai_fps': round(rate, 2),
                    'latency_p95_ms': float(np.percentile(self.latencies, 95)) if self.latencies else None}


class Hub:
    def __init__(self, store, device=None):
        self.store = store
        self.device = device or DeviceManager(os.getenv('SENTINEL_DEVICE', 'auto'))
        self.workers = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.storage_error = None
        self.evidence_queue = queue.Queue(maxsize=8)
        self.evidence_thread = threading.Thread(target=self.write_evidence, daemon=True, name='evidence')
        self.max_bytes = int(os.getenv('SENTINEL_EVIDENCE_MAX_BYTES', str(2 * 1024 ** 3)))
        self.retention_days = int(os.getenv('SENTINEL_RETENTION_DAYS', '7'))

    def start(self):
        self.evidence_thread.start()
        self.device_thread = threading.Thread(target=self.device.initialize, daemon=True, name='model-init')
        self.device_thread.start()
        for config in self.store.cameras():
            self.reload(Camera.model_validate(config))

    def stop(self):
        self.stop_event.set()
        with self.lock:
            for worker in self.workers.values():
                worker.stop_event.set()
            for worker in self.workers.values():
                try:
                    worker.stop()
                except RuntimeError:
                    logger.error('Camera shutdown exceeded timeout')
        self.evidence_thread.join(timeout=10)
        if hasattr(self, 'device_thread'):
            self.device_thread.join(timeout=8)

    def reload(self, camera):
        with self.lock:
            previous = self.workers.pop(camera.camera_id, None)
            if previous:
                previous.stop()
            if camera.enabled:
                worker = CameraWorker(camera, self)
                self.workers[camera.camera_id] = worker
                worker.start()

    def delete(self, camera_id):
        with self.lock:
            previous = self.workers.pop(camera_id, None)
            if previous:
                previous.stop()

    def statuses(self):
        with self.lock:
            return [w.status() for w in self.workers.values()]

    def create_incident(self, body, worker=None, actor='capture', evidence_jpeg=None):
        try:
            record = self.store.incident(body, actor)
            if worker:
                with worker.lock:
                    frames = list(worker.ring)
                    jpeg = evidence_jpeg or worker.jpeg
                try:
                    self.evidence_queue.put_nowait((record['event_id'], worker, frames, jpeg, time.time()))
                except queue.Full:
                    self.store.update_incident(record['event_id'], {'evidence_status': 'queue_full'})
            else:
                self.store.update_incident(record['event_id'], {'evidence_status': 'not_supplied'})
            return record
        except ValueError:
            raise
        except Exception:
            self.storage_error = 'Incident persistence failed; check disk and permissions'
            raise RuntimeError(self.storage_error) from None

    def cleanup(self):
        root = self.store.root / 'evidence'
        files = sorted((p for p in root.iterdir() if p.is_file() and p.suffix in ('.jpg', '.avi')), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        cutoff = time.time() - self.retention_days * 86400
        for path in files:
            if path.stat().st_mtime < cutoff or total > self.max_bytes:
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
        # Mark expired references so the UI never promises missing evidence.
        for record in self.store.incidents(10000):
            evidence = record.get('evidence', {})
            kept = {k: v for k, v in evidence.items() if (root / v).is_file()}
            if kept != evidence:
                self.store.update_incident(record['event_id'], {'evidence': kept, 'evidence_status': 'expired'})

    def write_evidence(self):
        last_cleanup = 0
        while not self.stop_event.is_set():
            try:
                if time.time() - last_cleanup > 60:
                    self.cleanup()
                    last_cleanup = time.time()
                try:
                    event_id, worker, frames, jpeg, created = self.evidence_queue.get(timeout=1)
                except queue.Empty:
                    continue
                # 10 seconds pre-event and up to 5 seconds post-event, sampled at 1 fps.
                self.stop_event.wait(max(0, created + 5 - time.time()))
                with worker.lock:
                    frames += [(t, b) for t, b in worker.ring if created < t <= created + 5]
                frames = sorted({t: b for t, b in frames if created - 10 <= t <= created + 5}.items())
                name = uuid.uuid4().hex
                root = self.store.root / 'evidence'
                evidence = {}
                if jpeg:
                    snapshot = root / (name + '.jpg')
                    snapshot.write_bytes(jpeg)
                    evidence['snapshot'] = snapshot.name
                if frames:
                    image = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
                    clip = root / (name + '.avi')
                    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*'MJPG'), 1, (image.shape[1], image.shape[0]))
                    try:
                        if not writer.isOpened():
                            raise RuntimeError('Clip encoder unavailable')
                        for _, data in frames:
                            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                            if frame is not None and frame.shape == image.shape:
                                writer.write(frame)
                    finally:
                        writer.release()
                    evidence['clip'] = clip.name
                self.store.update_incident(event_id, {'evidence': evidence, 'evidence_status': 'stored' if evidence else 'unavailable',
                                                      'clip_sample_fps': 1, 'evidence_timestamps': [t for t, _ in frames]})
                self.cleanup()
                self.storage_error = None
            except Exception:
                self.storage_error = 'Evidence persistence failed; check disk and permissions'
                if 'event_id' in locals():
                    try:
                        self.store.update_incident(event_id, {'evidence_status': 'failed'})
                    except Exception:
                        pass
                logger.error(self.storage_error)
