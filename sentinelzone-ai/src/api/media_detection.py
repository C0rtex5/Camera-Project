"""Calibrated person / heavy-machinery detection for restored recordings.

The dashboard scales every box with ``canvas / packet.frame_width``. This module
guarantees the boxes it returns live in the pixel space of the exact JPEG the
browser decodes: inference runs on an aspect-preserving downscale and the boxes
are mapped back with the same exact factors used to build the served frame.

Detection is best-effort and honest about it:

* no weights configured, or a load failure -> no boxes and an explicit reason;
* the returned boxes carry the model confidence and the sentinel class id, and
  the caller is expected to show the recording as *not validated* regardless,
  because a decoded frame is not a validated safety measurement.

Set ``SENTINEL_MEDIA_DETECT=0`` to disable overlay detection entirely.
"""
from __future__ import annotations

import atexit
import logging
import time
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from src.perception.frame_calibration import (
    HEAVY_EQUIPMENT,
    LIGHT_VEHICLE,
    WORKER,
    clip_boxes,
    geometry_for,
    resize_for_inference,
    to_native,
)

logger = logging.getLogger("MediaDetection")

_LOCK = threading.RLock()
_DETECTOR = None
_DETECTOR_KEY: Optional[str] = None
_DETECTOR_ERROR: Optional[str] = None
_CACHE: OrderedDict = OrderedDict()
# Detection payloads are small (a handful of boxes per frame), so the cache is
# sized to hold an entire clip. At 384 entries a 901-frame recording evicted its
# own warm results and fell back to ~60 ms inline inference mid-playback.
CACHE_SIZE = int(os.getenv("SENTINEL_MEDIA_DETECTION_CACHE", "1600"))
DEFAULT_IMGSZ = 960


def detection_enabled() -> bool:
    return os.getenv("SENTINEL_MEDIA_DETECT", "1").strip().lower() not in ("0", "false", "no", "off")


REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_weights_path() -> Optional[str]:
    """Locate detector weights regardless of the process working directory.

    The service may be launched from a scratch directory (the source app runs
    from ``.source-runtime``), so a bare relative name such as ``yolov8n.pt``
    is resolved against the installation root as well.
    """
    configured = os.getenv("SENTINEL_DETECTOR_WEIGHTS")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    else:
        candidates.extend([REPO_ROOT / "yolov8n.pt", Path("yolov8n.pt")])
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def get_detector():
    """Lazily build the shared detector. Returns (detector, error).

    The success is cached against the resolved weights path, but a *failure* is
    never memoised permanently: a weights file that is missing now may appear
    after a volume is mounted or a job is published, and a cached failure would
    silently disable the overlay for the rest of the process lifetime.
    """
    global _DETECTOR, _DETECTOR_KEY, _DETECTOR_ERROR
    weights = resolve_weights_path()
    with _LOCK:
        if weights is None:
            _DETECTOR_ERROR = f"detector weights not available: {os.getenv('SENTINEL_DETECTOR_WEIGHTS') or 'yolov8n.pt'}"
            return None, _DETECTOR_ERROR
        if _DETECTOR is not None and _DETECTOR_KEY == weights:
            return _DETECTOR, None
        try:
            from src.perception.detector import ConstructionSafetyDetector

            detector = ConstructionSafetyDetector(
                weights_path=weights,
                device=os.getenv("SENTINEL_DEVICE", "cpu"),
                conf_threshold=float(os.getenv("SENTINEL_DETECTOR_CONF", "0.25")),
                iou_threshold=float(os.getenv("SENTINEL_DETECTOR_IOU", "0.45")),
                # The original app's PPE compliance rule: HSV inspection of the
                # head and torso crops. It only adds the per-worker PPE records;
                # the detection path is unchanged.
                allow_demo_fallback=True,
            )
        except Exception as error:  # pragma: no cover - depends on local weights
            _DETECTOR_ERROR = f"detector unavailable: {type(error).__name__}"
            logger.warning("Overlay detection disabled: %s", _DETECTOR_ERROR)
            return None, _DETECTOR_ERROR
        _DETECTOR, _DETECTOR_KEY, _DETECTOR_ERROR = detector, weights, None
        return detector, None


#: Ultralytics predictors are not safe to call concurrently: ``predict()`` mutates
#: the model's own predictor and overrides, so two threads sharing one detector
#: corrupt each other's run. Measured with four prewarm workers: the same frame
#: returned different boxes on 40% of re-reads, which is what put boxes on frames
#: they did not belong to. Inference is therefore serialised. Frame decode and
#: letterboxing still overlap; only the forward pass is exclusive, and torch
#: already parallelises inside it.
_INFERENCE_LOCK = threading.Lock()

_WORKER_LOCAL = threading.local()


def build_detector(weights: Optional[str] = None):
    """Construct a detector instance without touching the shared cache."""
    from src.perception.detector import ConstructionSafetyDetector

    target = weights or resolve_weights_path()
    if target is None:
        return None, f"detector weights not available: {os.getenv('SENTINEL_DETECTOR_WEIGHTS') or 'yolov8n.pt'}"
    try:
        return ConstructionSafetyDetector(
            weights_path=target,
            device=os.getenv("SENTINEL_DEVICE", "cpu"),
            conf_threshold=float(os.getenv("SENTINEL_DETECTOR_CONF", "0.25")),
            iou_threshold=float(os.getenv("SENTINEL_DETECTOR_IOU", "0.45")),
            allow_demo_fallback=True,
        ), None
    except Exception as error:  # pragma: no cover - depends on local weights
        return None, f"detector unavailable: {type(error).__name__}"


def worker_detector():
    """A detector owned by the calling thread.

    A single shared instance serialises the warmers: measured 17 detections/s
    with four threads sharing one model, against 42 detections/s when each
    thread owns its own.
    """
    weights = resolve_weights_path()
    if weights is None:
        return None, f"detector weights not available: {os.getenv('SENTINEL_DETECTOR_WEIGHTS') or 'yolov8n.pt'}"
    if getattr(_WORKER_LOCAL, "key", None) != weights:
        detector, error = build_detector(weights)
        if detector is None:
            return None, error
        _WORKER_LOCAL.detector, _WORKER_LOCAL.key = detector, weights
    return getattr(_WORKER_LOCAL, "detector", None), None


def _ppe_to_native(telemetry, geometry, width: int, height: int) -> list[dict]:
    """Map the detector's per-worker PPE records back onto the native frame.

    The PPE crops are taken on the resized inference frame, so the boxes must be
    mapped exactly like the detections are, or the panel would match a note to
    the wrong worker.
    """
    records = []
    for record in telemetry or []:
        box = record.get("bbox")
        try:
            mapped = clip_boxes(
                to_native(np.asarray([box], dtype=np.float32).reshape(-1, 4), geometry), width, height
            )[0]
        except Exception:
            # A malformed record must not cost the caller its detections.
            continue
        records.append({
            "bbox": [round(float(v), 2) for v in mapped[:4]],
            "hardhat": bool(record.get("hardhat_detected")),
            "vest": bool(record.get("vest_detected")),
            # False means the worker was too small or too blurred to judge; the
            # panel shows UNKNOWN rather than claiming a missing hardhat.
            "measured": bool(record.get("measured")),
            "ppe_compliant": record.get("ppe_compliant"),
        })
    return records


def detect_boxes(frame: np.ndarray, imgsz: int = DEFAULT_IMGSZ, detector=None, error: Optional[str] = None) -> tuple[list[list[float]], dict, list[dict]]:
    """Return boxes and PPE records in the coordinate space of ``frame``.

    The PPE records come from the same inference call as the boxes, so enabling
    them costs no extra inference; they are carried through the cache with the
    boxes rather than requested again.
    """
    if not detection_enabled():
        return [], {"enabled": False, "reason": "disabled by SENTINEL_MEDIA_DETECT"}, []
    if detector is None and not error:
        detector, error = get_detector()
    if detector is None:
        return [], {"enabled": False, "reason": error}, []

    height, width = frame.shape[:2]
    geometry = geometry_for(width, height, imgsz)
    resized, geometry = resize_for_inference(frame, imgsz, geometry)
    started = time.perf_counter()
    with _INFERENCE_LOCK:
        raw, ppe_telemetry = detector.detect(resized)
    inference_ms = round(1000 * (time.perf_counter() - started), 2)
    native = clip_boxes(to_native(np.asarray(raw, dtype=np.float32).reshape(-1, 6), geometry), width, height)
    boxes = [[round(float(v), 2) for v in row[:6]] for row in native]
    ppe = _ppe_to_native(ppe_telemetry, geometry, width, height)
    calibration = {
        "enabled": True,
        "frame_width": width,
        "frame_height": height,
        "inference_width": geometry.inference_width,
        "inference_height": geometry.inference_height,
        "uniform_scale": geometry.uniform,
        "inference_ms": inference_ms,
        "max_relative_scale_error": round(geometry.max_relative_error, 9),
        "workers": sum(1 for row in native if int(row[5]) == WORKER),
        "heavy_equipment": sum(1 for row in native if int(row[5]) == HEAVY_EQUIPMENT),
        "light_vehicle": sum(1 for row in native if int(row[5]) == LIGHT_VEHICLE),
    }
    return boxes, calibration, ppe


def source_index_for(media_id: str, output_index: int) -> int:
    """Output (30 fps) index -> source frame index that supplies the pixels."""
    try:
        from src.api import media as media_module

        entry = media_module.media_index().get(media_id) or {}
        return media_module.source_index_for_output(
            output_index,
            entry.get("source_fps") or entry.get("fps") or 0.0,
            entry.get("source_frame_count") or entry.get("frame_count") or 0,
        )
    except Exception:  # pragma: no cover - catalog access is best effort
        return output_index


def cached_detection(media_id: str, index: int, mtime_ns: int,
                     imgsz: int = DEFAULT_IMGSZ) -> bool:
    """Whether this frame's detection is already cached."""
    key = (media_id, source_index_for(media_id, index), mtime_ns, imgsz)
    with _LOCK:
        return key in _CACHE


def _store(key, result):
    with _LOCK:
        _CACHE[key] = result
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return result


def detect_for_media(media_id: str, path: str, index: int, mtime_ns: int, imgsz: int = DEFAULT_IMGSZ, frame: Optional[np.ndarray] = None) -> tuple[list[list[float]], dict, list[dict]]:
    """Cached calibrated detection for one frame of one recording.

    Serves from cache when possible. On a miss the caller may pass an already
    decoded ``frame`` so the video is read once instead of twice; otherwise the
    frame is pulled through the shared sequential reader. Either way the caller
    gets a genuine per-frame detection - nothing is interpolated or repeated.
    """
    source_index = source_index_for(media_id, index)
    key = (media_id, source_index, mtime_ns, imgsz)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

    if not detection_enabled():
        # Three elements, like every other return: the caller unpacks
        # `boxes, calibration, ppe`, so a two-element return here raised
        # "not enough values to unpack" and turned every frame into a 500 in the
        # mode documented as "disable the overlay entirely".
        return [], {"enabled": False, "reason": "disabled by SENTINEL_MEDIA_DETECT"}, []

    if frame is None:
        try:
            from src.api import media as media_module

            raw = media_module.frame_source(path).read(source_index)
            frame, _ = media_module.normalize_frame(raw) if raw is not None else None
        except Exception:
            frame = None
    result = ([], {"enabled": False, "reason": "frame could not be decoded"}, []) if frame is None else detect_boxes(frame, imgsz)

    # A frame that could not be decoded is not a detection: caching it would
    # serve an empty frame as though the clip had been analysed and found empty.
    if frame is not None:
        _store(key, result)
    try:
        from src.api import media as media_module

        entry = media_module.media_index().get(media_id) or {}
        total = entry.get("source_frame_count") or entry.get("frame_count") or 0
    except Exception:  # pragma: no cover - catalog access is best effort
        total = 0
    prewarm(media_id, path, source_index, total, imgsz)
    return result


class _Prewarmer:
    """Parallel background detection workers that stay ahead of the playhead.

    Detection costs ~60 ms per frame on one CPU thread, which caps a single
    worker near 18 detections/s - below the 30 fps the player now requests, so
    the playhead outruns the producer and playback falls back to ~5 fps.

    Four workers measured 42 detections/s on this machine, which outruns a 30 fps
    consumer. Each worker owns a private capture and claims frames from a shared
    counter in round-robin order, so every worker advances sequentially and never
    seeks.

    Work is keyed on the *source* frame index: presenting a 24 fps recording at
    30 fps holds some frames, and those display frames share one genuine
    detection of the same real source frame. Nothing is interpolated or
    synthesised.
    """

    def __init__(self, media_id: str, path: str, mtime_ns: int, imgsz: int, workers: int = 0):
        from src.api import media as media_module

        self.media_id = media_id
        self.path = path
        self.mtime_ns = mtime_ns
        self.imgsz = imgsz
        self.workers = workers or int(os.getenv("SENTINEL_MEDIA_PREWARM_WORKERS", "4"))
        entry = media_module.media_index().get(media_id) or {}
        self.source_fps = entry.get("source_fps") or entry.get("fps") or 0.0
        self.source_frames = entry.get("source_frame_count") or entry.get("frame_count") or 0
        self.output_total = entry.get("frame_count") or self.source_frames
        self._normalize = media_module.normalize_frame
        self.cursor = 0
        self.target = 0
        self._claim = 0
        self.lock = threading.Lock()
        self.threads: list = []
        self.detectors: list = []
        self.detector_error: Optional[str] = None

    def _encode_outputs(self, source_index: int, normalized) -> None:
        """Pre-encode every output frame that shares this source frame.

        Playing a clip back to its start then costs no decode at all, which
        removes the wrap-around seek that used to stall every loop.
        """
        from src.api import media as media_module

        if not self.source_fps or not self.source_frames:
            return
        for output_index in self._output_indices_for(source_index):
            try:
                key = media_module.frame_key(self.media_id, output_index)
                if media_module.cached_frame(key) is not None:
                    continue
                media_module.encode_jpeg(normalized, key)
            except Exception:  # pragma: no cover - pre-encoding is best effort
                continue

    def _output_indices_for(self, source_index: int) -> list:
        from src.api import media as media_module

        # A source frame can feed more than one 30 fps output frame (a 24 fps
        # source is presented at 30 fps by holding frames).
        approx = int(round(source_index * media_module.NORMALIZED_FPS / float(self.source_fps)))
        indices = []
        for candidate in (approx - 1, approx, approx + 1):
            if 0 <= candidate < self.output_total:
                if media_module.source_index_for_output(candidate, self.source_fps, self.source_frames) == source_index:
                    indices.append(candidate)
        return indices


    def request(self, output_index: int, total_output: int) -> None:
        if _SHUTDOWN.is_set():
            return
        budget = int(os.getenv("SENTINEL_MEDIA_PREWARM", "1200"))
        if budget <= 0:
            return
        # How many source frames the requested output window actually needs.
        from src.api import media as media_module

        first_source = media_module.source_index_for_output(output_index, self.source_fps, self.source_frames)
        wanted = min(self.source_frames, first_source + budget)
        with self.lock:
            if self.cursor == 0 and self.target == 0 and first_source > 0:
                self.cursor = first_source  # opened mid-clip: start at the viewer
            self.target = max(self.target, wanted)
            if any(thread.is_alive() for thread in self.threads):
                return
            # Build the per-slot models sequentially. Constructing several
            # YOLO instances concurrently is unsafe and was dropping the tail
            # of a worker's range.
            self._claim = self.cursor
            slots = max(1, self.workers)
            if len(self.detectors) != slots:
                self.detectors = []
                for _ in range(slots):
                    detector, error = build_detector()
                    if detector is None:
                        self.detector_error = error
                        break
                    self.detectors.append(detector)
            if not self.detectors:
                detector, error = get_detector()
                if detector is None:
                    return
                self.detectors = [detector]
            self.threads = []
            for slot in range(len(self.detectors)):
                thread = threading.Thread(target=self._run, args=(slot,), name=f"prewarm-{self.media_id}-{slot}", daemon=True)
                self.threads.append(thread)
                thread.start()

    def stop(self) -> None:
        """Release this clip's background workers so another can be warmed."""
        with self.lock:
            self.target = self.cursor
        for thread in list(self.threads):
            if thread.is_alive():
                thread.join(timeout=0.2)

    def _run(self, slot: int) -> None:
        try:
            capture = cv2.VideoCapture(self.path)
            if not capture.isOpened():
                return
            try:
                cursor = None
                while not _SHUTDOWN.is_set():
                    with self.lock:
                        index = self._claim
                        if index >= self.target:
                            return
                        self._claim = index + 1
                    if cursor is None or index < cursor or index - cursor > 240:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                        cursor = index
                    while cursor < index:
                        if not capture.grab():
                            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                            cursor = index
                            break
                        cursor += 1
                    key = (self.media_id, index, self.mtime_ns, self.imgsz)
                    with _LOCK:
                        if key in _CACHE:
                            with self.lock:
                                self.cursor = max(self.cursor, index + 1)
                            continue
                    ok, frame = capture.read()
                    if not ok:
                        # A failed read must not abandon the rest of this
                        # worker's range: retry once from an exact seek.
                        try:
                            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                            ok, frame = capture.read()
                        except Exception:
                            ok, frame = False, None
                    if not ok:
                        continue
                    try:
                        normalized, _ = self._normalize(frame)
                        _store(key, detect_boxes(normalized, self.imgsz, detector=self.detectors[slot]))
                    except Exception:  # pragma: no cover - keep the worker alive
                        continue
                    # Progress is only reported once the result is really stored.
                    with self.lock:
                        self.cursor = max(self.cursor, index + 1)
                    self._encode_outputs(index, normalized)
            finally:
                capture.release()
        except Exception:  # pragma: no cover - prewarming is best effort
            return


_PREWARMERS: dict = {}
_SHUTDOWN = threading.Event()


def shutdown(timeout: float = 1.0) -> None:
    """Stop background workers before interpreter teardown.

    Without this a daemon thread can be destroyed while still inside an OpenCV
    read, which prints a confusing "terminate called without an active
    exception" during shutdown.
    """
    _SHUTDOWN.set()
    for worker in list(_PREWARMERS.values()):
        for thread in list(getattr(worker, "threads", []) or []):
            if thread.is_alive():
                thread.join(timeout=timeout)
    try:
        from src.api import media as media_module

        media_module.close_frame_sources()
    except Exception:  # pragma: no cover - best effort
        pass


atexit.register(shutdown)


def prewarm(media_id: str, path: str, source_index: int, source_total: int, imgsz: int = DEFAULT_IMGSZ, mtime_ns: int = 0) -> None:
    """Ask the background workers to stay ahead of the playhead."""
    if not detection_enabled():
        return
    if int(os.getenv("SENTINEL_MEDIA_PREWARM", "1200")) <= 0:
        return  # explicitly disabled
    if not mtime_ns:
        try:
            mtime_ns = Path(path).stat().st_mtime_ns
        except OSError:
            return
    worker = _PREWARMERS.get(media_id)
    if worker is None or worker.mtime_ns != mtime_ns:
        # Warming several clips at once starves the clip actually being watched:
        # 14 clips x 4 workers saturated the CPU and pushed the serve path from
        # ~6 ms to ~28 ms median. Only the clip in use gets background workers;
        # others are served inline (correct, just not pre-warmed) until selected.
        limit = max(1, int(os.getenv("SENTINEL_MEDIA_PREWARM_CLIPS", "1")))
        warming = [existing for existing in _PREWARMERS.values() if any(t.is_alive() for t in existing.threads)]
        if worker is None and len(warming) >= limit:
            # The clip the viewer just asked for wins the slot: stop the
            # background workers of the ones nobody is looking at any more.
            for existing in warming[: len(warming) - limit + 1]:
                existing.stop()
        worker = _Prewarmer(media_id, path, mtime_ns, imgsz)
        _PREWARMERS[media_id] = worker
        if len(_PREWARMERS) > 8:
            _PREWARMERS.pop(next(iter(_PREWARMERS)))
    worker.request(source_index, source_total or source_index + 1)


def prewarm_state(media_id: str) -> dict:
    worker = _PREWARMERS.get(media_id)
    if worker is None:
        # A clip that has not started warming - including one skipped because
        # another clip holds the prewarm slot - must still report the full shape.
        # Returning a partial dict makes a caller that reads ready_through fail
        # with a KeyError instead of simply seeing zero progress.
        return {"active": False, "ready_through": 0, "target": 0, "workers": 0}
    return {
        "active": any(thread.is_alive() for thread in worker.threads),
        "ready_through": max(0, worker.cursor - 1),
        "target": worker.target,
        "workers": worker.workers,
    }


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
