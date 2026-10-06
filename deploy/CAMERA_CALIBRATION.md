# Camera calibration from surveyed points

Use `scripts/calibrate_camera.py` to create the ground-plane transform for the actual camera stream. This is required before preparing camera-aligned forecasting data. The tool does not invent camera intrinsics, ground distances, or image points.

1. Obtain the camera's intrinsic matrix `K` and distortion coefficients `dist` for the exact stream resolution and lens settings. Ground correspondences alone do not replace intrinsic lens calibration.
2. Copy `config/calibration_survey.example.json` to a private working file and set the camera identity and exact stream dimensions. Fill in measured `K` and `dist`.
3. Survey at least four non-collinear points on the ground plane in the site's metric coordinate system. Measure their corresponding raw-image pixel coordinates. Add them under `fit_points` using `{"pixel": [u, v], "ground": [x_metres, y_metres]}`.
4. Measure at least four different surveyed locations under `check_points`. These are held out from fitting and used to check accuracy. Spread both sets across the intended monitored area. All points must lie on the same ground plane and use the same datum as site manifests.
5. Run:

```bash
python scripts/calibrate_camera.py \
  --survey /path/to/measured-survey.json \
  --output config/cameras.json \
  --report /path/to/calibration-report.json
```

The tool removes lens distortion, fits the transform from metric ground coordinates to undistorted image pixels, and evaluates metric error on the independent check points. It rejects duplicate, collinear, out-of-image, and nonfinite coordinates. By default the check-point RMSE must be at most 0.15 metres, matching the original repository's calibration-drift threshold; use `--max-rmse-m` only when a different measured site requirement applies.

Successful output contains a validated camera profile and a report with per-point errors and the camera fingerprint used by forecasting. Existing output files are preserved; select new paths when recalibrating, inspect the results, then replace the deployment profile and corresponding model together.

A passing fit checks only the supplied points. It does not establish accuracy on nonplanar terrain, outside the surveyed area, or after the camera moves, zooms, changes resolution, or changes lens settings. Those changes require new measurements and a matching forecast artifact.
