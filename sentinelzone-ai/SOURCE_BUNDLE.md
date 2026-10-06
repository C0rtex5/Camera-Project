# Source bundle — what is inside, and what is not

One archive of the application's source. Unpack it and build from it:

```bash
tar -xzf sentinelzone-ai-source-2026-10-02.tar.gz
cd sentinelzone-ai
docker build -f Dockerfile -t sentinelzone-ai:local .
```

## Inside

| | |
|---|---|
| `src/` | the application: `api/`, `dashboard/`, `perception/`, `graph/`, `edge/`, `spatial/`, `schemas/`, and the generated demo assets the hub falls back to |
| `tests/` | 377 Python tests and 4 browser suites |
| `scripts/` | the test runner, the image builders, the validation harnesses |
| `config/`, `data/manifests/` | the site configuration and the hazard manifests in force |
| `deploy/` | deployment documentation, the systemd unit, and the validation record |
| `Dockerfile`, `Dockerfile.edge`, `Dockerfile.gpu`, `Dockerfile.demo-media` | the images |
| `compose*.yaml` | compose files for the demo, GPU and SSH variants |
| `requirements-runtime.lock`, `pyproject.toml` | the pinned runtime and the project definition |
| `weights/yolo26n.pt`, `yolov8n.pt` | the detector weights |
| `data/roboflow_downloaded/data.yaml` | the dataset description the settings test checks |
| `data/construction_safety/`, `data/incidents/` | the sample datasets and sample incidents |
| `dam_sample_*.jpg` | the detector test images |

## Not inside, and why

| excluded | size | where it is |
|---|---|---|
| `.git/` | 493 MB | the original checkout |
| `.venv/`, `.venv-prod/` | 1.9 GB | recreate from `requirements-runtime.lock` |
| `build/`, `*.egg-info`, `__pycache__` | generated | recreate by running |
| `data/real_videos/`, `data/test_videos/` | 149 MB | **inside the Docker image** at `/opt/sentinelzone/data` |
| `data/roboflow_downloaded/*/` | 29 MB | the training images and labels |
| `.source-runtime/` | run state | created on first run |

## What this means for the tests

Everything that tests logic runs against this bundle: the ghost-frame and
calibration suites, the browser suites, the API and pipeline tests.

Two kinds of test need something the bundle deliberately leaves out:

- **Tests that read `git show HEAD:<path>`** — `test_settings_alignment` verifies
  that the settings files still match the original repository. With no `.git`
  here it compares against nothing and reports a difference, even though the
  files in this bundle are byte-identical to the originals. Run that test in the
  original checkout.
- **Tests that decode a recording** — anything touching a real clip needs
  `data/real_videos` and `data/test_videos`. Those are in the Docker image; copy
  them out, or run the tests inside the image.

Everything else runs as it does in the original checkout.
