"""Frame calibration for detection boxes.

The dashboard draws a detection box by scaling it with
``canvas / packet.frame_width`` and ``canvas / packet.frame_height``. A box is
therefore only correct when it is expressed in the *pixel space of the image the
UI actually decoded*. The restored recordings come back at native resolution
(720p up to 4K, plus portrait clips), so two different corrections matter:

1. **Inference scale** - running the detector on a downscaled copy for speed and
   mapping the returned boxes back to the native frame.
2. **Display scale** - mapping native-frame boxes onto the frame the packet
   advertises, which is the frame the browser decodes.

Both mappings are exact, reversible and unit-tested. Nothing here guesses: a
calibration error is measured, not assumed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_STRIDE = 32
# SentinelZone tracker taxonomy produced by the detector.
WORKER = 0
SPOTTER = 1
HEAVY_EQUIPMENT = 2
LIGHT_VEHICLE = 3
CLASS_NAMES = {WORKER: "worker", SPOTTER: "spotter", HEAVY_EQUIPMENT: "heavy_equipment", LIGHT_VEHICLE: "light_vehicle"}


@dataclass(frozen=True)
class InferenceGeometry:
    """How a native frame was resized for inference, and how to invert it."""

    source_width: int
    source_height: int
    inference_width: int
    inference_height: int

    @property
    def scale_x(self) -> float:
        return self.source_width / self.inference_width

    @property
    def scale_y(self) -> float:
        return self.source_height / self.inference_height

    @property
    def uniform(self) -> bool:
        """True when the resize kept the aspect ratio, so one scale applies to both axes."""
        return abs(self.scale_x - self.scale_y) < 1e-6

    @property
    def max_relative_error(self) -> float:
        return abs(self.scale_x - self.scale_y) / max(self.scale_x, self.scale_y, 1e-9)


def inference_size(width: int, height: int, imgsz: int = 960, stride: int | None = None) -> tuple[int, int]:
    """Aspect-preserving size no larger than ``imgsz`` on the long side.

    The aspect ratio is kept *exactly* so the resize is a single uniform scale
    and the inverse box mapping carries no cross-axis error. ``stride`` may be
    supplied to snap both sides to a multiple of that stride (marginally faster
    padding inside the detector) at the cost of a small aspect distortion; the
    measured calibration error is reported either way.
    """
    if width <= 0 or height <= 0:
        raise ValueError("Frame dimensions must be positive")
    imgsz = max(1, int(imgsz))
    scale = min(1.0, imgsz / float(max(width, height)))
    target_w = max(1, int(round(width * scale)))
    target_h = max(1, int(round(height * scale)))
    if stride:
        target_w = max(stride, int(round(target_w / stride)) * stride)
        target_h = max(stride, int(round(target_h / stride)) * stride)
        while max(target_w, target_h) > imgsz and max(target_w, target_h) > stride:
            if target_w >= target_h and target_w > stride:
                target_w -= stride
            elif target_h > stride:
                target_h -= stride
            else:
                break
        return target_w, target_h

    # Without a stride constraint, search a small window for the pair that keeps
    # the aspect ratio closest, so scale_x and scale_y agree as tightly as the
    # integer grid allows.
    best = (target_w, target_h)
    best_error = _aspect_error(width, height, *best)
    for delta_w in (-2, -1, 0, 1, 2):
        for delta_h in (-2, -1, 0, 1, 2):
            candidate_w, candidate_h = target_w + delta_w, target_h + delta_h
            if candidate_w < 1 or candidate_h < 1 or max(candidate_w, candidate_h) > imgsz:
                continue
            error = _aspect_error(width, height, candidate_w, candidate_h)
            if error < best_error:
                best, best_error = (candidate_w, candidate_h), error
    return best


def _aspect_error(width: int, height: int, target_w: int, target_h: int) -> float:
    scale_x = width / target_w
    scale_y = height / target_h
    return abs(scale_x - scale_y) / max(scale_x, scale_y)


def geometry_for(width: int, height: int, imgsz: int = 960) -> InferenceGeometry:
    inference_width, inference_height = inference_size(width, height, imgsz)
    return InferenceGeometry(width, height, inference_width, inference_height)


def resize_for_inference(frame: np.ndarray, imgsz: int = 960, geometry: InferenceGeometry | None = None) -> tuple[np.ndarray, InferenceGeometry]:
    import cv2

    height, width = frame.shape[:2]
    geometry = geometry or geometry_for(width, height, imgsz)
    if (geometry.inference_width, geometry.inference_height) == (width, height):
        return frame, geometry
    resized = cv2.resize(frame, (geometry.inference_width, geometry.inference_height), interpolation=cv2.INTER_AREA)
    return resized, geometry


def scale_boxes(boxes: np.ndarray, scale_x: float, scale_y: float) -> np.ndarray:
    """Map boxes from inference space back to native frame space."""
    if boxes is None or len(boxes) == 0:
        return np.empty((0, 6), dtype=np.float32)
    scaled = np.array(boxes, dtype=np.float32, copy=True)
    scaled[:, 0] *= scale_x
    scaled[:, 1] *= scale_y
    scaled[:, 2] *= scale_x
    scaled[:, 3] *= scale_y
    return scaled


def to_native(boxes: np.ndarray, geometry: InferenceGeometry) -> np.ndarray:
    return scale_boxes(boxes, geometry.scale_x, geometry.scale_y)


def to_display(boxes: np.ndarray, source_width: int, source_height: int, display_width: int, display_height: int) -> np.ndarray:
    """Map native-frame boxes onto the frame the packet advertises.

    ``display_*`` must describe the image the browser decodes, otherwise the
    dashboard scales the boxes with the wrong denominator.
    """
    return scale_boxes(boxes, display_width / float(source_width), display_height / float(source_height))


def clip_boxes(boxes: np.ndarray, width: int, height: int) -> np.ndarray:
    if boxes is None or len(boxes) == 0:
        return np.empty((0, 6), dtype=np.float32)
    clipped = np.array(boxes, dtype=np.float32, copy=True)
    clipped[:, 0] = np.clip(clipped[:, 0], 0, width)
    clipped[:, 2] = np.clip(clipped[:, 2], 0, width)
    clipped[:, 1] = np.clip(clipped[:, 1], 0, height)
    clipped[:, 3] = np.clip(clipped[:, 3], 0, height)
    return clipped


def iou(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection-over-union of two xyxy boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def calibration_report(geometry: InferenceGeometry, boxes: np.ndarray | None = None) -> dict:
    """Measured calibration facts, suitable for an audit record."""
    report = {
        "source_width": geometry.source_width,
        "source_height": geometry.source_height,
        "inference_width": geometry.inference_width,
        "inference_height": geometry.inference_height,
        "scale_x": round(geometry.scale_x, 6),
        "scale_y": round(geometry.scale_y, 6),
        "uniform_scale": geometry.uniform,
        "max_relative_scale_error": round(geometry.max_relative_error, 9),
        "box_count": 0 if boxes is None else int(len(boxes)),
    }
    if boxes is not None and len(boxes):
        report["box_width_px_min"] = round(float(np.min(boxes[:, 2] - boxes[:, 0])), 3)
        report["box_width_px_max"] = round(float(np.max(boxes[:, 2] - boxes[:, 0])), 3)
        report["out_of_frame_boxes"] = int(
            np.sum(
                (boxes[:, 0] < -1) | (boxes[:, 1] < -1)
                | (boxes[:, 2] > geometry.source_width + 1) | (boxes[:, 3] > geometry.source_height + 1)
            )
        )
    return report
