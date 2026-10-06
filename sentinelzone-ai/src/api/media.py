"""Read-only catalog of the original repository videos and datasets.

The original SentinelZone-AI media lives in ``data/real_videos`` and
``data/test_videos`` and the YOLO datasets live under ``data/``. Those trees are
deliberately excluded from the slim runtime image and from git-lfs-pointer clones,
so the running application resolves them from the first populated location:

1. ``SENTINEL_MEDIA_DIRS`` / ``SENTINEL_DATASET_DIRS`` (comma separated, explicit)
2. the repository checkout that contains this file (``<repo>/data/...``)
3. the process working directory (``./data/...``)

Only original, already-restored assets are served. Nothing here scans a network,
writes to the media tree, or fabricates a catalog entry.
"""
from __future__ import annotations

import math
import os
import re
import threading
from collections import OrderedDict
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np
import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
LOCK = threading.RLock()
PROBE_CACHE: dict = {}
FRAME_CACHE: OrderedDict = OrderedDict()
# The JPEG cache is bounded by bytes, not entries: a 1080p frame is ~200 KB, so
# a 300-frame clip needs ~60 MB. Once a clip is pre-encoded the player replays
# it from cache, which also removes the seek that would otherwise happen when
# the playhead wraps back to frame 0.
JPEG_CACHE_BYTES = int(os.getenv("SENTINEL_MEDIA_JPEG_CACHE_MB", "512")) * 1024 * 1024
FRAME_CACHE_BYTES = 0
MAX_SHA256_BYTES = 512 * 1024 * 1024  # guard against pathological single files
SHOWCASE_SCENARIOS = {
    "worker_in_excavator_blind_spot": "scenario_worker_in_excavator_blind_spot",
    "worker_near_heavy_equipment_exca9": "scenario_exca_near_miss",
    "worker_and_excavator_near_barrier": "scenario_worker_and_excavator_near_barrier",
}
SHOWCASE_BY_STEM = {stem.lower(): scenario for stem, scenario in SHOWCASE_SCENARIOS.items()}

# Presentation format. Every recording is decoded, scaled and letterboxed onto
# one canonical 1920x1080 canvas and exposed on a fixed 30 fps timeline, so the
# player always has the same geometry and rate to work with regardless of the
# source (720p, 1080p, 4K, portrait, 20/24/25/29.97 fps).
NORMALIZED_WIDTH = 1920
NORMALIZED_HEIGHT = 1080
NORMALIZED_FPS = 30
# Preferred group when the same bytes exist in more than one folder.
GROUP_PREFERENCE = ("real_videos", "test_videos")


def source_index_for_output(output_index: int, source_fps: float, source_frames: int) -> int:
    """Map a 30 fps output frame to the source frame that supplies its pixels.

    Sources slower than 30 fps are presented by holding frames (a 24 fps source
    shows each source frame 1.25x). Sources at or above 30 fps are sampled. No
    synthetic intermediate frame is ever invented, so every displayed image is a
    real decoded frame of the original recording.
    """
    if source_frames <= 0:
        return 0
    if not source_fps or source_fps <= 0:
        return max(0, min(source_frames - 1, output_index))
    ratio = source_fps / float(NORMALIZED_FPS)
    index = int(math.floor(output_index * ratio + 0.5))
    return max(0, min(source_frames - 1, index))


def output_frame_count(source_frames: int, source_fps: float) -> int:
    if source_frames <= 0:
        return 0
    if not source_fps or source_fps <= 0:
        return source_frames
    return max(1, int(math.floor(source_frames * NORMALIZED_FPS / float(source_fps) + 0.5)))


def normalize_frame(frame: np.ndarray) -> tuple[np.ndarray, dict]:
    """Scale to fit and letterbox onto the canonical 1920x1080 canvas.

    Aspect ratio is preserved, so nothing is stretched; the bars are reported so
    a viewer can tell where they are.
    """
    height, width = frame.shape[:2]
    if (width, height) == (NORMALIZED_WIDTH, NORMALIZED_HEIGHT):
        return frame, {"letterbox": False, "offset_x": 0, "offset_y": 0, "scale": 1.0}
    scale = min(NORMALIZED_WIDTH / float(width), NORMALIZED_HEIGHT / float(height))
    new_width = max(1, min(NORMALIZED_WIDTH, int(round(width * scale))))
    new_height = max(1, min(NORMALIZED_HEIGHT, int(round(height * scale))))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    resized = cv2.resize(frame, (new_width, new_height), interpolation=interpolation)
    canvas = np.zeros((NORMALIZED_HEIGHT, NORMALIZED_WIDTH, 3), dtype=np.uint8)
    offset_x = (NORMALIZED_WIDTH - new_width) // 2
    offset_y = (NORMALIZED_HEIGHT - new_height) // 2
    canvas[offset_y:offset_y + new_height, offset_x:offset_x + new_width] = resized
    return canvas, {
        "letterbox": True,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "scale": round(scale, 6),
    }


def _env_paths(name: str) -> list[Path]:
    raw = os.getenv(name, "")
    paths = [Path(item.strip()).expanduser() for item in raw.split(",") if item.strip()]
    return [path.resolve() for path in paths]


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def media_roots() -> list[Path]:
    explicit = _env_paths("SENTINEL_MEDIA_DIRS")
    if explicit:
        return explicit
    return _dedupe([
        (REPO_ROOT / "data" / "real_videos").resolve(),
        (REPO_ROOT / "data" / "test_videos").resolve(),
        (Path("data") / "real_videos").resolve(),
        (Path("data") / "test_videos").resolve(),
    ])


def data_roots() -> list[Path]:
    explicit = _env_paths("SENTINEL_DATASET_DIRS")
    if explicit:
        return explicit
    return _dedupe([(REPO_ROOT / "data").resolve(), (Path("data") / "data").resolve()])


def _manifest_lfs_oids() -> dict[str, str]:
    candidates = [REPO_ROOT / "data" / "original_assets_manifest.json", Path("data") / "original_assets_manifest.json"]
    for path in candidates:
        try:
            payload = path.read_text()
        except OSError:
            continue
        try:
            import json

            data = json.loads(payload)
        except ValueError:
            continue
        return {item["path"]: item["oid"] for item in data.get("lfs_objects", []) if item.get("path")}
    return {}


def _media_id(root: Path, path: Path) -> str:
    group = root.name if root.name not in (".", "") else "media"
    return f"{re.sub(r'[^A-Za-z0-9_-]+', '_', group)}_{re.sub(r'[^A-Za-z0-9_-]+', '_', path.stem)}".strip("_")


def _title(path: Path) -> str:
    return re.sub(r"[_\-]+", " ", path.stem).strip().title()


def _sha256(path: Path) -> str | None:
    size = path.stat().st_size
    if size > MAX_SHA256_BYTES:
        return None
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe(path: Path) -> dict:
    key = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
    with LOCK:
        cached = PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    result = {
        "width": None,
        "height": None,
        "fps": None,
        "frame_count": None,
        "duration_seconds": None,
        "playable": False,
        "note": "",
    }
    if path.stat().st_size < 10_240:
        result["note"] = "upstream asset smaller than 10 KiB; retained for provenance only"
    else:
        capture = cv2.VideoCapture(str(path))
        try:
            opened = capture.isOpened()
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        finally:
            capture.release()
        result.update({
            "width": width or None,
            "height": height or None,
            "fps": round(fps, 3) if fps else None,
            "frame_count": frame_count or None,
            "duration_seconds": round(frame_count / fps, 3) if frame_count and fps else None,
            "playable": bool(opened and frame_count > 0),
        })
        if not result["playable"]:
            result["note"] = "video container could not be opened by the runtime decoder"
    with LOCK:
        PROBE_CACHE[key] = result
    return result


def _media_files() -> list[tuple[Path, Path]]:
    found: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for root in media_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append((root, resolved))
    return found


# No lru_cache: the signature is the directory's mtime and DEPTH, so caching it
# for the life of the process made the comparison below always true and the
# invalidation could never fire. Recomputing it is two stat calls.
def _catalog_signature() -> tuple:
    marks = []
    for root in media_roots() + data_roots():
        try:
            marks.append((str(root), root.stat().st_mtime_ns))
        except OSError:
            marks.append((str(root), 0))
    return tuple(marks)


def _presentation(item: dict) -> dict:
    """Attach the canonical 1920x1080 @ 30 fps presentation fields."""
    source_frames = item.get("frame_count") or 0
    source_fps = item.get("fps") or 0.0
    playable = bool(item.get("playable"))
    item["source_width"] = item.get("width")
    item["source_height"] = item.get("height")
    item["source_fps"] = round(source_fps, 3) if source_fps else None
    item["source_frame_count"] = source_frames
    item["normalized"] = True
    item["presentation_width"] = NORMALIZED_WIDTH
    item["presentation_height"] = NORMALIZED_HEIGHT
    item["presentation_fps"] = NORMALIZED_FPS
    if playable:
        item["width"] = NORMALIZED_WIDTH
        item["height"] = NORMALIZED_HEIGHT
        item["fps"] = NORMALIZED_FPS
        item["frame_count"] = output_frame_count(source_frames, source_fps)
    return item


def _content_key(item: dict) -> str:
    return item.get("lfs_oid") or item.get("sha256") or f"{item['path']}:{item['size_bytes']}"


def catalog(refresh: bool = False, include_duplicates: bool = True) -> list[dict]:
    """Index the restored recordings.

    Identical bytes are listed once. When the same file exists in more than one
    folder the ``real_videos`` copy wins; the other copy is kept addressable and
    reported as an alias so no provenance is lost, but it never appears twice in
    the UI.
    """
    signature = _catalog_signature()
    if not refresh:
        with LOCK:
            cached = _CATALOG_CACHE.get("signature")
            if cached == signature and _CATALOG_CACHE.get("include_duplicates") == include_duplicates:
                return _CATALOG_CACHE["items"]
    oids = _manifest_lfs_oids()
    items: list[dict] = []
    for root, path in _media_files():
        stat = path.stat()
        item = {
            "id": _media_id(root, path),
            "title": _title(path),
            "filename": path.name,
            "group": root.name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "sha256": _sha256(path),
            "lfs_oid": oids.get(f"data/{root.name}/{path.name}"),
            "showcase_scenario_id": SHOWCASE_BY_STEM.get(path.stem.lower()),
        }
        item.update(_probe(path))
        items.append(_presentation(item))

    # One entry per distinct recording, preferring the primary folder.
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(_content_key(item), []).append(item)
    listed: list[dict] = []
    for members in groups.values():
        members.sort(key=lambda entry: (GROUP_PREFERENCE.index(entry["group"]) if entry["group"] in GROUP_PREFERENCE else 9, entry["filename"]))
        primary, *aliases = members
        primary["duplicate_count"] = len(aliases)
        primary["aliases"] = [{"id": alias["id"], "filename": alias["filename"], "group": alias["group"]} for alias in aliases]
        for alias in aliases:
            alias["duplicate_of"] = primary["id"]
            alias["duplicate_count"] = len(aliases)
            alias["aliases"] = []
        listed.append(primary)
        if include_duplicates:
            listed.extend(aliases)

    listed.sort(key=lambda entry: (entry["group"], entry["filename"]))
    with LOCK:
        _CATALOG_CACHE.update({"signature": signature, "items": listed, "include_duplicates": include_duplicates})
    return listed


#: The UI never shows a recording whose frame is larger than 1080p. A frame is
#: compared on its long side so an ordinary portrait 720p recording (720x1280) is
#: kept, while 4K (3840x2160) and portrait 4K (2160x3840) are hidden.
UI_MAX_LONG_SIDE = 1920


def within_ui_resolution(item: dict) -> bool:
    """True when a recording is no larger than 1080p on its long side.

    A recording whose source geometry could not be measured is kept: absence of
    a measurement is not evidence that it exceeds the limit.
    """
    width = int(item.get("source_width") or 0)
    height = int(item.get("source_height") or 0)
    if width <= 0 or height <= 0:
        return True
    return max(width, height) <= UI_MAX_LONG_SIDE


def hidden_by_resolution(refresh: bool = False) -> list[dict]:
    """Recordings withheld from the UI for exceeding 1080p, for auditing."""
    return [item for item in catalog(refresh=refresh, include_duplicates=False)
            if not within_ui_resolution(item)]


#: Some of the restored stock clips carry a third party's own annotations burned
#: into the pixels - flat salmon PPE labels reading "helmet 0.89" and the like,
#: over footage that also carries a studio watermark. They are part of the media,
#: not this application's output, and nothing in this repository draws them. The
#: panel says so rather than letting them be read as our detections.
_ANNOTATION_CACHE: dict = {}
_ANNOTATION_LOCK = threading.Lock()


def _frame_has_annotation_band(frame) -> bool:
    """True if the upper part of this frame carries the label's flat salmon band."""
    top = frame[:260]
    if top.size == 0:
        return False
    blue = top[:, :, 0].astype(np.int16)
    green = top[:, :, 1].astype(np.int16)
    red = top[:, :, 2].astype(np.int16)
    band = ((red > 200) & (green > 120) & (green < 200)
            & (blue > 120) & (blue < 200) & (np.abs(green - blue) < 45))
    return int(band.sum()) > 1500


def baked_in_annotations(media_id: str, path, frame_count: int) -> bool:
    """Whether this recording ships with annotations burned into the footage.

    Sampled at several points through the clip and cached per media: a single
    frame could coincidentally carry that colour, and the labels appear only on
    the frames where a head is detected by the third party's model.
    """
    key = f"{media_id}:{path}"
    with _ANNOTATION_LOCK:
        cached = _ANNOTATION_CACHE.get(key)
    if cached is not None:
        return cached

    verdict = False
    total = int(frame_count or 0)
    probes = [int(total * f) for f in (0.05, 0.2, 0.4, 0.6, 0.8, 0.95)] if total else [10, 60, 150]
    hits = checked = 0
    capture = cv2.VideoCapture(str(path))
    try:
        if capture.isOpened():
            for index in probes:
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue
                checked += 1
                if _frame_has_annotation_band(frame):
                    hits += 1
    finally:
        capture.release()
    # Two hits: one frame of that colour is a coincidence, two is the overlay.
    verdict = hits >= 2 and checked > 0
    with _ANNOTATION_LOCK:
        _ANNOTATION_CACHE[key] = verdict
    return verdict


def unique_catalog(refresh: bool = False) -> list[dict]:
    """Catalog with duplicate recordings removed - what the UI lists.

    Also withholds any recording larger than 1080p. Every UI surface reads this,
    so one filter covers the scenario dropdown, the restored-assets panel and
    the displayed counts. The files stay on disk and addressable, and the
    withheld set is reportable through :func:`hidden_by_resolution`.
    """
    return [item for item in catalog(refresh=refresh, include_duplicates=False)
            if within_ui_resolution(item)]


_CATALOG_CACHE: dict = {}


def video_dir(group: str) -> Path:
    """Return the first populated directory for a media group, creating nothing."""
    fallback = Path("data") / group
    for root in media_roots():
        if root.name == group and root.is_dir():
            return root
    return fallback


def resolve_media_path(value: str) -> str:
    """Resolve a legacy relative ``data/...`` path against the populated roots."""
    candidate = Path(value)
    if candidate.is_absolute():
        return str(candidate)
    parts = candidate.parts
    if len(parts) >= 3 and parts[0] == "data" and parts[1] in {"real_videos", "test_videos"}:
        root = video_dir(parts[1])
        resolved = root.joinpath(*parts[2:])
        if resolved.is_file():
            return str(resolved)
    return value


def media_index() -> dict[str, dict]:
    with LOCK:
        return {item["id"]: item for item in catalog()}


def _media_entry(media_id: str) -> dict:
    entry = media_index().get(media_id)
    if entry is None:
        raise HTTPException(404, "Media not found")
    return entry


class FrameSource:
    """Sequential-first frame reader.

    Seeking in an inter-frame-coded MP4 is expensive: on this machine a random
    ``POS_FRAMES`` seek plus decode costs ~79 ms while decoding the next frame in
    sequence costs ~2.2 ms. Playback therefore walks forward from a persistent
    cursor and only seeks when the request jumps (scrub backwards, or a change
    of clip). Measured effect on a 24 fps clip: 12.6 fps ceiling with per-request
    seeking, 445 fps sequential.
    """

    MAX_CATCHUP = 240  # frames we will decode forward before preferring a seek

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._capture = cv2.VideoCapture(path)
        if not self._capture.isOpened():
            raise RuntimeError(f"media could not be opened: {path}")
        self._next_index = 0
        self._closed = False

    def read(self, index: int) -> Optional[np.ndarray]:
        with self._lock:
            if self._closed:
                raise RuntimeError("frame source is closed")
            if index < self._next_index or index - self._next_index > self.MAX_CATCHUP:
                if not self._capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                    self._next_index = 0
                    self._capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                self._next_index = index
            while self._next_index < index:
                if not self._capture.grab():
                    self._next_index = 0
                    self._capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                    break
                self._next_index += 1
            ok, frame = self._capture.read()
            self._next_index = index + 1
            return frame if ok else None

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._capture.release()

    @property
    def cursor(self) -> int:
        return self._next_index


_FRAME_SOURCES: "OrderedDict[str, FrameSource]" = OrderedDict()
MAX_FRAME_SOURCES = 6


def frame_source(path: str) -> FrameSource:
    """Return the shared reader for a path, evicting the least recently used."""
    with LOCK:
        source = _FRAME_SOURCES.get(path)
        if source is not None:
            _FRAME_SOURCES.move_to_end(path)
            return source
        source = FrameSource(path)
        _FRAME_SOURCES[path] = source
        while len(_FRAME_SOURCES) > MAX_FRAME_SOURCES:
            _, evicted = _FRAME_SOURCES.popitem(last=False)
            try:
                evicted.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        return source


def close_frame_sources() -> None:
    with LOCK:
        while _FRAME_SOURCES:
            _, source = _FRAME_SOURCES.popitem()
            try:
                source.close()
            except Exception:  # pragma: no cover
                pass


def decode_frame(media_id: str, index: int) -> tuple[np.ndarray, Path, int]:
    """Decode one frame of the 30 fps presentation timeline.

    ``index`` is an output frame index. The source frame that supplies its pixels
    is derived from the source frame rate, the frame is decoded through the
    sequential reader and letterboxed onto the canonical 1920x1080 canvas, so
    callers always receive the same geometry.
    """
    entry = _media_entry(media_id)
    if not entry["playable"]:
        raise HTTPException(409, entry["note"] or "Media is not playable")
    if index < 0:
        raise HTTPException(404, "Frame not found")
    total = entry.get("frame_count") or 0
    if total and index >= total:
        raise HTTPException(404, "Frame not found")
    path = Path(entry["path"])
    source_index = source_index_for_output(
        index,
        entry.get("source_fps") or entry.get("fps") or 0.0,
        entry.get("source_frame_count") or 0,
    )
    try:
        source = frame_source(str(path))
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    raw = source.read(source_index)
    if raw is None:
        raise HTTPException(404, "Frame not found")
    frame, _transform = normalize_frame(raw)
    return frame, path, path.stat().st_mtime_ns


def frame_jpeg(media_id: str, index: int) -> bytes:
    return frame_jpeg_for(media_id, index)[0]


def frame_jpeg_for(media_id: str, index: int, frame=None) -> tuple[bytes, tuple]:
    """Return (jpeg, cache key), reusing a pre-encoded frame when available.

    A warmed clip is therefore served from cache with no decode and no encode,
    which is what makes playback and the loop wrap smooth.
    """
    key = frame_key(media_id, index)
    cached = cached_frame(key)
    if cached is not None:
        return cached, key
    if frame is None:
        frame, _path, _ = decode_frame(media_id, index)
    return encode_jpeg(frame, key), key


def encode_jpeg(frame: np.ndarray, key=None) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise HTTPException(500, "Frame encoding failed")
    data = buffer.tobytes()
    if key is not None:
        store_frame_jpeg(key, data)
    return data


def store_frame_jpeg(key, data: bytes) -> None:
    """Cache an encoded frame, evicting least-recently-used entries by bytes."""
    global FRAME_CACHE_BYTES
    with LOCK:
        if key in FRAME_CACHE:
            FRAME_CACHE_BYTES -= len(FRAME_CACHE.pop(key))
        FRAME_CACHE[key] = data
        FRAME_CACHE.move_to_end(key)
        FRAME_CACHE_BYTES += len(data)
        while FRAME_CACHE and FRAME_CACHE_BYTES > JPEG_CACHE_BYTES:
            _, evicted = FRAME_CACHE.popitem(last=False)
            FRAME_CACHE_BYTES -= len(evicted)


def cached_frame(key) -> Optional[bytes]:
    with LOCK:
        data = FRAME_CACHE.get(key)
        if data is not None:
            FRAME_CACHE.move_to_end(key)
        return data


def frame_key(media_id: str, index: int):
    entry = _media_entry(media_id)
    path = Path(entry["path"])
    return (str(path), path.stat().st_mtime_ns, index)


def _split_dir(root: Path, split: str, kind: str, layout: str) -> Path:
    return (root / kind / split) if layout == "images_first" else (root / split / kind)


def _dataset_entry(root: Path) -> dict | None:
    data_yaml = root / "data.yaml"
    if not data_yaml.is_file():
        return None
    try:
        manifest = yaml.safe_load(data_yaml.read_text()) or {}
    except Exception:
        manifest = {}
    layout = "images_first" if (root / "images" / "train").is_dir() else "split_first"
    names = manifest.get("names")
    if isinstance(names, dict):
        def _class_key(item):
            try:
                return (0, int(item[0]))
            except (TypeError, ValueError):
                return (1, str(item[0]))
        class_names = [str(value) for _, value in sorted(names.items(), key=_class_key)]
    elif isinstance(names, list):
        class_names = [str(value) for value in names]
    else:
        class_names = []
    splits = []
    if layout == "images_first":
        split_dirs = [root / "images" / name for name in sorted(path.name for path in (root / "images").glob("*") if path.is_dir())]
    else:
        split_dirs = [path for path in sorted(root.iterdir()) if path.is_dir() and (path / "images").is_dir()]
    for image_root in split_dirs:
        split = image_root.name
        images = sorted(path for path in (image_root if layout == "images_first" else image_root / "images").glob("*") if path.is_file())
        label_root = root / "labels" / split if layout == "images_first" else image_root / "labels"
        labels = sorted(label_root.glob("*.txt")) if label_root.is_dir() else []
        if not images and not labels:
            continue
        image_stems = {item.stem for item in images}
        label_stems = {item.stem for item in labels}
        splits.append({
            "name": split,
            "images": len(images),
            "labels": len(labels),
            "complete": bool(images) and image_stems == label_stems,
        })
    if not splits:
        return None
    readme = root / "README.dataset.txt"
    license_name = ""
    source = ""
    if readme.is_file():
        text = readme.read_text(errors="replace")
        for line in text.splitlines():
            if line.lower().startswith("license:"):
                license_name = line.split(":", 1)[1].strip()
            if line.lower().startswith("https://universe.roboflow.com/"):
                source = line.strip()
    return {
        "id": re.sub(r"[^A-Za-z0-9_-]+", "_", root.name).strip("_"),
        "name": _title(root),
        "path": str(root),
        "layout": layout,
        "class_count": len(class_names),
        "class_names": class_names,
        "splits": splits,
        "total_images": sum(split["images"] for split in splits),
        "total_labels": sum(split["labels"] for split in splits),
        "license": license_name,
        "source": source or manifest.get("roboflow", {}).get("url", ""),
    }


def datasets() -> list[dict]:
    entries: list[dict] = []
    seen: set[Path] = set()
    for root in data_roots():
        if not root.is_dir():
            continue
        for candidate in sorted(path for path in root.iterdir() if path.is_dir()):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            entry = _dataset_entry(candidate)
            if entry is not None:
                entry["id"] = re.sub(r"[^A-Za-z0-9_-]+", "_", candidate.name).strip("_")
                entry["path"] = str(candidate)
                entries.append(entry)
    return entries


def dataset_index() -> dict[str, dict]:
    return {entry["id"]: entry for entry in datasets()}


def _range_response(path: Path, range_header: str | None) -> Response:
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if match:
            first, last = match.groups()
            if first == "" and last:
                length = min(int(last), size)
                start, end = size - length, size - 1
            else:
                start = int(first or 0)
                end = min(int(last), size - 1) if last else size - 1
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
    length = max(0, end - start + 1)

    def stream() -> Iterator[bytes]:
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f'inline; filename="{path.name}"',
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(stream(), status_code=status, media_type="video/mp4", headers=headers)


def summary() -> dict:
    items = unique_catalog()
    return {
        "original_media": len(items),
        "playable_media": sum(1 for item in items if item["playable"]),
        "datasets": len(datasets()),
        "showcase_scenarios": [item["showcase_scenario_id"] for item in items if item["showcase_scenario_id"]],
    }


def router() -> APIRouter:
    r = APIRouter(prefix="/api/v1/showcase", tags=["original-media"])

    @r.get("/media")
    def media():
        items = unique_catalog()
        return {
            "count": len(items),
            "playable": sum(1 for item in items if item["playable"]),
            "showcase": [item["id"] for item in items if item["showcase_scenario_id"]],
            "media": items,
        }

    @r.get("/media/{media_id}")
    def media_detail(media_id: str):
        return _media_entry(media_id)

    @r.get("/media/{media_id}/frames/{index}")
    def media_frame(media_id: str, index: int):
        data = frame_jpeg(media_id, index)
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @r.get("/media/{media_id}/file")
    def media_file(media_id: str, request: Request):
        entry = _media_entry(media_id)
        if not entry["playable"]:
            raise HTTPException(409, entry["note"] or "Media is not playable")
        return _range_response(Path(entry["path"]), request.headers.get("range"))

    @r.get("/datasets")
    def dataset_list():
        entries = datasets()
        return {"count": len(entries), "datasets": entries}

    @r.get("/datasets/{dataset_id}/manifest")
    def dataset_manifest(dataset_id: str):
        entry = dataset_index().get(dataset_id)
        if entry is None:
            raise HTTPException(404, "Dataset not found")
        data_yaml = Path(entry["path"]) / "data.yaml"
        if not data_yaml.is_file():
            raise HTTPException(404, "Dataset manifest unavailable")
        return Response(content=data_yaml.read_text(), media_type="text/yaml", headers={"Cache-Control": "no-store"})

    @r.get("/media-summary")
    def media_summary():
        return JSONResponse(summary())

    return r
