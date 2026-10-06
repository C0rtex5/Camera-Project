import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import torch

from src.api.live_service import CameraConfig
from src.edge.checkpoint import camera_fingerprint, validate_checkpoint


class ForecastTrainingTests(unittest.TestCase):
    def test_training_artifact_matches_camera_contract(self):
        profile = CameraConfig(camera_id='gate', site_id='SITE-01', url_env='CAMERA_GATE', width=640, height=480,
                               homography={'K': np.eye(3).tolist(), 'H': np.eye(3).tolist(), 'dist': [0]*5}).model_dump()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'train').mkdir()
            (root/'validation').mkdir()
            (root/'camera.json').write_text(json.dumps([profile]))
            (root/'metadata.json').write_text(json.dumps({'camera_fingerprint': camera_fingerprint(profile), 'sample_interval_seconds': .1}))
            for split, offset in [('train', 0.), ('validation', .1)]:
                # Synthetic fixtures verify mechanics only, never site model accuracy.
                history = np.zeros((2,30,8), np.float32)
                history[1,:,0] = 3 + offset
                target = np.zeros((2,50,2), np.float32)
                target[1,:,0] = 3 + offset
                np.savez(root/split/'sample.npz', node_history=history, class_ids=np.array([0,2]),
                         edge_index=np.array([[0,1],[1,0]]), edge_attr=np.zeros((2,5),np.float32), future_xy=target)
            output = root/'candidate.pt'
            script = Path(__file__).resolve().parents[1]/'scripts/train_forecaster.py'
            run = subprocess.run([sys.executable,str(script),'--dataset',str(root),'--camera-config',str(root/'camera.json'),
                                  '--output',str(output),'--epochs','1'],capture_output=True,text=True,timeout=40)
            self.assertEqual(run.returncode,0,run.stderr)
            artifact = torch.load(output, map_location='cpu', weights_only=True)
            self.assertTrue(validate_checkpoint(artifact,profile))
            with self.assertRaises(ValueError):
                validate_checkpoint(artifact,{**profile,'width':1280})
            artifact['metadata']['observation_interval_seconds'] = .2
            with self.assertRaises(ValueError):
                validate_checkpoint(artifact,profile)

    def test_legacy_unannotated_weights_rejected_in_production(self):
        with self.assertRaises(ValueError):
            validate_checkpoint({'arbitrary_weights': torch.zeros(1)}, {})
