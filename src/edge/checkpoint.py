"""Contract tying a forecasting artifact to camera calibration and sampling."""
import hashlib
import json
from pathlib import Path

import numpy as np

from src.edge.gatv2_model import HAS_PYG


def camera_fingerprint(camera):
    normalized = {key: camera[key] for key in ('site_id', 'camera_id', 'width', 'height')}
    normalized['homography'] = {key: np.asarray(camera['homography'][key], dtype=float).tolist() for key in ('K', 'dist', 'H')}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def model_contract():
    return {
        'format_version': 1,
        'architecture': 'WorkZoneSTGNN',
        'graph_backend': 'torch_geometric' if HAS_PYG else 'native',
        'observation_steps': 30,
        'observation_interval_seconds': 0.1,
        'forecast_steps': 50,
        'forecast_interval_seconds': 0.1,
        'coordinate_space': 'absolute_metric_ground_xy',
        'class_ids': {'worker': 0, 'spotter': 1, 'heavy_equipment': 2, 'light_vehicle': 3},
    }


def validate_checkpoint(payload, camera):
    if not isinstance(payload, dict) or 'model_state_dict' not in payload or 'metadata' not in payload:
        raise ValueError('Production requires a checkpoint with camera and validation metadata')
    metadata = payload['metadata']
    for key, expected in model_contract().items():
        if metadata.get(key) != expected:
            raise ValueError(f'Forecast checkpoint contract mismatch: {key}')
    if metadata.get('camera_fingerprint') != camera_fingerprint(camera):
        raise ValueError('Forecast checkpoint does not match this camera calibration/resolution')
    validation = metadata.get('validation', {})
    for key in ('ade_m', 'fde_m'):
        if not isinstance(validation.get(key), (float, int)) or not np.isfinite(validation[key]) or validation[key] < 0:
            raise ValueError('Forecast checkpoint lacks finite held-out validation metrics')
    if validation.get('samples', 0) < 1 or not metadata.get('dataset_sha256'):
        raise ValueError('Forecast checkpoint lacks held-out data provenance')
    return payload['model_state_dict']
