"""Verify or explicitly fetch the original Git LFS media and dataset splits.

The default mode is read-only. ``--fetch-lfs`` is the only mode that may contact the
configured upstream and populate missing video objects; it never checks out or
rewrites the rest of the worktree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    import cv2
except ImportError:  # pragma: no cover - the Docker runtime includes OpenCV
    cv2 = None

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a runtime dependency
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "original_assets_manifest.json"
VIDEO_PATTERNS = "data/real_videos/**,data/test_videos/**"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_lfs_pointer(path: Path) -> bool:
    try:
        return path.read_bytes()[:64].startswith(b"version https://git-lfs.github.com/spec/v1")
    except OSError:
        return False


def fetch_lfs() -> None:
    command = [
        "git", "lfs", "pull", "origin", "main",
        "--include=" + VIDEO_PATTERNS,
    ]
    print("Fetching original video objects:", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def verify_media(manifest: dict, errors: list[str], warnings: list[str]) -> None:
    # A removal recorded in the manifest is a decision, not a loss: it is
    # reported so the deviation stays visible, but it is not an error. An
    # unrecorded missing file is still an error.
    removed = {item["path"]: item for item in manifest.get("removed_objects", [])}
    for path_str, record in removed.items():
        reason = record.get("reason", "no reason recorded")
        state = "still present" if (ROOT / path_str).is_file() else "absent as intended"
        print(f"LFS REMOVED  {path_str} ({record.get('size', '?')} bytes) - {state}: {reason}")
        if not (ROOT / path_str).is_file():
            continue
        warnings.append(f"recorded as removed but still on disk: {path_str}")

    for item in manifest["lfs_objects"]:
        path = ROOT / item["path"]
        if not path.is_file():
            if item["path"] in removed:
                continue
            errors.append(f"missing LFS object: {item['path']}")
            continue
        if is_lfs_pointer(path):
            errors.append(f"LFS pointer remains instead of binary: {item['path']}")
            continue
        size = path.stat().st_size
        if size != item["size"]:
            errors.append(f"size mismatch: {item['path']} ({size} != {item['size']})")
            continue
        digest = sha256_file(path)
        if digest != item["oid"]:
            errors.append(f"SHA-256 mismatch: {item['path']}")
            continue
        print(f"LFS OK  {item['path']} ({size} bytes)")

        if path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
            # The upstream repository contains one intentionally tiny 258-byte
            # sample. Keep it as a provenance object, but do not mislabel it as a
            # decodable production clip.
            if size < 10240:
                warnings.append(f"tiny upstream media retained as provenance only: {item['path']}")
                continue
            if cv2 is None:
                warnings.append(f"OpenCV unavailable; skipped decode check: {item['path']}")
                continue
            capture = cv2.VideoCapture(str(path))
            try:
                opened = capture.isOpened()
                frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            finally:
                capture.release()
            if not opened or frames <= 0:
                errors.append(f"video cannot be decoded: {item['path']}")


def split_files(root: Path, split: str, kind: str, layout: str) -> list[Path]:
    directory = (root / kind / split) if layout == "images_first" else (root / split / kind)
    return sorted(directory.glob("*"))


def verify_dataset_splits(name: str, spec: dict, errors: list[str]) -> None:
    root = ROOT / spec["path"]
    layout = spec.get("layout", "split_first")
    if not root.is_dir():
        errors.append(f"missing dataset directory: {spec['path']}")
        return
    for split, expected in spec["splits"].items():
        images = split_files(root, split, "images", layout)
        labels = split_files(root, split, "labels", layout)
        if len(images) != expected:
            errors.append(f"{name} {split} image count {len(images)} != {expected}")
        if len(labels) != expected:
            errors.append(f"{name} {split} label count {len(labels)} != {expected}")
        image_stems = {path.stem for path in images}
        label_stems = {path.stem for path in labels}
        if image_stems != label_stems:
            missing = sorted(image_stems - label_stems)[:5]
            extra = sorted(label_stems - image_stems)[:5]
            errors.append(f"{name} {split} image/label parity mismatch; missing labels={missing}, extra labels={extra}")
        print(f"DATASET OK  {name}/{split}: {len(images)} images, {len(labels)} labels")


def verify_construction_yaml(errors: list[str]) -> None:
    path = ROOT / "data" / "construction_safety" / "data.yaml"
    if not path.is_file():
        errors.append("missing data/construction_safety/data.yaml")
        return
    text = path.read_text()
    if "D:/" in text or "C:/" in text or ":\\" in text:
        errors.append("construction_safety/data.yaml still contains a machine-specific absolute path")
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
            if not str(data.get("path", "")).startswith((".", "data/")):
                errors.append("construction_safety/data.yaml path is not repository-relative")
        except Exception as error:
            errors.append(f"construction_safety/data.yaml is invalid YAML: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-lfs", action="store_true", help="fetch only original data/real_videos and data/test_videos LFS objects")
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help="asset manifest path")
    args = parser.parse_args()
    manifest_path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as error:
        print(f"manifest error: {error}", file=sys.stderr)
        return 1

    if args.fetch_lfs:
        try:
            fetch_lfs()
        except subprocess.CalledProcessError as error:
            print(f"LFS fetch failed: {error}", file=sys.stderr)
            return 1

    errors: list[str] = []
    warnings: list[str] = []
    verify_media(manifest, errors, warnings)
    for name, spec in manifest.get("datasets", {}).items():
        if name == "construction_safety":
            verify_dataset_splits(name, spec, errors)
        else:
            verify_dataset_splits(name, spec, errors)
    verify_construction_yaml(errors)

    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"asset verification failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"asset verification passed: {len(manifest['lfs_objects'])} LFS objects, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
