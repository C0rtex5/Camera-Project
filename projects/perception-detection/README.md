# Perception & Detection

> Sub-project `perception-detection` · status **extraction-ready** · cards **SEC-02, SEC-03**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

Everything downstream is only as good as the detections it receives. This sub-project turns
camera imagery into calibrated person / heavy-machinery / PPE boxes on the **native frame**, and
owns the geometry contract that guarantees every other project (overlay, tracker, twin) reasons in
the same pixel space. It is also the only project allowed to depend on the heavyweight torch /
ultralytics stack for inference.

## Owned paths

| Path | Contents |
|---|---|
| `src/perception/detector.py` | `ConstructionSafetyDetector`: YOLO inference wrapper, PPE flags, weight resolution |
| `src/perception/frame_calibration.py` | Letterbox geometry: native ↔ inference frames, scaling, IoU, calibration report |
| `src/perception/dataset_downloader.py` | Roboflow / synthetic construction-safety dataset acquisition |
| `src/perception/train_detector.py` | Detector training entry point |
| `src/perception/generate_test_videos.py` | Synthetic scenario video generation for demos |
| `weights/`, `yolov8n.pt` | Checkpoints (large binaries; move to LFS or a registry on extraction) |
| `data/construction_safety/`, `data/roboflow_downloaded/` | Tracked demo/validation dataset splits |
| `data/reports/detector_evaluation.json` | Latest detector evaluation result |
| `scripts/evaluate_detector.py`, `deploy/DETECTION_CALIBRATION.md` | Evaluation harness and calibration guidance |
| `tests/test_detector.py`, `tests/test_frame_calibration.py` | Unit tests for this project |

## Interfaces it publishes

- **`ConstructionSafetyDetector.detect`** — native-frame boxes with class, confidence and PPE
  flags; an empty frame is a normal result, not an error. Consumed by `api-gateway`,
  `media-pipeline`, `edge-runtime`.
- **Frame geometry (`geometry_for`, `scale_boxes`, `to_native`, `iou`)** — the single definition of
  what "native pixel" means for every overlay. Consumed by `api-gateway`, `media-pipeline`.
- **Weight resolution (`SENTINEL_DETECTOR_WEIGHTS`)** — a missing checkpoint degrades to an explicit
  unavailable state. Consumed by `deployment-runtime`, `validation-harness`.

## Dependencies

- **Depends on:** nothing (leaf project).
- **Depended on by:** `api-gateway`, `media-pipeline`, `edge-runtime`, `validation-harness`.

## How to run it

```bash
python scripts/run_project_tests.py perception-detection   # owned test slice
python scripts/evaluate_detector.py                        # precision against restored ground truth
python src/perception/train_detector.py                    # optional training path
```

## Acceptance criteria

1. A checkpoint loads and returns person / machinery / PPE boxes for a native frame.
2. Boxes survive the native → inference → native round trip within 1 px.
3. The evaluation script reports precision/recall against the restored ground truth.
4. No import of `src.api`, `src.graph` or any gateway state from this project.

## Risks and open edges

- **Weights are tracked binaries** in a git repository; the extraction step must move them to LFS
  or a model registry before this project becomes an independent repository.
- **Demo data is not site evidence.** `data/construction_safety` validates plumbing and must never
  be cited as site accuracy (the validation policy in `validation-harness` enforces this).
- **Heaviest dependency in the platform** — this project is the main reason the CPU image is large.
