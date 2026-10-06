# Align forecasting with the live camera

The production loader requires camera-aware checkpoint metadata. Existing bare weight files still load in development, but do not bypass production checks. Loading a file successfully does not establish predictive accuracy.

The artifact records a SHA-256 fingerprint of the camera ID, site ID, image dimensions, and calibration matrices; the graph backend; Sentinel class IDs; 30 observation samples at 10 Hz; and 50 future samples at 10 Hz. Live tracking resamples its timestamped observations to that 10 Hz model input, even when CPU inference runs below 10 frames per second. Large gaps reset tracking. Retrain and revalidate if calibration, camera position, resolution, or coordinate datum changes. Camera frame rate should support the intended prediction response time.

## Reviewed data contract

Create `dataset/train/` and `dataset/validation/` containing `.npz` trajectory windows from separate recordings. Do not put overlapping windows from one recording in both splits. Supply measured future tracks, not the application's own forecast output as training truth.

Each file contains:

| Array | Shape | Meaning |
|---|---|---|
| `node_history` | N × 30 × 8 | Metric x, y, vx, vy, ax, ay, heading, angular velocity; same EKF state convention as the live tracker |
| `class_ids` | N | Integer IDs: worker 0, spotter 1, heavy equipment 2, light vehicle 3 |
| `edge_index` | 2 × E | Integer directed source/destination node indices |
| `edge_attr` | E × 5 | Relative x, relative y, distance, relative speed, constant 1; same proximity graph as runtime |
| `future_xy` | N × 50 × 2 | Reviewed future absolute metric ground positions |

Use at least two agents and one graph edge per window. Match tracks across all observation and forecast samples. Use the live runtime's 25 m graph neighborhood.

Write `dataset/metadata.json` with `sample_interval_seconds: 0.1` and `camera_fingerprint`, computed by `src.edge.checkpoint.camera_fingerprint` using the validated first camera profile in `config/cameras.json`. This makes the dataset's camera association explicit before training.

## Train and validate

```bash
python scripts/train_forecaster.py \
  --dataset /path/to/reviewed-dataset \
  --camera-config config/cameras.json \
  --output weights/forecast-candidate.pt \
  --epochs 50
```

The trainer validates dimensions and class IDs, rejects exact duplicate windows across splits, trains the Gaussian-mixture trajectory loss, and saves the candidate with the best held-out average displacement error. Metadata contains dataset hashes, the seed, validation sample count, average displacement error (ADE), and final displacement error (FDE), both in metres. The model and runtime must use the same graph backend.

Inspect held-out trajectories and measure missed hazards, nuisance alarms, time-to-collision behavior, occlusion recovery, and camera reconnect behavior on representative footage. ADE/FDE alone do not establish collision-alert performance. Once the candidate meets the site's measured requirements, deploy it as `weights/forecast.pt`. No production checkpoint or site accuracy result is fabricated by the setup process.

The repository's synthetic one-epoch training test validates artifact creation and calibration mismatch rejection only. Actual training remains pending the camera and reviewed site recordings.
