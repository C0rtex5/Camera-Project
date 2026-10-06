"""Measure detector precision for person and heavy machinery on the restored assets.

Two independent measurements, because the evidence available differs:

* **Images** - ``data/roboflow_downloaded`` carries YOLO ground-truth labels, so
  precision, recall and F1 are computed by IoU matching at a configurable
  threshold. This is the only place where "precision" is a real measurement.
* **Videos** - the restored recordings have no ground truth, so the harness
  reports coverage (frames with a worker / heavy-equipment detection), class mix,
  calibration error and per-frame latency. It never reports precision for them.

Usage:
    python scripts/evaluate_detector.py --limit 120
    python scripts/evaluate_detector.py --out data/reports
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.perception.detector import ConstructionSafetyDetector  # noqa: E402
from src.perception.frame_calibration import (  # noqa: E402
    HEAVY_EQUIPMENT,
    WORKER,
    calibration_report,
    clip_boxes,
    geometry_for,
    iou,
    resize_for_inference,
    to_native,
)

# Ground-truth taxonomy of the restored Roboflow export (17 classes).
#
# Important labelling note: this dataset is *partially labelled*. Only 19 of 398
# images carry a `Person` body box, while 194 carry at least one person-related
# annotation (hardhat, vest, mask, gloves...). Scoring a person detection against
# `Person` boxes alone therefore mislabels ~175 images as false positives.
#
# Both views are reported:
#   * `person_body_strict`  - matched against `Person` boxes only.
#   * `person`              - matched against any person-related annotation,
#                             which is the dataset's real person-presence signal.
GT_PERSON_BODY = {"Person"}
GT_PERSON = {
    "Person", "Hardhat", "NO-Hardhat", "Mask", "NO-Mask",
    "Safety Vest", "NO-Safety Vest", "Gloves", "Safety Shoes",
}
GT_HEAVY = {"EXCAVATORS", "dump truck", "truck", "wheel loader", "mini-van"}
GROUPS = {"person": GT_PERSON, "person_body_strict": GT_PERSON_BODY, "heavy_machinery": GT_HEAVY}
PREDICTED_CLASS_FOR_GROUP = {
    "person": WORKER,
    "person_body_strict": WORKER,
    "heavy_machinery": HEAVY_EQUIPMENT,
}


def load_ground_truth(dataset_root: Path) -> dict:
    data_yaml = yaml.safe_load((dataset_root / "data.yaml").read_text()) or {}
    names = data_yaml.get("names") or []
    if isinstance(names, dict):
        names = [value for _, value in sorted(names.items(), key=lambda item: int(item[0]))]
    gt_index = {name: idx for idx, name in enumerate(names)}

    samples = []
    for split in ("train", "valid", "test"):
        images_dir = dataset_root / split / "images"
        labels_dir = dataset_root / split / "labels"
        if not images_dir.is_dir():
            continue
        for image_path in sorted(images_dir.glob("*")):
            if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            label_path = labels_dir / f"{image_path.stem}.txt"
            boxes = []
            if label_path.is_file():
                for line in label_path.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) < 5:
                        continue
                    class_id = int(float(parts[0]))
                    cx, cy, w, h = (float(value) for value in parts[1:5])
                    boxes.append({
                        "class_name": names[class_id] if 0 <= class_id < len(names) else str(class_id),
                        "xywh": (cx, cy, w, h),
                    })
            samples.append({"path": image_path, "split": split, "boxes": boxes})
    return {"names": names, "index": gt_index, "samples": samples}


def box_contains(prediction: np.ndarray, truth: np.ndarray) -> bool:
    """True when the ground-truth centre falls inside the predicted box.

    Required because this dataset annotates *worn regions* (hardhat, vest, mask)
    rather than full bodies. A correct person box therefore has almost no IoU with
    the hardhat box it contains, but containment is unambiguous.
    """
    cx = (truth[0] + truth[2]) / 2.0
    cy = (truth[1] + truth[3]) / 2.0
    return bool(prediction[0] <= cx <= prediction[2] and prediction[1] <= cy <= prediction[3])


def matches(prediction: np.ndarray, truth: np.ndarray, threshold: float, allow_containment: bool) -> bool:
    if iou(prediction, truth) >= threshold:
        return True
    return allow_containment and box_contains(prediction, truth)


def match_counts(predictions: list[dict], truths: list[dict], threshold: float, allow_containment: bool = False) -> tuple[int, int, int]:
    """Greedy IoU matching, best match first. Returns (tp, fp, fn)."""
    if not predictions and not truths:
        return 0, 0, 0
    pairs = []
    for pi, prediction in enumerate(predictions):
        for ti, truth in enumerate(truths):
            overlap = iou(prediction["box"], truth["box"])
            if overlap >= threshold or (allow_containment and box_contains(prediction["box"], truth["box"])):
                pairs.append((overlap, pi, ti))
    pairs.sort(reverse=True)
    used_pred, used_truth = set(), set()
    true_positive = 0
    for _, pi, ti in pairs:
        if pi in used_pred or ti in used_truth:
            continue
        used_pred.add(pi)
        used_truth.add(ti)
        true_positive += 1
    false_positive = len(predictions) - true_positive
    false_negative = len(truths) - true_positive
    return true_positive, false_positive, false_negative


def evaluate_images(ground_truth: dict, detector, imgsz: int, iou_threshold: float, limit: int | None) -> dict:
    samples = ground_truth["samples"]
    if limit:
        step = max(1, len(samples) // limit)
        samples = samples[::step][:limit]

    tallies = {group: Counter(tp=0, fp=0, fn=0) for group in GROUPS}
    presence = {group: Counter(tp=0, fp=0, fn=0, tn=0) for group in GROUPS}
    per_class = {name: Counter(total=0, hit=0) for group in GROUPS for name in GROUPS[group]}
    unlabelled_positive_images = []
    geometry_errors = []
    timings = []
    class_counter = Counter()
    evaluated = 0

    for sample in samples:
        image = cv2.imread(str(sample["path"]))
        if image is None:
            continue
        evaluated += 1
        height, width = image.shape[:2]
        geometry = geometry_for(width, height, imgsz)

        started = time.perf_counter()
        resized, geometry = resize_for_inference(image, imgsz, geometry)
        detections, _ = detector.detect(resized)
        timings.append(1000 * (time.perf_counter() - started))

        native = clip_boxes(to_native(np.asarray(detections, dtype=np.float32).reshape(-1, 6), geometry), width, height)
        geometry_errors.append(calibration_report(geometry, native if len(native) else None)["max_relative_scale_error"])
        for row in native:
            class_counter[int(row[5])] += 1

        annotated = bool(sample["boxes"])
        detections_present = bool(len(native))
        if detections_present and not annotated and len(unlabelled_positive_images) < 15:
            unlabelled_positive_images.append(str(sample["path"].relative_to(ROOT)))

        for group, class_names in GROUPS.items():
            predicted_class = PREDICTED_CLASS_FOR_GROUP[group]
            truths = []
            for item in sample["boxes"]:
                if item["class_name"] not in class_names:
                    continue
                cx, cy, w, h = item["xywh"]
                truths.append({
                    "class_name": item["class_name"],
                    "box": np.array([
                        (cx - w / 2) * width, (cy - h / 2) * height,
                        (cx + w / 2) * width, (cy + h / 2) * height,
                    ]),
                })
            predictions = [{"box": row[:4]} for row in native if int(row[5]) == predicted_class]
            # Person annotations are worn regions in this dataset, so containment
            # is an equally valid match rule for the person groups.
            allow_containment = group in ("person", "person_body_strict")

            if not truths and not predictions:
                continue
            true_positive, false_positive, false_negative = match_counts(predictions, truths, iou_threshold, allow_containment)
            tallies[group].update(tp=true_positive, fp=false_positive, fn=false_negative)

            # Per ground-truth class recall, so a missing class (for example
            # EXCAVATORS) is visible instead of hidden inside an average.
            for truth in truths:
                bucket = per_class[truth["class_name"]]
                bucket["total"] += 1
                if any(matches(prediction["box"], truth["box"], iou_threshold, allow_containment) for prediction in predictions):
                    bucket["hit"] += 1

            has_prediction = bool(predictions)
            has_truth = bool(truths)
            if has_prediction and has_truth:
                presence[group].update(tp=1)
            elif has_prediction:
                presence[group].update(fp=1)
            elif has_truth:
                presence[group].update(fn=1)
            else:
                presence[group].update(tn=1)

    def summarise(counter: Counter) -> dict:
        tp, fp, fn = counter["tp"], counter["fp"], counter["fn"]
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
        return {
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
        }

    def summarise_presence(counter: Counter) -> dict:
        tp, fp, fn, tn = counter["tp"], counter["fp"], counter["fn"], counter["tn"]
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        return {
            "images_with_true_presence": tp, "images_with_false_alarm": fp,
            "images_missed": fn, "images_correctly_empty": tn,
            "presence_precision": round(precision, 4) if precision is not None else None,
            "presence_recall": round(recall, 4) if recall is not None else None,
        }

    per_class_report = {
        name: {
            "ground_truth_boxes": bucket["total"],
            "recalled": bucket["hit"],
            "recall": round(bucket["hit"] / bucket["total"], 4) if bucket["total"] else None,
        }
        for name, bucket in sorted(per_class.items())
        if bucket["total"]
    }

    return {
        "images_evaluated": evaluated,
        "iou_threshold": iou_threshold,
        "inference_imgsz": imgsz,
        "groups": {group: summarise(counter) for group, counter in tallies.items()},
        "image_level_presence": {group: summarise_presence(counter) for group, counter in presence.items()},
        "per_ground_truth_class": per_class_report,
        "predicted_class_histogram": dict(class_counter),
        "median_inference_ms": round(statistics.median(timings), 2) if timings else None,
        "max_calibration_scale_error": round(max(geometry_errors), 9) if geometry_errors else None,
        "images_with_detections_but_no_annotations": unlabelled_positive_images,
    }


def evaluate_videos(paths: list[Path], detector, imgsz: int, sample_frames: int) -> dict:
    results = []
    for path in paths:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            results.append({"file": path.name, "error": "could not open"})
            continue
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 25) or 25
        indices = np.unique(np.linspace(0, max(0, total - 1), min(sample_frames, max(total, 1))).astype(int))
        workers = heavy = frames_with_detection = 0
        class_counter = Counter()
        timings = []
        calibration_error = 0.0
        out_of_frame = 0
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            started = time.perf_counter()
            resized, geometry = resize_for_inference(frame, imgsz)
            detections, _ = detector.detect(resized)
            timings.append(1000 * (time.perf_counter() - started))
            native = clip_boxes(to_native(np.asarray(detections, dtype=np.float32).reshape(-1, 6), geometry), width, height)
            report = calibration_report(geometry, native if len(native) else None)
            calibration_error = max(calibration_error, report["max_relative_scale_error"])
            out_of_frame += report.get("out_of_frame_boxes", 0)
            for row in native:
                class_counter[int(row[5])] += 1
            frame_workers = sum(1 for row in native if int(row[5]) == WORKER)
            frame_heavy = sum(1 for row in native if int(row[5]) == HEAVY_EQUIPMENT)
            workers += frame_workers
            heavy += frame_heavy
            frames_with_detection += 1 if len(native) else 0
        capture.release()
        sampled = len(indices)
        results.append({
            "file": path.name,
            "native_width": width, "native_height": height,
            "duration_s": round(total / fps, 2) if fps else None,
            "frames_sampled": sampled,
            "frames_with_detection": frames_with_detection,
            "coverage": round(frames_with_detection / sampled, 3) if sampled else None,
            "mean_workers_per_frame": round(workers / sampled, 2) if sampled else None,
            "mean_heavy_equipment_per_frame": round(heavy / sampled, 2) if sampled else None,
            "class_histogram": dict(class_counter),
            "median_inference_ms": round(statistics.median(timings), 2) if timings else None,
            "max_calibration_scale_error": round(calibration_error, 9),
            "out_of_frame_boxes": out_of_frame,
        })
    totals = {
        "videos": len(results),
        "frames_with_worker": sum(1 for r in results if (r.get("mean_workers_per_frame") or 0) > 0),
        "frames_with_heavy_equipment": sum(1 for r in results if (r.get("mean_heavy_equipment_per_frame") or 0) > 0),
        "worst_calibration_error": round(max([r.get("max_calibration_scale_error", 0) for r in results] or [0]), 9),
        "out_of_frame_boxes_total": sum(r.get("out_of_frame_boxes", 0) for r in results),
        "unreadable_files": [r["file"] for r in results if r.get("error")],
    }
    return {"videos": results, "totals": totals}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", default="yolov8n.pt")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--match-iou", type=float, default=0.5, help="IoU required to count a detection as a true positive")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--limit", type=int, default=None, help="cap evaluated images")
    parser.add_argument("--video-frames", type=int, default=12, help="frames sampled per video")
    parser.add_argument("--dataset", default="data/roboflow_downloaded")
    parser.add_argument("--videos", nargs="*", default=None)
    parser.add_argument("--out", default="data/reports")
    args = parser.parse_args()

    dataset_root = ROOT / args.dataset
    if not dataset_root.is_dir():
        print(f"dataset not found: {dataset_root}")
        return 1

    detector = ConstructionSafetyDetector(weights_path=args.weights, device=args.device, conf_threshold=args.conf, iou_threshold=args.iou)
    ground_truth = load_ground_truth(dataset_root)
    print(f"evaluating {len(ground_truth['samples'])} labeled images from {args.dataset}")

    images_report = evaluate_images(ground_truth, detector, args.imgsz, args.match_iou, args.limit)
    for group, metrics in images_report["groups"].items():
        print(f"  {group}: {metrics}")

    if args.videos:
        video_paths = [ROOT / value for value in args.videos]
    else:
        video_paths = sorted((ROOT / "data" / "real_videos").glob("*.mp4"))
    videos_report = evaluate_videos(video_paths, detector, args.imgsz, args.video_frames)
    print(f"  videos: {videos_report['totals']}")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "weights": args.weights,
        "device": args.device,
        "conf_threshold": args.conf,
        "nms_iou": args.iou,
        "match_iou": args.match_iou,
        "dataset": args.dataset,
        "images": images_report,
        "videos": videos_report,
    }
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "detector_evaluation.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print(f"report written to {out_dir / 'detector_evaluation.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
