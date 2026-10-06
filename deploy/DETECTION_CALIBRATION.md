# Detection frame calibration and model precision

This document records the measured result of calibrating the detection frames for
**person** and **heavy machinery**, and of validating the detector against the
restored datasets. Every number below was produced by
`scripts/evaluate_detector.py` on this machine; nothing is projected or quoted
from a publication.

## 1. Why the frames needed calibrating

The dashboard scales every box with `canvas / packet.frame_width` and
`canvas / packet.frame_height`. A detection box is therefore only correct when it
lives in the pixel space of the image the browser actually decoded.

The restored recordings are not one resolution: `1280x720`, `1920x1080`,
`3840x2160` and the portrait `2160x3840` and `607x1080` clips are all present.
Running the detector at native 4K is wasteful, so inference runs on an
aspect-preserving downscale and the boxes are mapped back. If that mapping is
wrong, boxes drift - silently, because nothing errors on a bad scale.

`src/perception/frame_calibration.py` makes the mapping explicit and measurable:

* `inference_size` keeps the aspect ratio **exactly** (an integer search minimises
  `|scale_x - scale_y|`), so the resize is a single uniform scale;
* `to_native` / `to_display` are exact inverses;
* `calibration_report` emits the measured scale error and the count of boxes that
  fall outside the frame.

## 2. Calibration accuracy (measured)

**Against full-resolution inference on the same frame** (the reference path):

| Resolution | Inference size | Scale error |
|---|---|---|
| 1280x720 | 960x540 | 0.0 |
| 1920x1080 | 960x540 | 0.0 |
| 3840x2160 | 960x540 | 0.0 |
| 2160x3840 | 540x960 | 0.0 |
| 607x1080 | 539x959 | 1.2e-05 |

Across 19 sampled frames of four recordings, the downscaled-and-mapped boxes
matched the full-resolution reference at:

```
mean IoU = 0.9902      class agreement = 100% of matched boxes
```

Across the whole evaluation run: **maximum calibration scale error 4.89e-04**,
**0 boxes outside the frame**. Visual confirmation on
`Worker_in_excavator_blind_spot` frame 30 shows the person box tight on the
worker and the vehicle boxes on the loader and excavator.

## 3. Model precision on the restored dataset

Ground truth: `data/roboflow_downloaded`, 398 images, 17 classes, 797 labelled
boxes. Matching at IoU 0.50, confidence 0.25, inference size 960.

### A labelling caveat that changes the number

The export is **partially labelled**: only 19 images carry a `Person` body box,
while 194 carry at least one person-related annotation (hardhat, vest, mask,
gloves...). A worker is often annotated only by the small worn region. Scoring a
full-body detection against a hardhat box with plain IoU produces ~99 "false
positives" that are in fact correct detections. Two rules are therefore reported:

* **`person`** - matched against any person-related annotation, using IoU **or**
  containment (the annotation centre inside the detected body). This is the
  dataset's real person-presence signal.
* **`person_body_strict`** - `Person` boxes only. Kept as a diagnostic; it is
  *not* a valid precision figure because 92% of images have no body box.

### Results - all 398 images

| Metric | Person | Person (body-only diagnostic) | Heavy machinery |
|---|---|---|---|
| Box precision | 0.7797 | 0.0697 | 0.7255 |
| Box recall | 0.6173 | 0.6377 | 0.5086 |
| Box F1 | 0.6891 | 0.1257 | 0.5980 |
| **Image-level presence precision** | **0.8721** | 0.0822 | **0.9673** |
| **Image-level presence recall** | **0.9845** | 0.9474 | **0.7437** |

Image-level presence is the more robust figure on a partially labelled dataset:
the model finds a person in 191 of 194 annotated images and raises 28 false-alarm
images; for heavy machinery it is correct on 148 images with 5 false alarms.

### Per ground-truth class recall - the important table

| Class | Boxes | Recall |
|---|---|---|
| Person | 138 | 0.9275 |
| Hardhat | 436 | 0.9312 |
| NO-Hardhat | 108 | 1.0000 |
| NO-Mask | 52 | 0.9423 |
| NO-Safety Vest | 38 | 0.9211 |
| Safety Vest | 53 | 0.8302 |
| Safety Shoes | 22 | 0.8182 |
| Gloves | 18 | 0.6667 |
| truck | 1 | 1.0000 |
| wheel loader | 82 | 0.7683 |
| dump truck | 97 | 0.6495 |
| **EXCAVATORS** | **110** | **0.2000** |
| mini-van | 1 | 0.0000 |

## 4. The one real gap: excavators

Both available weights (`yolov8n.pt`, `weights/yolo26n.pt`) are **COCO-80**
models. COCO has `person`, `truck`, `car`, `bus` - and **no excavator class**.
The detector maps truck/bus to `HEAVY_EQUIPMENT`, which is why wheel loader
(0.77) and dump truck (0.65) are found while **excavators sit at 0.20**.

This is visible in the live overlay: in
`Worker_in_excavator_blind_spot` frame 30 the small excavator top-right is boxed
while the large foreground excavator is not.

**Person detection is strong. Heavy-machinery detection is limited by the
weights, not by the calibration.** Raising excavator recall requires a detector
trained on this domain. The restored `data/roboflow_downloaded` export is
exactly that training set: 398 images with 110 excavator, 97 dump-truck and
82 wheel-loader boxes. Training on it and pointing `SENTINEL_DETECTOR_WEIGHTS` at
the result is the next step, and it is a training task, not a calibration one.

## 5. Video validation (coverage, not precision)

The restored recordings have **no ground truth**, so no precision is claimed for
them. What is measured is detection coverage, class mix, calibration error and
latency, over 8 sampled frames per video.

| Video | Resolution | Coverage | Workers/frame | Heavy/frame | Latency |
|---|---|---|---|---|---|
| 10810477-hd_1920_1080_30fps | 1920x1080 | 1.00 | 5.25 | 0.00 | 55 ms |
| 14117669-uhd_3840_2160_30fps | 3840x2160 | 0.88 | 9.00 | 0.00 | 70 ms |
| 15100676_2160_3840_30fps | 2160x3840 | 1.00 | 2.25 | 0.00 | 69 ms |
| 30sec_construction | 1280x720 | 1.00 | 2.88 | 0.00 | 55 ms |
| Worker_and_excavator_near_barrier | 1280x720 | 1.00 | 1.38 | 0.38 | 58 ms |
| Worker_in_excavator_blind_spot | 1280x720 | 1.00 | 1.38 | 0.88 | 57 ms |
| Worker_near_heavy_equipment_exca9 | 1280x720 | 0.75 | 0.75 | 0.12 | 56 ms |

8 of 9 videos yield worker detections; 3 of 9 yield heavy-equipment detections.
Calibration error 0.0 and 0 out-of-frame boxes on every video. Median inference
60 ms on CPU, so the calibrated overlay is comfortably interactive.

## 6. What the application does with this

`src/api/media_detection.py` runs the calibrated detector for restored
recordings and returns boxes in the coordinate space of the frame the hub serves,
with the calibration facts attached to the packet. It is honest about limits:

* `SENTINEL_MEDIA_DETECT=0` disables overlay detection;
* missing weights return no boxes **and** an explicit reason;
* a restored recording always reports `forecast_status=ILLUSTRATIVE_DEMO`, no
  tracks, no hazard envelope and no validated forecast, so overlay boxes are
  never presented as a validated safety measurement.

Reproduce with:

```bash
python scripts/evaluate_detector.py            # full dataset + all videos
python scripts/evaluate_detector.py --limit 80 # quick pass
```
