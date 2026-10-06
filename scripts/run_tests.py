"""Run tests without overwriting repository incident records or manifests."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
os.environ.setdefault('OMP_NUM_THREADS', '2')
# The full legacy suite exercises the original demo APIs. Production-specific
# subprocess tests set SENTINEL_MODE=production explicitly; keep the parent
# suite deterministic even when run from the production Docker image.
os.environ['SENTINEL_MODE'] = 'demo'
os.environ.pop('SENTINEL_API_TOKEN', None)
os.environ.pop('SENTINEL_CAMERA_CONFIG', None)
with tempfile.TemporaryDirectory(prefix='sentinel-tests-') as tmp:
    root = Path(tmp)
    for name in ('src', 'config', 'weights', 'yolov8n.pt'):
        (root / name).symlink_to(repo / name, target_is_directory=(repo / name).is_dir())
    (root / 'data').mkdir()
    for name in ('test_videos', 'real_videos', 'roboflow_downloaded'):
        (root / 'data' / name).symlink_to(repo / 'data' / name, target_is_directory=True)
    os.environ['YOLO_CONFIG_DIR'] = str(root / 'yolo')
    os.environ['MPLCONFIGDIR'] = str(root / 'matplotlib')
    os.chdir(root)
    suite = unittest.defaultTestLoader.discover(str(repo / 'tests'), pattern='test_*.py', top_level_dir=str(repo))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
