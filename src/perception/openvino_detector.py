"""OpenVINO GPU inference with the same YOLO postprocessing and class mapping."""
import os
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.perception.detector import ConstructionSafetyDetector, InferenceUnavailable


class OpenVINOSafetyDetector(ConstructionSafetyDetector):
    def __init__(self, device, model_path=None, conf_threshold=.35, iou_threshold=.45):
        import openvino as ov
        import yaml
        target = Path(model_path or os.getenv('SENTINEL_OPENVINO_MODEL', '/opt/sentinel/yolov8n_openvino_model/yolov8n.xml'))
        if not target.is_file():
            raise InferenceUnavailable('Packaged OpenVINO model is missing')
        weights = Path(os.getenv('SENTINEL_WEIGHTS', '/opt/sentinel/yolov8n.pt'))
        fingerprint = target.parent / 'source.sha256'
        if not fingerprint.is_file() or not weights.is_file() or fingerprint.read_text().strip() != hashlib.sha256(weights.read_bytes()).hexdigest():
            raise InferenceUnavailable('OpenVINO export does not match configured model weights')
        names = yaml.safe_load((target.parent / 'metadata.yaml').read_text())['names']
        self.model = SimpleNamespace(names=names)
        self.device = device
        self.conf_threshold, self.iou_threshold = conf_threshold, iou_threshold
        self.compiled = ov.Core().compile_model(str(target), device.split(':', 1)[1], {'PERFORMANCE_HINT': 'LATENCY'})
        self.output = self.compiled.output(0)
        shape = list(self.compiled.input(0).shape)
        self.size = tuple(shape[2:])

    def detect(self, frame):
        import torch
        from ultralytics.data.augment import LetterBox
        from ultralytics.utils import ops
        if frame is None or frame.size == 0:
            raise ValueError('Expected a nonempty BGR image')
        try:
            image = LetterBox(self.size, auto=False)(image=frame)
            tensor = np.ascontiguousarray(image[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
            output = self.compiled([tensor])[self.output]
            if output.ndim != 3 or output.shape[1] != 4 + len(self.model.names):
                raise InferenceUnavailable('Unsupported OpenVINO detector output')
            detections = ops.non_max_suppression(torch.from_numpy(output.copy()), self.conf_threshold,
                                                  self.iou_threshold, nc=len(self.model.names), max_det=100)[0]
            if len(detections) == 0:
                return np.empty((0, 6), dtype=np.float32), []
            detections[:, :4] = ops.scale_boxes(self.size, detections[:, :4], frame.shape)
            boxes = detections.numpy()
            return self._process_detections(boxes[:, :4], boxes[:, 4], boxes[:, 5].astype(int), frame)
        except InferenceUnavailable:
            raise
        except Exception:
            raise InferenceUnavailable('OpenVINO GPU inference failed') from None
