"""Model-backed detection. Empty scenes and unavailable inference are distinct."""
import os
from pathlib import Path

import numpy as np


class InferenceUnavailable(RuntimeError):
    pass


class ConstructionSafetyDetector:
    def __init__(self, weights_path=None, conf_threshold=0.35, iou_threshold=0.45, device=None):
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.model = None
        self.error = None
        if device is None or device == 'auto':
            try:
                import torch
                device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
            except ImportError:
                device = 'cpu'
        self.device = device
        target = Path(weights_path or os.getenv('SENTINEL_WEIGHTS', 'yolov8n.pt'))
        if not target.is_file():
            self.error = 'Model weights are missing; automatic downloads are disabled'
            return
        try:
            from ultralytics import YOLO
            self.model = YOLO(str(target))
        except Exception:
            self.error = 'Model could not be loaded'

    def detect(self, frame):
        if self.model is None:
            raise InferenceUnavailable(self.error or 'Detection model unavailable')
        if frame is None or frame.size == 0:
            raise ValueError('Expected a nonempty BGR image')
        try:
            results = self.model.predict(source=frame, conf=self.conf_threshold,
                                         iou=self.iou_threshold, device=self.device, verbose=False, max_det=100)
            boxes = results[0].boxes
            if boxes is None or len(boxes) == 0:
                return np.empty((0, 6), dtype=np.float32), []
            return self._process_detections(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy(),
                                            boxes.cls.cpu().numpy().astype(int), frame)
        except InferenceUnavailable:
            raise
        except Exception as exc:
            raise InferenceUnavailable('Model inference failed') from exc

    def _process_detections(self, xyxy, conf, cls_ids, frame=None):
        names = self.model.names if self.model is not None else {}
        normalize = lambda s: str(s).lower().replace('_', '').replace('-', '').replace(' ', '')
        # Map by model metadata, never by overlapping numeric class IDs.
        entities = {'person': 0, 'worker': 0, 'spotter': 1, 'machinery': 2,
                    'excavator': 2, 'forklift': 2, 'truck': 2, 'bus': 2, 'vehicle': 3, 'car': 3}
        ppe_names = {'hardhat': 'helmet', 'helmet': 'helmet', 'safetyvest': 'vest', 'vest': 'vest',
                     'nohardhat': 'no_helmet', 'nohelmet': 'no_helmet', 'nosafetyvest': 'no_vest', 'novest': 'no_vest'}
        output, equipment = [], []
        for box, score, cid in zip(xyxy, conf, cls_ids):
            if score < self.conf_threshold:
                continue
            name = normalize(names.get(int(cid), '') if isinstance(names, dict) else names[int(cid)])
            if name in entities:
                output.append([*map(float, box), float(score), entities[name]])
            elif name in ppe_names:
                equipment.append((box, ppe_names[name]))
        ppe = []
        for index, det in enumerate(output):
            if det[5] not in (0, 1):
                continue
            x1, y1, x2, y2 = det[:4]
            present = {name for b, name in equipment if x1 <= (b[0]+b[2])/2 <= x2 and y1 <= (b[1]+b[3])/2 <= y2}
            helmet = 'noncompliant' if 'no_helmet' in present else ('compliant' if 'helmet' in present else 'unknown')
            vest = 'noncompliant' if 'no_vest' in present else ('compliant' if 'vest' in present else 'unknown')
            state = 'noncompliant' if 'noncompliant' in (helmet, vest) else ('compliant' if helmet == vest == 'compliant' else 'unknown')
            ppe.append({'detection_index': index, 'worker_index': index, 'helmet': helmet, 'vest': vest, 'state': state,
                        'has_hardhat': helmet == 'compliant', 'has_vest': vest == 'compliant', 'ppe_compliant': state == 'compliant'})
        return np.asarray(output, dtype=np.float32).reshape(-1, 6), ppe
