"""Bounded live camera workers; URLs and credentials never enter telemetry."""
import json
import logging
import uuid
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.edge.edge_runtime import SentinelEdgeRuntime
from src.perception.detector import ConstructionSafetyDetector


logger = logging.getLogger("SentinelLive")
class CameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: str = Field(default="SITE-01", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    camera_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    url_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    inference_fps: float = Field(default=5, gt=0, le=30)
    stale_after_seconds: float = Field(default=1.0, gt=0, le=30)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    homography: dict

    @model_validator(mode="after")
    def calibrated(self):
        for key in ("K", "H"):
            matrix = np.asarray(self.homography.get(key), dtype=float)
            if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12:
                raise ValueError(f"{key} must be a finite invertible 3x3 calibration matrix")
        dist = np.asarray(self.homography.get("dist"), dtype=float)
        if dist.size not in (4, 5, 8, 12, 14) or not np.isfinite(dist).all():
            raise ValueError("Invalid lens distortion calibration")
        return self


class CameraWorker:
    def __init__(self, config, detector, runtime, source_url=None):
        self.config, self.detector, self.runtime = config, detector, runtime
        self.source_url = source_url
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.runtime_lock = threading.RLock()
        self.frame_event = threading.Event()
        self.pending_frame = None
        self.generation = 0
        self.error_code = None
        self.capture_thread = threading.Thread(target=self.capture, name=config.camera_id + "-capture", daemon=True)
        self.thread = threading.Thread(target=self.run, name=config.camera_id, daemon=True)
        self.state = "CONNECTING"
        self.last_frame = None
        self.telemetry = None
        self.jpeg = None
        self.frame_id = None

    def snapshot(self):
        with self.lock:
            age = None if self.last_frame is None else time.monotonic() - self.last_frame
            fresh = self.state == "STREAMING" and age is not None and age < self.config.stale_after_seconds
            device = self.detector.device if isinstance(self.detector.device, str) else "unknown"
            return {"camera_id": self.config.camera_id, "device": device, "state": self.state if fresh or self.state != "STREAMING" else "STALE",
                    "error_code": self.error_code, "frame_age_seconds": age, "stale_after_seconds": self.config.stale_after_seconds, "telemetry": self.telemetry if fresh else None}

    def capture(self):
        delay = 1
        while not self.stop_event.is_set():
            capture = None
            try:
                url = self.source_url or os.environ[self.config.url_env]
                capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
                if not capture.isOpened():
                    raise RuntimeError("Camera connection unavailable")
                while not self.stop_event.is_set():
                    ok, frame = capture.read()
                    if not ok:
                        raise RuntimeError("Camera read failed")
                    if frame.shape[:2] != (self.config.height, self.config.width):
                        raise RuntimeError("Frame resolution differs from calibration")
                    with self.lock:
                        # One slot: replace unprocessed frames, never queue video.
                        self.pending_frame = (frame, time.monotonic(), self.generation)
                        self.frame_event.set()
                    delay = 1
            except Exception:
                with self.lock:
                    self.generation += 1
                    self.error_code = "CAMERA_CONNECTION_OR_CALIBRATION_ERROR"
                    self.state = "UNAVAILABLE"
                    self.pending_frame = None
                    self.telemetry = None
                    self.jpeg = None
            finally:
                if capture is not None:
                    capture.release()
            self.stop_event.wait(delay)
            delay = min(delay * 2, 30)

    def run(self):
        while not self.stop_event.is_set():
            if not self.frame_event.wait(timeout=1):
                continue
            with self.lock:
                pending = self.pending_frame
                self.pending_frame = None
                self.frame_event.clear()
            if pending is None:
                continue
            frame, captured_at, generation = pending
            try:
                detections, ppe = self.detector.detect(frame)
                # Status/capture must remain responsive while forecasting is slow.
                with self.lock:
                    if generation != self.generation:
                        continue
                    previous_frame = self.last_frame
                with self.runtime_lock:
                    if previous_frame is None or captured_at - previous_frame > 2:
                        self.runtime.tracks.clear()
                        self.runtime.last_timestamp = None
                        from src.edge.conflict_engine import AlertHysteresisDebouncer
                        self.runtime.debouncer = AlertHysteresisDebouncer()
                    result = self.runtime.execute_frame_cycle(detections, timestamp=captured_at)
                    result["ppe"] = ppe
                    result["detections"] = detections.tolist()
                    result["tracks"] = [
                        {"track_id": t.track_id, "class_id": t.class_id,
                         "position": t.kf.state[:2].tolist(), "velocity": t.kf.state[2:4].tolist(),
                         "history": [list(map(float, point[:2])) for point in t.history[-30:]],
                         "forecast_trajectory": result.get("forecast_paths", {}).get(t.track_id, []),
                         "last_observed_age_seconds": captured_at - t.history_times[-1]}
                        for t in self.runtime.tracks.values()
                    ]
                result["inference_age_seconds"] = time.monotonic() - captured_at
                ok, jpeg = cv2.imencode(".jpg", frame)
                with self.lock:
                    if generation != self.generation:
                        continue
                    self.frame_id = uuid.uuid4().hex
                    result.update(frame_id=self.frame_id, frame_width=frame.shape[1], frame_height=frame.shape[0],
                                  captured_at=time.time() - (time.monotonic() - captured_at))
                    self.jpeg = jpeg.tobytes() if ok else None
                    self.telemetry = result
                    self.last_frame = captured_at
                    self.state = "STREAMING"
                    self.error_code = None
            except Exception:
                with self.lock:
                    if generation == self.generation:
                        self.state = "INFERENCE_ERROR"
                        self.error_code = "DETECTOR_OR_FORECAST_ERROR"
                        self.telemetry = None
                        self.jpeg = None
            self.stop_event.wait(1 / self.config.inference_fps)
        self.runtime.close()


class LiveService:
    def __init__(self, config_path=None, *, configs=None, sources=None):
        configs = configs if configs is not None else [CameraConfig.model_validate(item) for item in json.loads(Path(config_path).read_text())]
        sources = sources or {}
        if not configs or len({c.camera_id for c in configs}) != len(configs):
            raise ValueError("Configure at least one camera with unique IDs")
        import torch
        torch.set_num_threads(int(os.getenv("SENTINEL_CPU_THREADS", "2")))
        weights = os.environ["SENTINEL_DETECTOR_WEIGHTS"]
        checkpoint = os.environ["SENTINEL_GNN_CHECKPOINT"]
        if not Path(checkpoint).is_file():
            raise ValueError("A validated GNN checkpoint is required for production")
        self.workers = {}
        for config in configs:
            if not sources.get(config.camera_id, os.environ.get(config.url_env, "")).startswith(("rtsp://", "rtsps://")):
                raise ValueError(f"Set the RTSP source variable for {config.camera_id}")
            detector = ConstructionSafetyDetector(weights_path=weights, device=os.getenv("SENTINEL_DEVICE", "auto"))
            runtime = SentinelEdgeRuntime(config.homography, checkpoint, mqtt_host=None, device=detector.device, camera_profile=config.model_dump())
            # The configured site and the active manifest name different sites in
            # the original repository, so an exact match would leave this camera
            # with no zones at all. Resolve tolerantly and record which manifest
            # is in force, so events report a site that matches their own zone.
            from src.edge.manifest_selector import resolve_manifest
            from src.schemas.contracts import ShiftSafetyManifest
            resolved = resolve_manifest(config.site_id)
            if resolved:
                try:
                    manifest = ShiftSafetyManifest.model_validate(resolved["manifest"])
                except Exception:
                    manifest = None
                if manifest is not None:
                    loaded = runtime.load_shift_manifest(
                        manifest.model_dump(mode="json"), match=resolved["match"])
                    logger.info(
                        "camera %s: %d envelopes from %s (site=%s, match=%s)",
                        config.camera_id, loaded, resolved["path"].name,
                        manifest.site_id, resolved["match"])
            self.workers[config.camera_id] = CameraWorker(config, detector, runtime, sources.get(config.camera_id))

    def start(self):
        for worker in self.workers.values():
            worker.capture_thread.start()
            worker.thread.start()

    def close(self):
        if getattr(self, 'tunnel', None):
            self.tunnel.close()
        for worker in self.workers.values():
            worker.stop_event.set()
        for worker in self.workers.values():
            worker.capture_thread.join(timeout=6)
            worker.thread.join(timeout=12)

    def status(self):
        return [worker.snapshot() for worker in self.workers.values()]
